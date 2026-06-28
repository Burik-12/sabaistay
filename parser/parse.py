#!/usr/bin/env python3
"""
SabaiStay — парсер объявлений через Claude API (заготовка под API-ключ).

Заменяет ручной разбор: берёт сырые посты (формат raw_post / sample_tg.json),
прогоняет каждый через Claude со СТРОГОЙ JSON-схемой (parser/listing_schema.json) →
гарантированно валидный JSON с фильтруемыми полями. Район нормализуется детерминированно
через parser/normalize_district.py. PDPA-safe: контакты не извлекаются (их нет в схеме).

Запуск (нужен ANTHROPIC_API_KEY в окружении — Владимир подключит, когда сделает проект платным):
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 parse.py ../data/sample_tg.json -o ../data/parsed.json          # по одному
    python3 parse.py ../data/sample_tg.json -o ../data/parsed.json --batch  # Batch API, −50%

Модель: claude-haiku-4-5 — осознанный выбор проекта под объём/стоимость (см. parser/prompt.md).
Если плохо читает тайский/смешанный текст — переключить на claude-opus-4-8 через --model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.exit("Нужен пакет anthropic: pip install anthropic")

from normalize_district import DistrictNormalizer

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "parser" / "listing_schema.json"
DISTRICTS_PATH = ROOT / "data" / "districts.json"

DEFAULT_MODEL = "claude-haiku-4-5"   # под объём; claude-opus-4-8 — если нужна точность на тайском
MAX_TOKENS = 1024                    # вывод маленький (одно объявление)

# Структурные выводы не поддерживают числовые/строковые ограничения — снимаем их из схемы.
_STRIP_KEYS = {"minimum", "maximum", "multipleOf", "minLength", "maxLength",
               "minItems", "maxItems", "pattern"}


def sanitize_schema(node):
    """Рекурсивно убрать ключи, не поддерживаемые structured outputs."""
    if isinstance(node, dict):
        return {k: sanitize_schema(v) for k, v in node.items() if k not in _STRIP_KEYS}
    if isinstance(node, list):
        return [sanitize_schema(x) for x in node]
    return node


def build_system_prompt(districts: dict) -> str:
    """Системный промпт = правила + газеттир районов. Кешируется (cache_control)."""
    lines = []
    for d in districts["districts"]:
        aliases = ", ".join(d.get("aliases", [])[:6])
        lines.append(f"- {d['canonical']} (варианты: {aliases})")
    districts_block = "\n".join(lines)
    return f"""Ты — извлекатель данных для поисковика жилья на острове Ко Пханган (Таиланд).
Из свободного текста объявления (русский/английский/тайский/смесь) извлеки структуру строго по JSON-схеме.

ПРАВИЛА:
1. Не выдумывай. Нет в тексте — null (или false для удобств). Лучше пропуск, чем галлюцинация.
2. is_listing=false для мусора: реклама приложений/услуг, обсуждение, обрывок без цены и объекта, поиск жилья.
3. Цена — число без пробелов/знаков («15к»→15000, диапазон→нижняя граница). По умолчанию THB.
4. price_period: ฿/мес/monthly/long term→month; ночь/night→night; продажа/sale→sale; иначе unknown.
5. Студия→bedrooms 0. Удобства — только явно упомянутые.
6. district_raw — дословно из текста; district_canonical — ближайший из списка ниже, иначе null (не угадывай).
7. status_hint: «сдано/rented/taken/занято»→rented; иначе active.
8. confidence — честная самооценка 0..1.
9. Контакты (телефон/имя/@username) НЕ извлекаем — политика приватности.

КАНОНИЧЕСКИЕ РАЙОНЫ:
{districts_block}"""


def build_params(raw_text: str, system: str, schema: dict, model: str) -> dict:
    """Параметры одного запроса (общие для single и batch)."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": raw_text}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }


def extract_json(message) -> dict:
    """Первый text-блок ответа — валидный JSON (гарантия structured outputs)."""
    text = next((b.text for b in message.content if b.type == "text"), None)
    return json.loads(text) if text else {}


def enrich(parsed: dict, post: dict, dn: DistrictNormalizer) -> dict:
    """Добавить источник и снапнуть район к канону+координатам нормализатором."""
    res = dn.normalize(parsed.get("district_raw") or parsed.get("district_canonical"))
    if res:
        parsed["district_canonical"] = res["canonical"]
        parsed["lat"], parsed["lng"] = res["lat"], res["lng"]
    return {
        "source": post.get("source", "telegram"),
        "source_url": post["source_url"],
        "posted_at": post.get("posted_at"),
        "parsed": parsed,
    }


def run_single(client, posts, system, schema, model, dn) -> list[dict]:
    out = []
    for i, post in enumerate(posts, 1):
        try:
            msg = client.messages.create(**build_params(post["raw_text"], system, schema, model))
            out.append(enrich(extract_json(msg), post, dn))
        except anthropic.AuthenticationError:
            sys.exit("Неверный или отсутствующий ANTHROPIC_API_KEY")
        except anthropic.APIStatusError as e:
            print(f"[warn] {post['source_url']}: {e.status_code} {e.message}", file=sys.stderr)
        print(f"  {i}/{len(posts)}", end="\r", file=sys.stderr)
    return out


def run_batch(client, posts, system, schema, model, dn) -> list[dict]:
    """Batch API: −50% к стоимости, до 24ч (обычно <1ч)."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    by_id = {f"post-{i}": p for i, p in enumerate(posts)}
    batch = client.messages.batches.create(requests=[
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
            **build_params(p["raw_text"], system, schema, model)))
        for cid, p in by_id.items()
    ])
    print(f"Batch {batch.id} создан, ждём…", file=sys.stderr)
    while client.messages.batches.retrieve(batch.id).processing_status != "ended":
        time.sleep(30)
    out = []
    for r in client.messages.batches.results(batch.id):
        if r.result.type == "succeeded":
            out.append(enrich(extract_json(r.result.message), by_id[r.custom_id], dn))
        else:
            print(f"[warn] {r.custom_id}: {r.result.type}", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Парсинг сырых постов в фильтруемые поля через Claude")
    ap.add_argument("input", help="JSON-массив сырых постов (формат raw_post)")
    ap.add_argument("-o", "--out", required=True, help="куда сохранить разобранные объявления")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", action="store_true", help="через Batch API (−50%, медленнее)")
    ap.add_argument("--limit", type=int, help="ограничить число постов (для теста)")
    args = ap.parse_args()

    for p in (SCHEMA_PATH, DISTRICTS_PATH, Path(args.input)):
        if not p.exists():
            sys.exit(f"Нет файла: {p}")
    posts = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if isinstance(posts, dict):
        posts = posts.get("listings") or posts.get("posts") or []
    if not posts:
        sys.exit("Входной список постов пуст — проверь источник данных.")
    if args.limit:
        posts = posts[:args.limit]

    schema = sanitize_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    districts = json.loads(DISTRICTS_PATH.read_text(encoding="utf-8"))
    system = build_system_prompt(districts)
    dn = DistrictNormalizer()
    client = anthropic.Anthropic()  # читает ANTHROPIC_API_KEY из окружения

    runner = run_batch if args.batch else run_single
    listings = runner(client, posts, system, schema, args.model, dn)

    real = sum(1 for r in listings if r["parsed"].get("is_listing"))
    payload = {"_meta": {"model": args.model, "total": len(listings), "listings_count": real},
               "listings": listings}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nГотово: {len(listings)} разобрано ({real} объявлений) → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
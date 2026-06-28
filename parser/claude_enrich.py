#!/usr/bin/env python3
"""
SabaiStay — обогащение объявлений через Claude API (Haiku).

Запускает Claude на объявлениях, которые regex-парсер не смог разобрать:
нет цены, нет района, или уверенность < 0.65.

Стоимость: ~$0.05–0.10 за ~100–150 объявлений через claude-haiku-4-5.

──────────────────────────────────────
ЗАПУСК
──────────────────────────────────────
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 parser/claude_enrich.py --dry-run   # посмотреть сколько будет обработано
  python3 parser/claude_enrich.py             # обновить data/sample_listings.json

Опции:
  --limit N       обработать не более N объявлений (default: все)
  --dry-run       только показать что будет делаться, не писать файл
  --force         обработать даже те, у которых уже есть цена и район
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
    sys.exit("Установи зависимость: pip install anthropic")

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "sample_listings.json"
DISTRICTS_FILE = ROOT / "data" / "districts.json"

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 512

DISTRICTS_LIST: list[str] = []


def _load_districts() -> list[str]:
    with open(DISTRICTS_FILE) as f:
        d = json.load(f)
    return [di["canonical"] for di in d["districts"]]


EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "price_amount": {
            "type": ["integer", "null"],
            "description": "Цена аренды в тайских батах (THB). null если не указана."
        },
        "price_period": {
            "type": ["string", "null"],
            "enum": ["monthly", "weekly", "daily", "yearly", None],
            "description": "Период: monthly/weekly/daily/yearly. null если неясно."
        },
        "bedrooms": {
            "type": ["integer", "null"],
            "description": "Число спален. 0 = студия. null если не указано."
        },
        "property_type": {
            "type": ["string", "null"],
            "enum": ["house", "villa", "bungalow", "apartment", "condo", "room", "studio", "land", "other", None]
        },
        "district_canonical": {
            "type": ["string", "null"],
            "description": "Район Ко Пхангана из допустимого списка. null если не упомянут."
        },
        "area_sqm": {
            "type": ["number", "null"],
            "description": "Площадь в кв.м. null если не указана."
        }
    },
    "required": ["price_amount", "price_period", "bedrooms", "property_type", "district_canonical", "area_sqm"]
}


def _build_prompt(raw_text: str) -> str:
    districts = ", ".join(DISTRICTS_LIST)
    return f"""You are a real estate parser for Koh Phangan, Thailand rental listings.

Extract structured data from this rental listing. Return ONLY a JSON object matching the schema.

Valid districts (use EXACT spelling or null if not mentioned):
{districts}

Rules:
- price_amount: convert to Thai Baht integers. "15k" = 15000, "15 per month" = 15000 (assume thousands if small round number ≤ 100 without currency), "150,000" = 150000.
- If currency is USD/EUR/$ convert to THB at 35 THB/USD.
- price_period: infer from context ("per month" → monthly, "per night" → daily).
- district_canonical: match to the valid list above; use phonetic similarity. If ambiguous, null.
- property_type: infer from description.

Listing text:
---
{raw_text[:1500]}
---

Return only the JSON object, no explanation."""


def _needs_enrichment(parsed: dict) -> bool:
    return parsed.get("price_amount") is None or not parsed.get("district_canonical")


def _enrich(client: anthropic.Anthropic, raw_text: str) -> dict | None:
    prompt = _build_prompt(raw_text)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # снимаем markdown-обёртку если есть
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError, anthropic.APIError) as e:
        print(f"  [warn] ошибка: {e}", file=sys.stderr)
        return None


def _apply(parsed: dict, extracted: dict) -> bool:
    """Применяем только те поля, которых ещё нет. Возвращает True если что-то изменилось."""
    changed = False
    for field in ("price_amount", "price_period", "bedrooms", "property_type", "district_canonical", "area_sqm"):
        val = extracted.get(field)
        if val is not None and parsed.get(field) is None:
            parsed[field] = val
            changed = True
    # Пересчёт уверенности
    if changed:
        found = sum([
            parsed.get("price_amount") is not None,
            bool(parsed.get("district_canonical")),
            parsed.get("bedrooms") is not None,
            parsed.get("property_type") not in (None, "other"),
        ])
        parsed["confidence"] = round(min(0.55 + found * 0.12, 0.92), 2)
    return changed


def run(args: argparse.Namespace) -> None:
    global DISTRICTS_LIST
    DISTRICTS_LIST = _load_districts()

    with open(DATA_FILE) as f:
        data = json.load(f)

    listings = data["listings"]
    candidates = [
        l for l in listings
        if l["parsed"].get("is_listing")
        and (args.force or _needs_enrichment(l["parsed"]))
    ]
    print(f"Кандидатов на обогащение: {len(candidates)}/{len(listings)}")

    if args.limit:
        candidates = candidates[:args.limit]
        print(f"Лимит: обрабатываем {len(candidates)}")

    if args.dry_run:
        print("--dry-run: файл не будет изменён.")
        for l in candidates[:5]:
            p = l["parsed"]
            print(f"  {l['external_id']}: price={p.get('price_amount')}, district={p.get('district_canonical')}")
            print(f"    {l['raw_text'][:100]}…")
        return

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Нужна переменная ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=api_key)
    enriched = 0

    for i, listing in enumerate(candidates):
        raw = listing.get("raw_text") or ""
        print(f"[{i+1}/{len(candidates)}] {listing['external_id']} …", end=" ", flush=True)
        extracted = _enrich(client, raw)
        if extracted:
            changed = _apply(listing["parsed"], extracted)
            if changed:
                enriched += 1
                p = listing["parsed"]
                print(f"✓ price={p.get('price_amount')} district={p.get('district_canonical')}")
            else:
                print("— ничего нового")
        else:
            print("✗ ошибка")
        time.sleep(0.3)  # rate limit

    print(f"\nОбогащено: {enriched}/{len(candidates)}")

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Сохранено → {DATA_FILE}")
    print("\nТеперь пересобери витрину:")
    print("  python3 web/build.py && git add data/ web/index.html && git commit -m 'data: claude enrich'")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Обогащение объявлений через Claude API")
    ap.add_argument("--limit", type=int, default=0, help="макс. кол-во (0 = все)")
    ap.add_argument("--dry-run", action="store_true", help="только показать, не писать")
    ap.add_argument("--force", action="store_true", help="обрабатывать даже полностью заполненные")
    return ap.parse_args()


if __name__ == "__main__":
    run(_parse_args())

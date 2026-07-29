#!/usr/bin/env python3
"""
SabaiStay — сбор объявлений из Facebook через managed-акторы Apify (Фаза 3).

Почему managed-актор, а не свой парсер: FB меняет разметку раз в 3–6 недель,
и чинить её должен автор актора, а не мы. Подробности и цены — docs/facebook-ingest-plan.md.

Пишет в тот же формат, что и Telegram-коллектор (raw_post → data/dumps/*.ndjson),
поэтому scripts/update_site.py подхватывает результат без единой правки.

Три вещи, ради которых всё написано именно так:
  1. Инкрементальность — помним дату последнего успешного сбора (state-файл),
     передаём её актору, не платим второй раз за уже собранное.
  2. Сторож тишины — если прогон вернул ноль постов дважды подряд, шлём алерт.
     Молча остановившийся сбор хуже упавшего: витрина выглядит рабочей, а данные мертвы.
  3. Схемы входа акторов — в data/fb_sources.json, не в коде: авторы меняют поля.

Переменные окружения:
    APIFY_TOKEN            — обязательно, токен Apify
    SABAI_DATA_DIR         — куда писать дампы (как у коллектора TG)
    SABAI_ALERT_BOT_TOKEN  — опционально, бот для алертов
    SABAI_ALERT_CHAT_ID    — опционально, куда слать алерт

Запуск:
    python3 ingest/fb_apify.py           # обычный прогон (по таймеру)
    python3 ingest/fb_apify.py --dry-run # показать, что будет отправлено, без запуска актора
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SABAI_DATA_DIR") or (ROOT / "data"))
OUT_DIR = DATA_DIR / "dumps"
CONFIG = ROOT / "data" / "fb_sources.json"
STATE = DATA_DIR / "fb_state.json"

API = "https://api.apify.com/v2"
POLL_INTERVAL_SEC = 15
RUN_TIMEOUT_SEC = 1800          # 30 мин на прогон актора — дальше считаем зависшим
SILENCE_ALERT_AFTER = 2         # столько пустых прогонов подряд до алерта


# ─────────────────────────── состояние ───────────────────────────

def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("⚠️  fb_state.json битый — начинаю с чистого", file=sys.stderr)
    return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────── Apify API ───────────────────────────

def _req(url: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def run_actor(actor: str, payload: dict, token: str) -> list[dict]:
    """Запустить актор и дождаться результата. Возвращает элементы датасета."""
    start = _req(f"{API}/acts/{actor}/runs?token={token}", "POST", payload)["data"]
    run_id, dataset_id = start["id"], start["defaultDatasetId"]
    print(f"   запущен run {run_id}")

    deadline = time.time() + RUN_TIMEOUT_SEC
    status = start["status"]
    while status in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise TimeoutError(f"актор {actor} не завершился за {RUN_TIMEOUT_SEC}s (run {run_id})")
        time.sleep(POLL_INTERVAL_SEC)
        status = _req(f"{API}/actor-runs/{run_id}?token={token}")["data"]["status"]

    if status != "SUCCEEDED":
        raise RuntimeError(f"актор {actor} завершился со статусом {status} (run {run_id})")

    items = _req(f"{API}/datasets/{dataset_id}/items?token={token}&format=json&clean=true")
    return items if isinstance(items, list) else []


# ─────────────────────────── нормализация ───────────────────────────

def _first(d: dict, *keys: str) -> str | None:
    """Первое непустое значение из возможных названий поля.

    У акторов поля называются по-разному (и меняются между версиями),
    поэтому берём по списку синонимов, а не по одному имени.
    """
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def to_raw_post(item: dict, source: str) -> dict | None:
    """Элемент датасета Apify → формат raw_post (db/schema.sql), как у Telegram."""
    text = _first(item, "text", "message", "description", "postText", "title")
    if not text:
        return None  # объявление без текста парсить нечем

    url = _first(item, "url", "postUrl", "link", "listingUrl") or ""
    ext = _first(item, "id", "postId", "listingId", "facebookId")
    if not ext:
        if not url:
            return None  # без идентификатора не сможем дедуплицировать
        ext = url.rstrip("/").split("/")[-1]

    posted = _first(item, "time", "date", "publishedAt", "creationTime", "createdAt")
    price = item.get("price")
    if isinstance(price, dict):  # Marketplace отдаёт {amount, currency}
        amount = price.get("amount") or price.get("value")
        if amount:
            text = f"{text}\n\nЦена: {amount} {price.get('currency', '')}".strip()
    elif isinstance(price, (int, float, str)) and str(price).strip():
        text = f"{text}\n\nЦена: {price}"

    return {
        "source": source,
        "source_url": url,
        "external_id": f"{source}/{ext}",
        "posted_at": posted,
        "raw_text": text,
    }


# ─────────────────────────── запись ───────────────────────────

def load_seen() -> set[str]:
    seen: set[str] = set()
    if not OUT_DIR.exists():
        return seen
    for f in OUT_DIR.glob("*.ndjson"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["external_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return seen


def save_posts(posts: list[dict], name: str, seen: set[str]) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(OUT_DIR / f"{name}.ndjson", "a", encoding="utf-8") as f:
        for p in posts:
            if p["external_id"] in seen:
                continue
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
            seen.add(p["external_id"])
            written += 1
        f.flush()  # пайплайн сборки читает конкурентно — не держим в буфере
    return written


# ─────────────────────────── алерты ───────────────────────────

def alert(text: str) -> None:
    """Пуш в Telegram. Молчание сбора должно быть слышно, иначе витрина тихо протухнет."""
    token = os.getenv("SABAI_ALERT_BOT_TOKEN")
    chat = os.getenv("SABAI_ALERT_CHAT_ID")
    print(f"ALERT: {text}", file=sys.stderr)
    if not token or not chat:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode({"chat_id": chat, "text": f"SabaiStay FB: {text}"}).encode(),
            ),
            timeout=20,
        )
    except (urllib.error.URLError, OSError) as e:
        print(f"   не смог отправить алерт: {e}", file=sys.stderr)


# ─────────────────────────── основной поток ───────────────────────────

def render_input(template: dict, until_date: str, limit: int) -> dict:
    """Подставить {until_date} / {limit} в шаблон входа актора."""
    def walk(v):
        if isinstance(v, str):
            if v == "{limit}":
                return limit
            return v.replace("{until_date}", until_date).replace("{limit}", str(limit))
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(template)


def probe(urls: list[str], token: str) -> int:
    """Проверить группы по одной на маленьком лимите: отдаются ли посты вообще.

    Снаружи Facebook закрыт наглухо — даже публичная группа не отдаёт ничего без входа,
    поэтому «публичная или нет» не проверить ни curl'ом, ни глазами через инкогнито
    с уверенностью. Единственный честный ответ даёт сам актор. Прогон на 5 постов
    стоит копейки, а ошибка в конфиге иначе всплывёт через сутки пустых прогонов.
    """
    ok = 0
    for url in urls:
        clean = url.strip().rstrip("/").split("?")[0]
        print(f"\n→ {clean}")
        payload = {"startUrls": [{"url": clean}], "resultsLimit": 5}
        try:
            items = run_actor("apify~facebook-groups-scraper", payload, token)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, TimeoutError) as e:
            print(f"   ❌ не отдала данные ({type(e).__name__}: {e})")
            print("      закрытая группа, опечатка в адресе или актор её не тянет — не годится")
            continue

        posts = [p for p in (to_raw_post(i, "fb_group") for i in items) if p]
        if not items:
            print("   ❌ ноль постов — скорее всего группа закрытая, в сбор не берём")
            continue

        ok += 1
        print(f"   ✅ отдала {len(items)} постов, распознано как текст: {len(posts)}")
        for p in posts[:2]:
            preview = " ".join((p.get("raw_text") or "").split())[:110]
            print(f"      «{preview}»")
        if len(posts) < len(items) * 0.5:
            print("      ⚠️  много постов без текста — возможно, там одни фото без описания")
    return ok


def main() -> None:
    dry = "--dry-run" in sys.argv
    token = os.getenv("APIFY_TOKEN")
    if not token and not dry:
        sys.exit("Не задан APIFY_TOKEN — положи его в /home/rw/sabaistay-secrets/.env")

    # Проверка групп-кандидатов до правки конфига:
    #     python3 ingest/fb_apify.py --probe https://facebook.com/groups/xxx https://...
    if "--probe" in sys.argv:
        urls = [a for a in sys.argv[sys.argv.index("--probe") + 1:] if a.startswith("http")]
        if not urls:
            sys.exit("Укажи хотя бы один адрес группы после --probe")
        good = probe(urls, token)
        print(f"\nГодных групп: {good} из {len(urls)}")
        if good:
            print("Годные добавь в data/fb_sources.json → tasks[0].input.startUrls")
        return

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    defaults = cfg.get("defaults", {})
    limit = int(defaults.get("limit_per_run", 200))
    days_back = int(defaults.get("first_run_days_back", 14))

    state = load_state()
    seen = load_seen()
    today = datetime.now(timezone.utc).date()
    total_written = 0
    failures: list[str] = []

    for task in cfg.get("tasks", []):
        name = task["name"]
        if not task.get("enabled", False):
            print(f"— {name}: выключен в конфиге, пропускаю")
            continue

        st = state.setdefault(name, {})
        until = st.get("last_success") or str(today - timedelta(days=days_back))
        payload = render_input(task["input"], until, limit)

        print(f"→ {name}: актор {task['actor']}, инкремент с {until}")
        if dry:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue

        try:
            items = run_actor(task["actor"], payload, token)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, TimeoutError) as e:
            print(f"   ❌ {type(e).__name__}: {e}", file=sys.stderr)
            failures.append(f"{name}: {e}")
            st["consecutive_empty"] = st.get("consecutive_empty", 0) + 1
            continue

        posts = [p for p in (to_raw_post(i, task["source"]) for i in items) if p]
        written = save_posts(posts, name, seen)
        total_written += written
        print(f"   получено {len(items)}, распознано {len(posts)}, новых {written}")

        # Сторож тишины: пусто — считаем; что-то пришло — счётчик сбрасываем.
        if not items:
            st["consecutive_empty"] = st.get("consecutive_empty", 0) + 1
            if st["consecutive_empty"] >= SILENCE_ALERT_AFTER:
                alert(f"{name}: {st['consecutive_empty']} пустых прогонов подряд — похоже, актор сломался")
        else:
            st["consecutive_empty"] = 0
            st["last_success"] = str(today)

        # Отдельный сигнал: посты идут, а текста в них нет — сменилась схема актора.
        if items and len(posts) < len(items) * 0.3:
            alert(f"{name}: из {len(items)} элементов распозналось только {len(posts)} — проверь схему актора")

    save_state(state)

    if failures and not dry:
        alert("прогон с ошибками: " + "; ".join(failures[:3]))

    print(f"\nИтого новых постов: {total_written}")
    if total_written:
        print("Дальше их подхватит scripts/update_site.py (таймер sabaistay-build).")


if __name__ == "__main__":
    main()

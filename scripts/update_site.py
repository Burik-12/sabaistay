#!/usr/bin/env python3
"""
SabaiStay — пайплайн обновления витрины (запускается таймером sabaistay-build на VPS).

    dumps/*.ndjson (от коллектора telegram_telethon.py)
      → parse_lite  → merge по external_id в data/sample_listings.json
      → web/build.py → web/index.html
      → git pull --rebase / commit / push → Vercel авто-деплой (паттерн Bro)

Новые объявления пишутся в /tmp/sabai_new_batch.json — задел под нотификации
бота подписки (Фаза 5). Никаких платных вызовов, только stdlib + локальные модули.

Запуск вручную (проверка без git):  python3 scripts/update_site.py --no-git
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "parser"))

import parse_lite  # noqa: E402  — regex-парсер
import build       # noqa: E402  — генератор витрины (build.main())

DATA_DIR = Path(os.getenv("SABAI_DATA_DIR") or (ROOT / "data"))
DUMPS_DIR = DATA_DIR / "dumps"
LISTINGS = ROOT / "data" / "sample_listings.json"
NEW_BATCH = Path(os.getenv("SABAI_NEW_BATCH") or "/tmp/sabai_new_batch.json")
STATE_PATH = DATA_DIR / "collector_state.json"   # сторож тишины: сколько прогонов без новых постов
SILENCE_RUNS = 8                                 # таймер раз в 3 ч → ≈ сутки молчания


def read_dumps() -> list[dict]:
    """Собрать сырые посты из dumps/, дедуп по external_id.

    Поддержаны оба формата: *.ndjson (по объекту на строку — от коллектора) и
    *.json (JSON-массив — например разовый бэкфилл telegram_bulk_fetch.py).
    """
    posts: list[dict] = []
    seen: set[str] = set()
    if not DUMPS_DIR.exists():
        return posts

    def _add(p: dict) -> None:
        ext = p.get("external_id")
        if ext and ext not in seen:
            seen.add(ext)
            posts.append(p)

    for f in sorted(DUMPS_DIR.glob("*.ndjson")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _add(json.loads(line))
            except json.JSONDecodeError:
                continue  # частично записанная строка (конкурентная запись коллектора) — пропускаем
    for f in sorted(DUMPS_DIR.glob("*.json")):
        try:
            arr = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for p in (arr if isinstance(arr, list) else arr.get("listings", [])):
            _add(p)
    return posts


def merge(posts: list[dict]) -> tuple[int, list[dict]]:
    """Разобрать посты и влить новые (по external_id) в LISTINGS.

    Возвращает (сколько новых записей всего, список новых настоящих объявлений).
    """
    parsed = parse_lite.parse_all(posts)
    existing = json.loads(LISTINGS.read_text(encoding="utf-8")) if LISTINGS.exists() else {"listings": []}
    ex_list = existing.get("listings", []) if isinstance(existing, dict) else existing
    seen_ids = {p.get("external_id") for p in ex_list if p.get("external_id")}
    new_ones = [p for p in parsed if p.get("external_id") and p["external_id"] not in seen_ids]
    merged = ex_list + new_ones
    LISTINGS.write_text(json.dumps({"listings": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    new_listings = [p for p in new_ones if p.get("parsed", {}).get("is_listing")]
    return len(new_ones), new_listings


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def commit_push(n_listings: int) -> None:
    """pull --rebase → add → commit (если есть изменения) → push с одним ретраем."""
    git("pull", "--rebase", "--autostash")
    paths = ["data/sample_listings.json", "web/index.html"]
    if (ROOT / "data" / "own_listings.json").exists():
        paths.append("data/own_listings.json")
    if (ROOT / "web" / "img" / "own").exists():
        paths.append("web/img/own")
    git("add", *paths)
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("Изменений нет — коммит и пуш пропущены")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    git("commit", "-m", f"chore: update listings — {n_listings} объявлений ({stamp})")
    for attempt in range(2):
        r = git("push", "origin", "main")
        if r.returncode == 0:
            print("Запушено → Vercel задеплоит")
            return
        print(f"[push] попытка {attempt + 1} не удалась: {r.stderr.strip()}", file=sys.stderr)
        git("pull", "--rebase", "--autostash")
    print("[push] не удалось после ретрая — оставляю коммит локально", file=sys.stderr)


def alert(text: str) -> None:
    """Пуш в Telegram. Те же переменные, что у FB-сборщика — один канал тревог."""
    token = os.getenv("SABAI_ALERT_BOT_TOKEN")
    chat = os.getenv("SABAI_ALERT_CHAT_ID")
    print(f"ALERT: {text}", file=sys.stderr)
    if not token or not chat:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode({"chat_id": chat, "text": f"SabaiStay: {text}"}).encode(),
            ),
            timeout=20,
        )
    except (urllib.error.URLError, OSError) as e:
        print(f"[alert] не отправлен: {e}", file=sys.stderr)


def check_silence(n_new: int) -> None:
    """Сторож тишины: сбор, вставший молча, опаснее упавшего.

    Витрина продолжает выглядеть рабочей, а объявления протухают — именно так проект
    и простоял месяц со снимком от 27 июня. Считаем прогоны подряд без единого нового
    поста; при достижении порога шлём тревогу и повторяем её не чаще раза в сутки,
    чтобы не превратить сигнал в фон.
    """
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    now = datetime.now(timezone.utc)
    if n_new:
        state = {"empty_runs": 0, "last_new_at": now.isoformat()}
    else:
        state["empty_runs"] = int(state.get("empty_runs", 0)) + 1
        empty = state["empty_runs"]
        # Таймер ходит раз в 3 часа: 8 пустых прогонов ≈ сутки без единого объявления.
        if empty >= SILENCE_RUNS:
            last_alert = state.get("last_alert_at")
            stale_alert = True
            if last_alert:
                try:
                    stale_alert = (now - datetime.fromisoformat(last_alert)).total_seconds() > 86_400
                except ValueError:
                    pass
            if stale_alert:
                since = state.get("last_new_at", "неизвестно когда")
                alert(f"нет новых объявлений {empty} прогонов подряд (последние были {since[:16]}). "
                      "Проверь sabaistay-collector — возможно, отвалилась сессия Telegram.")
                state["last_alert_at"] = now.isoformat()

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Обновление витрины SabaiStay из dumps")
    ap.add_argument("--no-git", action="store_true", help="не коммитить/пушить (локальный прогон)")
    args = ap.parse_args()

    posts = read_dumps()
    print(f"Прочитано {len(posts)} постов из {DUMPS_DIR}")
    n_new, new_listings = merge(posts)
    print(f"Новых записей: {n_new} (из них объявлений: {len(new_listings)})")
    check_silence(n_new)

    build.main()  # → web/index.html

    NEW_BATCH.write_text(json.dumps(new_listings, ensure_ascii=False), encoding="utf-8")
    if new_listings:
        print(f"Пачка новых объявлений → {NEW_BATCH} (для нотификаций бота, Фаза 5)")

    if args.no_git:
        print("--no-git: git-шаги пропущены")
        return
    commit_push(len(new_listings))


if __name__ == "__main__":
    main()

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


def main() -> None:
    ap = argparse.ArgumentParser(description="Обновление витрины SabaiStay из dumps")
    ap.add_argument("--no-git", action="store_true", help="не коммитить/пушить (локальный прогон)")
    args = ap.parse_args()

    posts = read_dumps()
    print(f"Прочитано {len(posts)} постов из {DUMPS_DIR}")
    n_new, new_listings = merge(posts)
    print(f"Новых записей: {n_new} (из них объявлений: {len(new_listings)})")

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

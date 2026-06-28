#!/usr/bin/env python3
"""
SabaiStay — одноразовый массовый сбор истории Telegram-каналов.

В отличие от telegram_telethon.py (always-on userbot), этот скрипт скачивает
историю каналов за один запуск и выходит. Подходит для:
  - Mac: первоначальное заполнение (без VPS)
  - GitHub Actions: плановые обновления (session файл → GitHub Secret TG_SESSION_B64)
  - Разовый добор при пропуске userbot'а

Каналы с закрытым web-превью, которые берёт этот скрипт (и не берёт preview.py):
  rent_phangan (~8к участников, рус), adsphangan, phangan_rent, arenda_phangan

──────────────────────────────────────────────────────
ЗАПУСК НА MAC (первый раз — одноразовый дамп)
──────────────────────────────────────────────────────
  pip install telethon

  # Создай API-ключи на https://my.telegram.org → My Applications
  export TG_API_ID=1234567
  export TG_API_HASH=abcdef1234567890abcdef1234567890

  # Первый запуск попросит код из Telegram
  python3 ingest/telegram_bulk_fetch.py -o /tmp/tg_raw.json
  # Создастся файл sabaistay_bulk.session — НЕ коммить в git, он в .gitignore

  # Дальше: разобрать и слить с базой
  python3 parser/parse_lite.py /tmp/tg_raw.json --merge data/sample_listings.json -o data/sample_listings.json
  python3 web/build.py && git add data/ web/index.html && git commit -m "data: Telethon bulk fetch"

──────────────────────────────────────────────────────
GITHUB ACTIONS (автоматика каждые 6 часов)
──────────────────────────────────────────────────────
  1. Залогинься на Mac: python3 ingest/telegram_bulk_fetch.py ...
     (создаст sabaistay_bulk.session)
  2. Закодируй: base64 -i sabaistay_bulk.session | tr -d '\\n'  → скопируй
  3. GitHub → Settings → Secrets → New repository secret:
       TG_API_ID  = 1234567
       TG_API_HASH = abcdef...
       TG_SESSION_B64 = (вставь base64 из шага 2)
  4. Пуш → Actions автоматически переключится на Telethon-режим

──────────────────────────────────────────────────────
ОГРАНИЧЕНИЯ / ГИГИЕНА
──────────────────────────────────────────────────────
  - Отдельный аккаунт желателен (не личный, не бот RW) — на случай бана.
    Для первоначального разового дампа личный аккаунт допустим (read-only, без спама).
  - НЕ трогаем списки участников (GetParticipants) — за это банят.
  - Задержки WARMUP_SEC и CHANNEL_DELAY_SEC снижают риск FloodWait.
  - Серая зона Telegram ToS + PDPA: храним факты+ссылку, не строим датасеты персданных.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError
except ImportError:
    sys.exit("Установи зависимость: pip install telethon")

# Каналы по умолчанию — в порядке приоритета.
# koh_phangan_rent работает и через бесплатный метод, но включаем для полноты.
CHANNELS_DEFAULT = [
    "rent_phangan",      # флагман, рус, ~8к подписчиков — превью отключено
    "koh_phangan_rent",  # англ, превью открыто (тоже включаем для полноты)
    "adsphangan",        # общие объявления Пхангана
    "phangan_rent",      # аренда
    "arenda_phangan",    # аренда (рус)
]

WARMUP_SEC = 10       # пауза после логина перед первым запросом (снижает риск FloodWait)
CHANNEL_DELAY_SEC = 8  # пауза между каналами


def _to_raw_post(message, channel: str) -> dict:
    """Сообщение Telethon → формат raw_post (совместим с parse_lite.py)."""
    return {
        "source": "telegram",
        "source_url": f"https://t.me/{channel}/{message.id}",
        "external_id": f"{channel}/{message.id}",
        "posted_at": message.date.isoformat() if message.date else None,
        "raw_text": message.message or "",
    }


async def _fetch_channel(
    client: TelegramClient, channel: str, limit: int, min_id: int
) -> list[dict]:
    posts = []
    kwargs: dict = {"limit": limit}
    if min_id > 0:
        kwargs["min_id"] = min_id
    try:
        async for msg in client.iter_messages(channel, **kwargs):
            if msg.message and msg.message.strip():
                posts.append(_to_raw_post(msg, channel))
    except FloodWaitError as e:
        wait = e.seconds + 5
        print(f"[floodwait] {channel}: пауза {wait}s…", file=sys.stderr)
        await asyncio.sleep(wait)
        return await _fetch_channel(client, channel, limit, min_id)
    except Exception as e:
        print(f"[warn] {channel}: {e}", file=sys.stderr)
    return posts


async def run(args: argparse.Namespace) -> None:
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("Нужны переменные TG_API_ID и TG_API_HASH (https://my.telegram.org → My Applications)")

    session = os.getenv("TG_SESSION", "sabaistay_bulk")
    channels = args.channels if args.channels else CHANNELS_DEFAULT

    client = TelegramClient(session, int(api_id), api_hash)
    await client.start(phone=os.getenv("TG_PHONE"))
    print(f"Подключён ({session}). Разминка {WARMUP_SEC}s…", file=sys.stderr)
    await asyncio.sleep(WARMUP_SEC)

    all_posts: list[dict] = []
    for i, ch in enumerate(channels):
        print(f"  📥 {ch} (limit={args.limit}" + (f", min_id={args.min_id}" if args.min_id else "") + ")…", file=sys.stderr)
        posts = await _fetch_channel(client, ch, args.limit, args.min_id)
        print(f"     → {len(posts)} постов", file=sys.stderr)
        all_posts.extend(posts)
        if i < len(channels) - 1:
            await asyncio.sleep(CHANNEL_DELAY_SEC)

    await client.disconnect()

    # Дедуп по external_id (на случай пересечений между каналами)
    deduped = list({p["external_id"]: p for p in all_posts}.values())
    print(f"Итого: {len(deduped)} уникальных постов", file=sys.stderr)

    payload = json.dumps(deduped, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"Сохранено → {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(payload + "\n")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Одноразовый сбор истории Telegram-каналов для SabaiStay"
    )
    ap.add_argument(
        "channels", nargs="*",
        help="каналы (по умолчанию: CHANNELS_DEFAULT)"
    )
    ap.add_argument(
        "-o", "--out",
        help="выходной файл (иначе stdout)"
    )
    ap.add_argument(
        "--limit", type=int, default=200,
        help="максимум сообщений на канал (default=200; для первоначального дампа поставь 1000+)"
    )
    ap.add_argument(
        "--min-id", type=int, default=0, dest="min_id",
        help="пропустить сообщения с id ≤ min_id (для инкрементального добора)"
    )
    return ap.parse_args()


if __name__ == "__main__":
    asyncio.run(run(_parse_args()))

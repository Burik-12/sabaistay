#!/usr/bin/env python3
"""
SabaiStay — постоянный мониторинг Telegram-каналов через Telethon (Фаза 1).

Always-on userbot: realtime (events.NewMessage) + добор по min_id как страховка. Покрывает каналы с
ОТКЛЮЧЁННЫМ web-превью, которые не берёт бесплатный telegram_preview.py (rent_phangan и др.).

⚠️ ГИГИЕНА ПРОТИВ БАНА (см. data/sources.md, docs/roadmap.md):
- ОТДЕЛЬНЫЙ номер/SIM, НЕ личный и НЕ привязан к боевому боту RW. Бан этого аккаунта не должен ничего ломать.
- Прогреть аккаунт несколько дней до скрейпа; разминка при старте; задержки; FloodWait-backoff.
- НЕ трогать списки участников (GetParticipants) — за это банят. Нам нужны только посты.
- Серая зона ToS Telegram + PDPA: храним факты+ссылку, не строим датасеты персданных.

Запуск (Владимир подключит аккаунт, когда сделает проект платным):
    pip install telethon
    export TG_API_ID=...        # с my.telegram.org
    export TG_API_HASH=...
    export TG_SESSION=sabaistay # ОБЯЗАТЕЛЬНО (изоляция аккаунта); создаст sabaistay.session
    export TG_PHONE=+66...      # для неинтерактивного старта на VPS (иначе спросит в консоли)
    python3 telegram_telethon.py
Хостинг 24/7: дешёвый VPS (Hetzner ~€4 / Fly / Railway ~$5). Первый логин — на локальной машине,
потом перенести .session на сервер (в репозиторий НЕ коммитить — он в .gitignore).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
except ImportError:
    sys.exit("Нужен пакет telethon: pip install telethon")

CHANNELS = [
    "rent_phangan",     # ~8к, рус, флагман — превью отключено
    "koh_phangan_rent", # работает и через бесплатный метод, тут — для realtime
    "adsphangan",       # объявления
    "phangan_rent",
    "arenda_phangan",
]

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "dumps"
POLL_INTERVAL_SEC = 300          # добор истории раз в 5 мин (страховка к realtime)
BACKFILL_LIMIT = 100             # сколько последних постов подтянуть при старте
WARMUP_SEC = 15                  # пауза после логина перед первым запросом (анти-флуд)
CHANNEL_DELAY_SEC = 10           # пауза между каналами (анти-флуд)

_seen: set[str] = set()          # external_id уже сохранённых постов (дедуп realtime+poll)
_lock = asyncio.Lock()           # сериализация доступа к client.iter_messages


def to_raw_post(message, channel: str) -> dict:
    """Сообщение Telethon → формат таблицы raw_post (db/schema.sql)."""
    return {
        "source": "telegram",
        "source_url": f"https://t.me/{channel}/{message.id}",
        "external_id": f"{channel}/{message.id}",
        "posted_at": message.date.isoformat() if message.date else None,
        "raw_text": message.message or "",
    }


def load_seen() -> None:
    """Подтянуть уже сохранённые external_id из NDJSON, чтобы не дублировать после рестарта."""
    if not OUT_DIR.exists():
        return
    for f in OUT_DIR.glob("*.ndjson"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                _seen.add(json.loads(line)["external_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    if _seen:
        print(f"Загружено {len(_seen)} уже известных постов", file=sys.stderr)


def save(post: dict) -> None:
    """MVP-хранилище: NDJSON на канал, с дедупом. Позже — INSERT ... ON CONFLICT DO NOTHING в Postgres."""
    if not post["raw_text"].strip():
        return  # медиа-only пост без текста — пропускаем
    if post["external_id"] in _seen:
        return  # дубль (realtime + poll перекрываются)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    channel = post["external_id"].split("/")[0]
    with open(OUT_DIR / f"{channel}.ndjson", "a", encoding="utf-8") as f:
        f.write(json.dumps(post, ensure_ascii=False) + "\n")
    _seen.add(post["external_id"])


async def resolve_channels(client: TelegramClient) -> tuple[list, dict]:
    """Резолв username → entity (надёжнее, чем строки в events.NewMessage). Вернёт (entities, id→username)."""
    entities, id_to_name = [], {}
    for ch in CHANNELS:
        try:
            ent = await client.get_entity(ch)
            entities.append(ent)
            id_to_name[ent.id] = ch
        except Exception as e:
            print(f"[warn] не резолвится {ch}: {e}", file=sys.stderr)
        await asyncio.sleep(2)
    return entities, id_to_name


async def backfill(client: TelegramClient) -> None:
    """Подтянуть свежую историю при старте (catch-up). Медленно — анти-флуд."""
    for channel in CHANNELS:
        try:
            async with _lock:
                async for msg in client.iter_messages(channel, limit=BACKFILL_LIMIT):
                    save(to_raw_post(msg, channel))
        except FloodWaitError as e:
            print(f"[floodwait] {channel}: ждём {e.seconds}s", file=sys.stderr)
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[warn] backfill {channel}: {e}", file=sys.stderr)
        await asyncio.sleep(CHANNEL_DELAY_SEC)


async def poll_loop(client: TelegramClient) -> None:
    """Страховка: периодический добор новых сообщений по min_id (на случай пропуска событий)."""
    last_seen: dict[str, int] = {}
    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        for channel in CHANNELS:
            try:
                kw = {"min_id": last_seen[channel]} if channel in last_seen else {"limit": 20}
                async with _lock:
                    async for msg in client.iter_messages(channel, **kw):
                        save(to_raw_post(msg, channel))
                        last_seen[channel] = max(last_seen.get(channel, 0), msg.id)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                print(f"[warn] poll {channel}: {e}", file=sys.stderr)
            await asyncio.sleep(2)


async def main() -> None:
    api_id, api_hash = os.getenv("TG_API_ID"), os.getenv("TG_API_HASH")
    session = os.getenv("TG_SESSION")
    if not api_id or not api_hash:
        sys.exit("Заполни TG_API_ID и TG_API_HASH (с my.telegram.org)")
    if not session:
        sys.exit("Заполни TG_SESSION (имя файла сессии — изоляция аккаунта)")

    client = TelegramClient(session, int(api_id), api_hash)
    try:
        await client.start(phone=os.getenv("TG_PHONE"))  # на VPS задай TG_PHONE, иначе спросит в консоли
    except Exception as e:
        sys.exit(f"[error] логин не удался: {e}. Сначала авторизуйся на локальной машине, перенеси .session.")

    print(f"Подключён. Разминка {WARMUP_SEC}s…", file=sys.stderr)
    await asyncio.sleep(WARMUP_SEC)

    entities, id_to_name = await resolve_channels(client)

    @client.on(events.NewMessage(chats=entities))
    async def on_new(event):           # realtime: новый пост в любом из каналов
        if event.chat is None:
            return                     # защита: chat может быть None
        channel = id_to_name.get(event.chat_id) or getattr(event.chat, "username", None) or str(event.chat_id)
        save(to_raw_post(event.message, channel))

    load_seen()
    print("Бэкафилл…", file=sys.stderr)
    await backfill(client)
    print(f"Слушаю {len(entities)} каналов realtime + добор каждые {POLL_INTERVAL_SEC}s", file=sys.stderr)

    loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT):       # graceful shutdown на рестарт/деплой VPS
        try:
            loop.add_signal_handler(s, lambda: asyncio.ensure_future(client.disconnect()))
        except NotImplementedError:
            pass

    poller = asyncio.create_task(poll_loop(client))
    try:
        await client.run_until_disconnected()
    finally:
        poller.cancel()
        if client.is_connected():
            await client.disconnect()
        print("Отключён.", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())

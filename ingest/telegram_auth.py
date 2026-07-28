#!/usr/bin/env python3
"""
SabaiStay — одноразовая авторизация Telethon-аккаунта сборщика.

Запускается ОДИН РАЗ вручную (нужен код из Telegram, который приходит на телефон).
После успеха создаётся файл сессии, и коллектор дальше работает сам, без участия
человека — systemd поднимает его при перезагрузке VPS.

Что делает:
  1. проверяет, что заданы TG_API_ID / TG_API_HASH / TG_PHONE / TG_SESSION;
  2. логинится (спросит код из Telegram, при 2FA — пароль);
  3. проверяет доступность каналов из CHANNELS и печатает отчёт
     (какие живые, какие не существуют/закрыты — чтобы не держать мусор в списке);
  4. подсказывает следующий шаг.

Запуск на VPS (одной командой):
    ssh root@rw-vps
    sudo -u rw env $(grep -v '^#' /home/rw/sabaistay-secrets/.env | xargs) \
        /home/rw/sabaistay/venv/bin/python /home/rw/sabaistay/ingest/telegram_auth.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

try:
    from telethon import TelegramClient
    from telethon.errors import (
        ChannelPrivateError,
        FloodWaitError,
        SessionPasswordNeededError,
        UsernameNotOccupiedError,
    )
except ImportError:
    sys.exit("Нет telethon. Установи: venv/bin/pip install telethon")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from telegram_telethon import CHANNELS  # единый список каналов — не дублируем


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        sys.exit(f"Не задана переменная {name} — проверь /home/rw/sabaistay-secrets/.env")
    return val


async def main() -> None:
    api_id = int(_require("TG_API_ID"))
    api_hash = _require("TG_API_HASH")
    session = _require("TG_SESSION")
    phone = os.getenv("TG_PHONE")

    session_file = Path(f"{session}.session")
    if session_file.exists():
        print(f"⚠️  Сессия уже есть: {session_file}")
        print("   Если хочешь перелогиниться — удали файл и запусти скрипт снова.\n")

    client = TelegramClient(session, api_id, api_hash)

    print("→ Подключаюсь к Telegram…")
    await client.connect()

    if not await client.is_user_authorized():
        if not phone:
            sys.exit("Не задан TG_PHONE — добавь его в .env (формат +66…)")
        print(f"→ Отправляю код на {phone} (придёт в приложение Telegram, не по SMS)")
        await client.send_code_request(phone)
        code = input("Введи код из Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            import getpass

            pwd = getpass.getpass("Включена двухфакторка. Пароль: ")
            await client.sign_in(password=pwd)

    me = await client.get_me()
    print(f"\n✅ Авторизован: {me.first_name} (@{me.username or 'без юзернейма'}, id={me.id})")
    print(f"   Сессия сохранена: {session_file.resolve()}\n")

    print("→ Проверяю каналы (это же покажет, какие из списка мертвы):")
    alive, dead = [], []
    for ch in CHANNELS:
        try:
            entity = await client.get_entity(ch)
            count = 0
            async for _ in client.iter_messages(entity, limit=1):
                count += 1
            title = getattr(entity, "title", ch)
            status = "есть посты" if count else "пусто"
            print(f"   ✅ {ch:<20} {title[:34]:<36} {status}")
            alive.append(ch)
        except (UsernameNotOccupiedError, ValueError):
            print(f"   ❌ {ch:<20} не существует")
            dead.append(ch)
        except ChannelPrivateError:
            print(f"   🔒 {ch:<20} закрыт (нужна подписка этим аккаунтом)")
            dead.append(ch)
        except FloodWaitError as e:
            print(f"   ⏳ {ch:<20} FloodWait {e.seconds}s — пропускаю")
        except Exception as e:  # noqa: BLE001 — отчёт важнее строгой типизации
            print(f"   ⚠️  {ch:<20} {type(e).__name__}: {e}")
            dead.append(ch)
        await asyncio.sleep(2)  # анти-флуд: не частим запросами

    await client.disconnect()

    print(f"\nИтог: живых каналов {len(alive)} из {len(CHANNELS)}")
    if dead:
        print(f"Мёртвые/недоступные (стоит убрать из CHANNELS): {', '.join(dead)}")
    print(
        "\nДальше — запустить сбор:\n"
        "    systemctl enable --now sabaistay-collector.service sabaistay-build.timer\n"
        "    journalctl -u sabaistay-collector -f   # видно живые посты\n"
    )


if __name__ == "__main__":
    asyncio.run(main())

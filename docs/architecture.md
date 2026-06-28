# Архитектура SabaiStay

## Поток данных

```
Facebook (Apify actor, расписание) ─┐
Telegram (Telethon 24/7 / t.me/s)  ─┼─► RAW (сырьё) ─► Claude-парсер ─► нормализация района ─► дедуп ─► Postgres ─► витрина (Next.js + Leaflet)
Airbnb/Booking (коллекторы, потом) ─┘     (очередь)     (текст→JSON)      (газеттир + fuzzy)    (pHash+   │
                                                                                              сигнатура)  └─► бот «подписка на фильтр» (пуши)
```

## Компоненты

### 1. Ingest (сбор)
- **Facebook** — главный источник. Managed-актор на Apify по публичным группам + поиску Marketplace «Koh Phangan», по расписанию (каждые 3-6 ч). Для закрытых групп — cookie «расходного» аккаунта + резидентный тайский прокси. См. `ingest/README.md`.
- **Telegram** — Telethon userbot 24/7 на VPS: `events.NewMessage` по списку каналов + добор по `min_id` как страховка. Бесплатный старт — `ingest/telegram_preview.py` (парсит `t.me/s/<channel>` без аккаунта).
- Каждый сырой пост сохраняется с `source`, `source_url`, `source_posted_at`, `raw_text` (временно, для разбора), вложения по ссылкам.

### 2. Парсер (текст → поля)
- Один вызов Claude на объявление со **строгой JSON-схемой** (`parser/listing_schema.json`) → гарантированно валидный JSON. Дёшево: Batch API + кеш схемы/газеттира, сотни объявлений/мес < $5.
- Модель: Haiku 4.5 для объёма, Sonnet — если плохо читает тайский/смешанный текст.
- Промпт и правила — `parser/prompt.md`.

### 3. Нормализация района
- Не доверять только LLM. Свободное название → канон по газеттиру `data/districts.json` (алиасы: Sri Thanu/Srithanu, Ban Tai/Baan Tai, Chaloklum/Chalok Lam…) → координаты центроида для карты.

### 4. Дедуп
- Одна вилла у разных агентов — норма рынка. Ключи: perceptual-hash фото (dHash/pHash, Hamming ≤4-5) + сигнатура (район + спальни + ценовой бакет).
- Не выкидываем, а **группируем** в `dedup_group` → на карточке «найдено в N источниках» со всеми ссылками.

### 5. База
- Postgres (Neon). Одна основная таблица `listing` + `raw_post` для сырья. Схема — `db/schema.sql`.
- **Свежесть:** `first_seen`/`last_seen`, авто-expire по `last_seen`, статусы `active/rented/expired`. LLM ловит «сдано/rented/taken».

### 6. Витрина
- Форк страницы `/listings` из RW web (фильтр-бар + карта Leaflet с кластерами + шортлист + NL-поиск на Claude), перекрашенный под бренд. Хостинг — отдельный Vercel-проект, отдельный домен.

### 7. Бот «подписка на фильтр» (киллер-фича)
- На базе `intake_bot.py` из RW (python-telegram-bot). Юзер задаёт критерии → пуш «новый дом по твоему фильтру» со ссылкой. Поверх той же базы.

### 8. Сравнение с рынком (потом)
- Коллекторы `rental-market-ci` из RW дают медианы Airbnb/Booking/Agoda по районам → на карточке «на X% ниже/выше медианы района».

## Отделение от RW (decoupling)
- Отдельный репозиторий (этот). Код-ядро **скопировано**, не подключено вживую.
- НЕ тащить: amoCRM-интеграцию, мастер-базу объектов RW, любые приватные данные RW.
- Отдельная Neon-база, отдельный Vercel-проект, отдельный домен, отдельные ключи/секреты.

## Технологический стек
- **Backend/ingest:** Python (Telethon, парсеры, вызовы Claude). 
- **БД:** Postgres (Neon).
- **Витрина:** Next.js 15 + Vercel, Leaflet/react-leaflet-cluster.
- **Бот:** python-telegram-bot.
- **LLM:** Claude (структурный вывод + NL-поиск).
- **FB-сбор:** Apify managed actor + резидентный прокси.
- **TG-сбор:** Telethon на VPS (Hetzner/Fly/Railway).

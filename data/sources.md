# Источники — каналы Telegram (статус сбора)

Обновлено: 28-06-2026.

## Статус web-превью (`t.me/s/<channel>`, без аккаунта)

| Канал | Сообщений | Контент | Статус |
|---|---|---|---|
| `koh_phangan_rent` | ~20/стр | аренда, англ | ✅ **работает**, 15 стр → ~47 объявлений |
| `phangan_villas` | 36 | инвест-контент (не аренда) | ⚛️ открыто, но не полезно |
| `rent_phangan` | — | аренда, рус (~8к) — **главный** | 🔒 превью закрыто → нужен Telethon |
| `adsphangan` | — | объявления Пхангана | 🔒 превью закрыто |
| `phangan_rent` | — | аренда | 🔒 превью закрыто |
| `arenda_phangan` | — | аренда, рус | 🔒 превью закрыто |
| `ru_phangan` | — | общий рус-канал | 🔒 превью закрыто |
| `kohphangan_rent` | — | новый пустой канал | ❌ только "Channel created" |
| `phangan_real_estate` | — | новый пустой канал | ❌ только "Channel created" |
| `phanganthai` | — | переименованный канал | ❌ не релевантно |

Проверено 29 каналов по разным паттернам именования — все либо закрыты, либо не содержат арендных объявлений, кроме `koh_phangan_rent`.

## Вывод и план

**Без аккаунта (Режим B):** только `koh_phangan_rent`, ~40-50 объявлений/мес. Уже подключено, обновляется каждые 6 часов через GitHub Actions.

**С аккаунтом Telethon (Режим A):** `rent_phangan` (~8к подписчиков, рус) + 4 других канала → ожидаемый прирост в 5-10x объявлений. Скрипт готов: `ingest/telegram_bulk_fetch.py`. Требуется разово подключить аккаунт.

## Как подключить Telethon (Режим A)

### Шаг 1 — API-ключи (5 минут)
1. Открой [my.telegram.org](https://my.telegram.org) → войди своим Telegram
2. My Applications → Create new application → заполни любые данные
3. Скопируй `api_id` (число) и `api_hash` (строка)

### Шаг 2 — Первый логин на Mac
```bash
pip install telethon

export TG_API_ID=1234567
export TG_API_HASH=abcdef1234567890abcdef1234567890

# Запустить: введёт код из Telegram
python3 ingest/telegram_bulk_fetch.py -o /tmp/tg_first_dump.json
```
После ввода кода создастся `sabaistay_bulk.session` — это файл авторизации.

### Шаг 3 — Залить данные в базу (первый большой дамп)
```bash
# Первый дамп — берём побольше (1000 последних постов с каждого канала)
python3 ingest/telegram_bulk_fetch.py --limit 1000 -o /tmp/tg_first_dump.json

python3 parser/parse_lite.py /tmp/tg_first_dump.json \
  --merge data/sample_listings.json \
  -o data/sample_listings.json

python3 web/build.py
git add data/sample_listings.json web/index.html
git commit -m "data: первый Telethon дамп — rent_phangan"
git push
```

### Шаг 4 — GitHub Actions (автоматика)
```bash
# Закодируй session файл
base64 -i sabaistay_bulk.session | tr -d '\n'  # скопируй вывод
```
Затем в GitHub → Settings → Secrets and variables → Actions:
- `TG_API_ID` = 1234567
- `TG_API_HASH` = abcdef...
- `TG_SESSION_B64` = (вставить base64 из команды выше)

После этого GitHub Actions автоматически переключится на Режим A.

### Примечание об аккаунте
Для разового дампа личный аккаунт подходит (скрипт только читает, не пишет).
Для постоянного Режима A (Actions каждые 6 часов) — желателен отдельный аккаунт
на тайской SIM-карте, чтобы возможный бан не затронул личный аккаунт.

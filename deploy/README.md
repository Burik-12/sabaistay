# Деплой SabaiStay на VPS (автосбор 24/7)

Автосбор Telegram живёт на `rw-vps` рядом с ботами Bro/Клуба. Схема (паттерн Bro):

```
sabaistay-collector.service   Telethon 24/7 → data/dumps/*.ndjson
sabaistay-build.timer (3ч)  → scripts/update_site.py: parse → build → git push → Vercel
```

Код синкается через GitHub (VPS коммитит своим deploy-key). Приватные данные
(сессия, .env, подписки) лежат **вне** репозитория в `/home/rw/sabaistay-secrets/`.

## Что нужно от Владимира (интерактивные шаги)

1. **TG API-ключи** — https://my.telegram.org → API development tools → `TG_API_ID`, `TG_API_HASH`.
2. **Телефон аккаунта-сборщика** (`TG_PHONE`) — желательно отдельная SIM, не личный аккаунт RW.
3. **Авторизация Telethon** — при первом запуске Telegram пришлёт код; ввести его один раз
   (создаст файл `.session`). Дальше сессия живёт сама.
4. **Deploy key в GitHub** — добавить публичный ключ VPS в Settings → Deploy keys (✓ write).

Остальное (venv, юниты, клон, таймеры) — механическое, ставится по шагам ниже.

## Раскладка (root@rw-vps)

```bash
# 1. Deploy-ключ VPS (по образцу github-bro)
sudo -u rw ssh-keygen -t ed25519 -f /home/rw/.ssh/sabai_deploy_ed25519 -N '' -C sabaistay-vps
cat /home/rw/.ssh/sabai_deploy_ed25519.pub    # → GitHub Burik-12/sabaistay → Deploy keys (write)

# ~/.ssh/config (от имени rw) — добавить блок:
#   Host github-sabai
#     HostName github.com
#     User git
#     IdentityFile ~/.ssh/sabai_deploy_ed25519
#     IdentitiesOnly yes

# 2. Клон + venv
sudo -u rw git clone git@github-sabai:Burik-12/sabaistay.git /home/rw/sabaistay
sudo -u rw git -C /home/rw/sabaistay config user.name  "sabaistay-vps"
sudo -u rw git -C /home/rw/sabaistay config user.email "bot@sabaistay.co"
sudo -u rw python3 -m venv /home/rw/sabaistay/venv
sudo -u rw /home/rw/sabaistay/venv/bin/pip install -q telethon
# (python-telegram-bot добавится в Фазе 5 — боту)

# 3. Секреты
sudo -u rw mkdir -p /home/rw/sabaistay-secrets
sudo -u rw cp /home/rw/sabaistay/deploy/env.example /home/rw/sabaistay-secrets/.env
sudo -u rw nano /home/rw/sabaistay-secrets/.env        # заполнить TG_* значениями
chmod 600 /home/rw/sabaistay-secrets/.env

# 4. Первичная авторизация Telethon (интерактивно, введёшь код из Telegram)
sudo -u rw bash -lc 'set -a; source /home/rw/sabaistay-secrets/.env; set +a; \
  /home/rw/sabaistay/venv/bin/python /home/rw/sabaistay/ingest/telegram_telethon.py'
#   Дождись «Слушаю N каналов…», затем Ctrl-C — сессия создана.

# 5. Разовый бэкфилл истории (закрыть разрыв 29-06 → сейчас)
sudo -u rw bash -lc 'set -a; source /home/rw/sabaistay-secrets/.env; set +a; \
  /home/rw/sabaistay/venv/bin/python /home/rw/sabaistay/ingest/telegram_bulk_fetch.py \
    --limit 500 -o /home/rw/sabaistay/data/dumps/backfill.json'
# update_site.read_dumps() читает и *.json-массивы, и *.ndjson — файл подхватится.
sudo -u rw /home/rw/sabaistay/venv/bin/python /home/rw/sabaistay/scripts/update_site.py --no-git

# 6. Юниты
cp /home/rw/sabaistay/deploy/systemd/sabaistay-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sabaistay-collector.service sabaistay-build.timer

# 7. Проверка
journalctl -u sabaistay-collector -f          # видим живые посты
systemctl list-timers | grep sabaistay
systemctl start sabaistay-build.service && journalctl -u sabaistay-build -n 30
```

## Заметки

- **Форматы dumps**: коллектор пишет `*.ndjson` (по объекту на строку), `telegram_bulk_fetch.py`
  — `*.json` (массив). `update_site.read_dumps()` читает оба и дедупит по `external_id`.
  Плюс коллектор при старте сам делает `backfill()` по всем каналам — так что даже без
  ручного бэкфилла свежая история подтянется.
- **Python 3.14**: проверено — Telethon 1.44 ставится и импортируется (2026-07-16).
- **Изоляция от RW**: отдельный репо, отдельный deploy-key, отдельные секреты. Инфра общая.
- **Подписки бота** (`data/subscriptions.ndjson`, Фаза 5) — только на VPS, добавить в `~/backups`.

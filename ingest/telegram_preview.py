#!/usr/bin/env python3
"""
SabaiStay — бесплатный сбор объявлений из публичного Telegram-канала через web-превью
t.me/s/<channel>. БЕЗ аккаунта, БЕЗ ключей, БЕЗ затрат. Только стандартная библиотека.

Это стартовый/тестовый сборщик (Фаза 1). Для realtime и чтения чатов позже — Telethon
userbot 24/7 (см. docs/roadmap.md). Здесь — чтобы быстро набрать первые объявления и
проверить парсер на живых данных.

Использование:
    python3 telegram_preview.py rent_phangan                 # последние посты
    python3 telegram_preview.py rent_phangan --pages 5        # листать вглубь (5 страниц)
    python3 telegram_preview.py rent_phangan -o posts.json    # сохранить в файл

Выход: JSON-массив постов с полями source, source_url, external_id, posted_at, raw_text.
Формат совпадает с таблицей raw_post (db/schema.sql) — можно лить прямо в базу.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Границы «пузыря» сообщения в разметке t.me/s
_WRAP_SPLIT = re.compile(r'tgme_widget_message_wrap')
_DATA_POST = re.compile(r'data-post="([^"]+)"')
_TEXT_DIV = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _clean(html_fragment: str) -> str:
    """HTML-фрагмент текста поста → чистый текст с переносами строк."""
    text = _BR.sub("\n", html_fragment)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    # схлопнуть лишние пустые строки/пробелы
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_channel_html(page_html: str) -> list[dict]:
    """Вытащить посты из одной страницы t.me/s/<channel>."""
    posts: list[dict] = []
    chunks = _WRAP_SPLIT.split(page_html)
    for chunk in chunks:
        m_post = _DATA_POST.search(chunk)
        if not m_post:
            continue
        external_id = m_post.group(1)  # формат: "channel/12345"
        m_text = _TEXT_DIV.search(chunk)
        if not m_text:
            continue  # медиа-only пост без текста — пропускаем
        text = _clean(m_text.group(1))
        if not text:
            continue
        m_time = _TIME.search(chunk)
        posts.append(
            {
                "source": "telegram",
                "source_url": f"https://t.me/{external_id}",
                "external_id": external_id,
                "posted_at": m_time.group(1) if m_time else None,
                "raw_text": text,
            }
        )
    return posts


def _min_msg_id(posts: list[dict]) -> int | None:
    ids = []
    for p in posts:
        try:
            ids.append(int(p["external_id"].split("/")[-1]))
        except (ValueError, IndexError):
            pass
    return min(ids) if ids else None


def scrape(channel: str, pages: int = 1, delay: float = 1.5) -> list[dict]:
    """Собрать посты, листая до `pages` страниц вглубь истории."""
    channel = channel.lstrip("@").strip()
    seen: dict[str, dict] = {}
    before: int | None = None
    for i in range(pages):
        url = f"https://t.me/s/{channel}"
        if before is not None:
            url += f"?before={before}"
        try:
            page_html = _fetch(url)
        except urllib.error.URLError as e:
            print(f"[warn] страница {i + 1}: {e}", file=sys.stderr)
            break
        page_posts = parse_channel_html(page_html)
        if not page_posts:
            break
        for p in page_posts:
            seen[p["external_id"]] = p
        nxt = _min_msg_id(page_posts)
        if nxt is None or nxt == before:
            break
        before = nxt
        if i < pages - 1:
            time.sleep(delay)  # вежливость к серверу
    # новые сверху
    return sorted(seen.values(), key=lambda p: p["external_id"], reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Бесплатный сбор постов из t.me/s/<channel>")
    ap.add_argument("channel", help="username канала, напр. rent_phangan")
    ap.add_argument("--pages", type=int, default=1, help="сколько страниц листать вглубь")
    ap.add_argument("-o", "--out", help="файл для сохранения JSON (иначе stdout)")
    args = ap.parse_args()

    posts = scrape(args.channel, pages=args.pages)
    payload = json.dumps(posts, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"Сохранено {len(posts)} постов → {args.out}", file=sys.stderr)
    else:
        print(payload)
    print(f"[ok] собрано {len(posts)} постов из @{args.channel}", file=sys.stderr)


if __name__ == "__main__":
    main()

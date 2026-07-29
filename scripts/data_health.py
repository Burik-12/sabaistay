#!/usr/bin/env python3
"""
Здоровье базы SabaiStay: свежесть, полнота разбора, дубли, источники.

Витрина умеет выглядеть работающей на мёртвых данных — карточки на месте, фильтры
нажимаются, и понять, что последний живой пост был месяц назад, можно только вчитавшись
в даты. Этот отчёт делает состояние базы измеримым: одна команда, одна страница цифр.

    python3 scripts/data_health.py            # отчёт в консоль
    python3 scripts/data_health.py --json     # то же машинно (для бота/дашборда)
    python3 scripts/data_health.py --alert    # молчит, пока всё в порядке; иначе пишет в Telegram

Ключевые пороги вынесены в константы: их стоит менять по мере роста базы, а не спорить
с ними в голове каждый раз.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

from dedup import dedup          # noqa: E402
from freshness import assess     # noqa: E402

LISTINGS = ROOT / "data" / "sample_listings.json"

# Пороги тревоги
MAX_SILENCE_DAYS = 3      # дольше — сбор, скорее всего, встал
MIN_FRESH_SHARE = 0.10    # доля объявлений не старше недели
MIN_DISTRICT_SHARE = 0.60  # доля с распознанным районом (без него объект не попадает на карту)
MIN_PRICE_SHARE = 0.80    # доля с ценой


def _load() -> list[dict]:
    data = json.loads(LISTINGS.read_text(encoding="utf-8"))
    rows = data["listings"] if isinstance(data, dict) else data
    return [r for r in rows if (r.get("parsed") or {}).get("is_listing")]


def collect(rows: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    total = len(rows)
    if not total:
        return {"total": 0, "problems": ["база пуста"]}

    ages = []
    for r in rows:
        fresh = assess(r.get("posted_at"), (r.get("parsed") or {}).get("status_hint"), today)
        ages.append(fresh)

    def share(pred) -> float:
        return sum(1 for r in rows if pred(r.get("parsed") or {})) / total

    newest = max((r.get("posted_at") or "" for r in rows), default="")
    silence_days = (today - date.fromisoformat(newest[:10])).days if newest else None

    groups = dedup(rows)
    fresh_week = sum(1 for f in ages if f["age_days"] is not None and f["age_days"] <= 7)

    stats = {
        "total": total,
        "objects_after_dedup": len(groups),
        "duplicate_groups": sum(1 for g in groups if g["size"] > 1),
        "newest_post": newest[:10],
        "silence_days": silence_days,
        "fresh_week": fresh_week,
        "fresh_week_share": round(fresh_week / total, 3),
        "stale": sum(1 for f in ages if f["stale"]),
        "rented": sum(1 for f in ages if f["status"] == "rented"),
        "with_district": round(share(lambda p: bool(p.get("district_canonical"))), 3),
        "with_price": round(share(lambda p: p.get("price_amount") is not None), 3),
        "with_period": round(share(lambda p: p.get("price_period") in ("month", "night", "week")), 3),
        "with_bedrooms": round(share(lambda p: p.get("bedrooms") is not None), 3),
        "with_area": round(share(lambda p: p.get("area_sqm") is not None), 3),
        "by_source": dict(Counter(r.get("source") or "?" for r in rows).most_common()),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    problems = []
    if silence_days is not None and silence_days > MAX_SILENCE_DAYS:
        problems.append(f"нет новых объявлений {silence_days} дн. (последнее {stats['newest_post']}) — проверь сбор")
    if stats["fresh_week_share"] < MIN_FRESH_SHARE:
        problems.append(f"свежих за неделю всего {fresh_week} из {total} — витрине почти нечего показать")
    if stats["with_district"] < MIN_DISTRICT_SHARE:
        problems.append(f"район распознан у {stats['with_district']:.0%} — остальные не попадают на карту")
    if stats["with_price"] < MIN_PRICE_SHARE:
        problems.append(f"цена есть у {stats['with_price']:.0%} — фильтр по цене работает вслепую")
    stats["problems"] = problems
    return stats


def _bar(share: float, width: int = 22) -> str:
    filled = round(share * width)
    return "█" * filled + "·" * (width - filled)


def report(s: dict) -> str:
    if not s.get("total"):
        return "База пуста."
    lines = [
        "СОСТОЯНИЕ БАЗЫ SABAISTAY",
        "",
        f"  объявлений:        {s['total']}",
        f"  объектов (дедуп):  {s['objects_after_dedup']}  (склеено групп: {s['duplicate_groups']})",
        f"  последний пост:    {s['newest_post']}"
        + (f"  — {s['silence_days']} дн. назад" if s["silence_days"] is not None else ""),
        f"  свежих за неделю:  {s['fresh_week']}",
        f"  протухших:         {s['stale']}   помечено «сдано»: {s['rented']}",
        "",
        "  ПОЛНОТА РАЗБОРА",
    ]
    for label, key in (("район", "with_district"), ("цена", "with_price"), ("период", "with_period"),
                       ("спальни", "with_bedrooms"), ("площадь", "with_area")):
        lines.append(f"    {label:9} {_bar(s[key])} {s[key]:.0%}")
    lines += ["", "  ИСТОЧНИКИ"]
    for src, n in s["by_source"].items():
        lines.append(f"    {src:16} {n}")
    if s["problems"]:
        lines += ["", "  ⚠️  ТРЕБУЕТ ВНИМАНИЯ"]
        lines += [f"    • {p}" for p in s["problems"]]
    else:
        lines += ["", "  ✓ отклонений нет"]
    return "\n".join(lines)


def alert(text: str) -> None:
    """Тревога в Telegram — те же переменные, что у сборщиков: один канал на все сигналы."""
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Отчёт о здоровье базы SabaiStay")
    ap.add_argument("--json", action="store_true", help="вывести машинно-читаемый JSON")
    ap.add_argument("--alert", action="store_true", help="отправить тревогу, если есть проблемы")
    args = ap.parse_args()

    stats = collect(_load())

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(report(stats))

    if args.alert and stats.get("problems"):
        alert("состояние базы — " + "; ".join(stats["problems"]))
    # Ненулевой код возврата, чтобы таймер/CI мог отличить «всё хорошо» от «есть проблемы».
    sys.exit(1 if stats.get("problems") else 0)


if __name__ == "__main__":
    main()

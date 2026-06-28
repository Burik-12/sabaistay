#!/usr/bin/env python3
"""
SabaiStay — свежесть и авто-expire объявлений (Фаза 4).

Аренда «живёт» недолго: дом сдают за дни-недели, а старый пост так и висит. Чтобы витрина
не показывала мертвечину, каждому объявлению считаем возраст и статус:
    active  — свежее/актуальное (показываем);
    rented  — парсер увидел «сдано/занято» (status_hint) — прячем;
    expired — слишком старое, скорее всего уже не актуально — прячем по умолчанию.

ВАЖНО про точку отсчёта:
  • ПРОД: now = реальное «сегодня», а возраст лучше считать от last_seen (когда последний раз
    видели пост живым), а не от даты публикации — перепост продлевает жизнь.
  • ДЕМО (статичная витрина из исторического скрейпа): now = дата последнего поста в выборке
    («снимок»), иначе годовалые демо-данные все разом стали бы «неактуально» и фича была бы не видна.

Чистый Python, без сети. Самотест:  python3 freshness.py
"""
from __future__ import annotations

from datetime import date

FRESH_DAYS = 7        # ≤ — «свежее»
RECENT_DAYS = 30      # ≤ — «N дней назад»
EXPIRE_DAYS = 45      # > — считаем неактуальным (auto-expire)


def _parse(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def assess(posted_at, status_hint: str | None = None, now=None) -> dict:
    """Возраст + статус + человеческая метка. cls — класс для бейджа витрины (fresh/recent/old/stale/unknown)."""
    today = _parse(now) or date.today()

    if status_hint == "rented":
        return {"status": "rented", "age_days": None, "label": "снято", "cls": "stale", "stale": True}

    posted = _parse(posted_at)
    if posted is None:
        return {"status": "active", "age_days": None, "label": "дата неизвестна", "cls": "unknown", "stale": False}

    age = max(0, (today - posted).days)
    if age > EXPIRE_DAYS:
        return {"status": "expired", "age_days": age, "label": "🔴 неактуально", "cls": "stale", "stale": True}
    if age <= FRESH_DAYS:
        label = "сегодня" if age == 0 else ("вчера" if age == 1 else "🟢 свежее")
        return {"status": "active", "age_days": age, "label": label, "cls": "fresh", "stale": False}
    if age <= RECENT_DAYS:
        return {"status": "active", "age_days": age, "label": f"{age} дн. назад", "cls": "recent", "stale": False}
    weeks = round(age / 7)
    return {"status": "active", "age_days": age, "label": f"{weeks} нед. назад", "cls": "old", "stale": False}


# ─────────────────────────── самотест ───────────────────────────
def _selftest() -> None:
    now = date(2025, 8, 1)
    cases = [
        ("2025-08-01", None, "active", "fresh"),
        ("2025-07-31", None, "active", "fresh"),      # вчера
        ("2025-07-20", None, "active", "recent"),     # 12 дней
        ("2025-07-01", None, "active", "old"),        # 31 день
        ("2025-05-01", None, "expired", "stale"),     # 92 дня → expired
        ("2025-07-30", "rented", "rented", "stale"),  # status_hint перебивает возраст
        (None, None, "active", "unknown"),
    ]
    for posted, hint, exp_status, exp_cls in cases:
        r = assess(posted, hint, now)
        assert r["status"] == exp_status, f"{posted}: статус {r['status']} ≠ {exp_status}"
        assert r["cls"] == exp_cls, f"{posted}: cls {r['cls']} ≠ {exp_cls}"
        print(f"  {str(posted):12} {str(hint):7} → {r['status']:8} {r['label']}")
    assert assess("2025-07-25", None, now)["stale"] is False
    assert assess("2025-01-01", None, now)["stale"] is True
    print("\n✓ freshness: все проверки пройдены")


if __name__ == "__main__":
    _selftest()

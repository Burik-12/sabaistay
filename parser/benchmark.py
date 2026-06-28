#!/usr/bin/env python3
"""
SabaiStay — сравнение объявления с рынком (Airbnb/Booking + долгосрочная медиана).

Двухслойно (см. data/market_benchmark.json):
- ГЛАВНОЕ: цена объявления ฿/мес vs медиана ДОЛГОСРОЧНОЙ аренды района (по числу спален) → «−18% к рынку».
- СПРАВКА: медиана ПОСУТОЧНОЙ ставки Airbnb/Booking (฿/ночь) — контекст «сколько тут стоит посуточно».

Чистый Python, без сети/ключей — тестируется офлайн. Используется витриной (web/build.py) и ботом.
Источник медиан сейчас — сид-оценки; заменятся реальными из ingest/market_airbnb_booking.py (Фаза 5).
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "market_benchmark.json"
_CHEAP = -8   # порог «дешевле рынка», %
_PRICEY = 8   # порог «дороже рынка», %


class MarketBenchmark:
    def __init__(self, path: Path | str = _DEFAULT):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.districts = data["districts"]
        self.is_seed = data.get("source") == "seed"

    def compare(self, district: str | None, price: float | None,
                period: str = "month", bedrooms: int | None = None) -> dict | None:
        """Вернёт бенчмарк или None, если сравнивать не с чем."""
        if not district or price is None or period != "month":
            return None  # сравниваем только помесячную аренду с известной ценой и районом
        d = self.districts.get(district)
        if not d:
            return None
        bed_key = str(bedrooms) if bedrooms in (1, 2, 3) else "overall"
        median = d["monthly_median_thb"].get(bed_key) or d["monthly_median_thb"]["overall"]
        pct = round((price - median) / median * 100)
        kind = "cheap" if pct <= _CHEAP else "pricey" if pct >= _PRICEY else "market"
        label = ("≈ рынок" if kind == "market"
                 else f"{'+' if pct > 0 else '−'}{abs(pct)}% к рынку")
        return {
            "monthly_median": median,
            "pct": pct,
            "kind": kind,
            "label": label,
            "airbnb_adr": d.get("airbnb_adr_thb"),
            "booking_adr": d.get("booking_adr_thb"),
            "seed": self.is_seed,
        }


_SELFTEST = [
    # (район, цена/мес, спален) → ожидаемый kind
    ("Hin Kong", 35000, 2, "cheap"),    # медиана 2BR 40000 → −13%
    ("Sri Thanu", 34000, 1, "pricey"),  # медиана 1BR 25000 → +36%
    ("Chaloklum", 40000, 2, "pricey"),  # медиана 2BR 35000 → +14%
    ("Ban Tai", 42000, 2, "market"),    # медиана 2BR 42000 → 0%
]

if __name__ == "__main__":
    import sys
    mb = MarketBenchmark()
    ok = 0
    for district, price, beds, expect in _SELFTEST:
        b = mb.compare(district, price, "month", beds)
        got = b["kind"] if b else None
        mark = "✓" if got == expect else "✗"
        ok += got == expect
        adr = f", Airbnb ~{b['airbnb_adr']}฿/ночь" if b else ""
        print(f"{mark} {district:12} {price:>6}฿ {beds}BR → {b['label'] if b else '—':14} ({got}){adr}")
    print(f"\n{ok}/{len(_SELFTEST)} прошло" + ("  ⚠️ медианы — сид-оценки" if mb.is_seed else ""))
    sys.exit(0 if ok == len(_SELFTEST) else 1)

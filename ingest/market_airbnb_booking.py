#!/usr/bin/env python3
"""
SabaiStay — сбор рыночных медиан (Airbnb/Booking/FazWaz) → data/market_benchmark.json (Фаза 5, заготовка).

Заполняет бенчмарк реальными числами вместо сид-оценок. Двухслойно:
- monthly_median_thb — медиана ДОЛГОСРОЧНОЙ аренды по районам (источник: FazWaz long-term, объявления нашей же базы).
- airbnb_adr_thb / booking_adr_thb — медиана ПОСУТОЧНОЙ ставки (Airbnb/Booking).

⚠️ Airbnb/Booking скрейпятся так же тяжело, как Facebook (антибот, ToS) — тот же managed-подход (Apify/прокси,
см. fb-setup-checklist.md). НЕ блокировать запуск продукта этим: сид-оценки в market_benchmark.json уже дают
рабочую фичу. Реальный сбор — когда будет бюджет.

ПЕРЕИСПОЛЬЗОВАНИЕ: в проекте RW уже есть пайплайн rental-market-ci (коллекторы Airbnb/Booking/Agoda/Vrbo/FazWaz
+ district_centroids), который считает медианы ADR по районам. Его КОД можно скопировать сюда (медианы —
агрегат, не персданные, не инвентарь RW), но запускать как отдельный модуль SabaiStay.

Запуск (позже):
    python3 market_airbnb_booking.py -o ../data/market_benchmark.json
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISTRICTS = ROOT / "data" / "districts.json"


def collect_airbnb_adr(district: str) -> list[float]:
    """TODO (платно): посуточные ставки Airbnb по району → список ฿/ночь.
    Реализация: managed-актор/прокси (как FB) или перенос коллектора из RW rental-market-ci."""
    raise NotImplementedError("Airbnb-сбор — Фаза 5. Пока используются сид-оценки в market_benchmark.json")


def collect_booking_adr(district: str) -> list[float]:
    """TODO (платно): посуточные ставки Booking по району → список ฿/ночь."""
    raise NotImplementedError("Booking-сбор — Фаза 5")


def collect_longterm_monthly(district: str) -> dict[str, list[float]]:
    """TODO: помесячные ставки долгосрочной аренды по числу спален (FazWaz + наши объявления).
    Вернуть {"1": [...], "2": [...], "3": [...]} списков ฿/мес."""
    raise NotImplementedError("Long-term медианы — Фаза 5")


def median_or_none(values: list[float]) -> int | None:
    return round(statistics.median(values)) if values else None


def build_benchmark() -> dict:
    """Собрать market_benchmark.json. Каркас — заполнить, когда подключим коллекторы."""
    districts = json.loads(DISTRICTS.read_text(encoding="utf-8"))["districts"]
    out = {"version": "auto", "currency": "THB", "source": "collected", "districts": {}}
    for d in districts:
        name = d["canonical"]
        monthly = collect_longterm_monthly(name)
        out["districts"][name] = {
            "monthly_median_thb": {k: median_or_none(v) for k, v in monthly.items()},
            "airbnb_adr_thb": median_or_none(collect_airbnb_adr(name)),
            "booking_adr_thb": median_or_none(collect_booking_adr(name)),
            "sample_n": sum(len(v) for v in monthly.values()),
        }
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Сбор рыночных медиан → market_benchmark.json")
    ap.add_argument("-o", "--out", default=str(ROOT / "data" / "market_benchmark.json"))
    args = ap.parse_args()
    try:
        data = build_benchmark()
    except NotImplementedError as e:
        sys.exit(f"Заготовка: {e}")
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Записано → {args.out}")


if __name__ == "__main__":
    main()

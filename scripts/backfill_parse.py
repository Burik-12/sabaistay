#!/usr/bin/env python3
"""
Добор полей в уже собранных объявлениях после доработок парсера.

Парсер со временем учится: читает падежи в названиях районов, числительные словом
(«с двумя спальнями»), достраивает период у цен без явного «в месяц». Но объявления,
разобранные раньше, остаются с прежними — часто пустыми — полями, из-за чего выпадают
из фильтров и с карты.

Скрипт заполняет ТОЛЬКО пустые значения и никогда не перетирает уже распознанное
(единственное исключение — период `unknown`, который по сути и есть «пусто»).
Поэтому прогон безопасно повторять сколько угодно раз.

    python3 scripts/backfill_parse.py            # показать, что изменится
    python3 scripts/backfill_parse.py --apply    # записать
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import parse_lite  # noqa: E402

TARGET = ROOT / "data" / "sample_listings.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="Добор районов/спален/периода в собранных объявлениях")
    ap.add_argument("--apply", action="store_true", help="записать изменения в файл")
    ap.add_argument("--file", default=str(TARGET), help="файл с постами")
    args = ap.parse_args()

    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["listings"] if isinstance(data, dict) else data

    stats = {"район": 0, "спальни": 0, "период": 0}
    examples: list[str] = []

    for row in rows:
        parsed = row.get("parsed") or {}
        if not parsed.get("is_listing"):
            continue
        text = row.get("raw_text") or ""

        if not parsed.get("district_canonical"):
            raw, canon = parse_lite._parse_district(text)
            if canon:
                parsed["district_raw"] = raw
                parsed["district_canonical"] = canon
                stats["район"] += 1
                if len(examples) < 10:
                    examples.append(f"  район   {canon:16} | {' '.join(text.split())[:60]}")

        if parsed.get("bedrooms") is None:
            beds = parse_lite._parse_beds(text)
            if beds is not None:
                parsed["bedrooms"] = beds
                stats["спальни"] += 1
                if len(examples) < 10:
                    examples.append(f"  спален  {beds!s:16} | {' '.join(text.split())[:60]}")

        # Период `unknown` — это «не смогли определить», а не осознанное значение,
        # поэтому его достраиваем; month/night, наоборот, не трогаем никогда.
        if parsed.get("price_period") in (None, "unknown") and parsed.get("price_amount"):
            _, period = parse_lite._parse_price(text)
            if period != "unknown":
                parsed["price_period"] = period
                stats["период"] += 1
                if len(examples) < 10:
                    examples.append(f"  период  {period:16} | {' '.join(text.split())[:60]}")

    total = sum(1 for r in rows if (r.get("parsed") or {}).get("is_listing"))
    print(f"объявлений: {total}")
    for key, val in stats.items():
        print(f"  добрано {key}: {val}")
    if examples:
        print("\nпримеры:")
        print("\n".join(examples))

    if not args.apply:
        print("\nЭто был сухой прогон. Записать: --apply")
        return

    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nЗаписано в {path}")


if __name__ == "__main__":
    main()

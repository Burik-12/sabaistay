#!/usr/bin/env python3
"""
Разовый добор районов в уже собранных данных.

Поиск района научился читать падежи и слитные написания («тонг салы», «бантае»),
но объявления, разобранные до этого, остались без района — а значит вне карты
и вне фильтра по району. Скрипт проходит по сохранённым постам и заполняет
ТОЛЬКО пустой район, ничего больше не трогая: цена, спальни, тип и confidence
остаются как были, поэтому прогон безопасно повторять.

    python3 scripts/backfill_districts.py            # показать, что изменится
    python3 scripts/backfill_districts.py --apply    # записать
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
    ap = argparse.ArgumentParser(description="Добор районов в собранных объявлениях")
    ap.add_argument("--apply", action="store_true", help="записать изменения в файл")
    ap.add_argument("--file", default=str(TARGET), help="файл с постами")
    args = ap.parse_args()

    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["listings"] if isinstance(data, dict) else data

    filled, examples = 0, []
    for row in rows:
        parsed = row.get("parsed") or {}
        if not parsed.get("is_listing") or parsed.get("district_canonical"):
            continue
        raw, canon = parse_lite._parse_district(row.get("raw_text") or "")
        if not canon:
            continue
        parsed["district_raw"] = raw
        parsed["district_canonical"] = canon
        filled += 1
        if len(examples) < 8:
            text = (row.get("raw_text") or "").replace("\n", " ")[:70]
            examples.append(f"  + {canon:16} ← {raw!r:20} | {text}")

    total = sum(1 for r in rows if (r.get("parsed") or {}).get("is_listing"))
    with_district = sum(
        1 for r in rows
        if (r.get("parsed") or {}).get("is_listing") and (r.get("parsed") or {}).get("district_canonical")
    )
    print(f"объявлений: {total}")
    print(f"с районом: {with_district} ({with_district * 100 // max(total, 1)}%), из них добрано сейчас: {filled}")
    if examples:
        print("\nпримеры добора:")
        print("\n".join(examples))

    if not args.apply:
        print("\nЭто был сухой прогон. Записать: --apply")
        return

    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nЗаписано в {path}")


if __name__ == "__main__":
    main()

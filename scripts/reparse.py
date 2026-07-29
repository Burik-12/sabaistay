#!/usr/bin/env python3
"""
Полный повторный разбор собранной базы текущей версией парсера.

Зачем отдельно от backfill_parse.py: тот только ДОПОЛНЯЕТ пустые поля и никогда не
трогает уже распознанное. Это безопасно, но бесполезно, когда парсер начинает разбирать
что-то *иначе*, а не полнее — например, перестаёт считать рекламу отеля объявлением или
берёт из прайс-листа месячную цену вместо недельной. Такие записи backfill не исправит:
поле у них заполнено, просто неверно.

Разбор целиком выводится из `raw_text`, поэтому пересчёт ничего не теряет: сырой текст
и метаданные поста (дата, ссылка, источник) остаются нетронутыми.

    python3 scripts/reparse.py            # показать, что изменится
    python3 scripts/reparse.py --apply    # записать (перед этим делает .bak)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import parse_lite  # noqa: E402

TARGET = ROOT / "data" / "sample_listings.json"
# Поля, расхождения по которым показываем поимённо: они видны на витрине и влияют на фильтры.
WATCH = ("is_listing", "price_amount", "price_period", "district_canonical",
         "bedrooms", "property_type", "status_hint", "area_sqm")


def main() -> None:
    ap = argparse.ArgumentParser(description="Повторный разбор базы текущим парсером")
    ap.add_argument("--apply", action="store_true", help="записать изменения в файл")
    ap.add_argument("--file", default=str(TARGET), help="файл с постами")
    ap.add_argument("--examples", type=int, default=6, help="сколько примеров показать на поле")
    args = ap.parse_args()

    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["listings"] if isinstance(data, dict) else data

    changes: Counter = Counter()
    examples: dict[str, list[str]] = {f: [] for f in WATCH}
    was_listing = sum(1 for r in rows if (r.get("parsed") or {}).get("is_listing"))

    for row in rows:
        old = row.get("parsed") or {}
        new = parse_lite.parse_post(row).get("parsed", {})
        for field in WATCH:
            if old.get(field) != new.get(field):
                changes[field] += 1
                if len(examples[field]) < args.examples:
                    snippet = " ".join((row.get("raw_text") or "").split())[:64]
                    examples[field].append(f"    {old.get(field)!s:>14} → {new.get(field)!s:<14} | {snippet}")
        row["parsed"] = new

    now_listing = sum(1 for r in rows if (r.get("parsed") or {}).get("is_listing"))

    print(f"записей в базе: {len(rows)}")
    print(f"считались объявлениями: {was_listing} → {now_listing} ({now_listing - was_listing:+d})\n")
    if not changes:
        print("Разбор не изменился — база уже соответствует текущему парсеру.")
        return
    for field, n in changes.most_common():
        print(f"  {field}: {n} изменений")
        for line in examples[field]:
            print(line)
        print()

    if not args.apply:
        print("Это был сухой прогон. Записать: --apply")
        return

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Записано в {path}\nРезервная копия прежнего разбора: {backup}")


if __name__ == "__main__":
    main()

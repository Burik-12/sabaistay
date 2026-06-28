#!/usr/bin/env python3
"""
SabaiStay — проверка образца + демонстрация ФИЛЬТРА (ядро продукта).

Грузит data/sample_listings.json, проверяет обязательные поля схемы, сверяет район
нормализатором (parser/normalize_district.py) и прогоняет несколько фильтр-запросов —
показывает, как из хаоса объявлений получается то, чего нет в FB/Telegram: нормальный поиск.

Чистый Python, без сети и ключей. Запуск:  python3 validate_sample.py
"""
from __future__ import annotations

import json
from pathlib import Path

from normalize_district import DistrictNormalizer

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample_listings.json"
SCHEMA = ROOT / "parser" / "listing_schema.json"


def main() -> None:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    required = schema["required"]
    dn = DistrictNormalizer()

    rows = data["listings"]
    listings = [r for r in rows if r["parsed"]["is_listing"]]
    noise = [r for r in rows if not r["parsed"]["is_listing"]]

    # 1) валидация обязательных полей
    missing = 0
    for r in rows:
        for key in required:
            if key not in r["parsed"]:
                missing += 1
                print(f"  ✗ {r['source_url']}: нет поля {key}")
    print(f"Схема: {len(rows)} записей, пропущенных обязательных полей: {missing}")

    # 2) сверка района нормализатором
    dist_ok = dist_fix = 0
    for r in listings:
        raw = r["parsed"]["district_raw"]
        canon = r["parsed"]["district_canonical"]
        res = dn.normalize(raw)
        got = res["canonical"] if res else None
        if got == canon:
            dist_ok += 1
        elif got and canon is None:
            dist_fix += 1  # нормализатор нашёл район там, где парсер не уверился
    print(f"Районы: совпало с нормализатором {dist_ok}/{len(listings)}; нормализатор дополнил {dist_fix}")

    # 3) сводка
    print(f"\nВсего постов: {len(rows)} | объявлений: {len(listings)} | мусор отсеян: {len(noise)}")
    geo = sum(1 for r in listings if r["parsed"]["district_canonical"])
    priced = sum(1 for r in listings if r["parsed"]["price_amount"])
    print(f"С районом (ляжет на карту): {geo}/{len(listings)} | с ценой: {priced}/{len(listings)}")

    # 4) ДЕМО ФИЛЬТРА — то, чего нет в FB Marketplace и Telegram
    def show(title, pred):
        hits = [r for r in listings if pred(r["parsed"])]
        print(f"\n🔎 {title}  →  {len(hits)} совпадений")
        for r in hits:
            p = r["parsed"]
            price = f"{p['price_amount']:,}฿/мес".replace(",", " ") if p["price_amount"] else "цена?"
            bd = f"{p['bedrooms']}BR" if p["bedrooms"] is not None else "?BR"
            print(f"   • {p['district_canonical'] or '—':16} {bd:4} {price:14} {p['property_type']:8} {r['source_url']}")

    show("Аренда ≤ 40 000฿/мес, 2 спальни",
         lambda p: p["price_period"] == "month" and (p["price_amount"] or 9e9) <= 40000 and p["bedrooms"] == 2)
    show("Sri Thanu / Hin Kong (запад острова), любой бюджет",
         lambda p: p["district_canonical"] in ("Sri Thanu", "Hin Kong"))
    show("Виллы с бассейном",
         lambda p: p["property_type"] == "villa" and p["amenities"]["pool"])
    show("Бюджет ≤ 20 000฿/мес",
         lambda p: p["price_period"] == "month" and (p["price_amount"] or 9e9) <= 20000)


if __name__ == "__main__":
    main()
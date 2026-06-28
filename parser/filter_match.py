#!/usr/bin/env python3
"""
SabaiStay — движок сопоставления объявления с фильтром (ядро продукта).

Один и тот же предикат нужен в двух местах:
  • витрина — «покажи всё, что подходит под мой фильтр» (web/build.py отдаёт это в JS);
  • бот «подписка на фильтр» — «пни меня, когда появится новый дом под мой фильтр» (bot/subscribe_bot.py).
Чтобы поведение совпадало, держим логику здесь, а не дублируем.

Формат критериев = saved_filter.criteria (db/schema.sql), всё опционально:
    {
      "district":      "Sri Thanu" | ["Sri Thanu", "Hin Kong"],   # любой из
      "period":        "month" | "night" | "sale",                # тип цены
      "price_min":     20000,
      "price_max":     40000,
      "bedrooms":      2,            # точное число спален (0 = студия)
      "bedrooms_min":  1,
      "bedrooms_max":  3,
      "property_type": "villa" | ["villa", "house"],
      "pool": true, "wifi": true, "aircon": true, "kitchen": true,
      "seaview": true, "washing_machine": true, "pets_allowed": true, "motorbike": true
    }

Философия пуша: лучше пропустить, чем спамить. Если фильтр требует поле, которого
в объявлении нет (цена/спальни/район = null) — это НЕ совпадение (не пушим «неизвестность»).

Чистый Python, без сети и ключей. Самотест:  python3 filter_match.py
"""
from __future__ import annotations

import json
from pathlib import Path

AMENITY_KEYS = ("pool", "wifi", "aircon", "kitchen", "seaview",
                "washing_machine", "pets_allowed", "motorbike")


def _as_set(value) -> set[str]:
    """Критерий-перечисление: строка или список → множество для проверки «любой из»."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return set(value)


def matches(parsed: dict, criteria: dict, *, require_active: bool = True) -> bool:
    """True, если объявление (parsed-поля из listing_schema) удовлетворяет всем заданным критериям."""
    if not parsed.get("is_listing", True):
        return False
    if require_active and parsed.get("status_hint") == "rented":
        return False

    # тип цены (month/night/sale) — если задан, должен совпасть точно
    period = criteria.get("period")
    if period and parsed.get("price_period") != period:
        return False

    # цена — констрейнт требует известную цену, иначе не совпадение
    price = parsed.get("price_amount")
    if criteria.get("price_max") is not None:
        if price is None or price > criteria["price_max"]:
            return False
    if criteria.get("price_min") is not None:
        if price is None or price < criteria["price_min"]:
            return False

    # спальни (0 = студия — валидное значение, не «нет данных»)
    bedrooms = parsed.get("bedrooms")
    needs_bed = any(criteria.get(k) is not None for k in ("bedrooms", "bedrooms_min", "bedrooms_max"))
    if needs_bed and bedrooms is None:
        return False
    if criteria.get("bedrooms") is not None and bedrooms != criteria["bedrooms"]:
        return False
    if criteria.get("bedrooms_min") is not None and bedrooms < criteria["bedrooms_min"]:
        return False
    if criteria.get("bedrooms_max") is not None and bedrooms > criteria["bedrooms_max"]:
        return False

    # район (канон) — любой из заданных
    districts = _as_set(criteria.get("district"))
    if districts and parsed.get("district_canonical") not in districts:
        return False

    # тип объекта — любой из заданных
    ptypes = _as_set(criteria.get("property_type"))
    if ptypes and parsed.get("property_type") not in ptypes:
        return False

    # удобства — каждое затребованное (True) должно быть явно True в объявлении
    amenities = parsed.get("amenities") or {}
    for key in AMENITY_KEYS:
        if criteria.get(key) and not amenities.get(key):
            return False

    return True


def match_listings(listings: list[dict], criteria: dict) -> list[dict]:
    """Отфильтровать список объявлений (формат строк sample_listings.json: {..., 'parsed': {...}})."""
    return [r for r in listings if matches(r.get("parsed", r), criteria)]


def describe(criteria: dict) -> str:
    """Человеческое описание фильтра для бота: «Sri Thanu · до 30 000฿/мес · 2 спальни · бассейн»."""
    parts: list[str] = []
    districts = _as_set(criteria.get("district"))
    if districts:
        parts.append(" / ".join(sorted(districts)))
    ptypes = _as_set(criteria.get("property_type"))
    if ptypes:
        ru = {"villa": "вилла", "house": "дом", "studio": "студия", "bungalow": "бунгало",
              "apartment": "апартаменты", "room": "комната", "land": "участок"}
        parts.append(" / ".join(ru.get(t, t) for t in sorted(ptypes)))

    unit = {"month": "฿/мес", "night": "฿/ночь", "sale": "฿"}.get(criteria.get("period"), "฿")
    pmin, pmax = criteria.get("price_min"), criteria.get("price_max")
    if pmin is not None and pmax is not None:
        parts.append(f"{pmin:,}–{pmax:,}{unit}".replace(",", " "))
    elif pmax is not None:
        parts.append(f"до {pmax:,}{unit}".replace(",", " "))
    elif pmin is not None:
        parts.append(f"от {pmin:,}{unit}".replace(",", " "))

    if criteria.get("bedrooms") is not None:
        if criteria["bedrooms"] == 0:
            if "studio" not in ptypes:                  # не дублируем «студия», если это и тип объекта
                parts.append("студия")
        else:
            parts.append(f"{criteria['bedrooms']} спальни")
    elif criteria.get("bedrooms_min") is not None and criteria.get("bedrooms_max") is not None:
        parts.append(f"{criteria['bedrooms_min']}–{criteria['bedrooms_max']} спален")
    elif criteria.get("bedrooms_min") is not None:
        parts.append(f"от {criteria['bedrooms_min']} спален")

    labels = {"pool": "бассейн", "wifi": "wi-fi", "aircon": "кондиционер", "kitchen": "кухня",
              "seaview": "вид на море", "washing_machine": "стиралка",
              "pets_allowed": "можно с питомцами", "motorbike": "байк"}
    parts += [labels[k] for k in AMENITY_KEYS if criteria.get(k)]

    return " · ".join(parts) if parts else "любое жильё"


# ─────────────────────────── самотест ───────────────────────────
def _selftest() -> None:
    root = Path(__file__).resolve().parent.parent
    rows = json.loads((root / "data" / "sample_listings.json").read_text(encoding="utf-8"))["listings"]
    listings = [r for r in rows if r["parsed"]["is_listing"]]

    cases = [
        ({"period": "month", "price_max": 40000, "bedrooms": 2}, "2 спальни ≤ 40k/мес"),
        ({"district": ["Sri Thanu", "Hin Kong"]}, "запад острова"),
        ({"property_type": "villa", "pool": True}, "виллы с бассейном"),
        ({"period": "month", "price_max": 20000}, "бюджет ≤ 20k/мес"),
        ({"district": "Sri Thanu", "bedrooms_min": 1}, "Sri Thanu, от 1 спальни"),
    ]
    for crit, name in cases:
        hits = match_listings(listings, crit)
        # инвариант: каждое попадание реально удовлетворяет критерию
        assert all(matches(r["parsed"], crit) for r in hits)
        print(f"🔎 {describe(crit):42} → {len(hits):2} совпадений  [{name}]")

    # детерминированные проверки на конкретных строках образца
    hin_kong_2br = next(r["parsed"] for r in listings if r["parsed"]["title"] == "Дом 2 спальни, Hin Kong")
    assert matches(hin_kong_2br, {"period": "month", "price_max": 40000, "bedrooms": 2})
    assert not matches(hin_kong_2br, {"price_max": 30000})          # 35k > 30k
    assert not matches(hin_kong_2br, {"pool": True})                # бассейна нет
    assert not matches(hin_kong_2br, {"district": "Sri Thanu"})     # другой район
    assert not matches(hin_kong_2br, {"period": "night"})           # помесячно, не ночь

    # null-поля не пушим: объявление без района не ловится фильтром по району
    no_district = next(r["parsed"] for r in listings if r["parsed"]["district_canonical"] is None)
    assert not matches(no_district, {"district": "Wok Tum"})

    print("\n✓ filter_match: все проверки пройдены")


if __name__ == "__main__":
    _selftest()

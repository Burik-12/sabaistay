#!/usr/bin/env python3
"""
SabaiStay — лёгкий regex-парсер объявлений (без API-ключа).

Разбирает посты из t.me/s/koh_phangan_rent: извлекает район/цену/спальни/тип/удобства
через паттерны. Точность ~70-80% по сравнению с Claude-версией, зато бесплатно и мгновенно.
Используй parse.py (Claude) для финального качества когда будет API-ключ.

Запуск:
    python3 parse_lite.py ../data/sample_tg.json -o ../data/sample_listings.json
    python3 parse_lite.py /tmp/tg_raw.json --merge ../data/sample_listings.json -o ../data/sample_listings.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))
from normalize_district import DistrictNormalizer  # noqa: E402

_dn = DistrictNormalizer()

# ── спам-фильтр ──────────────────────────────────────────────────────────────
_SPAM = re.compile(
    r"(LocUs|mobile app|AppStore|Google Play|лукас|looking for|ищу\s+жильё|"
    r"wanted|need\s+to\s+rent|ISO\s|#wanted|^(seeking|🔍\s*seeking))",
    re.IGNORECASE,
)

# ── цена ─────────────────────────────────────────────────────────────────────
# "35000 per month", "35,000/month", "35k/month", "35 000 ฿", "15 per month" (отсекаем < 1000)
_PRICE = re.compile(
    r"(\d[\d\s,]*)(?:\s*k)?\s*(?:per\s+month|/month|฿?\s*/\s*month|thb\s*/\s*month|baht\s*/\s*month|в\s+месяц|/\s*мес(?:яц)?|\s+в\s+мес(?:яц)?)",
    re.IGNORECASE,
)
_PRICE_SHORT = re.compile(
    r"(\d+(?:[.,]\d{3})+)\s*(?:฿|thb|baht|бат)?(?:\s*/\s*month|\s+per\s+month)?",
    re.IGNORECASE,
)
_PRICE_K = re.compile(r"(\d+(?:\.\d+)?)k\s*(?:/\s*month|per\s+month)?", re.IGNORECASE)
_NIGHT = re.compile(r"(\d[\d\s,]*)\s*(?:฿|thb|бат)?\s*(?:per\s+night|/night|a\s+night|в\s+сутки|/\s*сутки|сутки)", re.IGNORECASE)
# Русские форматы: "25 000бат", "20 000 бат", "18 тыс бат", "18тыс"
_PRICE_RU_TYS = re.compile(r"(\d+(?:[.,]\d+)?)\s*тыс\.?\s*(?:бат|baht|฿)?", re.IGNORECASE)
_PRICE_RU_BAT = re.compile(r"(\d[\d\s]{2,})\s*бат\b", re.IGNORECASE)
# Контекстный: "месяц N бат/тыс" или "N бат ... месяц"
_PRICE_RU_MONTH_CTX = re.compile(
    r"(?:месяц|мес\.?)\s*(?:от\s+)?(\d[\d\s]*(?:тыс\.?)?)\s*(?:бат|baht|฿)?|"
    r"(\d[\d\s]*)\s*(?:тыс\.?)?\s*(?:бат|baht|฿)\s*/?\s*(?:месяц|мес\.?|month)",
    re.IGNORECASE,
)


def _parse_price(text: str) -> tuple[float | None, str]:
    """Возвращает (price_amount, price_period)."""
    # посуточно
    m = _NIGHT.search(text)
    if m:
        v = float(re.sub(r"[\s,]", "", m.group(1)))
        if v >= 100:
            return v, "night"

    # помесячно — k-нотация
    m = _PRICE_K.search(text)
    if m:
        v = float(m.group(1)) * 1000
        if v >= 1000:
            return v, "month"

    # помесячно — явный per month / в месяц
    m = _PRICE.search(text)
    if m:
        raw = re.sub(r"[\s,]", "", m.group(1))
        try:
            v = float(raw)
            if v >= 1000:
                return v, "month"
        except ValueError:
            pass

    # помесячно — контекст "месяц N бат"
    m = _PRICE_RU_MONTH_CTX.search(text)
    if m:
        raw = re.sub(r"[\s,]", "", m.group(1) or m.group(2) or "")
        if raw:
            raw = re.sub(r"тыс\.?$", "", raw, flags=re.IGNORECASE)
            try:
                v = float(raw)
                if "тыс" in (m.group(0) or "").lower() or v < 500:
                    v *= 1000
                if v >= 1000:
                    return v, "month"
            except ValueError:
                pass

    # помесячно — число с разделителем тысяч
    m = _PRICE_SHORT.search(text)
    if m:
        raw = re.sub(r"[\s,.]", "", m.group(1))
        try:
            v = float(raw)
            if v >= 1000:
                return v, "month"
        except ValueError:
            pass

    # русская цена "18 тыс бат"
    m = _PRICE_RU_TYS.search(text)
    if m:
        try:
            v = float(re.sub(r"[\s,]", "", m.group(1))) * 1000
            if v >= 1000:
                return v, "unknown"
        except ValueError:
            pass

    # русская цена "25 000бат" / "25 000 бат"
    m = _PRICE_RU_BAT.search(text)
    if m:
        try:
            v = float(re.sub(r"[\s,]", "", m.group(1)))
            if v >= 1000:
                return v, "unknown"
        except ValueError:
            pass

    return None, "unknown"


# ── спальни ──────────────────────────────────────────────────────────────────
_BEDS = re.compile(
    r"(\d+)\s*(?:bedroom|bed(?:room)?s?|bd|br|спал[еьни]+|комнат[аы]?)\b",
    re.IGNORECASE,
)
_STUDIO = re.compile(r"\bstudio\b|\bстудия\b", re.IGNORECASE)


def _parse_beds(text: str) -> int | None:
    if _STUDIO.search(text):
        return 0
    m = _BEDS.search(text)
    if m:
        v = int(m.group(1))
        return v if v <= 20 else None
    return None


# ── тип жилья ─────────────────────────────────────────────────────────────────
_TYPES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bvillas?\b|\bвилла\b", re.IGNORECASE), "villa"),
    (re.compile(r"\bbungalows?\b|\bбунгало\b", re.IGNORECASE), "bungalow"),
    (re.compile(r"\bapartments?\b|\bapts?\b|\bcondo(?:minium)?s?\b|\bапартамент", re.IGNORECASE), "apartment"),
    (re.compile(r"\bstudios?\b|\bстудия\b", re.IGNORECASE), "studio"),
    (re.compile(r"\b(?:single|double|private)\s+rooms?\b|\brooms?\s+for\s+rent\b|\bavailable\s+rooms?\b|\brooms?\s+available\b|\bкомнат[ау]\s+сдам", re.IGNORECASE), "room"),
    (re.compile(r"\btownhouse\b|\bhouse\b|\bдом\b", re.IGNORECASE), "house"),
]

def _parse_type(text: str) -> str:
    for pat, t in _TYPES:
        if pat.search(text):
            return t
    return "other"


# ── удобства ─────────────────────────────────────────────────────────────────
_AMEN: list[tuple[str, re.Pattern]] = [
    ("pool",            re.compile(r"\bpool\b|\bбассейн\b", re.IGNORECASE)),
    ("wifi",            re.compile(r"\bwifi\b|\bwi-fi\b|\bинтернет\b", re.IGNORECASE)),
    ("kitchen",         re.compile(r"\bkitchen\b|\bкухня\b|\bкухн[еи]\b", re.IGNORECASE)),
    ("aircon",          re.compile(r"\baircon\b|\bair\s*con\b|\ba/?c\b|\bкондиц", re.IGNORECASE)),
    ("seaview",         re.compile(r"\bsea\s*view\b|\bocean\s*view\b|\bвид\s+на\s+море\b", re.IGNORECASE)),
    ("washing_machine", re.compile(r"\bwash(?:ing)?\s+machine\b|\bстирал", re.IGNORECASE)),
    ("pets_allowed",    re.compile(r"\bpets?\s+(?:allow|ok|welcome|friend)\b|\bживотные\b", re.IGNORECASE)),
    ("motorbike",       re.compile(r"\bmotorbike\b|\bmotorcycle\b|\bscooter\b|\bбайк\b", re.IGNORECASE)),
]

def _parse_amenities(text: str) -> dict:
    return {key: bool(pat.search(text)) for key, pat in _AMEN}


# ── район ────────────────────────────────────────────────────────────────────
def _parse_district(text: str) -> tuple[str | None, str | None]:
    res = _dn.normalize(text)
    if res:
        return res["canonical"], res["canonical"]
    return None, None


# ── язык ─────────────────────────────────────────────────────────────────────
def _parse_language(text: str) -> str:
    has_ru = bool(re.search(r"[а-яёА-ЯЁ]", text))
    has_th = bool(re.search(r"[฀-๿]", text))
    has_en = bool(re.search(r"[a-zA-Z]", text))
    if has_ru and has_en:
        return "mixed"
    if has_ru:
        return "ru"
    if has_th:
        return "th"
    if has_en:
        return "en"
    return "other"


# ── статус (rented/active) ───────────────────────────────────────────────────
_RENTED = re.compile(
    r"\brented\b|\btaken\b|\bno longer\b|\bunavailable\b|\bсдано\b|\bзанято\b",
    re.IGNORECASE,
)

def _parse_status(text: str) -> str:
    return "rented" if _RENTED.search(text) else "active"


# ── контакт ─────────────────────────────────────────────────────────────────
_CONTACT_TG = re.compile(
    r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{3,31})(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
# Имена каналов-источников — не контакты продавца
_NOT_CONTACT_TG = {
    "rent_phangan", "koh_phangan_rent", "adsphangan", "phangan_rent",
    "arenda_phangan", "ru_phangan", "phangan_villas", "phangan_homes",
    "phangan_longterm", "sabaistay", "phanganthai", "phangan_real_estate",
    "kohphangan_rent", "channel",
}


def _parse_contact(text: str) -> dict:
    """Извлекает Telegram-контакт продавца (первый @username, не являющийся каналом)."""
    for m in _CONTACT_TG.finditer(text):
        uname = m.group(1).lower()
        if uname not in _NOT_CONTACT_TG and len(uname) >= 4:
            return {"telegram": "@" + m.group(1)}
    return {}


# ── is_listing ────────────────────────────────────────────────────────────────
_RENT_SIGNAL = re.compile(
    r"\bfor\s+rent\b|\barendu\b|\barendat\b|\bper\s+month\b|\b/month\b|\bснять\b|\bсдаётся\b|"
    r"\bсдам\b|\bсдаём\b|\bсдаем\b|\bсдаю\b|\bв\s+аренду\b|\bаренда\b|\bаренду\b|"
    r"\bпомесячно\b|\bдолгосрок\b|\bна\s+месяц\b|\bв\s+месяц\b|\bтыс\.?\s*бат\b|\bбат\b|"
    r"\bавailable\b|\b฿\b|thb|\bbaht\b|\bper\s+night\b",
    re.IGNORECASE,
)
# Сильный сигнал предложения аренды (достаточно без района/цены)
_SUPPLY_SIGNAL = re.compile(
    r"\bсдам\b|\bсдаём\b|\bсдаем\b|\bсдаю\b|\bсдаётся\b",
    re.IGNORECASE,
)


def _is_listing(text: str, price: float | None, district: str | None) -> bool:
    if _SPAM.search(text):
        return False
    if len(text.strip()) < 15:
        return False
    # цена ≥ 1000
    if price is not None and price >= 1000:
        return True
    # сильный сигнал предложения аренды (сдам/сдаём/сдаю)
    if _SUPPLY_SIGNAL.search(text):
        return True
    # район + сигнал аренды
    if district and _RENT_SIGNAL.search(text):
        return True
    return False


# ── заголовок ─────────────────────────────────────────────────────────────────
def _make_title(district: str | None, ptype: str, beds: int | None) -> str | None:
    if not district:
        return None
    type_ru = {"house": "дом", "villa": "вилла", "bungalow": "бунгало",
                "apartment": "апартаменты", "studio": "студия", "room": "комната", "other": "жильё"}
    t = type_ru.get(ptype, "жильё")
    if beds == 0:
        return f"{district} — {t} (студия)"
    if beds:
        return f"{district} — {t} {beds} спал."
    return f"{district} — {t}"


# ── основная функция ──────────────────────────────────────────────────────────
def parse_post(post: dict) -> dict:
    text = post.get("raw_text", "")
    price, period = _parse_price(text)
    beds = _parse_beds(text)
    ptype = _parse_type(text)
    district_raw, district_canon = _parse_district(text)
    amenities = _parse_amenities(text)
    status = _parse_status(text)
    lang = _parse_language(text)
    contact = _parse_contact(text)
    listing = _is_listing(text, price, district_canon)

    # уверенность: считаем сколько ключевых полей найдено
    found = sum([price is not None, district_canon is not None, beds is not None, ptype != "other"])
    confidence = round(min(0.5 + found * 0.12, 0.92), 2)
    if not listing:
        confidence = 0.1

    return {
        **post,
        "parsed": {
            "is_listing": listing,
            "title": _make_title(district_canon, ptype, beds),
            "district_raw": district_raw,
            "district_canonical": district_canon,
            "price_amount": price,
            "price_currency": "THB",
            "price_period": period,
            "bedrooms": beds,
            "bathrooms": None,
            "property_type": ptype,
            "area_sqm": None,
            "amenities": amenities,
            "available_from": None,
            "available_to": None,
            "min_stay_months": None,
            "language": lang,
            "status_hint": status,
            "confidence": confidence,
            "contact": contact,
        },
    }


def parse_all(posts: list[dict]) -> list[dict]:
    return [parse_post(p) for p in posts]


def main() -> None:
    ap = argparse.ArgumentParser(description="Лёгкий regex-парсер объявлений SabaiStay")
    ap.add_argument("input", help="JSON с сырыми постами (формат sample_tg.json)")
    ap.add_argument("-o", "--out", help="файл для результата (иначе stdout)")
    ap.add_argument("--merge", help="существующий listings.json — новые записи добавятся, дубли (по external_id) пропустятся")
    args = ap.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    # поддержка и простого списка, и обёртки {"listings": [...]}
    posts = raw if isinstance(raw, list) else raw.get("listings", raw)

    parsed = parse_all(posts)

    if args.merge and Path(args.merge).exists():
        existing = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        ex_list = existing if isinstance(existing, list) else existing.get("listings", [])
        seen_ids = {p.get("external_id") for p in ex_list if p.get("external_id")}
        new_ones = [p for p in parsed if p.get("external_id") not in seen_ids]
        merged = ex_list + new_ones
        result = {"listings": merged}
        print(f"Merge: {len(ex_list)} existing + {len(new_ones)} new = {len(merged)} total", file=sys.stderr)
    else:
        result = {"listings": parsed}

    listings = [p for p in result["listings"] if p.get("parsed", {}).get("is_listing")]
    noise = len(result["listings"]) - len(listings)
    print(f"Итого: {len(result['listings'])} постов | {len(listings)} объявлений | {noise} мусор", file=sys.stderr)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"Сохранено → {args.out}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
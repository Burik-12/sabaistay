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
from difflib import SequenceMatcher
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
_NIGHT = re.compile(r"(\d[\d\s,]*)\s*(?:฿|thb|бат)?\s*(?:per\s+night|/night|a\s+night|per\s+day|/day|a\s+day|daily|nightly|в\s+сутки|/\s*сутки|сутки|в\s+день|за\s+ночь|за\s+сутки)", re.IGNORECASE)
# Русские форматы: "25 000бат", "20 000 бат", "18 тыс бат", "18тыс"
_PRICE_RU_TYS = re.compile(r"(\d+(?:[.,]\d+)?)\s*тыс\.?\s*(?:бат|baht|฿)?", re.IGNORECASE)
_PRICE_RU_BAT = re.compile(r"(\d[\d\s]{2,})\s*бат\b", re.IGNORECASE)
# Контекстный: "месяц N бат/тыс" или "N бат ... месяц"
_PRICE_RU_MONTH_CTX = re.compile(
    r"(?:месяц|мес\.?)\s*(?:от\s+)?(\d[\d\s]*(?:тыс\.?)?)\s*(?:бат|baht|฿)?|"
    r"(\d[\d\s]*)\s*(?:тыс\.?)?\s*(?:бат|baht|฿)\s*/?\s*(?:месяц|мес\.?|month)",
    re.IGNORECASE,
)


# Цена без явного «в месяц»/«за ночь» — половина объявлений. Оставлять их без периода
# плохо: они выпадают из фильтра «помесячно» и из сравнения с медианой района.
# На Пхангане посуточные ставки почти не поднимаются выше 7–8к (замер по данным:
# верхняя квартиль ночных — 7 000), а помесячные начинаются от ~10к. Поэтому цену
# в диапазоне ниже считаем месячной; выше 200к — скорее продажа, туда не лезем.
_MONTH_GUESS_MIN = 10_000
_MONTH_GUESS_MAX = 200_000


def _guess_period(value: float) -> str:
    return "month" if _MONTH_GUESS_MIN <= value < _MONTH_GUESS_MAX else "unknown"


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
                return v, _guess_period(v)
        except ValueError:
            pass

    # русская цена "25 000бат" / "25 000 бат"
    m = _PRICE_RU_BAT.search(text)
    if m:
        try:
            v = float(re.sub(r"[\s,]", "", m.group(1)))
            if v >= 1000:
                return v, _guess_period(v)
        except ValueError:
            pass

    return None, "unknown"


# ── спальни ──────────────────────────────────────────────────────────────────
# Между числом и «спальней» часто стоит прилагательное: «с 1 большой спальней».
# «комнат» намеренно НЕ включаем: в объявлениях полно ванных, гостиных и игровых комнат.
_BEDS = re.compile(
    r"(\d+)\s*(?:-|х|x)?\s*(?:[а-яё]+\s+)?(?:bedroom|bed(?:room)?s?|bd|br|спал[а-яё]*)\b",
    re.IGNORECASE,
)
_STUDIO = re.compile(r"\bstudio\b|\bстудия\b", re.IGNORECASE)

# «с двумя спальнями», «дом с одной спальней» — числительное словом.
_BEDS_WORD_NUM = {
    "одно": 1, "одной": 1, "одна": 1, "однa": 1,
    "двух": 2, "двумя": 2, "две": 2, "два": 2, "дву": 2,
    "трёх": 3, "трех": 3, "тремя": 3, "три": 3,
    "четырёх": 4, "четырех": 4, "четырьмя": 4, "четыре": 4,
    "пяти": 5, "пятью": 5, "пять": 5,
}
_BEDS_WORD = re.compile(
    r"\b(одно|одной|одна|двух|двумя|две|два|дву|трёх|трех|тремя|три|"
    r"четырёх|четырех|четырьмя|четыре|пяти|пятью|пять)\s*-?\s*(спал[а-яё]*)",
    re.IGNORECASE,
)

# «двуспальная КРОВАТЬ» — это про мебель, а не про число спален. Самая частая ловушка
# в объявлениях отелей и гестхаусов, где таких фраз по три штуки на пост.
_BED_FURNITURE = re.compile(r"кроват|bed\s*(?:size|linen)|матрас", re.IGNORECASE)


def _parse_beds(text: str) -> int | None:
    if _STUDIO.search(text):
        return 0

    # Цифрой. Пропускаем совпадения, за которыми идёт мебель («2х спальную кровать»).
    for m in _BEDS.finditer(text):
        if _BED_FURNITURE.search(text[m.end():m.end() + 24]):
            continue
        v = int(m.group(1))
        if v <= 20:
            return v

    # Числительным словом — с той же оговоркой про мебель.
    for m in _BEDS_WORD.finditer(text):
        if _BED_FURNITURE.search(text[m.end():m.end() + 24]):
            continue
        return _BEDS_WORD_NUM[m.group(1).lower()]
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
_DISTRICT_WORD = re.compile(r"[a-zA-Zа-яёА-ЯЁ]+")

# Слова, которые сами по себе слишком похожи на топонимы и дают ложные срабатывания
# на fuzzy (например, «дом» ↔ «Дом Сила»). Проверено на выборке из sample_listings.
_DISTRICT_STOP = {
    "дом", "дома", "тай", "тая", "бан", "баан", "хаус", "вилла", "виллы",
    "house", "villa", "home", "room", "pool", "koh", "ko", "phangan", "пханган",
    "пхангане", "остров", "море", "sea", "beach", "пляж", "аренда", "rent",
}


_ALIAS_MAP = dict(_dn._alias_to_canon)          # окно → канон, точное совпадение за O(1)
_ALIAS_BY_HEAD: dict[str, list[str]] = {}       # первые 2 буквы → алиасы, для дешёвого fuzzy
for _a in _ALIAS_MAP:
    _ALIAS_BY_HEAD.setdefault(_a[:2], []).append(_a)
_DISTRICT_CACHE: dict[str, str | None] = {}     # окна повторяются от объявления к объявлению


def _probe_district(probe: str) -> str | None:
    """Одно окно текста → канон района. Точное совпадение, затем осторожный fuzzy.

    Fuzzy сравниваем только с алиасами на те же две первые буквы: полный перебор
    всех алиасов на каждое окно делал разбор витрины непрактично медленным
    (секунды на объявление), а качество от этого не менялось.
    """
    if probe in _DISTRICT_CACHE:
        return _DISTRICT_CACHE[probe]
    hit = _ALIAS_MAP.get(probe)
    if hit is None and len(probe) >= 5:
        best, best_score = None, 0.0
        for alias in _ALIAS_BY_HEAD.get(probe[:2], ()):
            if abs(len(alias) - len(probe)) > 3:
                continue
            score = SequenceMatcher(None, probe, alias).ratio()
            if score > best_score:
                best, best_score = alias, score
        if best and best_score >= 0.82:         # 0.82 ловит падежи: «тонг салы» → «тонг сала»
            hit = _ALIAS_MAP[best]
    _DISTRICT_CACHE[probe] = hit
    return hit


def _parse_district(text: str) -> tuple[str | None, str | None]:
    """Ищем район не во всём тексте разом, а по окнам из 1–3 слов.

    Нормализатор умеет fuzzy, но только на короткой строке: на полном объявлении
    он молчал, из-за чего район не определялся почти нигде («тонг салы», «бантае»,
    «шри тану» — падежи и написания, которых нет в алиасах дословно).
    Окна идут от длинных к коротким, чтобы «тонг най пан ной» не схлопнулся в «тонг».
    """
    if not text:
        return None, None

    # 1) Дословное вхождение алиаса в текст — самый надёжный и дешёвый путь.
    res = _dn.normalize(text)
    if res:
        return res["canonical"], res["canonical"]

    # 2) Окна слов. Район почти всегда в начале объявления — дальше 60 слов не идём.
    words = [w for w in _DISTRICT_WORD.findall(text.lower())[:60] if len(w) > 2]
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            chunk = words[i:i + size]
            if size == 1 and chunk[0] in _DISTRICT_STOP:
                continue
            probe = " ".join(chunk)
            canon = _probe_district(probe)
            if canon:
                return probe, canon
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


# ── дата доступности ────────────────────────────────────────────────────────
_MONTH_EN = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RU = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
# "1 March 2026", "March 1", "1 Mar"
_AVAIL_EN_DMY = re.compile(
    r"(?:available\s+(?:from\s+)?|from\s+|avail\.?\s+(?:from\s+)?)"
    r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
_AVAIL_EN_MDY = re.compile(
    r"(?:available\s+(?:from\s+)?|from\s+|avail\.?\s+(?:from\s+)?)"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
# "available from January" (только месяц, без дня)
_AVAIL_EN_M = re.compile(
    r"(?:available\s+(?:from\s+)?|avail\.?\s+(?:from\s+)?|from\s+)"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(\d{4}))?(?!\s*\d)",
    re.IGNORECASE,
)
# "с 1 марта", "доступно с 5 апреля", "свободно с марта"
_AVAIL_RU = re.compile(
    r"(?:доступн[оа]?\s+с|свободн[оа]?\s+с|сдаётся\s+с|заезд\s+с|от|с)\s+"
    r"(?:(\d{1,2})\s+)?"
    r"(янв(?:аря?)?|фев(?:раля?)?|мар(?:та?)?|апр(?:еля?)?|мая|май(?:я)?|июн(?:я)?|июл(?:я)?|авг(?:уста?)?|сен(?:тября?)?|окт(?:ября?)?|ноя(?:бря?)?|дек(?:абря?)?)"
    r"(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
# ISO / DD.MM.YYYY / DD/MM/YYYY после ключевого слова
_AVAIL_DATE = re.compile(
    r"(?:available\s+(?:from\s+)?|from\s+|доступн[оа]?\s+с|свободн[оа]?\s+с|с\s+)"
    r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?",
    re.IGNORECASE,
)
# "available now"
_AVAIL_NOW = re.compile(r"\bavailable\s+now\b|\bсвободно?\s+сейчас\b|\bдоступно?\s+сейчас\b", re.IGNORECASE)


def _parse_available_from(text: str) -> str | None:
    """Возвращает ISO дату 'YYYY-MM-DD' или 'YYYY-MM' (если день неизвестен), либо 'now', либо None."""
    import datetime
    cur_year = datetime.date.today().year
    _YEAR_MIN, _YEAR_MAX = cur_year - 1, cur_year + 3

    def _valid_year(yr_str: str | None) -> int | None:
        if not yr_str:
            return cur_year
        y = int(yr_str)
        if y < 100:
            y += 2000
        return y if _YEAR_MIN <= y <= _YEAR_MAX else None

    if _AVAIL_NOW.search(text):
        return "now"

    # ISO / DD.MM.YYYY
    m = _AVAIL_DATE.search(text)
    if m:
        d, mo, yr = m.group(1), m.group(2), m.group(3)
        try:
            day, month = int(d), int(mo)
            if 1 <= month <= 12 and 1 <= day <= 31:
                year = _valid_year(yr)
                if year:
                    return f"{year}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    # "1 March [2026]"
    m = _AVAIL_EN_DMY.search(text)
    if m:
        day, mon_str, yr = int(m.group(1)), m.group(2).lower()[:3], m.group(3)
        month = _MONTH_EN.get(mon_str)
        if month:
            year = _valid_year(yr)
            if year:
                return f"{year}-{month:02d}-{day:02d}"

    # "March 1 [2026]"
    m = _AVAIL_EN_MDY.search(text)
    if m:
        mon_str, day, yr = m.group(1).lower()[:3], int(m.group(2)), m.group(3)
        month = _MONTH_EN.get(mon_str)
        if month:
            year = _valid_year(yr)
            if year:
                return f"{year}-{month:02d}-{day:02d}"

    # "available from January [2026]"
    m = _AVAIL_EN_M.search(text)
    if m:
        mon_str, yr = m.group(1).lower()[:3], m.group(2)
        month = _MONTH_EN.get(mon_str)
        if month:
            year = _valid_year(yr)
            if year:
                return f"{year}-{month:02d}"

    # "с 1 марта [2026]"
    m = _AVAIL_RU.search(text)
    if m:
        day_s, mon_str, yr = m.group(1), m.group(2).lower()[:3], m.group(3)
        month = _MONTH_RU.get(mon_str)
        if month:
            year = _valid_year(yr)
            if year:
                day = int(day_s) if day_s else None
                if day:
                    return f"{year}-{month:02d}-{day:02d}"
                return f"{year}-{month:02d}"

    return None


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
    available_from = _parse_available_from(text)
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
            "available_from": available_from,
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
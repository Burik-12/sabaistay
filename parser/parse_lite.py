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

# Не жильё в аренду, хотя по форме похоже: реклама отелей и хостелов, инвест-предложения,
# прокат снаряжения. Такие посты идут в каналы по расписанию и в базе оказываются десятками
# копий — четверть «объявлений» была именно ими. Признаки подобраны так, чтобы не задеть
# обычные посты: живой человек, сдающий дом, не пишет «12 категорий номеров» и «завтрак
# по требованию». Слова «отель» и «resort» намеренно НЕ в списке — они часто встречаются
# в нормальных объявлениях («дом на территории резорта»).
_NOT_HOUSING = re.compile(
    r"инвестор|партнерств|партнёрств|под\s+залог\s+документов|"
    r"\bхостел|\bhostel\b|категори\w*\s+номеров|номер\w*\s+из\s+наших|"
    r"завтрак\s+по\s+требованию|"
    r"\bкаяк|\bsup\b|сап[\s-]?серф|байдарк",
    re.IGNORECASE,
)

# ── цена ─────────────────────────────────────────────────────────────────────
# "35000 per month", "35,000/month", "35k/month", "35 000 ฿", "15 per month" (отсекаем < 1000)
_PRICE = re.compile(
    r"(\d[\d\s,]*)(?:\s*k)?\s*(?:฿|thb|baht|бат)?\s*"
    r"(?:per\s+month|/month|monthly|/\s*month|в\s+месяц|/\s*мес(?:яц)?|\s+в\s+мес(?:яц)?)",
    re.IGNORECASE,
)
_PRICE_SHORT = re.compile(
    r"(\d+(?:[.,]\d{3})+)\s*(?:฿|thb|baht|бат)?(?:\s*/\s*month|\s+per\s+month)?",
    re.IGNORECASE,
)
_PRICE_K = re.compile(r"(\d+(?:\.\d+)?)k\s*(?:/\s*month|per\s+month)?", re.IGNORECASE)
# В списке валют не хватало «baht» (было только thb/฿/бат), из-за чего самая частая
# английская форма «1500 baht per night» не распознавалась как посуточная вовсе.
_NIGHT = re.compile(r"(\d[\d\s,]*)\s*(?:฿|thb|baht|бат)?\s*(?:per\s+night|/night|a\s+night|per\s+day|/day|a\s+day|daily|nightly|в\s+сутки|/\s*сутки|сутки|в\s+день|за\s+ночь|за\s+сутки)", re.IGNORECASE)
# Русские форматы: "25 000бат", "20 000 бат", "18 тыс бат", "18тыс"
_PRICE_RU_TYS = re.compile(r"(\d+(?:[.,]\d+)?)\s*тыс\.?\s*(?:бат|baht|฿)?", re.IGNORECASE)
_PRICE_RU_BAT = re.compile(r"(\d[\d\s]{2,})\s*бат\b", re.IGNORECASE)
# Латинская запись валюты вплотную к числу — «45 000thb», «60000 baht». Без периода:
# он достраивается по величине суммы (_guess_period).
_PRICE_THB = re.compile(r"(\d[\d\s]{2,})\s*(?:thb|baht|฿)\b", re.IGNORECASE)
# Контекстный: "месяц N бат/тыс" или "N бат ... месяц".
# Внутри — [^\S\n] вместо \s: перевод строки склеивал цену одной строки со словом
# «Месяц» со следующей. Прайс-лист «Неделя - 7000бат ⏎ Месяц - 25 000 бат» из-за
# этого превращался в «7 000 ฿/мес» — дом показывался втрое дешевле, чем сдаётся.
_PRICE_RU_MONTH_CTX = re.compile(
    # «мес. ⏎ 25 000» — перенос здесь безобиден: метка стоит ПЕРЕД ценой,
    # и склеить соседнюю строку прайс-листа она не может
    r"(?:месяц|мес\.?)[^\S\n]*(?:\n[^\S\n]*){0,2}(?:от\s+)?(\d[\d\s]*(?:тыс\.?)?)[^\S\n]*(?:бат|baht|฿)?|"
    r"(\d[\d\s]*)[^\S\n]*(?:тыс\.?)?[^\S\n]*(?:бат|baht|฿)[^\S\n]*/?[^\S\n]*(?:месяц|мес\.?|month)",
    re.IGNORECASE,
)

# Прайс-лист с меткой ПЕРЕД ценой — очень частый формат в объявлениях острова:
#     Сутки - 1000бат
#     Неделя - 7000бат
#     Месяц - 25 000 бат
# Такие метки надёжнее всех прочих признаков: период назван прямым текстом.
# Когда в посте есть и суточная, и месячная цена, витрина должна брать месячную —
# она про долгую аренду, а не про отельные сутки.
_PRICE_LABELED = re.compile(
    r"\b(месяц|мес|month|monthly|неделя|нед|week|weekly|сутки|день|ночь|night|day|daily)\b"
    # между меткой и ценой часто стоит уточнение в скобках («Цена за месяц (март) — 80 000»)
    # или перенос строки («3 мес. ⏎ 25 000»). Метка здесь идёт ПЕРЕД ценой, поэтому
    # перенос не грозит склейкой соседних строк прайс-листа.
    r"[^\S\n]*(?:\([^)\n]{0,24}\))?[^\S\n]*(?:от|from)?[^\S\n]*[-–—:.,]{0,2}[^\S\n]*(?:\n[^\S\n]*){0,2}"
    r"(\d[\d\s]*(?:[.,]\d{3})?)[^\S\n]*(тыс\.?|k\b)?[^\S\n]*(?:฿|thb|baht|бат|б)?",
    re.IGNORECASE,
)
# Признак, что метка периода — хвост предыдущей цены («25 000 per month», «1500 бат в сутки»),
# а не заголовок следующей. Перевод строки намеренно запрещён: через строку это уже
# отдельная позиция прайс-листа.
_PRICE_TAIL_LABEL = re.compile(
    r"\d[\d\s]*(?:฿|thb|baht|бат|б)?[^\S\n]*(?:per|/|за|в)[^\S\n]*$",
    re.IGNORECASE,
)
_LABEL_PERIOD = {
    "месяц": "month", "мес": "month", "month": "month", "monthly": "month",
    "неделя": "week", "нед": "week", "week": "week", "weekly": "week",
    "сутки": "night", "день": "night", "ночь": "night",
    "night": "night", "day": "night", "daily": "night",
}


def _labeled_prices(text: str) -> dict[str, float]:
    """Цены из прайс-листа вида «Месяц - 25 000», сгруппированные по периоду.

    Берём первое вхождение каждого периода: дальше по тексту обычно идут цены
    на другие объекты («также есть виллы от 30 000») или на технику.
    """
    out: dict[str, float] = {}
    for m in _PRICE_LABELED.finditer(text):
        label, raw, mult = m.group(1), m.group(2), m.group(3)
        period = _LABEL_PERIOD.get(label.lower().rstrip("."))
        if not period or period in out:
            continue
        # «25000 per month 6000 security deposit»: метка описывает цену СЛЕВА, а справа
        # стоит депозит. Отличаем такой хвост по предлогу вплотную к метке (per/в/за//):
        # в прайс-листе «... 5 500 бат ⏎ Месяц - 20 000» его нет, там метка своя.
        if _PRICE_TAIL_LABEL.search(text[max(0, m.start() - 16):m.start()]):
            continue
        try:
            value = float(re.sub(r"[\s,.]", "", raw))
        except ValueError:
            continue
        if mult:                       # «Месяц - 38 тыс бат» / «Month - 38k»
            value *= 1000
        if value >= 500:
            out[period] = value
    return out


# Хвост поста, где владелец перечисляет ДРУГИЕ свои объекты: «также доступны виллы
# от 30 000», «есть ещё домики по 20 000». Их цены к текущему объявлению отношения не
# имеют, но стоят ближе к явному «/мес» и потому перебивали основную цену — вилла за
# 45 000 ฿ показывалась на витрине за 30 000 ฿. Для разбора цены хвост отрезаем.
_OTHER_OFFERS = re.compile(
    r"(?:^|\n|[.!?]\s*)\s*(?:✅\s*)?(?:а\s+)?"
    r"(?:так\s*же\s+есть|"
    r"также\s+(?:доступн|есть|сдаю|имеются)|"
    r"(?:есть|остал(?:о|и)сь|имеются)\s+(?:ещё|еще|также|другие)|"
    r"другие\s+(?:виллы|дома|варианты|объекты)|"
    r"other\s+(?:villas|houses|options)\s+available)",
    re.IGNORECASE,
)


def _main_offer(text: str) -> str:
    """Текст без перечисления других объектов владельца."""
    m = _OTHER_OFFERS.search(text)
    return text[:m.start()] if m and m.start() > 60 else text


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
    """Возвращает (price_amount, price_period).

    Порядок веток задаёт приоритет: сначала явно помеченная месячная цена, затем
    прочие месячные признаки, и только потом посуточные. Если в посте есть обе
    цены («сутки 1 500, месяц 25 000»), витрине нужна месячная — она про долгую
    аренду. Самые «слепые» паттерны (любое число рядом со словом «бат») стоят
    последними, чтобы не перебивать явные указания периода.
    """
    text = _main_offer(text)
    labeled = _labeled_prices(text)
    if "month" in labeled:
        return labeled["month"], "month"

    # помесячно — явный per month / в месяц (выше посуточного: если названы обе цены,
    # витрине нужна месячная)
    m = _PRICE.search(text)
    if m:
        raw = re.sub(r"[\s,]", "", m.group(1))
        try:
            v = float(raw)
            if v >= 1000:
                return v, "month"
        except ValueError:
            pass

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

    # Прайс-лист без месячной строки — берём суточную/недельную. Стоит выше «слепых»
    # паттернов ниже: там любое число рядом со словом «бат» уходит в догадку о периоде,
    # а здесь период назван прямым текстом.
    if "night" in labeled:
        return labeled["night"], "night"
    if "week" in labeled:
        return labeled["week"], "week"

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

    # цена в латинской записи валюты без периода: "45 000thb", "60000 baht"
    m = _PRICE_THB.search(text)
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
# Пометка «уже сдано» в самом посте. В Telegram такие правки редки (объявление
# чаще просто удаляют), но в группах Facebook приписка «RENTED»/«сдан» — норма,
# и без неё карточка живёт на витрине ещё три месяца, до авто-протухания.
# Формы подобраны узко, чтобы не задеть обычный текст: «сдаётся», «сдам» и ник
# @arendaphangan под это правило не подпадают.
_RENTED = re.compile(
    r"\brented(?:\s*out)?\b|\btaken\b|\bno longer\b|\bunavailable\b|"
    r"\bсдан[оаы]?\b|\bсдал[иа]\b|уже\s+сдан|больше\s+не\s+сда|"
    r"\bзанят[оа]\b|не\s?актуальн|\bзабронирован[оа]?\b",
    re.IGNORECASE,
)

def _parse_status(text: str) -> str:
    return "rented" if _RENTED.search(text) else "active"


# ── площадь ──────────────────────────────────────────────────────────────────
# Поле area_sqm раньше всегда оставалось пустым, хотя витрина умеет по нему
# фильтровать, сортировать и считать цену за квадрат — три фильтра и три сортировки
# просто ничего не находили.
#
# Главная ловушка — метры расстояния: «до пляжа 300 м» встречается чаще, чем площадь.
# Поэтому засчитываем только явный квадрат («кв.м», «м²», «sqm») либо число в скобках
# рядом со словом «метр» — так пишут именно про размер жилья: «Апартаменты (42 метра)».
_AREA_SQ = re.compile(
    r"(\d{2,4}(?:[.,]\d)?)\s*(?:кв\.?\s*м|м\s*кв|м²|м2|sq\.?\s*m|sqm|square\s+met)",
    re.IGNORECASE,
)
_AREA_PAREN = re.compile(r"\(\s*(\d{2,4})\s*(?:метр\w*|м)\s*\)", re.IGNORECASE)

_AREA_MIN, _AREA_MAX = 15, 1500  # меньше — комната в тексте про мебель, больше — участок


def _parse_area(text: str) -> float | None:
    for pattern in (_AREA_SQ, _AREA_PAREN):
        for raw in pattern.findall(text):
            try:
                value = float(str(raw).replace(",", "."))
            except ValueError:
                continue
            if _AREA_MIN <= value <= _AREA_MAX:
                return value
    return None


# ── окно доступности: дата заезда и срок аренды ─────────────────────────────
# Для того, кто ищет жильё на сезон, «с какого числа» и «на сколько» важнее половины
# остальных полей: дом за 20k, который освобождается только в марте, человеку,
# прилетающему в декабре, не нужен. Разбираем только явные формулировки — лучше
# промолчать на карточке, чем показать придуманный срок.
_MON_EN = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
           r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?")
_MONTH_EN = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
# Русский месяц берём словом целиком и сверяем по основе: голый префикс «мар» иначе
# находит месяц в «маркетплейсе», а «с 7 АПРЕЛЬЯ» (живая опечатка) не должен теряться.
_MON_RU = r"((?:янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)[а-яё]*)\.?"
_MONTH_RU_STEMS = (("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5),
                   ("июн", 6), ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10),
                   ("ноябр", 11), ("декабр", 12))
_MONTH_RU_SHORT = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "июн": 6, "июл": 7,
                   "авг": 8, "сен": 9, "сент": 9, "окт": 10, "ноя": 11, "нояб": 11, "дек": 12}


def _ru_month(word: str) -> int | None:
    w = word.lower()
    if w in _MONTH_RU_SHORT:
        return _MONTH_RU_SHORT[w]
    if w in ("май", "мая", "мае"):
        return 5
    for stem, num in _MONTH_RU_STEMS:
        if stem != "ма" and w.startswith(stem):
            return num
    return None


_FROM_EN = r"(?:\bavailable\s+(?:from|after|since|on)?\s*|\bavail\.?\s+(?:from\s+)?|\bfrom\s+|\bstarting\s+)"
_FROM_RU = (r"(?<![а-яё])(?:(?:доступ\w*|свобод\w*|сда[её]тся|сдается|заезд\w*|заселени\w*|освобожда\w*)\s+)?"
            r"(?:с|со|от|после)\s+")
_AVAIL_EN_DMY = re.compile(_FROM_EN + r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON_EN + r"(?:\s+(\d{4}))?(?!\d)", re.I)
_AVAIL_EN_MDY = re.compile(_FROM_EN + _MON_EN + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4})(?!\d))?", re.I)
_AVAIL_EN_M = re.compile(_FROM_EN + _MON_EN + r"(?:\s+(\d{4}))?(?!\s*\d)", re.I)
_AVAIL_RU = re.compile(_FROM_RU + r"(?:(\d{1,2})\s+)?" + _MON_RU + r"(?:\s+(\d{4})(?!\d))?", re.I)
# 14.04 / 14/04/2026 после «с / from / available»; «1.5 км» и время «с 10.30» отсекаем
_AVAIL_DATE = re.compile(
    r"(?:" + _FROM_EN + r"|" + _FROM_RU + r")"
    r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d.,:])(?!\s*(?:km|км|м\b|m\b|%|ч\b|h\b))",
    re.I,
)
_AVAIL_NOW = re.compile(
    r"\bavailable\s+(?:now|today|immediately|right\s+now)\b|\bready\s+to\s+move\s+in\b|"
    r"(?<![а-яё])(?:свобод\w*|доступ\w*)\s+(?:с\s+)?(?:сейчас|сегодня)|"
    r"(?<![а-яё])(?:сейчас|сегодня)\s+(?:свобод\w*|освобод\w*)",
    re.I,
)


def _ref_date(posted_at):
    """Точка отсчёта для дат без года — день публикации (иначе «с 1 марта» из поста
    2023 года превращается в март текущего года)."""
    import datetime
    try:
        return datetime.date.fromisoformat(str(posted_at)[:10])
    except (TypeError, ValueError):
        return datetime.date.today()


def _resolve_date(month: int, day: int | None, yr: str | None, ref) -> str | None:
    if not 1 <= month <= 12 or (day is not None and not 1 <= day <= 31):
        return None
    if yr:
        year = int(yr)
        year += 2000 if year < 100 else 0
        if not ref.year - 1 <= year <= ref.year + 2:
            return None
    else:
        # без года — ближайшее такое число после публикации; пару месяцев назад
        # («available from 1 march» в посте от 15 марта) — всё ещё этот год
        year = ref.year
        if (month, day or 1) < (ref.month, ref.day) and (ref.month - month) % 12 > 2:
            year += 1
    return f"{year}-{month:02d}-{day:02d}" if day else f"{year}-{month:02d}"


def _parse_available_from(text: str, posted_at=None) -> str | None:
    """ISO 'YYYY-MM-DD' или 'YYYY-MM' (день неизвестен), 'now' — либо None."""
    if _AVAIL_NOW.search(text):
        return "now"
    ref = _ref_date(posted_at)
    found: list[tuple[int, str]] = []   # (позиция, дата) — берём самое раннее упоминание

    for m in _AVAIL_DATE.finditer(text):
        r = _resolve_date(int(m.group(2)), int(m.group(1)), m.group(3), ref)
        if r:
            found.append((m.start(), r)); break
    for rx, order in ((_AVAIL_EN_DMY, "dm"), (_AVAIL_EN_MDY, "md"), (_AVAIL_EN_M, "m")):
        m = rx.search(text)
        if not m:
            continue
        if order == "dm":
            day, mon, yr = int(m.group(1)), m.group(2), m.group(3)
        elif order == "md":
            mon, day, yr = m.group(1), int(m.group(2)), m.group(3)
        else:
            mon, day, yr = m.group(1), None, m.group(2)
        r = _resolve_date(_MONTH_EN[mon.lower()[:3]], day, yr, ref)
        if r:
            found.append((m.start(), r))
    for m in _AVAIL_RU.finditer(text):
        month = _ru_month(m.group(2))
        if not month:
            continue
        r = _resolve_date(month, int(m.group(1)) if m.group(1) else None, m.group(3), ref)
        if r:
            found.append((m.start(), r)); break
    return min(found)[1] if found else None


_UNIT_M = r"(?:months?|mths?|mos?\b|мес(?:яц\w*)?\.?|м-ц\w*)"
_NUM = r"(\d{1,2})(?:\s*-?\s*(?:х|ти|ех|ух|x)(?![а-яёa-z]))?"
_RNG = _NUM + r"(?:\s*(?:[-–—]|to|до)\s*" + _NUM + r")?"
_STAY_PATTERNS = (
    # «Minimum 3 months», «Минимальный срок аренды 3 мес.», «min stay: 6 months»
    ("min", re.compile(r"(?:\bmin(?:imum|\.)?|(?<![а-яё])миним\w*)\s*(?:(?:stay|rent(?:al)?|term|period|contract|lease)\s*)?"
                       r"(?:срок\w*\s*)?(?:аренды\s*)?(?:is\s+|of\s+|[:\-–]\s*)?(?:от\s+)?" + _RNG + r"\s*" + _UNIT_M, re.I)),
    # «сроком на 6-9 месяцев», «Contract for 6 months», «сроком до 2–3 месяцев»
    ("term", re.compile(r"(?:(?<![а-яё])срок\w*|\bcontract|\blease(?:\s+term)?|\brental\s+term)\s*(?:аренды\s*)?(?:[:\-–]\s*)?"
                        r"(на|for|of|до|up\s+to)?\s*" + _RNG + r"\s*" + _UNIT_M, re.I)),
    # «Сдаётся на 7 месяцев», «available for 6 months»
    ("term", re.compile(r"(?:(?<![а-яё])сда[её]тся|(?<![а-яё])сда[юм]|\bavailable|\bfor\s+rent)\s+(на|for)\s+" + _RNG + r"\s*" + _UNIT_M, re.I)),
    # «(от 6 месяцев)», «от 3х месяц», «from 3 months», «от 3 до 6 месяцев»
    ("min", re.compile(r"(?:(?<![а-яё])от|\bfrom|\bat\s+least)\s+" + _RNG + r"\s*" + _UNIT_M, re.I)),
    # «долгосрочным гостям 6-12 мес» — голый диапазон
    ("range", re.compile(r"(?<![\d.,])(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*" + _UNIT_M, re.I)),
)
# Срок, который на самом деле про оплату/скидку/рассрочку, а не про аренду
_STAY_NOT_BEFORE = re.compile(r"(?:(?<![а-яё])(?:рассроч\w*|кредит\w*|оплат\w*|скидк\w*|за)|\b(?:installment|pay\w*|discount))\s*\S*\s*$", re.I)
_STAY_TIER_AFTER = re.compile(r"^[^\S\n]*[-–—:=]?[^\S\n]*(?:\d{1,3}[ .,]\d{3}|\d{4,}|\d+\s*(?:k|к|тыс))", re.I)
_STAY_LONG = re.compile(r"long[\s-]?term|долгосро\w*|длительн\w*\s+(?:срок|период|аренд)|на\s+долгий\s+срок|долгую\s+аренду", re.I)
_STAY_SEASON = re.compile(r"(?<![а-яё])на\s+(?:весь\s+)?сезон(?![а-яё])|\bfor\s+(?:the\s+)?(?:whole\s+)?season\b", re.I)


def _parse_stay(text: str) -> tuple[int | None, int | None, str | None]:
    """(мин. месяцев, макс. месяцев, подсказка 'long'|'season'|None)."""
    lo = hi = None
    for kind, rx in _STAY_PATTERNS:
        for m in rx.finditer(text):
            if _STAY_NOT_BEFORE.search(text[max(0, m.start() - 24):m.start()]):
                continue
            if not m.group(0).endswith(".") and _STAY_TIER_AFTER.match(text[m.end():m.end() + 14]):
                continue   # «3 месяца — 26 000»: ступень прайса, а не срок
            groups = [g for g in m.groups() if g and g.strip()]
            prep = groups[0].lower() if groups and not groups[0].isdigit() else None
            nums = [int(g) for g in groups if g.isdigit()]
            if not nums or not all(1 <= n <= 24 for n in nums):
                continue
            if len(nums) == 2 and nums[0] >= nums[1]:
                continue
            if kind == "range" and len(nums) != 2:
                continue
            if prep in ("до", "up to"):
                lo, hi = None, nums[-1]
            elif len(nums) == 2:
                lo, hi = nums
            elif kind == "term":
                lo = hi = nums[0]
            else:
                lo, hi = nums[0], None
            break
        if lo or hi:
            break
    hint = "long" if _STAY_LONG.search(text) else ("season" if _STAY_SEASON.search(text) else None)
    return lo, hi, hint


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
    if _SPAM.search(text) or _NOT_HOUSING.search(text):
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
    area = _parse_area(text)
    lang = _parse_language(text)
    contact = _parse_contact(text)
    available_from = _parse_available_from(text, post.get("posted_at"))
    min_stay, max_stay, stay_hint = _parse_stay(text)
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
            "area_sqm": area,
            "amenities": amenities,
            "available_from": available_from,
            "available_to": None,
            "min_stay_months": min_stay,
            "max_stay_months": max_stay,
            "stay_hint": stay_hint,
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
#!/usr/bin/env python3
"""
SabaiStay — кросс-источниковый дедуп (Фаза 4): один дом, выложенный в FB и Telegram, — одна карточка.

Два слоя дедупа:
  1) ТЕКСТОВАЯ сигнатура (этот файл) — бесплатно, по фактам: район + спальни + тип + цена (±допуск)
     + пересечение слов заголовка. Работает уже сейчас, без фото и ключей.
  2) Перцептивный хэш фото (pHash, таблица listing_phash) — платный слой поверх, когда потекут
     фото из FB. Ловит репосты с тем же фото при другом тексте. Здесь НЕ реализуем (нужны фото).

Принцип — лучше НЕ склеить два разных дома, чем ошибочно слить. Поэтому предикат консервативный:
требуем совпадения района и достаточно подтверждающих признаков.

Вход/выход — каноническая форма проекта (sample_listings.json / parse.py):
    {"source": "telegram", "source_url": "...", "parsed": {<поля listing_schema>}}

Чистый Python. Самотест:  python3 dedup.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PRICE_TOL = 0.12          # цены считаем «той же», если расходятся не больше чем на 12%
TITLE_JACCARD = 0.5       # порог пересечения слов заголовка как подтверждающий признак

_WORD = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_STOP = {"дом", "вилла", "house", "villa", "br", "спальни", "спален", "комнаты", "the", "in", "на", "и"}


def _tokens(title: str | None) -> set[str]:
    if not title:
        return set()
    return {w.lower() for w in _WORD.findall(title)} - _STOP


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _price_close(pa, pb) -> bool | None:
    """None — если хотя бы одной цены нет (не блокер и не подтверждение)."""
    if pa is None or pb is None:
        return None
    hi = max(pa, pb)
    return hi == 0 or abs(pa - pb) / hi <= PRICE_TOL


def same_object(a: dict, b: dict) -> bool:
    """True, если два разобранных объявления — почти наверняка один и тот же физический объект."""
    # ── жёсткие блокеры: явное расхождение → точно разные объекты ──
    da, db = a.get("district_canonical"), b.get("district_canonical")
    if da and db and da != db:
        return False
    ba, bb = a.get("bedrooms"), b.get("bedrooms")
    if ba is not None and bb is not None and ba != bb:
        return False
    ta, tb = a.get("property_type"), b.get("property_type")
    if ta not in (None, "other") and tb not in (None, "other") and ta != tb:
        return False
    pa, pb = a.get("price_amount"), b.get("price_amount")
    price_close = _price_close(pa, pb)
    if price_close is False:
        return False
    pera, perb = a.get("price_period"), b.get("price_period")
    if pera not in (None, "unknown") and perb not in (None, "unknown") and pera != perb:
        return False

    # ── подтверждения (нужно ≥2, и одно из них — общий район) ──
    title_close = _jaccard(_tokens(a.get("title")), _tokens(b.get("title"))) >= TITLE_JACCARD
    signals = sum([
        bool(da and db and da == db),                              # тот же район
        price_close is True,                                       # близкая цена
        ba is not None and bb is not None and ba == bb,            # те же спальни
        title_close,                                               # пересекающийся заголовок
    ])
    same_district = bool(da and db and da == db)
    return same_district and signals >= 2


def dedup(rows: list[dict]) -> list[dict]:
    """Сгруппировать объявления по объекту (union-find). Вернёт список групп, в каждой — представитель + источники."""
    n = len(rows)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    # блокинг по району: сравниваем только внутри одного канонического района (+ «без района» отдельно),
    # чтобы не гонять O(n²) по всей базе на больших объёмах.
    blocks: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        blocks.setdefault(r["parsed"].get("district_canonical") or "∅", []).append(i)
    for idxs in blocks.values():
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                i, j = idxs[ii], idxs[jj]
                if same_object(rows[i]["parsed"], rows[j]["parsed"]):
                    union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out = []
    for gid, members in enumerate(sorted(groups.values(), key=lambda m: -len(m)), 1):
        ms = [rows[i] for i in members]
        rep = max(ms, key=lambda r: (r["parsed"].get("confidence") or 0,
                                     sum(1 for v in r["parsed"].values() if v not in (None, "", "unknown"))))
        out.append({
            "group_id": gid,
            "size": len(ms),
            "representative": rep,
            "sources": [{"source": m["source"], "source_url": m["source_url"]} for m in ms],
        })
    return out


# ─────────────────────────── самотест ───────────────────────────
def _selftest() -> None:
    root = Path(__file__).resolve().parent.parent
    rows = json.loads((root / "data" / "sample_listings.json").read_text(encoding="utf-8"))["listings"]
    listings = [r for r in rows if r["parsed"]["is_listing"]]

    groups = dedup(listings)
    merged = [g for g in groups if g["size"] > 1]
    print(f"Реальная база: {len(listings)} объявлений → {len(groups)} объектов "
          f"({len(merged)} групп с дублями)")
    for g in merged:
        d = g["representative"]["parsed"]
        print(f"  • {d.get('district_canonical') or '—'} {d.get('title') or '—'}: {g['size']} источника")

    # ── контролируемая пара «один дом в TG и FB» — должна слиться ──
    same = [
        {"source": "telegram", "source_url": "https://t.me/koh_phangan_rent/1",
         "parsed": {"is_listing": True, "title": "Вилла 2 спальни Sri Thanu бассейн",
                    "district_canonical": "Sri Thanu", "bedrooms": 2, "property_type": "villa",
                    "price_amount": 45000, "price_period": "month", "confidence": 0.9, "amenities": {}}},
        {"source": "fb_group", "source_url": "https://facebook.com/groups/x/posts/2",
         "parsed": {"is_listing": True, "title": "2 bedroom villa Sri Thanu with pool",
                    "district_canonical": "Sri Thanu", "bedrooms": 2, "property_type": "villa",
                    "price_amount": 47000, "price_period": "month", "confidence": 0.8, "amenities": {}}},
    ]
    g = dedup(same)
    assert len(g) == 1 and g[0]["size"] == 2, "одинаковый дом из TG и FB должен слиться"
    assert {s["source"] for s in g[0]["sources"]} == {"telegram", "fb_group"}
    print(f"\n✓ кросс-источник: TG+FB слиты в 1 объект, {g[0]['size']} источника")

    # ── контроль на ложное слияние: тот же район/спальни, но другой тип и далёкая цена ──
    diff = [
        same[0],
        {"source": "telegram", "source_url": "https://t.me/x/3",
         "parsed": {"is_listing": True, "title": "Студия Sri Thanu",
                    "district_canonical": "Sri Thanu", "bedrooms": 0, "property_type": "studio",
                    "price_amount": 12000, "price_period": "month", "confidence": 0.9, "amenities": {}}},
    ]
    assert len(dedup(diff)) == 2, "разные объекты не должны сливаться"
    print("✓ контроль: вилла 45k и студия 12k в одном районе НЕ слиты")
    print("\n✓ dedup: все проверки пройдены")


if __name__ == "__main__":
    _selftest()

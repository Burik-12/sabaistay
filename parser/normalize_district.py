#!/usr/bin/env python3
"""
SabaiStay — нормализатор районов Пхангана.

Свободное название района из объявления ("Hinkong", "Sri tanu", "Madurwan road") →
канонический район из data/districts.json + координаты центроида (для карты).

Двухступенчато: (1) точное совпадение по алиасам/канону (подстрока), (2) fuzzy-добор
по токенам через difflib. Чистый Python, без внешних зависимостей и сети — тестируется
офлайн. Используется после LLM-парсера как детерминированная «защёлка» к канону.

    from normalize_district import DistrictNormalizer
    dn = DistrictNormalizer()
    dn.normalize("Hinkong")       # -> {'canonical': 'Hin Kong', 'lat': 9.748, 'lng': 99.9785, 'match': 'exact'}
    dn.normalize("Madurwan road") # -> {'canonical': 'Madeua Wan', ..., 'match': 'exact'}
    dn.normalize("somewhere")     # -> None
"""
from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "districts.json"
_WORD = re.compile(r"[a-zа-яё]+", re.IGNORECASE)


class DistrictNormalizer:
    def __init__(self, gazetteer_path: Path | str = _DEFAULT_PATH, fuzzy_cutoff: float = 0.86):
        data = json.loads(Path(gazetteer_path).read_text(encoding="utf-8"))
        self.fuzzy_cutoff = fuzzy_cutoff
        self._coords: dict[str, dict] = {}
        self._alias_to_canon: dict[str, str] = {}
        for d in data["districts"]:
            canon = d["canonical"]
            self._coords[canon] = {"lat": d["lat"], "lng": d["lng"]}
            keys = [canon.lower()] + [a.lower() for a in d.get("aliases", [])]
            for k in keys:
                self._alias_to_canon[k] = canon
        # длинные алиасы проверяем первыми (чтобы "thong nai pan noi" не схлопнулся в "thong")
        self._aliases_by_len = sorted(self._alias_to_canon, key=len, reverse=True)

    def _result(self, canon: str, match: str) -> dict:
        return {"canonical": canon, **self._coords[canon], "match": match}

    def normalize(self, text: str | None) -> dict | None:
        if not text:
            return None
        t = text.lower().strip()

        # 1) точное совпадение по подстроке (длинные алиасы вперёд)
        for alias in self._aliases_by_len:
            if alias in t:
                return self._result(self._alias_to_canon[alias], "exact")

        # 2) fuzzy по полной строке и по отдельным словам
        best_canon, best_score = None, 0.0
        candidates = list(self._alias_to_canon.items())
        probes = [t] + _WORD.findall(t)
        for probe in probes:
            if len(probe) < 4:
                continue
            for alias, canon in candidates:
                score = SequenceMatcher(None, probe, alias).ratio()
                if score > best_score:
                    best_canon, best_score = canon, score
        if best_canon and best_score >= self.fuzzy_cutoff:
            return self._result(best_canon, f"fuzzy:{best_score:.2f}")
        return None


# Самотест на реальных написаниях из koh_phangan_rent
_SELFTEST = [
    ("Hinkong", "Hin Kong"), ("Hin Kong", "Hin Kong"), ("Sri tanu", "Sri Thanu"),
    ("Woktum", "Wok Tum"), ("Thong sala", "Thong Sala"), ("Madurwan road", "Madeua Wan"),
    ("Haad yao", "Haad Yao"), ("Chaloklum", "Chaloklum"), ("Bantai", "Ban Tai"),
    ("Thong nai pan noi", "Thong Nai Pan Noi"), ("somewhere unknown", None),
]

if __name__ == "__main__":
    dn = DistrictNormalizer()
    ok = 0
    for raw, expected in _SELFTEST:
        res = dn.normalize(raw)
        got = res["canonical"] if res else None
        mark = "✓" if got == expected else "✗"
        if got == expected:
            ok += 1
        extra = f"  [{res['match']}]" if res else ""
        print(f"{mark} {raw:22} → {str(got):20} (ждали {expected}){extra}")
    print(f"\n{ok}/{len(_SELFTEST)} прошло")
    sys.exit(0 if ok == len(_SELFTEST) else 1)

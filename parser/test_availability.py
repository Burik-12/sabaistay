#!/usr/bin/env python3
"""
Проверка разбора окна доступности: дата заезда и срок аренды.

Кейсы — живые формулировки из каналов Пхангана (data/sample_listings.json), часть
сокращена. Отдельно — ловушки, где число месяцев в тексте есть, но это не срок аренды:
ступени прайса («Pay 3 month 78,000»), рассрочка на байк, «минимум 3 дня».

    python3 parser/test_availability.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_lite import _parse_available_from, _parse_stay  # noqa: E402

# (текст, дата публикации, ожидаемое available_from, чем кейс важен)
DATE_CASES = [
    ("Haad yao Available from 18 march 25000 per month", "2026-03-02", "2026-03-18", "англ. день + месяц"),
    ("Available after 19 January Contract for 6 months", "2025-12-20", "2026-01-19", "after + переход года"),
    ("Available from 1 February", "2024-11-10", "2025-02-01", "без года: ближайший февраль после поста"),
    ("Hot offer. available today for long term rental. 25,000 THB per month.", "2025-01-10", "now", "available today"),
    ("Аренда доступна с 1 мая сроком на 6-9 месяцев.50 000 месяц.", "2026-04-02", "2026-05-01", "рус. «доступна с 1 мая»"),
    ("📆 ЖИЛЬЕ ДОСТУПНО: • С 7 АПРЕЛЬЯ • 27000 бат/месяц", "2026-03-20", "2026-04-07", "живая опечатка «АПРЕЛЬЯ»"),
    ("Сдается с 14.04 Возможно посуточно и на долгосрок🙌🏼", "2026-04-01", "2026-04-14", "дата цифрами"),
    ("дом 2 спальни/2 санузлах. Со 2 февраля от 3х месяц. Район Тонг Най Пан.", "2025-01-15", "2025-02-02", "«Со 2 февраля»"),
    ("В Шритану!! Доступен с 23 февраля. 10 минуту до пляжа", "2026-02-10", "2026-02-23", "«Доступен с»"),
    ("комната 17000 месяц, свободен с сегодня по 7 декабря.", "2025-11-01", "now", "«свободен с сегодня»"),
    ("С 1 декабря свободна вилла в Шритану, 45к", "2025-11-05", "2025-12-01", "день без года в том же году"),
    ("Available from Dec 1, 2025. 30000 THB monthly", "2025-10-01", "2025-12-01", "месяц-день-год"),
    ("Available from January", "2025-11-20", "2026-01", "только месяц"),
    ("Свободно с марта, Бан Тай, 20 000", "2026-01-10", "2026-03", "рус. только месяц"),
    ("Available from 11 march, possible for long term", "2026-03-15", "2026-03-11", "дата чуть раньше поста — тот же год"),
    # ловушки
    ("Дом нашли с маркетплейса, 2 спальни, 25000", "2026-01-10", None, "«мар» в «маркетплейсе» — не март"),
    ("Вилла в 1.5 км от пляжа, from 1.5 km to the beach", "2026-01-10", None, "1.5 км — не дата"),
    ("Цена от 25 000 бат, 2 спальни", "2026-01-10", None, "«от» перед ценой"),
    ("Сдам дом, 30000 в месяц, Шритану", "2026-01-10", None, "даты нет — ничего не выдумываем"),
]

# (текст, ожидаемые (мин, макс, подсказка), чем кейс важен)
STAY_CASES = [
    ("able today 9500 per month 10000 deposit Minimum 3 months rent Hot water", (3, None, None), "Minimum N months"),
    ("Available for long term rent Minimum 3 months contract 28000 per month", (3, None, "long"), "минимум + долгосрок"),
    ("tranquility, and stunning mountain views Minimum: 6 months ✅ Included", (6, None, None), "двоеточие"),
    ("Минимальный срок аренды 3 мес. \n\n25 000 при оплате за 3 мес сразу.", (3, None, None), "рус. «Минимальный срок аренды»"),
    ("💰 23 000฿ / месяц ( от 6 месяцев) Пишите в личку", (6, None, None), "«(от 6 месяцев)»"),
    ("Со 2 февраля от 3х месяц. Район Тонг Най Пан.", (3, None, None), "«от 3х месяц»"),
    ("Аренда доступна с 1 мая сроком на 6-9 месяцев.50 000 месяц.", (6, 9, None), "«сроком на 6-9»"),
    ("Available after 19 January Contract for 6 months Deposit 10000 bath", (6, 6, None), "Contract for N months"),
    ("Пришлю фото и больше информации. Сдаётся на 7 месяцев.", (7, 7, None), "«Сдаётся на 7 месяцев»"),
    ("хэтажный дом в стиле эко-шале сроком до 2–3 месяцев. Дом расположен", (None, 3, None), "«сроком до 2–3»"),
    ("СРОК (Предпочтение долгосрочным гостям 6-12 мес!) 3 спальни вилла", (6, 12, "long"), "голый диапазон"),
    ("Rent from 3 to 6 months, 35000 THB", (3, 6, None), "англ. from N to M"),
    ("Сдам виллу на сезон, Хинконг", (None, None, "season"), "«на сезон»"),
    ("Villa for the whole season, Sri Thanu", (None, None, "season"), "for the whole season"),
    ("Хинконг / Шритану на длительный период (полгода, год).", (None, None, "long"), "длительный период"),
    # ловушки: месяцы есть, срока нет
    ("Available from 1 march 1 month 28,000 bath Pay 3 month 78,000 bath", (None, None, None), "ступени прайса Pay N month"),
    ("Даём рассрочку на срок до 5 месяцев (подробности у менеджера)", (None, None, None), "рассрочка на байк"),
    ("500 бат в сутки (минимум 3 дня)", (None, None, None), "минимум в днях — не месяцы"),
    ("17к бат / месяц 15к бат при оплате за 3 месяца сразу", (None, None, None), "скидка за оплату"),
    ("бережной эксплуатации 1.5 месяца (животных не было)", (None, None, None), "1.5 месяца б/у"),
    ("Цена на высокий сезон 60 000thb", (None, None, None), "цена на высокий сезон — не срок"),
    ("- 35000 бат за 1 месяц - 34000 бат в месяц на 3 месяца (оплата", (None, None, None), "прайс по срокам"),
]


def main() -> None:
    failed = 0
    for text, posted, exp, why in DATE_CASES:
        got = _parse_available_from(text, posted)
        ok = got == exp
        failed += not ok
        print(f"  {'✓' if ok else '✗'} {str(got):12} {why}")
        if not ok:
            print(f"      ожидалось {exp!r}; текст: {' '.join(text.split())[:100]}")
    for text, exp, why in STAY_CASES:
        got = _parse_stay(text)
        ok = got == exp
        failed += not ok
        print(f"  {'✓' if ok else '✗'} {str(got):22} {why}")
        if not ok:
            print(f"      ожидалось {exp!r}; текст: {' '.join(text.split())[:100]}")

    total = len(DATE_CASES) + len(STAY_CASES)
    if failed:
        print(f"\n✗ доступность: провалено {failed} из {total}")
        sys.exit(1)
    print(f"\n✓ доступность: все {total} проверок пройдены")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
SabaiStay — Telegram-бот «подписка на фильтр» (Фаза 4, киллер-фича).

Ценность: в FB Marketplace нет фильтра, в Telegram-каналах не найти. Здесь человек один раз
описывает, что ищет — «сри тану, до 30к, 2 спальни, бассейн» — и получает пуш, как только
ПОЯВЛЯЕТСЯ новый подходящий дом. Поверх уже готовой базы это почти бесплатно.

Что готово и тестируется СЕЙЧАС (без денег, без сети):
  • parse_query()  — естественный запрос (рус/англ) → критерии фильтра (saved_filter.criteria);
  • matches()      — из parser/filter_match.py, общий с витриной;
  • хранилище подписок — NDJSON (MVP; позже таблица saved_filter в Postgres).
Что ждёт платную фазу:
  • токен бота (бесплатно у @BotFather, но смысл есть, когда в базу текут новые объявления);
  • вызов notify_new_listings() из пайплайна сбора → парсер → нормализация → база.

Запуск:
    python3 subscribe_bot.py --demo                 # офлайн: разобрать запрос и показать совпадения
    export SABAISTAY_BOT_TOKEN=...                   # токен @BotFather
    pip install python-telegram-bot && python3 subscribe_bot.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))
from filter_match import matches, match_listings, describe  # noqa: E402

DISTRICTS_PATH = ROOT / "data" / "districts.json"
SUBS_PATH = ROOT / "data" / "subscriptions.ndjson"   # MVP-хранилище подписок (в .gitignore)

# ────────────────────────── разбор запроса ──────────────────────────
_NUM = r"(\d[\d\s.,]*)"                                # число с пробелами/разделителями тысяч

_TYPE_WORDS = [
    (r"вилл|villa", "villa"), (r"дом|house", "house"), (r"студи|studio", "studio"),
    (r"бунгало|bungalow", "bungalow"), (r"апарт|кондо|condo|apart", "apartment"),
    (r"комнат|room", "room"),
]
_AMENITY_WORDS = [
    (r"бассейн|pool", "pool"), (r"seaview|sea view|вид на мор|у мор|с видом", "seaview"),
    (r"кондиционер|кондей|aircon|a/c", "aircon"), (r"wi-?fi|вай-?фай|интернет", "wifi"),
    (r"кухн|kitchen", "kitchen"), (r"стиралк|washing", "washing_machine"),
    (r"питом|собак|кош|pet", "pets_allowed"), (r"байк|мотобайк|scooter|bike", "motorbike"),
]


def _to_int(raw: str, has_k: bool) -> int:
    n = float(re.sub(r"[\s.,]", "", raw))
    return int(n * 1000) if has_k else int(n)


def _districts_index() -> list[tuple[str, str]]:
    """[(нижний-регистр алиас, канон)], длинные алиасы первыми — чтобы 'sri thanu' бил раньше 'thanu'."""
    data = json.loads(DISTRICTS_PATH.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for d in data["districts"]:
        for name in [d["canonical"], *d.get("aliases", [])]:
            pairs.append((name.lower(), d["canonical"]))
    return sorted(pairs, key=lambda p: -len(p[0]))


_DISTRICT_INDEX = _districts_index()


def parse_query(text: str) -> dict:
    """Свободный текст → критерии фильтра. Лучше недо-извлечь, чем выдумать (пустой фильтр ловит всё)."""
    t = " " + text.lower().strip() + " "
    crit: dict = {}

    # район(ы) — все совпавшие алиасы из газеттира
    found: list[str] = []
    for alias, canon in _DISTRICT_INDEX:
        if re.search(r"\b" + re.escape(alias) + r"\b", t) and canon not in found:
            found.append(canon)
    if found:
        crit["district"] = found[0] if len(found) == 1 else found

    # цена: «до N» / «от N» / «N-M» (k/к = ×1000). Число < 1000 без «к» — это не цена,
    # а скорее спальни («от 2 спален»): аренда на Пхангане измеряется тысячами.
    def grab(pattern: str):
        m = re.search(pattern, t)
        if not m:
            return None
        has_k = bool(re.match(r"\s*[kк]", t[m.end():]))
        val = _to_int(m.group(1), has_k)
        return val if (has_k or val >= 1000) else None

    rng = re.search(_NUM + r"\s*[-–—]\s*" + _NUM, t)
    rng_lo = rng_hi = None
    if rng:
        rng_lo, rng_hi = sorted((_to_int(rng.group(1), False), _to_int(rng.group(2), False)))
    if rng_hi is not None and rng_hi >= 1000:          # настоящий ценовой диапазон
        crit["price_min"], crit["price_max"] = rng_lo, rng_hi
    else:                                              # маленький диапазон — это про спальни, не цена
        pmax = grab(r"(?:до|<|≤|не дороже|макс\w*|under|max)\D{0,4}" + _NUM)
        pmin = grab(r"(?:от|>|≥|более|min)\D{0,4}" + _NUM)
        if pmax is not None:
            crit["price_max"] = pmax
        if pmin is not None:
            crit["price_min"] = pmin

    # тип цены
    if re.search(r"ноч|посуточ|/night|за ночь", t):
        crit["period"] = "night"
    elif re.search(r"продаж|куплю|на продажу|for sale", t):
        crit["period"] = "sale"
    elif re.search(r"мес|/mo|month|долгосро|long term", t):
        crit["period"] = "month"

    # спальни: «студия» → 0; «N-M спален» → диапазон; «от N» → min; «N спал/BR» → точное
    if re.search(r"студи|studio", t):
        crit["bedrooms"] = 0
    else:
        brange = re.search(r"(\d+)\s*[-–—]\s*(\d+)\s*(?:спал|bed|br)", t)
        bmin = re.search(r"от\s*(\d+)\s*(?:спал|bed|br)", t)
        bexact = re.search(r"(\d+)\s*(?:спал|комнат|bed|br)", t)
        if brange:
            crit["bedrooms_min"], crit["bedrooms_max"] = sorted(
                (int(brange.group(1)), int(brange.group(2))))
        elif bmin:
            crit["bedrooms_min"] = int(bmin.group(1))
        elif bexact:
            crit["bedrooms"] = int(bexact.group(1))

    # тип объекта и удобства
    for pat, val in _TYPE_WORDS:
        if re.search(pat, t):
            crit["property_type"] = val
            break
    for pat, key in _AMENITY_WORDS:
        if re.search(pat, t):
            crit[key] = True

    return crit


# ────────────────────────── хранилище подписок ──────────────────────────
def load_subs() -> list[dict]:
    if not SUBS_PATH.exists():
        return []
    out = []
    for line in SUBS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def save_subs(subs: list[dict]) -> None:
    SUBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUBS_PATH.write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in subs), encoding="utf-8")


def add_sub(user_id: int, criteria: dict, mode: str = "instant") -> None:
    subs = load_subs()
    subs.append({"user_id": user_id, "criteria": criteria, "active": True,
                 "mode": mode, "pending": [], "last_notified": None})
    save_subs(subs)


def user_subs(user_id: int) -> list[dict]:
    return [s for s in load_subs() if s["user_id"] == user_id and s.get("active", True)]


# ────────────────────────── рассылка новинок ──────────────────────────
async def notify_new_listings(bot, new_listings: list[dict]) -> int:
    """Пайплайн зовёт это на свежей пачке объявлений. instant-подписки — пуш сразу; digest — в очередь."""
    sent = 0
    subs = load_subs()
    changed = False
    for sub in subs:
        if not sub.get("active", True):
            continue
        hits = match_listings(new_listings, sub["criteria"])
        if not hits:
            continue
        if sub.get("mode") == "digest":
            sub.setdefault("pending", []).extend(hits)   # копим, отдадим раз в день
            changed = True
        else:
            for r in hits:
                await bot.send_message(chat_id=sub["user_id"], text=_listing_card(r),
                                       parse_mode="HTML", disable_web_page_preview=False)
                sent += 1
    if changed:
        save_subs(subs)
    return sent


def _digest_line(row: dict) -> str:
    p = row.get("parsed", row)
    price = f"{p['price_amount']:,}".replace(",", " ") + "฿" if p.get("price_amount") else "цена в источнике"
    bd = "студия" if p.get("bedrooms") == 0 else (f"{p['bedrooms']}BR" if p.get("bedrooms") else "")
    bits = " · ".join(x for x in (p.get("district_canonical"), bd, price) if x)
    return f"• {bits} — {row.get('source_url', '')}"


def build_digest(sub: dict, max_items: int = 10) -> str:
    """Чистый форматтер дайджеста (без сети) — тестируется офлайн."""
    items = sub.get("pending", [])
    head = f"🌅 Свежее по фильтру «{describe(sub['criteria'])}» — {len(items)} шт.:"
    lines = [head] + [_digest_line(r) for r in items[:max_items]]
    if len(items) > max_items:
        lines.append(f"…и ещё {len(items) - max_items}")
    return "\n".join(lines)


async def send_digests(bot) -> int:
    """Раз в день (по расписанию/cron): отдать накопленное digest-подписчикам и очистить очередь."""
    subs = load_subs()
    sent = 0
    for sub in subs:
        if sub.get("mode") == "digest" and sub.get("pending"):
            await bot.send_message(chat_id=sub["user_id"], text=build_digest(sub),
                                   parse_mode="HTML", disable_web_page_preview=True)
            sub["pending"] = []
            sent += 1
    if sent:
        save_subs(subs)
    return sent


def _listing_card(row: dict) -> str:
    p = row.get("parsed", row)
    price = f"{p['price_amount']:,}".replace(",", " ") + "฿" if p.get("price_amount") else "цена в источнике"
    unit = {"month": "/мес", "night": "/ночь", "sale": ""}.get(p.get("price_period"), "")
    bd = "студия" if p.get("bedrooms") == 0 else (f"{p['bedrooms']} спальни" if p.get("bedrooms") else "")
    head = p.get("title") or p.get("district_canonical") or "Новое объявление"
    bits = " · ".join(x for x in (p.get("district_canonical"), bd, price + unit) if x)
    return f"🏝 <b>{head}</b>\n{bits}\n🔗 {row.get('source_url', '')}"


# ────────────────────────── Кнопочный билдер фильтра ──────────────────────────
# Чистая часть (без telegram): варианты кнопок + сборка черновика в критерии. Клавиатура — в run().
BUILD_DISTRICTS = ["Sri Thanu", "Hin Kong", "Thong Sala", "Ban Tai",
                   "Haad Yao", "Chaloklum", "Thong Nai Pan Noi", "Wok Tum"]
BUILD_PRICES = [("≤15к", 15000), ("≤20к", 20000), ("≤30к", 30000), ("≤50к", 50000), ("любая", 0)]
BUILD_BEDS = [("студия", 0), ("1+", 1), ("2+", 2), ("3+", 3), ("любое", -1)]
BUILD_AMENS = [("🏊", "pool"), ("🌊", "seaview"), ("📶", "wifi"), ("🍳", "kitchen")]


def draft_to_criteria(draft: dict) -> dict:
    """Черновик из кнопок → критерии фильтра (saved_filter.criteria). Период по умолчанию — помесячно."""
    c: dict = {"period": "month"}
    if draft.get("district"):
        c["district"] = draft["district"]
    if draft.get("price_max"):
        c["price_max"] = draft["price_max"]
    bed = draft.get("bed")
    if bed == 0:
        c["bedrooms"] = 0
    elif bed and bed > 0:
        c["bedrooms_min"] = bed
    for a in draft.get("am", []):
        c[a] = True
    return c


def draft_is_empty(draft: dict) -> bool:
    return list(draft_to_criteria(draft).keys()) == ["period"]


# ────────────────────────── Telegram-бот ──────────────────────────
HELP = (
    "Я слежу за арендой на Пхангане и пишу, когда появляется дом под твой фильтр.\n\n"
    "🔘 <b>/new</b> — собрать фильтр кнопками (просто и быстро)\n"
    "📝 <b>/subscribe</b> запрос — то же текстом. Примеры:\n"
    "   <code>/subscribe сри тану, до 30к, 2 спальни, бассейн</code>\n"
    "   <code>/subscribe вилла у моря, 1-2 спальни, до 50000/мес</code>\n"
    "📋 <b>/myfilters</b> — мои подписки\n"
    "❌ <b>/unsubscribe N</b> — убрать подписку №N\n\n"
    "Режим у каждой подписки: 🔔 моментально или 🌅 дайджест раз в день (выбираешь в /new)."
)


def run() -> None:
    token = os.getenv("SABAISTAY_BOT_TOKEN")
    if not token:
        sys.exit("Нет SABAISTAY_BOT_TOKEN. Создай бота у @BotFather. Офлайн-проверка: --demo")
    try:
        from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
    except ImportError:
        sys.exit("Нужен пакет: pip install python-telegram-bot")

    def kb_for(draft: dict):
        """Инлайн-клавиатура билдера: текущий выбор помечен ✓, тумблер режима, сохранить/отмена."""
        d = draft.get("district")
        rows = [
            [InlineKeyboardButton(("✓ " if d == x else "") + x, callback_data="b|d|" + x) for x in BUILD_DISTRICTS[:4]],
            [InlineKeyboardButton(("✓ " if d == x else "") + x, callback_data="b|d|" + x) for x in BUILD_DISTRICTS[4:]],
            [InlineKeyboardButton(("✓ " if draft.get("price_max") == v else "") + lbl, callback_data="b|p|" + str(v)) for lbl, v in BUILD_PRICES],
            [InlineKeyboardButton(("✓ " if draft.get("bed") == v else "") + lbl, callback_data="b|bed|" + str(v)) for lbl, v in BUILD_BEDS],
            [InlineKeyboardButton(("✓" if a in draft.get("am", []) else "") + ic, callback_data="b|am|" + a) for ic, a in BUILD_AMENS],
            [InlineKeyboardButton("🔔 моментально" if draft.get("mode", "instant") == "instant" else "🌅 дайджест раз в день", callback_data="b|mode|x")],
            [InlineKeyboardButton("✅ Сохранить", callback_data="b|save"), InlineKeyboardButton("✖ Отмена", callback_data="b|cancel")],
        ]
        return InlineKeyboardMarkup(rows)

    def build_text(draft: dict) -> str:
        return "🔘 Собери фильтр кнопками:\n<b>" + describe(draft_to_criteria(draft)) + "</b>"

    async def new_cmd(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        ctx.user_data["draft"] = {"am": []}
        await update.message.reply_text(build_text(ctx.user_data["draft"]),
                                        parse_mode="HTML", reply_markup=kb_for(ctx.user_data["draft"]))

    async def on_build(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        q = update.callback_query
        await q.answer()
        draft = ctx.user_data.setdefault("draft", {"am": []})
        parts = q.data.split("|")
        field = parts[1]
        if field == "cancel":
            ctx.user_data.pop("draft", None)
            await q.edit_message_text("Отменил. /new — собрать заново.")
            return
        if field == "save":
            if draft_is_empty(draft):
                await q.answer("Выбери хотя бы район, цену или спальни", show_alert=True)
                return
            crit = draft_to_criteria(draft)
            mode = draft.get("mode", "instant")
            add_sub(q.from_user.id, crit, mode)
            ctx.user_data.pop("draft", None)
            await q.edit_message_text(f"✅ Подписка создана:\n<b>{describe(crit)}</b>\n"
                                      f"Режим: {'🌅 дайджест раз в день' if mode == 'digest' else '🔔 моментально'}",
                                      parse_mode="HTML")
            return
        if field == "d":
            draft["district"] = None if draft.get("district") == parts[2] else parts[2]
        elif field == "p":
            v = int(parts[2]); draft["price_max"] = None if (v == 0 or draft.get("price_max") == v) else v
        elif field == "bed":
            v = int(parts[2]); draft["bed"] = None if (v == -1 or draft.get("bed") == v) else v
        elif field == "am":
            am = draft.setdefault("am", [])
            am.remove(parts[2]) if parts[2] in am else am.append(parts[2])
        elif field == "mode":
            draft["mode"] = "digest" if draft.get("mode", "instant") == "instant" else "instant"
        await q.edit_message_text(build_text(draft), parse_mode="HTML", reply_markup=kb_for(draft))

    async def start(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        await update.message.reply_text("Привет! " + HELP, parse_mode="HTML")

    async def subscribe(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        query = " ".join(ctx.args)
        if not query:
            await update.message.reply_text("Опиши, что ищешь. Пример:\n/subscribe сри тану, до 30к, 2 спальни")
            return
        crit = parse_query(query)
        if not crit:
            await update.message.reply_text("Не понял фильтр. Укажи район, цену или число спален.")
            return
        add_sub(update.effective_user.id, crit)
        await update.message.reply_text(f"✅ Подписка создана:\n<b>{describe(crit)}</b>\n"
                                        "Напишу, как появится подходящее.", parse_mode="HTML")

    async def myfilters(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        subs = user_subs(update.effective_user.id)
        if not subs:
            await update.message.reply_text("Подписок пока нет. /subscribe чтобы добавить.")
            return
        lines = [f"{i}. {describe(s['criteria'])}" for i, s in enumerate(subs, 1)]
        await update.message.reply_text("Твои подписки:\n" + "\n".join(lines))

    async def unsubscribe(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        uid = update.effective_user.id
        try:
            idx = int(ctx.args[0]) - 1
        except (IndexError, ValueError):
            await update.message.reply_text("Укажи номер: /unsubscribe 1 (см. /myfilters)")
            return
        mine = user_subs(uid)
        if not 0 <= idx < len(mine):
            await update.message.reply_text("Нет подписки с таким номером.")
            return
        target = mine[idx]
        subs = load_subs()
        for s in subs:
            if s is target or (s["user_id"] == uid and s["criteria"] == target["criteria"]):
                s["active"] = False
                break
        save_subs(subs)
        await update.message.reply_text("Подписка убрана.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("myfilters", myfilters))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CallbackQueryHandler(on_build, pattern=r"^b\|"))
    print("Бот запущен (polling).", file=sys.stderr)
    app.run_polling()


# ────────────────────────── офлайн-демо ──────────────────────────
def demo() -> None:
    rows = json.loads((ROOT / "data" / "sample_listings.json").read_text(encoding="utf-8"))["listings"]
    listings = [r for r in rows if r["parsed"]["is_listing"]]
    queries = [
        "сри тану, до 30к, 2 спальни, бассейн",
        "вилла у моря до 50000 в месяц",
        "хин конг или сри тану, любой бюджет",
        "студия до 15к",
        "от 2 спален, бассейн",
    ]
    for q in queries:
        crit = parse_query(q)
        hits = match_listings(listings, crit)
        print(f'\n💬 «{q}»')
        print(f'   → фильтр: {describe(crit)}')
        print(f'   → {len(hits)} совпадений из {len(listings)} объявлений базы')
        for r in hits[:3]:
            p = r["parsed"]
            print(f'      • {(p.get("district_canonical") or "—"):14} {p.get("title") or "—"}  {r["source_url"]}')

    # кнопочный билдер: черновик из тапов → те же критерии
    draft = {"district": "Sri Thanu", "price_max": 30000, "bed": 2, "am": ["pool"], "mode": "digest"}
    print("\n🔘 Кнопочный билдер (черновик из тапов):")
    print(f'   район=Sri Thanu · ≤30к · 2+ · 🏊 · режим=дайджест  →  {describe(draft_to_criteria(draft))}')

    # дайджест: накопили совпадения → одно сообщение раз в день
    sub = {"criteria": {"district": ["Hin Kong", "Sri Thanu"]},
           "pending": match_listings(listings, {"district": ["Hin Kong", "Sri Thanu"]})}
    print("\n🌅 Пример дайджеста (вместо пуша на каждый дом):")
    for line in build_digest(sub).splitlines():
        print("   " + line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SabaiStay — бот «подписка на фильтр»")
    ap.add_argument("--demo", action="store_true", help="офлайн: разобрать запросы и показать совпадения")
    args = ap.parse_args()
    demo() if args.demo else run()

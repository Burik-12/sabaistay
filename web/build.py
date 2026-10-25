#!/usr/bin/env python3
"""
SabaiStay — сборка витрины (demo). Визуальный стиль: «морская карта Пхангана».

Читает data/sample_listings.json + data/districts.json и генерирует web/index.html:
сплит-вид «карточки + карта Leaflet (подложка OpenFreeMap)» с фильтром-чипами и шторками.
Данные ВШИТЫ в html (открывается из file:// без сервера). Без npm, без сети на сборку
(Leaflet/тайлы/шрифты тянутся с CDN при открытии). Пересобрать:  python3 build.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from urllib.parse import quote

# Боевой домен для абсолютных ссылок в OG-мете (превью в Telegram/Facebook) и canonical.
# ВАЖНО: домен sabaistay.co на 29-07-2026 ещё НЕ куплен — пока он не зарегистрирован,
# абсолютные ссылки на него мертвы и превью при шеринге не подтягивается. Поэтому по
# умолчанию берём рабочий адрес Vercel; после покупки домена — задать SABAI_SITE_URL
# (переменной окружения или правкой этой строки).
SITE_URL = os.getenv("SABAI_SITE_URL", "https://sabaistay.vercel.app")

# Telegram-контакт для приёма объявлений от владельцев («Сдаёшь жильё?») и для просьб
# об удалении. Пока не задан — блок на витрине не рендерится (неработающая кнопка хуже
# её отсутствия), а на странице приватности остаётся пометка «подставить перед запуском».
# Задать: SABAI_CONTACT_TG=nickname (без @) или правкой этой строки.
CONTACT_TG = os.getenv("SABAI_CONTACT_TG", "").lstrip("@")
CONTACT_EMAIL = os.getenv("SABAI_CONTACT_EMAIL", "")
# Бот подписки — отдельно от контакта для связи: контакт может быть личным аккаунтом,
# а сюда ведёт кнопка «следить за поиском» с полезной нагрузкой фильтра.
# Пока переменная пуста, кнопка на витрине не рисуется вовсе — мёртвая ссылка хуже её отсутствия.
BOT_USERNAME = os.getenv("SABAI_BOT_USERNAME", "").lstrip("@")

# Лого-знак «закат над водой» для фавикона (тёмная бирюза — под светлую вкладку браузера). Канон — web/brand.svg.
FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<circle cx="32" cy="26" r="12" fill="#ef6c45"/>'
               '<g stroke="#0d5563" stroke-linecap="round" fill="none">'
               '<line x1="8" y1="40" x2="56" y2="40" stroke-width="3.4"/>'
               '<line x1="14" y1="47" x2="50" y2="47" stroke-width="2.8" opacity=".55"/>'
               '<line x1="21" y1="53" x2="43" y2="53" stroke-width="2.8" opacity=".3"/></g></svg>')

ROOT = Path(__file__).resolve().parent.parent
LISTINGS = ROOT / "data" / "sample_listings.json"
DISTRICTS = ROOT / "data" / "districts.json"
OUT = ROOT / "web" / "index.html"

sys.path.insert(0, str(ROOT / "parser"))
from benchmark import MarketBenchmark  # noqa: E402  — сравнение с рынком (Airbnb/Booking + медиана)
from dedup import dedup               # noqa: E402  — склейка одного дома из FB+TG в одну карточку
import freshness                     # noqa: E402  — пороги свежести отдаём в JS, чтобы не разъехались
from freshness import assess         # noqa: E402  — возраст/статус (active/rented/expired)


_THONG_SALA = (9.7117, 100.0036)  # пирс Тонгсала — точка отсчёта расстояний

# Силуэт Пхангана для мини-плашки на карточке (вместо фото): viewBox 0..64, проекция
# x=(lng-99.955)*361.29+5.806, y=(9.822-lat)*361.29+3 — та же формула в JS ставит точку района.
ISLAND_PATH = ("M38.3 6.1 L44.5 8.8 L50.2 12.0 L53.5 21.2 L52.1 24.9 L54.9 30.5 L51.3 37.8 L49.9 46.0 "
               "L48.8 53.6 L47.0 59.2 L42.7 57.2 L36.9 49.4 L28.2 46.2 L22.1 44.0 L14.5 40.6 L11.9 35.2 "
               "L11.6 29.7 L9.1 19.6 L13.4 16.0 L14.5 11.7 L19.9 9.9 L24.2 14.9 L27.8 16.0 L31.5 12.0Z")


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lng2 - lng1) / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _place(canon: str | None, coords: dict, seen: dict) -> tuple:
    """Координаты района с джиттером, чтобы карточки одного района не слипались на карте."""
    if not (canon and canon in coords):
        return None, None, None
    lat_c, lng_c = coords[canon]
    dist = round(_haversine(lat_c, lng_c, *_THONG_SALA), 1)
    n = seen.get(canon, 0); seen[canon] = n + 1
    lat = lat_c + ((n % 5) - 2) * 0.0045
    lng = lng_c + (((n // 5) % 5) - 2) * 0.0045
    return round(lat, 5), round(lng, 5), dist


def build_rows() -> tuple[list[dict], str | None]:
    data = json.loads(LISTINGS.read_text(encoding="utf-8"))
    coords = {d["canonical"]: (d["lat"], d["lng"]) for d in json.loads(DISTRICTS.read_text(encoding="utf-8"))["districts"]}
    bench = MarketBenchmark()
    listings = [r for r in data["listings"] if r["parsed"]["is_listing"]]
    # свои объявления SabaiStay (владелец согласился на контакты/фото — PDPA не нарушается)
    own_path = ROOT / "data" / "own_listings.json"
    own_records = json.loads(own_path.read_text(encoding="utf-8")).get("listings", []) if own_path.exists() else []
    # «снимок»: для статичного демо свежесть считаем от даты последнего поста (см. freshness.py).
    snapshot = max((r.get("posted_at") for r in listings + own_records if r.get("posted_at")), default=None)
    groups = dedup(listings)           # один объект = одна карточка, источников может быть несколько
    rows, seen = [], {}
    for g in groups:
        rep = g["representative"]
        p = rep["parsed"]
        canon = p["district_canonical"]
        lat, lng, dist = _place(canon, coords, seen)
        fresh = assess(rep.get("posted_at"), p.get("status_hint"), snapshot)
        rows.append({
            "sources": g["sources"], "dup": g["size"],
            "title": p.get("title") or (canon or "Объявление"),
            "district": canon, "price": p["price_amount"], "period": p["price_period"],
            "bedrooms": p["bedrooms"], "type": p["property_type"],
            "amenities": p["amenities"], "confidence": p["confidence"],
            "posted_at": rep.get("posted_at"), "fresh": fresh,
            "rented": p.get("status_hint") == "rented",
            "lat": lat, "lng": lng,
            "bench": bench.compare(canon, p["price_amount"], p["price_period"], p["bedrooms"]),
            "contact": p.get("contact") or {},
            "text": (rep.get("raw_text") or "").strip(),
            "available_from": p.get("available_from"),
            "area_sqm": p.get("area_sqm"),
            "dist_km": dist,
            "id": rep.get("external_id") or g["sources"][0]["source_url"],
        })
    # ── свои объявления: минуя дедуп (чтобы не слиплись с TG-дублем и не потеряли featured) ──
    for i, rec in enumerate(own_records):
        p = rec.get("parsed") or {}
        canon = p.get("district_canonical")
        lat, lng, dist = _place(canon, coords, seen)
        fresh = assess(rec.get("posted_at"), p.get("status_hint"), snapshot)
        src_url = rec.get("source_url") or ""
        rows.append({
            "sources": [{"source": "own", "source_url": src_url}], "dup": 1,
            "title": p.get("title") or (canon or "Объявление"),
            "district": canon, "price": p.get("price_amount"), "period": p.get("price_period") or "unknown",
            "bedrooms": p.get("bedrooms"), "type": p.get("property_type"),
            "amenities": p.get("amenities") or {}, "confidence": p.get("confidence"),
            "posted_at": rec.get("posted_at"), "fresh": fresh,
            "rented": p.get("status_hint") == "rented",
            "lat": lat, "lng": lng,
            "bench": bench.compare(canon, p.get("price_amount"), p.get("price_period"), p.get("bedrooms")),
            "contact": rec.get("contact") or p.get("contact") or {},
            "text": (rec.get("raw_text") or "").strip(),
            "available_from": p.get("available_from"),
            "area_sqm": p.get("area_sqm"),
            "dist_km": dist,
            "id": rec.get("external_id") or src_url or f"own/{i + 1}",
            "own": True,
            "featured": bool(rec.get("featured", True)),
            "photos": rec.get("photos") or [],
        })
    # свежие — выше, но свои featured всегда сверху
    rows.sort(key=lambda r: (not r.get("featured"), r["fresh"]["age_days"] is None, r["fresh"]["age_days"] or 0))
    return rows, snapshot


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>SabaiStay — аренда жилья на Ко Пхангане с фильтром и картой</title>
<meta name="description" content="Все объявления об аренде на острове Ко Пханган из Telegram-каналов в одном месте — с нормальным фильтром по району, цене, спальням и удобствам, картой и сравнением с рынком.">
<meta property="og:type" content="website">
<meta property="og:title" content="SabaiStay — аренда жилья на Ко Пхангане с фильтром">
<meta property="og:description" content="Объявления Пхангана из Telegram-каналов — с фильтром, картой и ценой к рынку. Поделись ссылкой на свой поиск.">
<meta property="og:image" content="__SITE__/og.png">
<meta property="og:url" content="__SITE__/">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0a3742">
<link rel="canonical" href="__SITE__/">
<!-- Тема ставится до первой отрисовки: иначе тёмная тема мигает светлой. -->
<script>
(function(){var s=null;try{s=localStorage.getItem('sabaistay_theme')}catch(e){}
var dark=s?s==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
document.documentElement.setAttribute('data-theme',dark?'dark':'light');})();
</script>
<!-- Vercel Web Analytics: без кук и персданных. Работает после включения тумблера
     Analytics в дашборде проекта; пока он выключен — скрипт просто 404, это безвредно
     (defer, ничего от него не зависит). -->
<script defer src="/_vercel/insights/script.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
<!-- JS карты подключён в конце body: в <head> он блокировал отрисовку списка,
     хотя список — это то, ради чего человек пришёл. Подложка (MapLibre + OpenFreeMap)
     догружается ещё позже — только когда карта реально на экране. -->
<link rel="preconnect" href="https://unpkg.com" crossorigin>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="icon" href="data:image/svg+xml,__FAVICON__">
<!-- Шрифты не блокируют первый экран: грузим как print и переключаем на all, когда придут. -->
<link rel="stylesheet" media="print" onload="this.media='all'"
      href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@600;700;800&family=Inter:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@600;700;800&family=Inter:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap"></noscript>
<style>
  /* ── токены (docs/redesign-brief.md, раздел 4) ── */
  :root{
    --bg:#eef1ea; --surface:#fafbf7; --surface-2:#e4e9e1; --ink:#0e2329; --muted:#566a71;
    --hair:#d3dbd3; --sea-deep:#0a3742; --sea:#0d5563; --on-sea:#fff;
    --coral:#ef6c45; --coral-ink:#b9461f; --cta:#c4502a; --on-cta:#fff;
    --fresh:#2f8f5b; --fresh-ink:#23774a; --fresh-bg:#e1f0e6;
    --warn:#8a5a12; --warn-bg:#fbf1dc; --warn-hair:#ecd9ac; --stale:#8a9599;
    --land:#e6e4d6; --scrim:rgb(6 26 32 / .45);
    --shadow:0 1px 2px rgb(14 35 41 / .06); --shadow-h:0 4px 14px rgb(14 35 41 / .10);
    --shadow-sheet:0 -8px 30px rgb(0 0 0 / .18); --shadow-pop:0 10px 34px rgb(14 35 41 / .18);
    --ease:cubic-bezier(.2,.8,.2,1);
    --disp:"Baloo 2",system-ui,sans-serif;
    --body:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --mono:"Space Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
    --top-h:56px; --bar-h:56px;
    color-scheme:light;
  }
  html[data-theme="dark"]{
    --bg:#0b1a1f; --surface:#11252b; --surface-2:#173139; --ink:#e6efec; --muted:#9bb0b3;
    --hair:#244047; --sea-deep:#071820; --sea:#5cc3c9; --on-sea:#06171c;
    --coral:#ff8a63; --coral-ink:#ff9a76; --cta:#ff8a63; --on-cta:#0b1a1f;
    --fresh:#5cc98b; --fresh-ink:#5cc98b; --fresh-bg:#15362a;
    --warn:#e9c27a; --warn-bg:#2a2113; --warn-hair:#4a3a1c; --stale:#6f8186;
    --land:#13262c; --scrim:rgb(0 0 0 / .55);
    --shadow:0 1px 2px rgb(0 0 0 / .3); --shadow-h:0 4px 14px rgb(0 0 0 / .4);
    --shadow-pop:0 10px 34px rgb(0 0 0 / .5);
    color-scheme:dark;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{font:400 14px/1.45 var(--body);color:var(--ink);background:var(--bg);
       display:flex;flex-direction:column;height:100dvh;overflow:hidden;-webkit-font-smoothing:antialiased}
  button,input,select{font:inherit;color:inherit}
  button{cursor:pointer}
  a{color:var(--sea)}
  :focus-visible{outline:2px solid var(--sea);outline-offset:2px}
  .num{font-family:var(--mono)}
  .ico{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  [hidden]{display:none!important}
  .vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

  /* ── шапка: одна строка ── */
  .top{height:var(--top-h);flex:none;display:flex;align-items:center;gap:12px;padding:0 20px;
       background:var(--sea-deep);color:#eef4f2;position:relative;z-index:1200}
  .brand{display:flex;align-items:center;gap:9px;text-decoration:none;color:#fff;flex:none}
  .brandmark{width:30px;height:30px}
  .mark{font-family:var(--disp);font-weight:800;font-size:23px;letter-spacing:-.2px;line-height:1}
  .mark .m2{color:var(--coral)}
  html[data-theme="dark"] .mark .m2{color:#ff8a63}
  .search{flex:1 1 auto;max-width:520px;margin-left:12px;position:relative;display:flex;align-items:center}
  .search .ico{position:absolute;left:13px;color:#8fb3b1;pointer-events:none}
  .search input{width:100%;height:38px;border-radius:999px;border:1px solid rgb(255 255 255 / .14);
       background:rgb(255 255 255 / .08);color:#fff;padding:0 14px 0 38px;font-size:14px}
  .search input::placeholder{color:#9cbcb9}
  .search input:focus{outline:none;border-color:#7fd0d3;background:rgb(255 255 255 / .13)}
  .top-actions{margin-left:auto;display:flex;align-items:center;gap:6px;flex:none}
  .tbtn{height:36px;min-width:36px;display:inline-flex;align-items:center;justify-content:center;gap:6px;
        border-radius:999px;border:1px solid rgb(255 255 255 / .18);background:transparent;color:#e7f1ef;
        padding:0 12px;font-size:13px;font-weight:600;white-space:nowrap}
  .tbtn:hover{background:rgb(255 255 255 / .1)}
  .tbtn.icon{padding:0;width:36px}
  .tbtn .badge{font-family:var(--mono);font-size:12px}

  /* ── ряд фильтров-чипов ── */
  .fbar{flex:none;min-height:var(--bar-h);display:flex;align-items:center;gap:8px;padding:9px 20px;
        background:var(--surface);border-bottom:1px solid var(--hair);position:relative;z-index:1100}
  .chips{display:flex;gap:8px;align-items:center;min-width:0;overflow-x:auto;scrollbar-width:none;flex:1 1 auto}
  .chips::-webkit-scrollbar{display:none}
  .chip{flex:none;height:36px;display:inline-flex;align-items:center;gap:6px;padding:0 14px;border-radius:999px;
        border:1px solid var(--hair);background:var(--surface);color:var(--ink);font-size:13.5px;font-weight:500;white-space:nowrap;
        transition:background .16s var(--ease),border-color .16s var(--ease),color .16s var(--ease)}
  .chip:hover{border-color:var(--sea)}
  .chip .car{font-size:10px;opacity:.6}
  .chip.on{background:var(--sea);border-color:var(--sea);color:var(--on-sea)}
  .chip .cnt{font-family:var(--mono);font-size:11.5px;font-weight:700;min-width:18px;height:18px;border-radius:9px;
        display:inline-flex;align-items:center;justify-content:center;background:var(--surface-2);color:var(--ink);padding:0 5px}
  .chip.on .cnt{background:rgb(255 255 255 / .22);color:inherit}
  .seg{flex:none;display:inline-flex;height:36px;border:1px solid var(--hair);border-radius:999px;padding:2px;background:var(--surface)}
  .seg button{border:none;background:none;border-radius:999px;padding:0 12px;font-size:13px;font-weight:500;color:var(--muted);white-space:nowrap}
  .seg button.on{background:var(--sea);color:var(--on-sea)}
  .watch{flex:none;height:38px;display:inline-flex;align-items:center;gap:7px;padding:0 16px;border-radius:999px;
         background:var(--cta);color:var(--on-cta);font-weight:600;font-size:14px;text-decoration:none;white-space:nowrap}
  .watch:hover{filter:brightness(1.06)}

  /* ── плашка возраста данных: только когда снимок старше 3 дней ── */
  .data-age{flex:none;display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;padding:8px 20px;font-size:13px;
            background:var(--warn-bg);color:var(--warn);border-bottom:1px solid var(--warn-hair)}
  .data-age b{font-weight:700}
  .data-age button{border:1px solid currentColor;background:none;color:inherit;border-radius:999px;height:30px;padding:0 12px;font-size:12.5px;font-weight:600}

  /* ── полотно ── */
  .wrap{display:flex;flex:1;min-height:0}
  .list{width:46%;min-width:360px;max-width:640px;overflow-y:auto;padding:12px 16px 40px;background:var(--bg)}
  #map{flex:1;min-width:0;height:100%;background:var(--land);position:relative}

  /* строка результата */
  .rbar{display:flex;align-items:center;gap:8px 12px;flex-wrap:wrap;padding:2px 2px 12px;font-size:13px;color:var(--muted)}
  .rbar .rc{color:var(--ink);font-weight:600;white-space:nowrap}
  .rbar .info{border:none;background:none;color:var(--muted);padding:0 2px;height:28px;display:inline-flex;align-items:center;gap:5px;font-size:13px}
  .rbar .info:hover{color:var(--sea)}
  .rbar .spacer{flex:1}
  .lbtn{border:1px solid var(--hair);background:var(--surface);border-radius:999px;height:32px;padding:0 12px;
        display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--ink);white-space:nowrap}
  .lbtn:hover{border-color:var(--sea)}
  .lbtn.ok{color:var(--fresh-ink);border-color:var(--fresh)}
  .sortw{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
  .sortw select{height:32px;border:1px solid var(--hair);border-radius:999px;background:var(--surface);padding:0 10px;font-size:13px;color:var(--ink);max-width:180px}
  .infopop{position:absolute;z-index:1300;max-width:330px;background:var(--surface);color:var(--ink);border:1px solid var(--hair);
           border-radius:14px;box-shadow:var(--shadow-pop);padding:14px 16px;font-size:13px;line-height:1.5}
  .infopop p{margin:0 0 8px}.infopop p:last-child{margin:0}

  /* ── карточка ── */
  #cards{display:flex;flex-direction:column;gap:12px}
  .card{position:relative;display:grid;grid-template-columns:64px minmax(0,1fr);column-gap:14px;
        background:var(--surface);border:1px solid var(--hair);border-radius:14px;padding:12px 14px 10px;
        box-shadow:var(--shadow);cursor:pointer;transition:box-shadow .18s var(--ease),border-color .18s var(--ease)}
  .card:hover{box-shadow:var(--shadow-h)}
  .card.active{border-color:var(--sea)}
  .card.stale{opacity:.6}
  .card.stale:hover,.card.stale.open{opacity:1}
  .card.own{border-color:var(--coral)}
  .c-mark{width:64px;height:64px;border-radius:12px;background:var(--surface-2);display:flex;align-items:center;justify-content:center;overflow:hidden}
  .c-mark svg{width:64px;height:64px;display:block}
  .c-mark .isl{fill:var(--land);stroke:var(--sea);stroke-width:1.2;stroke-linejoin:round;opacity:.9}
  html[data-theme="dark"] .c-mark .isl{fill:#1f3a42}
  .c-mark .dot{fill:var(--coral)}
  .c-mark .halo{fill:var(--coral);opacity:.22}
  .c-mark .isl.unk{stroke-dasharray:2 2;opacity:.55}
  .c-mark img{width:100%;height:100%;object-fit:cover}
  .c-body{min-width:0}
  .c-eye{display:flex;align-items:center;gap:8px;padding-right:30px;min-height:18px}
  .c-where{font:700 11px/1.3 var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--sea);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
  .c-where.nogeo{color:var(--muted)}
  .c-fresh{margin-left:auto;flex:none;display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:500;color:var(--muted);white-space:nowrap}
  .c-fresh::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--stale)}
  .c-fresh.f-hot,.c-fresh.f-fresh{color:var(--fresh-ink)}
  .c-fresh.f-hot::before,.c-fresh.f-fresh::before{background:var(--fresh)}
  .c-fresh.f-hot::before{box-shadow:0 0 0 3px var(--fresh-bg)}
  .c-fresh.f-recent::before{background:var(--sea);opacity:.55}
  .c-price{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;margin:1px 0 1px}
  .c-price .p{font:700 26px/1.1 var(--mono);color:var(--coral-ink);letter-spacing:-.04em;word-spacing:-.25em;white-space:nowrap}
  .c-price .p small{font:500 13px var(--body);color:var(--muted);letter-spacing:0;margin-left:2px}
  .c-price .p.none{font:500 15px var(--body);color:var(--muted)}
  .bench{font-size:12px;font-weight:600;border-radius:999px;padding:2px 9px;white-space:nowrap;background:var(--surface-2);color:var(--muted)}
  .bench.cheap{background:var(--fresh-bg);color:var(--fresh-ink)}
  .c-facts{font-size:14px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .c-amen{font-size:13px;color:var(--muted);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .c-amen .now{color:var(--fresh-ink);font-weight:600}
  .sep{opacity:.45;margin:0 5px}
  .tag{font-size:11.5px;font-weight:600;border-radius:6px;padding:1px 7px;background:var(--surface-2);color:var(--muted);margin-left:6px;white-space:nowrap}
  .tag.own{background:var(--coral-ink);color:#fff}
  html[data-theme="dark"] .tag.own{color:var(--on-cta)}
  .c-foot{grid-column:1/-1;display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:8px;border-top:1px solid var(--hair)}
  .c-src{border:none;background:none;padding:0;font-size:13px;color:var(--muted);text-align:left;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:auto;height:32px}
  .c-src:hover{color:var(--sea)}
  .c-src b{font-weight:600;color:var(--sea)}
  .btn{height:32px;display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:0 14px;border-radius:999px;
       font-size:13.5px;font-weight:600;text-decoration:none;white-space:nowrap;flex:none;border:1px solid transparent}
  .btn.pri{background:var(--cta);color:var(--on-cta)}
  .btn.pri:hover{filter:brightness(1.06)}
  .btn.sec{border-color:var(--hair);color:var(--ink);background:var(--surface)}
  .btn.sec:hover{border-color:var(--sea);color:var(--sea)}
  .bm{position:absolute;top:8px;right:8px;width:36px;height:36px;border:none;background:none;border-radius:50%;
      display:flex;align-items:center;justify-content:center;color:var(--muted)}
  .bm:hover{color:var(--coral-ink);background:var(--surface-2)}
  .bm.saved{color:var(--coral-ink)}
  .bm.saved .ico{fill:currentColor}
  .stale-note{font-size:12px;color:var(--muted);font-style:italic}
  /* раскрытие карточки */
  .c-more{grid-column:1/-1;margin-top:10px;font-size:13px}
  .c-text{font:13px/1.55 var(--body);white-space:pre-wrap;word-break:break-word;margin:0 0 10px;max-height:300px;overflow-y:auto;color:var(--ink);
          background:var(--bg);border-radius:10px;padding:10px 12px}
  .c-text mark{background:#fff0a8;color:inherit;border-radius:2px}
  .c-srcs{display:flex;flex-wrap:wrap;gap:6px 12px;margin:0 0 8px}
  .c-srcs a{text-decoration:none;font-size:13px}
  .c-srcs a:hover{text-decoration:underline}
  .c-meta{color:var(--muted);margin:0 0 4px}
  .c-meta a{color:var(--muted)}
  .own-photo{grid-column:1/-1;width:calc(100% + 28px);margin:-12px -14px 12px;height:160px;object-fit:cover;border-radius:13px 13px 0 0;display:block}

  /* горячие */
  .hot{background:var(--surface);border:1px solid var(--hair);border-radius:14px;padding:12px 14px}
  .hot h3{margin:0 0 8px;font:600 14px var(--body);color:var(--ink)}
  .hot-row{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}
  .hot-row::-webkit-scrollbar{display:none}
  .hot-card{flex:1 0 150px;border:1px solid var(--hair);border-radius:12px;padding:9px 12px;background:var(--bg);cursor:pointer;text-align:left}
  .hot-card:hover{border-color:var(--sea)}
  .hot-card .hp{font:700 16px var(--mono);color:var(--coral-ink)}
  .hot-card .hm{font-size:12px;color:var(--muted)}

  .load-more{display:flex;justify-content:center;padding:6px 0}
  .owner-cta{background:var(--surface);border:1px dashed var(--coral);border-radius:14px;padding:16px 18px;margin-top:16px;text-align:center}
  .owner-cta b{display:block;font:700 17px var(--disp);margin-bottom:4px}
  .owner-cta p{margin:0 0 12px;font-size:13px;color:var(--muted)}
  .listfoot{font-size:12.5px;color:var(--muted);padding:18px 4px 24px;border-top:1px solid var(--hair);margin-top:18px;line-height:1.7}
  .listfoot p{margin:0 0 6px}
  .listfoot a{color:var(--sea);text-decoration:none}
  .listfoot a:hover{text-decoration:underline}
  .listfoot nav{display:flex;flex-wrap:wrap;gap:4px 14px}
  .listfoot nav button{border:none;background:none;padding:0;color:var(--sea);font-size:12.5px}

  /* пустое состояние */
  .empty{padding:36px 18px;text-align:center;color:var(--muted)}
  .empty .big{font-size:30px;display:block;margin-bottom:8px}
  .empty h2{font:700 20px/1.25 var(--disp);color:var(--ink);margin:0 0 6px}
  .empty p{margin:0 auto 14px;max-width:44ch}
  .relax{display:flex;flex-direction:column;gap:8px;align-items:center;margin:0 0 16px}
  .relax button{border:1px solid var(--hair);background:var(--surface);border-radius:999px;height:36px;padding:0 16px;font-size:13.5px;color:var(--ink)}
  .relax button:hover{border-color:var(--sea);color:var(--sea)}
  .relax button b{font-family:var(--mono)}
  .empty-actions{display:flex;gap:8px;justify-content:center;flex-wrap:wrap}

  /* ── шторка / поповер / модалка: один компонент ── */
  .scrim{position:fixed;inset:0;background:transparent;z-index:2000}
  .scrim.dim{background:var(--scrim)}
  .sheet{position:fixed;z-index:2001;background:var(--surface);color:var(--ink);border:1px solid var(--hair);border-radius:16px;
         box-shadow:var(--shadow-pop);display:flex;flex-direction:column;max-height:min(560px,80dvh);width:360px}
  .sheet.modal{left:50%;top:50%;transform:translate(-50%,-50%);width:min(680px,calc(100vw - 32px));max-height:86dvh;border-radius:20px}
  .sh-head{display:flex;align-items:center;gap:8px;padding:14px 16px 10px;border-bottom:1px solid var(--hair)}
  .sh-head h2{margin:0;font:700 18px var(--disp);flex:1}
  .sh-x{width:36px;height:36px;border:none;background:none;border-radius:50%;font-size:18px;color:var(--muted)}
  .sh-x:hover{background:var(--surface-2);color:var(--ink)}
  .sh-handle{display:none}
  .sh-body{overflow-y:auto;padding:6px 16px 14px;overscroll-behavior:contain}
  .sh-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-top:1px solid var(--hair)}
  .sh-foot .clr{border:none;background:none;text-decoration:underline;font-size:14px;font-weight:500;padding:8px 4px}
  .sh-foot .show{height:44px;padding:0 20px;border:none;border-radius:12px;background:var(--cta);color:var(--on-cta);font-weight:700;font-size:15px}
  .sec{padding:12px 0;border-bottom:1px solid var(--hair)}
  .sec:last-child{border-bottom:none}
  .sec h3{margin:0 0 10px;font:600 15px var(--body)}
  .sheet.pop .sec > h3{display:none}
  .sec .hint{font-size:12.5px;color:var(--muted);margin:-6px 0 10px}
  .pills{display:flex;flex-wrap:wrap;gap:8px}
  .pill{height:38px;padding:0 14px;border-radius:999px;border:1px solid var(--hair);background:var(--surface);font-size:13.5px;font-weight:500;display:inline-flex;align-items:center;gap:6px}
  .pill:hover{border-color:var(--sea)}
  .pill.on{background:var(--sea);border-color:var(--sea);color:var(--on-sea)}
  .pill small{font-family:var(--mono);font-size:11px;opacity:.7}
  .segw{display:flex;flex-wrap:wrap;gap:8px}
  .segw .pill{min-width:56px;justify-content:center}
  .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
  .tile{display:flex;align-items:center;gap:9px;min-height:46px;padding:8px 12px;border:1px solid var(--hair);border-radius:12px;background:var(--surface);font-size:14px;cursor:pointer}
  .tile:has(input:checked){border-color:var(--sea);box-shadow:inset 0 0 0 1px var(--sea)}
  .tile input,.chk input{width:18px;height:18px;accent-color:var(--sea);margin:0;flex:none}
  .chk{display:flex;align-items:center;gap:10px;min-height:40px;font-size:14px;cursor:pointer}
  .range2{display:flex;gap:10px;align-items:center}
  .range2 label{flex:1;display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:var(--muted)}
  .range2 input{height:42px;border:1px solid var(--hair);border-radius:10px;background:var(--bg);padding:0 12px;font:500 14px var(--mono);color:var(--ink);width:100%}
  .range2 .dash{color:var(--muted);padding-top:18px}
  .hist{display:flex;align-items:flex-end;gap:2px;height:64px;margin:4px 9px 0}
  .hist i{flex:1;background:var(--hair);border-radius:2px 2px 0 0;min-height:2px}
  .hist i.in{background:var(--sea)}
  .dual{position:relative;height:28px;margin:0 0 12px}
  .dual .track{position:absolute;left:9px;right:9px;top:13px;height:3px;border-radius:2px;background:var(--hair)}
  .dual .fill{position:absolute;top:13px;height:3px;background:var(--sea);border-radius:2px}
  .dual input{position:absolute;inset:0;width:100%;margin:0;background:none;pointer-events:none;-webkit-appearance:none;appearance:none;height:28px}
  .dual input::-webkit-slider-thumb{-webkit-appearance:none;pointer-events:auto;width:22px;height:22px;border-radius:50%;background:var(--surface);border:2px solid var(--sea);box-shadow:var(--shadow-h);cursor:grab}
  .dual input::-moz-range-thumb{pointer-events:auto;width:18px;height:18px;border-radius:50%;background:var(--surface);border:2px solid var(--sea);cursor:grab}
  .dual input::-webkit-slider-runnable-track{background:none}
  .menu-list{display:flex;flex-direction:column;padding:6px 0}
  .menu-list button,.menu-list a{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:0 4px;border:none;background:none;
        font-size:14.5px;color:var(--ink);text-decoration:none;text-align:left;border-bottom:1px solid var(--hair)}
  .menu-list > :last-child{border-bottom:none}
  .menu-list .val{font-size:13px;color:var(--muted);font-weight:500}
  .menu-list button:hover,.menu-list a:hover{color:var(--sea)}
  .stats-table{width:100%;border-collapse:collapse;font-size:13.5px}
  .stats-table th{font-size:12px;font-weight:600;color:var(--muted);padding:8px 10px;border-bottom:1px solid var(--hair);text-align:left}
  .stats-table td{padding:8px 10px;border-bottom:1px solid var(--hair)}
  .stats-table td.n{font-family:var(--mono);text-align:right}
  .stats-table th.n{text-align:right}
  .stats-table .st-price{font-family:var(--mono);font-weight:700;color:var(--coral-ink);text-align:right}
  kbd{background:var(--bg);border:1px solid var(--hair);border-radius:5px;padding:1px 7px;font-family:var(--mono);font-size:12px}
  .kbd-row{display:flex;gap:12px;align-items:center;min-height:36px}

  /* ── карта: пины, кластеры, подписи ── */
  .pin{display:flex;align-items:center;justify-content:center;font:700 12px var(--mono);color:#0e2329;background:#fff;
       border:1px solid rgb(14 35 41 / .18);border-radius:999px;height:24px;padding:0 8px;box-shadow:0 1px 4px rgb(0 0 0 / .25);
       white-space:nowrap;transition:transform .14s var(--ease),background .14s}
  .pin.dim{color:#7b878b}
  .pin.on{background:var(--coral);border-color:var(--coral);color:#fff;transform:scale(1.15)}
  html[data-theme="dark"] .pin.on{color:#0b1a1f}
  .leaflet-marker-icon:has(.pin.on){z-index:10000!important}
  .cluster{width:36px;height:36px;border-radius:50%;background:var(--sea-deep);color:#fff;font:700 12.5px var(--mono);display:flex;align-items:center;justify-content:center;
           border:2px solid #fff;box-shadow:0 2px 6px rgb(0 0 0 / .3)}
  html[data-theme="dark"] .cluster{background:#2a7d86;border-color:#0b1a1f}
  .mapnote{background:var(--surface);color:var(--muted);border:1px solid var(--hair);border-radius:999px;padding:5px 12px;font-size:12px;box-shadow:var(--shadow)}
  .mapnote b{color:var(--ink)}
  .coords{font:400 11px var(--mono);color:var(--muted);background:color-mix(in srgb,var(--surface) 80%,transparent);border-radius:6px;padding:3px 8px;letter-spacing:.04em}
  .leaflet-container{font-family:var(--body);background:var(--land)}
  .leaflet-popup-content-wrapper{border-radius:14px;background:var(--surface);color:var(--ink);box-shadow:var(--shadow-pop)}
  .leaflet-popup-tip{background:var(--surface)}
  .leaflet-popup-content{margin:12px 14px;font-size:13px;line-height:1.45;min-width:180px}
  .mp-price{font:700 18px var(--mono);color:var(--coral-ink)}
  .mp-price small{font:500 12px var(--body);color:var(--muted)}
  .mp-where{font:700 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--sea)}
  .mp-meta{color:var(--muted);margin:2px 0 8px}
  .mp-acts{display:flex;gap:6px}
  .mp-acts .btn{height:32px;font-size:13px;padding:0 12px}
  .leaflet-control-attribution{font-size:10.5px;background:color-mix(in srgb,var(--surface) 85%,transparent)!important;color:var(--muted)}
  .leaflet-control-attribution a{color:var(--sea)}
  .leaflet-bar a{background:var(--surface);color:var(--ink);border-color:var(--hair)}

  /* ── мобильная пилюля (карта / следить) ── */
  .fpill{display:none}
  .scroll-top{position:fixed;bottom:22px;right:18px;width:42px;height:42px;border-radius:50%;border:1px solid var(--hair);background:var(--surface);color:var(--ink);
              box-shadow:var(--shadow-h);font-size:18px;display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .2s;z-index:1150}
  .scroll-top.vis{opacity:1;pointer-events:auto}

  /* мобила: страница скроллится целиком, шапка и чипы липкие */
  @media (max-width:760px){
    :root{--top-h:52px;--bar-h:52px}
    body{height:auto;min-height:100dvh;overflow:visible;display:block}
    .top{position:sticky;top:0;padding:0 12px 0 16px;gap:10px}
    .mark{display:none}
    .brandmark{width:28px;height:28px}
    .search{margin-left:0;max-width:none}
    .search input{height:36px;font-size:16px}  /* 16px: iOS не зумит поле */
    .top .hide-m,.menu-list .hide-m{display:none}
    .fbar{position:sticky;top:var(--top-h);padding:8px 0 8px 16px;z-index:1100}
    .chips{padding-right:16px}
    .fbar .watch{display:none}
    .data-age{padding:8px 16px;font-size:12.5px}
    .wrap{display:block}
    .list{width:auto;min-width:0;max-width:none;overflow:visible;padding:10px 16px 110px}
    #map{display:none;height:calc(100dvh - var(--top-h) - var(--bar-h));position:sticky;top:calc(var(--top-h) + var(--bar-h))}
    .wrap.mode-map .list{display:none}
    .wrap.mode-map #map{display:block}
    /* карта на мобиле — ровно экран под шапкой и чипами, иначе атрибуция уезжает за край */
    body.mode-map .data-age{display:none!important}
    #map .leaflet-bottom.leaflet-left{bottom:64px}  /* над плавающей пилюлей */
    .mapnote{font-size:11.5px}
    .rbar{flex-wrap:nowrap}
    .rbar #rupd{display:none}
    .rbar .sortw .lbl{display:none}
    .sortw select{max-width:150px}
    .rbar .share .lbl{display:none}
    .card{grid-template-columns:52px minmax(0,1fr);column-gap:12px;padding:12px 14px 10px}
    .c-mark,.c-mark svg{width:52px;height:52px}
    .c-price .p{font-size:24px}
    .own-photo{width:calc(100% + 28px);margin:-12px -14px 12px}
    .btn{height:40px}
    .c-src{height:40px}
    .bm{width:44px;height:44px;top:4px;right:4px}
    .fpill{position:fixed;left:50%;bottom:calc(18px + env(safe-area-inset-bottom));transform:translateX(-50%);z-index:1150;display:flex;
           background:var(--ink);color:var(--bg);border-radius:999px;box-shadow:0 6px 22px rgb(0 0 0 / .3);overflow:hidden}
    .fpill button,.fpill a{height:46px;display:inline-flex;align-items:center;gap:7px;padding:0 18px;border:none;background:none;color:inherit;font-weight:600;font-size:14.5px;text-decoration:none;white-space:nowrap}
    .fpill > * + *{border-left:1px solid color-mix(in srgb,var(--bg) 25%,transparent)}
    body.sheet-open .fpill{display:none}
    .scroll-top{bottom:calc(84px + env(safe-area-inset-bottom));right:16px}
    /* шторки на мобиле — снизу */
    .sheet,.sheet.modal{left:0;right:0;bottom:0;top:auto;transform:none;width:auto;max-height:88dvh;border-radius:20px 20px 0 0;border-bottom:none;
           box-shadow:var(--shadow-sheet);padding-bottom:env(safe-area-inset-bottom)}
    .sheet.modal.full{height:94dvh;max-height:none}
    .sheet.pop{max-height:62dvh}
    .sh-handle{display:block;width:40px;height:5px;border-radius:3px;background:var(--hair);margin:8px auto 0}
    .sh-head{padding-top:6px}
    .scrim{background:var(--scrim)}
    .sheet.anim-in{animation:sheetUp .28s var(--ease)}
  }
  @keyframes sheetUp{from{transform:translateY(100%)}to{transform:none}}
  @keyframes fadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1}}
  @media (min-width:761px){.sheet.pop.anim-in{animation:fadeIn .16s var(--ease)}}
  #cards.refresh{animation:swap .2s ease}
  @keyframes swap{from{opacity:.3}to{opacity:1}}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
  @media print{
    .top-actions,.fbar,.fpill,.scroll-top,#map,.load-more,.data-age{display:none!important}
    body{height:auto;overflow:visible;display:block}
    .wrap{display:block}.list{width:100%;max-width:none;overflow:visible}
    .card{break-inside:avoid;box-shadow:none}
  }
</style>
</head>
<body>
<header class="top">
  <a class="brand" href="./" aria-label="SabaiStay">
    <svg class="brandmark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="26" r="12" fill="#ef6c45"/><g stroke="#bfe0db" stroke-linecap="round" fill="none"><line x1="8" y1="40" x2="56" y2="40" stroke-width="3.4"/><line x1="14" y1="47" x2="50" y2="47" stroke-width="2.8" opacity=".55"/><line x1="21" y1="53" x2="43" y2="53" stroke-width="2.8" opacity=".3"/></g></svg>
    <span class="mark">Sabai<span class="m2">Stay</span></span>
  </a>
  <div class="search" role="search">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input id="f-search" type="search" autocomplete="off" data-i18n-ph="searchPh" data-i18n-aria="searchPh" placeholder="Район, pool, 2br…" aria-label="Поиск">
  </div>
  <div class="top-actions">
    <button class="tbtn hide-m" id="lang-btn" type="button" data-i18n-aria="langAria">EN</button>
    <button class="tbtn" id="fav-btn" type="button" hidden data-i18n-aria="favs"><svg class="ico" viewBox="0 0 24 24"><path d="M6 3h12v18l-6-4-6 4z"/></svg><span class="badge" id="fav-num">0</span></button>
    <button class="tbtn icon" id="menu-btn" type="button" aria-haspopup="dialog" data-i18n-aria="menu"><svg class="ico" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>
  </div>
</header>

<nav class="fbar" data-i18n-aria="filters">
  <div class="chips" id="chips"></div>
  <a class="watch js-watch" id="watch-btn" target="_blank" rel="noopener" hidden><svg class="ico" viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg><span data-i18n="watchSearch">Следить за поиском</span></a>
</nav>

<div class="data-age" id="data-age" role="status" hidden></div>

<div class="wrap" id="wrap">
  <main class="list" id="list">
    <h1 class="vh" data-i18n="h1">Аренда жилья на Ко Пхангане</h1>
    <div class="rbar" id="rbar">
      <span class="rc" id="rcount"></span>
      <button class="info" id="info-btn" type="button" aria-haspopup="dialog"><span id="rupd"></span><svg class="ico" viewBox="0 0 24 24" style="width:15px;height:15px"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg></button>
      <span class="spacer"></span>
      <button class="lbtn share" id="share-btn" type="button"><svg class="ico" viewBox="0 0 24 24"><path d="M7 17 17 7M9 7h8v8"/></svg><span class="lbl" data-i18n="share">поделиться</span></button>
      <label class="sortw"><span class="lbl" data-i18n="sortBy">Сорт:</span>
        <select id="f-sort" data-i18n-aria="sortBy">
          <option value="" data-i18n="sortFresh">сначала свежие</option>
          <option value="cheap" data-i18n="sortCheap">дешевле</option>
          <option value="pricey" data-i18n="sortPricey">дороже</option>
          <option value="below" data-i18n="sortBelow">ниже рынка</option>
          <option value="sqm_desc" data-i18n="sortSqmDesc">площадь: больше</option>
          <option value="sqm_asc" data-i18n="sortSqmAsc">площадь: меньше</option>
          <option value="price_sqm_asc" data-i18n="sortPpsqm">цена/м²: дешевле</option>
          <option value="dist_asc" data-i18n="sortDist">ближе к пирсу</option>
        </select></label>
    </div>
    <div id="cards"></div>
    <div class="load-more" id="load-more" hidden><button class="lbtn" id="load-more-btn" type="button"></button></div>
    __OWNER_CTA__
    <footer class="listfoot">
      <p data-i18n="footAbout">SabaiStay собирает открытые объявления Telegram-каналов Пхангана. Мы не посредник: связь напрямую с автором, без комиссии. Каждое объявление ведёт в оригинал.</p>
      <nav>
        <a href="privacy.html" data-i18n="privacy">Приватность</a>
        <a href="privacy.html#remove" data-i18n="removeListing">Убрать объявление</a>
        <button type="button" id="foot-stats" data-i18n="districtStats">Статистика районов</button>
        <button type="button" id="foot-csv" data-i18n="csv">Скачать CSV</button>
        <span><span data-i18n="snapshot">снимок</span> <span class="num">__SNAPSHOT__</span></span>
      </nav>
    </footer>
  </main>
  <div id="map" role="region" data-i18n-aria="mapAria"></div>
</div>

<div class="fpill" id="fpill">
  <button type="button" id="mode-btn"><svg class="ico" viewBox="0 0 24 24"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></svg><span data-i18n="map">Карта</span></button>
  <a class="js-watch" id="watch-pill" target="_blank" rel="noopener" hidden><svg class="ico" viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg><span data-i18n="watchShort">Следить</span></a>
</div>
<button class="scroll-top" id="scroll-top" type="button" data-i18n-aria="toTop">↑</button>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const DATA = __DATA__;
const BOT_USERNAME='__BOT_USERNAME__';
const SNAP_ISO='__SNAPSHOT_ISO__';
const SNAP_DATE=SNAP_ISO?new Date(SNAP_ISO):null;
const ISLAND='__ISLAND_PATH__';
const HAS_L=typeof L!=='undefined'&&!!L.markerClusterGroup;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const LS={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};

// ── язык: RU/EN. Текст объявлений — как в оригинале, переводим только интерфейс ──
const I18N={
ru:{h1:'Аренда жилья на Ко Пхангане',searchPh:'Район, pool, 2br…',langAria:'Switch to English',favs:'Избранное',menu:'Меню',filters:'Фильтры',
  watchSearch:'Следить за поиском',watchShort:'Следить',watchTip:'Пришлём в Telegram новые объявления под этот фильтр',
  watchNew:'Присылать новые объявления',notifyMe:'Сообщить, когда появится',
  share:'поделиться',copied:'скопировано',sortBy:'Сорт:',sortFresh:'сначала свежие',sortCheap:'дешевле',sortPricey:'дороже',sortBelow:'ниже рынка',
  sortSqmDesc:'площадь: больше',sortSqmAsc:'площадь: меньше',sortPpsqm:'цена/м²: дешевле',sortDist:'ближе к пирсу',
  footAbout:'SabaiStay собирает открытые объявления Telegram-каналов Пхангана. Мы не посредник: связь напрямую с автором, без комиссии. Каждое объявление ведёт в оригинал.',
  privacy:'Приватность',removeListing:'Убрать объявление',districtStats:'Статистика районов',csv:'Скачать CSV',snapshot:'снимок',
  mapAria:'Карта объявлений',map:'Карта',list:'Список',toTop:'Наверх',
  allFilters:'Фильтры',district:'Район',price:'Цена',beds:'Спальни',type:'Тип',monthly:'Помесячно',nightly:'Посуточно',
  freshness:'Свежесть',wholeIsland:'Весь остров',any:'Любые',anyType:'Любой',anyTime:'Когда угодно',anyTerm:'Любой срок',
  term:'Срок аренды',amenities:'Удобства',area:'Площадь, м²',from:'от',to:'до',more:'Ещё',
  onlyAvail:'Только с датой заезда',hideSeen:'Скрыть просмотренные',showStale:'Показывать устаревшие',
  clearAll:'Очистить всё',reset:'Сбросить',showN:'Показать {n}',priceHint:'Распределение цен в текущей выдаче',
  age1:'сегодня и вчера',age3:'за 3 дня',age7:'за неделю',age30:'за месяц',
  updated:'обновлено {ago}',fromChannels:'из {n} Telegram-каналов',
  infoP1:'Собираем открытые посты {n} Telegram-каналов аренды Пхангана и раскладываем по району, цене и спальням.',
  infoP2:'Каждая карточка ведёт в оригинальный пост. Мы не посредник: пишешь автору напрямую, без комиссии.',
  infoP3:'Твоё объявление попало сюда, а ты против? <a href="privacy.html#remove">Убрать объявление</a>.',
  staleAuto:'Свежих объявлений нет — последнее обновление <b>{ago}</b>. Показываем последние известные: часть могла уйти, пиши сразу нескольким.',
  staleOld:'Обновлено <b>{ago}</b> — часть вариантов могла уйти, пиши сразу нескольким.',
  showOldToo:'Показать старые тоже ({n})',
  emptyTitle:'Под такой фильтр сейчас ничего нет',emptyText:'Ослабь одно условие — вот что сработает лучше всего:',
  emptyNoRelax:'Попробуй сбросить фильтры или подпишись — пришлём, как появится.',
  noFreshTitle:'Свежих объявлений нет',noFreshText:'Последнее обновление {ago}. Старые объявления скрыты — часть из них ещё может быть актуальна.',
  showKnown:'Показать последние известные ({n})',resetFilters:'Сбросить фильтры',
  relaxDistrict:'Весь остров',relaxPrice:'Без лимита цены',relaxBed:'Любые спальни',relaxType:'Любой тип',relaxAm:'Без «{x}»',
  relaxAge:'За любое время',relaxPeriod:'Любой срок',relaxSqm:'Любая площадь',relaxAvail:'И без даты заезда',relaxSeen:'Включая просмотренные',relaxSearch:'Без поиска «{x}»',
  variants:'{n} {w}',
  listingsN:'{n} {w}',noDistrict:'район не указан',bedShort:'{n} сп',studio:'студия',sqm:'{n} м²',toPier:'{n} км до пирса',
  availNow:'свободно сейчас',availFrom:'с {d}',fewData:'мало данных',seen:'смотрел',maybeRented:'возможно, уже сдано',
  write:'Написать',open:'Открыть',openTg:'Открыть в Telegram',fromSrc:'из {s}',andMore:'ещё {n}',
  allSources:'Источники',airbnbHere:'Airbnb в этом районе ~{p} ฿/ночь',estimate:'оценка',medianTip:'медиана района {p} ฿/мес',
  report:'Пожаловаться / убрать',belowMkt:'ниже рынка −{n}%',aboveMkt:'выше рынка +{n}%',
  hotTitle:'Свежее и ниже рынка',loadMore:'Показать ещё {n} из {m}',
  today:'сегодня',yesterday:'вчера',daysAgo:'{n} дн.',weeksAgo:'{n} нед.',monthsAgo:'{n} мес.',rented:'снято',dateUnknown:'дата неизвестна',
  agoToday:'сегодня',agoDays:'{n} {w} назад',
  onMap:'<b>{a}</b> на карте · {b} без точного района — в списке',
  currency:'Валюта',theme:'Тема',themeAuto:'как в системе',themeLight:'светлая',themeDark:'тёмная',language:'Язык',
  shortcuts:'Горячие клавиши',kSearch:'поиск',kClear:'очистить поиск / закрыть',kReset:'сбросить фильтры',
  favsTitle:'Избранное',clearFavs:'Очистить избранное',noFavs:'Пока пусто',
  stDistrict:'Район',stCount:'Объявл.',stActive:'Актуальных',stMedian:'Медиана, ฿/мес',noDistrictRow:'Без района',
  perMonth:'/мес',perNight:'/ночь',priceNone:'цена не указана',upTo:'до {p}',fromP:'от {p}',bedsPlus:'{n}+ сп',
  phoneHidden:'[телефон скрыт — см. оригинал]',ownerTitle:'Сдаёшь жильё на Пхангане?',ownerText:'Добавим объявление на витрину бесплатно — его увидят те, кто уже ищет жильё с фильтром по району и цене.',ownerBtn:'Добавить объявление'},
en:{h1:'Rentals on Koh Phangan',searchPh:'Area, pool, 2br…',langAria:'Переключить на русский',favs:'Saved',menu:'Menu',filters:'Filters',
  watchSearch:'Get alerts for this search',watchShort:'Alerts',watchTip:'We will send new listings matching this filter to Telegram',
  watchNew:'Send me new listings',notifyMe:'Notify me when one appears',
  share:'share',copied:'copied',sortBy:'Sort:',sortFresh:'newest first',sortCheap:'cheapest',sortPricey:'most expensive',sortBelow:'below market',
  sortSqmDesc:'area: largest',sortSqmAsc:'area: smallest',sortPpsqm:'price/m²: lowest',sortDist:'closest to pier',
  footAbout:'SabaiStay collects public rental posts from Koh Phangan Telegram channels. We are not an agent: you contact the author directly, no commission. Every listing links to the original.',
  privacy:'Privacy',removeListing:'Remove a listing',districtStats:'Area stats',csv:'Download CSV',snapshot:'snapshot',
  mapAria:'Listings map',map:'Map',list:'List',toTop:'Back to top',
  allFilters:'Filters',district:'Area',price:'Price',beds:'Bedrooms',type:'Type',monthly:'Monthly',nightly:'Nightly',
  freshness:'Posted',wholeIsland:'Whole island',any:'Any',anyType:'Any',anyTime:'Any time',anyTerm:'Any term',
  term:'Rental term',amenities:'Amenities',area:'Area, m²',from:'from',to:'to',more:'More',
  onlyAvail:'Only with a move-in date',hideSeen:'Hide viewed',showStale:'Show outdated',
  clearAll:'Clear all',reset:'Reset',showN:'Show {n}',priceHint:'Price distribution in current results',
  age1:'today & yesterday',age3:'last 3 days',age7:'last week',age30:'last month',
  updated:'updated {ago}',fromChannels:'from {n} Telegram channels',
  infoP1:'We collect public posts from {n} Koh Phangan rental Telegram channels and sort them by area, price and bedrooms.',
  infoP2:'Every card links to the original post. We are not an agent: you message the author directly, no commission.',
  infoP3:'Your listing is here and you don\'t want it to be? <a href="privacy.html#remove">Remove it</a>.',
  staleAuto:'No fresh listings — last update <b>{ago}</b>. Showing the last known ones: some may be gone, message several at once.',
  staleOld:'Updated <b>{ago}</b> — some listings may be gone, message several at once.',
  showOldToo:'Show older too ({n})',
  emptyTitle:'Nothing matches this filter right now',emptyText:'Loosen one condition — this would help most:',
  emptyNoRelax:'Try resetting the filters, or subscribe and we will tell you when something appears.',
  noFreshTitle:'No fresh listings',noFreshText:'Last update {ago}. Older listings are hidden — some may still be available.',
  showKnown:'Show last known ({n})',resetFilters:'Reset filters',
  relaxDistrict:'Whole island',relaxPrice:'No price limit',relaxBed:'Any bedrooms',relaxType:'Any type',relaxAm:'Without “{x}”',
  relaxAge:'Any time',relaxPeriod:'Any term',relaxSqm:'Any size',relaxAvail:'Also without move-in date',relaxSeen:'Include viewed',relaxSearch:'Without “{x}” search',
  variants:'{n} {w}',
  listingsN:'{n} {w}',noDistrict:'area not given',bedShort:'{n} bd',studio:'studio',sqm:'{n} m²',toPier:'{n} km to pier',
  availNow:'available now',availFrom:'from {d}',fewData:'sparse data',seen:'viewed',maybeRented:'may already be taken',
  write:'Message',open:'Open',openTg:'Open in Telegram',fromSrc:'from {s}',andMore:'+{n} more',
  allSources:'Sources',airbnbHere:'Airbnb in this area ~{p} ฿/night',estimate:'estimate',medianTip:'area median {p} ฿/mo',
  report:'Report / remove',belowMkt:'below market −{n}%',aboveMkt:'above market +{n}%',
  hotTitle:'Fresh and below market',loadMore:'Show {n} more of {m}',
  today:'today',yesterday:'yesterday',daysAgo:'{n}d ago',weeksAgo:'{n}w ago',monthsAgo:'{n}mo ago',rented:'taken',dateUnknown:'date unknown',
  agoToday:'today',agoDays:'{n} {w} ago',
  onMap:'<b>{a}</b> on map · {b} without exact area are in the list',
  currency:'Currency',theme:'Theme',themeAuto:'system',themeLight:'light',themeDark:'dark',language:'Language',
  shortcuts:'Keyboard shortcuts',kSearch:'search',kClear:'clear search / close',kReset:'reset filters',
  favsTitle:'Saved',clearFavs:'Clear saved',noFavs:'Nothing yet',
  stDistrict:'Area',stCount:'Listings',stActive:'Active',stMedian:'Median, ฿/mo',noDistrictRow:'No area',
  perMonth:'/mo',perNight:'/night',priceNone:'price not given',upTo:'up to {p}',fromP:'from {p}',bedsPlus:'{n}+ bd',
  phoneHidden:'[phone hidden — see original]',ownerTitle:'Renting out a place on Phangan?',ownerText:'We will add your listing for free — people already searching by area and price will see it.',ownerBtn:'Add a listing'}};
const WORDS={ru:{listing:['объявление','объявления','объявлений'],variant:['вариант','варианта','вариантов'],day:['день','дня','дней']},
             en:{listing:['listing','listings'],variant:['option','options'],day:['day','days']}};
let LANG=(LS.get('sabaistay_lang')||((navigator.language||'ru').toLowerCase().startsWith('ru')?'ru':'en'));
if(!I18N[LANG])LANG='ru';
function t(k,v){let s=(I18N[LANG][k]??I18N.ru[k]??k);if(v)for(const x in v)s=s.split('{'+x+'}').join(v[x]);return s;}
function word(n,k){const f=WORDS[LANG][k];if(LANG==='en')return n===1?f[0]:f[1];
  return n%10===1&&n%100!==11?f[0]:(n%10>=2&&n%10<=4&&(n%100<10||n%100>=20)?f[1]:f[2]);}
function applyStatic(){
  document.documentElement.lang=LANG;
  document.querySelectorAll('[data-i18n]').forEach(e=>{e.textContent=t(e.dataset.i18n);});
  document.querySelectorAll('[data-i18n-ph]').forEach(e=>{e.placeholder=t(e.dataset.i18nPh);});
  document.querySelectorAll('[data-i18n-aria]').forEach(e=>{e.setAttribute('aria-label',t(e.dataset.i18nAria));});
  $('lang-btn').textContent=LANG==='ru'?'EN':'RU';
  document.querySelectorAll('.js-watch').forEach(e=>e.title=t('watchTip'));
}
const TYPE={ru:{villa:'вилла',house:'дом',room:'комната',studio:'студия',bungalow:'бунгало',apartment:'апартаменты',land:'участок',other:'жильё'},
            en:{villa:'villa',house:'house',room:'room',studio:'studio',bungalow:'bungalow',apartment:'apartment',land:'land',other:'place'}};
const AMEN={ru:{pool:'бассейн',seaview:'вид на море',kitchen:'кухня',aircon:'кондиционер',wifi:'Wi-Fi'},
            en:{pool:'pool',seaview:'sea view',kitchen:'kitchen',aircon:'air con',wifi:'Wi-Fi'}};
const AM_KEYS=['pool','seaview','kitchen','aircon','wifi'];
const TYPE_KEYS=['house','villa','apartment','bungalow','studio','room','other'];
const typeL=k=>TYPE[LANG][k]||k||TYPE[LANG].other;
const MON={ru:['','янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'],en:['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']};
function fmtDay(y,m,d){return LANG==='ru'?(d?(+d)+' '+MON.ru[+m]:MON.ru[+m]+' '+y):(d?MON.en[+m]+' '+(+d):MON.en[+m]+' '+y);}
const SRC={telegram:'Telegram',fb_group:'Facebook',fb_marketplace:'Facebook',booking:'Booking',airbnb:'Airbnb',manual:'SabaiStay',own:'SabaiStay'};
function srcName(s){const m=/t\.me\/(?:s\/)?([A-Za-z0-9_]+)/.exec(s.source_url||'');return m?'@'+m[1]:(SRC[s.source]||'↗');}

// ── свежесть считается В БРАУЗЕРЕ, а не запекается при сборке ───────────────
// Страница статическая: она живёт в кэше и на Vercel до следующего успешного сбора.
// Если зашить бейдж в момент генерации, объявление месячной давности так и будет
// показывать «сегодня» — ровно та ложь, после которой агрегатору перестают верить.
// Здесь считаем от реального времени пользователя; логика зеркалит parser/freshness.py,
// пороги подставляются оттуда при сборке, чтобы они не разъехались между Python и JS.
const FRESH_T={hot:__HOT_DAYS__,fresh:__FRESH_DAYS__,recent:__RECENT_DAYS__,expire:__EXPIRE_DAYS__};
function assessFresh(postedAt,rented){
  if(rented)return{status:'rented',age_days:null,label:'снято',cls:'stale',stale:true};
  const d=postedAt?new Date(String(postedAt).slice(0,10)):null;
  if(!d||isNaN(d))return{status:'active',age_days:null,label:'дата неизвестна',cls:'unknown',stale:false};
  const n=new Date();
  const age=Math.max(0,Math.floor((Date.UTC(n.getFullYear(),n.getMonth(),n.getDate())-d.getTime())/864e5));
  if(age>FRESH_T.expire)return{status:'expired',age_days:age,label:'🔴 неактуально',cls:'stale',stale:true};
  if(age<=FRESH_T.hot)return{status:'active',age_days:age,label:age===0?'⚡ сегодня':'⚡ вчера',cls:'hot',stale:false};
  if(age<=FRESH_T.fresh)return{status:'active',age_days:age,label:'🟢 '+age+' дн. назад',cls:'fresh',stale:false};
  if(age<=FRESH_T.recent)return{status:'active',age_days:age,label:age+' дн. назад',cls:'recent',stale:false};
  return{status:'active',age_days:age,label:Math.round(age/7)+' нед. назад',cls:'old',stale:false};
}
DATA.forEach(d=>{d.fresh=assessFresh(d.posted_at,d.rented);});
// Порядок по умолчанию — свежее сверху (свои featured остаются первыми).
DATA.sort((a,b)=>(b.featured?1:0)-(a.featured?1:0)||String(b.posted_at||'').localeCompare(String(a.posted_at||'')));
// Короткая подпись свежести для карточки (сам расчёт — assessFresh выше, не трогаем).
function freshText(fr){
  if(fr.status==='rented')return t('rented');
  const a=fr.age_days; if(a==null)return t('dateUnknown');
  if(a===0)return t('today'); if(a===1)return t('yesterday');
  if(a<14)return t('daysAgo',{n:a}); if(a<60)return t('weeksAgo',{n:Math.round(a/7)});
  return t('monthsAgo',{n:Math.round(a/30)});
}
function agoText(days){return days<=0?t('agoToday'):t('agoDays',{n:days,w:word(days,'day')});}
const SNAP_DAYS=SNAP_DATE?Math.max(0,Math.floor((Date.now()-SNAP_DATE.getTime())/864e5)):null;
const FRESH_TOTAL=DATA.filter(d=>!d.fresh.stale).length;
// Если живых объявлений нет вовсе (сбор стоял), прятать устаревшие по умолчанию = пустой
// сайт. Тогда по умолчанию показываем последние известные с честной пометкой о возрасте.
const DEFAULT_HIDE_STALE=FRESH_TOTAL>0;
const CHANNELS=new Set();DATA.forEach(d=>d.sources.forEach(s=>{const n=srcName(s);if(n[0]==='@')CHANNELS.add(n);}));

let CUR=LS.get('sabaistay_cur')==='usd'?'usd':'thb';
const RATE_USD=35;
const compact=n=>{if(CUR==='usd'){const u=Math.round(n/RATE_USD);return u>=1000?(u/1000).toFixed(1)+'k$':'$'+u;}return n>=1000?(Math.round(n/100)/10)+'k':(''+n);};
const priceFull=n=>{if(n==null)return null;if(CUR==='usd')return'$'+Math.round(n/RATE_USD).toLocaleString('en');return Math.round(n).toLocaleString('ru-RU')+' ฿';};
const firstUrl=d=>d.sources[0]?.source_url||'#';

// ── состояние фильтра: один объект, из него рисуются чипы, шторки и URL ──
const DEF={district:'',period:'',price:0,priceMin:0,bed:0,type:'',amenities:[],maxAge:0,hideStale:DEFAULT_HIDE_STALE,onlyAvail:false,hideSeen:false,sqmMin:0,sqmMax:0,search:'',sort:''};
let S=JSON.parse(JSON.stringify(DEF));
function readF(){return{...S,amenities:[...S.amenities]};}
function passes(d,f){
  if(f.district&&d.district!==f.district)return false;
  if(f.sqmMin&&(d.area_sqm==null||d.area_sqm<f.sqmMin))return false;
  if(f.sqmMax&&(d.area_sqm==null||d.area_sqm>f.sqmMax))return false;
  if(f.priceMin&&(d.price==null||d.price<f.priceMin))return false;
  if(f.price&&(d.price==null||d.price>f.price))return false;
  if(f.bed&&(d.bedrooms==null||d.bedrooms<f.bed))return false;
  if(f.type&&d.type!==f.type)return false;
  if(f.period==='month'&&!(d.period==='month'||d.period==='unknown'))return false;
  if(f.period==='night'&&d.period!=='night')return false;
  for(const a of f.amenities){if(!d.amenities[a])return false;}
  if(f.hideStale&&d.fresh.stale)return false;
  // Возраст: объявления без даты сюда не проходят — «когда угодно» это допускает,
  // а вот в срезе «за неделю» показывать неизвестно какой давности пост нечестно.
  if(f.maxAge&&(d.fresh.age_days==null||d.fresh.age_days>f.maxAge))return false;
  if(f.onlyAvail&&!d.available_from)return false;
  if(f.hideSeen&&getSeen().has(d.id))return false;
  if(f.search){const hay=(d.title+' '+d.text+' '+(d.district||'')+' '+(d.type||'')).toLowerCase();if(!hay.includes(f.search))return false;}
  return true;}
const countWith=f=>DATA.reduce((n,d)=>n+(passes(d,f)?1:0),0);

// ── избранное / просмотренные (localStorage, всё в try) ──
const FAV_KEY='sabaistay_fav',SEEN_KEY='sabaistay_seen';
function getFavs(){try{return JSON.parse(LS.get(FAV_KEY)||'[]');}catch(e){return[];}}
function saveFavs(a){LS.set(FAV_KEY,JSON.stringify(a));}
function getSeen(){try{return new Set(JSON.parse(LS.get(SEEN_KEY)||'[]'));}catch(e){return new Set();}}
function markSeen(id){const s=getSeen();s.add(id);LS.set(SEEN_KEY,JSON.stringify([...s]));}

// ── иконки ──
const IC={
  bm:'<svg class="ico" viewBox="0 0 24 24"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  ext:'<svg class="ico" viewBox="0 0 24 24" style="width:14px;height:14px"><path d="M7 17 17 7M9 7h8v8"/></svg>',
  msg:'<svg class="ico" viewBox="0 0 24 24" style="width:15px;height:15px"><path d="m22 3-9.5 18-2.5-8-8-2.5z"/></svg>',
  sliders:'<svg class="ico" viewBox="0 0 24 24"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
  list:'<svg class="ico" viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
  map:'<svg class="ico" viewBox="0 0 24 24"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></svg>'
};
// мини-силуэт острова с точкой района — замена фото-превью: мгновенно «где это»
function islandMark(d){
  if(d.own&&d.photos&&d.photos.length)return '<img src="'+esc(d.photos[0])+'" alt="" loading="lazy">';
  if(d.lat==null)return '<svg viewBox="0 0 64 64" aria-hidden="true"><path class="isl unk" d="'+ISLAND+'"/></svg>';
  const k=361.29,x=((d.lng-99.955)*k+5.806).toFixed(1),y=((9.822-d.lat)*k+3).toFixed(1);
  return '<svg viewBox="0 0 64 64" aria-hidden="true"><path class="isl" d="'+ISLAND+'"/><circle class="halo" cx="'+x+'" cy="'+y+'" r="6"/><circle class="dot" cx="'+x+'" cy="'+y+'" r="3"/></svg>';
}
// телефоны в тексте чужих объявлений не показываем (PDPA): связь — через оригинал
function maskPhones(s){return s.replace(/(?:\+\d|\b0)[\d\s().-]{7,}\d/g,m=>{const n=m.replace(/\D/g,'').length;return n>=9&&n<=13?t('phoneHidden'):m;});}
function benchHtml(d){
  const b=d.bench;if(!b||b.pct==null||Math.abs(b.pct)<15)return '';
  const tip=esc(t('medianTip',{p:Math.round(b.monthly_median).toLocaleString('ru-RU')}));
  return b.pct<0?'<span class="bench cheap" title="'+tip+'">'+t('belowMkt',{n:Math.abs(b.pct)})+'</span>'
                :'<span class="bench" title="'+tip+'">'+t('aboveMkt',{n:b.pct})+'</span>';
}
function availText(d){
  if(!d.available_from)return '';
  if(d.available_from==='now')return '<span class="now">'+t('availNow')+'</span>';
  const p=d.available_from.split('-');
  return esc(t('availFrom',{d:fmtDay(p[0],p[1],p[2])}));
}
const unitOf=d=>d.period==='night'?t('perNight'):(d.period==='sale'?'':t('perMonth'));
function hl(s,q){if(!q)return s;const re=new RegExp('('+q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');return s.replace(re,'<mark>$1</mark>');}

function makeCard(d,key){
  const card=document.createElement('article');
  const dim=d.fresh.stale&&FRESH_TOTAL>0;  // когда свежих нет вовсе, о возрасте говорит полоса сверху — не глушим всю ленту
  card.className='card'+(dim?' stale':'')+(d.own?' own':'');
  card.tabIndex=0;
  if(key!=null)card.dataset.key=key;
  const where=d.district?esc(d.district)+' · '+esc(typeL(d.type)):esc(typeL(d.type));
  const price=d.price!=null?'<span class="p">'+priceFull(d.price)+'<small>'+unitOf(d)+'</small></span>':'<span class="p none">'+t('priceNone')+'</span>';
  const facts=[];
  if(d.bedrooms!=null)facts.push(d.bedrooms===0?t('studio'):t('bedShort',{n:d.bedrooms}));
  if(!d.district)facts.push(t('noDistrict'));
  if(d.area_sqm)facts.push(t('sqm',{n:d.area_sqm}));
  if(d.dist_km!=null)facts.push(t('toPier',{n:String(d.dist_km).replace('.',LANG==='ru'?',':'.')}));
  if(d.price&&d.area_sqm)facts.push('≈'+Math.round(d.price/d.area_sqm).toLocaleString('ru-RU')+' ฿/м²');
  const am=AM_KEYS.filter(k=>d.amenities&&d.amenities[k]).map(k=>AMEN[LANG][k]);
  const av=availText(d); if(av)am.push(av);
  const seen=getSeen().has(d.id), saved=getFavs().includes(d.id);
  const tags=(d.own?'<span class="tag own">SabaiStay</span>':'')+(d.confidence!=null&&d.confidence<0.6?'<span class="tag">'+t('fewData')+'</span>':'')+(seen?'<span class="tag">'+t('seen')+'</span>':'');
  const tg=d.contact&&d.contact.telegram?d.contact.telegram.replace(/^@/,''):'';
  const isTg=(d.sources[0]?.source||'')==='telegram';
  const acts=(tg?'<a class="btn pri" href="https://t.me/'+esc(tg)+'" target="_blank" rel="noopener">'+IC.msg+t('write')+'</a>':'')+
    '<a class="btn '+(tg?'sec':'pri')+'" href="'+esc(firstUrl(d))+'" target="_blank" rel="noopener" title="'+(isTg?t('openTg'):t('open'))+'">'+t('open')+' '+IC.ext+'</a>';
  const more=d.sources.length>1?' · '+t('andMore',{n:d.sources.length-1}):'';
  card.innerHTML=
    ((d.own&&d.photos&&d.photos.length>1)?'<img class="own-photo" src="'+esc(d.photos[1])+'" alt="" loading="lazy">':'')+
    '<div class="c-mark">'+islandMark(d)+'</div>'+
    '<div class="c-body">'+
      '<div class="c-eye"><span class="c-where'+(d.district?'':' nogeo')+'">'+where+'</span><span class="c-fresh f-'+d.fresh.cls+'">'+freshText(d.fresh)+'</span></div>'+
      '<div class="c-price">'+price+benchHtml(d)+'</div>'+
      '<div class="c-facts">'+(facts.map(esc).join('<span class="sep">·</span>')||'&nbsp;')+tags+'</div>'+
      ((am.length||dim)?'<div class="c-amen">'+am.join('<span class="sep">·</span>')+(dim?(am.length?'<span class="sep">·</span>':'')+'<span class="stale-note">'+t('maybeRented')+'</span>':'')+'</div>':'')+
    '</div>'+
    '<button class="bm'+(saved?' saved':'')+'" type="button" data-id="'+esc(d.id)+'" aria-pressed="'+saved+'" aria-label="'+t('favs')+'">'+IC.bm+'</button>'+
    '<div class="c-foot"><button class="c-src" type="button" aria-expanded="false">'+t('fromSrc',{s:'<b>'+esc(srcName(d.sources[0]||{}))+'</b>'})+more+'</button>'+acts+'</div>';
  const toggle=()=>{
    const open=!card.classList.contains('open');
    card.classList.toggle('open',open);
    card.querySelector('.c-src').setAttribute('aria-expanded',String(open));
    let m=card.querySelector('.c-more');
    if(open&&!m){
      m=document.createElement('div');m.className='c-more';
      const b=d.bench;
      const txt=d.text?(d.own?d.text:maskPhones(d.text)):'';
      m.innerHTML=(txt?'<pre class="c-text">'+hl(esc(txt),S.search?esc(S.search):'')+'</pre>':'')+
        '<p class="c-meta">'+t('allSources')+':</p><div class="c-srcs">'+d.sources.map(s=>'<a href="'+esc(s.source_url)+'" target="_blank" rel="noopener">'+esc(srcName(s))+' ↗</a>').join('')+'</div>'+
        (b&&b.airbnb_adr?'<p class="c-meta">'+t('airbnbHere',{p:Math.round(b.airbnb_adr).toLocaleString('ru-RU')})+(b.seed?' ('+t('estimate')+')':'')+'</p>':'')+
        (b&&b.monthly_median?'<p class="c-meta">'+t('medianTip',{p:Math.round(b.monthly_median).toLocaleString('ru-RU')})+'</p>':'')+
        '<p class="c-meta"><a href="privacy.html#remove">'+t('report')+'</a></p>';
      card.appendChild(m);
    } else if(m) m.hidden=!open;
    if(open){markSeen(d.id);}
  };
  card.addEventListener('click',e=>{
    if(e.target.closest('a,.bm,.c-more'))return;
    toggle();
    if(key!=null&&d.lat!=null)focusMarker(key,true);
  });
  card.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target===card){e.preventDefault();card.click();}});
  card.querySelector('.bm').addEventListener('click',e=>{e.stopPropagation();toggleFav(d.id);});
  if(key!=null&&d.lat!=null){
    card.addEventListener('mouseenter',()=>pinOn(key,true));
    card.addEventListener('mouseleave',()=>pinOn(key,false));
  }
  return card;
}

// ── карта: Leaflet для пинов, подложка OpenFreeMap (MapLibre) — без ключей ──
let map=null,clusterGroup=null,markers={},basemap=null,basemapLoading=false,mapNote=null;
const STYLE={light:'https://tiles.openfreemap.org/styles/positron',dark:'https://tiles.openfreemap.org/styles/dark'};
const OFM_ATTR='<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">© OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
const isDark=()=>document.documentElement.getAttribute('data-theme')==='dark';
const isMobile=()=>matchMedia('(max-width:760px)').matches;
if(HAS_L){
  map=L.map('map',{zoomControl:true,attributionControl:true,maxZoom:18,minZoom:10}).setView([9.745,100.025],12);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
  clusterGroup=L.markerClusterGroup({maxClusterRadius:50,showCoverageOnHover:false,
    iconCreateFunction:c=>L.divIcon({className:'',html:'<div class="cluster">'+c.getChildCount()+'</div>',iconSize:[36,36]})});
  map.addLayer(clusterGroup);
  const Note=L.Control.extend({onAdd(){const e=L.DomUtil.create('div','mapnote');L.DomEvent.disableClickPropagation(e);return e;}});
  mapNote=new Note({position:'bottomleft'}).addTo(map);
  const Coords=L.Control.extend({onAdd(){const e=L.DomUtil.create('div','coords');e.textContent='9.74°N · 100.01°E';return e;}});
  new Coords({position:'topright'}).addTo(map);
  let _rsz;
  addEventListener('resize',()=>{clearTimeout(_rsz);_rsz=setTimeout(()=>map.invalidateSize(),200);});
  addEventListener('orientationchange',()=>setTimeout(()=>map.invalidateSize(),300));
}
function loadScript(src){return new Promise((ok,fail)=>{const s=document.createElement('script');s.src=src;s.onload=ok;s.onerror=fail;document.head.appendChild(s);});}
function loadCss(href){const l=document.createElement('link');l.rel='stylesheet';l.href=href;document.head.appendChild(l);}
function rasterFallback(){
  // Запасная подложка, если нет WebGL или CDN MapLibre недоступен: тайлы OSM (малый трафик, атрибуция обязательна).
  basemap=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,
    attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'}).addTo(map);
  basemap.bringToBack();
}
function hasWebGL(){try{const c=document.createElement('canvas');return !!(c.getContext('webgl2')||c.getContext('webgl'));}catch(e){return false;}}
// Подложку грузим только когда карта на экране: на мобиле список первичен, и ~200 КБ
// векторного движка не должны съедать трафик тому, кто карту не открывал.
function ensureBasemap(){
  if(!map||basemap||basemapLoading)return;
  basemapLoading=true;
  if(!hasWebGL()){rasterFallback();return;}
  loadCss('https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css');
  loadScript('https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js')
    .then(()=>loadScript('https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.0/leaflet-maplibre-gl.js'))
    .then(()=>{
      basemap=L.maplibreGL({style:isDark()?STYLE.dark:STYLE.light,attribution:OFM_ATTR}).addTo(map);
      tintBasemap();
      const gl=basemap.getMaplibreMap&&basemap.getMaplibreMap();
      if(gl)gl.on('style.load',tintBasemap);
    })
    .catch(()=>{rasterFallback();});
}
// Палитра подложки под токены страницы: суша — «бумага», вода — тихая бирюза.
function tintBasemap(){
  const gl=basemap&&basemap.getMaplibreMap&&basemap.getMaplibreMap();
  if(!gl||!gl.isStyleLoaded||!gl.getLayer)return;
  const dark=isDark();
  try{
    if(gl.getLayer('background'))gl.setPaintProperty('background','background-color',dark?'#13262c':'#ecebdf');
    if(gl.getLayer('water'))gl.setPaintProperty('water','fill-color',dark?'#0a1a20':'#cfe3e2');
  }catch(e){}
}
function setBasemapTheme(){
  if(!basemap)return;
  if(basemap.getMaplibreMap){basemap.getMaplibreMap().setStyle(isDark()?STYLE.dark:STYLE.light);}
}
function pinOn(key,on){const m=markers[key];const el=m&&m.getElement&&m.getElement();const p=el&&el.querySelector('.pin');if(p)p.classList.toggle('on',on);}
let _activeKey=null;
function focusMarker(key,fly){
  const m=markers[key];if(!m||!map)return;
  if(_activeKey!=null)pinOn(_activeKey,false);
  _activeKey=key;
  if(isMobile())return;  // на мобиле карта скрыта под списком — не дёргаем её
  const rm=matchMedia('(prefers-reduced-motion: reduce)').matches;
  clusterGroup.zoomToShowLayer(m,()=>{pinOn(key,true);});
  if(fly)map.flyTo(m.getLatLng(),Math.max(map.getZoom(),14),{duration:rm?0:.5,animate:!rm});
}
function popupHtml(d){
  return '<div class="mp-where">'+esc(d.district||'')+' · '+esc(typeL(d.type))+'</div>'+
    '<div class="mp-price">'+(d.price!=null?priceFull(d.price)+'<small>'+unitOf(d)+'</small>':t('priceNone'))+'</div>'+
    '<div class="mp-meta">'+(d.bedrooms!=null?t('bedShort',{n:d.bedrooms})+' · ':'')+freshText(d.fresh)+'</div>'+
    '<div class="mp-acts"><a class="btn pri" href="'+esc(firstUrl(d))+'" target="_blank" rel="noopener">'+t('open')+' '+IC.ext+'</a></div>';
}

// ── выдача ──
const PAGE_SIZE=30;
let _view=[],_rendered=0;
function sortView(view,s){
  const P=x=>x==null?Infinity:x;
  if(s==='cheap')view.sort((a,b)=>P(a.price)-P(b.price));
  else if(s==='pricey')view.sort((a,b)=>(b.price??-1)-(a.price??-1));
  else if(s==='below')view.sort((a,b)=>P(a.bench&&a.bench.pct)-P(b.bench&&b.bench.pct));
  else if(s==='sqm_desc')view.sort((a,b)=>(b.area_sqm??-1)-(a.area_sqm??-1));
  else if(s==='sqm_asc')view.sort((a,b)=>P(a.area_sqm)-P(b.area_sqm));
  else if(s==='price_sqm_asc')view.sort((a,b)=>P(a.price&&a.area_sqm?a.price/a.area_sqm:null)-P(b.price&&b.area_sqm?b.price/b.area_sqm:null));
  else if(s==='dist_asc')view.sort((a,b)=>P(a.dist_km)-P(b.dist_km));
  return [...view.filter(d=>d.featured),...view.filter(d=>!d.featured)];
}
function renderPage(){
  const cards=$('cards');
  const end=Math.min(_rendered+PAGE_SIZE,_view.length);
  for(let i=_rendered;i<end;i++){
    cards.appendChild(makeCard(_view[i],i));
    if(i===2&&_rendered===0){const h=hotSection();if(h)cards.appendChild(h);}
  }
  _rendered=end;
  const rem=_view.length-_rendered;
  $('load-more').hidden=rem<=0;
  if(rem>0)$('load-more-btn').textContent=t('loadMore',{n:Math.min(rem,PAGE_SIZE),m:rem});
}
function render(first){
  const f=readF();
  const cards=$('cards'); cards.innerHTML='';
  _view=sortView(DATA.filter(d=>passes(d,f)),f.sort);
  const shown=_view.length;
  // маркеры
  markers={};_activeKey=null;
  if(clusterGroup){
    clusterGroup.clearLayers();
    const ms=[];
    _view.forEach((d,i)=>{
      if(d.lat==null)return;
      const icon=L.divIcon({className:'',html:'<div class="pin'+(d.fresh.stale&&FRESH_TOTAL>0?' dim':'')+'">'+(d.price!=null?compact(d.price):'·')+'</div>',iconSize:null});
      const m=L.marker([d.lat,d.lng],{icon,keyboard:false});
      m.bindPopup(()=>popupHtml(d),{closeButton:true,autoPanPadding:[30,30]});
      m.on('click',()=>{
        if(_activeKey!=null)pinOn(_activeKey,false);
        _activeKey=i;pinOn(i,true);
        if(isMobile())return;
        let c=cards.querySelector('.card[data-key="'+i+'"]');
        while(!c&&_rendered<_view.length){renderPage();c=cards.querySelector('.card[data-key="'+i+'"]');}
        if(c){cards.querySelectorAll('.card.active').forEach(x=>x.classList.remove('active'));c.classList.add('active');c.scrollIntoView({behavior:'smooth',block:'nearest'});}
      });
      markers[i]=m;ms.push(m);
    });
    clusterGroup.addLayers(ms);
    const onMap=ms.length;
    mapNote.getContainer().innerHTML=t('onMap',{a:onMap,b:shown-onMap});
    mapNote.getContainer().hidden=!shown;
  }
  _rendered=0;
  if(shown)renderPage(); else {cards.appendChild(emptyState(f));$('load-more').hidden=true;}
  // строка результата
  $('rcount').textContent=t('listingsN',{n:shown,w:word(shown,'listing')});
  $('f-sort').value=f.sort;
  renderDataAge(f);
  // возраст уже сказан полосой сверху — не повторяем его в строке результата
  $('rupd').textContent=(SNAP_DAYS!=null&&$('data-age').hidden?t('updated',{ago:agoText(SNAP_DAYS)})+' · ':'')+t('fromChannels',{n:CHANNELS.size});
  renderChips();
  writeHash(f);
  updateWatchLink(f,shown);
  if(SHEET.kind)refreshSheet(false);
}
// Изменение фильтра — это новая выдача, а не продолжение прежней: список в начало.
function applyFilter(){
  const list=$('list'); if(list)list.scrollTop=0;
  if(window.scrollY>0&&isMobile()&&!SHEET.kind)window.scrollTo({top:0});
  render(false);
  const c=$('cards');c.classList.remove('refresh');void c.offsetWidth;c.classList.add('refresh');
}
function setF(patch){Object.assign(S,patch);applyFilter();}

// ── возраст данных: тихо, пока свежо; полоса — только если снимок старше 3 дней ──
function renderDataAge(f){
  const el=$('data-age');
  if(SNAP_DAYS==null||SNAP_DAYS<3){el.hidden=true;return;}
  const ago=agoText(SNAP_DAYS);
  if(!f.hideStale&&FRESH_TOTAL===0){el.innerHTML='<span>'+t('staleAuto',{ago})+'</span>';}
  else{
    const hidden=f.hideStale?countWith({...f,hideStale:false})-countWith(f):0;
    el.innerHTML='<span>'+t('staleOld',{ago})+'</span>'+(hidden?'<button type="button" id="show-old">'+t('showOldToo',{n:hidden})+'</button>':'');
    const b=$('show-old');if(b)b.onclick=()=>setF({hideStale:false});
  }
  el.hidden=false;
}

// ── пустое состояние: что ослабить, с живыми счётчиками ──
function relaxOptions(f){
  const o=[];
  const add=(label,patch)=>{const n=countWith({...f,...patch});if(n>0)o.push({label,patch,n});};
  if(f.district)add(t('relaxDistrict'),{district:''});
  if(f.price||f.priceMin)add(t('relaxPrice'),{price:0,priceMin:0});
  if(f.bed)add(t('relaxBed'),{bed:0});
  if(f.type)add(t('relaxType'),{type:''});
  f.amenities.forEach(a=>add(t('relaxAm',{x:AMEN[LANG][a]||a}),{amenities:f.amenities.filter(x=>x!==a)}));
  if(f.maxAge)add(t('relaxAge'),{maxAge:0});
  if(f.period)add(t('relaxPeriod'),{period:''});
  if(f.sqmMin||f.sqmMax)add(t('relaxSqm'),{sqmMin:0,sqmMax:0});
  if(f.onlyAvail)add(t('relaxAvail'),{onlyAvail:false});
  if(f.hideSeen)add(t('relaxSeen'),{hideSeen:false});
  if(f.search)add(t('relaxSearch',{x:f.search}),{search:''});
  return o.sort((a,b)=>b.n-a.n).slice(0,3);
}
function emptyState(f){
  const box=document.createElement('div');box.className='empty';
  const watch=BOT_USERNAME?'<a class="btn pri js-watch" href="'+esc(watchHref(f))+'" target="_blank" rel="noopener">🔔 '+t('notifyMe')+'</a>':'';
  const withStale=f.hideStale?countWith({...f,hideStale:false}):0;
  if(withStale>0){
    // ноль только из-за устаревания: честно говорим и даём показать последние известные
    box.innerHTML='<span class="big">🧭</span><h2>'+t('noFreshTitle')+'</h2><p>'+t('noFreshText',{ago:SNAP_DAYS!=null?agoText(SNAP_DAYS):'—'})+'</p>'+
      '<div class="empty-actions"><button class="btn '+(watch?'sec':'pri')+'" type="button" data-act="known">'+t('showKnown',{n:withStale})+'</button>'+watch+'</div>';
  }else{
    const rx=relaxOptions(f);
    box.innerHTML='<span class="big">🧭</span><h2>'+t('emptyTitle')+'</h2><p>'+(rx.length?t('emptyText'):t('emptyNoRelax'))+'</p>'+
      (rx.length?'<div class="relax">'+rx.map((r,i)=>'<button type="button" data-rx="'+i+'">'+esc(r.label)+' — <b>'+r.n+'</b> '+word(r.n,'variant')+'</button>').join('')+'</div>':'')+
      '<div class="empty-actions">'+watch+'<button class="btn '+(watch?'sec':'pri')+'" type="button" data-act="reset">'+t('resetFilters')+'</button></div>';
    box.querySelectorAll('[data-rx]').forEach(b=>b.onclick=()=>setF(rx[+b.dataset.rx].patch));
  }
  box.querySelector('[data-act="known"]')?.addEventListener('click',()=>setF({hideStale:false}));
  box.querySelector('[data-act="reset"]')?.addEventListener('click',resetFilters);
  return box;
}

// ── горячие: только по-настоящему свежее и заметно ниже медианы района ──
function hotSection(){
  // Медиану считаем по всем живым объявлениям района — иначе на узкой выборке
  // «свежих» она пляшет и любая цена оказывается «ниже рынка».
  const active=DATA.filter(d=>!d.fresh.stale&&d.price!=null&&(d.period==='month'||d.period==='unknown')&&d.district&&d.lat!=null);
  const med={},byD={};
  active.forEach(d=>{(byD[d.district]=byD[d.district]||[]).push(d.price);});
  Object.entries(byD).forEach(([k,p])=>{const s=[...p].sort((a,b)=>a-b);const m=Math.floor(s.length/2);med[k]=s.length%2?s[m]:Math.round((s[m-1]+s[m])/2);});
  // Нижняя отсечка отбраковывает ошибки разбора (суточная цена, принятая за месячную).
  const HOT_MAX_AGE=14;
  const hot=active.filter(d=>d.fresh.age_days!=null&&d.fresh.age_days<=HOT_MAX_AGE&&med[d.district]&&d.price<med[d.district]*0.85&&d.price>med[d.district]*0.35)
    .sort((a,b)=>String(b.posted_at||'').localeCompare(String(a.posted_at||''))).slice(0,4);
  if(hot.length<2)return null;
  const sec=document.createElement('section');sec.className='hot';
  sec.innerHTML='<h3>'+t('hotTitle')+'</h3><div class="hot-row">'+hot.map((d,i)=>{
    const disc=Math.round((1-d.price/med[d.district])*100);
    return '<button type="button" class="hot-card" data-i="'+i+'"><div class="hp">'+priceFull(d.price)+'<small style="font:500 12px var(--body);color:var(--muted)">'+t('perMonth')+'</small></div>'+
      '<div class="hm">'+esc(d.district)+' · −'+disc+'% · '+freshText(d.fresh)+'</div></button>';}).join('')+'</div>';
  sec.querySelectorAll('.hot-card').forEach(b=>b.onclick=()=>window.open(firstUrl(hot[+b.dataset.i]),'_blank','noopener'));
  return sec;
}

// ── ряд чипов ──
const AGE_KEYS={1:'age1',3:'age3',7:'age7',30:'age30'};
const kfmt=n=>CUR==='usd'?'$'+Math.round(n/RATE_USD):(n>=1000?(Math.round(n/100)/10)+'k':n+'');
function activeCount(){
  return (S.district?1:0)+(S.period?1:0)+(S.maxAge?1:0)+((S.sqmMin||S.sqmMax)?1:0)+((S.price||S.priceMin)?1:0)+(S.bed?1:0)+(S.type?1:0)+
    S.amenities.length+(S.onlyAvail?1:0)+(S.hideSeen?1:0)+(S.hideStale!==DEFAULT_HIDE_STALE?1:0);
}
function renderChips(){
  const n=activeCount();
  const priceLbl=S.price&&S.priceMin?kfmt(S.priceMin)+'–'+kfmt(S.price):S.price?t('upTo',{p:kfmt(S.price)}):S.priceMin?t('fromP',{p:kfmt(S.priceMin)}):t('price');
  const c=(k,label,on)=>'<button type="button" class="chip'+(on?' on':'')+'" data-chip="'+k+'" aria-haspopup="dialog">'+esc(label)+' <span class="car">▾</span></button>';
  $('chips').innerHTML=
    '<button type="button" class="chip'+(n?' on':'')+'" data-chip="all" aria-haspopup="dialog">'+IC.sliders+t('allFilters')+(n?' <span class="cnt">'+n+'</span>':'')+'</button>'+
    c('district',S.district||t('district'),!!S.district)+
    c('price',priceLbl,!!(S.price||S.priceMin))+
    c('bed',S.bed?t('bedsPlus',{n:S.bed}):t('beds'),!!S.bed)+
    c('type',S.type?typeL(S.type):t('type'),!!S.type)+
    '<span class="seg" role="group" aria-label="'+t('term')+'"><button type="button" data-period="month" class="'+(S.period==='month'?'on':'')+'" aria-pressed="'+(S.period==='month')+'">'+t('monthly')+'</button><button type="button" data-period="night" class="'+(S.period==='night'?'on':'')+'" aria-pressed="'+(S.period==='night')+'">'+t('nightly')+'</button></span>'+
    c('age',S.maxAge?t(AGE_KEYS[S.maxAge]||'freshness'):t('freshness'),!!S.maxAge);
}
$('chips').addEventListener('click',e=>{
  const p=e.target.closest('[data-period]');
  if(p){setF({period:S.period===p.dataset.period?'':p.dataset.period});return;}
  const c=e.target.closest('[data-chip]');if(!c)return;
  const k=c.dataset.chip;
  if(SHEET.kind===k){closeSheet();return;}
  openSheet(k,c);
});

// ── шторка / поповер / модалка ──
const SHEET={kind:null,anchor:null,el:null,scrim:null};
const SHEET_TITLES={all:'allFilters',district:'district',price:'price',bed:'beds',type:'type',age:'freshness',menu:'menu',favs:'favsTitle',stats:'districtStats',keys:'shortcuts',info:'fromChannels'};
const FILTER_KINDS=['all','district','price','bed','type','age'];
function openSheet(kind,anchor){
  closeSheet(true);
  SHEET.kind=kind;SHEET.anchor=anchor||null;
  const scrim=document.createElement('div');scrim.className='scrim'+((kind==='all'||['favs','stats','keys'].includes(kind))?' dim':'');
  scrim.onclick=()=>closeSheet();
  const el=document.createElement('div');
  const isModal=!anchor||kind==='all';
  el.className='sheet anim-in '+(isModal?'modal':'pop')+(kind==='all'?' full':'');
  el.setAttribute('role','dialog');el.setAttribute('aria-modal','true');
  const filt=FILTER_KINDS.includes(kind);
  el.innerHTML='<div class="sh-handle" aria-hidden="true"></div><div class="sh-head"><h2></h2><button class="sh-x" type="button" aria-label="×">✕</button></div>'+
    '<div class="sh-body"></div>'+(filt?'<div class="sh-foot"><button type="button" class="clr"></button><button type="button" class="show"></button></div>':'');
  document.body.append(scrim,el);document.body.classList.add('sheet-open');
  SHEET.el=el;SHEET.scrim=scrim;
  el.querySelector('.sh-x').onclick=()=>closeSheet();
  if(filt){
    el.querySelector('.clr').onclick=()=>{
      if(kind==='all')resetFilters();
      else setF(kind==='district'?{district:''}:kind==='price'?{price:0,priceMin:0}:kind==='bed'?{bed:0}:kind==='type'?{type:''}:{maxAge:0});
    };
    el.querySelector('.show').onclick=()=>closeSheet();
  }
  refreshSheet(true);
  placeSheet();
  swipeToClose(el);
  (el.querySelector('.sh-body button, .sh-body input, .sh-body a')||el.querySelector('.sh-x')).focus({preventScroll:true});
}
function placeSheet(){
  const el=SHEET.el;if(!el)return;
  if(!el.classList.contains('pop')||isMobile()){el.style.left=el.style.top='';return;}
  const r=SHEET.anchor.getBoundingClientRect();
  const w=el.offsetWidth;
  el.style.top=(r.bottom+8)+'px';
  el.style.left=Math.max(12,Math.min(r.left,innerWidth-w-12))+'px';
}
function closeSheet(silent){
  if(!SHEET.el)return;
  SHEET.el.remove();SHEET.scrim.remove();
  const a=SHEET.anchor;SHEET.kind=null;SHEET.el=null;SHEET.scrim=null;SHEET.anchor=null;
  document.body.classList.remove('sheet-open');
  if(!silent&&a&&document.contains(a))a.focus({preventScroll:true});
}
// свайп вниз по шапке шторки закрывает её (мобила)
function swipeToClose(el){
  let y0=null,dy=0;
  const head=[el.querySelector('.sh-handle'),el.querySelector('.sh-head')];
  head.forEach(h=>{
    h.addEventListener('touchstart',e=>{y0=e.touches[0].clientY;dy=0;el.style.transition='none';},{passive:true});
    h.addEventListener('touchmove',e=>{if(y0==null)return;dy=Math.max(0,e.touches[0].clientY-y0);el.style.transform='translateY('+dy+'px)';},{passive:true});
    h.addEventListener('touchend',()=>{if(y0==null)return;el.style.transition='transform .2s var(--ease)';
      if(dy>80)closeSheet();else el.style.transform='';y0=null;});
  });
}
function refreshSheet(first){
  const el=SHEET.el;if(!el)return;
  const k=SHEET.kind;
  el.querySelector('h2').textContent=k==='info'?'SabaiStay':k==='price'?t('price')+', ฿'+(S.period==='night'?t('perNight'):t('perMonth')):t(SHEET_TITLES[k]||k,{n:CHANNELS.size});
  const body=el.querySelector('.sh-body');
  const st=body.scrollTop;
  const ae=document.activeElement;
  const typing=ae&&body.contains(ae)&&ae.tagName==='INPUT'&&ae.type!=='checkbox';
  if(first||!typing){
    body.innerHTML=sheetBody(k);
    bindSheet(body,k);
    body.scrollTop=st;
  }else updateHist(body);
  if(FILTER_KINDS.includes(k)){
    const n=countWith(readF());
    el.querySelector('.show').textContent=t('showN',{n:n+' '+word(n,'variant')});
    el.querySelector('.clr').textContent=k==='all'?t('clearAll'):t('reset');
  }
}
function pills(items,cur,attr,cls){return '<div class="'+(cls||'pills')+'">'+items.map(([v,l,n])=>'<button type="button" class="pill'+(String(cur)===String(v)?' on':'')+'" data-'+attr+'="'+esc(v)+'" aria-pressed="'+(String(cur)===String(v))+'">'+esc(l)+(n!=null?' <small>'+n+'</small>':'')+'</button>').join('')+'</div>';}
function secDistrict(){
  const f={...readF(),district:''};const cnt={};
  DATA.forEach(d=>{if(d.district&&passes(d,f))cnt[d.district]=(cnt[d.district]||0)+1;});
  const ds=[...new Set(DATA.filter(d=>d.district).map(d=>d.district))].sort();
  return '<div class="sec"><h3>'+t('district')+'</h3>'+pills([['',t('wholeIsland'),countWith(f)],...ds.map(d=>[d,d,cnt[d]||0])],S.district,'district')+'</div>';
}
function priceValues(){
  const f={...readF(),price:0,priceMin:0};
  const night=S.period==='night';
  return DATA.filter(d=>d.price!=null&&(night?d.period==='night':(d.period==='month'||d.period==='unknown'))&&passes(d,f)).map(d=>d.price).sort((a,b)=>a-b);
}
let _hist=null;
function secPrice(){
  const vals=priceValues(),night=S.period==='night',step=night?100:1000;
  const unit=night?'฿'+t('perNight'):'฿'+t('perMonth');
  // Шкала по 90-му перцентилю с потолком: пара объявлений «продажа за 1 млн», принятых за аренду,
  // иначе сплющивает всю гистограмму в один столбик. Всё дороже — в последнем столбике.
  let max=vals.length?vals[Math.min(vals.length-1,Math.floor(vals.length*0.9))]:(night?5000:100000);
  max=Math.min(night?20000:200000,Math.max(step*10,Math.ceil(max/step)*step));
  const B=28,bins=new Array(B).fill(0);
  vals.forEach(v=>{bins[Math.min(B-1,Math.floor(v/max*B))]++;});
  _hist={max,step,B,bins:Math.max(...bins,1)};
  const lo=Math.min(S.priceMin||0,max),hi=S.price?Math.min(S.price,max):max;
  return '<div class="sec"><h3>'+t('price')+', '+unit+'</h3>'+(vals.length>3?'<p class="hint">'+t('priceHint')+'</p>'+
    '<div class="hist" aria-hidden="true">'+bins.map(b=>'<i style="height:'+Math.max(3,Math.round(b/_hist.bins*100))+'%"></i>').join('')+'</div>'+
    '<div class="dual"><div class="track"></div><div class="fill"></div>'+
    '<input type="range" id="pr-lo" min="0" max="'+max+'" step="'+step+'" value="'+lo+'" aria-label="'+t('from')+'">'+
    '<input type="range" id="pr-hi" min="0" max="'+max+'" step="'+step+'" value="'+hi+'" aria-label="'+t('to')+'"></div>':'')+
    '<div class="range2"><label>'+t('from')+'<input type="number" inputmode="numeric" id="pr-min" step="'+step+'" min="0" value="'+(S.priceMin||'')+'" placeholder="0"></label><span class="dash">—</span>'+
    '<label>'+t('to')+'<input type="number" inputmode="numeric" id="pr-max" step="'+step+'" min="0" value="'+(S.price||'')+'" placeholder="'+max.toLocaleString('ru-RU')+'+"></label></div></div>';
}
function updateHist(body){
  if(!_hist)return;
  const lo=S.priceMin||0,hi=S.price||_hist.max;
  body.querySelectorAll('.hist i').forEach((b,i)=>{const a=i*_hist.max/_hist.B,z=(i+1)*_hist.max/_hist.B;b.classList.toggle('in',z>lo&&a<hi);});
  const fill=body.querySelector('.dual .fill');
  if(fill){const L0=Math.min(lo,_hist.max)/_hist.max,H0=Math.min(hi,_hist.max)/_hist.max;
    fill.style.left='calc(9px + '+L0+' * (100% - 18px))';fill.style.width='calc('+(H0-L0)+' * (100% - 18px))';}
}
function secBed(){return '<div class="sec"><h3>'+t('beds')+'</h3>'+pills([[0,t('any')],[1,'1+'],[2,'2+'],[3,'3+'],[4,'4+']],S.bed,'bed','segw')+'</div>';}
function secType(){
  const f={...readF(),type:''};const cnt={};DATA.forEach(d=>{if(passes(d,f))cnt[d.type||'other']=(cnt[d.type||'other']||0)+1;});
  return '<div class="sec"><h3>'+t('type')+'</h3>'+pills([['',t('anyType')],...TYPE_KEYS.filter(k=>cnt[k]).map(k=>[k,typeL(k),cnt[k]])],S.type,'type')+'</div>';
}
function secAge(){return '<div class="sec"><h3>'+t('freshness')+'</h3>'+pills([[0,t('anyTime')],[1,t('age1')],[3,t('age3')],[7,t('age7')],[30,t('age30')]],S.maxAge,'age')+'</div>';}
function secPeriod(){return '<div class="sec"><h3>'+t('term')+'</h3>'+pills([['',t('anyTerm')],['month',t('monthly')],['night',t('nightly')]],S.period,'period2')+'</div>';}
function secAmen(){return '<div class="sec"><h3>'+t('amenities')+'</h3><div class="tiles">'+AM_KEYS.map(k=>'<label class="tile"><input type="checkbox" data-am="'+k+'"'+(S.amenities.includes(k)?' checked':'')+'>'+AMEN[LANG][k]+'</label>').join('')+'</div></div>';}
function secArea(){return '<div class="sec"><h3>'+t('area')+'</h3><div class="range2"><label>'+t('from')+'<input type="number" inputmode="numeric" id="sq-min" step="10" min="0" value="'+(S.sqmMin||'')+'"></label><span class="dash">—</span><label>'+t('to')+'<input type="number" inputmode="numeric" id="sq-max" step="10" min="0" value="'+(S.sqmMax||'')+'"></label></div></div>';}
function secMore(){
  const ck=(id,on,l)=>'<label class="chk"><input type="checkbox" data-flag="'+id+'"'+(on?' checked':'')+'>'+l+'</label>';
  return '<div class="sec"><h3>'+t('more')+'</h3>'+ck('onlyAvail',S.onlyAvail,t('onlyAvail'))+ck('hideSeen',S.hideSeen,t('hideSeen'))+ck('showStale',!S.hideStale,t('showStale'))+'</div>';
}
function sheetBody(k){
  if(k==='district')return secDistrict();
  if(k==='price')return secPrice();
  if(k==='bed')return secBed();
  if(k==='type')return secType();
  if(k==='age')return secAge();
  if(k==='all')return secDistrict()+secPrice()+secBed()+secType()+secPeriod()+secAmen()+secAge()+secArea()+secMore();
  if(k==='menu')return menuBody();
  if(k==='favs')return favsBody();
  if(k==='stats')return statsBody();
  if(k==='info')return '<div class="infopop" style="position:static;box-shadow:none;border:none;padding:6px 0 12px;max-width:none"><p>'+t('infoP1',{n:CHANNELS.size})+'</p><p>'+t('infoP2')+'</p>'+(SNAP_ISO?'<p>'+t('snapshot')+' <span class="num">'+SNAP_ISO+'</span></p>':'')+'<p>'+t('infoP3')+'</p></div>';
  if(k==='keys')return '<div class="kbd-row"><kbd>/</kbd> '+t('kSearch')+'</div><div class="kbd-row"><kbd>Esc</kbd> '+t('kClear')+'</div><div class="kbd-row"><kbd>r</kbd> '+t('kReset')+'</div>';
  return '';
}
function bindSheet(body,k){
  body.querySelectorAll('[data-district]').forEach(b=>b.onclick=()=>setF({district:b.dataset.district}));
  body.querySelectorAll('[data-bed]').forEach(b=>b.onclick=()=>setF({bed:+b.dataset.bed}));
  body.querySelectorAll('[data-type]').forEach(b=>b.onclick=()=>setF({type:b.dataset.type}));
  body.querySelectorAll('[data-age]').forEach(b=>b.onclick=()=>setF({maxAge:+b.dataset.age}));
  body.querySelectorAll('[data-period2]').forEach(b=>b.onclick=()=>setF({period:b.dataset.period2,price:0,priceMin:0}));
  body.querySelectorAll('[data-am]').forEach(c=>c.onchange=()=>setF({amenities:AM_KEYS.filter(a=>body.querySelector('[data-am="'+a+'"]').checked)}));
  body.querySelectorAll('[data-flag]').forEach(c=>c.onchange=()=>{const v=c.checked,fl=c.dataset.flag;setF(fl==='showStale'?{hideStale:!v}:{[fl]:v});});
  const lo=body.querySelector('#pr-lo'),hi=body.querySelector('#pr-hi');
  if(lo&&hi){
    const sync=()=>{let a=+lo.value,b=+hi.value;if(a>b){[a,b]=[b,a];}
      S.priceMin=a||0;S.price=b>=_hist.max?0:b;
      const mn=body.querySelector('#pr-min'),mx=body.querySelector('#pr-max');mn.value=S.priceMin||'';mx.value=S.price||'';
      applyFilter();updateHist(body);};
    lo.oninput=sync;hi.oninput=sync;
  }
  const num=(id,key)=>{const e=body.querySelector('#'+id);if(e)e.oninput=()=>{S[key]=+e.value||0;applyFilter();updateHist(body);};};
  num('pr-min','priceMin');num('pr-max','price');num('sq-min','sqmMin');num('sq-max','sqmMax');
  updateHist(body);
  if(k==='menu')bindMenu(body);
  if(k==='favs')bindFavs(body);
}

// ── меню ☰: валюта, тема, язык, статистика, CSV, клавиши ──
function themeMode(){return LS.get('sabaistay_theme')||'auto';}
function menuBody(){
  const tm=themeMode();
  return '<div class="menu-list">'+
    '<button type="button" data-m="cur">'+t('currency')+'<span class="val">'+(CUR==='usd'?'$ USD':'฿ THB')+'</span></button>'+
    '<button type="button" data-m="theme">'+t('theme')+'<span class="val">'+t(tm==='dark'?'themeDark':tm==='light'?'themeLight':'themeAuto')+'</span></button>'+
    '<button type="button" data-m="lang">'+t('language')+'<span class="val">'+(LANG==='ru'?'Русский':'English')+'</span></button>'+
    '<button type="button" data-m="stats">'+t('districtStats')+'</button>'+
    '<button type="button" data-m="csv">'+t('csv')+'</button>'+
    '<button type="button" data-m="keys" class="hide-m">'+t('shortcuts')+'</button>'+
    '<a href="privacy.html">'+t('privacy')+'</a>'+
    '<a href="privacy.html#remove">'+t('removeListing')+'</a></div>';
}
function bindMenu(body){
  body.querySelectorAll('[data-m]').forEach(b=>b.onclick=()=>{
    const m=b.dataset.m;
    if(m==='cur'){CUR=CUR==='thb'?'usd':'thb';LS.set('sabaistay_cur',CUR);render(false);refreshSheet(true);}
    else if(m==='theme'){const order=['auto','light','dark'];setTheme(order[(order.indexOf(themeMode())+1)%3]);refreshSheet(true);}
    else if(m==='lang'){setLang(LANG==='ru'?'en':'ru');refreshSheet(true);}
    else if(m==='stats')openSheet('stats');
    else if(m==='csv'){exportCSV();closeSheet();}
    else if(m==='keys')openSheet('keys');
  });
}
function setTheme(mode){
  if(mode==='auto'){try{localStorage.removeItem('sabaistay_theme')}catch(e){}}else LS.set('sabaistay_theme',mode);
  const dark=mode==='dark'||(mode==='auto'&&matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.setAttribute('data-theme',dark?'dark':'light');
  setBasemapTheme();
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(themeMode()==='auto')setTheme('auto');});
function setLang(l){LANG=l;LS.set('sabaistay_lang',l);applyStatic();render(false);}
$('lang-btn').onclick=()=>setLang(LANG==='ru'?'en':'ru');
$('menu-btn').onclick=e=>{if(SHEET.kind==='menu'){closeSheet();return;}openSheet('menu',isMobile()?null:e.currentTarget);};
$('info-btn').onclick=e=>{if(SHEET.kind==='info'){closeSheet();return;}openSheet('info',isMobile()?null:e.currentTarget);};

// ── статистика районов ──
function statsBody(){
  const by={};
  DATA.forEach(d=>{const k=d.district||t('noDistrictRow');const v=by[k]=by[k]||{count:0,prices:[],active:0};v.count++;if(!d.fresh.stale)v.active++;
    if(d.price!=null&&(d.period==='month'||d.period==='unknown'))v.prices.push(d.price);});
  const median=a=>{if(!a.length)return null;const s=[...a].sort((x,y)=>x-y);const m=Math.floor(s.length/2);return s.length%2?s[m]:Math.round((s[m-1]+s[m])/2);};
  return '<table class="stats-table"><thead><tr><th>'+t('stDistrict')+'</th><th class="n">'+t('stCount')+'</th><th class="n">'+t('stActive')+'</th><th class="n">'+t('stMedian')+'</th></tr></thead><tbody>'+
    Object.entries(by).sort((a,b)=>b[1].count-a[1].count).map(([k,v])=>{const m=median(v.prices);
      return '<tr><td>'+esc(k)+'</td><td class="n">'+v.count+'</td><td class="n">'+v.active+'</td><td class="st-price">'+(m?Math.round(m).toLocaleString('ru-RU')+' ฿':'—')+'</td></tr>';}).join('')+'</tbody></table>';
}

// ── CSV ──
function exportCSV(){
  const f=readF();
  const view=DATA.filter(d=>passes(d,f));
  const hdr=['Заголовок','Район','Цена, ฿','Период','Спален','Тип','Площадь, м²','Доступно с','Свежесть','Ссылка'];
  const q=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const csv=[hdr,...view.map(d=>[d.title,d.district||'',d.price||'',d.period||'',d.bedrooms??'',d.type||'',d.area_sqm||'',d.available_from||'',d.fresh.label,firstUrl(d)])]
    .map(r=>r.map(q).join(',')).join('\r\n');
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'}));
  a.download='sabaistay_'+new Date().toISOString().slice(0,10)+'.csv';
  document.body.appendChild(a);a.click();a.remove();
}
$('foot-csv').onclick=exportCSV;
$('foot-stats').onclick=()=>openSheet('stats');

// ── избранное ──
function toggleFav(id){
  const a=getFavs();const i=a.indexOf(id);if(i>=0)a.splice(i,1);else a.push(id);saveFavs(a);
  document.querySelectorAll('.bm[data-id="'+CSS.escape(id)+'"]').forEach(b=>{const on=a.includes(id);b.classList.toggle('saved',on);b.setAttribute('aria-pressed',String(on));});
  updateFavCount();
  if(SHEET.kind==='favs')refreshSheet(true);
}
function updateFavCount(){const n=getFavs().length;$('fav-btn').hidden=!n;$('fav-num').textContent=n;}
function favsBody(){
  const favs=getFavs();const all=DATA.filter(d=>favs.includes(d.id));
  if(!all.length)return '<p style="color:var(--muted)">'+t('noFavs')+'</p>';
  return '<div id="fav-cards" style="display:flex;flex-direction:column;gap:12px;padding-top:8px"></div><p><button type="button" class="lbtn" id="fav-clear">'+t('clearFavs')+'</button></p>';
}
function bindFavs(body){
  const c=body.querySelector('#fav-cards');if(c){const favs=getFavs();DATA.filter(d=>favs.includes(d.id)).forEach(d=>c.appendChild(makeCard(d,null)));}
  const b=body.querySelector('#fav-clear');if(b)b.onclick=()=>{saveFavs([]);updateFavCount();document.querySelectorAll('.bm.saved').forEach(x=>x.classList.remove('saved'));closeSheet();};
}
$('fav-btn').onclick=()=>openSheet('favs');

// ── URL: состояние фильтра живёт в хэше (ключи прежние — старые ссылки работают) ──
function writeHash(f){
  const p=new URLSearchParams();
  if(f.district)p.set('district',f.district);
  if(f.maxAge)p.set('age',f.maxAge);
  if(f.period)p.set('period',f.period);
  if(f.sqmMin)p.set('sqmMin',f.sqmMin);
  if(f.sqmMax)p.set('sqmMax',f.sqmMax);
  if(f.priceMin)p.set('priceMin',f.priceMin);
  if(f.price)p.set('price',f.price);
  if(f.bed)p.set('bed',f.bed);
  if(f.type)p.set('type',f.type);
  if(f.amenities.length)p.set('am',f.amenities.join(','));
  // скрытие устаревших пишем, только если оно отличается от умолчания: умолчание само
  // зависит от того, есть ли свежие данные, и не должно «залипать» в ссылке
  if(f.hideStale!==DEFAULT_HIDE_STALE)p.set('stale',f.hideStale?'1':'0');
  if(f.onlyAvail)p.set('avail','1');
  if(f.hideSeen)p.set('seen','1');
  if(f.search)p.set('q',f.search);
  if(f.sort)p.set('sort',f.sort);
  const s=p.toString();
  history.replaceState(null,'',s?('#'+s):location.pathname+location.search);
}
function applyHash(){
  if(!location.hash)return;
  const p=new URLSearchParams(location.hash.slice(1));
  const g=k=>p.get(k);
  if(g('district'))S.district=g('district');
  if(g('period'))S.period=g('period');
  S.maxAge=+g('age')||0;S.sqmMin=+g('sqmMin')||0;S.sqmMax=+g('sqmMax')||0;S.priceMin=+g('priceMin')||0;S.price=+g('price')||0;S.bed=+g('bed')||0;
  if(g('type'))S.type=g('type');
  if(g('am'))S.amenities=g('am').split(',').filter(a=>AM_KEYS.includes(a));
  if(g('stale')==='0')S.hideStale=false; else if(g('stale')==='1')S.hideStale=FRESH_TOTAL>0;
  S.onlyAvail=g('avail')==='1';S.hideSeen=g('seen')==='1';
  if(g('q')){S.search=g('q');$('f-search').value=S.search;}
  if(g('sort'))S.sort=g('sort');
}
// Сброс возвращает к умолчанию — а умолчание при старом снимке уже «показывать последние
// известные», поэтому сброс больше не прячет всё обратно в ноль.
function resetFilters(){S=JSON.parse(JSON.stringify(DEF));$('f-search').value='';applyFilter();}

// ── «следить за поиском»: фильтр уезжает в бота ссылкой ──────────────
// Полезная нагрузка Telegram — 64 символа из [A-Za-z0-9_-], поэтому поля склеиваем
// через «~» и упаковываем в base64url.
function watchPayload(f){
  const parts=[f.district||'',f.price||'',f.bed||'',f.type||'',(f.amenities||[]).join('.'),f.period||'month'];
  const b=btoa(String.fromCharCode(...new TextEncoder().encode(parts.join('~'))));
  return b.replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
}
// Текстовый поиск в подписку не переносится (бот сопоставляет поля, а не свободный текст).
function hasWatchableFilter(f){return !!(f.district||f.price||f.bed||f.type||(f.amenities||[]).length);}
function watchHref(f){
  const payload=watchPayload(f);
  const ok=hasWatchableFilter(f)&&payload.length<=63;
  return 'https://t.me/'+BOT_USERNAME+(ok?'?start=f'+payload:'');
}
function updateWatchLink(f,shown){
  if(!BOT_USERNAME)return;
  document.querySelectorAll('.js-watch').forEach(el=>{el.href=watchHref(f);el.hidden=false;});
  const lbl=$('watch-btn').querySelector('span');
  lbl.textContent=hasWatchableFilter(f)?(shown?t('watchSearch'):t('notifyMe')):t('watchNew');
}

// ── поиск в шапке ──
let _st;
$('f-search').addEventListener('input',e=>{clearTimeout(_st);_st=setTimeout(()=>setF({search:e.target.value.trim().toLowerCase()}),120);});
$('f-search').addEventListener('keydown',function(e){if(e.key==='Escape'){this.value='';this.blur();setF({search:''});}});
$('f-sort').addEventListener('change',e=>setF({sort:e.target.value}));
$('load-more-btn').onclick=renderPage;

// ── поделиться поиском ──
$('share-btn').onclick=function(){
  const btn=this,lbl=btn.querySelector('.lbl');
  const done=()=>{btn.classList.add('ok');lbl.textContent=t('copied');setTimeout(()=>{btn.classList.remove('ok');lbl.textContent=t('share');},2000);};
  if(navigator.share&&isMobile()){navigator.share({title:'SabaiStay',url:location.href}).catch(()=>{});return;}
  if(navigator.clipboard)navigator.clipboard.writeText(location.href).then(done,()=>{});
};

// ── мобила: список ↔ карта плавающей пилюлей ──
function setMode(m){
  const wrap=$('wrap'),btn=$('mode-btn');
  wrap.classList.toggle('mode-map',m==='map');
  document.body.classList.toggle('mode-map',m==='map');
  btn.innerHTML=m==='map'?IC.list+'<span>'+t('list')+'</span>':IC.map+'<span>'+t('map')+'</span>';
  btn.dataset.mode=m;
  window.scrollTo({top:0});
  if(m==='map'&&map){
    // Сначала размер, потом подложка: созданная в скрытом контейнере, она рисуется 400×300.
    map.invalidateSize();
    const b=clusterGroup.getLayers().length?clusterGroup.getBounds():null;
    if(b&&b.isValid())map.fitBounds(b,{maxZoom:14,animate:false,padding:[24,24]});
    ensureBasemap();
  }
}
$('mode-btn').onclick=()=>setMode($('mode-btn').dataset.mode==='map'?'list':'map');

// ── клавиатура ──
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&SHEET.kind){closeSheet();return;}
  const tag=document.activeElement.tagName;
  const noInput=tag!=='INPUT'&&tag!=='TEXTAREA'&&tag!=='SELECT';
  if(e.key==='/'&&noInput){e.preventDefault();$('f-search').focus();$('f-search').select();}
  if(e.key==='r'&&noInput&&!e.metaKey&&!e.ctrlKey){e.preventDefault();resetFilters();}
});
addEventListener('resize',()=>{if(SHEET.kind)placeSheet();});

// ── наверх ──
(function(){
  const btn=$('scroll-top'),list=$('list');
  const on=()=>btn.classList.toggle('vis',(isMobile()?window.scrollY:list.scrollTop)>600);
  list.addEventListener('scroll',on,{passive:true});addEventListener('scroll',on,{passive:true});
  btn.onclick=()=>{list.scrollTo({top:0,behavior:'smooth'});window.scrollTo({top:0,behavior:'smooth'});};
})();

// ссылка с другим фильтром, вставленная в ту же вкладку, меняет только хэш — перечитываем
addEventListener('hashchange',()=>{S=JSON.parse(JSON.stringify(DEF));$('f-search').value='';applyHash();closeSheet(true);applyFilter();});
// ── старт ──
applyStatic();
applyHash();
render(true);
updateFavCount();
setMode('list');
// На десктопе карта видна сразу — подложку грузим, когда браузер освободится после списка.
if(!isMobile())(window.requestIdleCallback||setTimeout)(()=>ensureBasemap(),{timeout:1500});
</script>
</body>
</html>"""


def _ru_date(iso: str | None) -> str:
    if not iso:
        return "—"
    months = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {months[int(m)]} {y}"


def _write_privacy(snap_iso: str) -> None:
    """web/privacy.src.html → web/privacy.html с подстановкой контактов.

    Контакт задаётся один раз (SABAI_CONTACT_TG) и попадает и на витрину, и сюда —
    иначе страница удаления живёт своей жизнью и легко остаётся с фиктивным адресом.
    """
    src = OUT.parent / "privacy.src.html"
    if not src.exists():
        return

    parts = []
    if CONTACT_TG:
        parts.append(f'Telegram: <a href="https://t.me/{CONTACT_TG}">@{CONTACT_TG}</a>')
    if CONTACT_EMAIL:
        parts.append(f'✉️ <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>')

    if parts:
        block = " &nbsp;·&nbsp; ".join(parts)
        todo = ""
        inline = " или ".join(p.split(": ", 1)[-1] for p in parts) + "."
    else:
        block = "контакт появится здесь после запуска бота"
        todo = ('<p class="todo">↑ Задать SABAI_CONTACT_TG (и при желании SABAI_CONTACT_EMAIL) '
                'и пересобрать — контакт подставится и сюда, и на витрину.</p>')
        inline = "см. блок выше."

    html = (src.read_text(encoding="utf-8")
            .replace("__CONTACT_BLOCK__", block)
            .replace("__CONTACT_TODO__", todo)
            .replace("__CONTACT_INLINE__", inline)
            .replace("__UPDATED__", _ru_date(snap_iso) if snap_iso else "")
            .replace("__SITE__", SITE_URL))
    (OUT.parent / "privacy.html").write_text(html, encoding="utf-8")


def _write_seo_files(snap_iso: str) -> None:
    """robots.txt + sitemap.xml рядом с index.html.

    Витрина — одна страница (состояние поиска живёт в URL-хэше, для поисковика
    это тот же документ), поэтому в карте сайта только «/» и «/privacy».
    lastmod берём от даты снимка данных — она и есть дата обновления содержимого.
    """
    (OUT.parent / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n" f"Sitemap: {SITE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )
    lastmod = f"\n    <lastmod>{snap_iso}</lastmod>" if snap_iso else ""
    (OUT.parent / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url>\n    <loc>{SITE_URL}/</loc>{lastmod}\n"
        "    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n"
        f"  <url>\n    <loc>{SITE_URL}/privacy</loc>\n"
        "    <changefreq>yearly</changefreq>\n    <priority>0.3</priority>\n  </url>\n"
        "</urlset>\n",
        encoding="utf-8",
    )


def main() -> None:
    rows, snapshot = build_rows()
    snap_iso = snapshot[:10] if snapshot else ""
    geo = sum(1 for r in rows if r["lat"] is not None)
    stale = sum(1 for r in rows if r["fresh"]["stale"])
    # Свежесть в браузере пересчитывается от реального времени (см. assessFresh), поэтому
    # запечённые метки в данные не кладём: лишние ~35 КБ и риск, что кто-то станет им верить.
    for r in rows:
        r.pop("fresh", None)
    owner_cta = (
        '<div class="owner-cta"><b data-i18n="ownerTitle">Сдаёшь жильё на Пхангане?</b>'
        '<p data-i18n="ownerText">Добавим объявление на витрину бесплатно — его увидят те, кто уже ищет '
        "жильё с фильтром по району и цене.</p>"
        f'<a class="btn pri" href="https://t.me/{CONTACT_TG}" target="_blank" rel="noopener">'
        '<span data-i18n="ownerBtn">Добавить объявление</span></a></div>'
        if CONTACT_TG
        else ""
    )
    # Кнопки «следить за поиском» рисует JS (шапка фильтров, мобильная пилюля, пустое
    # состояние) и только когда BOT_USERNAME задан — мёртвая ссылка хуже её отсутствия.
    html = (HTML.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
                .replace("__OWNER_CTA__", owner_cta)
                .replace("__BOT_USERNAME__", BOT_USERNAME)
                .replace("__SNAPSHOT__", _ru_date(snapshot))
                .replace("__SNAPSHOT_ISO__", snap_iso)
                .replace("__FAVICON__", quote(FAVICON_SVG))
                .replace("__ISLAND_PATH__", ISLAND_PATH)
                .replace("__HOT_DAYS__", str(freshness.HOT_DAYS))
                .replace("__FRESH_DAYS__", str(freshness.FRESH_DAYS))
                .replace("__RECENT_DAYS__", str(freshness.RECENT_DAYS))
                .replace("__EXPIRE_DAYS__", str(freshness.EXPIRE_DAYS))
                .replace("__SITE__", SITE_URL))
    OUT.write_text(html, encoding="utf-8")
    _write_seo_files(snap_iso)
    _write_privacy(snap_iso)
    print(f"Собрано web/index.html: {len(rows)} объектов ({geo} на карте, {stale} неактуальных). "
          f"Снимок: {_ru_date(snapshot)}. Стиль: морская карта Пхангана.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
SabaiStay — сборка витрины (demo). Визуальный стиль: «морская карта Пхангана».

Читает data/sample_listings.json + data/districts.json и генерирует web/index.html:
сплит-вид «карточки + карта Leaflet» с рабочим фильтром (район/цена/спальни/тип/бассейн).
Данные ВШИТЫ в html (открывается из file:// без сервера). Без npm, без сети на сборку
(Leaflet/тайлы/шрифты тянутся с CDN при открытии). Пересобрать:  python3 build.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

# Боевой домен для абсолютных ссылок в OG-мете (превью в Telegram/Facebook). Пусто до деплоя —
# тогда превью-картинка не подтянется при шеринге (мета валидна, но нужен абсолютный URL + PNG).
# На деплое: задай домен и экспортируй og.svg → og.png (FB/TG не рендерят SVG в og:image).
SITE_URL = "https://sabaistay.co"

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
from freshness import assess         # noqa: E402  — возраст/статус (active/rented/expired)


def build_rows() -> tuple[list[dict], str | None]:
    data = json.loads(LISTINGS.read_text(encoding="utf-8"))
    coords = {d["canonical"]: (d["lat"], d["lng"]) for d in json.loads(DISTRICTS.read_text(encoding="utf-8"))["districts"]}
    bench = MarketBenchmark()
    listings = [r for r in data["listings"] if r["parsed"]["is_listing"]]
    # «снимок»: для статичного демо свежесть считаем от даты последнего поста (см. freshness.py).
    snapshot = max((r.get("posted_at") for r in listings if r.get("posted_at")), default=None)
    groups = dedup(listings)           # один объект = одна карточка, источников может быть несколько
    rows, seen = [], {}
    for g in groups:
        rep = g["representative"]
        p = rep["parsed"]
        canon = p["district_canonical"]
        lat = lng = None
        if canon and canon in coords:
            lat, lng = coords[canon]
            n = seen.get(canon, 0); seen[canon] = n + 1
            lat += ((n % 5) - 2) * 0.0045
            lng += (((n // 5) % 5) - 2) * 0.0045
        fresh = assess(rep.get("posted_at"), p.get("status_hint"), snapshot)
        rows.append({
            "sources": g["sources"], "dup": g["size"],
            "title": p.get("title") or (canon or "Объявление"),
            "district": canon, "price": p["price_amount"], "period": p["price_period"],
            "bedrooms": p["bedrooms"], "type": p["property_type"],
            "amenities": p["amenities"], "confidence": p["confidence"],
            "posted_at": rep.get("posted_at"), "fresh": fresh,
            "lat": round(lat, 5) if lat else None, "lng": round(lng, 5) if lng else None,
            "bench": bench.compare(canon, p["price_amount"], p["price_period"], p["bedrooms"]),
            "contact": p.get("contact") or {},
            "text": (rep.get("raw_text") or "").strip(),
            "available_from": p.get("available_from"),
            "area_sqm": p.get("area_sqm"),
            "id": rep.get("external_id") or g["sources"][0]["source_url"],
        })
    # свежие — выше: по умолчанию сортируем по возрасту (None-возраст в конец)
    rows.sort(key=lambda r: (r["fresh"]["age_days"] is None, r["fresh"]["age_days"] or 0))
    return rows, snapshot


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SabaiStay — аренда жилья на Ко Пхангане с фильтром и картой</title>
<meta name="description" content="Все объявления об аренде на острове Ко Пханган из Telegram-каналов в одном месте — с нормальным фильтром по району, цене, спальням и удобствам, картой и сравнением с рынком.">
<meta property="og:type" content="website">
<meta property="og:title" content="SabaiStay — аренда жилья на Ко Пхангане с фильтром">
<meta property="og:description" content="Объявления Пхангана из Telegram-каналов — с фильтром, картой и ценой к рынку. Поделись ссылкой на свой поиск.">
<meta property="og:image" content="__SITE__/og.png">
<meta property="og:url" content="__SITE__/">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0d3b3a">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="icon" href="data:image/svg+xml,__FAVICON__">
<link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@500;600;700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root{
    --sea-deep:#0a3742; --sea:#0d5563; --paper:#eaeee6; --surface:#f7f9f3;
    --land:#e4dcc4; --coral:#ef6c45; --coral-d:#d8552f; --ink:#0e2329;
    --muted:#5d7077; --hair:#cbd4cc;
    --sand:#e8d9b8;
    --disp:"Baloo 2",system-ui,sans-serif;
    --mono:"Space Mono",ui-monospace,monospace;
    --body:system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{font-family:var(--body);color:var(--ink);background:var(--paper)}

  /* ── шапка: панель глубокого залива ── */
  .top{background:linear-gradient(150deg,#062b34 0%,var(--sea-deep) 55%,#0d4754 100%);color:#eef4f2;padding:13px 22px 12px;border-bottom:3px solid var(--coral)}
  .top .brand{display:flex;align-items:center;gap:7px 13px;flex-wrap:wrap}
  .brandmark{width:30px;height:30px;flex:0 0 auto;filter:drop-shadow(0 1px 2px rgba(0,0,0,.25))}
  .mark{font-family:var(--disp);font-weight:800;font-size:25px;letter-spacing:-.2px;color:#fff;line-height:1}
  .mark .m2{color:var(--coral)}
  .tagline{font-size:13px;color:#9fc0bd;max-width:none}
  .coordbar{margin-left:auto;font-family:var(--mono);font-size:11px;color:#7fa6a4;letter-spacing:.5px;display:flex;gap:14px;align-items:center}
  .compass{font-size:13px;color:var(--coral)}
  .snap{color:#cfe0dd}

  /* ── фильтр: приборная панель карты ── */
  .filters{display:flex;flex-wrap:wrap;gap:16px 18px;padding:12px 22px;background:var(--surface);border-bottom:1px solid var(--hair);align-items:flex-end}
  .filters .f{display:flex;flex-direction:column;gap:4px}
  .filters label{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)}
  .filters select,.filters input{font-family:var(--body);font-size:14px;padding:7px 9px;border:1px solid var(--hair);border-radius:9px;background:#fff;color:var(--ink);min-width:130px}
  .filters select:focus,.filters input:focus{outline:2px solid var(--sea);outline-offset:1px;border-color:var(--sea)}
  .chk{flex-direction:row!important;align-items:center;gap:7px;font-family:var(--body);font-size:14px;color:var(--ink);padding-bottom:7px}
  .chk input{width:17px;height:17px;accent-color:var(--coral)}
  .reset{font-family:var(--mono);font-size:11px;color:var(--sea);background:none;border:none;cursor:pointer;padding-bottom:9px;text-transform:uppercase;letter-spacing:.6px}
  .reset:hover{color:var(--coral)}

  /* ── полотно ── */
  .wrap{display:flex;height:calc(100vh - 132px)}
  .list{width:44%;min-width:330px;overflow-y:auto;padding:14px 14px 40px;background:var(--paper)}
  #map{flex:1;height:100%;background:var(--land)}
  .count{font-family:var(--mono);font-size:11px;color:var(--muted);padding:2px 4px 11px;letter-spacing:.4px;display:flex;flex-wrap:wrap;gap:7px;align-items:center}
  .rcount{font-weight:700;color:var(--sea)}
  .chip{font-family:var(--mono);font-size:11px;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:20px;padding:3px 9px;cursor:pointer;letter-spacing:.2px}
  .chip:hover{border-color:var(--coral);color:var(--coral)}
  .chip .x{color:var(--muted);margin-left:3px}
  .listfoot{font-family:var(--mono);font-size:11px;color:var(--muted);padding:16px 6px 24px;border-top:1px solid var(--line);margin-top:12px;line-height:1.9}
  .listfoot a{color:var(--sea);text-decoration:none}
  .listfoot a:hover{color:var(--coral);text-decoration:underline}
  a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--coral);outline-offset:2px}

  /* ── карточка-«отметка на карте» ── */
  .card{background:var(--surface);border:1px solid var(--hair);border-radius:13px;padding:13px 15px;margin-bottom:11px;cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s}
  .card:hover,.card.active{border-color:var(--coral);box-shadow:0 4px 16px rgba(14,35,41,.09)}
  .card.active{transform:translateX(2px)}
  .card-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;gap:6px;min-width:0}
  .dtag{font-family:var(--mono);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--sea);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .dtag.nogeo{color:var(--muted)}
  .title{font-family:var(--disp);font-weight:700;font-size:18px;line-height:1.2;margin:4px 0 7px}
  .specs{font-size:13px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .spec-amen{font-size:14px;letter-spacing:2px}
  .card[data-type="villa"]{border-left:4px solid #ef6c45}
  .card[data-type="house"]{border-left:4px solid #3d8b7a}
  .card[data-type="bungalow"]{border-left:4px solid #c4a35a}
  .card[data-type="apartment"]{border-left:4px solid #5b8cb8}
  .card[data-type="studio"]{border-left:4px solid #9b72b0}
  .card[data-type="room"]{border-left:4px solid #78a87c}
  .foot{display:flex;justify-content:space-between;align-items:baseline;margin-top:11px;gap:10px}
  .price{font-family:var(--mono);font-weight:700;font-size:18px;color:var(--coral-d)}
  .price small{font-weight:400;font-size:11px;color:var(--muted)}
  .price.none{color:var(--muted);font-weight:400;font-size:13px}
  .priceblock{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  .bench{font-family:var(--mono);font-size:11px;font-weight:700;border-radius:5px;padding:1px 6px;white-space:nowrap}
  .bench-cheap{background:#e0efe4;color:#2f7d46}
  .bench-pricey{background:#fbe4dc;color:var(--coral-d)}
  .bench-market{background:#eceee7;color:var(--muted)}
  .ref{margin-top:7px;font-family:var(--mono);font-size:11px;color:var(--muted)}
  .ref .seed{opacity:.65;font-style:italic}
  .srcs{display:flex;align-items:center;gap:9px;flex-wrap:wrap;justify-content:flex-end}
  .src{font-family:var(--mono);font-size:11px;color:var(--sea);text-decoration:none;white-space:nowrap}
  .src:hover{color:var(--coral);text-decoration:underline}
  .multi{font-family:var(--mono);font-size:10px;font-weight:700;color:#fff;background:var(--sea);border-radius:5px;padding:1px 6px;letter-spacing:.3px;white-space:nowrap}
  .draft{font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.5px;color:#fff;background:var(--muted);border-radius:4px;padding:1px 5px;vertical-align:middle}
  .fresh-badge{font-family:var(--mono);font-size:10px;font-weight:700;border-radius:5px;padding:1px 6px;letter-spacing:.2px;white-space:nowrap;margin-left:auto}
  .fb-fresh{background:#dcefe1;color:#2f7d46}
  .fb-recent{background:#e9f0e2;color:#5d7a48}
  .fb-old{background:#f1ecdd;color:#8a763a}
  .fb-stale{background:#efe3df;color:#9a6450}
  .fb-unknown{background:#eceee7;color:var(--muted)}
  .card.stale{opacity:.58}
  .card.stale:hover,.card.stale.active{opacity:1}
  .sounding.dim{background:var(--muted);box-shadow:0 1px 3px rgba(0,0,0,.25)}
  .contact-btn{font-family:var(--mono);font-size:11px;color:#fff!important;background:var(--sea);border-radius:6px;padding:3px 9px;text-decoration:none!important;white-space:nowrap;letter-spacing:.2px}
  .contact-btn:hover{background:var(--coral)!important}
  .nogeo-divider{font-family:var(--mono);font-size:11px;color:var(--muted);padding:10px 6px 6px;border-top:2px dashed var(--hair);margin-top:10px;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
  .nogeo-divider:hover{color:var(--sea)}
  .nogeo-divider .nd-arrow{font-size:10px;transition:transform .2s;margin-left:auto}
  .nogeo-open .nd-arrow{transform:rotate(180deg)}
  .nogeo-cards{display:none}
  .nogeo-cards.open{display:block}
  .empty{padding:42px 22px;text-align:center;color:var(--muted)}
  .empty .big{font-size:30px;display:block;margin-bottom:8px}
  .empty b{display:block;font-family:var(--disp);font-size:16px;color:var(--ink);margin-bottom:4px}
  .empty .clear{margin-top:14px;font-family:var(--mono);font-size:12px;color:#fff;background:var(--coral);border:none;border-radius:8px;padding:8px 16px;cursor:pointer}

  /* ── пины-«глубины» на карте ── */
  .sounding{display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:700;font-size:11px;color:#fff;background:var(--coral);border:2px solid #fff;border-radius:11px;height:22px;padding:0 7px;box-shadow:0 1px 4px rgba(0,0,0,.3);white-space:nowrap;transition:transform .12s}
  .sounding.hot{transform:scale(1.22);background:var(--coral-d);z-index:1000}

  /* анимация появления только при первой отрисовке */
  .anim .card{animation:rise .4s ease both;animation-delay:calc(var(--i)*35ms)}
  @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

  /* ── toggle для мобильного фильтра ── */
  .filter-toggle{display:none;align-items:center;gap:6px;width:100%;padding:10px 18px;background:var(--surface);border:none;border-bottom:1px solid var(--hair);font-family:var(--mono);font-size:12px;color:var(--sea);cursor:pointer;letter-spacing:.4px;text-align:left}
  .filter-toggle:hover{color:var(--coral)}
  .ftbadge{font-weight:700;color:var(--coral)}
  .ftarrow{margin-left:auto;display:inline-block;transition:transform .2s;font-size:10px}
  .filter-toggle[aria-expanded="true"] .ftarrow{transform:rotate(180deg)}

  @media (max-width:760px){
    .wrap{flex-direction:column;height:auto}
    .list{width:100%;min-width:0;order:2;max-height:none}
    #map{order:1;height:44vh;width:100%}
    .brand{flex-wrap:wrap;gap:6px 12px}
    .tagline{font-size:12px;flex-basis:100%;order:3}
    .coordbar{margin-left:auto}
    .geo,.compass{display:none}
    .filter-toggle{display:flex}
    .filters{display:none;flex-wrap:wrap;gap:10px 12px;padding:12px 16px 14px;overflow-x:unset}
    .filters.open{display:flex}
    .filters .f{flex:0 0 calc(50% - 6px)}
    .filters select,.filters input{width:100%;min-width:unset;box-sizing:border-box}
    .filters .chk{flex:0 0 auto}
    .reset{flex:0 0 auto}
  }
  /* ── мобильный переключатель карта/список ── */
.view-toggle{display:none;gap:0;background:var(--surface);border-bottom:1px solid var(--hair);padding:8px 14px}
.vt-btn{flex:1;font-family:var(--mono);font-size:12px;padding:8px 0;background:#fff;border:1px solid var(--hair);color:var(--sea);cursor:pointer;letter-spacing:.3px}
.vt-btn:first-child{border-radius:8px 0 0 8px}
.vt-btn:last-child{border-radius:0 8px 8px 0;border-left:none}
.vt-btn.active{background:var(--sea);color:#fff;border-color:var(--sea)}
@media(max-width:760px){.view-toggle{display:flex}}
@media(max-width:760px){.wrap.mobile-list #map{display:none}.wrap.mobile-map .list{display:none}.wrap.mobile-map #map{height:80vh!important}}

/* ── дата доступности ── */
.avail-now{font-family:var(--mono);font-size:11px;color:#2d9e5f;font-weight:700;letter-spacing:.2px}
.avail-date{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.2px}

/* ── строка поиска (широкая) ── */
.f-search-wrap{flex:0 0 100%!important;max-width:100%}
.f-search-wrap input{width:100%;min-width:0;box-sizing:border-box}

/* ── закладки ── */
.bm-btn{position:absolute;top:10px;right:12px;background:none;border:none;font-size:16px;cursor:pointer;line-height:1;padding:2px;opacity:.5;transition:opacity .15s,transform .15s}
.bm-btn:hover{opacity:1;transform:scale(1.2)}
.bm-btn.saved{opacity:1;color:var(--coral)}
.card{position:relative}
.fav-section{background:#fff8f6;border-bottom:2px solid var(--coral);padding:10px 22px 14px}
.fav-section h3{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--coral-d);margin:0 0 8px}
.fav-section .fav-cards{display:flex;flex-direction:column}
.fav-clear{font-family:var(--mono);font-size:11px;color:var(--muted);background:none;border:none;cursor:pointer;padding:0;margin-top:6px}
.fav-clear:hover{color:var(--coral)}

/* ── кнопка поделиться ── */
.share-btn{font-family:var(--mono);font-size:11px;color:var(--sea);background:none;border:1px solid var(--hair);border-radius:6px;padding:4px 10px;cursor:pointer;letter-spacing:.3px;white-space:nowrap}
.share-btn:hover{border-color:var(--sea);background:var(--surface)}
.share-btn.copied{color:#2d9e5f;border-color:#2d9e5f}

/* ── валюта ── */
.cur-btn{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--muted);background:none;border:1px solid var(--hair);border-radius:6px;padding:4px 10px;cursor:pointer;white-space:nowrap}
.cur-btn:hover{border-color:var(--sea);color:var(--sea)}
.cur-btn.usd{color:#2d9e5f;border-color:#2d9e5f}

/* ── кнопки шапки ── */
.header-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:4px}

/* ── «уже смотрел» ── */
.seen-badge{font-family:var(--mono);font-size:9px;letter-spacing:.3px;color:var(--muted);border:1px solid var(--hair);border-radius:4px;padding:1px 5px;margin-left:4px;vertical-align:middle}

/* ── кластеры маркеров ── */
.cluster-icon{width:34px;height:34px;background:var(--coral);color:#fff;font-family:var(--mono);font-size:12px;font-weight:700;border-radius:50%;display:flex;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3)}

/* ── модальное окно ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,30,30,.55);z-index:9000;display:flex;align-items:center;justify-content:center;padding:20px}
.modal-box{background:#fff;border-radius:14px;max-width:680px;width:100%;max-height:80vh;overflow-y:auto;padding:28px 28px 22px;position:relative;box-shadow:0 8px 40px rgba(0,0,0,.25)}
.modal-close{position:absolute;top:14px;right:16px;background:none;border:none;font-size:20px;cursor:pointer;color:var(--muted);line-height:1}
.modal-close:hover{color:var(--text)}
.modal-box h2{font-family:var(--display);font-size:20px;margin:0 0 16px;color:var(--sea-d)}
.stats-table{width:100%;border-collapse:collapse;font-size:13px}
.stats-table th{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);padding:6px 10px;border-bottom:2px solid var(--hair);text-align:left}
.stats-table td{padding:7px 10px;border-bottom:1px solid var(--hair)}
.stats-table tr:hover td{background:var(--surface)}
.stats-table .st-district{font-weight:700;color:var(--sea-d)}
.stats-table .st-price{font-family:var(--mono);font-weight:700;color:var(--coral-d)}

/* ── CSV кнопка ── */
.export-btn{font-family:var(--mono);font-size:11px;color:var(--muted);background:none;border:1px solid var(--hair);border-radius:6px;padding:4px 10px;cursor:pointer;white-space:nowrap}
.export-btn:hover{border-color:var(--sea);color:var(--sea)}

/* ── дней до заезда ── */
.avail-soon{color:#e07b00;font-weight:700}
.avail-ready{color:#2d9e5f;font-weight:700}

/* ── пагинация ── */
.load-more-wrap{text-align:center;padding:18px 0 10px}
.load-more-btn{font-family:var(--mono);font-size:12px;color:var(--sea);background:none;border:2px solid var(--sea);border-radius:8px;padding:8px 28px;cursor:pointer;letter-spacing:.3px}
.load-more-btn:hover{background:var(--sea);color:#fff}

/* ── подсветка поиска ── */
.hl{background:#fff3b0;border-radius:2px;padding:0 1px}

/* ── горячие предложения ── */
.hot-section{background:linear-gradient(135deg,#fff8f0 0%,#fff2e8 100%);border-bottom:2px solid var(--coral);padding:12px 22px 16px}
.hot-section h3{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--coral-d);margin:0 0 10px}
.hot-cards{display:flex;gap:10px;flex-wrap:wrap}
.hot-card{background:#fff;border:1px solid var(--hair);border-radius:10px;padding:10px 14px;flex:1 1 180px;cursor:pointer;transition:box-shadow .15s;min-width:140px}
.hot-card:hover{box-shadow:0 3px 12px rgba(0,0,0,.1)}
.hot-card .hc-title{font-size:12px;font-weight:700;color:var(--sea-d);margin-bottom:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hot-card .hc-price{font-family:var(--mono);font-size:14px;font-weight:700;color:var(--coral-d)}
.hot-card .hc-meta{font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:2px}

/* ── качество объявления ── */
.quality{font-size:11px;letter-spacing:-.5px;margin-left:4px;opacity:.8}

/* ── expand карточки ── */
.card-raw{margin-top:8px;border-top:1px solid var(--hair);padding-top:2px}
.card-raw-toggle{font-family:var(--mono);font-size:11px;color:var(--sea);cursor:pointer;padding:6px 0 0;list-style:none;display:block}
.card-raw-toggle::-webkit-details-marker{display:none}
.card-raw-toggle::before{content:'▸ '}
details[open] .card-raw-toggle::before{content:'▾ '}
.card-raw-toggle:hover{color:var(--coral)}
.card-raw-text{font-family:var(--mono);font-size:11px;color:var(--muted);white-space:pre-wrap;word-break:break-word;padding:6px 0 0;margin:0;max-height:260px;overflow-y:auto;line-height:1.5}

@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

/* ── тёмная тема ── */
html[data-theme="dark"] {
  --paper:#0e1c20; --surface:#152027; --ink:#d4e8e4; --hair:#263c44;
  --muted:#7aa0a8; --land:#1e3038; --sand:#1c2015;
  --sea:#2a9bb0; --sea-deep:#061418; --coral:#f07c55; --coral-d:#e05e38;
}
html[data-theme="dark"] .top{background:linear-gradient(150deg,#020c0f 0%,#061418 55%,#0a1f28 100%)}
html[data-theme="dark"] .filters,.filters.open{background:var(--surface)}
html[data-theme="dark"] .filters select,html[data-theme="dark"] .filters input{background:#1a2c34;border-color:var(--hair);color:var(--ink)}
html[data-theme="dark"] .card{background:var(--surface)}
html[data-theme="dark"] .modal-box{background:#152027;color:var(--ink)}
html[data-theme="dark"] .modal-box h2{color:var(--ink)}
html[data-theme="dark"] .hot-section{background:linear-gradient(135deg,#1a140a 0%,#1c1208 100%)}
html[data-theme="dark"] .hot-card{background:#1e2e36;border-color:var(--hair)}
html[data-theme="dark"] .fav-section{background:#1a0f0c}
html[data-theme="dark"] .vt-btn{background:var(--surface);color:var(--ink);border-color:var(--hair)}
html[data-theme="dark"] .vt-btn.active{background:var(--sea);color:#fff;border-color:var(--sea)}

/* ── кнопка темы ── */
.theme-btn{font-family:var(--mono);font-size:13px;background:none;border:1px solid var(--hair);border-radius:6px;padding:4px 8px;cursor:pointer;color:var(--muted);white-space:nowrap}
.theme-btn:hover{border-color:var(--sea);color:var(--sea)}

/* ── счётчик избранного ── */
.fav-count-btn{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--coral-d);background:none;border:1px solid var(--coral);border-radius:6px;padding:4px 10px;cursor:pointer;white-space:nowrap;display:none}
.fav-count-btn:hover{background:var(--coral);color:#fff}
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <svg class="brandmark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="26" r="12" fill="#ef6c45"/><g stroke="#bfe0db" stroke-linecap="round" fill="none"><line x1="8" y1="40" x2="56" y2="40" stroke-width="3.4"/><line x1="14" y1="47" x2="50" y2="47" stroke-width="2.8" opacity=".55"/><line x1="21" y1="53" x2="43" y2="53" stroke-width="2.8" opacity=".3"/></g></svg>
    <span class="mark">Sabai<span class="m2">Stay</span></span>
    <span class="tagline">все объявления Пхангана из Telegram-каналов аренды — с фильтром, картой и ценой к рынку</span>
    <span class="coordbar"><span class="compass">✦&nbsp;N</span><span class="geo">9.74°N · 100.01°E</span><span class="snap">снимок __SNAPSHOT__</span><span id="count2"></span></span>
    <span class="header-actions">
      <button class="share-btn" id="share-btn" title="Скопировать ссылку с фильтрами">🔗 поделиться</button>
      <button class="cur-btn" id="cur-btn" title="Переключить валюту THB / USD">฿ THB</button>
      <button class="export-btn" id="export-btn" title="Скачать список в CSV">⬇ CSV</button>
      <button class="share-btn" id="stats-btn" title="Статистика по районам">📊 районы</button>
      <button class="fav-count-btn" id="fav-count-btn" title="Перейти к избранному">🔖 <span id="fav-count-num">0</span></button>
      <button class="theme-btn" id="theme-btn" title="Переключить тёмную тему">🌙</button>
    </span>
  </div>
</header>

<div class="view-toggle" role="group" aria-label="Режим отображения">
  <button class="vt-btn active" id="vt-list">📋 Список</button>
  <button class="vt-btn" id="vt-map">🗺 Карта</button>
</div>
<button class="filter-toggle" id="filter-toggle" aria-controls="filter-panel" aria-expanded="false">
  <span>⚙ Фильтры</span><span id="ftbadge" class="ftbadge"></span><span class="ftarrow">▾</span>
</button>
<section class="filters" aria-label="Фильтр" id="filter-panel">
  <div class="f f-search-wrap"><label for="f-search">Поиск</label>
    <input id="f-search" type="text" placeholder="pool, sea view, 2br, studio…" autocomplete="off"></div>
  <div class="f"><label for="f-district">Район</label>
    <select id="f-district"><option value="">весь остров</option></select></div>
  <div class="f"><label for="f-sqm-min">Площадь от, м²</label>
    <input id="f-sqm-min" type="number" step="10" placeholder="от"></div>
  <div class="f"><label for="f-sqm-max">Площадь до, м²</label>
    <input id="f-sqm-max" type="number" step="10" placeholder="до"></div>
  <div class="f"><label for="f-price-min">Цена от, ฿/мес</label>
    <input id="f-price-min" type="number" step="5000" placeholder="от 0"></div>
  <div class="f"><label for="f-price">Цена до, ฿/мес</label>
    <input id="f-price" type="number" step="5000" placeholder="без лимита"></div>
  <div class="f"><label for="f-bed">Спален от</label>
    <select id="f-bed"><option value="">любое</option><option>1</option><option>2</option><option>3</option></select></div>
  <div class="f"><label for="f-type">Тип жилья</label>
    <select id="f-type"><option value="">любой</option><option value="villa">вилла</option><option value="house">дом</option><option value="bungalow">бунгало</option><option value="apartment">апартаменты</option><option value="studio">студия</option><option value="room">комната</option><option value="other">другое</option></select></div>
  <label class="chk"><input type="checkbox" class="f-am" value="pool"> 🏊 бассейн</label>
  <label class="chk"><input type="checkbox" class="f-am" value="seaview"> 🌊 море</label>
  <label class="chk"><input type="checkbox" class="f-am" value="wifi"> 📶 wi-fi</label>
  <label class="chk"><input type="checkbox" class="f-am" value="kitchen"> 🍳 кухня</label>
  <label class="chk"><input type="checkbox" class="f-am" value="aircon"> ❄️ кондей</label>
  <label class="chk"><input type="checkbox" id="f-stale"> скрыть неактуальные</label>
  <label class="chk"><input type="checkbox" id="f-avail"> только с датой заезда</label>
  <div class="f"><label for="f-sort">Сортировка</label>
    <select id="f-sort"><option value="">сначала свежие</option><option value="cheap">сначала дешёвые</option><option value="pricey">сначала дорогие</option><option value="sqm_desc">площадь: больше</option><option value="sqm_asc">площадь: меньше</option></select></div>
  <button class="reset" id="reset">сбросить</button>
</section>

<div id="stats-modal" class="modal-overlay" style="display:none" onclick="if(event.target===this)closeStats()">
  <div class="modal-box">
    <button class="modal-close" onclick="closeStats()">✕</button>
    <h2>Статистика по районам</h2>
    <div id="stats-table-wrap"></div>
  </div>
</div>

<div id="hot-section" class="hot-section" style="display:none">
  <h3>🔥 Горячие предложения</h3>
  <div class="hot-cards" id="hot-cards"></div>
</div>

<div id="fav-section" class="fav-section" style="display:none">
  <h3>🔖 Избранное</h3>
  <div class="fav-cards" id="fav-cards"></div>
  <button class="fav-clear" onclick="clearFav()">очистить избранное</button>
</div>

<div class="wrap">
  <div class="list"><div class="count" id="count"></div><div id="cards"></div>
    <div class="load-more-wrap" id="load-more-wrap" style="display:none">
      <button class="load-more-btn" id="load-more-btn">показать ещё</button>
    </div>
    <footer class="listfoot">Источник: Telegram-каналы аренды Пхангана · каждое объявление ведёт в оригинал · снимок __SNAPSHOT__<br><a href="privacy.html">Приватность</a> · <a href="privacy.html#remove">Убрать объявление</a></footer>
  </div>
  <div id="map"></div>
</div>

<script>
const DATA = __DATA__;
const TYPE={villa:"вилла",house:"дом",room:"комната",studio:"студия",bungalow:"бунгало",apartment:"апартаменты",land:"участок",other:"жильё"};
const AM={pool:"🏊",wifi:"📶",kitchen:"🍳",aircon:"❄️",seaview:"🌊"};
const PERIOD={month:"/мес",night:"/ночь",sale:""};
const SRC={telegram:"Telegram",fb_group:"Facebook",fb_marketplace:"Facebook",booking:"Booking",airbnb:"Airbnb",manual:"вручную"};
const SRCICON={telegram:"📨",fb_group:"📘",fb_marketplace:"📘",booking:"🏨",airbnb:"🛏"};
const SNAP_ISO='__SNAPSHOT_ISO__';
const SNAP_DATE=SNAP_ISO?new Date(SNAP_ISO):null;
let CUR='thb';
const RATE_USD=35;
const compact=n=>{if(CUR==='usd'){const u=Math.round(n/RATE_USD);return u>=1000?(u/1000).toFixed(1)+'k$':'$'+u;}return n>=1000?(n/1000)+'k':(''+n);};
const priceFull=n=>{if(n==null)return null;if(CUR==='usd')return'$'+Math.round(n/RATE_USD).toLocaleString('en');return n.toLocaleString('ru-RU');};
const firstUrl=d=>d.sources[0].source_url;
function sourcesHtml(arr){
  if(arr.length===1){const s=arr[0];return '<a class="src" href="'+s.source_url+'" target="_blank" rel="noopener">↗ '+(SRC[s.source]||"оригинал")+'</a>';}
  return '<span class="multi">🔗 '+arr.length+' источника</span>'+arr.map(s=>'<a class="src" title="'+(SRC[s.source]||"источник")+'" href="'+s.source_url+'" target="_blank" rel="noopener">'+(SRCICON[s.source]||"↗")+'</a>').join("");
}
function contactBtn(contact){
  if(!contact||!contact.telegram)return '';
  const u=contact.telegram.replace(/^@/,'');
  return '<a class="contact-btn" href="https://t.me/'+u+'" target="_blank" rel="noopener">✉ написать</a>';
}

// ── тёмная тема: ранняя инициализация (до map) ──
(function(){
  const TKEY='sabaistay_theme';
  const saved=localStorage.getItem(TKEY);
  const prefersDark=window.matchMedia('(prefers-color-scheme:dark)').matches;
  const dark=saved?saved==='dark':prefersDark;
  document.documentElement.setAttribute('data-theme',dark?'dark':'light');
  const btn=document.getElementById('theme-btn');
  if(btn)btn.textContent=dark?'☀️':'🌙';
})();

// карта — тихие тайлы CartoDB под «бумагу карты»
const map=L.map('map',{zoomControl:true}).setView([9.745,100.01],12);
const _dark=document.documentElement.getAttribute('data-theme')==='dark';
const tileL=L.tileLayer(
  _dark?'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
       :'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  {attribution:'© OpenStreetMap © CARTO',subdomains:'abcd',maxZoom:19}).addTo(map);
let markers={};
const clusterGroup=L.markerClusterGroup({maxClusterRadius:55,showCoverageOnHover:false,iconCreateFunction:function(c){return L.divIcon({className:'',html:'<div class="cluster-icon">'+c.getChildCount()+'</div>',iconSize:[34,34]});}});
map.addLayer(clusterGroup);

// район-дропдаун из реальных данных
[...new Set(DATA.filter(d=>d.district).map(d=>d.district))].sort().forEach(d=>{
  const o=document.createElement('option');o.value=d;o.textContent=d;document.getElementById('f-district').appendChild(o);
});

const AMLABEL={pool:'🏊 бассейн',seaview:'🌊 море',wifi:'📶 wi-fi',kitchen:'🍳 кухня',aircon:'❄️ кондей'};
function readF(){return{
  district:document.getElementById('f-district').value,
  sqmMin:+document.getElementById('f-sqm-min').value||0,
  sqmMax:+document.getElementById('f-sqm-max').value||0,
  priceMin:+document.getElementById('f-price-min').value||0,
  price:+document.getElementById('f-price').value||0,
  bed:+document.getElementById('f-bed').value||0,
  type:document.getElementById('f-type').value,
  amenities:Array.from(document.querySelectorAll('.f-am:checked')).map(e=>e.value),
  hideStale:document.getElementById('f-stale').checked,
  onlyAvail:document.getElementById('f-avail').checked,
  search:(document.getElementById('f-search').value||'').trim().toLowerCase()};
}
function passes(d,f){
  if(f.district&&d.district!==f.district)return false;
  if(f.sqmMin&&(d.area_sqm==null||d.area_sqm<f.sqmMin))return false;
  if(f.sqmMax&&(d.area_sqm==null||d.area_sqm>f.sqmMax))return false;
  if(f.priceMin&&(d.price==null||d.price<f.priceMin))return false;
  if(f.price&&(d.price==null||d.price>f.price))return false;
  if(f.bed&&(d.bedrooms==null||d.bedrooms<f.bed))return false;
  if(f.type&&d.type!==f.type)return false;
  for(const a of f.amenities){if(!d.amenities[a])return false;}
  if(f.hideStale&&d.fresh.stale)return false;
  if(f.onlyAvail&&!d.available_from)return false;
  if(f.search){const hay=(d.title+' '+d.text+' '+(d.district||'')).toLowerCase();if(!hay.includes(f.search))return false;}
  return true;}
const amen=a=>Object.keys(AM).filter(k=>a[k]).map(k=>AM[k]).join(" ");
const escHtml=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function makeCard(d,i,key,target){
  const card=document.createElement('article'); card.className="card"+(d.fresh.stale?" stale":""); card.style.setProperty('--i',i);
  const fb='<span class="fresh-badge fb-'+d.fresh.cls+'">'+d.fresh.label+'</span>';
  const dtag=d.district?'<span class="dtag">📍 '+d.district+'</span>':'<span class="dtag nogeo">📍 район не указан</span>';
  const draft=d.confidence<0.6?' <span class="draft">черновик</span>':'';
  const unit=PERIOD[d.period]!=null?PERIOD[d.period]:'';
  const price=d.price!=null
    ? '<span class="price">'+priceFull(d.price)+' ฿<small>'+unit+'</small></span>'
    : '<span class="price none">цена не указана</span>';
  const b=d.bench;
  const benchBadge=b?'<span class="bench bench-'+b.kind+'" title="медиана района: '+priceFull(b.monthly_median)+' ฿/мес">'+b.label+'</span>':'';
  const ref=b&&b.airbnb_adr?'<div class="ref">🏨 Airbnb здесь ~'+priceFull(b.airbnb_adr)+'฿/ночь'+(b.seed?' <span class="seed">оценка</span>':'')+'</div>':'';
  const amenStr=amen(d.amenities);
  const availHtml=(()=>{
    if(!d.available_from)return '';
    if(d.available_from==='now')return '<span class="avail-now">🟢 доступно сейчас</span>';
    const parts=d.available_from.split('-');
    const mon=['','янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
    const label=parts.length===2?mon[+parts[1]]+' '+parts[0]:(parts[2]+' '+mon[+parts[1]]+' '+parts[0]);
    let daysHtml='';
    if(SNAP_DATE&&parts.length===3){
      const avDate=new Date(d.available_from);
      const diff=Math.round((avDate-SNAP_DATE)/(1000*60*60*24));
      if(diff<=0)daysHtml=' <span class="avail-ready">✓ уже можно</span>';
      else if(diff<=14)daysHtml=' <span class="avail-soon">через '+diff+' дн.</span>';
    }
    return '<span class="avail-date">📅 с '+label+'</span>'+daysHtml;
  })();
  const expandHtml=d.text?'<details class="card-raw"><summary class="card-raw-toggle">читать целиком</summary><pre class="card-raw-text">'+escHtml(d.text)+'</pre></details>':'';
  const isSaved=getFavs().includes(d.id);
  const seen=getSeen().has(d.id);
  const bmBtn='<button class="bm-btn'+(isSaved?' saved':'')+'" data-id="'+escHtml(d.id)+'" title="'+(isSaved?'Убрать из избранного':'В избранное')+'" aria-label="Закладка">🔖</button>';
  const sqmHtml=d.area_sqm?'<span>📐 '+d.area_sqm+' м²</span>':'';
  const seenHtml=seen?'<span class="seen-badge">уже смотрел</span>':'';
  // качество объявления
  const qScore=(d.price!=null?1:0)+(d.district?1:0)+(d.bedrooms!=null?1:0)+(d.type&&d.type!=='other'?1:0);
  const qHtml='<span class="quality" title="Полнота данных">'+('★'.repeat(qScore)+'☆'.repeat(4-qScore))+'</span>';
  // подсветка поиска
  const _srch=readF().search;
  function hlTitle(t){if(!_srch)return t;const re=new RegExp('('+_srch.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');return t.replace(re,'<mark class="hl">$1</mark>');}
  card.setAttribute('data-type',d.type);
  if(key!=null)card.setAttribute('data-key',String(key));
  card.innerHTML=bmBtn+'<div class="card-top">'+dtag+fb+'</div>'+
    '<div class="title">'+hlTitle(escHtml(d.title))+draft+qHtml+seenHtml+'</div>'+
    '<div class="specs"><span>🛏 '+(d.bedrooms??'—')+'</span><span>🏠 '+(TYPE[d.type]||d.type)+'</span>'+sqmHtml+(amenStr?'<span class="spec-amen">'+amenStr+'</span>':'')+(availHtml?availHtml:'')+'</div>'+
    ref+
    '<div class="foot"><span class="priceblock">'+price+benchBadge+'</span><span class="srcs">'+contactBtn(d.contact)+sourcesHtml(d.sources)+'</span></div>'+
    expandHtml;
  // клик по expand не должен открывать карту / ссылку
  card.querySelector('details')?.addEventListener('click',e=>e.stopPropagation());
  // закладка — стопим всплытие, переключаем
  card.querySelector('.bm-btn')?.addEventListener('click',e=>{e.stopPropagation();toggleFav(d.id);});
  // история просмотра
  card.addEventListener('click',()=>{markSeen(d.id);card.querySelector('.seen-badge')||card.querySelector('.title')?.insertAdjacentHTML('beforeend','<span class="seen-badge">уже смотрел</span>');},{once:true,capture:false});
  if(d.lat!=null&&key!=null){
    const m=markers[key]; // уже добавлен в render()
    if(m){
      const hot=on=>{const el=m.getElement&&m.getElement();if(el){const s=el.querySelector('.sounding');if(s)s.classList.toggle('hot',on);}};
      card.onmouseenter=()=>{hot(true);m.openPopup()};
      card.onmouseleave=()=>hot(false);
      const rm=matchMedia('(prefers-reduced-motion: reduce)').matches;
      card.onclick=()=>{document.querySelectorAll('.card.active').forEach(c=>c.classList.remove('active'));card.classList.add('active');map.flyTo([d.lat,d.lng],14,{duration:rm?0:.5,animate:!rm});m.openPopup()};
    }
  } else if(d.lat==null){card.onclick=()=>window.open(firstUrl(d),'_blank');}
  if(target)target.appendChild(card);
  return card;
}
const PAGE_SIZE=30;
let _geoView=[], _noGeoView=[], _geoRendered=0;

function _pluralObj(n){return n%10===1&&n%100!==11?'е':n%10>=2&&n%10<=4&&(n%100<10||n%100>=20)?'я':'й';}

function renderPage(cards){
  const end=Math.min(_geoRendered+PAGE_SIZE,_geoView.length);
  for(let i=_geoRendered;i<end;i++) makeCard(_geoView[i],i,i+1,cards);
  _geoRendered=end;
  const lmw=document.getElementById('load-more-wrap');
  const lmb=document.getElementById('load-more-btn');
  if(lmw&&lmb){
    const rem=_geoView.length-_geoRendered;
    if(rem>0){lmw.style.display='';lmb.textContent='показать ещё '+Math.min(rem,PAGE_SIZE)+' из '+rem;}
    else{lmw.style.display='none';}
  }
}

function render(first){
  const f=readF(), cards=document.getElementById('cards');
  cards.innerHTML=""; cards.classList.toggle('anim',!!first);
  clusterGroup.clearLayers(); markers={};
  let view=DATA.map(d=>d);
  const sortV=document.getElementById('f-sort').value;
  if(sortV==='cheap')view.sort((a,b)=>(a.price==null?Infinity:a.price)-(b.price==null?Infinity:b.price));
  else if(sortV==='pricey')view.sort((a,b)=>(b.price==null?-1:b.price)-(a.price==null?-1:a.price));
  else if(sortV==='fresh')view.sort((a,b)=>String(b.posted_at||'').localeCompare(String(a.posted_at||'')));
  else if(sortV==='sqm_desc')view.sort((a,b)=>(b.area_sqm??-1)-(a.area_sqm??-1));
  else if(sortV==='sqm_asc')view.sort((a,b)=>(a.area_sqm??Infinity)-(b.area_sqm??Infinity));
  _geoView=[]; _noGeoView=[];
  view.forEach(d=>{if(!passes(d,f))return; (d.lat!=null?_geoView:_noGeoView).push(d);});
  const shown=_geoView.length+_noGeoView.length;
  // добавляем все маркеры на карту сразу
  _geoView.forEach((d,idx)=>{
    if(d.lat==null)return;
    const unit=d.period==='daily'?' /д':(d.period==='weekly'?' /нед':'');
    const icon=L.divIcon({className:'',html:'<div class="sounding'+(d.fresh.stale?' dim':'')+'">'+(d.price!=null?compact(d.price):'·')+'</div>',iconSize:null});
    const m=L.marker([d.lat,d.lng],{icon});
    const amenStr=(d.amenities||[]).map(a=>AMLABEL[a]||a).join(' ');
    clusterGroup.addLayer(m);
    m.bindPopup('<b>'+d.title+'</b><br>'+(TYPE[d.type]||d.type)+(d.bedrooms!=null?' · '+d.bedrooms+' сп.':'')+(amenStr?' '+amenStr:'')+'<br>'+(d.price!=null?priceFull(d.price)+' ฿'+unit:'цена не указана')+(d.sources.length>1?'<br>'+d.sources.length+' источника':'')+'<br><a href="'+(d.sources[0]?.source_url||'#')+'" target="_blank">оригинал ↗</a>');
    markers[idx+1]=m;
    (function(key){m.on('click',function(){
      let card=document.querySelector('.card[data-key="'+key+'"]');
      const cardsEl=document.getElementById('cards');
      while(!card&&_geoRendered<_geoView.length){renderPage(cardsEl);card=document.querySelector('.card[data-key="'+key+'"]');}
      if(card){document.querySelectorAll('.card.active').forEach(c=>c.classList.remove('active'));card.classList.add('active');card.scrollIntoView({behavior:'smooth',block:'nearest'});}
    });})(idx+1);
  });
  // рендерим первую страницу карточек
  _geoRendered=0;
  renderPage(cards);
  // секция "без района"
  if(_noGeoView.length>0){
    const wrap=document.createElement('div'); wrap.id='nogeo-wrap';
    const btn=document.createElement('div'); btn.className='nogeo-divider'; btn.setAttribute('role','button');
    btn.innerHTML='📍 Без района — '+_noGeoView.length+' объявлени'+_pluralObj(_noGeoView.length)+' (район не указан в тексте) <span class="nd-arrow">▾</span>';
    const inner=document.createElement('div'); inner.className='nogeo-cards';
    btn.onclick=()=>{btn.classList.toggle('nogeo-open');inner.classList.toggle('open');};
    _noGeoView.forEach((d,i)=>makeCard(d,_geoView.length+i,null,inner));
    wrap.appendChild(btn); wrap.appendChild(inner); cards.appendChild(wrap);
  }
  if(!shown)cards.innerHTML='<div class="empty"><span class="big">🧭</span><b>В этих координатах пусто</b>под такой фильтр объявлений нет — ослабь условия<button class="clear" onclick="resetFilters()">сбросить фильтр</button></div>';
  const word='объявлени'+_pluralObj(shown);
  const tail=_noGeoView.length?' · '+_noGeoView.length+' без района':'';
  const chips=[];
  if(f.district)chips.push(['district',f.district]);
  if(f.price)chips.push(['price','до '+priceFull(f.price)+'฿']);
  if(f.bed)chips.push(['bed','от '+f.bed+' спален']);
  if(f.type)chips.push(['type',TYPE[f.type]||f.type]);
  f.amenities.forEach(a=>chips.push(['am:'+a,AMLABEL[a]||a]));
  if(f.hideStale)chips.push(['stale','без неактуальных']);
  document.getElementById('count').innerHTML='<span class="rcount">'+shown+' '+word+'</span>'+tail+
    chips.map(c=>'<button class="chip" onclick="chipClear(\''+c[0]+'\')">'+c[1]+'<span class="x">✕</span></button>').join('');
  document.getElementById('count2').textContent=shown+'/'+DATA.length;
  writeHash(f);
  const n=(f.district?1:0)+(f.sqmMin?1:0)+(f.sqmMax?1:0)+(f.priceMin?1:0)+(f.price?1:0)+(f.bed?1:0)+(f.type?1:0)+f.amenities.length+(f.hideStale?1:0)+(f.onlyAvail?1:0)+(f.search?1:0);
  const fb=document.getElementById('ftbadge');if(fb)fb.textContent=n?' · '+n:'';
  renderHotOffers();
}
function chipClear(k){
  if(k.startsWith('am:')){const el=document.querySelector('.f-am[value="'+k.slice(3)+'"]');if(el)el.checked=false;}
  else if(k==='stale')document.getElementById('f-stale').checked=false;
  else document.getElementById('f-'+k).value='';
  render(false);
}
function writeHash(f){
  const p=new URLSearchParams();
  if(f.district)p.set('district',f.district);
  if(f.sqmMin)p.set('sqmMin',f.sqmMin);
  if(f.sqmMax)p.set('sqmMax',f.sqmMax);
  if(f.priceMin)p.set('priceMin',f.priceMin);
  if(f.price)p.set('price',f.price);
  if(f.bed)p.set('bed',f.bed);
  if(f.type)p.set('type',f.type);
  if(f.amenities.length)p.set('am',f.amenities.join(','));
  if(f.hideStale)p.set('stale','1');
  if(f.onlyAvail)p.set('avail','1');
  if(f.search)p.set('q',f.search);
  const sort=document.getElementById('f-sort').value; if(sort)p.set('sort',sort);
  const s=p.toString();
  history.replaceState(null,'',s?('#'+s):location.pathname+location.search);
}
function applyHash(){
  if(!location.hash)return;
  const p=new URLSearchParams(location.hash.slice(1));
  const set=(id,key)=>{const v=p.get(key);if(v!=null)document.getElementById(id).value=v;};
  set('f-district','district');set('f-sqm-min','sqmMin');set('f-sqm-max','sqmMax');set('f-price-min','priceMin');set('f-price','price');set('f-bed','bed');set('f-type','type');set('f-sort','sort');set('f-search','q');
  const am=p.get('am'); if(am)am.split(',').forEach(v=>{const el=document.querySelector('.f-am[value="'+v+'"]');if(el)el.checked=true;});
  document.getElementById('f-stale').checked=p.get('stale')==='1';
  document.getElementById('f-avail').checked=p.get('avail')==='1';
}
function resetFilters(){['f-district','f-sqm-min','f-sqm-max','f-price-min','f-price','f-bed','f-type','f-sort','f-search'].forEach(id=>document.getElementById(id).value='');document.querySelectorAll('.f-am').forEach(e=>e.checked=false);document.getElementById('f-stale').checked=false;document.getElementById('f-avail').checked=false;render(false);}
['f-district','f-sqm-min','f-sqm-max','f-price-min','f-price','f-bed','f-type','f-sort','f-search'].forEach(id=>document.getElementById(id).addEventListener('input',()=>render(false)));
document.querySelectorAll('.f-am, #f-stale, #f-avail').forEach(e=>e.addEventListener('change',()=>render(false)));
document.getElementById('reset').onclick=resetFilters;
// мобильный toggle фильтра
(function(){
  const btn=document.getElementById('filter-toggle'),panel=document.getElementById('filter-panel');
  if(!btn)return;
  btn.onclick=function(){const open=panel.classList.toggle('open');btn.setAttribute('aria-expanded',String(open));};
})();
applyHash();
render(true);
// мобильный переключатель карта/список
(function(){
  const wrap=document.querySelector('.wrap');
  const btnList=document.getElementById('vt-list');
  const btnMap=document.getElementById('vt-map');
  if(!btnList||!btnMap||!wrap)return;
  function showList(){wrap.classList.add('mobile-list');wrap.classList.remove('mobile-map');btnList.classList.add('active');btnMap.classList.remove('active');btnList.setAttribute('aria-pressed','true');btnMap.setAttribute('aria-pressed','false');}
  function showMap(){wrap.classList.add('mobile-map');wrap.classList.remove('mobile-list');btnMap.classList.add('active');btnList.classList.remove('active');btnMap.setAttribute('aria-pressed','true');btnList.setAttribute('aria-pressed','false');window.dispatchEvent(new Event('resize'));}
  btnList.addEventListener('click',showList);
  btnMap.addEventListener('click',showMap);
  showList();
})();

// ── кнопка «показать ещё» ────────────────────────────
(function(){
  const btn=document.getElementById('load-more-btn');
  if(btn)btn.addEventListener('click',()=>renderPage(document.getElementById('cards')));
})();

// ── горячие предложения ───────────────────────────────
function renderHotOffers(){
  const sec=document.getElementById('hot-section');
  const cont=document.getElementById('hot-cards');
  if(!sec||!cont)return;
  const active=DATA.filter(d=>!d.fresh.stale&&d.price!=null&&d.period==='monthly'&&d.district&&d.lat!=null);
  // медиана по районам
  const medByDistrict={};
  const byD={};
  active.forEach(d=>{(byD[d.district]=byD[d.district]||[]).push(d.price);});
  Object.entries(byD).forEach(([d,prices])=>{const s=[...prices].sort((a,b)=>a-b);const m=Math.floor(s.length/2);medByDistrict[d]=s.length%2?s[m]:Math.round((s[m-1]+s[m])/2);});
  const hot=active.filter(d=>medByDistrict[d.district]&&d.price<medByDistrict[d.district]*0.85)
    .sort((a,b)=>String(b.posted_at||'').localeCompare(String(a.posted_at||''))).slice(0,4);
  if(hot.length<2){sec.style.display='none';return;}
  sec.style.display='';
  cont.innerHTML=hot.map(d=>{
    const url=d.sources[0]?.source_url||'#';
    const disc=Math.round((1-d.price/medByDistrict[d.district])*100);
    return'<div class="hot-card" onclick="window.open(\''+url+'\',\'_blank\')">'+
      '<div class="hc-title">'+escHtml(d.title)+'</div>'+
      '<div class="hc-price">'+priceFull(d.price)+(CUR==='thb'?' ฿':'')+'<small>/мес</small></div>'+
      '<div class="hc-meta">'+d.district+' · -'+disc+'% от медианы · '+d.fresh.label+'</div>'+
      '</div>';
  }).join('');
}

// ── валюта THB / USD ─────────────────────────────────
(function(){
  const btn=document.getElementById('cur-btn');
  if(!btn)return;
  btn.addEventListener('click',()=>{
    CUR=CUR==='thb'?'usd':'thb';
    btn.textContent=CUR==='usd'?'$ USD':'฿ THB';
    btn.classList.toggle('usd',CUR==='usd');
    render(false);
  });
})();

// ── история просмотра ────────────────────────────────
const SEEN_KEY='sabaistay_seen';
function getSeen(){try{return new Set(JSON.parse(localStorage.getItem(SEEN_KEY)||'[]'));}catch{return new Set();}}
function markSeen(id){const s=getSeen();s.add(id);localStorage.setItem(SEEN_KEY,JSON.stringify([...s]));}

// ── статистика по районам ─────────────────────────────
function openStats(){
  const modal=document.getElementById('stats-modal');
  const wrap=document.getElementById('stats-table-wrap');
  if(!modal||!wrap)return;
  const byDistrict={};
  DATA.forEach(d=>{
    const k=d.district||'Без района';
    if(!byDistrict[k])byDistrict[k]={count:0,prices:[],active:0};
    byDistrict[k].count++;
    if(!d.fresh.stale)byDistrict[k].active++;
    if(d.price!=null&&d.period==='monthly')byDistrict[k].prices.push(d.price);
  });
  const rows=Object.entries(byDistrict).sort((a,b)=>b[1].count-a[1].count);
  const median=arr=>{if(!arr.length)return null;const s=[...arr].sort((a,b)=>a-b);const m=Math.floor(s.length/2);return s.length%2?s[m]:Math.round((s[m-1]+s[m])/2);};
  wrap.innerHTML='<table class="stats-table"><thead><tr><th>Район</th><th>Объявл.</th><th>Актуальных</th><th>Медиана, ฿/мес</th></tr></thead><tbody>'+
    rows.map(([d,v])=>{
      const med=median(v.prices);
      return'<tr><td class="st-district">'+d+'</td><td>'+v.count+'</td><td>'+v.active+'</td><td class="st-price">'+(med?med.toLocaleString('ru-RU')+' ฿':'—')+'</td></tr>';
    }).join('')+'</tbody></table>';
  modal.style.display='flex';
}
function closeStats(){const m=document.getElementById('stats-modal');if(m)m.style.display='none';}
(function(){const btn=document.getElementById('stats-btn');if(btn)btn.addEventListener('click',openStats);})();
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeStats();});

// ── экспорт CSV ──────────────────────────────────────
function exportCSV(){
  const f=readF();
  const view=DATA.filter(d=>passes(d,f));
  const hdr=['Заголовок','Район','Цена, ฿','Период','Спален','Тип','Площадь, м²','Доступно с','Свежесть','Ссылка'];
  const esc=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const csv=[hdr,...view.map(d=>[
    d.title,d.district||'',d.price||'',d.period||'',d.bedrooms??'',d.type||'',
    d.area_sqm||'',d.available_from||'',d.fresh.label,firstUrl(d)
  ])].map(r=>r.map(esc).join(',')).join('\r\n');
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'}));
  a.download='sabaistay_'+new Date().toISOString().slice(0,10)+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
}
(function(){const btn=document.getElementById('export-btn');if(btn)btn.addEventListener('click',exportCSV);})();

// ── закладки (localStorage) ──────────────────────────
const FAV_KEY='sabaistay_fav';
function getFavs(){try{return JSON.parse(localStorage.getItem(FAV_KEY)||'[]');}catch{return[];}}
function saveFavs(a){localStorage.setItem(FAV_KEY,JSON.stringify(a));}
function toggleFav(id){
  let a=getFavs();
  const i=a.indexOf(id);
  if(i>=0){a.splice(i,1);}else{a.push(id);}
  saveFavs(a);
  renderFavSection();
  document.querySelectorAll('.bm-btn[data-id="'+id+'"]').forEach(b=>{
    b.classList.toggle('saved',a.includes(id));
    b.title=a.includes(id)?'Убрать из избранного':'В избранное';
  });
}
function clearFav(){saveFavs([]);renderFavSection();}
function updateFavCount(){
  const n=getFavs().length;
  const btn=document.getElementById('fav-count-btn');
  const num=document.getElementById('fav-count-num');
  if(btn)btn.style.display=n>0?'':'none';
  if(num)num.textContent=n;
}
function renderFavSection(){
  const favs=getFavs();
  const sec=document.getElementById('fav-section');
  const cont=document.getElementById('fav-cards');
  updateFavCount();
  if(!sec||!cont)return;
  if(favs.length===0){sec.style.display='none';cont.innerHTML='';return;}
  sec.style.display='';
  cont.innerHTML='';
  const all=DATA.filter(d=>favs.includes(d.id));
  all.forEach(d=>{
    const el=makeCard(d);
    el.querySelector('.bm-btn')?.classList.add('saved');
    cont.appendChild(el);
  });
  // обновляем звёздочки в основном списке
  document.querySelectorAll('.bm-btn').forEach(b=>{
    b.classList.toggle('saved',favs.includes(b.dataset.id));
    b.title=favs.includes(b.dataset.id)?'Убрать из избранного':'В избранное';
  });
}
renderFavSection();

// ── кнопка «поделиться» ─────────────────────────────
(function(){
  const btn=document.getElementById('share-btn');
  if(!btn)return;
  btn.addEventListener('click',()=>{
    const url=location.href;
    if(navigator.clipboard){
      navigator.clipboard.writeText(url).then(()=>{
        btn.textContent='✓ скопировано';btn.classList.add('copied');
        setTimeout(()=>{btn.textContent='🔗 поделиться';btn.classList.remove('copied');},2000);
      });
    }else{
      const el=document.createElement('textarea');el.value=url;
      document.body.appendChild(el);el.select();document.execCommand('copy');document.body.removeChild(el);
      btn.textContent='✓ скопировано';btn.classList.add('copied');
      setTimeout(()=>{btn.textContent='🔗 поделиться';btn.classList.remove('copied');},2000);
    }
  });
})();

// ── тёмная тема: переключение ──
(function(){
  const TKEY='sabaistay_theme';
  const html=document.documentElement;
  const btn=document.getElementById('theme-btn');
  if(btn)btn.addEventListener('click',function(){
    const nowDark=html.getAttribute('data-theme')==='dark';
    const next=nowDark?'light':'dark';
    html.setAttribute('data-theme',next);
    localStorage.setItem(TKEY,next);
    btn.textContent=next==='dark'?'☀️':'🌙';
    tileL.setUrl(next==='dark'
      ?'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
      :'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png');
  });
  window.matchMedia('(prefers-color-scheme:dark)').addEventListener('change',function(e){
    if(localStorage.getItem(TKEY))return;
    const next=e.matches?'dark':'light';
    html.setAttribute('data-theme',next);
    if(btn)btn.textContent=next==='dark'?'☀️':'🌙';
    tileL.setUrl(next==='dark'
      ?'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
      :'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png');
  });
})();

// ── клавиатурный шорткат: / → поиск ──
document.addEventListener('keydown',function(e){
  if(e.key==='/'&&document.activeElement.tagName!=='INPUT'&&document.activeElement.tagName!=='TEXTAREA'&&document.activeElement.tagName!=='SELECT'){
    e.preventDefault();
    const s=document.getElementById('f-search');
    if(s){s.focus();s.select();}
  }
});
document.getElementById('f-search')?.addEventListener('keydown',function(e){
  if(e.key==='Escape'){this.value='';this.blur();render(false);}
});

// ── счётчик избранного: клик → скролл к секции ──
document.getElementById('fav-count-btn')?.addEventListener('click',function(){
  document.getElementById('fav-section')?.scrollIntoView({behavior:'smooth',block:'start'});
});
</script>
</body>
</html>"""


def _ru_date(iso: str | None) -> str:
    if not iso:
        return "—"
    months = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {months[int(m)]} {y}"


def main() -> None:
    rows, snapshot = build_rows()
    snap_iso = snapshot[:10] if snapshot else ""
    html = (HTML.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
                .replace("__SNAPSHOT__", _ru_date(snapshot))
                .replace("__SNAPSHOT_ISO__", snap_iso)
                .replace("__FAVICON__", quote(FAVICON_SVG))
                .replace("__SITE__", SITE_URL))
    OUT.write_text(html, encoding="utf-8")
    geo = sum(1 for r in rows if r["lat"] is not None)
    stale = sum(1 for r in rows if r["fresh"]["stale"])
    print(f"Собрано web/index.html: {len(rows)} объектов ({geo} на карте, {stale} неактуальных). "
          f"Снимок: {_ru_date(snapshot)}. Стиль: морская карта Пхангана.")


if __name__ == "__main__":
    main()

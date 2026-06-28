-- SabaiStay — модель данных (Postgres / Neon)
-- Принцип PDPA-safe: храним факты объекта + ссылку на оригинал.
-- НЕ храним телефон/имя/контакт продавца как структурное поле (см. docs/legal-pdpa.md).

-- ─────────────────────────── Перечисления ───────────────────────────
CREATE TYPE source_kind   AS ENUM ('fb_marketplace', 'fb_group', 'telegram', 'airbnb', 'booking', 'manual');
CREATE TYPE price_period   AS ENUM ('month', 'night', 'sale', 'unknown');
CREATE TYPE property_kind  AS ENUM ('house', 'villa', 'studio', 'bungalow', 'apartment', 'room', 'land', 'other');
CREATE TYPE listing_status AS ENUM ('active', 'rented', 'expired', 'hidden');

-- ─────────────────────── Сырые посты (ingest) ───────────────────────
-- Буфер до разбора. raw_text чистим/обезличиваем после извлечения полей.
CREATE TABLE raw_post (
    id              BIGSERIAL PRIMARY KEY,
    source          source_kind NOT NULL,
    source_url      TEXT NOT NULL,                 -- ссылка на первоисточник
    external_id     TEXT,                          -- channel/msg_id или fb listing id
    raw_text        TEXT,                          -- временно, для разбора
    posted_at       TIMESTAMPTZ,                   -- когда опубликовано в источнике
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed          BOOLEAN NOT NULL DEFAULT false,
    parse_error     TEXT,
    UNIQUE (source, external_id)                   -- защита от повторного добора
);

-- ────────────────────────── Объявления ──────────────────────────────
CREATE TABLE listing (
    id              BIGSERIAL PRIMARY KEY,
    raw_post_id     BIGINT REFERENCES raw_post(id) ON DELETE SET NULL,

    -- источник
    source          source_kind NOT NULL,
    source_url      TEXT NOT NULL,
    source_posted_at TIMESTAMPTZ,

    -- фильтруемые факты (ядро ценности)
    title           TEXT,
    district        TEXT,                          -- канон из data/districts.json
    lat             DOUBLE PRECISION,              -- центроид района
    lng             DOUBLE PRECISION,
    price_amount    NUMERIC,
    price_currency  TEXT DEFAULT 'THB',
    price_period    price_period NOT NULL DEFAULT 'unknown',
    bedrooms        SMALLINT,
    bathrooms       SMALLINT,
    property_type   property_kind NOT NULL DEFAULT 'other',
    area_sqm        NUMERIC,
    amenities       JSONB NOT NULL DEFAULT '{}',    -- {pool,wifi,kitchen,aircon,motorbike,seaview,...}
    available_from  DATE,
    available_to    DATE,
    min_stay_months SMALLINT,
    language        TEXT,                           -- ru/en/th/mixed
    image_thumb_url TEXT,                           -- ссылка на превью у источника (НЕ ре-хостим оригиналы)

    -- сервис
    status          listing_status NOT NULL DEFAULT 'active',
    confidence      REAL,                           -- уверенность парсера 0..1
    dedup_group     BIGINT,                         -- объединение одного объекта из разных источников
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────── Индексы под фильтр витрины ──────────────────────
CREATE INDEX idx_listing_filter   ON listing (status, price_period, district, bedrooms);
CREATE INDEX idx_listing_price     ON listing (price_amount);
CREATE INDEX idx_listing_geo       ON listing (lat, lng);
CREATE INDEX idx_listing_dedup     ON listing (dedup_group);
CREATE INDEX idx_listing_freshness ON listing (last_seen);
CREATE INDEX idx_listing_amenities ON listing USING GIN (amenities);

-- ──────────────── Подписки на фильтр (киллер-фича) ───────────────────
CREATE TABLE saved_filter (
    id              BIGSERIAL PRIMARY KEY,
    tg_user_id      BIGINT NOT NULL,                -- кому слать пуш
    criteria        JSONB NOT NULL,                 -- {district, price_max, bedrooms_min, pool, period, ...}
    last_notified   TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_saved_filter_user ON saved_filter (tg_user_id, active);

-- Перцептивные хэши фото для дедупа (заполняется на этапе дедупа)
CREATE TABLE listing_phash (
    listing_id  BIGINT REFERENCES listing(id) ON DELETE CASCADE,
    phash       BIGINT NOT NULL,                    -- 64-битный perceptual hash
    PRIMARY KEY (listing_id, phash)
);
CREATE INDEX idx_phash ON listing_phash (phash);

-- ─────────────── Рыночный бенчмарк по районам (Airbnb/Booking + долгосрочная медиана) ───────────────
-- Справочные медианы для сравнения «−18% к рынку». Обновляется ingest/market_airbnb_booking.py.
CREATE TABLE market_benchmark (
    district          TEXT PRIMARY KEY,             -- канон из data/districts.json
    monthly_median    JSONB NOT NULL,               -- {"1": ฿/мес, "2": .., "3": .., "overall": ..}
    airbnb_adr        NUMERIC,                       -- медиана ฿/ночь Airbnb
    booking_adr       NUMERIC,                       -- медиана ฿/ночь Booking
    sample_n          INTEGER NOT NULL DEFAULT 0,
    source            TEXT NOT NULL DEFAULT 'seed',  -- seed | collected
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

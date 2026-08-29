-- Bhav Setu — Round 1 schema.
-- Single file, no migrations (Alembic is explicitly out of scope).
-- Applied by scripts/init_db.py, which drops and recreates everything with --force.

-- ─────────────────────────── reference tables ───────────────────────────

CREATE TABLE states (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    apmc_cess_pct   NUMERIC(5,3) NOT NULL DEFAULT 1.0,
    commission_pct  NUMERIC(5,3) NOT NULL DEFAULT 2.0,
    other_fees_pct  NUMERIC(5,3) NOT NULL DEFAULT 0.5
);

CREATE TABLE mandis (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    normalised_name TEXT NOT NULL,          -- lowercased, punctuation stripped; fuzzy-match target
    district        TEXT,
    state_id        INT REFERENCES states(id),
    apmc_code       TEXT,
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    is_enam         BOOLEAN DEFAULT FALSE,
    active          BOOLEAN DEFAULT TRUE,
    UNIQUE (normalised_name, district, state_id)
);
CREATE INDEX idx_mandis_geo ON mandis (lat, lon);

CREATE TABLE commodities (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    variety             TEXT,
    crop_group          TEXT,          -- cereal | pulse | oilseed | vegetable | fruit | spice
    perishability_class SMALLINT,      -- 1 = very perishable ... 5 = storable
    shelf_life_days     INT,           -- ambient, ungraded
    msp_applicable      BOOLEAN DEFAULT FALSE,
    UNIQUE (name, variety)
);

CREATE TABLE commodity_aliases (
    alias           TEXT PRIMARY KEY,
    normalised_alias TEXT NOT NULL,
    commodity_id    INT NOT NULL REFERENCES commodities(id) ON DELETE CASCADE
);
CREATE INDEX idx_alias_norm ON commodity_aliases (normalised_alias);

CREATE TABLE msp_schedule (
    commodity_id INT REFERENCES commodities(id),
    season       TEXT,                 -- kharif_2025 | rabi_2025_26
    msp_per_qtl  NUMERIC(10,2),
    valid_from   DATE,
    valid_to     DATE,
    PRIMARY KEY (commodity_id, season)
);

-- ─────────────────────────── core time series ───────────────────────────

CREATE TABLE price_observations (
    id              BIGSERIAL PRIMARY KEY,
    obs_date        DATE NOT NULL,
    mandi_id        INT  NOT NULL REFERENCES mandis(id),
    commodity_id    INT  NOT NULL REFERENCES commodities(id),
    variety         TEXT NOT NULL DEFAULT '',   -- '' not NULL: NULLs break the UNIQUE key
    grade           TEXT NOT NULL DEFAULT '',
    min_price       NUMERIC(10,2),
    max_price       NUMERIC(10,2),
    modal_price     NUMERIC(10,2) NOT NULL,
    arrival_qtl     NUMERIC(12,2),              -- always normalised to quintals
    source          TEXT NOT NULL,              -- csv_backfill | agmarknet_api | seed_demo
    is_imputed      BOOLEAN NOT NULL DEFAULT FALSE,
    suspect         BOOLEAN NOT NULL DEFAULT FALSE,
    raw             JSONB,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (obs_date, mandi_id, commodity_id, variety, grade)
);
CREATE INDEX idx_po_lookup ON price_observations (commodity_id, mandi_id, obs_date DESC);
CREATE INDEX idx_po_date   ON price_observations (obs_date DESC);

CREATE TABLE weather_daily (
    obs_date     DATE NOT NULL,
    mandi_id     INT NOT NULL REFERENCES mandis(id),
    rainfall_mm  NUMERIC(6,2),
    tmax_c       NUMERIC(5,2),
    tmin_c       NUMERIC(5,2),
    humidity_pct NUMERIC(5,2),
    is_forecast  BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (obs_date, mandi_id)
);

CREATE TABLE shock_events (
    id            SERIAL PRIMARY KEY,
    event_date    DATE NOT NULL,
    event_type    TEXT NOT NULL,   -- export_ban | export_allowed | stock_limit |
                                   -- msp_hike | import_duty | buffer_release | strike
    commodity_id  INT REFERENCES commodities(id),
    scope         TEXT,            -- national | state:<name>
    direction     SMALLINT,        -- -1 bearish for farmer, +1 bullish
    magnitude     SMALLINT,        -- 1 low, 2 medium, 3 high
    source_url    TEXT,
    title         TEXT,
    decay_days    INT NOT NULL DEFAULT 30,
    UNIQUE (event_date, event_type, commodity_id, scope)
);
CREATE INDEX idx_shock_date ON shock_events (event_date DESC);

CREATE TABLE festivals (
    fest_date     DATE PRIMARY KEY,
    name          TEXT,
    demand_effect JSONB   -- {"vegetable": 1, "oilseed": 0, "pulse": 1}
);

-- OSRM is rate-limited: one call per coordinate pair, ever.
-- PK is the four coordinates rounded to 4 decimals (~11 m resolution).
CREATE TABLE distance_cache (
    from_lat     NUMERIC(9,4) NOT NULL,
    from_lon     NUMERIC(9,4) NOT NULL,
    to_lat       NUMERIC(9,4) NOT NULL,
    to_lon       NUMERIC(9,4) NOT NULL,
    road_km      NUMERIC(8,2) NOT NULL,
    duration_min NUMERIC(8,2),
    source       TEXT NOT NULL DEFAULT 'osrm',   -- osrm | haversine_fallback
    cached_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_lat, from_lon, to_lat, to_lon)
);

-- ─────────────────────────── model outputs ──────────────────────────────

CREATE TABLE forecasts (
    id            BIGSERIAL PRIMARY KEY,
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_for    DATE NOT NULL,       -- as-of date of the information used
    target_date   DATE NOT NULL,
    horizon_days  SMALLINT NOT NULL,
    mandi_id      INT NOT NULL REFERENCES mandis(id),
    commodity_id  INT NOT NULL REFERENCES commodities(id),
    p10           NUMERIC(10,2),
    p50           NUMERIC(10,2),
    p90           NUMERIC(10,2),
    model_version TEXT NOT NULL,
    features_hash TEXT,
    UNIQUE (issued_for, target_date, mandi_id, commodity_id, model_version)
);
CREATE INDEX idx_fc_lookup ON forecasts (mandi_id, commodity_id, target_date);

CREATE TABLE model_registry (
    version        TEXT PRIMARY KEY,
    trained_at     TIMESTAMPTZ,
    train_start    DATE,
    train_end      DATE,
    algo           TEXT,
    params         JSONB,
    metrics        JSONB,     -- {"mape_h7": 8.2, "coverage_80": 0.79, "rupee_uplift_pct": 8.4}
    is_active      BOOLEAN NOT NULL DEFAULT FALSE,
    artifact_path  TEXT
);
-- at most one active model version
CREATE UNIQUE INDEX idx_model_one_active ON model_registry (is_active) WHERE is_active;

-- ─────────────────────────── users and the loop ─────────────────────────

CREATE TABLE farmers (
    id             BIGSERIAL PRIMARY KEY,
    phone_e164     TEXT UNIQUE NOT NULL,
    name           TEXT,
    language       TEXT DEFAULT 'mr',       -- mr | hi | en
    village        TEXT,
    lat            DOUBLE PRECISION,
    lon            DOUBLE PRECISION,
    home_mandi_id  INT REFERENCES mandis(id),
    risk_profile   TEXT DEFAULT 'balanced', -- cautious | balanced | aggressive
    fpo_id         INT,
    consent_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lots (
    id             BIGSERIAL PRIMARY KEY,
    farmer_id      BIGINT REFERENCES farmers(id),
    commodity_id   INT REFERENCES commodities(id),
    variety        TEXT,
    quantity_qtl   NUMERIC(10,2) NOT NULL,
    remaining_qtl  NUMERIC(10,2) NOT NULL,
    harvest_date   DATE,
    quality_grade  TEXT DEFAULT 'B',       -- A | B | C, self-declared
    storage_type   TEXT DEFAULT 'ambient', -- ambient | shed | cold_store
    status         TEXT DEFAULT 'open',    -- open | partially_sold | closed
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recommendations (
    id              BIGSERIAL PRIMARY KEY,
    lot_id          BIGINT REFERENCES lots(id),
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    action          TEXT,               -- sell_now | hold | split | sell_to_procurement
    sell_now_qtl    NUMERIC(10,2),
    hold_qtl        NUMERIC(10,2),
    hold_days       SMALLINT,
    target_mandi_id INT REFERENCES mandis(id),
    expected_net_now      NUMERIC(12,2),
    expected_net_strategy NUMERIC(12,2),
    expected_gain         NUMERIC(12,2),
    confidence      NUMERIC(4,3),
    reason_code     TEXT,
    reason_text     TEXT,
    model_version   TEXT,
    payload         JSONB               -- full alternatives considered
);

-- ⭐ THE MOAT TABLE — what the farmer actually got, not what the board said
CREATE TABLE sale_reports (
    id                BIGSERIAL PRIMARY KEY,
    lot_id            BIGINT REFERENCES lots(id),
    farmer_id         BIGINT REFERENCES farmers(id),
    recommendation_id BIGINT REFERENCES recommendations(id),
    sale_date         DATE NOT NULL,
    mandi_id          INT REFERENCES mandis(id),
    channel           TEXT,             -- mandi | fpo | private_trader | procurement | direct
    quantity_qtl      NUMERIC(10,2),
    gross_price_qtl   NUMERIC(10,2),    -- rate quoted to him
    net_received_qtl  NUMERIC(10,2),    -- what he actually took home, per quintal
    deductions_json   JSONB,            -- {"commission": 420, "hamali": 160}
    followed_advice   BOOLEAN,
    source            TEXT NOT NULL DEFAULT 'whatsapp',  -- whatsapp | web | seed_demo
    reported_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    verification      TEXT DEFAULT 'self_reported'       -- self_reported | slip_photo | fpo_verified
);
CREATE INDEX idx_sr_mandi ON sale_reports (mandi_id, sale_date DESC);

CREATE TABLE transparency_scores (
    mandi_id       INT REFERENCES mandis(id),
    commodity_id   INT REFERENCES commodities(id),
    window_end     DATE,
    n_reports      INT,
    median_gap_pct NUMERIC(6,3),   -- (official_modal - farmer_net) / official_modal
    shrunk_gap_pct NUMERIC(6,3),   -- empirical-Bayes shrunk toward the global mean
    score          NUMERIC(4,1),   -- 0-10, 10 = most transparent
    PRIMARY KEY (mandi_id, commodity_id, window_end)
);

-- Redis holds the hot copy; this is the durable one so a Redis restart
-- mid-demo does not wipe a conversation.
CREATE TABLE bot_sessions (
    phone_e164 TEXT PRIMARY KEY,
    state      TEXT NOT NULL DEFAULT 'NEW',
    context    JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_runs (
    id            BIGSERIAL PRIMARY KEY,
    job           TEXT NOT NULL,
    started_at    TIMESTAMPTZ,
    ended_at      TIMESTAMPTZ,
    status        TEXT,       -- ok | partial | failed
    rows_in       INT,
    rows_kept     INT,
    rows_rejected INT,
    error         TEXT
);
CREATE INDEX idx_ingestion_job ON ingestion_runs (job, started_at DESC);

-- ───────────────────────── coverage (Phase A2) ──────────────────────────
-- Round 1 asked "is this mandi usable?". With fourteen crops across four
-- districts that is the wrong unit: Nashik is dense in onion and empty in
-- banana, and a mandi-level verdict hides both facts. This view is the
-- (district x crop) grain the audit and the crop list are decided on.
--
-- `mandis` here counts markets that actually reported, not markets configured —
-- a district with four yards and one reporting is a different thing from a
-- district with four reporting, and the difference must be visible.

CREATE INDEX idx_mandis_district ON mandis (district);

CREATE VIEW crop_coverage AS
SELECT
    m.district                                          AS district,
    c.id                                                AS commodity_id,
    c.name                                              AS commodity,
    c.perishability_class                               AS perishability_class,
    count(*)::int                                       AS row_count,
    count(DISTINCT p.mandi_id)::int                     AS mandis,
    min(p.obs_date)                                     AS first_date,
    max(p.obs_date)                                     AS last_date,
    (max(p.obs_date) - min(p.obs_date) + 1)::int        AS span_days,
    count(DISTINCT p.obs_date)::int                     AS observed_days,
    count(*) FILTER (WHERE p.is_imputed)::int           AS imputed_rows,
    count(*) FILTER (WHERE p.suspect)::int              AS suspect_rows,
    round(min(p.modal_price), 2)                        AS price_min,
    -- percentile_cont takes double precision, so the cast is explicit rather
    -- than left to an implicit numeric conversion that could pick a different
    -- overload on another Postgres version.
    round(percentile_cont(0.5)
          WITHIN GROUP (ORDER BY p.modal_price::double precision)::numeric, 2)
                                                        AS price_median,
    round(max(p.modal_price), 2)                        AS price_max,
    count(p.arrival_qtl)::int                           AS arrival_rows
FROM price_observations p
JOIN mandis      m ON m.id = p.mandi_id
JOIN commodities c ON c.id = p.commodity_id
GROUP BY m.district, c.id, c.name, c.perishability_class;

-- ─────────────────────── Phase 12: transport pooling ────────────────────
-- Transport is the one cost a small farmer can actually control. At Rs 42/km a
-- 62 km trip costs Rs 2,604 whether the truck is full or not -- Rs 260/qtl on a
-- ten-quintal lot, which is exactly why the nearest mandi so often wins. Four
-- farmers going the same morning split it four ways.

CREATE TABLE transport_pools (
    id            BIGSERIAL PRIMARY KEY,
    mandi_id      INT REFERENCES mandis(id) NOT NULL,
    travel_date   DATE NOT NULL,
    capacity_qtl  NUMERIC(10,2) NOT NULL DEFAULT 90,
    distance_km   NUMERIC(8,2) NOT NULL DEFAULT 0,
    created_by    TEXT,
    status        TEXT NOT NULL DEFAULT 'open',   -- open | full | departed | cancelled
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pool_lookup ON transport_pools (mandi_id, travel_date, status);

CREATE TABLE pool_members (
    id         BIGSERIAL PRIMARY KEY,
    pool_id    BIGINT REFERENCES transport_pools(id) ON DELETE CASCADE NOT NULL,
    farmer_id  BIGINT REFERENCES farmers(id),
    farmer_name TEXT NOT NULL,
    village    TEXT DEFAULT '',
    qty_qtl    NUMERIC(10,2) NOT NULL CHECK (qty_qtl > 0),
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pool_members ON pool_members (pool_id);

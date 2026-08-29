# DATA-SOURCES.md — where the training data comes from

> Written 27 August 2026, after probing every candidate source live rather than
> reading its documentation. Every number below was measured, not quoted.

---

## The answer, in one line

**CEDA Agri Market Data (Ashoka University) gives us 4.8 years of daily prices
*and* arrivals, for 28 crops across 16 Maharashtra districts, in about 900 API
requests.** It is the source the project should have been built on from the
start, and it was already half-wired in `backend/ingestion/ceda.py`.

```bash
python scripts/fetch_ceda_bulk.py --from 2021-01-01
python scripts/check_data_readiness.py --csv
```

---

## What was wrong before

The repo's `data/raw/mandi_history.csv` held 2,804 rows that looked like two
years of history and were not:

| Month | Rows |
|---|---:|
| 2023-10 | 960 |
| 2024-10 | 895 |
| 2025-09 | 141 |
| 2025-10 | 808 |

**70 distinct dates in a 753-day span** — three October snapshots, onion only,
taken through a portal export capped at 1,000 rows. The feature builder needs 60
real observations inside a 400-day window, so this produced **zero** trainable
rows. Not a small dataset: an empty one.

---

## Sources evaluated

| Source | Verdict | Why |
|---|---|---|
| **CEDA Agri Market Data** | ✅ **chosen** | Daily prices + arrivals, 2018→2025, district granularity, no key needed, ~900 requests for everything |
| data.gov.in Agmarknet API | 🟡 forward feed only | Filters on state + commodity, **no date filter**. Returns the current window. Cannot backfill — keep it for the daily job |
| Agmarknet.gov.in portal | ❌ not needed | HTML scrape of an ASP.NET form; slow, brittle, and CEDA already carries the same DMI data |
| CEDA portal *export button* | ❌ the original trap | Caps exports at 1,000 rows. This is what produced the three October snapshots |
| Kaggle mirrors | 🟡 backup only | Several exist (2001–2026, 75M rows). Needs an account, unverifiable provenance, no arrivals guarantee. Use only if CEDA goes down |

**Sources:**
[CEDA Agri Market Data](https://agmarknet.ceda.ashoka.edu.in/) ·
[data.gov.in current daily prices](https://www.data.gov.in/catalog/current-daily-price-various-commodities-various-markets-mandi) ·
[Agmarknet portal](https://agmarknet.gov.in/) ·
[Kaggle: Daily Market Prices of Commodity India 2001–2026](https://www.kaggle.com/datasets/khandelwalmanas/daily-commodity-prices-india) ·
[Kaggle: Daily Wholesale Commodity Prices – India Mandis](https://www.kaggle.com/datasets/ishankat/daily-wholesale-commodity-prices-india-mandis)

---

## The CEDA API, as measured

Two POST endpoints, no authentication, no API key:

```
POST https://agmarknet.ceda.ashoka.edu.in/api/prices
POST https://agmarknet.ceda.ashoka.edu.in/api/quantities

{"state_id":"27","commodity_id":"78","district_id":"521",
 "calculation_type":"d","start_date":"2021-01-01","end_date":"2026-08-27"}
```

`/api/prices` returns `t, cmdty, district_id, district, p_min, p_max, p_modal`.
`/api/quantities` returns the same keys with `qty`. They merge on `(t, district)`.

### The performance finding that mattered

The existing `ceda.py` chunks requests into 6-month windows, on the belief that
long spans time out. **Measured, the opposite is true:**

| Window | Latency | Rows |
|---|---:|---:|
| 6 months | 10–18s (39s under concurrency) | ~180 |
| 12 months | **0.3s** | 357 |
| 24 months | **0.3s** | 695 |
| 68 months | 15s | 1,729 |

Per-request overhead dominates, so chunking multiplies it. Six-month chunking
turned an 896-request job into 8,960 and a 40-minute pull into a **16-hour** one —
measured at 9 requests/minute before it was killed and rewritten.

`scripts/fetch_ceda_bulk.py` therefore asks for the entire span in one request
and bisects only when the server actually refuses it (502/504).

---

## What the data looks like

Density for Pune district, 2021-01 → 2025-10 (4.8 years), daily rows:

| Crop | Rows | Rows/year | Character |
|---|---:|---:|---|
| Tomato | 1,729 | ~360 | year-round vegetable |
| Onion | 1,688 | ~352 | year-round, storable |
| Potato | ~1,690 | ~350 | year-round, storable |
| Pomegranate | 1,249 | ~260 | fruit, spread over 3 bahars |
| Banana | ~1,100 | ~230 | fruit, year-round |
| Grapes | ~950 | ~200 | fruit, Jan–Apr concentrated |
| **Mango** | **387** | **~81** | fruit, Mar–Jun only |

**This confirms the vegetables-vs-fruits split in [MODEL.md](MODEL.md)
empirically.** Mango's 81 rows/year is almost exactly the ~85 business days that
document predicted from its March–June marketing window — which is why fruits
need 3–4 calendar years to give what a vegetable gives in 2.

Arrivals (`arrival_qtl`) are present on **92–96%** of rows, so the whole arrivals
feature group is live rather than all-NaN.

---

## What actually landed — end to end, 27 August 2026

The full chain was run, not just the fetch:

| Stage | Result |
|---|---|
| CEDA pull, 4 districts × 28 crops × 4.8 yr | **121,410 rows** (185 requests, 0 failures) |
| Ingested to Postgres (13 configured crops) | **50,604 price observations**, 91.6% with arrivals |
| Weather backfill (Open-Meteo) | **35,292 rows**, all 17 mandis |
| Soil & ET0 backfill (Open-Meteo, Phase 14) | **7,922 rows** from Jun 2025, all 17 mandis |
| Training matrix | **176,221 rows × 45 features** — all gates passed |
| Baseline floor recorded | `baseline-v1`, 50 series, in `model_registry` |

Per district, 2021-01-01 → 2025-10-30:

| District | Crops | Observations |
|---|---:|---:|
| Pune | 13 | 14,364 |
| Nashik | 13 | 12,315 |
| Ahmednagar | 13 | 12,015 |
| Solapur | 12 | 11,910 |

Measured across all 28 crops the same data supports **445,576** training rows;
the 176,221 above is the 13 crops that have `crops.yaml` entries.

### The baseline floor, recorded before the model was trained

This is the number LightGBM has to beat, and it was written down first — on
purpose, because a benchmark recorded after seeing the challenger's score is not
a benchmark.

| h | MAPE% | PICP | dir. acc | pinball ₹ | vs naive |
|---|---:|---:|---:|---:|---:|
| 1 | 21.26 | 0.798 | 0.697 | 249.93 | +0.041 |
| 3 | 23.36 | 0.788 | 0.666 | 310.13 | +0.069 |
| 7 | 26.85 | 0.788 | 0.660 | 375.38 | +0.065 |
| 15 | 33.11 | 0.792 | 0.619 | 478.71 | +0.032 |

PICP sits at 0.79 against a 0.80 target — the seasonal-naive bands are already
honest, which is exactly what makes them a fair opponent.

---

## Verified result — Pune district alone

Run through `scripts/check_data_readiness.py`, which applies the *exact* two
rules `build_features()` applies (60 real observations inside a 400-day window,
and a settleable label at each horizon):

```
  series measured        28
  series producing rows  28
  crops / districts      28 / 1
  training matrix rows   153,369   (gate: 20,000)

  ✅ READY.
```

**7.7× the Phase B1 gate from one district.** Density runs 0.96–0.98 observations
per calendar day — essentially every trading day — with a longest gap of 11 days
on most crops and 32 on onion. Every one of the 28 crops clears its duration
floor, including the seasonal fruits: mango at 4.0 years against a 3.0-year
floor, grapes/orange/pomegranate at 4.7–4.8.

Adding Nashik, Ahmadnagar and Solapur multiplies this roughly fourfold.

### Which crops actually get trained on

Of the 28 crops pulled, **13 map to `config/crops.yaml` and resolve cleanly**
through entity resolution: onion, potato, garlic, tomato, brinjal, cauliflower,
green chilli, okra *(from "Bhindi (Ladies Finger)")*, banana, mango, grapes,
orange, pomegranate.

The other 15 are pulled and cached but **dropped at ingestion**, because they
have no `crops.yaml` entry and therefore no `k_c`, shelf life or max-hold. That
is deliberate. Adding them would roughly double the training rows, but it would
mean inventing spoilage constants that drive rupee figures shown to a farmer —
and the plans are explicit that a wrong `k_c` produces confident nonsense no
model can rescue. The data is cached and free to enable the moment someone does
the agronomy properly.

Worth noting that entity resolution **refused** every bad fuzzy match rather than
guessing: Apple→Grapes (54.5), Beans→Banana (54.5) and Pumpkin→Phulkobi (53.3)
were all rejected under the 90-point threshold. No silent mis-mapping.

---

## The catalogue

Enumerated by probing IDs against the live API and reading the `cmdty` name back
off each reply — not guessed. Stored in `config/sources.yaml → ceda`.

**Our 14 configured crops:** onion 23 · potato 24 · garlic 25 · tomato 78 ·
brinjal 35 · cauliflower 34 · green chilli 87 · okra 85 *(CEDA: "Bhindi")* ·
banana 19 · mango 20 · grapes 22 · orange 18 · pomegranate 190.

**Cabbage is not carried by CEDA** under any id found in a 1–280 sweep. It is
left unmapped rather than pointed at something close — a wrong mapping is worse
than a gap.

**Bonus crops pulled anyway** (they cost nothing and give the global model more
rows to borrow strength from): apple 17 · pineapple 21 · papaya 72 · watermelon
73 · chikoo 71 · guava 185 · musk melon 187 · bitter gourd 81 · bottle gourd 82 ·
pumpkin 84 · beans 94 · green peas 50 · green ginger 103 · coriander leaves 43 ·
methi leaves 46.

**16 Maharashtra districts** (state_id 27): Pune 521 · Nashik 516 ·
Ahmadnagar 522 · Solapur 526 · Kolhapur 530 · Sangli 531 · Satara 527 ·
Aurangabad 515 · Nagpur 505 · Mumbai 519 · Thane 517 · Amravati 503 ·
Buldana 500 · Chandrapur 509 · Osmanabad 525 · Raigarh 520.

Pune is the anchor: it carries **52 commodities**, the widest fruit-and-vegetable
trade in the state.

---

## The one honest limitation — say this to the judges

**CEDA is district granularity, not market granularity.** Each row is a
district's daily aggregate across its market yards, so one `(district, crop)`
pair is one series and the district name is written as the mandi name.

Why this is defensible rather than a fudge:

- The **forecast** is a district-level price signal, which is what a farmer's
  hold-or-sell decision actually turns on. Price moves are regional; two yards
  40 km apart move together.
- The **economics** — commission, cess, hamali, the diesel to reach *that* yard,
  spoilage over the days held — stay genuinely market-level, because they are
  arithmetic over `config/mandis.yaml`, not model output. The compare page is
  unaffected.
- Arrivals come from the same aggregate, so the supply signal stays consistent
  with the price signal.

If a judge asks "is this market-level?", the answer is: *the forecast is
district-level and the money is market-level, and we can show you exactly where
the line is.* That is a much better answer than a market-level model trained on
70 dates.

---

## Soil & groundwater (Phase 14)

Same provider, same requests — Open-Meteo serves soil moisture and reference
evapotranspiration from the *identical* archive and forecast calls we already
made for rainfall and temperature, so this stream cost **zero extra API
requests**. Four daily variables were added to `config/sources.yaml`:

| Variable | Column | What it is |
|---|---|---|
| `soil_moisture_0_to_7cm_mean` | `soil_moisture_surface` | Dries first; drives crusting |
| `soil_moisture_7_to_28cm_mean` | `soil_moisture_root` | **The zone a crop drinks from** |
| `soil_temperature_0_to_7cm_mean` | `soil_temp_c` | Surface soil temperature |
| `et0_fao_evapotranspiration` | `et0_mm` | FAO-56 reference ET, mm/day |

Both endpoints serve all four; verified against the live API before the schema
was changed.

**The measured distribution, and why it matters.** Across 7,905 observed days
the root-zone series is bimodal:

| | Value |
|---|---:|
| Monsoon plateau (Jul–Sep) | **0.430** |
| Dry-season plateau (Dec–May) | **0.268** |
| p05 / p25 / p50 / p85 | 0.180 / 0.279 / 0.313 / 0.461 |
| Observed min / max | 0.123 / 0.517 |

This is a **higher scale than soil-physics tables assume** for a medium loam,
and it never approaches the textbook wilting point of 0.12. Thresholds
calibrated from a table therefore misclassify the dry season as wet — which is
exactly what happened, and is written up in
[STATUS.md](STATUS.md#phase-14--soil--groundwater). The thresholds in
`config/irrigation.yaml` are now percentiles of this series, re-derivable with
`python scripts/calibrate_soil.py`.

**Crop coefficients** are FAO-56 Table 12 mid-season values (Kc_mid), one per
configured crop. Pomegranate has no table row; its 0.90 is the midpoint of the
comparable citrus/stone-fruit range and is flagged as an assumption everywhere
it is used.

**This stream does not feed the price model.** It is a second question answered
from data already fetched, not an addition to the forecast feature set.

---

## Reproducing the pull

```bash
# everything: 16 districts x 28 crops x 4.8 years  (~900 requests, ~30 min)
python scripts/fetch_ceda_bulk.py --from 2021-01-01 --workers 8

# just the anchor district, if you are short of time (~60 requests, ~3 min)
python scripts/fetch_ceda_bulk.py --from 2021-01-01 --districts Pune

# see the plan without fetching
python scripts/fetch_ceda_bulk.py --dry-run
```

Every window is cached to `data/artifacts/ceda_cache/` before it is parsed, so a
throttle, a Ctrl-C or a dropped connection costs nothing already fetched. Re-run
freely.

Output goes to `data/raw/mandi_history.csv` in the canonical schema
`sources.yaml → csv_backfill` already expects, so the existing cleaning,
entity-resolution and upsert path takes it unchanged.

**Be a good citizen.** This is a public research server run by a university, not
a commercial API. 8 workers with a 0.25s pause is enough; do not raise it.

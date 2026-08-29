# SETUP.md — from `git clone` to a running product

> Written for someone with an empty machine and no context. Every step says what
> it does and roughly how long it takes.
>
> **The repo carries the trained model and the price data**, so you do not have
> to re-pull four years of CEDA history or retrain anything. Total setup is about
> ten minutes, nearly all of it `pip install`.

---

## What you need first

| | |
|---|---|
| **Docker Desktop** | running — Postgres and Redis live in it |
| **Python 3.10+** | `python --version` |
| **Node 18+** | `node --version` |

---

## The five commands

```bash
git clone https://github.com/sanskarjoshiii/bhav-setu-main.git
cd bhav-setu-main

cp .env.example .env      # then edit it — see "The .env file" below

make install              # python venv + backend deps      (~4 min)
cd frontend && npm install && cd ..                        # (~2 min)

make up                   # Postgres on 5433, Redis on 6380  (~30 s)
make setup                # schema, data, model, demo rows   (~3 min)
```

Then two terminals:

```bash
make api                  # http://localhost:8000/docs
make web                  # http://localhost:3000
```

Check it worked:

```bash
curl localhost:8000/api/v1/health
```

You should see `"provider":"lightgbm"` and `"priceRows":50600`.

---

## The `.env` file

`.env` is gitignored — it never travels with the repo. Copy the example and fill
in the two lines that matter:

```bash
DATABASE_URL=postgresql+psycopg://bhav:bhav@localhost:5433/bhav
REDIS_URL=redis://localhost:6380/0
```

Those match `docker-compose.yml`, so they work unedited.

The `DATA_GOV_IN_API_KEY` and WhatsApp lines can stay blank. Nothing in the
demo path needs them — the data is already in the repo.

⚠️ **`DATABASE_URL` is also the signing secret** for session tokens and the OTP
hash. If you change it, everyone's sessions are invalidated. That is fine on
your own machine; just do not be surprised.

---

## What `make setup` actually does

Four steps, and you can run them individually if one fails:

```bash
python scripts/init_db.py --force        # 19 tables, seeds crops/mandis/festivals
python scripts/backfill.py --skip-ceda --skip-agmarknet
python scripts/restore_model.py          # registers lgbm-v2 as the active model
python scripts/seed_demo_data.py --reset # demo farmer, sale reports, one pool per district
```

**`init_db.py --force` drops and recreates every table.** It does not touch
`data/`, so re-running it costs you the backfill (about two minutes), not the
data itself.

**`backfill.py` reads `data/raw/mandi_history.csv`** — already in the repo — and
runs it through cleaning, entity resolution and the upsert. It also fetches
weather from Open-Meteo (free, no key) and warms the road-distance cache from
OSRM. Both are the only steps that need internet.

**`restore_model.py`** is the one that is easy to miss. Git carries the twelve
boosters in `data/artifacts/models/lgbm-v2/`, but it cannot carry the
`model_registry` table — and without a row there marked active, the API answers
503 on every forecast. This script puts those rows back from
`data/artifacts/model_registry.json`.

---

## What is in the repo, and what is not

| | Size | Why |
|---|---:|---|
| ✅ `data/raw/mandi_history.csv` | 10 MB | the CEDA pull — saves a 40-minute re-fetch |
| ✅ `data/artifacts/models/lgbm-v2/` | 15 MB | the promoted model, 12 boosters + manifest |
| ✅ `data/artifacts/model_registry.json` | 32 KB | the rows Postgres needs |
| ❌ `data/artifacts/train_matrix.parquet` | 13 MB | derived — `build_dataset.py` rebuilds it |
| ❌ `data/artifacts/ceda_cache/` | 33 MB | raw API responses, only useful for a re-pull |
| ❌ `.env` | — | secrets never travel |

**The committed model is the one the accuracy page describes.** If you retrain
you will get a different model, and its numbers will no longer match what is
written in [STATUS.md](STATUS.md).

---

## Verifying it works

```bash
make check-product        # 110 tests: economics, decision, API, auth (~25 s)
```

Do **not** run `make test` before a demo — the full suite is 339 tests and takes
about three and a half hours, because the ingestion and feature tests hammer
Postgres.

A quick manual check of the thing that matters:

```bash
curl "localhost:8000/api/v1/compare?crop=onion&qty_qtl=10"
```

Ahmednagar should come back ranked **4th by gross price and 2nd by net** — the
market with the worse board price paying more in hand. That is the product's
central claim, and it is arithmetic rather than prediction.

---

## Signing in

OTP delivery is set to `channel: log` in `config/locations.yaml`, so **the code
appears on screen** instead of being sent — the WhatsApp Cloud API is not wired
yet. Everything else about the flow is real: expiry, attempt limits, rate
limiting, single use, signed sessions.

1. Go to `/signup`
2. Any 10-digit number works
3. The six-digit code is shown in a dashed box on step 3, and printed in the API log

---

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot reach the server` on every page | API not running | `make api` |
| Every forecast returns 503 | model not registered | `python scripts/restore_model.py` |
| `connection refused` on 5433 | Docker not up | `make up` |
| Health says `"crops":0` | backfill not run | `python scripts/backfill.py --skip-ceda --skip-agmarknet` |
| OTP always says "expired" | Redis not up | `docker compose ps`, then `make up` |
| Demo data looks wrong | someone clicked around | `make reset-demo` |

`POST /api/v1/admin/reset-demo` does the same as `make reset-demo` and is the
fastest way to recover mid-presentation. It never touches real prices or the
model.

---

## Two honest notes

**The backtest uplift is −0.23%**, and the accuracy page shows it with that
sign. The naive baseline is −0.20%, so it is the holding economics rather than
the model. What the product defends is the Net In-Hand calculation and the
market comparison — both arithmetic.

**The forecast is district-level.** CEDA aggregates a district's market yards
into one daily figure, so Lasalgaon and Yeola share Nashik's price in our data.
The live per-market feed exists and is keyed, but gives only today's prices, not
history. Details in [DATA-SOURCES.md](DATA-SOURCES.md).

# SETUP.md — from `git clone` to a running product

> Written for a teammate with an empty machine and no context. Follow it top to
> bottom; every step says what it does, how long it takes, and how to tell it
> worked.
>
> **The repo carries the trained model and the price data**, so you never re-pull
> four years of history or retrain anything. Budget **~15 minutes**, nearly all
> of it `pip install` and `npm install`.

---

## 0. What you need installed

| | Check with | Notes |
|---|---|---|
| **Docker Desktop** | `docker --version` | Must be **running** before step 4. Postgres, Redis and MongoDB all live in it. |
| **Python 3.10+** | `python --version` | 3.10 or 3.11 preferred. |
| **Node 18+** | `node --version` | For the Next.js website. |
| **Git** | `git --version` | |

> **Windows users:** there is no `make` on Windows. Everywhere this guide says
> `make <target>`, run **`.\make.ps1 <target>`** in PowerShell instead. Both are
> listed at each step so you do not have to translate.

---

## 1. Clone the repo

```bash
git clone https://github.com/sanskarjoshiii/bhav-setu-main.git
cd bhav-setu-main
```

---

## 2. Put the `.env` file in place

**Sanskar will send you `.env` separately** — over chat, not through Git. It is
gitignored on purpose, because it holds the Gmail app password used to send
sign-in codes.

Drop it in the **repo root**, next to `README.md`:

```
bhav-setu-main/
├── .env          ←  here
├── README.md
├── docker-compose.yml
└── ...
```

Check it landed:

```bash
# macOS / Linux
ls -la .env

# Windows PowerShell
Get-Item .env
```

> **Do not commit it, and do not paste it into a chat that is logged.** If you
> ever need to rebuild it from scratch, `.env.example` lists every variable with
> a comment explaining it.

⚠️ **`DATABASE_URL` is also the signing secret** for session tokens, the OTP hash
and magic-link tokens. If you change it, every existing session is invalidated.
That is harmless on your own machine — just don't be surprised.

---

## 3. Install dependencies (~6 min)

Two separate installs — Python for the backend, npm for the website.

```bash
# Backend: creates .venv/ and installs from backend/requirements.txt   (~4 min)
make install
#   Windows:  .\make.ps1 install

# Frontend                                                            (~2 min)
cd frontend
npm install
cd ..
```

**Verify:**

```bash
# macOS / Linux
.venv/bin/python -c "import fastapi, lightgbm, pymongo; print('backend deps ok')"

# Windows
.venv\Scripts\python -c "import fastapi, lightgbm, pymongo; print('backend deps ok')"
```

---

## 4. Start the databases (~1 min first time, ~30 s after)

Make sure **Docker Desktop is running**, then:

```bash
make up
#   Windows:  .\make.ps1 up
```

This starts three containers. The first run downloads the MongoDB image
(~250 MB), so give it a minute.

| Container | Port | What it holds |
|---|---|---|
| `postgres` | **5433** | Prices, weather, farmers, lots, pools — the source of truth |
| `redis` | **6380** | OTP codes and magic-link nonces (they expire, so they live here) |
| `mongo` | **27018** | Farmer history as readable documents |

> The ports are deliberately **not** the defaults (5432 / 6379 / 27017) so this
> project never collides with a Postgres or Mongo you already have installed.

**Verify — all three must say `running`:**

```bash
docker compose ps
```

---

## 5. Load the data, model and demo rows (~3 min)

One command does all five steps:

```bash
make setup
#   Windows:  .\make.ps1 setup
```

<details>
<summary>What it actually runs, if you ever need a step on its own</summary>

Note the interpreter: **`.venv/bin/python`**, not bare `python`. The system
Python has none of these dependencies installed.

```bash
# macOS / Linux  (Windows: .venv\Scripts\python)
.venv/bin/python scripts/init_db.py --force                      # 21 tables + reference data
.venv/bin/python scripts/backfill.py --skip-ceda --skip-agmarknet  # prices, weather, soil
.venv/bin/python scripts/restore_model.py                        # registers lgbm-v2 as active
.venv/bin/python scripts/seed_demo_data.py --reset               # demo farmers, reports, pools
.venv/bin/python scripts/backfill_history.py                     # mirrors history into MongoDB
```

- **`init_db.py --force` drops and recreates every table.** It never touches
  `data/`, so re-running costs you the backfill (~2 min), not the data.
- **`backfill.py`** reads `data/raw/mandi_history.csv` (already in the repo) and
  also fetches weather from Open-Meteo — **the only steps that need internet.**
- **`restore_model.py`** is the easy one to miss. Git carries the model files but
  cannot carry the `model_registry` table, and without an active row there the
  API answers **503 on every forecast**.
- **`backfill_history.py`** builds one MongoDB document per farmer so the history
  view is not empty on a fresh clone.

</details>

---

## 6. Run it — two terminals

**Terminal 1 — the API:**

```bash
make api
#   Windows:  .\make.ps1 api
```
→ http://localhost:8000/docs

**Terminal 2 — the website:**

```bash
make web
#   Windows:  .\make.ps1 web
```
→ http://localhost:3000

> ⚠️ **Never run `npm run build` or `make check-frontend` while `make web` is
> running.** They share the `frontend/.next` folder and the build overwrites what
> the dev server is serving — the site loses **all its CSS** and renders as plain
> unstyled text. If that happens, see [Troubleshooting](#9-when-something-breaks).

---

## 7. Verify it actually works

**a) Health check** — the fastest signal that everything is wired:

```bash
curl localhost:8000/api/v1/health
```

Expected:

```json
{"status":"ok","database":true,"provider":"lightgbm",
 "modelVersion":"lgbm-v2","crops":13,"mandis":4,"priceRows":50600}
```

If `provider` is not `lightgbm`, run `make restore-model`
(Windows: `.\make.ps1 restore-model`).

**b) MongoDB history is populated:**

```bash
curl localhost:8000/api/v1/history/store-status
```

Expected: `{"available":true,"farmers":<n>,"events":<n>}` with non-zero counts.

**c) The product's central claim** — the rank flip:

```bash
curl "localhost:8000/api/v1/compare?crop=onion&qty_qtl=10"
```

Ahmednagar should come back **4th by gross price but 2nd by net** — the market
with the worse board price paying more in hand. That is arithmetic, not
prediction, and it is the demo moment.

**d) Test suite** (optional, ~25 s):

```bash
make check-product
#   Windows:  .\make.ps1 check-product
```

> Do **not** run `make test` before a demo. The full suite is 390 tests and takes
> about three and a half hours — the ingestion and feature tests hammer Postgres.

---

## 8. Signing in (how the OTP works)

Go to http://localhost:3000/signup. **There are two ways to get the code:**

### With an email address (the code is really sent)

Fill the optional **Email** field on step 1. The six-digit code is emailed
through the Gmail account in `.env`. The screen then says
*"a six-digit code was emailed to p\*\*\*\*\*0@gmail.com"* and **does not show
the code** — check the inbox.

### Without an email (demo mode)

Leave Email blank. The code appears in a dashed box on step 3 and is printed in
the API log. This is what `otp.channel: log` in `config/locations.yaml` does.

> **There is no SMS.** Nothing sends a code to a phone — that needs a paid
> gateway and, in India, DLT sender-ID registration. Any 10-digit number works
> for signing up; the number is just the account identity.

Everything else about the flow is real: 10-minute expiry, 5 wrong guesses burns
the code, per-number rate limiting, single use, signed sessions.

---

## 9. Viewing the MongoDB history

This is where every farmer's activity is stored — **the thing to show judges.**

### Connection details

| | |
|---|---|
| **Connection string** | `mongodb://bhav:bhav@localhost:27018/?authSource=admin` |
| **Database** | `bhav_history` |
| **Username / password** | `bhav` / `bhav` |
| **Port** | `27018` (not the default 27017) |

> `authSource=admin` is **required** — leave it off and you get an auth failure.

### Option A — MongoDB Compass (the nice GUI, best for judges)

1. Download from **https://www.mongodb.com/products/compass** (free) and install.
2. Open it. On the connect screen paste:
   ```
   mongodb://bhav:bhav@localhost:27018/?authSource=admin
   ```
3. Click **Connect**.
4. In the left sidebar open **`bhav_history`**. Two collections:

   | Collection | What is in it |
   |---|---|
   | **`farmers`** | One document per farmer — id, name, phone, email, village, district, lat/lon, language, risk profile, home market, and a count of everything they have done |
   | **`events`** | One document per action — signup, login, recommendation, sale report, pool join, irrigation advice — each with a plain-English `summary` and a **copy of the farmer's details** so it reads on its own |

5. Click a collection to browse. Useful filters to paste into the **Filter** box:

   ```js
   // everything one farmer has ever done, newest first
   { farmerId: 1 }

   // only the selling advice we gave
   { type: "recommendation" }

   // farmers from one district
   { district: "Nashik" }
   ```

   In `events`, sort by `{ at: -1 }` to get newest-first.

### Option B — the terminal (no install needed)

```bash
make mongo-shell
#   Windows:  .\make.ps1 mongo-shell
```

That drops you into `mongosh` already connected. Then:

```js
show collections                      // farmers, events

db.farmers.countDocuments()           // how many farmers we hold history for
db.events.countDocuments()            // how many recorded actions

// one farmer, fully
db.farmers.findOne({ name: "Ramesh Pawar" })

// their timeline, newest first
db.events.find({ farmerId: 1 }).sort({ at: -1 }).limit(10).pretty()

// what kinds of events exist, and how many of each
db.events.aggregate([{ $group: { _id: "$type", n: { $sum: 1 } } }])
```

Type `exit` to leave.

### Option C — through the API (no Mongo tooling at all)

```bash
# every farmer we hold history for, with their counts
curl localhost:8000/api/v1/history/farmers

# one farmer: profile + full timeline
curl localhost:8000/api/v1/history/farmers/1
```

Or open http://localhost:8000/docs and try them in the browser — easiest for a
live demo.

### Rebuilding the history

Documents are a **mirror**; Postgres is the source of truth. Safe to re-run any
time:

```bash
make history-backfill
#   Windows:  .\make.ps1 history-backfill
```

Run this after `make reset-demo`, or if MongoDB was down while someone was using
the site. It adds what is missing and prunes documents whose Postgres farmer is
gone.

---

## 10. When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| **The whole site is unstyled** — serif text, no colours | `npm run build` ran while the dev server was up and clobbered `.next` | Stop `make web`, delete `frontend/.next`, run `make web` again |
| `Cannot reach the server` on every page | API not running | `make api` |
| Every forecast returns **503** | Model not registered | `make restore-model` |
| `connection refused` on 5433 | Docker not up | `make up` |
| Health says `"crops":0` | Backfill not run | `make setup` |
| OTP always says "expired" | Redis not up | `docker compose ps`, then `make up` |
| History endpoints return **422** "not reachable" | MongoDB not up | `docker compose up -d mongo` |
| Mongo history is empty | Never backfilled | `make history-backfill` |
| Compass says authentication failed | Missing `authSource=admin` | Use the full connection string above |
| Emails not arriving | `SMTP_*` blank in `.env`, or Gmail app password revoked | Check `.env`; the code then falls back to on-screen demo mode |
| Demo data looks wrong | Someone clicked around | `make reset-demo`, then `make history-backfill` |
| `Unknown target` from `make.ps1` | Typo, or a Makefile-only target | Run `make.ps1` targets listed in this guide |

`POST /api/v1/admin/reset-demo` does the same as `make reset-demo` and is the
fastest way to recover mid-presentation. It never touches real prices or the
model.

---

## 11. What is in the repo, and what is not

| | Size | Why |
|---|---:|---|
| ✅ `data/raw/mandi_history.csv` | 10 MB | The CEDA pull — saves a 40-minute re-fetch |
| ✅ `data/artifacts/models/lgbm-v2/` | 15 MB | The promoted model, 12 boosters + manifest |
| ✅ `data/artifacts/model_registry.json` | 32 KB | The rows Postgres needs to serve it |
| ❌ `data/artifacts/train_matrix.parquet` | 13 MB | Derived — `build_dataset.py` rebuilds it |
| ❌ `data/artifacts/ceda_cache/` | 33 MB | Raw API responses, only useful for a re-pull |
| ❌ `.env` | — | Secrets never travel through Git |

**The committed model is the one the accuracy page describes.** If you retrain,
you get a different model and its numbers will no longer match
[STATUS.md](STATUS.md).

---

## 12. Two honest notes for the demo

**The backtest uplift is −0.23%**, and the accuracy page shows it with that sign.
The naive baseline is −0.20%, so it is the holding economics rather than the
model. What the product defends is the Net In-Hand calculation and the market
comparison — both arithmetic, not prediction.

**The forecast is district-level.** CEDA aggregates a district's market yards
into one daily figure, so Lasalgaon and Yeola share Nashik's price in our data.
The live per-market feed exists and is keyed, but gives only today's prices, not
history. Details in [DATA-SOURCES.md](DATA-SOURCES.md).

---

## Quick reference

```bash
make up                # start Postgres + Redis + MongoDB
make setup             # schema, data, model, demo rows, history  (one time)
make api               # backend  -> localhost:8000/docs
make web               # website  -> localhost:3000
make check-product     # the 110 tests that matter (~25 s)
make reset-demo        # put demo data back to its starting state
make history-backfill  # rebuild MongoDB history from Postgres
make mongo-shell       # open a Mongo shell on bhav_history
make soil              # refresh soil moisture / ET0 from Open-Meteo
make down              # stop the containers
```

On Windows, prefix every one of those with `.\make.ps1` instead of `make`.

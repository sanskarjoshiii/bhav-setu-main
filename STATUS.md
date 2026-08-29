# STATUS.md — Where Bhav Setu actually stands

> Updated 29 August 2026. Every claim here was verified by running it.
> **368 backend tests collected, 2 skipped.** The full suite last ran green at
> 339; Phase 14 added 27, and those 27 plus the 95-test product core
> (economics, decision, API, schema) and the ingestion suite were re-run for
> this push. Frontend builds clean, **19 routes**.

---

## The one-paragraph answer

**The product is real end to end.** Real government mandi data in Postgres, a
trained LightGBM quantile model serving through the port, the Net In-Hand
economics and the decision engine in Python, a FastAPI backend behind every
page, and the website reading from it instead of inventing numbers. The
WhatsApp agent is the one headline feature not built — it was explicitly out of
scope for this push.

**One finding we are not hiding: the backtest uplift is negative (−0.23%).** The
timing advice did not beat selling immediately on the held-out window, and the
naive baseline does not either (−0.20%), so it is the holding economics rather
than the model. What we can defend is the economics and the market comparison,
which are arithmetic, not prediction. Details in [the backtest section](#phase-7--backtest).

---

## Scoreboard

| Phase | What it is | State |
|---|---|---|
| **1** | Real data | ✅ 50,600 observations, 13 crops × 4 districts × 4.8 yr |
| **2** | Multi-crop DB + cleaning | ✅ 13 crops seeded, audit per district × crop |
| **3** | Feature builder | ✅ 45 features, leakage-tested, 176,221-row matrix |
| **4** | Trained model | ✅ `lgbm-v2` promoted — beats baseline ~40% on pinball |
| **5** | Economics in Python | ✅ Ported exactly, 26 tests, rank-flip guaranteed |
| **6** | Decision engine | ✅ Convexity fix + 6 constraints, 30 tests |
| **7** | Backtest | ✅ Built and run — **uplift is negative, reported honestly** |
| **8** | FastAPI backend | ✅ 20 endpoints, 25 tests |
| **9** | Website on real data | ✅ Every number page rewired; build clean |
| **10** | Real OTP accounts | ✅ OTP, sessions, locations — 29 tests |
| **11** | WhatsApp agent | ❌ **Explicitly excluded from this push** |
| **12** | Community pooling | ✅ Tables, API, page — **filtered by district** |
| **13** | Deploy + seed + reset | 🟡 Seed and reset done; deploy files not written |
| **14** | Soil & groundwater | ✅ Ingested, advisory, `/irrigation` page — 27 tests |

---

## What runs right now

```bash
make up          # Postgres + Redis
make api         # http://localhost:8000/docs
make web         # http://localhost:3000
make test         # 339 passed, 2 skipped (~3.5 h)
make check-product # the 110 that matter, in ~25 s
```

`GET /api/v1/health` currently answers:

```json
{"status":"ok","database":true,"provider":"lightgbm",
 "modelVersion":"lgbm-v2","crops":13,"mandis":4,"priceRows":50600}
```

---

## Phase 5 — Economics

`backend/economics/` — `spoilage.py`, `net_realisation.py`, `compare.py`.

Ported from the TypeScript exactly, including the rule that `net_per_qtl`
divides by the **original** quantity so spoilage shows as a lower rate rather
than hiding inside a smaller total. Verified to the rupee against the reference
lot: ₹2,010 board price → **₹1,867.02 in hand** on 80 qtl at 62 km.

**The rank flip is real and guaranteed by a test.** Live output for a 10-quintal
onion lot:

| Mandi | Distance | Gross ₹/qtl | Net ₹/qtl | Gross rank | Net rank |
|---|---:|---:|---:|---:|---:|
| Nashik | 76 km | **1,450** | **1,043** | **1st** | **1st** |
| Ahmednagar | 156 km | 1,131 | 403 | 4th | **2nd** |
| Pune | 242 km | 1,211 | 119 | 2nd | 3rd |
| Solapur | 413 km | 1,167 | −643 | 3rd | 4th |

From Vinchur, a 10-quintal onion lot. **Ahmednagar has the worst board price and
the second-best net** — it overtakes Pune, which quotes ₹80 more, purely on
distance. Solapur goes negative: a whole truck for ten quintals over 413 km
costs ₹1,735/qtl of diesel against a crop worth ₹1,167.

---

## Phase 6 — Decision engine

`backend/decision/` — `engine.py`, `constraints.py`, `confidence.py`, `explain.py`.

**The convexity fix is in and tested.** Scoring is
`e_net − risk_lambda × downside × exposure`, which curves the objective in the
sell fraction so an interior optimum — a split — can actually win. The
anti-vacuity test sweeps 90 combinations and asserts splits are reachable;
**26 of 90 produce genuine two-tranche splits.**

Worth knowing: splits are common at 180 qtl (two trucks are needed anyway) and
rare at 80 qtl (splitting pays for two half-empty trucks). That is correct
economics, not a bug.

All six constraints have a test proving each one fires. Perishable crops
(tomato, okra, banana, cauliflower) are never told to hold.

---

## Phase 7 — Backtest

**This is the uncomfortable section, and it stays uncomfortable.**

520 scenarios over a 150-day held-out window, each built by the **live**
`optimise()` and settled on prices that actually happened:

| | LightGBM | Baseline |
|---|---:|---:|
| Uplift | **−0.23%** | −0.20% |
| Win rate | 26.2% | 29.2% |
| Actions | 409 sell-now, 24 split, 18 hold | 301 / 19 / 5 |

**What it means.** The engine says sell-now ~90% of the time, which is the right
call and ties with the baseline exactly. On the ~10% where it deviates, it
slightly loses. Since the naive baseline is negative too, this is the **holding
economics** — spoilage plus interest plus a second truck — not a model defect.

A diagnostic found the model over-predicts onion by **+4.0% at h=7 and +8.3% at
h=15**, exceeding the actual price 70–80% of the time in this window. That is
regime drift: prices fell through the held-out period and a model trained mostly
on earlier data kept expecting the old level.

**We publish this on the accuracy page with its real sign.** The landing page
leads with metrics that are true and verified — the model's 40% pinball
improvement and its honest band coverage — rather than promoting a negative
uplift to a headline.

---

## Phase 8 — FastAPI backend

20 endpoints, 25 tests, all serving from Postgres:

`/health` · `/mandis` · `/districts` · `/crops` · `/prices/today` ·
`/prices/series` · `/forecast` · `/recommend` · `/compare` · `/accuracy` ·
`/accuracy/versions` · `/sale-reports` (GET+POST) · `/transparency` ·
`/history` (GET+POST) · `/pools` (GET+POST+join+leave) · `/admin/reset-demo`

**The port stayed sealed.** A test greps every consumer directory and fails if
anything imports LightGBM or opens a booster. `routers/accuracy.py` imports
`ml.registry` for version metadata — that is allowed and documented, because it
reads Postgres rows rather than the model.

`InsufficientData` becomes a **422 with a readable sentence**, never a 500. Ask
for a crop we do not carry and you get *"unknown crop 'dragonfruit'. Known
crops: …"*, which the UI shows verbatim.

---

## Phase 9 — Website on real data

`frontend/lib/api.ts` became `fetch` calls and **no component changed** — the
single-seam design paid off exactly as the plan predicted. The advisor and
compare pages needed no data changes at all.

| Page | State |
|---|---|
| Dashboard | ✅ real prices, districts, crops, forecast chart |
| Advisor | ✅ real recommendation + error state |
| Compare | ✅ real rank-flip table |
| Accuracy | ✅ real metrics, real per-crop backtest, real coverage curve |
| Transparency / Reports | ✅ real sale reports, totals derived from them |
| Community | ✅ real pools, real saving |
| Home | ✅ real headline metrics |
| Chat | 🟡 still scripted — the WhatsApp agent is out of scope |

**Three fabricated charts were deleted from the accuracy page.** A monthly bar
chart showing the strategy beating the baseline every month, an invented
calibration curve, and a scatter of random numbers — on a page titled *"How
wrong are we, honestly?"*. They are replaced with the real per-crop backtest and
the real coverage-per-horizon curve. `lib/mock/accuracy.ts` is deleted.

Mock files that legitimately remain: `crops.ts` (emoji, Marathi names,
categories — display metadata the plan says to keep), `chat.ts` (the excluded
agent), `mandis.ts` (used only by the map component), `recommendation.ts`
(a form default).

---

## Phase 10 — Accounts, locations and OTP

`backend/auth/` — `otp.py`, `session.py`; `api/routers/auth.py`, `locations.py`;
`config/locations.yaml`.

**Registration and login are one flow.** A farmer types his number, gets a code,
and supplies his name and village at the same moment if we have not seen him.
Asking him to choose "sign up" or "sign in" is a question about a service he has
not used yet.

### Locations — 4 districts, 10 villages each

Only the districts we hold prices for. Offering a village in Kolhapur would take
a registration and then have nothing to say. Each village carries real
coordinates and its road distance to that district's market, shown live in the
form as he picks — *"About 128.3 km to Ahmednagar market."*

### The OTP, and why each rule is there

| Rule | Why |
|---|---|
| Ten-minute TTL, enforced by Redis | not a timestamp we must remember to check |
| Five wrong guesses, then burned | six digits falls to a script in under a minute |
| Single use — deleted on success | a code read over a shoulder is not a permanent key |
| Ten requests/hour per number | nobody can pump a stranger's phone |
| 60-second resend cooldown | "resend" should say wait, not eat the hourly budget |
| Stored as an HMAC, never plaintext | one breach should not become account takeover |

`otp.channel: log` returns the code in the response so the demo needs no phone;
`whatsapp` returns nothing and delivers for real. One line in config, and
`verify` behaves identically either way.

### Sessions

Compact signed tokens — payload, expiry, HMAC-SHA256. A farmer cannot edit his
own id and read someone else's lots; a tampered or expired token is a **401** and
the frontend signs him out rather than showing a half-broken page.

**Verified in a browser, end to end:** register → OTP → advisor → community →
home, with zero console errors. The session survives navigation and the UI
switches to Marathi when he registers with `language: mr`.

---

## Phase 12 — Community pooling

`transport_pools` and `pool_members` tables, `backend/community/pools.py`, REST
endpoints. Capacity is enforced — joining beyond the truck is a 400 with
*"only N qtl of space left"*. Leaving frees the capacity and recalculates for
everyone remaining.

**Filtered by the farmer's district.** A signed-in farmer sees trucks leaving
from where he lives, with an "All districts" toggle. One open pool is seeded per
district, so whichever district a judge registers in has something to show.

Live, Ahmednagar: **44.7 km, ₹188 alone → ₹75 pooled, ₹113 saved per quintal.**

Two bugs found by running it and fixed:

- **Every pool showed ₹0 saving.** `REFERENCE_LAT/LON` sat on Ahmednagar's exact
  market coordinates, so distance was zero and transport was free. It is now
  Vinchur — a real village, deliberately not a market yard — and a district's
  pool measures village-to-market inside that district.
- **Pools accumulated across test runs** (19 in Pune). The API tests now clean up
  after themselves, and `reset-demo` clears every pool.

---

## Phase 14 — Soil & groundwater

The second decision the product makes. Prices answer *when to sell*; this
answers *when to irrigate*, from the same Open-Meteo pull — so the whole stream
costs **no extra API requests**.

`config/irrigation.yaml`, `backend/agronomy/irrigation.py`,
`GET /api/v1/irrigation`, and the `/irrigation` page.

**What was added to the data.** Four columns on `weather_daily` —
`soil_moisture_surface` (0–7 cm), `soil_moisture_root` (7–28 cm),
`soil_temp_c`, and `et0_mm` (FAO-56 reference evapotranspiration). Backfilled
from June 2025: **7,922 rows across all 17 mandis**, of which 7,905 carry a
root-zone reading.

**The arithmetic** is FAO-56's simplest form — crop demand is `Kc × ET0`, and
irrigation must supply whatever the rain did not. Growth stages are deliberately
not modelled, because we do not know a sowing date; mid-season Kc over-states
demand for a young crop, and the page says so instead of implying a precision
it does not have.

Live for onion at Nashik, through the year:

| As of | Soil | Status | Shortfall | Forecast | Verdict |
|---|---:|---|---:|---:|---|
| 15 Nov | 0.309 | adequate | 37 mm | 0 mm | Plan to irrigate |
| 10 Mar | 0.320 | adequate | 52 mm | 0 mm | Plan to irrigate |
| 20 May | 0.273 | dry | 62 mm | 18 mm | Irrigate within a day or two unless the rain arrives |
| 20 Jul | 0.391 | wet | 0 mm | 213 mm | Hold off — rain is coming |

### The bug that made this worth doing carefully

The first cut used textbook medium-loam thresholds — field capacity 0.30,
wilting point 0.12. They are correct soil physics and **wrong for this data
source**. Open-Meteo's ERA5 7–28 cm layer over the Deccan sits on a higher
scale: across 7,905 observed days the series is bimodal, with a monsoon plateau
at **0.430** and a dry-season plateau at **0.268**, and it never once reaches
0.12.

So the *dry season* sat above the configured field capacity, and Nashik in
March — **0.2 mm of rain all month against 6.2 mm/day of ET0** — was reported as
*"the soil is still wet, no irrigation needed."* That is the most harmful
sentence this advisory could produce.

The thresholds are now derived from the ingested distribution rather than a
textbook: wilting point = p05, refill point = p25, field capacity = midway
between the two plateaus, saturation = p85. `scripts/calibrate_soil.py`
re-derives them and `make calibrate-soil` fails if the config has drifted.
`test_a_dry_season_deficit_is_never_called_wet` pins the behaviour across six
dry-season dates.

### Honesty

- **Soil moisture is modelled at the market's coordinates, not measured in a
  field.** Good enough to say "dry" or "wet"; not good enough to schedule a pump
  to the hour. The page says this.
- **Pomegranate has no FAO-56 row.** Its Kc of 0.90 is the midpoint of the
  comparable range, flagged as an assumption through the API (`kcIsAssumed`) and
  capped at medium confidence.
- **This stream is deliberately not wired into the price model.** Soil moisture
  in Nashik does not move the onion price in any way we could defend, and adding
  it would invalidate the trained model for no gain. A test asserts it has not
  leaked into `features/registry.py`.

`make soil` refreshes it — worth a weekly cron, since the forecast half of the
window expires within sixteen days.

## Bilingual output

Both languages are generated end to end, with Devanagari numerals:

```
EN: Sell all 80 qtl of onion today at Nashik — about ₹1,267/qtl in hand
MR: सर्व 80 क्विंटल कांदा आजच Nashik येथे विका — हातात सुमारे ₹1,267/क्विंटल
    reason: आवक नेहमीपेक्षा १५% कमी आहे
```

---

## What is genuinely not done

1. **WhatsApp agent** (Phase 11) — excluded from this push by request.
2. **OTP delivery** — the flow, rate limiting and sessions are real and tested;
   the code is shown on screen (`otp.channel: log`) rather than sent, because
   real delivery rides on the WhatsApp Cloud API. Flipping that is one config
   line once Phase 11 lands.
3. **Deploy files** (Phase 13) — `docker-compose.prod.yml`, `daily_job.py` and
   `RUNBOOK.md` are not written. Seeding and the reset endpoint **are** done.
4. **The backtest uplift is negative.** Stated plainly above rather than buried.
5. **Village coordinates are town centroids**, to about three decimals. They
   drive transport cost, so a 2–3 km error moves a 60 km trip by a few percent.
   Fine for ranking markets; replace with surveyed points before quoting a rupee
   figure as exact.
6. **A district has one market in our data**, so two farmers in different
   talukas of the same district see the same price. Real intra-district spread
   is around ₹600/qtl for onion — see [DATA-SOURCES.md](DATA-SOURCES.md).

---

## Demo-day commands

```bash
make up                       # infrastructure
make api                      # backend
make web                      # website
make reset-demo               # put the demo back to its starting state
curl localhost:8000/api/v1/health   # is everything wired?
```

If something breaks on stage, `POST /api/v1/admin/reset-demo` restores the demo
rows without touching real prices or the trained model.

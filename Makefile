PY ?= python
VENV := .venv
ifeq ($(OS),Windows_NT)
	BIN := $(VENV)/Scripts
else
	BIN := $(VENV)/bin
endif

.PHONY: up down install initdb backfill collect train train-dry evaluate-baseline backtest api web seed test \
        check-product reset-demo check-phase10 \
        check-data check-data-csv build-dataset check-phaseB2 \
        check-phase0 check-phase1 check-phase2 check-phase3 check-phase4 \
        check-phaseA0 check-phaseA1 check-phaseA2 check-phaseA3 \
        check-phase5 check-phase6 check-phase7 check-phase8 check-phase9 \
        check-frontend check-phase11 check-phase12

# ── infrastructure ─────────────────────────────────────────────────────────
up:
	docker compose up -d
	docker compose ps

down:
	docker compose down

install:
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -r backend/requirements.txt

# ── pipeline ───────────────────────────────────────────────────────────────
initdb:
	$(BIN)/python scripts/init_db.py --force

backfill:
	$(BIN)/python scripts/backfill.py

# The daily forward feed (Phase A1). Point cron at this; it resumes from its
# own page cache, so re-running after a throttle costs nothing.
collect:
	$(BIN)/python scripts/collect_daily.py --once

# Before anything else in the model track: how far is the data from trainable?
# Works with Postgres down (--csv), so there is no excuse not to run it.
check-data:
	$(BIN)/python scripts/check_data_readiness.py

check-data-csv:
	$(BIN)/python scripts/check_data_readiness.py --csv --verbose

# Phase B1 — build the matrix and refuse to write a bad one.
build-dataset:
	$(BIN)/python scripts/build_dataset.py --from 2022-01-01

# Phase B2/B3 — train, score, then run the promotion gate. Note: this only
# promotes if the model beats the recorded baseline, so `make evaluate-baseline`
# has to have run at least once or the gate refuses outright.
train:
	$(BIN)/python scripts/train.py --from 2022-01-01 --promote

# Same, without touching model_registry or the artifacts dir.
train-dry:
	$(BIN)/python scripts/train.py --from 2022-01-01 --dry-run

# Swap day's gate: the SAME contract file the baseline passed, unmodified.
check-phaseB2:
	cd backend && ../$(BIN)/python -m pytest tests/test_phaseA0_port.py --provider lightgbm -v

evaluate-baseline:
	$(BIN)/python scripts/evaluate_baseline.py

backtest:
	$(BIN)/python scripts/backtest.py

seed:
	$(BIN)/python scripts/seed_demo_data.py

# ── servers ────────────────────────────────────────────────────────────────
api:
	cd backend && ../$(BIN)/python -m uvicorn api.main:app --reload --port 8000

web:
	cd frontend && npm run dev

# ── tests ──────────────────────────────────────────────────────────────────
test:
	cd backend && ../$(BIN)/python -m pytest

# ── Track A: the model-independent product (see PLAN-NOMODEL.md) ───────────
check-phaseA0:
	cd backend && ../$(BIN)/python -m pytest tests/test_phaseA0_port.py -v

check-phaseA1:
	cd backend && ../$(BIN)/python -m pytest tests/test_phaseA1_collect.py -v

check-phaseA2:
	cd backend && ../$(BIN)/python -m pytest tests/test_phaseA2_crops.py -v

check-phaseA3:
	cd backend && ../$(BIN)/python -m pytest tests/test_phaseA3_baseline.py -v

# ── original phase gates ───────────────────────────────────────────────────
check-phase0:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase0_scaffold.py -v

check-phase1:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase1_schema.py -v

check-phase2:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase2_ingestion.py -v

check-phase3:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase3_features.py -v

check-phase4:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase4_model.py -v

check-phase5:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase5_economics.py -v

# The whole product track in one go: economics, decision, API.
check-product:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase5_economics.py tests/test_phase6_decision.py tests/test_phase8_api.py -v

# Reset the demo to its starting state. Real prices are never touched.
reset-demo:
	$(BIN)/python scripts/seed_demo_data.py --reset

# Phase 10 — OTP, sessions, locations, pooling by district.
check-phase10:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase10_auth.py -v

check-phase6:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase6_decision.py -v

check-phase7:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase7_backtest.py -v

check-phase8:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase8_api.py -v

check-phase9:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase9_bot.py -v

# Phase 9 in PLAN-FINAL is the frontend wiring, and a clean production build is
# its real gate. This was labelled check-phase10, which collided with the OTP
# target above and had nothing to do with auth.
check-frontend:
	cd frontend && npm run build

check-phase11:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase11_whatsapp.py -v

check-phase12:
	cd backend && ../$(BIN)/python -m pytest tests/test_phase12_demo.py -v

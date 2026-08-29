# Windows shim for the Makefile targets (Windows has no `make`).
#   .\make.ps1 up
#   .\make.ps1 initdb
#   .\make.ps1 check-phase1
param([Parameter(Mandatory = $true)][string]$Target)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Need-Venv {
    if (-not (Test-Path $Py)) { throw "No venv found. Run: .\make.ps1 install" }
}

switch ($Target) {
    "up"       { docker compose up -d; docker compose ps }
    "down"     { docker compose down }
    "install"  {
        python -m venv (Join-Path $Root ".venv")
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r (Join-Path $Root "backend\requirements.txt")
    }
    "initdb"   { Need-Venv; & $Py (Join-Path $Root "scripts/init_db.py") --force }
    "backfill" { Need-Venv; & $Py (Join-Path $Root "scripts\backfill.py") }
    "train"    { Need-Venv; & $Py (Join-Path $Root "scripts\train.py") --from 2022-01-01 --promote }
    "backtest" { Need-Venv; & $Py (Join-Path $Root "scripts\backtest.py") }
    "seed"     { Need-Venv; & $Py (Join-Path $Root "scripts/seed_demo_data.py") }
    "api"      { Need-Venv; Push-Location (Join-Path $Root "backend"); try { & $Py -m uvicorn api.main:app --reload --port 8000 } finally { Pop-Location } }
    "web"      { Push-Location (Join-Path $Root "frontend"); try { npm run dev } finally { Pop-Location } }
    "test"     { Need-Venv; Push-Location (Join-Path $Root "backend"); try { & $Py -m pytest } finally { Pop-Location } }

    # One command from a fresh clone to a running product.
    "setup"    {
        Need-Venv
        & $Py (Join-Path $Root "scripts/init_db.py") --force
        & $Py (Join-Path $Root "scripts/backfill.py") --skip-ceda --skip-agmarknet
        & $Py (Join-Path $Root "scripts/restore_model.py")
        & $Py (Join-Path $Root "scripts/seed_demo_data.py") --reset
        & $Py (Join-Path $Root "scripts/backfill_history.py")
        Write-Host ""
        Write-Host "  Ready. Now:  .\make.ps1 api    (and in another terminal)  .\make.ps1 web"
        Write-Host "  Check it:    curl localhost:8000/api/v1/health"
        Write-Host ""
    }
    "reset-demo"       { Need-Venv; & $Py (Join-Path $Root "scripts/seed_demo_data.py") --reset }
    "restore-model"    { Need-Venv; & $Py (Join-Path $Root "scripts/restore_model.py") }
    # Phase 14 — refresh soil moisture / ET0 from Open-Meteo.
    "soil"             { Need-Venv; & $Py (Join-Path $Root "scripts/backfill.py") --only weather --weather-from 2025-06-01 }
    "calibrate-soil"   { Need-Venv; & $Py (Join-Path $Root "scripts/calibrate_soil.py") --check }
    # Phase 15 — rebuild the MongoDB farmer history from Postgres.
    "history-backfill" { Need-Venv; & $Py (Join-Path $Root "scripts/backfill_history.py") --prune }
    "mongo-shell"      { docker compose exec mongo mongosh -u bhav -p bhav --authenticationDatabase admin bhav_history }
    "check-product"    {
        Need-Venv
        Push-Location (Join-Path $Root "backend")
        try { & $Py -m pytest tests/test_phase5_economics.py tests/test_phase6_decision.py tests/test_phase8_api.py -v }
        finally { Pop-Location }
    }
    default {
        if ($Target -match "^check-phase(\d+)$") {
            $n = $Matches[1]
            if ($n -eq "10") { Push-Location (Join-Path $Root "frontend"); try { npm run build } finally { Pop-Location }; break }
            Need-Venv
            $file = Get-ChildItem (Join-Path $Root "backend\tests") -Filter "test_phase$n`_*.py" | Select-Object -First 1
            if (-not $file) { throw "No test file for phase $n yet." }
            Push-Location (Join-Path $Root "backend")
            try { & $Py -m pytest "tests/$($file.Name)" -v } finally { Pop-Location }
        } else {
            throw "Unknown target: $Target"
        }
    }
}
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Build + run BRT 360 locally on Windows: API on http://127.0.0.1:8000,
# UI on http://localhost:5173. Each runs in its own window; close them to stop.
#
# Usage:  powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\run_local.ps1" [-SkipTests] [-NoBuild]
#   -NoBuild   skip pip/npm install, migrations and seed - just start both servers

param([switch]$SkipTests, [switch]$NoBuild)

$ErrorActionPreference = "Stop"
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

if (-not $NoBuild) {
    Step "Backend: virtualenv + dependencies"
    Push-Location $backend
    try {
        if (-not (Test-Path $py)) { python -m venv .venv; Check "python -m venv" }
        & $py -m pip install -q -r requirements.txt; Check "pip install"
        if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "created backend\.env from .env.example" }

        Step "Backend: migrations + seed"
        & $py -m alembic upgrade head; Check "alembic upgrade"
        & $py -m app.seed; Check "seed"

        if (-not $SkipTests) {
            Step "Backend: test suite"
            & $py -m pytest -q -p no:cacheprovider; Check "pytest"
        }
    } finally { Pop-Location }

    Step "Frontend: dependencies + type-check"
    Push-Location $frontend
    try {
        if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund; Check "npm install" }
        npx tsc -b; Check "tsc"
    } finally { Pop-Location }
}

Step "Starting API (new window)"
Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command", "& '$py' -m uvicorn app.main:app --reload --port 8000"

Step "Starting UI (new window)"
Start-Process powershell -WorkingDirectory $frontend -ArgumentList "-NoExit", "-Command", "npm run dev"

Write-Host "`nAPI:  http://127.0.0.1:8000/docs"
Write-Host "UI:   http://localhost:5173   (login: admin@default.local / password from backend\.env)"

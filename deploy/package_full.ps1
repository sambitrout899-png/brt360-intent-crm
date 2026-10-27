# Packs the WHOLE project (not just the Phase 7 overlay) into
# brt360_full_deploy.tar.gz, ready for deploy/brt360.sh on a fresh server.
# Excludes local-only state: venvs, node_modules, builds, caches, databases,
# .env (secrets stay on each machine), old bundles and deploy backups.
#
# Usage:  powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\package_full.ps1" [-SkipChecks]

param([switch]$SkipChecks)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipChecks) {
    Write-Host "==> Backend tests" -ForegroundColor Cyan
    Push-Location backend
    $py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
    & $py -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Backend tests failed - not packaging." }
    Pop-Location

    Write-Host "==> Frontend type-check + build" -ForegroundColor Cyan
    Push-Location frontend
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Frontend build failed - not packaging." }
    Pop-Location
}

# Windows' bsdtar - a GNU tar from Git earlier on PATH reads "D:\..." as host:path.
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $tar)) { $tar = "tar" }

$out = "brt360_full_deploy.tar.gz"
if (Test-Path $out) { Remove-Item $out -Confirm:$false }
& $tar -czf $out `
    --exclude "./.git" --exclude "*/.venv" --exclude "*/node_modules" --exclude "*/dist" `
    --exclude "*/__pycache__" --exclude "*/.pytest_cache" --exclude "*.pyc" `
    --exclude "*.db" --exclude "*.sqlite" --exclude "*.db-journal" `
    --exclude "*/.env" --exclude "./*.tar.gz" --exclude "./.deploy_backups" `
    -C $root .
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

$count = (& $tar -tzf $out | Where-Object { -not $_.EndsWith("/") } | Measure-Object).Count
Write-Host "==> Built $root\$out ($count files, $([math]::Round((Get-Item $out).Length / 1KB)) KB)" -ForegroundColor Green

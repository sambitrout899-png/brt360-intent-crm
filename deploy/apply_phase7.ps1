# Windows equivalent of apply_phase7.sh - applies the Phase 7 overlay onto
# an existing Phase 6 install.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File apply_phase7.ps1 -AppDir D:\apps\brt360-intent-crm [-Bundle .\phase7_deploy.tar.gz] [-SkipTests] [-SkipFrontend]
#
# Steps: back up overwritten files + SQLite DB -> extract overlay ->
# pip install -> alembic upgrade head -> pytest -> npm install + build.
# Rollback: restore <backup>\files over AppDir after running
#   .venv\Scripts\python.exe -m alembic downgrade 2d78011f8fbc   (in backend\, before restoring files)

param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$Bundle = "phase7_deploy.tar.gz",
    [switch]$SkipTests,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$AppDir = (Resolve-Path $AppDir).Path
$Bundle = (Resolve-Path $Bundle).Path
# Windows' bsdtar - a GNU tar from Git earlier on PATH reads "D:\..." as host:path.
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $tar)) { $tar = "tar" }
if (-not (Test-Path "$AppDir\backend") -or -not (Test-Path "$AppDir\frontend")) { throw "$AppDir has no backend\ + frontend\" }

$backup = Join-Path $AppDir ".deploy_backups\phase7-$(Get-Date -Format yyyyMMdd-HHmmss)"

Step "Backing up files the overlay replaces -> $backup"
New-Item -ItemType Directory -Force "$backup\files" | Out-Null
$overlay = & $tar -tzf $Bundle | Where-Object { $_ -and -not $_.EndsWith("/") }
Check "tar -t"
foreach ($f in $overlay) {
    $src = Join-Path $AppDir $f
    if (Test-Path $src) {
        $dst = Join-Path "$backup\files" $f
        New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
        Copy-Item $src $dst
    } else {
        Add-Content -Encoding utf8 "$backup\new_files.txt" $f
    }
}

Step "Backing up the database"
$dbUrl = $env:DATABASE_URL
if (-not $dbUrl -and (Test-Path "$AppDir\backend\.env")) {
    $line = Select-String -Path "$AppDir\backend\.env" -Pattern '^DATABASE_URL=' | Select-Object -Last 1
    if ($line) { $dbUrl = $line.Line.Substring(13).Trim('"', "'") }
}
if (-not $dbUrl) { $dbUrl = "sqlite:///./brt360.db" }
if ($dbUrl.StartsWith("sqlite:///")) {
    $dbFile = $dbUrl.Substring(10)
    if (-not [IO.Path]::IsPathRooted($dbFile)) { $dbFile = Join-Path "$AppDir\backend" $dbFile }
    if (Test-Path $dbFile) { Copy-Item $dbFile $backup; Write-Host "copied $dbFile" } else { Write-Host "no SQLite file yet - nothing to back up" }
} else {
    Write-Warning "Non-SQLite DATABASE_URL - take a database backup (e.g. pg_dump) before continuing."
    $ok = Read-Host "Continue? [y/N]"
    if ($ok -notmatch '^[yY]$') { throw "Aborted before any change." }
}

Step "Extracting overlay into $AppDir"
& $tar -xzf $Bundle -C $AppDir
Check "tar -x"

Step "Backend: dependencies + migration"
Push-Location "$AppDir\backend"
try {
    $py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
    & $py -m pip install -q -r requirements.txt; Check "pip install"
    & $py -m alembic upgrade head; Check "alembic upgrade"
    & $py -m alembic current
    if (-not $SkipTests) {
        Step "Backend: test suite"
        & $py -m pytest -q -p no:cacheprovider; Check "pytest"
    }
} finally { Pop-Location }

if (-not $SkipFrontend) {
    Step "Frontend: install + build"
    Push-Location "$AppDir\frontend"
    try {
        if (Test-Path package-lock.json) { npm ci } else { npm install }; Check "npm install"
        npm run build; Check "npm run build"
    } finally { Pop-Location }
}

Step "Phase 7 deployed"
Write-Host "Backup: $backup"
Write-Host "Restart the API process and publish frontend\dist wherever the UI is served from."

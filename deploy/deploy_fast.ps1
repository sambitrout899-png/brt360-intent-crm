# Fast deploy: same result as deploy_sto.ps1 -Package, minus the slow parts.
#
#   - no test suite on this PC or on the server (run the tests yourself when you want them:
#     backend\.venv\Scripts\python.exe -m pytest -q   - or use deploy_sto.ps1 -Package)
#   - the UI is built here (seconds) and shipped inside the bundle, so the server runs no npm at all
#   - the server reinstalls Python packages only when requirements.txt changed
#   - ONE connection: the bundle is streamed over the same ssh session that applies it - one password
#     prompt (none with an SSH key)
#
# Still the full, safe apply on the server (deploy/apply_sto.sh): stop -> back up code + database ->
# extract -> migrate -> seed -> restart, with a rollback script if anything fails.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_fast.ps1" [-Server root@187.127.132.149] [-AppDir /opt/brt360] [-Typecheck]
#
#   -Typecheck   also run the TypeScript check before building the UI (adds ~10 s)
#   -DryRun      build and package only - nothing is sent

param(
    [string]$Server = "root@187.127.132.149",
    [string]$AppDir = "/opt/brt360",
    [switch]$Typecheck,
    [switch]$DryRun   # build + package only, don't connect
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $root "brt360_full_deploy.tar.gz"
$staging = Join-Path $root ".deploy_ui"
$stagedDist = Join-Path $staging "frontend\dist"
$watch = [System.Diagnostics.Stopwatch]::StartNew()

# ---------- 1. the UI, built for production (API on the same origin) ----------
Write-Host "==> Building the UI for production" -ForegroundColor Cyan
Push-Location (Join-Path $root "frontend")
$envFile = ".env.production.local"
try {
    # VITE_API_BASE must be EMPTY in production (nginx proxies /api on the same host) - a blank
    # value can't be passed through PowerShell's environment, so it goes in a build-only env file.
    Set-Content -Path $envFile -Value "VITE_API_BASE=" -Encoding ascii
    if ($Typecheck) {
        npx tsc -b
        if ($LASTEXITCODE -ne 0) { throw "TypeScript check failed - not deploying." }
    }
    # built to a staging folder, so this PC's own frontend\dist is left alone
    npx vite build --outDir $stagedDist --emptyOutDir --logLevel warn
    if ($LASTEXITCODE -ne 0) { throw "UI build failed - not deploying." }
} finally {
    Remove-Item $envFile -ErrorAction SilentlyContinue
    Pop-Location
}
if (-not (Test-Path (Join-Path $stagedDist "index.html"))) { throw "UI build produced no index.html" }

# ---------- 2. the bundle (code + the built UI as frontend/dist) ----------
Write-Host "==> Packaging" -ForegroundColor Cyan
$tar = Join-Path $env:SystemRoot "System32\tar.exe"   # Windows bsdtar (GNU tar from Git reads "D:\" as host:path)
if (-not (Test-Path $tar)) { $tar = "tar" }
if (Test-Path $bundle) { Remove-Item $bundle -Confirm:$false }
Push-Location $root
try {
    # docs/deliverables (demo videos and packages, ~1 GB) is not part of the app
    & $tar -czf $bundle `
        --exclude "./.git" --exclude "*/.venv" --exclude "*/node_modules" --exclude "./.deploy_ui" `
        --exclude "*/__pycache__" --exclude "*/.pytest_cache" --exclude "*.pyc" `
        --exclude "*.db" --exclude "*.sqlite" --exclude "*.db-journal" `
        --exclude "*/.env" --exclude "*/.env.*.local" --exclude "./*.tar.gz" --exclude "./.deploy_backups" `
        --exclude "./docs/deliverables" --exclude "*.pem" `
        . -C $staging ./frontend/dist   # the production UI last, so it is what extracts
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
} finally {
    Pop-Location
}
$listing = & $tar -tzf $bundle
if (-not ($listing -match "^\./frontend/dist/index\.html$")) { throw "The bundle has no frontend/dist/index.html - not deploying." }
if (-not ($listing -match "^\./deploy/apply_sto\.sh$")) { throw "The bundle has no deploy/apply_sto.sh - not deploying." }
Write-Host ("    {0} KB" -f [math]::Round((Get-Item $bundle).Length / 1KB))
if ($DryRun) {
    Write-Host ("==> Dry run - built and packaged in {0:mm\:ss}, nothing sent" -f $watch.Elapsed) -ForegroundColor Yellow
    return
}

# ---------- 3. one connection: upload + apply ----------
Write-Host "==> Uploading and applying on $Server (one password prompt)" -ForegroundColor Cyan
$remote = "set -e; cat > /tmp/brt360_full_deploy.tar.gz; " +
          "tar -xzf /tmp/brt360_full_deploy.tar.gz -O ./deploy/apply_sto.sh | sed 's/\r$//' > /tmp/apply_sto.sh; " +
          "SKIP_TESTS=1 PREBUILT_UI=1 bash /tmp/apply_sto.sh /tmp/brt360_full_deploy.tar.gz $AppDir"
# cmd.exe's "<" streams the file byte-for-byte (a PowerShell pipe would re-encode it)
cmd /c "ssh -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $Server `"$remote`" < `"$bundle`""
$sshExit = $LASTEXITCODE
if ($sshExit -eq 255) {
    throw "SSH connection/authentication failed or was interrupted. Deployment completion is unknown. Check connectivity with: ssh $Server . If server deployment steps appeared above, check the service and backup before retrying; otherwise no server-side apply was confirmed."
}
if ($sshExit -ne 0) { throw "Remote upload/apply exited with code $sshExit. If the server printed a rollback command, use that backup when recovery is needed; otherwise inspect the upload/apply error above." }

Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("==> Deployed in {0:mm\:ss}" -f $watch.Elapsed) -ForegroundColor Green

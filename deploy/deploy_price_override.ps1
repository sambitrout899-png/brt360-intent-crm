# Deploys the Quote Price Override release (INT-116) - one script, end to end.
#
#   On this PC:  pre-flight checks -> feature tests (+ optional full suite) -> TypeScript check
#                -> production UI build -> bundle -> bundle content checks
#   On the server, over ONE ssh session (one password prompt; none with an SSH key):
#                upload -> deploy/apply_sto.sh (stop API, back up code + database, extract,
#                migrate to c4e6a8b0d2f3, restart, rollback script) -> verify the feature is live
#                (migration, table, intent, step builder, plain-English recognition, API health)
#
# The bundle is this working tree as it is now - including any other uncommitted work.
# The script shows what is uncommitted and asks before sending anything (-Yes skips the question).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_price_override.ps1"
#       [-Server root@187.127.132.149] [-AppDir /opt/brt360] [-SshPort 22] [-IdentityFile C:\keys\id_ed25519]
#       [-FullTests] [-SkipTests] [-SkipTypecheck] [-AutoRollback] [-DryRun] [-Yes]
#
#   -FullTests      also run the whole backend suite before shipping (~6 min); any failure stops the deploy
#   -SkipTests      skip the local feature tests (not recommended)
#   -SkipTypecheck  skip the TypeScript check
#   -AutoRollback   if the post-deploy verification fails, restore the backed-up code + database at once
#                   (otherwise the rollback command is printed and the decision is yours)
#   -DryRun         run the checks, build and package only - nothing is sent
#   -Yes            don't ask for confirmation before deploying

[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*$')]
    [string]$Server = 'root@187.127.132.149',
    [ValidatePattern('^/[a-zA-Z0-9_./-]+$')]
    [string]$AppDir = '/opt/brt360',
    [ValidatePattern('^[a-zA-Z0-9_@.-]+$')]
    [string]$Service = 'brt360-api',
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$SshPort = 22,
    [string]$IdentityFile,
    [switch]$FullTests,
    [switch]$SkipTests,
    [switch]$SkipTypecheck,
    [switch]$AutoRollback,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$Migration = 'c4e6a8b0d2f3'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$bundleName = 'brt360_price_override_deploy.tar.gz'
$bundle = Join-Path $root $bundleName
$staging = Join-Path $root '.deploy_price_override'
$remoteScriptRel = 'deploy/.release_price_override.sh'
$watch = [System.Diagnostics.Stopwatch]::StartNew()

function Step([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Fail([string]$text) { throw $text }

# ssh.exe: PATH first, then the usual install places - the Windows OpenSSH folder is often missing from
# PATH, and a 32-bit PowerShell sees System32\OpenSSH only through Sysnative.
function Find-Ssh {
    $onPath = Get-Command ssh.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($onPath) { return $onPath.Source }
    $candidates = @(
        "$env:SystemRoot\System32\OpenSSH\ssh.exe",
        "$env:SystemRoot\Sysnative\OpenSSH\ssh.exe",
        "$env:ProgramFiles\OpenSSH\ssh.exe",
        "$env:ProgramFiles\Git\usr\bin\ssh.exe",
        "${env:ProgramFiles(x86)}\Git\usr\bin\ssh.exe"
    )
    $git = Get-Command git.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($git) { $candidates += Join-Path (Split-Path (Split-Path $git.Source)) 'usr\bin\ssh.exe' }   # <Git>\cmd\git.exe -> <Git>\usr\bin\ssh.exe
    foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c)) { return $c } }
    return $null
}

if ($AppDir -eq '/' -or $AppDir.Split('/') -contains '..') { Fail 'AppDir must be an absolute application directory without parent traversal.' }

# ---------- 0. pre-flight ----------
Step 'Pre-flight checks'
foreach ($tool in @('npx', 'git')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Fail "$tool is not on PATH." }
}
$sshExe = Find-Ssh
if (-not $DryRun -and -not $sshExe) {
    Fail 'No ssh.exe found (PATH, Windows OpenSSH, Git for Windows). Install the client: Settings > System > Optional features > OpenSSH Client.'
}
if ($sshExe) { Write-Host "    ssh: $sshExe" }
if (-not (Test-Path $python)) { Fail "Backend virtualenv not found: $python" }
if ($IdentityFile) { $IdentityFile = (Resolve-Path -LiteralPath $IdentityFile).Path }

$featureFiles = @(
    "backend/migrations/versions/${Migration}_quote_price_override.py",
    'backend/app/crm_core/quote_override_service.py',
    'backend/app/intent_engine/quote_intents.py',
    'backend/tests/test_quote_price_override.py',
    'deploy/apply_sto.sh',
    'deploy/brt360.sh'
)
foreach ($f in $featureFiles) {
    if (-not (Test-Path (Join-Path $root $f))) { Fail "Missing release file: $f" }
}

Push-Location $backend
try {
    # (no 2>$null on native commands: under -ErrorAction Stop, PowerShell 5.1 turns any stderr line into an error)
    $heads = @(& $python -m alembic heads | Where-Object { $_ -match '\S' })
    if ($LASTEXITCODE -ne 0) { Fail 'alembic heads failed - check the backend virtualenv.' }
    if ($heads.Count -ne 1) { Fail "Expected exactly one migration head, found $($heads.Count): $($heads -join '; ')" }
    $history = (& $python -m alembic history) -join "`n"
    if ($history -notmatch $Migration) { Fail "Migration $Migration is not in the migration history." }
    Write-Host "    migration head: $($heads[0])"
} finally { Pop-Location }

$dirty = @(git -C $root status --short)
Write-Host ("    working tree: {0} uncommitted / untracked path(s) will ship with this release" -f $dirty.Count)

# ---------- 1. local tests ----------
if (-not $SkipTests) {
    Push-Location $backend
    try {
        Step 'Feature tests (tests/test_quote_price_override.py)'
        & $python -m pytest -q -p no:cacheprovider tests/test_quote_price_override.py
        if ($LASTEXITCODE -ne 0) { Fail 'Price override tests failed - not deploying.' }
        if ($FullTests) {
            Step 'Full backend suite'
            & $python -m pytest -q -p no:cacheprovider --deselect tests/test_admin_test_runner.py::test_trigger_test_run_executes_real_suite_and_reports_results
            if ($LASTEXITCODE -ne 0) { Fail 'Backend suite has failures - not deploying. (Run without -FullTests to ship anyway.)' }
        }
    } finally { Pop-Location }
}

# ---------- 2. production UI ----------
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
$stagedDist = Join-Path $staging 'frontend\dist'
Push-Location $frontend
$envFile = '.env.production.local'
try {
    if (-not $SkipTypecheck) {
        Step 'TypeScript check'
        npx tsc -b
        if ($LASTEXITCODE -ne 0) { Fail 'TypeScript check failed - not deploying.' }
    }
    Step 'Building the UI for production'
    # VITE_API_BASE must be EMPTY in production (nginx proxies /api on the same host) - a blank
    # value can't be passed through PowerShell's environment, so it goes in a build-only env file.
    Set-Content -Path $envFile -Value 'VITE_API_BASE=' -Encoding ascii
    npx vite build --outDir $stagedDist --emptyOutDir --logLevel warn
    if ($LASTEXITCODE -ne 0) { Fail 'UI build failed - not deploying.' }
} finally {
    Remove-Item $envFile -ErrorAction SilentlyContinue
    Pop-Location
}
if (-not (Test-Path (Join-Path $stagedDist 'index.html'))) { Fail 'UI build produced no index.html' }

# ---------- 3. the server-side script (shipped inside the bundle) ----------
$remoteScript = @'
#!/usr/bin/env bash
# Server side of deploy/deploy_price_override.ps1 - generated at packaging time, do not edit.
set -Eeuo pipefail
BUNDLE="$1"; APP_DIR="$2"; SERVICE="$3"; API_PORT="$4"; AUTO_ROLLBACK="${5:-0}"
MIGRATION='__MIGRATION__'
step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root - the release stops/starts $SERVICE and writes to $APP_DIR."
command -v flock >/dev/null || die "flock is missing on the server."
exec 9>/tmp/brt360-deploy.lock
flock -n 9 || die "Another BRT 360 deployment is running on this server."

step "Applying the release: back up code + database, stop, extract, migrate, restart"
tar -xzf "$BUNDLE" -O ./deploy/apply_sto.sh | sed 's/\r$//' > /tmp/apply_sto.sh
SKIP_TESTS=1 PREBUILT_UI=1 SERVICE="$SERVICE" API_PORT="$API_PORT" bash /tmp/apply_sto.sh "$BUNDLE" "$APP_DIR"

BACKUP="$(ls -dt "$APP_DIR"/.deploy_backups/sto-* 2>/dev/null | head -1)"
[[ -n "$BACKUP" ]] || die "No backup folder found after the apply step."

verify_failed() {
  printf '\n\033[31mVERIFICATION FAILED: %s\033[0m\n' "$1" >&2
  if [[ "$AUTO_ROLLBACK" == "1" ]]; then
    step "Rolling back to $BACKUP"
    bash "$BACKUP/rollback_sto.sh"
  else
    echo "The release is live but not verified. Inspect: journalctl -u $SERVICE -n 80 --no-pager" >&2
    echo "Roll back with: bash $BACKUP/rollback_sto.sh" >&2
  fi
  exit 1
}

step "Verifying the price override release"
cd "$APP_DIR/backend"
MIGRATION="$MIGRATION" .venv/bin/python - <<'PY' || verify_failed "feature checks"
import os
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

migration = os.environ["MIGRATION"]
scripts = ScriptDirectory.from_config(Config("alembic.ini"))
head = scripts.get_current_head()
assert any(r.revision == migration for r in scripts.iterate_revisions(head, "base")), f"{migration} not in the shipped history"
print(f"  ok  migration {migration} is part of head {head}")

from app.db import engine
columns = {c["name"] for c in inspect(engine).get_columns("quote_price_overrides")}
needed = {"quote_id", "list_price", "computed_price", "override_price", "effective_discount_pct", "reason", "active", "ended_reason"}
assert needed <= columns, f"quote_price_overrides is missing {sorted(needed - columns)}"
print("  ok  table quote_price_overrides")

from app.intent_engine.intent_registry import BUILTIN
assert "INT-116" in BUILTIN, "INT-116 is not in the intent catalog"
from app.experience.routes_intent import _STEP_BUILDERS
assert "INT-116" in _STEP_BUILDERS, "INT-116 has no step builder"
print("  ok  intent INT-116 registered:", BUILTIN["INT-116"].label)

from app.intent_engine import classifier
said = classifier._process_step("Override the price of quote 12 to 4500 - competing offer", None)
assert said is not None and said.intent_type == "INT-116", f"recognised as {said and said.intent_type}"
assert said.entities.get("override_price") == 4500.0, said.entities
print("  ok  plain-English recognition")
PY

ok=""
for _ in $(seq 1 30); do
  if systemctl is-active --quiet "$SERVICE" && curl -fsS --max-time 3 "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || verify_failed "API health check on port $API_PORT"
echo "  ok  API health"

rm -f "$BUNDLE" /tmp/apply_sto.sh
step "Quote Price Override (INT-116) deployed and verified"
echo "Backup:     $BACKUP"
echo "Roll back:  bash $BACKUP/rollback_sto.sh"
echo "Try it:     open a draft quote -> 'Override price', or say 'Override the price of quote <id> to <amount> - <why>'"
'@
$remoteScript = $remoteScript.Replace('__MIGRATION__', $Migration).Replace("`r`n", "`n")
$remoteScriptPath = Join-Path $staging ($remoteScriptRel -replace '/', '\')
New-Item -ItemType Directory -Force (Split-Path $remoteScriptPath) | Out-Null
[IO.File]::WriteAllText($remoteScriptPath, $remoteScript, [Text.UTF8Encoding]::new($false))

# ---------- 4. bundle ----------
Step 'Packaging'
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'   # Windows bsdtar (GNU tar from Git reads "D:\" as host:path)
if (-not (Test-Path $tar)) { $tar = 'tar' }
if (Test-Path $bundle) { Remove-Item $bundle -Confirm:$false }
Push-Location $root
try {
    & $tar -czf $bundle `
        --exclude './.git' --exclude '*/.venv' --exclude '*/node_modules' --exclude './.deploy_ui' --exclude './.deploy_price_override' `
        --exclude '*/__pycache__' --exclude '*/.pytest_cache' --exclude '*.pyc' `
        --exclude '*.db' --exclude '*.sqlite' --exclude '*.db-journal' `
        --exclude '*/.env' --exclude '*/.env.*.local' --exclude './*.tar.gz' --exclude './.deploy_backups' `
        --exclude './docs/deliverables' `
        . -C $staging ./frontend/dist "./$remoteScriptRel"   # the production UI last, so it is what extracts
    if ($LASTEXITCODE -ne 0) { Fail 'tar failed' }
} finally { Pop-Location }

$listing = & $tar -tzf $bundle
$mustShip = @('./frontend/dist/index.html', "./$remoteScriptRel") + ($featureFiles | Where-Object { $_ -notlike 'backend/tests/*' } | ForEach-Object { "./$_" })
foreach ($f in $mustShip) {
    if (-not ($listing -contains $f)) { Fail "The bundle is missing $f - not deploying." }
}
Write-Host ("    {0} ({1:N1} MB)" -f $bundleName, ((Get-Item $bundle).Length / 1MB))

if ($DryRun) {
    Write-Host ("==> Dry run complete in {0:mm\:ss} - bundle at {1}, nothing sent" -f $watch.Elapsed, $bundle) -ForegroundColor Yellow
    return
}

# ---------- 5. confirm ----------
if (-not $Yes) {
    Write-Host ''
    Write-Host "About to deploy to $Server ($AppDir). The API is stopped while the database is backed up and migrated." -ForegroundColor Yellow
    if ($dirty.Count -gt 0) { Write-Host "$($dirty.Count) uncommitted / untracked path(s) in this working tree ship with it." -ForegroundColor Yellow }
    $answer = Read-Host 'Type DEPLOY to continue'
    if ($answer -cne 'DEPLOY') { Write-Host 'Cancelled - nothing sent.'; return }
}

# ---------- 6. one connection: upload + apply + verify ----------
Step "Uploading, applying and verifying on $Server"
$remoteBundle = "/tmp/$bundleName"
$auto = if ($AutoRollback) { '1' } else { '0' }
$remote = "set -e; umask 077; cat > $remoteBundle; " +
          "tar -xzf $remoteBundle -O ./$remoteScriptRel | sed 's/\r$//' > /tmp/brt360_price_override.sh; " +
          "bash /tmp/brt360_price_override.sh $remoteBundle $AppDir $Service $ApiPort $auto"
$sshArgs = "-p $SshPort -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=3"
if ($IdentityFile) { $sshArgs += " -i `"$IdentityFile`"" }
# cmd.exe's "<" streams the file byte-for-byte (a PowerShell pipe would re-encode it). The command line
# goes in a .cmd file so PowerShell 5.1's argument quoting can't mangle the quoted ssh path or the remote command.
$launcher = Join-Path $staging 'upload.cmd'
[IO.File]::WriteAllText($launcher, "@echo off`r`n`"$sshExe`" $sshArgs $Server `"$remote`" < `"$bundle`"`r`nexit /b %ERRORLEVEL%`r`n",
    [Text.Encoding]::ASCII)
& $launcher
$sshExit = $LASTEXITCODE
if ($sshExit -eq 255) {
    Fail "SSH connection/authentication failed or was interrupted - whether the server applied anything is unknown. Check with: & `"$sshExe`" $Server . If deployment steps appeared above, check the service and the backup it printed before retrying."
}
if ($sshExit -ne 0) { Fail "Deployment failed on the server (exit $sshExit). Use the rollback command it printed above if you need to recover." }

Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $bundle -ErrorAction SilentlyContinue
Write-Host ("==> Deployed and verified in {0:mm\:ss}" -f $watch.Elapsed) -ForegroundColor Green

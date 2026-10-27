# Deploys the intent speed & accuracy release - one script, end to end.
#
#   Ships: the model sees only the intents a request could mean (intent_engine/shortlist.py) - all 128
#          intents reachable, prompts that fit the own model's 8k context; the agent's planner asked once
#          per request, not once per part; the Flow Studio prompt sends only the tables it is about; the
#          assistant no longer drafts a new action before answering (the panel fetches the draft itself);
#          rules said in plain English ("If the Company type is PSU, convert the prospect into an account
#          automatically ... after the Add prospect intent") are built as an automation on that intent,
#          and remarks about a rule are never offered as new actions. No database migration.
#
#   On this PC:  pre-flight checks -> feature tests (+ optional full suite) -> TypeScript check
#                -> production UI build -> bundle -> bundle content checks
#   On the server, over ONE ssh session (one password prompt; none with an SSH key):
#                upload -> deploy/apply_sto.sh (stop API, back up code + database, extract,
#                migrate, restart, rollback script) -> verify the release -> API health
#                -> times one real request against the own model (information only - never fails the deploy)
#
# The bundle is this working tree as it is now - including any other uncommitted work.
# The script shows what is uncommitted and asks before sending anything (-Yes skips the question).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_intent_speed.ps1"
#       [-Server root@187.127.132.149] [-AppDir /opt/brt360] [-SshPort 22] [-IdentityFile C:\keys\id_ed25519]
#       [-FullTests] [-SkipTests] [-SkipTypecheck] [-AutoRollback] [-DryRun] [-Yes]
#
#   -FullTests      also run the whole backend suite before shipping (~12 min); any failure stops the deploy
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
# No new migration - these are the releases already on the server; the shipped history must still hold them.
$Migrations = @('c4e6a8b0d2f3', 'd5f7b9c1e3a4', 'f8c0e2a4b6d9', 'a1c3e5f7b9d2', 'b3e5a7c9d1f4', 'c6e8a0b2d4f7')   # ... Fulfillment, Inventory (+ Presales holds)
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$bundleName = 'brt360_intent_speed_deploy.tar.gz'
$bundle = Join-Path $root $bundleName
$staging = Join-Path $root '.deploy_intent_speed'
$remoteScriptRel = 'deploy/.release_intent_speed.sh'
$featureTests = @('tests/test_intent_shortlist.py', 'tests/test_conflicts_and_scratch.py', 'tests/test_sales_to_order.py',
                  'tests/test_local_llm.py', 'tests/test_training_reasoning.py', 'tests/test_process_rule.py', 'tests/test_process_variant.py')
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
    'backend/app/intent_engine/shortlist.py',
    'backend/app/intent_engine/classifier.py',
    'backend/app/orchestration/agent.py',
    'backend/app/orchestration/assistant.py',
    'backend/app/orchestration/flow_ai.py',
    'backend/app/orchestration/process_fit.py',
    'backend/app/orchestration/process_builder.py',
    'frontend/src/components/intent/ProcessFitReport.tsx',
    'backend/app/experience/routes_admin.py',
    'backend/app/crm_core/training_data.py',
    'backend/app/crm_core/training_reasoning.py',
    'backend/tests/test_intent_shortlist.py',
    'frontend/src/components/intent/AssistantPanel.tsx',
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
    foreach ($m in $Migrations) {
        if ($history -notmatch $m) { Fail "Migration $m is not in the migration history." }
    }
    Write-Host "    migration head: $($heads[0])"
} finally { Pop-Location }

$dirty = @(git -C $root status --short)
Write-Host ("    working tree: {0} uncommitted / untracked path(s) will ship with this release" -f $dirty.Count)

# ---------- 1. local tests ----------
if (-not $SkipTests) {
    Push-Location $backend
    try {
        Step "Feature tests ($($featureTests -join ', '))"
        & $python -m pytest -q -p no:cacheprovider @featureTests
        if ($LASTEXITCODE -ne 0) { Fail 'Feature tests failed - not deploying.' }
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
# Server side of deploy/deploy_intent_speed.ps1 - generated at packaging time, do not edit.
set -Eeuo pipefail
BUNDLE="$1"; APP_DIR="$2"; SERVICE="$3"; API_PORT="$4"; AUTO_ROLLBACK="${5:-0}"
MIGRATIONS='__MIGRATIONS__'
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

step "Verifying the release (read-only checks)"
cd "$APP_DIR/backend"
MIGRATIONS="$MIGRATIONS" .venv/bin/python - <<'PY' || verify_failed "feature checks"
import os
from alembic.config import Config
from alembic.script import ScriptDirectory

scripts = ScriptDirectory.from_config(Config("alembic.ini"))
head = scripts.get_current_head()
shipped = {r.revision for r in scripts.iterate_revisions(head, "base")}
for migration in os.environ["MIGRATIONS"].split():
    assert migration in shipped, f"{migration} not in the shipped history"
print(f"  ok  migrations {os.environ['MIGRATIONS']} are part of head {head} (this release adds none)")

from app.db import SessionLocal
from app.intent_engine import classifier, shortlist
from app.models.tenant import Tenant
from app.orchestration import agent, assistant, flow_ai, flow_records

prompt = classifier.system_prompt(None, "reserve a SIM from inventory for order 33")
assert "INT-123" in prompt, "the shortlist does not reach the newer intents"
assert len(prompt) < len(classifier.system_prompt()) * 0.8, "the classifier prompt is not shortlisted"
assert classifier.system_prompt().startswith("You are the NLU classifier"), "the original prompt changed"
print(f"  ok  classifier prompt shortlisted (~{len(prompt) // 4} tokens; all 128 intents reachable)")

db = SessionLocal()
try:
    for tenant in db.query(Tenant).all():
        entries = classifier.prompt_entries(db, tenant.id)
        catalog = {e["code"]: e for e in entries}
        goal = "Add prospect Globex Ltd, then generate a lead for Fiber 500, then approve the lead"
        size = max(len(agent.plan_prompt(catalog, goal, None)), len(classifier.system_prompt(classifier.custom_intent_specs(db, tenant.id), goal, entries)))
        assert size // 4 < 6000, f"tenant {tenant.id}: prompt ~{size // 4} tokens - too close to the model's 8k context"
        print(f"  ok  tenant {tenant.id} ({tenant.name}): {len(entries)} active intents, largest prompt ~{size // 4} tokens")
finally:
    db.close()

tables = flow_ai._tables_for_prompt(flow_records.catalog(), "when an order is approved add an order item", "order")
assert 0 < len(tables) <= flow_ai.PROMPT_TABLES, "the Flow Studio prompt still sends the whole schema"
assert not hasattr(assistant, "_offer") and hasattr(agent, "reuse_plans"), "assistant/agent changes not deployed"
print(f"  ok  Flow Studio prompt: {len(tables)} of {len(flow_records.catalog())} tables; one planner call per request")

from app.orchestration import process_fit
db = SessionLocal()
try:
    tenant = db.query(Tenant).order_by(Tenant.id).first()
    line = process_fit.rule_variant(db, tenant.id, "If the Company type is PSU, then convert the prospect into an account automatically")
    assert line and "(INT-15)" in line and "[company_type] is PSU" in line and "(INT-35)" in line, line
    assert process_fit.rule_variant(db, tenant.id, "When an order is approved, notify the Sales Manager") is None
    print("  ok  rules in plain English: after Add a prospect, when Company type is PSU -> Convert to an account")
    draft = flow_ai.heuristic_generate('if the Company type is "PSU", then the Prospect should be auto-converted to Account', [])
    assert "Could not interpret" not in draft["notes"] and any(n.get("config", {}).get("intent") == "INT-35" for n in draft["nodes"]), draft["notes"]
    assert flow_ai.detect_after_intent("... after the Prospect is saved")[0]["trigger_intent_type"] == "INT-15"
    print("  ok  Flow Studio built-in reader: condition + Run an action (Convert to an account), after Add a prospect")
finally:
    db.close()
PY

ok=""
for _ in $(seq 1 30); do
  if systemctl is-active --quiet "$SERVICE" && curl -fsS --max-time 3 "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || verify_failed "API health check on port $API_PORT"
echo "  ok  API health"

step "Timing one real request on the own model (information only)"
.venv/bin/python - <<'PY' || echo "  (skipped - the timing check itself failed; the release is deployed)"
import time
from dataclasses import replace
from app.crm_core import llm
from app.intent_engine import classifier

status = llm.local_status()
if not status["reachable"]:
    print(f"  --  own model not running ({status['error']}) - tenants on 'external' or 'rules' are unaffected")
else:
    target = replace(llm.local_target(), timeout=180.0)
    text = "Acme Telecom's internet has been down since this morning"
    for run in (1, 2):
        started = time.perf_counter()
        try:
            result = classifier._classify_with_llm(text, target, [], None, classifier.prompt_entries(None, None))
            print(f"  run {run}: {time.perf_counter() - started:5.1f}s -> {result.intent_type} ({result.confidence:.2f}) [{target.model}]")
        except llm.LlmUnavailable as exc:
            print(f"  run {run}: {time.perf_counter() - started:5.1f}s -> {exc}")
    print("  (run 1 may include loading the model into memory; run 2 is the normal speed)")
PY

rm -f "$BUNDLE" /tmp/apply_sto.sh
step "Intent speed & accuracy release deployed and verified"
echo "Backup:     $BACKUP"
echo "Roll back:  bash $BACKUP/rollback_sto.sh"
echo "Try it:     the intent bar / Ask anything - e.g. 'reserve a SIM for order 33', 'show me the invoices for customer 12'."
echo "Next:       Admin -> AI Settings -> export training data and re-train the own model on the new prompts (deploy/llm/README.md)."
'@
$remoteScript = $remoteScript.Replace('__MIGRATIONS__', ($Migrations -join ' ')).Replace("`r`n", "`n")
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
        --exclude './.git' --exclude '*/.venv' --exclude '*/node_modules' `
        --exclude './.deploy_ui' --exclude './.deploy_price_override' --exclude './.deploy_flow_errors' --exclude './.deploy_fulfillment' `
        --exclude './.deploy_intent_speed' `
        --exclude '*/__pycache__' --exclude '*/.pytest_cache' --exclude '*.pyc' `
        --exclude '*.db' --exclude '*.sqlite' --exclude '*.db-journal' `
        --exclude '*/.env' --exclude '*/.env.*.local' --exclude './*.tar.gz' --exclude './.deploy_backups' `
        --exclude './docs/deliverables' --exclude './backend/.vite' `
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
    Write-Host "About to deploy to $Server ($AppDir). The API is stopped while the database is backed up." -ForegroundColor Yellow
    if ($dirty.Count -gt 0) { Write-Host "$($dirty.Count) uncommitted / untracked path(s) in this working tree ship with it." -ForegroundColor Yellow }
    $answer = Read-Host 'Type DEPLOY to continue'
    if ($answer -cne 'DEPLOY') { Write-Host 'Cancelled - nothing sent.'; return }
}

# ---------- 6. one connection: upload + apply + verify ----------
Step "Uploading, applying and verifying on $Server"
$remoteBundle = "/tmp/$bundleName"
$auto = if ($AutoRollback) { '1' } else { '0' }
$remote = "set -e; umask 077; cat > $remoteBundle; " +
          "tar -xzf $remoteBundle -O ./$remoteScriptRel | sed 's/\r$//' > /tmp/brt360_intent_speed.sh; " +
          "bash /tmp/brt360_intent_speed.sh $remoteBundle $AppDir $Service $ApiPort $auto"
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

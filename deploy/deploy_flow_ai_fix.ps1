# Deploys the Flow Studio AI fix - one script, end to end.
#
#   Ships: Flow Studio "Describe the flow" fixes -
#          - a smaller AI prompt (compact field list; table schemas only when the description is about records),
#            so the on-server model answers inside its time limit
#          - when the AI is not used, the note says why (timed out, unreachable, bad draft) instead of
#            "no AI key configured"
#          - the built-in rule parser reads "For any B2B Prospect if the Company type is PSU, convert it as Account"
#          - Intent Command Bar: a rule's action is the CRM's own (Convert to an account, INT-35) before a custom flow
#            whose name shares the words ("NGO Prospect to Account Conversion"); a variant may run a FLOW-n step
#          - "I need to create a process which will ..." is a design request: no junk "To create a process" field,
#            and never run as the single action it mentions
#          - the reasoner: an automation asked in plain English ("create an order automatically for B2B prospects
#            with Company Type Government") is understood (rules, or the AI model), reasoned over the existing
#            intents, the open choices asked, existing flows recommended, the rest planned as flows - built on consent;
#            flows can test the record's customer (customer.<field>); INT-23 returns its quote_id
#          - a custom flow is chosen only when the request names it: "create a new B2B prospect" is Add a prospect,
#            not "NGO Prospect to Account Conversion" (the model's pick is checked); "in order to" is no order
#          - Analytics & Reports (new page): slice and dice every entity (pivot, date grains, bins, table
#            calculations, drill to the records, CSV), saved reports and dashboards, the telecom KPI scorecard,
#            forecasts (Holt-Winters), churn risk + CLV, deal win probability, segments, next best offer,
#            usage/fraud alerts, plain-English questions and AI explanations
#          - telecom demo history for analytics (NOT loaded by the deploy - run it yourself on the server when wanted):
#              cd <app>/backend && .venv/bin/python -m scripts.seed_analytics_demo --tenant-slug default
#            and the win model no longer counts feasibility-failed deals as open pipeline
#   Migration: d8a0c2e4f6b1 (analytics_reports) - apply_sto.sh runs alembic upgrade head, as every release does.
#
#   On this PC:  pre-flight checks -> feature tests (+ optional full suite) -> TypeScript check
#                -> production UI build -> bundle -> bundle content checks
#   On the server, over ONE ssh session (one password prompt; none with an SSH key):
#                upload -> deploy/apply_sto.sh (stop API, back up code + database, extract,
#                migrate, restart, rollback script) -> [optional: set the local model's time limit]
#                -> verify the fix is live -> API health -> local model status
#
# The bundle is this working tree as it is now - including any other uncommitted work.
# The script shows what is uncommitted and asks before sending anything (-Yes skips the question).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_flow_ai_fix.ps1"
#       [-Server root@187.127.132.149] [-AppDir /opt/brt360] [-SshPort 22] [-IdentityFile C:\keys\id_ed25519]
#       [-LlmTimeout 110] [-FullTests] [-SkipTests] [-SkipTypecheck] [-AutoRollback] [-DryRun] [-Yes]
#
#   -LlmTimeout     also set LOCAL_LLM_TIMEOUT_SECONDS in the server's backend/.env (seconds, 30-115 -
#                   keep it under nginx's 120 s proxy timeout). Omit to leave the server's setting as it is.
#   -FullTests      also run the whole backend suite before shipping (~4 min); any failure stops the deploy
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
    [ValidateRange(0, 115)][int]$LlmTimeout = 0,
    [switch]$FullTests,
    [switch]$SkipTests,
    [switch]$SkipTypecheck,
    [switch]$AutoRollback,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$bundleName = 'brt360_flow_ai_fix_deploy.tar.gz'
$bundle = Join-Path $root $bundleName
$staging = Join-Path $root '.deploy_flow_ai_fix'
$remoteScriptRel = 'deploy/.release_flow_ai_fix.sh'
$featureTests = @('tests/test_analytics.py', 'tests/test_analytics_demo_data.py', 'tests/test_migrated_schema.py', 'tests/test_lookalike_flow.py','tests/test_reasoner.py','tests/test_assistant.py', 'tests/test_agent.py', 'tests/test_flow_studio_phrasing.py','tests/test_process_rule.py', 'tests/test_design_requests.py', 'tests/test_process_variant.py','tests/test_intent_shortlist.py',
                  'tests/test_flow_record_operations.py')
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
if ($LlmTimeout -ne 0 -and $LlmTimeout -lt 30) { Fail '-LlmTimeout must be between 30 and 115 seconds (or omitted).' }

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
    'backend/app/orchestration/flow_ai.py',
    'backend/app/orchestration/process_builder.py',
    'backend/app/orchestration/process_fit.py',
    'backend/app/orchestration/assistant.py',
    'backend/app/orchestration/reasoner.py',
    'backend/app/orchestration/flow_engine.py',
    'backend/app/orchestration/flow_schema.py',
    'backend/app/orchestration/fulfillment_ops.py',
    'backend/app/orchestration/actions_sales.py',
    'backend/app/orchestration/agent.py',
    'backend/app/orchestration/scratch.py',
    'backend/app/intent_engine/classifier.py',
    'backend/tests/test_lookalike_flow.py',
    'backend/app/analytics/semantic.py',
    'backend/app/analytics/engine.py',
    'backend/app/analytics/ml.py',
    'backend/app/analytics/predictive.py',
    'backend/app/analytics/telecom.py',
    'backend/app/analytics/nl.py',
    'backend/app/analytics/insights.py',
    'backend/app/analytics/demo_data.py',
    'backend/scripts/seed_analytics_demo.py',
    'backend/app/models/analytics.py',
    'backend/app/experience/routes_analytics.py',
    'backend/migrations/versions/d8a0c2e4f6b1_analytics_reports.py',
    'frontend/src/pages/AnalyticsPage.tsx',
    'frontend/src/components/analytics/Charts.tsx',
    'frontend/src/components/intent/ProcessBuilder.tsx',
    'backend/app/crm_core/llm.py',
    'frontend/src/pages/FlowStudio.tsx',
    'backend/tests/test_flow_studio_phrasing.py',
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
# Server side of deploy/deploy_flow_ai_fix.ps1 - generated at packaging time, do not edit.
set -Eeuo pipefail
BUNDLE="$1"; APP_DIR="$2"; SERVICE="$3"; API_PORT="$4"; AUTO_ROLLBACK="${5:-0}"; LLM_TIMEOUT="${6:-0}"
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

ENV_FILE="$APP_DIR/backend/.env"
if [[ "$LLM_TIMEOUT" != "0" ]]; then
  step "Setting LOCAL_LLM_TIMEOUT_SECONDS=$LLM_TIMEOUT in $ENV_FILE"
  touch "$ENV_FILE"
  cp -p "$ENV_FILE" "$BACKUP/backend.env.before_llm_timeout"
  if grep -q '^LOCAL_LLM_TIMEOUT_SECONDS=' "$ENV_FILE"; then
    sed -i "s/^LOCAL_LLM_TIMEOUT_SECONDS=.*/LOCAL_LLM_TIMEOUT_SECONDS=$LLM_TIMEOUT/" "$ENV_FILE"
  else
    printf '\nLOCAL_LLM_TIMEOUT_SECONDS=%s\n' "$LLM_TIMEOUT" >> "$ENV_FILE"
  fi
  systemctl restart "$SERVICE"
  echo "  (previous .env kept at $BACKUP/backend.env.before_llm_timeout)"
fi

step "Verifying the release (read-only checks)"
cd "$APP_DIR/backend"
.venv/bin/python - <<'PY' || verify_failed "feature checks"
from app.orchestration import flow_ai
from app.orchestration.process_builder import _business_action

said = "For any B2B Prospect if the Company type is PSU, convert it as Account"
rest = flow_ai._LEAD_IN.sub("", said)
assert rest.startswith("if the Company"), rest
assert _business_action("convert it as Account")[0] == "INT-35", "convert ... as Account is not read as INT-35"
draft = flow_ai.heuristic_generate(rest, [])
kinds = {n["type"]: n for n in draft["nodes"]}
assert "Could not interpret" not in draft["notes"], draft["notes"]
assert kinds["condition"]["config"]["value"] == "PSU" and kinds["action"]["config"]["intent"] == "INT-35", kinds
print("  ok  rule parser drafts: if company_type = PSU -> Convert to an account (INT-35)")

line = flow_ai._field_line({"path": "record.company_type", "data_type": "picklist", "label": "Company type",
                            "values": ["PSU", "LLP"], "settable": True})
assert line == "record.company_type (picklist, settable) = PSU|LLP", line
assert hasattr(flow_ai, "_RECORD_WORDS"), "record-word gate missing"
print("  ok  compact AI prompt (field lines; table schemas only for record work)")

import inspect
from app.orchestration import process_builder, process_fit
assert "_business_action" in inspect.getsource(process_fit.rule_variant), "rule_variant does not prefer the built-in action"
assert "FLOW" in inspect.getsource(process_builder._Design.variant), "variants cannot run custom flows"
print("  ok  Intent Command Bar rules: built-in Convert (INT-35) before look-alike custom flows; variants may run FLOW-n")

from app.orchestration import assistant
asked = "I need to create a process which will create an order automatically from the prospect for a prospect with Company Type"
assert process_fit.read_data(asked) is None and assistant._DESIGN_ASK.search(asked), "a process request is still read as a field / one action"
print("  ok  'create a process which will ...' is a design request - not a field, not a single action")

from app.orchestration import flow_engine, reasoner
assert "customer" in inspect.getsource(flow_engine._lookup) and hasattr(flow_engine, "customer_of"), "flows can't see the record's customer"
goal = reasoner.Goal(text="x", outcome="order", field_label="Company type", field_key="company_type", value="Government", product="*")
stretches, _why = reasoner.road(goal, presales=False, auto_approve=True)
assert [[c for c, _e in s.steps] for s in stretches] == [["INT-46", "INT-17", "INT-05", "INT-06", "INT-22"], ["INT-24"]], stretches
print("  ok  reasoner: prospect -> lead -> quote -> approved -> proposal, then the order when the customer accepts")

from app.intent_engine import classifier
from app.orchestration import process_builder
ngo = {"label": "NGO Prospect to Account Conversion", "phrases": ["ngo prospect to account conversion"]}
assert not classifier.custom_fits("create a new B2B prospect", ngo), "a look-alike custom flow still wins on one shared word"
assert classifier.custom_fits("run the NGO prospect to account conversion", ngo), "a named custom flow is no longer found"
assert process_builder._entity_of("In order to make this happen, notify the prospect") == "party", "'in order to' still means an order"
print("  ok  a custom flow is picked only when named ('create a new B2B prospect' -> Add a prospect); 'in order to' is no order")

from sqlalchemy import inspect as _inspect
from app.db import SessionLocal, engine as _engine
from app.analytics import engine as _aengine, ml as _ml
from app.models.tenant import Tenant
cols = {c["name"] for c in _inspect(_engine).get_columns("analytics_reports")}
assert {"spec_json", "brt_id", "sys_created_at"} <= cols, "analytics_reports is not migrated: %s" % sorted(cols)
_db = SessionLocal()
try:
    _tid = _db.query(Tenant.id).order_by(Tenant.id).first()[0]
    _r = _aengine.query(_db, _tid, None, {"entity": "order", "dimensions": [{"field": "status"}], "measures": [{"agg": "count"}]})
finally:
    _db.close()
_fc = _ml.forecast(["a", "b", "c", "d"], [1.0, 2.0, 3.0, 4.0], "month", 2, lambda s: s + "+")
assert len(_fc["forecast"]) == 2, _fc
print("  ok  analytics: reports table migrated; %d orders read in %d status groups; forecasting runs" % (_r["scanned"], _r["groups"]))

from app.config import get_settings
s = get_settings()
print(f"  ok  local model: {s.local_llm_model} at {s.local_llm_base_url}, time limit {s.local_llm_timeout_seconds:g}s")
PY

ok=""
for _ in $(seq 1 30); do
  if systemctl is-active --quiet "$SERVICE" && curl -fsS --max-time 3 "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || verify_failed "API health check on port $API_PORT"
echo "  ok  API health"

step "Local model server (information only)"
if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags | .venv/bin/python -c 'import sys, json
for m in json.load(sys.stdin).get("models", []):
    d = m.get("details") or {}
    print("  %s: %s, %.1f GB" % (m.get("name"), d.get("parameter_size", "?"), (m.get("size") or 0) / 1e9))'
  command -v nvidia-smi >/dev/null && echo "  GPU present" || echo "  no GPU - CPU only: a 7B model is roughly 2x slower than the 3B"
else
  echo "  Ollama is not answering on 127.0.0.1:11434"
fi

rm -f "$BUNDLE" /tmp/apply_sto.sh
step "Flow Studio AI fix deployed and verified"
echo "Backup:     $BACKUP"
echo "Roll back:  bash $BACKUP/rollback_sto.sh"
echo "Try it:     Admin -> Flow Studio -> Describe the flow -> Generate. If the AI is not used, the note now says why."
echo "Speed:      bash $APP_DIR/deploy/local_llm.sh bench   (over ~20 s per answer: choose brt360-telecom-previous in Admin -> AI Settings)"
'@
$remoteScript = $remoteScript.Replace("`r`n", "`n")
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
        --exclude './.deploy_ui' --exclude './.deploy_price_override' --exclude './.deploy_flow_errors' --exclude './.deploy_flow_ai_fix' `
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
    if ($LlmTimeout) { Write-Host "LOCAL_LLM_TIMEOUT_SECONDS will be set to $LlmTimeout on the server." -ForegroundColor Yellow }
    $answer = Read-Host 'Type DEPLOY to continue'
    if ($answer -cne 'DEPLOY') { Write-Host 'Cancelled - nothing sent.'; return }
}

# ---------- 6. one connection: upload + apply + verify ----------
Step "Uploading, applying and verifying on $Server"
$remoteBundle = "/tmp/$bundleName"
$auto = if ($AutoRollback) { '1' } else { '0' }
$remote = "set -e; umask 077; cat > $remoteBundle; " +
          "tar -xzf $remoteBundle -O ./$remoteScriptRel | sed 's/\r$//' > /tmp/brt360_flow_ai_fix.sh; " +
          "bash /tmp/brt360_flow_ai_fix.sh $remoteBundle $AppDir $Service $ApiPort $auto $LlmTimeout"
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
Write-Host ("==> Deployed and verified in {0:mm\:ss} - backup + rollback command printed above" -f $watch.Elapsed) -ForegroundColor Green

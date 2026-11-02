# Release BRT 360: staging first, check it, then production - one script around deploy_docker.ps1.
#
#   1. pre-flight   on main, nothing uncommitted, pushed to GitHub, one migration head, SSH works
#   2. staging      deploy_docker.ps1 -Environment staging, then health + UI checks on staging.brt360-tech.com
#   3. you confirm  try the change on staging, then type DEPLOY (skipped with -Yes)
#   4. production   deploy_docker.ps1 -Environment production, then health + UI checks on brt360-tech.com
#
# Every deploy takes a database dump and a code backup first and prints the rollback if it fails (apply.sh).
# A full log is written to %TEMP%\brt360_deploy_<time>.log.
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_release.ps1"
#   ... -Target staging           staging only
#   ... -Target production        production only (after staging was checked earlier)
#   ... -RunTests                 run the backend tests for this release's areas + the frontend build first
#   ... -Yes                      don't stop for confirmation between staging and production
#   ... -SkipPreflight            skip the git / migration checks (not the SSH check)

param(
    [ValidateSet("both", "staging", "production")]
    [string]$Target = "both",
    [string]$Server = "root@187.127.132.149",
    [switch]$RunTests,
    [switch]$Yes,
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$deploy = Join-Path $PSScriptRoot "deploy_docker.ps1"
$urls = @{ staging = "https://staging.brt360-tech.com"; production = "https://brt360-tech.com" }
$log = Join-Path $env:TEMP ("brt360_deploy_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
$watch = [System.Diagnostics.Stopwatch]::StartNew()

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Fail($text) { Write-Host "    FAIL  $text" -ForegroundColor Red; throw $text }

function Invoke-Native([string]$exe, [string[]]$arguments) {
    # native tools write progress to stderr; don't let PowerShell 5.1 turn that into an error
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $exe @arguments 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
    return @{ Code = $LASTEXITCODE; Out = ($out -join "`n") }
}

function Test-Site([string]$environment) {
    $base = $urls[$environment]
    Step "Checking $environment ($base)"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $healthy = $false
    for ($i = 1; $i -le 20; $i++) {   # containers may take a minute to come up
        try {
            $r = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 10
            if ($r.status -eq "ok") { $healthy = $true; break }
        } catch { }
        Write-Host "    waiting for the API ($i/20)..."
        Start-Sleep -Seconds 6
    }
    if (-not $healthy) { Fail "$base/api/health did not answer ok - look at the deploy output above (it prints the rollback)" }
    Ok "API healthy"
    # staging's pages sit behind nginx basic auth (setup_staging.sh: user brt360); /api/ doesn't
    $headers = @{}
    if ($environment -eq "staging" -and $env:BRT360_STAGING_PAGE_PW) {
        $pair = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("brt360:$($env:BRT360_STAGING_PAGE_PW)"))
        $headers["Authorization"] = "Basic $pair"
    }
    try {
        $page = Invoke-WebRequest -Uri "$base/" -Headers $headers -UseBasicParsing -TimeoutSec 15
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch "<div id=`"root`"") { Fail "$base/ did not serve the app (HTTP $($page.StatusCode))" }
        Ok "UI served"
    } catch {
        $response = $_.Exception.Response
        $code = if ($response) { [int]$response.StatusCode } else { 0 }
        if ($environment -eq "staging" -and $code -eq 401 -and -not $headers.Count -and "$($response.Headers['WWW-Authenticate'])" -match "Basic") {
            Ok "UI behind the staging page password (set `$env:BRT360_STAGING_PAGE_PW to check the page itself)"
        } else {
            Fail "$base/ did not load: $($_.Exception.Message)"
        }
    }
    # the new endpoints answer (401 without a login = routed to the API, not a 404 from nginx / an old build)
    foreach ($path in "/api/admin/product-model/x/guidance", "/api/field-service/work-orders/0/network-attributes") {
        try {
            Invoke-WebRequest -Uri "$base$path" -Method Get -UseBasicParsing -TimeoutSec 15 | Out-Null
            Ok "$path reachable"
        } catch {
            $code = [int]$_.Exception.Response.StatusCode
            if ($code -in 401, 403, 405) { Ok "$path reachable (HTTP $code without a login)" }
            elseif ($code -eq 404) { Fail "$path is 404 - the new build isn't running on $environment" }
            else { Write-Host "    WARN  $path answered HTTP $code" -ForegroundColor Yellow }
        }
    }
}

function Deploy([string]$environment) {
    Step "Deploying to $environment"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $deploy -Environment $environment -Server $Server
    if ($LASTEXITCODE -ne 0) { Fail "deploy_docker.ps1 failed for $environment (exit $LASTEXITCODE) - the rollback steps are printed above" }
    Test-Site $environment
}

Start-Transcript -Path $log -Append | Out-Null
try {
    Write-Host "BRT 360 release - target: $Target - log: $log" -ForegroundColor Cyan
    Push-Location $root

    # ------------------------------------------------------------------ 1. pre-flight
    if (-not $SkipPreflight) {
        Step "Pre-flight checks"
        $branch = (Invoke-Native git @("rev-parse", "--abbrev-ref", "HEAD")).Out.Trim()
        if ($branch -ne "main") { Fail "on branch '$branch' - releases go from main" }
        Ok "on main"

        $dirty = (Invoke-Native git @("status", "--porcelain", "--untracked-files=no")).Out.Trim()
        if ($dirty) { Fail "uncommitted changes to tracked files - commit or stash them first:`n$dirty" }
        Ok "no uncommitted changes"

        $fetch = Invoke-Native git @("fetch", "origin", "main", "--quiet")
        if ($fetch.Code -ne 0) { Fail "git fetch failed: $($fetch.Out)" }
        $local = (Invoke-Native git @("rev-parse", "HEAD")).Out.Trim()
        $remote = (Invoke-Native git @("rev-parse", "origin/main")).Out.Trim()
        if ($local -ne $remote) { Fail "local main ($($local.Substring(0,7))) differs from GitHub ($($remote.Substring(0,7))) - push / pull first" }
        Ok "pushed to GitHub ($($local.Substring(0,7)))"

        $python = Join-Path $root "backend\.venv\Scripts\python.exe"
        if (Test-Path $python) {
            Push-Location (Join-Path $root "backend")
            try { $heads = Invoke-Native $python @("-m", "alembic", "heads") } finally { Pop-Location }
            $count = ([regex]::Matches($heads.Out, "\(head\)")).Count
            if ($heads.Code -ne 0 -or $count -ne 1) { Fail "alembic must have exactly one head:`n$($heads.Out)" }
            Ok "one migration head: $(($heads.Out -split ' ')[0])"
        }
    }

    if ($RunTests) {
        Step "Tests for this release"
        Push-Location (Join-Path $root "backend")
        try {
            $tests = @("tests/test_ai_model_and_network_guidance.py", "tests/test_boundaries.py", "tests/test_product_configuration.py",
                       "tests/test_product_attributes.py", "tests/test_phase5_fulfillment.py", "tests/test_fulfillment_orchestration.py",
                       "tests/test_presales_inventory_e2e.py", "tests/test_inventory.py")
            & .\.venv\Scripts\python.exe -m pytest @tests -q -p no:cacheprovider
            if ($LASTEXITCODE -ne 0) { Fail "backend tests failed" }
            Ok "backend tests passed"
        } finally { Pop-Location }
        Push-Location (Join-Path $root "frontend")
        try {
            $build = Invoke-Native "npm.cmd" @("run", "build")
            if ($build.Code -ne 0) { Write-Host $build.Out; Fail "frontend build failed" }
            Ok "frontend builds"
        } finally { Pop-Location }
    }

    Step "SSH to $Server"
    # BatchMode never prompts: it passes only when the key is loaded in ssh-agent (or has no passphrase).
    $ssh = Invoke-Native ssh @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10", $Server, "echo ok")
    if ($ssh.Code -eq 0 -and $ssh.Out -match "ok") {
        Ok "SSH works (no passphrase prompts)"
    } elseif ($ssh.Out -match "Permission denied") {
        # a passphrase-protected key that isn't in ssh-agent: ssh asks for the passphrase during each deploy
        Write-Host ("    NOTE  Your key needs its passphrase - ssh will ask for it during each deploy" +
                    $(if ($Target -eq "both") { " (twice: staging and production)" } else { "" }) + ".") -ForegroundColor Yellow
        Write-Host "          To type it once per session instead (as administrator, one time):  Set-Service ssh-agent -StartupType Manual" -ForegroundColor DarkGray
        Write-Host "          then:  Start-Service ssh-agent; ssh-add" -ForegroundColor DarkGray
    } else {
        Fail "Can't reach $Server over SSH: $($ssh.Out)"
    }

    # ------------------------------------------------------------------ 2-4. staging, confirm, production
    if ($Target -in "both", "staging") { Deploy "staging" }

    if ($Target -eq "both") {
        if (-not $Yes) {
            Write-Host "`nStaging is up: $($urls.staging)" -ForegroundColor Green
            Write-Host "  - Admin -> Product Modeller -> a product -> Guidance -> Get guidance -> apply a few"
            Write-Host "  - Field Service -> an order's installation job -> Network attributes -> change one -> Save"
            $answer = Read-Host "Type DEPLOY to release to PRODUCTION (anything else stops here)"
            if ($answer -cne "DEPLOY") {
                Write-Host "Stopped after staging - production untouched. Release it later with: -Target production" -ForegroundColor Yellow
                return
            }
        }
    }

    if ($Target -in "both", "production") { Deploy "production" }

    Write-Host ("`n==> Release done in {0:mm\:ss} - log: {1}" -f $watch.Elapsed, $log) -ForegroundColor Green
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}

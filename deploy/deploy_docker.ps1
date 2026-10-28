# Deploy to the Docker install - deploy_fast.ps1's counterpart once the server
# runs BRT 360 on Docker Compose (deploy/compose/README.md, stage C).
#
#   - the code is packaged here and streamed over ONE ssh session
#   - the server builds the images itself (API and UI), runs migrations + seed
#     first, then replaces the containers - deploy/compose/apply.sh
#   - a database dump and a code backup are taken first; a failure prints the rollback
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_docker.ps1" [-Environment staging] [-Server root@187.127.132.149]
#
#   -Environment  production (default, /opt/brt360) or staging (/opt/brt360-staging, made once by
#                 deploy/compose/setup_staging.sh) - try a change on staging first
#   -AppDir       overrides the folder the environment deploys to
#   -DryRun       package only - nothing is sent

param(
    [ValidateSet("production", "staging")]
    [string]$Environment = "production",
    [string]$Server = "root@187.127.132.149",
    [string]$AppDir = "",
    [switch]$DryRun
)

if (-not $AppDir) { $AppDir = @{ production = "/opt/brt360"; staging = "/opt/brt360-staging" }[$Environment] }
Write-Host "==> Deploying to $Environment ($AppDir)" -ForegroundColor Cyan

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $root "brt360_docker_deploy.tar.gz"
$watch = [System.Diagnostics.Stopwatch]::StartNew()

Write-Host "==> Packaging" -ForegroundColor Cyan
$tar = Join-Path $env:SystemRoot "System32\tar.exe"   # Windows bsdtar (GNU tar from Git reads "D:\" as host:path)
if (-not (Test-Path $tar)) { $tar = "tar" }
if (Test-Path $bundle) { Remove-Item $bundle -Confirm:$false }
Push-Location $root
try {
    & $tar -czf $bundle `
        --exclude "./.git" --exclude "*/.venv" --exclude "*/node_modules" --exclude "*/dist" --exclude "./.deploy_ui" `
        --exclude "*/__pycache__" --exclude "*/.pytest_cache" --exclude "*.pyc" `
        --exclude "*.db" --exclude "*.sqlite" --exclude "*.db-journal" `
        --exclude "*/.env" --exclude "*/.env.*.local" --exclude "./deploy/compose/.env" --exclude "./deploy/compose/backups" `
        --exclude "./deploy/compose/monitoring/alertmanager.yml" `
        --exclude "./*.tar.gz" --exclude "./.deploy_backups" --exclude "./sermon_output" `
        --exclude "*.pem" --exclude "./docs/deliverables" .   # keys stay on the server; demo videos (~1 GB) aren't the app
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
} finally {
    Pop-Location
}
$listing = & $tar -tzf $bundle
foreach ($required in "./deploy/compose/apply.sh", "./deploy/compose/docker-compose.yml", "./backend/Dockerfile", "./frontend/Dockerfile") {
    if (-not ($listing -contains $required)) { throw "The bundle has no $required - not deploying." }
}
Write-Host ("    {0} KB" -f [math]::Round((Get-Item $bundle).Length / 1KB))
if ($DryRun) {
    Write-Host ("==> Dry run - packaged in {0:mm\:ss}, nothing sent" -f $watch.Elapsed) -ForegroundColor Yellow
    return
}

Write-Host "==> Uploading and applying on $Server" -ForegroundColor Cyan
$remote = "set -e; cat > /tmp/brt360_docker_deploy_$Environment.tar.gz; " +
          "tar -xzf /tmp/brt360_docker_deploy_$Environment.tar.gz -O ./deploy/compose/apply.sh | sed 's/\r$//' > /tmp/brt360_apply_docker_$Environment.sh; " +
          "bash /tmp/brt360_apply_docker_$Environment.sh /tmp/brt360_docker_deploy_$Environment.tar.gz $AppDir"
# cmd.exe's "<" streams the file byte-for-byte (a PowerShell pipe would re-encode it)
cmd /c "ssh -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $Server `"$remote`" < `"$bundle`""
$sshExit = $LASTEXITCODE
if ($sshExit -eq 255) { throw "SSH connection/authentication failed or was interrupted - check with: ssh $Server" }
if ($sshExit -ne 0) { throw "Remote apply exited with code $sshExit - the server printed the rollback steps above." }
Write-Host ("==> Deployed in {0:mm\:ss}" -f $watch.Elapsed) -ForegroundColor Green

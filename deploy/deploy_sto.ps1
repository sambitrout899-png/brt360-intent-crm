# Ships the Sales-to-Order release from this PC to the CRM server and runs
# deploy/apply_sto.sh there (backup code + database -> extract -> build /
# migrate / test -> restart). The release only adds; nothing existing is
# removed or reset.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_sto.ps1" [-Server root@187.127.132.149] [-AppDir /opt/brt360] [-Package] [-SkipTests]
#
#   -Package    rebuild brt360_full_deploy.tar.gz first (tests + UI build), via package_full.ps1
#   -SkipTests  skip the backend test suite on the server

param(
    [string]$Server = "root@187.127.132.149",
    [string]$AppDir = "/opt/brt360",
    [switch]$Package,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $root "brt360_full_deploy.tar.gz"
$script = Join-Path $PSScriptRoot "apply_sto.sh"

if ($Package -or -not (Test-Path $bundle)) {
    & (Join-Path $PSScriptRoot "package_full.ps1")
}
if (-not (Test-Path $bundle)) { throw "No bundle at $bundle" }

Write-Host "==> Copying bundle + apply_sto.sh to ${Server}:/tmp/" -ForegroundColor Cyan
scp $bundle $script "${Server}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

$skip = if ($SkipTests) { "SKIP_TESTS=1 " } else { "" }
Write-Host "==> Running apply_sto.sh on $Server" -ForegroundColor Cyan
ssh -t $Server "sed -i 's/\r$//' /tmp/apply_sto.sh && ${skip}bash /tmp/apply_sto.sh /tmp/brt360_full_deploy.tar.gz $AppDir"
if ($LASTEXITCODE -ne 0) { throw "Deploy failed on the server - see the output above for the rollback command." }

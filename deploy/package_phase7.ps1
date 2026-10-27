# Builds phase7_deploy.tar.gz - an overlay of every file Phase 7 added or
# changed (same shape as phase6_deploy.tar.gz) plus the deploy scripts.
# Verifies the backend tests and the frontend build first, so a broken
# tree never gets packaged.
#
# Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File deploy\package_phase7.ps1 [-SkipChecks]

param([switch]$SkipChecks)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$manifest = Get-Content "deploy\phase7_manifest.txt" | Where-Object { $_.Trim() }
$missing = $manifest | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "Manifest files missing:`n$($missing -join "`n")" }

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

$files = @($manifest) + @(
    "deploy/phase7_manifest.txt",
    "deploy/apply_phase7.sh",
    "deploy/apply_phase7.ps1",
    "deploy/rollback_phase7.sh",
    "deploy/brt360.sh"
)
$list = Join-Path $env:TEMP "phase7_filelist.txt"
# LF-only, no BOM: tar takes every byte of a line as part of the name.
[IO.File]::WriteAllText($list, ($files -join "`n") + "`n")

# Windows' bsdtar, not a GNU tar from Git/MSYS earlier on PATH (GNU tar
# reads "D:\..." as host:path).
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $tar)) { $tar = "tar" }

$out = "phase7_deploy.tar.gz"
if (Test-Path $out) { Remove-Item $out -Confirm:$false }
& $tar -czf $out -T $list
if ($LASTEXITCODE -ne 0) { throw "tar failed" }
Remove-Item $list -Confirm:$false

Write-Host "==> Built $root\$out ($((& $tar -tzf $out | Measure-Object).Count) files)" -ForegroundColor Green

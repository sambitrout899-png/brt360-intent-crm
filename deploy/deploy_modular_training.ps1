# Windows entrypoint for updating an existing Linux installation.
# Run with -PackageOnly to build the archive without connecting to a server.
[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*$')]
    [string]$Server,
    [string]$PublicOrigin,
    [ValidatePattern('^/[a-zA-Z0-9_./-]+$')]
    [string]$AppDir = '/opt/brt360',
    [ValidatePattern('^[a-zA-Z0-9_@.-]+$')]
    [string]$Service = 'brt360-api',
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$SshPort = 22,
    [string]$IdentityFile,
    [switch]$PackageOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $root 'brt360_full_deploy.tar.gz'
$linuxScript = Join-Path $PSScriptRoot 'deploy_modular_training.sh'

if (-not $PackageOnly) {
    if (-not $Server -or -not $PublicOrigin) {
        throw 'Supply -Server user@server and -PublicOrigin https://your-domain, or use -PackageOnly.'
    }
    $origin = $null
    if (-not [Uri]::TryCreate($PublicOrigin, [UriKind]::Absolute, [ref]$origin) -or
        $origin.Scheme -notin @('http', 'https') -or -not $origin.Host -or
        $origin.UserInfo -or $origin.Query -or $origin.Fragment -or
        $origin.AbsolutePath -ne '/') {
        throw 'PublicOrigin must be an HTTP(S) origin without credentials, path, query or fragment.'
    }
    $PublicOrigin = $origin.GetLeftPart([UriPartial]::Authority)
    # Keep remote shell arguments limited to literal, quoted values.
    if ($PublicOrigin -notmatch '^https?://[a-zA-Z0-9.\[\]:-]+$') { throw 'Unsupported origin hostname.' }
    if ($AppDir -eq '/' -or $AppDir.Split('/') -contains '..') { throw 'Use an absolute application directory without parent traversal.' }
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $scp = (Get-Command scp.exe -ErrorAction Stop).Source
    $sshOptions = @('-p', "$SshPort", '-o', 'ServerAliveInterval=30')
    $scpOptions = @('-P', "$SshPort")
    if ($IdentityFile) {
        $IdentityFile = (Resolve-Path -LiteralPath $IdentityFile -ErrorAction Stop).Path
        $sshOptions += @('-i', $IdentityFile)
        $scpOptions += @('-i', $IdentityFile)
    }
}

Push-Location $root
try {
    Write-Host 'Packaging application (build and regression tests run on the server)...' -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot 'package_full.ps1') -SkipChecks
    if (-not (Test-Path -LiteralPath $bundle)) { throw 'Package was not created.' }
    if ($PackageOnly) { Write-Host "Package ready: $bundle"; return }

    $remoteDir = '/tmp/brt360-modular-' + [Guid]::NewGuid().ToString('N')
    Write-Host "Uploading to ${Server}:$remoteDir" -ForegroundColor Cyan
    & $ssh @sshOptions $Server "umask 077 && mkdir '$remoteDir'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create remote staging directory.' }
    & $scp @scpOptions $bundle $linuxScript "${Server}:$remoteDir/"
    if ($LASTEXITCODE -ne 0) { throw "Upload failed. Remote staging: $remoteDir" }

    $run = "env SERVICE='$Service' API_PORT='$ApiPort' bash '$remoteDir/deploy_modular_training.sh' '$AppDir' '$remoteDir/brt360_full_deploy.tar.gz' '$PublicOrigin'"
    $command = "sed -i 's/\r$//' '$remoteDir/deploy_modular_training.sh' && " +
        'if [ "$(id -u)" -eq 0 ]; then ' + $run + '; else sudo ' + $run + '; fi'
    Write-Host 'Deploying: build, backup, tests, migrations, restart and health checks. Allow a maintenance window.' -ForegroundColor Cyan
    & $ssh @sshOptions -t $Server $command
    if ($LASTEXITCODE -ne 0) {
        throw "Deployment failed. Review output for the recovery backup. Uploaded files retained at $remoteDir."
    }
    Write-Host "Deployment completed: $PublicOrigin" -ForegroundColor Green
    Write-Host "Uploaded release retained at $remoteDir; recovery backup location is shown above."
}
finally { Pop-Location }

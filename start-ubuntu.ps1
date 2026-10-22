param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start.ps1') -Ubuntu -CheckOnly:$CheckOnly
exit $LASTEXITCODE

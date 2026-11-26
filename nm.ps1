$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'scripts\nm.ps1'
& $launcher @args
exit $LASTEXITCODE

$env:PYTHONUTF8 = '1'
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$nmHome = if ($env:NICHE_HOME) { $env:NICHE_HOME } else { Join-Path $env:USERPROFILE '.niche-machine' }
$python = Join-Path $nmHome 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    if ($args[0] -in @('setup','doctor')) { py -3.12 (Join-Path $dir 'nm.py') @args; exit $LASTEXITCODE }
    throw 'Niche Machine is not set up. Run: .\nm.ps1 setup'
}
$env:PYTHONDONTWRITEBYTECODE = '1'
& $python (Join-Path $dir 'nm.py') @args
exit $LASTEXITCODE



param([Parameter(ValueFromRemainingArguments=$true)][string[]]$InstallerArgs)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$existing = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $existing) {
    & $existing (Join-Path $projectRoot 'install.py') @InstallerArgs
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    & py.exe -3 (Join-Path $projectRoot 'install.py') @InstallerArgs
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    & python.exe (Join-Path $projectRoot 'install.py') @InstallerArgs
} else {
    throw 'Python 3.10+ is required. Please install Python and enable Add Python to PATH.'
}
exit $LASTEXITCODE

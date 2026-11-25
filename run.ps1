$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$seasonPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $seasonPython)) {
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Нужен Python 3.13.' }
    & $seasonPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
}
& $seasonPython -m season_ledger @args

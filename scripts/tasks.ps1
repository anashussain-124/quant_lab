param(
    [ValidateSet("test", "lint", "typecheck")]
    [string]$Task = "test"
)

$Python = Join-Path $PSScriptRoot "..\qt\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Configured project interpreter not found: $Python"
}

switch ($Task) {
    "test" { & $Python -m pytest -q }
    "lint" { & $Python -m ruff check . }
    "typecheck" { & $Python -m mypy --strict configs features hypotheses search validation risk portfolio sim execution orchestrator_v4 reports_v4 }
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

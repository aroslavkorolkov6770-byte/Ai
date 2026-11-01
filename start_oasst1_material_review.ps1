$ErrorActionPreference = "Stop"

# Выборочная проверка материала отделена от чата и review тем.
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Virtual environment was not found. Follow the setup commands in README.md."
    exit 1
}

Set-Location -LiteralPath $projectRoot
Write-Host "Local OASST1 material review: http://127.0.0.1:8014/"
Write-Host "Only one material review server should write this ledger. Press Ctrl+C to stop."

try {
    & $pythonPath -m evaluation.oasst1_material_review_web --port 8014
} catch {
    Write-Error "Could not start the local material review server: $($_.Exception.Message)"
    exit 1
}

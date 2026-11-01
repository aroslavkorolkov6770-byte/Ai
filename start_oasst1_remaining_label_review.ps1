$ErrorActionPreference = "Stop"

# Вторая партия проверяется отдельно от завершённого пилота на порту 8012.
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Virtual environment was not found. Follow the setup commands in README.md."
    exit 1
}

Set-Location -LiteralPath $projectRoot
Write-Host "Remaining OASST1 topic review: http://127.0.0.1:8013/"
Write-Host "Only one server should write this batch ledger. Press Ctrl+C to stop."

try {
    & $pythonPath -m evaluation.oasst1_label_review_web --batch remaining --port 8013
} catch {
    Write-Error "Could not start the local review server: $($_.Exception.Message)"
    exit 1
}

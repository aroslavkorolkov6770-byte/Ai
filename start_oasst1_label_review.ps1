$ErrorActionPreference = "Stop"

# Экран работает отдельно от чата и прежней проверки качества диалогов.
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Virtual environment was not found. Follow the setup commands in README.md."
    exit 1
}

Set-Location -LiteralPath $projectRoot
Write-Host "Local OASST1 topic review: http://127.0.0.1:8012/"
Write-Host "Only one review server should write this ledger. Press Ctrl+C to stop."

try {
    & $pythonPath -m evaluation.oasst1_label_review_web --port 8012
} catch {
    Write-Error "Could not start the local label review server: $($_.Exception.Message)"
    exit 1
}

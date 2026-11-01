$ErrorActionPreference = "Stop"

# Отдельный экран проверяет только новый пилот, а не рабочий чат.
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Virtual environment was not found. See README.md."
    exit 1
}

Set-Location -LiteralPath $projectRoot
Write-Host "OASST2 quality review: http://127.0.0.1:8015/"
Write-Host "Use only one review server for this ledger. Press Ctrl+C to stop."

try {
    & $pythonPath -m evaluation.oasst2_quality_review_web --port 8015
    if ($LASTEXITCODE -ne 0) {
        throw "Review server exited with code $LASTEXITCODE."
    }
} catch {
    Write-Error "Could not start the review server: $($_.Exception.Message)"
    exit 1
}

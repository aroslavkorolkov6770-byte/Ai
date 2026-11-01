$ErrorActionPreference = "Stop"

# The script can be started from any folder and finds the project root itself.
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Virtual environment was not found. Follow the setup commands in README.md."
    exit 1
}

Set-Location -LiteralPath $projectRoot
Write-Host "Starting local OASST1 review: http://127.0.0.1:8011/"
Write-Host "This screen is local only. Press Ctrl+C to stop the review server."

try {
    & $pythonPath -m evaluation.oasst1_review_web --host 127.0.0.1 --port 8011
} catch {
    Write-Error "Could not start the local review server: $($_.Exception.Message)"
    exit 1
}

$ErrorActionPreference = "Stop"

# The script can be started from any folder and finds the project root itself.
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Virtual environment was not found. Follow the setup commands in README.md."
    exit 1
}

Set-Location -LiteralPath $projectRoot
Write-Host "Starting local API: http://127.0.0.1:8000/"
Write-Host "Press Ctrl+C to stop the server."

try {
    & $pythonPath -m uvicorn ai_core.api_server:app --host 127.0.0.1 --port 8000
} catch {
    Write-Error "Could not start the API: $($_.Exception.Message)"
    exit 1
}

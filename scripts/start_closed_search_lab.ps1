param([switch]$RestartSearch, [switch]$RestartExporter, [switch]$SkipExporter)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$labRoot = Join-Path $taskRoot '.runtime\closed-search-lab-20261028'
$siteRoot = Join-Path $labRoot 'tea-app-main'
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
$nodePath = (Get-Command node -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath (Join-Path $siteRoot '.env.local'))) { throw 'Сначала требуется настройка изолированного стенда.' }

# Перезапускается только наш процесс закрытого поиска. Рабочая LMS не затрагивается.
$searchListener = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($searchListener) {
    $searchWorker = Get-CimInstance Win32_Process -Filter "ProcessId = $($searchListener.OwningProcess)"
    $searchParent = Get-CimInstance Win32_Process -Filter "ProcessId = $($searchWorker.ParentProcessId)"
    if ($searchWorker.CommandLine -notlike '*ai_core.site_search_api:app*' -or $searchParent.CommandLine -notlike "*$taskRoot\.venv\Scripts\python.exe*ai_core.site_search_api:app*") { throw 'Порт 8001 занят другим процессом.' }
    if ($RestartSearch) {
        Stop-Process -Id $searchWorker.ProcessId,$searchParent.ProcessId -Force
        $searchListener = $null
    }
}
if (-not $searchListener) {
    $searchProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','ai_core.site_search_api:app','--host','127.0.0.1','--port','8001') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot 'search.stdout.log') -RedirectStandardError (Join-Path $labRoot 'search.stderr.log') -PassThru
    Write-Output "Поиск запущен: $($searchProcess.Id)"
}
$siteListener = Get-NetTCPConnection -LocalPort 3040 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($siteListener) {
    $siteProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($siteListener.OwningProcess)"
    if ($siteProcess.CommandLine -notlike "*$siteRoot*next*") { throw 'Порт 3040 занят другим процессом.' }
} else {
    & $nodePath (Join-Path $siteRoot 'scripts\prepare-pdf-viewer.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить локальный просмотр PDF.' }
    $siteProcess = Start-Process -FilePath $nodePath -ArgumentList @((Join-Path $siteRoot 'node_modules\next\dist\bin\next'),'dev','--hostname','127.0.0.1','--port','3040') -WorkingDirectory $siteRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot 'site.stdout.log') -RedirectStandardError (Join-Path $labRoot 'site.stderr.log') -PassThru
    Write-Output "Тестовый сайт запущен: $($siteProcess.Id)"
}
$exportScript = Join-Path $taskRoot 'evaluation\closed_search\export_site.cjs'
$exporters = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like "*$exportScript*" })
if ($RestartExporter -and $exporters.Count -gt 0) {
    # Найден именно обработчик этого стенда по полному пути, не чужой Node-процесс.
    $exporters | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    $exporters = @()
}
if (-not $exporters -and -not $SkipExporter) {
    $exportProcess = Start-Process -FilePath $nodePath -ArgumentList @($exportScript) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot 'export.stdout.log') -RedirectStandardError (Join-Path $labRoot 'export.stderr.log') -PassThru
    Write-Output "Фоновый обработчик запущен: $($exportProcess.Id)"
}
& $nodePath (Join-Path $taskRoot 'evaluation\closed_search\wait_ready.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Поиск запущен, но ещё не готов к вопросам.' }
& $nodePath (Join-Path $taskRoot 'evaluation\closed_search\wait_site_ready.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Поиск готов, но маршруты тестового сайта ещё не работают.' }
Write-Output 'Адрес стенда: http://127.0.0.1:3040/tasks?tab=standards'

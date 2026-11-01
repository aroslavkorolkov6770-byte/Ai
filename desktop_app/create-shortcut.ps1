$ErrorActionPreference = 'Stop'
try {
    $appExecutable = Join-Path $PSScriptRoot 'dist\VatesAI-win32-x64\VatesAI.exe'
    if (-not (Test-Path -LiteralPath $appExecutable)) { throw 'Сначала соберите приложение.' }
    $desktopDirectory = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktopDirectory 'Ватэс AI.lnk'
    $shortcutShell = New-Object -ComObject WScript.Shell
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $appExecutable) {
        throw 'На рабочем столе уже есть другой ярлык Ватэс AI. Он не изменён.'
    }
    $shortcut.TargetPath = $appExecutable
    $shortcut.WorkingDirectory = Split-Path -Parent $appExecutable
    $shortcut.IconLocation = "$appExecutable,0"
    $shortcut.Description = 'Собственная нейросеть: чат и управление локальным API'
    $shortcut.Save()
    Write-Output $shortcutPath
} catch {
    Write-Error "Не удалось создать ярлык: $($_.Exception.Message)"
    exit 1
}

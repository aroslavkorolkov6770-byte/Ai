$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

# Рисуем собственный векторный знак и сохраняем PNG внутри стандартного ICO.
$bitmap = New-Object System.Drawing.Bitmap 256, 256
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$stream = New-Object System.IO.MemoryStream
$green = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(35, 66, 51))
$light = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(217, 233, 187)), 19
try {
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.FillEllipse($green, 4, 4, 248, 248)
    $light.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $light.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $graphics.DrawLine($light, 70, 80, 126, 177)
    $graphics.DrawLine($light, 126, 177, 186, 80)
    $graphics.DrawLine($light, 115, 79, 139, 119)
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $payload = $stream.ToArray()
    $iconPath = Join-Path $PSScriptRoot 'vates.ico'
    $output = [System.IO.File]::Create($iconPath)
    $writer = New-Object System.IO.BinaryWriter $output
    try {
        $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]1)
        $writer.Write([byte]0); $writer.Write([byte]0); $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$payload.Length); $writer.Write([uint32]22)
        $writer.Write($payload)
    } finally { $writer.Dispose(); $output.Dispose() }
    Write-Output $iconPath
} finally {
    $light.Dispose(); $green.Dispose(); $stream.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
}

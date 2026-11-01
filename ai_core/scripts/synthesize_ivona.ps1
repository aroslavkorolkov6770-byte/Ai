[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$InputPath = [System.Environment]::GetEnvironmentVariable('VATES_AI_TTS_INPUT_PATH'),

    [ValidateNotNullOrEmpty()]
    [string]$OutputPath = [System.Environment]::GetEnvironmentVariable('VATES_AI_TTS_OUTPUT_PATH')
)

# Этот сценарий запускается только локальным API. Он не получает текст как
# фрагмент PowerShell-команды: API передаёт его во временном UTF-8 файле.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw 'Input file for IVONA synthesis was not found.'
}

$inputFullPath = [System.IO.Path]::GetFullPath($InputPath)
$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$text = [System.IO.File]::ReadAllText($inputFullPath, [System.Text.Encoding]::UTF8).Trim()

if ([string]::IsNullOrWhiteSpace($text)) {
    throw 'IVONA synthesis text is empty.'
}

if ($text.Length -gt 4000) {
    throw 'IVONA synthesis text is too long.'
}

Add-Type -AssemblyName System.Speech
$synthesizer = [System.Speech.Synthesis.SpeechSynthesizer]::new()

try {
    # Единственный допустимый голос ВатэсAi. Ни default voice, ни другой
    # доступный системный голос здесь не используются как незаметный fallback.
    $synthesizer.SelectVoice('IVONA 2 Tatyana OEM')
    $synthesizer.Rate = 0
    $synthesizer.Volume = 100
    $synthesizer.SetOutputToWaveFile($outputFullPath)
    $synthesizer.Speak($text)
}
finally {
    $synthesizer.Dispose()
}

if (-not (Test-Path -LiteralPath $outputFullPath -PathType Leaf)) {
    throw 'IVONA did not create the WAV file.'
}

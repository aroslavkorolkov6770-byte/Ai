"""Локальная озвучка ответов ВатэсAi через установленный Windows SAPI-голос.

Модуль намеренно не использует облачный TTS и не передаёт текст за пределы
компьютера. Текст записывается только во временный файл на время синтеза,
после чего WAV читается в память, а временная папка автоматически удаляется.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


IVONA_TATYANA_VOICE_NAME = "IVONA 2 Tatyana OEM"
MAX_TTS_INPUT_CHARACTERS = 4_000
MAX_TTS_AUDIO_BYTES = 25 * 1024 * 1024
SYNTHESIS_TIMEOUT_SECONDS = 45
SYNTHESIS_SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "synthesize_ivona.ps1"


class LocalVoiceError(RuntimeError):
    """Поднимается, когда локальная Windows-озвучка недоступна."""


def _powershell_executable() -> str:
    """Находит штатный PowerShell, не полагаясь на пользовательский shell."""

    # PowerShell 7 корректно видит IVONA в текущей Windows-конфигурации.
    # Windows PowerShell 5.1 на этой системе видит запись голоса, но сам
    # SelectVoice аварийно завершается, поэтому он оставлен лишь последним
    # диагностическим fallback, а не используется при наличии pwsh.
    for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        executable = shutil.which(candidate)
        if executable:
            return executable
    raise LocalVoiceError("Локальный PowerShell для IVONA не найден.")


def _clean_text(text: str) -> str:
    """Проверяет ограниченный текст для синтеза без журналирования его содержимого."""

    cleaned = text.strip()
    if not cleaned:
        raise LocalVoiceError("Для озвучивания нужен непустой текст.")
    if len(cleaned) > MAX_TTS_INPUT_CHARACTERS:
        raise LocalVoiceError("Текст ответа слишком длинный для локальной озвучки.")
    return cleaned


def synthesize_ivona_tatyana(text: str) -> bytes:
    """Синтезирует WAV установленным IVONA Tatyana без shell-интерполяции текста.

    Аргументы процесса передаются списком, а сам текст — только через UTF-8
    временный файл. Это исключает выполнение фразы пользователя как команды
    PowerShell и не оставляет аудио или исходный текст в папке проекта.
    """

    cleaned_text = _clean_text(text)
    if not SYNTHESIS_SCRIPT_PATH.is_file():
        raise LocalVoiceError("Сценарий локальной озвучки IVONA не найден.")
    try:
        synthesis_script = SYNTHESIS_SCRIPT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise LocalVoiceError("Не удалось прочитать сценарий локальной озвучки IVONA.") from error

    with tempfile.TemporaryDirectory(prefix="vates-ai-ivona-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        input_path = temporary_path / "answer.txt"
        output_path = temporary_path / "answer.wav"
        input_path.write_text(cleaned_text, encoding="utf-8")
        child_environment = os.environ.copy()
        child_environment["VATES_AI_TTS_INPUT_PATH"] = str(input_path)
        child_environment["VATES_AI_TTS_OUTPUT_PATH"] = str(output_path)

        try:
            completed = subprocess.run(
                [
                    _powershell_executable(),
                    "-NoProfile",
                    "-NonInteractive",
                    # -File блокируется политикой исполнения для созданных
                    # файлов на некоторых Windows. -Command - выполняет этот
                    # же проверяемый локальный сценарий из stdin и не меняет
                    # политику Windows. Сам текст ответа остаётся в temp-файле.
                    "-Command",
                    "-",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SYNTHESIS_TIMEOUT_SECONDS,
                input=synthesis_script,
                env=child_environment,
            )
        except subprocess.TimeoutExpired as error:
            raise LocalVoiceError("Локальная озвучка IVONA превысила лимит времени.") from error
        except OSError as error:
            raise LocalVoiceError("Не удалось запустить локальную озвучку IVONA.") from error

        if completed.returncode != 0:
            # Не возвращаем stdout/stderr клиенту: они могут содержать локальные
            # пути. Сам текст ответа в команду не передавался и в журнал не попадает.
            raise LocalVoiceError("IVONA не смогла сформировать аудиофайл.")

        try:
            audio_size = output_path.stat().st_size
            if audio_size <= 44:
                raise LocalVoiceError("IVONA вернула пустой аудиофайл.")
            if audio_size > MAX_TTS_AUDIO_BYTES:
                raise LocalVoiceError("Аудиоответ IVONA оказался слишком большим.")
            return output_path.read_bytes()
        except OSError as error:
            raise LocalVoiceError("Не удалось прочитать аудио IVONA.") from error

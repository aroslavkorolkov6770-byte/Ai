"""Проверки локальной IVONA-озвучки без реального проигрывания аудио в тестах."""

from __future__ import annotations

import asyncio
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from ai_core import api_server
from ai_core.local_tts import (
    IVONA_TATYANA_VOICE_NAME,
    LocalVoiceError,
    MAX_TTS_INPUT_CHARACTERS,
    synthesize_ivona_tatyana,
)


class LocalTtsTests(unittest.TestCase):
    """Проверяет границы данных и безопасный запуск штатного SAPI-сценария."""

    def test_rejects_empty_or_oversized_text(self) -> None:
        with self.assertRaises(LocalVoiceError):
            synthesize_ivona_tatyana("   ")

        with self.assertRaises(LocalVoiceError):
            synthesize_ivona_tatyana("а" * (MAX_TTS_INPUT_CHARACTERS + 1))

    def test_text_is_written_to_temp_file_not_interpolated_into_command(self) -> None:
        captured: dict[str, object] = {}
        answer_text = "Фраза должна попасть только во временный файл."

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["command"] = command
            environment = kwargs["env"]
            self.assertIsInstance(environment, dict)
            input_path = Path(environment["VATES_AI_TTS_INPUT_PATH"])
            output_path = Path(environment["VATES_AI_TTS_OUTPUT_PATH"])
            captured["input_text"] = input_path.read_text(encoding="utf-8")
            captured["output_path"] = output_path
            captured["script"] = kwargs["input"]
            output_path.write_bytes(b"RIFF" + (b"0" * 41))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("ai_core.local_tts.shutil.which", return_value="powershell.exe"), patch(
            "ai_core.local_tts.subprocess.run",
            side_effect=fake_run,
        ):
            audio = synthesize_ivona_tatyana(answer_text)

        command = captured["command"]
        self.assertIsInstance(command, list)
        self.assertEqual(captured["input_text"], answer_text)
        self.assertNotIn(answer_text, " ".join(command))
        self.assertIn("-NoProfile", command)
        self.assertIn("-NonInteractive", command)
        self.assertIn("-Command", command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn(answer_text, captured["script"])
        self.assertEqual(audio[:4], b"RIFF")
        self.assertFalse(Path(captured["output_path"]).exists())

    def test_subprocess_error_does_not_expose_local_paths(self) -> None:
        failed = subprocess.CompletedProcess(["powershell.exe"], 1, "", "C:\\secret\\path")
        with patch("ai_core.local_tts.shutil.which", return_value="powershell.exe"), patch(
            "ai_core.local_tts.subprocess.run",
            return_value=failed,
        ), self.assertRaises(LocalVoiceError) as error:
            synthesize_ivona_tatyana("Проверка ошибки")

        self.assertNotIn("C:\\secret", str(error.exception))

    def test_api_returns_ephemeral_wav_without_audio_storage(self) -> None:
        wav = b"RIFF" + (b"0" * 41)
        with patch.object(api_server, "synthesize_ivona_tatyana", return_value=wav):
            response = asyncio.run(
                api_server.synthesize_voice(
                    api_server.VoiceSynthesisRequest(text="Короткий ответ")
                )
            )

        self.assertEqual(response.media_type, "audio/wav")
        self.assertEqual(response.body, wav)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_api_reports_unavailable_voice_without_fallback(self) -> None:
        with patch.object(
            api_server,
            "synthesize_ivona_tatyana",
            side_effect=LocalVoiceError("voice unavailable"),
        ), self.assertRaises(HTTPException) as error:
            asyncio.run(
                api_server.synthesize_voice(
                    api_server.VoiceSynthesisRequest(text="Короткий ответ")
                )
            )

        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn("Microsoft", str(error.exception.detail))

    def test_synthesis_script_pins_the_one_configured_voice(self) -> None:
        script_path = Path(__file__).resolve().parent.parent / "ai_core" / "scripts" / "synthesize_ivona.ps1"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn(f"SelectVoice('{IVONA_TATYANA_VOICE_NAME}')", script)
        self.assertNotIn("SetOutputToDefaultAudioDevice", script)


if __name__ == "__main__":
    unittest.main()

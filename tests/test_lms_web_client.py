"""Регрессия единого браузерного чата без LMS-переключателя."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class LmsWebClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = (ROOT / "web_client" / "index.html").read_text(encoding="utf-8")

    def test_chat_has_no_lms_switch_or_context_field(self) -> None:
        self.assertNotIn('id="lmsMode"', self.client)
        self.assertNotIn('id="lmsContext"', self.client)
        self.assertNotIn('id="lmsContextGroup"', self.client)
        self.assertIn('Один чат:', self.client)

    def test_browser_always_uses_automatic_route(self) -> None:
        self.assertIn("response_mode: 'auto'", self.client)
        self.assertNotIn('id="internetSearch"', self.client)
        self.assertNotIn("use_internet:", self.client)
        self.assertNotIn("lms_context:", self.client)
        self.assertNotIn("lms-knowledge-", self.client)
        self.assertNotIn("LMS-", self.client)
        self.assertIn("проверяемых источников", self.client)

    def test_browser_uses_location_only_for_weather_and_does_not_persist_it(self) -> None:
        self.assertIn("navigator.geolocation.getCurrentPosition", self.client)
        self.assertIn("const weatherRequest = isWeatherMessage(messageText);", self.client)
        self.assertIn("if (weatherRequest && browserLocation)", self.client)
        self.assertIn("browserLocationReceivedAt", self.client)
        self.assertIn("location_accuracy_m", self.client)
        self.assertIn("const weatherCorePattern", self.client)
        self.assertIn("const weatherClothingPattern", self.client)
        self.assertIn(
            "weatherClothingPattern.test(messageText) && weatherTimePattern.test(messageText)",
            self.client,
        )
        self.assertNotIn("weatherFollowupPattern", self.client)
        self.assertNotIn("зонт|одеж|одет|надет", self.client)
        self.assertNotIn("localStorage.setItem('ai-browser-location'", self.client)

    def test_browser_feedback_sends_only_opaque_token_and_rating(self) -> None:
        self.assertIn("appendFeedbackActions(data.feedback_token)", self.client)
        self.assertIn("feedback_token: feedbackToken", self.client)
        self.assertIn("rating", self.client)
        self.assertIn("'Полезно'", self.client)
        self.assertIn("'Нужна доработка'", self.client)
        self.assertNotIn("feedback_conversation_id", self.client)

    def test_chat_has_a_single_responsive_message_workspace(self) -> None:
        self.assertIn('role="log"', self.client)
        self.assertIn('class="message-input"', self.client)
        self.assertIn("Shift+Enter — новая строка", self.client)
        self.assertIn("appendTypingIndicator", self.client)
        self.assertIn("@media (max-width: 880px)", self.client)
        self.assertNotIn('id="conversationList"', self.client)
        self.assertNotIn('id="manualContext"', self.client)

    def test_voice_mode_is_explicit_and_does_not_store_audio(self) -> None:
        self.assertIn('id="voiceModeToggle"', self.client)
        self.assertIn('id="micButton"', self.client)
        self.assertIn('id="voiceFixedName"', self.client)
        self.assertIn("SpeechRecognitionConstructor", self.client)
        self.assertIn("IVONA 2 Tatyana", self.client)
        self.assertIn("'/voice/synthesize'", self.client)
        self.assertIn("new Audio(audioUrl)", self.client)
        self.assertIn("URL.createObjectURL(audioBlob)", self.client)
        self.assertIn("speechAbortController.abort()", self.client)
        self.assertIn("localStorage.setItem('ai-voice-mode'", self.client)
        self.assertIn("Разрешите его в настройках браузера", self.client)
        self.assertNotIn('id="voiceSelect"', self.client)
        self.assertNotIn("window.speechSynthesis", self.client)
        self.assertNotIn("SpeechSynthesisUtterance", self.client)
        self.assertNotIn("MediaRecorder", self.client)
        self.assertNotIn("ai-voice-audio", self.client)


if __name__ == "__main__":
    unittest.main()

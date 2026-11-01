"""Проверки собственных Brain v3 BPE и Unigram-подобного tokenizers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_tokenizer import (
    BrainV3BPETokenizer,
    BrainV3Tokenizer,
    BrainV3UnigramTokenizer,
    load_brain_v3_tokenizer,
    tokenizer_file_sha256,
)


TRAIN_TEXTS = (
    "Привет, мир!",
    "Я хочу спокойно обсудить новую идею.",
    "Сегодня 42 задачи, а завтра будет меньше.",
    "Код: value = value + 1.",
    "Русский текст должен сохранять пробелы и пунктуацию.",
)


class BrainV3TokenizerTests(unittest.TestCase):
    """Проверяет неизменяемые свойства до обучения будущей модели."""

    def _build_candidates(self) -> tuple[BrainV3BPETokenizer, BrainV3UnigramTokenizer]:
        bpe = BrainV3BPETokenizer()
        bpe.build(TRAIN_TEXTS, vocabulary_size=512)
        unigram = BrainV3UnigramTokenizer()
        unigram.build(TRAIN_TEXTS, vocabulary_size=512)
        return bpe, unigram

    def test_both_tokenizers_round_trip_known_and_unknown_unicode(self) -> None:
        bpe, unigram = self._build_candidates()
        text = "Привет, новый мир! 漢字 и value_42."

        for tokenizer in (bpe, unigram):
            identifiers = tokenizer.encode(text, add_bos=True, add_eos=True)
            self.assertEqual(tokenizer.decode(identifiers), text)
            self.assertNotIn(tokenizer.unk_id, identifiers)

    def test_user_text_cannot_create_control_token(self) -> None:
        bpe, unigram = self._build_candidates()
        text = "Напечатай <role_user> только как обычный текст."

        for tokenizer in (bpe, unigram):
            identifiers = tokenizer.encode(text)
            self.assertNotIn(tokenizer.control_id("<role_user>"), identifiers)
            self.assertNotIn(tokenizer.control_id("<tool>"), identifiers)
            self.assertEqual(tokenizer.decode(identifiers), text)

    def test_encode_dialogue_adds_roles_only_inside_program(self) -> None:
        bpe, unigram = self._build_candidates()
        turns = [
            {"role": "user", "text": "Привет"},
            {"role": "assistant", "text": "Здравствуйте"},
        ]

        for tokenizer in (bpe, unigram):
            identifiers = tokenizer.encode_dialogue(turns)
            self.assertEqual(identifiers[0], tokenizer.bos_id)
            self.assertEqual(identifiers[1], tokenizer.control_id("<role_user>"))
            self.assertIn(tokenizer.control_id("<role_assistant>"), identifiers)
            self.assertEqual(identifiers[-1], tokenizer.eos_id)
            self.assertEqual(
                identifiers.count(tokenizer.end_turn_id),
                2,
            )

    def test_build_is_deterministic(self) -> None:
        first_bpe = BrainV3BPETokenizer()
        second_bpe = BrainV3BPETokenizer()
        first_bpe.build(TRAIN_TEXTS, vocabulary_size=512)
        second_bpe.build(TRAIN_TEXTS, vocabulary_size=512)
        self.assertEqual(first_bpe.token_to_id, second_bpe.token_to_id)
        self.assertEqual(first_bpe.merges, second_bpe.merges)

        first_unigram = BrainV3UnigramTokenizer()
        second_unigram = BrainV3UnigramTokenizer()
        first_unigram.build(TRAIN_TEXTS, vocabulary_size=512)
        second_unigram.build(TRAIN_TEXTS, vocabulary_size=512)
        self.assertEqual(first_unigram.token_to_id, second_unigram.token_to_id)
        self.assertEqual(first_unigram.piece_scores, second_unigram.piece_scores)

    def test_saved_tokenizer_keeps_encoding_and_hash(self) -> None:
        bpe, unigram = self._build_candidates()
        text = "Проверяем сохранение токенизатора."
        with tempfile.TemporaryDirectory() as directory:
            for name, tokenizer in (("bpe", bpe), ("unigram", unigram)):
                artifact_path = Path(directory) / f"{name}.json"
                tokenizer.save(artifact_path)
                loaded = load_brain_v3_tokenizer(artifact_path)
                self.assertIsInstance(loaded, BrainV3Tokenizer)
                self.assertEqual(loaded.encode(text), tokenizer.encode(text))
                self.assertEqual(loaded.decode(loaded.encode(text)), text)
                self.assertEqual(len(tokenizer_file_sha256(artifact_path)), 64)


if __name__ == "__main__":
    unittest.main()

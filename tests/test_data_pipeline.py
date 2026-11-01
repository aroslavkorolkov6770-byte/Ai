"""Базовые проверки датасета и собственной токенизации."""

from pathlib import Path
import tempfile
import unittest

from ai_core.dataset_tools import (
    dataset_fingerprint,
    normalize_prompt,
    prompt_overlap,
    split_dataset,
)
from ai_core.tokenizer import (
    CharacterTokenizer,
    SubwordBPETokenizer,
    WordTokenizer,
    load_tokenizer,
)
from ai_core.training_data import load_training_pairs


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = PROJECT_ROOT / "training_data" / "dataset.jsonl"


class DataPipelineTests(unittest.TestCase):
    def test_dataset_is_valid_and_not_empty(self) -> None:
        pairs = load_training_pairs(DATASET_PATH)
        self.assertEqual(len(pairs), 1329)
        self.assertTrue(all(pair.prompt and pair.completion for pair in pairs))

    def test_tokenizer_can_save_load_and_decode(self) -> None:
        tokenizer = WordTokenizer()
        tokenizer.build(["Привет, мир!", "Ответ готов."])
        encoded = tokenizer.encode("Привет, мир!", add_bos=True, add_eos=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer.json"
            tokenizer.save(path)
            loaded = WordTokenizer.load(path)
        self.assertEqual(loaded.decode(encoded), "Привет, мир!")

    def test_character_tokenizer_preserves_unknown_words(self) -> None:
        tokenizer = CharacterTokenizer()
        tokenizer.build(["Привет, мир!"])
        encoded = tokenizer.encode("Привет, три мир!", add_bos=True, add_eos=True)
        self.assertNotIn(tokenizer.unk_id, encoded)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "character-tokenizer.json"
            tokenizer.save(path)
            loaded = load_tokenizer(path)
        self.assertEqual(loaded.decode(encoded), "Привет, три мир!")

    def test_bpe_tokenizer_round_trip_is_shorter_than_characters(self) -> None:
        texts = [
            "привет привет привет",
            "привет, как дела?",
            "дела идут хорошо",
        ]
        tokenizer = SubwordBPETokenizer()
        tokenizer.build(texts, limit=48)
        source = "привет, как дела?"
        encoded = tokenizer.encode(source, add_bos=True, add_eos=True)

        self.assertEqual(tokenizer.decode(encoded), source)
        self.assertLess(len(encoded), len(source) + 2)
        self.assertGreater(len(tokenizer.merges), 0)

    def test_bpe_tokenizer_save_load_and_build_are_deterministic(self) -> None:
        texts = ["alpha beta alpha", "beta gamma beta"]
        first = SubwordBPETokenizer()
        second = SubwordBPETokenizer()
        first.build(texts, limit=32)
        second.build(texts, limit=32)

        self.assertEqual(first.token_to_id, second.token_to_id)
        self.assertEqual(first.merges, second.merges)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bpe-tokenizer.json"
            first.save(path)
            loaded = load_tokenizer(path)

        self.assertIsInstance(loaded, SubwordBPETokenizer)
        self.assertEqual(loaded.encode(texts[0]), first.encode(texts[0]))
        self.assertEqual(loaded.decode(loaded.encode(texts[1])), texts[1])

    def test_bpe_tokenizer_preserves_eos_when_truncated(self) -> None:
        tokenizer = SubwordBPETokenizer()
        tokenizer.build(["абвгд"], limit=10)
        encoded = tokenizer.encode(
            "абвгдабвгдабвгд",
            add_bos=True,
            add_eos=True,
            max_length=5,
        )

        self.assertEqual(len(encoded), 5)
        self.assertEqual(encoded[-1], tokenizer.eos_id)

    def test_dataset_split_is_deterministic_and_has_no_prompt_overlap(self) -> None:
        pairs = load_training_pairs(DATASET_PATH)
        first = split_dataset(pairs, validation_ratio=0.1, test_ratio=0.1, seed=42)
        second = split_dataset(pairs, validation_ratio=0.1, test_ratio=0.1, seed=42)

        self.assertEqual(first, second)
        self.assertEqual(
            len(first.train) + len(first.validation) + len(first.test),
            len(pairs),
        )
        self.assertTrue(first.train)
        self.assertTrue(first.validation)
        self.assertTrue(first.test)
        self.assertEqual(prompt_overlap(first), set())

    def test_dataset_fingerprint_changes_when_content_changes(self) -> None:
        pairs = load_training_pairs(DATASET_PATH)
        original = dataset_fingerprint(pairs)
        changed = list(pairs)
        changed[0] = type(pairs[0])(
            prompt=pairs[0].prompt,
            completion=pairs[0].completion + " изменено",
        )

        self.assertNotEqual(original, dataset_fingerprint(changed))

    def test_normalization_preserves_programming_language_symbols(self) -> None:
        self.assertEqual(normalize_prompt("Как начать в C++?"), "как начать в c++")
        self.assertEqual(normalize_prompt("Как начать в C#?"), "как начать в c#")
        self.assertNotEqual(
            normalize_prompt("Как начать в C++?"),
            normalize_prompt("Как начать в C#?"),
        )


if __name__ == "__main__":
    unittest.main()

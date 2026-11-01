"""Проверки реальных рисков обучения: потеря текста, утечка и запись весов."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_core.dialogue_data import DialogueExample, dialogue_examples_to_pairs
from ai_core.tokenizer import CharacterTokenizer
from ai_core.training_data import TrainingPair
from ai_core.training_preflight import (
    PROJECT_ROOT, analyze_training_preflight, make_run_directory, require_valid_preflight,
)


class TrainingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.splits = {
            "train": [TrainingPair("abc 1", "cab")],
            "validation": [TrainingPair("abc 2", "bac")],
            "test": [TrainingPair("abc 3", "cba")],
        }
        self.tokenizer = CharacterTokenizer()
        self.tokenizer.build(["abc 123 длинный вопрос ответ"])

    def analyze(self, max_len=100, dialogues=None):
        return analyze_training_preflight(self.splits, self.tokenizer, max_len, dialogues)

    def test_valid_data_does_not_claim_quality_approval(self):
        report = self.analyze()
        self.assertTrue(report["checks_passed"])
        self.assertFalse(report["quality_approved"])
        require_valid_preflight(report)

    def test_long_target_is_counted_before_eos_replacement(self):
        self.splits["train"][0] = TrainingPair("abc", "cab" * 30)
        report = self.analyze(20)
        self.assertEqual(report["splits"]["train"]["target_max"], 92)
        self.assertEqual(report["splits"]["train"]["target_over_limit"], 1)
        with self.assertRaisesRegex(ValueError, "truncation_train"):
            require_valid_preflight(report)

    def test_long_prompt_is_not_silently_accepted(self):
        self.splits["train"][0] = TrainingPair("abc" * 20, "cab")
        self.assertEqual(self.analyze(20)["splits"]["train"]["prompt_over_limit"], 1)

    def test_service_tokens_count_at_exact_boundary(self):
        self.splits["train"][0] = TrainingPair("abc", "cab")
        report = self.analyze(5)
        self.assertEqual(report["splits"]["train"]["examples_over_limit"], 0)

    def test_final_train_overlap_after_appending_dialogue_is_detected(self):
        self.splits["train"].append(TrainingPair("ABC 2!", "cab"))
        report = self.analyze()
        self.assertIn("cross_split_prompt_overlap", report["blocking_reasons"])
        self.assertEqual(report["cross_split_prompt_overlap"][0]["count"], 1)

    def test_validation_test_overlap_is_also_detected(self):
        self.splits["test"] = list(self.splits["validation"])
        self.assertIn("cross_split_prompt_overlap", self.analyze()["blocking_reasons"])

    def test_heldout_question_inside_history_is_detected(self):
        dialogue = DialogueExample(
            messages=({"role": "user", "content": "abc 2"},
                      {"role": "assistant", "content": "bac"}),
            prompt="abc 1", completion="cab",
        )
        self.splits["train"].extend(dialogue_examples_to_pairs([dialogue]))
        texts = [value for pairs in self.splits.values() for pair in pairs
                 for value in (pair.prompt, pair.completion)]
        self.tokenizer.build(texts)
        report = self.analyze(1000, [dialogue])
        self.assertEqual(report["dialogue_history_overlap"]["validation"], 1)
        self.assertIn("dialogue_history_overlaps_holdout", report["blocking_reasons"])

    def test_unused_dialogue_does_not_contaminate_selected_training(self):
        unused = DialogueExample(messages=({"role": "user", "content": "abc 2"},),
                                 prompt="not selected", completion="cab")
        self.assertTrue(self.analyze(dialogues=[unused])["checks_passed"])

    def test_history_discarded_by_formatter_is_not_a_false_leak(self):
        messages = [{"role": "user", "content": "abc 2"},
                    {"role": "assistant", "content": "bac"}]
        messages.extend({"role": "user" if i % 2 == 0 else "assistant", "content": "abc 1"}
                        for i in range(6))
        example = DialogueExample(tuple(messages), "abc 1", "cab")
        self.splits["train"].extend(dialogue_examples_to_pairs([example]))
        self.tokenizer.build([self.splits["train"][-1].prompt])
        self.assertTrue(self.analyze(1000, [example])["checks_passed"])

    def test_invalid_recipe_stops_before_model_creation(self):
        import ai_core.train as trainer
        pairs = [TrainingPair(f"abcdef0123456789 {i}", "abc" * 20) for i in range(8)]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trainer, "load_primary_training_pairs", return_value=pairs), \
                patch.object(trainer, "load_web_foundation_pairs", return_value=[]), \
                patch.object(trainer, "load_training_pairs_for_topic", return_value=pairs), \
                patch.object(trainer, "load_dialogue_examples", return_value=[]), \
                patch.object(trainer, "TinyGRUAttentionSeq2Seq") as constructor:
            with self.assertRaisesRegex(ValueError, "truncation_train"):
                trainer.train_model(epochs=1, batch_size=2, learning_rate=.001, max_len=16,
                                    validation_ratio=.125, test_ratio=.125, seed=42,
                                    architecture="gru", output_dir=Path(directory) / "blocked")
            constructor.assert_not_called()

    def test_report_does_not_contain_prompt_text(self):
        self.splits["train"].append(TrainingPair("abc 2", "cab"))
        self.assertNotIn("abc 2", json.dumps(self.analyze(), ensure_ascii=False))

    def test_empty_heldout_is_explicit(self):
        self.splits["test"] = []
        self.assertIn("empty_test", self.analyze()["blocking_reasons"])

    def test_candidate_cannot_overwrite_active_or_existing_directory(self):
        with self.assertRaises(ValueError):
            make_run_directory(PROJECT_ROOT / "model_artifacts" / "another")
        with tempfile.TemporaryDirectory() as directory:
            candidate = make_run_directory(Path(directory) / "candidate")
            self.assertTrue(candidate.is_dir())
            with self.assertRaises(FileExistsError):
                make_run_directory(candidate)

    def test_real_trainer_preflight_and_one_epoch_preserve_active_checkpoint(self):
        # Малый учебный опыт во временной папке проверяет весь путь сохранения.
        # Он не служит обучением продукта и не использует пользовательские чаты.
        import torch
        import ai_core.train as trainer
        pairs = [TrainingPair(f"abcdef0123456789 {i}", "abc") for i in range(8)]
        checkpoint = PROJECT_ROOT / "model_artifacts" / "tiny_transformer.pt"
        before = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        old_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        try:
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(trainer, "load_primary_training_pairs", return_value=pairs), \
                    patch.object(trainer, "load_web_foundation_pairs", return_value=[]), \
                    patch.object(trainer, "load_training_pairs_for_topic", return_value=pairs), \
                    patch.object(trainer, "load_dialogue_examples", return_value=[]):
                parameters = dict(epochs=1, batch_size=2, learning_rate=.001, max_len=32,
                                  validation_ratio=.125, test_ratio=.125, seed=42,
                                  architecture="gru", embed_dim=8, n_layers=1)
                diagnostic = Path(directory) / "diagnostic"
                trainer.train_model(**parameters, output_dir=diagnostic, preflight_only=True)
                self.assertEqual([p.name for p in diagnostic.iterdir()], ["training_preflight.json"])
                run = Path(directory) / "candidate"
                trainer.train_model(**parameters, output_dir=run)
                saved = torch.load(run / "tiny_transformer.pt", map_location="cpu", weights_only=True)
                self.assertEqual(saved["selected_epoch"], 1)
                self.assertTrue((run / "tokenizer.json").exists())
                metrics = json.loads((run / "training_metrics.json").read_text(encoding="utf-8"))
                self.assertTrue(metrics["training_preflight"]["checks_passed"])
        finally:
            torch.set_num_threads(old_threads)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

"""Проверяет безопасный blueprint многоходовых dialogue-данных Brain v3."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from ai_core.brain_v3_dialogue_authoring import (
    DialogueAuthoringError,
    authoring_blueprint_markdown,
    build_dialogue_authoring_blueprint,
)
from ai_core.brain_v3_dialogue_coverage import (
    DEFAULT_POLICY,
    load_dialogue_coverage_policy,
)


ROOT = Path(__file__).resolve().parent.parent
COVERAGE_REPORT = (
    ROOT
    / "evaluation"
    / "results"
    / "brain-v3-dialogue-coverage-v0.4"
    / "brain_v3_dialogue_coverage.json"
)


def _current_coverage_report() -> dict[str, object]:
    """Читает versioned safe metadata report, а не raw dialogue-корпус."""
    return json.loads(COVERAGE_REPORT.read_text(encoding="utf-8"))


class BrainV3DialogueAuthoringTests(unittest.TestCase):
    """Гарантирует точные квоты и отсутствие утечки неизвестных входных полей."""

    def test_current_report_becomes_exact_split_feasible_next_batch_blueprint(self) -> None:
        policy = load_dialogue_coverage_policy(DEFAULT_POLICY)
        blueprint = build_dialogue_authoring_blueprint(
            _current_coverage_report(),
            policy,
        )

        self.assertEqual(blueprint["base_corpus_snapshot"]["dialogue_records"], 11_945)
        self.assertEqual(blueprint["base_corpus_snapshot"]["dialogue_turns"], 147_970)
        self.assertEqual(blueprint["new_batch"]["dialogue_records"], 3_475)
        self.assertEqual(blueprint["new_batch"]["dialogue_turns"], 13_900)
        self.assertEqual(
            blueprint["new_batch"]["dialogue_records_by_turn_count"],
            {"2": 434, "4": 2_607, "6": 434},
        )
        self.assertEqual(
            blueprint["new_batch"]["split_quotas"]["train"]["additional_turns"],
            11_120,
        )
        self.assertEqual(
            blueprint["new_batch"]["split_quotas"]["validation"]["additional_turns"],
            1_390,
        )
        self.assertEqual(
            blueprint["new_batch"]["split_quotas"]["test"]["additional_turns"],
            1_390,
        )
        self.assertEqual(
            blueprint["new_batch"]["split_quotas"]["train"]["six_turn_dialogue_records"],
            347,
        )
        self.assertEqual(
            blueprint["new_batch"]["split_quotas"]["validation"]["four_turn_dialogue_records"],
            260,
        )
        self.assertEqual(
            blueprint["new_batch"]["split_quotas"]["test"]["four_turn_dialogue_records"],
            261,
        )
        self.assertEqual(blueprint["target_after_next_batch"]["dialogue_records"], 15_420)
        self.assertEqual(blueprint["target_after_next_batch"]["dialogue_turns"], 161_870)
        self.assertTrue(
            all(
                count > 0
                for count in blueprint["new_batch"]["dialogue_records_by_turn_count"].values()
            )
        )
        self.assertGreaterEqual(
            blueprint["target_after_next_batch"]["multi_turn_dialogue_ratio"],
            0.35,
        )
        for dimension in (
            "topic_coverage",
            "dialogue_goal_coverage",
            "emotional_tone_coverage",
        ):
            quota = blueprint["new_batch"][dimension]
            self.assertEqual(
                sum(quota["new_batch_turn_quota"].values()),
                blueprint["new_batch"]["dialogue_turns"],
            )
            self.assertTrue(
                all(
                    value % 2 == 0
                    for value in quota["new_batch_turn_quota"].values()
                )
            )
            self.assertTrue(
                all(
                    value in {0, 1}
                    for value in quota["parity_adjustment_turns"].values()
                )
            )
            self.assertTrue(
                all(
                    quota["new_batch_turn_quota"][label]
                    >= quota["policy_minimum_additional_turns"][label]
                    for label in quota["new_batch_turn_quota"]
                )
            )
        self.assertFalse(blueprint["planning_boundary"]["raw_dialogues_created"])
        self.assertFalse(blueprint["planning_boundary"]["training_started"])

    def test_unknown_coverage_field_never_appears_in_blueprint_or_markdown(self) -> None:
        policy = load_dialogue_coverage_policy(DEFAULT_POLICY)
        report = _current_coverage_report()
        secret = "текст, который не должен попасть в authoring blueprint"
        report["observed"]["private_note"] = secret

        blueprint = build_dialogue_authoring_blueprint(report, policy)
        rendered = json.dumps(blueprint, ensure_ascii=False) + authoring_blueprint_markdown(
            blueprint
        )

        self.assertNotIn(secret, rendered)
        self.assertNotIn("private_note", rendered)

    def test_mismatched_policy_or_inconsistent_split_report_is_rejected(self) -> None:
        policy = load_dialogue_coverage_policy(DEFAULT_POLICY)
        mismatched_policy_report = _current_coverage_report()
        mismatched_policy_report["policy_id"] = "untrusted_policy_v1"
        with self.assertRaises(DialogueAuthoringError):
            build_dialogue_authoring_blueprint(mismatched_policy_report, policy)

        inconsistent_split_report = copy.deepcopy(_current_coverage_report())
        inconsistent_split_report["observed"]["dialogue_turns_by_split"]["train"] += 2
        with self.assertRaises(DialogueAuthoringError):
            build_dialogue_authoring_blueprint(inconsistent_split_report, policy)


if __name__ == "__main__":
    unittest.main()

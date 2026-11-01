"""Проверяет, что Brain v3 contract остаётся независимой оценкой, а не train-data."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai_core.brain_v3_contract import (
    DEFAULT_BRAIN_V3_CONTRACT,
    BrainV3ContractError,
    MINIMUM_EXPANDED_HISTORY_SCENARIOS,
    MINIMUM_EXPANDED_SCENARIOS,
    MINIMUM_EXPANDED_SCENARIOS_PER_DOMAIN,
    contract_summary,
    load_brain_v3_contract,
)


class BrainV3ContractTests(unittest.TestCase):
    """Небольшие регрессии для запретов контракта будущего генератора."""

    def _payload(self) -> dict[str, object]:
        return json.loads(DEFAULT_BRAIN_V3_CONTRACT.read_text(encoding="utf-8"))

    def _load_payload(self, payload: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            load_brain_v3_contract(path)

    def test_shipped_contract_covers_all_domains_and_hides_prompts_in_summary(self) -> None:
        contract = load_brain_v3_contract()
        summary = contract_summary(contract)

        self.assertGreaterEqual(len(contract.scenarios), MINIMUM_EXPANDED_SCENARIOS)
        self.assertEqual(len(summary["domains"]), 12)
        self.assertEqual(summary["contract_tier"], "expanded")
        self.assertGreaterEqual(
            summary["scenarios_with_history"], MINIMUM_EXPANDED_HISTORY_SCENARIOS
        )
        self.assertTrue(
            all(
                count >= MINIMUM_EXPANDED_SCENARIOS_PER_DOMAIN
                for count in summary["domains"].values()
            )
        )
        self.assertTrue(summary["evaluation_only"])
        self.assertFalse(summary["training_eligible"])
        self.assertNotIn("prompt", json.dumps(summary, ensure_ascii=False).casefold())

    def test_expanded_contract_cannot_drop_below_quality_minimum(self) -> None:
        payload = self._payload()
        scenarios = copy.deepcopy(payload["scenarios"])
        assert isinstance(scenarios, list)
        payload["scenarios"] = scenarios[: MINIMUM_EXPANDED_SCENARIOS - 1]

        with self.assertRaises(BrainV3ContractError):
            self._load_payload(payload)

    def test_contract_cannot_be_marked_as_training_eligible(self) -> None:
        payload = self._payload()
        payload["training_eligible"] = True

        with self.assertRaises(BrainV3ContractError):
            self._load_payload(payload)

    def test_duplicate_normalised_prompt_is_rejected(self) -> None:
        payload = self._payload()
        scenarios = copy.deepcopy(payload["scenarios"])
        assert isinstance(scenarios, list)
        scenarios[1]["prompt"] = scenarios[0]["prompt"].upper()
        payload["scenarios"] = scenarios

        with self.assertRaises(BrainV3ContractError):
            self._load_payload(payload)

    def test_hidden_reference_answer_is_rejected(self) -> None:
        payload = self._payload()
        scenarios = copy.deepcopy(payload["scenarios"])
        assert isinstance(scenarios, list)
        scenarios[0]["expected"]["answer"] = "Готовая фраза не должна попасть в контракт."
        payload["scenarios"] = scenarios

        with self.assertRaises(BrainV3ContractError):
            self._load_payload(payload)

    def test_dynamic_tool_cases_require_a_final_result_instead_of_an_action_promise(self) -> None:
        contract = load_brain_v3_contract()
        scenarios = {scenario.identifier: scenario for scenario in contract.scenarios}
        expected_routes = {
            "tool-weather-sunset-final-result": "tool_weather",
            "tool-weather-rain-final-result": "tool_weather",
            "web-final-result-with-source": "tool_web",
            "web-final-result-without-source": "tool_web",
        }

        for identifier, expected_route in expected_routes.items():
            with self.subTest(identifier=identifier):
                scenario = scenarios[identifier]
                self.assertEqual(scenario.expected_route, expected_route)
                self.assertIn("tool_result_returned", scenario.properties)
                self.assertIn("no_action_promise", scenario.properties)
                self.assertIn("fact_grounded", scenario.properties)

    def test_no_action_promise_is_rejected_without_a_tool_result_contract(self) -> None:
        payload = self._payload()
        scenarios = copy.deepcopy(payload["scenarios"])
        assert isinstance(scenarios, list)
        scenario = next(
            item
            for item in scenarios
            if item["id"] == "tool-weather-sunset-final-result"
        )
        properties = scenario["expected"]["properties"]
        assert isinstance(properties, list)
        properties.remove("tool_result_returned")
        payload["scenarios"] = scenarios

        with self.assertRaises(BrainV3ContractError):
            self._load_payload(payload)

    def test_tool_result_property_is_rejected_on_dialogue_route(self) -> None:
        payload = self._payload()
        scenarios = copy.deepcopy(payload["scenarios"])
        assert isinstance(scenarios, list)
        scenario = next(
            item
            for item in scenarios
            if item["id"] == "tool-weather-sunset-final-result"
        )
        scenario["expected"]["route"] = "direct_dialogue"
        payload["scenarios"] = scenarios

        with self.assertRaises(BrainV3ContractError):
            self._load_payload(payload)

    def test_location_dependent_web_question_requires_context_before_search(self) -> None:
        contract = load_brain_v3_contract()
        scenarios = {scenario.identifier: scenario for scenario in contract.scenarios}
        scenario = scenarios["web-pickup-point-needs-context"]

        self.assertEqual(scenario.expected_route, "clarify")
        self.assertEqual(scenario.expected_length, "short")
        self.assertEqual(
            scenario.properties,
            frozenset({"ask_when_missing", "no_fake_certainty", "relevant", "brief"}),
        )


if __name__ == "__main__":
    unittest.main()

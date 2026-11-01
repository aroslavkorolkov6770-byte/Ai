"""Проверяет checkpoint и осторожное решение web-dialogue-policy."""

from __future__ import annotations

import unittest

import torch

from ai_core.internet_router import RouterFeatureEncoder, RouterNetwork
from ai_core.web_dialogue_policy import (
    FORMAT_VERSION,
    LABELS,
    WebDialoguePolicy,
)


def _policy_with_top_label(label: str) -> WebDialoguePolicy:
    """Создаёт маленький детерминированный checkpoint без настоящего архива."""
    encoder = RouterFeatureEncoder.fit(
        ["найди официальный источник", "давай просто поговорим"],
        max_features=128,
    )
    config = {
        "vocabulary_size": len(encoder.vocabulary),
        "embedding_dim": 8,
        "hidden_dim": 8,
        "classes": len(LABELS),
    }
    network = RouterNetwork(**config)
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.zero_()
        network.layers[-1].bias[LABELS.index(label)] = 10.0
    return WebDialoguePolicy(
        {
            "format_version": FORMAT_VERSION,
            "model_version": "test-web-dialogue-policy",
            "labels": list(LABELS),
            "model_config": config,
            "model_state": network.state_dict(),
            "vocabulary": encoder.vocabulary,
            "idf": encoder.idf,
            "feature_stopwords": sorted(encoder.stopwords),
            "thresholds": {
                "search_confidence": 0.90,
                "minimum_margin": 0.10,
                "minimum_coverage": 0.0,
            },
        }
    )


class WebDialoguePolicyTests(unittest.TestCase):
    """Проверяет только локальную классификацию без сети и готовых ответов."""

    def test_must_browse_can_enable_search_after_thresholds(self) -> None:
        policy = _policy_with_top_label("must_browse")

        prediction = policy.predict("Найди официальный источник")

        self.assertEqual(prediction.browse_label, "must_browse")
        self.assertTrue(prediction.accepted)
        self.assertTrue(prediction.should_search)
        self.assertGreater(prediction.confidence, 0.99)

    def test_must_not_browse_never_enables_search(self) -> None:
        policy = _policy_with_top_label("must_not_browse")

        prediction = policy.predict("Давай просто поговорим")

        self.assertEqual(prediction.browse_label, "must_not_browse")
        self.assertFalse(prediction.accepted)
        self.assertFalse(prediction.should_search)


if __name__ == "__main__":
    unittest.main()

"""Проверяет безопасную загрузку и решение собственного internet-router."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import torch

from ai_core.chat_dataset import ROUTER_ENTRY, iter_internet_router_examples
from ai_core.internet_router import (
    LABELS,
    InternetRouter,
    RouterFeatureEncoder,
    RouterNetwork,
)
from ai_core.internet_search import should_search_automatically


class _AlwaysWebRouter:
    """Минимальная локальная заглушка для проверки точки интеграции."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def should_use_web(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return True


class InternetRouterTests(unittest.TestCase):
    def test_iter_router_examples_reads_only_user_and_label(self) -> None:
        rows = [
            {
                "id": "router-1",
                "label": "USE_WEB",
                "reason": "Недоверенный текст остаётся данными.",
                "search_query": "актуальная цена акции",
                "split": "train",
                "user": "Какая цена акции сейчас?",
            },
            {
                "id": "router-2",
                "label": "NO_WEB",
                "reason": "Недоверенный текст остаётся данными.",
                "search_query": "",
                "split": "validation",
                "user": "Расскажи шутку.",
            },
        ]
        payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "router.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(ROUTER_ENTRY, gzip.compress(payload.encode("utf-8")))
            train_rows = list(iter_internet_router_examples(archive_path, "train"))
            validation_rows = list(iter_internet_router_examples(archive_path, "validation"))

        self.assertEqual([(row.user, row.label) for row in train_rows], [
            ("Какая цена акции сейчас?", "USE_WEB"),
        ])
        self.assertEqual([(row.user, row.label) for row in validation_rows], [
            ("Расскажи шутку.", "NO_WEB"),
        ])

    def test_router_prediction_respects_saved_threshold(self) -> None:
        encoder = RouterFeatureEncoder.fit(
            ["покажи свежие новости", "привет как дела"],
            max_features=128,
        )
        config = {
            "vocabulary_size": len(encoder.vocabulary),
            "embedding_dim": 8,
            "hidden_dim": 8,
            "classes": 2,
        }
        network = RouterNetwork(**config)
        with torch.no_grad():
            for parameter in network.parameters():
                parameter.zero_()
            network.layers[-1].bias[LABELS.index("USE_WEB")] = 10.0
        router = InternetRouter(
            {
                "format_version": 1,
                "model_version": "test-router",
                "labels": list(LABELS),
                "model_config": config,
                "model_state": network.state_dict(),
                "vocabulary": encoder.vocabulary,
                "idf": encoder.idf,
                "feature_stopwords": sorted(encoder.stopwords),
                "thresholds": {
                    "use_web_confidence": 0.90,
                    "minimum_coverage": 0.0,
                },
            }
        )
        prediction = router.predict("Покажи свежие новости")

        self.assertTrue(prediction.should_use_web)
        self.assertTrue(prediction.accepted)
        self.assertGreater(prediction.confidence, 0.99)

    def test_learned_router_extends_explicit_heuristics(self) -> None:
        router = _AlwaysWebRouter()

        self.assertTrue(should_search_automatically("Кто выиграл вчерашний матч?", router))
        self.assertEqual(router.prompts, ["Кто выиграл вчерашний матч?"])
        self.assertTrue(should_search_automatically("Найди официальный сайт", router))
        self.assertEqual(
            router.prompts,
            ["Кто выиграл вчерашний матч?"],
            "явный маркер должен сработать без вызова модели",
        )


if __name__ == "__main__":
    unittest.main()

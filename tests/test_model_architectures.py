"""Проверки собственных нейросетевых архитектур без запуска обучения."""

import unittest

import torch

from ai_core.model import TinyGRUAttentionSeq2Seq


class GRUAttentionModelTests(unittest.TestCase):
    """Проверяет формы тензоров, padding и ограничения новой модели."""

    def setUp(self) -> None:
        torch.manual_seed(42)
        self.model = TinyGRUAttentionSeq2Seq(
            vocab_size=24,
            embed_dim=16,
            hidden_dim=16,
            n_layers=2,
            max_len=12,
            dropout=0.0,
        )
        self.model.eval()

    def test_forward_returns_finite_logits_for_padded_batch(self) -> None:
        encoder_ids = torch.tensor(
            [
                [1, 5, 6, 2, 0],
                [1, 7, 2, 0, 0],
            ],
            dtype=torch.long,
        )
        decoder_ids = torch.tensor(
            [
                [1, 8, 9, 10],
                [1, 11, 0, 0],
            ],
            dtype=torch.long,
        )
        padding_mask = encoder_ids.eq(0)

        logits = self.model(
            encoder_ids,
            decoder_ids,
            encoder_attention_mask=padding_mask,
        )

        self.assertEqual(logits.shape, (2, 4, 24))
        self.assertTrue(torch.isfinite(logits).all().item())

    def test_encode_and_decode_have_compatible_shapes(self) -> None:
        encoder_ids = torch.tensor([[1, 5, 6, 2]], dtype=torch.long)
        decoder_ids = torch.tensor([[1, 8, 9]], dtype=torch.long)

        memory, hidden, padding_mask = self.model.encode(encoder_ids)
        logits = self.model.decode(
            decoder_ids,
            (memory, hidden, padding_mask),
        )

        self.assertEqual(memory.shape, (1, 4, 16))
        self.assertEqual(hidden.shape, (2, 1, 16))
        self.assertEqual(padding_mask.shape, (1, 4))
        self.assertEqual(logits.shape, (1, 3, 24))

    def test_rejects_empty_or_too_long_sequences(self) -> None:
        with self.assertRaisesRegex(ValueError, "не должен быть пустым"):
            self.model.encode(torch.empty((1, 0), dtype=torch.long))

        with self.assertRaisesRegex(ValueError, "больше max_len"):
            self.model.encode(torch.ones((1, 13), dtype=torch.long))

        with self.assertRaisesRegex(ValueError, "хотя бы один токен"):
            self.model.encode(torch.zeros((1, 3), dtype=torch.long))


if __name__ == "__main__":
    unittest.main()

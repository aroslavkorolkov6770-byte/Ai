"""Проверяет чистую логику выбора размера первого Brain v3 прототипа."""

from __future__ import annotations

import unittest

from ai_core.brain_v3_hardware import BYTES_PER_GIB, gibibytes, recommend_training_profile


class BrainV3HardwareTests(unittest.TestCase):
    """Рекомендация не запускает benchmark и не зависит от железа тестовой машины."""

    def test_cpu_machine_with_sixteen_gib_is_limited_to_small_prototype(self) -> None:
        profile = recommend_training_profile(
            cuda_available=False,
            total_memory_bytes=16 * BYTES_PER_GIB,
            logical_cpu_cores=16,
        )

        self.assertEqual(profile["profile"], "cpu-prototype-only")
        self.assertEqual(profile["prototype_parameter_range"], "5-15M")
        self.assertFalse(profile["scale_training_allowed"])
        self.assertIn("CUDA", profile["reason"])

    def test_cuda_presence_requires_real_vram_measurement_before_scaling(self) -> None:
        profile = recommend_training_profile(
            cuda_available=True,
            total_memory_bytes=32 * BYTES_PER_GIB,
            logical_cpu_cores=16,
        )

        self.assertEqual(profile["profile"], "gpu-capability-measurement-required")
        self.assertFalse(profile["scale_training_allowed"])
        self.assertIn("VRAM", profile["next_step"])

    def test_unknown_memory_stays_unknown_instead_of_becoming_zero(self) -> None:
        self.assertIsNone(gibibytes(None))


if __name__ == "__main__":
    unittest.main()

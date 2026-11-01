"""Проводит воспроизводимый локальный CPU/GPU baseline для Brain v3.

Скрипт не запускает обучение, не читает пользовательские диалоги и не требует
интернет. Он выполняет короткую матричную операцию, близкую по типу к базовым
операциям Transformer, а затем записывает факт о доступном железе в новый
каталог результатов.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch

from ai_core.brain_v3_hardware import (
    gibibytes,
    recommend_training_profile,
    total_physical_memory_bytes,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "brain-v3-hardware-baseline"


def _validate_positive(name: str, value: int, minimum: int, maximum: int) -> int:
    """Проверяет размер benchmark до выделения заметной памяти."""
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} должен быть в диапазоне от {minimum} до {maximum}.")
    return value


def _set_cpu_threads(requested_threads: int | None) -> int:
    """Ограничивает benchmark восемью потоками, чтобы не блокировать рабочий ПК."""
    logical_cores = os.cpu_count() or 1
    threads = requested_threads if requested_threads is not None else min(logical_cores, 8)
    threads = _validate_positive("threads", threads, 1, logical_cores)
    torch.set_num_threads(threads)
    try:
        # Interop-потоки не дают заметной пользы здесь, зато могут мешать UI.
        torch.set_num_interop_threads(1)
    except RuntimeError:
        # В редком случае рантайм уже зафиксировал значение; основной замер
        # всё равно остаётся валидным и фиксирует активное число потоков.
        pass
    return torch.get_num_threads()


def _matrix_memory_mib(matrix_size: int) -> float:
    """Оценивает память трёх квадратных float32-матриц в МиБ."""
    return round((3 * matrix_size * matrix_size * 4) / (1024**2), 2)


def _measure_cpu(matrix_size: int, warmup: int, iterations: int) -> dict[str, Any]:
    """Измеряет CPU matmul с прогревом и предварительно выделенным output."""
    left = torch.randn((matrix_size, matrix_size), dtype=torch.float32, device="cpu")
    right = torch.randn((matrix_size, matrix_size), dtype=torch.float32, device="cpu")
    output = torch.empty((matrix_size, matrix_size), dtype=torch.float32, device="cpu")

    for _ in range(warmup):
        torch.mm(left, right, out=output)
    start = time.perf_counter()
    for _ in range(iterations):
        torch.mm(left, right, out=output)
    elapsed_seconds = time.perf_counter() - start
    checksum = round(float(output[0, 0].item()), 6)
    operations = 2 * (matrix_size**3) * iterations
    gflops = round(operations / max(elapsed_seconds, 1e-9) / 1e9, 3)
    return {
        "device": "cpu",
        "matrix_size": matrix_size,
        "warmup_iterations": warmup,
        "measured_iterations": iterations,
        "estimated_working_set_mib": _matrix_memory_mib(matrix_size),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "approximate_gflops": gflops,
        "checksum": checksum,
    }


def _cuda_details() -> dict[str, Any]:
    """Читает свойства CUDA только если PyTorch действительно её видит."""
    available = bool(torch.cuda.is_available())
    details: dict[str, Any] = {
        "available": available,
        "torch_cuda_version": torch.version.cuda,
        "device_count": torch.cuda.device_count() if available else 0,
    }
    if not available:
        return details
    properties = torch.cuda.get_device_properties(0)
    details.update(
        {
            "device_name": properties.name,
            "total_memory_bytes": int(properties.total_memory),
            "total_memory_gib": gibibytes(int(properties.total_memory)),
        }
    )
    return details


def _markdown_report(report: dict[str, Any]) -> str:
    """Строит понятный Markdown-отчёт, не скрывая ограничения замера."""
    hardware = report["hardware"]
    cpu = report["cpu_benchmark"]
    recommendation = report["recommendation"]
    cuda = hardware["cuda"]
    cuda_state = "доступна" if cuda["available"] else "не доступна"
    return "\n".join(
        [
            "# Brain v3 — аппаратный baseline",
            "",
            f"Дата замера (UTC): {report['measured_at_utc']}",
            "",
            "## Подтверждённая конфигурация",
            "",
            f"- ОС: {hardware['platform']}",
            f"- Python: {hardware['python']}",
            f"- PyTorch: {hardware['torch_version']}",
            f"- Логических CPU-потоков: {hardware['logical_cpu_cores']}",
            f"- RAM: {hardware['total_memory_gib'] if hardware['total_memory_gib'] is not None else 'не удалось определить'} ГиБ",
            f"- CUDA в PyTorch: {cuda_state}",
            "",
            "## Короткий CPU-замер",
            "",
            f"- Матрица: {cpu['matrix_size']} × {cpu['matrix_size']} float32, {cpu['measured_iterations']} измеряемых итераций.",
            f"- Время: {cpu['elapsed_seconds']} с; ориентировочная производительность: {cpu['approximate_gflops']} GFLOPS.",
            f"- Оценка рабочей памяти операции: {cpu['estimated_working_set_mib']} МиБ.",
            "",
            "Это не скорость обучения и не оценка качества ИИ. Это только повторяемая точка отсчёта для будущих запусков на этой же машине.",
            "",
            "## Рекомендация для следующего шага",
            "",
            f"- Режим: `{recommendation['profile']}`.",
            f"- Параметры первого прототипа: {recommendation['prototype_parameter_range']}.",
            f"- Контекст: {recommendation['context_tokens']} токенов; batch: {recommendation['batch_sequences']} последовательностей.",
            f"- Масштабное предобучение разрешено: {'да' if recommendation['scale_training_allowed'] else 'нет'}.",
            f"- Почему: {recommendation['reason']}",
            f"- Практический следующий шаг: {recommendation['next_step']}",
            "",
            "Обучение этим скриптом не запускалось. В сеть он не обращался и GitHub не изменял.",
            "",
        ]
    )


def benchmark(
    *,
    output: Path,
    matrix_size: int = 768,
    warmup: int = 2,
    iterations: int = 10,
    threads: int | None = None,
) -> dict[str, Any]:
    """Выполняет benchmark и пишет артефакты только в ещё не существующий каталог."""
    if output.exists():
        raise ValueError(f"Каталог результата уже существует: {output}")
    matrix_size = _validate_positive("matrix-size", matrix_size, 128, 2_048)
    warmup = _validate_positive("warmup", warmup, 1, 20)
    iterations = _validate_positive("iterations", iterations, 1, 100)
    active_threads = _set_cpu_threads(threads)
    total_memory = total_physical_memory_bytes()
    cuda = _cuda_details()
    report = {
        "format_version": 1,
        "measured_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "hardware": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "torch_version": torch.__version__,
            "logical_cpu_cores": os.cpu_count(),
            "torch_cpu_threads": active_threads,
            "total_memory_bytes": total_memory,
            "total_memory_gib": gibibytes(total_memory),
            "cuda": cuda,
        },
        "cpu_benchmark": _measure_cpu(matrix_size, warmup, iterations),
        "recommendation": recommend_training_profile(
            cuda_available=cuda["available"],
            total_memory_bytes=total_memory,
            logical_cpu_cores=os.cpu_count(),
        ),
    }
    output.mkdir(parents=True)
    (output / "brain_v3_hardware_baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "BRAIN_V3_HARDWARE_REPORT.md").write_text(
        _markdown_report(report),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """Разбирает параметры краткого безопасного замера."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--matrix-size", type=int, default=768)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--threads", type=int)
    args = parser.parse_args()
    try:
        report = benchmark(
            output=args.output,
            matrix_size=args.matrix_size,
            warmup=args.warmup,
            iterations=args.iterations,
            threads=args.threads,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Аппаратный baseline Brain v3 остановлен: {error}") from error
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

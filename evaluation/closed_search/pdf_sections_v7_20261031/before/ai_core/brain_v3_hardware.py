"""Небольшие переносимые функции для этапа аппаратного baseline Brain v3.

Этот модуль намеренно не обучает модель и не обращается к интернету. Его задача
— получить понятные параметры машины и по ним выбрать *безопасный размер
прототипа*. Рекомендация не обещает качество будущей модели: она ограничивает
первый эксперимент так, чтобы не перегрузить компьютер пользователя.
"""

from __future__ import annotations

import ctypes
import os
from typing import Any


BYTES_PER_GIB = 1024**3


def total_physical_memory_bytes() -> int | None:
    """Возвращает установленную оперативную память без дополнительных пакетов.

    Для Windows используется системная функция GlobalMemoryStatusEx. На других
    системах применяется POSIX-интерфейс, если он доступен. При неизвестном
    окружении функция возвращает ``None`` вместо выдуманного значения.
    """
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            """Точная структура Windows API для чтения физической памяти."""

            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        try:
            succeeded = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        except (AttributeError, OSError):
            return None
        return int(status.ullTotalPhys) if succeeded else None

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        physical_pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    if not isinstance(page_size, int) or not isinstance(physical_pages, int):
        return None
    return page_size * physical_pages if page_size > 0 and physical_pages > 0 else None


def gibibytes(value: int | None) -> float | None:
    """Переводит байты в ГиБ, сохраняя неизвестное значение неизвестным."""
    return round(value / BYTES_PER_GIB, 2) if value is not None else None


def recommend_training_profile(
    *,
    cuda_available: bool,
    total_memory_bytes: int | None,
    logical_cpu_cores: int | None,
) -> dict[str, Any]:
    """Возвращает консервативный профиль следующего эксперимента.

    Решение строится только по реально доступному вычислительному устройству и
    RAM. Размер в параметрах — диапазон для инженерного прототипа, а не лимит
    качества и не обещание, что модель станет универсальным собеседником.
    """
    memory_gib = gibibytes(total_memory_bytes)
    cpu_cores = logical_cpu_cores or 1

    if cuda_available:
        return {
            "profile": "gpu-capability-measurement-required",
            "next_step": "Измерить свободную VRAM и скорость GPU отдельным benchmark перед выбором размера.",
            "prototype_parameter_range": "не определён до замера VRAM",
            "context_tokens": "не определён до замера VRAM",
            "batch_sequences": "не определён до замера VRAM",
            "scale_training_allowed": False,
            "reason": "GPU обнаружен, но размер видеопамяти и реальная пропускная способность ещё не подтверждены.",
        }

    if memory_gib is None or memory_gib < 12:
        parameter_range = "2-5M"
        context_tokens = "96-128"
        batch_sequences = "2-4"
    elif memory_gib <= 24:
        parameter_range = "5-15M"
        context_tokens = "128-256"
        batch_sequences = "4-8"
    else:
        parameter_range = "10-30M"
        context_tokens = "128-256"
        batch_sequences = "4-8"

    return {
        "profile": "cpu-prototype-only",
        "next_step": "Начать только с малого Transformer-прототипа и измерить loss, скорость и память на чистом локальном корпусе.",
        "prototype_parameter_range": parameter_range,
        "context_tokens": context_tokens,
        "batch_sequences": batch_sequences,
        "scale_training_allowed": False,
        "reason": (
            "CUDA-ускоритель PyTorch недоступен; при "
            f"{memory_gib if memory_gib is not None else 'неизвестном количестве'} ГиБ RAM "
            f"и {cpu_cores} логических CPU-потоках крупное предобучение будет "
            "непрактичным. Прототип нужен только для проверки архитектуры, "
            "токенизатора, декодирования и контура оценки."
        ),
    }

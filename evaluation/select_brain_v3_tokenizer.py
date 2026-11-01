"""Выбирает Brain v3 tokenizer по train/validation без утечки test-данных."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Скрипт запускается и через ``python evaluation/...py``, и через модульный
# запуск. Добавляем только корень текущего локального проекта, не внешние пути.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_core.brain_v3_corpus import (
    DEFAULT_MANIFEST,
    audit_corpus,
    file_sha256,
    load_corpus_manifest,
)
from ai_core.brain_v3_tokenizer import (
    BYTE_TOKEN_PREFIX,
    BrainV3BPETokenizer,
    BrainV3Tokenizer,
    BrainV3UnigramTokenizer,
    tokenizer_file_sha256,
)


DEFAULT_ARTIFACT = PROJECT_ROOT / "model_artifacts" / "versions" / "brain-v3-tokenizer-pilot-v0.1"
DEFAULT_REPORT = PROJECT_ROOT / "evaluation" / "results" / "brain-v3-tokenizer-pilot-v0.1"


@dataclass(frozen=True)
class TokenizationMetrics:
    """Безопасные числовые показатели одного tokenizer на одном split."""

    records: int
    characters: int
    tokens: int
    unknown_tokens: int
    byte_fallback_tokens: int
    round_trip_exact_records: int
    maximum_tokens_per_record: int
    elapsed_seconds: float

    def to_payload(self) -> dict[str, int | float]:
        average_tokens = self.tokens / self.records if self.records else 0.0
        unknown_ratio = self.unknown_tokens / self.tokens if self.tokens else 0.0
        byte_ratio = self.byte_fallback_tokens / self.tokens if self.tokens else 0.0
        round_trip_ratio = (
            self.round_trip_exact_records / self.records
            if self.records
            else 0.0
        )
        return {
            "records": self.records,
            "characters": self.characters,
            "tokens": self.tokens,
            "average_tokens_per_record": round(average_tokens, 6),
            "unknown_tokens": self.unknown_tokens,
            "unknown_token_ratio": round(unknown_ratio, 8),
            "byte_fallback_tokens": self.byte_fallback_tokens,
            "byte_fallback_token_ratio": round(byte_ratio, 8),
            "round_trip_exact_records": self.round_trip_exact_records,
            "round_trip_exact_ratio": round(round_trip_ratio, 8),
            "maximum_tokens_per_record": self.maximum_tokens_per_record,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
        }


def _require_clean_audit(manifest_path: Path) -> dict[str, Any]:
    """Допускает tokenizer только после свежей чистой проверки корпуса."""
    report = audit_corpus(manifest_path)
    if report["records_accepted"] <= 0:
        raise RuntimeError("В корпусе нет принятых записей для tokenizer.")
    if report["records_rejected"] != 0:
        raise RuntimeError("Tokenizer нельзя строить на корпусе с отклонёнными строками.")
    if report["cross_split_exact_duplicates"] != 0:
        raise RuntimeError("Tokenizer нельзя строить при точных пересечениях split.")
    if report["cross_split_near_duplicate_candidates"] != 0:
        raise RuntimeError("Tokenizer нельзя строить при близких пересечениях split.")
    return report


def _texts_from_record(payload: dict[str, Any]) -> Iterable[str]:
    """Извлекает только учебный текст уже проверенной записи без метаданных."""
    record_type = payload.get("record_type")
    if record_type == "language_document":
        text = payload.get("text")
        if isinstance(text, str):
            yield text
        return
    if record_type == "dialogue":
        turns = payload.get("turns")
        if isinstance(turns, list):
            for turn in turns:
                if isinstance(turn, dict) and isinstance(turn.get("text"), str):
                    yield turn["text"]


def load_split_texts(manifest_path: Path) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Читает тексты по split после аудита и повторной SHA-256 проверки."""
    audit_report = _require_clean_audit(manifest_path)
    manifest = load_corpus_manifest(manifest_path)
    texts_by_split = {"train": [], "validation": [], "test": []}
    for source in manifest.sources:
        if file_sha256(source.raw_path) != source.raw_sha256:
            raise RuntimeError(f"SHA-256 источника изменился: {source.source_id}.")
        with source.raw_path.open("r", encoding="utf-8") as raw_file:
            for line in raw_file:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise RuntimeError("Проверенный JSONL содержит не-объект.")
                split = payload.get("split")
                if split not in texts_by_split:
                    raise RuntimeError("Проверенный JSONL содержит неизвестный split.")
                texts_by_split[split].extend(_texts_from_record(payload))
    if not all(texts_by_split.values()):
        raise RuntimeError("Для выбора tokenizer нужны непустые train, validation и test.")
    return texts_by_split, audit_report


def measure_tokenizer(
    tokenizer: BrainV3Tokenizer,
    texts: Iterable[str],
) -> TokenizationMetrics:
    """Считает обратимость и сжатие, не сохраняя тестовые строки в отчёт."""
    started_at = time.perf_counter()
    records = 0
    characters = 0
    tokens = 0
    unknown_tokens = 0
    byte_fallback_tokens = 0
    round_trip_exact_records = 0
    maximum_tokens_per_record = 0
    for text in texts:
        identifiers = tokenizer.encode(text)
        decoded = tokenizer.decode(identifiers)
        records += 1
        characters += len(text)
        tokens += len(identifiers)
        unknown_tokens += identifiers.count(tokenizer.unk_id)
        byte_fallback_tokens += sum(
            1
            for identifier in identifiers
            if tokenizer.id_to_token.get(identifier, "").startswith(BYTE_TOKEN_PREFIX)
        )
        if decoded == text:
            round_trip_exact_records += 1
        maximum_tokens_per_record = max(maximum_tokens_per_record, len(identifiers))
    return TokenizationMetrics(
        records=records,
        characters=characters,
        tokens=tokens,
        unknown_tokens=unknown_tokens,
        byte_fallback_tokens=byte_fallback_tokens,
        round_trip_exact_records=round_trip_exact_records,
        maximum_tokens_per_record=maximum_tokens_per_record,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def _candidate_is_eligible(metrics: TokenizationMetrics) -> bool:
    """Не допускает кандидат с потерей текста или неизвестными токенами."""
    return (
        metrics.records > 0
        and metrics.unknown_tokens == 0
        and metrics.round_trip_exact_records == metrics.records
    )


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, int, str]:
    validation = candidate["validation"]
    return (
        float(validation["average_tokens_per_record"]),
        int(candidate["vocabulary_size"]),
        str(candidate["name"]),
    )


def _display_path(path: Path) -> str:
    """Делает путь отчёта относительным только когда он внутри проекта."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _report_markdown(payload: dict[str, Any]) -> str:
    """Формирует отчёт без отображения исходных фраз или словаря tokenizer."""
    lines = [
        "# Brain v3 — выбор собственного tokenizer",
        "",
        "## Границы эксперимента",
        "",
        "- Словарь строился только на разрешённой train-части корпуса.",
        "- Validation использовалась для выбора; test была измерена только после выбора.",
        "- В отчёт не выводятся тексты корпуса, тестовые фразы или список подслов.",
        "- Этот артефакт не является запуском обучения языковой модели.",
        "",
        "## Аудит входного корпуса",
        "",
        f"- Принято записей: `{payload['corpus_audit']['records_accepted']}`.",
        f"- Отклонено записей: `{payload['corpus_audit']['records_rejected']}`.",
        f"- Близких пересечений split: `{payload['corpus_audit']['cross_split_near_duplicate_candidates']}`.",
        "",
        "## Кандидаты по validation",
        "",
        "| Кандидат | Словарь | Средняя длина | Unknown | Round-trip |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for candidate in payload["candidates"]:
        validation = candidate["validation"]
        lines.append(
            "| {name} | {vocabulary_size} | {average:.3f} | {unknown} | {round_trip:.3f} |".format(
                name=candidate["name"],
                vocabulary_size=candidate["vocabulary_size"],
                average=float(validation["average_tokens_per_record"]),
                unknown=validation["unknown_tokens"],
                round_trip=float(validation["round_trip_exact_ratio"]),
            )
        )
    selected = payload["selected"]
    test = selected["test"]
    lines.extend(
        [
            "",
            "## Выбор",
            "",
            f"Выбран `{selected['name']}` с размером словаря `{selected['vocabulary_size']}`.",
            "Критерий: нулевые unknown, точный round-trip validation, затем минимальная",
            "средняя длина последовательности и размер словаря как tie-breaker.",
            "",
            "## Независимый test выбранного кандидата",
            "",
            f"- Записей: `{test['records']}`.",
            f"- Средняя длина: `{test['average_tokens_per_record']}` токена.",
            f"- Unknown: `{test['unknown_tokens']}`.",
            f"- Точный round-trip: `{test['round_trip_exact_ratio']}`.",
            f"- SHA-256 tokenizer: `{selected['tokenizer_sha256']}`.",
            "",
            "## Честное ограничение",
            "",
            "Успешный tokenizer доказывает только корректное представление текста. Он не",
            "доказывает естественный диалог и не отменяет quality gate корпуса для",
            "предобучения будущей генеративной модели.",
        ]
    )
    return "\n".join(lines) + "\n"


def select_tokenizer(
    manifest_path: Path,
    artifact_dir: Path,
    report_dir: Path,
    *,
    bpe_vocabulary_size: int,
    unigram_vocabulary_size: int,
) -> dict[str, Any]:
    """Строит два кандидата, выбирает по validation и тестирует победителя."""
    if artifact_dir.exists() or report_dir.exists():
        raise FileExistsError("Папка результата уже существует; новый прогон должен иметь новую версию.")
    texts_by_split, audit_report = load_split_texts(manifest_path)

    bpe = BrainV3BPETokenizer()
    bpe.build(texts_by_split["train"], vocabulary_size=bpe_vocabulary_size)
    unigram = BrainV3UnigramTokenizer()
    unigram.build(texts_by_split["train"], vocabulary_size=unigram_vocabulary_size)

    candidate_objects = (
        ("brain-v3-bpe", bpe),
        ("brain-v3-unigram", unigram),
    )
    candidates: list[dict[str, Any]] = []
    for name, tokenizer in candidate_objects:
        validation_metrics = measure_tokenizer(tokenizer, texts_by_split["validation"])
        candidates.append(
            {
                "name": name,
                "tokenizer_type": tokenizer.tokenizer_type,
                "vocabulary_size": tokenizer.vocab_size,
                "validation": validation_metrics.to_payload(),
                "eligible": _candidate_is_eligible(validation_metrics),
            }
        )
    eligible_candidates = [
        candidate
        for candidate in candidates
        if candidate["eligible"]
    ]
    if not eligible_candidates:
        raise RuntimeError("Ни один tokenizer не прошёл round-trip и unknown quality gate.")
    selected_summary = min(eligible_candidates, key=_candidate_sort_key)
    selected_name = str(selected_summary["name"])
    selected_tokenizer = dict(candidate_objects)[selected_name]

    artifact_dir.mkdir(parents=True)
    tokenizer_path = artifact_dir / "tokenizer.json"
    selected_tokenizer.save(tokenizer_path)
    selected_test = measure_tokenizer(selected_tokenizer, texts_by_split["test"])
    selected = {
        **selected_summary,
        "test": selected_test.to_payload(),
        "tokenizer_path": _display_path(tokenizer_path),
        "tokenizer_sha256": tokenizer_file_sha256(tokenizer_path),
    }
    payload = {
        "format_version": 1,
        "manifest_path": _display_path(manifest_path),
        "corpus_audit": audit_report,
        "selection_scope": {
            "build_split": "train",
            "selection_split": "validation",
            "final_measurement_split": "test",
        },
        "candidates": candidates,
        "selected": selected,
        "training_started": False,
    }
    report_dir.mkdir(parents=True)
    (report_dir / "brain_v3_tokenizer_selection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "BRAIN_V3_TOKENIZER_REPORT.md").write_text(
        _report_markdown(payload),
        encoding="utf-8",
    )
    return payload


def parse_arguments() -> argparse.Namespace:
    """Возвращает параметры воспроизводимого запуска без опасных значений."""
    parser = argparse.ArgumentParser(description="Выбор собственного Brain v3 tokenizer.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--bpe-vocab-size", type=int, default=512)
    parser.add_argument("--unigram-vocab-size", type=int, default=512)
    arguments = parser.parse_args()
    if arguments.bpe_vocab_size < 300 or arguments.unigram_vocab_size < 300:
        parser.error("Размер словаря должен быть не меньше 300 из-за byte-fallback.")
    return arguments


def main() -> None:
    """Запускает выбор и выводит только путь к безопасному отчёту."""
    arguments = parse_arguments()
    payload = select_tokenizer(
        arguments.manifest.resolve(),
        arguments.artifact.resolve(),
        arguments.report.resolve(),
        bpe_vocabulary_size=arguments.bpe_vocab_size,
        unigram_vocabulary_size=arguments.unigram_vocab_size,
    )
    print(
        "Выбран tokenizer: {name}; отчёт: {report}".format(
            name=payload["selected"]["name"],
            report=arguments.report,
        )
    )


if __name__ == "__main__":
    main()

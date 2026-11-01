"""Формирует Markdown-отчёт о качестве локального обучающего датасета."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_core.data_quality import (
    analyze_dataset_quality,
    analyze_topic_distribution,
    render_quality_report,
    render_topic_distribution,
)
from ai_core.dialogue_data import (
    dialogue_dataset_fingerprint,
    dialogue_examples_to_pairs,
    load_dialogue_examples,
)
from ai_core.training_corpus import load_web_foundation_pairs
from ai_core.training_corpus import WEB_DIALOGUE_DATASET_PATH
from ai_core.training_data import load_training_pairs
from ai_core.dataset_tools import dataset_fingerprint


DATASET_PATH = PROJECT_ROOT / "training_data" / "dataset.jsonl"
REPORT_PATH = PROJECT_ROOT / "DATASET_QUALITY_REPORT.md"
WEB_FOUNDATION_REPORT_PATH = PROJECT_ROOT / "WEB_FOUNDATION_DATASET_REPORT.md"
WEB_DIALOGUE_REPORT_PATH = PROJECT_ROOT / "WEB_DIALOGUE_DATASET_REPORT.md"


def main() -> None:
    """Загружает JSONL и сохраняет только производный Markdown-отчёт."""
    pairs = load_training_pairs(DATASET_PATH)
    report = analyze_dataset_quality(pairs)
    topic_counts = analyze_topic_distribution(pairs)
    markdown = render_quality_report(report) + "\n" + render_topic_distribution(
        topic_counts
    )
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    foundation_pairs = load_web_foundation_pairs()
    foundation_report = analyze_dataset_quality(foundation_pairs)
    foundation_topics = analyze_topic_distribution(foundation_pairs)
    foundation_markdown = "\n".join(
        [
            "# Отчёт о ручном наборе веб-разработки",
            "",
            "Файл создан внутри проекта и не содержит готовых моделей или чужих весов.",
            "Исходный `training_data/dataset.jsonl` этим набором не изменяется.",
            "",
            f"- Файл: `training_data/web_foundation.jsonl`.",
            f"- SHA-256 набора: `{dataset_fingerprint(foundation_pairs)}`.",
            "",
            render_quality_report(foundation_report).rstrip(),
            "",
            render_topic_distribution(foundation_topics).rstrip(),
        ]
    ) + "\n"
    WEB_FOUNDATION_REPORT_PATH.write_text(
        foundation_markdown,
        encoding="utf-8",
    )
    web_dialogue_examples = load_dialogue_examples(WEB_DIALOGUE_DATASET_PATH)
    web_dialogue_pairs = dialogue_examples_to_pairs(web_dialogue_examples)
    web_dialogue_report = analyze_dataset_quality(web_dialogue_pairs)
    web_dialogue_markdown = "\n".join(
        [
            "# Отчёт о веб-диалогах",
            "",
            "Эти примеры добавляются только в train-часть тематического обучения.",
            "Они не заменяют validation/test и не изменяют историю реальных диалогов.",
            "",
            f"- Файл: `training_data/{WEB_DIALOGUE_DATASET_PATH.name}`.",
            f"- Диалоговых примеров: {len(web_dialogue_examples)}.",
            f"- Fingerprint: `{dialogue_dataset_fingerprint(web_dialogue_examples)}`.",
            "",
            render_quality_report(web_dialogue_report).rstrip(),
        ]
    ) + "\n"
    WEB_DIALOGUE_REPORT_PATH.write_text(
        web_dialogue_markdown,
        encoding="utf-8",
    )
    print(f"Отчёт сохранён: {REPORT_PATH}")
    print(f"Отчёт веб-набора сохранён: {WEB_FOUNDATION_REPORT_PATH}")
    print(f"Отчёт веб-диалогов сохранён: {WEB_DIALOGUE_REPORT_PATH}")
    print(
        f"Примеров: {report.total_examples}; конфликтов: "
        f"{report.conflicting_prompt_groups}; веб-примеров: "
        f"{foundation_report.total_examples}; веб-диалогов: "
        f"{len(web_dialogue_examples)}."
    )


if __name__ == "__main__":
    main()

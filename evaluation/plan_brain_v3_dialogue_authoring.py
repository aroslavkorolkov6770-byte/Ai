"""Создаёт immutable metadata-only blueprint следующего dialogue-кандидата."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from ai_core.brain_v3_dialogue_authoring import (
    DialogueAuthoringError,
    authoring_blueprint_markdown,
    build_dialogue_authoring_blueprint,
)
from ai_core.brain_v3_dialogue_coverage import (
    DEFAULT_POLICY,
    DialogueCoverageError,
    load_dialogue_coverage_policy,
)


ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = ROOT / "evaluation" / "results"
DEFAULT_COVERAGE_REPORT = (
    RESULTS_ROOT
    / "brain-v3-dialogue-coverage-v0.4"
    / "brain_v3_dialogue_coverage.json"
)
DEFAULT_REPORT_DIRECTORY = RESULTS_ROOT / "brain-v3-dialogue-authoring-blueprint-v0.4"


def _require_path_inside(path: Path, root: Path, code: str) -> Path:
    """Разрешает CLI читать и писать только внутри известного project root."""
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise DialogueAuthoringError(code) from error
    return resolved


def _load_coverage_report(path: Path) -> dict[str, Any]:
    """Загружает metadata report и не передаёт его свободные поля в blueprint."""
    resolved = _require_path_inside(path, RESULTS_ROOT, "coverage_report_outside_results")
    if not resolved.is_file():
        raise DialogueAuthoringError("coverage_report_not_found")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DialogueAuthoringError("coverage_report_invalid_json") from error
    if not isinstance(payload, dict):
        raise DialogueAuthoringError("coverage_report_invalid_json")
    return payload


def create_report(
    coverage_report_path: Path,
    policy_path: Path,
    report_directory: Path,
) -> dict[str, Any]:
    """Сохраняет новый report-dir атомарно и никогда не перезаписывает старый."""
    destination = _require_path_inside(
        report_directory,
        RESULTS_ROOT,
        "authoring_report_outside_results",
    )
    if destination.exists():
        raise DialogueAuthoringError("authoring_report_directory_exists")
    coverage_report = _load_coverage_report(coverage_report_path)
    policy = load_dialogue_coverage_policy(
        _require_path_inside(policy_path, ROOT, "dialogue_policy_outside_project")
    )
    blueprint = build_dialogue_authoring_blueprint(coverage_report, policy)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=destination.parent,
        prefix=".dialogue-authoring-blueprint-",
    ) as temporary_directory:
        temporary = Path(temporary_directory)
        (temporary / "dialogue_authoring_blueprint.json").write_text(
            json.dumps(blueprint, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (temporary / "DIALOGUE_AUTHORING_BLUEPRINT.md").write_text(
            authoring_blueprint_markdown(blueprint),
            encoding="utf-8",
        )
        temporary.replace(destination)
    return blueprint


def main() -> None:
    """Разбирает только явные local paths и печатает safe JSON blueprint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coverage-report",
        type=Path,
        default=DEFAULT_COVERAGE_REPORT,
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--report-directory",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY,
    )
    args = parser.parse_args()
    try:
        blueprint = create_report(
            args.coverage_report,
            args.policy,
            args.report_directory,
        )
    except (DialogueAuthoringError, DialogueCoverageError, OSError, ValueError) as error:
        raise SystemExit(f"Dialogue authoring blueprint остановлен: {error}") from error
    print(json.dumps(blueprint, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

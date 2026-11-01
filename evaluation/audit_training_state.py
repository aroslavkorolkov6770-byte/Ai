"""Инвентаризация существующих учебных файлов и весов без обучения и сети."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(output):
    manifest_path = ROOT / "data/brain_v3/corpus_manifest.v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = []
    # Потоковое чтение не держит большой корпус в RAM. Тексты в отчёт не пишем.
    for source in manifest["sources"]:
        path = manifest_path.parent / source["raw_path"]
        counts, types, tiers = Counter(), Counter(), Counter()
        turns = 0
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                counts[record["split"]] += 1
                types[record["record_type"]] += 1
                tiers[record["quality_tier"]] += 1
                turns += len(record.get("turns", []))
        digest = digest_file(path)
        sources.append({"source_id": source["source_id"], "records": sum(counts.values()),
                        "splits": dict(counts), "record_types": dict(types),
                        "quality_tiers": dict(tiers), "dialogue_turns": turns,
                        "sha256": digest, "matches_manifest": digest == source["raw_sha256"],
                        "declared_training_approval": source["approved_for_training"]})
    artifact = ROOT / "model_artifacts/tiny_transformer.pt"
    checkpoint = torch.load(artifact, map_location="cpu", weights_only=True)
    metrics = json.loads((artifact.parent / "training_metrics.json").read_text(encoding="utf-8"))
    report = {
        "at_utc": datetime.now(timezone.utc).isoformat(), "manifest_sha256": digest_file(manifest_path),
        "sources": sources, "corpus_records": sum(s["records"] for s in sources),
        "all_source_hashes_match": all(s["matches_manifest"] for s in sources),
        "active_checkpoint": {"sha256": digest_file(artifact),
            "version": checkpoint["model_version"], "architecture": checkpoint["architecture"],
            "parameters": sum(value.numel() for value in checkpoint["model_state"].values()),
            "config": checkpoint["model_config"],
            "reported_train_examples": metrics["training_examples_total"],
            "reported_test_token_accuracy": metrics["test_token_accuracy"]},
        "runtime": {"torch": torch.__version__, "cuda_available": torch.cuda.is_available()},
        "limitations": ["Это инвентаризация; смысловая оценка и near-duplicate audit не запускались повторно.",
                        "Одобрение источника в манифесте не означает ручную проверку каждой записи.",
                        "Точность токенов из старого отчёта не равна качеству свободных ответов."],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "training_state.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": report["corpus_records"], "source_hashes_match": report["all_source_hashes_match"],
                      "parameters": report["active_checkpoint"]["parameters"], "cuda": report["runtime"]["cuda_available"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        audit(args.output)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f"Инвентаризация не завершена: {error}\n")

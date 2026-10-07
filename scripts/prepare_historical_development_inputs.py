"""Project only TRAIN and VALIDATION historical cases from a sealed split plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from kiwoom_monitor.application.historical_research_split import (
    load_historical_evaluation_plan,
)
from kiwoom_monitor.application.research_splits import (
    DEVELOPMENT_PARTITION_VERSION,
    DevelopmentPartitionSpec,
    ResearchEvaluationSpec,
)
from kiwoom_monitor.infrastructure.historical_reconstruction import (
    write_historical_research_input,
)
from kiwoom_monitor.infrastructure.research_data_source import (
    development_partition_start,
    load_frozen_research_export,
    prepare_development_partition,
)


PACKAGE_VERSION = "historical_development_inputs/v1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="봉인된 역사 분할 계획에서 TRAIN·VALIDATION 입력만 만듭니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--split-plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source = load_frozen_research_export(args.source)
    plan = load_historical_evaluation_plan(args.split_plan)
    if (
        plan["source_dataset_id"] != source.manifest.get("dataset_id")
        or plan["source_revision_ids_hash"] != source.manifest.get("revision_ids_hash")
    ):
        raise ValueError("historical split plan does not bind to the selected source")
    if plan["oos_status"] != "SEALED" or plan["oos_results_included"] is not False:
        raise ValueError("historical development projection requires sealed OOS")
    evaluation = ResearchEvaluationSpec.from_dict(plan["evaluation"])
    development = tuple(
        row for row in plan["case_assignments"] if row["role"] in {"TRAIN", "VALIDATION"}
    )
    if [row["role"] for row in development] != ["TRAIN", "VALIDATION"]:
        raise ValueError("historical development projection requires TRAIN and VALIDATION")

    output = args.output
    if output.exists():
        raise ValueError("historical development input package is immutable")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        partitions = []
        for assignment in development:
            role = str(assignment["role"])
            fold_name = str(assignment["fold_name"])
            partition = DevelopmentPartitionSpec(DEVELOPMENT_PARTITION_VERSION, fold_name)
            selected = partition.evaluation_for(evaluation)
            projected = prepare_development_partition(source, partition, evaluation)
            child_name = role.lower()
            child = staging / child_name
            write_historical_research_input(projected, child)
            verified = load_frozen_research_export(child)
            development_partition_start(verified, selected)
            partitions.append({
                "role": role,
                "fold_name": fold_name,
                "path": child_name,
                "dataset_id": verified.manifest["dataset_id"],
                "revision_count": verified.manifest["revision_count"],
                "revision_ids_hash": verified.manifest["revision_ids_hash"],
                "evaluation": selected.to_dict(),
            })
        body = {
            "schema_version": 1,
            "version": PACKAGE_VERSION,
            "source_dataset_id": plan["source_dataset_id"],
            "source_revision_ids_hash": plan["source_revision_ids_hash"],
            "split_plan_id": plan["plan_id"],
            "partitions": partitions,
            "oos_included": False,
        }
        manifest = {**body, "package_id": "historical-development-" + _hash_document(body)}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8",
        )
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(json.dumps({
        "status": "ok",
        "output": str(output),
        "package_id": manifest["package_id"],
        "partitions": partitions,
        "oos_included": False,
    }, ensure_ascii=False))
    return 0


def _hash_document(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

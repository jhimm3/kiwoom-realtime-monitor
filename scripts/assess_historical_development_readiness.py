"""Measure whether frozen historical TRAIN/VALIDATION inputs are complete."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_research_readiness import (
    assess_historical_development_readiness,
)
from kiwoom_monitor.infrastructure.research_data_source import load_research_input


def assess_package(package: Path) -> dict[str, object]:
    package = package.resolve()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != "historical_development_inputs/v1":
        raise ValueError("unsupported historical development package")
    if manifest.get("oos_included") is not False:
        raise ValueError("readiness assessment requires an OOS-free package")
    reports = []
    for partition in manifest.get("partitions", []):
        if partition.get("role") not in {"TRAIN", "VALIDATION"}:
            raise ValueError("readiness assessment allows TRAIN and VALIDATION only")
        dataset = load_research_input(
            package / str(partition["path"]), session_profile="krx-regular/v1",
        )
        reports.append(assess_historical_development_readiness(dataset).to_dict())
    if len(reports) != 2:
        raise ValueError("readiness assessment requires exactly two development partitions")
    return {
        "version": "historical_development_package_readiness/v1",
        "package_id": str(manifest.get("package_id", "")),
        "status": "READY" if all(item["status"] == "READY" for item in reports) else "BLOCKED",
        "oos_included": False,
        "partitions": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = assess_package(args.package)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())

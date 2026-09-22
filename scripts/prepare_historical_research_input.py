"""Adapt one immutable reconstruction case to the existing research runner input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.infrastructure.historical_reconstruction import (
    adapt_historical_reconstruction_for_research,
    load_historical_reconstruction,
    write_historical_research_input,
)
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_research_export


def main() -> int:
    parser = argparse.ArgumentParser(
        description="과거 복원 사례 하나를 기존 1분 전략 runner용 동결 입력으로 변환합니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = load_historical_reconstruction(args.source)
    dataset = adapt_historical_reconstruction_for_research(source, selected_date=args.date)
    manifest = write_historical_research_input(dataset, args.output)
    verified = load_frozen_research_export(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "revision_count": len(verified.observations),
        "source_dataset_id": manifest["source_dataset_id"],
        "rank_factor_supported": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

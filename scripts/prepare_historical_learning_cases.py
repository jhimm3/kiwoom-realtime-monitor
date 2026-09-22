"""Create immutable retrospective LLM learning cases from a reconstruction export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_learning_cases import (
    build_historical_learning_cases,
    load_historical_learning_cases,
    write_historical_learning_cases,
)
from kiwoom_monitor.infrastructure.historical_reconstruction import load_historical_reconstruction


def main() -> int:
    parser = argparse.ArgumentParser(
        description="사후 후보·복원 뉴스·미래 결과를 분리한 LLM 사례 묶음을 만듭니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = load_historical_reconstruction(args.source)
    dataset = build_historical_learning_cases(source)
    manifest = write_historical_learning_cases(dataset, args.output)
    verified = load_historical_learning_cases(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "case_count": len(verified.cases),
        "counts": manifest["counts"],
        "strict_backtest_input": False,
        "ai_interpretation_generated": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

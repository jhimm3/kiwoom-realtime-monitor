"""Prepare immutable TRAIN/VALIDATION news inputs while omitting sealed OOS payload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_news_development_inputs import (
    build_historical_news_development_inputs,
    load_historical_news_development_inputs,
    write_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_event_split import (
    load_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    load_historical_news_review_decisions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="사람 검토 뉴스의 TRAIN·VALIDATION 개발 입력만 동결합니다."
    )
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--event-split", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    dataset = build_historical_news_development_inputs(
        load_historical_news_review_decisions(args.decisions),
        load_historical_news_event_split(args.event_split),
    )
    manifest = write_historical_news_development_inputs(dataset, args.output)
    verified = load_historical_news_development_inputs(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "counts": manifest["counts"],
        "train_records": len(verified.train),
        "validation_records": len(verified.validation),
        "oos_included": False,
        "model_weight_training_ready": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

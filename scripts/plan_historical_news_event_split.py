"""Create an immutable chronological split of human-reviewed canonical news events."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_news_event_split import (
    build_historical_news_event_split,
    load_historical_news_event_split,
    write_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    load_historical_news_review_decisions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="사람 검토 뉴스 사건을 시간순 TRAIN/VALIDATION/OOS 계획으로 동결합니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--train-events", required=True, type=int)
    parser.add_argument("--validation-events", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    decisions = load_historical_news_review_decisions(args.source)
    plan = build_historical_news_event_split(
        decisions,
        train_events=args.train_events,
        validation_events=args.validation_events,
    )
    write_historical_news_event_split(plan, args.output)
    verified = load_historical_news_event_split(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "plan_id": verified["plan_id"],
        "counts": verified["counts"],
        "partitions": verified["partitions"],
        "oos_status": verified["boundaries"]["oos_status"],
        "model_weight_training_ready": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

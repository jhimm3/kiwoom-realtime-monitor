"""Create an immutable review queue from retrospective learning cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_learning_cases import load_historical_learning_cases
from kiwoom_monitor.application.historical_news_review_queue import (
    build_historical_news_review_queue,
    load_historical_news_review_queue,
    write_historical_news_review_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="과거 뉴스 관계를 기사 단위로 묶은 사람 검토 대기열을 만듭니다."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = load_historical_learning_cases(args.source)
    dataset = build_historical_news_review_queue(source)
    manifest = write_historical_news_review_queue(dataset, args.output)
    verified = load_historical_news_review_queue(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "item_count": len(verified.items),
        "counts": manifest["counts"],
        "llm_used": False,
        "human_review_complete": False,
        "model_weight_training_ready": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

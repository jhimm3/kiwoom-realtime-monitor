"""Prepare an immutable target-free request set from news VALIDATION inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_news_blind_validation import (
    build_historical_news_blind_validation,
    load_historical_news_blind_validation,
    write_historical_news_blind_validation,
)
from kiwoom_monitor.application.historical_news_development_inputs import (
    load_historical_news_development_inputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="과거 뉴스 VALIDATION에서 정답이 없는 공통 평가 요청을 만듭니다."
    )
    parser.add_argument("--development-inputs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    dataset = build_historical_news_blind_validation(
        load_historical_news_development_inputs(args.development_inputs)
    )
    manifest = write_historical_news_blind_validation(dataset, args.output)
    verified = load_historical_news_blind_validation(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "request_set_id": manifest["request_set_id"],
        "requests": len(verified.requests),
        "human_target_included": False,
        "oos_included": False,
        "training_allowed": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

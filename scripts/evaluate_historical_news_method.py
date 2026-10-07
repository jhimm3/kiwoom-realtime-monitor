"""Evaluate one blind method result against held validation targets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_news_blind_validation import (
    load_historical_news_blind_validation,
)
from kiwoom_monitor.application.historical_news_development_inputs import (
    load_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_method_evaluation import (
    build_historical_news_method_evaluation,
    load_historical_news_method_evaluation,
    write_historical_news_method_evaluation,
)
from kiwoom_monitor.application.historical_news_method_results import (
    load_historical_news_method_results,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="블라인드 뉴스 방법 결과를 VALIDATION 정답으로 채점합니다.")
    parser.add_argument("--development-inputs", required=True, type=Path)
    parser.add_argument("--blind-validation", required=True, type=Path)
    parser.add_argument("--method-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = build_historical_news_method_evaluation(
        load_historical_news_development_inputs(args.development_inputs),
        load_historical_news_blind_validation(args.blind_validation),
        load_historical_news_method_results(args.method_results),
    )
    write_historical_news_method_evaluation(report, args.output)
    verified = load_historical_news_method_evaluation(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "evaluation_id": verified["evaluation_id"],
        "method": verified["method"],
        "metrics": verified["metrics"],
        "oos_used": False,
        "automatic_model_promotion": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

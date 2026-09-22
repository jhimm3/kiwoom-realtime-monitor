"""Bind external method predictions to one blind historical-news request set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_news_blind_validation import (
    load_historical_news_blind_validation,
)
from kiwoom_monitor.application.historical_news_method_results import (
    build_historical_news_method_results,
    load_historical_news_method_results,
    write_historical_news_method_results,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="블라인드 뉴스 예측 결과를 불변 저장합니다.")
    parser.add_argument("--blind-validation", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--method-kind", required=True, choices=("prompt_baseline", "rag", "fine_tuned"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--implementation-version", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    predictions = []
    for line in args.predictions.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("prediction JSONL rows must be objects")
        predictions.append(value)
    dataset = build_historical_news_method_results(
        load_historical_news_blind_validation(args.blind_validation),
        method={
            "kind": args.method_kind,
            "provider": args.provider,
            "model": args.model,
            "implementation_version": args.implementation_version,
            "artifact_id": args.artifact_id,
        },
        predictions=predictions,
    )
    manifest = write_historical_news_method_results(dataset, args.output)
    verified = load_historical_news_method_results(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "result_set_id": manifest["result_set_id"],
        "predictions": len(verified.predictions),
        "abstained": manifest["counts"]["abstained"],
        "human_target_used": False,
        "oos_used": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

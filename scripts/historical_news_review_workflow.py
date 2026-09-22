"""Export review CSVs and import validated historical news decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.application.historical_news_review_decisions import (
    build_historical_news_review_decisions,
    export_historical_news_review_sheet,
    load_historical_news_review_decisions,
    write_historical_news_review_decisions,
)
from kiwoom_monitor.application.historical_news_review_queue import (
    load_historical_news_review_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="과거 뉴스 사람 검토 표를 내보내거나 결과를 가져옵니다.")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="편집 가능한 UTF-8 CSV 검토표를 만듭니다.")
    export.add_argument("--source", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--include-all", action="store_true")
    export.add_argument("--limit", type=int)

    import_command = commands.add_parser("import", help="완료된 행을 검증해 불변 결과로 만듭니다.")
    import_command.add_argument("--source", required=True, type=Path)
    import_command.add_argument("--input", required=True, type=Path)
    import_command.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    source = load_historical_news_review_queue(args.source)
    if args.command == "export":
        result = export_historical_news_review_sheet(
            source,
            args.output,
            rule_relevant_only=not args.include_all,
            limit=args.limit,
        )
        print(json.dumps({"status": "ok", "output": str(args.output), **result}, ensure_ascii=False))
        return 0

    dataset = build_historical_news_review_decisions(source, args.input)
    manifest = write_historical_news_review_decisions(dataset, args.output)
    verified = load_historical_news_review_decisions(args.output)
    print(json.dumps({
        "status": "ok",
        "output": str(args.output),
        "dataset_id": manifest["dataset_id"],
        "decision_count": len(verified.decisions),
        "counts": manifest["counts"],
        "source_queue_review_complete": manifest["boundaries"]["source_queue_review_complete"],
        "model_weight_training_ready": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
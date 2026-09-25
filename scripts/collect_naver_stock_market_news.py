"""Collect date-scoped Naver Stock flash and world feeds into the historical DB."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.naver_stock_market_news import SOURCES, collect_day


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 증권 플래시·해외뉴스를 날짜별 원응답과 발행시각으로 저장")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--source", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--database", type=Path, default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--delay", type=float, default=0.7)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end or args.max_pages < 1 or args.delay < 0:
        parser.error("invalid date range, max-pages, or delay")
    current = start
    failed = 0
    while current <= end:
        for source in SOURCES if args.source == "all" else (args.source,):
            try:
                state = collect_day(args.database, source, current.isoformat(),
                                    max_pages=args.max_pages, delay_seconds=args.delay)
                print(json.dumps({"source": source, "date": current.isoformat(),
                                  "state": state["state"], "pages": state["pages"],
                                  "articles": state["articles"]}, ensure_ascii=False), flush=True)
            except Exception as error:
                failed += 1
                print(json.dumps({"source": source, "date": current.isoformat(),
                                  "state": "error", "error": f"{type(error).__name__}: {error}"},
                                 ensure_ascii=False), file=sys.stderr, flush=True)
        current += timedelta(days=1)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

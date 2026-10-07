"""Resume PC BODY/RULE preparation for audited, already collected market articles.

The input is the fixed missing-row manifest produced by
``audit_market_news_recovery.py``. Source rows are read only. Existing ready
prepared rows are skipped, so restarting this command is safe.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.import_historical_market_news_to_nas import _catalog_matcher
from scripts.preprocess_historical_news_locally import ConcurrentArticlePreparation


def _write_status(path: Path, payload: dict) -> None:
    payload["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    audit = ROOT / "data/historical_collection/audits/market-news-20260929"
    parser.add_argument("--manifest", type=Path, default=audit / "market-news-unprepared.csv")
    parser.add_argument("--market-database", type=Path, default=ROOT / "data/naver_stock_market_news.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "data/historical_collection/prepared-market-news.sqlite3")
    parser.add_argument("--candidates", type=Path, default=Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"))
    parser.add_argument("--status", type=Path, default=audit / "preparation-status.json")
    parser.add_argument("--error-log", type=Path, default=audit / "preparation-errors.jsonl")
    parser.add_argument("--body-log", type=Path, default=audit / "preparation-body-errors.jsonl")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="Maximum manifest rows; zero means all")
    parser.add_argument("--progress-every", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.limit < 0 or args.progress_every < 1:
        parser.error("workers must be 1..8, limit non-negative, progress-every positive")
    manifest = args.manifest.resolve(strict=True)
    market = args.market_database.resolve(strict=True)
    output = args.output.resolve(strict=True)
    matcher = _catalog_matcher(args.candidates.resolve(strict=True))
    args.status.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    state = {"state": "running", "manifest": str(manifest), "output": str(output),
             "started_at_utc": datetime.now(timezone.utc).isoformat(), "manifest_rows_seen": 0,
             "submitted_or_skipped": 0, "ready": 0, "failed": 0, "already_ready": 0,
             "workers": args.workers, "body_timeout_seconds": 2.0}
    _write_status(args.status, state)

    with (args.error_log.open("a", encoding="utf-8") as errors,
          args.body_log.open("a", encoding="utf-8", buffering=1) as body_errors):
        body_log_lock = threading.Lock()

        def record_body_request(url: str, status: str, elapsed_ms: int, error: str) -> None:
            if status == "ok":
                return
            line = json.dumps({"at_utc": datetime.now(timezone.utc).isoformat(),
                               "identity": url, "status": status,
                               "elapsed_ms": elapsed_ms, "error": error},
                              ensure_ascii=False) + "\n"
            with body_log_lock:
                body_errors.write(line)

        def record_error(scope: str, code: str, identity: str, detail: str) -> None:
            errors.write(json.dumps({"at_utc": datetime.now(timezone.utc).isoformat(),
                                     "scope": scope, "code": code, "identity": identity,
                                     "error": detail}, ensure_ascii=False) + "\n")
            errors.flush()

        try:
            with ConcurrentArticlePreparation(output=output, search_database=market,
                                              market_database=market, matcher=matcher,
                                              workers=args.workers, body_timeout_seconds=2.0,
                                              error_observer=record_error,
                                              body_request_observer=record_body_request) as preparation:
                with manifest.open(newline="", encoding="utf-8-sig") as source:
                    for row in csv.DictReader(source):
                        if args.limit and state["manifest_rows_seen"] >= args.limit:
                            break
                        state["manifest_rows_seen"] += 1
                        identity = row["identity"]
                        if not identity or row["source"] not in ("flash", "world"):
                            raise ValueError(f"invalid manifest row {state['manifest_rows_seen']}")
                        preparation.submit("historical_market", "GLOBAL", identity)
                        state["submitted_or_skipped"] += 1
                        if state["manifest_rows_seen"] % args.progress_every == 0:
                            preparation.drain()
                            state.update(ready=preparation.ready, failed=preparation.failed,
                                         already_ready=preparation.skipped,
                                         elapsed_seconds=round(time.time() - started, 1))
                            _write_status(args.status, state)
                            print(json.dumps(state, ensure_ascii=False), flush=True)
            state.update(state="complete", ready=preparation.ready, failed=preparation.failed,
                         already_ready=preparation.skipped,
                         elapsed_seconds=round(time.time() - started, 1))
        except BaseException as error:
            state.update(state="stopped", error=f"{type(error).__name__}: {error}",
                         elapsed_seconds=round(time.time() - started, 1))
            _write_status(args.status, state)
            raise
    _write_status(args.status, state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

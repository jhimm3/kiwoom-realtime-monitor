"""Recheck a deterministic sample of summary-only market articles, read-only.

This records the *current* response and extraction result for each sampled URL.
It cannot reconstruct the HTTP response that caused the original fallback.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiwoom_monitor.infrastructure.article_text import (
    parse_article_text_with_metadata, system_ssl_context,
)

AUDIT = ROOT / "data/historical_collection/audits/market-news-20260929"


def _sample(path: Path, per_group: int) -> list[dict[str, str]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row["body_status"] == "summary_only":
                groups[(row["source"], row["target_date"][:4])].append(row)
    rng = random.Random(20260929)
    return sorted((row for group in groups.values()
                   for row in rng.sample(group, min(per_group, len(group)))),
                  key=lambda row: (row["source"], row["target_date"], row["identity"]))


def _probe(row: dict[str, str], timeout: float) -> dict[str, object]:
    url = row["identity"]
    record: dict[str, object] = {"source": row["source"], "target_date": row["target_date"],
                                 "identity": url, "status": "unknown", "http_status": None,
                                 "html_bytes": None, "body_marker": None, "error": ""}
    started = time.monotonic()
    try:
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        })
        with urlopen(request, timeout=timeout, context=system_ssl_context()) as response:
            record["http_status"] = response.status
            raw = response.read(2_000_000)
            charset = response.headers.get_content_charset() if response.headers else None
        html = raw.decode(charset or "utf-8", errors="replace")
        record["html_bytes"] = len(raw)
        record["body_marker"] = "dic_area" in html or "article_body" in html
        try:
            body, _ = parse_article_text_with_metadata(html)
        except ValueError as error:
            record["status"] = "http_200_no_extractable_body"
            record["error"] = str(error)[:200]
        else:
            record["status"] = "fulltext_now"
            record["extracted_characters"] = len(body)
    except HTTPError as error:
        record["http_status"] = error.code
        record["status"] = f"http_{error.code}"
        record["error"] = str(error)[:200]
    except (TimeoutError, URLError, OSError) as error:
        record["status"] = "request_error"
        record["error"] = f"{type(error).__name__}: {error}"[:200]
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=AUDIT / "market-news-body-retry.csv")
    parser.add_argument("--output", type=Path, default=AUDIT / "market-news-body-fallback-sample.csv")
    parser.add_argument("--per-group", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--source", choices=("flash", "world"))
    args = parser.parse_args()
    if args.per_group < 1 or args.timeout <= 0:
        parser.error("per-group and timeout must be positive")
    chosen = _sample(args.input, args.per_group)
    if args.source:
        chosen = [row for row in chosen if row["source"] == args.source]
    fieldnames = ["source", "target_date", "identity", "status", "http_status",
                  "html_bytes", "body_marker", "extracted_characters", "error", "elapsed_ms"]
    counts: Counter[str] = Counter()
    with args.output.open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        for row in chosen:
            result = _probe(row, args.timeout)
            writer.writerow(result)
            destination.flush()
            counts[str(result["status"])] += 1
    print({"sampled": len(chosen), "counts": dict(counts), "output": str(args.output)})


if __name__ == "__main__":
    main()

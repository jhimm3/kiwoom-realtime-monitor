"""Process NAS historical BODY/RULE jobs on this PC through the authenticated API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.application.news_analysis import assess_stock_news, extractive_news_summary
from kiwoom_monitor.application.news_rules import classify_supply_contract
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION
from kiwoom_monitor.infrastructure.article_text import clean_article_text, fetch_article_text_with_metadata
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.historical_backfill import (
    candidate_non_stock_codes, load_article_body_snapshot,
)


def prepare_job(job: dict, article: dict, body: dict | None = None,
                *, search_database: Path | None = None,
                allow_network: bool = True) -> dict:
    """Use the same extraction and rule functions as the NAS runner."""
    result: dict = {"job_key": job["job_key"], "attempts": job["attempts"], "stage": job["stage"]}
    document = dict(article["document"])
    if job["stage"] == "BODY":
        if job["processing_version"] != ARTICLE_BODY_EXTRACTOR_VERSION:
            raise ValueError("PC와 NAS의 본문 추출기 버전이 다릅니다.")
        source = document.get("historical_source")
        if search_database is not None and isinstance(source, dict):
            snapshot = load_article_body_snapshot(
                search_database, str(source.get("provider") or ""),
                str(source.get("office_id") or ""), str(source.get("article_id") or ""),
            )
            if snapshot and snapshot["body_text"]:
                try:
                    fetched_at = datetime.fromisoformat(snapshot["fetched_at"]).timestamp()
                except ValueError:
                    fetched_at = time.time()
                result.update(body_text=snapshot["body_text"], body_status="fulltext",
                              fetched_at=fetched_at,
                              original_published_at=snapshot["published_at"],
                              source_url=snapshot["source_url"])
                return result
        text = published_at = fetched_url = ""
        urls = dict.fromkeys((str(document.get("link") or ""),
                             str(document.get("original_link") or ""))) if allow_network else ()
        for url in urls:
            if not url:
                continue
            try:
                fetched = fetch_article_text_with_metadata(url, timeout_seconds=15.0)
                text, published_at = fetched if isinstance(fetched, tuple) else (fetched, "")
                fetched_url = url
                break
            except Exception:
                continue
        status = "fulltext" if text else "summary_only"
        if not text:
            text = str(document.get("description") or "").strip()
        if not text:
            raise ValueError("기사 본문과 목록 요약을 가져오지 못했습니다.")
        result.update(body_text=text, body_status=status, fetched_at=time.time(),
                      original_published_at=published_at, source_url=fetched_url)
        return result

    if job["stage"] != "RULE" or body is None:
        raise ValueError("규칙 작업에 본문 리비전이 없습니다.")
    target_code = str(job["payload"].get("stock_code") or job.get("target_id") or article["stock_code"])
    target_name = str(job["payload"].get("stock_name") or document.get("stock_name") or target_code)
    document.update(identity=article["identity"], stock_code=target_code, stock_name=target_name)
    cleaned = clean_article_text(str(body.get("body_text") or ""))
    if not cleaned:
        cleaned = str(document.get("description") or "")
    assessment = assess_stock_news(target_name, str(document.get("title") or ""),
                                   str(document.get("description") or ""),
                                   article_body=cleaned if body.get("status") == "fulltext" else "")
    classified = None if target_code == "GLOBAL" else classify_supply_contract(document, cleaned)
    result.update(
        assessment=asdict(assessment),
        core_sentences=list(extractive_news_summary("", str(document.get("title") or ""), cleaned))
        if body.get("status") == "fulltext" else [],
        rule_result=classified.as_document() if classified else None,
    )
    return result


def require_pc_processing_scope(health: dict, scope: str) -> None:
    capabilities = health.get("historical_news_pc_scopes") or ()
    required = "legacy_backlog" if scope in {"pc", "all"} else (
        "market" if scope == "pc_market" else "search"
    )
    if required not in capabilities:
        raise SystemExit(
            f"NAS build does not reserve {scope} historical BODY/RULE jobs for PC; "
            "rebuild the server before starting this worker"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="PC에서 과거 뉴스 BODY/RULE을 계산해 NAS 작업 원장에 적재합니다.")
    parser.add_argument("--data-source", type=Path, default=Path("data/data_source.json"))
    parser.add_argument("--search-database", type=Path, default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--candidates", type=Path, default=Path(
        r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--scope", choices=("all", "pc", "pc_market", "pc_search"), default="pc")
    parser.add_argument("--max-jobs", type=int, default=0, help="0이면 계속 실행합니다.")
    parser.add_argument("--idle-exit-after", type=float, default=60.0)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or args.max_jobs < 0 or args.idle_exit_after < 0:
        parser.error("workers는 1~4, max-jobs/idle-exit-after는 0 이상이어야 합니다.")
    config = json.loads(args.data_source.resolve(strict=True).read_text(encoding="utf-8-sig"))
    url, token = str(config.get("server_url") or "").strip(), str(config.get("access_token") or "").strip()
    if not url or not token:
        raise SystemExit("NAS server_url/access_token 설정이 필요합니다.")
    client = CentralContentClient(url, token, timeout_seconds=60)
    require_pc_processing_scope(client.load_health(), args.scope)
    excluded_codes = tuple(sorted(candidate_non_stock_codes(args.candidates.resolve(strict=True))))
    completed = failed = 0
    idle_since = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while args.max_jobs == 0 or completed + failed < args.max_jobs:
            # The NAS claim transaction owns each job. Separate lanes avoid starving RULE.
            claimed = []
            for stage in ("BODY", "RULE"):
                for _ in range(args.workers if stage == "BODY" else max(1, args.workers // 2)):
                    if args.max_jobs and completed + failed + len(claimed) >= args.max_jobs:
                        break
                    response = client.claim_historical_news_job(stage, excluded_codes, args.scope)
                    if response.get("job"):
                        claimed.append(response)
            if not claimed:
                if args.idle_exit_after and time.monotonic() - idle_since >= args.idle_exit_after:
                    break
                time.sleep(2.0)
                continue
            idle_since = time.monotonic()
            futures = [pool.submit(prepare_job, item["job"], item["article"], item.get("body"),
                                   search_database=args.search_database)
                       for item in claimed]
            for item, future in zip(claimed, futures, strict=True):
                job = item["job"]
                try:
                    prepared = future.result()
                except Exception as error:
                    prepared = {"job_key": job["job_key"], "attempts": job["attempts"],
                                "stage": job["stage"], "error": f"{type(error).__name__}: {error}"[:1000]}
                    failed += 1
                else:
                    completed += 1
                # An HTTP failure is deliberately fatal: a claimed job will be recovered by the NAS lease.
                saved = client.complete_historical_news_job(prepared)
                print(json.dumps({"stage": job["stage"], "job_key": job["job_key"],
                                  "collection_scope": item["article"].get("collection_scope", ""),
                                  "state": saved["state"], "completed": completed, "failed": failed},
                                 ensure_ascii=False), flush=True)
    print(json.dumps({"completed": completed, "failed": failed}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

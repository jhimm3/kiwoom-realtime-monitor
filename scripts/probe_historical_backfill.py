from __future__ import annotations

import argparse
from collections import deque
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_for_futures
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.preprocess_historical_news_locally import ConcurrentArticlePreparation

from kiwoom_monitor.infrastructure.historical_backfill import (
    article_publication_records,
    claim_news_backfill_job,
    claim_news_range_job,
    clear_news_backfill_jobs,
    exclude_non_stock_news_jobs,
    fetch_article_publication,
    fetch_naver_historical_search_page,
    fetch_naver_stock_news_page,
    finish_news_backfill_job,
    finish_news_range_job,
    inspect_candidate_database,
    inspect_daishin_environment,
    import_daishin_backfill_ndjson,
    latest_candidates,
    release_news_backfill_job,
    release_news_range_job,
    seed_news_range_jobs,
    seed_news_backfill_jobs,
    store_daishin_probe_payload,
    store_article_publication_result,
    store_article_publication_results,
    store_news_search_observation,
    store_naver_historical_search_page,
    store_naver_stock_news_page,
    split_news_range_job,
)


DEFAULT_CANDIDATE_DB = Path(
    r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"
)
DEFAULT_OUTPUT_DB = Path("data/historical_intelligence.sqlite3")


def _store_resolved_observation(
    database: Path, page: object, item: object, published_at: str,
) -> None:
    source_date = str(getattr(item, "search_published_date", ""))
    # The search-page store already wrote this relation. Only an item whose
    # search response had no date needs a second relation after article fetch.
    if source_date:
        return
    if not source_date and len(published_at) >= 10:
        source_date = published_at[:10]
    if not source_date:
        return
    start_date = str(getattr(page, "target_date"))
    end_date = str(getattr(page, "target_end_date", "") or start_date)
    if start_date <= source_date <= end_date:
        store_news_search_observation(database, page, item, source_date)


def _article_request_host(item: object) -> str:
    source = str(
        getattr(item, "original_url", "") or getattr(item, "portal_url", "")
    )
    return (urlparse(source).hostname or "unknown").lower()


class _ArticleFetchPool:
    """Keep article downloads moving while the next search pages are discovered."""

    def __init__(
        self, *, workers: int, article_delay: float,
        fetcher: object = fetch_article_publication,
    ) -> None:
        self._delay = max(0.0, article_delay)
        self._fetcher = fetcher
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="news-article",
        )
        self._futures: dict[object, tuple[object, str]] = {}
        self._waiting: dict[str, deque[object]] = {}
        self._active_hosts: set[str] = set()

    def _fetch_one(self, item: object):
        try:
            return self._fetcher(item)  # type: ignore[operator]
        finally:
            if self._delay > 0:
                time.sleep(self._delay)

    def _start(self, item: object, host: str) -> None:
        future = self._pool.submit(self._fetch_one, item)
        self._futures[future] = (item, host)

    def submit(self, item: object) -> None:
        host = _article_request_host(item)
        if host in self._active_hosts:
            self._waiting.setdefault(host, deque()).append(item)
            return
        self._active_hosts.add(host)
        self._start(item, host)

    def _finish(self, future: object):
        item, host = self._futures.pop(future)
        queued = self._waiting.get(host)
        if queued:
            self._start(queued.popleft(), host)
            if not queued:
                self._waiting.pop(host, None)
        else:
            self._active_hosts.remove(host)
        return item, future.result()  # type: ignore[union-attr]

    def drain(self, *, wait: bool = False):
        while self._futures:
            ready = [future for future in self._futures if future.done()]  # type: ignore[union-attr]
            if not ready:
                if not wait:
                    return
                ready = list(
                    completed
                    for completed in wait_for_futures(
                        list(self._futures), return_when=FIRST_COMPLETED,
                    ).done
                )
            for future in ready:
                yield self._finish(future)
            if not wait:
                # Newly scheduled followers may already be complete. Drain those
                # too, then return as soon as only active work remains.
                continue

    def close(self) -> None:
        for _item, _result in self.drain(wait=True):
            pass
        self._pool.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class _RequestStartLimiter:
    def __init__(self, interval: float) -> None:
        self._interval = max(0.0, interval)
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                delay = self._next_start - now
                if delay <= 0:
                    self._next_start = now + self._interval
                    return
            time.sleep(delay)

    def defer(self, delay: float) -> None:
        """Apply one shared cooldown to every search worker."""
        with self._lock:
            self._next_start = max(
                self._next_start, time.monotonic() + max(0.0, delay),
            )


class _SearchPagePool:
    """Overlap Naver search responses without exceeding the configured start rate."""

    def __init__(
        self, *, workers: int, request_delay: float,
        fetcher: object = fetch_naver_historical_search_page,
        throttle_delay: float = 60.0,
        throttle_retries: int = 10,
        throttle_observer: object | None = None,
    ) -> None:
        self._limiter = _RequestStartLimiter(request_delay)
        self._fetcher = fetcher
        self._throttle_delay = max(0.0, throttle_delay)
        self._throttle_retries = max(0, throttle_retries)
        self._throttle_observer = throttle_observer
        self._observer_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="news-search",
        )

    def fetch_batch(self, job: object, page_indexes: list[int]):
        def fetch(page_index: int):
            target_end_date = str(
                getattr(job, "target_end_date", "")
                or getattr(job, "target_date")
            )
            throttles = 0
            while True:
                self._limiter.wait()
                try:
                    page = self._fetcher(  # type: ignore[operator]
                        getattr(job, "code"), getattr(job, "query_text"),
                        getattr(job, "target_date"), 1 + page_index * 10,
                        target_end_date=target_end_date,
                    )
                    return page_index, page
                except HTTPError as error:
                    if error.code not in {403, 429} or throttles >= self._throttle_retries:
                        raise
                    throttles += 1
                    self._limiter.defer(self._throttle_delay)
                    if self._throttle_observer is not None:
                        with self._observer_lock:
                            self._throttle_observer(  # type: ignore[operator]
                                page_index + 1, error.code, self._throttle_delay,
                            )

        futures = [self._pool.submit(fetch, page_index) for page_index in page_indexes]
        return sorted((future.result() for future in futures), key=lambda row: row[0])

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _fetch_article_publications(
    items: list[object], *, workers: int, article_delay: float,
    fetcher: object = fetch_article_publication,
):
    """Yield article fetches as they finish, with one active request per host."""
    with _ArticleFetchPool(
        workers=workers, article_delay=article_delay, fetcher=fetcher,
    ) as pool:
        for item in items:
            pool.submit(item)
        yield from pool.drain(wait=True)


def _write_news_heartbeat(
    path: Path | None, job: object, phase: str, *, page: int = 0,
    pages_observed: int = 0, items_observed: int = 0, article: int = 0,
) -> None:
    if path is None:
        return
    document = {
        "schema": "historical-news-job-heartbeat/v1",
        "pid": os.getpid(),
        "phase": phase,
        "code": str(getattr(job, "code")),
        "target_date": str(getattr(job, "target_date")),
        "query": str(getattr(job, "query_text")),
        "page": page,
        "pages_observed": pages_observed,
        "items_observed": items_observed,
        "article": article,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        for retry in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if retry == 4:
                    return
                time.sleep(0.05)
    except OSError:
        return
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="기존 후보 DB와 대신·네이버 증권 과거자료 수집 경계를 표본 검증합니다."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidates = subparsers.add_parser("candidates", help="후보 DB를 읽기 전용으로 점검합니다.")
    candidates.add_argument("--database", type=Path, default=DEFAULT_CANDIDATE_DB)
    candidates.add_argument("--limit", type=int, default=5)

    naver = subparsers.add_parser("naver-news", help="네이버 증권 종목 뉴스 목록 표본을 저장합니다.")
    naver.add_argument("code", help="6자리 종목코드")
    naver.add_argument("--pages", type=int, default=1)
    naver.add_argument("--page-size", type=int, default=20)
    naver.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    history = subparsers.add_parser(
        "naver-history", help="날짜 지정 네이버 뉴스 검색 표본을 저장합니다."
    )
    history.add_argument("code", help="6자리 종목코드")
    history.add_argument("query", help="해당 날짜에 유효한 종목명 또는 별칭")
    history.add_argument("target_date", help="YYYY-MM-DD")
    history.add_argument("--pages", type=int, default=1)
    history.add_argument(
        "--skip-original-time", action="store_true",
        help="원문 발행시각 확인을 생략하고 검색 날짜만 저장합니다.",
    )
    history.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    news_seed = subparsers.add_parser(
        "news-seed", help="후보 DB에서 날짜·종목별 과거 뉴스 작업을 생성합니다."
    )
    news_seed.add_argument("--database", type=Path, default=DEFAULT_CANDIDATE_DB)
    news_seed.add_argument("--start-date", default="")
    news_seed.add_argument("--end-date", default="")
    news_seed.add_argument(
        "--name-transition-days", type=int, default=14,
        help="상호변경일 앞뒤로 구·신 종목명을 함께 검색할 일수(기본 14일)",
    )
    news_seed.add_argument(
        "--reset-jobs", action="store_true",
        help="기사·원문 기록은 보존하고 뉴스 작업 큐만 비운 뒤 다시 생성합니다.",
    )
    news_seed.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    news_range_seed = subparsers.add_parser(
        "news-range-seed", help="밀도가 높은 대기 뉴스 일자를 월 범위 작업으로 묶습니다."
    )
    news_range_seed.add_argument("--minimum-density", type=float, default=0.5)
    news_range_seed.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    news_exclude = subparsers.add_parser(
        "news-exclude-nonstocks", help="기존 대기 원장에서 ETF·ETN·리츠 등 비주식을 제외합니다."
    )
    news_exclude.add_argument("--database", type=Path, default=DEFAULT_CANDIDATE_DB)
    news_exclude.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    news_run = subparsers.add_parser(
        "news-run", help="대기 중인 과거 뉴스 작업을 재개 가능하게 실행합니다."
    )
    news_run.add_argument("--jobs", type=int, default=1)
    news_run.add_argument("--max-pages", type=int, default=100)
    news_run.add_argument("--request-delay", type=float, default=0.5)
    news_run.add_argument("--search-workers", type=int, default=1)
    news_run.add_argument("--article-delay", type=float, default=0.2)
    news_run.add_argument("--article-workers", type=int, default=1)
    news_run.add_argument("--heartbeat-file", type=Path)
    news_run.add_argument("--prepared-output", type=Path,
                          default=Path("data/historical_collection/prepared_news.sqlite3"))
    news_run.add_argument("--prepare-workers", type=int, default=4)
    news_run.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    daishin = subparsers.add_parser("daishin-preflight", help="CREON Plus 조회 환경만 점검합니다.")
    daishin.add_argument("--no-connect", action="store_true")

    sample = subparsers.add_parser("daishin-sample", help="CREON StockChart 분봉 한 묶음을 저장합니다.")
    sample.add_argument("code", help="6자리 종목코드")
    sample.add_argument("--interval", type=int, choices=(1, 5), default=1)
    sample.add_argument("--count", type=int, default=200)
    sample.add_argument("--venue", choices=("A", "K", "N"), default="K")
    sample.add_argument("--session", choices=("regular", "regular_and_after"), default="regular")
    sample.add_argument("--adjustment", choices=("raw", "adjusted"), default="raw")
    sample.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    imported = subparsers.add_parser(
        "daishin-import", help="연속조회 NDJSON을 별도 표본 DB에 반영합니다."
    )
    imported.add_argument("artifact", type=Path)
    imported.add_argument("--before-date", default="", help="이 날짜보다 오래된 봉만 반영")
    imported.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    args = parser.parse_args()
    if args.command == "candidates":
        result = {
            "overview": asdict(inspect_candidate_database(args.database)),
            "latest_candidates": [asdict(item) for item in latest_candidates(args.database, args.limit)],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "news-seed":
        cleared = clear_news_backfill_jobs(args.output) if args.reset_jobs else 0
        inserted = seed_news_backfill_jobs(
            args.database, args.output,
            start_date=args.start_date, end_date=args.end_date,
            name_transition_days=args.name_transition_days,
        )
        print(json.dumps({
            "cleared_jobs": cleared,
            "inserted": inserted,
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "news-range-seed":
        inserted = seed_news_range_jobs(
            args.output, minimum_density=args.minimum_density,
        )
        print(json.dumps({
            "inserted_range_jobs": inserted,
            "minimum_density": args.minimum_density,
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "news-exclude-nonstocks":
        result = exclude_non_stock_news_jobs(args.database, args.output)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    if args.command == "news-run":
        if args.jobs < 1 or args.jobs > 10000:
            parser.error("--jobs must be between 1 and 10000")
        if args.max_pages < 1 or args.max_pages > 100:
            parser.error("--max-pages must be between 1 and 100")
        if args.search_workers < 1 or args.search_workers > 8:
            parser.error("--search-workers must be between 1 and 8")
        if args.article_workers < 1 or args.article_workers > 16:
            parser.error("--article-workers must be between 1 and 16")
        if args.prepare_workers < 1 or args.prepare_workers > 8:
            parser.error("--prepare-workers must be between 1 and 8")
        completed_jobs = 0
        failed_jobs = 0
        truncated_jobs = 0
        claimed_jobs = 0
        collector_unavailable = False
        for _ in range(args.jobs):
            job = claim_news_range_job(args.output) or claim_news_backfill_job(args.output)
            if job is None:
                break
            claimed_jobs += 1
            pages_observed = 0
            items_observed = 0
            usable = 0
            unreadable = 0
            missing_time = 0
            _write_news_heartbeat(args.heartbeat_file, job, "claimed")
            try:
                exhausted = False
                statuses = {}
                occurrences = []
                scheduled = set()
                item_pages = {}
                completed_articles = 0

                def record_search_throttle(
                    page: int, status: int, delay: float,
                ) -> None:
                    _write_news_heartbeat(
                        args.heartbeat_file, job, f"search_throttled_{status}",
                        page=page, pages_observed=pages_observed,
                        items_observed=items_observed,
                        article=completed_articles,
                    )

                with _SearchPagePool(
                    workers=args.search_workers, request_delay=args.request_delay,
                    throttle_observer=record_search_throttle,
                ) as search_pool, _ArticleFetchPool(
                    workers=args.article_workers, article_delay=args.article_delay,
                ) as article_pool, ConcurrentArticlePreparation(
                    output=args.prepared_output, search_database=args.output,
                    market_database=Path("data/naver_stock_market_news.sqlite3"),
                    matcher=(), workers=args.prepare_workers, allow_network=False,
                ) as preparation:
                    batch_start = 0
                    while batch_start < args.max_pages:
                        batch_width = 1 if batch_start == 0 else args.search_workers
                        indexes = list(range(
                            batch_start,
                            min(args.max_pages, batch_start + batch_width),
                        ))
                        _write_news_heartbeat(
                            args.heartbeat_file, job, "search_page",
                            page=indexes[0] + 1, pages_observed=pages_observed,
                            items_observed=items_observed,
                        )
                        batch = search_pool.fetch_batch(job, indexes)
                        for page_index, page in batch:
                            _write_news_heartbeat(
                                args.heartbeat_file, job, "search_page",
                                page=page_index + 1, pages_observed=pages_observed,
                                items_observed=items_observed,
                            )
                            store_naver_historical_search_page(args.output, page)
                            pages_observed += 1
                            items_observed += len(page.items)
                            page_records = article_publication_records(
                                args.output, list(page.items),
                            )
                            for item in page.items:
                                key = (item.office_id, item.article_id)
                                occurrences.append(key)
                                item_pages[key] = page
                                if key in statuses or key in scheduled:
                                    continue
                                status, published_at = page_records[key]
                                if status in {"", "not_fetched"}:
                                    scheduled.add(key)
                                    article_pool.submit(item)
                                else:
                                    statuses[key] = status
                                    _store_resolved_observation(
                                        args.output, page, item, published_at,
                                    )
                                    if status == "published_at_found":
                                        preparation.submit(
                                            "historical_backfill", page.code,
                                            item.original_url or item.portal_url or
                                            f"naver:{item.office_id}:{item.article_id}",
                                        )
                            drained = list(article_pool.drain())
                            store_article_publication_results(
                                args.output, [result for _item, result in drained],
                            )
                            for item, result in drained:
                                key = (item.office_id, item.article_id)
                                _store_resolved_observation(
                                    args.output, item_pages[key], item, result.published_at,
                                )
                                statuses[key] = result.status
                                if result.status == "published_at_found":
                                    preparation.submit(
                                        "historical_backfill", item_pages[key].code,
                                        item.original_url or item.portal_url or
                                        f"naver:{item.office_id}:{item.article_id}",
                                    )
                                completed_articles += 1
                                _write_news_heartbeat(
                                    args.heartbeat_file, job, "article",
                                    page=page_index + 1, pages_observed=pages_observed,
                                    items_observed=items_observed,
                                    article=completed_articles,
                                )
                            if not page.has_structured_news or len(page.items) < 10:
                                exhausted = True
                                break
                        if exhausted:
                            break
                        batch_start += batch_width
                    drained = list(article_pool.drain(wait=True))
                    store_article_publication_results(
                        args.output, [result for _item, result in drained],
                    )
                    for item, result in drained:
                        key = (item.office_id, item.article_id)
                        _store_resolved_observation(
                            args.output, item_pages[key], item, result.published_at,
                        )
                        statuses[key] = result.status
                        if result.status == "published_at_found":
                            preparation.submit(
                                "historical_backfill", item_pages[key].code,
                                item.original_url or item.portal_url or
                                f"naver:{item.office_id}:{item.article_id}",
                            )
                        completed_articles += 1
                        _write_news_heartbeat(
                            args.heartbeat_file, job, "article",
                            page=pages_observed, pages_observed=pages_observed,
                            items_observed=items_observed, article=completed_articles,
                        )
                    preparation.drain(all_pending=True)
                for key in occurrences:
                    status = statuses[key]
                    if status == "published_at_found":
                        usable += 1
                    elif status == "time_not_found":
                        missing_time += 1
                    else:
                        unreadable += 1
                state = "complete" if exhausted else "truncated"
                if job.range_id and not exhausted and job.target_date < job.target_end_date:
                    split_news_range_job(args.output, job)
                    state = "split"
                elif job.range_id:
                    finish_news_range_job(
                        args.output, job, state=state, pages_observed=pages_observed,
                        items_observed=items_observed, usable_articles=usable,
                        unreadable_articles=unreadable,
                        missing_time_articles=missing_time,
                        error="" if exhausted else "page_limit_reached",
                    )
                else:
                    finish_news_backfill_job(
                        args.output, job, state=state, pages_observed=pages_observed,
                        items_observed=items_observed, usable_articles=usable,
                        unreadable_articles=unreadable,
                        missing_time_articles=missing_time,
                        error="" if exhausted else "page_limit_reached",
                    )
                if state == "complete":
                    completed_jobs += 1
                else:
                    truncated_jobs += 1
                _write_news_heartbeat(
                    args.heartbeat_file, job, state,
                    pages_observed=pages_observed, items_observed=items_observed,
                )
                print(json.dumps({
                    "event": "news_job_finished",
                    "code": job.code,
                    "target_date": job.target_date,
                    "target_end_date": job.target_end_date or job.target_date,
                    "member_count": job.member_count,
                    "query": job.query_text,
                    "state": state,
                    "pages": pages_observed,
                    "items": items_observed,
                    "usable": usable,
                    "unreadable": unreadable,
                    "missing_time": missing_time,
                }, ensure_ascii=False), flush=True)
            except Exception as error:
                if isinstance(error, URLError):
                    release = release_news_range_job if job.range_id else release_news_backfill_job
                    release(
                        args.output, job,
                        error=f"collector_unavailable: {type(error).__name__}: {error}",
                    )
                    collector_unavailable = True
                    _write_news_heartbeat(
                        args.heartbeat_file, job, "collector_unavailable",
                        pages_observed=pages_observed, items_observed=items_observed,
                    )
                    print(json.dumps({
                        "event": "news_collector_unavailable",
                        "code": job.code,
                        "target_date": job.target_date,
                        "query": job.query_text,
                        "error": f"{type(error).__name__}: {error}",
                    }, ensure_ascii=False), flush=True)
                    break
                finish = finish_news_range_job if job.range_id else finish_news_backfill_job
                finish(
                    args.output, job, state="failed", pages_observed=pages_observed,
                    items_observed=items_observed, usable_articles=usable,
                    unreadable_articles=unreadable, missing_time_articles=missing_time,
                    error=f"{type(error).__name__}: {error}",
                )
                failed_jobs += 1
                _write_news_heartbeat(
                    args.heartbeat_file, job, "failed",
                    pages_observed=pages_observed, items_observed=items_observed,
                )
                print(json.dumps({
                    "event": "news_job_failed",
                    "code": job.code,
                    "target_date": job.target_date,
                    "query": job.query_text,
                    "pages": pages_observed,
                    "items": items_observed,
                    "error": f"{type(error).__name__}: {error}",
                }, ensure_ascii=False), flush=True)
        print(json.dumps({
            "claimed_jobs": claimed_jobs,
            "completed_jobs": completed_jobs,
            "failed_jobs": failed_jobs,
            "truncated_jobs": truncated_jobs,
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        if collector_unavailable:
            return 3
        return 2 if failed_jobs else 0
    if args.command == "daishin-preflight":
        result = inspect_daishin_environment(try_connection=False)
        output: dict[str, object] = {"python_environment": asdict(result)}
        if args.no_connect:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0 if not result.missing_progids else 2
        powershell32 = Path(
            r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
        )
        bridge = Path(__file__).with_name("daishin_stockchart_probe.ps1")
        completed = subprocess.run(
            [
                str(powershell32), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(bridge), "-Code", "005930", "-PreflightOnly",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw = completed.stdout.strip().splitlines()
        output["bridge"] = (
            json.loads(raw[-1].lstrip("\ufeff")) if raw else {
                "error": completed.stderr.strip() or "Daishin bridge returned no output"
            }
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if completed.returncode == 0 else 2
    if args.command == "daishin-sample":
        powershell32 = Path(
            r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
        )
        bridge = Path(__file__).with_name("daishin_stockchart_probe.ps1")
        completed = subprocess.run(
            [
                str(powershell32), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(bridge), "-Code", args.code, "-Interval", str(args.interval),
                "-Count", str(args.count), "-Venue", args.venue, "-Session", args.session,
                "-Adjustment", args.adjustment,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw = completed.stdout.strip().splitlines()
        if not raw:
            raise SystemExit(completed.stderr.strip() or "Daishin bridge returned no output")
        payload = json.loads(raw[-1].lstrip("\ufeff"))
        if completed.returncode or payload.get("error"):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
        saved = store_daishin_probe_payload(args.output, payload)
        print(json.dumps({
            "provider": "daishin_creon",
            "code": args.code,
            "interval": args.interval,
            "received": payload.get("received_count"),
            "saved": saved,
            "continue": payload.get("continue"),
            "oldest": payload["bars"][-1]["bar_time"] if payload.get("bars") else "",
            "newest": payload["bars"][0]["bar_time"] if payload.get("bars") else "",
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "daishin-import":
        result = import_daishin_backfill_ndjson(
            args.artifact, args.output, before_date=args.before_date,
        )
        result["output"] = str(args.output.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "naver-history":
        if args.pages < 1 or args.pages > 200:
            parser.error("--pages must be between 1 and 200")
        saved_items = 0
        pages_observed = 0
        publication_statuses: dict[str, int] = {}
        for page_index in range(args.pages):
            page = fetch_naver_historical_search_page(
                args.code, args.query, args.target_date, 1 + page_index * 10
            )
            store_naver_historical_search_page(args.output, page)
            pages_observed += 1
            saved_items += len(page.items)
            if not args.skip_original_time:
                for item in page.items:
                    result = fetch_article_publication(item)
                    store_article_publication_result(args.output, result)
                    publication_statuses[result.status] = publication_statuses.get(result.status, 0) + 1
            if not page.has_structured_news:
                break
        print(json.dumps({
            "provider": "naver_historical_search",
            "code": args.code,
            "query": args.query,
            "target_date": args.target_date,
            "pages_observed": pages_observed,
            "items_observed": saved_items,
            "publication_statuses": publication_statuses,
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.pages < 1 or args.pages > 100:
        parser.error("--pages must be between 1 and 100")
    saved_items = 0
    last_published_at = ""
    for page_number in range(1, args.pages + 1):
        page = fetch_naver_stock_news_page(args.code, page_number, args.page_size)
        store_naver_stock_news_page(args.output, page)
        saved_items += len(page.items)
        if page.items:
            last_published_at = page.items[-1].published_at
        if page.cluster_count < args.page_size:
            break
    print(json.dumps({
        "provider": "naver_stock",
        "code": args.code,
        "pages_requested": args.pages,
        "items_observed": saved_items,
        "oldest_item_on_last_page": last_published_at,
        "output": str(args.output.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

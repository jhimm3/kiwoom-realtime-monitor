from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_for_futures
from contextlib import nullcontext
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.preprocess_historical_news_locally import ConcurrentArticlePreparation
from scripts.historical_news_timing_log import NewsTimingLog

from kiwoom_monitor.infrastructure.historical_backfill import (
    ArticleFetchAttempt,
    ArticlePublicationResult,
    NaverHistoricalNewsItem,
    NaverHistoricalNewsPage,
    NAVER_HISTORICAL_SEARCH_PROVIDER,
    article_publication_records,
    claim_news_backfill_job,
    claim_news_article_pipeline,
    defer_news_article_host_403,
    claim_news_range_job,
    clear_news_backfill_jobs,
    exclude_non_stock_news_jobs,
    fetch_article_publication,
    fetch_naver_historical_search_page,
    fetch_naver_stock_news_page,
    finish_news_backfill_job,
    finish_news_article_pipeline,
    finish_news_article_pipeline_batch,
    finalize_news_article_jobs,
    finish_news_range_job,
    inspect_candidate_database,
    inspect_daishin_environment,
    import_daishin_backfill_ndjson,
    latest_candidates,
    release_news_backfill_job,
    release_news_range_job,
    news_job_key,
    seed_news_range_jobs,
    seed_news_backfill_jobs,
    store_daishin_probe_payload,
    store_article_publication_result,
    store_article_publication_results,
    store_article_fetch_attempts_only,
    store_news_search_observation,
    store_naver_historical_search_page,
    store_naver_stock_news_page,
    split_news_range_job,
    unfinished_news_article_pipeline_count,
)


DEFAULT_CANDIDATE_DB = Path(
    r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"
)
DEFAULT_OUTPUT_DB = Path("data/historical_intelligence.sqlite3")


_NewsTimingLog = NewsTimingLog


def _error_detail(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:400]


def _diagnostic_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    return {"url_path": f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:500],
            "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest()}


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


def _fetch_primary_article(item: NaverHistoricalNewsItem) -> ArticlePublicationResult:
    # Historical collection prioritizes throughput. Keep the archive URL in the
    # article ledger for a separate optional pass instead of falling back here.
    roles = ("publisher_original",) if item.original_url else ("naver_archive",)
    return fetch_article_publication(item, timeout=2, source_roles=roles)


class _ArticleFetchPool:
    """Bound a stalled host and stop trying articles without another source."""

    def __init__(
        self, *, workers: int, article_delay: float,
        fetcher: object = fetch_article_publication,
        stall_after_seconds: float = 8.0,
        timing_observer: Callable[..., None] | None = None,
        primary_only: bool = False,
    ) -> None:
        self._delay = max(0.0, article_delay)
        self._workers = workers
        self._stall_after_seconds = max(0.0, stall_after_seconds)
        self._fetcher = fetcher
        self._timing_observer = timing_observer
        self._primary_only = primary_only
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="news-article",
        )
        self._futures: dict[object, tuple[object, str]] = {}
        self._started_at: dict[object, float] = {}
        self._waiting: dict[str, deque[object]] = {}
        self._inflight_by_host: dict[str, int] = {}
        self._expanded_hosts: set[str] = set()
        self._timed_out_by_host: dict[str, int] = {}
        self._forbidden_by_host: dict[str, int] = {}
        self._blocked_403_hosts: set[str] = set()
        self._unreachable_hosts: set[str] = set()
        self._skipped: deque[tuple[object, ArticlePublicationResult]] = deque()
        self._skipped_articles = 0

    def _can_skip(self, item: object) -> bool:
        return (isinstance(item, NaverHistoricalNewsItem) and bool(item.original_url)
                and (self._primary_only or not item.portal_url))

    def _skip_unreachable(self, item: NaverHistoricalNewsItem, host: str) -> None:
        now = datetime.now(UTC).isoformat()
        source_url = item.original_url
        self._skipped.append((item, ArticlePublicationResult(
            NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
            "deferred_403" if host in self._blocked_403_hosts else "fetch_error",
            "", "", "", "", source_url, source_url, now,
            (ArticleFetchAttempt(
                "publisher_original_skipped", source_url, source_url,
                "deferred_403" if host in self._blocked_403_hosts else "fetch_error",
                None,
                (f"not requested: {host} returned three consecutive HTTP 403"
                 if host in self._blocked_403_hosts else
                 f"not requested: {host} timed out on three consecutive articles"),
                "", "", "", "",
            ),),
        )))
        self._skipped_articles += 1
        if self._timing_observer is not None:
            self._timing_observer(item, self._skipped[-1][1], host, 0)

    def _open_unreachable_host(self, host: str) -> None:
        self._unreachable_hosts.add(host)
        queued = self._waiting.pop(host, deque())
        still_fetchable: deque[object] = deque()
        for item in queued:
            if self._can_skip(item):
                self._skip_unreachable(item, host)  # type: ignore[arg-type]
            else:
                still_fetchable.append(item)
        if still_fetchable:
            self._waiting[host] = still_fetchable

    def _record_host_result(self, host: str, result: object) -> None:
        if host in self._unreachable_hosts or not isinstance(result, ArticlePublicationResult):
            return
        forbidden = any(
            attempt.url_role == "publisher_original"
            and (urlparse(attempt.requested_url).hostname or "").lower() == host
            and attempt.http_status == 403
            for attempt in result.attempts
        )
        if forbidden:
            count = self._forbidden_by_host.get(host, 0) + 1
            self._forbidden_by_host[host] = count
            if count >= 3:
                self._blocked_403_hosts.add(host)
                self._open_unreachable_host(host)
            return
        self._forbidden_by_host.pop(host, None)
        timed_out = any(
            attempt.url_role == "publisher_original"
            and (urlparse(attempt.requested_url).hostname or "").lower() == host
            and ("timed out" in attempt.error.lower() or "deadline exceeded" in attempt.error.lower())
            for attempt in result.attempts
        )
        if not timed_out:
            self._timed_out_by_host.pop(host, None)
            return
        count = self._timed_out_by_host.get(host, 0) + 1
        self._timed_out_by_host[host] = count
        if count >= 3:
            self._open_unreachable_host(host)

    def _fetch_one(self, item: object):
        try:
            return self._fetcher(item)  # type: ignore[operator]
        finally:
            if self._delay > 0:
                time.sleep(self._delay)

    def _start(self, item: object, host: str) -> None:
        future = self._pool.submit(self._fetch_one, item)
        self._futures[future] = (item, host)
        self._started_at[future] = time.monotonic()
        self._inflight_by_host[host] = self._inflight_by_host.get(host, 0) + 1

    def _fill_host(self, host: str) -> None:
        queued = self._waiting.get(host)
        limit = min(self._workers, 2 if host in self._expanded_hosts else 1)
        while queued and self._inflight_by_host.get(host, 0) < limit:
            self._start(queued.popleft(), host)
        if queued is not None and not queued:
            self._waiting.pop(host, None)

    def _expand_stalled_hosts(self) -> None:
        now = time.monotonic()
        for future, (_item, host) in list(self._futures.items()):
            if (host not in self._expanded_hosts and self._waiting.get(host)
                    and now - self._started_at[future] >= self._stall_after_seconds):
                self._expanded_hosts.add(host)
                self._fill_host(host)

    def submit(self, item: object) -> None:
        host = _article_request_host(item)
        if host in self._unreachable_hosts and self._can_skip(item):
            self._skip_unreachable(item, host)  # type: ignore[arg-type]
            return
        self._waiting.setdefault(host, deque()).append(item)
        self._fill_host(host)
        self._expand_stalled_hosts()

    def _finish(self, future: object):
        item, host = self._futures.pop(future)
        elapsed_ms = round((time.monotonic() - self._started_at.pop(future)) * 1000)
        self._inflight_by_host[host] -= 1
        if not self._inflight_by_host[host]:
            self._inflight_by_host.pop(host)
        try:
            result = future.result()  # type: ignore[union-attr]
        except Exception as error:
            if self._timing_observer is not None:
                self._timing_observer(item, error, host, elapsed_ms)
            raise
        if self._timing_observer is not None:
            self._timing_observer(item, result, host, elapsed_ms)
        self._record_host_result(host, result)
        self._fill_host(host)
        if host not in self._inflight_by_host:
            self._expanded_hosts.discard(host)
        return item, result

    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        oldest = max(
            self._started_at,
            key=self._started_at.__getitem__,
            default=None,
        )
        oldest_host = self._futures.get(oldest, (None, ""))[1] if oldest else ""
        return {
            "active": len(self._futures),
            "queued": sum(len(items) for items in self._waiting.values()),
            "oldest_seconds": round(now - self._started_at[oldest], 1) if oldest else 0,
            "oldest_host": oldest_host,
            "expanded_hosts": sorted(self._expanded_hosts),
            "unreachable_hosts": sorted(self._unreachable_hosts),
            "blocked_403_hosts": sorted(self._blocked_403_hosts),
            "skipped_articles": self._skipped_articles,
        }

    def drain(
        self, *, wait: bool = False, on_wait: object | None = None,
        wait_interval_seconds: float = 15.0,
    ):
        next_wait_report = time.monotonic() + max(0.01, wait_interval_seconds)
        while self._futures or self._skipped:
            while self._skipped:
                yield self._skipped.popleft()
            if not self._futures:
                break
            self._expand_stalled_hosts()
            ready = [future for future in self._futures if future.done()]  # type: ignore[union-attr]
            if not ready:
                if not wait:
                    return
                ready = list(
                    completed
                    for completed in wait_for_futures(
                        list(self._futures), timeout=min(1.0, max(0.01, wait_interval_seconds)),
                        return_when=FIRST_COMPLETED,
                    ).done
                )
                if not ready:
                    if on_wait is not None and time.monotonic() >= next_wait_report:
                        on_wait(self.snapshot())  # type: ignore[operator]
                        next_wait_report = time.monotonic() + max(0.01, wait_interval_seconds)
                    continue
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
        timing_observer: Callable[..., None] | None = None,
    ) -> None:
        self._limiter = _RequestStartLimiter(request_delay)
        self._fetcher = fetcher
        self._throttle_delay = max(0.0, throttle_delay)
        self._throttle_retries = max(0, throttle_retries)
        self._throttle_observer = throttle_observer
        self._timing_observer = timing_observer
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
                wait_started = time.monotonic()
                self._limiter.wait()
                limiter_wait_ms = round((time.monotonic() - wait_started) * 1000)
                fetch_started = time.monotonic()
                try:
                    page = self._fetcher(  # type: ignore[operator]
                        getattr(job, "code"), getattr(job, "query_text"),
                        getattr(job, "target_date"), 1 + page_index * 10,
                        target_end_date=target_end_date,
                    )
                    if self._timing_observer is not None:
                        self._timing_observer(page_index + 1, "ok", limiter_wait_ms,
                                              round((time.monotonic() - fetch_started) * 1000),
                                              len(page.items), None)
                    return page_index, page
                except HTTPError as error:
                    if self._timing_observer is not None:
                        self._timing_observer(page_index + 1, "http_error", limiter_wait_ms,
                                              round((time.monotonic() - fetch_started) * 1000),
                                              0, error)
                    if error.code not in {403, 429} or throttles >= self._throttle_retries:
                        raise
                    throttles += 1
                    self._limiter.defer(self._throttle_delay)
                    if self._throttle_observer is not None:
                        with self._observer_lock:
                            self._throttle_observer(  # type: ignore[operator]
                                page_index + 1, error.code, self._throttle_delay,
                            )
                except Exception as error:
                    if self._timing_observer is not None:
                        self._timing_observer(page_index + 1, "error", limiter_wait_ms,
                                              round((time.monotonic() - fetch_started) * 1000),
                                              0, error)
                    raise

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
    """Yield completed fetches; a stalled host may use one extra request."""
    with _ArticleFetchPool(
        workers=workers, article_delay=article_delay, fetcher=fetcher,
    ) as pool:
        for item in items:
            pool.submit(item)
        yield from pool.drain(wait=True)


def _write_news_heartbeat(
    path: Path | None, job: object, phase: str, *, page: int = 0,
    pages_observed: int = 0, items_observed: int = 0, article: int = 0,
    last_article_progress_at: str = "",
    fetch: dict[str, object] | None = None,
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
    if last_article_progress_at:
        document["last_article_progress_at"] = last_article_progress_at
    if fetch is not None:
        document["article_fetch"] = fetch
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


def _run_deferred_articles(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100")
    if not 1 <= args.article_workers <= 16 or not 1 <= args.prepare_workers <= 8:
        raise ValueError("invalid article or preparation worker count")
    timing_log = _NewsTimingLog(args.diagnostic_log)
    claimed = claim_news_article_pipeline(args.output, limit=args.limit)
    if not claimed:
        print(json.dumps({"claimed_articles": 0, "finalized_search_jobs": 0,
                          "unfinished_articles": unfinished_news_article_pipeline_count(args.output)}),
              flush=True)
        return 0
    by_article = {}
    for entry in claimed:
        key = (entry.item.office_id, entry.item.article_id)
        by_article.setdefault(key, []).append(entry)
    completed_keys: set[str] = set()
    try:
        with _ArticleFetchPool(
            workers=args.article_workers, article_delay=args.article_delay,
            fetcher=_fetch_primary_article, primary_only=True,
            timing_observer=lambda item, result, host, elapsed_ms:
                timing_log.write(
                    "deferred_article_request", office_id=item.office_id,
                    article_id=item.article_id, host=host, elapsed_ms=elapsed_ms,
                    status=getattr(result, "status", "error"),
                    error=_error_detail(result) if isinstance(result, Exception) else None,
                ),
        ) as pool, ConcurrentArticlePreparation(
            output=args.prepared_output, search_database=args.output,
            market_database=Path("data/naver_stock_market_news.sqlite3"),
            matcher=(), workers=args.prepare_workers, allow_network=False,
            error_observer=lambda scope, code, identity, detail:
                timing_log.write("preparation_error", scope=scope, code=code,
                                 identity=identity[:400], error=detail[:400]),
            timing_observer=lambda event, fields: timing_log.write(event, **fields),
        ) as preparation:
            for entries in by_article.values():
                if entries[0].article_fetch_status in {"", "not_fetched"}:
                    pool.submit(entries[0].item)
                else:
                    for entry in entries:
                        _finish_deferred_article_entry(
                            args.output, entry, entries[0].published_at,
                            entries[0].article_fetch_status, preparation,
                        )
            fetched = list(pool.drain(wait=True))
            blocked_hosts = set(pool.snapshot()["blocked_403_hosts"])
            deferred = [
                (item, result) for item, result in fetched
                if _article_request_host(item) in blocked_hosts
                and (result.status == "deferred_403" or any(
                    attempt.url_role == "publisher_original"
                    and attempt.http_status == 403 for attempt in result.attempts
                ))
            ]
            deferred_ids = {
                (item.office_id, item.article_id) for item, _result in deferred
            }
            resolved = [
                (item, result) for item, result in fetched
                if (item.office_id, item.article_id) not in deferred_ids
            ]
            store_article_fetch_attempts_only(
                args.output, [result for _item, result in deferred],
            )
            store_article_publication_results(
                args.output, [result for _item, result in resolved],
            )
            for item, result in resolved:
                for entry in by_article[(item.office_id, item.article_id)]:
                    _finish_deferred_article_entry(
                        args.output, entry, result.published_at,
                        result.status, preparation,
                    )
            preparation.drain(all_pending=True)
        deferred_entries = [
            entry for entry in claimed
            if (entry.item.office_id, entry.item.article_id) in deferred_ids
        ]
        completed_entries = [
            entry for entry in claimed
            if (entry.item.office_id, entry.item.article_id) not in deferred_ids
        ]
        for host in sorted(blocked_hosts):
            host_entries = [
                entry for entry in deferred_entries
                if _article_request_host(entry.item) == host
            ]
            if host_entries:
                retry_after = defer_news_article_host_403(
                    args.output, host_entries, host=host,
                )
                timing_log.write(
                    "publisher_http_403_cooldown", host=host,
                    articles=len(host_entries), retry_after=retry_after,
                )
        finish_news_article_pipeline_batch(args.output, completed_entries)
        completed_keys.update(entry.job_key for entry in completed_entries)
    except Exception as error:
        if not completed_keys:
            try:
                finish_news_article_pipeline_batch(
                    args.output, claimed,
                    error=f"{type(error).__name__}: {error}"[:400],
                )
            except RuntimeError:
                pass
        raise
    finalized = finalize_news_article_jobs(args.output, completed_keys)
    print(json.dumps({"claimed_articles": len(claimed),
                      "deferred_http_403": len(deferred_entries),
                      "fetched_articles": sum(
                          entries[0].article_fetch_status in {"", "not_fetched"}
                          for entries in by_article.values()),
                      "finalized_search_jobs": finalized}, ensure_ascii=False), flush=True)
    return 0


def _finish_deferred_article_entry(
    database: Path, entry: object, published_at: str, status: str,
    preparation: ConcurrentArticlePreparation,
) -> None:
    page = NaverHistoricalNewsPage(
        entry.code, entry.query_text, entry.target_date, entry.start, (), True,
        datetime.now(UTC).isoformat(), "", entry.target_end_date,
    )
    _store_resolved_observation(database, page, entry.item, published_at)
    if status == "published_at_found":
        preparation.submit(
            "historical_backfill", entry.code,
            entry.item.original_url or entry.item.portal_url or
            f"naver:{entry.item.office_id}:{entry.item.article_id}",
        )


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
    news_run.add_argument("--diagnostic-log", type=Path,
                          help="요청·대기·저장 단계의 누적 JSONL 로그")
    news_run.add_argument("--prepared-output", type=Path,
                          default=Path("data/historical_collection/prepared_news.sqlite3"))
    news_run.add_argument("--prepare-workers", type=int, default=4)
    news_run.add_argument("--search-only", action="store_true",
                          help="검색 목록만 저장하고 원문/BODY/RULE은 별도 article-run에 맡깁니다.")
    news_run.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    article_run = subparsers.add_parser(
        "article-run", help="저장된 검색 목록에서 원문과 PC BODY/RULE을 이어 처리합니다."
    )
    article_run.add_argument("--limit", type=int, default=50)
    article_run.add_argument("--article-workers", type=int, default=16)
    article_run.add_argument("--article-delay", type=float, default=0.2)
    article_run.add_argument("--prepare-workers", type=int, default=4)
    article_run.add_argument("--diagnostic-log", type=Path)
    article_run.add_argument("--prepared-output", type=Path,
                             default=Path("data/historical_collection/prepared_news.sqlite3"))
    article_run.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    article_finalize = subparsers.add_parser(
        "article-finalize", help="중단 이후 완료된 원문 대기열의 검색 작업을 정리합니다."
    )
    article_finalize.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

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
    if args.command == "article-finalize":
        print(json.dumps({"finalized_search_jobs": finalize_news_article_jobs(args.output)}))
        return 0
    if args.command == "article-run":
        return _run_deferred_articles(args)
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
        diagnostic_path = args.diagnostic_log
        if diagnostic_path is None and args.heartbeat_file is not None:
            diagnostic_path = (args.heartbeat_file.parent / "logs" /
                               f"news-timing-{datetime.now(UTC):%Y%m%d}.jsonl")
        timing_log = _NewsTimingLog(diagnostic_path)
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
            job_started = time.monotonic()
            job_fields = {"code": job.code, "target_date": job.target_date,
                          "target_end_date": job.target_end_date or job.target_date}
            timing_log.write("job_start", **job_fields, search_workers=args.search_workers,
                             article_workers=args.article_workers, prepare_workers=args.prepare_workers)
            _write_news_heartbeat(args.heartbeat_file, job, "claimed")
            current_stage = "claimed"
            try:
                exhausted = False
                statuses = {}
                occurrences = []
                scheduled = set()
                item_pages = {}
                completed_articles = 0
                last_article_progress_at = datetime.now(UTC).isoformat()

                def record_search_throttle(
                    page: int, status: int, delay: float,
                ) -> None:
                    timing_log.write("search_cooldown", **job_fields, page=page,
                                     http_status=status, cooldown_ms=round(delay * 1000))
                    _write_news_heartbeat(
                        args.heartbeat_file, job, f"search_throttled_{status}",
                        page=page, pages_observed=pages_observed,
                        items_observed=items_observed,
                        article=completed_articles,
                    )

                def record_search_timing(page: int, status: str, wait_ms: int,
                                         fetch_ms: int, items: int, error: BaseException | None) -> None:
                    timing_log.write("search_request", **job_fields, page=page,
                                     host="s.search.naver.com", status=status,
                                     limiter_wait_ms=wait_ms, fetch_ms=fetch_ms,
                                     items=items, http_status=getattr(error, "code", None),
                                     error=_error_detail(error) if error else None)

                def record_article_timing(item: object, result: object,
                                          host: str, elapsed_ms: int) -> None:
                    identity = {"office_id": str(getattr(item, "office_id", "")),
                                "article_id": str(getattr(item, "article_id", ""))}
                    if isinstance(result, Exception):
                        timing_log.write("article_error", **job_fields, **identity,
                                         host=host, elapsed_ms=elapsed_ms,
                                         error=_error_detail(result))
                        return
                    if not isinstance(result, ArticlePublicationResult):
                        return
                    if (result.status != "published_at_found" and
                            getattr(item, "original_url", "") and
                            getattr(item, "portal_url", "") and
                            item.original_url != item.portal_url):
                        timing_log.write("archive_deferred", **job_fields, **identity,
                                         original_status=result.status,
                                         portal_host=(urlparse(item.portal_url).hostname or "unknown").lower())
                    for attempt in result.attempts:
                        timing_log.write("article_request", **job_fields, **identity,
                                         host=(urlparse(attempt.requested_url).hostname or host).lower(),
                                         **_diagnostic_url(attempt.requested_url),
                                         url_role=attempt.url_role, status=attempt.status,
                                         http_status=attempt.http_status,
                                         elapsed_ms=attempt.elapsed_ms,
                                         open_ms=attempt.open_ms, read_ms=attempt.read_ms,
                                         parse_ms=attempt.parse_ms,
                                         failure_phase=attempt.failure_phase,
                                         error=attempt.error[:400] or None)
                    if not result.attempts:
                        timing_log.write("article_result", **job_fields, **identity,
                                         host=host, status=result.status, elapsed_ms=elapsed_ms)

                article_context = (
                    nullcontext(None) if args.search_only else _ArticleFetchPool(
                        workers=args.article_workers, article_delay=args.article_delay,
                        timing_observer=record_article_timing,
                        fetcher=_fetch_primary_article, primary_only=True,
                    )
                )
                preparation_context = (
                    nullcontext(None) if args.search_only else ConcurrentArticlePreparation(
                        output=args.prepared_output, search_database=args.output,
                        market_database=Path("data/naver_stock_market_news.sqlite3"),
                        matcher=(), workers=args.prepare_workers, allow_network=False,
                        error_observer=lambda scope, code, identity, detail:
                            timing_log.write("preparation_error", **job_fields,
                                             scope=scope, identity=identity[:400],
                                             error=detail[:400]),
                        timing_observer=lambda event, fields:
                            timing_log.write(event, **job_fields, **fields),
                    )
                )
                with _SearchPagePool(
                    workers=args.search_workers, request_delay=args.request_delay,
                    throttle_observer=record_search_throttle,
                    timing_observer=record_search_timing,
                ) as search_pool, article_context as article_pool, preparation_context as preparation:
                    pending_results: list[tuple[object, object]] = []

                    def persist_article_results(drained: list[tuple[object, object]], page_number: int) -> None:
                        nonlocal completed_articles, last_article_progress_at, current_stage
                        if not drained:
                            return
                        current_stage = "article_store"
                        store_started = time.monotonic()
                        store_article_publication_results(
                            args.output,
                            [result for _item, result in drained],
                        )
                        timing_log.write("article_store", **job_fields, page=page_number,
                                         count=len(drained), elapsed_ms=round((time.monotonic() - store_started) * 1000))
                        for item, result in drained:
                            key = (item.office_id, item.article_id)
                            current_stage = "resolved_observation"
                            _store_resolved_observation(
                                args.output, item_pages[key], item, result.published_at,
                            )
                            statuses[key] = result.status
                            if result.status == "published_at_found":
                                current_stage = "preparation_submit"
                                prepare_started = time.monotonic()
                                preparation.submit(
                                    "historical_backfill", item_pages[key].code,
                                    item.original_url or item.portal_url or
                                    f"naver:{item.office_id}:{item.article_id}",
                                )
                                timing_log.write("preparation_submit", **job_fields,
                                                 office_id=item.office_id, article_id=item.article_id,
                                                 elapsed_ms=round((time.monotonic() - prepare_started) * 1000))
                            completed_articles += 1
                            last_article_progress_at = datetime.now(UTC).isoformat()
                            _write_news_heartbeat(
                                args.heartbeat_file, job, "article",
                                page=page_number, pages_observed=pages_observed,
                                items_observed=items_observed,
                                article=completed_articles,
                                last_article_progress_at=last_article_progress_at,
                                fetch=article_pool.snapshot(),
                            )

                    def report_article_wait(fetch: dict[str, object]) -> None:
                        if pending_results:
                            persist_article_results(pending_results, pages_observed)
                            pending_results.clear()
                        _write_news_heartbeat(
                            args.heartbeat_file, job, "article_wait",
                            page=pages_observed, pages_observed=pages_observed,
                            items_observed=items_observed,
                            article=completed_articles,
                            last_article_progress_at=last_article_progress_at,
                            fetch=fetch,
                        )

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
                        search_batch_started = time.monotonic()
                        current_stage = "search_fetch"
                        batch = search_pool.fetch_batch(job, indexes)
                        timing_log.write("search_batch", **job_fields,
                                         first_page=indexes[0] + 1, pages=len(batch),
                                         elapsed_ms=round((time.monotonic() - search_batch_started) * 1000))
                        for page_index, page in batch:
                            _write_news_heartbeat(
                                args.heartbeat_file, job, "search_page",
                                page=page_index + 1, pages_observed=pages_observed,
                                items_observed=items_observed,
                            )
                            page_store_started = time.monotonic()
                            current_stage = "search_page_store"
                            store_naver_historical_search_page(
                                args.output, page,
                                deferred_job_key=news_job_key(job) if args.search_only else "",
                            )
                            timing_log.write("search_page_store", **job_fields,
                                             page=page_index + 1, items=len(page.items),
                                             elapsed_ms=round((time.monotonic() - page_store_started) * 1000))
                            pages_observed += 1
                            items_observed += len(page.items)
                            page_records = {}
                            if not args.search_only:
                                lookup_started = time.monotonic()
                                current_stage = "article_status_lookup"
                                page_records = article_publication_records(
                                    args.output, list(page.items),
                                )
                                timing_log.write("article_status_lookup", **job_fields,
                                                 page=page_index + 1, items=len(page.items),
                                                 elapsed_ms=round((time.monotonic() - lookup_started) * 1000))
                            for item in page.items:
                                key = (item.office_id, item.article_id)
                                occurrences.append(key)
                                item_pages[key] = page
                                if key in statuses or key in scheduled:
                                    continue
                                if args.search_only:
                                    statuses[key] = "not_fetched"
                                    continue
                                status, published_at = page_records[key]
                                if status in {"", "not_fetched"}:
                                    scheduled.add(key)
                                    current_stage = "article_submit"
                                    article_pool.submit(item)
                                else:
                                    statuses[key] = status
                                    current_stage = "resolved_observation"
                                    _store_resolved_observation(
                                        args.output, page, item, published_at,
                                    )
                                    if status == "published_at_found":
                                        current_stage = "preparation_submit"
                                        prepare_started = time.monotonic()
                                        preparation.submit(
                                            "historical_backfill", page.code,
                                            item.original_url or item.portal_url or
                                            f"naver:{item.office_id}:{item.article_id}",
                                        )
                                        timing_log.write("preparation_submit", **job_fields,
                                                         office_id=item.office_id, article_id=item.article_id,
                                                         elapsed_ms=round((time.monotonic() - prepare_started) * 1000))
                            if not args.search_only:
                                drained = list(article_pool.drain())
                                persist_article_results(drained, page_index + 1)
                            if not page.has_structured_news or len(page.items) < 10:
                                exhausted = True
                                break
                        if exhausted:
                            break
                        batch_start += batch_width
                    if not args.search_only:
                        article_wait_started = time.monotonic()
                        current_stage = "article_final_wait"
                        for result in article_pool.drain(
                            wait=True, on_wait=report_article_wait,
                        ):
                            pending_results.append(result)
                            if len(pending_results) >= 25:
                                persist_article_results(pending_results, pages_observed)
                                pending_results = []
                        persist_article_results(pending_results, pages_observed)
                        timing_log.write("article_final_wait", **job_fields,
                                         elapsed_ms=round((time.monotonic() - article_wait_started) * 1000))
                        prepare_drain_started = time.monotonic()
                        current_stage = "preparation_final_drain"
                        preparation.drain(all_pending=True)
                        timing_log.write("preparation_final_drain", **job_fields,
                                         elapsed_ms=round((time.monotonic() - prepare_drain_started) * 1000),
                                         ready=preparation.ready, failed=preparation.failed,
                                         skipped=preparation.skipped)
                for key in occurrences:
                    status = statuses[key]
                    if status == "published_at_found":
                        usable += 1
                    elif status == "time_not_found":
                        missing_time += 1
                    elif args.search_only and status in {"", "not_fetched"}:
                        continue
                    else:
                        unreadable += 1
                state = ("search_complete" if args.search_only and occurrences else
                         "complete") if exhausted else "truncated"
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
                if args.search_only and state == "search_complete":
                    finalize_news_article_jobs(args.output, {news_job_key(job)})
                if state in {"complete", "search_complete"}:
                    completed_jobs += 1
                else:
                    truncated_jobs += 1
                _write_news_heartbeat(
                    args.heartbeat_file, job, state,
                    pages_observed=pages_observed, items_observed=items_observed,
                )
                timing_log.write("job_finish", **job_fields, state=state,
                                 pages=pages_observed, items=items_observed,
                                 elapsed_ms=round((time.monotonic() - job_started) * 1000))
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
                timing_log.write("job_error", **job_fields, page=pages_observed,
                                 items=items_observed, stage=current_stage,
                                 elapsed_ms=round((time.monotonic() - job_started) * 1000),
                                 error=_error_detail(error))
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

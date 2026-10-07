from __future__ import annotations

import sqlite3
import logging
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Callable
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from kiwoom_monitor.application.news_grouping import NewsEventGroup, group_similar_news
from kiwoom_monitor.infrastructure.article_text import fetch_article_text
from kiwoom_monitor.infrastructure.central_ai_client import CentralAIClient
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient,
    NaverNewsCredentials,
    NewsAISettings,
    NewsFilterSettings,
    LocalNewsSources,
    OfficialNewsSettings,
    StockNewsItem,
    is_excluded_news,
)
from kiwoom_monitor.infrastructure.news_ai import DEFAULT_MODELS, analysis_body_hash, analyze_articles
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import (
    NewsAIRepository,
    news_identity,
)
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.naver_stock_news import NaverStockNewsClient
from kiwoom_monitor.presentation.news_view_model import StoredNewsEvidence


NEWS_CHECK_INTERVAL_SECONDS = 60.0
LOGGER = logging.getLogger(__name__)


class NewsSearchWorker(QThread):
    completed = Signal(str, str, object, bool, object, int, object, int)
    failed = Signal(str, str, str)

    def __init__(self, stock_code: str, stock_name: str, credentials: NaverNewsCredentials,
                 official: OfficialNewsSettings, dart_cache_path: Path, naver_since: datetime,
                 database_path: Path,
                 central_client: CentralNewsClient | None = None,
                 central_ai_settings: NewsAISettings | None = None,
                 stored_news_limit: int = 200,
                 page_offset: int = 0,
                 parent: QWidget | None = None,
                 source_settings: LocalNewsSources | None = None) -> None:
        super().__init__(parent)
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._credentials = credentials
        self._official = official
        self._dart_cache_path = dart_cache_path
        self._naver_since = naver_since
        self._database_path = database_path
        self._central_client = central_client
        self._central_ai_settings = central_ai_settings
        self._stored_news_limit = stored_news_limit
        self._page_offset = page_offset
        self._source_settings = source_settings or LocalNewsSources()

    def run(self) -> None:
        started = perf_counter()
        items: list[StockNewsItem] = []
        errors: list[str] = []
        naver_succeeded = False
        next_offset: int | None = None
        fetch_ms = 0.0
        try:
            if self._central_client is not None:
                page, next_offset = self._central_client.stored_page(
                    self._stock_code, self._stock_name, offset=self._page_offset,
                    ai=self._central_ai_settings,
                )
                items.extend(page)
                naver_succeeded = True
                fetch_ms = (perf_counter() - started) * 1000
            elif (self._source_settings.stock_name_enabled
                  and self._credentials.client_id and self._credentials.client_secret):
                items.extend(NaverNewsClient(self._credentials).search(self._stock_name, since=self._naver_since))
                naver_succeeded = True
        except HTTPError as error:
            message = "API 인증 또는 호출 한도를 확인하세요." if error.code in {401, 403, 429} else f"네이버 뉴스 응답 오류 ({error.code})"
            errors.append(message)
        except (URLError, TimeoutError, OSError) as error:
            errors.append(f"네트워크 연결 실패: {error}")
        except (ValueError, KeyError, RuntimeError) as error:
            errors.append(str(error))
        if self._central_client is None and self._source_settings.stock_site_enabled:
            try:
                batch = NaverStockNewsClient(endpoint=self._source_settings.stock_site_url).search(
                    self._stock_code, self._stock_name, since=self._naver_since,
                )
                items.extend(batch.items)
                naver_succeeded = True
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, RuntimeError) as error:
                errors.append(f"네이버 증권 종목뉴스: {error}")
        try:
            if self._central_client is None and self._official.dart_enabled and self._official.dart_api_key:
                items.extend(DartDisclosureClient(self._official.dart_api_key, self._dart_cache_path).search(
                    self._stock_code, self._stock_name))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as error:
            errors.append(f"DART: {error}")
        if items or not errors:
            unique = {item.original_link or item.link or item.title: item for item in items}
            fetched = tuple(unique.values())
            try:
                repository = StockNewsRepository(
                    self._database_path,
                    stored_news_limit=None if self._central_client is not None else self._stored_news_limit,
                )
                known = {news_identity(item) for item in repository.load(
                    self._stock_code, limit=self._page_offset + 200,
                )}
                known_ms = (perf_counter() - started) * 1000 - fetch_ms
                new_identities = {news_identity(item) for item in fetched} - known
                checked_at = datetime.now(UTC)
                new_count = repository.upsert(
                    self._stock_code, fetched, checked_at,
                    naver_checked_at=checked_at if naver_succeeded else None,
                )
                total_ms = (perf_counter() - started) * 1000
                if self._central_client is not None and total_ms >= 1000:
                    LOGGER.warning(
                        "stored news client slow code=%s offset=%s items=%s "
                        "fetch_ms=%.0f known_ms=%.0f upsert_ms=%.0f total_ms=%.0f",
                        self._stock_code, self._page_offset, len(fetched), fetch_ms,
                        known_ms, total_ms - fetch_ms - known_ms, total_ms,
                    )
            except (OSError, ValueError, sqlite3.Error) as error:
                self.failed.emit(self._stock_code, self._stock_name, f"뉴스 저장 실패: {error}")
                return
            self.completed.emit(
                self._stock_code, self._stock_name, fetched, naver_succeeded,
                new_identities, new_count, next_offset, self._page_offset,
            )
        else:
            self.failed.emit(self._stock_code, self._stock_name, " / ".join(errors))


class NewsPrepareWorker(QThread):
    """DB 조회와 사건 묶음을 UI 스레드 밖에서 준비한다."""

    completed = Signal(int, str, object, object, object, bool, object)
    failed = Signal(int, str, str)

    def __init__(self, request_id: int, stock_code: str, stock_name: str, database_path: Path,
                 news_filter: NewsFilterSettings, show_low_relevance: bool,
                 loaded_limit: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request_id = request_id
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._database_path = database_path
        self._news_filter = news_filter
        self._show_low_relevance = show_low_relevance
        self._loaded_limit = loaded_limit

    def run(self) -> None:
        started = perf_counter()
        try:
            repository = StockNewsRepository(
                self._database_path, stored_news_limit=self._news_filter.stored_news_limit,
            )
            items = repository.load(self._stock_code, limit=self._loaded_limit)
            load_ms = (perf_counter() - started) * 1000
            filtered = tuple(
                item for item in items
                if not is_excluded_news(item, self._news_filter)
                and (item.assessment.relevant or self._show_low_relevance)
            )
            groups = group_similar_news(filtered)
            group_ms = (perf_counter() - started) * 1000 - load_ms
            representatives = tuple(group.representative for group in groups)
            ai_results = NewsAIRepository(self._database_path).load_many(
                self._stock_code, representatives, self._stock_name,
            )
            recently_checked = repository.recently_checked(
                self._stock_code, NEWS_CHECK_INTERVAL_SECONDS,
            )
            last_naver_check = repository.last_naver_checked_at(self._stock_code)
            total_ms = (perf_counter() - started) * 1000
            if total_ms >= 1000:
                LOGGER.warning(
                    "stored news prepare slow code=%s items=%s groups=%s "
                    "load_ms=%.0f group_ms=%.0f remaining_ms=%.0f total_ms=%.0f",
                    self._stock_code, len(items), len(groups), load_ms, group_ms,
                    total_ms - load_ms - group_ms, total_ms,
                )
            self.completed.emit(
                self._request_id, self._stock_code, items, groups, ai_results,
                recently_checked, last_naver_check,
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            self.failed.emit(self._request_id, self._stock_code, str(error))


class NewsEvidenceWorker(QThread):
    """선택 기사에 정확히 대응하는 NAS revision 근거만 조회한다."""

    completed = Signal(int, str, str, object)

    def __init__(self, request_id: int, stock_code: str, item: StockNewsItem,
                 central_client: CentralNewsClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request_id = request_id
        self._stock_code = stock_code
        self._item = item
        self._central_client = central_client

    def run(self) -> None:
        identity = news_identity(self._item)
        for attempt in range(2):
            if self.isInterruptionRequested():
                return
            try:
                evidence = load_stored_news_evidence(
                    self._central_client, self._stock_code, self._item,
                    cancelled=self.isInterruptionRequested,
                )
                self.completed.emit(self._request_id, self._stock_code, identity, evidence)
                return
            except RuntimeError as error:
                message = str(error)
                if attempt == 0 and "HTTP 404" not in message:
                    continue
                notice = (
                    "구버전 중앙 서버에는 저장 근거 조회 기능이 없습니다."
                    if "HTTP 404" in message else f"저장 자료를 불러오지 못했습니다: {message}"
                )
                self.completed.emit(
                    self._request_id, self._stock_code, identity,
                    StoredNewsEvidence(identity=identity, notice=notice),
                )
                return
            except _EvidenceCancelled:
                return
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.completed.emit(
                    self._request_id, self._stock_code, identity,
                    StoredNewsEvidence(
                        identity=identity,
                        notice=f"저장 자료를 불러오지 못했습니다: {error}",
                    ),
                )
                return


def load_stored_news_evidence(
    client: CentralNewsClient, stock_code: str, item: StockNewsItem,
    *, cancelled: Callable[[], bool] = lambda: False,
) -> StoredNewsEvidence:
    """목록 판본→기사→본문→규칙 revision을 엄격한 ID 일치로 결합한다."""
    identity = news_identity(item)
    def load(kind: str, *, target: str = "", identity_value: str = "",
             limit: int = 100) -> list[dict[str, object]]:
        if cancelled():
            raise _EvidenceCancelled
        result = _history_rows(client.load_news_history(
            kind, target=target, identity=identity_value, limit=limit,
        ))
        if cancelled():
            raise _EvidenceCancelled
        return result

    target_rows = load("article", target=stock_code, identity_value=identity, limit=20)
    target_matches = _matching_article_rows(target_rows, stock_code, identity, item)
    rows = target_rows
    matches = target_matches
    if not matches:
        global_rows = load("article", target="GLOBAL", identity_value=identity, limit=20)
        rows = global_rows
        matches = _matching_article_rows(global_rows, "GLOBAL", identity, item)
    if not matches:
        notice = (
            "NAS에는 같은 기사 정체성의 다른 제목·요약 판본만 있어 판정을 함께 표시하지 않습니다."
            if target_rows or rows else "저장 자료 없음"
        )
        return StoredNewsEvidence(identity=identity, title=item.title, notice=notice)

    article = matches[0]
    article_revision_id = str(article.get("article_revision_id") or "")
    if not article_revision_id:
        raise ValueError("기사 revision ID가 없습니다.")
    historical = bool(rows and str(rows[0].get("article_revision_id") or "") != article_revision_id)

    if cancelled():
        raise _EvidenceCancelled
    body_response = client.load_news_history(
        "body", target=article_revision_id, limit=20, stock_code=stock_code,
    )
    if cancelled():
        raise _EvidenceCancelled
    body_rows = _history_rows(body_response)
    assessment = body_response.get("assessment") if isinstance(body_response, dict) else None
    body = next(
        (row for row in body_rows if str(row.get("article_revision_id") or "") == article_revision_id),
        None,
    )
    if body is None and target_matches:
        global_rows = load("article", target="GLOBAL", identity_value=identity, limit=20)
        global_matches = _matching_article_rows(global_rows, "GLOBAL", identity, item)
        if global_matches:
            global_article = global_matches[0]
            global_revision_id = str(global_article.get("article_revision_id") or "")
            if cancelled():
                raise _EvidenceCancelled
            global_response = client.load_news_history(
                "body", target=global_revision_id, limit=20, stock_code=stock_code,
            )
            if cancelled():
                raise _EvidenceCancelled
            global_bodies = _history_rows(global_response)
            global_body = next(
                (row for row in global_bodies
                 if str(row.get("article_revision_id") or "") == global_revision_id
                 and str(row.get("status") or "") in {"fulltext", "summary_only"}),
                None,
            )
            if global_body is not None:
                article = global_article
                article_revision_id = global_revision_id
                body = global_body
                assessment = (
                    global_response.get("assessment")
                    if isinstance(global_response, dict) else None
                )
                historical = bool(
                    global_rows
                    and str(global_rows[0].get("article_revision_id") or "") != article_revision_id
                )
    if body is None:
        return StoredNewsEvidence(
            identity=identity, title=item.title, article_revision_id=article_revision_id,
            historical_revision=historical, notice="본문이 아직 처리 중입니다.",
        )

    body_revision_id = str(body.get("body_revision_id") or "")
    status = str(body.get("status") or "missing")
    core_sentences: tuple[str, ...] = ()
    if (
        status == "fulltext" and isinstance(assessment, dict)
        and str(assessment.get("article_revision_id") or "") == article_revision_id
        and str(assessment.get("body_revision_id") or "") == body_revision_id
        and isinstance(assessment.get("core_sentences"), list)
    ):
        core_sentences = tuple(
            sentence for value in assessment["core_sentences"]
            if (sentence := str(value).strip())
        )
    event = None
    if body_revision_id and status in {"fulltext", "summary_only"}:
        memberships = [
            row for row in load("membership", identity_value=article_revision_id, limit=50)
            if str(row.get("article_revision_id") or "") == article_revision_id
            and str(row.get("body_revision_id") or "") == body_revision_id
        ]
        if memberships:
            seen_event_ids: set[str] = set()
            for membership in memberships:
                event_id = str(membership.get("event_id") or "")
                if not event_id or event_id in seen_event_ids or len(seen_event_ids) >= 5:
                    continue
                seen_event_ids.add(event_id)
                events = load(
                    "event", target=stock_code, identity_value=event_id, limit=10,
                )
                event = next(
                    (row for row in events
                     if str(row.get("event_revision_id") or "")
                     == str(membership.get("event_revision_id") or "")
                     and str(row.get("stock_code") or "") == stock_code
                     and str(row.get("article_revision_id") or "") == article_revision_id
                     and str(row.get("body_revision_id") or "") == body_revision_id),
                    None,
                )
                if event is not None:
                    break
    from kiwoom_monitor.infrastructure.article_text import clean_article_text_with_details

    raw_body_text = str(body.get("body_text") or "")
    body_text, cut_marker, _cut_at = clean_article_text_with_details(raw_body_text)
    cleaning_notice = ""
    if status == "fulltext" and not body_text:
        body_text = ""
        cleaning_notice = "저장 본문이 포털 메뉴·짧은 속보 문구뿐이라 제거했습니다. 검색 요약을 사용합니다."
    elif cut_marker:
        cleaning_notice = f"저장 본문의 기사 종료 뒤 포털 문구를 제거했습니다. ({cut_marker})"
    return StoredNewsEvidence(
        identity=identity,
        title=item.title,
        article_revision_id=article_revision_id,
        body_revision_id=body_revision_id,
        body_status=status,
        body_text=body_text,
        core_sentences=core_sentences,
        body_error=str(body.get("error") or ""),
        event=event,
        historical_revision=historical,
        notice=cleaning_notice or (
            "동일 기사 정체성과 제목·요약이 일치하는 GLOBAL 저장 본문을 사용했습니다."
            if str(article.get("stock_code") or "") == "GLOBAL" else ""
        ),
    )


def _history_rows(document: object) -> list[dict[str, object]]:
    if not isinstance(document, dict):
        raise ValueError("뉴스 이력 응답 형식이 올바르지 않습니다.")
    values = document.get("revisions", [])
    if not isinstance(values, list):
        raise ValueError("뉴스 이력 revision 형식이 올바르지 않습니다.")
    return [value for value in values if isinstance(value, dict)]


class _EvidenceCancelled(Exception):
    pass


def _matching_article_rows(
    rows: list[dict[str, object]], expected_stock: str, identity: str, item: StockNewsItem,
) -> list[dict[str, object]]:
    matches = []
    for row in rows:
        document = row.get("document")
        if not isinstance(document, dict):
            continue
        if str(row.get("stock_code") or "") != expected_stock:
            continue
        if str(row.get("identity") or "") != identity:
            continue
        if str(document.get("title") or "") != item.title:
            continue
        if str(document.get("description") or "") != item.description:
            continue
        matches.append(row)
    return matches


class AINewsWorker(QThread):
    completed = Signal(object, object, str, str, object, int)
    failed = Signal(str, bool, int)

    def __init__(self, groups: tuple[NewsEventGroup, ...], stock_name: str, settings: NewsAISettings,
                 parent: QWidget | None = None, *, stock_code: str = "",
                 central_client: CentralAIClient | None = None) -> None:
        super().__init__(parent)
        self._groups, self._stock_name, self._settings = groups, stock_name, settings
        self._stock_code, self._central_client = stock_code, central_client
        self.central_cache_hit = False

    def run(self) -> None:
        api_attempted = False
        article_count = 0
        try:
            if self._central_client is not None:
                events = []
                for group in self._groups:
                    events.append({
                        "identity": news_identity(group.representative),
                        "title": group.representative.title,
                        "articles": [{
                            "title": item.title, "description": item.description,
                            "link": item.link, "original_link": item.original_link,
                        } for item in group.items],
                    })
                    article_count += len(group.items)
                api_attempted = True
                results, body_hashes, provider, model, usage, self.central_cache_hit = self._central_client.analyze(
                    self._stock_code, self._stock_name, self._settings.provider,
                    self._settings.model, events, article_count,
                )
                self.completed.emit(results, body_hashes, provider, model, usage, article_count)
                return
            event_inputs: list[tuple[str, str]] = []
            body_hashes: list[str] = []
            for group in self._groups:
                article_sections: list[str] = []
                last_error: Exception | None = None
                for index, item in enumerate(group.items, start=1):
                    body = ""
                    for url in dict.fromkeys((item.link, item.original_link)):
                        if not url:
                            continue
                        try:
                            body = fetch_article_text(url)
                            break
                        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                            last_error = error
                    if body:
                        article_sections.append(f"[관련 기사 {index}/{len(group.items)}: {item.title}]\n{body}")
                    elif item.description:
                        article_sections.append(f"[관련 기사 {index}/{len(group.items)}: {item.title} · 검색 요약]\n{item.description}")
                if not article_sections:
                    raise ValueError(str(last_error or "기사 본문을 가져오지 못했습니다."))
                combined_body = "\n\n".join(article_sections)
                event_inputs.append((group.representative.title, combined_body))
                body_hashes.append(analysis_body_hash(self._stock_name, combined_body))
                article_count += len(group.items)
            api_attempted = True
            model = self._settings.model.strip() or DEFAULT_MODELS[self._settings.provider]
            results, usage = analyze_articles(self._settings, self._stock_name, tuple(event_inputs))
            provider = self._settings.provider
            self.completed.emit(results, tuple(body_hashes), provider, model, usage, article_count)
        except Exception as error:  # worker boundary: show a recoverable message in the UI
            self.failed.emit(str(error), api_attempted, article_count)

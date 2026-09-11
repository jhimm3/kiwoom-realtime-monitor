from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
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


NEWS_CHECK_INTERVAL_SECONDS = 180.0


class NewsSearchWorker(QThread):
    completed = Signal(str, str, object, bool, object, int)
    failed = Signal(str, str, str)

    def __init__(self, stock_code: str, stock_name: str, credentials: NaverNewsCredentials,
                 official: OfficialNewsSettings, dart_cache_path: Path, naver_since: datetime,
                 database_path: Path,
                 central_client: CentralNewsClient | None = None,
                 central_ai_settings: NewsAISettings | None = None,
                 parent: QWidget | None = None) -> None:
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

    def run(self) -> None:
        items: list[StockNewsItem] = []
        errors: list[str] = []
        naver_succeeded = False
        try:
            if self._central_client is not None:
                items.extend(self._central_client.search(
                    self._stock_code, self._stock_name, since=self._naver_since,
                    ai=self._central_ai_settings,
                ))
                naver_succeeded = True
            elif self._credentials.client_id and self._credentials.client_secret:
                items.extend(NaverNewsClient(self._credentials).search(self._stock_name, since=self._naver_since))
                naver_succeeded = True
        except HTTPError as error:
            message = "API 인증 또는 호출 한도를 확인하세요." if error.code in {401, 403, 429} else f"네이버 뉴스 응답 오류 ({error.code})"
            errors.append(message)
        except (URLError, TimeoutError, OSError) as error:
            errors.append(f"네트워크 연결 실패: {error}")
        except (ValueError, KeyError, RuntimeError) as error:
            errors.append(str(error))
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
                repository = StockNewsRepository(self._database_path)
                known = {news_identity(item) for item in repository.load(self._stock_code)}
                new_identities = {news_identity(item) for item in fetched} - known
                checked_at = datetime.now(UTC)
                new_count = repository.upsert(
                    self._stock_code, fetched, checked_at,
                    naver_checked_at=checked_at if naver_succeeded else None,
                )
            except (OSError, ValueError, sqlite3.Error) as error:
                self.failed.emit(self._stock_code, self._stock_name, f"뉴스 저장 실패: {error}")
                return
            self.completed.emit(
                self._stock_code, self._stock_name, fetched, naver_succeeded,
                new_identities, new_count,
            )
        else:
            self.failed.emit(self._stock_code, self._stock_name, " / ".join(errors))


class NewsPrepareWorker(QThread):
    """DB 조회와 사건 묶음을 UI 스레드 밖에서 준비한다."""

    completed = Signal(int, str, object, object, object, bool, object)
    failed = Signal(int, str, str)

    def __init__(self, request_id: int, stock_code: str, stock_name: str, database_path: Path,
                 news_filter: NewsFilterSettings, show_low_relevance: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request_id = request_id
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._database_path = database_path
        self._news_filter = news_filter
        self._show_low_relevance = show_low_relevance

    def run(self) -> None:
        try:
            repository = StockNewsRepository(self._database_path)
            items = repository.load(self._stock_code)
            filtered = tuple(
                item for item in items
                if not is_excluded_news(item, self._news_filter)
                and (item.assessment.relevant or self._show_low_relevance)
            )
            groups = group_similar_news(filtered)
            representatives = tuple(group.representative for group in groups)
            ai_results = NewsAIRepository(self._database_path).load_many(
                self._stock_code, representatives, self._stock_name,
            )
            recently_checked = repository.recently_checked(
                self._stock_code, NEWS_CHECK_INTERVAL_SECONDS,
            )
            last_naver_check = repository.last_naver_checked_at(self._stock_code)
            self.completed.emit(
                self._request_id, self._stock_code, items, groups, ai_results,
                recently_checked, last_naver_check,
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            self.failed.emit(self._request_id, self._stock_code, str(error))


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

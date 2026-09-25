"""Common and market news in the dedicated news process."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QSettings, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig, NaverNewsClient, StockNewsItem, is_excluded_news, news_provider,
)
from kiwoom_monitor.infrastructure.naver_stock_market_news import PAGE_SIZE, fetch_page
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker


LABELS = {"common": "공통뉴스", "flash": "실시간 속보", "world": "해외뉴스"}


class MarketNewsLoadWorker(QThread):
    loaded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, source: str, config_path: Path, database_path: Path,
                 central: CentralNewsClient | None,
                 limit: int = 200,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._config_path = config_path
        self._database_path = database_path
        self._central = central
        self._limit = max(1, min(int(limit), 1000))

    def run(self) -> None:
        try:
            if self._central is not None:
                items = self._central.market_feed(self._source, limit=self._limit)
            else:
                items = self._load_local()
            if not self.isInterruptionRequested():
                self.loaded.emit(self._source, items)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(self._source, str(error))

    def _load_local(self) -> list[dict[str, object]]:
        config = LocalNaverNewsConfig(self._config_path)
        settings = config.load_sources()
        news_filter = config.load_filter()
        repository = StockNewsRepository(self._database_path, stored_news_limit=self._limit)
        key = f"MARKET:{self._source}"
        cutoff = datetime.now(UTC) - timedelta(days=2)
        previous = repository.last_naver_checked_at(key)
        since = max(previous.astimezone(UTC), cutoff) if previous else cutoff
        if not repository.recently_checked(key, 60):
            fetched: list[StockNewsItem] = []
            try:
                if self._source == "common":
                    client = NaverNewsClient(config.load())
                    for query in settings.common_queries[:50]:
                        if self.isInterruptionRequested():
                            return []
                        start = 1
                        while start <= self._limit and len(fetched) < self._limit:
                            if self.isInterruptionRequested():
                                return []
                            display = min(100, self._limit - start + 1)
                            page = client.search_page(query, start=start, display=display)
                            reached_cutoff = False
                            for article in page.items:
                                if (article.published_at is not None and
                                        article.published_at.astimezone(UTC) <= since):
                                    reached_cutoff = True
                                    break
                                fetched.append(article)
                            if reached_cutoff or len(page.items) < display:
                                break
                            start += display
                else:
                    endpoints = {"flash": settings.flash_url, "world": settings.world_url}
                    for day in (date.today(), date.today() - timedelta(days=1),
                                date.today() - timedelta(days=2)):
                        page_number = 1
                        while len(fetched) < self._limit and page_number <= 100:
                            if self.isInterruptionRequested():
                                return []
                            page = fetch_page(self._source, day.isoformat(), page_number,
                                              timeout=8, endpoints=endpoints)
                            for article in page.articles:
                                published = datetime.fromisoformat(article.published_at)
                                if published.astimezone(UTC) <= since:
                                    continue
                                fetched.append(StockNewsItem(
                                    article.title, article.summary, article.url, "",
                                    published,
                                    assess_stock_news("시황", article.title, article.summary),
                                ))
                            if len(page.articles) < PAGE_SIZE[self._source] or page.older_count:
                                break
                            page_number += 1
                repository.upsert(key, tuple(fetched), naver_checked_at=datetime.now(UTC))
            except Exception:
                if not repository.load(key, limit=self._limit):
                    raise
        return [{"title": item.title, "description": item.description,
                 "link": item.link, "original_link": item.original_link,
                 "published_at": item.published_at.isoformat() if item.published_at else "",
                 "query_membership": news_provider(item), "category": item.assessment.category}
                for item in repository.load(key, limit=self._limit)
                if not is_excluded_news(item, news_filter)]


class MarketNewsWindow(QDialog):
    def __init__(self, config_path: Path, database_path: Path) -> None:
        super().__init__(None)
        self.setWindowTitle("시장 뉴스")
        self.resize(950, 660)
        self._config_path = config_path
        self._database_path = database_path.with_name("market_news.sqlite3")
        self._central: CentralNewsClient | None = None
        self._source_settings = None
        self._items: dict[str, list[dict[str, object]]] = {}
        self._workers: dict[str, MarketNewsLoadWorker] = {}
        self._settings_worker: SettingsRequestWorker | None = None
        self._limit = 200
        self._tabs = QTabWidget()
        self._tables: dict[str, QTableWidget] = {}
        self._details: dict[str, QTextBrowser] = {}
        self._status = QLabel()
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh_current)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self._status, 1)
        bottom.addWidget(refresh)
        layout.addLayout(bottom)
        self._tabs.currentChanged.connect(self.refresh_current)
        self._window_settings = QSettings("KiwoomMonitor", "MarketNewsWindow")
        geometry = self._window_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def show_news(self) -> None:
        try:
            self._limit = LocalNaverNewsConfig(self._config_path).load_filter().stored_news_limit
            source = DataSourceConfig(self._config_path.with_name("data_source.json")).load()
            self._central = (CentralNewsClient(source.server_url, source.access_token)
                             if source.mode in {"local_server", "personal_server"} else None)
            self._source_settings = (None if self._central is not None
                                     else LocalNaverNewsConfig(self._config_path).load_sources())
            if self._central is not None:
                self.show()
                self._status.setText("NAS 뉴스 수집원 설정 조회 중…")
                if self._settings_worker is None:
                    worker = SettingsRequestWorker(CentralOperationalSettingsClient(source).load)
                    self._settings_worker = worker
                    worker.succeeded.connect(self._on_settings_loaded)
                    worker.failed.connect(self._on_settings_failed)
                    worker.finished.connect(lambda: setattr(self, "_settings_worker", None))
                    worker.start()
                return
            else:
                settings = self._source_settings
                available = tuple(source_name for source_name, enabled in (
                    ("common", settings.common_enabled),
                    ("flash", settings.market_enabled), ("world", settings.market_enabled),
                ) if enabled)
        except Exception as error:
            self._status.setText(f"뉴스 설정을 읽지 못했습니다: {error}")
            available = ()
        self._configure_tabs(available)

    def _on_settings_loaded(self, values: dict[str, object]) -> None:
        available = tuple(name for name, enabled in (
            ("common", values.get("news_query_set_enabled", False)),
            ("flash", values.get("news_naver_market_enabled", False)),
            ("world", values.get("news_naver_market_enabled", False)),
        ) if enabled)
        self._configure_tabs(available)

    def _on_settings_failed(self, message: str) -> None:
        self._status.setText(f"NAS 뉴스 수집원 설정 조회 실패: {message}")

    def _configure_tabs(self, available: tuple[str, ...]) -> None:
        self._tabs.blockSignals(True)
        self._tabs.clear()
        self._tables.clear()
        self._details.clear()
        for source_name in available:
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            table = QTableWidget(0, 3)
            table.setHorizontalHeaderLabels(("발행시각", "출처", "제목"))
            table.horizontalHeader().setStretchLastSection(True)
            detail = QTextBrowser()
            detail.setOpenExternalLinks(True)
            table.currentCellChanged.connect(
                lambda row, _column, _previous_row, _previous_column, key=source_name:
                self._show_detail(key, row)
            )
            table.cellDoubleClicked.connect(
                lambda row, _column, key=source_name: self._open_article(key, row)
            )
            panel_layout.addWidget(table, 2)
            panel_layout.addWidget(detail, 1)
            self._tables[source_name] = table
            self._details[source_name] = detail
            self._tabs.addTab(panel, LABELS[source_name])
        self._tabs.blockSignals(False)
        self._status.setText("수집된 뉴스를 조회합니다." if available else "활성화된 시장 뉴스 수집원이 없습니다.")
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh_current()

    def refresh_current(self, *_args: object) -> None:
        index = self._tabs.currentIndex()
        if index < 0:
            return
        source = next((key for key, table in self._tables.items()
                       if self._tabs.widget(index).findChild(QTableWidget) is table), "")
        if not source or source in self._workers:
            return
        self._status.setText(f"{LABELS[source]} 조회 중…")
        worker = MarketNewsLoadWorker(source, self._config_path, self._database_path,
                                      self._central,
                                      self._limit, self)
        self._workers[source] = worker
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda key=source: self._workers.pop(key, None))
        worker.start()

    def _on_loaded(self, source: str, values: object) -> None:
        items = [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
        self._items[source] = items
        table = self._tables.get(source)
        if table is None:
            return
        table.setRowCount(len(items))
        for row, item in enumerate(items):
            for column, value in enumerate((item.get("published_at"),
                                            item.get("query_membership"), item.get("title"))):
                table.setItem(row, column, QTableWidgetItem(str(value or "")))
        self._status.setText(f"{LABELS[source]} {len(items)}건" if items else
                             f"{LABELS[source]}에 표시할 기사가 없습니다.")
        if items:
            table.setCurrentCell(0, 0)

    def _on_failed(self, source: str, message: str) -> None:
        self._status.setText(f"{LABELS[source]} 조회 실패: {message}")

    def _show_detail(self, source: str, row: int) -> None:
        items = self._items.get(source, [])
        if row < 0 or row >= len(items):
            return
        item = items[row]
        core = item.get("core_sentences")
        summary = "\n".join(str(value) for value in core) if isinstance(core, list) and core else str(item.get("description") or "")
        link = str(item.get("original_link") or item.get("link") or "")
        detail = self._details[source]
        detail.setPlainText(f"{item.get('title') or ''}\n\n{summary}\n\n{link}")

    def _open_article(self, source: str, row: int) -> None:
        items = self._items.get(source, [])
        if 0 <= row < len(items):
            link = str(items[row].get("original_link") or items[row].get("link") or "")
            if urlsplit(link).scheme == "https":
                QDesktopServices.openUrl(QUrl(link))

    def shutdown(self) -> None:
        self._window_settings.setValue("geometry", self.saveGeometry())
        if self._settings_worker is not None:
            self._settings_worker.requestInterruption()
            self._settings_worker.wait(1000)
        for worker in tuple(self._workers.values()):
            worker.requestInterruption()
            worker.wait(10_000)
        self.close()

    def closeEvent(self, event) -> None:
        self._window_settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

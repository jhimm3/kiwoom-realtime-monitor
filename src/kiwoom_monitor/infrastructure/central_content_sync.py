from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_sync_utils import (
    central_document,
    normalize_batch_size,
    table_names,
    upload_documents,
)
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupError, ThemeBackupService


@dataclass(frozen=True)
class CentralContentSyncResult:
    news_articles: int = 0
    news_ai: int = 0
    news_ai_shared: int = 0
    news_request_usage: int = 0
    journal_news_links: int = 0
    theme_profiles: int = 0
    theme_stocks: int = 0
    theme_metadata: int = 0

    @property
    def total(self) -> int:
        return sum(self.__dict__.values())


class CentralContentSyncService:
    """기존 로컬 콘텐츠를 개인 중앙 서버에 비파괴 방식으로 복사한다.

    중앙 서버의 (collection, owner, key) upsert를 사용하므로 앱을 다시 켜도
    같은 자료가 늘어나지 않는다. 이 단계에서는 로컬 DB가 계속 원본이며,
    중앙 서버 장애가 로컬 뉴스·테마 사용을 막지 않는다.
    """

    def __init__(self, client: CentralContentClient, *, batch_size: int = 500) -> None:
        self._client = client
        self._batch_size = normalize_batch_size(batch_size)
        self._pull_cursors: dict[str, float] = {}
        self._cursor_path: Path | None = None

    def push(self, main_database_path: Path, news_database_path: Path) -> CentralContentSyncResult:
        news = self._read_news(news_database_path)
        themes = self._read_themes(main_database_path)
        counts: dict[str, int] = {}
        for collection, documents in {**news, **themes}.items():
            try:
                counts[collection] = self._upload(collection, documents)
            except RuntimeError as error:
                if collection != "journal_news_link" or "HTTP 404" not in str(error):
                    raise
                # 이전 NAS 이미지에는 아직 이 컬렉션이 없다. 로컬 연결은 보존하고
                # 서버 재빌드 뒤 다음 동기화에서 다시 전송한다.
                counts[collection] = 0
        return CentralContentSyncResult(
            news_articles=counts.get("news_article", 0),
            news_ai=counts.get("news_ai", 0),
            news_ai_shared=counts.get("news_ai_shared", 0),
            news_request_usage=counts.get("news_request_usage", 0),
            journal_news_links=counts.get("journal_news_link", 0),
            theme_profiles=counts.get("theme_profile", 0),
            theme_stocks=counts.get("theme_stock", 0),
            theme_metadata=counts.get("theme_metadata", 0),
        )

    def replace_themes(self, main_database_path: Path) -> CentralContentSyncResult:
        """중앙 테마를 현재 로컬 스냅샷과 같게 만든다.

        추가만 하는 upsert와 달리 삭제·이름 변경도 반영된다. 두 컬렉션은
        중앙 서버에서 각각 한 트랜잭션으로 교체된다.
        """
        themes = self._read_themes(main_database_path)
        profiles = self._client.replace("theme_profile", themes["theme_profile"])
        stocks = self._client.replace("theme_stock", themes["theme_stock"])
        metadata = self._client.replace("theme_metadata", themes["theme_metadata"])
        return CentralContentSyncResult(
            theme_profiles=profiles, theme_stocks=stocks, theme_metadata=metadata,
        )

    def load_theme_snapshot(self) -> tuple[dict[str, list[dict[str, Any]]], float | None]:
        """현재 중앙 테마 스냅샷과 완료 표식의 서버 저장 시각을 읽는다."""
        collections = {
            name: self._client.load_all(name, page_size=self._batch_size, updated_after=0.0)
            for name in ("theme_profile", "theme_stock", "theme_metadata")
        }
        completed_at: float | None = None
        for value in collections["theme_metadata"]:
            try:
                updated_at = float(value.get("updated_at"))
            except (TypeError, ValueError):
                continue
            completed_at = updated_at if completed_at is None else max(completed_at, updated_at)
        return collections, completed_at

    def apply_theme_snapshot(
        self, main_database_path: Path, collections: dict[str, list[dict[str, Any]]],
    ) -> CentralContentSyncResult:
        """이미 읽은 중앙 테마 스냅샷을 로컬 DB에 적용한다."""
        counts = self._write_themes(main_database_path, collections)
        return CentralContentSyncResult(
            theme_profiles=counts.get("theme_profile", 0),
            theme_stocks=counts.get("theme_stock", 0),
            theme_metadata=counts.get("theme_metadata", 0),
        )

    def pull(self, main_database_path: Path, news_database_path: Path) -> CentralContentSyncResult:
        """중앙 자료를 현재 PC의 캐시에 병합한다. 중앙 자료는 삭제하지 않는다."""
        self._load_pull_cursors(news_database_path)
        collections: dict[str, list[dict[str, Any]]] = {}
        next_cursors = dict(self._pull_cursors)
        for name in (
            "news_article", "news_ai", "news_ai_shared", "journal_news_link",
            "theme_profile", "theme_stock", "theme_metadata",
        ):
            try:
                documents = self._client.load_all(
                    name, page_size=self._batch_size, updated_after=self._pull_cursors.get(name, 0.0),
                )
            except RuntimeError as error:
                if name != "journal_news_link" or "HTTP 404" not in str(error):
                    raise
                documents = []
            collections[name] = documents
            timestamps = [float(value.get("updated_at", 0.0)) for value in documents]
            if timestamps:
                next_cursors[name] = max(timestamps)
        news_counts = self._write_news(news_database_path, collections)
        theme_counts = self._write_themes(main_database_path, collections)
        # 두 DB 반영이 모두 끝난 뒤에만 커서를 전진시킨다. 앱을 다시 켜도
        # 1만 건이 넘는 전체 자료 대신 이후 변경분만 받는다.
        self._pull_cursors = next_cursors
        self._save_pull_cursors()
        return CentralContentSyncResult(
            news_articles=news_counts.get("news_article", 0),
            news_ai=news_counts.get("news_ai", 0),
            news_ai_shared=news_counts.get("news_ai_shared", 0),
            journal_news_links=news_counts.get("journal_news_link", 0),
            theme_profiles=theme_counts.get("theme_profile", 0),
            theme_stocks=theme_counts.get("theme_stock", 0),
            theme_metadata=theme_counts.get("theme_metadata", 0),
        )

    def _load_pull_cursors(self, news_database_path: Path) -> None:
        if self._cursor_path is not None:
            return
        self._cursor_path = news_database_path.with_name("central_content_cursor.json")
        try:
            document = json.loads(self._cursor_path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                self._pull_cursors = {
                    str(name): max(0.0, float(value))
                    for name, value in document.items()
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._pull_cursors = {}

    def _save_pull_cursors(self) -> None:
        path = self._cursor_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".tmp-{id(self)}")
        temporary.write_text(
            json.dumps(self._pull_cursors, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        temporary.replace(path)

    def _upload(self, collection: str, documents: list[dict[str, Any]]) -> int:
        return upload_documents(self._client, collection, documents, self._batch_size)

    @staticmethod
    def _read_news(path: Path) -> dict[str, list[dict[str, Any]]]:
        result = {name: [] for name in (
            "news_article", "news_ai", "news_ai_shared", "news_request_usage", "journal_news_link",
        )}
        if not path.is_file():
            return result
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = _table_names(connection)
            if "stock_news" in tables:
                for row in connection.execute("SELECT * FROM stock_news"):
                    values = dict(row)
                    result["news_article"].append(_document(
                        str(values.get("stock_code", "")), str(values.get("identity", "")), values,
                    ))
            if "stock_news_ai" in tables:
                for row in connection.execute("SELECT * FROM stock_news_ai"):
                    values = dict(row)
                    result["news_ai"].append(_document(
                        str(values.get("stock_code", "")), str(values.get("identity", "")), values,
                    ))
            if "news_ai_shared" in tables:
                for row in connection.execute("SELECT * FROM news_ai_shared"):
                    values = dict(row)
                    result["news_ai_shared"].append(_document(
                        "shared", str(values.get("identity", "")), values,
                    ))
            if "news_ai_requests" in tables:
                for row in connection.execute("SELECT * FROM news_ai_requests"):
                    values = dict(row)
                    key = "|".join(str(values.get(name, "")) for name in (
                        "requested_at", "provider", "model", "id",
                    ))
                    result["news_request_usage"].append(_document("usage", key, values))
            if "journal_news_links" in tables:
                for row in connection.execute("SELECT * FROM journal_news_links"):
                    values = dict(row)
                    result["journal_news_link"].append(_document(
                        str(values.get("group_id", "")),
                        f'{values.get("stock_code", "")}|{values.get("identity", "")}', values,
                    ))
        finally:
            connection.close()
        return result

    @staticmethod
    def _read_themes(path: Path) -> dict[str, list[dict[str, Any]]]:
        result = {"theme_profile": [], "theme_stock": [], "theme_metadata": []}
        if not path.is_file():
            return result
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = _table_names(connection)
            if not {"theme_profiles", "profile_themes"}.issubset(tables):
                return result
            query = (
                "SELECT p.profile_name,t.theme_name,t.default_color "
                "FROM profile_themes t JOIN theme_profiles p ON p.profile_id=t.profile_id"
            )
            for row in connection.execute(query):
                values = dict(row)
                owner, key = str(values["profile_name"]), str(values["theme_name"])
                result["theme_profile"].append(_document(owner, key, values))
            if "profile_stock_themes" in tables:
                query = (
                    "SELECT p.profile_name,s.stock_code,s.theme_name,s.custom_color "
                    "FROM profile_stock_themes s JOIN theme_profiles p ON p.profile_id=s.profile_id"
                )
                for row in connection.execute(query):
                    values = dict(row)
                    owner = str(values["profile_name"])
                    key = f'{values["stock_code"]}|{values["theme_name"]}'
                    result["theme_stock"].append(_document(owner, key, values))
        finally:
            connection.close()
        if {"settings", "stocks", "stock_aliases", "theme_profiles", "profile_themes"}.issubset(tables):
            result["theme_metadata"].append(_document(
                "default", "full", ThemeBackupService(path).export_document(),
            ))
        return result

    @staticmethod
    def _write_news(path: Path, collections: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        counts = {"news_article": 0, "news_ai": 0, "news_ai_shared": 0, "journal_news_link": 0}
        if not path.is_file():
            return counts
        connection = sqlite3.connect(path)
        try:
            tables = _table_names(connection)
            mappings = {
                "news_article": ("stock_news", ("stock_code", "identity")),
                "news_ai": ("stock_news_ai", ("stock_code", "identity")),
                "news_ai_shared": ("news_ai_shared", ("identity",)),
                "journal_news_link": ("journal_news_links", ("group_id", "stock_code", "identity")),
            }
            with connection:
                for collection, (table, conflict) in mappings.items():
                    if table not in tables:
                        continue
                    for value in collections[collection]:
                        document = value.get("document")
                        if isinstance(document, dict) and _upsert_row(connection, table, conflict, document):
                            counts[collection] += 1
        finally:
            connection.close()
        return counts

    @staticmethod
    def _write_themes(path: Path, collections: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        counts = {"theme_profile": 0, "theme_stock": 0, "theme_metadata": 0}
        if not path.is_file():
            return counts
        connection = sqlite3.connect(path)
        try:
            if not {"theme_profiles", "profile_themes", "profile_stock_themes"}.issubset(_table_names(connection)):
                return counts
            with connection:
                for value in collections["theme_profile"]:
                    document = value.get("document")
                    if not isinstance(document, dict):
                        continue
                    profile = str(document.get("profile_name") or value.get("owner") or "").strip()
                    theme = str(document.get("theme_name") or value.get("key") or "").strip()
                    if not profile or not theme:
                        continue
                    connection.execute("INSERT OR IGNORE INTO theme_profiles(profile_name) VALUES(?)", (profile,))
                    profile_id = connection.execute(
                        "SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (profile,),
                    ).fetchone()[0]
                    connection.execute(
                        "INSERT INTO profile_themes(profile_id,theme_name,default_color) VALUES(?,?,?) "
                        "ON CONFLICT(profile_id,theme_name) DO UPDATE SET default_color=excluded.default_color",
                        (profile_id, theme, str(document.get("default_color") or "#DCE6F1")),
                    )
                    counts["theme_profile"] += 1
                for value in collections["theme_stock"]:
                    document = value.get("document")
                    if not isinstance(document, dict):
                        continue
                    profile = str(document.get("profile_name") or value.get("owner") or "").strip()
                    code, theme = str(document.get("stock_code") or ""), str(document.get("theme_name") or "").strip()
                    row = connection.execute(
                        "SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (profile,),
                    ).fetchone()
                    if row is None or not code or not theme:
                        continue
                    connection.execute(
                        "INSERT OR IGNORE INTO profile_themes(profile_id,theme_name) VALUES(?,?)", (row[0], theme),
                    )
                    connection.execute(
                        "INSERT INTO profile_stock_themes(profile_id,stock_code,theme_name,custom_color) VALUES(?,?,?,?) "
                        "ON CONFLICT(profile_id,stock_code,theme_name) DO UPDATE SET custom_color=excluded.custom_color",
                        (row[0], code, theme, document.get("custom_color")),
                    )
                    counts["theme_stock"] += 1
        finally:
            connection.close()
        metadata = collections.get("theme_metadata", [])
        if metadata:
            document = metadata[-1].get("document")
            if isinstance(document, dict):
                try:
                    ThemeBackupService(path).import_document(document)
                    counts["theme_metadata"] = 1
                except ThemeBackupError:
                    pass
        return counts


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return table_names(connection)


def _document(owner: str, key: str, values: dict[str, Any]) -> dict[str, Any]:
    return central_document(owner, key, values)


def _upsert_row(
    connection: sqlite3.Connection, table: str, conflict: tuple[str, ...], values: dict[str, Any],
) -> bool:
    allowed = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    columns = [column for column in values if column in allowed]
    if not columns or any(column not in columns for column in conflict):
        return False
    updates = [column for column in columns if column not in conflict]
    sql = f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
    if updates:
        sql += f" ON CONFLICT({','.join(conflict)}) DO UPDATE SET " + ",".join(
            f"{column}=excluded.{column}" for column in updates
        )
    else:
        sql += f" ON CONFLICT({','.join(conflict)}) DO NOTHING"
    connection.execute(sql, tuple(values[column] for column in columns))
    return True

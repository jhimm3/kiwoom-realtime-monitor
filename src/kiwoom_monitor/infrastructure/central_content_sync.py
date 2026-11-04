from __future__ import annotations

import json
import hashlib
import socket
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.domain.news_ai_contract import decode_news_ai_provenance, decode_news_input_source, select_news_copy_source
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import _retain_matching_body_projections

from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentClient,
    CentralContentHttpError,
    CentralThemeConflict,
    is_missing_collection_error,
)
from kiwoom_monitor.infrastructure.central_sync_utils import (
    central_document,
    normalize_batch_size,
    table_names,
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
    pending_collections: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum((
            self.news_articles, self.news_ai, self.news_ai_shared, self.news_request_usage,
            self.journal_news_links, self.theme_profiles, self.theme_stocks, self.theme_metadata,
        ))


class CentralContentSyncService:
    """기존 로컬 콘텐츠를 개인 중앙 서버에 비파괴 방식으로 복사한다.

    뉴스는 중앙 서버의 (collection, owner, key) upsert를 사용하므로 앱을 다시 켜도
    같은 자료가 늘어나지 않는다. 이 단계에서는 로컬 DB가 계속 원본이며,
    중앙 서버 장애가 로컬 뉴스·테마 사용을 막지 않는다.
    """

    def __init__(self, client: CentralContentClient, *, batch_size: int = 500) -> None:
        self._client = client
        self._batch_size = normalize_batch_size(batch_size)
        self._pull_cursors: dict[str, float] = {}
        self._cursor_path: Path | None = None
        self._push_manifest: dict[str, str] = {}
        self._manifest_path: Path | None = None
        self._pending_collections: tuple[str, ...] = ()

    @property
    def pending_collections(self) -> tuple[str, ...]:
        return self._pending_collections

    def push(
        self, main_database_path: Path, news_database_path: Path, *,
        sync_news_catalog: bool = True,
    ) -> CentralContentSyncResult:
        self._pending_collections = ()
        self._load_push_manifest(news_database_path)
        news = self._read_news(news_database_path, include_catalog=sync_news_catalog)
        v2_enabled = self._journal_news_links_v2_enabled()
        counts: dict[str, int] = {}
        pending: list[str] = []
        if news["journal_v2_news_links"] and not v2_enabled:
            pending.append("journal_v2_news_links")
        for collection, documents in news.items():
            if collection == "journal_v2_news_links" and not v2_enabled:
                continue
            try:
                counts[collection] = self._upload_changed(collection, documents)
            except RuntimeError as error:
                if collection not in {"journal_news_link", "journal_v2_news_links"} or not is_missing_collection_error(error):
                    raise
                # 이전 NAS 이미지에는 아직 이 컬렉션이 없다. 로컬 연결은 보존하고
                # 서버 재빌드 뒤 다음 동기화에서 다시 전송한다.
                counts[collection] = 0
                pending.append(collection)
        self._pending_collections = tuple(pending)
        return CentralContentSyncResult(
            news_articles=counts.get("news_article", 0),
            news_ai=counts.get("news_ai", 0),
            news_ai_shared=counts.get("news_ai_shared", 0),
            news_request_usage=counts.get("news_request_usage", 0),
            journal_news_links=(
                counts.get("journal_news_link", 0)
                + counts.get("journal_v2_news_links", 0)
            ),
            theme_profiles=counts.get("theme_profile", 0),
            theme_stocks=counts.get("theme_stock", 0),
            theme_metadata=counts.get("theme_metadata", 0),
            pending_collections=self._pending_collections,
        )

    def replace_themes(self, main_database_path: Path) -> CentralContentSyncResult:
        """Synchronize one accepted generation; dirty local material is pinned first.

        An unbound PC can publish into an empty native stream. Existing remote or
        legacy material requires an explicit adoption decision and stays untouched.
        """
        if not main_database_path.is_file():
            raise ThemeBackupError("테마 동기화 원본 DB가 없습니다.")
        owner = ThemeBackupService(main_database_path, now_provider=self._client.server_now)
        scope = self._client.theme_sync_scope
        state = owner.prepare_theme_publication(server_scope=scope, origin_device=socket.gethostname())
        prepared = state["prepared"]
        if prepared is not None:
            receipt = self._client.publish_theme_snapshot(
                prepared["document"], publication_id=prepared["publication_id"],
                expected_snapshot_id=prepared["expected_snapshot_id"],
                effective_at=prepared["effective_at"], origin_device=prepared["origin_device"],
            )
            settled = owner.acknowledge_theme_publication(
                receipt, publication_id=prepared["publication_id"], server_scope=scope,
            )
            snapshot = settled["ack"]["snapshot"]
            changed = receipt["changed"] or receipt["replayed"]
            pending = ("theme_generation",) if settled["pending"] else ()
        else:
            snapshot = self._client.load_accepted_theme_snapshot()
            if snapshot is None:
                raise CentralThemeConflict(409, "remote_stream_missing: 복구 정책 확인이 필요합니다.")
            previous_id = state["ack"]["snapshot"]["snapshot_id"]
            if snapshot["snapshot_id"] != previous_id:
                # Current history has no native-vs-legacy source-kind proof.
                # Do not turn a remote head or timestamp into an adoption policy.
                raise CentralThemeConflict(409, "remote_generation_requires_reconciliation")
            settled = owner.confirm_accepted_theme_snapshot(
                snapshot, server_scope=scope, expected_snapshot_id=previous_id,
                expected_revision=state["receive_revision"],
            )
            changed = snapshot["snapshot_id"] != previous_id
            pending = ()
        profiles = snapshot["document"]["profiles"] if changed else []
        return CentralContentSyncResult(
            theme_profiles=sum(len(profile.get("themes", [])) for profile in profiles),
            theme_stocks=sum(len(profile.get("stock_themes", [])) for profile in profiles),
            theme_metadata=int(changed), pending_collections=pending,
        )

    def load_theme_snapshot(self) -> tuple[dict[str, list[dict[str, Any]]], float | None]:
        """현재 중앙 테마 스냅샷과 완료 표식의 서버 저장 시각을 읽는다."""
        try:
            return self._client.load_current_theme_snapshot()
        except CentralContentHttpError as error:
            if error.status_code != 404:
                raise
        # Older deployed servers retain their actual collection read contract.
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
        *, expected_revision: str | None = None,
    ) -> CentralContentSyncResult:
        """이미 읽은 중앙 테마 스냅샷을 로컬 DB에 적용한다."""
        counts = self._write_themes(main_database_path, collections,
                                    expected_revision=expected_revision)
        return CentralContentSyncResult(
            theme_profiles=counts.get("theme_profile", 0),
            theme_stocks=counts.get("theme_stock", 0),
            theme_metadata=counts.get("theme_metadata", 0),
        )

    def pull(
        self, main_database_path: Path, news_database_path: Path, *,
        sync_news_catalog: bool = True,
    ) -> CentralContentSyncResult:
        """뉴스/연결 자료를 병합한다. 테마는 dispatcher의 generation 경로가 소유한다."""
        self._pending_collections = ()
        self._load_pull_cursors(news_database_path)
        v2_enabled = self._journal_news_links_v2_enabled()
        collections: dict[str, list[dict[str, Any]]] = {}
        next_cursors = dict(self._pull_cursors)
        pending: list[str] = []
        names = ["journal_news_link"]
        if sync_news_catalog:
            names[0:0] = ["news_article", "news_ai", "news_ai_shared"]
        if v2_enabled:
            names.append("journal_v2_news_links")
        else:
            pending.append("journal_v2_news_links")
        for name in names:
            try:
                documents = self._client.load_all(
                    name, page_size=self._batch_size, updated_after=self._pull_cursors.get(name, 0.0),
                )
            except RuntimeError as error:
                if name not in {"journal_news_link", "journal_v2_news_links"} or not is_missing_collection_error(error):
                    raise
                documents = []
                pending.append(name)
            collections[name] = documents
            timestamps = [float(value.get("updated_at", 0.0)) for value in documents]
            if timestamps:
                next_cursors[name] = max(timestamps)
        received_documents = [
            (collection, document)
            for collection, documents in collections.items()
            for document in documents
        ]
        manifest_changes: list[tuple[str, dict[str, Any], str, str]] = []
        if received_documents:
            self._load_push_manifest(news_database_path)
            for collection, document in received_documents:
                key = _manifest_key(collection, document)
                fingerprint = _manifest_hash(document, collection=collection)
                if self._push_manifest.get(key) != fingerprint:
                    manifest_changes.append((collection, document, key, fingerprint))
        changed_collections = {name: [] for name in collections}
        for collection, document, _key, _fingerprint in manifest_changes:
            changed_collections[collection].append(document)
        news_counts = self._write_news(news_database_path, changed_collections)
        # 두 DB 반영이 모두 끝난 뒤에만 커서를 전진시킨다. 앱을 다시 켜도
        # 1만 건이 넘는 전체 자료 대신 이후 변경분만 받는다.
        cursors_changed = next_cursors != self._pull_cursors
        self._pull_cursors = next_cursors
        if cursors_changed:
            self._save_pull_cursors()
        if manifest_changes:
            for _collection, _document, key, fingerprint in manifest_changes:
                self._push_manifest[key] = fingerprint
            self._save_push_manifest()
        self._pending_collections = tuple(pending)
        return CentralContentSyncResult(
            news_articles=news_counts.get("news_article", 0),
            news_ai=news_counts.get("news_ai", 0),
            news_ai_shared=news_counts.get("news_ai_shared", 0),
            journal_news_links=(
                news_counts.get("journal_news_link", 0)
                + news_counts.get("journal_v2_news_links", 0)
            ),
            theme_profiles=0,
            theme_stocks=0,
            theme_metadata=0,
            pending_collections=self._pending_collections,
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

    def _upload_changed(self, collection: str, documents: list[dict[str, Any]]) -> int:
        changed = [
            document for document in documents
            if self._push_manifest.get(_manifest_key(collection, document)) != _manifest_hash(document, collection=collection)
        ]
        saved = 0
        for offset in range(0, len(changed), self._batch_size):
            batch = changed[offset:offset + self._batch_size]
            saved += self._client.upsert(collection, batch)
            for document in batch:
                self._push_manifest[_manifest_key(collection, document)] = _manifest_hash(document, collection=collection)
            # 성공한 batch는 즉시 기록한다. 뒤 batch 실패 시 이미 끝난 자료를
            # 다시 전송하지 않고 실패한 변경만 다음 실행에서 재시도한다.
            self._save_push_manifest()
        return saved

    @staticmethod
    def push_manifest_exists(news_database_path: Path) -> bool:
        path = news_database_path.with_name("central_content_push_manifest.json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return isinstance(value, dict) and bool(value)

    def seed_push_manifest(
        self, main_database_path: Path, news_database_path: Path, *, allow_existing: bool = False,
        sync_news_catalog: bool = True,
    ) -> bool:
        """기존 seeded 설치를 한 번만 증분 manifest 기준선으로 이전한다."""
        path = news_database_path.with_name("central_content_push_manifest.json")
        if path.is_file() and not allow_existing:
            return False
        self._load_push_manifest(news_database_path)
        for collection, documents in {
            **self._read_news(news_database_path, include_catalog=sync_news_catalog),
        }.items():
            for document in documents:
                self._push_manifest[_manifest_key(collection, document)] = _manifest_hash(document, collection=collection)
        self._save_push_manifest()
        return True

    def _load_push_manifest(self, news_database_path: Path) -> None:
        if self._manifest_path is not None:
            return
        self._manifest_path = news_database_path.with_name("central_content_push_manifest.json")
        try:
            document = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                self._push_manifest = {
                    str(key): str(value) for key, value in document.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._push_manifest = {}

    def _save_push_manifest(self) -> None:
        path = self._manifest_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".tmp-{id(self)}")
        temporary.write_text(
            json.dumps(self._push_manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8",
        )
        temporary.replace(path)

    def _journal_news_links_v2_enabled(self) -> bool:
        capability_reader = getattr(self._client, "capabilities", None)
        if not callable(capability_reader):
            return False
        try:
            return bool(capability_reader().get("journal_news_links_v2", False))
        except RuntimeError as error:
            if is_missing_collection_error(error):
                return False
            raise

    @staticmethod
    def _read_news(
        path: Path, *, include_catalog: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        result = {name: [] for name in (
            "news_article", "news_ai", "news_ai_shared", "news_request_usage",
            "journal_news_link", "journal_v2_news_links",
        )}
        if not path.is_file():
            return result
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = _table_names(connection)
            if include_catalog and "stock_news" in tables:
                for row in connection.execute("SELECT * FROM stock_news"):
                    values = dict(row)
                    if "input_source_json" in values:
                        values["input_source_contract"] = "v1"
                    item = _document(
                        str(values.get("stock_code", "")), str(values.get("identity", "")), values,
                    )
                    item["collector_id"] = f"local-sync:{socket.gethostname().strip() or 'unknown'}"
                    item["collection_scope"] = "local_projection_sync"
                    result["news_article"].append(item)
            if include_catalog and "stock_news_ai" in tables:
                for row in connection.execute("SELECT * FROM stock_news_ai"):
                    values = dict(row)
                    result["news_ai"].append(_document(
                        str(values.get("stock_code", "")), str(values.get("identity", "")), values,
                    ))
            if include_catalog and "news_ai_shared" in tables:
                for row in connection.execute("SELECT * FROM news_ai_shared"):
                    values = dict(row)
                    result["news_ai_shared"].append(_document(
                        "shared", str(values.get("identity", "")), values,
                    ))
            if include_catalog and "news_ai_requests" in tables:
                for row in connection.execute("SELECT * FROM news_ai_requests"):
                    values = dict(row)
                    key = "|".join(str(values.get(name, "")) for name in (
                        "requested_at", "provider", "model", "id",
                    ))
                    result["news_request_usage"].append(_document("usage", key, values))
            if "journal_news_links" in tables:
                for row in connection.execute("SELECT * FROM journal_news_links"):
                    values = dict(row)
                    is_v2 = str(values.get("origin_broker", "legacy")) != "legacy"
                    collection = "journal_v2_news_links" if is_v2 else "journal_news_link"
                    if is_v2 and not _valid_scoped_journal_link(values):
                        continue
                    if not is_v2:
                        values.update(_legacy_scope_values())
                    owner = (
                        str(values.get("origin_account_ref", ""))
                        if is_v2 else str(values.get("group_id", ""))
                    )
                    key = _journal_news_link_key(values) if is_v2 else "|".join((
                        str(values.get("stock_code", "")), str(values.get("identity", "")),
                    ))
                    result[collection].append(_document(owner, key, values))
        finally:
            connection.close()
        return result

    @staticmethod
    def _write_news(path: Path, collections: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        counts = {
            "news_article": 0, "news_ai": 0, "news_ai_shared": 0,
            "journal_news_link": 0, "journal_v2_news_links": 0,
        }
        if not path.is_file():
            return counts
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = _table_names(connection)
            mappings = {
                "news_article": ("stock_news", ("stock_code", "identity")),
                "news_ai": ("stock_news_ai", ("stock_code", "identity")),
                "news_ai_shared": ("news_ai_shared", ("identity",)),
            }
            with connection:
                metadata_ready = all(name in tables for name in ("stock_news", "stock_news_ai", "news_ai_shared")) and all(
                    required <= {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
                    for table, required in (("stock_news", {"input_source_json"}), ("stock_news_ai", {"provenance_json"}),
                                             ("news_ai_shared", {"provenance_json", "company_impacts"})))
                current_articles = {}
                if metadata_ready:
                    connection.execute("BEGIN IMMEDIATE")
                    keys = sorted({(str(value["owner"]), str(value["key"])) for value in collections.get("news_article", ())})
                    if keys:
                        sql = ("WITH wanted(owner,identity) AS (VALUES " + ",".join("(?,?)" for _ in keys) + ") "
                               "SELECT n.* FROM stock_news n JOIN wanted w ON w.owner=n.stock_code AND w.identity=n.identity")
                        current_articles = {(str(row["stock_code"]), str(row["identity"])): dict(row) for row in
                                            connection.execute(sql, tuple(part for key in keys for part in key)).fetchall()}
                for collection, (table, conflict) in mappings.items():
                    if table not in tables:
                        continue
                    incoming = collections.get(collection, [])
                    retained = set()
                    if metadata_ready and collection in {"news_ai", "news_ai_shared"}:
                        groups = {}
                        for index, value in enumerate(incoming):
                            reference = decode_news_ai_provenance(value.get("document", {}).get("provenance_json"))
                            owner = str(value["owner"]) if collection == "news_ai" else str(reference.get("target_id") or "")
                            groups.setdefault((owner, str(value.get("target_name") or "")), []).append(index)
                        for (owner, name), indices in groups.items():
                            own, shared = _retain_matching_body_projections(connection, owner,
                                tuple(str(incoming[index]["key"]) for index in indices),
                                tuple(decode_news_ai_provenance(incoming[index].get("document", {}).get("provenance_json")) for index in indices), name)
                            retained.update(indices[index] for index in (own if collection == "news_ai" else shared))
                    for index, value in enumerate(incoming):
                        if index in retained:
                            continue
                        document = value.get("document")
                        if isinstance(document, dict):
                            if collection == "news_article":
                                source = decode_news_input_source(document.get("input_source_json"))
                                if metadata_ready:
                                    prior = current_articles.get((str(value["owner"]), str(value["key"])))
                                    source, replace_article = select_news_copy_source(
                                        prior.get("input_source_json") if prior else None, source, value.get("native_source_cut"))
                                    if not replace_article and prior is not None:
                                        document = prior
                                document = {**document, "input_source_json": (
                                    json.dumps(source, ensure_ascii=False, separators=(",", ":"))
                                    if source is not None else None
                                )}
                            if collection in {"news_ai", "news_ai_shared"}:
                                document = {**document, "provenance_json": json.dumps(
                                    decode_news_ai_provenance(document.get("provenance_json")),
                                    ensure_ascii=False, separators=(",", ":"),
                                )}
                                for name in ("theme_candidates", "company_impacts", "positive_evidence", "negative_evidence"):
                                    if isinstance(document.get(name), (list, dict)):
                                        document[name] = json.dumps(document[name], ensure_ascii=False, separators=(",", ":"))
                            if _upsert_row(connection, table, conflict, document):
                                counts[collection] += 1
                if "journal_news_links" in tables:
                    columns = {
                        str(row[1]) for row in connection.execute(
                            "PRAGMA table_info(journal_news_links)"
                        )
                    }
                    scoped = "origin_broker" in columns
                    conflict = (
                        (
                            "origin_broker", "origin_environment", "origin_account_ref",
                            "group_id", "stock_code", "identity",
                        )
                        if scoped else ("group_id", "stock_code", "identity")
                    )
                    for collection in ("journal_news_link", "journal_v2_news_links"):
                        for value in collections.get(collection, []):
                            document = value.get("document")
                            normalized = _journal_link_document(
                                value, v2=collection == "journal_v2_news_links", scoped=scoped,
                            )
                            if normalized is not None and _merge_journal_link(
                                connection, conflict, normalized,
                            ):
                                counts[collection] += 1
        finally:
            connection.close()
        return counts

    @staticmethod
    def _write_themes(path: Path, collections: dict[str, list[dict[str, Any]]], *,
                      expected_revision: str | None = None) -> dict[str, int]:
        if not path.is_file() or expected_revision is None:
            raise ThemeBackupError("로컬 revision 없는 테마 교체는 허용하지 않습니다.")
        metadata = collections.get("theme_metadata", [])
        document = metadata[-1].get("document") if metadata else None
        if not isinstance(document, dict):
            raise ThemeBackupError("원격 테마 전체 스냅샷이 없습니다.")
        ThemeBackupService(path).import_document(document, expected_revision=expected_revision)
        return {"theme_profile": len(collections.get("theme_profile", [])),
                "theme_stock": len(collections.get("theme_stock", [])), "theme_metadata": 1}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return table_names(connection)


def _document(owner: str, key: str, values: dict[str, Any]) -> dict[str, Any]:
    return central_document(owner, key, values)


def _legacy_scope_values() -> dict[str, str]:
    return {
        "origin_broker": "legacy",
        "origin_environment": "unknown",
        "origin_account_ref": "legacy-unassigned",
        "canonical_account_ref": "legacy-unassigned",
    }


def _valid_scoped_journal_link(values: dict[str, Any]) -> bool:
    required = (
        "group_id", "stock_code", "identity", "origin_broker",
        "origin_environment", "origin_account_ref", "canonical_account_ref",
    )
    if not all(str(values.get(name, "")).strip() for name in required):
        return False
    if str(values.get("origin_broker")) != "kiwoom":
        return False
    if str(values.get("origin_environment")) not in {"real", "mock"}:
        return False
    try:
        uuid.UUID(str(values["origin_account_ref"]))
        uuid.UUID(str(values["canonical_account_ref"]))
    except (ValueError, AttributeError):
        return False
    # Alias 증명 저장소가 이 로컬 DB에는 없으므로 0c에서는 동일 ref만 수입한다.
    return str(values["origin_account_ref"]) == str(values["canonical_account_ref"])


def _journal_news_link_key(values: dict[str, Any]) -> str:
    identity = {
        "origin_scope": {
            "broker": str(values.get("origin_broker", "")),
            "environment": str(values.get("origin_environment", "")),
            "account_ref": str(values.get("origin_account_ref", "")),
        },
        "group_id": str(values.get("group_id", "")),
        "stock_code": str(values.get("stock_code", "")),
        "identity": str(values.get("identity", "")),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "journal-news-link:v2:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _journal_link_document(
    envelope: object, *, v2: bool, scoped: bool,
) -> dict[str, Any] | None:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("document"), dict):
        return None
    document = dict(envelope["document"])
    explicit_revision = bool(str(document.get("updated_at", "")).strip())
    document["_explicit_revision"] = explicit_revision
    if not explicit_revision:
        document["updated_at"] = (
            str(document.get("linked_at", "")).strip()
            or str(envelope.get("updated_at", "")).strip()
            or "1970-01-01T00:00:00+00:00"
        )
    if v2:
        if not scoped or not _valid_scoped_journal_link(document):
            return None
        owner, key = str(envelope.get("owner", "")), str(envelope.get("key", ""))
        expected = _journal_news_link_key(document)
        legacy_key = "|".join((
            str(document.get("group_id", "")), str(document.get("stock_code", "")),
            str(document.get("identity", "")),
        ))
        # v2 DB가 이미 배포된 기간의 canonical owner/plain key를 읽기 호환한다.
        if not (
            (owner == str(document["origin_account_ref"]) and key == expected)
            or (owner == str(document["canonical_account_ref"]) and key == legacy_key)
        ):
            return None
        document.update({
            "source_collection": "journal_v2_news_links", "source_owner": owner,
            "source_key": key, "source_content_hash": _content_hash(document),
        })
        return document
    # v1 자료에 계좌처럼 보이는 필드가 포함돼도 레거시 범위에서만 복원한다.
    if scoped:
        document.update(_legacy_scope_values())
    else:
        for name in _legacy_scope_values():
            document.pop(name, None)
    expected = "|".join((str(document.get("stock_code", "")), str(document.get("identity", ""))))
    if str(envelope.get("owner", "")) != str(document.get("group_id", "")) or str(envelope.get("key", "")) != expected:
        return None
    document.update({
        "source_collection": "journal_news_link",
        "source_owner": str(envelope.get("owner", "")),
        "source_key": str(envelope.get("key", "")),
        "source_content_hash": _content_hash(document),
    })
    return document


def _content_hash(document: dict[str, Any]) -> str:
    payload = {
        key: value for key, value in document.items()
        if not key.startswith("source_") and key != "_explicit_revision"
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _manifest_key(collection: str, envelope: dict[str, Any]) -> str:
    return json.dumps(
        (collection, str(envelope.get("owner", "")), str(envelope.get("key", ""))),
        ensure_ascii=False, separators=(",", ":"),
    )


def _manifest_hash(envelope: dict[str, Any], *, collection: str) -> str:
    document = envelope.get("document")
    if (collection in {"news_ai", "news_ai_shared"} and isinstance(document, dict)
            and "provenance_json" in document):
        provenance = document["provenance_json"]
        if isinstance(provenance, str):
            try:
                provenance = json.loads(provenance)
            except ValueError:
                pass
        if provenance in (
            None,
            {"status": "UNKNOWN"},
            {"status": "UNKNOWN", "origin": "reported_copy", "reported_origin": "UNKNOWN",
             "reported_status": "UNKNOWN"},
        ):
            # The server labels an unlinked AI copy without adding source evidence.
            # A legacy local table may have no column for that label.
            document = {key: value for key, value in document.items() if key != "provenance_json"}
    if (collection == "theme_metadata" and isinstance(document, dict)
            and document.get("format") == ThemeBackupService.FORMAT
            and document.get("version") == ThemeBackupService.VERSION
            and isinstance(document.get("profiles"), list)):
        # Backup generation is not a theme edit; keep it in the transmitted artifact.
        document = {key: value for key, value in document.items() if key != "created_at"}
    encoded = json.dumps(
        document if isinstance(document, dict) else {},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _merge_journal_link(
    connection: sqlite3.Connection, conflict: tuple[str, ...], values: dict[str, Any],
) -> bool:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(journal_news_links)")}
    if "is_deleted" not in columns:
        return _upsert_row(connection, "journal_news_links", conflict, values)
    if any(name not in values for name in conflict):
        return False
    where = " AND ".join(f"{name}=?" for name in conflict)
    current = connection.execute(
        f"SELECT updated_at,is_deleted,source_content_hash FROM journal_news_links WHERE {where}",
        tuple(values[name] for name in conflict),
    ).fetchone()
    revision = str(values.get("updated_at") or values.get("linked_at") or "")
    if not revision:
        return False
    incoming_deleted = bool(values.get("is_deleted", False))
    explicit_revision = bool(values.pop("_explicit_revision", False))
    if current is not None:
        current_revision, current_deleted, current_hash = str(current[0]), bool(current[1]), str(current[2])
        # UTC and KST strings can denote the same instant or sort in the wrong
        # order. Keep stored receipts intact and compare their actual moments.
        current_order, incoming_order = current_revision, revision
        try:
            current_order = as_kst(datetime.fromisoformat(current_revision.replace('Z', '+00:00')))
            incoming_order = as_kst(datetime.fromisoformat(revision.replace('Z', '+00:00')))
        except ValueError:
            current_order, incoming_order = current_revision, revision
        if current_order > incoming_order or (current_order == incoming_order and current_deleted):
            return False
        if current_order == incoming_order and current_hash not in {"unknown", str(values.get("source_content_hash"))}:
            return False
        if current_deleted and not incoming_deleted and (
            not explicit_revision or incoming_order <= current_order
        ):
            return False
    values["updated_at"] = revision
    return _upsert_row(connection, "journal_news_links", conflict, values)


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

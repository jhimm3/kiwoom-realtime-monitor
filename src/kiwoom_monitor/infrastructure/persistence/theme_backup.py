"""테마 DB만 별도로 이식하는 JSON 백업."""

from __future__ import annotations

from kiwoom_monitor.domain.time_basis import as_kst, kst_wall_now, kst_now
from kiwoom_monitor.domain.research_contract import ThemeSnapshotSource

import json
import hashlib
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

from .backup_file import write_json_backup
from .settings_repository import SettingsNativeWriteError, SettingsRepository


class ThemeBackupError(SettingsNativeWriteError):
    pass


class ThemeRevisionConflict(ThemeBackupError):
    pass


class ThemeBackupService:
    FORMAT = "kiwoom-realtime-monitor-theme-db"
    VERSION = 1

    SYNC_STATE_KEY = "central_theme_sync_v1"

    @staticmethod
    def validate_accepted_snapshot(snapshot: object) -> dict:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("snapshot_id"), str) or not snapshot["snapshot_id"]:
            raise ThemeBackupError("accepted theme receipt is required")
        document = snapshot.get("document")
        if not isinstance(document, dict):
            raise ThemeBackupError("accepted theme document is required")
        source = ThemeSnapshotSource.from_document(
            document, effective_at=snapshot.get("effective_at"), origin_device=snapshot.get("origin_device"),
        )
        if (snapshot.get("content_hash") != source.content_hash
                or snapshot.get("profile_id") != source.profile_id
                or snapshot.get("origin_device") != source.origin_device
                or snapshot.get("effective_at") != source.effective_at
                or any(type(snapshot.get(key)) not in (int, float) for key in ("received_at", "available_at"))
                or "revision_of" not in snapshot
                or snapshot["revision_of"] is not None and not isinstance(snapshot["revision_of"], str)):
            raise ThemeBackupError("accepted theme receipt does not match its source")
        return json.loads(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))

    @contextmanager
    def _sync_transaction(self):
        with closing(sqlite3.connect(self._database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection

    @classmethod
    def _read_sync_state(cls, connection, server_scope: str) -> dict:
        row = connection.execute("SELECT value FROM settings WHERE key=?", (cls.SYNC_STATE_KEY,)).fetchone()
        if row is None:
            return {"schema": 1, "server_scope": server_scope, "ack": None, "prepared": None}
        try:
            state = json.loads(row[0])
        except (TypeError, ValueError) as error:
            raise ThemeBackupError("theme sync recovery state is unreadable") from error
        if (not isinstance(state, dict) or state.get("schema") != 1
                or state.get("server_scope") != server_scope
                or "ack" not in state or "prepared" not in state
                or any(state[key] is not None and not isinstance(state[key], dict) for key in ("ack", "prepared"))):
            raise ThemeBackupError("theme sync recovery source is unknown")
        try:
            ack, prepared = state["ack"], state["prepared"]
            if ack is not None:
                cls.validate_accepted_snapshot(ack["snapshot"])
                if not all(isinstance(ack[key], str) and ack[key] for key in ("local_revision", "local_content_hash")):
                    raise ValueError("missing accepted local identity")
            if prepared is not None:
                source = ThemeSnapshotSource.from_document(
                    prepared["document"], effective_at=prepared["effective_at"], origin_device=prepared["origin_device"],
                )
                expected = ack["snapshot"]["snapshot_id"] if ack is not None else None
                if (prepared["expected_snapshot_id"] != expected or prepared["content_hash"] != source.content_hash
                        or not all(isinstance(prepared[key], str) and prepared[key]
                                   for key in ("publication_id", "local_revision"))):
                    raise ValueError("prepared identity mismatch")
        except (KeyError, TypeError, ValueError) as error:
            raise ThemeBackupError("theme sync recovery identity is invalid") from error
        return state

    @classmethod
    def _write_sync_state(cls, connection, state: dict) -> None:
        connection.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (cls.SYNC_STATE_KEY, json.dumps(state, ensure_ascii=False, allow_nan=False)),
        )

    def prepare_theme_publication(self, *, server_scope: str, origin_device: str) -> dict:
        """Pin the editable snapshot once, durably, before any remote operation."""
        with self._sync_transaction() as connection:
            state = self._read_sync_state(connection, server_scope)
            if state["prepared"] is not None:
                return state
            revision = self._revision(connection)
            document = self.export_document(connection)
            source = ThemeSnapshotSource.from_document(document, origin_device=origin_device)
            ack = state["ack"]
            if ack is None or source.content_hash != ack["local_content_hash"]:
                state["prepared"] = {
                    "publication_id": uuid.uuid4().hex,
                    "expected_snapshot_id": ack["snapshot"]["snapshot_id"] if ack is not None else None,
                    "document": document, "content_hash": source.content_hash,
                    "local_revision": revision, "effective_at": source.effective_at,
                    "origin_device": source.origin_device,
                }
                self._write_sync_state(connection, state)
            return {**state, "receive_revision": revision}

    def acknowledge_theme_publication(self, receipt: dict, *, publication_id: str, server_scope: str) -> dict:
        accepted = self.validate_accepted_snapshot(receipt.get("snapshot"))
        changed, replayed = receipt.get("changed"), receipt.get("replayed")
        if type(changed) is not bool or type(replayed) is not bool or changed and replayed:
            raise ThemeBackupError("theme publication receipt kind is invalid")
        with self._sync_transaction() as connection:
            state = self._read_sync_state(connection, server_scope)
            prepared = state["prepared"]
            if not isinstance(prepared, dict) or prepared.get("publication_id") != publication_id:
                raise ThemeRevisionConflict("theme prepared publication changed")
            if accepted["content_hash"] != prepared["content_hash"]:
                raise ThemeBackupError("theme publication receipt content mismatch")
            if changed or replayed:
                if (accepted["snapshot_id"] != publication_id
                        or accepted["revision_of"] != prepared["expected_snapshot_id"]
                        or accepted["effective_at"] != prepared["effective_at"]
                        or accepted["origin_device"] != prepared["origin_device"]):
                    raise ThemeBackupError("theme publication receipt identity mismatch")
            elif accepted["snapshot_id"] != prepared["expected_snapshot_id"]:
                raise ThemeBackupError("theme no-op receipt is not the expected predecessor")
            state["ack"] = {"snapshot": accepted, "local_revision": prepared["local_revision"],
                            "local_content_hash": prepared["content_hash"]}
            state["prepared"] = None
            self._write_sync_state(connection, state)
            pending = self._revision(connection) != prepared["local_revision"]
            if pending:
                # A full backup restore may recreate row IDs without changing the
                # editable material; only actual unsent content remains pending.
                document = self.export_document(connection)
                pending = ThemeSnapshotSource.from_document(document).content_hash != prepared["content_hash"]
            return {**state, "pending": pending}

    def confirm_accepted_theme_snapshot(self, snapshot: dict, *, server_scope: str,
                                      expected_snapshot_id: str, expected_revision: str) -> dict:
        accepted = self.validate_accepted_snapshot(snapshot)
        with self._sync_transaction() as connection:
            state = self._read_sync_state(connection, server_scope)
            ack = state["ack"]
            if (state["prepared"] is not None or ack is None
                    or self._revision(connection) != expected_revision
                    or ack["snapshot"]["snapshot_id"] != expected_snapshot_id):
                raise ThemeRevisionConflict("local theme edit or baseline changed during receive")
            if accepted["snapshot_id"] == expected_snapshot_id:
                if accepted != ack["snapshot"]:
                    raise ThemeBackupError("accepted generation changed its original receipt")
                return state
            raise ThemeBackupError("remote theme lineage requires explicit reconciliation")

    @classmethod
    def validate_document(cls, document: object) -> None:
        if not isinstance(document, dict) or document.get("format") != cls.FORMAT or document.get("version") != cls.VERSION:
            raise ThemeBackupError("이 프로그램에서 만든 테마 DB 백업 파일이 아닙니다.")
        if not isinstance(document.get("profiles"), list):
            raise ThemeBackupError("테마 프로필 백업 형식이 올바르지 않습니다.")

    def __init__(self, database_path: Path, profile_name: str | None = None, *,
                 now_provider: Callable[[], datetime] | None = None,
                 settings: SettingsRepository | None = None) -> None:
        self._database_path = database_path
        self._settings = settings
        if settings is not None and settings.database_path.resolve() != database_path.resolve():
            raise ThemeBackupError("테마와 설정은 같은 조회 역할 DB를 사용해야 합니다.")
        self._profile_name = profile_name
        self._now = (lambda: as_kst(now_provider())) if now_provider is not None else (lambda: kst_now())

    def _require_settings_connection(self, connection: sqlite3.Connection) -> None:
        """The injected UI port may write only this actual native database."""
        if self._settings is not None:
            try:
                self._settings.require_native_connection(connection, self._database_path)
            except SettingsNativeWriteError as error:
                raise ThemeBackupError("테마 복원의 실제 연결과 설정 DB가 일치하지 않습니다.") from error

    def read_sync_snapshot(self) -> dict[str, object]:
        """Read the editable document and its CAS revision from one SQLite snapshot."""
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("BEGIN")
            revision = self._revision(connection)
            document = self.export_document(connection)
            source = ThemeSnapshotSource.from_document(document)
            return {"document": document, "revision": revision,
                    "content_hash": source.content_hash}
        finally:
            connection.close()

    def revision(self) -> str:
        connection = sqlite3.connect(self._database_path)
        try:
            return self._revision(connection)
        finally:
            connection.close()

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> str:
        tables = (
            "theme_profiles", "profile_themes", "profile_stock_themes",
            "profile_theme_name_decisions", "profile_theme_suggestions",
            "stock_aliases", "stocks",
        )
        available = {str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        digest = hashlib.sha256()
        for table in tables:
            if table not in available:
                continue
            digest.update(table.encode())
            # Import owns the stock catalog, not market observations or their receipts.
            columns = "code,name,market" if table == "stocks" else "*"
            for row in connection.execute(f"SELECT {columns} FROM {table} ORDER BY rowid"):
                digest.update(json.dumps(row, ensure_ascii=False, default=str).encode())
        if "settings" in available:
            row = connection.execute(
                "SELECT value FROM settings WHERE key='theme_active_profile'"
            ).fetchone()
            digest.update(json.dumps(row, ensure_ascii=False).encode())
        return digest.hexdigest()

    def export_document(self, connection: sqlite3.Connection | None = None) -> dict[str, object]:
        """현재 테마 저장 구조를 파일 형식과 동일한 문서로 반환한다."""
        if self._profile_name is not None:
            raise ThemeBackupError("개별 프로필은 파일 내보내기를 사용하세요.")
        owns_connection = connection is None
        if connection is None:
            connection = sqlite3.connect(self._database_path)
        try:
            if owns_connection:
                connection.execute("BEGIN")
            self._require_settings_connection(connection)
            active_row = connection.execute(
                "SELECT value FROM settings WHERE key='theme_active_profile'"
            ).fetchone()
            if self._settings is not None and (
                    active_row is None or type(active_row[0]) is not str or not active_row[0]):
                raise ThemeBackupError("테마 원자료 미준비: 저장된 선택을 보존하며 빈 자료를 백업하지 않습니다.")
            common = {
                "format": self.FORMAT,
                "version": self.VERSION,
                "created_at": self._now().isoformat(timespec="seconds"),
                "active_profile": str(active_row[0]) if active_row is not None else "",
                "aliases": [{"alias": alias, "code": code} for alias, code in connection.execute("SELECT alias, stock_code FROM stock_aliases ORDER BY alias")],
                "stock_catalog": [{"code": code, "name": name, "market": market} for code, name, market in connection.execute("SELECT code, name, market FROM stocks ORDER BY code")],
            }
            has_profiles = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='theme_profiles'"
            ).fetchone() is not None
            if self._settings is not None and not has_profiles:
                raise ThemeBackupError("테마 원자료 미준비: 현재 프로필 자료를 백업할 수 없습니다.")
            if has_profiles:
                profiles = []
                active_found = self._settings is None
                if self._settings is None:
                    profile_sql = "SELECT profile_id, profile_name FROM theme_profiles ORDER BY profile_name COLLATE NOCASE"
                    profile_values = ()
                else:
                    # Reuse the required snapshot list; SQLite owns NOCASE.
                    profile_sql = "SELECT profile_id,profile_name,profile_name=? COLLATE NOCASE FROM theme_profiles ORDER BY profile_name COLLATE NOCASE"
                    profile_values = (active_row[0],)
                for profile_row in connection.execute(profile_sql, profile_values):
                    profile_id, name = profile_row[:2]
                    if self._settings is not None:
                        active_found = active_found or bool(profile_row[2])
                    profiles.append({
                        "name": name,
                        "themes": [{"name": theme, "color": color} for theme, color in connection.execute("SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (profile_id,))],
                        "stock_themes": [{"code": code, "theme": theme, "color": color} for code, theme, color in connection.execute("SELECT stock_code, theme_name, custom_color FROM profile_stock_themes WHERE profile_id=? ORDER BY stock_code, theme_name", (profile_id,))],
                        "theme_name_decisions": self._decision_documents(connection, profile_id),
                        "theme_suggestions": self._suggestion_documents(connection, profile_id),
                    })
                if not active_found:
                    raise ThemeBackupError("테마 원자료 미준비: 저장된 선택을 보존하며 빈 자료를 백업하지 않습니다.")
                return {**common, "profiles": profiles}
            return {
                **common,
                "themes": [{"name": name, "color": color} for name, color in connection.execute("SELECT theme_name, default_color FROM themes ORDER BY theme_name")],
                "stock_themes": [
                    {"code": code, "theme": theme, "color": color}
                    for code, theme, color in connection.execute(
                        "SELECT st.stock_code, t.theme_name, st.custom_color FROM stock_themes st "
                        "JOIN themes t ON t.theme_id = st.theme_id ORDER BY st.stock_code, t.theme_name"
                    )
                ],
            }
        finally:
            if owns_connection:
                connection.close()

    def import_document(self, document: dict[object, object], *,
                        expected_revision: str | None = None) -> None:
        """현재 프로필 문서를 적용한다. 예전 문서는 기존 import_from 경로가 처리한다."""
        if self._profile_name is not None:
            raise ThemeBackupError("개별 프로필은 파일 가져오기를 사용하세요.")
        if isinstance(document.get("profiles"), list):
            self._import_all_profiles(document, expected_revision=expected_revision)
            return
        raise ThemeBackupError("테마 프로필 백업 형식이 올바르지 않습니다.")

    def apply_document_in_transaction(
        self, connection: sqlite3.Connection, document: dict[object, object],
    ) -> None:
        """Apply profiles inside a caller-owned transaction on this database."""
        self.validate_document(document)
        self._apply_all_profiles(connection, document)

    def export_to(self, path: Path) -> None:
        if self._profile_name is not None:
            self._export_profile(path)
            return
        if self._has_profile_schema():
            write_json_backup(path, self.export_document())
            return
        connection = sqlite3.connect(self._database_path)
        try:
            document = {
                "format": self.FORMAT,
                "version": self.VERSION,
                "created_at": kst_wall_now().isoformat(timespec="seconds"),
                "themes": [{"name": name, "color": color} for name, color in connection.execute("SELECT theme_name, default_color FROM themes ORDER BY theme_name")],
                "stock_themes": [
                    {"code": code, "theme": theme, "color": color}
                    for code, theme, color in connection.execute(
                        "SELECT st.stock_code, t.theme_name, st.custom_color FROM stock_themes st "
                        "JOIN themes t ON t.theme_id = st.theme_id ORDER BY st.stock_code, t.theme_name"
                    )
                ],
                "aliases": [{"alias": alias, "code": code} for alias, code in connection.execute("SELECT alias, stock_code FROM stock_aliases ORDER BY alias")],
                "stock_catalog": [{"code": code, "name": name, "market": market} for code, name, market in connection.execute("SELECT code, name, market FROM stocks ORDER BY code")],
            }
        finally:
            connection.close()
        write_json_backup(path, document)

    def import_from(self, path: Path) -> None:
        if self._profile_name is not None:
            self._import_profile(path)
            return
        try:
            preview = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 읽을 수 없습니다.") from error
        if isinstance(preview, dict) and isinstance(preview.get("profiles"), list):
            self._import_all_profiles(preview)
            return
        if self._settings is not None:
            raise ThemeBackupError("조회 역할은 현재 프로필 백업 형식만 복원할 수 있습니다. 원본 파일은 보존됩니다.")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") != self.VERSION:
            raise ThemeBackupError("이 프로그램에서 만든 테마 DB 백업 파일이 아닙니다.")
        themes = document.get("themes")
        stock_themes = document.get("stock_themes")
        aliases = document.get("aliases")
        catalog = document.get("stock_catalog", [])
        if not all(isinstance(value, list) for value in (themes, stock_themes, aliases, catalog)):
            raise ThemeBackupError("테마 DB 백업 파일 형식이 올바르지 않습니다.")
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                for item in catalog:
                    if not isinstance(item, dict):
                        continue
                    code, name, market = str(item.get("code", "")).upper(), str(item.get("name", "")).strip(), str(item.get("market", "")).strip()
                    if len(code) == 6 and code.isalnum() and name:
                        connection.execute("INSERT INTO stocks(code, name, market) VALUES (?, ?, ?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=CURRENT_TIMESTAMP", (code, name, market))
                connection.execute("DELETE FROM stock_themes")
                connection.execute("DELETE FROM themes")
                for item in themes:
                    if isinstance(item, dict) and str(item.get("name", "")).strip():
                        connection.execute("INSERT INTO themes(theme_name, default_color) VALUES (?, ?)", (str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")))
                codes = {row[0] for row in connection.execute("SELECT code FROM stocks")}
                for item in stock_themes:
                    if not isinstance(item, dict):
                        continue
                    code, theme = str(item.get("code", "")), str(item.get("theme", "")).strip()
                    if code not in codes or not theme:
                        continue
                    row = connection.execute("SELECT theme_id FROM themes WHERE theme_name = ?", (theme,)).fetchone()
                    if row:
                        connection.execute("INSERT INTO stock_themes(stock_code, theme_id, custom_color) VALUES (?, ?, ?)", (code, row[0], item.get("color") or None))
                connection.execute("DELETE FROM stock_aliases")
                for item in aliases:
                    if isinstance(item, dict) and str(item.get("alias", "")).strip() and str(item.get("code", "")) in codes:
                        connection.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?)", (str(item["alias"]).strip(), str(item["code"])))
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()

    def _has_profile_schema(self) -> bool:
        connection = sqlite3.connect(self._database_path)
        try:
            return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='theme_profiles'").fetchone() is not None
        finally:
            connection.close()

    def _import_all_profiles(self, document: dict[object, object], *,
                             expected_revision: str | None = None) -> None:
        self.validate_document(document)
        if self._settings is not None:
            self._import_all_profiles_with_settings(document, expected_revision=expected_revision)
            return
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if expected_revision is not None and self._revision(connection) != expected_revision:
                    raise ThemeRevisionConflict("원격 조회 중 로컬 테마가 변경되었습니다.")
                self._apply_all_profiles(connection, document)
        except ThemeRevisionConflict:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ThemeBackupError("테마 프로필 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()

    def _import_all_profiles_with_settings(self, document: dict[object, object], *,
                                           expected_revision: str | None) -> None:
        def write(connection):
            if expected_revision is not None and self._revision(connection) != expected_revision:
                raise ThemeRevisionConflict("원격 조회 중 로컬 테마가 변경되었습니다.")
            self._apply_all_profiles(connection, document, settings_connection_checked=True)

        try:
            self._settings.commit_native_write(self._database_path, write)
        except SettingsNativeWriteError as error:
            failure = error.__cause__
            if (error.native_committed is False and isinstance(failure, ThemeRevisionConflict)
                    and error.close_error is None):
                raise failure
            if error.native_committed is False:
                message = "테마 프로필 복원을 적용하지 못했습니다. COMMIT 전 변경은 되돌렸습니다."
            elif error.native_committed is None:
                message = "테마 복원 COMMIT 결과를 확인할 수 없습니다. 현재 테마는 미적용 상태입니다. 재시작 후 DB를 확인하세요."
            else:
                message = "테마 복원은 DB에 저장됐지만 정리 또는 현재 화면 적용에 실패했습니다. 다시 열어 확인하세요."
            if error.cache_invalidated is False:
                message += " 현재 설정 캐시도 다시 읽을 준비를 마치지 못했습니다."
            raise ThemeBackupError(message, native_committed=error.native_committed,
                cache_invalidated=error.cache_invalidated,
                cache_invalidation_error=error.cache_invalidation_error,
                close_error=error.close_error) from failure

    def _export_profile(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            profile = connection.execute("SELECT profile_id, profile_name FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (self._profile_name,)).fetchone()
            if profile is None:
                raise ThemeBackupError("내보낼 테마 프로필을 찾을 수 없습니다.")
            profile_id, profile_name = profile
            document = {
                "format": self.FORMAT, "version": self.VERSION, "created_at": kst_wall_now().isoformat(timespec="seconds"), "profile": profile_name,
                "themes": [{"name": name, "color": color} for name, color in connection.execute("SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (profile_id,))],
                "stock_themes": [{"code": code, "theme": theme, "color": color} for code, theme, color in connection.execute("SELECT stock_code, theme_name, custom_color FROM profile_stock_themes WHERE profile_id=? ORDER BY stock_code, theme_name", (profile_id,))],
                "theme_name_decisions": self._decision_documents(connection, profile_id),
                "theme_suggestions": self._suggestion_documents(connection, profile_id),
                "aliases": [{"alias": alias, "code": code} for alias, code in connection.execute("SELECT alias, stock_code FROM stock_aliases ORDER BY alias")],
                "stock_catalog": [{"code": code, "name": name, "market": market} for code, name, market in connection.execute("SELECT code, name, market FROM stocks ORDER BY code")],
            }
        finally:
            connection.close()
        write_json_backup(path, document)

    def _import_profile(self, path: Path) -> None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") != self.VERSION:
            raise ThemeBackupError("이 프로그램에서 만든 테마 DB 백업 파일이 아닙니다.")
        themes, stock_themes = document.get("themes"), document.get("stock_themes")
        if not isinstance(themes, list) or not isinstance(stock_themes, list):
            raise ThemeBackupError("테마 DB 백업 파일 형식이 올바르지 않습니다.")
        connection = sqlite3.connect(self._database_path)
        try:
            with connection:
                profile = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (self._profile_name,)).fetchone()
                if profile is None:
                    connection.execute("INSERT INTO theme_profiles(profile_name) VALUES (?)", (self._profile_name,))
                    profile = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=?", (self._profile_name,)).fetchone()
                profile_id = profile[0]
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=?", (profile_id,))
                connection.execute("DELETE FROM profile_themes WHERE profile_id=?", (profile_id,))
                connection.execute("DELETE FROM profile_theme_name_decisions WHERE profile_id=?", (profile_id,))
                connection.execute("DELETE FROM profile_theme_suggestions WHERE profile_id=?", (profile_id,))
                for item in themes:
                    if isinstance(item, dict) and str(item.get("name", "")).strip():
                        connection.execute("INSERT INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)", (profile_id, str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")))
                for item in stock_themes:
                    if isinstance(item, dict) and str(item.get("code", "")).strip() and str(item.get("theme", "")).strip():
                        connection.execute("INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) VALUES (?, ?, ?, ?)", (profile_id, str(item["code"]), str(item["theme"]).strip(), item.get("color") or None))
                decisions = document.get("theme_name_decisions", [])
                if isinstance(decisions, list):
                    self._import_name_decisions(connection, profile_id, decisions)
                suggestions = document.get("theme_suggestions", [])
                if isinstance(suggestions, list):
                    self._import_suggestions(connection, profile_id, suggestions)
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()

    def _apply_all_profiles(self, connection: sqlite3.Connection,
                            document: dict[object, object], *,
                            settings_connection_checked: bool = False) -> None:
        """Apply profiles using the caller's transaction and SQLite connection."""
        if not settings_connection_checked:
            self._require_settings_connection(connection)
        updated_at = self._now().isoformat() if self._settings is not None else None
        profiles = document["profiles"]
        connection.execute("DELETE FROM profile_stock_themes")
        connection.execute("DELETE FROM profile_themes")
        connection.execute("DELETE FROM theme_profiles")
        catalog = document.get("stock_catalog", [])
        if isinstance(catalog, list):
            for item in catalog:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).upper()
                name = str(item.get("name", "")).strip()
                market = str(item.get("market", "")).strip()
                if len(code) == 6 and code.isalnum() and name:
                    if updated_at is None:
                        connection.execute("INSERT INTO stocks(code, name, market) VALUES (?, ?, ?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=CURRENT_TIMESTAMP", (code, name, market))
                    else:
                        connection.execute("INSERT INTO stocks(code,name,market,updated_at) VALUES (?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,market=excluded.market,updated_at=excluded.updated_at", (code,name,market,updated_at))
        for profile in profiles:
            if not isinstance(profile, dict) or not str(profile.get("name", "")).strip():
                continue
            name = str(profile["name"]).strip()
            connection.execute("INSERT INTO theme_profiles(profile_name) VALUES (?)", (name,))
            profile_id = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=?", (name,)).fetchone()[0]
            themes = profile.get("themes", [])
            if isinstance(themes, list):
                for item in themes:
                    if isinstance(item, dict) and str(item.get("name", "")).strip():
                        connection.execute("INSERT INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)", (profile_id, str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")))
            assignments = profile.get("stock_themes", [])
            if isinstance(assignments, list):
                for item in assignments:
                    if isinstance(item, dict) and str(item.get("code", "")).strip() and str(item.get("theme", "")).strip():
                        connection.execute("INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) VALUES (?, ?, ?, ?)", (profile_id, str(item["code"]), str(item["theme"]).strip(), item.get("color") or None))
            decisions = profile.get("theme_name_decisions", [])
            if isinstance(decisions, list):
                self._import_name_decisions(connection, profile_id, decisions, fallback_now=updated_at)
            suggestions = profile.get("theme_suggestions", [])
            if isinstance(suggestions, list):
                self._import_suggestions(connection, profile_id, suggestions)
        if self._settings is not None:
            active_profile = document.get("active_profile")
            if (type(active_profile) is not str or not active_profile
                    or connection.execute("SELECT 1 FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (active_profile,)).fetchone() is None):
                raise ThemeBackupError("복원할 테마의 현재 선택과 실제 프로필이 일치하지 않습니다. 기존 자료를 보존합니다.")
            self._settings.write_many_in_transaction(connection, {"theme_active_profile": active_profile}, updated_at=updated_at)
        else:
            if not connection.execute("SELECT 1 FROM theme_profiles").fetchone():
                connection.execute("INSERT INTO theme_profiles(profile_name) VALUES ('기본 테마')")
            active_profile = str(document.get("active_profile", "")).strip()
            if active_profile and connection.execute(
                "SELECT 1 FROM theme_profiles WHERE profile_name=? COLLATE NOCASE",
                (active_profile,),
            ).fetchone():
                connection.execute(
                    "INSERT INTO settings(key,value) VALUES('theme_active_profile',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (active_profile,),
                )
        codes = {str(row[0]) for row in connection.execute("SELECT code FROM stocks")}
        connection.execute("DELETE FROM stock_aliases")
        aliases = document.get("aliases", [])
        if isinstance(aliases, list):
            for item in aliases:
                if isinstance(item, dict) and str(item.get("alias", "")).strip() and str(item.get("code", "")) in codes:
                    connection.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?)", (str(item["alias"]).strip(), str(item["code"])))

    @staticmethod
    def _decision_documents(
        connection: sqlite3.Connection, profile_id: int,
    ) -> list[dict[str, object]]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='profile_theme_name_decisions'"
        ).fetchone()
        if exists is None:
            return []
        return [
            {
                "kind": kind,
                "source": source,
                "target": target,
                "decision_source": decision_source,
                "updated_at": updated_at,
            }
            for kind, source, target, decision_source, updated_at in connection.execute(
                "SELECT decision_kind,source_name,target_name,decision_source,updated_at "
                "FROM profile_theme_name_decisions WHERE profile_id=? "
                "ORDER BY decision_kind,source_name,target_name",
                (profile_id,),
            )
        ]

    @staticmethod
    def _import_name_decisions(
        connection: sqlite3.Connection, profile_id: int, decisions: list[object], *, fallback_now: str | None = None,
    ) -> None:
        allowed = {"alias", "split_to", "keep_separate"}
        for item in decisions:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", ""))
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            if kind not in allowed or not source or not target:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO profile_theme_name_decisions("
                "profile_id,decision_kind,source_name,target_name,decision_source,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (profile_id, kind, source, target,
                 str(item.get("decision_source") or "user"),
                 str(item.get("updated_at") or fallback_now or kst_now().isoformat(timespec="seconds"))),
            )

    @staticmethod
    def _suggestion_documents(
        connection: sqlite3.Connection, profile_id: int,
    ) -> list[dict[str, object]]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='profile_theme_suggestions'"
        ).fetchone()
        if exists is None:
            return []
        columns = (
            "stock_code", "news_identity", "raw_theme_name", "evidence", "confidence",
            "provider", "model", "body_hash", "analyzed_at", "status",
            "reviewed_theme_names", "reviewed_at", "article_title",
            "article_published_at", "article_url",
        )
        return [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(
                "SELECT stock_code,news_identity,raw_theme_name,evidence,confidence,provider,model,"
                "body_hash,analyzed_at,status,reviewed_theme_names,reviewed_at,article_title,"
                "article_published_at,article_url "
                "FROM profile_theme_suggestions WHERE profile_id=? "
                "ORDER BY analyzed_at,stock_code,news_identity,raw_theme_name",
                (profile_id,),
            )
        ]

    @staticmethod
    def _import_suggestions(
        connection: sqlite3.Connection, profile_id: int, suggestions: list[object],
    ) -> None:
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            stock_code = str(item.get("stock_code", ""))
            identity = str(item.get("news_identity", ""))
            raw_name = str(item.get("raw_theme_name", "")).strip()
            status = str(item.get("status", "pending"))
            if not stock_code or not identity or not raw_name or status not in {"pending", "approved", "rejected"}:
                continue
            connection.execute(
                "INSERT OR REPLACE INTO profile_theme_suggestions("
                "profile_id,stock_code,news_identity,raw_theme_name,evidence,confidence,provider,model,"
                "body_hash,analyzed_at,status,reviewed_theme_names,reviewed_at,article_title,"
                "article_published_at,article_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (profile_id, stock_code, identity, raw_name, str(item.get("evidence", "")),
                 max(0, min(100, int(item.get("confidence", 0)))), str(item.get("provider", "")),
                 str(item.get("model", "")), str(item.get("body_hash", "")),
                 str(item.get("analyzed_at", "")), status,
                 str(item.get("reviewed_theme_names") or "[]"), item.get("reviewed_at"),
                 str(item.get("article_title", "")), str(item.get("article_published_at", "")),
                 str(item.get("article_url", ""))),
            )

"""사용자 설정·표 구성·테마를 이식 가능한 JSON 파일로 백업한다."""

from __future__ import annotations

from kiwoom_monitor.domain.time_basis import as_kst, kst_now, kst_wall_now

import json
import os
import sqlite3
import tempfile
from base64 import b64decode, b64encode
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from kiwoom_monitor.application.stock_enrichment import PREMARKET_CATALOG_SETTING

from .database import DEFAULT_COLUMNS, DEFAULT_SETTINGS
from .backup_file import write_json_backup
from .theme_backup import ThemeBackupError, ThemeBackupService


class SettingsBackupError(ValueError):
    def __init__(self, message: str, *, local_database_applied: bool = False) -> None:
        super().__init__(message)
        self.local_database_applied = local_database_applied


class SettingsBackupService:
    FORMAT = "kiwoom-realtime-monitor-settings"
    VERSION = 4
    THEME_SETTING_KEYS = frozenset(key for key in DEFAULT_SETTINGS if key.startswith("theme_") and not key.endswith("_dir") and not key.startswith("theme_manager_"))
    # 백업 파일은 사용자가 고른 아이콘/알림 소리만 포함한다. 복원 시에는
    # 허용된 폴더와 확장자, 크기를 모두 다시 확인한다.
    MAX_BACKUP_DOCUMENT_BYTES = 32 * 1024 * 1024
    MAX_BACKUP_ASSET_TOTAL_BYTES = 20 * 1024 * 1024
    _ASSET_RULES = (
        ("data/strength_icons", frozenset({".png"}), 2 * 1024 * 1024),
        ("data/near_high_icons", frozenset({".png"}), 2 * 1024 * 1024),
        ("data/near_high_sounds", frozenset({".wav", ".mp3", ".ogg", ".m4a"}), 5 * 1024 * 1024),
    )

    def __init__(self, database_path: Path, *, now_provider: Callable[[], datetime] | None = None) -> None:
        self._database_path = database_path
        # Receipt clock for newly accepted legacy catalog rows. Archive
        # timestamps are not rewritten; startup restores have no API clock yet.
        self._now_provider = now_provider or kst_now

    def accept_preserved_current_settings(
        self, *, rows: dict[str, list[tuple]], ownership: dict, receipt_at: Callable[[], str],
    ) -> tuple[dict, bool, dict]:
        """Accept selected raw rows into an explicitly owned offline target only.

        This is separate from normal backup restore: no defaults, coercion, assets,
        sync, cache publication or live runtime activation. The first receipt and
        data share the same native transaction. Original versions remain original.
        """
        import math
        import re

        columns = {
            "settings": ("key", "value"),
            "column_settings": ("column_name", "visible", "position", "width"),
            "central_setting_versions": ("setting_key", "updated_at"),
            "central_column_setting_versions": ("column_name", "updated_at"),
        }
        if set(rows) != set(columns) or any(
            not isinstance(row, tuple) or len(row) != len(columns[table])
            or any(type(value) not in (type(None), int, float, str, bytes)
                   or type(value) is int and not -(2**63) <= value < 2**63
                   or type(value) is float and math.isnan(value) for value in row)
            for table, values in rows.items() for row in values
        ):
            raise SettingsBackupError("unsupported raw settings row")
        count = sum(map(len, rows.values()))
        owner_keys = {"format", "operation_id", "target_id", "source_instance", "source_owner",
                      "manifest_sha256", "selection_sha256", "target_role", "counts"}
        counts = ownership.get("counts", {})
        if (set(ownership) != owner_keys or ownership.get("format") != "offline-settings-target/v1"
                or any(not isinstance(ownership[key], str)
                       or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", ownership[key])
                       for key in ("operation_id", "target_id", "source_instance", "source_owner", "target_role"))
                or any(not isinstance(ownership[key], str) or not re.fullmatch(r"[0-9a-f]{64}", ownership[key])
                       for key in ("manifest_sha256", "selection_sha256"))
                or set(counts) != {"captured", "selected", "unselected", "unresolved", "reference_pending"}
                or any(type(value) is not int or value < 0 for value in counts.values())
                or counts.get("selected") != count or not count
                or counts.get("captured") != count + counts.get("unselected", -1)
                or counts.get("unresolved") != counts.get("unselected")
                or counts.get("reference_pending", 0) > counts.get("unselected", -1)):
            raise SettingsBackupError("invalid offline settings ownership")
        expected = {**ownership, "format": "offline-current-settings-receipt/v1",
                    "state": "selected_raw_settings_accepted_only", "runtime_state": "OFFLINE_UNBOUND",
                    "order_authority": False, "overall_P2_complete": False}
        path = self._database_path.absolute()
        if path.resolve() != path or path.is_symlink() or path.name != "offline.sqlite3":
            raise SettingsBackupError("redirected or unexpected offline target")
        marker = path.parent / "settings-target-owner.json"
        owns_preaccept = False
        if marker.exists():
            if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != ownership:
                raise SettingsBackupError("offline target ownership conflict")
            owns_preaccept = True
        if not path.exists():
            if not owns_preaccept:
                raise SettingsBackupError("offline target ownership is required")
            with path.open("xb"):
                pass
        metrics = dict(connections=1, begin=0, commit=0, rollback=0, selects=0,
                       ddl_statements=0, data_insert_rows=0, receipt_insert_rows=0)
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)

        def select(sql, parameters=()):
            metrics["selects"] += 1
            return connection.execute(sql, parameters).fetchall()

        def verify_rows():
            for table, names in columns.items():
                actual = select(f"SELECT {','.join(names)} FROM {table} ORDER BY rowid")
                if len(actual) != len(rows[table]) or any(
                    type(left) is not type(right)
                    or (left.hex() != right.hex() if type(left) is float else left != right)
                    for a, b in zip(actual, rows[table], strict=True) for left, right in zip(a, b, strict=True)
                ):
                    raise SettingsBackupError("offline target raw data conflict")

        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            metrics["begin"] = 1
            native_objects = select("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%'")
            objects = {row[0] for row in native_objects}
            allowed = {*columns, "settings_transition_receipts"}
            if objects - allowed or any(row[1] != "table" for row in native_objects):
                raise SettingsBackupError("offline target contains unrelated objects")
            existing = select("SELECT receipt_json FROM settings_transition_receipts WHERE operation_id=?",
                              (ownership["operation_id"],)) if "settings_transition_receipts" in objects else []
            if existing:
                receipt = json.loads(existing[0][0])
                if {key: value for key, value in receipt.items() if key != "accepted_at"} != expected:
                    raise SettingsBackupError("immutable settings receipt conflict")
                verify_rows()
                connection.rollback()
                metrics["rollback"] = 1
                return receipt, True, metrics
            if not owns_preaccept or objects:
                raise SettingsBackupError("not an empty owned settings target")
            # No affinity is intentional in this offline carrier: original NULL,
            # BLOB and SQLite primitive types must survive, including legacy rows.
            # Existing repository readers retain their own current conversions.
            for table, names in columns.items():
                declaration = ",".join(name + (" PRIMARY KEY" if index == 0 else "")
                                       for index, name in enumerate(names))
                connection.execute(f"CREATE TABLE {table} ({declaration})")
                metrics["ddl_statements"] += 1
                connection.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in names)})", rows[table])
                metrics["data_insert_rows"] += len(rows[table])
            connection.execute("CREATE TABLE settings_transition_receipts (operation_id TEXT PRIMARY KEY,receipt_json TEXT NOT NULL)")
            metrics["ddl_statements"] += 1
            verify_rows()
            accepted_clock = datetime.fromisoformat(receipt_at())
            if accepted_clock.utcoffset() is None:
                raise SettingsBackupError("settings transition receipt clock must be aware")
            accepted_at = as_kst(accepted_clock).isoformat()
            receipt = {**expected, "accepted_at": accepted_at}
            connection.execute("INSERT INTO settings_transition_receipts VALUES (?,?)",
                               (ownership["operation_id"], json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False)))
            metrics["receipt_insert_rows"] = 1
            connection.commit()
            metrics["commit"] = 1
            return receipt, False, metrics
        except BaseException:
            connection.rollback()
            metrics["rollback"] = 1
            raise
        finally:
            connection.close()

    def export_to(self, path: Path, include_settings: bool = True, include_themes: bool = True, excluded_setting_keys: frozenset[str] = frozenset(), include_column_widths: bool = True, *, connection: sqlite3.Connection | None = None) -> None:
        owns_connection = connection is None
        if connection is None:
            connection = sqlite3.connect(self._database_path)
        try:
            settings = {key: value for key, value in connection.execute("SELECT key, value FROM settings").fetchall()
                        if key != PREMARKET_CATALOG_SETTING and key not in excluded_setting_keys
                        and (include_themes or key != ThemeBackupService.SYNC_STATE_KEY)}
            columns = [
                {
                    "name": name,
                    "visible": bool(visible),
                    "position": position,
                    **({"width": width} if include_column_widths else {}),
                }
                for name, visible, position, width in connection.execute(
                    "SELECT column_name, visible, position, width FROM column_settings ORDER BY position"
                )
            ]
            document: dict[str, Any] = {
                "format": self.FORMAT,
                "version": self.VERSION,
                "created_at": kst_wall_now().isoformat(timespec="seconds"),
            }
            if include_settings:
                from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
                news_config = LocalNaverNewsConfig(self._database_path.parent / "naver_news.dat")
                news_filter = news_config.load_filter()
                news_ai = news_config.load_ai()
                news_official = news_config.load_official()
                document.update({
                    "settings": settings,
                    "columns": columns,
                    "assets": self._export_assets(settings),
                    # API 키·Client Secret은 넣지 않고 공개 정보인 이름·주소만 이식한다.
                    "news_shortcuts": [
                        {"name": name, "url": url}
                        for name, url in news_config.load_shortcuts()
                    ],
                    "news_settings": {
                        "filter": {
                            "enabled": news_filter.enabled,
                            "excluded_words": list(news_filter.excluded_words),
                            "excluded_providers": list(news_filter.excluded_providers),
                            "provider_filter_enabled": news_filter.provider_filter_enabled,
                            "visible_columns": list(news_filter.visible_columns),
                            "positive_color": news_filter.positive_color,
                            "negative_color": news_filter.negative_color,
                            "mixed_color": news_filter.mixed_color,
                            "neutral_color": news_filter.neutral_color,
                            "stored_news_limit": news_filter.stored_news_limit,
                        },
                        "ai": {
                            "provider": news_ai.provider,
                            "model": news_ai.model,
                            "daily_limit": news_ai.daily_limit,
                            "auto_recent_limit": news_ai.auto_recent_limit,
                            "auto_analyze": news_ai.auto_analyze,
                            "request_mode": news_ai.request_mode,
                            "batch_size": news_ai.batch_size,
                        },
                        "official": {"dart_enabled": news_official.dart_enabled},
                    },
                })
            if include_themes:
                document.update({
                    "theme_data": ThemeBackupService(self._database_path).export_document(connection=connection),
                    "theme_settings": {key: value for key, value in settings.items() if key in self.THEME_SETTING_KEYS},
                })
            write_json_backup(path, document)
        finally:
            if owns_connection:
                connection.close()

    def _export_assets(self, settings: dict[str, str]) -> list[dict[str, str]]:
        data_dir = self._database_path.parent
        paths = {
            settings.get(f"strength_icon_{level}_image", "") for level in ("interest", "caution", "fire")
        } | {
            settings.get(f"near_high_icon_{level}_image", "") for level in ("interest", "caution", "fire")
        } | {
            settings.get(f"near_high_sound_{level}", "") for level in ("interest", "caution", "fire")
        }
        assets: list[dict[str, str]] = []
        total_size = 0
        for stored in sorted(value for value in paths if value):
            resolved = self._resolve_asset_path(data_dir, stored)
            if resolved is None:
                continue
            source, maximum_size = resolved
            try:
                size = source.stat().st_size
            except OSError:
                continue
            if not source.is_file() or size > maximum_size or total_size + size > self.MAX_BACKUP_ASSET_TOTAL_BYTES:
                continue
            try:
                assets.append({"path": stored, "content": b64encode(source.read_bytes()).decode("ascii")})
                total_size += size
            except OSError:
                continue
        return assets

    def validate_from(
        self, path: Path, *, include_settings: bool = True, include_themes: bool = True,
        include_column_widths: bool = True, include_column_layout: bool = True,
    ) -> None:
        self._read_document(path, include_settings, include_themes, include_column_widths, include_column_layout)

    def _read_document(
        self, path: Path, include_settings: bool, include_themes: bool,
        include_column_widths: bool, include_column_layout: bool,
    ) -> dict[str, Any]:
        try:
            if path.stat().st_size > self.MAX_BACKUP_DOCUMENT_BYTES:
                raise SettingsBackupError("설정 백업 파일이 너무 큽니다.")
            document = json.loads(path.read_text(encoding="utf-8"))
        except SettingsBackupError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SettingsBackupError("설정 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") not in {1, 2, 3, self.VERSION}:
            raise SettingsBackupError("이 프로그램에서 만든 설정 백업 파일이 아닙니다.")
        settings = document.get("settings", {})
        columns = document.get("columns", [])
        themes = document.get("themes", [])
        stock_themes = document.get("stock_themes", [])
        aliases = document.get("aliases", [])
        if not all(isinstance(value, list) for value in (columns, themes, stock_themes, aliases)) or not isinstance(settings, dict):
            raise SettingsBackupError("설정 백업 파일 형식이 올바르지 않습니다.")
        if include_themes and document["version"] == self.VERSION:
            try:
                ThemeBackupService.validate_document(document.get("theme_data"))
            except ThemeBackupError as error:
                raise SettingsBackupError("설정 백업의 테마 프로필 형식이 올바르지 않습니다.") from error
        if include_settings:
            valid_columns = {name for name, _, _, _ in DEFAULT_COLUMNS}
            try:
                for item in columns:
                    if isinstance(item, dict) and str(item.get("name", "")) in valid_columns:
                        if include_column_widths or include_column_layout:
                            int(item.get("position", 0))
                        if include_column_widths:
                            int(item.get("width", 100))
            except (TypeError, ValueError) as error:
                raise SettingsBackupError("설정 백업의 표 구성 형식이 올바르지 않습니다.") from error
            self._validate_assets(document.get("assets", []))
        return document

    def import_from(self, path: Path, include_settings: bool = True, include_themes: bool = True, excluded_setting_keys: frozenset[str] = frozenset(), include_column_widths: bool = True, include_column_layout: bool = True) -> None:
        document = self._read_document(path, include_settings, include_themes, include_column_widths, include_column_layout)
        self._apply_documents_in_one_transaction((
            (document, include_settings, include_themes, excluded_setting_keys,
             include_column_widths, include_column_layout),
        ))

    def import_from_settings_and_themes(
        self, settings_path: Path | None, themes_path: Path | None,
        excluded_setting_keys: frozenset[str] = frozenset(),
        include_column_widths: bool = False,
    ) -> None:
        """Validate both Drive documents, then apply their monitor DB changes once."""
        documents: list[tuple[dict[str, Any], bool, bool, frozenset[str], bool, bool]] = []
        if settings_path is not None:
            settings = self._read_document(
                settings_path, True, False, include_column_widths, True,
            )
            documents.append((settings, True, False, excluded_setting_keys,
                              include_column_widths, True))
        if themes_path is not None:
            themes = self._read_document(themes_path, False, True, False, False)
            documents.append((themes, False, True, excluded_setting_keys,
                              False, False))
        self._apply_documents_in_one_transaction(tuple(documents))

    def _apply_documents_in_one_transaction(
        self,
        documents: tuple[tuple[dict[str, Any], bool, bool, frozenset[str], bool, bool], ...],
    ) -> None:
        if not documents:
            return
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                for document, include_settings, include_themes, excluded, widths, layout in documents:
                    self._apply_document_to_connection(
                        connection, document, include_settings, include_themes,
                        excluded, widths, layout,
                    )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise SettingsBackupError("설정 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()
        for document, include_settings, _, _, _, _ in documents:
            if include_settings:
                try:
                    self._import_assets(document.get("assets", []))
                    self._import_news_settings(
                        document.get("news_settings"), document.get("news_shortcuts"),
                    )
                except (OSError, ValueError) as error:
                    raise SettingsBackupError(
                        "설정 DB는 적용됐지만 뉴스 설정 또는 자산 파일 저장에 실패했습니다.",
                        local_database_applied=True,
                    ) from error

    def _apply_document_to_connection(
        self, connection: sqlite3.Connection, document: dict[str, Any],
        include_settings: bool, include_themes: bool,
        excluded_setting_keys: frozenset[str], include_column_widths: bool,
        include_column_layout: bool,
    ) -> None:
        settings = document.get("settings", {})
        columns = document.get("columns", [])
        valid_columns = {name for name, _, _, _ in DEFAULT_COLUMNS}
        imported_columns = [
            item for item in columns
            if isinstance(item, dict) and str(item.get("name", "")) in valid_columns
        ]
        if include_settings:
            connection.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, str(value)) for key, value in settings.items()
                 if (key in DEFAULT_SETTINGS or include_themes and key == ThemeBackupService.SYNC_STATE_KEY)
                 and key != PREMARKET_CATALOG_SETTING and key not in excluded_setting_keys],
            )
            if include_column_widths:
                connection.executemany(
                    "UPDATE column_settings SET visible=?,position=?,width=? WHERE column_name=?",
                    [(int(bool(item.get("visible"))), int(item.get("position", 0)),
                      max(20, int(item.get("width", 100))), str(item["name"]))
                     for item in imported_columns],
                )
            elif include_column_layout:
                connection.executemany(
                    "UPDATE column_settings SET visible=?,position=? WHERE column_name=?",
                    [(int(bool(item.get("visible"))), int(item.get("position", 0)),
                      str(item["name"])) for item in imported_columns],
                )
        if include_themes:
            theme_data = document.get("theme_data")
            if isinstance(theme_data, dict):
                ThemeBackupService(self._database_path).apply_document_in_transaction(
                    connection, theme_data,
                )
            else:
                self._apply_legacy_themes(connection, document)
            theme_settings = document.get("theme_settings", {})
            if isinstance(theme_settings, dict):
                connection.executemany(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ((key, str(value)) for key, value in theme_settings.items()
                     if key in self.THEME_SETTING_KEYS and key not in excluded_setting_keys),
                )

    def _apply_legacy_themes(
        self, connection: sqlite3.Connection, document: dict[str, Any],
    ) -> None:
        stock_catalog = document.get("stock_catalog", [])
        imported_at = None
        if isinstance(stock_catalog, list):
            for item in stock_catalog:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).upper()
                name = str(item.get("name", "")).strip()
                market = str(item.get("market", "")).strip()
                if len(code) == 6 and code.isalnum() and name:
                    if imported_at is None:
                        imported_at = as_kst(self._now_provider()).isoformat(timespec="seconds")
                    connection.execute(
                        "INSERT INTO stocks(code,name,market,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(code) DO UPDATE SET name=excluded.name,market=excluded.market,"
                        "updated_at=excluded.updated_at", (code, name, market, imported_at),
                    )
        connection.execute("DELETE FROM stock_themes")
        connection.execute("DELETE FROM themes")
        for item in document.get("themes", []):
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                connection.execute(
                    "INSERT INTO themes(theme_name,default_color) VALUES(?,?)",
                    (str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")),
                )
        stock_codes = {code for (code,) in connection.execute("SELECT code FROM stocks")}
        for item in document.get("stock_themes", []):
            if not isinstance(item, dict):
                continue
            code, theme = str(item.get("code", "")), str(item.get("theme", "")).strip()
            if code not in stock_codes or not theme:
                continue
            row = connection.execute(
                "SELECT theme_id FROM themes WHERE theme_name=?", (theme,),
            ).fetchone()
            if row:
                connection.execute(
                    "INSERT INTO stock_themes(stock_code,theme_id,custom_color) VALUES(?,?,?)",
                    (code, row[0], item.get("color") or None),
                )
        connection.execute("DELETE FROM stock_aliases")
        for item in document.get("aliases", []):
            if isinstance(item, dict):
                alias, code = str(item.get("alias", "")).strip(), str(item.get("code", ""))
                if alias and code in stock_codes:
                    connection.execute(
                        "INSERT INTO stock_aliases(alias,stock_code) VALUES(?,?)", (alias, code),
                    )

    def _import_news_settings(self, raw_settings: Any, raw_shortcuts: Any) -> None:
        if not isinstance(raw_settings, dict) and not isinstance(raw_shortcuts, list):
            return
        from kiwoom_monitor.infrastructure.naver_news import (
            LocalNaverNewsConfig, NewsAISettings, NewsFilterSettings, OfficialNewsSettings,
        )
        shortcuts: list[tuple[str, str]] = []
        for item in raw_shortcuts[:5] if isinstance(raw_shortcuts, list) else ():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:80]
            url = str(item.get("url", "")).strip()[:2048]
            if name and (url.startswith("https://") or url.startswith("http://")):
                shortcuts.append((name, url))
        config = LocalNaverNewsConfig(self._database_path.parent / "naver_news.dat")
        current_filter, current_ai, current_official = config.load_filter(), config.load_ai(), config.load_official()
        filter_data = raw_settings.get("filter", {}) if isinstance(raw_settings, dict) else {}
        ai_data = raw_settings.get("ai", {}) if isinstance(raw_settings, dict) else {}
        official_data = raw_settings.get("official", {}) if isinstance(raw_settings, dict) else {}
        if not isinstance(filter_data, dict):
            filter_data = {}
        if not isinstance(ai_data, dict):
            ai_data = {}
        if not isinstance(official_data, dict):
            official_data = {}
        allowed_columns = {"time", "provider", "category", "outlook", "title"}
        raw_columns = filter_data.get("visible_columns", current_filter.visible_columns)
        if not isinstance(raw_columns, (list, tuple)):
            raw_columns = current_filter.visible_columns
        columns = tuple(
            value for value in raw_columns
            if isinstance(value, str) and value in allowed_columns
        ) or current_filter.visible_columns
        news_filter = NewsFilterSettings(
            bool(filter_data.get("enabled", current_filter.enabled)),
            self._text_tuple(filter_data.get("excluded_words"), current_filter.excluded_words),
            self._text_tuple(filter_data.get("excluded_providers"), current_filter.excluded_providers),
            bool(filter_data.get("provider_filter_enabled", current_filter.provider_filter_enabled)),
            columns,
            self._color(filter_data.get("positive_color"), current_filter.positive_color),
            self._color(filter_data.get("negative_color"), current_filter.negative_color),
            self._color(filter_data.get("mixed_color"), current_filter.mixed_color),
            self._color(filter_data.get("neutral_color"), current_filter.neutral_color),
            self._bounded_int(filter_data.get("stored_news_limit"), current_filter.stored_news_limit, 50, 1000),
        )
        provider = str(ai_data.get("provider", current_ai.provider))
        request_mode = str(ai_data.get("request_mode", current_ai.request_mode))
        news_ai = NewsAISettings(
            provider if provider in {"none", "openai", "gemini", "claude", "chatgpt_plan"} else current_ai.provider,
            current_ai.api_key,
            str(ai_data.get("model", current_ai.model))[:200],
            self._bounded_int(ai_data.get("daily_limit"), current_ai.daily_limit, 0, 1_000_000),
            self._bounded_int(ai_data.get("auto_recent_limit"), current_ai.auto_recent_limit, 1, 1000),
            bool(ai_data.get("auto_analyze", current_ai.auto_analyze)),
            request_mode if request_mode in {"single", "batch"} else current_ai.request_mode,
            self._bounded_int(ai_data.get("batch_size"), current_ai.batch_size, 2, 20),
        )
        news_official = OfficialNewsSettings(
            current_official.dart_api_key,
            bool(official_data.get("dart_enabled", current_official.dart_enabled)),
        )
        config.save(
            config.load(), news_filter, news_ai, news_official,
            tuple(shortcuts) if isinstance(raw_shortcuts, list) else None,
        )

    @staticmethod
    def _text_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(value, list):
            return default
        return tuple(dict.fromkeys(str(item).strip()[:200] for item in value if str(item).strip()))

    @staticmethod
    def _color(value: Any, default: str) -> str:
        text = str(value).upper()
        return text if len(text) == 7 and text.startswith("#") and all(c in "0123456789ABCDEF" for c in text[1:]) else default

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError):
            return default

    def _validate_assets(self, assets: Any) -> list[tuple[Path, bytes]]:
        if not isinstance(assets, list):
            raise SettingsBackupError("설정 백업의 자산 목록 형식이 올바르지 않습니다.")
        data_dir = self._database_path.parent
        total_size = 0
        prepared: list[tuple[Path, bytes]] = []
        seen: set[Path] = set()
        for item in assets:
            if not isinstance(item, dict):
                raise SettingsBackupError("설정 백업의 자산 항목 형식이 올바르지 않습니다.")
            stored, content = item.get("path"), item.get("content")
            if not isinstance(stored, str) or not isinstance(content, str):
                raise SettingsBackupError("설정 백업의 자산 경로 또는 자료가 올바르지 않습니다.")
            resolved = self._resolve_asset_path(data_dir, stored)
            if resolved is None:
                raise SettingsBackupError("설정 백업에 허용되지 않은 자산 경로가 있습니다.")
            destination, maximum_size = resolved
            if destination in seen:
                raise SettingsBackupError("설정 백업에 중복된 자산 경로가 있습니다.")
            seen.add(destination)
            # Base64는 원본보다 약 4/3배 크다. 먼저 길이를 확인해 불필요한
            # 대용량 디코딩을 막는다.
            if len(content) > ((maximum_size + 2) // 3) * 4:
                raise SettingsBackupError("설정 백업 자산이 허용 크기를 초과했습니다.")
            try:
                data = b64decode(content, validate=True)
            except ValueError as error:
                raise SettingsBackupError("설정 백업 자산의 Base64 자료가 손상되었습니다.") from error
            if len(data) > maximum_size or total_size + len(data) > self.MAX_BACKUP_ASSET_TOTAL_BYTES:
                raise SettingsBackupError("설정 백업 자산의 전체 크기가 허용치를 초과했습니다.")
            total_size += len(data)
            prepared.append((destination, data))
        return prepared

    def _import_assets(self, assets: Any) -> None:
        prepared = self._validate_assets(assets)
        for destination, data in prepared:
            temporary: Path | None = None
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=destination.parent,
                    prefix=f".{destination.name}.", suffix=".tmp", delete=False,
                ) as output:
                    temporary = Path(output.name)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    @classmethod
    def _resolve_asset_path(cls, data_dir: Path, stored: str) -> tuple[Path, int] | None:
        """저장된 논리 data/ 경로를 실제 DB 폴더의 허용 자산으로 해석한다."""
        relative = Path(stored)
        if not stored or relative.is_absolute():
            return None
        normalized = relative.as_posix()
        data_dir = data_dir.resolve()
        for directory, extensions, maximum_size in cls._ASSET_RULES:
            if not normalized.startswith(f"{directory}/"):
                continue
            candidate = (data_dir / relative.relative_to("data")).resolve()
            allowed_directory = (data_dir / Path(directory).relative_to("data")).resolve()
            try:
                candidate.relative_to(allowed_directory)
            except ValueError:
                continue
            if candidate.suffix.casefold() in extensions:
                return candidate, maximum_size
        return None

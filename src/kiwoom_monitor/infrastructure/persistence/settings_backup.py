"""사용자 설정·표 구성·테마를 이식 가능한 JSON 파일로 백업한다."""

from __future__ import annotations

import json
import sqlite3
from base64 import b64decode, b64encode
from datetime import datetime
from pathlib import Path
from typing import Any

from .database import DEFAULT_COLUMNS, DEFAULT_SETTINGS


class SettingsBackupError(ValueError):
    pass


class SettingsBackupService:
    FORMAT = "kiwoom-realtime-monitor-settings"
    VERSION = 2

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def export_to(self, path: Path, include_settings: bool = True, include_themes: bool = True, excluded_setting_keys: frozenset[str] = frozenset(), include_column_widths: bool = True) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            settings = {key: value for key, value in connection.execute("SELECT key, value FROM settings").fetchall() if key not in excluded_setting_keys}
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
            themes = [
                {"name": name, "color": color}
                for name, color in connection.execute("SELECT theme_name, default_color FROM themes ORDER BY theme_name")
            ]
            stock_themes = [
                {"code": code, "theme": theme, "color": color}
                for code, theme, color in connection.execute(
                    "SELECT st.stock_code, t.theme_name, st.custom_color "
                    "FROM stock_themes st JOIN themes t ON t.theme_id = st.theme_id "
                    "ORDER BY st.stock_code, t.theme_name"
                )
            ]
            aliases = [
                {"alias": alias, "code": code}
                for alias, code in connection.execute("SELECT alias, stock_code FROM stock_aliases ORDER BY alias")
            ]
            stock_catalog = [
                {"code": code, "name": name, "market": market}
                for code, name, market in connection.execute("SELECT code, name, market FROM stocks ORDER BY code")
            ]
        finally:
            connection.close()
        document: dict[str, Any] = {
            "format": self.FORMAT,
            "version": self.VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        if include_settings:
            document.update({"settings": settings, "columns": columns, "assets": self._export_assets(settings)})
        if include_themes:
            document.update({"themes": themes, "stock_themes": stock_themes, "aliases": aliases, "stock_catalog": stock_catalog})
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def _export_assets(self, settings: dict[str, str]) -> list[dict[str, str]]:
        root = self._database_path.parent.parent
        paths = {
            settings.get(f"strength_icon_{level}_image", "") for level in ("interest", "caution", "fire")
        } | {
            settings.get(f"near_high_icon_{level}_image", "") for level in ("interest", "caution", "fire")
        } | {
            settings.get(f"near_high_sound_{level}", "") for level in ("interest", "caution", "fire")
        }
        assets: list[dict[str, str]] = []
        for stored in sorted(value for value in paths if value):
            source = root / stored
            if source.is_file() and self._is_asset_path(stored):
                assets.append({"path": stored, "content": b64encode(source.read_bytes()).decode("ascii")})
        return assets

    def import_from(self, path: Path, include_settings: bool = True, include_themes: bool = True, excluded_setting_keys: frozenset[str] = frozenset(), include_column_widths: bool = True) -> None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SettingsBackupError("설정 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") not in {1, self.VERSION}:
            raise SettingsBackupError("이 프로그램에서 만든 설정 백업 파일이 아닙니다.")
        settings = document.get("settings", {})
        columns = document.get("columns", [])
        themes = document.get("themes", [])
        stock_themes = document.get("stock_themes", [])
        aliases = document.get("aliases", [])
        stock_catalog = document.get("stock_catalog", [])
        assets = document.get("assets", [])
        if not all(isinstance(value, list) for value in (columns, themes, stock_themes, aliases)) or not isinstance(settings, dict):
            raise SettingsBackupError("설정 백업 파일 형식이 올바르지 않습니다.")

        valid_columns = {name for name, _, _, _ in DEFAULT_COLUMNS}
        imported_columns = [
            item for item in columns
            if isinstance(item, dict) and str(item.get("name", "")) in valid_columns
        ]
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                if include_settings:
                    connection.executemany("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [(key, str(value)) for key, value in settings.items() if key in DEFAULT_SETTINGS and key not in excluded_setting_keys])
                    if include_column_widths:
                        connection.executemany("UPDATE column_settings SET visible = ?, position = ?, width = ? WHERE column_name = ?", [(int(bool(item.get("visible"))), int(item.get("position", 0)), max(20, int(item.get("width", 100))), str(item["name"])) for item in imported_columns])
                    else:
                        connection.executemany("UPDATE column_settings SET visible = ?, position = ? WHERE column_name = ?", [(int(bool(item.get("visible"))), int(item.get("position", 0)), str(item["name"])) for item in imported_columns])
                if include_themes:
                    for item in (stock_catalog if isinstance(stock_catalog, list) else ()):
                        if not isinstance(item, dict): continue
                        code, name, market = str(item.get("code", "")).upper(), str(item.get("name", "")).strip(), str(item.get("market", "")).strip()
                        if len(code) == 6 and code.isalnum() and name: connection.execute("INSERT INTO stocks(code, name, market) VALUES (?, ?, ?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=CURRENT_TIMESTAMP", (code, name, market))
                    connection.execute("DELETE FROM stock_themes"); connection.execute("DELETE FROM themes")
                    for item in themes:
                        if isinstance(item, dict) and str(item.get("name", "")).strip(): connection.execute("INSERT INTO themes(theme_name, default_color) VALUES (?, ?)", (str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")))
                    stock_codes = {code for (code,) in connection.execute("SELECT code FROM stocks")}
                    for item in stock_themes:
                        if not isinstance(item, dict): continue
                        code, theme = str(item.get("code", "")), str(item.get("theme", "")).strip()
                        if code not in stock_codes or not theme: continue
                        row = connection.execute("SELECT theme_id FROM themes WHERE theme_name = ?", (theme,)).fetchone()
                        if row: connection.execute("INSERT INTO stock_themes(stock_code, theme_id, custom_color) VALUES (?, ?, ?)", (code, row[0], item.get("color") or None))
                    connection.execute("DELETE FROM stock_aliases")
                    for item in aliases:
                        if isinstance(item, dict):
                            alias, code = str(item.get("alias", "")).strip(), str(item.get("code", ""))
                            if alias and code in stock_codes: connection.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?)", (alias, code))
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise SettingsBackupError("설정 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()
        if include_settings:
            self._import_assets(assets)

    def _import_assets(self, assets: Any) -> None:
        if not isinstance(assets, list):
            return
        root = self._database_path.parent.parent
        for item in assets:
            if not isinstance(item, dict):
                continue
            stored, content = str(item.get("path", "")), item.get("content")
            if not isinstance(content, str) or not self._is_asset_path(stored):
                continue
            try:
                destination = root / stored
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b64decode(content, validate=True))
            except (OSError, ValueError):
                continue

    @staticmethod
    def _is_asset_path(stored: str) -> bool:
        normalized = Path(stored).as_posix()
        return normalized.startswith(("data/strength_icons/", "data/near_high_icons/", "data/near_high_sounds/"))

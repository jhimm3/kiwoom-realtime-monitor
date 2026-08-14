"""사용자 설정·표 구성·테마를 이식 가능한 JSON 파일로 백업한다."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .database import DEFAULT_COLUMNS, DEFAULT_SETTINGS


class SettingsBackupError(ValueError):
    pass


class SettingsBackupService:
    FORMAT = "kiwoom-realtime-monitor-settings"
    VERSION = 1

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def export_to(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            settings = dict(connection.execute("SELECT key, value FROM settings").fetchall())
            columns = [
                {"name": name, "visible": bool(visible), "position": position, "width": width}
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
        finally:
            connection.close()
        document = {
            "format": self.FORMAT,
            "version": self.VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "settings": settings,
            "columns": columns,
            "themes": themes,
            "stock_themes": stock_themes,
            "aliases": aliases,
        }
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_from(self, path: Path) -> None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SettingsBackupError("설정 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") != self.VERSION:
            raise SettingsBackupError("이 프로그램에서 만든 설정 백업 파일이 아닙니다.")
        settings = document.get("settings")
        columns = document.get("columns")
        themes = document.get("themes")
        stock_themes = document.get("stock_themes")
        aliases = document.get("aliases")
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
                connection.executemany(
                    "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    [(key, str(value)) for key, value in settings.items() if key in DEFAULT_SETTINGS],
                )
                connection.executemany(
                    "UPDATE column_settings SET visible = ?, position = ?, width = ? WHERE column_name = ?",
                    [
                        (int(bool(item.get("visible"))), int(item.get("position", 0)), max(20, int(item.get("width", 100))), str(item["name"]))
                        for item in imported_columns
                    ],
                )
                connection.execute("DELETE FROM stock_themes")
                connection.execute("DELETE FROM themes")
                for item in themes:
                    if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                        continue
                    connection.execute(
                        "INSERT INTO themes(theme_name, default_color) VALUES (?, ?)",
                        (str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")),
                    )
                stock_codes = {code for (code,) in connection.execute("SELECT code FROM stocks")}
                for item in stock_themes:
                    if not isinstance(item, dict):
                        continue
                    code, theme = str(item.get("code", "")), str(item.get("theme", "")).strip()
                    if code not in stock_codes or not theme:
                        continue
                    row = connection.execute("SELECT theme_id FROM themes WHERE theme_name = ?", (theme,)).fetchone()
                    if row:
                        connection.execute(
                            "INSERT INTO stock_themes(stock_code, theme_id, custom_color) VALUES (?, ?, ?)",
                            (code, row[0], item.get("color") or None),
                        )
                connection.execute("DELETE FROM stock_aliases")
                for item in aliases:
                    if not isinstance(item, dict):
                        continue
                    alias, code = str(item.get("alias", "")).strip(), str(item.get("code", ""))
                    if alias and code in stock_codes:
                        connection.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?)", (alias, code))
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise SettingsBackupError("설정 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()

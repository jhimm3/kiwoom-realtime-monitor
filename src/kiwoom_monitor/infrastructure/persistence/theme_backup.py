"""테마 DB만 별도로 이식하는 JSON 백업."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


class ThemeBackupError(ValueError):
    pass


class ThemeBackupService:
    FORMAT = "kiwoom-realtime-monitor-theme-db"
    VERSION = 1

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def export_to(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            document = {
                "format": self.FORMAT,
                "version": self.VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"),
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
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_from(self, path: Path) -> None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
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

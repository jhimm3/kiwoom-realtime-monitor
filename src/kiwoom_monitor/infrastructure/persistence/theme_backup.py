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

    def __init__(self, database_path: Path, profile_name: str | None = None) -> None:
        self._database_path = database_path
        self._profile_name = profile_name

    def export_to(self, path: Path) -> None:
        if self._profile_name is not None:
            self._export_profile(path)
            return
        if self._has_profile_schema():
            self._export_all_profiles(path)
            return
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
        if self._profile_name is not None:
            self._import_profile(path)
            return
        try:
            preview = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 읽을 수 없습니다.") from error
        if isinstance(preview, dict) and isinstance(preview.get("profiles"), list):
            self._import_all_profiles(preview)
            return
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

    def _has_profile_schema(self) -> bool:
        connection = sqlite3.connect(self._database_path)
        try:
            return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='theme_profiles'").fetchone() is not None
        finally:
            connection.close()

    def _export_all_profiles(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            profiles = []
            for profile_id, name in connection.execute("SELECT profile_id, profile_name FROM theme_profiles ORDER BY profile_name COLLATE NOCASE"):
                profiles.append({
                    "name": name,
                    "themes": [{"name": theme, "color": color} for theme, color in connection.execute("SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (profile_id,))],
                    "stock_themes": [{"code": code, "theme": theme, "color": color} for code, theme, color in connection.execute("SELECT stock_code, theme_name, custom_color FROM profile_stock_themes WHERE profile_id=? ORDER BY stock_code, theme_name", (profile_id,))],
                })
            document = {
                "format": self.FORMAT, "version": self.VERSION, "created_at": datetime.now().isoformat(timespec="seconds"),
                "profiles": profiles,
                "aliases": [{"alias": alias, "code": code} for alias, code in connection.execute("SELECT alias, stock_code FROM stock_aliases ORDER BY alias")],
                "stock_catalog": [{"code": code, "name": name, "market": market} for code, name, market in connection.execute("SELECT code, name, market FROM stocks ORDER BY code")],
            }
        finally:
            connection.close()
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def _import_all_profiles(self, document: dict[object, object]) -> None:
        if document.get("format") != self.FORMAT or document.get("version") != self.VERSION:
            raise ThemeBackupError("이 프로그램에서 만든 테마 DB 백업 파일이 아닙니다.")
        profiles = document.get("profiles")
        if not isinstance(profiles, list):
            raise ThemeBackupError("테마 프로필 백업 형식이 올바르지 않습니다.")
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
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
                            connection.execute("INSERT INTO stocks(code, name, market) VALUES (?, ?, ?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=CURRENT_TIMESTAMP", (code, name, market))
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
                if not connection.execute("SELECT 1 FROM theme_profiles").fetchone():
                    connection.execute("INSERT INTO theme_profiles(profile_name) VALUES ('기본 테마')")
                codes = {str(row[0]) for row in connection.execute("SELECT code FROM stocks")}
                connection.execute("DELETE FROM stock_aliases")
                aliases = document.get("aliases", [])
                if isinstance(aliases, list):
                    for item in aliases:
                        if isinstance(item, dict) and str(item.get("alias", "")).strip() and str(item.get("code", "")) in codes:
                            connection.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?)", (str(item["alias"]).strip(), str(item["code"])))
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ThemeBackupError("테마 프로필 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()

    def _export_profile(self, path: Path) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            profile = connection.execute("SELECT profile_id, profile_name FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (self._profile_name,)).fetchone()
            if profile is None:
                raise ThemeBackupError("내보낼 테마 프로필을 찾을 수 없습니다.")
            profile_id, profile_name = profile
            document = {
                "format": self.FORMAT, "version": self.VERSION, "created_at": datetime.now().isoformat(timespec="seconds"), "profile": profile_name,
                "themes": [{"name": name, "color": color} for name, color in connection.execute("SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (profile_id,))],
                "stock_themes": [{"code": code, "theme": theme, "color": color} for code, theme, color in connection.execute("SELECT stock_code, theme_name, custom_color FROM profile_stock_themes WHERE profile_id=? ORDER BY stock_code, theme_name", (profile_id,))],
                "aliases": [{"alias": alias, "code": code} for alias, code in connection.execute("SELECT alias, stock_code FROM stock_aliases ORDER BY alias")],
                "stock_catalog": [{"code": code, "name": name, "market": market} for code, name, market in connection.execute("SELECT code, name, market FROM stocks ORDER BY code")],
            }
        finally:
            connection.close()
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def _import_profile(self, path: Path) -> None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
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
                for item in themes:
                    if isinstance(item, dict) and str(item.get("name", "")).strip():
                        connection.execute("INSERT INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)", (profile_id, str(item["name"]).strip(), str(item.get("color") or "#DCE6F1")))
                for item in stock_themes:
                    if isinstance(item, dict) and str(item.get("code", "")).strip() and str(item.get("theme", "")).strip():
                        connection.execute("INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) VALUES (?, ?, ?, ?)", (profile_id, str(item["code"]), str(item["theme"]).strip(), item.get("color") or None))
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ThemeBackupError("테마 DB 백업 파일을 적용할 수 없습니다.") from error
        finally:
            connection.close()

"""사용자 설정·표 구성·테마를 이식 가능한 JSON 파일로 백업한다."""

from __future__ import annotations

import json
import sqlite3
from base64 import b64decode, b64encode
from datetime import datetime
from pathlib import Path
from typing import Any

from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig,
    NewsAISettings,
    NewsFilterSettings,
    OfficialNewsSettings,
)

from .database import DEFAULT_COLUMNS, DEFAULT_SETTINGS


class SettingsBackupError(ValueError):
    pass


class SettingsBackupService:
    FORMAT = "kiwoom-realtime-monitor-settings"
    VERSION = 3
    # 백업 파일은 사용자가 고른 아이콘/알림 소리만 포함한다. 복원 시에는
    # 허용된 폴더와 확장자, 크기를 모두 다시 확인한다.
    MAX_BACKUP_DOCUMENT_BYTES = 32 * 1024 * 1024
    MAX_BACKUP_ASSET_TOTAL_BYTES = 20 * 1024 * 1024
    _ASSET_RULES = (
        ("data/strength_icons", frozenset({".png"}), 2 * 1024 * 1024),
        ("data/near_high_icons", frozenset({".png"}), 2 * 1024 * 1024),
        ("data/near_high_sounds", frozenset({".wav", ".mp3", ".ogg", ".m4a"}), 5 * 1024 * 1024),
    )

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
        total_size = 0
        for stored in sorted(value for value in paths if value):
            resolved = self._resolve_asset_path(root, stored)
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

    def import_from(self, path: Path, include_settings: bool = True, include_themes: bool = True, excluded_setting_keys: frozenset[str] = frozenset(), include_column_widths: bool = True, include_column_layout: bool = True) -> None:
        try:
            if path.stat().st_size > self.MAX_BACKUP_DOCUMENT_BYTES:
                raise SettingsBackupError("설정 백업 파일이 너무 큽니다.")
            document = json.loads(path.read_text(encoding="utf-8"))
        except SettingsBackupError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise SettingsBackupError("설정 백업 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or document.get("format") != self.FORMAT or document.get("version") not in {1, 2, self.VERSION}:
            raise SettingsBackupError("이 프로그램에서 만든 설정 백업 파일이 아닙니다.")
        settings = document.get("settings", {})
        columns = document.get("columns", [])
        themes = document.get("themes", [])
        stock_themes = document.get("stock_themes", [])
        aliases = document.get("aliases", [])
        stock_catalog = document.get("stock_catalog", [])
        assets = document.get("assets", [])
        news_shortcuts = document.get("news_shortcuts")
        news_settings = document.get("news_settings")
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
                    elif include_column_layout:
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
            self._import_news_settings(news_settings, news_shortcuts)

    def _import_news_settings(self, raw_settings: Any, raw_shortcuts: Any) -> None:
        if not isinstance(raw_settings, dict) and not isinstance(raw_shortcuts, list):
            return
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
        columns = tuple(
            value for value in filter_data.get("visible_columns", current_filter.visible_columns)
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
        )
        provider = str(ai_data.get("provider", current_ai.provider))
        request_mode = str(ai_data.get("request_mode", current_ai.request_mode))
        news_ai = NewsAISettings(
            provider if provider in {"none", "openai", "gemini", "claude"} else current_ai.provider,
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

    def _import_assets(self, assets: Any) -> None:
        if not isinstance(assets, list):
            return
        root = self._database_path.parent.parent
        total_size = 0
        for item in assets:
            if not isinstance(item, dict):
                continue
            stored, content = str(item.get("path", "")), item.get("content")
            resolved = self._resolve_asset_path(root, stored)
            if not isinstance(content, str) or resolved is None:
                continue
            destination, maximum_size = resolved
            # Base64는 원본보다 약 4/3배 크다. 먼저 길이를 확인해 불필요한
            # 대용량 디코딩을 막는다.
            if len(content) > ((maximum_size + 2) // 3) * 4:
                continue
            try:
                data = b64decode(content, validate=True)
                if len(data) > maximum_size or total_size + len(data) > self.MAX_BACKUP_ASSET_TOTAL_BYTES:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                total_size += len(data)
            except (OSError, ValueError):
                continue

    @classmethod
    def _resolve_asset_path(cls, root: Path, stored: str) -> tuple[Path, int] | None:
        """백업 자산의 실제 대상 경로와 허용 크기를 안전하게 반환한다."""
        relative = Path(stored)
        if not stored or relative.is_absolute():
            return None
        normalized = relative.as_posix()
        root = root.resolve()
        candidate = (root / relative).resolve()
        for directory, extensions, maximum_size in cls._ASSET_RULES:
            allowed_directory = (root / directory).resolve()
            try:
                candidate.relative_to(allowed_directory)
            except ValueError:
                continue
            if normalized.startswith(f"{directory}/") and candidate.suffix.casefold() in extensions:
                return candidate, maximum_size
        return None

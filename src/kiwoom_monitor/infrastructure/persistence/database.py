from __future__ import annotations

import sqlite3
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository


DEFAULT_SETTINGS = {
    "refresh_interval_seconds": "30",
    "ui_mode": "responsive",
    "strength_interest": "0.5", "strength_caution": "1.0", "strength_fire": "2.0", "strength_show_icon": "1",
    "near_high_threshold_percent": "1.0", "near_high_alert_enabled": "1", "theme_custom_separators": "",
    "ui_font_size": "0", "ui_row_height": "0", "theme_badge_font_size": "0", "theme_badge_padding": "2", "high_distance_period": "250",
}

DEFAULT_COLUMNS = (
    ("rank", 1, 0, 60),
    ("stock", 1, 1, 150),
    ("themes", 1, 2, 240),
    ("change_rate", 1, 3, 100),
    ("strength_1m", 1, 4, 100),
    ("current_price", 1, 5, 110),
    ("trade_value_1m", 0, 6, 100), ("trade_value_5m", 0, 7, 100), ("trade_value_60m", 0, 8, 100), ("trade_value_day", 0, 9, 100),
    ("strength_5m", 0, 10, 100), ("strength_60m", 0, 11, 100), ("strength_day", 0, 12, 100), ("new_high", 0, 13, 100), ("high_distance", 0, 14, 100),
)


class Database:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self.settings = SettingsRepository(database_path)

    def initialize(self) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                self._apply_v1(connection)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            connection.executemany("INSERT OR IGNORE INTO column_settings(column_name, visible, position, width) VALUES (?, ?, ?, ?)", DEFAULT_COLUMNS)
            connection.executemany("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", DEFAULT_SETTINGS.items())
            connection.execute("CREATE TABLE IF NOT EXISTS stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            self._add_stock_columns(connection)
            connection.execute("CREATE TABLE IF NOT EXISTS stock_aliases (alias TEXT PRIMARY KEY, stock_code TEXT NOT NULL, FOREIGN KEY(stock_code) REFERENCES stocks(code))")
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS themes (theme_id INTEGER PRIMARY KEY, theme_name TEXT NOT NULL UNIQUE, default_color TEXT NOT NULL DEFAULT '#DCE6F1');
            CREATE TABLE IF NOT EXISTS stock_themes (stock_code TEXT NOT NULL, theme_id INTEGER NOT NULL, custom_color TEXT, PRIMARY KEY(stock_code, theme_id), FOREIGN KEY(stock_code) REFERENCES stocks(code), FOREIGN KEY(theme_id) REFERENCES themes(theme_id));
            """)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _apply_v1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE column_settings (
                column_name TEXT PRIMARY KEY,
                visible INTEGER NOT NULL CHECK (visible IN (0, 1)),
                position INTEGER NOT NULL,
                width INTEGER NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            DEFAULT_SETTINGS.items(),
        )
        connection.executemany(
            "INSERT INTO column_settings(column_name, visible, position, width) VALUES (?, ?, ?, ?)",
            DEFAULT_COLUMNS,
        )

    @staticmethod
    def _add_stock_columns(connection: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(stocks)")}
        for name in ("market_cap", "float_ratio", "circulating_market_cap"):
            if name not in existing:
                connection.execute(f"ALTER TABLE stocks ADD COLUMN {name} REAL")

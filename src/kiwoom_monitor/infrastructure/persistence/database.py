from __future__ import annotations

import sqlite3
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository


DEFAULT_SETTINGS = {
    "refresh_interval_seconds": "30",
    "rank_query_type": "5",
    "rank_row_odd_color": "#FFFFFF",
    "rank_row_even_color": "#F2F2F2",
    "rank_changed_row_color": "#E2F0D9",
    "rank_changed_highlight_seconds": "2.00",
    "rank_changed_highlight_enabled": "1",
    "ui_mode": "responsive",
    "strength_1m_interest": "0.05", "strength_1m_caution": "0.1", "strength_1m_fire": "0.2",
    "strength_5m_interest": "2.5", "strength_5m_caution": "5.0", "strength_5m_fire": "10.0",
    "strength_60m_interest": "30.0", "strength_60m_caution": "60.0", "strength_60m_fire": "120.0",
    "strength_day_interest": "195.0", "strength_day_caution": "390.0", "strength_day_fire": "780.0",
    "strength_display_mode": "live",
    "trade_display_1m_mode": "live", "trade_display_5m_mode": "live", "trade_display_60m_mode": "live", "trade_display_day_mode": "live",
    "trade_value_alert_enabled": "1", "trade_value_1m_alert_eok": "40.0", "trade_value_5m_alert_eok": "200.0", "trade_value_60m_alert_eok": "2400.0", "trade_value_day_alert_eok": "57600.0",
    "strength_show_icon": "1",
    "strength_icon_interest": "👀", "strength_icon_caution": "⚠️", "strength_icon_fire": "🔥",
    "strength_icon_interest_image": "", "strength_icon_caution_image": "", "strength_icon_fire_image": "",
    "near_high_interest_percent": "15.0", "near_high_caution_percent": "10.0", "near_high_fire_percent": "5.0", "near_high_row_alert_level": "interest", "near_high_alert_enabled": "1", "theme_custom_separators": "", "theme_text_heading_marker": "🔥", "theme_import_exclusions": "",
    "near_high_show_icon": "1", "near_high_icon_interest": "🔎", "near_high_icon_caution": "⚠️", "near_high_icon_fire": "🔥",
    "near_high_icon_interest_image": "", "near_high_icon_caution_image": "", "near_high_icon_fire_image": "",
    "near_high_sound_enabled": "1", "near_high_sound_interest": "data/near_high_sounds/interest.mp3", "near_high_sound_caution": "data/near_high_sounds/caution.mp3", "near_high_sound_fire": "data/near_high_sounds/fire.mp3",
    "ui_font_size": "0", "ui_row_height": "0", "theme_badge_enabled": "1", "theme_badge_font_size": "0", "theme_badge_padding": "2", "high_distance_period": "250", "window_width": "1160", "window_height": "720", "settings_dialog_width": "680", "settings_dialog_height": "650",
    "decimal_change_rate": "2", "decimal_trade_value": "2", "decimal_strength": "4", "decimal_high_distance": "2",
    "market_cap_highlight_low_eok": "10000",
    "market_cap_highlight_middle_eok": "50000",
    "market_cap_highlight_high_eok": "100000",
    "market_cap_highlight_enabled": "1",
    "market_cap_highlight_badge_enabled": "0",
    "market_cap_highlight_low_color": "#0070C0",
    "market_cap_highlight_middle_color": "#C55A11",
    "market_cap_highlight_high_color": "#C00000",
    "market_cap_highlight_low_badge_color": "#DCE6F1",
    "market_cap_highlight_middle_badge_color": "#FCE4D6",
    "market_cap_highlight_high_badge_color": "#F4CCCC",
    "show_server_clock": "1",
    "theme_trade_summary_enabled": "1",
    "theme_trade_summary_period": "day",
    "theme_trade_summary_excluded_stocks": "",
    "theme_trade_summary_excluded_enabled": "1",
    "theme_image_import_dir": "",
    "theme_excel_import_dir": "",
    "theme_manager_stock_column_width": "170",
    "theme_manager_theme_column_width": "330",
    "google_drive_auto_download": "1",
    "google_drive_auto_upload": "1",
    "google_drive_auto_upload_on_exit": "0",
    "google_drive_sync_target": "both",
    "google_drive_unsynced_changes": "0",
    "google_drive_local_changed_at": "",
    "google_drive_last_upload_success_at": "",
    "krx_stock_catalog_date": "",
    "krx_stock_catalog_format_version": "3",
}

DEFAULT_COLUMNS = (
    ("rank", 1, 0, 32),
    ("stock", 1, 1, 142),
    ("themes", 1, 2, 223),
    ("change_rate", 1, 3, 52),
    ("current_price", 1, 4, 59),
    ("trade_value_1m", 1, 5, 55),
    ("strength_1m", 1, 6, 61),
    ("trade_value_5m", 0, 7, 0),
    ("strength_5m", 0, 8, 0),
    ("trade_value_60m", 0, 9, 0),
    ("strength_60m", 0, 10, 0),
    ("trade_value_day", 0, 11, 0),
    ("strength_day", 0, 12, 0),
    ("high_distance", 1, 13, 79),
    ("new_high_price", 1, 14, 67),
    ("market_cap", 1, 15, 101),
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
            removed_new_high = connection.execute("DELETE FROM column_settings WHERE column_name = 'new_high'").rowcount
            if removed_new_high:
                connection.execute("UPDATE column_settings SET position = position - 1 WHERE position > 13")
            connection.executemany("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", DEFAULT_SETTINGS.items())
            connection.execute("CREATE TABLE IF NOT EXISTS stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            self._add_stock_columns(connection)
            connection.execute("CREATE TABLE IF NOT EXISTS stock_aliases (alias TEXT PRIMARY KEY, stock_code TEXT NOT NULL, FOREIGN KEY(stock_code) REFERENCES stocks(code))")
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS themes (theme_id INTEGER PRIMARY KEY, theme_name TEXT NOT NULL UNIQUE, default_color TEXT NOT NULL DEFAULT '#DCE6F1');
            CREATE TABLE IF NOT EXISTS stock_themes (stock_code TEXT NOT NULL, theme_id INTEGER NOT NULL, custom_color TEXT, PRIMARY KEY(stock_code, theme_id), FOREIGN KEY(stock_code) REFERENCES stocks(code), FOREIGN KEY(theme_id) REFERENCES themes(theme_id));
            CREATE TABLE IF NOT EXISTS new_high_snapshot (period INTEGER NOT NULL, stock_code TEXT NOT NULL, PRIMARY KEY(period, stock_code), FOREIGN KEY(stock_code) REFERENCES stocks(code));
            CREATE TABLE IF NOT EXISTS new_high_snapshot_meta (period INTEGER PRIMARY KEY, checked_at TEXT NOT NULL);
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
        for name in ("market_cap", "float_ratio", "circulating_market_cap", "high_250_price", "fundamentals_updated_at", "nxt_enabled", "nxt_checked_at", "last_price", "last_price_updated_at"):
            if name not in existing:
                connection.execute(f"ALTER TABLE stocks ADD COLUMN {name} REAL")

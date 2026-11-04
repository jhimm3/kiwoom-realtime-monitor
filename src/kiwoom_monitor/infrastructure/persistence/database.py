from __future__ import annotations

import sqlite3
import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_schema import (
    create_market_data_metadata_table,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)
from kiwoom_monitor.infrastructure.persistence.stock_aliases import seed_known_stock_aliases


DEFAULT_SETTINGS = {
    "refresh_interval_seconds": "30",
    "rank_query_type": "5",
    "rank_row_odd_color": "#FFFFFF",
    "rank_row_even_color": "#F2F2F2",
    "rank_changed_row_color": "#E2F0D9",
    "rank_changed_highlight_seconds": "1.00",
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
    "near_high_sound_enabled": "1", "near_high_sound_cooldown_seconds": "0", "near_high_sound_interest": "data/near_high_sounds/interest.mp3", "near_high_sound_caution": "data/near_high_sounds/caution.mp3", "near_high_sound_fire": "data/near_high_sounds/fire.mp3",
    "ui_font_size": "0", "ui_row_height": "0", "theme_badge_enabled": "1", "theme_badge_font_size": "0", "theme_badge_padding": "2", "high_distance_period": "250", "high_header_cycle_periods": "5,20,250,historical", "window_width": "1160", "window_height": "720", "window_x": "", "window_y": "", "settings_dialog_width": "680", "settings_dialog_height": "650",
    "decimal_change_rate": "2", "decimal_trade_value": "2", "decimal_strength": "4", "decimal_high_distance": "2",
    "market_cap_highlight_low_eok": "10000",
    "market_cap_highlight_middle_eok": "50000",
    "market_cap_highlight_high_eok": "100000",
    "market_cap_highlight_enabled": "1",
    "market_cap_highlight_badge_enabled": "0",
    "upper_limit_highlight_enabled": "1",
    "market_cap_highlight_low_color": "#0070C0",
    "market_cap_highlight_middle_color": "#C55A11",
    "market_cap_highlight_high_color": "#C00000",
    "market_cap_highlight_low_badge_color": "#DCE6F1",
    "market_cap_highlight_middle_badge_color": "#FCE4D6",
    "market_cap_highlight_high_badge_color": "#F4CCCC",
    "show_server_clock": "1",
    "theme_trade_summary_enabled": "1",
    "theme_trade_summary_period": "day",
    "theme_group_sort_basis": "all",
    "theme_trade_summary_excluded_stocks": "",
    "theme_trade_summary_excluded_enabled": "1",
    "theme_image_import_dir": "",
    "theme_image_import_mode": "theme_column",
    "theme_image_import_theme_header": "테마",
    "theme_image_import_custom_separators": "",
    "theme_image_import_exclusions": "",
    "theme_new_import_custom_separators": "",
    "theme_new_import_exclusions": "",
    "theme_text_import_custom_separators": "",
    "theme_text_import_exclusions": "",
    "theme_text_include_subcategories": "0",
    "theme_excel_import_custom_separators": "",
    "theme_excel_import_exclusions": "",
    "theme_active_profile": "기본 테마",
    "theme_profiles_initialized": "0",
    "theme_excel_import_dir": "",
    "theme_manager_stock_column_width": "170",
    "theme_manager_theme_column_width": "330",
    "google_drive_auto_download": "1",
    "google_drive_auto_upload": "1",
    "google_drive_auto_upload_on_exit": "0",
    "google_drive_sync_target": "both",
    "auto_update_check": "0",
    "google_drive_unsynced_changes": "0",
    "google_drive_local_changed_at": "",
    "google_drive_last_upload_success_at": "",
    "krx_stock_catalog_date": "",
    "krx_stock_catalog_format_version": "3",
    "kind_name_history_sync_date": "",
    "daily_high_adjusted_basis_version": "0",
    "historical_high_adjusted_basis_version": "0",
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


MAIN_SCHEMA_VERSION = 8
ROLE_SETTINGS_SCHEMA_VERSION = 1
ROLE_SETTINGS_TABLES = {
    "settings": ("key", "value"),
    "column_settings": ("column_name", "visible", "position", "width"),
    "central_setting_versions": ("setting_key", "updated_at"),
    "central_column_setting_versions": ("column_name", "updated_at"),
}
_ROLE_SETTINGS_SQL = {
    table: f"CREATE TABLE {table} (" + ",".join(
        name + " BLOB" + (" PRIMARY KEY" if index == 0 else "")
        for index, name in enumerate(columns)
    ) + ")"
    for table, columns in ROLE_SETTINGS_TABLES.items()
}
_ROLE_SETTINGS_SQL["role_settings_schema"] = (
    "CREATE TABLE role_settings_schema (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
    "schema_version INTEGER NOT NULL,role TEXT NOT NULL,seed_kind TEXT NOT NULL,"
    "seed_sha256 TEXT NOT NULL,preservation_receipt_json TEXT)"
)


def _role_settings_seed(rows: dict[str, list[tuple]], *, tables=ROLE_SETTINGS_TABLES) -> tuple[dict, str]:
    """Freeze/validate initialization input without changing SQLite primitives."""
    if not isinstance(rows, dict) or set(rows) != set(tables):
        raise ValueError("invalid role settings relations")
    frozen, digest = {}, hashlib.sha256()
    for table, columns in tables.items():
        frozen[table] = tuple(rows[table])
        digest.update(table.encode("ascii") + b"\0")
        for row in frozen[table]:
            if not isinstance(row, tuple) or len(row) != len(columns):
                raise ValueError("invalid role settings row")
            for value in row:
                kind = type(value)
                if value is None:
                    encoded = b"N"
                elif kind is str:
                    encoded = b"T" + value.encode("utf-8")
                elif kind is bytes:
                    encoded = b"B" + value
                elif kind is int and -(2**63) <= value < 2**63:
                    encoded = b"I" + str(value).encode("ascii")
                elif kind is float and not math.isnan(value):
                    encoded = b"R" + value.hex().encode("ascii")
                else:
                    raise ValueError("unsupported role settings primitive")
                digest.update(len(encoded).to_bytes(8, "big") + encoded)
        digest.update(b"\0")
    return frozen, digest.hexdigest()


# Shared current SQL, with no settings or legacy theme migration in the role cache.
_QUERY_CACHE_SQL = """
            CREATE TABLE IF NOT EXISTS new_high_snapshot (period INTEGER NOT NULL, stock_code TEXT NOT NULL, PRIMARY KEY(period, stock_code), FOREIGN KEY(stock_code) REFERENCES stocks(code));
            CREATE TABLE IF NOT EXISTS new_high_snapshot_meta (period INTEGER PRIMARY KEY, checked_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS minute_bars (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                minute TEXT NOT NULL,
                open_price INTEGER NOT NULL,
                high_price INTEGER NOT NULL,
                low_price INTEGER NOT NULL,
                close_price INTEGER NOT NULL,
                volume INTEGER NOT NULL,
                trade_value_eok REAL,
                PRIMARY KEY(trade_date, stock_code, minute)
            );
            CREATE INDEX IF NOT EXISTS idx_minute_bars_date_code ON minute_bars(trade_date, stock_code);
            CREATE TABLE IF NOT EXISTS market_index_minute_bars (
                trade_date TEXT NOT NULL, market TEXT NOT NULL, minute TEXT NOT NULL,
                open_value REAL NOT NULL, high_value REAL NOT NULL,
                low_value REAL NOT NULL, close_value REAL NOT NULL,
                trade_value_eok REAL,
                PRIMARY KEY(trade_date, market, minute)
            );
            CREATE INDEX IF NOT EXISTS idx_market_index_bars_date_market
                ON market_index_minute_bars(trade_date, market, minute);
            CREATE TABLE IF NOT EXISTS top20_trade_value_index (
                minute TEXT PRIMARY KEY,
                trade_date TEXT NOT NULL,
                trade_value_eok REAL NOT NULL,
                stock_codes TEXT NOT NULL,
                stock_count INTEGER NOT NULL,
                capture_state TEXT NOT NULL DEFAULT 'realtime_complete',
                kospi_trade_value_eok REAL NOT NULL DEFAULT 0,
                kosdaq_trade_value_eok REAL NOT NULL DEFAULT 0,
                unknown_trade_value_eok REAL NOT NULL DEFAULT 0,
                kospi_stock_count INTEGER NOT NULL DEFAULT 0,
                kosdaq_stock_count INTEGER NOT NULL DEFAULT 0,
                unknown_stock_count INTEGER NOT NULL DEFAULT 0,
                cohort_segments TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_top20_trade_value_index_date
                ON top20_trade_value_index(trade_date, minute);
            CREATE TABLE IF NOT EXISTS top20_statistics_daily_cache (
                trade_date TEXT PRIMARY KEY,
                summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_index_daily_bars (
                trade_date TEXT NOT NULL, market TEXT NOT NULL,
                open_value REAL NOT NULL, high_value REAL NOT NULL,
                low_value REAL NOT NULL, close_value REAL NOT NULL,
                volume INTEGER NOT NULL DEFAULT 0, trade_value_eok REAL,
                PRIMARY KEY(trade_date, market)
            );
            CREATE TABLE IF NOT EXISTS minute_history_sync_log (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                bar_count INTEGER NOT NULL,
                PRIMARY KEY(trade_date, stock_code)
            );
            CREATE TABLE IF NOT EXISTS daily_bars (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                high_price INTEGER NOT NULL,
                trade_value_eok REAL,
                close_price INTEGER,
                PRIMARY KEY(stock_code, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date ON daily_bars(stock_code, trade_date);
            CREATE TABLE IF NOT EXISTS daily_bar_sync_log (
                stock_code TEXT PRIMARY KEY,
                synced_on TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS intraday_highs (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                high_price INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(trade_date, stock_code)
            );
            CREATE INDEX IF NOT EXISTS idx_intraday_highs_date_code ON intraday_highs(trade_date, stock_code);
            CREATE TABLE IF NOT EXISTS market_data_finalization_log (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                finalized_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(trade_date, stock_code)
            );
            CREATE TABLE IF NOT EXISTS market_data_unconfirmed_log (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                missing_parts TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(trade_date, stock_code)
            );
            CREATE TABLE IF NOT EXISTS historical_high_evidence (
                stock_code TEXT NOT NULL,
                period TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                high_price INTEGER NOT NULL,
                adjustment_types TEXT NOT NULL DEFAULT '',
                adjustment_rate TEXT NOT NULL DEFAULT '',
                adjustment_event TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(stock_code, period, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_historical_high_evidence_code_date
                ON historical_high_evidence(stock_code, trade_date);
"""

_THEME_PROFILE_SQL = """
            CREATE TABLE IF NOT EXISTS theme_profiles (
                profile_id INTEGER PRIMARY KEY,
                profile_name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );
            CREATE TABLE IF NOT EXISTS profile_themes (
                profile_id INTEGER NOT NULL,
                theme_name TEXT NOT NULL COLLATE NOCASE,
                default_color TEXT NOT NULL DEFAULT '#DCE6F1',
                PRIMARY KEY(profile_id, theme_name),
                FOREIGN KEY(profile_id) REFERENCES theme_profiles(profile_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS profile_stock_themes (
                profile_id INTEGER NOT NULL,
                stock_code TEXT NOT NULL,
                theme_name TEXT NOT NULL COLLATE NOCASE,
                custom_color TEXT,
                PRIMARY KEY(profile_id, stock_code, theme_name),
                FOREIGN KEY(profile_id, theme_name) REFERENCES profile_themes(profile_id, theme_name) ON DELETE CASCADE
            );
"""


QUERY_ROLE_SCHEMA_VERSION = 2
FULL_ROLE_SCHEMA_VERSION = 1
_QUERY_ROLE_SCHEMA_SQL = (
    "CREATE TABLE role_settings_schema (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
    "schema_version INTEGER NOT NULL,role TEXT NOT NULL,seed_kind TEXT NOT NULL,"
    "seed_sha256 TEXT NOT NULL,preservation_receipt_json TEXT,owned_layout_sha256 TEXT NOT NULL)"
)
_FULL_ROLE_SCHEMA_SQL = _QUERY_ROLE_SCHEMA_SQL[:-1] + (
    ",store_kind TEXT NOT NULL DEFAULT 'full' CHECK(store_kind='full'))"
)
_LEGACY_THEME_SQL = """
CREATE TABLE IF NOT EXISTS themes (theme_id INTEGER PRIMARY KEY, theme_name TEXT NOT NULL UNIQUE, default_color TEXT NOT NULL DEFAULT '#DCE6F1');
CREATE TABLE IF NOT EXISTS stock_themes (stock_code TEXT NOT NULL, theme_id INTEGER NOT NULL, custom_color TEXT, PRIMARY KEY(stock_code, theme_id), FOREIGN KEY(stock_code) REFERENCES stocks(code), FOREIGN KEY(theme_id) REFERENCES themes(theme_id));
"""
_QUERY_ROLE_TABLES = {'central_column_setting_versions', 'central_setting_versions', 'column_settings', 'daily_bar_sync_log', 'daily_bars', 'historical_high_evidence', 'intraday_highs', 'kind_name_disclosures', 'market_data_finalization_log', 'market_data_observation_meta', 'market_data_unconfirmed_log', 'market_index_daily_bars', 'market_index_minute_bars', 'minute_bars', 'minute_history_sync_log', 'new_high_snapshot', 'new_high_snapshot_meta', 'profile_stock_themes', 'profile_theme_name_decisions', 'profile_theme_suggestions', 'profile_themes', 'role_settings_schema', 'settings', 'stock_aliases', 'stock_name_history', 'stocks', 'theme_profiles', 'top20_statistics_daily_cache', 'top20_trade_value_index'}
_QUERY_ROLE_INDEXES = set(re.findall(r"CREATE INDEX IF NOT EXISTS\s+(\w+)", _QUERY_CACHE_SQL)) | {"idx_profile_theme_suggestions_status"}


def _owned_role_layout_digest(objects):
    encoded = json.dumps(sorted(objects), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Explicit desktop installers share the original native projections.
# Settings-only initialization retains its original four-relation fingerprint.
QUERY_STATE_TABLES = {
    **ROLE_SETTINGS_TABLES,
    "theme_profiles": ("profile_id", "profile_name"),
    "profile_themes": ("profile_id", "theme_name", "default_color"),
    "profile_stock_themes": ("profile_id", "stock_code", "theme_name", "custom_color"),
    "profile_theme_name_decisions": (
        "profile_id", "decision_kind", "source_name", "target_name", "decision_source", "updated_at"),
    "profile_theme_suggestions": (
        "profile_id", "stock_code", "news_identity", "raw_theme_name", "evidence", "confidence",
        "provider", "model", "body_hash", "analyzed_at", "status", "reviewed_theme_names",
        "reviewed_at", "article_title", "article_published_at", "article_url"),
    "stocks": ("code", "name", "market", "updated_at"),
    "stock_aliases": ("alias", "stock_code"),
}


def _current_state_input(role, state, *, layout="query"):
    """Freeze one verified selection/target plan before acquiring a native DB."""
    if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", role):
        raise ValueError("explicit query role identity is required")
    if (not isinstance(state, dict) or not isinstance(state.get("closure"), dict)
            or state["closure"].get("state") != "CLOSED"):
        raise ValueError("one closed current-state selection is required")
    if state.get("target_layout", "query") != layout:
        raise ValueError("current-state target layout must be selected explicitly")
    rows, seed_sha256 = _role_settings_seed(state.get("rows"), tables=QUERY_STATE_TABLES)
    binding = state.get("binding")
    labels = ("source_instance", "source_owner", "operation_id", "target_role")
    hashes = ("manifest_sha256", "selection_sha256")
    counts = ("captured", "selected", "unselected", "unresolved", "reference_pending", "retained_source")
    if (not isinstance(binding, dict) or set(binding) != {*labels, *hashes, *counts}
            or binding.get("target_role") != role
            or any(not isinstance(binding.get(k), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", binding[k]) for k in labels)
            or any(not isinstance(binding.get(k), str) or not re.fullmatch(r"[0-9a-f]{64}", binding[k]) for k in hashes)
            or any(type(binding.get(k)) is not int or binding[k] < 0 for k in counts)
            or binding["selected"] != sum(map(len, rows.values())) or not binding["selected"]
            or binding["captured"] != binding["selected"] + binding["unselected"]):
        raise ValueError("current-state source/selection binding conflict")
    for key in ("target_id", "target_operation_id"):
        if not isinstance(state.get(key), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", state[key]):
            raise ValueError("explicit current-state target identity is required")
    present, layouts = state.get("source_relations"), state.get("source_layouts")
    if not isinstance(present, dict) or not isinstance(layouts, dict) or set(present) != set(rows) or set(layouts) != set(rows):
        raise ValueError("current-state source relation/layout binding conflict")
    for table, columns in QUERY_STATE_TABLES.items():
        source_layout = layouts[table]
        if type(present[table]) is not bool or (not present[table] and (rows[table] or source_layout is not None)):
            raise ValueError("current-state source presence conflict")
        if not present[table]:
            continue
        source_columns = source_layout.get("columns") if isinstance(source_layout, dict) else None
        pk = (0, 1, 2, 3) if table in {"profile_theme_name_decisions", "profile_theme_suggestions"} else (0, 1, 2) if table == "profile_stock_themes" else (0, 1) if table == "profile_themes" else (0,)
        nocase = {1} if table == "profile_themes" else {2} if table == "profile_stock_themes" else {2, 3} if table == "profile_theme_name_decisions" else {3} if table == "profile_theme_suggestions" else set()
        native_key = [[columns[i], "NOCASE" if i in nocase else "BINARY"] for i in pk]
        if (not isinstance(source_columns, list) or not all(type(name) is str for name in source_columns)
                or len(source_columns) != len(set(source_columns))
                or (not set(columns) <= set(source_columns) if table == "stocks" else source_columns != list(columns))
                or source_layout.get("primary_key") != [columns[i] for i in pk]
                or not isinstance(source_layout.get("foreign_keys"), list)
                or not isinstance(source_layout.get("unique_keys"), list)
                or (source_layout.get("profile_id_type") != "INTEGER" or [["profile_name", "NOCASE"]] not in source_layout["unique_keys"]
                    if table == "theme_profiles" else native_key not in source_layout["unique_keys"])):
            raise ValueError("unsupported current-state source layout")
    # No values are copied into the receipt. This is immutable source/target
    # identity; editable rows are never compared with the seed on reentry.
    plan = dict(format=f"{layout}-role-current-state-plan/v1", binding=binding,
                target_id=state["target_id"], target_operation_id=state["target_operation_id"],
                source_relations=present, source_layouts=layouts,
                relation_counts={table: len(value) for table, value in rows.items()}, seed_sha256=seed_sha256)
    if layout == "full":
        # Defaults describe the new target; they never change the original cut.
        plan["fresh_defaults_sha256"] = _role_settings_seed({
            "settings": list(DEFAULT_SETTINGS.items()), "column_settings": list(DEFAULT_COLUMNS),
            "central_setting_versions": [], "central_column_setting_versions": [],
        })[1]
    plan = json.loads(json.dumps(plan, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return rows, plan


def _current_state_receipt(plan, accepted_at, *, layout="query"):
    if layout == "full" and (
        not isinstance(plan.get("fresh_defaults_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", plan["fresh_defaults_sha256"])
    ):
        raise ValueError("full current-state fresh defaults definition conflict")
    if (not isinstance(accepted_at, str)
            or datetime.fromisoformat(accepted_at).utcoffset() != timedelta(hours=9)):
        raise ValueError("current-state acceptance requires an injected aware KST clock")
    return dict(format=f"{layout}-role-current-state-receipt/v1", plan=plan, accepted_at=accepted_at,
                state="SEEDED_UNBOUND", runtime_state="OFFLINE_UNBOUND", applied=False,
                role_ready=False, order_authority=False, consumer_status="NOT_EVALUATED",
                source_authenticity="UNVERIFIED")


def _role_settings_input(role, preserved_rows, preservation_receipt, *, schema_version):
    if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", role):
        raise ValueError("explicit role settings identity is required")
    selected = preserved_rows is not None
    if selected != (preservation_receipt is not None):
        raise ValueError("selected settings require original acceptance receipt")
    seed_kind, receipt_json = "FRESH", None
    if selected:
        if (not isinstance(preservation_receipt, dict)
                or preservation_receipt.get("format") != "offline-current-settings-receipt/v1"
                or preservation_receipt.get("target_role") != role
                or preservation_receipt.get("state") != "selected_raw_settings_accepted_only"
                or preservation_receipt.get("runtime_state") != "OFFLINE_UNBOUND"
                or preservation_receipt.get("order_authority") is not False
                or preservation_receipt.get("overall_P2_complete") is not False):
            raise ValueError("role settings acceptance receipt conflict")
        seed_kind = "SELECTED"
        receipt_json = json.dumps(preservation_receipt, ensure_ascii=False, sort_keys=True, allow_nan=False)
        rows, seed_sha256 = _role_settings_seed(preserved_rows)
        count = sum(map(len, rows.values()))
        if (not isinstance(preservation_receipt.get("counts"), dict) or not count
                or preservation_receipt["counts"].get("selected") != count):
            raise ValueError("role settings receipt count conflict")
    else:
        rows, seed_sha256 = _role_settings_seed({
            "settings": list(DEFAULT_SETTINGS.items()), "column_settings": list(DEFAULT_COLUMNS),
            "central_setting_versions": [], "central_column_setting_versions": [],
        })
    expected = (schema_version, role, seed_kind, seed_sha256, receipt_json)
    return rows, expected, selected


class Database:
    def __init__(self, database_path: Path, *, now_provider=None) -> None:
        self._database_path = database_path
        self.settings = SettingsRepository(database_path, now_provider=now_provider)

    def initialize_role_settings(
        self, *, role: str, preserved_rows: dict[str, list[tuple]] | None = None,
        preservation_receipt: dict | None = None,
    ) -> dict:
        """Initialize an explicit settings-only role store, never select a runtime.

        Fresh installation seeds existing defaults once. Selected installation
        seeds only the selected original rows, including missing/orphan versions.
        BLOB affinity deliberately preserves NULL/text/int/real/blob primitives.
        Normal SettingsRepository/ColumnSettingsRepository keep their public
        read conversions and normal writes; installation never marks applied.
        """
        rows, expected, selected = _role_settings_input(
            role, preserved_rows, preservation_receipt, schema_version=ROLE_SETTINGS_SCHEMA_VERSION,
        )
        seed_sha256 = expected[3]
        path = self._database_path.absolute()
        if path.resolve() != path or path.is_symlink():
            raise ValueError("redirected role settings target")
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            objects = connection.execute(
                "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if objects:
                if {name: sql for name, kind, sql in objects if kind == "table"} != _ROLE_SETTINGS_SQL or any(
                    kind != "table" for _, kind, _ in objects
                ):
                    raise ValueError("not an owned role settings schema")
                metadata = connection.execute(
                    "SELECT schema_version,role,seed_kind,seed_sha256,preservation_receipt_json "
                    "FROM role_settings_schema WHERE singleton=1"
                ).fetchall()
                if (len(metadata) != 1 or metadata[0][:2] != expected[:2]
                        or selected and metadata[0] != expected):
                    raise ValueError("role settings initialization conflict")
                # Current settings are editable. A retry checks the immutable
                # initial receipt/fingerprint, never replaces later normal edits.
                actual, replayed = metadata[0], True
                connection.rollback()
            else:
                stored_sha256 = self._create_role_settings_seed(
                    connection, rows, metadata_sql=_ROLE_SETTINGS_SQL["role_settings_schema"],
                )
                if stored_sha256 != seed_sha256:
                    raise ValueError("role settings primitive roundtrip conflict")
                connection.execute("INSERT INTO role_settings_schema VALUES (1,?,?,?,?,?)", expected)
                connection.commit()
                actual, replayed = expected, False
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        self.settings.clear_cache()
        return dict(state="role_settings_initialized_unbound", schema_version=actual[0], role=actual[1],
                    seed_kind=actual[2], seed_sha256=actual[3], replayed=replayed,
                    order_authority=False, runtime_state="OFFLINE_UNBOUND", applied=False,
                    role_ready=False, consumer_status="NOT_EVALUATED")

    def _initialize_stock_catalog(self, connection: sqlite3.Connection, *, seed_aliases: bool = True) -> None:
        connection.execute("CREATE TABLE IF NOT EXISTS stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        self._add_stock_columns(connection)
        connection.execute("CREATE TABLE IF NOT EXISTS stock_aliases (alias TEXT PRIMARY KEY, stock_code TEXT NOT NULL, FOREIGN KEY(stock_code) REFERENCES stocks(code))")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS stock_name_history ("
            "stock_code TEXT NOT NULL, old_name TEXT NOT NULL, new_name TEXT NOT NULL, "
            "changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, source TEXT NOT NULL DEFAULT 'KRX', "
            "decision TEXT NOT NULL DEFAULT 'pending', "
            "PRIMARY KEY(stock_code, old_name), FOREIGN KEY(stock_code) REFERENCES stocks(code))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS kind_name_disclosures ("
            "acpt_no TEXT PRIMARY KEY, stock_code TEXT NOT NULL, current_name TEXT NOT NULL, "
            "disclosed_on TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending')"
        )
        name_history_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(stock_name_history)")}
        if "decision" not in name_history_columns:
            connection.execute("ALTER TABLE stock_name_history ADD COLUMN decision TEXT NOT NULL DEFAULT 'pending'")
        if seed_aliases:
            seed_known_stock_aliases(connection)

    @staticmethod
    def _create_role_settings_seed(connection, rows, *, metadata_sql):
        for table, statement in _ROLE_SETTINGS_SQL.items():
            connection.execute(metadata_sql if table == "role_settings_schema" else statement)
            if table in rows:
                connection.executemany(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in ROLE_SETTINGS_TABLES[table])})",
                    rows[table],
                )
        _, stored_sha256 = _role_settings_seed({
            table: connection.execute(
                f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid"
            ).fetchall() for table, columns in ROLE_SETTINGS_TABLES.items()
        })
        return stored_sha256

    def initialize_query_role(
        self, *, role: str, preserved_rows: dict[str, list[tuple]] | None = None,
        preservation_receipt: dict | None = None, preserved_state: dict | None = None,
        receipt_at=None,
    ) -> dict:
        """Own one fresh query-role schema/seed transaction; never adopt other DBs.

        SELECTED accepts only the original four settings relations. It creates
        no theme profile, including a profile with the canonical default name.
        """
        return self._initialize_desktop_role(
            role=role, layout="query", preserved_rows=preserved_rows,
            preservation_receipt=preservation_receipt, preserved_state=preserved_state,
            receipt_at=receipt_at,
        )

    def initialize_full_role(
        self, *, role: str, preserved_state: dict | None = None, receipt_at=None,
        allow_create: bool = True,
    ) -> dict:
        """Accept a distinct full target, never upgrade a query or legacy DB.

        Only selected source rows determine the immutable seed. Missing fresh
        defaults are filled once, after its primitive/reference roundtrip.
        Restore validation can require an existing owned target; it never
        creates a replacement for a missing or emptied before-image.
        """
        return self._initialize_desktop_role(
            role=role, layout="full", preserved_state=preserved_state, receipt_at=receipt_at,
            allow_create=allow_create,
        )

    def _initialize_desktop_role(
        self, *, role, layout, preserved_rows=None, preservation_receipt=None,
        preserved_state=None, receipt_at=None, connection=None, allow_create=True,
    ):
        if layout not in {"query", "full"}:
            raise ValueError("unknown desktop storage layout")
        metadata_sql = _FULL_ROLE_SCHEMA_SQL if layout == "full" else _QUERY_ROLE_SCHEMA_SQL
        schema_version = FULL_ROLE_SCHEMA_VERSION if layout == "full" else QUERY_ROLE_SCHEMA_VERSION
        state_plan = None
        if preserved_state is not None:
            if preserved_rows is not None or preservation_receipt is not None or not callable(receipt_at):
                raise ValueError("current-state installation is exclusive and requires a lazy receipt clock")
            state_rows, state_plan = _current_state_input(role, preserved_state, layout=layout)
            rows = {table: state_rows[table] for table in ROLE_SETTINGS_TABLES}
            expected = (schema_version, role, "SELECTED_USER_STATE", state_plan["seed_sha256"], None)
            selected = True
        else:
            if receipt_at is not None:
                raise ValueError("receipt clock belongs to explicit current-state installation")
            rows, expected, selected = _role_settings_input(
                role, preserved_rows, preservation_receipt, schema_version=schema_version,
            )
        path = self._database_path.absolute()
        if path.resolve() != path or path.is_symlink():
            raise ValueError(f"redirected {layout} role target")
        owns_connection = connection is None
        if owns_connection:
            target = path if allow_create else path.as_uri() + "?mode=rw"
            options = {} if allow_create else {"uri": True}
            connection = sqlite3.connect(target, **options)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            objects = connection.execute(
                "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if objects:
                names = {(name, kind) for name, kind, _ in objects}
                tables = _QUERY_ROLE_TABLES | ({"themes", "stock_themes", "legacy_news_transfer_migrations"} if layout == "full" else set())
                required = {(name,"table") for name in tables} | {(name,"index") for name in _QUERY_ROLE_INDEXES}
                schema = {name:sql for name, kind, sql in objects if kind == "table"}
                if (names != required or schema.get("role_settings_schema") != metadata_sql
                        or any(schema.get(name) != _ROLE_SETTINGS_SQL[name] for name in ROLE_SETTINGS_TABLES)):
                    raise ValueError(f"not an owned {layout} role schema")
                metadata = connection.execute(
                    "SELECT schema_version,role,seed_kind,seed_sha256,preservation_receipt_json,owned_layout_sha256"
                    + (",store_kind" if layout == "full" else "")
                    + " FROM role_settings_schema WHERE singleton=1"
                ).fetchall()
                if (len(metadata) != 1 or metadata[0][:2] != expected[:2]
                        or layout == "full" and metadata[0][6] != "full"
                        or metadata[0][2] not in {"FRESH", "SELECTED", "SELECTED_USER_STATE"}
                        or selected and metadata[0][:4 if state_plan is not None else 5] != expected[:4 if state_plan is not None else 5]
                        or metadata[0][5] != _owned_role_layout_digest(objects)):
                    raise ValueError(f"{layout} role initialization or owned layout conflict")
                if metadata[0][2] == "SELECTED_USER_STATE":
                    receipt = json.loads(metadata[0][4])
                    if not isinstance(receipt, dict):
                        raise ValueError("current-state first receipt conflict")
                    plan = state_plan if state_plan is not None else receipt.get("plan")
                    if (not isinstance(plan, dict) or plan.get("seed_sha256") != metadata[0][3]
                            or plan.get("binding", {}).get("target_role") != role
                            or plan.get("format") != f"{layout}-role-current-state-plan/v1"
                            or receipt != _current_state_receipt(plan, receipt.get("accepted_at"), layout=layout)):
                        raise ValueError("current-state first receipt conflict")
                actual, replayed = metadata[0], True
                connection.rollback()
            else:
                if not allow_create:
                    raise ValueError(f"missing owned {layout} role schema")
                stored_sha256 = self._create_role_settings_seed(connection, rows, metadata_sql=metadata_sql)
                if stored_sha256 != (_role_settings_seed(rows)[1] if state_plan is not None else expected[3]):
                    raise ValueError(f"{layout} role primitive roundtrip conflict")
                # execute, not executescript: DDL/seed/profile/receipt are one tx.
                self._initialize_stock_catalog(connection, seed_aliases=False)
                for statement in (_QUERY_CACHE_SQL + _THEME_PROFILE_SQL + (_LEGACY_THEME_SQL if layout == "full" else "")).split(";"):
                    if statement.strip():
                        connection.execute(statement)
                self._add_daily_bar_columns(connection)
                self._add_upper_limit_price_column(connection)
                create_market_data_metadata_table(connection)
                self._create_theme_name_decisions(connection)
                self._create_theme_suggestions(connection)
                self._add_theme_suggestion_article_context(connection)
                if layout == "full":
                    # Current compatibility readers require these empty tables;
                    # no old news rows, migration history or aliases are seeded.
                    self._create_legacy_news_transfer_ledger(connection)
                if expected[2] == "FRESH":
                    connection.execute("INSERT INTO theme_profiles(profile_name) VALUES (?)", (DEFAULT_SETTINGS["theme_active_profile"],))
                if state_plan is not None:
                    self._seed_current_state(connection, state_rows, expected[3])
                    if layout == "full":
                        self._complete_full_defaults(connection)
                    # The first clock is sampled only here, never on a replay.
                    receipt = _current_state_receipt(state_plan, receipt_at(), layout=layout)
                    expected = (*expected[:4], json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False))
                objects = connection.execute(
                    "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                actual = (*expected, _owned_role_layout_digest(objects))
                connection.execute(
                    "INSERT INTO role_settings_schema "
                    "(singleton,schema_version,role,seed_kind,seed_sha256,preservation_receipt_json,owned_layout_sha256) "
                    "VALUES (1,?,?,?,?,?,?)", actual,
                )
                connection.commit()
                replayed = False
        except BaseException:
            connection.rollback()
            raise
        finally:
            if owns_connection:
                connection.close()
        self.settings.clear_cache()
        result = dict(state=f"{layout}_role_initialized_unbound", schema_version=actual[0], role=actual[1],
                    seed_kind=actual[2], seed_sha256=actual[3], owned_layout_sha256=actual[5], replayed=replayed,
                    order_authority=False, runtime_state="OFFLINE_UNBOUND", applied=False,
                    role_ready=False, consumer_status="NOT_EVALUATED")
        if actual[2] == "SELECTED_USER_STATE":
            result["receipt"] = json.loads(actual[4])
            result["source_authenticity"] = "UNVERIFIED"
        if layout == "full":
            result["layout"] = "full"
        return result

    @staticmethod
    def _complete_full_defaults(connection):
        """Fill only missing fresh target values, without source version stamps."""
        connection.executemany("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", DEFAULT_SETTINGS.items())
        connection.executemany(
            "INSERT OR IGNORE INTO column_settings(column_name,visible,position,width) VALUES (?,?,?,?)",
            DEFAULT_COLUMNS,
        )
        active = connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone()[0]
        connection.execute("INSERT OR IGNORE INTO theme_profiles(profile_name) VALUES (?)", (active,))

    @staticmethod
    def _seed_current_state(connection, rows, seed_sha256):
        """Borrow the initializer's one transaction; no normalization or clocks."""
        for table in ("stocks", "theme_profiles", "profile_themes", "profile_stock_themes",
                      "profile_theme_name_decisions", "profile_theme_suggestions", "stock_aliases"):
            columns = QUERY_STATE_TABLES[table]
            connection.executemany(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                rows[table],
            )
        stored = {table: connection.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid"
        ).fetchall() for table, columns in QUERY_STATE_TABLES.items()}
        if _role_settings_seed(stored, tables=QUERY_STATE_TABLES)[1] != seed_sha256:
            raise ValueError("current-state primitive roundtrip conflict")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("current-state native foreign key conflict")
        for table in ("profile_stock_themes", "profile_theme_suggestions"):
            if connection.execute(f"SELECT 1 FROM {table} t LEFT JOIN stocks s ON s.code=t.stock_code WHERE s.code IS NULL LIMIT 1").fetchone():
                raise ValueError("current-state stock catalog reference conflict")
        active = connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchall()
        if active and (len(active) != 1 or type(active[0][0]) is not str or len(connection.execute(
                "SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", active[0]
        ).fetchall()) != 1):
            raise ValueError("current-state native active profile conflict")

    def initialize(self, *, settings_role: str | None = None) -> dict | None:
        if settings_role is not None:
            return self.initialize_role_settings(role=settings_role)
        connection = sqlite3.connect(self._database_path)
        try:
            role_schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='role_settings_schema'").fetchone()
            if role_schema:
                if role_schema[0] != _FULL_ROLE_SCHEMA_SQL:
                    raise ValueError("role settings require explicit settings_role initialization")
                roles = connection.execute("SELECT role FROM role_settings_schema WHERE singleton=1").fetchall()
                if len(roles) != 1:
                    raise ValueError("full role initialization identity conflict")
                # The outer initializer retains its one connection/close owner.
                # Normal startup validates the owned full store, without the
                # legacy alias/default/profile migration work below.
                return self._initialize_desktop_role(role=roles[0][0], layout="full", connection=connection)
            connection.execute("PRAGMA foreign_keys = ON")
            migrations = SQLiteMigrationRunner(connection)
            migrations.prepare()
            migrations.ensure_compatible(MAIN_SCHEMA_VERSION)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS central_setting_versions ("
                "setting_key TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS central_column_setting_versions ("
                "column_name TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                self._apply_v1(connection)
                migrations.record_applied(1, "initial_settings_and_columns")
            else:
                migrations.record_applied(1, "initial_settings_and_columns")
            connection.executemany("INSERT OR IGNORE INTO column_settings(column_name, visible, position, width) VALUES (?, ?, ?, ?)", DEFAULT_COLUMNS)
            removed_new_high = connection.execute("DELETE FROM column_settings WHERE column_name = 'new_high'").rowcount
            if removed_new_high:
                connection.execute("UPDATE column_settings SET position = position - 1 WHERE position > 13")
            connection.executemany("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", DEFAULT_SETTINGS.items())
            self._initialize_stock_catalog(connection)
            connection.executescript(_LEGACY_THEME_SQL + _QUERY_CACHE_SQL)
            self._initialize_theme_profiles(connection)
            self._add_daily_bar_columns(connection)
            self._add_minute_bar_columns(connection)
            self._add_top20_index_columns(connection)
            # v2는 과거에 버전 없이 누적된 현재 호환 스키마의 기준점이다.
            # 실제 데이터 변환은 없으며 이후 변경부터 v3 migration으로 추가한다.
            migrations.record_applied(2, "legacy_compatibility_schema_baseline")
            migrations.apply(
                (
                    SQLiteMigration(1, "initial_settings_and_columns", lambda _: None),
                    SQLiteMigration(2, "legacy_compatibility_schema_baseline", lambda _: None),
                    SQLiteMigration(
                        3,
                        "market_data_observation_metadata",
                        create_market_data_metadata_table,
                    ),
                    SQLiteMigration(
                        4,
                        "stock_upper_limit_price_cache",
                        self._add_upper_limit_price_column,
                    ),
                    SQLiteMigration(
                        5,
                        "profile_theme_name_decisions",
                        self._create_theme_name_decisions,
                    ),
                    SQLiteMigration(
                        6,
                        "profile_theme_suggestions",
                        self._create_theme_suggestions,
                    ),
                    SQLiteMigration(
                        7,
                        "profile_theme_suggestion_article_context",
                        self._add_theme_suggestion_article_context,
                    ),
                    SQLiteMigration(
                        8,
                        "legacy_news_transfer_ledger",
                        self._create_legacy_news_transfer_ledger,
                    ),
                )
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _add_minute_bar_columns(connection: sqlite3.Connection) -> None:
        """기존 DB의 분봉에도 실제 분당 거래대금 저장 열을 추가한다."""
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(minute_bars)")}
        if "trade_value_eok" not in columns:
            connection.execute("ALTER TABLE minute_bars ADD COLUMN trade_value_eok REAL")

    @staticmethod
    def _add_top20_index_columns(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(top20_trade_value_index)")}
        for name, column_type in (
            ("kospi_trade_value_eok", "REAL NOT NULL DEFAULT 0"),
            ("kosdaq_trade_value_eok", "REAL NOT NULL DEFAULT 0"),
            ("unknown_trade_value_eok", "REAL NOT NULL DEFAULT 0"),
            ("kospi_stock_count", "INTEGER NOT NULL DEFAULT 0"),
            ("kosdaq_stock_count", "INTEGER NOT NULL DEFAULT 0"),
            ("unknown_stock_count", "INTEGER NOT NULL DEFAULT 0"),
            ("cohort_segments", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE top20_trade_value_index ADD COLUMN {name} {column_type}")

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
        for name in ("market_cap", "float_ratio", "float_shares", "circulating_market_cap", "high_250_price", "historical_high_price", "historical_high_first_year", "historical_high_last_year", "historical_high_occurred_on", "historical_high_updated_at", "historical_high_checked_on", "fundamentals_updated_at", "nxt_enabled", "nxt_checked_at", "last_price", "last_price_updated_at"):
            if name not in existing:
                column_type = "TEXT" if name.endswith(("_on", "_at")) else "REAL"
                connection.execute(f"ALTER TABLE stocks ADD COLUMN {name} {column_type}")

    @staticmethod
    def _add_upper_limit_price_column(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(stocks)")}
        if "upper_limit_price" not in columns:
            connection.execute("ALTER TABLE stocks ADD COLUMN upper_limit_price INTEGER")

    @staticmethod
    def _create_theme_name_decisions(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS profile_theme_name_decisions ("
            "profile_id INTEGER NOT NULL,"
            "decision_kind TEXT NOT NULL CHECK(decision_kind IN "
            "('alias', 'split_to', 'keep_separate')),"
            "source_name TEXT NOT NULL COLLATE NOCASE,"
            "target_name TEXT NOT NULL COLLATE NOCASE,"
            "decision_source TEXT NOT NULL DEFAULT 'user',"
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "PRIMARY KEY(profile_id, decision_kind, source_name, target_name),"
            "FOREIGN KEY(profile_id) REFERENCES theme_profiles(profile_id) ON DELETE CASCADE)"
        )

    @staticmethod
    def _create_theme_suggestions(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS profile_theme_suggestions ("
            "profile_id INTEGER NOT NULL,"
            "stock_code TEXT NOT NULL,"
            "news_identity TEXT NOT NULL,"
            "raw_theme_name TEXT NOT NULL COLLATE NOCASE,"
            "evidence TEXT NOT NULL,"
            "confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 100),"
            "provider TEXT NOT NULL,"
            "model TEXT NOT NULL,"
            "body_hash TEXT NOT NULL,"
            "analyzed_at TEXT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),"
            "reviewed_theme_names TEXT NOT NULL DEFAULT '[]',"
            "reviewed_at TEXT,"
            "PRIMARY KEY(profile_id,stock_code,news_identity,raw_theme_name),"
            "FOREIGN KEY(profile_id) REFERENCES theme_profiles(profile_id) ON DELETE CASCADE)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_profile_theme_suggestions_status "
            "ON profile_theme_suggestions(profile_id,status,analyzed_at DESC)"
        )

    @staticmethod
    def _add_theme_suggestion_article_context(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(profile_theme_suggestions)")
        }
        for name in ("article_title", "article_published_at", "article_url"):
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE profile_theme_suggestions ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _create_legacy_news_transfer_ledger(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS legacy_news_transfer_migrations ("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,completed_at TEXT NOT NULL)"
        )

    @staticmethod
    def _add_daily_bar_columns(connection: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(daily_bars)")}
        if "close_price" not in existing:
            connection.execute("ALTER TABLE daily_bars ADD COLUMN close_price INTEGER")
        for column in ("open_price", "low_price", "volume"):
            if column not in existing:
                connection.execute(f"ALTER TABLE daily_bars ADD COLUMN {column} INTEGER")

    @staticmethod
    def _initialize_theme_profiles(connection: sqlite3.Connection) -> None:
        """Keep each theme set separate while retaining the shared stock catalog."""
        connection.executescript(_THEME_PROFILE_SQL)
        initialized = connection.execute("SELECT value FROM settings WHERE key='theme_profiles_initialized'").fetchone()
        if initialized and str(initialized[0]) == "1":
            return
        connection.execute("INSERT OR IGNORE INTO theme_profiles(profile_name) VALUES ('기본 테마')")
        default_id = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name='기본 테마'").fetchone()[0]
        for name, color in connection.execute("SELECT theme_name, default_color FROM themes"):
            connection.execute("INSERT OR IGNORE INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)", (default_id, name, color))
        for code, name, color in connection.execute("SELECT st.stock_code, t.theme_name, st.custom_color FROM stock_themes st JOIN themes t ON t.theme_id=st.theme_id"):
            connection.execute("INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) VALUES (?, ?, ?, ?)", (default_id, code, name, color))
        connection.execute("UPDATE settings SET value='1' WHERE key='theme_profiles_initialized'")

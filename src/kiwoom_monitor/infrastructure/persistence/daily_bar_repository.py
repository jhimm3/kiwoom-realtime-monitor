"""최근 250거래일 일봉 고가·직접 거래대금 캐시."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets
from kiwoom_monitor.infrastructure.persistence.local_bar_observations import (
    local_daily_bar_observation,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    upsert_market_data_metadata,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_schema import (
    MARKET_DATA_METADATA_TABLE,
)


class DailyBarRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_targets(self, codes: tuple[str, ...]) -> dict[str, DailyHighTargets]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(daily_bars)")}
            optional = ", ".join(
                column if column in columns else f"NULL AS {column}"
                for column in ("open_price", "low_price", "volume")
            )
            rows = connection.execute(
                f"SELECT stock_code, trade_date, high_price, trade_value_eok, close_price, {optional} FROM daily_bars WHERE stock_code IN ({placeholders}) ORDER BY trade_date DESC",
                codes,
            ).fetchall()
        finally:
            connection.close()
        grouped: dict[str, list[DailyBar]] = {}
        for code, trade_date, high_price, trade_value, close_price, open_price, low_price, volume in rows:
            grouped.setdefault(str(code), []).append(DailyBar(str(trade_date).replace("-", ""), int(high_price), float(trade_value) if trade_value is not None else None, int(close_price) if close_price else None, int(open_price) if open_price else None, int(low_price) if low_price else None, int(volume) if volume is not None else None))
        # 5·20일 값은 저장된 원본으로 복원한다. 250일 값은 이전 버전 DB에
        # 아직 30개만 남아 있을 수 있으므로 여기서 불완전하게 계산하지 않고,
        # stocks에 저장해 둔 마지막 정상 ka10081 계산값을 계속 사용한다.
        return {
            code: DailyHighTargets.from_daily_bars(
                tuple(bars[:250]), as_of=date.today(), include_high_250=False,
            )
            for code, bars in grouped.items()
        }

    def refreshed_today(self, codes: tuple[str, ...], today: date) -> set[str]:
        if not codes:
            return set()
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                f"SELECT stock_code FROM daily_bar_sync_log WHERE stock_code IN ({placeholders}) AND synced_on=?",
                (*codes, today.isoformat()),
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows}

    def refreshed_since(self, codes: tuple[str, ...], since: date) -> set[str]:
        """Return codes refreshed on or after a given calendar date."""
        if not codes:
            return set()
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                f"SELECT stock_code FROM daily_bar_sync_log WHERE stock_code IN ({placeholders}) AND synced_on>=?",
                (*codes, since.isoformat()),
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows}

    def finalized_codes(self, codes: tuple[str, ...], trade_date: date) -> set[str]:
        if not codes:
            return set()
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                f"SELECT stock_code FROM market_data_finalization_log WHERE trade_date=? AND stock_code IN ({placeholders})",
                (trade_date.isoformat(), *codes),
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows}

    def mark_finalized(self, codes: tuple[str, ...], trade_date: date) -> None:
        if not codes:
            return
        connection = sqlite3.connect(self._path)
        try:
            self._ensure_unconfirmed_table(connection)
            connection.executemany(
                "INSERT OR REPLACE INTO market_data_finalization_log(trade_date,stock_code) VALUES (?,?)",
                ((trade_date.isoformat(), code) for code in codes),
            )
            connection.executemany(
                "DELETE FROM market_data_unconfirmed_log WHERE trade_date=? AND stock_code=?",
                ((trade_date.isoformat(), code) for code in codes),
            )
            connection.commit()
        finally:
            connection.close()

    def mark_unconfirmed(self, code: str, trade_date: date, missing_parts: tuple[str, ...], attempts: int) -> None:
        """제한 재시도 후에도 확정하지 못한 분봉·일봉만 기록한다."""
        if not code or not missing_parts:
            return
        connection = sqlite3.connect(self._path)
        try:
            self._ensure_unconfirmed_table(connection)
            connection.execute(
                "INSERT INTO market_data_unconfirmed_log(trade_date,stock_code,missing_parts,attempts) VALUES(?,?,?,?) "
                "ON CONFLICT(trade_date,stock_code) DO UPDATE SET missing_parts=excluded.missing_parts, "
                "attempts=excluded.attempts, updated_at=CURRENT_TIMESTAMP",
                (trade_date.isoformat(), code, ",".join(missing_parts), int(attempts)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_unconfirmed_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS market_data_unconfirmed_log ("
            "trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, missing_parts TEXT NOT NULL, "
            "attempts INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(trade_date, stock_code))"
        )

    def latest_trade_date_before(self, codes: tuple[str, ...], before: date) -> date | None:
        """Find the latest actual cached trading day among the visible stocks."""
        if not codes:
            return None
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            row = connection.execute(
                f"SELECT MAX(trade_date) FROM daily_bars WHERE stock_code IN ({placeholders}) AND trade_date<?",
                (*codes, before.isoformat()),
            ).fetchone()
        finally:
            connection.close()
        if not row or not row[0]:
            return None
        try:
            return date.fromisoformat(str(row[0]))
        except ValueError:
            return None

    def missing_latest_bar(self, codes: tuple[str, ...], expected: date) -> tuple[str, ...]:
        """Return codes whose newest cached daily bar predates the expected weekday."""
        if not codes:
            return ()
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                f"SELECT stock_code, MAX(trade_date) FROM daily_bars WHERE stock_code IN ({placeholders}) GROUP BY stock_code",
                codes,
            ).fetchall()
        finally:
            connection.close()
        latest = {str(code): str(trade_date) for code, trade_date in rows if trade_date}
        return tuple(code for code in codes if latest.get(code, "") < expected.isoformat())

    def upsert_targets(
        self,
        code: str,
        targets: DailyHighTargets,
        synced_on: date,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        if not code or not targets.daily_bars:
            return
        rows = tuple((code, f"{bar.trade_date[:4]}-{bar.trade_date[4:6]}-{bar.trade_date[6:]}", bar.high_price, bar.trade_value_eok, bar.close_price, bar.open_price, bar.low_price, bar.volume) for bar in targets.daily_bars[:250])
        connection = sqlite3.connect(self._path)
        try:
            connection.executemany(
                "INSERT INTO daily_bars(stock_code, trade_date, high_price, trade_value_eok, close_price, open_price, low_price, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(stock_code, trade_date) DO UPDATE SET high_price=excluded.high_price, trade_value_eok=excluded.trade_value_eok, close_price=excluded.close_price, open_price=excluded.open_price, low_price=excluded.low_price, volume=excluded.volume "
                "WHERE daily_bars.high_price != excluded.high_price OR COALESCE(daily_bars.trade_value_eok, -1) != COALESCE(excluded.trade_value_eok, -1) OR COALESCE(daily_bars.close_price, -1) != COALESCE(excluded.close_price, -1)",
                rows,
            )
            available_at = observed_at or datetime.now()
            for bar in targets.daily_bars[:250]:
                trading_day = date.fromisoformat(
                    f"{bar.trade_date[:4]}-{bar.trade_date[4:6]}-{bar.trade_date[6:]}"
                )
                observation = local_daily_bar_observation(
                    code,
                    trading_day,
                    bar,
                    available_at=available_at,
                    source="kiwoom-ka10081",
                )
                upsert_market_data_metadata(connection, trading_day.isoformat(), observation)
            connection.execute(
                "INSERT INTO daily_bar_sync_log(stock_code, synced_on) VALUES (?, ?) ON CONFLICT(stock_code) DO UPDATE SET synced_on=excluded.synced_on",
                (code, synced_on.isoformat()),
            )
            connection.commit()
        finally:
            connection.close()

    def purge_before(self, cutoff: date) -> None:
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("DELETE FROM daily_bars WHERE trade_date < ?", (cutoff.isoformat(),))
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (MARKET_DATA_METADATA_TABLE,),
            ).fetchone():
                connection.execute(
                    f"DELETE FROM {MARKET_DATA_METADATA_TABLE} "
                    "WHERE dataset_kind='daily_bar' AND observation_key < ?",
                    (cutoff.isoformat(),),
                )
            connection.commit()
        finally:
            connection.close()

    def retain_latest(self, limit: int = 250) -> None:
        """종목별 최신 일봉만 남겨 거래일 기준 보존 개수를 일정하게 한다."""
        if limit <= 0:
            return
        connection = sqlite3.connect(self._path)
        try:
            connection.execute(
                "DELETE FROM daily_bars WHERE rowid IN ("
                "SELECT rowid FROM ("
                "SELECT rowid, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS sequence "
                "FROM daily_bars"
                ") WHERE sequence>?"
                ")",
                (limit,),
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (MARKET_DATA_METADATA_TABLE,),
            ).fetchone():
                connection.execute(
                    f"DELETE FROM {MARKET_DATA_METADATA_TABLE} AS metadata "
                    "WHERE dataset_kind='daily_bar' AND NOT EXISTS ("
                    "SELECT 1 FROM daily_bars AS bars WHERE bars.stock_code=metadata.subject "
                    "AND bars.trade_date=metadata.observation_key)"
                )
            connection.commit()
        finally:
            connection.close()

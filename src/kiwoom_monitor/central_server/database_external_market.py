"""External market bar persistence for the existing QueryStore backends."""
from __future__ import annotations

from typing import Any

from .database_codec import bounded_limit


class SQLiteExternalMarketStoreMixin:
    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_external_bars VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,instrument,contract,timeframe,bar_time) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,updated_at=excluded.updated_at "
                "WHERE central_external_bars.open IS NOT excluded.open "
                "OR central_external_bars.high IS NOT excluded.high "
                "OR central_external_bars.low IS NOT excluded.low "
                "OR central_external_bars.close IS NOT excluded.close "
                "OR central_external_bars.volume IS NOT excluded.volume",
                [_external_bar_values(value) for value in values],
            )

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT provider,instrument,contract,timeframe,bar_time,open,high,low,close,volume,updated_at "
                "FROM central_external_bars WHERE instrument=? AND timeframe=? "
                "ORDER BY bar_time DESC LIMIT ?", (instrument, timeframe, bounded_limit(limit, 10000)),
            ).fetchall()
        return [_external_bar_result(row) for row in reversed(rows)]


class PostgresExternalMarketStoreMixin:
    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="external_market.bars", writer_kind="external_market:bars",
            operation="save_external_bars", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_external_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(provider,instrument,contract,timeframe,bar_time) DO UPDATE SET "
                "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
                "volume=EXCLUDED.volume,updated_at=EXCLUDED.updated_at "
                "WHERE (central_external_bars.open,central_external_bars.high,"
                "central_external_bars.low,central_external_bars.close,"
                "central_external_bars.volume) IS DISTINCT FROM "
                "(EXCLUDED.open,EXCLUDED.high,EXCLUDED.low,EXCLUDED.close,EXCLUDED.volume)",
                [_external_bar_values(value) for value in values],
            )

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.external_market_bars", writer_kind=f"bars:{timeframe}",
            operation="load_external_bars", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT provider,instrument,contract,timeframe,bar_time::text,open,high,low,close,volume,updated_at "
                "FROM central_external_bars WHERE instrument=%s AND timeframe=%s "
                "ORDER BY bar_time DESC LIMIT %s", (instrument, timeframe, bounded_limit(limit, 10000)),
            )
            rows = cursor.fetchall()
        return [_external_bar_result(row) for row in reversed(rows)]


def _external_bar_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["provider"]), str(value["instrument"]), str(value["contract"]),
        str(value["timeframe"]), str(value["bar_time"]), value.get("open"), value.get("high"),
        value.get("low"), value.get("close"), value.get("volume"), float(value["updated_at"]),
    )


def _external_bar_result(row: tuple[object, ...]) -> dict[str, Any]:
    keys = ("provider", "instrument", "contract", "timeframe", "bar_time", "open", "high", "low", "close", "volume", "updated_at")
    return dict(zip(keys, row, strict=True))

"""최근 30일 일봉 고가·직접 거래대금 캐시."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets


class DailyBarRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_targets(self, codes: tuple[str, ...]) -> dict[str, DailyHighTargets]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                f"SELECT stock_code, trade_date, high_price, trade_value_eok FROM daily_bars WHERE stock_code IN ({placeholders}) ORDER BY trade_date DESC",
                codes,
            ).fetchall()
        finally:
            connection.close()
        grouped: dict[str, list[DailyBar]] = {}
        for code, trade_date, high_price, trade_value in rows:
            grouped.setdefault(str(code), []).append(DailyBar(str(trade_date).replace("-", ""), int(high_price), float(trade_value) if trade_value is not None else None))
        return {code: DailyHighTargets.from_daily_bars(tuple(bars[:30])) for code, bars in grouped.items()}

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

    def upsert_targets(self, code: str, targets: DailyHighTargets, synced_on: date) -> None:
        if not code or not targets.daily_bars:
            return
        rows = tuple((code, f"{bar.trade_date[:4]}-{bar.trade_date[4:6]}-{bar.trade_date[6:]}", bar.high_price, bar.trade_value_eok) for bar in targets.daily_bars[:30])
        connection = sqlite3.connect(self._path)
        try:
            connection.executemany(
                "INSERT INTO daily_bars(stock_code, trade_date, high_price, trade_value_eok) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(stock_code, trade_date) DO UPDATE SET high_price=excluded.high_price, trade_value_eok=excluded.trade_value_eok "
                "WHERE daily_bars.high_price != excluded.high_price OR COALESCE(daily_bars.trade_value_eok, -1) != COALESCE(excluded.trade_value_eok, -1)",
                rows,
            )
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
            connection.commit()
        finally:
            connection.close()

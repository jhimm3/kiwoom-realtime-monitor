"""당일 1분봉을 로컬 SQLite에 보관하는 저장소."""

from __future__ import annotations

import sqlite3
import csv
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv


class MinuteBarRepository:
    """앱을 다시 열어도 당일 분봉 이력을 이어 쓸 수 있게 한다."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def upsert_bars(self, code: str, bars: tuple[MinuteOhlcv, ...]) -> None:
        if not code or not bars:
            return
        self.upsert_many({code: bars})

    def upsert_many(self, bars_by_code: dict[str, tuple[MinuteOhlcv, ...]]) -> None:
        """여러 종목의 변경 분봉을 하나의 SQLite 트랜잭션으로 저장한다."""
        rows = tuple(
            (
                bar.minute.date().isoformat(),
                code,
                bar.minute.isoformat(timespec="minutes"),
                int(bar.open_price), int(bar.high_price), int(bar.low_price), int(bar.close_price), int(bar.volume),
            )
            for code, bars in bars_by_code.items()
            if code
            for bar in bars
        )
        if not rows:
            return
        connection = sqlite3.connect(self._path)
        try:
            connection.executemany(
                "INSERT INTO minute_bars(trade_date, stock_code, minute, open_price, high_price, low_price, close_price, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trade_date, stock_code, minute) DO UPDATE SET "
                "open_price=excluded.open_price, high_price=excluded.high_price, low_price=excluded.low_price, "
                "close_price=excluded.close_price, volume=excluded.volume",
                rows,
            )
            connection.commit()
        finally:
            connection.close()

    def record_history_sync(self, code: str, trade_date: date, completed_at: datetime, bar_count: int) -> None:
        """해당 날짜 분봉을 ka10080으로 마지막 보완한 시각을 남긴다."""
        if not code:
            return
        connection = sqlite3.connect(self._path)
        try:
            connection.execute(
                "INSERT INTO minute_history_sync_log(trade_date, stock_code, completed_at, bar_count) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(trade_date, stock_code) DO UPDATE SET completed_at=excluded.completed_at, bar_count=excluded.bar_count",
                (trade_date.isoformat(), code, completed_at.isoformat(timespec="seconds"), int(bar_count)),
            )
            connection.commit()
        finally:
            connection.close()

    def update_comparison_reports(self, daily_values_by_code: dict[str, tuple[tuple[str, float], ...]], today: date) -> int:
        """이미 수신한 ka10081 일봉 거래대금으로 개발 확인용 월별 CSV를 갱신한다."""
        targets = {
            (code, f"{day[:4]}-{day[4:6]}-{day[6:]}"): value
            for code, values in daily_values_by_code.items()
            for day, value in values
            if len(day) == 8 and day.isdigit() and day != today.strftime("%Y%m%d")
        }
        if not targets:
            return 0
        codes = tuple(sorted({code for code, _ in targets}))
        placeholders = ",".join("?" for _ in codes)
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                "SELECT trade_date, stock_code, open_price, high_price, low_price, close_price, volume "
                f"FROM minute_bars WHERE stock_code IN ({placeholders})",
                codes,
            ).fetchall()
            sync_rows = connection.execute(
                f"SELECT trade_date, stock_code, completed_at FROM minute_history_sync_log WHERE stock_code IN ({placeholders})",
                codes,
            ).fetchall()
        finally:
            connection.close()
        totals: dict[tuple[str, str], tuple[float, int]] = {}
        for trade_date, code, open_price, high_price, low_price, close_price, volume in rows:
            key = (str(code), str(trade_date))
            if key not in targets:
                continue
            value, count = totals.get(key, (0.0, 0))
            value += int(volume) * (int(open_price) + int(high_price) + int(low_price) + int(close_price)) / 4 / 100_000_000
            totals[key] = (value, count + 1)
        sync_times = {(str(code), str(trade_date)): str(completed_at) for trade_date, code, completed_at in sync_rows}
        report_rows = [
            {
                "date": trade_date,
                "code": code,
                "minute_count": count,
                "minute_history_backfill_completed_at": sync_times.get((code, trade_date), ""),
                "minute_trade_value_eok": f"{minute_value:.4f}",
                "daily_trade_value_eok": f"{daily_value:.4f}",
                "difference_eok": f"{minute_value - daily_value:.4f}",
                "difference_percent": f"{(minute_value / daily_value * 100 - 100) if daily_value else 0:.4f}",
            }
            for (code, trade_date), (minute_value, count) in totals.items()
            if (daily_value := targets[(code, trade_date)]) is not None
        ]
        if not report_rows:
            return 0
        report_dir = self._path.parent / "logs" / "developer_checks"
        report_dir.mkdir(parents=True, exist_ok=True)
        fields = ("date", "code", "minute_count", "minute_history_backfill_completed_at", "minute_trade_value_eok", "daily_trade_value_eok", "difference_eok", "difference_percent")
        reports: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in report_rows:
            reports[str(row["date"])[:7]].append(row)
        for month, rows_for_month in reports.items():
            report_path = report_dir / f"minute_daily_trade_value_comparison_{month}.csv"
            existing: dict[tuple[str, str], dict[str, object]] = {}
            if report_path.is_file():
                with report_path.open("r", newline="", encoding="utf-8-sig") as source:
                    existing = {(str(row.get("date", "")), str(row.get("code", ""))): row for row in csv.DictReader(source)}
            for row in rows_for_month:
                existing[(str(row["date"]), str(row["code"]))] = row
            with report_path.open("w", newline="", encoding="utf-8-sig") as output:
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writeheader()
                writer.writerows(value for _, value in sorted(existing.items()))
        return len(report_rows)

    def purge_before(self, cutoff: date) -> None:
        """기준일보다 오래된 분봉을 정리한다."""
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("DELETE FROM minute_bars WHERE trade_date < ?", (cutoff.isoformat(),))
            connection.execute("DELETE FROM minute_history_sync_log WHERE trade_date < ?", (cutoff.isoformat(),))
            connection.commit()
        finally:
            connection.close()

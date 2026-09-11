"""당일 1분봉을 로컬 SQLite에 보관하는 저장소."""

from __future__ import annotations

import sqlite3
import csv
import json
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.market_data_contract import CandidateUniverse, ObservationOrigin
from kiwoom_monitor.infrastructure.persistence.local_bar_observations import (
    local_minute_bar_observation,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    upsert_market_data_metadata,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_schema import (
    MARKET_DATA_METADATA_TABLE,
)


class MinuteBarRepository:
    """앱을 다시 열어도 당일 분봉 이력을 이어 쓸 수 있게 한다."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def upsert_bars(self, code: str, bars: tuple[MinuteOhlcv, ...]) -> None:
        if not code or not bars:
            return
        self._upsert_many(
            {code: bars}, source="kiwoom-ka10080", origin=ObservationOrigin.QUERY,
            candidate_universe=CandidateUniverse.UNKNOWN,
        )

    def upsert_many(self, bars_by_code: dict[str, tuple[MinuteOhlcv, ...]]) -> None:
        """여러 종목의 변경 분봉을 하나의 SQLite 트랜잭션으로 저장한다."""
        self._upsert_many(
            bars_by_code, source="kiwoom-websocket-0B", origin=ObservationOrigin.REALTIME,
            candidate_universe=CandidateUniverse.RANKING_VISIBLE,
        )

    def _upsert_many(
        self,
        bars_by_code: dict[str, tuple[MinuteOhlcv, ...]],
        *,
        source: str,
        origin: ObservationOrigin,
        candidate_universe: CandidateUniverse,
    ) -> None:
        rows = tuple(
            (
                bar.minute.date().isoformat(),
                code,
                bar.minute.isoformat(timespec="minutes"),
                int(bar.open_price), int(bar.high_price), int(bar.low_price), int(bar.close_price), int(bar.volume),
                float(bar.trade_value_eok),
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
                "INSERT INTO minute_bars(trade_date, stock_code, minute, open_price, high_price, low_price, close_price, volume, trade_value_eok) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trade_date, stock_code, minute) DO UPDATE SET "
                "open_price=excluded.open_price, high_price=excluded.high_price, low_price=excluded.low_price, "
                "close_price=excluded.close_price, volume=excluded.volume, trade_value_eok=excluded.trade_value_eok",
                rows,
            )
            available_at = datetime.now()
            for code, bars in bars_by_code.items():
                if not code:
                    continue
                for bar in bars:
                    observation = local_minute_bar_observation(
                        code,
                        bar.minute,
                        bar,
                        available_at=available_at,
                        source=source,
                        actual_trade_value=bar.trade_value_eok_override is not None,
                        origin=origin,
                        candidate_universe=candidate_universe,
                    )
                    upsert_market_data_metadata(
                        connection, bar.minute.isoformat(timespec="minutes"), observation
                    )
            connection.commit()
        finally:
            connection.close()

    def upsert_market_index_minutes(
        self, rows: dict[tuple[str, datetime], tuple[float, float, float, float, float | None]],
    ) -> None:
        """이미 구독 중인 코스피·코스닥 실시간 값을 1분 OHLC로 보관한다."""
        values = tuple(
            (minute.date().isoformat(), market, minute.isoformat(timespec="minutes"), *ohlc)
            for (market, minute), ohlc in rows.items()
            if market in {"kospi", "kosdaq"}
        )
        if not values:
            return
        connection = sqlite3.connect(self._path)
        try:
            connection.executemany(
                "INSERT INTO market_index_minute_bars(trade_date,market,minute,open_value,high_value,low_value,close_value,trade_value_eok) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,market,minute) DO UPDATE SET "
                "high_value=MAX(market_index_minute_bars.high_value,excluded.high_value), "
                "low_value=MIN(market_index_minute_bars.low_value,excluded.low_value), "
                "close_value=excluded.close_value, trade_value_eok=excluded.trade_value_eok",
                values,
            )
            connection.commit()
        finally:
            connection.close()

    def replace_market_index_minutes(
        self, rows: dict[tuple[str, datetime], tuple[float, float, float, float, float | None]],
    ) -> None:
        """ka20005의 완성된 분봉으로 실시간 부분 봉을 교체한다."""
        if not rows:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._replace_market_index_minutes(connection, rows)

    def replace_market_index_daily(self, market: str, rows: tuple[tuple[object, ...], ...]) -> None:
        if not rows:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._replace_market_index_daily(connection, market, rows)

    def replace_market_index_history(
        self,
        minute_rows: dict[tuple[str, datetime], tuple[float, float, float, float, float | None]],
        daily_rows: dict[str, tuple[tuple[object, ...], ...]],
    ) -> None:
        """한 번의 지수 보완 결과를 분봉·일봉 모두 반영하거나 모두 되돌린다."""
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._replace_market_index_minutes(connection, minute_rows)
                for market, rows in daily_rows.items():
                    self._replace_market_index_daily(connection, market, rows)

    @staticmethod
    def _replace_market_index_minutes(
        connection: sqlite3.Connection,
        rows: dict[tuple[str, datetime], tuple[float, float, float, float, float | None]],
    ) -> None:
        values = tuple(
            (minute.date().isoformat(), market, minute.isoformat(timespec="minutes"), *ohlc)
            for (market, minute), ohlc in rows.items()
        )
        connection.executemany(
            "INSERT INTO market_index_minute_bars VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(trade_date,market,minute) DO UPDATE SET open_value=excluded.open_value,"
            "high_value=excluded.high_value,low_value=excluded.low_value,close_value=excluded.close_value,"
            "trade_value_eok=COALESCE(excluded.trade_value_eok,market_index_minute_bars.trade_value_eok)",
            values,
        )

    @staticmethod
    def _replace_market_index_daily(
        connection: sqlite3.Connection,
        market: str,
        rows: tuple[tuple[object, ...], ...],
    ) -> None:
        values = tuple((str(row[0])[:10], market, *row[1:7]) for row in rows)
        connection.executemany(
            "INSERT INTO market_index_daily_bars VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,market) "
            "DO UPDATE SET open_value=excluded.open_value,high_value=excluded.high_value,low_value=excluded.low_value,"
            "close_value=excluded.close_value,volume=excluded.volume,trade_value_eok=excluded.trade_value_eok",
            values,
        )

    def upsert_top20_trade_value_index(
        self, minute: datetime, trade_value_eok: float, stock_codes: tuple[str, ...],
        capture_state: str = "realtime_complete", *,
        kospi_trade_value_eok: float = 0.0, kosdaq_trade_value_eok: float = 0.0,
        unknown_trade_value_eok: float = 0.0, kospi_stock_count: int = 0,
        kosdaq_stock_count: int = 0, unknown_stock_count: int = 0,
        cohort_segments: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        """30초 순위로 고정한 종목군의 1분 거래대금 합계를 저장한다."""
        connection = sqlite3.connect(self._path)
        try:
            connection.execute(
                "INSERT INTO top20_trade_value_index("
                "minute,trade_date,trade_value_eok,stock_codes,stock_count,capture_state,"
                "kospi_trade_value_eok,kosdaq_trade_value_eok,unknown_trade_value_eok,"
                "kospi_stock_count,kosdaq_stock_count,unknown_stock_count,updated_at"
                ",cohort_segments) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?) "
                "ON CONFLICT(minute) DO UPDATE SET "
                "trade_value_eok=excluded.trade_value_eok,stock_codes=excluded.stock_codes,"
                "stock_count=excluded.stock_count,capture_state=excluded.capture_state,"
                "kospi_trade_value_eok=excluded.kospi_trade_value_eok,"
                "kosdaq_trade_value_eok=excluded.kosdaq_trade_value_eok,"
                "unknown_trade_value_eok=excluded.unknown_trade_value_eok,"
                "kospi_stock_count=excluded.kospi_stock_count,"
                "kosdaq_stock_count=excluded.kosdaq_stock_count,"
                "unknown_stock_count=excluded.unknown_stock_count,"
                "cohort_segments=excluded.cohort_segments,"
                "updated_at=CURRENT_TIMESTAMP",
                (
                    minute.isoformat(timespec="minutes"), minute.date().isoformat(),
                    float(trade_value_eok), json.dumps(stock_codes, ensure_ascii=False),
                    len(stock_codes), capture_state,
                    float(kospi_trade_value_eok), float(kosdaq_trade_value_eok),
                    float(unknown_trade_value_eok), int(kospi_stock_count),
                    int(kosdaq_stock_count), int(unknown_stock_count),
                    json.dumps(cohort_segments, ensure_ascii=False),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def load_recent_top20_trade_value_index(
        self, limit: int = 120, *, completed_only: bool = True, trade_date: date | None = None,
    ) -> tuple[tuple[datetime, float, tuple[str, ...], str, float, float, float], ...]:
        # 이전 버전이 잘못 남긴 장외·새벽 행은 차트와 시간대 통계에서
        # 영구적으로 제외한다. TOP20 지수의 유효 수집시간은 08:00~19:59다.
        conditions: list[str] = ["substr(minute,12,5)>='08:00'", "substr(minute,12,5)<'20:00'"]
        parameters: list[object] = []
        if completed_only:
            conditions.append("capture_state='realtime_complete'")
        if trade_date is not None:
            conditions.append("trade_date=?"); parameters.append(trade_date.isoformat())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                "SELECT minute,trade_value_eok,stock_codes,capture_state,"
                "kospi_trade_value_eok,kosdaq_trade_value_eok,unknown_trade_value_eok "
                f"FROM top20_trade_value_index {where} ORDER BY minute DESC LIMIT ?",
                (*parameters, max(1, int(limit))),
            ).fetchall()
        finally:
            connection.close()
        result = []
        for minute, value, raw_codes, state, kospi, kosdaq, unknown in reversed(rows):
            try:
                codes = tuple(str(code) for code in json.loads(str(raw_codes)))
                total = float(value)
                market_values = [float(kospi), float(kosdaq), float(unknown)]
                # 시장별 저장 기능을 넣기 전 기록은 합계만 있으므로 과거 막대를 유지한다.
                if total > 0 and sum(market_values) <= 0:
                    market_values[2] = total
                result.append((
                    datetime.fromisoformat(str(minute)), total, codes, str(state),
                    market_values[0], market_values[1], market_values[2],
                ))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(result)

    def load_top20_trade_value_index_for_date(
        self, trade_date: date,
    ) -> tuple[tuple[datetime, float, tuple[str, ...], str, float, float, float], ...]:
        return self.load_recent_top20_trade_value_index(
            1_440, completed_only=True, trade_date=trade_date,
        )

    def load_top20_daily_trade_values(
        self, limit: int = 365,
    ) -> tuple[tuple[date, float, float, float], ...]:
        """전체시장 값과 비교 가능한 KRX 정규장 TOP20 합계를 반환한다.

        분·5분·60분 기록에는 NXT 전후장을 그대로 보존하되, 일봉은
        KRX 전체시장 누적 거래대금과 같은 09:00~15:29 범위로 맞춘다.
        """
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute(
                "SELECT trade_date,SUM(kospi_trade_value_eok),SUM(kosdaq_trade_value_eok),"
                "SUM(CASE WHEN kospi_trade_value_eok+kosdaq_trade_value_eok+unknown_trade_value_eok<=0 "
                "THEN trade_value_eok ELSE unknown_trade_value_eok END) FROM top20_trade_value_index "
                "WHERE capture_state='realtime_complete' AND strftime('%w',trade_date) NOT IN ('0','6') "
                "AND substr(minute,12,5)>='09:00' AND substr(minute,12,5)<='15:29' "
                "GROUP BY trade_date "
                "ORDER BY trade_date DESC LIMIT ?", (max(1, int(limit)),),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            (date.fromisoformat(str(day)), float(kospi or 0), float(kosdaq or 0), float(unknown or 0))
            for day, kospi, kosdaq, unknown in reversed(rows)
        )

    def load_top20_statistics(self, days: int) -> tuple[
        tuple[tuple[str, float, int], ...],
        tuple[tuple[date, float, float, float], ...],
    ]:
        """수집 시간대 평균과 정규장 전체시장 대비 TOP20 비중을 반환한다."""
        cutoff = (date.today() - timedelta(days=max(1, int(days)) - 1)).isoformat()
        connection = sqlite3.connect(self._path)
        try:
            hourly = connection.execute(
                "SELECT substr(minute,12,2)||':00',AVG(trade_value_eok),COUNT(DISTINCT trade_date) "
                "FROM top20_trade_value_index WHERE capture_state='realtime_complete' AND trade_date>=? "
                "AND strftime('%w',trade_date) NOT IN ('0','6') "
                "AND substr(minute,12,5)>='08:00' AND substr(minute,12,5)<'20:00' "
                "GROUP BY substr(minute,12,2) "
                "ORDER BY AVG(trade_value_eok) DESC",
                (cutoff,),
            ).fetchall()
            top20 = connection.execute(
                "SELECT trade_date,SUM(trade_value_eok) FROM top20_trade_value_index "
                "WHERE capture_state='realtime_complete' AND trade_date>=? "
                "AND substr(minute,12,5)>='09:00' AND substr(minute,12,5)<='15:29' "
                "GROUP BY trade_date ORDER BY trade_date",
                (cutoff,),
            ).fetchall()
            market = connection.execute(
                "SELECT trade_date,market,MAX(trade_value_eok) FROM market_index_minute_bars "
                "WHERE trade_date>=? AND substr(minute,12,5)>='09:00' AND substr(minute,12,5)<='15:29' "
                "GROUP BY trade_date,market",
                (cutoff,),
            ).fetchall()
        finally:
            connection.close()
        market_by_day: dict[str, dict[str, float]] = defaultdict(dict)
        for day, name, value in market:
            market_by_day[str(day)][str(name)] = float(value or 0)
        comparisons = tuple(
            (
                date.fromisoformat(str(day)), float(value or 0),
                market_by_day.get(str(day), {}).get("kospi", 0.0),
                market_by_day.get(str(day), {}).get("kosdaq", 0.0),
            )
            for day, value in top20
        )
        return (
            tuple((str(hour), float(value or 0), int(day_count or 0)) for hour, value, day_count in hourly),
            comparisons,
        )

    def repair_top20_market_splits(self) -> int:
        """과거 합계 기록을 저장된 구성 종목·분봉·시장 정보로 다시 분리한다."""
        connection = sqlite3.connect(self._path)
        repaired = 0
        try:
            rows = connection.execute(
                "SELECT minute,trade_value_eok,stock_codes FROM top20_trade_value_index "
                "WHERE trade_value_eok>0 AND kospi_trade_value_eok=0 AND kosdaq_trade_value_eok=0"
            ).fetchall()
            for minute, total, raw_codes in rows:
                try:
                    codes = tuple(str(code) for code in json.loads(str(raw_codes)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not codes:
                    continue
                placeholders = ",".join("?" for _ in codes)
                values = connection.execute(
                    "SELECT s.market,COALESCE(m.trade_value_eok,0) FROM stocks s "
                    "LEFT JOIN minute_bars m ON m.stock_code=s.code AND m.minute=? "
                    f"WHERE s.code IN ({placeholders})",
                    (str(minute), *codes),
                ).fetchall()
                amounts = [0.0, 0.0, 0.0]; counts = [0, 0, 0]
                for market, value in values:
                    normalized = str(market).strip().upper()
                    index = 0 if ("KOSPI" in normalized or "코스피" in normalized or normalized in {"유가", "STK", "1"}) else (1 if ("KOSDAQ" in normalized or "코스닥" in normalized or normalized in {"KSQ", "2"}) else 2)
                    amounts[index] += float(value or 0.0); counts[index] += 1
                # 분봉이 아직 저장되지 않은 차액은 미확인으로 보존한다.
                amounts[2] += max(0.0, float(total) - sum(amounts))
                connection.execute(
                    "UPDATE top20_trade_value_index SET kospi_trade_value_eok=?,kosdaq_trade_value_eok=?,"
                    "unknown_trade_value_eok=?,kospi_stock_count=?,kosdaq_stock_count=?,unknown_stock_count=? "
                    "WHERE minute=?",
                    (*amounts, *counts, str(minute)),
                )
                repaired += 1
            connection.commit()
        finally:
            connection.close()
        return repaired

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
                "SELECT trade_date, stock_code, open_price, high_price, low_price, close_price, volume, trade_value_eok "
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
        for trade_date, code, open_price, high_price, low_price, close_price, volume, stored_trade_value in rows:
            key = (str(code), str(trade_date))
            if key not in targets:
                continue
            value, count = totals.get(key, (0.0, 0))
            value += (
                float(stored_trade_value)
                if stored_trade_value is not None
                else int(volume) * (int(open_price) + int(high_price) + int(low_price) + int(close_price)) / 4 / 100_000_000
            )
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
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (MARKET_DATA_METADATA_TABLE,),
            ).fetchone():
                connection.execute(
                    f"DELETE FROM {MARKET_DATA_METADATA_TABLE} "
                    "WHERE dataset_kind='minute_bar' AND substr(observation_key,1,10) < ?",
                    (cutoff.isoformat(),),
                )
            connection.execute("DELETE FROM minute_history_sync_log WHERE trade_date < ?", (cutoff.isoformat(),))
            connection.execute("DELETE FROM market_index_minute_bars WHERE trade_date < ?", (cutoff.isoformat(),))
            connection.commit()
        finally:
            connection.close()

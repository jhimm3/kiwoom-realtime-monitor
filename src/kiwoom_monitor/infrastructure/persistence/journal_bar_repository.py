"""매매일지 분봉·일봉·시장지수 봉과 백필 상태 저장소."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    ObservationOrigin,
)
from kiwoom_monitor.infrastructure.persistence.local_bar_observations import (
    local_daily_bar_observation,
    local_minute_bar_observation,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    upsert_market_data_metadata,
)

@dataclass(frozen=True)
class BarBackfillState:
    trade_date: date
    stock_code: str
    state: str
    bar_count: int
    message: str = ""

class JournalBarRepositoryMixin:
    _path: Path

    def mark_bar_backfill(self, code: str, day: date, state: str, message: str = "", now: datetime | None = None) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._mark_bar_backfill(connection, code, day, state, message, updated_at)

    @staticmethod
    def _mark_bar_backfill(
        connection: sqlite3.Connection,
        code: str,
        day: date,
        state: str,
        message: str,
        updated_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO journal_bar_backfill VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, stock_code) DO UPDATE SET state=excluded.state, "
            "message=excluded.message, updated_at=excluded.updated_at",
            (day.isoformat(), code, state, message[:500], updated_at),
        )

    def bar_backfill_state(self, code: str, day: date) -> BarBackfillState:
        with closing(sqlite3.connect(self._path)) as connection:
            bar_row = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN source='after_close_confirmed' THEN 1 ELSE 0 END) "
                "FROM journal_minute_bars WHERE trade_date=? AND stock_code=?",
                (day.isoformat(), code),
            ).fetchone()
            status_row = connection.execute(
                "SELECT state, message FROM journal_bar_backfill WHERE trade_date=? AND stock_code=?",
                (day.isoformat(), code),
            ).fetchone()
        count = int(bar_row[0] or 0) if bar_row else 0
        confirmed = int(bar_row[1] or 0) if bar_row else 0
        if confirmed > 0:
            return BarBackfillState(day, code, "확정", count)
        if status_row and status_row[0] == "실패":
            return BarBackfillState(day, code, "실패", count, str(status_row[1] or ""))
        if count > 0:
            return BarBackfillState(day, code, "일부", count)
        return BarBackfillState(day, code, "미조회", 0)

    def bar_backfill_states(
        self, candidates: tuple[tuple[str, date], ...],
    ) -> dict[tuple[str, date], BarBackfillState]:
        """여러 회차의 상태를 두 SQL로 읽어 목록 렌더링의 N+1을 피한다."""
        unique = tuple(dict.fromkeys(candidates))
        if not unique:
            return {}
        codes = tuple(dict.fromkeys(code for code, _day in unique))
        first_day = min(day for _code, day in unique).isoformat()
        last_day = max(day for _code, day in unique).isoformat()
        placeholders = ",".join("?" for _ in codes)
        wanted = {(code, day.isoformat()) for code, day in unique}
        with closing(sqlite3.connect(self._path)) as connection:
            bar_rows = connection.execute(
                "SELECT trade_date,stock_code,COUNT(*),"
                "SUM(CASE WHEN source='after_close_confirmed' THEN 1 ELSE 0 END) "
                "FROM journal_minute_bars WHERE trade_date>=? AND trade_date<=? "
                f"AND stock_code IN ({placeholders}) GROUP BY trade_date,stock_code",
                (first_day, last_day, *codes),
            ).fetchall()
            status_rows = connection.execute(
                "SELECT trade_date,stock_code,state,message FROM journal_bar_backfill "
                "WHERE trade_date>=? AND trade_date<=? "
                f"AND stock_code IN ({placeholders})",
                (first_day, last_day, *codes),
            ).fetchall()
        bars = {
            (str(day), str(code)): (int(count or 0), int(confirmed or 0))
            for day, code, count, confirmed in bar_rows
            if (str(code), str(day)) in wanted
        }
        statuses = {
            (str(day), str(code)): (str(state), str(message or ""))
            for day, code, state, message in status_rows
            if (str(code), str(day)) in wanted
        }
        result: dict[tuple[str, date], BarBackfillState] = {}
        for code, day in unique:
            count, confirmed = bars.get((day.isoformat(), code), (0, 0))
            state, message = statuses.get((day.isoformat(), code), ("", ""))
            if confirmed > 0:
                value = BarBackfillState(day, code, "확정", count)
            elif state == "실패":
                value = BarBackfillState(day, code, "실패", count, message)
            elif count > 0:
                value = BarBackfillState(day, code, "일부", count)
            else:
                value = BarBackfillState(day, code, "미조회", 0)
            result[(code, day)] = value
        return result

    def bar_backfill_candidates(self, before: date) -> tuple[tuple[str, date], ...]:
        """저장된 체결 중 장이 끝난 날짜·종목만 오래된 순서로 반환한다."""
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT DISTINCT stock_code, substr(filled_at, 1, 10) AS trade_date "
                "FROM trade_fills WHERE substr(filled_at, 1, 10) < ? "
                "ORDER BY trade_date, stock_code",
                (before.isoformat(),),
            ).fetchall()
        return tuple((str(row[0]), date.fromisoformat(str(row[1]))) for row in rows)

    def list_bar_backfill_states(self, before: date | None = None) -> tuple[BarBackfillState, ...]:
        cutoff = before or date.max
        candidates = self.bar_backfill_candidates(cutoff)
        values = self.bar_backfill_states(candidates)
        return tuple(values[candidate] for candidate in candidates)

    def upsert_daily_bars(
        self, code: str, rows: tuple[tuple[object, ...], ...], now: datetime | None = None,
    ) -> None:
        confirmed_at = (now or datetime.now()).isoformat(timespec="seconds")
        values: list[tuple[object, ...]] = []
        for row in rows:
            if len(row) < 7:
                continue
            try:
                values.append((
                    datetime.fromisoformat(str(row[0])).date().isoformat(), code,
                    int(row[1]), int(row[2]), int(row[3]), int(row[4]), int(row[5]),
                    float(row[6]) if row[6] is not None else 0.0, confirmed_at,
                ))
            except (TypeError, ValueError):
                # 손상된 일봉 한 행 때문에 매매일지 프로세스 전체를 종료하지 않는다.
                continue
        if not values:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO journal_daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(trade_date, stock_code) DO UPDATE SET open_price=excluded.open_price, "
                    "high_price=excluded.high_price, low_price=excluded.low_price, close_price=excluded.close_price, "
                    "volume=excluded.volume, trade_value_eok=excluded.trade_value_eok, confirmed_at=excluded.confirmed_at",
                    values,
                )
                available_at = now or datetime.now()
                for value in values:
                    trading_day = date.fromisoformat(str(value[0]))
                    observation = local_daily_bar_observation(
                        code,
                        trading_day,
                        value,
                        available_at=available_at,
                        source="journal-daily-query",
                        origin=ObservationOrigin.QUERY,
                    )
                    upsert_market_data_metadata(
                        connection, trading_day.isoformat(), observation
                    )

    def load_daily_bars(self, code: str, base_day: date, limit: int = 250) -> tuple[tuple[object, ...], ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT trade_date, open_price, high_price, low_price, close_price, volume, trade_value_eok "
                "FROM journal_daily_bars WHERE stock_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
                (code, base_day.isoformat(), limit),
            ).fetchall()
        return tuple((
            datetime.combine(date.fromisoformat(str(row[0])), time()).isoformat(timespec="minutes"),
            *row[1:], "daily_confirmed",
        ) for row in reversed(rows))

    def import_monitor_daily_bars(self, monitor_path: Path, code: str, base_day: date, limit: int = 250) -> int:
        if not monitor_path.is_file():
            return 0
        uri = monitor_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
            rows = connection.execute(
                "SELECT trade_date, open_price, high_price, low_price, close_price, volume, "
                "COALESCE(trade_value_eok, 0) "
                "FROM daily_bars WHERE stock_code=? AND trade_date<=? AND open_price IS NOT NULL "
                "AND high_price IS NOT NULL AND low_price IS NOT NULL AND close_price IS NOT NULL "
                "AND volume IS NOT NULL ORDER BY trade_date DESC LIMIT ?",
                (code, base_day.isoformat(), limit),
            ).fetchall()
        converted = tuple((datetime.combine(date.fromisoformat(str(row[0])), time()).isoformat(timespec="minutes"), *row[1:]) for row in reversed(rows))
        self.upsert_daily_bars(code, converted)
        return len(converted)

    def upsert_bars(self, code: str, bars: tuple[MinuteOhlcv, ...], source: str, confirmed_at: datetime | None = None,
                    *, preserve_confirmed: bool = False) -> None:
        if not bars:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._upsert_bars(connection, code, bars, source, confirmed_at, preserve_confirmed)

    def save_bar_backfill_result(
        self,
        code: str,
        day: date,
        bars: tuple[MinuteOhlcv, ...],
        confirmed_at: datetime | None = None,
    ) -> bool:
        """한 날짜의 보완 분봉과 완료 상태를 모두 저장하거나 모두 되돌린다."""
        matching = tuple(bar for bar in bars if bar.minute.date() == day)
        saved_at = confirmed_at or datetime.now()
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                if not matching:
                    self._mark_bar_backfill(
                        connection,
                        code,
                        day,
                        "실패",
                        "해당 거래일의 분봉이 반환되지 않았습니다.",
                        saved_at.isoformat(timespec="seconds"),
                    )
                    return False
                self._upsert_bars(
                    connection, code, matching, "after_close_confirmed", saved_at, False,
                )
                self._mark_bar_backfill(
                    connection,
                    code,
                    day,
                    "확정",
                    "",
                    saved_at.isoformat(timespec="seconds"),
                )
        return True

    @staticmethod
    def _upsert_bars(
        connection: sqlite3.Connection,
        code: str,
        bars: tuple[MinuteOhlcv, ...],
        source: str,
        confirmed_at: datetime | None,
        preserve_confirmed: bool,
    ) -> None:
        rows = [(
            bar.minute.date().isoformat(), code, bar.minute.isoformat(timespec="minutes"),
            bar.open_price, bar.high_price, bar.low_price, bar.close_price, bar.volume,
            float(bar.trade_value_eok), source,
            confirmed_at.isoformat(timespec="seconds") if confirmed_at else None,
        ) for bar in bars]
        update_guard = " WHERE journal_minute_bars.source NOT IN ('api_confirmed', 'after_close_confirmed')" if preserve_confirmed else ""
        preserved = {}
        if preserve_confirmed:
            preserved = {
                str(minute): (str(saved_source), saved_at)
                for minute, saved_source, saved_at in connection.execute(
                    "SELECT minute,source,confirmed_at FROM journal_minute_bars "
                    "WHERE stock_code=? AND source IN ('api_confirmed','after_close_confirmed')",
                    (code,),
                )
            }
        connection.executemany(
            "INSERT INTO journal_minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, stock_code, minute) DO UPDATE SET "
            "open_price=excluded.open_price, high_price=excluded.high_price, low_price=excluded.low_price, "
            "close_price=excluded.close_price, volume=excluded.volume, trade_value_eok=excluded.trade_value_eok, "
            "source=excluded.source, confirmed_at=excluded.confirmed_at" + update_guard,
            rows,
        )
        for bar in bars:
            key = bar.minute.isoformat(timespec="minutes")
            saved_source, saved_at = preserved.get(key, (source, None))
            effective_available_at = confirmed_at or datetime.now()
            if saved_at:
                try:
                    effective_available_at = datetime.fromisoformat(str(saved_at))
                except ValueError:
                    pass
            if saved_source == "realtime_partial":
                completeness = DataCompleteness.PARTIAL
                origin = ObservationOrigin.REALTIME
                value_kind = DataValueKind.UNKNOWN
            elif saved_source == "after_close_confirmed":
                completeness = DataCompleteness.COMPLETE
                origin = ObservationOrigin.BACKFILLED
                value_kind = DataValueKind.ESTIMATED
            else:
                completeness = DataCompleteness.COMPLETE
                origin = ObservationOrigin.QUERY
                value_kind = DataValueKind.ESTIMATED
            observation = local_minute_bar_observation(
                code,
                bar.minute,
                bar,
                available_at=effective_available_at,
                source=f"journal-{saved_source}",
                actual_trade_value=False,
                value_kind=value_kind,
                completeness=completeness,
                origin=origin,
            )
            upsert_market_data_metadata(
                connection,
                bar.minute.isoformat(timespec="minutes"),
                observation,
                preserve_complete=preserve_confirmed,
            )

    def import_live_bars(self, monitor_path: Path, code: str, now: datetime) -> int:
        """메인 DB를 읽기 전용으로 짧게 열어 실시간 봉을 일지 DB로 복사한다."""
        if not monitor_path.is_file():
            return 0
        uri = monitor_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
            rows = connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok "
                "FROM minute_bars WHERE trade_date=? AND stock_code=? ORDER BY minute",
                (now.date().isoformat(), code),
            ).fetchall()
        bars = tuple(MinuteOhlcv(datetime.fromisoformat(row[0]), *map(int, row[1:6]), trade_value_eok_override=float(row[6] or 0)) for row in rows)
        self.upsert_bars(code, bars, "realtime_partial", preserve_confirmed=True)
        return len(bars)

    def load_bars(self, code: str, now: datetime) -> tuple[tuple[object, ...], ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            return tuple(connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
                "FROM journal_minute_bars WHERE trade_date=? AND stock_code=? ORDER BY minute",
                (now.date().isoformat(), code),
            ).fetchall())

    def load_chart_bars(self, code: str, now: datetime) -> tuple[tuple[object, ...], ...]:
        """선택일과 DB에 존재하는 직전 거래일 분봉을 시간순으로 읽는다."""
        target = now.date().isoformat()
        with closing(sqlite3.connect(self._path)) as connection:
            previous = connection.execute(
                "SELECT MAX(trade_date) FROM journal_minute_bars WHERE stock_code=? AND trade_date<?",
                (code, target),
            ).fetchone()[0]
            days = (target,) if not previous else (previous, target)
            placeholders = ",".join("?" for _ in days)
            return tuple(connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
                f"FROM journal_minute_bars WHERE stock_code=? AND trade_date IN ({placeholders}) ORDER BY minute",
                (code, *days),
            ).fetchall())

    def load_chart_bars_range(
        self, code: str, start_day: date, end_day: date,
    ) -> tuple[tuple[object, ...], ...]:
        """매매 묶음의 첫날부터 마지막 날까지와 직전 저장 거래일을 읽는다."""
        if start_day > end_day:
            start_day, end_day = end_day, start_day
        start_text, end_text = start_day.isoformat(), end_day.isoformat()
        with closing(sqlite3.connect(self._path)) as connection:
            previous = connection.execute(
                "SELECT MAX(trade_date) FROM journal_minute_bars WHERE stock_code=? AND trade_date<?",
                (code, start_text),
            ).fetchone()[0]
            conditions = "trade_date BETWEEN ? AND ?"
            parameters: tuple[object, ...] = (code, start_text, end_text)
            if previous:
                conditions = f"({conditions} OR trade_date=?)"
                parameters = (*parameters, previous)
            return tuple(connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
                f"FROM journal_minute_bars WHERE stock_code=? AND {conditions} ORDER BY minute",
                parameters,
            ).fetchall())

    def load_monitor_market_index_bars(
        self, monitor_path: Path, market: str, start_day: date, end_day: date,
    ) -> tuple[tuple[object, ...], ...]:
        """메인 DB에 저장된 지수 1분봉에서 선택 기간과 직전 거래일을 읽는다."""
        if market not in {"kospi", "kosdaq"} or not monitor_path.is_file():
            return ()
        if start_day > end_day:
            start_day, end_day = end_day, start_day
        uri = monitor_path.resolve().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                if not connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_index_minute_bars'"
                ).fetchone():
                    return ()
                start_text, end_text = start_day.isoformat(), end_day.isoformat()
                previous = connection.execute(
                    "SELECT MAX(trade_date) FROM market_index_minute_bars WHERE market=? AND trade_date<?",
                    (market, start_text),
                ).fetchone()[0]
                conditions = "trade_date BETWEEN ? AND ?"
                parameters: tuple[object, ...] = (market, start_text, end_text)
                if previous:
                    conditions = f"({conditions} OR trade_date=?)"
                    parameters = (*parameters, previous)
                raw = connection.execute(
                    "SELECT minute,open_value,high_value,low_value,close_value,0,"
                    "trade_value_eok,'market_index' FROM market_index_minute_bars "
                    f"WHERE market=? AND {conditions} ORDER BY minute", parameters,
                ).fetchall()
                result = []; previous_by_day: dict[str, float] = {}
                for row in raw:
                    day = str(row[0])[:10]; cumulative = row[6]
                    minute_value = None
                    if cumulative is not None:
                        current = float(cumulative); previous_value = previous_by_day.get(day)
                        minute_value = max(0.0, current - previous_value) if previous_value is not None else None
                        previous_by_day[day] = current
                    result.append((*row[:6], minute_value, row[7]))
                return tuple(result)
        except sqlite3.Error:
            return ()

    def load_monitor_market_index_daily(self, monitor_path: Path, market: str, base_day: date) -> tuple[tuple[object, ...], ...]:
        if market not in {"kospi", "kosdaq"} or not monitor_path.is_file(): return ()
        try:
            uri = monitor_path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_index_daily_bars'").fetchone(): return ()
                return tuple((*row, "market_index_daily") for row in connection.execute(
                    "SELECT trade_date||'T00:00',open_value,high_value,low_value,close_value,volume,trade_value_eok "
                    "FROM market_index_daily_bars WHERE market=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 250",
                    (market, base_day.isoformat()),
                ).fetchall()[::-1])
        except sqlite3.Error: return ()

    def load_monitor_stock_catalog(self, monitor_path: Path) -> tuple[tuple[str, str], ...]:
        if not monitor_path.is_file(): return ()
        try:
            uri = monitor_path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                return tuple((str(code), str(name)) for code, name in connection.execute(
                    "SELECT code,name FROM stocks WHERE code<>'' AND name<>'' ORDER BY name"
                ).fetchall())
        except sqlite3.Error:
            return ()

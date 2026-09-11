"""체결 시점 시장 스냅샷 저장·조회·장후 보완 저장소."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from kiwoom_monitor.domain.snapshot_provenance import mark_news_backfilled
from kiwoom_monitor.domain.trade_snapshot_observations import entry_context_observations
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    upsert_market_data_metadata,
)

@dataclass(frozen=True)
class TradeEntrySnapshot:
    execution_key: str
    order_no: str
    stock_code: str
    stock_name: str
    side: str
    executed_at: datetime
    price: int
    quantity: int
    market: str
    rank: int | None = None
    trade_value_1m_eok: float | None = None
    trade_value_5m_eok: float | None = None
    themes: tuple[str, ...] = ()
    theme_ranks: dict[str, int] | None = None
    high_distance_percent: float | None = None
    news: tuple[dict[str, object], ...] = ()
    investor_flow: dict[str, object] | None = None
    orderbook: dict[str, object] | None = None
    market_state: dict[str, object] | None = None
    capture_state: str = "realtime_partial"

class JournalSnapshotRepositoryMixin:
    _path: Path

    def save_entry_snapshot(self, value: TradeEntrySnapshot, now: datetime | None = None) -> None:
        """실시간 체결 시점 자료를 중복 체결번호 기준으로 안전하게 저장한다."""
        captured_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = (
            value.execution_key, value.order_no, value.stock_code, value.stock_name, value.side,
            value.executed_at.isoformat(timespec="seconds"), value.price, value.quantity, value.market,
            value.rank, value.trade_value_1m_eok, value.trade_value_5m_eok,
            json.dumps(value.themes, ensure_ascii=False),
            json.dumps(value.theme_ranks or {}, ensure_ascii=False), value.high_distance_percent,
            json.dumps(value.news, ensure_ascii=False), json.dumps(value.investor_flow or {}, ensure_ascii=False),
            json.dumps(value.orderbook or {}, ensure_ascii=False), json.dumps(value.market_state or {}, ensure_ascii=False),
            value.capture_state, captured_at,
        )
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_entry_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(execution_key) DO UPDATE SET "
                    "rank=COALESCE(excluded.rank,trade_entry_snapshots.rank), trade_value_1m_eok=COALESCE(excluded.trade_value_1m_eok,trade_entry_snapshots.trade_value_1m_eok), "
                    "trade_value_5m_eok=COALESCE(excluded.trade_value_5m_eok,trade_entry_snapshots.trade_value_5m_eok), themes_json=excluded.themes_json, "
                    "theme_ranks_json=excluded.theme_ranks_json, high_distance_percent=COALESCE(excluded.high_distance_percent,trade_entry_snapshots.high_distance_percent), "
                    "news_json=excluded.news_json, investor_flow_json=CASE "
                    "WHEN COALESCE(json_extract(trade_entry_snapshots.investor_flow_json,'$.backfilled'),0)=1 "
                    "AND COALESCE(json_extract(excluded.investor_flow_json,'$.backfilled'),0)<>1 "
                    "THEN trade_entry_snapshots.investor_flow_json ELSE excluded.investor_flow_json END, "
                    "orderbook_json=excluded.orderbook_json, "
                    "market_state_json=excluded.market_state_json, capture_state=excluded.capture_state, captured_at=excluded.captured_at",
                    payload,
                )
                stored = _snapshot_for_key(connection, value.execution_key)
                if stored is not None:
                    _save_snapshot_metadata(
                        connection, stored, datetime.fromisoformat(captured_at)
                    )

    def load_entry_snapshots(self, code: str, start: datetime, end: datetime) -> tuple[TradeEntrySnapshot, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM trade_entry_snapshots "
                "WHERE stock_code=? AND executed_at>=? AND executed_at<? ORDER BY executed_at",
                (code, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(_snapshot_from_row(row) for row in rows)

    def program_backfill_candidates(self, trade_date: date, codes: tuple[str, ...]) -> tuple[TradeEntrySnapshot, ...]:
        if not codes:
            return ()
        start = datetime.combine(trade_date, time.min)
        end = start + timedelta(days=1)
        values: list[TradeEntrySnapshot] = []
        for code in codes:
            values.extend(
                snapshot for snapshot in self.load_entry_snapshots(code, start, end)
                if not bool(((snapshot.investor_flow or {}).get("program_trade") or {}).get("available"))
            )
        return tuple(values)

    def investor_backfill_candidates(self, trade_date: date, codes: tuple[str, ...]) -> tuple[TradeEntrySnapshot, ...]:
        if not codes:
            return ()
        start = datetime.combine(trade_date, time.min); end = start + timedelta(days=1)
        values: list[TradeEntrySnapshot] = []
        for code in codes:
            for snapshot in self.load_entry_snapshots(code, start, end):
                investor = snapshot.investor_flow or {}
                legacy_zero = (
                    investor.get("foreign_net_buy_quantity") == 0
                    and investor.get("institution_net_buy_quantity") == 0
                    and str(investor.get("as_of_date", "")) == trade_date.strftime("%Y%m%d")
                )
                # 과거 버전은 장중에 외국인만 0이고 기관 값이 먼저 들어온 경우도
                # 확정·보완 완료로 잘못 저장했다. 이 형태도 한 번 다시 조회한다.
                legacy_partial_zero = (
                    investor.get("foreign_net_buy_quantity") == 0
                    and investor.get("institution_net_buy_quantity") is not None
                    and str(investor.get("as_of_date", "")) == trade_date.strftime("%Y%m%d")
                )
                missing_values = (
                    investor.get("foreign_net_buy_quantity") is None
                    or investor.get("institution_net_buy_quantity") is None
                    or str(investor.get("as_of_date", "")) != trade_date.strftime("%Y%m%d")
                )
                requested_before_close = False
                if not bool(investor.get("backfilled")):
                    try:
                        requested = datetime.fromisoformat(str(investor.get("requested_at", "")))
                        requested_before_close = (
                            requested.date() == trade_date and requested.time() < time(20, 5)
                        )
                    except ValueError:
                        pass
                if (
                    str(investor.get("status", "")) == "pending_close"
                    or not bool(investor.get("available"))
                    or legacy_zero
                    or legacy_partial_zero
                    or missing_values
                    or requested_before_close
                ):
                    values.append(snapshot)
        return tuple(values)

    def entry_snapshot_codes(self, trade_date: date) -> tuple[str, ...]:
        start = datetime.combine(trade_date, time.min); end = start + timedelta(days=1)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT DISTINCT stock_code FROM trade_entry_snapshots WHERE executed_at>=? AND executed_at<?",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(str(row[0]) for row in rows if row and row[0])

    def save_investor_flow_backfill(self, execution_key: str, value: dict[str, object]) -> None:
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            row = connection.execute(
                "SELECT investor_flow_json FROM trade_entry_snapshots WHERE execution_key=?", (execution_key,),
            ).fetchone()
            if not row:
                return
            try:
                previous = dict(json.loads(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                previous = {}
            program = previous.get("program_trade")
            merged = dict(value)
            if isinstance(program, dict):
                merged["program_trade"] = program
            with connection:
                captured_at = datetime.now()
                connection.execute(
                    "UPDATE trade_entry_snapshots SET investor_flow_json=?, captured_at=? WHERE execution_key=?",
                    (json.dumps(merged, ensure_ascii=False), captured_at.isoformat(timespec="seconds"), execution_key),
                )
                snapshot = _snapshot_for_key(connection, execution_key)
                if snapshot is not None:
                    _save_snapshot_metadata(connection, snapshot, captured_at, fields=("investor_flow",))

    def save_snapshot_news_backfill(self, execution_key: str, news: tuple[dict[str, object], ...]) -> None:
        backfilled_news = mark_news_backfilled(news)
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            with connection:
                captured_at = datetime.now()
                connection.execute(
                    "UPDATE trade_entry_snapshots SET news_json=?, captured_at=? WHERE execution_key=?",
                    (json.dumps(backfilled_news, ensure_ascii=False), captured_at.isoformat(timespec="seconds"), execution_key),
                )
                snapshot = _snapshot_for_key(connection, execution_key)
                if snapshot is not None:
                    _save_snapshot_metadata(connection, snapshot, captured_at, fields=("news",))

    def save_program_trade_backfill(self, execution_key: str, program_trade: dict[str, object]) -> None:
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            row = connection.execute(
                "SELECT investor_flow_json FROM trade_entry_snapshots WHERE execution_key=?", (execution_key,),
            ).fetchone()
            if not row:
                return
            try:
                investor = dict(json.loads(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                investor = {}
            investor["program_trade"] = program_trade
            with connection:
                captured_at = datetime.now()
                connection.execute(
                    "UPDATE trade_entry_snapshots SET investor_flow_json=?, captured_at=? WHERE execution_key=?",
                    (json.dumps(investor, ensure_ascii=False), captured_at.isoformat(timespec="seconds"), execution_key),
                )
                snapshot = _snapshot_for_key(connection, execution_key)
                if snapshot is not None:
                    _save_snapshot_metadata(connection, snapshot, captured_at, fields=("program_trade",))


_SNAPSHOT_COLUMNS = (
    "execution_key,order_no,stock_code,stock_name,side,executed_at,price,quantity,market,rank,"
    "trade_value_1m_eok,trade_value_5m_eok,themes_json,theme_ranks_json,high_distance_percent,"
    "news_json,investor_flow_json,orderbook_json,market_state_json,capture_state"
)


def _snapshot_for_key(
    connection: sqlite3.Connection, execution_key: str
) -> TradeEntrySnapshot | None:
    row = connection.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM trade_entry_snapshots WHERE execution_key=?",
        (execution_key,),
    ).fetchone()
    return _snapshot_from_row(row) if row is not None else None


def _snapshot_from_row(row: tuple[object, ...]) -> TradeEntrySnapshot:
    return TradeEntrySnapshot(
        str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]),
        datetime.fromisoformat(str(row[5])), int(row[6]), int(row[7]), str(row[8]), row[9],
        row[10], row[11], tuple(json.loads(str(row[12]))), dict(json.loads(str(row[13]))), row[14],
        tuple(json.loads(str(row[15]))), dict(json.loads(str(row[16]))),
        dict(json.loads(str(row[17]))), dict(json.loads(str(row[18]))), str(row[19]),
    )


def _save_snapshot_metadata(
    connection: sqlite3.Connection,
    snapshot: TradeEntrySnapshot,
    available_at: datetime,
    *,
    fields: tuple[str, ...] | None = None,
) -> None:
    observations = entry_context_observations(snapshot, available_at)
    selected = fields or tuple(observations)
    for field in selected:
        observation = observations[field]
        upsert_market_data_metadata(
            connection,
            snapshot.execution_key,
            observation,
            preserve_complete=True,
            preserve_backfilled=True,
        )

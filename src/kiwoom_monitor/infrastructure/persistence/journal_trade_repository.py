"""매매유형·체결·수동 묶음·복기·실제 비용 저장소."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeReview
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification


class JournalTradeRepositoryMixin:
    _path: Path
    def save_trade_setup(
        self, group_id: str, classification: TradeSetupClassification, manual_type: str = "",
        now: datetime | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_setup_classifications VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET automatic_type=excluded.automatic_type, "
                    "confidence=excluded.confidence, evidence_json=excluded.evidence_json, "
                    "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                    (group_id, classification.setup_type, classification.confidence,
                     json.dumps(list(classification.evidence), ensure_ascii=False), manual_type, updated_at),
                )

    def load_trade_setup(self, group_id: str) -> tuple[TradeSetupClassification, str] | None:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT automatic_type, confidence, evidence_json, manual_type "
                "FROM trade_setup_classifications WHERE group_id=?", (group_id,),
            ).fetchone()
        return self._trade_setup_from_row(row) if row else None

    def load_trade_setups(
        self, group_ids: tuple[str, ...],
    ) -> dict[str, tuple[TradeSetupClassification, str]]:
        if not group_ids:
            return {}
        result: dict[str, tuple[TradeSetupClassification, str]] = {}
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(group_ids):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT group_id,automatic_type,confidence,evidence_json,manual_type "
                    f"FROM trade_setup_classifications WHERE group_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                result.update({str(row[0]): self._trade_setup_from_row(row[1:]) for row in rows})
        return result

    @staticmethod
    def _trade_setup_from_row(row: tuple[object, ...]) -> tuple[TradeSetupClassification, str]:
        try:
            evidence = tuple(str(value) for value in json.loads(str(row[2])))
        except (TypeError, ValueError):
            evidence = ()
        return TradeSetupClassification(str(row[0]), int(row[1]), evidence), str(row[3] or "")

    def save_trade_setup_cycle_override(
        self, group_id: str, cycle_index: int, manual_type: str, now: datetime | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                if manual_type:
                    connection.execute(
                        "INSERT INTO trade_setup_cycle_overrides VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(group_id, cycle_index) DO UPDATE SET "
                        "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                        (group_id, cycle_index, manual_type, updated_at),
                    )
                    self._record_sync_state(
                        connection, "journal_cycle_overrides", f"{group_id}|{cycle_index}", False, updated_at,
                    )
                else:
                    connection.execute(
                        "DELETE FROM trade_setup_cycle_overrides WHERE group_id=? AND cycle_index=?",
                        (group_id, cycle_index),
                    )
                    self._record_sync_state(
                        connection, "journal_cycle_overrides", f"{group_id}|{cycle_index}", True, updated_at,
                    )

    def load_trade_setup_cycle_overrides(self, group_id: str) -> dict[int, str]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT cycle_index, manual_type FROM trade_setup_cycle_overrides WHERE group_id=?",
                (group_id,),
            ).fetchall()
        return {int(index): str(value) for index, value in rows if str(value)}

    def load_trade_setup_cycle_overrides_many(
        self, group_ids: tuple[str, ...],
    ) -> dict[str, dict[int, str]]:
        if not group_ids:
            return {}
        result: dict[str, dict[int, str]] = {}
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(group_ids):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT group_id,cycle_index,manual_type FROM trade_setup_cycle_overrides "
                    f"WHERE group_id IN ({placeholders})", chunk,
                ).fetchall()
                for group_id, index, value in rows:
                    if str(value):
                        result.setdefault(str(group_id), {})[int(index)] = str(value)
        return result

    def save_trade_analysis_setup(
        self,
        group_id: str,
        classification: TradeSetupClassification,
        legacy_override: tuple[int, str] | None = None,
        now: datetime | None = None,
    ) -> None:
        """자동 판정 저장과 과거 단일 수동 유형 이전을 원자적으로 확정한다."""
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_setup_classifications VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET automatic_type=excluded.automatic_type, "
                    "confidence=excluded.confidence, evidence_json=excluded.evidence_json, "
                    "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                    (
                        group_id, classification.setup_type, classification.confidence,
                        json.dumps(list(classification.evidence), ensure_ascii=False), "", updated_at,
                    ),
                )
                if legacy_override is not None:
                    cycle_index, manual_type = legacy_override
                    connection.execute(
                        "INSERT INTO trade_setup_cycle_overrides VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(group_id, cycle_index) DO UPDATE SET "
                        "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                        (group_id, cycle_index, manual_type, updated_at),
                    )
                    self._record_sync_state(
                        connection, "journal_cycle_overrides", f"{group_id}|{cycle_index}", False, updated_at,
                    )

    @staticmethod
    def _record_sync_state(
        connection: sqlite3.Connection,
        collection: str,
        document_key: str,
        is_deleted: bool,
        updated_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO journal_sync_states VALUES (?, ?, ?, ?) "
            "ON CONFLICT(collection, document_key) DO UPDATE SET "
            "is_deleted=excluded.is_deleted, updated_at=excluded.updated_at",
            (collection, document_key, int(is_deleted), updated_at),
        )

    def upsert_fills(self, fills: tuple[TradeFill, ...]) -> None:
        if not fills:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._upsert_fills(connection, fills)

    def upsert_history_sync(
        self,
        fills: tuple[TradeFill, ...],
        costs: tuple[DailyTradeCost, ...],
        now: datetime | None = None,
    ) -> None:
        """한 번의 체결 조회 결과와 실제 비용을 모두 반영하거나 모두 되돌린다."""
        if not fills and not costs:
            return
        confirmed_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._upsert_fills(connection, fills)
                self._upsert_trade_costs(connection, costs, confirmed_at)

    @staticmethod
    def _upsert_fills(connection: sqlite3.Connection, fills: tuple[TradeFill, ...]) -> None:
        rows = tuple((
            fill.order_no, fill.stock_code, fill.stock_name, fill.side,
            fill.filled_at.isoformat(timespec="seconds"), fill.quantity, fill.price,
            fill.order_type, fill.market,
        ) for fill in fills)
        connection.executemany(
            "INSERT INTO trade_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(order_no, stock_code, filled_at, side) DO UPDATE SET "
            "stock_name=excluded.stock_name, quantity=excluded.quantity, price=excluded.price, "
            "order_type=excluded.order_type, market=excluded.market",
            rows,
        )

    def load_fills(self, start: datetime, end: datetime) -> tuple[TradeFill, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT order_no, stock_code, stock_name, side, filled_at, quantity, price, order_type, market "
                "FROM trade_fills WHERE filled_at>=? AND filled_at<? ORDER BY filled_at DESC",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(TradeFill(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5], row[6], row[7], row[8]) for row in rows)

    def load_fills_with_entry_context(
        self, start: datetime, end: datetime, lookback_days: int = 365,
    ) -> tuple[TradeFill, ...]:
        """Load prior fills only for stocks traded in the requested period.

        This lets a sell inside the date filter find its earlier buy without
        mixing unrelated stocks or completed older episodes into the screen.
        """
        with closing(sqlite3.connect(self._path)) as connection:
            codes = tuple(
                str(row[0]) for row in connection.execute(
                    "SELECT DISTINCT stock_code FROM trade_fills WHERE filled_at>=? AND filled_at<?",
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                ).fetchall()
            )
            if not codes:
                return ()
            context_start = start - timedelta(days=max(0, int(lookback_days)))
            placeholders = ",".join("?" for _ in codes)
            rows = connection.execute(
                "SELECT order_no, stock_code, stock_name, side, filled_at, quantity, price, order_type, market "
                f"FROM trade_fills WHERE stock_code IN ({placeholders}) AND filled_at>=? AND filled_at<? "
                "ORDER BY filled_at DESC",
                (*codes, context_start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(
            TradeFill(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5], row[6], row[7], row[8])
            for row in rows
        )

    def load_group_overrides(self) -> dict[str, str]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute("SELECT fill_key, group_id FROM trade_group_overrides").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def assign_group(self, fill_keys: tuple[str, ...], group_id: str, now: datetime | None = None) -> None:
        if not fill_keys or not group_id:
            return
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO trade_group_overrides VALUES (?, ?, ?) "
                    "ON CONFLICT(fill_key) DO UPDATE SET group_id=excluded.group_id, updated_at=excluded.updated_at",
                    ((key, group_id, updated_at) for key in fill_keys),
                )
                for key in fill_keys:
                    self._record_sync_state(
                        connection, "journal_group_overrides", key, False, updated_at,
                    )

    def clear_group_assignments(self, fill_keys: tuple[str, ...]) -> None:
        if not fill_keys:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany("DELETE FROM trade_group_overrides WHERE fill_key=?", ((key,) for key in fill_keys))
                deleted_at = datetime.now().isoformat(timespec="microseconds")
                for key in fill_keys:
                    self._record_sync_state(
                        connection, "journal_group_overrides", key, True, deleted_at,
                    )

    def load_review(self, group_id: str) -> TradeReview:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT reason, review, tags, rating, status FROM trade_reviews WHERE group_id=?", (group_id,)
            ).fetchone()
        return TradeReview(group_id, *(str(value or "") for value in row)) if row else TradeReview(group_id)

    def load_reviews(self, group_ids: tuple[str, ...]) -> dict[str, TradeReview]:
        if not group_ids:
            return {}
        result: dict[str, TradeReview] = {}
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(group_ids):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT group_id,reason,review,tags,rating,status FROM trade_reviews "
                    f"WHERE group_id IN ({placeholders})", chunk,
                ).fetchall()
                result.update({
                    str(row[0]): TradeReview(str(row[0]), *(str(value or "") for value in row[1:]))
                    for row in rows
                })
        return result

    def save_review(self, review: TradeReview, now: datetime | None = None) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_reviews VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET reason=excluded.reason, review=excluded.review, "
                    "tags=excluded.tags, rating=excluded.rating, status=excluded.status, updated_at=excluded.updated_at",
                    (review.group_id, review.reason, review.review, review.tags, review.rating, review.status, updated_at),
                )

    def upsert_trade_costs(self, costs: tuple[DailyTradeCost, ...], now: datetime | None = None) -> None:
        if not costs:
            return
        confirmed_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._upsert_trade_costs(connection, costs, confirmed_at)

    @staticmethod
    def _upsert_trade_costs(
        connection: sqlite3.Connection,
        costs: tuple[DailyTradeCost, ...],
        confirmed_at: str,
    ) -> None:
        rows = tuple((
            value.fill_date.isoformat(), value.settlement_date.isoformat(), value.stock_code, value.side,
            value.gross_amount, value.settlement_amount, value.commission, value.tax, value.total_cost, confirmed_at,
        ) for value in costs)
        connection.executemany(
            "INSERT INTO daily_trade_costs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(fill_date, stock_code, side) DO UPDATE SET "
            "settlement_date=excluded.settlement_date, gross_amount=excluded.gross_amount, "
            "settlement_amount=excluded.settlement_amount, commission=excluded.commission, "
            "tax=excluded.tax, total_cost=excluded.total_cost, confirmed_at=excluded.confirmed_at",
            rows,
        )

    def load_trade_costs(self, start: datetime, end: datetime) -> tuple[DailyTradeCost, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT fill_date, settlement_date, stock_code, side, gross_amount, settlement_amount, "
                "commission, tax, total_cost FROM daily_trade_costs WHERE fill_date>=? AND fill_date<?",
                (start.date().isoformat(), end.date().isoformat()),
            ).fetchall()
        return tuple(DailyTradeCost(date.fromisoformat(row[0]), date.fromisoformat(row[1]), *row[2:]) for row in rows)


def _chunks(values: tuple[str, ...], size: int = 800):
    for start in range(0, len(values), size):
        yield values[start : start + size]

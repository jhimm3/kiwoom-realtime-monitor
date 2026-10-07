"""매매유형·체결·수동 묶음·복기·실제 비용 저장소."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.journal_enrichment import (
    EnrichmentTask,
    JournalAnalysisRevision,
    JournalExecutionProjectionPage,
    JournalExecutionProjectionResult,
    JournalResearchLink,
)
from kiwoom_monitor.application.trade_journal_summary import TradeReview, trade_fill_key
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.domain.order_contract import (
    AccountEnvironment,
    AccountScope,
    LEGACY_ACCOUNT_SCOPE,
)


_KST = ZoneInfo("Asia/Seoul")


class JournalTradeRepositoryMixin:
    _path: Path

    def list_account_scopes(self) -> tuple[AccountScope, ...]:
        """List journal accounts without exposing broker account numbers."""
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT DISTINCT origin_broker,origin_environment,canonical_account_ref "
                "FROM trade_fills ORDER BY origin_environment,canonical_account_ref"
            ).fetchall()
        scopes = tuple(
            AccountScope(str(row[0]), AccountEnvironment(str(row[1])), str(row[2]))
            for row in rows
        )
        return scopes or (LEGACY_ACCOUNT_SCOPE,)

    def project_execution_event_page(
        self, page: JournalExecutionProjectionPage, now: datetime | None = None,
    ) -> JournalExecutionProjectionResult:
        """Atomically persist immutable fill evidence and its source cursor."""
        if page.origin_scope == LEGACY_ACCOUNT_SCOPE:
            raise ValueError("execution projection cannot use the legacy account scope")
        scope = page.origin_scope
        canonical = page.effective_scope
        projected_at = (now or datetime.now()).isoformat(timespec="seconds")
        touched_intents = tuple(dict.fromkeys(event.intent_id for event in page.events))
        inserted = detailed = aggregate = 0
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    "SELECT canonical_account_ref,cursor FROM journal_execution_projection_states "
                    "WHERE projection_name=? AND origin_broker=? AND origin_environment=? "
                    "AND origin_account_ref=?",
                    (
                        page.projection_name, scope.broker, scope.environment.value,
                        scope.account_ref,
                    ),
                ).fetchone()
                stored_cursor = int(state[1]) if state else 0
                if state is not None and str(state[0]) != canonical.account_ref:
                    raise ValueError("execution projection canonical account changed")
                if page.from_cursor < stored_cursor:
                    if page.next_cursor > stored_cursor:
                        raise ValueError("execution projection page overlaps the stored cursor")
                    unresolved = self._execution_projection_unresolved(
                        connection, scope, touched_intents,
                    )
                    return JournalExecutionProjectionResult(
                        stored_cursor, 0, 0, 0, unresolved,
                    )
                if page.from_cursor != stored_cursor:
                    raise ValueError("execution projection page has a cursor gap")
                for event in page.events:
                    existing = connection.execute(
                        "SELECT content_hash FROM journal_execution_event_projections WHERE "
                        "origin_broker=? AND origin_environment=? AND origin_account_ref=? "
                        "AND source_event_id=?",
                        (
                            scope.broker, scope.environment.value, scope.account_ref,
                            event.source_event_id,
                        ),
                    ).fetchone()
                    if existing is None and event.fill_identity is not None:
                        existing = connection.execute(
                            "SELECT content_hash FROM journal_execution_event_projections WHERE "
                            "origin_broker=? AND origin_environment=? AND origin_account_ref=? "
                            "AND fill_identity=?",
                            (
                                scope.broker, scope.environment.value, scope.account_ref,
                                event.fill_identity,
                            ),
                        ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != event.content_hash:
                            raise sqlite3.IntegrityError(
                                "execution projection identity has conflicting content"
                            )
                        continue
                    connection.execute(
                        "INSERT INTO journal_execution_event_projections("
                        "origin_broker,origin_environment,origin_account_ref,canonical_account_ref,"
                        "projection_name,source_event_id,accepted_sequence,event_type,trading_date,"
                        "intent_id,run_id,decision_id,broker_order_id,broker_execution_id,stock_code,"
                        "venue,side,occurred_at,received_at,quantity,price,broker_as_of,fill_identity,"
                        "content_hash,projected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            scope.broker, scope.environment.value, scope.account_ref,
                            canonical.account_ref, page.projection_name, event.source_event_id,
                            event.accepted_sequence, event.event_type, event.trading_date,
                            event.intent_id, event.run_id, event.decision_id,
                            event.broker_order_id, event.broker_execution_id, event.stock_code,
                            event.venue, event.side, event.occurred_at.isoformat(),
                            event.received_at.isoformat(), event.quantity, event.price,
                            event.broker_as_of.isoformat() if event.broker_as_of else None,
                            event.fill_identity, event.content_hash, projected_at,
                        ),
                    )
                    inserted += 1
                    detailed += event.event_type == "FILL"
                    aggregate += event.event_type == "BROKER_FILL_AGGREGATE"
                connection.execute(
                    "INSERT INTO journal_execution_projection_states("
                    "projection_name,origin_broker,origin_environment,origin_account_ref,"
                    "canonical_account_ref,cursor,updated_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(projection_name,origin_broker,origin_environment,origin_account_ref) "
                    "DO UPDATE SET canonical_account_ref=excluded.canonical_account_ref,"
                    "cursor=excluded.cursor,updated_at=excluded.updated_at",
                    (
                        page.projection_name, scope.broker, scope.environment.value,
                        scope.account_ref, canonical.account_ref, page.next_cursor, projected_at,
                    ),
                )
                unresolved = self._execution_projection_unresolved(
                    connection, scope, touched_intents,
                )
        return JournalExecutionProjectionResult(
            page.next_cursor, inserted, detailed, aggregate, unresolved,
        )

    def load_execution_projection_cursor(
        self, projection_name: str, account_scope: AccountScope,
    ) -> int:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT cursor FROM journal_execution_projection_states WHERE projection_name=? "
                "AND origin_broker=? AND origin_environment=? AND origin_account_ref=?",
                (
                    projection_name, account_scope.broker,
                    account_scope.environment.value, account_scope.account_ref,
                ),
            ).fetchone()
        return int(row[0]) if row else 0

    def load_execution_fill_projections(
        self,
        account_scope: AccountScope,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[dict[str, object], ...]:
        date_sql = ""
        date_values: tuple[str, ...] = ()
        if start is not None:
            date_sql += " AND trading_date>=?"
            start_day = start.astimezone(_KST).date() if start.tzinfo is not None else start.date()
            date_values += (start_day.isoformat(),)
        if end is not None:
            date_sql += " AND trading_date<=?"
            end_day = end.astimezone(_KST).date() if end.tzinfo is not None else end.date()
            date_values += (end_day.isoformat(),)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT origin_broker,origin_environment,origin_account_ref,canonical_account_ref,"
                "source_event_id,accepted_sequence,trading_date,intent_id,run_id,decision_id,"
                "broker_order_id,broker_execution_id,stock_code,venue,side,occurred_at,received_at,"
                "quantity,price,fill_identity FROM journal_execution_event_projections WHERE "
                "origin_broker=? AND origin_environment=? AND canonical_account_ref=? "
                "AND event_type='FILL'" + date_sql + " ORDER BY accepted_sequence",
                (
                    account_scope.broker, account_scope.environment.value,
                    account_scope.account_ref, *date_values,
                ),
            ).fetchall()
        keys = (
            "origin_broker", "origin_environment", "origin_account_ref", "canonical_account_ref",
            "source_event_id", "accepted_sequence", "trading_date", "intent_id", "run_id",
            "decision_id", "broker_order_id", "broker_execution_id", "stock_code", "venue",
            "side", "occurred_at", "received_at", "quantity", "price", "fill_identity",
        )
        values = tuple(dict(zip(keys, row, strict=True)) for row in rows)
        if start is None and end is None:
            return values
        return tuple(row for row in values if _projection_time_in_range(row, start, end))

    @staticmethod
    def _execution_projection_unresolved(
        connection: sqlite3.Connection,
        scope: AccountScope,
        intent_ids: tuple[str, ...],
    ) -> dict[str, int]:
        if not intent_ids:
            return {}
        placeholders = ",".join("?" for _ in intent_ids)
        rows = connection.execute(
            "SELECT intent_id,"
            "SUM(CASE WHEN event_type='BROKER_FILL_AGGREGATE' THEN quantity ELSE 0 END),"
            "SUM(CASE WHEN event_type='FILL' THEN quantity ELSE 0 END) "
            "FROM journal_execution_event_projections WHERE origin_broker=? "
            "AND origin_environment=? AND origin_account_ref=? "
            f"AND intent_id IN ({placeholders}) GROUP BY intent_id",
            (
                scope.broker, scope.environment.value, scope.account_ref, *intent_ids,
            ),
        ).fetchall()
        return {
            str(intent_id): max(0, int(aggregate or 0) - int(detailed or 0))
            for intent_id, aggregate, detailed in rows
        }

    def save_trade_setup(
        self, group_id: str, classification: TradeSetupClassification, manual_type: str = "",
        now: datetime | None = None, *, account_scope: AccountScope | None = None,
        canonical_scope: AccountScope | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        scope = account_scope or LEGACY_ACCOUNT_SCOPE
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_setup_classifications("
                    "group_id,automatic_type,confidence,evidence_json,manual_type,updated_at,"
                    "origin_broker,origin_environment,origin_account_ref,canonical_account_ref) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET automatic_type=excluded.automatic_type, "
                    "confidence=excluded.confidence, evidence_json=excluded.evidence_json, "
                    "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                    (group_id, classification.setup_type, classification.confidence,
                     json.dumps(list(classification.evidence), ensure_ascii=False), manual_type, updated_at,
                     *_scope_values(scope, canonical_scope)),
                )

    def load_trade_setup(
        self, group_id: str, account_scope: AccountScope | None = None,
    ) -> tuple[TradeSetupClassification, str] | None:
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT automatic_type, confidence, evidence_json, manual_type "
                "FROM trade_setup_classifications WHERE group_id=?" + scope_sql,
                (group_id, *scope_values),
            ).fetchone()
        return self._trade_setup_from_row(row) if row else None

    def load_trade_setups(
        self, group_ids: tuple[str, ...], account_scope: AccountScope | None = None,
    ) -> dict[str, tuple[TradeSetupClassification, str]]:
        if not group_ids:
            return {}
        result: dict[str, tuple[TradeSetupClassification, str]] = {}
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(group_ids):
                placeholders = ",".join("?" for _ in chunk)
                scope_sql, scope_values = _scope_filter(account_scope)
                rows = connection.execute(
                    "SELECT group_id,automatic_type,confidence,evidence_json,manual_type "
                    f"FROM trade_setup_classifications WHERE group_id IN ({placeholders})" + scope_sql,
                    (*chunk, *scope_values),
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
        *, account_scope: AccountScope | None = None, canonical_scope: AccountScope | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        scope = account_scope or LEGACY_ACCOUNT_SCOPE
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                if manual_type:
                    connection.execute(
                        "INSERT INTO trade_setup_cycle_overrides("
                        "group_id,cycle_index,manual_type,updated_at,origin_broker,origin_environment,"
                        "origin_account_ref,canonical_account_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(group_id, cycle_index) DO UPDATE SET "
                        "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                        (group_id, cycle_index, manual_type, updated_at,
                         *_scope_values(scope, canonical_scope)),
                    )
                    self._record_sync_state(
                        connection, "journal_cycle_overrides", f"{group_id}|{cycle_index}", False,
                        updated_at, scope, canonical_scope,
                    )
                else:
                    connection.execute(
                        "DELETE FROM trade_setup_cycle_overrides WHERE group_id=? AND cycle_index=? "
                        "AND origin_broker=? AND origin_environment=? AND origin_account_ref=?",
                        (
                            group_id, cycle_index, scope.broker,
                            scope.environment.value, scope.account_ref,
                        ),
                    )
                    self._record_sync_state(
                        connection, "journal_cycle_overrides", f"{group_id}|{cycle_index}", True,
                        updated_at, scope, canonical_scope,
                    )

    def load_trade_setup_cycle_overrides(
        self, group_id: str, account_scope: AccountScope | None = None,
    ) -> dict[int, str]:
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT cycle_index, manual_type FROM trade_setup_cycle_overrides WHERE group_id=?" + scope_sql,
                (group_id, *scope_values),
            ).fetchall()
        return {int(index): str(value) for index, value in rows if str(value)}

    def load_trade_setup_cycle_overrides_many(
        self, group_ids: tuple[str, ...], account_scope: AccountScope | None = None,
    ) -> dict[str, dict[int, str]]:
        if not group_ids:
            return {}
        result: dict[str, dict[int, str]] = {}
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(group_ids):
                placeholders = ",".join("?" for _ in chunk)
                scope_sql, scope_values = _scope_filter(account_scope)
                rows = connection.execute(
                    "SELECT group_id,cycle_index,manual_type FROM trade_setup_cycle_overrides "
                    f"WHERE group_id IN ({placeholders})" + scope_sql, (*chunk, *scope_values),
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
        *, account_scope: AccountScope | None = None, canonical_scope: AccountScope | None = None,
    ) -> None:
        """자동 판정 저장과 과거 단일 수동 유형 이전을 원자적으로 확정한다."""
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        scope = account_scope or LEGACY_ACCOUNT_SCOPE
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_setup_classifications("
                    "group_id,automatic_type,confidence,evidence_json,manual_type,updated_at,"
                    "origin_broker,origin_environment,origin_account_ref,canonical_account_ref) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET automatic_type=excluded.automatic_type, "
                    "confidence=excluded.confidence, evidence_json=excluded.evidence_json, "
                    "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                    (
                        group_id, classification.setup_type, classification.confidence,
                        json.dumps(list(classification.evidence), ensure_ascii=False), "", updated_at,
                        *_scope_values(scope, canonical_scope),
                    ),
                )
                if legacy_override is not None:
                    cycle_index, manual_type = legacy_override
                    connection.execute(
                        "INSERT INTO trade_setup_cycle_overrides("
                        "group_id,cycle_index,manual_type,updated_at,origin_broker,origin_environment,"
                        "origin_account_ref,canonical_account_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(group_id, cycle_index) DO UPDATE SET "
                        "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                        (group_id, cycle_index, manual_type, updated_at,
                         *_scope_values(scope, canonical_scope)),
                    )
                    self._record_sync_state(
                        connection, "journal_cycle_overrides", f"{group_id}|{cycle_index}", False,
                        updated_at, scope, canonical_scope,
                    )

    @staticmethod
    def _record_sync_state(
        connection: sqlite3.Connection,
        collection: str,
        document_key: str,
        is_deleted: bool,
        updated_at: str,
        scope: AccountScope = LEGACY_ACCOUNT_SCOPE,
        canonical_scope: AccountScope | None = None,
    ) -> None:
        collection = (
            collection.replace("journal_", "journal_v2_", 1)
            if scope != LEGACY_ACCOUNT_SCOPE else collection
        )
        effective = canonical_scope or scope
        owner = scope.account_ref if scope != LEGACY_ACCOUNT_SCOPE else "legacy"
        connection.execute(
            "INSERT INTO journal_sync_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
            "is_deleted=excluded.is_deleted, updated_at=excluded.updated_at",
            (
                collection, owner, document_key, scope.broker, scope.environment.value,
                scope.account_ref, effective.account_ref, int(is_deleted), updated_at,
            ),
        )

    def upsert_fills(
        self, fills: tuple[TradeFill, ...], *, account_scope: AccountScope | None = None,
    ) -> None:
        if not fills:
            return
        _validate_write_scope(account_scope, tuple(fill.origin_scope for fill in fills))
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._upsert_fills(connection, fills)

    def upsert_history_sync(
        self,
        fills: tuple[TradeFill, ...],
        costs: tuple[DailyTradeCost, ...],
        now: datetime | None = None,
        *,
        account_scope: AccountScope | None = None,
    ) -> None:
        """한 번의 체결 조회 결과와 실제 비용을 모두 반영하거나 모두 되돌린다."""
        if not fills and not costs:
            return
        _validate_write_scope(
            account_scope,
            tuple(fill.origin_scope for fill in fills)
            + tuple(cost.origin_scope for cost in costs),
        )
        confirmed_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                self._upsert_fills(connection, fills)
                self._upsert_trade_costs(connection, costs, confirmed_at)

    @staticmethod
    def _upsert_fills(connection: sqlite3.Connection, fills: tuple[TradeFill, ...]) -> None:
        rows = tuple((
            trade_fill_key(fill), fill.origin_scope.broker, fill.origin_scope.environment.value,
            fill.origin_scope.account_ref, fill.effective_scope.account_ref,
            fill.order_no, fill.stock_code, fill.stock_name, fill.side,
            fill.filled_at.isoformat(timespec="seconds"), fill.quantity, fill.price,
            fill.order_type, fill.market,
        ) for fill in fills)
        connection.executemany(
            "INSERT INTO trade_fills("
            "fill_key,origin_broker,origin_environment,origin_account_ref,canonical_account_ref,"
            "order_no,stock_code,stock_name,side,filled_at,quantity,price,order_type,market) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fill_key) DO UPDATE SET "
            "stock_name=excluded.stock_name, quantity=excluded.quantity, price=excluded.price, "
            "order_type=excluded.order_type, market=excluded.market, "
            "canonical_account_ref=excluded.canonical_account_ref",
            rows,
        )

    def load_fills(
        self, start: datetime, end: datetime, account_scope: AccountScope | None = None,
    ) -> tuple[TradeFill, ...]:
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                f"SELECT {_FILL_COLUMNS} FROM trade_fills WHERE filled_at>=? AND filled_at<?"
                f"{scope_sql} ORDER BY filled_at DESC",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), *scope_values),
            ).fetchall()
        return tuple(_fill_from_row(row) for row in rows)

    def load_fills_with_entry_context(
        self, start: datetime, end: datetime, lookback_days: int = 365,
        account_scope: AccountScope | None = None,
    ) -> tuple[TradeFill, ...]:
        """Load prior fills only for stocks traded in the requested period.

        This lets a sell inside the date filter find its earlier buy without
        mixing unrelated stocks or completed older episodes into the screen.
        """
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            scope_rows = tuple(
                (str(row[0]), str(row[1]), str(row[2])) for row in connection.execute(
                    "SELECT DISTINCT canonical_account_ref,origin_broker,origin_environment "
                    "FROM trade_fills WHERE filled_at>=? AND filled_at<?" + scope_sql,
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), *scope_values),
                ).fetchall()
            )
            if not scope_rows:
                return ()
            context_start = start - timedelta(days=max(0, int(lookback_days)))
            rows: list[tuple[object, ...]] = []
            for canonical_ref, broker, environment in scope_rows:
                codes = tuple(str(row[0]) for row in connection.execute(
                    "SELECT DISTINCT stock_code FROM trade_fills WHERE filled_at>=? AND filled_at<? "
                    "AND canonical_account_ref=? AND origin_broker=? AND origin_environment=?",
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"),
                     canonical_ref, broker, environment),
                ).fetchall())
                if not codes:
                    continue
                placeholders = ",".join("?" for _ in codes)
                rows.extend(connection.execute(
                    f"SELECT {_FILL_COLUMNS} FROM trade_fills WHERE stock_code IN ({placeholders}) "
                    "AND canonical_account_ref=? AND origin_broker=? AND origin_environment=? "
                    "AND filled_at>=? AND filled_at<? ORDER BY filled_at DESC",
                    (*codes, canonical_ref, broker, environment,
                     context_start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                ).fetchall())
        return tuple(sorted((_fill_from_row(row) for row in rows), key=lambda fill: fill.filled_at, reverse=True))

    def load_group_overrides(self) -> dict[str, str]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute("SELECT fill_key, group_id FROM trade_group_overrides").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def assign_group(
        self, fill_keys: tuple[str, ...], group_id: str, now: datetime | None = None, *,
        account_scope: AccountScope | None = None, canonical_scope: AccountScope | None = None,
    ) -> None:
        if not fill_keys or not group_id:
            return
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        scope = account_scope or LEGACY_ACCOUNT_SCOPE
        effective_scope = canonical_scope or scope
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                if account_scope is not None:
                    _validate_fill_key_scope(connection, fill_keys, scope, effective_scope)
                connection.executemany(
                    "INSERT INTO trade_group_overrides("
                    "fill_key,group_id,updated_at,origin_broker,origin_environment,origin_account_ref,"
                    "canonical_account_ref) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(fill_key) DO UPDATE SET group_id=excluded.group_id, "
                    "updated_at=excluded.updated_at, origin_broker=excluded.origin_broker, "
                    "origin_environment=excluded.origin_environment, "
                    "origin_account_ref=excluded.origin_account_ref, "
                    "canonical_account_ref=excluded.canonical_account_ref",
                    ((key, group_id, updated_at, *_scope_values(scope, canonical_scope)) for key in fill_keys),
                )
                for key in fill_keys:
                    self._record_sync_state(
                        connection, "journal_group_overrides", key, False, updated_at,
                        scope, canonical_scope,
                    )

    def clear_group_assignments(
        self, fill_keys: tuple[str, ...], *, account_scope: AccountScope | None = None,
        canonical_scope: AccountScope | None = None,
    ) -> None:
        if not fill_keys:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                scope = account_scope or LEGACY_ACCOUNT_SCOPE
                effective = canonical_scope or scope
                if account_scope is not None:
                    _validate_fill_key_scope(connection, fill_keys, scope, effective)
                connection.executemany(
                    "DELETE FROM trade_group_overrides WHERE fill_key=? AND origin_broker=? "
                    "AND origin_environment=? AND origin_account_ref=?",
                    ((key, scope.broker, scope.environment.value, scope.account_ref) for key in fill_keys),
                )
                deleted_at = datetime.now().isoformat(timespec="microseconds")
                for key in fill_keys:
                    self._record_sync_state(
                        connection, "journal_group_overrides", key, True, deleted_at,
                        scope, canonical_scope,
                    )

    def load_review(
        self, group_id: str, account_scope: AccountScope | None = None,
    ) -> TradeReview:
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT reason,review,tags,rating,status,origin_broker,origin_environment,"
                "origin_account_ref,canonical_account_ref FROM trade_reviews WHERE group_id=?" + scope_sql,
                (group_id, *scope_values),
            ).fetchone()
        if row is None:
            return TradeReview(group_id, origin_scope=account_scope or LEGACY_ACCOUNT_SCOPE)
        origin, canonical = _stored_scopes(row, 5)
        return TradeReview(group_id, *(str(value or "") for value in row[:5]), origin, canonical)

    def load_reviews(
        self, group_ids: tuple[str, ...], account_scope: AccountScope | None = None,
    ) -> dict[str, TradeReview]:
        if not group_ids:
            return {}
        result: dict[str, TradeReview] = {}
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(group_ids):
                placeholders = ",".join("?" for _ in chunk)
                scope_sql, scope_values = _scope_filter(account_scope)
                rows = connection.execute(
                    "SELECT group_id,reason,review,tags,rating,status,origin_broker,origin_environment,"
                    "origin_account_ref,canonical_account_ref FROM trade_reviews "
                    f"WHERE group_id IN ({placeholders})" + scope_sql, (*chunk, *scope_values),
                ).fetchall()
                for row in rows:
                    origin, canonical = _stored_scopes(row, 6)
                    result[str(row[0])] = TradeReview(
                        str(row[0]), *(str(value or "") for value in row[1:6]), origin, canonical,
                    )
        return result

    def save_review(
        self, review: TradeReview, now: datetime | None = None, *,
        account_scope: AccountScope | None = None,
    ) -> None:
        _validate_write_scope(account_scope, (review.origin_scope,))
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_reviews("
                    "group_id,reason,review,tags,rating,status,updated_at,origin_broker,origin_environment,"
                    "origin_account_ref,canonical_account_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET reason=excluded.reason, review=excluded.review, "
                    "tags=excluded.tags, rating=excluded.rating, status=excluded.status, updated_at=excluded.updated_at",
                    (review.group_id, review.reason, review.review, review.tags, review.rating, review.status,
                     updated_at, *_scope_values(review.origin_scope, review.account_scope)),
                )

    def upsert_trade_costs(
        self, costs: tuple[DailyTradeCost, ...], now: datetime | None = None, *,
        account_scope: AccountScope | None = None,
    ) -> None:
        if not costs:
            return
        _validate_write_scope(account_scope, tuple(cost.origin_scope for cost in costs))
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
            value.origin_scope.broker, value.origin_scope.environment.value,
            value.origin_scope.account_ref, value.effective_scope.account_ref,
            value.fill_date.isoformat(), value.settlement_date.isoformat(), value.stock_code, value.side,
            value.gross_amount, value.settlement_amount, value.commission, value.tax, value.total_cost, confirmed_at,
        ) for value in costs)
        connection.executemany(
            "INSERT INTO daily_trade_costs("
            "origin_broker,origin_environment,origin_account_ref,canonical_account_ref,fill_date,"
            "settlement_date,stock_code,side,gross_amount,settlement_amount,commission,tax,total_cost,confirmed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT("
            "origin_broker,origin_environment,origin_account_ref,fill_date,stock_code,side) DO UPDATE SET "
            "settlement_date=excluded.settlement_date, gross_amount=excluded.gross_amount, "
            "settlement_amount=excluded.settlement_amount, commission=excluded.commission, "
            "tax=excluded.tax, total_cost=excluded.total_cost, confirmed_at=excluded.confirmed_at, "
            "canonical_account_ref=excluded.canonical_account_ref",
            rows,
        )

    def load_trade_costs(
        self, start: datetime, end: datetime, account_scope: AccountScope | None = None,
    ) -> tuple[DailyTradeCost, ...]:
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT fill_date, settlement_date, stock_code, side, gross_amount, settlement_amount, "
                "commission, tax, total_cost,origin_broker,origin_environment,origin_account_ref,"
                "canonical_account_ref FROM daily_trade_costs WHERE fill_date>=? AND fill_date<?" + scope_sql,
                (start.date().isoformat(), end.date().isoformat(), *scope_values),
            ).fetchall()
        return tuple(_cost_from_row(row) for row in rows)

    def ensure_enrichment_task(
        self, task: EnrichmentTask, now: datetime | None = None,
    ) -> EnrichmentTask:
        """동일 입력·정책 작업을 하나만 만들고 기존 진행 상태를 보존한다."""
        created_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO journal_enrichment_tasks "
                    "(task_id,target_type,target_ref,kind,input_fingerprint,policy_version,state,"
                    "attempts,next_retry_at,owner,result_json,last_error,created_at,updated_at,started_at,completed_at,"
                    "origin_broker,origin_environment,origin_account_ref,canonical_account_ref) "
                    "VALUES (?,?,?,?,?,?,'pending',0,NULL,'','{}','',?,?,NULL,NULL,?,?,?,?)",
                    (
                        task.task_id, task.target_type, task.target_ref, task.kind,
                        task.input_fingerprint, task.policy_version, created_at, created_at,
                        *_scope_values(task.origin_scope, task.effective_scope),
                    ),
                )
                row = connection.execute(
                    f"SELECT {_ENRICHMENT_TASK_COLUMNS} FROM journal_enrichment_tasks WHERE task_id=?",
                    (task.task_id,),
                ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("매매일지 보완 작업을 읽을 수 없습니다.")
        return _enrichment_task_from_row(row)

    def load_enrichment_task(self, task_id: str) -> EnrichmentTask | None:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                f"SELECT {_ENRICHMENT_TASK_COLUMNS} FROM journal_enrichment_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return _enrichment_task_from_row(row) if row is not None else None

    def transition_enrichment_task(
        self,
        task_id: str,
        *,
        expected_states: tuple[str, ...],
        state: str,
        now: datetime,
        owner: str = "",
        next_retry_at: datetime | None = None,
        result: Mapping[str, object] | None = None,
        error: str = "",
        increment_attempts: bool = False,
    ) -> bool:
        if not expected_states:
            return False
        placeholders = ",".join("?" for _ in expected_states)
        updated_at = now.isoformat(timespec="seconds")
        completed_at = updated_at if state in {"complete", "unavailable", "cancelled"} else None
        started_at = updated_at if state == "running" else None
        result_json = (
            json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
            if result is not None else None
        )
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE journal_enrichment_tasks SET state=?, "
                    "attempts=attempts+?, next_retry_at=?, owner=?, "
                    "result_json=COALESCE(?,result_json), last_error=?, "
                    "updated_at=?, started_at=COALESCE(?,started_at), completed_at=? "
                    f"WHERE task_id=? AND state IN ({placeholders})",
                    (
                        state, int(increment_attempts),
                        next_retry_at.isoformat(timespec="seconds") if next_retry_at else None,
                        owner if state == "running" else "", result_json, error[:1000],
                        updated_at, started_at, completed_at, task_id, *expected_states,
                    ),
                )
        return cursor.rowcount == 1

    def recover_running_enrichment_tasks(self, now: datetime) -> int:
        """전 프로세스가 남긴 running을 즉시 재검사 가능한 실패 상태로 돌린다."""
        value = now.isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE journal_enrichment_tasks SET state='retryable_failed',next_retry_at=?,owner='',"
                    "last_error='프로세스 종료로 결과 확인 필요',updated_at=? WHERE state='running'",
                    (value, value),
                )
        return cursor.rowcount

    def list_enrichment_tasks(
        self, *, states: tuple[str, ...] = (), kinds: tuple[str, ...] = (), limit: int = 500,
        account_scope: AccountScope | None = None,
    ) -> tuple[EnrichmentTask, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if states:
            clauses.append("state IN (" + ",".join("?" for _ in states) + ")")
            parameters.extend(states)
        if kinds:
            clauses.append("kind IN (" + ",".join("?" for _ in kinds) + ")")
            parameters.extend(kinds)
        if account_scope is not None:
            clauses.extend((
                "canonical_account_ref=?", "origin_broker=?", "origin_environment=?",
            ))
            parameters.extend((
                account_scope.account_ref, account_scope.broker, account_scope.environment.value,
            ))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(max(1, int(limit)))
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                f"SELECT {_ENRICHMENT_TASK_COLUMNS} FROM journal_enrichment_tasks{where} "
                "ORDER BY updated_at,task_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return tuple(_enrichment_task_from_row(row) for row in rows)

    def save_journal_analysis_revision(
        self, revision: JournalAnalysisRevision,
    ) -> JournalAnalysisRevision:
        """동일 입력·분석 버전은 한 revision으로 보존하고 사용자 복기는 건드리지 않는다."""
        payload = (
            revision.revision_id, revision.group_id, revision.analysis_kind,
            revision.input_fingerprint, revision.analysis_version,
            json.dumps(dict(revision.content), ensure_ascii=False, sort_keys=True),
            revision.created_at.isoformat(timespec="seconds"),
            *_scope_values(revision.origin_scope, revision.effective_scope),
        )
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO journal_analysis_revisions("
                    "revision_id,group_id,analysis_kind,input_fingerprint,analysis_version,content_json,"
                    "created_at,origin_broker,origin_environment,origin_account_ref,canonical_account_ref) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    payload,
                )
                row = connection.execute(
                    f"SELECT {_ANALYSIS_REVISION_COLUMNS} FROM journal_analysis_revisions WHERE revision_id=?",
                    (revision.revision_id,),
                ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("매매일지 분석 revision을 읽을 수 없습니다.")
        stored = _analysis_revision_from_row(row)
        if (
            stored.group_id, stored.analysis_kind, stored.input_fingerprint,
            stored.analysis_version, stored.content, stored.origin_scope, stored.canonical_scope,
        ) != (
            revision.group_id, revision.analysis_kind, revision.input_fingerprint,
            revision.analysis_version, revision.content, revision.origin_scope, revision.canonical_scope,
        ):
            raise sqlite3.IntegrityError("같은 분석 revision ID에 다른 내용이 있습니다.")
        return stored

    def load_journal_analysis_revisions(
        self, group_id: str, account_scope: AccountScope | None = None,
    ) -> tuple[JournalAnalysisRevision, ...]:
        scope_sql, scope_values = _scope_filter(account_scope)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                f"SELECT {_ANALYSIS_REVISION_COLUMNS} FROM journal_analysis_revisions WHERE group_id=?"
                + scope_sql + " "
                "ORDER BY created_at,revision_id",
                (group_id, *scope_values),
            ).fetchall()
        return tuple(_analysis_revision_from_row(row) for row in rows)

    def save_journal_research_link(self, link: JournalResearchLink) -> JournalResearchLink:
        payload = (
            link.link_id, link.execution_ref, link.run_id, link.decision_id, link.snapshot_id,
            link.entry_thesis_id, link.evidence_timing,
            link.available_at.isoformat() if link.available_at else None,
            (link.created_at or datetime.now()).isoformat(timespec="seconds"),
            *_scope_values(link.origin_scope, link.effective_scope),
        )
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO journal_research_links("
                    "link_id,execution_ref,run_id,decision_id,snapshot_id,entry_thesis_id,evidence_timing,"
                    "available_at,created_at,origin_broker,origin_environment,origin_account_ref,"
                    "canonical_account_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    payload,
                )
                row = connection.execute(
                    f"SELECT {_RESEARCH_LINK_COLUMNS} FROM journal_research_links WHERE link_id=?",
                    (link.link_id,),
                ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("매매일지 연구 근거 링크를 읽을 수 없습니다.")
        stored = _research_link_from_row(row)
        if (
            stored.execution_ref, stored.run_id, stored.decision_id, stored.snapshot_id,
            stored.entry_thesis_id, stored.evidence_timing, stored.available_at,
            stored.origin_scope, stored.canonical_scope,
        ) != (
            link.execution_ref, link.run_id, link.decision_id, link.snapshot_id,
            link.entry_thesis_id, link.evidence_timing, link.available_at,
            link.origin_scope, link.canonical_scope,
        ):
            raise sqlite3.IntegrityError("같은 연구 근거 링크 ID에 다른 내용이 있습니다.")
        return stored

    def load_journal_research_links(
        self, execution_refs: tuple[str, ...], account_scope: AccountScope | None = None,
    ) -> tuple[JournalResearchLink, ...]:
        refs = tuple(dict.fromkeys(value for value in execution_refs if value))
        if not refs:
            return ()
        values: list[JournalResearchLink] = []
        with closing(sqlite3.connect(self._path)) as connection:
            for chunk in _chunks(refs):
                placeholders = ",".join("?" for _ in chunk)
                scope_sql, scope_values = _scope_filter(account_scope)
                rows = connection.execute(
                    f"SELECT {_RESEARCH_LINK_COLUMNS} FROM journal_research_links "
                    f"WHERE execution_ref IN ({placeholders})" + scope_sql + " ORDER BY created_at,link_id",
                    (*chunk, *scope_values),
                ).fetchall()
                values.extend(_research_link_from_row(row) for row in rows)
        return tuple(values)


_FILL_COLUMNS = (
    "order_no,stock_code,stock_name,side,filled_at,quantity,price,order_type,market,"
    "origin_broker,origin_environment,origin_account_ref,canonical_account_ref"
)


def _validate_write_scope(
    expected: AccountScope | None, actual_scopes: tuple[AccountScope, ...],
) -> None:
    if expected is None:
        if any(scope != LEGACY_ACCOUNT_SCOPE for scope in actual_scopes):
            raise ValueError("scoped journal writes require an explicit account_scope")
        return
    if expected == LEGACY_ACCOUNT_SCOPE:
        raise ValueError("legacy-unassigned cannot be used for a new scoped journal write")
    if any(scope != expected for scope in actual_scopes):
        raise ValueError("journal write scope does not match its fills or costs")


def _scope_filter(scope: AccountScope | None) -> tuple[str, tuple[str, ...]]:
    if scope is None:
        return "", ()
    return (
        " AND canonical_account_ref=? AND origin_broker=? AND origin_environment=?",
        (scope.account_ref, scope.broker, scope.environment.value),
    )


def _scope_values(
    origin: AccountScope, canonical: AccountScope | None = None,
) -> tuple[str, str, str, str]:
    effective = canonical or origin
    if effective.broker != origin.broker or effective.environment != origin.environment:
        raise ValueError("canonical journal scope cannot cross broker or environment")
    return origin.broker, origin.environment.value, origin.account_ref, effective.account_ref


def _validate_fill_key_scope(
    connection: sqlite3.Connection, fill_keys: tuple[str, ...], expected_origin: AccountScope,
    expected_canonical: AccountScope,
) -> None:
    placeholders = ",".join("?" for _ in fill_keys)
    rows = connection.execute(
        "SELECT fill_key,origin_broker,origin_environment,origin_account_ref,canonical_account_ref "
        "FROM trade_fills "
        f"WHERE fill_key IN ({placeholders})",
        fill_keys,
    ).fetchall()
    if len(rows) != len(set(fill_keys)) or any(
        tuple(str(value) for value in row[1:])
        != (
            expected_origin.broker, expected_origin.environment.value,
            expected_origin.account_ref, expected_canonical.account_ref,
        )
        for row in rows
    ):
        raise ValueError("group assignment contains fills from a different account scope")


def _stored_scopes(row: tuple[object, ...], start: int) -> tuple[AccountScope, AccountScope | None]:
    broker = str(row[start])
    environment = AccountEnvironment(str(row[start + 1]))
    origin = AccountScope(broker, environment, str(row[start + 2]))
    canonical_ref = str(row[start + 3])
    canonical = None if canonical_ref == origin.account_ref else AccountScope(
        broker, environment, canonical_ref,
    )
    return origin, canonical


def _fill_from_row(row: tuple[object, ...]) -> TradeFill:
    origin, canonical = _stored_scopes(row, 9)
    return TradeFill(
        str(row[0]), str(row[1]), str(row[2]), str(row[3]),
        datetime.fromisoformat(str(row[4])), int(row[5]), int(row[6]),
        str(row[7]), str(row[8]), origin, canonical,
    )


def _projection_time_in_range(
    row: Mapping[str, object], start: datetime | None, end: datetime | None,
) -> bool:
    occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
    if occurred_at.tzinfo is not None:
        occurred_at = occurred_at.astimezone(_KST).replace(tzinfo=None)
    comparable_start = start
    comparable_end = end
    if comparable_start is not None and comparable_start.tzinfo is not None:
        comparable_start = comparable_start.astimezone(_KST).replace(tzinfo=None)
    if comparable_end is not None and comparable_end.tzinfo is not None:
        comparable_end = comparable_end.astimezone(_KST).replace(tzinfo=None)
    return (
        (comparable_start is None or occurred_at >= comparable_start)
        and (comparable_end is None or occurred_at < comparable_end)
    )


def _cost_from_row(row: tuple[object, ...]) -> DailyTradeCost:
    origin, canonical = _stored_scopes(row, 9)
    return DailyTradeCost(
        date.fromisoformat(str(row[0])), date.fromisoformat(str(row[1])),
        str(row[2]), str(row[3]), int(row[4]), int(row[5]), int(row[6]),
        int(row[7]), int(row[8]), origin, canonical,
    )


def _chunks(values: tuple[str, ...], size: int = 800):
    for start in range(0, len(values), size):
        yield values[start : start + size]


_ENRICHMENT_TASK_COLUMNS = (
    "task_id,target_type,target_ref,kind,input_fingerprint,policy_version,state,attempts,"
    "next_retry_at,owner,result_json,last_error,origin_broker,origin_environment,"
    "origin_account_ref,canonical_account_ref"
)

_ANALYSIS_REVISION_COLUMNS = (
    "revision_id,group_id,analysis_kind,input_fingerprint,analysis_version,content_json,created_at,"
    "origin_broker,origin_environment,origin_account_ref,canonical_account_ref"
)

_RESEARCH_LINK_COLUMNS = (
    "link_id,execution_ref,run_id,decision_id,snapshot_id,entry_thesis_id,"
    "evidence_timing,available_at,created_at,origin_broker,origin_environment,"
    "origin_account_ref,canonical_account_ref"
)


def _enrichment_task_from_row(row: tuple[object, ...]) -> EnrichmentTask:
    try:
        result = dict(json.loads(str(row[10] or "{}")))
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {}
    try:
        next_retry_at = datetime.fromisoformat(str(row[8])) if row[8] else None
    except ValueError:
        next_retry_at = None
    origin, canonical = _stored_scopes(row, 12)
    return EnrichmentTask(
        task_id=str(row[0]), target_type=str(row[1]), target_ref=str(row[2]), kind=str(row[3]),
        input_fingerprint=str(row[4]), policy_version=str(row[5]), state=str(row[6]),
        attempts=int(row[7] or 0), next_retry_at=next_retry_at, owner=str(row[9] or ""),
        result=result, last_error=str(row[11] or ""), origin_scope=origin, canonical_scope=canonical,
    )


def _analysis_revision_from_row(row: tuple[object, ...]) -> JournalAnalysisRevision:
    try:
        content = dict(json.loads(str(row[5] or "{}")))
    except (TypeError, ValueError, json.JSONDecodeError):
        content = {}
    origin, canonical = _stored_scopes(row, 7)
    return JournalAnalysisRevision(
        str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]), content,
        datetime.fromisoformat(str(row[6])), origin, canonical,
    )


def _research_link_from_row(row: tuple[object, ...]) -> JournalResearchLink:
    origin, canonical = _stored_scopes(row, 9)
    return JournalResearchLink(
        str(row[0]), str(row[1]),
        str(row[2]) if row[2] is not None else None,
        str(row[3]) if row[3] is not None else None,
        str(row[4]) if row[4] is not None else None,
        str(row[5]) if row[5] is not None else None,
        str(row[6]),
        datetime.fromisoformat(str(row[7])) if row[7] else None,
        datetime.fromisoformat(str(row[8])),
        origin,
        canonical,
    )

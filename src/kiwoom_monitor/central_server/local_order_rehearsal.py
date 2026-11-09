"""Run an explicit real-account route against a local fake order peer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from kiwoom_monitor.application.market_session_schedule import MarketPhase, MarketSession, session_window_at
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.domain.order_contract import (
    LocalRehearsalOrderIntent, OrderSide, OrderState, OrderType, TERMINAL_ORDER_STATES,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import LocalRehearsalOrderTransport
from kiwoom_monitor.infrastructure.persistence.execution_repository import (
    ExecutionRepository, execution_broker_legs, execution_broker_venue,
)
from .database import SQLiteQueryStore
from .execution_runtime import ExecutionRuntime


@dataclass(frozen=True)
class RehearsalConditions:
    symbol: str
    venue: str
    available_at: datetime
    phase: MarketPhase
    eligible: bool | None
    allows_order: bool | None
    limit_price: int | None


class _RehearsalStore(SQLiteQueryStore):
    local_order_rehearsal = True


class LocalOrderRehearsal:
    """One leased local account, with orders scoped to explicit symbol and action slots."""

    def __init__(self, directory: Path, fake_peer, *, account_ref: str, run_id: str, symbol: str | None = None,
                 venue: str, sessions: tuple[MarketSession, ...], maximum_age: timedelta, now_provider,
                 lease_now_provider=None, plan_slots: dict[str, dict[str, OrderSide]] | None = None):
        allowed_sessions = ({MarketSession.KRX_REGULAR} if venue in {'KRX', 'SOR'} else {
            MarketSession.NXT_PRE_MARKET, MarketSession.NXT_MAIN_MARKET, MarketSession.NXT_AFTER_MARKET,
        })
        slots = {symbol: {side.value: side for side in OrderSide}} if plan_slots is None else plan_slots
        if (venue not in {'KRX', 'NXT', 'SOR'} or not sessions or set(sessions) - allowed_sessions
                or maximum_age <= timedelta(0) or not account_ref.strip() or not run_id.strip()
                or (symbol is None) != (plan_slots is not None) or not slots
                or any(not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit()
                       or group.get('BUY') is not OrderSide.BUY or group.get('SELL') is not OrderSide.SELL
                       or any(not slot or ':' in slot or not isinstance(side, OrderSide) for slot, side in group.items())
                       for code, group in slots.items())):
            raise ValueError('explicit rehearsal account, stock, session and freshness policy required')
        transport = LocalRehearsalOrderTransport(fake_peer, account_ref=account_ref, now_provider=now_provider)
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / 'rehearsal.json'
        identity = {'account_ref': account_ref, 'run_id': run_id, 'venue': venue,
                    'sessions': sorted(session.value for session in sessions), 'maximum_age': maximum_age.total_seconds()}
        identity.update({'symbol': symbol} if plan_slots is None else {'plan_slots': slots})
        if not marker.exists():
            if any(directory.iterdir()):
                raise ValueError('local rehearsal requires an empty directory or its own saved ledger')
            with marker.open('x', encoding='utf-8') as handle:
                json.dump(identity, handle)
        elif json.loads(marker.read_text(encoding='utf-8')) != identity:
            raise ValueError('local rehearsal scope changed')

        self._peer, self._sessions, self._maximum_age = fake_peer, frozenset(sessions), maximum_age
        self._account_ref, self._symbol, self._venue, self._run_id = account_ref, symbol, venue, run_id
        self._slots = {code: dict(group) for code, group in slots.items()}
        self._lock = RLock()
        self._conditions: RehearsalConditions | None = None
        self._store = _RehearsalStore(directory / 'rehearsal.sqlite3', now_provider=now_provider)
        self._store._rehearsal_lease_now = lease_now_provider or now_provider
        self._store.initialize()
        self._repository = ExecutionRepository(self._store)
        lifecycle = OrderLifecycle(self._repository, transport, now_provider=now_provider,
                                   rehearsal_order_rejection=self._entry_rejection)
        self._runtime = ExecutionRuntime(lifecycle, self._repository, account_ref=account_ref, run_id=run_id,
                                         owner_token=uuid4().hex, now_provider=now_provider,
                                         rehearsal_lease_now=lease_now_provider)
        self._runtime.start(new_orders_enabled=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        with self._lock:
            try:
                self._runtime.stop()
            finally:
                self._store.close()

    def _order_id(self, side, symbol, slot):
        if not isinstance(side, OrderSide):
            raise ValueError('explicit rehearsal order side required')
        code, action = symbol or self._symbol, slot or side.value
        if self._slots.get(code, {}).get(action) is not side:
            raise ValueError('rehearsal symbol and action slot do not match the approved plan')
        return f'{self._run_id}:{action}' if self._symbol else f'{self._run_id}:{code}:{action}'

    def read_order(self, side: OrderSide, *, symbol=None, slot=None):
        return self._runtime.load_intent(self._order_id(side, symbol, slot))

    def read_events(self, side: OrderSide, *, symbol=None, slot=None):
        return self._runtime.load_intent_events(self._order_id(side, symbol, slot))

    def read_plan(self, symbol):
        return {slot: record for slot, side in self._slots[symbol].items()
                if (record := self.read_order(side, symbol=symbol, slot=slot)) is not None}

    def renew_ownership(self):
        self._runtime.heartbeat()

    def _usable(self, cut, now, symbol):
        return (isinstance(cut, RehearsalConditions) and (cut.symbol, cut.venue) == (symbol, self._venue)
                and cut.available_at.utcoffset() is not None and timedelta(0) <= now - cut.available_at <= self._maximum_age)

    def _entry_rejection(self, intent, now):
        cut = self._conditions
        if not self._usable(cut, now, intent.symbol) or cut.allows_order is not True or cut.eligible is not True:
            return 'LOCAL_REHEARSAL_CURRENT_CONDITIONS_UNCONFIRMED'
        venue = 'KRX' if intent.venue == 'SOR' else intent.venue
        window = session_window_at(now, venue=venue, eligible=cut.eligible, observed_phase=cut.phase.value)
        if window.session not in self._sessions or not window.permits('continuous_trade'):
            return 'LOCAL_REHEARSAL_SESSION_UNCONFIRMED'
        return None

    def _reconcile(self):
        recovery = self._peer.read_recovery()
        account = recovery.account
        now = self._runtime.server_now()
        if (account.account_ref != self._account_ref or not timedelta(0) <= now - account.as_of <= self._maximum_age):
            raise ValueError('local rehearsal requires a fresh matching account')
        details = {detail.broker_order_id: detail for detail in recovery.order_details}
        orders = {order.broker_order_id: order for order in recovery.orders}
        allowed_venues = {'KRX', 'NXT'} if self._venue == 'SOR' else {self._venue}
        if (len(details) != len(recovery.order_details) or len(orders) != len(recovery.orders)
                or set(details) != set(orders)
                or any(detail.venue not in allowed_venues or detail.reasons for detail in details.values())
                or any(order.account_ref != self._account_ref or order.symbol not in self._slots
                       or not timedelta(0) <= now - order.as_of <= self._maximum_age for order in orders.values())):
            raise ValueError('local rehearsal broker evidence does not match the route')
        records = tuple(record for code in self._slots for record in self.read_plan(code).values())
        self._runtime.recover_amendments(recovery, records=records)
        matched = set()
        for record in records:
            refreshed = self._runtime.load_intent(record.intent.intent_id)
            for leg in execution_broker_legs(refreshed):
                number = leg.broker_order_id
                if number in matched:
                    raise ValueError('local rehearsal physical order belongs to more than one intent')
                matched.add(number)
                if number not in orders:
                    continue
                detail = details[number]
                if self._venue == 'SOR' and detail.venue != execution_broker_venue(refreshed):
                    raise ValueError('local SOR evidence conflicts with the confirmed physical venue')
                if (orders[number].symbol, detail.side, detail.limit_price, detail.ordered_quantity,
                        detail.original_broker_order_id or '') != (record.intent.symbol, record.intent.side,
                        leg.limit_price, leg.ordered_quantity, leg.original_broker_order_id):
                    raise ValueError('local rehearsal physical order does not match the ledger')
                if leg.last_broker_as_of is None or orders[number].as_of > leg.last_broker_as_of:
                    self._runtime.reconcile(record.intent.intent_id, orders[number], broker_venue=detail.venue)
        return account, matched == set(orders)

    def reconcile(self):
        with self._lock, self._runtime.automation_decision_guard():
            return self._reconcile()

    def step(self, side: OrderSide, *, current_conditions, entry_quantity: int | None = None,
             exit_quantity: int | None = None, symbol=None, slot=None):
        """Reconcile, reassess now, then make at most one fake broker request."""
        order_id = self._order_id(side, symbol, slot)
        code, action = symbol or self._symbol, slot or side.value
        with self._lock, self._runtime.automation_decision_guard():
            account, covered = self._reconcile()
            record = self._runtime.load_intent(order_id)
            if not covered:
                return record
            self._conditions = current_conditions(side)
            try:
                now = self._runtime.server_now()
                cut = self._conditions
                if not self._usable(cut, now, code):
                    return record
                price = cut.limit_price if type(cut.limit_price) is int and cut.limit_price > 0 else None
                if record is not None and record.state is not OrderState.QUEUED:
                    return self._manage(record, cut, now, price, account)
                quantity = self._new_quantity(side, account, code, action, entry_quantity, exit_quantity)
                if quantity is None or price is None or cut.allows_order is not True:
                    return record
                intent = record.intent if record is not None else LocalRehearsalOrderIntent(
                    order_id, self._run_id, f'{order_id}:decision',
                    self._account_ref, 'real', code, self._venue, side, quantity, OrderType.LIMIT, price,
                    now, now + self._maximum_age, 'local-order-rehearsal/v1',
                )
                if (intent.quantity, intent.limit_price) != (quantity, price) or self._entry_rejection(intent, now):
                    return record
                self._runtime.set_new_orders_enabled(True)
                return self._runtime.submit(intent, account)
            finally:
                self._conditions = None

    def _manage(self, record, cut, now, price, account):
        def decide(_, leg):
            if cut.allows_order is False:
                return False, None
            if (self._entry_rejection(record.intent, now)
                    or record.filled_quantity != record.detailed_filled_quantity):
                return None, None
            if record.intent.side is OrderSide.BUY and price is not None:
                increase = max(0, price - leg.limit_price) * leg.remaining_quantity
                if increase > account.available_cash_won - account.reserved_open_buy_won:
                    return False, None
            if record.intent.side is OrderSide.SELL and leg.remaining_quantity > account.positions.get(record.intent.symbol, 0):
                return False, None
            return True, price
        return self._runtime.manage_rehearsal_limit(record.intent.intent_id, decide)

    def _new_quantity(self, side, account, symbol, slot, entry_quantity, exit_quantity):
        held = account.positions.get(symbol, 0)
        other = [row for action, row in self.read_plan(symbol).items() if action != slot]
        if any(row.state not in TERMINAL_ORDER_STATES or row.filled_quantity != row.detailed_filled_quantity
               for row in other):
            return None
        net = sum(row.detailed_filled_quantity * (1 if row.intent.side is OrderSide.BUY else -1) for row in other)
        if net != held:
            return None
        if side is OrderSide.BUY:
            quantity = entry_quantity
            correct_position = held == 0 if slot == 'BUY' else held > 0
        else:
            quantity = held if slot == 'SELL' else exit_quantity
            correct_position = held > 0 and (slot == 'SELL' or type(quantity) is int and 0 < quantity < held)
        return quantity if correct_position and type(quantity) is int and quantity > 0 else None

"""Point-in-time VI clocks and isolated MICRO execution evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import ClassVar

from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


@dataclass(frozen=True)
class ViClockProjection:
    version: ClassVar[str] = "micro-vi-clock/v1"
    observed_at: datetime | None = None
    available_at: datetime | None = None
    fid: str | None = None
    reason: str = ""


def project_vi_clock(
    receipt: Mapping[str, object], *, trading_date: date | None, decision_at: datetime,
) -> ViClockProjection:
    """Project the raw live clock without changing the receipt or proving event order.

    The caller supplies the confirmed KST trading date. REST backfill and
    archived rows cannot supply this live-clock evidence.
    """
    if decision_at.utcoffset() is None:
        raise ValueError("VI decision time must include its timezone")
    if receipt.get("source") != "kiwoom-websocket-1h":
        return ViClockProjection(reason="unsupported_source")
    received_at = _receipt_time(receipt.get("received_at"))
    available_at = _receipt_time(receipt.get("available_at"))
    if received_at is None or available_at is None or received_at > available_at:
        return ViClockProjection(reason="invalid_receipt_time")
    if available_at > decision_at:
        return ViClockProjection(reason="not_yet_available")
    if trading_date is None or received_at.date() != trading_date:
        return ViClockProjection(reason="unconfirmed_trading_date")

    raw_values = receipt.get("raw_values")
    if not isinstance(raw_values, Mapping):
        return ViClockProjection(reason="missing_raw_values")
    wire_kind = str(raw_values.get("9068", "")).strip()
    if receipt.get("event_kind") == "ACTIVATED" and wire_kind == "1":
        fid = "1223"
    elif receipt.get("event_kind") == "RELEASED" and wire_kind == "2":
        fid = "1224"
    else:
        return ViClockProjection(reason="unconfirmed_event_kind")

    clock = raw_values.get(fid)
    if not isinstance(clock, str) or len(clock) != 6 or not clock.isascii() or not clock.isdigit():
        return ViClockProjection(reason="invalid_event_clock")
    try:
        local_clock = time(int(clock[:2]), int(clock[2:4]), int(clock[4:]))
    except ValueError:
        return ViClockProjection(reason="invalid_event_clock")
    observed_at = datetime.combine(trading_date, local_clock, KST)
    if observed_at > received_at:
        return ViClockProjection(reason="event_after_receipt")
    return ViClockProjection(observed_at=observed_at, available_at=available_at, fid=fid)


def _receipt_time(value: object) -> datetime | None:
    if type(value) not in (float, int):
        return None
    try:
        return datetime.fromtimestamp(value, KST)
    except (ValueError, OverflowError, OSError):
        return None


MICRO_EVIDENCE_VERSION = "micro-synthetic-evidence/v1"
MICRO_POLICY_VERSION = "micro-policy/v1"
MICRO_RESULT_VERSION = "micro-execution-result/v1"
MICRO_VARIANTS = frozenset({"VI_GAP_UP", "VI_FLAT_DOWN_RISE", "VI_DOWNSIDE_REBOUND", "BOOK_SCALP"})
_MARKET_KINDS = frozenset({"VI_ACTIVATED", "VI_RELEASED", "BOOK", "TRADE", "GAP"})
_BROKER_KINDS = frozenset({"ORDER_ARRIVED", "FILL", "CANCELLED"})
_BPS = 10_000


def _integer(value: object, *, minimum: int = 1) -> bool:
    return type(value) is int and value >= minimum


@dataclass(frozen=True)
class _MicroPolicy:
    variant: str
    quantity: int
    min_reaction_bps: int
    max_spread_bps: int
    stop_loss_bps: int
    take_profit_bps: int
    min_bid_ask_ratio_bps: int | None = None

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> _MicroPolicy:
        variant = document.get("variant")
        if (document.get("version") != MICRO_POLICY_VERSION
                or not isinstance(variant, str) or variant not in MICRO_VARIANTS):
            raise ValueError("unsupported MICRO policy version or variant")
        fields = ["quantity", "min_reaction_bps", "max_spread_bps", "stop_loss_bps", "take_profit_bps"]
        if variant == "BOOK_SCALP":
            fields.append("min_bid_ask_ratio_bps")
        if set(document) != {"version", "variant", *fields}:
            raise ValueError("MICRO policy requires explicit variant parameters")
        for name in fields:
            if not _integer(document[name], minimum=0 if name in {"min_reaction_bps", "max_spread_bps"} else 1):
                raise ValueError(f"invalid MICRO policy parameter: {name}")
        if document["stop_loss_bps"] >= _BPS:
            raise ValueError("MICRO stop loss must be below 100 percent")
        return cls(variant, **{name: document[name] for name in fields})


@dataclass(frozen=True)
class _MicroEvent:
    kind: str
    ref: str
    observed_at: datetime
    available_at: datetime
    data: Mapping[str, object]

    @classmethod
    def from_document(cls, row: object) -> _MicroEvent:
        if not isinstance(row, Mapping):
            raise ValueError("MICRO event must be an object")
        kind, ref, data = row.get("kind"), row.get("ref"), row.get("data")
        if (not isinstance(kind, str) or kind not in _MARKET_KINDS | _BROKER_KINDS
                or not isinstance(ref, str) or not ref.strip() or not isinstance(data, Mapping)):
            raise ValueError("invalid MICRO event kind, reference or data")
        try:
            observed = aware_iso_kst(row["observed_at"])
            available = aware_iso_kst(row["available_at"])
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("MICRO event requires explicit offset-bearing times") from exc
        if observed > available:
            raise ValueError("MICRO event occurs after its availability")
        return cls(kind, ref, observed, available, data)


@dataclass
class _MicroOrder:
    order_id: str
    quantity: int
    decision_at: datetime
    arrived_at: datetime | None = None
    filled: int = 0
    notional: int = 0
    latest_fill_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_filled: int | None = None

    @property
    def terminal(self) -> bool:
        return self.filled == self.quantity or self.cancel_filled == self.filled

    def accept(self, event: _MicroEvent) -> bool:
        if event.kind == "ORDER_ARRIVED":
            if self.arrived_at is not None or event.observed_at < self.decision_at:
                return False
            self.arrived_at = event.observed_at
            return True
        if self.arrived_at is None or event.observed_at < self.arrived_at:
            return False
        if event.kind == "CANCELLED":
            filled = event.data.get("filled_quantity")
            if (self.terminal or self.cancelled_at is not None or not _integer(filled, minimum=0)
                    or not self.filled <= filled < self.quantity
                    or (self.latest_fill_at is not None and event.observed_at < self.latest_fill_at)):
                return False
            self.cancelled_at, self.cancel_filled = event.observed_at, filled
            return True
        quantity, price = event.data.get("quantity"), event.data.get("price_won")
        if not _integer(quantity) or not _integer(price) or self.filled + quantity > self.quantity:
            return False
        if self.cancelled_at is not None and (
            event.observed_at > self.cancelled_at or self.filled + quantity > self.cancel_filled
        ):
            return False
        self.filled += quantity
        self.notional += quantity * price
        self.latest_fill_at = max(self.latest_fill_at or event.observed_at, event.observed_at)
        return True


def _top_level(levels: object, *, bids: bool) -> tuple[int, int] | None:
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    previous = None
    for level in levels:
        if (not isinstance(level, (list, tuple)) or len(level) != 2
                or not all(_integer(value) for value in level)):
            return None
        price, _ = level
        if previous is not None and (price >= previous if bids else price <= previous):
            return None
        previous = price
    return tuple(levels[0])


@dataclass
class _MicroReplay:
    policy: _MicroPolicy
    scope: tuple[str, str, str]
    decisions: list[dict] = field(default_factory=list)
    executions: list[dict] = field(default_factory=list)
    orders: dict[str, _MicroOrder] = field(default_factory=dict)
    market_order: tuple[str, int, int, datetime] | None = None
    market_issue: str = ""
    broker_issue: str = ""
    input_issue: str = "missing_market_evidence"
    activation: _MicroEvent | None = None
    release: _MicroEvent | None = None
    book: _MicroEvent | None = None
    protective_trigger: _MicroEvent | None = None

    @property
    def position(self) -> int:
        return sum(order.filled if key == "entry" else -order.filled for key, order in self.orders.items())

    def consume(self, row: Mapping[str, object], event: _MicroEvent) -> None:
        symbol, venue, clock = self.scope
        correct_scope = (row.get("symbol", symbol), row.get("venue", venue), row.get("clock_id")) == self.scope
        if event.kind in _BROKER_KINDS:
            order_id = event.data.get("order_id")
            order = self.orders.get(order_id) if isinstance(order_id, str) else None
            if not correct_scope or order is None or not order.accept(event):
                self.broker_issue = "unconfirmed_broker_receipt"
                return
            self.executions.append({**self._reference(event), "order_id": order_id, **{
                key: event.data[key] for key in ("price_won", "quantity", "filled_quantity") if key in event.data
            }, "position_quantity": self.position})
            if order_id == "entry":
                self._continue_protection(order, event)
            return
        if self.market_issue:
            return
        if not correct_scope:
            self.market_issue = "unconfirmed_market_scope"
            return
        if not self._accept_market_order(row, event):
            self.market_issue = "unconfirmed_market_order"
            return
        if event.kind == "VI_ACTIVATED":
            self.activation, self.release, self.book = event, None, None
            self.input_issue = "missing_vi_release"
        elif event.kind == "VI_RELEASED":
            self.release, self.book = event, None
            self.input_issue = "missing_order_book"
        elif event.kind == "BOOK":
            self.book = event
            self.input_issue = "missing_ordered_trade"
        elif event.kind == "TRADE":
            self._trade(event)

    def _accept_market_order(self, row: Mapping[str, object], event: _MicroEvent) -> bool:
        stream, generation, sequence = row.get("stream_id"), row.get("generation"), row.get("sequence")
        if (event.kind == "GAP" or not isinstance(stream, str) or not stream.strip()
                or not _integer(generation, minimum=0) or not _integer(sequence)):
            return False
        previous = self.market_order
        if previous is not None and (
            (stream, generation, sequence) != (previous[0], previous[1], previous[2] + 1)
            or event.observed_at < previous[3]
        ):
            return False
        self.market_order = (stream, generation, sequence, event.observed_at)
        return True

    def _trade(self, event: _MicroEvent) -> None:
        price = event.data.get("price_won")
        if not _integer(price) or not _integer(event.data.get("quantity")):
            self.input_issue = "invalid_trade"
            return
        entry = self.orders.get("entry")
        if entry is not None:
            self._observe_position(entry, event, price)
            return
        eligible, reason, refs = self._entry_signal(price, event)
        self.input_issue = reason
        if eligible and not self.broker_issue:
            self._submit("entry", self.policy.quantity, event, self.policy.variant, refs)

    def _observe_position(self, entry: _MicroOrder, event: _MicroEvent, price: int) -> None:
        if not self.position or "exit" in self.orders or event.observed_at <= entry.latest_fill_at:
            return
        price_basis = price * entry.filled * _BPS
        if self.protective_trigger is None and price_basis <= entry.notional * (_BPS - self.policy.stop_loss_bps):
            self.protective_trigger = event
        if self.protective_trigger is not None:
            self._continue_protection(entry, event)
        elif (entry.terminal and not self.broker_issue
              and price_basis >= entry.notional * (_BPS + self.policy.take_profit_bps)):
            self._submit("exit", self.position, event, "take_profit", (event.ref,))

    def _continue_protection(self, entry: _MicroOrder, event: _MicroEvent) -> None:
        trigger = self.protective_trigger
        if trigger is None or not entry.terminal or self.broker_issue or "exit" in self.orders:
            return
        refs = (trigger.ref,) if trigger.ref == event.ref else (trigger.ref, event.ref)
        self._submit("exit", self.position, event, "protective_stop", refs)

    def _entry_signal(self, price: int, event: _MicroEvent) -> tuple[bool, str, tuple[str, ...]]:
        if self.book is None:
            return False, "missing_order_book", ()
        book = self.book.data
        bid = _top_level(book.get("bids"), bids=True)
        ask = _top_level(book.get("asks"), bids=False)
        if book.get("tradable") is not True or bid is None or ask is None or bid[0] >= ask[0]:
            return False, "unconfirmed_tradable_book", ()
        refs = (self.book.ref, event.ref)
        within_spread = (ask[0] - bid[0]) * _BPS <= bid[0] * self.policy.max_spread_bps
        if self.policy.variant == "BOOK_SCALP":
            pressure = bid[1] * _BPS >= ask[1] * self.policy.min_bid_ask_ratio_bps
            reacted = (price - ask[0]) * _BPS >= ask[0] * self.policy.min_reaction_bps
            return within_spread and pressure and reacted, "", refs
        if self.activation is None or self.release is None:
            return False, "missing_vi_episode", ()
        activation, release = self.activation.data, self.release.data
        episode = activation.get("episode_id")
        trigger_price, release_price = activation.get("trigger_price_won"), release.get("release_price_won")
        if (not isinstance(episode, str) or not episode.strip() or episode != release.get("episode_id")
                or not _integer(trigger_price) or not _integer(release_price)
                or activation.get("direction") not in ("UP", "DOWN")):
            return False, "unconfirmed_vi_episode", ()
        variant = self.policy.variant
        if variant == "VI_DOWNSIDE_REBOUND":
            matching_variant = activation["direction"] == "DOWN"
        else:
            matching_variant = activation["direction"] == "UP" and (
                release_price > trigger_price if variant == "VI_GAP_UP" else release_price <= trigger_price
            )
        reacted = price > release_price and (price - release_price) * _BPS >= release_price * self.policy.min_reaction_bps
        return within_spread and matching_variant and reacted, "", (self.activation.ref, self.release.ref, *refs)

    def _submit(self, order_id: str, quantity: int, event: _MicroEvent, reason: str, refs: tuple[str, ...]) -> None:
        self.orders[order_id] = _MicroOrder(order_id, quantity, event.available_at)
        self.decisions.append({
            **self._reference(event), "order_id": order_id, "action": order_id.upper(),
            "quantity": quantity, "reason": reason, "evidence_refs": list(refs),
        })

    @staticmethod
    def _reference(event: _MicroEvent) -> dict:
        return {"kind": event.kind, "ref": event.ref,
                "observed_at": event.observed_at.isoformat(), "available_at": event.available_at.isoformat()}

    def result(self) -> dict[str, object]:
        pending = next((order for order in self.orders.values() if not order.terminal), None)
        reason = self.broker_issue or self.market_issue or self.input_issue
        if self.position:
            status = "OPEN"
            if not reason:
                reason = ("protective_waiting_entry_reconciliation" if self.protective_trigger and "exit" not in self.orders
                          else "position_not_closed")
        elif pending or self.broker_issue:
            status, reason = "UNKNOWN", reason or "missing_terminal_receipt"
        elif "exit" in self.orders:
            status, reason = "COMPLETED", ""
        elif "entry" in self.orders:
            status, reason = "UNFILLED", "entry_cancelled_without_fill"
        else:
            status = "UNKNOWN" if reason else "NO_TRADE"
        return {
            "version": MICRO_RESULT_VERSION, "source_kind": "synthetic", "variant": self.policy.variant,
            "symbol": self.scope[0], "venue": self.scope[1], "status": status, "reason": reason,
            "market_issue": self.market_issue, "broker_issue": self.broker_issue,
            "decisions": self.decisions, "executions": self.executions,
            "position_quantity": self.position, "protected_quantity": self.position,
            "pending_order": None if pending is None else {
                "order_id": pending.order_id, "requested_quantity": pending.quantity,
                "detailed_filled_quantity": pending.filled, "cancel_reported_filled_quantity": pending.cancel_filled,
            },
        }


def replay_micro_execution(
    scenario: Mapping[str, object], policy: Mapping[str, object], *, checkpoint=lambda: None,
) -> dict[str, object]:
    """Run one synthetic entry/exit episode using explicit source order and broker receipts."""
    if scenario.get("version") != MICRO_EVIDENCE_VERSION:
        raise ValueError("unsupported MICRO scenario version")
    scope = tuple(scenario.get(key) for key in ("symbol", "venue", "clock_id"))
    events = scenario.get("events")
    if not all(isinstance(value, str) and value.strip() for value in scope) or not isinstance(events, (list, tuple)):
        raise ValueError("invalid MICRO scenario scope or events")
    replay = _MicroReplay(_MicroPolicy.from_document(policy), scope)
    seen: dict[str, Mapping[str, object]] = {}
    previous = None
    for index, row in enumerate(events):
        if index % 128 == 0:
            checkpoint()
        event = _MicroEvent.from_document(row)
        if event.ref in seen:
            if seen[event.ref] != row:
                raise ValueError("conflicting MICRO evidence reference")
            continue
        if previous is not None and event.available_at < previous:
            raise ValueError("MICRO events must retain recorded availability order")
        seen[event.ref] = row
        previous = event.available_at
        replay.consume(row, event)
    return replay.result()

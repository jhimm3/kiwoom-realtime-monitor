"""Point-in-time VI clocks and isolated MICRO execution evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import ClassVar

from kiwoom_monitor.application.micro_historical_vi import HistoricalMicroVi
from kiwoom_monitor.domain.market_data_contract import TradingVenue, kiwoom_hhmmss, kiwoom_vi_phase, kiwoom_vi_venue
from kiwoom_monitor.domain.simulation_contract import SimulationCostModel, default_research_cost_model
from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


@dataclass(frozen=True)
class ViClockProjection:
    version: ClassVar[str] = "micro-vi-clock/v2"
    observed_at: datetime | None = None
    available_at: datetime | None = None
    fid: str | None = None
    reason: str = ""
    kind: str | None = None
    trigger_at: datetime | None = None


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
    phase = kiwoom_vi_phase(raw_values, received_clock=received_at.time())
    if phase is None:
        return ViClockProjection(reason="unconfirmed_vi_trigger")
    trigger_at = datetime.combine(trading_date, phase.trigger_time, KST)
    if phase.release_time is not None:
        return ViClockProjection(observed_at=datetime.combine(trading_date, phase.release_time, KST),
                                 available_at=available_at, fid="1224", kind="RELEASED", trigger_at=trigger_at)
    return ViClockProjection(observed_at=trigger_at, available_at=available_at, fid="1223",
                             kind="EXTENDED" if phase.extended else "ACTIVATED", trigger_at=trigger_at)


def _receipt_time(value: object) -> datetime | None:
    if type(value) not in (float, int):
        return None
    try:
        return datetime.fromtimestamp(value, KST)
    except (ValueError, OverflowError, OSError):
        return None


MICRO_EVIDENCE_VERSION = "micro-synthetic-evidence/v1"
MICRO_FROZEN_EVIDENCE_VERSION = 'micro-frozen-evidence/v1'
MICRO_POLICY_VERSION = "micro-policy/v1"
MICRO_RESULT_VERSION = "micro-execution-result/v1"
MICRO_SHADOW_INPUT_VERSION = "micro-native-shadow-input/v2"
MICRO_VARIANTS = frozenset({"VI_GAP_UP", "VI_FLAT_DOWN_RISE", "VI_DOWNSIDE_REBOUND", "BOOK_SCALP"})
_MARKET_KINDS = frozenset({"VI_ACTIVATED", "VI_RELEASED", "BOOK", "TRADE", "GAP"})
_BROKER_KINDS = frozenset({"ORDER_ARRIVED", "FILL", "CANCELLED"})
_BPS = 10_000


def assess_micro_shadow_inputs(
    *, symbol: str, venue: str, variant: str, decision_at: datetime, trading_date: date | None,
    trade_snapshot: Mapping[str, object] | None = None,
    vi_history: Sequence[Mapping[str, object]] | None = None,
    market_operation: Mapping[str, object] | None = None,
    kind_halt_resume: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Report the current native inputs without granting a signal or execution.

    Callers supply one collector RAM cut and the visible native 0s and 1h
    receipts. The receipts describe observations, not current tradability.
    """
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isalnum():
        raise ValueError("MICRO Shadow requires a six-character stock code")
    if venue not in {TradingVenue.KRX, TradingVenue.NXT} or variant not in MICRO_VARIANTS:
        raise ValueError("unsupported MICRO Shadow venue or variant")
    if decision_at.utcoffset() is None:
        raise ValueError("MICRO Shadow decision time must include its timezone")
    cut = decision_at.astimezone(KST)
    native_vi = _native_vi_observations(vi_history, symbol, venue, cut, trading_date)
    episode = native_vi.get("latest_episode")
    vi = native_vi if variant != "BOOK_SCALP" else None
    release_at, indication = None, None
    if vi is not None and episode is not None and episode["release"] is not None:
        release_at = aware_iso_kst(episode["release"]["observed_at"])
        indication = _pre_release_indication(
            trade_snapshot, symbol, venue, cut, release_at, aware_iso_kst(episode["trigger_at"]),
        )

    trade = _native_0b_observation(trade_snapshot, symbol, venue, cut, release_at=release_at,
                                   release_indication=indication)
    book = _native_0d_observation(trade_snapshot, symbol, venue, cut)
    missing = []
    if book["state"] != "OBSERVED":
        missing.append("native_quote_depth")
    missing.extend(("native_instrument_tradability", "native_cross_stream_order"))
    if trade["state"] != "OBSERVED":
        missing.append("native_0b_observation")

    if vi is not None:
        missing.append("native_vi_release_execution_price")
        if episode is None or episode["activation"] is None or episode["release"] is None:
            missing.append("native_vi_activation_release_observations")
    return {
        "version": MICRO_SHADOW_INPUT_VERSION, "source_kind": "native",
        "symbol": symbol, "venue": venue, "variant": variant, "decision_at": cut.isoformat(),
        "status": "UNKNOWN", "candidate": None, "missing_inputs": missing,
        "trade": trade, "book": book, "vi": vi,
        "market_state_evidence": {
            "market_operation_0s": market_operation or {"state": "UNKNOWN", "reason": "no_visible_venue_0s"},
            "vi_1h": _latest_vi_state_evidence(native_vi),
            "kind_halt_resume": kind_halt_resume or {"state": "UNKNOWN", "reason": "no_visible_kind_trading_notice"},
        },
        "book_trade_clock_order": _book_trade_clock_order(book, trade),
    }


def _native_0d_observation(snapshot: Mapping[str, object] | None, symbol: str, venue: str, cut: datetime) -> dict:
    if venue != "KRX":
        return {"state": "UNKNOWN", "reason": "book_venue_unavailable"}
    if (snapshot is None or snapshot.get("durable") is not False
            or _offset_clock(snapshot.get("decision_time")) != cut):
        return {"state": "UNKNOWN", "reason": "collector_cut_unconfirmed"}
    if snapshot.get("micro_book_registration_failed") is True:
        return {"state": "UNKNOWN", "reason": "book_registration_unavailable"}
    source_id = snapshot.get("source_id")
    rows = snapshot.get("micro_books")
    if not isinstance(source_id, str) or not source_id or not isinstance(rows, tuple):
        return {"state": "UNKNOWN", "reason": "book_cut_unavailable"}
    for row in rows:
        if (row.get("code"), row.get("item"), row.get("subscription_market")) != (symbol, symbol, venue):
            continue
        available = _offset_clock(row.get("available_at"))
        generation = row.get("generation")
        if available is None or available > cut or not _integer(generation, minimum=0):
            continue
        level_counts = {
            side: sum(_integer(level["price_won"]) and _integer(level["quantity"], minimum=0)
                      for level in row[side])
            for side in ("asks", "bids")
        }
        return {"state": "OBSERVED" if all(level_counts.values()) else "PARTIAL",
                "source": "kiwoom-websocket-0D", "source_id": source_id,
                "generation": generation, "level_counts": level_counts, "latest": row}
    return {"state": "UNKNOWN", "reason": "no_scoped_retained_book"}


def _pre_release_indication(
    snapshot: Mapping[str, object] | None, symbol: str, venue: str, cut: datetime,
    release_at: datetime, trigger_at: datetime,
) -> dict:
    missing = {"state": "UNKNOWN", "reason": "no_pre_release_0d", "candidate_price_won": None}
    if (venue != "KRX" or snapshot is None or snapshot.get("durable") is not False
            or snapshot.get("micro_book_registration_failed") is True
            or _offset_clock(snapshot.get("decision_time")) != cut):
        return missing
    rows = snapshot.get("micro_indicative_history")
    if not isinstance(rows, tuple):
        return missing
    latest_at, latest = None, []
    for row in rows:
        if (row.get("code"), row.get("subscription_market"), row.get("generation")) != (
            symbol, venue, snapshot.get("generation"),
        ):
            continue
        at = _offset_clock(row.get("available_at"))
        if at is None or not trigger_at <= at < release_at:
            continue
        if latest_at is None or at > latest_at:
            latest_at, latest = at, [row]
        elif at == latest_at:
            latest.append(row)
    if latest_at is None:
        return missing
    gap = snapshot.get("gap_at")
    if isinstance(gap, datetime) and latest_at <= gap <= cut:
        return {**missing, "reason": "collector_gap_after_indication"}
    values = {(row.get("quote_time"), row.get("price_23"), row.get("price_291")) for row in latest}
    if len(values) != 1:
        return {**missing, "reason": "ambiguous_last_pre_release_0d"}
    quote_time, indicative, auction = values.pop()
    quote_second = kiwoom_hhmmss(quote_time)
    if quote_second is None:
        return {**missing, "reason": "unconfirmed_pre_release_0d_clock"}
    quote_at = datetime.combine(latest_at.date(), quote_second, KST)
    if not trigger_at <= quote_at <= latest_at:
        return {**missing, "reason": "unconfirmed_pre_release_0d_clock"}
    fids = {fid: price for fid, price in (("23", indicative), ("291", auction)) if _integer(price)}
    evidence = {"book_at": quote_at.isoformat(), "book_available_at": latest_at.isoformat(), "indicative_fids": fids}
    prices = set(fids.values())
    if len(prices) != 1:
        return {**missing, **evidence, "reason": "conflicting_0d_prices" if prices else "missing_0d_prices"}
    return {"state": "UNKNOWN", "reason": "no_matching_release_second_0b",
            "candidate_price_won": None, **evidence}


def _native_0b_observation(
    snapshot: Mapping[str, object] | None, symbol: str, venue: str, cut: datetime,
    *, release_at: datetime | None, release_indication: dict | None,
) -> dict:
    if snapshot is None:
        return {"state": "UNKNOWN", "reason": "collector_cut_unavailable"}
    if (snapshot.get("input_resolution") != "0B" or snapshot.get("durable") is not False
            or _offset_clock(snapshot.get("decision_time")) != cut):
        return {"state": "UNKNOWN", "reason": "collector_cut_unconfirmed"}
    approved = snapshot.get("approved_sources")
    if not isinstance(approved, tuple) or (symbol, venue) not in approved:
        return {"state": "UNKNOWN", "reason": "trade_source_not_approved"}
    if snapshot.get("retention_since") is None:
        return {"state": "UNKNOWN", "reason": "trade_retention_unavailable"}
    source_id, generation, rows = snapshot.get("source_id"), snapshot.get("generation"), snapshot.get("rows")
    if not isinstance(source_id, str) or not source_id or not _integer(generation, minimum=0) or not isinstance(rows, tuple):
        return {"state": "UNKNOWN", "reason": "trade_source_unconfirmed"}

    count, latest = 0, None
    same_second_count, same_second_trade, first_later_trade = 0, None, None
    indication_prices = set((release_indication or {}).get("indicative_fids", {}).values())
    indicative_price = indication_prices.pop() if len(indication_prices) == 1 else None
    match_count, first_match = 0, None
    for row in rows:
        if (row.get("code"), row.get("market"), row.get("source"), row.get("input_resolution")) != (
            symbol, venue, "kiwoom-websocket-0B", "0B",
        ):
            continue
        sequence = row.get("sequence")
        ref = row.get("input_ref")
        observed = _offset_clock(row.get("observed_at"))
        available = _receipt_time(row.get("available_at"))
        if (not _integer(sequence) or ref != f"ram-0B:{source_id}:{generation}:{sequence}"
                or observed is None or available is None or not observed <= available <= cut
                or not _integer(row.get("price_won"))):
            continue
        if latest is not None and sequence <= latest["sequence"]:
            return {"state": "UNKNOWN", "reason": "collector_sequence_unconfirmed"}
        count += 1
        latest = {"ref": ref, "sequence": sequence, "observed_at": observed.isoformat(),
                  "available_at": available.isoformat(), "price_won": row["price_won"]}
        if release_at is not None and _integer(row.get("volume")):
            if observed == release_at:
                same_second_count += 1
                same_second_trade = latest if same_second_count == 1 else None
                if row["price_won"] == indicative_price:
                    match_count += 1
                    if first_match is None:
                        first_match = latest
            elif observed > release_at and first_later_trade is None:
                first_later_trade = latest
    if latest is None:
        return {"state": "UNKNOWN", "reason": "no_scoped_retained_trade"}
    gap = snapshot.get("gap_at")
    result = {
        "state": "OBSERVED", "source": "kiwoom-websocket-0B", "source_id": source_id,
        "generation": generation, "count": count, "latest": latest,
        "collector_gap_at": gap.isoformat() if isinstance(gap, datetime) else None,
    }
    if release_at is not None:
        comparison = release_indication
        if match_count:
            comparison = {**comparison, "state": "INDICATIVE_MATCH", "reason": "matching_price_only",
                          "candidate_price_won": indicative_price, "matching_trade_count": match_count,
                          "single_matching_trade": first_match if match_count == 1 else None}
        result["release_comparison"] = {
            "release_at": release_at.isoformat(), "same_second_count": same_second_count,
            "single_same_second_trade": same_second_trade, "first_retained_later_trade": first_later_trade,
            "release_execution_price_won": None, "indicative_match": comparison,
        }
    return result


def _book_trade_clock_order(book: Mapping[str, object], trade: Mapping[str, object]) -> dict:
    quote, tick = book.get("latest"), trade.get("latest")
    if (not isinstance(quote, Mapping) or not isinstance(tick, Mapping)
            or (book.get("source_id"), book.get("generation")) != (trade.get("source_id"), trade.get("generation"))):
        return {"state": "UNKNOWN", "reason": "shared_collector_generation_unavailable"}
    quote_second = kiwoom_hhmmss(quote.get("quote_time"))
    quote_received = _offset_clock(quote.get("available_at"))
    trade_at = _offset_clock(tick.get("observed_at"))
    if (quote_second is None or quote_second == time.min or quote_received is None or trade_at is None
            or quote_received.date() != trade_at.date()):
        return {"state": "UNKNOWN", "reason": "reported_clock_unavailable"}
    quote_at = datetime.combine(quote_received.date(), quote_second, KST)
    if quote_at > quote_received:
        return {"state": "UNKNOWN", "reason": "reported_book_clock_after_receipt"}
    if quote_at == trade_at:
        trade_received = _offset_clock(tick.get("available_at"))
        receipt_order = {"state": "UNKNOWN", "reason": "distinct_receipt_clocks_unavailable"}
        if trade_received is not None and trade_received != quote_received:
            receipt_order = {"state": "OBSERVED", "basis": "application_receipt_clock",
                             "relation": "BOOK_BEFORE_TRADE" if quote_received < trade_received else "TRADE_BEFORE_BOOK",
                             "book_received_at": quote_received.isoformat(), "trade_received_at": trade_received.isoformat()}
        return {"state": "UNKNOWN", "reason": "same_reported_second", "application_receipt_order": receipt_order}
    return {"state": "OBSERVED", "basis": "reported_clock_seconds",
            "relation": "BOOK_BEFORE_TRADE" if quote_at < trade_at else "TRADE_BEFORE_BOOK",
            "book_at": quote_at.isoformat(), "trade_at": trade_at.isoformat()}


def _native_vi_observations(
    history: Sequence[Mapping[str, object]] | None, symbol: str, venue: str, cut: datetime,
    trading_date: date | None,
) -> dict:
    observations = []
    if history is None:
        return {"state": "UNKNOWN", "reason": "vi_history_unavailable", "observations": observations}
    if trading_date != cut.date():
        return {"state": "UNKNOWN", "reason": "unconfirmed_trading_date", "observations": observations}
    for receipt in history:
        ref = receipt.get("event_id")
        raw = receipt.get("raw_values")
        if (receipt.get("stock_code") != symbol or not isinstance(raw, Mapping)
                or kiwoom_vi_venue(receipt.get("exchange"), raw.get("9001")) != venue
                or not isinstance(ref, str) or not ref):
            continue
        projection = project_vi_clock(receipt, trading_date=trading_date, decision_at=cut)
        if projection.observed_at is not None:
            observations.append({
                "ref": ref, "kind": projection.kind, "fid": projection.fid,
                "trigger_at": projection.trigger_at.isoformat(),
                "observed_at": projection.observed_at.isoformat(),
                "available_at": projection.available_at.isoformat(),
            })
    latest_episode = None
    if observations:
        trigger = max(row["trigger_at"] for row in observations)
        current = sorted((row for row in observations if row["trigger_at"] == trigger),
                         key=lambda row: (row["available_at"], row["ref"]))
        latest_episode = {
            "trigger_at": trigger,
            "activation": next((row for row in current if row["kind"] == "ACTIVATED"), None),
            "release": next((row for row in current if row["kind"] == "RELEASED"), None),
        }
    return {
        "state": "OBSERVED" if observations else "UNKNOWN",
        "reason": "" if observations else "no_scoped_live_vi_clock_in_history",
        "selection": "bounded_native_vi_receipts", "clock_version": ViClockProjection.version,
        "observations": observations,
        "latest_episode": latest_episode,
    }


def _latest_vi_state_evidence(vi: dict) -> dict:
    episode = vi.get("latest_episode")
    if episode is None:
        return {"state": "UNKNOWN", "reason": vi["reason"]}
    last_observed = episode["release"]
    if last_observed is None:
        last_observed = max(
            (row for row in vi["observations"] if row["trigger_at"] == episode["trigger_at"]),
            key=lambda row: (row["available_at"], row["ref"]),
        )
    return {"state": "OBSERVED", "source": "kiwoom-websocket-1h", "last_observed": last_observed}


def _offset_clock(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            return aware_iso_kst(value)
        except (ValueError, OverflowError):
            pass
    return None


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
    provenance: Mapping[str, object] | None = None

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
        provenance = row.get('provenance')
        if provenance is not None and not isinstance(provenance, Mapping):
            raise ValueError('MICRO event provenance must be an object')
        return cls(kind, ref, observed, available, data, dict(provenance) if provenance is not None else None)


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
        if "fee_won" in event.data and not _integer(event.data["fee_won"], minimum=0):
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
    historical_vi: HistoricalMicroVi | None = None
    cost_model: SimulationCostModel = field(default_factory=default_research_cost_model)
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
            execution = {**self._reference(event), "order_id": order_id, **{
                key: event.data[key] for key in ("price_won", "quantity", "filled_quantity") if key in event.data
            }, "position_quantity": self.position}
            if event.kind == "FILL":
                if "fee_won" in event.data:
                    execution.update(fee_won=event.data["fee_won"], cost_basis="recorded_fill")
                else:
                    notional = event.data["price_won"] * event.data["quantity"]
                    execution.update(fee_won=sum(self.cost_model.fees_won(notional, sell=order_id == "exit")),
                                     cost_basis=self.cost_model.source)
            self.executions.append(execution)
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
            if self.historical_vi is not None and self.policy.variant != "BOOK_SCALP" and (
                self.activation is None or (self.activation.provenance or {}).get("source_kind") == "krx_vi_archive"
            ):
                self.activation = self._archived_activation(event)
            self.release, self.book = event, None
            self.input_issue = "missing_order_book"
        elif event.kind == "BOOK":
            self.book = event
            self.input_issue = "missing_ordered_trade"
        elif event.kind == "TRADE":
            self._trade(event)

    def _archived_activation(self, release: _MicroEvent) -> _MicroEvent | None:
        episode = self.historical_vi.match_release(release.observed_at)
        episode_id = release.data.get("episode_id")
        if episode is None or not isinstance(episode_id, str) or not episode_id.strip():
            return None
        # Supply the archived fact at the matching release without creating a
        # new event in the independently ordered market stream.
        return _MicroEvent("VI_ACTIVATED", episode.activation_ref, episode.triggered_at, release.available_at,
            {"episode_id": episode_id, "trigger_price_won": episode.trigger_price_won, "direction": episode.direction},
            {"source_kind": "krx_vi_archive", "source_ref": self.historical_vi.source_ref,
             "source_version": self.historical_vi.source_version, "source_sha256": episode.source_sha256})

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
                "observed_at": event.observed_at.isoformat(), "available_at": event.available_at.isoformat(),
                **({'provenance': dict(event.provenance)} if event.provenance is not None else {})}

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
        cost = sum(row.get("fee_won", 0) for row in self.executions)
        net = self.orders["exit"].notional - self.orders["entry"].notional - cost if status == "COMPLETED" else None
        return {
            "version": MICRO_RESULT_VERSION, "source_kind": "synthetic", "variant": self.policy.variant,
            "symbol": self.scope[0], "venue": self.scope[1], "status": status, "reason": reason,
            "market_issue": self.market_issue, "broker_issue": self.broker_issue,
            "decisions": self.decisions, "executions": self.executions,
            "total_cost_won": cost, "realized_pnl_won": net,
            **({"default_cost_model": self.cost_model.to_dict()} if any(
                row.get("cost_basis") == self.cost_model.source for row in self.executions) else {}),
            "position_quantity": self.position, "protected_quantity": self.position,
            "pending_order": None if pending is None else {
                "order_id": pending.order_id, "requested_quantity": pending.quantity,
                "detailed_filled_quantity": pending.filled, "cancel_reported_filled_quantity": pending.cancel_filled,
            },
        }


def _historical_kind_halt(receipt, scope, day, *, has_trade, read_at) -> dict:
    """Explain absent prints with a completed, same-day KRX backfill receipt."""
    symbol, venue, _ = scope
    day_text = day.isoformat()
    result = {"state": "UNKNOWN", "symbol": symbol, "venue": venue, "trading_date": day_text,
              "scope": "retrospective_only", "reason": "no_confirmed_full_day_halt"}
    if venue != "KRX":
        return {**result, "reason": "kind_venue_unavailable"}
    if not isinstance(receipt, Mapping) or (receipt.get("owner"), receipt.get("key")) != (
        f"{day_text}:{symbol}:KRX", "complete",
    ):
        return result
    document = receipt.get("document")
    if not isinstance(document, Mapping) or (document.get("kind"), document.get("reason"),
            document.get("as_of"), document.get("scope")) != ("minute_no_trade", "krx_full_day_halt", day_text, "full_day"):
        return result
    proof = document.get("halt_proof")
    if (document.get("window_closed") is not True or document.get("session_finalized") is not True
            or type(document.get("daily_volume")) is not int or document["daily_volume"] != 0
            or not isinstance(document.get("daily_revision_id"), str) or not document["daily_revision_id"]
            or not isinstance(proof, Mapping) or proof.get("source") != "krx-kind"
            or not isinstance(proof.get("halt_notice_url"), str) or not proof["halt_notice_url"]):
        return result
    completed = _offset_clock(document.get("completed_at"))
    available = _receipt_time(receipt.get("updated_at"))
    if completed is None or available is None or completed.date() < day or completed > available:
        return result
    if read_at is not None and available > read_at:
        return {**result, "reason": "kind_receipt_not_yet_available"}
    return {**result, "state": "CONFLICT" if has_trade else "CONFIRMED_FULL_DAY_HALT",
            "reason": "same_day_trade_observed" if has_trade else "krx_full_day_halt",
            "source": "krx-kind", "basis": "local_archive" if read_at is not None else "frozen_receipt",
            "available_at": available.isoformat(), "receipt": receipt}


def replay_micro_execution(
    scenario: Mapping[str, object], policy: Mapping[str, object], *, checkpoint=lambda: None,
    kind_halt_read_at: datetime | None = None,
) -> dict[str, object]:
    """Run one synthetic entry/exit episode using explicit source order and broker receipts."""
    frozen = scenario.get('version') == MICRO_FROZEN_EVIDENCE_VERSION
    if not frozen and scenario.get("version") != MICRO_EVIDENCE_VERSION:
        raise ValueError("unsupported MICRO scenario version")
    provenance = scenario.get('provenance') if frozen else None
    if frozen and (not isinstance(provenance, Mapping)
            or provenance.get('source_kind') not in {'synthetic', 'historical_reconstruction', 'historical_replay', 'hypothesis'}
            or any(not isinstance(provenance.get(key), str) or not provenance[key].strip()
                   for key in ('source_ref', 'source_version'))):
        raise ValueError('MICRO frozen evidence requires its declared research source and version')
    cut = aware_iso_kst(scenario['decision_at']) if frozen and 'decision_at' in scenario else None
    scope = tuple(scenario.get(key) for key in ("symbol", "venue", "clock_id"))
    events = scenario.get("events")
    if not all(isinstance(value, str) and value.strip() for value in scope) or not isinstance(events, (list, tuple)):
        raise ValueError("invalid MICRO scenario scope or events")
    historical_vi = None
    if "historical_vi" in scenario:
        if not frozen or cut is None:
            raise ValueError("historical VI requires frozen research and an explicit cut")
        historical_vi = HistoricalMicroVi.from_document(scenario["historical_vi"], symbol=scope[0], venue=scope[1], cut=cut)
    replay = _MicroReplay(_MicroPolicy.from_document(policy), scope, historical_vi)
    seen: dict[str, Mapping[str, object]] = {}
    previous = None
    same_day_trade = False
    available_events, available_rows, unavailable = [], [], []
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
        if (cut is not None and event.kind == 'TRADE' and event.observed_at.date() == cut.date()
                and (row.get('symbol', scope[0]), row.get('venue', scope[1]), row.get('clock_id')) == scope
                and _integer(event.data.get('price_won')) and _integer(event.data.get('quantity'))):
            same_day_trade = True
        if cut is not None and event.available_at > cut:
            unavailable.append(event.ref)
            continue
        replay.consume(row, event)
        available_events.append(event)
        if frozen:
            available_rows.append(dict(row))
    result = replay.result()
    if not frozen:
        return result
    book = replay.book.data if replay.book is not None else {}
    tradability = book.get('tradable') if type(book.get('tradable')) is bool else None
    ordered = replay.market_order is not None and not replay.market_issue
    release = replay.release.data if replay.release is not None else {}
    activation = replay.activation.data if replay.activation is not None else {}
    release_known = (replay.policy.variant == 'BOOK_SCALP' or
        _integer(release.get('release_price_won')) and isinstance(release.get('episode_id'), str)
        and release['episode_id'] == activation.get('episode_id'))
    missing = []
    if _top_level(book.get('bids'), bids=True) is None or _top_level(book.get('asks'), bids=False) is None:
        missing.append('quote_depth')
    if tradability is None: missing.append('instrument_tradability')
    if not ordered: missing.append('cross_stream_order')
    if not release_known: missing.append('vi_release_execution_price')
    if not any(event.kind == 'TRADE' for event in available_events): missing.append('trade_observation')
    halt = None
    if cut is not None:
        halt = _historical_kind_halt(
            scenario.get('kind_halt_receipt'), scope, cut.date(),
            has_trade=same_day_trade, read_at=kind_halt_read_at,
        )
    return {**result, 'source_kind': 'frozen_research_evidence', 'evidence_provenance': dict(provenance),
        **({'historical_vi': historical_vi.at_cut(cut)} if historical_vi is not None else {}),
        **({'historical_kind_halt': halt} if halt is not None else {}),
        'evidence': available_rows,
        'input_assessment': {'scope': 'declared_frozen_research', 'missing_inputs': missing,
            'tradable': tradability, 'cross_stream_order': 'RECORDED' if ordered else 'UNKNOWN',
            'vi_release_execution_price': release.get('release_price_won') if release_known else None,
            'unavailable_event_refs': unavailable, 'native_confirmation': False},
        'order_authorized': False}

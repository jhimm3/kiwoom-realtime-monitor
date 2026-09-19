"""Read-only account response adapters with separate real/mock reader guards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    BrokerFill,
    BrokerOrderSnapshot,
    OrderState,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution


ACCOUNT_PATH = "/api/dostk/acnt"
UNFILLED_API_ID = "ka10075"
FILLED_API_ID = "ka10076"
BALANCE_API_ID = "kt00018"
DEPOSIT_API_ID = "kt00001"
MAX_PAGES = 10
KST = ZoneInfo("Asia/Seoul")

UNFILLED_COLUMNS = (
    "acnt_no", "ord_no", "mang_empno", "stk_cd", "tsk_tp", "ord_stt",
    "stk_nm", "ord_qty", "ord_pric", "oso_qty", "cntr_tot_amt",
    "orig_ord_no", "io_tp_nm", "trde_tp", "tm", "cntr_no", "cntr_pric",
    "cntr_qty", "cur_prc", "sel_bid", "buy_bid", "unit_cntr_pric",
    "unit_cntr_qty", "tdy_trde_cmsn", "tdy_trde_tax", "ind_invsr",
    "stex_tp", "stex_tp_txt", "sor_yn", "stop_pric",
)
FILLED_COLUMNS = (
    "ord_no", "stk_nm", "io_tp_nm", "ord_pric", "ord_qty", "cntr_pric",
    "cntr_qty", "oso_qty", "tdy_trde_cmsn", "tdy_trde_tax", "ord_stt",
    "trde_tp", "orig_ord_no", "ord_tm", "stk_cd", "stex_tp",
    "stex_tp_txt", "sor_yn", "stop_pric",
)
BALANCE_COLUMNS = (
    "stk_cd", "stk_nm", "evltv_prft", "prft_rt", "pur_pric",
    "pred_close_pric", "rmnd_qty", "trde_able_qty", "cur_prc", "pred_buyq",
    "pred_sellq", "tdy_buyq", "tdy_sellq", "pur_amt", "pur_cmsn",
    "evlt_amt", "sell_cmsn", "tax", "sum_cmsn", "poss_rt", "crd_tp",
    "crd_tp_nm", "crd_loan_dt",
)


class AccountQueryResult(Protocol):
    payload: dict[str, Any]
    has_next: bool
    next_key: str


class AccountQueryBroker(Protocol):
    async def request(
        self, api_id: str, path: str, body: dict[str, Any], *,
        cont_yn: str = "N", next_key: str = "",
    ) -> AccountQueryResult: ...


@dataclass(frozen=True)
class AccountRecovery:
    account: AccountSnapshot
    orders: tuple[BrokerOrderSnapshot, ...]


MockAccountRecovery = AccountRecovery  # Preserve the existing mock import contract.


class _KiwoomAccountReader:
    """Shared paging/parsing; concrete readers enforce credential environment."""

    _order_market = "1"
    _balance_markets = ("KRX",)

    def __init__(
        self, broker: AccountQueryBroker, *, environment: str, account_ref: str,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if environment not in {"mock", "real"}:
            raise ValueError("account reader requires real or mock credentials")
        client = getattr(broker, "_client", None)
        if client is not None and client.environment != environment:
            raise ValueError("account reader broker environment mismatch")
        if not account_ref.strip():
            raise ValueError("account_ref is required")
        self._broker = broker
        self._environment = environment
        self._account_ref = account_ref
        self._now = now_provider or (lambda: datetime.now(KST))

    async def read(self) -> AccountRecovery:
        unfilled_pages = await self._pages(
            UNFILLED_API_ID, {"all_stk_tp": "0", "trde_tp": "0", "stex_tp": self._order_market},
        )
        unfilled_as_of = self._aware_now()
        filled_pages = await self._pages(
            FILLED_API_ID, {"qry_tp": "0", "sell_tp": "0", "stex_tp": self._order_market, "ord_no": ""},
        )
        filled_as_of = self._aware_now()
        balance_pages = ()
        for market in self._balance_markets:
            balance_pages += await self._pages(BALANCE_API_ID, {"qry_tp": "1", "dmst_stex_tp": market})
        deposit_pages = await self._pages(DEPOSIT_API_ID, {"qry_tp": "3"})
        deposit_as_of = self._aware_now()

        if self._environment == "real":
            venues = {}
            for pages, key, columns in ((unfilled_pages, "oso", UNFILLED_COLUMNS),
                                         (filled_pages, "cntr", FILLED_COLUMNS)):
                seen = set()
                for payload in pages:
                    for row in _rows(payload, key, columns):
                        order_id = str(row.get("ord_no") or "").strip()
                        if order_id in seen:
                            raise ValueError("real account pages repeat a broker order id")
                        seen.add(order_id)
                        venue = str(row.get("stex_tp") or "").strip()
                        if venue in {"1", "2"}:
                            if order_id in venues and venues[order_id] != venue:
                                raise ValueError("real account broker order id conflicts across venues")
                            venues[order_id] = venue

        open_orders = tuple(
            snapshot
            for payload in unfilled_pages
            for snapshot in parse_unfilled_orders(payload, self._account_ref, unfilled_as_of)
        )
        completed_orders = tuple(
            snapshot
            for payload in filled_pages
            for snapshot in parse_filled_orders(payload, self._account_ref, filled_as_of)
        )
        orders = merge_order_snapshots(open_orders, completed_orders)
        account = parse_account_snapshot(
            deposit_pages=deposit_pages,
            balance_pages=balance_pages,
            unfilled_pages=unfilled_pages,
            account_ref=self._account_ref,
            as_of=deposit_as_of,
        )
        return AccountRecovery(account, orders)

    async def _pages(self, api_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        pages: list[dict[str, Any]] = []
        cont_yn = "N"
        next_key = ""
        seen_keys = set()
        for _ in range(MAX_PAGES):
            result = await self._broker.request(
                api_id, ACCOUNT_PATH, body, cont_yn=cont_yn, next_key=next_key,
            )
            if not isinstance(result.payload, dict):
                raise ValueError(f"{api_id} response payload must be an object")
            if self._environment == "real":
                table = {UNFILLED_API_ID: "oso", FILLED_API_ID: "cntr",
                         BALANCE_API_ID: "acnt_evlt_remn_indv_tot"}.get(api_id)
                if table is not None and not isinstance(result.payload.get(table), list):
                    raise ValueError(f"{api_id} response is missing a complete account table")
            pages.append(result.payload)
            if not result.has_next:
                return tuple(pages)
            if not str(result.next_key).strip():
                raise ValueError(f"{api_id} continuation response is missing next_key")
            if self._environment == "real" and str(result.next_key) in seen_keys:
                raise ValueError(f"{api_id} continuation cursor repeated")
            seen_keys.add(str(result.next_key))
            cont_yn, next_key = "Y", str(result.next_key)
        raise ValueError(f"{api_id} exceeded the {MAX_PAGES}-page recovery limit")

    def _aware_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{self._environment} account clock must return a timezone-aware datetime")
        return value


class KiwoomMockAccountReader(_KiwoomAccountReader):
    def __init__(self, broker, *, environment, account_ref, now_provider=None):
        if environment != "mock":
            raise ValueError("mock account reader requires mock credentials")
        super().__init__(broker, environment=environment, account_ref=account_ref, now_provider=now_provider)


class KiwoomRealAccountReader(_KiwoomAccountReader):
    """Read integrated orders and compare both venue balance views without adding them."""

    _order_market = "0"
    _balance_markets = ("KRX", "NXT")

    def __init__(self, broker, *, environment, account_ref, now_provider=None):
        if environment != "real":
            raise ValueError("real account reader requires real credentials")
        super().__init__(broker, environment=environment, account_ref=account_ref, now_provider=now_provider)


def parse_unfilled_orders(
    payload: dict[str, Any], account_ref: str, as_of: datetime,
) -> tuple[BrokerOrderSnapshot, ...]:
    result: list[BrokerOrderSnapshot] = []
    for row in _rows(payload, "oso", UNFILLED_COLUMNS):
        ordered = _nonnegative(row.get("ord_qty"), "ord_qty")
        remaining = _nonnegative(row.get("oso_qty"), "oso_qty")
        if remaining > ordered:
            raise ValueError("unfilled quantity exceeds ordered quantity")
        reported = ordered - remaining
        fills: tuple[BrokerFill, ...] = ()
        execution_id = str(row.get("cntr_no") or "").strip()
        unit_quantity = _nonnegative(row.get("unit_cntr_qty"), "unit_cntr_qty", default=0)
        unit_price = _nonnegative(row.get("unit_cntr_pric"), "unit_cntr_pric", default=0)
        if execution_id and unit_quantity > 0 and unit_price > 0:
            fills = (BrokerFill(
                execution_id, unit_quantity, unit_price,
                _broker_time(row.get("tm"), as_of),
            ),)
            reported = max(reported, unit_quantity)
        if reported > ordered:
            raise ValueError("reported fill exceeds ordered quantity")
        result.append(_order_snapshot(row, account_ref, as_of, reported, remaining, fills))
    return tuple(result)


def parse_filled_orders(
    payload: dict[str, Any], account_ref: str, as_of: datetime,
) -> tuple[BrokerOrderSnapshot, ...]:
    result: list[BrokerOrderSnapshot] = []
    for row in _rows(payload, "cntr", FILLED_COLUMNS):
        ordered = _nonnegative(row.get("ord_qty"), "ord_qty")
        reported = _nonnegative(row.get("cntr_qty"), "cntr_qty")
        remaining = _nonnegative(row.get("oso_qty"), "oso_qty", default=max(0, ordered - reported))
        if reported > ordered or remaining > ordered:
            raise ValueError("broker order quantities are inconsistent")
        result.append(_order_snapshot(row, account_ref, as_of, reported, remaining, ()))
    return tuple(result)


def merge_order_snapshots(
    *groups: tuple[BrokerOrderSnapshot, ...],
) -> tuple[BrokerOrderSnapshot, ...]:
    merged: dict[str, BrokerOrderSnapshot] = {}
    for snapshot in (item for group in groups for item in group):
        previous = merged.get(snapshot.broker_order_id)
        if previous is None:
            merged[snapshot.broker_order_id] = snapshot
            continue
        if previous.account_ref != snapshot.account_ref or previous.symbol != snapshot.symbol:
            raise ValueError("the same broker order id has conflicting account or symbol")
        fills = {fill.execution_id: fill for fill in previous.fills}
        for fill in snapshot.fills:
            existing = fills.get(fill.execution_id)
            if existing is not None and existing != fill:
                raise ValueError("the same execution id has conflicting fill details")
            fills[fill.execution_id] = fill
        filled = max(previous.filled_quantity, snapshot.filled_quantity, sum(fill.quantity for fill in fills.values()))
        remaining = min(previous.remaining_quantity, snapshot.remaining_quantity)
        state = _merged_state(previous.state, snapshot.state, filled, remaining)
        merged[snapshot.broker_order_id] = BrokerOrderSnapshot(
            snapshot.broker_order_id, snapshot.account_ref, snapshot.symbol, state,
            filled, remaining, max(previous.as_of, snapshot.as_of),
            tuple(sorted(fills.values(), key=lambda fill: (fill.occurred_at, fill.execution_id))),
        )
    return tuple(sorted(merged.values(), key=lambda snapshot: snapshot.broker_order_id))


def parse_account_snapshot(
    *, deposit_pages: tuple[dict[str, Any], ...], balance_pages: tuple[dict[str, Any], ...],
    unfilled_pages: tuple[dict[str, Any], ...], account_ref: str, as_of: datetime,
) -> AccountSnapshot:
    orderable_values = [
        _nonnegative(payload.get("ord_alow_amt"), "ord_alow_amt", absolute=False)
        for payload in deposit_pages if payload.get("ord_alow_amt") not in (None, "")
    ]
    if not orderable_values:
        raise ValueError("kt00001 response is missing ord_alow_amt")
    if any(value != orderable_values[0] for value in orderable_values[1:]):
        raise ValueError("kt00001 continuation pages disagree on ord_alow_amt")

    positions: dict[str, int] = {}
    for payload in balance_pages:
        for row in _rows(payload, "acnt_evlt_remn_indv_tot", BALANCE_COLUMNS):
            symbol = _symbol(row.get("stk_cd"))
            quantity = _nonnegative(row.get("rmnd_qty"), "rmnd_qty", absolute=False)
            if symbol in positions and positions[symbol] != quantity:
                raise ValueError("kt00018 returned conflicting quantities for one symbol")
            positions[symbol] = quantity

    reserved = 0
    for payload in unfilled_pages:
        for row in _rows(payload, "oso", UNFILLED_COLUMNS):
            if not _is_buy(row.get("io_tp_nm")):
                continue
            remaining = _nonnegative(row.get("oso_qty"), "oso_qty")
            price = _nonnegative(row.get("ord_pric"), "ord_pric", default=0)
            reserved += remaining * price

    # kt00001 ord_alow_amt is already net of broker reservations. Reconstructing
    # available-before-known-reservations keeps OrderLifecycle's subtraction equal
    # to the broker's directly reported orderable amount.
    return AccountSnapshot(
        account_ref, orderable_values[0] + reserved, reserved, positions, as_of,
    )


def snapshot_from_order_execution(
    execution: OrderExecution, *, account_ref: str, as_of: datetime,
) -> BrokerOrderSnapshot:
    """Adapt one official 00 unit fill without inventing a broker execution id."""
    if not execution.order_no.strip() or not execution.execution_no.strip():
        raise ValueError("realtime fill requires order_no and execution_no")
    symbol = _symbol(execution.code)
    reported = execution.cumulative_filled_quantity or execution.quantity
    if execution.ordered_quantity is not None and reported > execution.ordered_quantity:
        raise ValueError("realtime cumulative fill exceeds ordered quantity")
    remaining = execution.remaining_quantity if execution.remaining_quantity is not None else 0
    state = (
        OrderState.FILLED
        if execution.remaining_quantity == 0
        else OrderState.PARTIALLY_FILLED
    )
    fill = BrokerFill(
        execution.execution_no, execution.quantity, execution.price,
        _broker_time(execution.trade_time, as_of),
    )
    return BrokerOrderSnapshot(
        execution.order_no, account_ref, symbol, state, reported, remaining, as_of, (fill,),
    )


def _order_snapshot(
    row: dict[str, Any], account_ref: str, as_of: datetime, filled: int,
    remaining: int, fills: tuple[BrokerFill, ...],
) -> BrokerOrderSnapshot:
    order_id = str(row.get("ord_no") or "").strip()
    if not order_id:
        raise ValueError("broker order row is missing ord_no")
    symbol = _symbol(row.get("stk_cd"))
    state = _order_state(row.get("ord_stt"), filled, remaining)
    return BrokerOrderSnapshot(order_id, account_ref, symbol, state, filled, remaining, as_of, fills)


def _order_state(raw: object, filled: int, remaining: int) -> OrderState:
    text = str(raw or "").strip().replace(" ", "")
    if "거부" in text:
        return OrderState.REJECTED
    if "취소" in text and remaining == 0:
        return OrderState.CANCELLED
    if filled > 0 and remaining == 0:
        return OrderState.FILLED
    if filled > 0:
        return OrderState.PARTIALLY_FILLED
    if "취소" in text:
        return OrderState.CANCEL_PENDING
    return OrderState.ACCEPTED


def _merged_state(
    first: OrderState, second: OrderState, filled: int, remaining: int,
) -> OrderState:
    states = {first, second}
    if OrderState.FILLED in states:
        return OrderState.FILLED
    if filled > 0 and remaining == 0 and OrderState.CANCELLED not in states:
        return OrderState.FILLED
    if OrderState.CANCELLED in states and remaining == 0:
        return OrderState.CANCELLED
    if OrderState.REJECTED in states and filled == 0:
        return OrderState.REJECTED
    if filled > 0:
        return OrderState.PARTIALLY_FILLED
    if OrderState.CANCEL_PENDING in states:
        return OrderState.CANCEL_PENDING
    return OrderState.ACCEPTED


def _rows(payload: dict[str, Any], key: str, columns: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    raw = payload.get(key, ())
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            rows.append(item)
        elif isinstance(item, (list, tuple)):
            rows.append(dict(zip(columns, item)))
        else:
            raise ValueError(f"{key} contains an invalid row")
    return tuple(rows)


def _symbol(raw: object) -> str:
    value = str(raw or "").strip().removeprefix("A").removesuffix("_NX").removesuffix("_AL")
    if len(value) != 6 or not value.isdigit():
        raise ValueError("broker row contains an invalid domestic stock code")
    return value


def _nonnegative(
    raw: object, name: str, *, default: int | None = None, absolute: bool = True,
) -> int:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        if default is not None:
            return default
        raise ValueError(f"broker response is missing {name}")
    try:
        value = int(text)
    except ValueError as error:
        raise ValueError(f"broker response has invalid {name}") from error
    if absolute:
        value = abs(value)
    if value < 0:
        raise ValueError(f"broker response has negative {name}")
    return value


def _is_buy(raw: object) -> bool:
    text = str(raw or "").strip().replace("+", "").replace("-", "")
    return "매수" in text


def _broker_time(raw: object, as_of: datetime) -> datetime:
    text = str(raw or "").strip().replace(":", "")
    if len(text) != 6 or not text.isdigit():
        return as_of
    local_as_of = as_of.astimezone(KST)
    candidate = local_as_of.replace(
        hour=int(text[0:2]), minute=int(text[2:4]), second=int(text[4:6]), microsecond=0,
    )
    if candidate > local_as_of + timedelta(minutes=1):
        candidate -= timedelta(days=1)
    return candidate.astimezone(as_of.tzinfo)

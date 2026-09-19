"""키움 WebSocket 주식체결(0B) 메시지 해석."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from kiwoom_monitor.domain.order_contract import AccountScope, LEGACY_ACCOUNT_SCOPE


@dataclass(frozen=True)
class TradeTick:
    code: str
    current_price: int | None
    cumulative_volume: int | None
    cumulative_trade_value: int | None
    trade_volume: int | None
    high_price: int | None
    trade_time: str | None
    change_rate: float | None = None
    # 0B FID 311은 억원 단위 시가총액이다.
    market_cap_eok: int | None = None
    market: str = "KRX"
    execution_strength: float | None = None
    session_type: str = ""


@dataclass(frozen=True)
class OrderExecution:
    """계좌 기반 주문체결(00) 중 실제 체결분만 전달한다."""

    order_no: str
    execution_no: str
    code: str
    name: str
    side: str
    price: int
    quantity: int
    trade_time: str
    market: str = ""
    ordered_quantity: int | None = None
    remaining_quantity: int | None = None
    cumulative_filled_quantity: int | None = None
    origin_scope: AccountScope = LEGACY_ACCOUNT_SCOPE


@dataclass(frozen=True)
class AccountBalanceChange:
    """계좌번호를 노출하지 않는 잔고(04) 변경 알림."""

    code: str
    name: str
    position_quantity: int
    orderable_quantity: int | None
    acquisition_price: int | None
    total_acquisition_won: int | None
    deposit_won: int | None
    current_price: int | None
    market: str = "KRX"
    origin_scope: AccountScope = LEGACY_ACCOUNT_SCOPE


@dataclass(frozen=True)
class MarketIndexTick:
    """업종지수(0J)·업종등락(0U)의 시장 요약."""

    market: str
    trade_time: str | None = None
    index_value: float | None = None
    change_rate: float | None = None
    cumulative_trade_value_million_won: int | None = None
    advancing_count: int | None = None
    declining_count: int | None = None
    flat_count: int | None = None


@dataclass(frozen=True)
class ProgramTradeTick:
    """종목프로그램매매(0w)의 누적값과 직전 증감값."""

    code: str
    trade_time: str | None
    net_buy_quantity: int | None
    net_buy_quantity_change: int | None
    net_buy_amount_million_won: int | None
    net_buy_amount_change_million_won: int | None
    market: str = "KRX"


@dataclass(frozen=True)
class StockPriceReference:
    """종목정보(0g)가 알린 현재 가격제한·기준가 묶음."""

    code: str
    upper_limit_price: int | None
    lower_limit_price: int | None
    base_price: int | None
    market: str = "KRX"


@dataclass(frozen=True)
class ViEvent:
    """시장 전체 VI(1h) 알림의 공식 FID를 보존한 사실 레코드."""

    code: str
    name: str
    event_kind: str
    vi_type: str
    trigger_price: int | None
    trigger_time: str | None
    release_time: str | None
    direction: str
    trigger_count: int | None
    exchange: str
    cumulative_volume: int | None
    cumulative_trade_value: int | None
    raw_values: dict[str, Any]


def parse_trade_ticks(message: dict[str, Any]) -> tuple[TradeTick, ...]:
    """`REAL` 0B 수신 메시지에서 화면에 필요한 값만 추출한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    ticks: list[TradeTick] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") != "0B":
            continue
        values = entry.get("values")
        code = _code(entry)
        if not isinstance(values, dict) or not code:
            continue
        ticks.append(
            TradeTick(
                code=code,
                current_price=_number(values.get("10"), absolute=True),
                cumulative_volume=_number(values.get("13")),
                cumulative_trade_value=_number(values.get("14")),
                trade_volume=_number(values.get("15")),
                high_price=_number(values.get("17"), absolute=True),
                trade_time=str(values["20"]).strip() if values.get("20") is not None else None,
                change_rate=_decimal(values.get("12")),
                market_cap_eok=_number(values.get("311"), absolute=True),
                market=_market(entry),
                execution_strength=_decimal(values.get("228")),
                session_type=str(values.get("290", "")).strip(),
            )
        )
    return tuple(ticks)


def parse_order_executions(
    message: dict[str, Any],
    account_scope_resolver: Callable[[str], AccountScope | None] | None = None,
) -> tuple[OrderExecution, ...]:
    """주문체결(00)의 접수·정정 이벤트를 제외하고 실제 체결만 해석한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[OrderExecution] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") != "00":
            continue
        values = entry.get("values")
        if not isinstance(values, dict) or str(values.get("913", "")).strip() != "체결":
            continue
        scope = _realtime_account_scope(values, account_scope_resolver)
        if scope is None:
            continue
        # Official 00 defines 914/915 as the unit fill and 910/911 as the
        # broker's fill fields. Prefer the unit pair so two fills in one second
        # remain distinct, while keeping the older pair as a compatibility fallback.
        price = _number(values.get("914"), absolute=True) or _number(values.get("910"), absolute=True) or 0
        quantity = _number(values.get("915"), absolute=True) or _number(values.get("911"), absolute=True) or 0
        if price <= 0 or quantity <= 0:
            continue
        raw_side = str(values.get("905", "")).strip().replace("+", "").replace("-", "")
        side = "매수" if "매수" in raw_side else "매도" if "매도" in raw_side else raw_side
        raw_code = str(values.get("9001") or entry.get("item") or "").strip()
        result.append(OrderExecution(
            str(values.get("9203", "")).strip(), str(values.get("909", "")).strip(),
            raw_code.removeprefix("A").removesuffix("_NX").removesuffix("_AL"),
            str(values.get("302", "")).strip(), side, price, quantity,
            str(values.get("908", "")).strip(), str(values.get("2135", "")).strip(),
            _number(values.get("900"), absolute=True),
            _number(values.get("902"), absolute=True),
            _cumulative_fill(values, quantity),
            scope,
        ))
    return tuple(result)


def parse_account_balance_changes(
    message: dict[str, Any],
    account_scope_resolver: Callable[[str], AccountScope | None] | None = None,
) -> tuple[AccountBalanceChange, ...]:
    """잔고(04)를 계좌 복구 재조회 신호에 필요한 최소 정보로 해석한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[AccountBalanceChange] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") != "04":
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        scope = _realtime_account_scope(values, account_scope_resolver)
        if scope is None:
            continue
        raw_code = str(values.get("9001") or entry.get("item") or "").strip()
        code = raw_code.removeprefix("A").removesuffix("_NX").removesuffix("_AL")
        position_quantity = _number(values.get("930"))
        if len(code) != 6 or not code.isdigit() or position_quantity is None or position_quantity < 0:
            continue
        orderable_quantity = _number(values.get("933"))
        if orderable_quantity is not None and orderable_quantity < 0:
            continue
        result.append(AccountBalanceChange(
            code=code,
            name=str(values.get("302", "")).strip(),
            position_quantity=position_quantity,
            orderable_quantity=orderable_quantity,
            acquisition_price=_number(values.get("931"), absolute=True),
            total_acquisition_won=_number(values.get("932"), absolute=True),
            deposit_won=_number(values.get("951")),
            current_price=_number(values.get("10"), absolute=True),
            market=_market(entry),
            origin_scope=scope,
        ))
    return tuple(result)


def _realtime_account_scope(
    values: dict[str, Any],
    resolver: Callable[[str], AccountScope | None] | None,
) -> AccountScope | None:
    if resolver is None:
        return LEGACY_ACCOUNT_SCOPE
    raw = str(values.get("9201", "")).strip()
    return resolver(raw) if raw else None


def parse_market_index_ticks(message: dict[str, Any]) -> tuple[MarketIndexTick, ...]:
    """코스피(001)·코스닥(101)의 0J/0U 실시간 값을 해석한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[MarketIndexTick] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") not in {"0J", "0U"}:
            continue
        values = entry.get("values")
        raw_item = entry.get("item")
        item = raw_item[0] if isinstance(raw_item, list) and raw_item else raw_item
        market = {"001": "kospi", "101": "kosdaq"}.get(str(item or "").strip())
        if not isinstance(values, dict) or market is None:
            continue
        result.append(MarketIndexTick(
            market=market,
            trade_time=str(values.get("20", "")).strip() or None,
            index_value=_decimal_absolute(values.get("10")),
            change_rate=_decimal(values.get("12")),
            cumulative_trade_value_million_won=_number(values.get("14"), absolute=True),
            advancing_count=_number(values.get("252"), absolute=True),
            declining_count=_number(values.get("255"), absolute=True),
            flat_count=_number(values.get("253"), absolute=True),
        ))
    return tuple(result)


def parse_program_trade_ticks(message: dict[str, Any]) -> tuple[ProgramTradeTick, ...]:
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[ProgramTradeTick] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") != "0w":
            continue
        values = entry.get("values")
        code = _code(entry)
        if not isinstance(values, dict) or not code:
            continue
        result.append(ProgramTradeTick(
            code=code,
            trade_time=str(values.get("20", "")).strip() or None,
            net_buy_quantity=_number(values.get("210")),
            net_buy_quantity_change=_number(values.get("211")),
            net_buy_amount_million_won=_number(values.get("212")),
            net_buy_amount_change_million_won=_number(values.get("213")),
            market=_market(entry),
        ))
    return tuple(result)


def parse_stock_price_references(message: dict[str, Any]) -> tuple[StockPriceReference, ...]:
    """종목정보(0g)의 상·하한가와 기준가를 한 기준 묶음으로 해석한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[StockPriceReference] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") != "0g":
            continue
        values = entry.get("values")
        code = _code(entry)
        if not isinstance(values, dict) or not code:
            continue
        result.append(StockPriceReference(
            code=code,
            upper_limit_price=_number(values.get("305"), absolute=True),
            lower_limit_price=_number(values.get("306"), absolute=True),
            base_price=_number(values.get("307"), absolute=True),
            market=_market(entry),
        ))
    return tuple(result)


def _cumulative_fill(values: dict[str, Any], unit_quantity: int) -> int | None:
    ordered = _number(values.get("900"), absolute=True)
    remaining = _number(values.get("902"), absolute=True)
    if ordered is not None and remaining is not None and remaining <= ordered:
        return max(unit_quantity, ordered - remaining)
    cumulative = _number(values.get("911"), absolute=True)
    return max(unit_quantity, cumulative) if cumulative is not None else unit_quantity


def parse_vi_events(message: dict[str, Any]) -> tuple[ViEvent, ...]:
    """공식 시장 전체 실시간 타입 1h를 해석한다.

    서버가 알 수 없는 코드값은 임의로 번역하지 않고 원문을 남긴다.
    """
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[ViEvent] = []
    for entry in message.get("data") or ():
        if not isinstance(entry, dict) or entry.get("type") != "1h":
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        code = str(values.get("9001") or _code(entry)).strip().removeprefix("A")
        if not code:
            continue
        category = str(values.get("9068", "")).strip()
        result.append(ViEvent(
            code=code.removesuffix("_NX").removesuffix("_AL"),
            name=str(values.get("302", "")).strip(),
            event_kind={"1": "ACTIVATED", "2": "RELEASED"}.get(category, category or "UNKNOWN"),
            vi_type={"1": "STATIC", "2": "DYNAMIC", "3": "BOTH"}.get(
                str(values.get("1225", "")).strip(), str(values.get("1225", "")).strip() or "UNKNOWN",
            ),
            trigger_price=_number(values.get("1221"), absolute=True),
            trigger_time=str(values.get("1223", "")).strip() or None,
            release_time=str(values.get("1224", "")).strip() or None,
            direction=str(values.get("9069", "")).strip(),
            trigger_count=_number(values.get("1490"), absolute=True),
            exchange=str(values.get("9081", entry.get("stex_tp", ""))).strip(),
            cumulative_volume=_number(values.get("13"), absolute=True),
            cumulative_trade_value=_number(values.get("14"), absolute=True),
            raw_values={str(key): value for key, value in values.items()},
        ))
    return tuple(result)


def _code(entry: dict[str, Any]) -> str:
    for field in ("item", "stk_cd", "code"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().removesuffix("_NX").removesuffix("_AL")
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0].strip().removesuffix("_NX").removesuffix("_AL")
    return ""


def _market(entry: dict[str, Any]) -> str:
    for field in ("item", "stk_cd", "code"):
        value = entry.get(field)
        raw = value.strip() if isinstance(value, str) else value[0].strip() if isinstance(value, list) and value and isinstance(value[0], str) else ""
        if raw:
            if raw.endswith("_NX"):
                return "NXT"
            if raw.endswith("_AL"):
                return "SOR"
            return "KRX"
    return "KRX"


def _number(value: object, *, absolute: bool = False) -> int | None:
    if value is None:
        return None
    try:
        number = int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return abs(number) if absolute else number


def _decimal(value: object) -> float | None:
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _decimal_absolute(value: object) -> float | None:
    number = _decimal(value)
    return abs(number) if number is not None else None

"""키움 WebSocket 주식체결(0B) 메시지 해석."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def parse_trade_ticks(message: dict[str, Any]) -> tuple[TradeTick, ...]:
    """`REAL` 0B 수신 메시지에서 화면에 필요한 값만 추출한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    ticks: list[TradeTick] = []
    for entry in message.get("data", []):
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


def parse_order_executions(message: dict[str, Any]) -> tuple[OrderExecution, ...]:
    """주문체결(00)의 접수·정정 이벤트를 제외하고 실제 체결만 해석한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[OrderExecution] = []
    for entry in message.get("data", []):
        if not isinstance(entry, dict) or entry.get("type") != "00":
            continue
        values = entry.get("values")
        if not isinstance(values, dict) or str(values.get("913", "")).strip() != "체결":
            continue
        price = _number(values.get("910"), absolute=True) or 0
        quantity = _number(values.get("911"), absolute=True) or 0
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
        ))
    return tuple(result)


def parse_market_index_ticks(message: dict[str, Any]) -> tuple[MarketIndexTick, ...]:
    """코스피(001)·코스닥(101)의 0J/0U 실시간 값을 해석한다."""
    if str(message.get("trnm", "")).upper() != "REAL":
        return ()
    result: list[MarketIndexTick] = []
    for entry in message.get("data", []):
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
    for entry in message.get("data", []):
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
            return "NXT" if raw.endswith("_NX") else "KRX"
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

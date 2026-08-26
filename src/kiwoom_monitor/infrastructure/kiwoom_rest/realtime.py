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
            )
        )
    return tuple(ticks)


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

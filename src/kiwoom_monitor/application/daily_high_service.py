"""ka10005 일봉으로 5일·20일 신고가 기준 가격을 계산한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DailyHighTargets:
    high_5_price: int | None
    high_20_price: int | None
    previous_day_trade_value_eok: float | None = None


class DailyHighService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load(self, code: str) -> DailyHighTargets:
        response = self._client.request("ka10005", "/api/dostk/mrkcond", {"stk_cd": code})
        records = response.get("stk_ddwkmm", [])
        if not isinstance(records, list):
            raise ValueError("ka10005 일봉 목록 형식이 올바르지 않습니다.")
        daily_rows = sorted(
            ((str(row.get("date", "")).strip(), row) for row in records if isinstance(row, dict)),
            key=lambda value: value[0], reverse=True,
        )
        prices = [price for _, row in daily_rows if (price := _price(row.get("high_pric"))) is not None]
        previous_trade_value = _daily_trade_value(daily_rows[1][1]) if len(daily_rows) > 1 else None
        return DailyHighTargets(_highest(prices[:5]), _highest(prices[:20]), previous_trade_value)


def _price(value: object) -> int | None:
    try:
        price = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _highest(prices: list[int]) -> int | None:
    return max(prices) if prices else None


def _daily_trade_value(row: dict[str, Any]) -> float | None:
    values = tuple(_price(row.get(key)) for key in ("open_pric", "high_pric", "low_pric", "cur_prc", "trde_qty"))
    if any(value is None for value in values):
        return None
    open_price, high_price, low_price, close_price, volume = values
    return volume * (high_price + open_price + low_price + close_price) / 4 / 100_000_000

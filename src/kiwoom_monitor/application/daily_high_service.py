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


class DailyHighService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load(self, code: str) -> DailyHighTargets:
        response = self._client.request("ka10005", "/api/dostk/mrkcond", {"stk_cd": code})
        records = response.get("stk_ddwkmm", [])
        if not isinstance(records, list):
            raise ValueError("ka10005 일봉 목록 형식이 올바르지 않습니다.")
        bars = sorted(
            ((str(row.get("date", "")).strip(), _price(row.get("high_pric"))) for row in records if isinstance(row, dict)),
            key=lambda value: value[0], reverse=True,
        )
        prices = [price for _, price in bars if price is not None]
        return DailyHighTargets(_highest(prices[:5]), _highest(prices[:20]))


def _price(value: object) -> int | None:
    try:
        price = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _highest(prices: list[int]) -> int | None:
    return max(prices) if prices else None

"""ka10001 기본정보에서 거래강도용 유통시가총액 원천을 읽는다."""
from __future__ import annotations
from typing import Any, Protocol
from .trade_strength import StockFundamentals


def _positive_int(value: object) -> int | None:
    try:
        number = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...

class StockFundamentalsService:
    def __init__(self, client: RestClient) -> None: self._client = client
    def load(self, code: str) -> StockFundamentals:
        row = self._client.request("ka10001", "/api/dostk/stkinfo", {"stk_cd": code})
        try:
            return StockFundamentals(
                float(str(row["mac"]).replace(",", "")),
                float(str(row["dstr_rt"]).replace(",", "")),
                _positive_int(row.get("250hgst")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{code}의 시가총액 또는 유통비율 값이 올바르지 않습니다.") from error

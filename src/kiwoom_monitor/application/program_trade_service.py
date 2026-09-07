"""장 마감 후 체결 스냅샷의 프로그램매매 누락값 보완."""

from __future__ import annotations

from datetime import date
from typing import Protocol


class ProgramTradeClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]: ...


class ProgramTradeService:
    def __init__(self, client: ProgramTradeClient) -> None:
        self._client = client

    def load_day(self, code: str, trade_date: date) -> tuple[dict[str, object], ...]:
        day = trade_date.strftime("%Y%m%d")
        try:
            response = self._request(f"{code}_AL", day)
        except Exception:
            response = self._request(code, day)
        rows = response.get("stk_tm_prm_trde_trnsn")
        if not isinstance(rows, list):
            return ()
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            result.append({
                "available": True, "source": "ka90008_after_close", "trade_time": str(row.get("tm", "")),
                "market": str(row.get("stex_tp", "통합")),
                "net_buy_amount_million_won": _integer(row.get("prm_netprps_amt")),
                "net_buy_amount_change_million_won": _integer(row.get("prm_netprps_amt_irds")),
                "net_buy_quantity": _integer(row.get("prm_netprps_qty")),
                "net_buy_quantity_change": _integer(row.get("prm_netprps_qty_irds")),
            })
        return tuple(result)

    def _request(self, code: str, day: str) -> dict[str, object]:
        return self._client.request("ka90008", "/api/dostk/mrkcond", {
            "amt_qty_tp": "1", "stk_cd": code, "date": day,
        })


def _integer(value: object) -> int | None:
    try:
        return int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None

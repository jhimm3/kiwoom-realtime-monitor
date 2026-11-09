"""장 마감 후 체결 스냅샷의 프로그램매매 누락값 보완."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Callable

from kiwoom_monitor.domain.time_basis import as_kst, kst_now
from typing import Protocol


class ProgramTradeClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]: ...


class ProgramTradeService:
    def __init__(self, client: ProgramTradeClient, *, now_provider: Callable[[], datetime] = kst_now) -> None:
        self._client = client
        self._now = now_provider

    def load_day(self, code: str, trade_date: date) -> tuple[dict[str, object], ...]:
        day = trade_date.strftime("%Y%m%d")
        stored_loader = getattr(self._client, "load_stored_program_flow", None)
        stored = stored_loader(code, day) if callable(stored_loader) else None
        if isinstance(stored, dict):
            response = stored
        else:
            now = as_kst(self._now())
            if now.date() != trade_date or now.time() < time(20, 5):
                return ()
            try:
                response = self._request(f"{code}_AL", day)
            except Exception:
                if as_kst(self._now()).date() != trade_date:
                    return ()
                response = self._request(code, day)
            if as_kst(self._now()).date() != trade_date:
                return ()
        return tuple(program_trade_rows(response.get("stk_tm_prm_trde_trnsn")))

    def _request(self, code: str, day: str) -> dict[str, object]:
        return self._client.request("ka90008", "/api/dostk/mrkcond", {
            "amt_qty_tp": "1", "stk_cd": code, "date": day,
        })


def program_trade_rows(rows: object) -> list[dict[str, object]]:
    """Project Kiwoom and existing stored rows onto the same program-flow fields."""
    if not isinstance(rows, list):
        return []
    return [{
        "available": bool(row.get("available", True)),
        "source": str(row.get("source", "ka90008_after_close")),
        "trade_time": str(row.get("trade_time", row.get("tm", ""))),
        "market": str(row.get("market", row.get("stex_tp", "통합"))),
        "net_buy_amount_million_won": _integer(row.get("net_buy_amount_million_won", row.get("prm_netprps_amt"))),
        "net_buy_amount_change_million_won": _integer(row.get("net_buy_amount_change_million_won", row.get("prm_netprps_amt_irds"))),
        "net_buy_quantity": _integer(row.get("net_buy_quantity", row.get("prm_netprps_qty"))),
        "net_buy_quantity_change": _integer(row.get("net_buy_quantity_change", row.get("prm_netprps_qty_irds"))),
    } for row in rows if isinstance(row, dict)]


def _integer(value: object) -> int | None:
    raw = str(value).strip().replace(",", "")
    # Kiwoom's program-flow response uses a double minus for some net sells.
    if raw.startswith("--") and raw[2:].isdigit():
        raw = raw[1:]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None

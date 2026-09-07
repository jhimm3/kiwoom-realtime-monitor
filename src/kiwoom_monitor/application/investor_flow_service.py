"""체결 시점 복기에 사용할 외국인·기관 수급 조회."""

from __future__ import annotations

from datetime import datetime, time
from typing import Protocol


class InvestorClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]: ...


class InvestorFlowService:
    def __init__(self, client: InvestorClient) -> None:
        self._client = client

    def load(self, code: str, observed_at: datetime) -> dict[str, object]:
        day = observed_at.strftime("%Y%m%d")
        basis = "SOR"
        try:
            response = self._request(f"{code}_AL", day)
            row = _same_day_row(response, day)
            if row is None:
                basis = "KRX"
                response = self._request(code, day)
                row = _same_day_row(response, day)
        except Exception:
            basis = "KRX"
            response = self._request(code, day)
            row = _same_day_row(response, day)
        foreign = _integer(row.get("for_daly_nettrde_qty")) if row else None
        institution = _integer(row.get("orgn_daly_nettrde_qty")) if row else None
        # ka10045의 당일 장중 값은 투자자별 집계 시점이 달라 한쪽만 먼저
        # 채워지기도 한다. 둘 중 하나가 0이 아니어도 확정값으로 취급하지 않고
        # 20:05 이후 장 마감 보완 결과로 교체한다.
        same_day_pending = bool(
            row and str(row.get("dt", "")) == day and observed_at.time() < time(20, 5)
        )
        return {
            "source": "ka10045", "requested_at": observed_at.isoformat(timespec="seconds"), "market_basis": basis,
            "as_of_date": str(row.get("dt", "")) if row else "",
            "foreign_net_buy_quantity": None if same_day_pending else foreign,
            "institution_net_buy_quantity": None if same_day_pending else institution,
            "foreign_period_accumulated": _integer(row.get("for_dt_acc")) if row else None,
            "institution_period_accumulated": _integer(row.get("orgn_dt_acc")) if row else None,
            "available": row is not None and not same_day_pending,
            "status": "pending_close" if same_day_pending else ("confirmed" if row else "unavailable"),
        }

    def _request(self, code: str, day: str) -> dict[str, object]:
        return self._client.request("ka10045", "/api/dostk/mrkcond", {
            "stk_cd": code, "strt_dt": day, "end_dt": day,
            "orgn_prsm_unp_tp": "1", "for_prsm_unp_tp": "1",
        })


def _integer(value: object) -> int | None:
    try:
        return int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _same_day_row(response: dict[str, object], day: str) -> dict[str, object] | None:
    rows = response.get("stk_orgn_trde_trnsn")
    if not isinstance(rows, list):
        return None
    return next(
        (value for value in rows if isinstance(value, dict) and str(value.get("dt", "")) == day),
        None,
    )

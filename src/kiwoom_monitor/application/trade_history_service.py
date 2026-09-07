"""키움 계좌별 주문체결내역을 매매일지용 체결 자료로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


class RestClient(Protocol):
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


@dataclass(frozen=True)
class TradeFill:
    order_no: str
    stock_code: str
    stock_name: str
    side: str
    filled_at: datetime
    quantity: int
    price: int
    order_type: str = ""
    market: str = ""


class TradeHistoryService:
    """kt00007을 날짜별로 조회한다. 한 날짜의 연속 페이지만 추가 요청한다."""

    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load_day(self, day: date) -> tuple[TradeFill, ...]:
        body = {
            "ord_dt": day.strftime("%Y%m%d"), "qry_tp": "4", "stk_bond_tp": "1",
            "sell_tp": "0", "stk_cd": "", "fr_ord_no": "", "dmst_stex_tp": "%",
        }
        records: list[dict[str, Any]] = []
        cont_yn, next_key = "N", ""
        for _ in range(20):
            response, has_next, response_next_key = self._client.request_with_continuation(
                "kt00007", "/api/dostk/acnt", body, cont_yn=cont_yn, next_key=next_key
            )
            values = response.get("acnt_ord_cntr_prps_dtl", [])
            if isinstance(values, list):
                records.extend(value for value in values if isinstance(value, dict))
            if not has_next or not response_next_key:
                break
            cont_yn, next_key = "Y", response_next_key
        fills = tuple(fill for record in records if (fill := self._to_fill(record, day)) is not None)
        return tuple(sorted({(fill.order_no, fill.stock_code, fill.filled_at, fill.side): fill for fill in fills}.values(), key=lambda fill: fill.filled_at, reverse=True))

    @staticmethod
    def _to_fill(record: dict[str, Any], day: date) -> TradeFill | None:
        quantity = _number(record.get("cntr_qty"))
        price = _number(record.get("cntr_uv"))
        if quantity <= 0 or price <= 0:
            return None
        side_text = str(record.get("io_tp_nm", "")).strip()
        side = "매도" if "매도" in side_text else ("매수" if "매수" in side_text else "")
        if not side:
            return None
        # 문서에는 ord_dtm으로 표기되어 있지만 실계좌 kt00007 응답은
        # ord_tm(HH:mm:ss)을 사용한다. 두 형식을 모두 받아 과거 시각을 보존한다.
        raw_clock = record.get("ord_tm") or record.get("ord_dtm") or record.get("cnfm_tm") or ""
        clock = "".join(character for character in str(raw_clock) if character.isdigit())[-6:].zfill(6)
        try:
            filled_at = datetime.combine(day, datetime.strptime(clock, "%H%M%S").time())
        except ValueError:
            return None
        code = str(record.get("stk_cd", "")).strip().lstrip("AJQ").zfill(6)
        if not code:
            return None
        return TradeFill(
            order_no=str(record.get("ord_no", "")).strip(), stock_code=code,
            stock_name=str(record.get("stk_nm", "")).strip(), side=side, filled_at=filled_at,
            quantity=quantity, price=price, order_type=str(record.get("trde_tp", "")).strip(),
            market=str(record.get("dmst_stex_tp", "")).strip(),
        )


def _number(value: object) -> int:
    try:
        return abs(int(str(value or "0").replace(",", "").strip()))
    except ValueError:
        return 0

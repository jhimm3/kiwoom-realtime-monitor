"""키움 계좌별 주문체결내역을 매매일지용 체결 자료로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from kiwoom_monitor.domain.order_contract import AccountScope, LEGACY_ACCOUNT_SCOPE
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import (
    AccountBatchClient,
    AccountQueryContext,
    as_account_batch_client,
)


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
    origin_scope: AccountScope = LEGACY_ACCOUNT_SCOPE
    canonical_scope: AccountScope | None = None

    def __post_init__(self) -> None:
        canonical = self.canonical_scope or self.origin_scope
        if (
            canonical.broker != self.origin_scope.broker
            or canonical.environment != self.origin_scope.environment
        ):
            raise ValueError("canonical trade scope cannot cross broker or environment")

    @property
    def effective_scope(self) -> AccountScope:
        return self.canonical_scope or self.origin_scope


@dataclass(frozen=True)
class TradeHistoryBatch:
    fills: tuple[TradeFill, ...]
    context: AccountQueryContext


class TradeHistoryService:
    """kt00007을 날짜별로 조회한다. 한 날짜의 연속 페이지만 추가 요청한다."""

    def __init__(self, client: RestClient | AccountBatchClient, account_scope: AccountScope = LEGACY_ACCOUNT_SCOPE) -> None:
        if account_scope != LEGACY_ACCOUNT_SCOPE:
            raise ValueError("verified account scope must come from the account-query context")
        self._client = as_account_batch_client(client)

    def load_day(self, day: date) -> tuple[TradeFill, ...]:
        return self.load_day_batch(day).fills

    def load_day_batch(self, day: date) -> TradeHistoryBatch:
        body = {
            "ord_dt": day.strftime("%Y%m%d"), "qry_tp": "4", "stk_bond_tp": "1",
            "sell_tp": "0", "stk_cd": "", "fr_ord_no": "", "dmst_stex_tp": "%",
        }
        batch = self._client.query_account_pages("kt00007", "/api/dostk/acnt", body)
        records: list[dict[str, Any]] = []
        for response in batch.pages:
            values = response.get("acnt_ord_cntr_prps_dtl", [])
            if isinstance(values, list):
                records.extend(value for value in values if isinstance(value, dict))
        fills = tuple(fill for record in records if (fill := self._to_fill(record, day, batch.context.scope)) is not None)
        ordered = tuple(sorted({(fill.order_no, fill.stock_code, fill.filled_at, fill.side): fill for fill in fills}.values(), key=lambda fill: fill.filled_at, reverse=True))
        return TradeHistoryBatch(ordered, batch.context)

    def _to_fill(self, record: dict[str, Any], day: date, account_scope: AccountScope) -> TradeFill | None:
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
            origin_scope=account_scope,
        )


def _number(value: object) -> int:
    try:
        return abs(int(str(value or "0").replace(",", "").strip()))
    except ValueError:
        return 0

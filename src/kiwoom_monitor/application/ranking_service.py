"""순위와 신고가 목록을 결합하는 애플리케이션 서비스."""

from __future__ import annotations

import time
from typing import Any, Protocol

from kiwoom_monitor.domain.ranking import RankedStock


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...

class StockWriter(Protocol):
    def upsert(self, code: str, name: str, market: str = "") -> None: ...


class RankingService:
    """ka00198 Top 20에 ka10016 신고가 상태를 결합한다."""

    NEW_HIGH_PERIODS = (5, 20, 250)
    STOCK_INFO_PATH = "/api/dostk/stkinfo"

    def __init__(self, client: RestClient, high_cache_seconds: float = 60.0, stocks: StockWriter | None = None) -> None:
        self._client = client
        self._high_cache_seconds = high_cache_seconds
        self._new_high_cache: dict[int, set[str]] = {}
        self._new_high_cached_at = 0.0
        self._stocks = stocks

    def load_top_stocks(self) -> tuple[RankedStock, ...]:
        new_high_codes = self._new_high_cache or {period: set() for period in self.NEW_HIGH_PERIODS}
        response = self._client.request("ka00198", self.STOCK_INFO_PATH, {"qry_tp": "1"})
        records = response.get("item_inq_rank", [])
        if not isinstance(records, list):
            raise ValueError("ka00198의 item_inq_rank 형식이 올바르지 않습니다.")

        stocks: list[RankedStock] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            code = str(record.get("stk_cd", "")).strip()
            name = str(record.get("stk_nm", "")).strip()
            if not code or not name:
                continue
            if self._stocks is not None:
                self._stocks.upsert(code, name)
            periods = frozenset(period for period, codes in new_high_codes.items() if code in codes)
            stocks.append(
                RankedStock(
                    rank=self._to_int(record.get("bigd_rank"), fallback=len(stocks) + 1),
                    code=code,
                    name=name,
                    change_rate=str(record.get("base_comp_chgr", "-")).strip() or "-",
                    new_high_periods=periods,
                )
            )
        return tuple(stocks)

    def _load_new_high_codes(self, period: int) -> set[str]:
        response = self._client.request(
            "ka10016",
            self.STOCK_INFO_PATH,
            {
                "mrkt_tp": "000",
                "ntl_tp": "1",
                "high_low_close_tp": "1",
                "stk_cnd": "0",
                "trde_qty_tp": "00000",
                "crd_cnd": "0",
                "updown_incls": "0",
                "dt": str(period),
                "stex_tp": "1",
            },
        )
        records = response.get("ntl_pric", [])
        if not isinstance(records, list):
            raise ValueError("ka10016의 ntl_pric 형식이 올바르지 않습니다.")
        return {str(record.get("stk_cd", "")).strip() for record in records if isinstance(record, dict)} - {""}

    def _load_new_high_codes_cached(self) -> dict[int, set[str]]:
        if self._new_high_cache and time.monotonic() - self._new_high_cached_at < self._high_cache_seconds:
            return self._new_high_cache
        self._new_high_cache = {period: self._load_new_high_codes(period) for period in self.NEW_HIGH_PERIODS}
        self._new_high_cached_at = time.monotonic()
        return self._new_high_cache

    def refresh_new_highs(self) -> None:
        """사용자가 요청할 때만 신고가 목록을 다시 조회한다."""
        self._new_high_cache = {period: self._load_new_high_codes(period) for period in self.NEW_HIGH_PERIODS}
        self._new_high_cached_at = time.monotonic()

    @staticmethod
    def _to_int(value: object, fallback: int) -> int:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return fallback

"""순위와 신고가 목록을 결합하는 애플리케이션 서비스."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Protocol

from kiwoom_monitor.domain.ranking import RankedStock


logger = logging.getLogger(__name__)


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...

class StockWriter(Protocol):
    def upsert(self, code: str, name: str, market: str = "") -> None: ...
    def upsert_many(self, stocks: tuple[tuple[str, str, str], ...]) -> None: ...
    def load_new_highs(self, periods: tuple[int, ...]) -> dict[int, set[str]]: ...
    def update_new_highs(self, values: dict[int, set[str]], checked_at: str) -> None: ...


class RankingService:
    """ka00198 Top 20에 ka10016 신고가 상태를 결합한다."""

    NEW_HIGH_PERIODS = (5, 20, 250)
    EXPECTED_STOCKS = 20
    STOCK_INFO_PATH = "/api/dostk/stkinfo"
    STALE_SNAPSHOT_RETRY_LIMIT = 20

    def __init__(self, client: RestClient, high_cache_seconds: float = 60.0, stocks: StockWriter | None = None, query_type: str = "5") -> None:
        self._client = client
        self._high_cache_seconds = high_cache_seconds
        self._new_high_cache: dict[int, set[str]] = {}
        self._new_high_cached_at = 0.0
        self._stocks = stocks
        self._query_type = query_type if query_type in {"1", "2", "3", "4", "5"} else "5"
        self._missing_current_price_logged: set[str] = set()

    def set_query_type(self, query_type: str) -> None:
        if query_type not in {"1", "2", "3", "4", "5"}:
            raise ValueError("순위 조회 기준이 올바르지 않습니다.")
        self._query_type = query_type

    def server_now(self) -> object | None:
        provider = getattr(self._client, "server_now", None)
        return provider() if callable(provider) else None

    def load_top_stocks(self) -> tuple[RankedStock, ...]:
        if not self._new_high_cache and self._stocks is not None:
            loader = getattr(self._stocks, "load_new_highs", None)
            if callable(loader):
                self._new_high_cache = loader(self.NEW_HIGH_PERIODS)
        new_high_codes = self._new_high_cache or {period: set() for period in self.NEW_HIGH_PERIODS}
        response: dict[str, Any] = {}
        # 간헐적으로 ka00198이 일부 순위만 반환한다. 정상 응답(20개)을
        # 우선 사용하도록 짧게 재시도하고, 끝까지 부분 응답이면 UI가
        # 기존 순위표를 유지하도록 그대로 반환한다.
        partial_response_retries = 0
        stale_snapshot_retries = 0
        while True:
            response = self._client.request("ka00198", self.STOCK_INFO_PATH, {"qry_tp": self._query_type})
            records = response.get("item_inq_rank", [])
            if isinstance(records, list) and len(records) >= self.EXPECTED_STOCKS:
                snapshot_at = self._snapshot_at(records)
                if self._is_stale_snapshot(snapshot_at) and stale_snapshot_retries < self.STALE_SNAPSHOT_RETRY_LIMIT:
                    stale_snapshot_retries += 1
                    logger.info(
                        "ka00198이 이전 기준 스냅샷(%s)을 반환해 0.75초 뒤 재조회합니다. (%d/%d)",
                        snapshot_at.strftime("%H:%M:%S") if snapshot_at else "알 수 없음",
                        stale_snapshot_retries,
                        self.STALE_SNAPSHOT_RETRY_LIMIT,
                    )
                    time.sleep(0.75)
                    continue
                break
            if partial_response_retries < 2:
                partial_response_retries += 1
                time.sleep(0.4)
                continue
            break
        records = response.get("item_inq_rank", [])
        if not isinstance(records, list):
            raise ValueError("ka00198의 item_inq_rank 형식이 올바르지 않습니다.")
        first_record = next((record for record in records if isinstance(record, dict)), {})
        preview = ", ".join(
            f"{str(record.get('bigd_rank', '?')).strip()}위 {str(record.get('stk_nm', '')).strip()}"
            for record in records[:3]
            if isinstance(record, dict)
        )
        logger.info(
            "ka00198 응답 기준: %s %s · 상위 %s",
            str(first_record.get("dt", "")).strip(),
            str(first_record.get("tm", "")).strip(),
            preview,
        )

        stocks: list[RankedStock] = []
        stock_rows: list[tuple[str, str, str]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            code = str(record.get("stk_cd", "")).strip()
            name = str(record.get("stk_nm", "")).strip()
            if not code or not name:
                continue
            stock_rows.append((code, name, ""))
            periods = frozenset(period for period, codes in new_high_codes.items() if code in codes)
            current_price = self._ranking_current_price(record)
            if current_price is None and code not in self._missing_current_price_logged:
                self._missing_current_price_logged.add(code)
                price_fields = {
                    key: record.get(key)
                    for key in record
                    if "pr" in key.casefold() or "price" in key.casefold()
                }
                logger.warning("ka00198 현재가 누락: %s(%s) · 가격 관련 응답=%s", name, code, price_fields)
            stocks.append(
                RankedStock(
                    rank=self._to_int(record.get("bigd_rank"), fallback=len(stocks) + 1),
                    code=code,
                    name=name,
                    change_rate=str(record.get("base_comp_chgr", "-")).strip() or "-",
                    new_high_periods=periods,
                    current_price=current_price,
                )
            )
        if self._stocks is not None:
            batch = getattr(self._stocks, "upsert_many", None)
            if callable(batch):
                batch(tuple(stock_rows))
            else:
                for code, name, market in stock_rows:
                    self._stocks.upsert(code, name, market)
        return tuple(stocks)

    def _is_stale_snapshot(self, snapshot_at: datetime | None) -> bool:
        """각 순위 기준 시각보다 이전 스냅샷이면 한 번만 보정 조회한다."""
        if snapshot_at is None:
            return False
        now = self.server_now()
        if not isinstance(now, datetime):
            return False
        base = now.replace(second=0, microsecond=0)
        if self._query_type in {"5", "4"}:
            expected = base.replace(second=30) if now.second >= 30 else base
        elif self._query_type == "1":
            expected = base
        elif self._query_type == "2":
            expected = base - timedelta(minutes=base.minute % 10)
        elif self._query_type == "3":
            expected = base.replace(minute=0)
        else:
            return False
        return snapshot_at < expected

    @staticmethod
    def _snapshot_at(records: object) -> datetime | None:
        if not isinstance(records, list):
            return None
        record = next((value for value in records if isinstance(value, dict)), None)
        if record is None:
            return None
        date = str(record.get("dt", "")).strip()
        clock = str(record.get("tm", "")).strip().zfill(6)
        if len(date) != 8 or len(clock) != 6 or not date.isdigit() or not clock.isdigit():
            return None
        try:
            return datetime.strptime(f"{date}{clock}", "%Y%m%d%H%M%S")
        except ValueError:
            return None

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
        if self._stocks is not None:
            updater = getattr(self._stocks, "update_new_highs", None)
            if callable(updater):
                now = self.server_now()
                checked_at = now.strftime("%Y-%m-%d %H:%M:%S") if isinstance(now, datetime) else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updater(self._new_high_cache, checked_at)

    @staticmethod
    def _to_int(value: object, fallback: int) -> int:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _to_price(value: object) -> int | None:
        try:
            price = abs(int(str(value).strip().replace(",", "")))
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None

    @classmethod
    def _ranking_current_price(cls, record: dict[str, Any]) -> int | None:
        # ka00198 문서상 cur_prc를 우선 사용한다. 서버 버전·시장 구분에 따라
        # 동일 의미의 필드명이 다르게 올 수 있어 호환 필드도 함께 처리한다.
        for key in ("cur_prc", "past_curr_prc", "now_pric", "current_price", "cur_price", "close_pric"):
            if (price := cls._to_price(record.get(key))) is not None:
                return price
        return None

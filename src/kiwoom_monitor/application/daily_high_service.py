"""ka10081 일봉으로 신고가 기준과 전일 거래대금을 조회한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol

from .daily_bar_coverage import DailySourceWindow, PERIODS, assess_daily_coverage, daily_query_date


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DailyBar:
    trade_date: str
    high_price: int
    trade_value_eok: float | None
    close_price: int | None = None
    open_price: int | None = None
    low_price: int | None = None
    volume: int | None = None


@dataclass(frozen=True)
class DailyHighTargets:
    high_5_price: int | None
    high_20_price: int | None
    high_250_price: int | None
    previous_day_trade_value_eok: float | None = None
    previous_day_close_price: int | None = None
    daily_trade_values_eok: tuple[tuple[str, float], ...] = ()
    daily_bars: tuple[DailyBar, ...] = ()
    period_statuses: tuple[tuple[str, str], ...] = ()
    collection_verified: bool = False
    scope: str = "unverified"
    query_basis_date: str = ""
    window_end: str = ""
    cached_high_250_price: int | None = None
    source_exhausted: bool = False

    def period_status(self, period: str) -> str:
        return dict(self.period_statuses).get(str(period), "unverified")

    def period_verified(self, period: str) -> bool:
        return self.period_status(period) in {"ready", "provisional"}

    @classmethod
    def from_daily_bars(
        cls,
        bars: tuple[DailyBar, ...],
        *,
        as_of: date | None = None,
        include_high_250: bool = True,
        period_statuses: tuple[tuple[str, str], ...] = (),
        collection_verified: bool = False,
        scope: str = "unverified",
        query_basis_date: str = "",
        window_end: str = "",
        cached_high_250_price: int | None = None,
        source_exhausted: bool = False,
    ) -> "DailyHighTargets":
        today_date = as_of or date.today()
        by_date = {}
        conflicting = set()
        for bar in bars:
            try:
                day = date.fromisoformat(f"{bar.trade_date[:4]}-{bar.trade_date[4:6]}-{bar.trade_date[6:]}")
            except ValueError:
                continue
            if day > today_date or bar.high_price <= 0:
                continue
            if bar.trade_date in by_date and by_date[bar.trade_date] != bar:
                conflicting.add(bar.trade_date)
            by_date[bar.trade_date] = bar
        ordered = tuple(by_date[key] for key in sorted(by_date, reverse=True))
        prices = [bar.high_price for bar in ordered]
        # 장중에는 오늘 일봉 다음의 전일 봉을, 장전·주말·장 종료 뒤에는
        # 가장 최신 완료 일봉을 직전 1일로 사용한다.
        today = (as_of or date.today()).strftime("%Y%m%d")
        previous_index = 1 if ordered and ordered[0].trade_date == today else 0
        previous_bar = ordered[previous_index] if len(ordered) > previous_index else None
        previous_value = previous_bar.trade_value_eok if previous_bar is not None else None
        previous_close = previous_bar.close_price if previous_bar is not None else None
        # 개발 확인 CSV는 최근 30일만 사용하지만, 원본 일봉은 신고가 계산과
        # 매매일지 재사용을 위해 최근 250거래일까지 보존한다.
        values = tuple((bar.trade_date, bar.trade_value_eok) for bar in ordered[:30] if bar.trade_value_eok is not None)
        # ka10001의 250일 최고가는 권리 조정 전 가격일 수 있다. 현재가·차트와
        # 같은 수정주가 기준은 ka10081 일봉의 최근 250개 고가로 계산한다.
        states = dict(period_statuses)
        for n in PERIODS:
            if conflicting or (n == 250 and not include_high_250):
                states[str(n)] = "unverified"
            elif len(ordered) < n and not source_exhausted and states.get(str(n)) in {"ready", "provisional"}:
                states[str(n)] = "insufficient_history"
        highs = [max(prices[:n]) if prices and (len(prices) >= n or source_exhausted)
                 and states.get(str(n)) in {"ready", "provisional"}
                 else None for n in PERIODS]
        return cls(*highs, previous_value, previous_close, values, ordered[:250],
                   tuple((str(n), states.get(str(n), "unverified")) for n in PERIODS),
                   collection_verified and not conflicting, scope, query_basis_date, window_end,
                   cached_high_250_price, source_exhausted)


class DailyHighService:
    def __init__(
        self,
        client: RestClient,
        *,
        include_nxt: bool = False,
        cached_high_250_loader: Callable[[str], int | None] | None = None,
    ) -> None:
        self._client = client
        self._include_nxt = include_nxt
        self._cached_high_250_loader = cached_high_250_loader

    def load(self, code: str) -> DailyHighTargets:
        # 영웅문의 KRXNXT 표기와 맞추기 위해 신고가·최고가와 직전 거래대금에
        # NXT 일봉을 함께 반영한다.
        krx_bars, krx_coverage = self._load_bars_with_coverage(code)
        bars, coverages = krx_bars, [krx_coverage]
        if not self._include_nxt:
            return self._targets(bars, coverages)
        try:
            nxt_bars, nxt_coverage = self._load_bars_with_coverage(f"{code}_NX")
            bars = _combine_krx_nxt_bars(krx_bars, nxt_bars)
            coverages.append(nxt_coverage)
        except Exception:
            coverages.append({})
        return self._targets(bars, coverages, code=code)

    def _targets(self, bars: tuple[DailyBar, ...], coverages: list[dict[str, Any]], *, code: str = "") -> DailyHighTargets:
        same_window = len({(coverage.get("query_basis_date"), coverage.get("window_end")) for coverage in coverages}) == 1
        exhausted = all(coverage.get("source_exhausted") is True for coverage in coverages)
        states = []
        for n in PERIODS:
            parts = [coverage.get("periods", {}).get(str(n), {}).get("status", "unverified") for coverage in coverages]
            # A verified exhausted short market has no older bars to contribute.
            verified = same_window and all(part in {"ready", "provisional", "insufficient_history"} for part in parts)
            status = ("provisional" if "provisional" in parts else "ready") if verified else "unverified"
            if verified and len(bars) < n and not exhausted:
                status = "insufficient_history"
            states.append((str(n), status))
        complete = same_window and all(coverage.get("collection_verified") is True for coverage in coverages)
        scope = "final" if complete and all(coverage.get("scope") == "final" for coverage in coverages) else "initial"
        cached = self._cached_high_250_loader(code) if code and self._cached_high_250_loader else None
        return DailyHighTargets.from_daily_bars(bars, as_of=daily_query_date(), period_statuses=tuple(states), collection_verified=complete,
                   scope=scope, query_basis_date=coverages[0].get("query_basis_date", ""),
                   window_end=min((coverage.get("window_end", "") for coverage in coverages), default=""),
                   cached_high_250_price=cached, source_exhausted=exhausted)

    def _load_bars_with_coverage(self, code: str) -> tuple[tuple[DailyBar, ...], dict[str, Any]]:
        market = "NXT" if code.endswith("_NX") else "KRX"
        plain_code = code.removesuffix("_NX")
        loader = getattr(self._client, "load_stored_daily_bars_with_coverage", None)
        if callable(loader):
            rows, coverage = loader(plain_code, market, 250)
            if coverage.get("query_basis_date") != daily_query_date().isoformat():
                coverage = {}
            # Preserve the old bars API while excluding rows outside its verified source window.
            if coverage:
                rows = tuple(row for row in rows if str(row.get("trading_date", "")) <= coverage.get("window_end", ""))
                rows = rows[:coverage.get("expected_count", 0)]
            return self._stored_bars(rows), coverage
        stored_loader = getattr(self._client, "load_stored_daily_bars", None)
        if callable(stored_loader):
            # Older NAS responses cannot certify coverage and must never trigger PC TR fallback.
            return self._stored_bars(stored_loader(plain_code, market, 250) or ()), {}
        basis = daily_query_date().isoformat()
        window = DailySourceWindow(basis, basis)
        continuation = getattr(self._client, "request_with_continuation", None)
        cont_yn, next_key = "N", ""
        while not window.complete:
            body = {"stk_cd": code, "base_dt": basis.replace("-", ""), "upd_stkpc_tp": "1"}
            if callable(continuation):
                payload, has_next, key = continuation("ka10081", "/api/dostk/chart", body,
                                                      cont_yn=cont_yn, next_key=next_key)
            else:
                payload, has_next, key = self._client.request("ka10081", "/api/dostk/chart", body), False, ""
            window.add_page(payload, has_next, key)
            cont_yn, next_key = "Y", key
        evidence = window.evidence(code=plain_code, market=market, scope="initial", checked_at=basis)
        coverage = assess_daily_coverage(window.rows, evidence, code=plain_code, market=market, query_basis_date=basis)
        if not callable(continuation) and len(window.rows) < 250:
            # request() hides source termination headers; short-history completion is unknown.
            coverage["collection_verified"] = False
            coverage["source_exhausted"] = False
            for period, part in coverage["periods"].items():
                if len(window.rows) < int(period):
                    part["status"] = "unverified"
        return self._stored_bars(window.rows), coverage

    @staticmethod
    def _stored_bars(rows: Any) -> tuple[DailyBar, ...]:
        return tuple(DailyBar(str(row.get("trading_date", "")).replace("-", ""), int(row["high"]),
                     float(row["trade_value_million_won"]) / 100 if row.get("trade_value_million_won") is not None else None,
                     int(row["close"]), int(row["open"]), int(row["low"]), int(row["volume"])) for row in rows)



def _combine_krx_nxt_bars(krx_bars: tuple[DailyBar, ...], nxt_bars: tuple[DailyBar, ...]) -> tuple[DailyBar, ...]:
    """날짜별 KRX·NXT 일봉을 합친다.

    최고가는 두 시장 중 높은 값, 거래대금은 합계로 쓴다. 종가는 KRX 종가를
    우선해 일일 강도 계산의 기준 종가가 기존 KRX 종가와 달라지지 않게 한다.
    """
    by_date: dict[str, DailyBar] = {bar.trade_date: bar for bar in krx_bars}
    for nxt in nxt_bars:
        krx = by_date.get(nxt.trade_date)
        if krx is None:
            by_date[nxt.trade_date] = nxt
            continue
        trade_values = (value for value in (krx.trade_value_eok, nxt.trade_value_eok) if value is not None)
        by_date[nxt.trade_date] = DailyBar(
            nxt.trade_date,
            max(krx.high_price, nxt.high_price),
            sum(trade_values) if krx.trade_value_eok is not None or nxt.trade_value_eok is not None else None,
            krx.close_price if krx.close_price is not None else nxt.close_price,
            krx.open_price if krx.open_price is not None else nxt.open_price,
            min(value for value in (krx.low_price, nxt.low_price) if value is not None)
            if krx.low_price is not None or nxt.low_price is not None else None,
            (krx.volume or 0) + (nxt.volume or 0),
        )
    return tuple(sorted(by_date.values(), key=lambda bar: bar.trade_date, reverse=True))

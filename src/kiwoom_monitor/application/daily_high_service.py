"""ka10081 일봉으로 신고가 기준과 전일 거래대금을 조회한다."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Callable, Protocol


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DailyBar:
    trade_date: str
    high_price: int
    trade_value_eok: float | None
    close_price: int | None = None


@dataclass(frozen=True)
class DailyHighTargets:
    high_5_price: int | None
    high_20_price: int | None
    high_250_price: int | None
    previous_day_trade_value_eok: float | None = None
    previous_day_close_price: int | None = None
    daily_trade_values_eok: tuple[tuple[str, float], ...] = ()
    daily_bars: tuple[DailyBar, ...] = ()

    @classmethod
    def from_daily_bars(
        cls,
        bars: tuple[DailyBar, ...],
        *,
        as_of: date | None = None,
        include_high_250: bool = True,
    ) -> "DailyHighTargets":
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date, reverse=True))
        prices = [bar.high_price for bar in ordered]
        # 장중에는 오늘 일봉 다음의 전일 봉을, 장전·주말·장 종료 뒤에는
        # 가장 최신 완료 일봉을 직전 1일로 사용한다.
        today = (as_of or date.today()).strftime("%Y%m%d")
        previous_index = 1 if ordered and ordered[0].trade_date == today else 0
        previous_bar = ordered[previous_index] if len(ordered) > previous_index else None
        previous_value = previous_bar.trade_value_eok if previous_bar is not None else None
        previous_close = previous_bar.close_price if previous_bar is not None else None
        # 개발 확인 CSV와 DB 보관은 최근 30일 범위만 사용한다.
        values = tuple((bar.trade_date, bar.trade_value_eok) for bar in ordered[:30] if bar.trade_value_eok is not None)
        # ka10001의 250일 최고가는 권리 조정 전 가격일 수 있다. 현재가·차트와
        # 같은 수정주가 기준은 ka10081 일봉의 최근 250개 고가로 계산한다.
        high_250 = _highest(prices[:250]) if include_high_250 else None
        return cls(_highest(prices[:5]), _highest(prices[:20]), high_250, previous_value, previous_close, values, ordered[:30])


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
        krx_bars = self._load_bars(code)
        targets = DailyHighTargets.from_daily_bars(krx_bars, as_of=date.today())
        if not self._include_nxt:
            return targets

        try:
            nxt_bars = self._load_bars(f"{code}_NX")
        except Exception:
            # NXT 일시 오류 때 KRX 단독값으로 낮추면 화면이 조회할 때마다
            # 흔들린다. 마지막 정상 KRX+NXT 250일 값을 유지하고, NXT가 다시
            # 성공했을 때만 오늘 기준 합산값으로 교체한다.
            cached = self._cached_high_250_loader(code) if self._cached_high_250_loader is not None else None
            if cached is not None and cached > (targets.high_250_price or 0):
                return replace(targets, high_250_price=cached)
            return targets
        return DailyHighTargets.from_daily_bars(_combine_krx_nxt_bars(krx_bars, nxt_bars), as_of=date.today())

    def _load_bars(self, code: str) -> tuple[DailyBar, ...]:
        response = self._client.request(
            "ka10081",
            "/api/dostk/chart",
            {"stk_cd": code, "base_dt": date.today().strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
        )
        # REST 일봉 차트의 목록 키는 stk_dt_pole_chart_qry다. 이전 응답 구조도
        # 읽어 두어 서버 전환·모의 환경의 명칭 차이로 신고가 계산이 멈추지 않게 한다.
        records = response.get("stk_dt_pole_chart_qry", response.get("stk_ddwkmm", []))
        if not isinstance(records, list):
            raise ValueError("ka10081 일봉 목록 형식이 올바르지 않습니다.")
        bars = tuple(
            DailyBar(day, high, _daily_trade_value(row), _price(row.get("cur_prc")))
            for row in records
            if isinstance(row, dict)
            if len(day := _record_date(row)) == 8 and day.isdigit()
            if (high := _price(row.get("high_pric"))) is not None
        )
        return bars


def _price(value: object) -> int | None:
    try:
        price = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _highest(prices: list[int]) -> int | None:
    return max(prices) if prices else None


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
        )
    return tuple(sorted(by_date.values(), key=lambda bar: bar.trade_date, reverse=True))


def _daily_trade_value(row: dict[str, Any]) -> float | None:
    # ka10081의 일별 거래대금(trde_prica)은 0B FID 14와 마찬가지로
    # 백만원 단위다. 억원 표시는 100으로 나눈다.
    direct_value = _positive_number(row.get("trde_prica"))
    return direct_value / 100 if direct_value is not None else None


def _record_date(row: dict[str, Any]) -> str:
    return str(row.get("date", row.get("dt", ""))).strip()


def _positive_number(value: object) -> float | None:
    try:
        number = abs(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

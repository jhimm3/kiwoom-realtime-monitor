"""ka10081 일봉으로 신고가 기준과 전일 거래대금을 조회한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DailyBar:
    trade_date: str
    high_price: int
    trade_value_eok: float | None


@dataclass(frozen=True)
class DailyHighTargets:
    high_5_price: int | None
    high_20_price: int | None
    previous_day_trade_value_eok: float | None = None
    daily_trade_values_eok: tuple[tuple[str, float], ...] = ()
    daily_bars: tuple[DailyBar, ...] = ()

    @classmethod
    def from_daily_bars(cls, bars: tuple[DailyBar, ...]) -> "DailyHighTargets":
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date, reverse=True))
        prices = [bar.high_price for bar in ordered]
        previous_value = ordered[1].trade_value_eok if len(ordered) > 1 else None
        values = tuple((bar.trade_date, bar.trade_value_eok) for bar in ordered if bar.trade_value_eok is not None)
        return cls(_highest(prices[:5]), _highest(prices[:20]), previous_value, values, ordered)


class DailyHighService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load(self, code: str) -> DailyHighTargets:
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
            DailyBar(day, high, _daily_trade_value(row))
            for row in records
            if isinstance(row, dict)
            if len(day := _record_date(row)) == 8 and day.isdigit()
            if (high := _price(row.get("high_pric"))) is not None
        )
        return DailyHighTargets.from_daily_bars(bars[:30])


def _price(value: object) -> int | None:
    try:
        price = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _highest(prices: list[int]) -> int | None:
    return max(prices) if prices else None


def _daily_trade_value(row: dict[str, Any]) -> float | None:
    # ka10081은 일별 거래대금(trde_prica)을 직접 전달한다. 분봉 합계와의
    # 개발자 확인 CSV도 이 값을 기준으로 비교해야 가격 평균 수식의 오차가 없다.
    direct_value = _positive_number(row.get("trde_prica"))
    return direct_value / 100_000_000 if direct_value is not None else None


def _record_date(row: dict[str, Any]) -> str:
    return str(row.get("date", row.get("dt", ""))).strip()


def _positive_number(value: object) -> float | None:
    try:
        number = abs(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

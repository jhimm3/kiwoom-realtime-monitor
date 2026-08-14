"""ka10080 REST 1분봉을 거래대금 계산용 데이터로 변환한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .minute_trade_value import MinuteOhlcv


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class MinuteChartService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load_today(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        response = self._client.request(
            "ka10080",
            "/api/dostk/chart",
            {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": today.strftime("%Y%m%d")},
        )
        records = response.get("stk_min_pole_chart_qry", [])
        if not isinstance(records, list):
            raise ValueError("ka10080의 분봉 목록 형식이 올바르지 않습니다.")
        bars = (bar for record in records if isinstance(record, dict) if (bar := self._to_bar(record, today)) is not None)
        return tuple(sorted(bars, key=lambda bar: bar.minute))

    @staticmethod
    def _to_bar(record: dict[str, Any], today: datetime) -> MinuteOhlcv | None:
        time = _parse_time(record.get("cntr_tm"), today)
        open_price = _positive_int(record.get("open_pric"))
        high_price = _positive_int(record.get("high_pric"))
        low_price = _positive_int(record.get("low_pric"))
        close_price = _positive_int(record.get("cur_prc"))
        volume = _positive_int(record.get("trde_qty"))
        if time is None or None in (open_price, high_price, low_price, close_price, volume):
            return None
        return MinuteOhlcv(time, open_price, high_price, low_price, close_price, volume)


def _parse_time(value: object, today: datetime) -> datetime | None:
    text = str(value).strip()
    for pattern in ("%Y%m%d%H%M%S", "%H%M%S"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed if pattern.startswith("%Y") else today.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
        except ValueError:
            continue
    return None


def _positive_int(value: object) -> int | None:
    try:
        return abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None

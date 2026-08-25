"""ka10080 REST 1분봉을 거래대금 계산용 데이터로 변환한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .minute_trade_value import MinuteOhlcv


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class MinuteChartService:
    _MAX_INITIAL_BARS = 730

    def __init__(self, client: RestClient, *, include_nxt: bool = False) -> None:
        self._client = client
        self._include_nxt = include_nxt

    def load_today(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        krx_bars = self._load_market(code, today)
        if not self._include_nxt:
            return krx_bars
        try:
            nxt_bars = self._load_market(f"{code}_NX", today)
        except Exception:
            # NXT 미지원 종목 또는 NXT 일시 조회 오류는 KRX 분봉으로 계속한다.
            return krx_bars
        return _combine_krx_nxt_bars(krx_bars, nxt_bars)

    def _load_market(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        body = {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": today.strftime("%Y%m%d")}
        response, has_next, next_key = self._request_page(body)
        records = self._records(response)
        # ka10080은 한 페이지에 약 390개만 돌려줄 수 있으므로, 시작 보완 시
        # 연속조회 한 페이지를 더 받아 최대 730개의 최근 1분봉을 채운다.
        if has_next and next_key:
            next_response, _, _ = self._request_page(body, cont_yn="Y", next_key=next_key)
            records.extend(self._records(next_response))
        by_minute = {
            bar.minute: bar
            for record in records
            if isinstance(record, dict)
            if (bar := self._to_bar(record, today)) is not None
        }
        return tuple(sorted(by_minute.values(), key=lambda bar: bar.minute)[-self._MAX_INITIAL_BARS :])

    def _request_page(self, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, Any], bool, str]:
        continuation = getattr(self._client, "request_with_continuation", None)
        if callable(continuation):
            response, has_next, response_next_key = continuation(
                "ka10080", "/api/dostk/chart", body, cont_yn=cont_yn, next_key=next_key
            )
            return response, bool(has_next), str(response_next_key or "")
        return self._client.request("ka10080", "/api/dostk/chart", body), False, ""

    @staticmethod
    def _records(response: dict[str, Any]) -> list[dict[str, Any]]:
        records = response.get("stk_min_pole_chart_qry", [])
        if not isinstance(records, list):
            raise ValueError("ka10080의 분봉 목록 형식이 올바르지 않습니다.")
        return records

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


def _combine_krx_nxt_bars(
    krx_bars: tuple[MinuteOhlcv, ...], nxt_bars: tuple[MinuteOhlcv, ...]
) -> tuple[MinuteOhlcv, ...]:
    """동일 분의 KRX·NXT 거래대금을 합산한다."""
    by_minute: dict[datetime, MinuteOhlcv] = {bar.minute: bar for bar in krx_bars}
    for nxt in nxt_bars:
        krx = by_minute.get(nxt.minute)
        if krx is None:
            by_minute[nxt.minute] = nxt
            continue
        by_minute[nxt.minute] = MinuteOhlcv(
            minute=krx.minute,
            open_price=krx.open_price,
            high_price=max(krx.high_price, nxt.high_price),
            low_price=min(krx.low_price, nxt.low_price),
            close_price=krx.close_price,
            volume=krx.volume + nxt.volume,
            trade_value_eok_override=krx.trade_value_eok + nxt.trade_value_eok,
        )
    return tuple(sorted(by_minute.values(), key=lambda bar: bar.minute)[-MinuteChartService._MAX_INITIAL_BARS :])

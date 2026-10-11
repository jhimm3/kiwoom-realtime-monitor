"""ka10080 REST 1분봉을 거래대금 계산용 데이터로 변환한다."""

from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from typing import Any, Protocol

from .minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataUnit, DataValueKind, MarketDataMetadata, ObservationOrigin, TradingVenue,
)
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_from_document
from kiwoom_monitor.domain.realtime_gap import market_clock


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class MinuteChartService:
    _MAX_INITIAL_BARS = 730
    _MAX_TWO_DAY_BARS = 1_500

    def __init__(self, client: RestClient, *, include_nxt: bool = False) -> None:
        self._client = client
        self._include_nxt = include_nxt

    @property
    def empty_coverage_authoritative(self) -> bool:
        # NAS day coverage is read separately from its numeric snapshot.
        return not any(callable(getattr(self._client, name, None)) for name in (
            "load_stored_minute_bars", "load_stored_minute_bars_with_coverage",
        ))

    def load_today(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        stored = self._load_stored_today(code, today)
        if stored is not None:
            return stored
        krx_bars, complete = self._load_market_with_completion(code, today)
        if not self._include_nxt:
            return krx_bars
        try:
            nxt_bars, nxt_complete = self._load_market_with_completion(f"{code}_NX", today)
        except Exception:
            # NXT 미지원 종목 또는 NXT 일시 조회 오류는 KRX 분봉으로 계속한다.
            return _combine_krx_nxt_bars(krx_bars, ())
        return _combine_krx_nxt_bars(krx_bars, nxt_bars, complete_scope=complete and nxt_complete)

    def load_today_with_completion(
        self, code: str, today: datetime,
    ) -> tuple[tuple[MinuteOhlcv, ...], bool]:
        """분봉과 중앙 장후 전체 조회 완료 근거를 함께 반환한다."""
        loader = getattr(self._client, "load_stored_minute_bars_with_coverage", None)
        if callable(loader):
            market = "COMBINED" if self._include_nxt else "KRX"
            result = loader(code, today.date().isoformat(), market)
            if result is not None:
                rows, complete = result
                return self._stored_rows_to_bars(rows, today), bool(complete)
        if not self.empty_coverage_authoritative:
            return self.load_today(code, today), False
        bars, complete = self._load_market_with_completion(code, today)
        if not self._include_nxt:
            return bars, complete
        try:
            nxt, nxt_complete = self._load_market_with_completion(f"{code}_NX", today)
        except Exception:
            return tuple(replace(bar, metadata=replace(bar.metadata, completeness=DataCompleteness.PARTIAL))
                         for bar in bars), False
        return _combine_krx_nxt_bars(bars, nxt, complete_scope=complete and nxt_complete), complete and nxt_complete

    def load_recent(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        """연속조회 없이 첫 페이지만 받아 장중 완료 봉을 가볍게 확인한다."""
        stored = self._load_stored_today(code, today)
        if stored is not None:
            return stored
        krx_bars = self._load_market_page(code, today)
        if not self._include_nxt:
            return krx_bars
        try:
            nxt_bars = self._load_market_page(f"{code}_NX", today)
        except Exception:
            return _combine_krx_nxt_bars(krx_bars, ())
        return _combine_krx_nxt_bars(krx_bars, nxt_bars)

    def load_two_trading_days(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        """선택 거래일과 그 직전 실제 거래일의 분봉을 충분히 가져온다."""
        stored = self._load_stored_two_trading_days(code, today)
        if stored is not None:
            return stored
        krx_bars, complete = self._load_market_with_completion(code, today, self._MAX_TWO_DAY_BARS)
        if not self._include_nxt:
            return _last_two_trading_days(krx_bars, today)
        try:
            nxt_bars, nxt_complete = self._load_market_with_completion(f"{code}_NX", today, self._MAX_TWO_DAY_BARS)
        except Exception:
            return _last_two_trading_days(_combine_krx_nxt_bars(krx_bars, ()), today)
        return _last_two_trading_days(_combine_krx_nxt_bars(krx_bars, nxt_bars, self._MAX_TWO_DAY_BARS,
                                      complete_scope=complete and nxt_complete), today)

    def _load_stored_two_trading_days(
        self, code: str, today: datetime,
    ) -> tuple[MinuteOhlcv, ...] | None:
        loader = getattr(self._client, "load_stored_recent_minute_bars", None)
        if not callable(loader):
            return None
        trading_date = today.date().isoformat()
        if self._include_nxt:
            try:
                combined_rows = loader(code, trading_date, "COMBINED", 2)
            except Exception:
                # 구 NAS는 COMBINED 조회를 모르므로 기존 두 시장 조회로 호환한다.
                combined_rows = None
            if combined_rows is not None:
                return _last_two_trading_days(
                    self._stored_rows_to_bars(combined_rows, today), today,
                )
        krx_rows = loader(code, trading_date, "KRX", 2)
        if krx_rows is None:
            return None
        krx_bars = self._stored_rows_to_bars(krx_rows, today)
        if not self._include_nxt:
            return _last_two_trading_days(krx_bars, today)
        nxt_rows = loader(code, trading_date, "NXT", 2)
        if nxt_rows is None:
            return None
        nxt_bars = self._stored_rows_to_bars(nxt_rows, today)
        return _last_two_trading_days(
            _combine_krx_nxt_bars(krx_bars, nxt_bars, self._MAX_TWO_DAY_BARS), today,
        )

    def _load_stored_today(
        self, code: str, today: datetime,
    ) -> tuple[MinuteOhlcv, ...] | None:
        loader = getattr(self._client, "load_stored_minute_bars", None)
        if not callable(loader):
            return None
        trading_date = today.date().isoformat()
        if self._include_nxt:
            try:
                combined_rows = loader(code, trading_date, "COMBINED")
            except Exception:
                combined_rows = None
            if combined_rows is not None:
                return self._stored_rows_to_bars(combined_rows, today)
        krx_rows = loader(code, trading_date, "KRX")
        if krx_rows is None:
            return None
        krx_bars = self._stored_rows_to_bars(krx_rows, today)
        if not self._include_nxt:
            return krx_bars
        nxt_rows = loader(code, trading_date, "NXT")
        if nxt_rows is None:
            return None
        nxt_bars = self._stored_rows_to_bars(nxt_rows, today)
        combined = _combine_krx_nxt_bars(krx_bars, nxt_bars)
        return combined

    @staticmethod
    def _stored_rows_to_bars(
        rows: object, today: datetime,
    ) -> tuple[MinuteOhlcv, ...]:
        if not isinstance(rows, (tuple, list)):
            raise ValueError("중앙 DB 분봉 목록 형식이 올바르지 않습니다.")
        values: dict[datetime, MinuteOhlcv] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("중앙 DB 분봉 행 형식이 올바르지 않습니다.")
            try:
                minute = datetime.fromisoformat(
                    f"{row.get('trading_date') or today.date().isoformat()}T{row['minute']}"
                ).replace(tzinfo=today.tzinfo)
                metadata = market_metadata_from_document(row.get("metadata"))
                if metadata.is_complete and (metadata.effective_at is None or metadata.available_at is None
                        or market_clock(metadata.effective_at) != market_clock(minute)):
                    metadata = replace(metadata, completeness=DataCompleteness.UNCONFIRMED)
                metadata = replace(metadata, source="NAS:" + metadata.source, unit=DataUnit.EOK_WON)
                bar = MinuteOhlcv(
                    minute=minute,
                    open_price=abs(int(row["open"])),
                    high_price=abs(int(row["high"])),
                    low_price=abs(int(row["low"])),
                    close_price=abs(int(row["close"])),
                    volume=abs(int(row["volume"])),
                    trade_value_eok_override=abs(float(row["trade_value_million_won"])) / 100,
                    metadata=metadata,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("중앙 DB 분봉 행 형식이 올바르지 않습니다.") from error
            values[minute] = bar
        return tuple(values[key] for key in sorted(values))

    def _load_market_page(self, code: str, today: datetime) -> tuple[MinuteOhlcv, ...]:
        body = {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": today.strftime("%Y%m%d")}
        response, _, _ = self._request_page(body)
        bars = tuple(
            bar for record in self._records(response) if isinstance(record, dict)
            if (bar := self._to_bar(record, today, venue=TradingVenue.NXT if code.endswith("_NX") else TradingVenue.KRX)) is not None
        )
        return tuple(sorted({bar.minute: bar for bar in bars}.values(), key=lambda bar: bar.minute))

    def _load_market(self, code: str, today: datetime, maximum: int | None = None) -> tuple[MinuteOhlcv, ...]:
        return self._load_market_with_completion(code, today, maximum)[0]

    def _load_market_with_completion(self, code: str, today: datetime, maximum: int | None = None) -> tuple[tuple[MinuteOhlcv, ...], bool]:
        maximum = maximum or self._MAX_INITIAL_BARS
        body = {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": today.strftime("%Y%m%d")}
        response, has_next, next_key = self._request_page(body)
        valid_pages = isinstance(response.get("stk_min_pole_chart_qry"), list)
        records = self._records(response)
        # ka10080은 한 페이지에 약 390개이므로 필요한 봉 수까지만 연속조회한다.
        while has_next and next_key and len(records) < maximum:
            next_response, has_next, next_key = self._request_page(body, cont_yn="Y", next_key=next_key)
            valid_pages = valid_pages and isinstance(next_response.get("stk_min_pole_chart_qry"), list)
            records.extend(self._records(next_response))
        by_minute = {
            bar.minute: bar
            for record in records
            if isinstance(record, dict)
            if (bar := self._to_bar(record, today, venue=TradingVenue.NXT if code.endswith("_NX") else TradingVenue.KRX)) is not None
        }
        complete = bool(
            valid_pages and not has_next
            and len(by_minute) == len(records)
            and sum(minute.date() == today.date() for minute in by_minute) <= maximum
        )
        return tuple(sorted(by_minute.values(), key=lambda bar: bar.minute)[-maximum:]), complete

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
    def _to_bar(record: dict[str, Any], today: datetime, *, venue: TradingVenue = TradingVenue.KRX) -> MinuteOhlcv | None:
        time = _parse_time(record.get("cntr_tm"), today)
        open_price = _positive_int(record.get("open_pric"))
        high_price = _positive_int(record.get("high_pric"))
        low_price = _positive_int(record.get("low_pric"))
        close_price = _positive_int(record.get("cur_prc"))
        volume = _positive_int(record.get("trde_qty"))
        if time is None or None in (open_price, high_price, low_price, close_price, volume):
            return None
        return MinuteOhlcv(time, open_price, high_price, low_price, close_price, volume, metadata=MarketDataMetadata(
            effective_at=time, available_at=today, venue=venue, unit=DataUnit.EOK_WON,
            value_kind=DataValueKind.ESTIMATED, completeness=(DataCompleteness.COMPLETE
                if time < today.replace(second=0, microsecond=0) else DataCompleteness.IN_PROGRESS),
            origin=ObservationOrigin.QUERY, source="kiwoom-ka10080",
        ))


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
    krx_bars: tuple[MinuteOhlcv, ...], nxt_bars: tuple[MinuteOhlcv, ...], maximum: int | None = None,
    *, complete_scope: bool = False,
) -> tuple[MinuteOhlcv, ...]:
    """동일 분의 KRX·NXT 거래대금을 합산한다."""
    by_minute: dict[datetime, MinuteOhlcv] = {
        bar.minute: bar if complete_scope else replace(bar, metadata=replace(
            bar.metadata or MarketDataMetadata(None, None), completeness=DataCompleteness.PARTIAL))
        for bar in krx_bars
    }
    original_krx = {bar.minute: bar for bar in krx_bars}
    for nxt in nxt_bars:
        krx = original_krx.get(nxt.minute)
        if krx is None:
            by_minute[nxt.minute] = nxt if complete_scope else replace(nxt, metadata=replace(
                nxt.metadata or MarketDataMetadata(None, None), completeness=DataCompleteness.PARTIAL))
            continue
        by_minute[nxt.minute] = MinuteOhlcv(
            minute=krx.minute,
            open_price=krx.open_price,
            high_price=max(krx.high_price, nxt.high_price),
            low_price=min(krx.low_price, nxt.low_price),
            close_price=krx.close_price,
            volume=krx.volume + nxt.volume,
            trade_value_eok_override=krx.trade_value_eok + nxt.trade_value_eok,
            metadata=replace(krx.metadata or MarketDataMetadata(None, None), completeness=(
                DataCompleteness.COMPLETE if krx.metadata and nxt.metadata and krx.metadata.is_complete
                and nxt.metadata.is_complete else DataCompleteness.PARTIAL), value_kind=(
                krx.metadata.value_kind if krx.metadata and nxt.metadata and krx.metadata.value_kind == nxt.metadata.value_kind
                else DataValueKind.ESTIMATED if any(item and item.value_kind == DataValueKind.ESTIMATED
                                                   for item in (krx.metadata, nxt.metadata)) else DataValueKind.UNKNOWN)),
        )
    limit = maximum or MinuteChartService._MAX_INITIAL_BARS
    return tuple(replace(bar, metadata=replace(bar.metadata or MarketDataMetadata(None, None), venue=TradingVenue.COMBINED))
                 for bar in sorted(by_minute.values(), key=lambda bar: bar.minute)[-limit:])


def _last_two_trading_days(bars: tuple[MinuteOhlcv, ...], target: datetime) -> tuple[MinuteOhlcv, ...]:
    available = sorted({bar.minute.date() for bar in bars if bar.minute.date() <= target.date()})
    days = set(available[-2:])
    return tuple(bar for bar in bars if bar.minute.date() in days)

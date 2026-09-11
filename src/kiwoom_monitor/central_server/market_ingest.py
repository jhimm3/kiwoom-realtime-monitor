from __future__ import annotations

from datetime import datetime, time as clock_time, timedelta
from typing import Any, Callable

from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    MarketDatasetKind,
    ObservationOrigin,
)

from .database import QueryStore
from .market_observations import (
    KST,
    bar_observation_key,
    daily_bar_observation,
    minute_bar_observation,
    ranking_observation,
)


class MarketDataIngestor:
    """키움 차트 조회 응답을 중앙 표준 분봉·일봉으로 저장한다."""

    def __init__(
        self,
        store: QueryStore,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now_provider or (lambda: datetime.now(KST))

    def ingest(self, api_id: str, body: dict[str, Any], payload: dict[str, Any]) -> None:
        if api_id == "ka10080":
            self._ingest_minutes(body, payload)
        elif api_id == "ka10081":
            self._ingest_daily(body, payload)
        elif api_id == "ka00198":
            self._ingest_ranking(body, payload)
        elif api_id == "ka10045":
            self._ingest_investor_flow(body, payload)
        elif api_id == "ka90008":
            self._ingest_program_flow(body, payload)
        elif api_id == "ka10016":
            self._ingest_new_highs(body, payload)
        elif api_id == "ka10001":
            self._ingest_fundamentals(body, payload)
        elif api_id == "ka10100":
            self._ingest_nxt_eligibility(body, payload)

    def _ingest_minutes(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        raw_code = str(body.get("stk_cd", "")).strip()
        code, market = _code_and_market(raw_code)
        records = payload.get("stk_min_pole_chart_qry", [])
        if not code or not isinstance(records, list):
            return
        base_date = _base_date(body.get("base_dt"))
        now = self._now()
        values: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            moment = _minute(record.get("cntr_tm"), base_date)
            open_price = _integer(record.get("open_pric"), positive=True)
            high = _integer(record.get("high_pric"), positive=True)
            low = _integer(record.get("low_pric"), positive=True)
            close = _integer(record.get("cur_prc"), positive=True)
            volume = _integer(record.get("trde_qty"), positive=True)
            if moment is None or None in (open_price, high, low, close, volume):
                continue
            values.append({
                "trading_date": moment.date().isoformat(), "minute": moment.strftime("%H:%M"),
                "code": code, "market": market, "open": open_price, "high": high,
                "low": low, "close": close, "volume": volume,
                "trade_value_million_won": round(
                    volume * (open_price + high + low + close) / 4 / 1_000_000
                ),
                "updated_at": now.timestamp(),
            })
        observations = []
        for value in values:
            effective = datetime.fromisoformat(f"{value['trading_date']}T{value['minute']}")
            completeness = (
                DataCompleteness.COMPLETE
                if effective < now.replace(tzinfo=None, second=0, microsecond=0)
                else DataCompleteness.IN_PROGRESS
            )
            observation = minute_bar_observation(
                value,
                origin=ObservationOrigin.QUERY,
                completeness=completeness,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            observations.append((bar_observation_key(observation), observation))
        if values:
            first_day = min(str(value["trading_date"]) for value in values)
            last_day = max(str(value["trading_date"]) for value in values)
            start = datetime.fromisoformat(first_day).replace(tzinfo=KST)
            end = datetime.fromisoformat(last_day).replace(tzinfo=KST) + timedelta(days=1)
            actual_keys = {
                item.observation_key
                for item in self._store.load_market_data_metadata_range(
                    MarketDatasetKind.MINUTE_BAR, f"{code}:{market}", start, end,
                )
                if item.metadata.value_kind == DataValueKind.ACTUAL
            }
            pairs = tuple(
                (value, observation)
                for value, observation in zip(values, observations)
                if observation[0] not in actual_keys
            )
            values = [pair[0] for pair in pairs]
            observations = [pair[1] for pair in pairs]
        self._store.replace_minute_bars(values, observations=observations)

    def _ingest_daily(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        raw_code = str(body.get("stk_cd", "")).strip()
        code, market = _code_and_market(raw_code)
        records = payload.get("stk_dt_pole_chart_qry", payload.get("stk_ddwkmm", []))
        if not code or not isinstance(records, list):
            return
        now = self._now()
        values: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_date = str(record.get("date", record.get("dt", ""))).strip()
            if len(raw_date) != 8 or not raw_date.isdigit():
                continue
            prices = [_integer(record.get(key), positive=True) for key in ("open_pric", "high_pric", "low_pric", "cur_prc")]
            volume = _integer(record.get("trde_qty"), positive=True)
            if any(value is None for value in prices):
                continue
            trade_value = _integer(record.get("trde_prica"), positive=True)
            values.append({
                "trading_date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}",
                "code": code, "market": market, "open": prices[0], "high": prices[1],
                "low": prices[2], "close": prices[3], "volume": volume or 0,
                "trade_value_million_won": trade_value,
                "updated_at": now.timestamp(),
            })
        observations = []
        for value in values:
            completeness = _daily_completeness(
                str(value["trading_date"]), str(value["market"]), now
            )
            observation = daily_bar_observation(value, completeness=completeness)
            observations.append((bar_observation_key(observation), observation))
        self._store.replace_daily_bars(values, observations=observations)

    def _ingest_ranking(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        records = payload.get("item_inq_rank", payload.get("result_list", []))
        if not isinstance(records, list):
            return
        first = next((row for row in records if isinstance(row, dict)), {})
        raw_date = str(first.get("dt", payload.get("base_date", ""))).strip()
        raw_time = str(first.get("tm", payload.get("base_time", ""))).strip().zfill(6)
        snapshot_key = (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}T{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:]}"
            if len(raw_date) == 8 and len(raw_time) == 6 else datetime.now().isoformat(timespec="seconds")
        )
        subject = str(body.get("qry_tp", "5"))
        value = {"query_type": subject, "items": records}
        self._store.save_dataset_snapshot(
            "ranking", subject, snapshot_key, value,
            observation=ranking_observation(
                subject, snapshot_key, value, datetime.now(), source="kiwoom-ka00198"
            ),
        )

    def _ingest_investor_flow(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        raw_code = str(body.get("stk_cd", "")).strip()
        code, market = _code_and_market(raw_code)
        day = str(body.get("end_dt", body.get("strt_dt", ""))).strip()
        if code and len(day) == 8:
            self._store.save_dataset_snapshot(
                "investor_flow", code, f"{day}:{market}",
                {"market": market, "rows": payload.get("stk_orgn_trde_trnsn", [])},
            )

    def _ingest_program_flow(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        raw_code = str(body.get("stk_cd", "")).strip()
        code, market = _code_and_market(raw_code)
        day = str(body.get("date", "")).strip()
        if code and len(day) == 8:
            self._store.save_dataset_snapshot(
                "program_flow", code, f"{day}:{market}",
                {"market": market, "rows": payload.get("stk_tm_prm_trde_trnsn", [])},
            )

    def _ingest_new_highs(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        records = payload.get("ntl_pric", [])
        if not isinstance(records, list):
            return
        period = str(body.get("dt", "")).strip() or "unknown"
        observed_at = self._now().isoformat(timespec="seconds")
        self._store.save_dataset_snapshot(
            "new_high", period, observed_at,
            {"period": period, "observed_at": observed_at, "items": records},
        )

    def _ingest_fundamentals(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        code, market = _code_and_market(str(body.get("stk_cd", "")).strip())
        if not code:
            return
        now = self._now()
        document = {
            "code": code, "market": market, "observed_at": now.isoformat(timespec="seconds"),
            "payload": payload,
        }
        self._store.upsert_documents("stock_fundamentals", [{
            "owner": code, "key": "latest", "document": document,
        }])
        # 시가총액 등 변동 필드는 날짜별 마지막 관측을 남기고, 같은 날의
        # 반복 조회는 같은 키를 갱신해 불필요한 중복을 만들지 않는다.
        self._store.save_dataset_snapshot(
            "stock_fundamentals", code, f"{now.date().isoformat()}:{market}", document,
        )

    def _ingest_nxt_eligibility(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        code, _market = _code_and_market(str(body.get("stk_cd", "")).strip())
        if not code:
            return
        now = self._now()
        enabled = str(payload.get("nxtEnable", payload.get("nxt_enable", ""))).strip().upper() == "Y"
        document = {
            "code": code, "enabled": enabled,
            "observed_at": now.isoformat(timespec="seconds"), "payload": payload,
        }
        self._store.upsert_documents("stock_nxt_eligibility", [{
            "owner": code, "key": "latest", "document": document,
        }])
        self._store.save_dataset_snapshot(
            "nxt_eligibility", code, now.date().isoformat(), document,
        )


def _code_and_market(raw: str) -> tuple[str, str]:
    market = "NXT" if raw.endswith("_NX") else "SOR" if raw.endswith("_AL") else "KRX"
    return raw.removesuffix("_NX").removesuffix("_AL"), market


def _base_date(value: object) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y%m%d")
    except ValueError:
        return datetime.now()


def _minute(value: object, base_date: datetime) -> datetime | None:
    text = str(value).strip()
    for pattern in ("%Y%m%d%H%M%S", "%H%M%S"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed if pattern.startswith("%Y") else base_date.replace(hour=parsed.hour, minute=parsed.minute, second=0)
        except ValueError:
            continue
    return None


def _integer(value: object, *, positive: bool = False) -> int | None:
    try:
        number = int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return abs(number) if positive else number


def _daily_completeness(
    trading_date: str, market: str, now: datetime
) -> DataCompleteness:
    if trading_date < now.date().isoformat():
        return DataCompleteness.COMPLETE
    if trading_date > now.date().isoformat():
        return DataCompleteness.UNCONFIRMED
    close_time = clock_time(15, 30) if market == "KRX" else clock_time(20)
    return (
        DataCompleteness.COMPLETE
        if now.time().replace(tzinfo=None) >= close_time
        else DataCompleteness.IN_PROGRESS
    )

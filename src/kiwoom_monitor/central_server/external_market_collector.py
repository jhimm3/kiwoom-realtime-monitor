"""Yahoo 지연 시세를 임시 공급원으로 사용하는 외부 시장 수집기."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context

from .database import QueryStore
from .futures_roll import (
    change_percent,
    evaluate_roll,
    latest_price,
    next_futures_contract,
    previous_daily_close,
    trailing_volume,
)


logger = logging.getLogger(__name__)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class YahooDelayedMarketCollector:
    """외부 지연 시세 실패를 다른 중앙 서비스와 격리해 누적 저장한다."""

    def __init__(
        self, store: QueryStore, symbols: dict[str, str], *, poll_seconds: int = 300,
        auto_roll_enabled: bool = True, roll_confirmations: int = 2,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._store = store
        self._symbols = dict(symbols)
        self._poll_seconds = max(60, int(poll_seconds))
        self._auto_roll_enabled = bool(auto_roll_enabled)
        self._roll_confirmations = max(1, int(roll_confirmations))
        self._opener = opener
        self._task: asyncio.Task[None] | None = None
        self._last_daily_date = ""
        self._daily_reference_closes: dict[tuple[str, str], float] = {}

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="yahoo-delayed-market-collector")

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def collect_once(self, *, include_daily: bool = True) -> dict[str, int]:
        saved: dict[str, int] = {}
        for instrument, configured_contract in self._symbols.items():
            count = 0
            state = await asyncio.to_thread(self._load_roll_state, instrument, configured_contract)
            active_contract = str(state["active_contract"])
            next_contract = next_futures_contract(instrument, active_contract)
            contracts = [active_contract]
            if self._auto_roll_enabled:
                contracts.append(next_contract)
            intraday_by_contract: dict[str, list[dict[str, Any]]] = {}
            errors: list[str] = []
            for contract in contracts:
                try:
                    intraday = await asyncio.to_thread(self._fetch, instrument, contract, "5m", "5d")
                    intraday_by_contract[contract] = intraday
                    await asyncio.to_thread(self._store.save_external_bars, intraday)
                    count += len(intraday)
                    if include_daily:
                        daily = await asyncio.to_thread(self._fetch, instrument, contract, "1d", "2y")
                        await asyncio.to_thread(self._store.save_external_bars, daily)
                        count += len(daily)
                        reference = previous_daily_close(daily)
                        if reference is not None:
                            self._daily_reference_closes[(instrument, contract)] = reference
                except Exception as error:
                    errors.append(f"{contract}: {error}")
                    logger.warning("Yahoo 지연 시세 월물 수집 실패: %s(%s) %s", instrument, contract, error)
            try:
                active_rows = intraday_by_contract.get(active_contract, [])
                next_rows = intraday_by_contract.get(next_contract, [])
                common_end = max(
                    (str(row.get("bar_time", "")) for row in active_rows + next_rows), default="",
                )
                active_volume = trailing_volume(active_rows, common_end)
                next_volume = trailing_volume(next_rows, common_end)
                evaluation = evaluate_roll(
                    active_contract, next_contract, active_volume, next_volume,
                    int(state.get("confirmation_count", 0)), self._roll_confirmations,
                ) if self._auto_roll_enabled else evaluate_roll(
                    active_contract, next_contract, 0, 0, 0, self._roll_confirmations,
                )
                selected = evaluation.active_contract
                reference_close = self._daily_reference_closes.get((instrument, selected))
                current_price = latest_price(intraday_by_contract.get(selected, []))
                new_state = {
                    "provider": "yahoo_delayed", "instrument": instrument,
                    "active_contract": selected,
                    "next_contract": next_futures_contract(instrument, selected),
                    "confirmation_count": evaluation.confirmation_count,
                    "required_confirmations": self._roll_confirmations,
                    "active_volume": active_volume if selected == active_contract else next_volume,
                    "candidate_volume": next_volume if selected == active_contract else None,
                    "current_price": current_price, "previous_daily_close": reference_close,
                    "change_pct": change_percent(current_price, reference_close),
                    "change_basis": "previous_daily_close",
                    "rolled_at": datetime.now(timezone.utc).isoformat() if evaluation.rolled else state.get("rolled_at"),
                    "previous_contract": active_contract if evaluation.rolled else state.get("previous_contract"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                await asyncio.to_thread(self._save_roll_state, instrument, new_state)
                status = "ok" if selected in intraday_by_contract else "failed"
                await asyncio.to_thread(self._save_status, instrument, selected, status, "; ".join(errors), count)
            except Exception as error:
                logger.warning("Yahoo 지연 시세 수집 실패: %s(%s) %s", instrument, active_contract, error)
                await asyncio.to_thread(self._save_status, instrument, active_contract, "failed", str(error), count)
            saved[instrument] = count
        return saved

    async def _run(self) -> None:
        while True:
            today = datetime.now(timezone.utc).date().isoformat()
            include_daily = self._last_daily_date != today
            await self.collect_once(include_daily=include_daily)
            if include_daily:
                self._last_daily_date = today
            await asyncio.sleep(self._poll_seconds)

    def _fetch(self, instrument: str, contract: str, interval: str, range_value: str) -> list[dict[str, Any]]:
        url = f"{YAHOO_CHART_URL.format(symbol=quote(contract, safe=''))}?interval={interval}&range={range_value}&includePrePost=true"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 KiwoomMonitor/1.1"})
        with self._opener(request, timeout=20, context=system_ssl_context()) as response:
            document = json.loads(response.read().decode("utf-8"))
        return parse_yahoo_chart(document, instrument, contract, interval)

    def _save_status(self, instrument: str, contract: str, status: str, error: str, count: int) -> None:
        self._store.upsert_documents("external_market_collection_status", [{
            "owner": instrument, "key": "current", "document": {
                "provider": "yahoo_delayed", "instrument": instrument, "contract": contract,
                "status": status, "error": error[:1000], "saved_rows": count,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        }])

    def _load_roll_state(self, instrument: str, configured_contract: str) -> dict[str, Any]:
        values = self._store.load_documents("external_market_roll_state", instrument, 1)
        if values and isinstance(values[0].get("document"), dict):
            state = dict(values[0]["document"])
            if state.get("active_contract"):
                return state
        return {"active_contract": configured_contract, "confirmation_count": 0}

    def _save_roll_state(self, instrument: str, state: dict[str, Any]) -> None:
        self._store.upsert_documents("external_market_roll_state", [{
            "owner": instrument, "key": "current", "document": state,
        }])


def parse_yahoo_chart(
    document: dict[str, Any], instrument: str, contract: str, timeframe: str,
) -> list[dict[str, Any]]:
    chart = document.get("chart", {}) if isinstance(document, dict) else {}
    results = chart.get("result", []) if isinstance(chart, dict) else []
    if not isinstance(results, list) or not results:
        error = chart.get("error") if isinstance(chart, dict) else None
        raise ValueError(f"Yahoo chart result가 없습니다: {error or 'unknown'}")
    result = results[0] if isinstance(results[0], dict) else {}
    timestamps = result.get("timestamp", [])
    indicators = result.get("indicators", {})
    quotes = indicators.get("quote", []) if isinstance(indicators, dict) else []
    values = quotes[0] if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict) else {}
    if not isinstance(timestamps, list) or not values:
        raise ValueError("Yahoo chart 시계열 형식이 올바르지 않습니다.")
    updated_at = time.time()
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        try:
            moment = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            continue
        close = _series_value(values, "close", index)
        if close is None:
            continue
        rows.append({
            "provider": "yahoo_delayed", "instrument": instrument, "contract": contract,
            "timeframe": timeframe, "bar_time": moment,
            "open": _series_value(values, "open", index), "high": _series_value(values, "high", index),
            "low": _series_value(values, "low", index), "close": close,
            "volume": _series_value(values, "volume", index), "updated_at": updated_at,
        })
    return rows


def _series_value(values: dict[str, Any], key: str, index: int) -> float | None:
    series = values.get(key, [])
    if not isinstance(series, list) or index >= len(series) or series[index] is None:
        return None
    try:
        return float(series[index])
    except (TypeError, ValueError):
        return None

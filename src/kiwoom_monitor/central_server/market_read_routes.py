"""Stored/live market read policy; collection and native storage remain app-owned."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol, TYPE_CHECKING

from .market_observations import as_kst
from kiwoom_monitor.application.market_data_coverage import evaluate_coverage
from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind, CoverageObservation

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .realtime_collector import CentralRealtimeCollector
    from .market_events import MarketEventService


class MarketReadStore(Protocol):
    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]: ...

    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]: ...

    def load_documents(self, collection: str, owner: str = "", limit: int = 1000) -> list[dict[str, Any]]: ...

    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]: ...

    def load_market_data_metadata_range(self, kind: MarketDatasetKind, subject: str,
                                      start: datetime, end: datetime) -> list[CoverageObservation]: ...

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]: ...


class MarketEventReadStore(Protocol):
    def load_market_event_history(self, kind: str, *, code: str = "", limit: int = 100) -> list[dict[str, Any]]: ...

    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]: ...

    def load_documents(self, collection: str, owner: str = "", limit: int = 1000) -> list[dict[str, Any]]: ...


def create_market_live_read_router(
    store: MarketReadStore, authorize: Callable[..., None],
    get_collector: Callable[[], CentralRealtimeCollector | None],
) -> APIRouter:
    """Read the app's current collector on each request without owning its lifecycle."""
    from fastapi import APIRouter, Depends, HTTPException, Query

    router = APIRouter()

    async def load_display_minute_bars(code: str, day: str, market: str) -> list[dict[str, Any]]:
        current_collector = get_collector()
        if current_collector is not None:
            return await current_collector.load_live_minute_bars(code, day, market)
        return await asyncio.to_thread(store.load_minute_bars, code, day, market)

    @router.get("/api/v1/market/minute-bars", dependencies=[Depends(authorize)])
    async def minute_bars(
        code: str = Query(min_length=6, max_length=12),
        trading_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        market: str = Query(default="", pattern=r"^(|KRX|NXT|SOR|COMBINED)$"),
    ) -> dict[str, object]:
        requested_market = market.upper()
        values = await load_display_minute_bars(
            code, trading_date,
            "" if requested_market == "COMBINED" else requested_market,
        )
        if requested_market == "COMBINED":
            values = _combined_minute_bars(values)
        coverage_markets = (
            ("KRX",) if requested_market in {"", "COMBINED"} and trading_date >= "2026-09-14"
            else (requested_market,) if requested_market else ("KRX",)
        )
        coverage_documents = [
            await asyncio.to_thread(
                store.load_documents,
                "market_data_coverage",
                f"{trading_date}:{code}:{value}",
                1,
            )
            for value in coverage_markets
        ]
        coverage_complete = bool(coverage_documents) and all(
            _archive_coverage_ready(rows, trading_date, "minute")
            for rows in coverage_documents
        )
        return {
            "code": code, "trading_date": trading_date, "market": requested_market, "bars": values,
            "coverage": {"complete": coverage_complete, "markets": list(coverage_markets)},
        }

    @router.get("/api/v1/market/recent-minute-bars", dependencies=[Depends(authorize)])
    async def recent_minute_bars(
        code: str = Query(min_length=6, max_length=12),
        end_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        market: str = Query(default="", pattern=r"^(|KRX|NXT|SOR|COMBINED)$"),
        trading_days: int = Query(default=2, ge=1, le=5),
    ) -> dict[str, object]:
        """TR 없이 중앙 저장 이력과 미저장 RAM 집계로 최근 거래일 분봉을 반환한다."""
        end = datetime.fromisoformat(end_date).date()
        values: list[dict[str, Any]] = []
        found_days: set[str] = set()
        for offset in range(31):
            day = (end - timedelta(days=offset)).isoformat()
            requested_market = market.upper()
            rows = await load_display_minute_bars(
                code, day,
                "" if requested_market == "COMBINED" else requested_market,
            )
            if requested_market == "COMBINED":
                rows = _combined_minute_bars(rows)
            if rows:
                values.extend(rows)
                found_days.add(day)
                if len(found_days) >= trading_days:
                    break
        values.sort(key=lambda value: (
            str(value.get("trading_date", "")), str(value.get("minute", "")),
        ))
        return {
            "code": code, "end_date": end_date, "market": market.upper(),
            "trading_days": sorted(found_days), "bars": values,
        }

    @router.get("/api/v1/market/latest-market-caps", dependencies=[Depends(authorize)])
    async def latest_market_caps(
        codes: list[str] = Query(default=[]),
    ) -> dict[str, object]:
        """Return only durable 0B market-cap references, regardless of tick age."""
        normalized = list(dict.fromkeys(str(code).strip() for code in codes))
        if not normalized or len(normalized) > 200 or any(
            re.fullmatch(r"\d{6}", code) is None for code in normalized
        ):
            raise HTTPException(
                status_code=422,
                detail="종목코드는 1~200개의 6자리 값이어야 합니다.",
            )
        values = await asyncio.to_thread(store.load_latest_market_caps, normalized)
        return {"market_caps": values}

    @router.get("/api/v1/market/trade-value-comparisons", dependencies=[Depends(authorize)])
    async def trade_value_comparisons(
        code: str = Query(min_length=6, max_length=12),
        trading_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(default=1500, ge=1, le=5000),
    ) -> dict[str, object]:
        owner = f"{trading_date}:{code}"
        values = await asyncio.to_thread(
            store.load_documents, "minute_trade_value_comparisons", owner, limit,
        )
        return {
            "code": code,
            "trading_date": trading_date,
            "summary": _trade_value_comparison_summary(values),
            "comparisons": values,
        }

    return router


def create_market_event_read_router(
    store: MarketEventReadStore, authorize: Callable[..., None],
    market_event_service: MarketEventService | None,
) -> APIRouter:
    """Read history and the app-owned condition runtime without owning producers."""
    from fastapi import APIRouter, Depends, Query

    router = APIRouter()

    @router.get("/api/v1/market/events", dependencies=[Depends(authorize)])
    async def market_events(
        kind: str = Query(pattern=r"^(vi|cohort|upper_limit)$"),
        code: str = Query(default="", max_length=12),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        history = await asyncio.to_thread(
            store.load_market_event_history, kind, code=code, limit=limit,
        )
        result: dict[str, object] = {"kind": kind, "code": code, "history": history}
        if kind == "cohort":
            current = await asyncio.to_thread(store.load_hot_cohort, active_only=False)
            result["current"] = [value for value in current if not code or value.get("stock_code") == code]
            diagnostic = await asyncio.to_thread(
                store.load_documents, "condition_search_status", "hot_cohort", 1,
            )
            result["condition"] = diagnostic[0]["document"] if diagnostic else {"status": "NOT_OBSERVED"}
            if market_event_service is not None:
                result["condition"]["runtime"] = market_event_service.condition_status()
        return result

    return router


def create_market_archive_read_router(
    store: MarketReadStore, authorize: Callable[..., None],
) -> APIRouter:
    """Keep separate registration around the app-owned market-events route."""
    from fastapi import APIRouter, Depends, HTTPException, Query

    router = APIRouter()

    @router.get("/api/v1/market/daily-bars", dependencies=[Depends(authorize)])
    async def daily_bars(
        code: str = Query(min_length=6, max_length=12),
        market: str = Query(default="", max_length=8),
        limit: int = Query(default=250, ge=1, le=5000),
    ) -> dict[str, object]:
        from .postgres_access import db_call_source
        from kiwoom_monitor.application.daily_bar_coverage import COLLECTION, choose_daily_coverage
        from datetime import timedelta, timezone
        with db_call_source("api.market.daily_bars"):
            values = await asyncio.to_thread(store.load_daily_bars, code, market.upper(), limit)
            documents = await asyncio.to_thread(store.load_documents, COLLECTION, f"{code}:{market.upper()}", 2)
        coverage = choose_daily_coverage(values, documents, code=code, market=market.upper(),
                                        query_basis_date=datetime.now(timezone(timedelta(hours=9))).date().isoformat())
        return {"code": code, "market": market.upper(), "bars": values, "coverage": coverage}

    @router.get("/api/v1/market/coverage", dependencies=[Depends(authorize)])
    async def market_coverage(
        kind: str = Query(max_length=40),
        subject: str = Query(min_length=1, max_length=100),
        start: datetime = Query(),
        end: datetime = Query(),
        available_by: datetime | None = Query(default=None),
        expected_seconds: int | None = Query(default=None, ge=1, le=86_400),
    ) -> dict[str, object]:
        try:
            dataset_kind = MarketDatasetKind(kind)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="지원하지 않는 관측 자료 종류입니다.") from error
        if dataset_kind == MarketDatasetKind.UNKNOWN:
            raise HTTPException(status_code=400, detail="지원하지 않는 관측 자료 종류입니다.")
        cadence_kinds = {
            MarketDatasetKind.CANDIDATE_SET,
            MarketDatasetKind.TOP20_INDEX,
            MarketDatasetKind.MARKET_STATE,
        }
        if expected_seconds and dataset_kind not in cadence_kinds:
            raise HTTPException(
                status_code=400,
                detail="이 자료 종류는 예상 주기로 결측을 단정할 수 없습니다.",
            )
        normalized_start, normalized_end = as_kst(start), as_kst(end)
        if normalized_end <= normalized_start:
            raise HTTPException(status_code=400, detail="end는 start보다 뒤여야 합니다.")
        cutoff = as_kst(available_by or datetime.now().astimezone())
        from .postgres_access import db_call_source
        with db_call_source("api.market.coverage"):
            observations = await asyncio.to_thread(
                store.load_market_data_metadata_range,
                dataset_kind,
                subject,
                normalized_start,
                normalized_end,
            )
        explicit_complete = await asyncio.to_thread(
            _explicit_coverage_complete,
            store,
            dataset_kind,
            subject,
            normalized_start,
            normalized_end,
            cutoff,
        )
        report = evaluate_coverage(
            tuple(observations),
            start=normalized_start,
            end=normalized_end,
            available_by=cutoff,
            explicit_complete=explicit_complete,
            expected_seconds=expected_seconds,
        )
        return {
            "kind": dataset_kind.value,
            "subject": subject,
            "start": normalized_start.isoformat(),
            "end": normalized_end.isoformat(),
            "available_by": cutoff.isoformat(),
            **report.as_document(),
        }

    @router.get("/api/v1/market/external-bars", dependencies=[Depends(authorize)])
    async def external_bars(
        instrument: str = Query(min_length=1, max_length=40),
        timeframe: str = Query(pattern=r"^(5m|1d)$"),
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> dict[str, object]:
        normalized = instrument.strip().upper()
        values = await asyncio.to_thread(store.load_external_bars, normalized, timeframe, limit)
        return {"instrument": normalized, "timeframe": timeframe, "bars": values}

    return router


def _archive_coverage_ready(values: list[dict[str, Any]], requested_day: str, kind: str) -> bool:
    if not values:
        return False
    document = values[0].get("document")
    if not isinstance(document, dict):
        return False
    as_of = str(document.get("as_of", "")).strip()
    return (
        document.get("kind") == kind
        and document.get("window_closed") is True
        and document.get("session_finalized") is True
        and bool(as_of) and as_of >= requested_day
    )


def _trade_value_comparison_summary(values: list[dict[str, Any]]) -> dict[str, object]:
    """KRX와 NXT가 모두 보완된 분만 주 비교 통계에 포함한다."""
    documents = [
        value.get("document")
        for value in values
        if isinstance(value.get("document"), dict)
    ]
    complete = [
        document for document in documents
        if document.get("query_scope") == "KRX+NXT"
    ]
    realtime_total = sum(
        int(document.get("realtime_trade_value_million_won", 0) or 0)
        for document in complete
    )
    query_total = sum(
        int(document.get("query_trade_value_million_won", 0) or 0)
        for document in complete
    )
    differences = [
        float(document["difference_percent"])
        for document in complete
        if document.get("difference_percent") is not None
    ]
    latest = max(
        (str(document.get("compared_at", "")) for document in documents),
        default="",
    )
    difference_total = realtime_total - query_total
    return {
        "complete_count": len(complete),
        "partial_count": len(documents) - len(complete),
        "scope_counts": {
            scope: sum(1 for document in documents if str(document.get("query_scope", "")) == scope)
            for scope in sorted({str(document.get("query_scope", "")) for document in documents})
            if scope
        },
        "latest_compared_at": latest or None,
        "total_realtime_trade_value_million_won": realtime_total,
        "total_query_trade_value_million_won": query_total,
        "total_difference_million_won": difference_total,
        "total_difference_percent": (
            round(difference_total / query_total * 100, 6) if query_total else None
        ),
        "average_difference_percent": (
            round(sum(differences) / len(differences), 6) if differences else None
        ),
        "mean_absolute_difference_percent": (
            round(sum(abs(value) for value in differences) / len(differences), 6)
            if differences else None
        ),
        "max_absolute_difference_percent": (
            round(max(abs(value) for value in differences), 6) if differences else None
        ),
    }


def _combined_minute_bars(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 분에 SOR 한 벌 또는 KRX+NXT 한 벌만 선택한다."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for value in values:
        key = (str(value.get("trading_date", "")), str(value.get("minute", "")))
        if key[0] and key[1]:
            grouped.setdefault(key, []).append(value)
    result: list[dict[str, Any]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        sor = [row for row in rows if str(row.get("market", "")).upper() == "SOR"]
        if sor:
            selected = dict(max(sor, key=lambda row: float(row.get("updated_at", 0) or 0)))
            selected["market"] = "COMBINED"
            selected["source_market"] = "SOR"
            result.append(selected)
            continue
        by_market = {
            str(row.get("market", "")).upper(): row
            for row in rows if str(row.get("market", "")).upper() in {"KRX", "NXT"}
        }
        krx, nxt = by_market.get("KRX"), by_market.get("NXT")
        base = krx or nxt
        if base is None:
            continue
        selected = dict(base)
        selected["market"] = "COMBINED"
        selected["source_market"] = "KRX+NXT" if krx is not None and nxt is not None else str(base["market"])
        if krx is not None and nxt is not None:
            selected.update({
                "high": max(int(krx["high"]), int(nxt["high"])),
                "low": min(int(krx["low"]), int(nxt["low"])),
                "volume": int(krx["volume"]) + int(nxt["volume"]),
                "trade_value_million_won": (
                    int(krx.get("trade_value_million_won", 0) or 0)
                    + int(nxt.get("trade_value_million_won", 0) or 0)
                ),
                "updated_at": max(
                    float(krx.get("updated_at", 0) or 0),
                    float(nxt.get("updated_at", 0) or 0),
                ),
            })
        result.append(selected)
    return result


def _explicit_coverage_complete(
    store: Any,
    kind: MarketDatasetKind,
    subject: str,
    start: datetime,
    end: datetime,
    available_by: datetime,
) -> bool:
    """실제 장후 조회가 끝났다는 별도 증거가 있을 때만 완전으로 승격한다."""
    covered_end = end - timedelta(microseconds=1)
    if kind != MarketDatasetKind.MINUTE_BAR or start.date() != covered_end.date():
        return False
    code, separator, market = subject.rpartition(":")
    if not separator or not code or market not in {"KRX", "NXT"}:
        return False
    values = store.load_documents(
        "market_data_coverage", f"{start.date().isoformat()}:{code}:{market}", 1
    )
    if not values or values[0].get("document", {}).get("kind") != "minute":
        return False
    try:
        return float(values[0]["updated_at"]) <= available_by.timestamp()
    except (KeyError, TypeError, ValueError, OSError):
        return False

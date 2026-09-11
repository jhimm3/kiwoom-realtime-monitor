import asyncio
import hmac
from pathlib import Path
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from .config import CentralServerSettings
from .contracts import ServerCapabilities, health_document
from .database import create_query_store
from .market_ingest import MarketDataIngestor
from .realtime_hub import RealtimeHub
from .realtime_collector import CentralRealtimeCollector
from .rest_broker import CentralRestBroker
from .resource_usage import resource_usage
from .autonomous_top20 import AutonomousTop20Service
from .external_market_collector import YahooDelayedMarketCollector
from .market_observations import as_kst
from kiwoom_monitor.application.market_data_coverage import evaluate_coverage
from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError


SERVER_BUILD = "2026.09.12-shared-backup-contract-v1"


def create_app(settings: CentralServerSettings | None = None) -> Any:
    """FastAPI 앱을 만든다. 서버 선택 의존성은 로컬 앱과 분리해 지연 로드한다."""
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
        from pydantic import BaseModel, Field
    except ImportError as error:
        raise RuntimeError("중앙 서버 의존성을 설치하세요: pip install -e .[server]") from error

    active = settings or CentralServerSettings.from_environment()
    broker: CentralRestBroker | None = None
    collector: CentralRealtimeCollector | None = None
    top20_service: AutonomousTop20Service | None = None
    external_market_service: YahooDelayedMarketCollector | None = None
    news_service = None
    ai_service = None
    realtime_hub = RealtimeHub()
    store = create_query_store(active.database_url)
    store.initialize()
    operational = {
        "ai_provider": active.ai_provider,
        "ai_model": active.ai_model,
        "ai_daily_limit": active.ai_daily_limit,
        "news_refresh_seconds": active.news_refresh_seconds,
        "dart_enabled": active.dart_enabled,
    }
    saved_operational = store.load_documents("server_operational_settings", "global", 1)
    if saved_operational and isinstance(saved_operational[0].get("document"), dict):
        operational.update(saved_operational[0]["document"])
    if active.kiwoom_configured:
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient, KiwoomSettings
        kiwoom_client = KiwoomRestClient(KiwoomSettings(
            active.kiwoom_app_key, active.kiwoom_secret_key, active.kiwoom_environment,
        ))
        broker = CentralRestBroker(kiwoom_client, store, MarketDataIngestor(store).ingest)
        collector = CentralRealtimeCollector(
            kiwoom_client.get_access_token, active.kiwoom_environment, realtime_hub,
            kiwoom_client.server_now, store,
        )
        if active.autonomous_top20_enabled:
            top20_service = AutonomousTop20Service(
                broker, realtime_hub, store,
                outbox_path=Path(active.top20_outbox_path),
            )
    if active.news_configured:
        from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
        from kiwoom_monitor.infrastructure.naver_news import NaverNewsClient, NaverNewsCredentials
        from .news_service import CentralNewsService
        naver_client = None
        if active.naver_news_client_id and active.naver_news_client_secret:
            naver_client = NaverNewsClient(NaverNewsCredentials(
                active.naver_news_client_id, active.naver_news_client_secret,
            ))
        dart_client = (
            DartDisclosureClient(active.dart_api_key, Path(active.dart_cache_path))
            if active.dart_api_key else None
        )
        news_service = CentralNewsService(
            naver_client, store, dart_client, refresh_seconds=int(operational["news_refresh_seconds"]),
        )
        news_service.update_operational_settings(
            refresh_seconds=int(operational["news_refresh_seconds"]),
            dart_enabled=bool(operational["dart_enabled"]),
        )
    if active.external_market_enabled and active.parsed_external_market_symbols():
        external_market_service = YahooDelayedMarketCollector(
            store, active.parsed_external_market_symbols(),
            poll_seconds=active.external_market_poll_seconds,
            auto_roll_enabled=active.external_market_auto_roll_enabled,
            roll_confirmations=active.external_market_roll_confirmations,
        )
    if active.ai_configured:
        from .ai_service import CentralAIService
        ai_service = CentralAIService(active, store)
        ai_service.update_operational_settings(
            provider=str(operational["ai_provider"]), model=str(operational["ai_model"]),
            daily_limit=int(operational["ai_daily_limit"]),
        )
    if news_service is not None:
        news_service.set_ai_service(ai_service)

    @asynccontextmanager
    async def lifespan(_app: Any):
        if broker is not None:
            await broker.start()
        if top20_service is not None:
            await top20_service.start()
        if collector is not None:
            await collector.start()
        if news_service is not None:
            await news_service.start()
        if external_market_service is not None:
            await external_market_service.start()
        yield
        if external_market_service is not None:
            await external_market_service.close()
        if news_service is not None:
            await news_service.close()
        if top20_service is not None:
            await top20_service.close()
        if collector is not None:
            await collector.close()
        if broker is not None:
            await broker.close()
        store.close()

    app = FastAPI(title="Kiwoom Monitor Personal Server", version="1", lifespan=lifespan)
    app.state.realtime_hub = realtime_hub

    class QueryRequest(BaseModel):
        api_id: str = Field(min_length=7, max_length=7)
        path: str
        body: dict[str, Any] = Field(default_factory=dict)
        cont_yn: str = "N"
        next_key: str = ""

    class DocumentInput(BaseModel):
        owner: str = Field(max_length=200)
        key: str = Field(min_length=1, max_length=2000)
        document: dict[str, Any]

    class DocumentBatch(BaseModel):
        documents: list[DocumentInput] = Field(min_length=1, max_length=1000)

    class DocumentSnapshot(BaseModel):
        documents: list[DocumentInput] = Field(default_factory=list, max_length=10000)

    class NewsSearchRequest(BaseModel):
        stock_code: str = Field(min_length=6, max_length=12)
        stock_name: str = Field(min_length=1, max_length=100)
        since: datetime | None = None
        ai_auto_analyze: bool = False
        ai_auto_recent_limit: int = Field(default=10, ge=1, le=1000)
        ai_provider: str = Field(default="none", max_length=20)
        ai_model: str = Field(default="", max_length=100)

    class AIEventInput(BaseModel):
        identity: str = Field(min_length=1, max_length=2000)
        title: str = Field(max_length=1000)
        body: str = Field(default="", max_length=2_000_000)
        body_hash: str = Field(default="", max_length=128)
        articles: list[dict[str, str]] = Field(default_factory=list, max_length=100)

    class AIAnalysisRequest(BaseModel):
        stock_code: str = Field(min_length=6, max_length=12)
        stock_name: str = Field(min_length=1, max_length=100)
        provider: str = Field(default="", max_length=20)
        model: str = Field(default="", max_length=100)
        events: list[AIEventInput] = Field(min_length=1, max_length=20)
        article_count: int = Field(default=0, ge=0, le=10_000)

    class OperationalSettingsUpdate(BaseModel):
        ai_provider: str = Field(pattern=r"^(none|openai|gemini|claude)$")
        ai_model: str = Field(default="", max_length=100)
        ai_daily_limit: int = Field(ge=0, le=1_000_000)
        news_refresh_seconds: int = Field(ge=60, le=86_400)
        dart_enabled: bool = False

    def authorize(authorization: str = Header(default="")) -> None:
        scheme, _, supplied = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not hmac.compare_digest(supplied, active.access_token):
            raise HTTPException(status_code=401, detail="유효한 서버 접속 토큰이 필요합니다.")

    def valid_token(supplied: str) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied, active.access_token)

    @app.get("/health")
    def health() -> dict[str, object]:
        document = health_document()
        document["server_build"] = SERVER_BUILD
        return document

    @app.get("/api/v1/capabilities", dependencies=[Depends(authorize)])
    def capabilities() -> dict[str, object]:
        return ServerCapabilities(
            kiwoom_rest=broker is not None, realtime_stream=collector is not None,
            news_archive=True, ai_analysis_archive=True, themes=True,
            trade_journal=True, shared_settings=True,
        ).as_document()

    @app.get("/api/v1/settings/operations", dependencies=[Depends(authorize)])
    def get_operational_settings() -> dict[str, object]:
        return dict(operational)

    @app.get("/api/v1/diagnostics/resources", dependencies=[Depends(authorize)])
    async def diagnostics_resources() -> dict[str, object]:
        database_size = await asyncio.to_thread(store.storage_size_bytes)
        return resource_usage("/app/data", database_size)

    @app.put("/api/v1/settings/operations", dependencies=[Depends(authorize)])
    async def put_operational_settings(values: OperationalSettingsUpdate) -> dict[str, object]:
        operational.update(values.model_dump())
        await asyncio.to_thread(store.upsert_documents, "server_operational_settings", [{
            "owner": "global", "key": "current", "document": dict(operational),
        }])
        if ai_service is not None:
            ai_service.update_operational_settings(
                provider=str(operational["ai_provider"]), model=str(operational["ai_model"]),
                daily_limit=int(operational["ai_daily_limit"]),
            )
        if news_service is not None:
            news_service.update_operational_settings(
                refresh_seconds=int(operational["news_refresh_seconds"]),
                dart_enabled=bool(operational["dart_enabled"]),
            )
        return dict(operational)

    @app.post("/api/v1/kiwoom/query", dependencies=[Depends(authorize)])
    async def kiwoom_query(query: QueryRequest) -> dict[str, object]:
        if broker is None:
            raise HTTPException(status_code=503, detail="서버에 키움 API 키가 설정되지 않았습니다.")
        if query.cont_yn == "N":
            archived = await asyncio.to_thread(_archived_chart_response, store, query.api_id, query.body)
            if archived is not None:
                return {
                    "payload": archived, "has_next": False, "next_key": "",
                    "cache_hit": False, "archive_hit": True,
                }
        try:
            result = await broker.request(
                query.api_id, query.path, query.body, cont_yn=query.cont_yn, next_key=query.next_key,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "payload": result.payload,
            "has_next": result.has_next,
            "next_key": result.next_key,
            "cache_hit": result.cache_hit,
        }

    @app.post("/api/v1/news/search", dependencies=[Depends(authorize)])
    async def news_search(query: NewsSearchRequest) -> dict[str, object]:
        if news_service is None:
            raise HTTPException(status_code=503, detail="서버에 네이버 뉴스 API 키가 설정되지 않았습니다.")
        try:
            items = await news_service.search(query.stock_code, query.stock_name, query.since, automation={
                "auto_analyze": query.ai_auto_analyze,
                "auto_recent_limit": query.ai_auto_recent_limit,
                "provider": query.ai_provider,
                "model": query.ai_model,
            })
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"stock_code": query.stock_code, "items": items}

    @app.post("/api/v1/news/analyze", dependencies=[Depends(authorize)])
    async def news_analyze(query: AIAnalysisRequest) -> dict[str, object]:
        if ai_service is None:
            raise HTTPException(status_code=503, detail="서버에 AI API 키가 설정되지 않았습니다.")
        try:
            return await ai_service.analyze(
                query.stock_code, query.stock_name, query.provider, query.model,
                [value.model_dump() for value in query.events], query.article_count,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except NewsAIProviderError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.get("/api/v1/market/minute-bars", dependencies=[Depends(authorize)])
    async def minute_bars(
        code: str = Query(min_length=6, max_length=12),
        trading_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        market: str = Query(default="", max_length=8),
    ) -> dict[str, object]:
        values = await asyncio.to_thread(store.load_minute_bars, code, trading_date, market.upper())
        return {"code": code, "trading_date": trading_date, "market": market.upper(), "bars": values}

    @app.get("/api/v1/market/daily-bars", dependencies=[Depends(authorize)])
    async def daily_bars(
        code: str = Query(min_length=6, max_length=12),
        market: str = Query(default="", max_length=8),
        limit: int = Query(default=250, ge=1, le=5000),
    ) -> dict[str, object]:
        values = await asyncio.to_thread(store.load_daily_bars, code, market.upper(), limit)
        return {"code": code, "market": market.upper(), "bars": values}

    @app.get("/api/v1/market/coverage", dependencies=[Depends(authorize)])
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

    @app.get("/api/v1/market/external-bars", dependencies=[Depends(authorize)])
    async def external_bars(
        instrument: str = Query(min_length=1, max_length=40),
        timeframe: str = Query(pattern=r"^(5m|1d)$"),
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> dict[str, object]:
        normalized = instrument.strip().upper()
        values = await asyncio.to_thread(store.load_external_bars, normalized, timeframe, limit)
        return {"instrument": normalized, "timeframe": timeframe, "bars": values}

    @app.get("/api/v1/market/snapshots/{kind}", dependencies=[Depends(authorize)])
    async def dataset_snapshots(
        kind: str, subject: str = Query(default="", max_length=32),
        limit: int = Query(default=100, ge=1, le=5000),
    ) -> dict[str, object]:
        allowed = {
            "ranking", "top20_membership", "top20_index", "market_state",
            "investor_flow", "program_flow", "new_high", "stock_fundamentals",
            "nxt_eligibility",
        }
        if kind not in allowed:
            raise HTTPException(status_code=404, detail="지원하지 않는 중앙 시장 자료입니다.")
        values = await asyncio.to_thread(store.load_dataset_snapshots, kind, subject, limit)
        return {"kind": kind, "subject": subject, "snapshots": values}

    content_collections = {
        "news_article", "news_ai", "news_ai_shared", "news_request_usage", "journal_news_link", "news_sync", "news_watchlist",
        "theme_profile", "theme_stock", "theme_metadata",
        "journal_settings", "journal_fills", "journal_reviews", "journal_setups",
        "journal_cycle_overrides", "journal_group_overrides", "journal_entry_snapshots",
        "journal_costs", "journal_stocks", "journal_backfill",
        "app_settings", "app_column_settings",
        "stock_fundamentals", "stock_nxt_eligibility",
    }

    @app.get("/api/v1/content/{collection}", dependencies=[Depends(authorize)])
    async def content_documents(
        collection: str, owner: str = Query(default="", max_length=200),
        limit: int = Query(default=1000, ge=1, le=10000),
        offset: int = Query(default=0, ge=0),
        updated_after: float = Query(default=0.0, ge=0.0),
    ) -> dict[str, object]:
        if collection not in content_collections:
            raise HTTPException(status_code=404, detail="지원하지 않는 중앙 자료 종류입니다.")
        values = await asyncio.to_thread(
            store.load_documents, collection, owner, limit, offset, updated_after,
        )
        return {"collection": collection, "owner": owner, "documents": values}

    @app.post("/api/v1/content/{collection}", dependencies=[Depends(authorize)])
    async def upsert_content(collection: str, batch: DocumentBatch) -> dict[str, object]:
        if collection not in content_collections:
            raise HTTPException(status_code=404, detail="지원하지 않는 중앙 자료 종류입니다.")
        values = [value.model_dump() for value in batch.documents]
        await asyncio.to_thread(store.upsert_documents, collection, values)
        return {"collection": collection, "saved": len(values)}

    @app.put("/api/v1/content/{collection}", dependencies=[Depends(authorize)])
    async def replace_content(collection: str, snapshot: DocumentSnapshot) -> dict[str, object]:
        # 삭제·이름 변경도 정확히 전파해야 하는 작은 설정 컬렉션에만 허용한다.
        if collection not in {"theme_profile", "theme_stock", "theme_metadata"}:
            raise HTTPException(status_code=405, detail="전체 교체를 지원하지 않는 중앙 자료 종류입니다.")
        values = [value.model_dump() for value in snapshot.documents]
        await asyncio.to_thread(store.replace_documents, collection, values)
        return {"collection": collection, "saved": len(values)}

    @app.websocket("/api/v1/realtime")
    async def realtime(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization", "")
        scheme, _, header_token = authorization.partition(" ")
        supplied = header_token if scheme.casefold() == "bearer" else websocket.query_params.get("token", "")
        if not valid_token(supplied):
            await websocket.close(code=4401, reason="유효한 서버 접속 토큰이 필요합니다.")
            return
        await websocket.accept()
        subscriber = realtime_hub.connect()

        async def send_events() -> None:
            while True:
                await websocket.send_json(await subscriber.queue.get())

        sender = asyncio.create_task(send_events())
        try:
            await websocket.send_json({"type": "ready", "schema_version": 1})
            while True:
                message = await websocket.receive_json()
                message_type = str(message.get("type", "")).casefold()
                if message_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif message_type == "subscribe":
                    codes, nxt_codes = realtime_hub.update_subscription(
                        subscriber, list(message.get("codes", [])), list(message.get("nxt_codes", [])),
                    )
                    await websocket.send_json({
                        "type": "subscribed", "codes": sorted(subscriber.codes),
                        "nxt_codes": sorted(subscriber.nxt_codes),
                        "upstream_code_count": len(codes), "upstream_nxt_code_count": len(nxt_codes),
                    })
                    for snapshot in await asyncio.to_thread(
                        store.load_realtime_snapshots, sorted(subscriber.codes),
                    ):
                        await websocket.send_json(snapshot)
                    # 같은 종목을 이미 다른 앱이 구독 중이면 상류 구독 변경 이벤트가
                    # 다시 발생하지 않는다. 각 앱에는 별도로 준비 완료를 알려준다.
                    await websocket.send_json({"type": "central_ready", "codes": sorted(subscriber.codes)})
                    if realtime_hub.upstream_ready_for(subscriber.codes):
                        await websocket.send_json({
                            "type": "connection_opened", "codes": sorted(subscriber.codes),
                        })
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender
            realtime_hub.disconnect(subscriber)

    return app


def _archived_chart_response(store: Any, api_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """완료 확인된 NAS 차트를 키움 TR보다 먼저 재사용한다."""
    raw_code = str(body.get("stk_cd", "")).strip()
    market = "NXT" if raw_code.endswith("_NX") else "SOR" if raw_code.endswith("_AL") else "KRX"
    code = raw_code.removesuffix("_NX").removesuffix("_AL")
    if not code:
        return None
    if api_id == "ka10080":
        raw_day = str(body.get("base_dt", "")).strip()
        if len(raw_day) != 8 or not raw_day.isdigit():
            return None
        day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"
        coverage = store.load_documents("market_data_coverage", f"{day}:{code}:{market}", 1)
        if not _archive_coverage_ready(coverage, day):
            return None
        bars = store.load_minute_bars(code, day, market)
        if not bars:
            return None
        return {"stk_min_pole_chart_qry": [{
            "cntr_tm": f"{raw_day}{str(bar['minute']).replace(':', '')}00",
            "open_pric": str(bar["open"]), "high_pric": str(bar["high"]),
            "low_pric": str(bar["low"]), "cur_prc": str(bar["close"]),
            "trde_qty": str(bar["volume"]),
        } for bar in reversed(bars)]}
    if api_id == "ka10081":
        raw_day = str(body.get("base_dt", "")).strip()
        if len(raw_day) != 8 or not raw_day.isdigit():
            return None
        day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"
        coverage = store.load_documents("market_data_coverage_daily", f"{code}:{market}", 1)
        if not _archive_coverage_ready(coverage, day):
            return None
        bars = [
            bar for bar in store.load_daily_bars(code, market, 5000)
            if str(bar.get("trading_date", "")) <= day
        ][:250]
        # 과거 버전이 NXT 빈 응답도 완료(rows=0)로 남긴 경우가 있다.
        # 빈 아카이브는 확정 자료가 아니므로 키움 조회를 우회하지 않는다.
        if not bars:
            return None
        return {"stk_dt_pole_chart_qry": [{
            "date": str(bar["trading_date"]).replace("-", ""),
            "open_pric": str(bar["open"]), "high_pric": str(bar["high"]),
            "low_pric": str(bar["low"]), "cur_prc": str(bar["close"]),
            "trde_qty": str(bar["volume"]),
            "trde_prica": str(bar.get("trade_value_million_won") or 0),
        } for bar in bars]}
    return None


def _archive_coverage_ready(values: list[dict[str, Any]], requested_day: str) -> bool:
    if not values:
        return False
    document = values[0].get("document")
    if not isinstance(document, dict):
        return False
    as_of = str(document.get("as_of", "")).strip()
    # 예전 coverage에는 as_of가 없으므로 저장된 봉 자체의 날짜 필터로
    # 호환한다. 명시된 as_of가 요청일보다 과거면 최근 구간이 빠진 자료다.
    return not as_of or as_of >= requested_day


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

import asyncio
import hashlib
import hmac
import json
import logging
import re
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import CentralServerSettings
from .contracts import ServerCapabilities, health_document
from .database import create_query_store
from .market_ingest import MarketDataIngestor, fundamentals_document_is_current
from .realtime_hub import RealtimeHub
from .realtime_collector import CentralRealtimeCollector
from .rest_broker import CentralRestBroker
from .resource_usage import resource_usage
from .autonomous_top20 import AutonomousTop20Service
from .external_market_collector import YahooDelayedMarketCollector
from .market_observations import KST, as_kst
from .market_events import MarketEventService
from .candidate_monitor import CandidateMonitor
from .account_query import AccountQuerySessionManager
from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    default_shadow_breakout_config,
)
from kiwoom_monitor.application.market_data_coverage import evaluate_coverage
from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError


SERVER_BUILD = "2026.09.21-market-cap-reference-v1"
logger = logging.getLogger(__name__)


def _verified_realtime_scope(
    raw_account_number: str, identity: Any, binding: Any, hmac_key: bytes,
):
    """Return only the anonymous scope after an in-memory 9201 fingerprint match."""
    if identity is None or binding is None:
        return None
    from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import account_identity_fingerprint
    try:
        fingerprint = account_identity_fingerprint(
            raw_account_number, identity.environment, hmac_key,
        )
    except ValueError:
        return None
    return binding.scope if hmac.compare_digest(fingerprint, identity.identity_fingerprint) else None


def create_app(settings: CentralServerSettings | None = None) -> Any:
    """FastAPI 앱을 만든다. 서버 선택 의존성은 로컬 앱과 분리해 지연 로드한다."""
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
        from pydantic import BaseModel, Field, ConfigDict
    except ImportError as error:
        raise RuntimeError("중앙 서버 의존성을 설치하세요: pip install -e .[server]") from error

    active = settings or CentralServerSettings.from_environment()
    broker: CentralRestBroker | None = None
    account_broker: CentralRestBroker | None = None
    collector: CentralRealtimeCollector | None = None
    mock_account_monitor = None
    mock_account_realtime = None
    mock_order_gateway = None
    mock_bundle = None
    mock_owner = None
    mock_automation_supervisor = None
    real_owner = None
    main_identity_reader = None
    main_binding = None
    main_identity = None
    top20_service: AutonomousTop20Service | None = None
    market_event_service: MarketEventService | None = None
    external_market_service: YahooDelayedMarketCollector | None = None
    candidate_monitor: CandidateMonitor | None = None
    candidate_settings_lock = asyncio.Lock()
    news_service = None
    ai_service = None
    realtime_hub = RealtimeHub()
    store = create_query_store(
        active.database_url,
        observation_history_enabled=active.research_observation_history_enabled,
    )
    store.initialize()
    credential_vault = None
    credential_statuses: dict[str, str] = {}
    if active.credential_directory:
        from kiwoom_monitor.central_server.credential_store import (
            CredentialStore, CredentialStoreError, compose_credential_settings, PROVIDER_FIELDS,
        )
        try:
            credential_vault = CredentialStore(active.credential_directory, store)
            active, credential_statuses = compose_credential_settings(active, credential_vault, store)
        except Exception as error:
            if isinstance(error, CredentialStoreError) and str(error) == "VAULT_ALREADY_OWNED":
                store.close()
                raise RuntimeError("VAULT_ALREADY_OWNED: use one server worker") from None
            # Directory/master/ownership failure must not resurrect env credentials.
            from dataclasses import replace
            active = replace(active, kiwoom_app_key="", kiwoom_secret_key="",
                             kiwoom_mock_app_key="", kiwoom_mock_secret_key="",
                             naver_news_client_id="", naver_news_client_secret="", dart_api_key="",
                             openai_api_key="", gemini_api_key="", anthropic_api_key="",
                             mock_account_monitor_enabled=False, mock_order_transport_enabled=False)
            credential_statuses = {provider: "RECOVERY_REQUIRED" for provider in PROVIDER_FIELDS}
    from .credential_runtime import CredentialRuntime, install_credential_routes
    credential_runtime = CredentialRuntime(credential_vault, store) if credential_vault is not None else None
    operational = {
        "revision": 0,
        "ai_provider": active.ai_provider,
        "ai_model": active.ai_model,
        "ai_daily_limit": active.ai_daily_limit,
        "news_refresh_seconds": active.news_refresh_seconds,
        "dart_enabled": active.dart_enabled,
        "news_query_set_enabled": active.news_query_set_enabled,
        "news_query_set": list(active.parsed_news_query_set()),
        "news_query_set_refresh_seconds": active.news_query_set_refresh_seconds,
        "external_market_enabled": active.external_market_enabled,
        "external_market_poll_seconds": active.external_market_poll_seconds,
        "external_market_auto_roll_enabled": active.external_market_auto_roll_enabled,
        "external_market_roll_confirmations": active.external_market_roll_confirmations,
        "hot_cohort_condition_enabled": True,
        "hot_cohort_condition_name": active.hot_cohort_condition_name,
        "hot_cohort_condition_substring": active.hot_cohort_condition_substring,
        "news_processing_excluded_providers": list(
            active.parsed_news_processing_excluded_providers()
        ),
        # The environment value only seeds the first persisted operational setting.
        # Later changes are applied at runtime without restarting the NAS server.
        "shadow_candidate_enabled": active.shadow_candidate_enabled,
        "shadow_candidate_config": None,
        "shadow_candidate_poll_seconds": active.shadow_candidate_poll_seconds,
        "shadow_candidate_universe_max_age_seconds": (
            active.shadow_candidate_universe_max_age_seconds or 90
        ),
    }
    if active.shadow_candidate_config_json.strip():
        try:
            configured_shadow = json.loads(active.shadow_candidate_config_json)
            if isinstance(configured_shadow, dict):
                operational["shadow_candidate_config"] = configured_shadow
        except json.JSONDecodeError:
            pass
    if operational["shadow_candidate_config"] is None:
        operational["shadow_candidate_config"] = default_shadow_breakout_config().to_dict()
    saved_operational = store.load_documents("server_operational_settings", "global", 1)
    if saved_operational and isinstance(saved_operational[0].get("document"), dict):
        operational.update(saved_operational[0]["document"])
    applied_operational_revision = int(operational.get("revision", 0))
    try:
        shadow_document = operational.get("shadow_candidate_config")
        if not isinstance(shadow_document, dict):
            raise ValueError("shadow candidate strategy config must be an object")
        operational["shadow_candidate_config"] = BreakoutStrategyConfig(
            **shadow_document
        ).to_dict()
    except (TypeError, ValueError):
        logger.warning("invalid saved shadow candidate settings disabled", exc_info=True)
        operational["shadow_candidate_enabled"] = False
        operational["shadow_candidate_config"] = default_shadow_breakout_config().to_dict()
    if active.mock_account_monitor_enabled and not active.kiwoom_mock_configured:
        store.close()
        raise ValueError("모의계좌 모니터에는 별도 모의투자 앱 키와 시크릿 키가 필요합니다.")
    if active.mock_order_transport_enabled and not active.mock_account_monitor_enabled:
        store.close()
        raise ValueError("모의주문 전송에는 모의계좌 모니터가 필요합니다.")
    if active.mock_account_monitor_enabled and (
        not active.mock_account_ref or not active.mock_execution_run_id
    ):
        store.close()
        raise ValueError("모의계좌 모니터에는 익명 account ref와 execution run ID가 필요합니다.")
    real_runtime_enabled = (credential_runtime is not None and active.account_identity_registry_enabled
                            and active.kiwoom_environment == "real")
    if active.kiwoom_configured or real_runtime_enabled:
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient, KiwoomSettings
        kiwoom_client = KiwoomRestClient(KiwoomSettings(
            active.kiwoom_app_key, active.kiwoom_secret_key, active.kiwoom_environment,
        ))
        broker = CentralRestBroker(
            kiwoom_client, store, MarketDataIngestor(store).ingest,
            ranking_reservation=True,
        )
        if active.account_identity_registry_enabled and not real_runtime_enabled:
            from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
            from kiwoom_monitor.domain.order_contract import AccountEnvironment
            main_identity_reader = KiwoomAccountIdentityReader(
                broker,
                environment=AccountEnvironment(active.kiwoom_environment),
                hmac_key=active.account_identity_key(),
                now_provider=lambda: as_kst(kiwoom_client.server_now()),
            )
        if active.market_event_collection_enabled:
            market_event_service = MarketEventService(
                broker, realtime_hub, store,
                exact_condition_name=str(operational["hot_cohort_condition_name"]),
                condition_substring=str(operational["hot_cohort_condition_substring"]),
                condition_enabled=bool(operational["hot_cohort_condition_enabled"]),
                now_provider=lambda: broker._client.server_now(),
            )
        def realtime_account_scope(raw):
            context = real_owner.bundle(real_owner.market_profile_id) if real_owner else None
            return _verified_realtime_scope(raw,
                context.identity if context else None if real_owner else main_identity,
                context.binding if context else None if real_owner else main_binding,
                active.account_identity_key())

        collector = CentralRealtimeCollector(
            lambda: broker._client.get_access_token(), active.kiwoom_environment, realtime_hub,
            lambda: broker._client.server_now(), store, market_event_service,
            account_scope_resolver=realtime_account_scope if active.account_identity_registry_enabled else None,
        )
        if active.autonomous_top20_enabled:
            top20_service = AutonomousTop20Service(
                broker, realtime_hub, store,
                outbox_path=Path(active.top20_outbox_path),
            )
    if active.mock_account_monitor_enabled:
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
        from .mock_runtime import MockAccountBundle

        mock_bundle = MockAccountBundle(
            store, settings=KiwoomSettings(
                active.kiwoom_mock_app_key, active.kiwoom_mock_secret_key, "mock",
            ), account_ref=active.mock_account_ref, run_id=active.mock_execution_run_id,
            identity_hmac_key=(
                active.account_identity_key() if active.account_identity_registry_enabled else None
            ), order_transport_enabled=active.mock_order_transport_enabled,
        )
        account_broker = mock_bundle.broker
        mock_account_monitor = mock_bundle.monitor
        mock_account_realtime = mock_bundle.realtime
        mock_order_gateway = mock_bundle.gateway
    if credential_runtime is not None and active.account_identity_registry_enabled:
        from .mock_runtime import MockCredentialOwner

        def publish_mock_bundle(profile_id, bundle):
            nonlocal mock_bundle, mock_account_monitor, mock_account_realtime, mock_order_gateway
            if profile_id == "nas-mock-default":
                mock_bundle = bundle
                mock_account_monitor = bundle.monitor if bundle else None
                mock_account_realtime = bundle.realtime if bundle else None
                mock_order_gateway = bundle.gateway if bundle else None
                app.state.mock_account_bundle = bundle
                app.state.mock_account_monitor = mock_account_monitor
                app.state.mock_account_realtime = mock_account_realtime
                app.state.mock_order_gateway = mock_order_gateway
            app.state.verified_account_bindings = tuple(
                (list(real_owner.account_bindings()) if real_owner is not None else
                 [main_binding] if main_binding is not None else []) +
                [b.binding for b in mock_owner._bundles.values() if b.binding is not None]
            )

        mock_owner = MockCredentialOwner(store, credential_vault,
            hmac_key=active.account_identity_key(), legacy_bundle=mock_bundle,
            legacy_monitor_enabled=active.mock_account_monitor_enabled, on_change=publish_mock_bundle)
        credential_runtime.register("kiwoom_mock", mock_owner.hooks())
    account_query_manager = (
        AccountQuerySessionManager(broker, lambda: main_binding)
        if broker is not None and not real_runtime_enabled else None
    )
    if real_runtime_enabled:
        from .real_runtime import RealCredentialOwner

        def publish_real_context(profile_id, context):
            nonlocal main_binding, main_identity, account_query_manager
            if profile_id == "nas-real-default":
                main_binding = context.binding if context else None
                main_identity = context.identity if context else None
                account_query_manager = context.account_queries if context else None
            app.state.verified_account_bindings = tuple(
                list(real_owner.account_bindings()) +
                (list(mock_owner.account_bindings()) if mock_owner else []))

        real_owner = RealCredentialOwner(store, credential_vault, hmac_key=active.account_identity_key(),
            market_client=kiwoom_client, market_broker=broker, market_collector=collector,
            on_change=publish_real_context,
            account_event_publisher=collector.publish_account_event if collector is not None else None)
        if collector is not None:
            collector._account_event_handler = real_owner.on_market_account_event
        credential_runtime.register("kiwoom_real", real_owner.hooks())
    if active.news_configured or credential_runtime is not None:
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
            jobs_enabled=active.news_history_jobs_enabled,
            query_set_enabled=bool(operational["news_query_set_enabled"]),
            query_set=tuple(str(value) for value in operational["news_query_set"]),
            query_set_refresh_seconds=int(operational["news_query_set_refresh_seconds"]),
            request_hard_limit=active.news_request_hard_limit,
            watchlist_request_limit=active.news_watchlist_request_limit,
            query_set_request_limit=active.news_query_set_request_limit,
            processing_excluded_providers=tuple(
                str(value) for value in operational["news_processing_excluded_providers"]
            ),
        )
        news_service.update_operational_settings(
            refresh_seconds=int(operational["news_refresh_seconds"]),
            dart_enabled=bool(operational["dart_enabled"]),
            query_set_enabled=bool(operational["news_query_set_enabled"]),
            query_set=tuple(str(value) for value in operational["news_query_set"]),
            query_set_refresh_seconds=int(operational["news_query_set_refresh_seconds"]),
            processing_excluded_providers=tuple(
                str(value) for value in operational["news_processing_excluded_providers"]
            ),
        )
    if credential_runtime is not None:
        from .news_credentials import NaverCredentialOwner, DartCredentialOwner
        naver_owner = NaverCredentialOwner(news_service, credential_vault)
        credential_runtime.register("naver", naver_owner.hooks())
        dart_owner = DartCredentialOwner(news_service, credential_vault, Path(active.dart_cache_path))
        credential_runtime.register("dart", dart_owner.hooks())
    if active.parsed_external_market_symbols():
        external_market_service = YahooDelayedMarketCollector(
            store, active.parsed_external_market_symbols(),
            poll_seconds=int(operational["external_market_poll_seconds"]),
            auto_roll_enabled=bool(operational["external_market_auto_roll_enabled"]),
            roll_confirmations=int(operational["external_market_roll_confirmations"]),
        )
    if active.ai_configured or credential_runtime is not None:
        from .ai_service import CentralAIService
        ai_service = CentralAIService(active, store)
        ai_service.update_operational_settings(
            provider=str(operational["ai_provider"]), model=str(operational["ai_model"]),
            daily_limit=int(operational["ai_daily_limit"]),
        )
        if credential_runtime is not None:
            from .ai_credentials import AICredentialOwner
            for provider in ("openai", "gemini", "claude"):
                owner = AICredentialOwner(ai_service, credential_vault, provider)
                credential_runtime.register(provider, owner.hooks())
    if bool(operational["shadow_candidate_enabled"]):
        candidate_monitor = CandidateMonitor.from_json(
            store, json.dumps(operational["shadow_candidate_config"]),
            poll_seconds=float(operational["shadow_candidate_poll_seconds"]),
            universe_max_age_seconds=int(operational["shadow_candidate_universe_max_age_seconds"]),
        )
    if news_service is not None:
        news_service.set_ai_service(ai_service)
    if mock_owner is not None:
        from .mock_automation_supervisor import MockAutomationSupervisor
        mock_automation_supervisor = MockAutomationSupervisor(store, mock_owner)

    @asynccontextmanager
    async def service_lifespan(_app: Any):
        nonlocal account_broker, main_binding, main_identity, mock_bundle
        nonlocal mock_account_monitor, mock_account_realtime, mock_order_gateway
        if broker is not None:
            await broker.start()
        verified_bindings = []
        if real_owner is not None:
            await real_owner.start()
            verified_bindings.extend(real_owner.account_bindings())
        if main_identity_reader is not None:
            from kiwoom_monitor.application.account_identity import bind_verified_account_identity
            try:
                main_identity = await main_identity_reader.verify()
                main_binding = bind_verified_account_identity(
                    main_identity, store,
                    credential_profile_id=("nas-main-mock-default"
                        if credential_vault is not None and active.kiwoom_environment == "mock"
                        else f"nas-{active.kiwoom_environment}-default"),
                )
                verified_bindings.append(main_binding)
            except Exception:
                if mock_owner is not None:
                    await mock_owner.close()
                elif mock_bundle is not None:
                    await mock_bundle.close()
                if broker is not None:
                    await broker.close()
                store.close()
                raise
        if mock_owner is not None:
            if main_binding is not None and main_binding.scope.environment.value == "mock":
                mock_owner.reserved_accounts.add(main_binding.scope.account_ref)
                mock_owner.reserved_profiles.add(main_binding.credential_profile_id)
            await mock_owner.start()
            await mock_automation_supervisor.start()
        elif mock_bundle is not None:
            try:
                await mock_bundle.start()
            except Exception as error:
                # 모의계좌 모니터는 중앙 시세·뉴스보다 낮은 선택 기능이다. 키움
                # 모의 토큰이 일시적으로 발급되지 않아도 NAS 서버 전체를 재시작
                # 루프에 넣지 않고, 이번 실행에서 모의계좌 기능만 닫는다.
                code = (
                    "ACCOUNT_CONTEXT_MISMATCH" if str(error) == "ACCOUNT_CONTEXT_MISMATCH"
                    else "MOCK_ACCOUNT_STARTUP_FAILED"
                )
                logger.error("모의계좌 기능만 비활성화합니다: %s", code)
                await mock_bundle.close()
                mock_bundle = None
                account_broker = None
                mock_account_monitor = None
                mock_account_realtime = None
                mock_order_gateway = None
                _app.state.mock_account_startup_error = code
                _app.state.mock_account_bundle = None
                _app.state.mock_account_monitor = None
                _app.state.mock_account_realtime = None
                _app.state.mock_order_gateway = None
            else:
                if mock_bundle.binding is not None:
                    verified_bindings.append(mock_bundle.binding)
        _app.state.verified_account_bindings = tuple(verified_bindings)
        if top20_service is not None:
            await top20_service.start()
        if market_event_service is not None:
            await market_event_service.start()
        if collector is not None:
            await collector.start()
        if news_service is not None:
            await news_service.start()
        if external_market_service is not None and bool(operational["external_market_enabled"]):
            await external_market_service.start()
        if candidate_monitor is not None:
            await candidate_monitor.start()
        yield
        if credential_runtime is not None:
            await credential_runtime.close()
        if mock_automation_supervisor is not None:
            await mock_automation_supervisor.close()
        if real_owner is not None:
            await real_owner.close()
        if mock_owner is not None:
            await mock_owner.close()
        elif mock_bundle is not None:
            await mock_bundle.close()
        if candidate_monitor is not None:
            await candidate_monitor.close()
        if external_market_service is not None:
            await external_market_service.close()
        if news_service is not None:
            await news_service.close()
        if ai_service is not None:
            await ai_service.close()
        if top20_service is not None:
            await top20_service.close()
        if collector is not None:
            await collector.close()
        if market_event_service is not None:
            await market_event_service.close()
        if account_query_manager is not None:
            await account_query_manager.close()
        if broker is not None:
            await broker.close()
        if credential_vault is not None:
            credential_vault.close()
        store.close()

    @asynccontextmanager
    async def lifespan(_app: Any):
        try:
            if credential_runtime is not None:
                await credential_runtime.start()
            async with service_lifespan(_app):
                yield
        finally:
            if credential_runtime is not None:
                await credential_runtime.close()
            if mock_automation_supervisor is not None:
                await mock_automation_supervisor.close()
            if real_owner is not None:
                await real_owner.close()
            if mock_owner is not None:
                await mock_owner.close()
            elif mock_bundle is not None:
                await mock_bundle.close()
            # Also release the vault when startup fails before service_lifespan yields.
            if credential_vault is not None:
                credential_vault.close()
                store.close()

    app = FastAPI(title="Kiwoom Monitor Personal Server", version="1", lifespan=lifespan)
    app.state.credential_statuses = credential_statuses
    app.state.credential_runtime = credential_runtime
    app.state.realtime_hub = realtime_hub
    app.state.market_event_service = market_event_service
    app.state.external_market_collector = external_market_service
    app.state.candidate_monitor = candidate_monitor
    app.state.mock_account_monitor = mock_account_monitor
    app.state.mock_account_realtime = mock_account_realtime
    app.state.mock_order_gateway = mock_order_gateway
    app.state.mock_account_bundle = mock_bundle
    app.state.mock_credential_owner = mock_owner
    app.state.mock_automation_supervisor = mock_automation_supervisor
    app.state.real_credential_owner = real_owner
    app.state.mock_account_startup_error = ""
    app.state.verified_account_bindings = ()

    class QueryRequest(BaseModel):
        api_id: str = Field(min_length=7, max_length=7)
        path: str
        body: dict[str, Any] = Field(default_factory=dict)
        cont_yn: str = "N"
        next_key: str = ""

    class AccountQueryRequest(BaseModel):
        api_id: str = Field(pattern=r"^(kt00007|kt00015)$")
        path: str = Field(min_length=1, max_length=100)
        body: dict[str, Any] = Field(default_factory=dict)
        batch_id: str = Field(default="", max_length=64)
        page_index: int = Field(default=0, ge=0, le=20)
        next_key: str = Field(default="", max_length=500)

    class MockOrderRequest(BaseModel):
        request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
        symbol: str = Field(pattern=r"^\d{6}$")
        side: str = Field(pattern=r"^(BUY|SELL)$")
        quantity: int = Field(ge=1, le=1_000_000)
        limit_price: int = Field(ge=1)
        expires_seconds: int = Field(default=120, ge=10, le=600)

    class MockCancelRequest(BaseModel):
        quantity: int = Field(default=0, ge=0, le=1_000_000)

    class AccountTarget(BaseModel):
        model_config = ConfigDict(extra="forbid")
        broker: str = Field(pattern=r"^kiwoom$")
        environment: str = Field(pattern=r"^(mock|real)$")
        account_ref: str = Field(min_length=36, max_length=36)

    class ScopedAccountQueryRequest(AccountQueryRequest):
        model_config = ConfigDict(extra="forbid")
        account_scope: AccountTarget
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        expected_binding_revision: int = Field(strict=True, ge=1)

    class ScopedMockOrderRequest(MockOrderRequest):
        model_config = ConfigDict(extra="forbid")
        account_scope: AccountTarget
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        expected_binding_revision: int = Field(strict=True, ge=1)
        quantity: int = Field(strict=True, ge=1, le=1_000_000)
        limit_price: int = Field(strict=True, ge=1)

    class ScopedMockCancelRequest(MockCancelRequest):
        model_config = ConfigDict(extra="forbid")
        account_scope: AccountTarget
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        expected_binding_revision: int = Field(strict=True, ge=1)
        quantity: int = Field(default=0, strict=True, ge=0, le=1_000_000)

    class DocumentInput(BaseModel):
        owner: str = Field(max_length=200)
        key: str = Field(min_length=1, max_length=2000)
        document: dict[str, Any]
        effective_at: str | None = Field(default=None, max_length=100)
        origin_device: str | None = Field(default=None, max_length=200)
        collector_id: str | None = Field(default=None, max_length=200)
        collection_scope: str | None = Field(default=None, max_length=200)

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

    class MockAutomationCandidatePublicationRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_ref: str = Field(min_length=36, max_length=36)
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        expected_binding_revision: int = Field(strict=True, ge=1)
        package: dict[str, Any]
        eligibility_policy: dict[str, Any]
        eligibility_receipt: dict[str, Any]

    class MockAutomationSpecPublicationRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_ref: str = Field(min_length=36, max_length=36)
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        expected_binding_revision: int = Field(strict=True, ge=1)
        shadow_event_id: str = Field(min_length=1, max_length=128)
        forward_profile: dict[str, Any]
        stage_revisions: list[dict[str, Any]] = Field(min_length=3, max_length=3)
        operating_spec: dict[str, Any]

    class MockAutomationStartRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_ref: str = Field(min_length=36, max_length=36)
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        spec_id: str = Field(min_length=1, max_length=128)
        expected_settings_revision: int = Field(strict=True, ge=1)
        credential_revision: int = Field(strict=True, ge=1)

    class MockAutomationControlRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_ref: str = Field(min_length=36, max_length=36)
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        spec_id: str = Field(min_length=1, max_length=128)
        expected_control_revision: int = Field(strict=True, ge=1)
        reason: str = Field(min_length=1, max_length=500)

    class MockAutomationResumeRequest(MockAutomationControlRequest):
        expected_settings_revision: int = Field(strict=True, ge=1)
        credential_revision: int = Field(strict=True, ge=1)

    class OperationalSettingsUpdate(BaseModel):
        expected_revision: int | None = Field(default=None, ge=0)
        ai_provider: str | None = Field(default=None, pattern=r"^(none|openai|gemini|claude)$")
        ai_model: str | None = Field(default=None, max_length=100)
        ai_daily_limit: int | None = Field(default=None, ge=0, le=1_000_000)
        news_refresh_seconds: int | None = Field(default=None, ge=60, le=86_400)
        dart_enabled: bool | None = None
        news_query_set_enabled: bool | None = None
        news_query_set: list[str] | None = Field(default=None, max_length=50)
        news_query_set_refresh_seconds: int | None = Field(default=None, ge=60, le=86_400)
        news_processing_excluded_providers: list[str] | None = Field(default=None, max_length=100)
        external_market_enabled: bool | None = Field(default=None, strict=True)
        external_market_poll_seconds: int | None = Field(default=None, ge=60, le=86_400, strict=True)
        external_market_auto_roll_enabled: bool | None = Field(default=None, strict=True)
        external_market_roll_confirmations: int | None = Field(default=None, ge=1, le=100, strict=True)
        hot_cohort_condition_enabled: bool | None = Field(default=None, strict=True)
        hot_cohort_condition_name: str | None = Field(default=None, max_length=120, strict=True)
        hot_cohort_condition_substring: str | None = Field(default=None, max_length=120, strict=True)
        shadow_candidate_enabled: bool | None = None
        shadow_candidate_config: dict[str, Any] | None = None
        shadow_candidate_poll_seconds: float | None = Field(default=None, ge=0.5, le=60)
        shadow_candidate_universe_max_age_seconds: int | None = Field(default=None, ge=1, le=3600)

    def authorize(authorization: str = Header(default="")) -> None:
        scheme, _, supplied = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not hmac.compare_digest(supplied, active.access_token):
            raise HTTPException(status_code=401, detail="유효한 서버 접속 토큰이 필요합니다.")

    def valid_token(supplied: str) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied, active.access_token)

    install_credential_routes(app, credential_runtime, authorize, active.credential_trusted_proxies, mock_owner, real_owner)

    @app.get("/api/v1/settings/market-profile", dependencies=[Depends(authorize)])
    async def market_profile_settings() -> dict[str, object]:
        try:
            document = await asyncio.to_thread(store.load_market_profile_settings)
        except ValueError:
            raise HTTPException(409, detail="MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED") from None
        # Persisted role intent is not proof of a completed live transport change.
        return {"settings": document, "applied_revision":
                real_owner.applied_market_role_revision() if real_owner is not None else None}

    @app.put("/api/v1/settings/market-profile", dependencies=[Depends(authorize)])
    async def put_market_profile(values: dict[str, Any]) -> dict[str, object]:
        from .credential_runtime import CredentialOperationError
        from .credential_store import CredentialStoreError
        if set(values) != {"market_profile_id", "expected_revision", "expected_binding_revision"}:
            raise HTTPException(422, detail="MARKET_PROFILE_SETTINGS_INVALID")
        if real_owner is None:
            raise HTTPException(503, detail="PROFILE_RUNTIME_NOT_READY")
        try:
            await real_owner.change_market_role(values["market_profile_id"],
                expected_revision=values["expected_revision"],
                expected_binding_revision=values["expected_binding_revision"])
        except CredentialOperationError as error:
            raise HTTPException(422 if error.code == "MARKET_PROFILE_SETTINGS_INVALID" else error.status,
                                detail=error.code) from None
        except CredentialStoreError:
            raise HTTPException(503, detail="PROFILE_RUNTIME_NOT_READY") from None
        except ValueError as error:
            code = str(error) if str(error) in {"ACCOUNT_CONTEXT_MISMATCH", "ACCOUNT_IDENTITY_UNVERIFIED",
                "ACCOUNT_SETTINGS_RECOVERY_REQUIRED", "MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED"} else "MARKET_ROLE_CHANGE_FAILED"
            raise HTTPException(409, detail=code) from None
        return await market_profile_settings()

    @app.get("/api/v1/settings/accounts/{account_ref}", dependencies=[Depends(authorize)])
    async def account_settings(
        account_ref: str,
        environment: str = Query(pattern=r"^(mock|real)$"),
        broker_name: str = Query(default="kiwoom", alias="broker", pattern=r"^kiwoom$"),
    ) -> dict[str, object]:
        try:
            document = await asyncio.to_thread(store.load_account_settings, {
                "broker": broker_name, "environment": environment, "account_ref": account_ref,
            })
        except ValueError as error:
            code = str(error)
            status = (404 if code == "ACCOUNT_IDENTITY_UNVERIFIED" else
                      409 if code == "ACCOUNT_SETTINGS_RECOVERY_REQUIRED" else 400)
            raise HTTPException(status_code=status, detail=code) from None
        # DB preferences are not proof of an admitted runtime.
        result = {"settings": document, "applied_revision": (
            real_owner.applied_settings_revision(document["scope"]) if real_owner and environment == "real" else
            mock_owner.applied_settings_revision(document["scope"]) if mock_owner else None
        )}
        if real_owner and environment == "real":
            result["monitor_status"] = real_owner.account_monitor_status(document["scope"])
        return result

    async def mock_order_document(record: Any, gateway: Any) -> dict[str, object]:
        intent = record.intent
        events = await asyncio.to_thread(gateway.events, intent.intent_id)
        return {
            "intent_id": intent.intent_id,
            "request_id": intent.decision_id.removeprefix("manual:"),
            "run_id": intent.run_id,
            "environment": intent.environment,
            "symbol": intent.symbol,
            "venue": intent.venue,
            "side": intent.side.value,
            "quantity": intent.quantity,
            "order_type": intent.order_type.value,
            "limit_price": intent.limit_price,
            "policy_version": intent.policy_version,
            "state": record.state.value,
            "broker_order_id": record.broker_order_id,
            "filled_quantity": record.filled_quantity,
            "created_at": intent.created_at.isoformat(),
            "expires_at": intent.expires_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "events": list(events),
        }

    @app.get("/health")
    async def health() -> dict[str, object]:
        document = health_document()
        document["server_build"] = SERVER_BUILD
        document["realtime_connection"] = collector.credential_connection_status() if collector is not None else None
        document["mock_account_available"] = (
            any(bundle.monitor._task is not None for bundle in mock_owner._bundles.values())
            if mock_owner is not None else mock_account_monitor is not None
        )
        if mock_owner is not None and mock_owner.errors.get("nas-mock-default"):
            document["mock_account_error"] = mock_owner.errors["nas-mock-default"]
        if _app_error := str(getattr(app.state, "mock_account_startup_error", "")):
            document["mock_account_error"] = _app_error
        return document

    @app.get("/api/v1/capabilities", dependencies=[Depends(authorize)])
    def capabilities() -> dict[str, object]:
        document = ServerCapabilities(
            kiwoom_rest=broker is not None, realtime_stream=collector is not None,
            news_archive=True, ai_analysis_archive=True, themes=True,
            trade_journal=True, shared_settings=True,
            runtime_credentials_v1=credential_runtime is not None,
            multi_account_query_v3=mock_owner is not None or (account_query_manager is not None and main_binding is not None),
            scoped_mock_orders_v2=mock_owner is not None,
            account_contexts_v3=mock_owner is not None or (account_query_manager is not None and main_binding is not None),
            execution_event_read_v1=mock_owner is not None,
            account_query_v2=account_query_manager is not None and main_binding is not None,
            journal_v2_sync=True,
            journal_news_links_v2=True,
            combined_minute_bars=True,
            trade_value_comparisons=True,
            planned_reconnect_v1=collector is not None,
            mock_automation_candidate_publish_v1=True,
            mock_automation_candidate_read_v1=True,
            mock_automation_spec_publish_v1=True,
            mock_automation_runtime_v1=mock_automation_supervisor is not None,
        ).as_document()
        document["realtime_connection"] = collector.credential_connection_status() if collector is not None else None
        return document

    @app.post("/api/v1/mock/orders", dependencies=[Depends(authorize)])
    async def submit_mock_order(command: MockOrderRequest) -> dict[str, object]:
        bundle = mock_owner.bundle() if mock_owner is not None else None
        gateway = bundle.gateway if bundle is not None else (mock_order_gateway if mock_owner is None else None)
        if gateway is None:
            raise HTTPException(status_code=503, detail="모의주문 전송이 활성화되지 않았습니다.")
        from kiwoom_monitor.domain.order_contract import OrderSide
        try:
            record = await gateway.submit_limit(
                request_id=command.request_id,
                symbol=command.symbol,
                side=OrderSide(command.side),
                quantity=command.quantity,
                limit_price=command.limit_price,
                expires_seconds=command.expires_seconds,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return await mock_order_document(record, gateway)

    @app.get("/api/v1/mock/orders/{intent_id}", dependencies=[Depends(authorize)])
    async def get_mock_order(intent_id: str) -> dict[str, object]:
        bundle = mock_owner.bundle() if mock_owner is not None else None
        gateway = bundle.gateway if bundle is not None else (mock_order_gateway if mock_owner is None else None)
        if gateway is None:
            raise HTTPException(status_code=503, detail="모의주문 전송이 활성화되지 않았습니다.")
        try:
            record = await asyncio.to_thread(gateway.load, intent_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return await mock_order_document(record, gateway)

    @app.post("/api/v1/mock/orders/{intent_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel_mock_order(
        intent_id: str, command: MockCancelRequest,
    ) -> dict[str, object]:
        bundle = mock_owner.bundle() if mock_owner is not None else None
        gateway = bundle.gateway if bundle is not None else (mock_order_gateway if mock_owner is None else None)
        if gateway is None:
            raise HTTPException(status_code=503, detail="모의주문 전송이 활성화되지 않았습니다.")
        try:
            record = await gateway.cancel(intent_id, command.quantity)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return await mock_order_document(record, gateway)

    def selected_account(scope, profile_id, revision):
        from kiwoom_monitor.domain.order_contract import AccountScope, AccountEnvironment
        try:
            target = AccountScope(scope["broker"], AccountEnvironment(scope["environment"]), scope["account_ref"])
        except (ValueError, KeyError):
            raise HTTPException(400, detail="ACCOUNT_SCOPE_INVALID") from None
        bundle = mock_owner.bundle(profile_id) if mock_owner is not None else None
        if bundle is None and real_owner is not None:
            bundle = real_owner.bundle(profile_id)
        binding = bundle.binding if bundle is not None else (
            main_binding if main_binding is not None and main_binding.credential_profile_id == profile_id else None)
        if binding is None:
            raise HTTPException(503, detail="PROFILE_RUNTIME_NOT_READY")
        if binding.scope != target or binding.binding_revision != revision:
            raise HTTPException(409, detail="ACCOUNT_CONTEXT_MISMATCH")
        manager = bundle.account_queries if bundle is not None else account_query_manager
        return binding, manager, bundle

    def selected_mock(account_ref, scope, profile_id, revision, *, orders=False):
        if scope["environment"] != "mock" or scope["account_ref"] != account_ref:
            raise HTTPException(409, detail="ACCOUNT_CONTEXT_MISMATCH")
        binding, _, bundle = selected_account(scope, profile_id, revision)
        if bundle is None or (orders and bundle.gateway is None):
            raise HTTPException(503, detail="MOCK_ORDER_TRANSPORT_DISABLED")
        return binding, bundle

    def scoped_context(binding):
        return {**binding.scope.to_dict(), "credential_profile_id": binding.credential_profile_id,
            "binding_revision": binding.binding_revision, "verified_at": binding.verified_at.isoformat(),
            "verification_method": binding.verification_method}

    @app.get("/api/v3/kiwoom/accounts", dependencies=[Depends(authorize)])
    def account_contexts():
        bindings = list(mock_owner.account_bindings()) if mock_owner is not None else []
        if real_owner is not None:
            bindings.extend(real_owner.account_bindings())
        if account_query_manager is not None and main_binding is not None:
            if main_binding not in bindings: bindings.insert(0, main_binding)
        labels = {
            profile["profile_id"]: str(profile.get("label", "")).strip()
            for profile in store.list_credential_profiles()
            if profile.get("lifecycle_state") != "archived"
        }
        accounts = []
        for binding in bindings:
            document = scoped_context(binding)
            label = labels.get(binding.credential_profile_id, "")
            if label:
                document["display_label"] = label
            accounts.append(document)
        return {"accounts": accounts}

    async def scoped_order_record(bundle, intent_id):
        record = await asyncio.to_thread(bundle.repository.load, intent_id)
        if (record is None or record.intent.environment != "mock"
                or record.intent.account_ref != bundle.account_ref or record.intent.run_id != bundle.run_id):
            raise HTTPException(404, detail="MOCK_ORDER_NOT_FOUND")
        return record

    @app.post("/api/v2/mock/accounts/{account_ref}/orders", dependencies=[Depends(authorize)])
    async def submit_scoped_mock_order(account_ref: str, command: ScopedMockOrderRequest):
        from kiwoom_monitor.domain.order_contract import OrderSide
        binding, bundle = selected_mock(account_ref, command.account_scope.model_dump(),
            command.credential_profile_id, command.expected_binding_revision, orders=True)
        gateway = bundle.gateway
        try:
            record = await gateway.submit_limit(request_id=command.request_id, symbol=command.symbol,
                side=OrderSide(command.side), quantity=command.quantity, limit_price=command.limit_price,
                expires_seconds=command.expires_seconds, scoped=True)
        except (ValueError, KeyError):
            raise HTTPException(409, detail="MOCK_ORDER_REQUEST_CONFLICT") from None
        except RuntimeError:
            raise HTTPException(503, detail="MOCK_ORDER_UNAVAILABLE") from None
        return {**await mock_order_document(record, gateway), "context": scoped_context(binding)}

    @app.get("/api/v2/mock/accounts/{account_ref}/orders/{intent_id}", dependencies=[Depends(authorize)])
    async def get_scoped_mock_order(account_ref: str, intent_id: str,
        credential_profile_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,96}$"),
        expected_binding_revision: int = Query(ge=1),
        environment: str = Query(pattern=r"^mock$"),
        broker_name: str = Query(default="kiwoom", alias="broker", pattern=r"^kiwoom$")):
        binding, bundle = selected_mock(account_ref,
            {"broker": broker_name, "environment": environment, "account_ref": account_ref},
            credential_profile_id, expected_binding_revision)
        record = await scoped_order_record(bundle, intent_id)
        return {**await mock_order_document(record, bundle.repository), "context": scoped_context(binding)}

    @app.get("/api/v2/mock/accounts/{account_ref}/execution-events", dependencies=[Depends(authorize)])
    async def get_scoped_mock_execution_events(
        account_ref: str,
        credential_profile_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,96}$"),
        expected_binding_revision: int = Query(ge=1),
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=1000),
        environment: str = Query(default="mock", pattern=r"^mock$"),
        broker_name: str = Query(default="kiwoom", alias="broker", pattern=r"^kiwoom$"),
    ):
        binding, bundle = selected_mock(
            account_ref,
            {"broker": broker_name, "environment": environment, "account_ref": account_ref},
            credential_profile_id,
            expected_binding_revision,
        )
        page = await asyncio.to_thread(
            bundle.repository.account_events,
            "mock",
            account_ref,
            after_sequence=after_sequence,
            limit=limit,
        )
        return {**page.to_dict(), "context": scoped_context(binding)}

    @app.post("/api/v2/mock/accounts/{account_ref}/orders/{intent_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel_scoped_mock_order(account_ref: str, intent_id: str, command: ScopedMockCancelRequest):
        binding, bundle = selected_mock(account_ref, command.account_scope.model_dump(),
            command.credential_profile_id, command.expected_binding_revision, orders=True)
        await scoped_order_record(bundle, intent_id)
        gateway = bundle.gateway
        try:
            record = await gateway.cancel(intent_id, command.quantity)
        except KeyError:
            raise HTTPException(404, detail="MOCK_ORDER_NOT_FOUND") from None
        except ValueError:
            raise HTTPException(409, detail="MOCK_ORDER_CANCEL_CONFLICT") from None
        except RuntimeError:
            raise HTTPException(503, detail="MOCK_ORDER_UNAVAILABLE") from None
        return {**await mock_order_document(record, gateway), "context": scoped_context(binding)}

    def operational_document() -> dict[str, object]:
        revision = int(operational["revision"])
        condition = market_event_service.condition_status() if market_event_service is not None else None
        return {
            **operational, "applied_revision": applied_operational_revision,
            "apply_status": "ACTIVE" if revision == applied_operational_revision and
                (condition is None or condition["apply_status"] != "RECOVERY_REQUIRED") else "RECOVERY_REQUIRED",
            "condition_runtime_supported": market_event_service is not None,
            "condition_status": condition,
        }

    @app.get("/api/v1/settings/operations", dependencies=[Depends(authorize)])
    async def get_operational_settings() -> dict[str, object]:
        return operational_document()

    @app.get("/api/v1/diagnostics/resources", dependencies=[Depends(authorize)])
    async def diagnostics_resources() -> dict[str, object]:
        database_size, storage_categories = await asyncio.gather(
            asyncio.to_thread(store.storage_size_bytes),
            asyncio.to_thread(store.storage_breakdown),
        )
        return {
            **resource_usage("/app/data", database_size),
            "storage_categories": storage_categories,
            "storage_category_bytes_are_estimates": True,
            "retention_policy": {
                "mode": "unlimited",
                "automatic_deletion_enabled": False,
            },
        }

    @app.put("/api/v1/settings/operations", dependencies=[Depends(authorize)])
    async def put_operational_settings(values: OperationalSettingsUpdate) -> dict[str, object]:
        nonlocal candidate_monitor, applied_operational_revision
        async with candidate_settings_lock:
            changes = values.model_dump(exclude_none=True, exclude={"expected_revision"})
            if values.expected_revision is not None and values.expected_revision != int(operational["revision"]):
                raise HTTPException(status_code=409, detail="OPERATIONAL_SETTINGS_REVISION_CONFLICT")
            proposed = {**operational, **changes}
            condition_fields = {"hot_cohort_condition_enabled", "hot_cohort_condition_name", "hot_cohort_condition_substring"}
            if condition_fields.intersection(changes) and market_event_service is None:
                raise HTTPException(status_code=422, detail="CONDITION_RUNTIME_NOT_READY")
            if proposed["hot_cohort_condition_enabled"] and not (
                    str(proposed["hot_cohort_condition_name"]).strip() or str(proposed["hot_cohort_condition_substring"]).strip()):
                raise HTTPException(status_code=422, detail="CONDITION_SELECTION_REQUIRED")
            condition_recovery = market_event_service is not None and market_event_service.condition_status()["apply_status"] == "RECOVERY_REQUIRED"
            condition_changed = condition_recovery or any(
                name in changes and proposed[name] != operational[name] for name in condition_fields)
            external_fields = {"external_market_enabled", "external_market_poll_seconds",
                               "external_market_auto_roll_enabled", "external_market_roll_confirmations"}
            if proposed["external_market_enabled"] and external_market_service is None:
                raise HTTPException(status_code=422, detail="EXTERNAL_MARKET_SYMBOLS_REQUIRED")
            shadow_fields = {
                "shadow_candidate_enabled", "shadow_candidate_config",
                "shadow_candidate_poll_seconds",
                "shadow_candidate_universe_max_age_seconds",
            }
            recovery_required = int(operational["revision"]) != applied_operational_revision
            condition_changed = condition_changed or (recovery_required and market_event_service is not None)
            external_changed = recovery_required or any(
                name in changes and proposed[name] != operational[name] for name in external_fields)
            shadow_changed = recovery_required or any(
                name in changes and proposed[name] != operational[name] for name in shadow_fields
            )
            replacement = candidate_monitor
            if shadow_changed and bool(proposed["shadow_candidate_enabled"]):
                try:
                    replacement = await asyncio.to_thread(
                        CandidateMonitor.from_json,
                        store,
                        json.dumps(proposed["shadow_candidate_config"]),
                        poll_seconds=float(proposed["shadow_candidate_poll_seconds"]),
                        universe_max_age_seconds=int(
                            proposed["shadow_candidate_universe_max_age_seconds"]
                        ),
                    )
                except (TypeError, ValueError) as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
            elif shadow_changed:
                replacement = None
            changed = any(proposed[name] != operational.get(name) for name in changes)
            if not changed and not recovery_required and not condition_recovery:
                return operational_document()
            proposed["revision"] = int(operational["revision"]) + (1 if changed else 0)
            try:
                await asyncio.to_thread(store.upsert_documents, "server_operational_settings", [{
                    "owner": "global", "key": "current", "document": proposed,
                }])
            except Exception as error:
                logger.exception("operational settings persistence failed")
                raise HTTPException(status_code=503, detail="OPERATIONAL_SETTINGS_SAVE_FAILED") from error
            operational.update(proposed)
            try:
                if condition_changed:
                    market_event_service.update_operational_settings(
                        enabled=bool(proposed["hot_cohort_condition_enabled"]),
                        exact_name=str(proposed["hot_cohort_condition_name"]),
                        substring=str(proposed["hot_cohort_condition_substring"]),
                    )
                if external_changed and external_market_service is not None:
                    await external_market_service.update_operational_settings(
                        enabled=bool(proposed["external_market_enabled"]),
                        poll_seconds=int(proposed["external_market_poll_seconds"]),
                        auto_roll_enabled=bool(proposed["external_market_auto_roll_enabled"]),
                        roll_confirmations=int(proposed["external_market_roll_confirmations"]),
                    )
                if ai_service is not None:
                    ai_service.update_operational_settings(
                        provider=str(proposed["ai_provider"]), model=str(proposed["ai_model"]),
                        daily_limit=int(proposed["ai_daily_limit"]),
                    )
                if news_service is not None:
                    news_service.update_operational_settings(
                        refresh_seconds=int(proposed["news_refresh_seconds"]),
                        dart_enabled=bool(proposed["dart_enabled"]),
                        query_set_enabled=bool(proposed["news_query_set_enabled"]),
                        query_set=tuple(str(value) for value in proposed["news_query_set"]),
                        query_set_refresh_seconds=int(proposed["news_query_set_refresh_seconds"]),
                        processing_excluded_providers=tuple(
                            str(value) for value in proposed["news_processing_excluded_providers"]
                        ),
                    )
                if shadow_changed:
                    previous = candidate_monitor
                    if previous is not None:
                        await previous.close()
                    candidate_monitor = replacement
                    app.state.candidate_monitor = replacement
                    if replacement is not None:
                        await replacement.start()
                applied_operational_revision = int(proposed["revision"])
            except Exception as error:
                logger.exception("operational settings runtime apply failed")
                raise HTTPException(status_code=503, detail="OPERATIONAL_SETTINGS_APPLY_PENDING") from error
            return operational_document()

    @app.post("/api/v1/kiwoom/query", dependencies=[Depends(authorize)])
    async def kiwoom_query(query: QueryRequest) -> dict[str, object]:
        from .rest_broker import ACCOUNT_RECOVERY_ENDPOINTS
        if query.api_id in ACCOUNT_RECOVERY_ENDPOINTS:
            raise HTTPException(400, detail="ACCOUNT_QUERY_SCOPE_REQUIRED")
        if query.cont_yn == "N":
            archived = await asyncio.to_thread(
                _stored_market_response, store, query.api_id, query.body,
            )
            if archived is not None:
                return {
                    "payload": archived, "has_next": False, "next_key": "",
                    "cache_hit": False, "archive_hit": True,
                }
        if broker is None:
            raise HTTPException(status_code=503, detail="서버에 키움 API 키가 설정되지 않았습니다.")
        if collector is not None and getattr(broker, "_credential_paused", False):
            connection_status = collector.credential_connection_status()
            if connection_status["planned_reconnect"]:
                raise HTTPException(503, detail={"code": "REALTIME_RECONNECTING", "connection_status": connection_status})
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

    @app.post("/api/v2/kiwoom/account-query", dependencies=[Depends(authorize)])
    async def account_query(query: AccountQueryRequest) -> dict[str, object]:
        if account_query_manager is None or main_binding is None:
            raise HTTPException(status_code=503, detail="ACCOUNT_IDENTITY_UNVERIFIED")
        try:
            return await account_query_manager.query(
                api_id=query.api_id, path=query.path, body=query.body,
                batch_id=query.batch_id, page_index=query.page_index,
                next_key=query.next_key,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/v3/kiwoom/account-query", dependencies=[Depends(authorize)])
    async def scoped_account_query(query: ScopedAccountQueryRequest):
        binding, manager, _ = selected_account(query.account_scope.model_dump(),
            query.credential_profile_id, query.expected_binding_revision)
        if manager is None: raise HTTPException(503, detail="PROFILE_RUNTIME_NOT_READY")
        try:
            result = await manager.query(api_id=query.api_id, path=query.path, body=query.body,
                batch_id=query.batch_id, page_index=query.page_index, next_key=query.next_key)
        except ValueError as error:
            code = str(error)
            if code not in {"ACCOUNT_QUERY_CURSOR_EXPIRED", "ACCOUNT_QUERY_BODY_MISMATCH", "ACCOUNT_QUERY_CURSOR_MISMATCH"}:
                code = "ACCOUNT_QUERY_INVALID"
            raise HTTPException(409, detail=code) from None
        except RuntimeError as error:
            code = str(error)
            if code in {"ACCOUNT_QUERY_BUSY", "ACCOUNT_QUERY_CLOSED"}:
                raise HTTPException(503, detail=code) from None
            raise HTTPException(409, detail="ACCOUNT_CONTEXT_MISMATCH") from None
        except Exception:
            raise HTTPException(502, detail="ACCOUNT_QUERY_UNAVAILABLE") from None
        if result["context"] != scoped_context(binding):
            raise HTTPException(409, detail="ACCOUNT_CONTEXT_MISMATCH")
        return result

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

    @app.get("/api/v1/news/history/{kind}", dependencies=[Depends(authorize)])
    async def news_history(
        kind: str,
        target: str = Query(default="", max_length=200),
        identity: str = Query(default="", max_length=2000),
        as_of: float | None = Query(default=None, ge=0.0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        if kind not in {"article", "body", "ai", "event", "membership"}:
            raise HTTPException(status_code=404, detail="지원하지 않는 뉴스 이력 종류입니다.")
        values = await asyncio.to_thread(
            store.load_news_history, kind, target=target, identity=identity,
            available_at=as_of, limit=limit,
        )
        return {"kind": kind, "target": target, "as_of": as_of,
                "known": bool(values), "revisions": values}

    @app.get("/api/v1/news/sources", dependencies=[Depends(authorize)])
    async def news_sources(
        source_id: str = Query(default="", max_length=200),
        days: int = Query(default=7, ge=1, le=31),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            store.load_news_source_diagnostics, source_id=source_id, days=days, limit=limit,
        )

    @app.get("/api/v1/market/minute-bars", dependencies=[Depends(authorize)])
    async def minute_bars(
        code: str = Query(min_length=6, max_length=12),
        trading_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        market: str = Query(default="", pattern=r"^(|KRX|NXT|SOR|COMBINED)$"),
    ) -> dict[str, object]:
        requested_market = market.upper()
        values = await asyncio.to_thread(
            store.load_minute_bars, code, trading_date,
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
            rows
            and rows[0].get("document", {}).get("kind") == "minute"
            and rows[0].get("document", {}).get("window_closed") is True
            and rows[0].get("document", {}).get("session_finalized") is True
            for rows in coverage_documents
        )
        return {
            "code": code, "trading_date": trading_date, "market": requested_market, "bars": values,
            "coverage": {"complete": coverage_complete, "markets": list(coverage_markets)},
        }

    @app.get("/api/v1/market/recent-minute-bars", dependencies=[Depends(authorize)])
    async def recent_minute_bars(
        code: str = Query(min_length=6, max_length=12),
        end_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        market: str = Query(default="", pattern=r"^(|KRX|NXT|SOR|COMBINED)$"),
        trading_days: int = Query(default=2, ge=1, le=5),
    ) -> dict[str, object]:
        """TR 없이 중앙 DB에서 마지막 N개 실제 거래일 분봉을 반환한다."""
        end = datetime.fromisoformat(end_date).date()
        values: list[dict[str, Any]] = []
        found_days: set[str] = set()
        for offset in range(31):
            day = (end - timedelta(days=offset)).isoformat()
            requested_market = market.upper()
            rows = await asyncio.to_thread(
                store.load_minute_bars, code, day,
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

    @app.get("/api/v1/market/latest-market-caps", dependencies=[Depends(authorize)])
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

    @app.get("/api/v1/market/trade-value-comparisons", dependencies=[Depends(authorize)])
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

    @app.get("/api/v1/market/events", dependencies=[Depends(authorize)])
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

    @app.get("/api/v1/research/observations", dependencies=[Depends(authorize)])
    async def research_observations(
        start: datetime = Query(),
        end: datetime = Query(),
        kinds: str = Query(min_length=1, max_length=64),
        subject: str = Query(default="", max_length=32),
        cursor: int = Query(default=0, ge=0),
        watermark: str = Query(default="", max_length=64),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> dict[str, object]:
        requested_kinds = tuple(dict.fromkeys(
            value.strip() for value in kinds.split(",") if value.strip()
        ))
        try:
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("research export timestamps must be timezone-aware")
            if not watermark:
                if cursor:
                    raise ValueError("cursor requires a fixed watermark")
                manifest = await asyncio.to_thread(
                    store.create_observation_export, start, end, requested_kinds, subject,
                )
                watermark = str(manifest["fixed_watermark"])
            page = await asyncio.to_thread(
                store.load_observation_export_page, watermark, cursor, limit,
            )
        except ValueError as error:
            status = 404 if "unknown research export watermark" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error
        manifest = page["manifest"]
        expected_range = manifest.get("captured_range", {})
        if (
            list(requested_kinds) != manifest.get("kinds")
            or subject != manifest.get("subject")
            or start.astimezone(timezone.utc).isoformat() != expected_range.get("start")
            or end.astimezone(timezone.utc).isoformat() != expected_range.get("end")
        ):
            raise HTTPException(status_code=409, detail="watermark parameters do not match its fixed dataset")
        return page

    @app.get("/api/v1/research/candidates", dependencies=[Depends(authorize)])
    async def research_candidates(
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        page = await asyncio.to_thread(store.load_shadow_candidates, after_sequence, limit)
        now = datetime.now(timezone.utc)
        for event in page["events"]:
            try:
                expires_at = datetime.fromisoformat(str(event.get("expires_at", "")))
                expired = expires_at.astimezone(timezone.utc) < now
            except ValueError:
                expired = True
            event["status"] = "EXPIRED" if expired else "ACTIVE"
        page["quality"] = (
            candidate_monitor.quality if candidate_monitor is not None
            else {"status": "DISABLED", "reason": "shadow_candidate_generation_disabled"}
        )
        return page

    @app.post(
        "/api/v1/research/mock-automation-candidates",
        dependencies=[Depends(authorize)],
    )
    async def publish_mock_automation_candidate(
        request: MockAutomationCandidatePublicationRequest,
    ) -> dict[str, object]:
        from kiwoom_monitor.application.mock_automation_candidate import (
            candidate_package_from_dict,
            eligibility_policy_from_dict,
            eligibility_receipt_from_dict,
            validate_publication_size,
        )
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
            ForwardEvaluationRepository,
        )
        from kiwoom_monitor.application.research_implementation import research_implementation_hash
        try:
            validate_publication_size(
                request.package, request.eligibility_policy, request.eligibility_receipt,
            )
            package = candidate_package_from_dict(request.package)
            policy = eligibility_policy_from_dict(request.eligibility_policy)
            receipt = eligibility_receipt_from_dict(request.eligibility_receipt)
            if receipt.account_ref != request.account_ref:
                raise ValueError("candidate publication account_ref conflict")
            session = package.candidate_spec.get("session_profile", {})
            profile = str(session.get("profile", "")) if isinstance(session, dict) else ""
            if package.scientific_implementation_hash != research_implementation_hash(profile):
                raise ValueError("candidate scientific implementation hash does not match this server")
            saved = await asyncio.to_thread(
                ForwardEvaluationRepository(store).publish_mock_automation_candidate,
                package, policy, receipt,
                credential_profile_id=request.credential_profile_id,
                expected_binding_revision=request.expected_binding_revision,
            )
        except (KeyError, TypeError, ValueError) as error:
            detail = str(error)
            status = 409 if any(token in detail for token in (
                "conflict", "current verified mock binding", "does not match this server",
                "immutable document",
            )) else 400
            raise HTTPException(status_code=status, detail=detail) from error
        return {
            "status": "saved" if saved else "unchanged",
            "package_hash": package.package_hash,
            "policy_id": policy.policy_id,
            "receipt_id": receipt.receipt_id,
            "eligibility_status": receipt.status.value,
            "orders_started": False,
        }

    def current_mock_binding(credential_profile_id: str) -> dict[str, Any] | None:
        bindings = [
            row for row in store.load_account_bindings()
            if str(row.get("credential_profile_id", "")) == credential_profile_id
            and str(row.get("broker", "")) == "kiwoom"
            and str(row.get("environment", "")) == "mock"
        ]
        return max(
            bindings, key=lambda row: int(row.get("binding_revision", 0)), default=None,
        )

    @app.get(
        "/api/v1/research/mock-automation-candidates/{account_ref}",
        dependencies=[Depends(authorize)],
    )
    async def list_mock_automation_candidates(
        account_ref: str,
        credential_profile_id: str = Query(
            min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$",
        ),
    ) -> dict[str, object]:
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
            ForwardEvaluationRepository,
        )
        binding = await asyncio.to_thread(current_mock_binding, credential_profile_id)
        if binding is None or str(binding.get("account_ref", "")) != account_ref:
            raise HTTPException(status_code=409, detail="current verified mock binding mismatch")
        repository = ForwardEvaluationRepository(store)
        try:
            publications = await asyncio.to_thread(
                repository.load_mock_automation_candidate_publications, account_ref,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return {
            "account_ref": account_ref,
            "binding": {
                "credential_profile_id": credential_profile_id,
                "broker": "kiwoom",
                "environment": "mock",
                "account_ref": account_ref,
                "binding_revision": int(binding["binding_revision"]),
                "verified_at": str(binding["verified_at"]),
                "verification_method": str(binding["verification_method"]),
            },
            "candidates": [{
                "package": package.to_dict(),
                "eligibility_policy": policy.to_dict(),
                "eligibility_receipt": receipt.to_dict(),
            } for package, policy, receipt in publications],
        }

    def find_shadow_candidate(event_id: str) -> dict[str, Any] | None:
        cursor = 0
        for _ in range(100):
            page = store.load_shadow_candidates(cursor, 1000)
            for event in page["events"]:
                if str(event.get("event_id", "")) == event_id:
                    return event
            if not page.get("has_more") or page.get("next_cursor") is None:
                return None
            cursor = int(page["next_cursor"])
        raise ValueError("shadow evidence search exceeded 100,000 events")

    @app.post(
        "/api/v1/research/mock-automation-specs",
        dependencies=[Depends(authorize)],
    )
    async def publish_mock_automation_spec(
        request: MockAutomationSpecPublicationRequest,
    ) -> dict[str, object]:
        from kiwoom_monitor.application.mock_automation_specification import (
            publish_ready_mock_automation_spec,
        )
        from kiwoom_monitor.domain.execution_activation import (
            forward_spec_from_dict,
            mock_automation_spec_from_dict,
            stage_revision_from_dict,
        )
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
            ForwardEvaluationRepository,
        )
        try:
            encoded = json.dumps(
                request.model_dump(), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > 256 * 1024:
                raise ValueError("mock automation spec publication is too large")
            profile = forward_spec_from_dict(request.forward_profile)
            stages = tuple(stage_revision_from_dict(value) for value in request.stage_revisions)
            spec = mock_automation_spec_from_dict(request.operating_spec)
            if (
                request.account_ref != spec.account_scope.account_ref
                or request.credential_profile_id != spec.credential_profile_id
                or request.expected_binding_revision != spec.binding_revision
            ):
                raise ValueError("mock automation spec request scope conflict")
            binding = await asyncio.to_thread(
                current_mock_binding, request.credential_profile_id,
            )
            if binding is None:
                raise ValueError("current verified mock binding is missing")
            shadow_event = await asyncio.to_thread(
                find_shadow_candidate, request.shadow_event_id,
            )
            if shadow_event is None:
                raise ValueError("stored shadow evidence event is missing")
            repository = ForwardEvaluationRepository(store)
            readiness, changed = await asyncio.to_thread(
                publish_ready_mock_automation_spec,
                repository,
                profile=profile,
                stage_revisions=stages,
                spec=spec,
                shadow_event=shadow_event,
                current_binding=binding,
            )
        except (KeyError, TypeError, ValueError) as error:
            detail = str(error)
            status = 409 if any(token in detail for token in (
                "conflict", "current verified mock binding", "stored strategy stage",
                "request scope",
            )) else 400
            raise HTTPException(status_code=status, detail=detail) from None
        return {
            "status": "saved" if changed else "unchanged",
            "spec_id": spec.spec_id,
            "readiness": readiness.status.value,
            "reasons": list(readiness.reasons),
            "orders_started": False,
        }

    @app.get(
        "/api/v1/research/mock-automation-specs/{account_ref}",
        dependencies=[Depends(authorize)],
    )
    async def list_mock_automation_specs(account_ref: str) -> dict[str, object]:
        from kiwoom_monitor.domain.execution_activation import assess_mock_automation_readiness
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
            ForwardEvaluationRepository,
        )
        repository = ForwardEvaluationRepository(store)
        specs = await asyncio.to_thread(repository.load_mock_automation_specs, account_ref)
        values = []
        for spec in specs:
            profile = await asyncio.to_thread(
                repository.load_profile, spec.strategy_ref, spec.forward_profile_id,
            )
            stage = await asyncio.to_thread(repository.latest_stage, spec.strategy_ref)
            readiness = (
                assess_mock_automation_readiness(spec, profile, strategy_stage=stage)
                if profile is not None else None
            )
            values.append({
                "spec": spec.to_dict(),
                "readiness": readiness.status.value if readiness is not None else "BLOCKED",
                "reasons": list(readiness.reasons) if readiness is not None else ["FORWARD_PROFILE_MISSING"],
            })
        return {"account_ref": account_ref, "specs": values}

    def require_mock_automation_supervisor():
        if mock_automation_supervisor is None:
            raise HTTPException(status_code=503, detail="MOCK_AUTOMATION_RUNTIME_UNAVAILABLE")
        return mock_automation_supervisor

    @app.get(
        "/api/v1/mock-automation/accounts/{account_ref}",
        dependencies=[Depends(authorize)],
    )
    async def mock_automation_status(
        account_ref: str,
        credential_profile_id: str = Query(
            min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$",
        ),
    ) -> dict[str, object]:
        supervisor = require_mock_automation_supervisor()
        return await asyncio.to_thread(
            supervisor.status,
            account_ref,
            credential_profile_id=credential_profile_id,
        )

    async def run_mock_automation_operation(operation: Any) -> dict[str, object]:
        from .credential_runtime import CredentialOperationError
        from .mock_automation_supervisor import MockAutomationSupervisorError
        try:
            return await operation
        except MockAutomationSupervisorError as error:
            raise HTTPException(status_code=error.status, detail=error.code) from None
        except CredentialOperationError as error:
            raise HTTPException(status_code=error.status, detail=error.code) from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    @app.post("/api/v1/mock-automation/start", dependencies=[Depends(authorize)])
    async def start_mock_automation(
        request: MockAutomationStartRequest,
    ) -> dict[str, object]:
        supervisor = require_mock_automation_supervisor()
        return await run_mock_automation_operation(supervisor.activate(
            credential_profile_id=request.credential_profile_id,
            account_ref=request.account_ref,
            spec_id=request.spec_id,
            expected_settings_revision=request.expected_settings_revision,
            credential_revision=request.credential_revision,
        ))

    @app.post("/api/v1/mock-automation/stop", dependencies=[Depends(authorize)])
    async def stop_mock_automation(
        request: MockAutomationControlRequest,
    ) -> dict[str, object]:
        supervisor = require_mock_automation_supervisor()
        return await run_mock_automation_operation(supervisor.stop(
            credential_profile_id=request.credential_profile_id,
            account_ref=request.account_ref,
            spec_id=request.spec_id,
            expected_control_revision=request.expected_control_revision,
            reason=request.reason,
        ))

    @app.post("/api/v1/mock-automation/resume", dependencies=[Depends(authorize)])
    async def resume_mock_automation_runtime(
        request: MockAutomationResumeRequest,
    ) -> dict[str, object]:
        supervisor = require_mock_automation_supervisor()
        return await run_mock_automation_operation(supervisor.resume(
            credential_profile_id=request.credential_profile_id,
            account_ref=request.account_ref,
            spec_id=request.spec_id,
            expected_control_revision=request.expected_control_revision,
            expected_settings_revision=request.expected_settings_revision,
            credential_revision=request.credential_revision,
            reason=request.reason,
        ))

    @app.get("/api/v1/market/snapshots/{kind}", dependencies=[Depends(authorize)])
    async def dataset_snapshots(
        kind: str, subject: str = Query(default="", max_length=32),
        limit: int = Query(default=100, ge=1, le=5000),
    ) -> dict[str, object]:
        allowed = {
            "ranking", "top20_membership", "top20_index", "market_state",
            "investor_flow", "program_flow", "new_high", "stock_fundamentals",
            "nxt_eligibility", "market_index_chart",
        }
        if kind not in allowed:
            raise HTTPException(status_code=404, detail="지원하지 않는 중앙 시장 자료입니다.")
        values = await asyncio.to_thread(store.load_dataset_snapshots, kind, subject, limit)
        return {"kind": kind, "subject": subject, "snapshots": values}

    @app.get("/api/v1/market/top20-statistics", dependencies=[Depends(authorize)])
    async def top20_statistics(start_date: str, end_date: str) -> dict[str, object]:
        try:
            start = datetime.fromisoformat(start_date).date()
            end = datetime.fromisoformat(end_date).date()
        except ValueError as error:
            raise HTTPException(status_code=422, detail="TOP20 통계 날짜 형식이 올바르지 않습니다.") from error
        if end < start or (end - start).days > 366:
            raise HTTPException(status_code=422, detail="TOP20 통계 범위는 최대 367일입니다.")
        result = await asyncio.to_thread(
            store.load_top20_statistics, start.isoformat(), end.isoformat(),
        )
        return {"start_date": start.isoformat(), "end_date": end.isoformat(), **result}

    content_collections = {
        "news_article", "news_ai", "news_ai_shared", "news_request_usage", "journal_news_link", "journal_v2_news_links", "news_sync", "news_watchlist",
        "theme_profile", "theme_stock", "theme_metadata",
        "journal_settings", "journal_fills", "journal_reviews", "journal_setups",
        "journal_cycle_overrides", "journal_group_overrides", "journal_entry_snapshots",
        "journal_costs", "journal_stocks", "journal_backfill",
        "journal_v2_fills", "journal_v2_reviews", "journal_v2_setups",
        "journal_v2_cycle_overrides", "journal_v2_group_overrides",
        "journal_v2_entry_snapshots", "journal_v2_costs",
        "journal_v2_enrichment_tasks", "journal_v2_analysis_revisions",
        "journal_v2_research_links", "journal_sync_states", "journal_v2_sync_states",
        "app_settings", "app_column_settings",
        "stock_fundamentals", "stock_nxt_eligibility", "stock_price_references",
        "historical_highs",
    }

    def validate_journal_document(collection: str, value: dict[str, Any]) -> None:
        if not collection.startswith("journal"):
            return
        document = value.get("document")
        if not isinstance(document, dict):
            raise HTTPException(status_code=422, detail="일지 문서 형식이 올바르지 않습니다.")
        if collection in {"journal_sync_states", "journal_v2_sync_states"}:
            target = str(document.get("collection", ""))
            is_v2_state = collection == "journal_v2_sync_states"
            owner = str(document.get("owner", ""))
            document_key = str(document.get("document_key", ""))
            if (
                not target or is_v2_state != target.startswith("journal_v2_")
                or str(value.get("owner", "")) != owner
                or str(value.get("key", "")) != document_key
            ):
                raise HTTPException(status_code=422, detail="일지 삭제 상태 namespace가 올바르지 않습니다.")
            if not is_v2_state:
                if owner != "legacy" or str(document.get("origin_broker", "legacy")) != "legacy":
                    raise HTTPException(status_code=422, detail="v1 삭제 상태는 legacy scope만 허용합니다.")
                return
            required = (
                "origin_broker", "origin_environment", "origin_account_ref",
                "canonical_account_ref",
            )
            if any(not str(document.get(name, "")).strip() for name in required):
                raise HTTPException(status_code=422, detail="검증 계좌 삭제 상태 scope가 필요합니다.")
            try:
                uuid.UUID(str(document["origin_account_ref"]))
                uuid.UUID(str(document["canonical_account_ref"]))
            except (ValueError, AttributeError):
                raise HTTPException(status_code=422, detail="검증 계좌 삭제 상태 UUID가 올바르지 않습니다.")
            if (
                document["origin_broker"] != "kiwoom"
                or document["origin_environment"] not in {"real", "mock"}
                or owner != str(document["origin_account_ref"])
            ):
                raise HTTPException(status_code=422, detail="검증 계좌 삭제 상태 scope가 일치하지 않습니다.")
            if document["origin_account_ref"] != document["canonical_account_ref"]:
                resolved = store.resolve_account_scope(
                    str(document["origin_broker"]), str(document["origin_environment"]),
                    str(document["origin_account_ref"]),
                )
                if (
                    not bool(resolved.get("verified"))
                    or str(resolved.get("canonical_account_ref", ""))
                    != str(document["canonical_account_ref"])
                ):
                    raise HTTPException(status_code=422, detail="검증된 계좌 alias가 필요합니다.")
            return
        if collection.startswith("journal_v2_"):
            required = (
                "origin_broker", "origin_environment", "origin_account_ref",
                "canonical_account_ref",
            )
            if any(not str(document.get(name, "")).strip() for name in required):
                raise HTTPException(status_code=422, detail="검증 계좌 scope가 필요합니다.")
            try:
                uuid.UUID(str(document["origin_account_ref"]))
                uuid.UUID(str(document["canonical_account_ref"]))
            except (ValueError, AttributeError):
                raise HTTPException(status_code=422, detail="검증 계좌 UUID가 올바르지 않습니다.")
            if (
                document["origin_broker"] != "kiwoom"
                or document["origin_environment"] not in {"real", "mock"}
                or str(value.get("owner", "")) != str(document["origin_account_ref"])
            ):
                raise HTTPException(status_code=422, detail="검증 계좌 scope 연결이 올바르지 않습니다.")
            if document["origin_account_ref"] != document["canonical_account_ref"]:
                resolved = store.resolve_account_scope(
                    str(document["origin_broker"]), str(document["origin_environment"]),
                    str(document["origin_account_ref"]),
                )
                if (
                    not bool(resolved.get("verified"))
                    or str(resolved.get("canonical_account_ref", ""))
                    != str(document["canonical_account_ref"])
                ):
                    raise HTTPException(status_code=422, detail="검증된 계좌 alias가 필요합니다.")
            if collection == "journal_v2_news_links":
                identity = {
                    "origin_scope": {
                        "broker": document["origin_broker"],
                        "environment": document["origin_environment"],
                        "account_ref": document["origin_account_ref"],
                    },
                    "group_id": str(document.get("group_id", "")),
                    "stock_code": str(document.get("stock_code", "")),
                    "identity": str(document.get("identity", "")),
                }
                encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                expected = "journal-news-link:v2:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                if str(value.get("key", "")) != expected:
                    raise HTTPException(status_code=422, detail="뉴스 연결 key와 scope가 일치하지 않습니다.")
            return
        if str(document.get("origin_broker", "legacy")) not in {"", "legacy"}:
            raise HTTPException(status_code=422, detail="v1 일지 문서는 legacy scope만 허용합니다.")
        if collection == "journal_news_link":
            expected = f'{document.get("stock_code", "")}|{document.get("identity", "")}'
            if str(value.get("owner", "")) != str(document.get("group_id", "")) or str(value.get("key", "")) != expected:
                raise HTTPException(status_code=422, detail="legacy 뉴스 연결 owner/key가 올바르지 않습니다.")

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
        for value in values:
            validate_journal_document(collection, value)
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

    @app.get("/api/v1/themes/history", dependencies=[Depends(authorize)])
    async def theme_history(
        as_of: float | None = Query(default=None, ge=0.0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        values = await asyncio.to_thread(
            store.load_theme_snapshots, available_at=as_of, limit=limit,
        )
        return {"as_of": as_of, "known": bool(values), "snapshots": values}

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
            await websocket.send_json({"type": "ready", "schema_version": 1,
                                       "connection_status": collector.credential_connection_status() if collector is not None else None})
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
                    await websocket.send_json({"type": "central_ready", "codes": sorted(subscriber.codes),
                                               "connection_status": collector.credential_connection_status() if collector is not None else None})
                    if realtime_hub.upstream_ready_for(subscriber.codes):
                        await websocket.send_json({
                            "type": "connection_opened", "scope": "client",
                            "codes": sorted(subscriber.codes),
                        })
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender
            realtime_hub.disconnect(subscriber)

    return app


def _stored_market_response(
    store: Any, api_id: str, body: dict[str, Any],
) -> dict[str, Any] | None:
    """완료 차트와 최신 종목 문서를 키움 TR보다 먼저 재사용한다."""
    if api_id in {"ka10001", "ka10100"}:
        code = str(body.get("stk_cd", "")).strip().removesuffix("_NX").removesuffix("_AL")
        collection = (
            "stock_fundamentals" if api_id == "ka10001"
            else "stock_nxt_eligibility"
        )
        values = store.load_documents(collection, code, 1) if code else []
        if values:
            document = values[0].get("document", {})
            if api_id == "ka10001" and not fundamentals_document_is_current(
                document, datetime.now(KST).date(),
            ):
                return None
            payload = document.get("payload") if isinstance(document, dict) else None
            if isinstance(payload, dict):
                return payload
    return _archived_chart_response(store, api_id, body)


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

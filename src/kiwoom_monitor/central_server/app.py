import asyncio
import hmac
import json
import logging
import re
import sqlite3
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from typing import Any, TYPE_CHECKING

from .config import CentralServerSettings
from kiwoom_monitor.domain.operation_context import OperationContextSnapshot, operation_context_scope
from kiwoom_monitor.infrastructure.central_content_client import _http_request_id
from kiwoom_monitor.infrastructure.logging_config import logging_sink_failure_snapshot
from .contracts import OPERATOR_STATE_VERSION, ServerCapabilities, health_document, operator_state_document
from .database import create_query_store
from .market_ingest import (
    MarketDataIngestor, fundamentals_document_is_current,
    nxt_eligibility_document_is_current,
)
from .realtime_hub import RealtimeHub
from .realtime_collector import CentralRealtimeCollector
from .rest_broker import CentralRestBroker
from .autonomous_top20 import AutonomousTop20Service
from .external_market_collector import YahooDelayedMarketCollector
from .market_observations import KST, as_kst
from .market_read_routes import (
    _archive_coverage_ready, _trade_value_comparison_summary,
    _combined_minute_bars, _explicit_coverage_complete,
)
from .market_events import MarketEventService
from .historical_news_archive import HistoricalNewsArchiveReader
from .account_query import AccountQuerySessionManager
from .market_query_routes import _stored_market_response, _archived_chart_response
from kiwoom_monitor.domain.strategy_contract import (
    MINUTE_SHADOW_VERSION,
    BreakoutStrategyConfig,
    default_shadow_breakout_config,
)

if TYPE_CHECKING:
    from .candidate_monitor import CandidateMonitor


SERVER_BUILD = "2026.10.23-runner-public-owner-v1"
logger = logging.getLogger(__name__)


def _token_matches(supplied: str, expected: str) -> bool:
    """Compare exact token values without compare_digest's ASCII-only str path."""
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


class _HttpOperationContextMiddleware:
    """Own HTTP correlation through downstream work and actual response sends."""

    def __init__(self, app, *, server_now, access_token):
        self.app = app
        self.server_now = server_now
        self.access_token = access_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = [(key.lower(), value) for key, value in scope.get("headers", ())]
        known_bearers = [self.access_token]
        for key, value in headers:
            if key == b"authorization":
                scheme, _, token = value.decode("latin-1").partition(" ")
                if scheme.casefold() == "bearer":
                    known_bearers.append(token)
        request_id = _http_request_id(
            next((value for key, value in headers if key == b"x-request-id"), b"").decode("latin-1"),
            *known_bearers,
        )
        expose_clock = next((value for key, value in headers if key == b"x-kiwoom-clock"), None) == b"1"

        async def send_with_context(message):
            if message["type"] == "http.response.start":
                response_headers = [(key, value) for key, value in message.get("headers", ())
                                    if key.lower() != b"x-request-id"
                                    and not (expose_clock and key.lower() == b"x-kiwoom-server-time")]
                response_headers.append((b"x-request-id", request_id.encode("latin-1")))
                if expose_clock:
                    response_headers.append((b"x-kiwoom-server-time", self.server_now().isoformat().encode("latin-1")))
                message = {**message, "headers": response_headers}
            await send(message)

        with operation_context_scope(OperationContextSnapshot("http.request", request_id)):
            await self.app(scope, receive, send_with_context)


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


def create_app(settings: CentralServerSettings | None = None, *, market_theme_shadow_bindings=(),
               market_theme_shadow_recording: bool = False) -> Any:
    """FastAPI 앱을 만든다. 서버 선택 의존성은 로컬 앱과 분리해 지연 로드한다."""
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Query
        from pydantic import BaseModel, Field, ConfigDict
    except ImportError as error:
        raise RuntimeError("중앙 서버 의존성을 설치하세요: pip install -e .[server]") from error

    active = settings or CentralServerSettings.from_environment()
    selected_research_artifact = active.selected_research_artifact()
    market_theme_shadow_bindings = tuple(market_theme_shadow_bindings)
    if market_theme_shadow_recording and not market_theme_shadow_bindings:
        raise ValueError("RAM shadow recording requires explicit monitor bindings")
    diagnostic_runs = None
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
    ram_shadow_session = None
    def context_changed(kind):
        if ram_shadow_session is not None:
            ram_shadow_session.context_changed(kind)
    def account_position_changed(account_ref):
        if ram_shadow_session is not None:
            ram_shadow_session.account_position_changed(account_ref)
    news_service = None
    news_job_runner = None
    ai_service = None
    def server_now() -> datetime:
        # Read the existing corrected broker clock; this never starts a REST call.
        if broker is not None:
            return as_kst(broker._client.server_now())
        if mock_bundle is not None:
            return as_kst(mock_bundle.client.server_now())
        return datetime.now(KST)
    # The installed tool owns its observers, routes and trace lifetime. Missing
    # tool code is a supported composition; a broken installed tool still fails.
    diagnostic_tools = None
    diagnostic_inputs = {}
    if find_spec(f"{__package__}.diagnostic_read_routes") is not None:
        from . import diagnostic_read_routes as diagnostic_tools
    store = create_query_store(
        active.database_url,
        observation_history_enabled=active.research_observation_history_enabled,
        now_provider=server_now,
    )
    credential_vault = None
    try:
        if diagnostic_tools is not None:
            diagnostic_inputs = diagnostic_tools.compose_application_inputs(store)
        realtime_hub = RealtimeHub(now_provider=server_now, observer=diagnostic_inputs.get("hub"))
        store.initialize()
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
                    raise RuntimeError("VAULT_ALREADY_OWNED: use one server worker") from None
                # Directory/master/ownership failure must not resurrect env credentials.
                from dataclasses import replace
                active = replace(active, kiwoom_app_key="", kiwoom_secret_key="",
                                 kiwoom_mock_app_key="", kiwoom_mock_secret_key="",
                                 naver_news_client_id="", naver_news_client_secret="", dart_api_key="",
                                 openai_api_key="", gemini_api_key="", anthropic_api_key="",
                                 mock_account_monitor_enabled=False, mock_order_transport_enabled=False)
                credential_statuses = {provider: "RECOVERY_REQUIRED" for provider in PROVIDER_FIELDS}
        historical_archive: HistoricalNewsArchiveReader | None = None
        historical_archive_state = "unconfigured"
        if active.historical_news_archive_path:
            historical_archive_state = "unavailable"
            try:
                archive_path = Path(active.historical_news_archive_path)
                if not archive_path.is_absolute():
                    raise ValueError("historical archive path must be absolute")
                cursor_key = hmac.digest(
                    active.access_token.encode("utf-8"), b"historical-news-archive-cursor/v1", "sha256",
                )
                historical_archive = HistoricalNewsArchiveReader(archive_path, cursor_key=cursor_key)
                historical_archive_state = "ready"
            except (OSError, ValueError, sqlite3.DatabaseError) as error:
                logger.warning("historical news archive unavailable: %s", type(error).__name__)
        from .credential_runtime import CredentialRuntime, install_credential_routes
        credential_runtime = CredentialRuntime(credential_vault, store, now_provider=server_now) if credential_vault is not None else None
        operational = {
            "revision": 0,
            "ai_provider": active.ai_provider,
            "ai_model": active.ai_model,
            "ai_daily_limit": active.ai_daily_limit,
            "news_refresh_seconds": active.news_refresh_seconds,
            "news_naver_api_enabled": active.news_naver_api_enabled,
            "news_naver_stock_enabled": active.news_naver_stock_enabled,
            "news_naver_market_enabled": active.news_naver_market_enabled,
            "news_naver_stock_url": active.news_naver_stock_url,
            "news_naver_flash_url": active.news_naver_flash_url,
            "news_naver_world_url": active.news_naver_world_url,
            "dart_enabled": active.dart_enabled,
            "news_query_set_enabled": active.news_query_set_enabled,
            "news_query_set": list(active.parsed_news_query_set()),
            "news_query_set_refresh_seconds": active.news_query_set_refresh_seconds,
            "external_market_enabled": active.external_market_enabled,
            "external_market_poll_seconds": active.external_market_poll_seconds,
            "external_market_auto_roll_enabled": active.external_market_auto_roll_enabled,
            "external_market_roll_confirmations": active.external_market_roll_confirmations,
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
        from kiwoom_monitor.domain.strategy_contract import LeaderBreakoutPolicy, document_leader_breakout_policy
        if operational["shadow_candidate_config"] is None:
            operational["shadow_candidate_config"] = document_leader_breakout_policy().to_dict()
        saved_operational = store.load_documents("server_operational_settings", "global", 1)
        if saved_operational and isinstance(saved_operational[0].get("document"), dict):
            operational.update({
                key: value for key, value in saved_operational[0]["document"].items()
                if key not in {"hot_cohort_condition_enabled", "hot_cohort_condition_name",
                               "hot_cohort_condition_substring"}
            })
        try:
            if "shadow_candidate_configs" in operational:
                from .candidate_monitor import CandidateMonitor
                operational["shadow_candidate_configs"] = CandidateMonitor.normalize_configs(
                    operational["shadow_candidate_configs"])
            else:
                shadow_document = operational.get("shadow_candidate_config")
                if not isinstance(shadow_document, dict):
                    raise ValueError("shadow candidate strategy config must be an object")
                if (not active.shadow_candidate_config_json.strip()
                        and shadow_document == default_shadow_breakout_config().to_dict()):
                    # Retire the previously implicit 5-bar/3%/5%/10-minute profile.
                    # Preserve the original stored document for historical comparison.
                    shadow_document = document_leader_breakout_policy().to_dict()
                    logger.info("implicit legacy shadow profile replaced by unconfigured 0B leader policy")
                if (not active.shadow_candidate_config_json.strip()
                        and shadow_document == json.loads(json.dumps(LeaderBreakoutPolicy().to_dict()))):
                    # Only the entirely unconfigured implicit draft changes in memory.
                    # Original stored and explicitly configured comparison policies stay intact.
                    shadow_document = document_leader_breakout_policy().to_dict()
                if shadow_document.get("version") != MINUTE_SHADOW_VERSION:
                    config = (LeaderBreakoutPolicy.from_dict(shadow_document) if "policy_id" in shadow_document
                              else BreakoutStrategyConfig(**shadow_document))
                    operational["shadow_candidate_config"] = config.to_dict()
                elif operational["shadow_candidate_enabled"]:
                    from kiwoom_monitor.application.section12_minute_research import MinuteShadowConfig

                    operational["shadow_candidate_config"] = MinuteShadowConfig.from_document(shadow_document).to_dict()
        except (TypeError, ValueError):
            logger.warning("invalid saved shadow candidate settings disabled", exc_info=True)
            operational["shadow_candidate_enabled"] = False
            if "shadow_candidate_configs" not in operational and (not isinstance(shadow_document, dict)
                    or shadow_document.get("version") != MINUTE_SHADOW_VERSION):
                operational["shadow_candidate_config"] = document_leader_breakout_policy().to_dict()
        if active.mock_account_monitor_enabled and not active.kiwoom_mock_configured:
            raise ValueError("모의계좌 모니터에는 별도 모의투자 앱 키와 시크릿 키가 필요합니다.")
        if active.mock_order_transport_enabled and not active.mock_account_monitor_enabled:
            raise ValueError("모의주문 전송에는 모의계좌 모니터가 필요합니다.")
        if active.mock_account_monitor_enabled and (
            not active.mock_account_ref or not active.mock_execution_run_id
        ):
            raise ValueError("모의계좌 모니터에는 익명 account ref와 execution run ID가 필요합니다.")
        real_runtime_enabled = (credential_runtime is not None and active.account_identity_registry_enabled
                                and active.kiwoom_environment == "real")
        if active.kiwoom_configured or real_runtime_enabled:
            from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient, KiwoomSettings
            kiwoom_client = KiwoomRestClient(KiwoomSettings(
                active.kiwoom_app_key, active.kiwoom_secret_key, active.kiwoom_environment,
            ))
            def daily_bars_changed(code: str, market: str) -> None:
                if top20_service is not None:
                    top20_service.daily_reference.invalidate(code, market)

            broker = CentralRestBroker(
                kiwoom_client, store, MarketDataIngestor(
                    store, now_provider=server_now, on_daily_change=daily_bars_changed,
                ).ingest,
                ranking_reservation=True, input_observer=diagnostic_inputs.get("broker"),
            )
            if active.account_identity_registry_enabled and not real_runtime_enabled:
                from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
                from kiwoom_monitor.domain.order_contract import AccountEnvironment
                main_identity_reader = KiwoomAccountIdentityReader(
                    broker,
                    environment=AccountEnvironment(active.kiwoom_environment),
                    hmac_key=active.account_identity_key(),
                    now_provider=server_now,
                )
            if active.market_event_collection_enabled:
                market_event_service = MarketEventService(
                    broker, store,
                    now_provider=server_now,
                    **diagnostic_inputs.get("market_events", {}),
                )
            def realtime_account_scope(raw):
                context = real_owner.bundle(real_owner.market_profile_id) if real_owner else None
                return _verified_realtime_scope(raw,
                    context.identity if context else None if real_owner else main_identity,
                    context.binding if context else None if real_owner else main_binding,
                    active.account_identity_key())

            collector = CentralRealtimeCollector(
                lambda: broker._client.get_access_token(), active.kiwoom_environment, realtime_hub,
                server_now, store, market_event_service,
                account_scope_resolver=realtime_account_scope if active.account_identity_registry_enabled else None,
                subscription_observer=diagnostic_inputs.get("subscription"),
                input_observer=diagnostic_inputs.get("collector"),
                micro_book_codes=active.micro_book_codes,
            )
            if active.autonomous_top20_enabled:
                top20_service = AutonomousTop20Service(
                    broker, realtime_hub, store,
                    now_provider=server_now,
                    minute_backfill_enabled=active.autonomous_top20_minute_backfill_enabled,
                    outbox_path=Path(active.top20_outbox_path),
                    on_membership_changed=lambda: context_changed("ranking"),
                    **diagnostic_inputs.get("top20", {}),
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
                now_provider=server_now,
                **diagnostic_inputs.get("accounts", {}),
            )
            account_broker = mock_bundle.broker
            mock_account_monitor = mock_bundle.monitor
            mock_account_realtime = mock_bundle.realtime
            mock_order_gateway = mock_bundle.gateway
        if credential_runtime is not None and active.account_identity_registry_enabled:
            from .mock_runtime import MockCredentialOwner

            async def publish_mock_bundle(profile_id, bundle):
                nonlocal mock_bundle, mock_account_monitor, mock_account_realtime, mock_order_gateway
                if bundle is not None:
                    bundle.monitor.set_position_observation_listener(lambda: account_position_changed(bundle.account_ref))
                if bundle is None:
                    context_changed("account")
                else:
                    account_position_changed(bundle.account_ref)
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
                    list(mock_owner.account_bindings())
                )
                if mock_automation_supervisor is not None:
                    await mock_automation_supervisor.bundle_changed(profile_id, bundle)

            mock_owner = MockCredentialOwner(store, credential_vault,
                hmac_key=active.account_identity_key(), legacy_bundle=mock_bundle,
                legacy_monitor_enabled=active.mock_account_monitor_enabled, on_change=publish_mock_bundle,
                now_provider=server_now, **diagnostic_inputs.get("accounts", {}))
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
                account_event_publisher=collector.publish_account_event if collector is not None else None,
                **diagnostic_inputs.get("accounts", {}))
            if collector is not None:
                collector._account_event_handler = real_owner.on_market_account_event
            credential_runtime.register("kiwoom_real", real_owner.hooks())
        if active.news_configured or credential_runtime is not None or bool(operational["news_naver_stock_enabled"]):
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
            if active.news_history_jobs_enabled:
                from .news_jobs import NewsJobRunner
                news_job_runner = NewsJobRunner(store, parallelism=3, now_provider=server_now,
                    diagnostic_scope=diagnostic_inputs.get("news_scope"),
                    paused=diagnostic_inputs.get("news_paused"))
            news_service = CentralNewsService(
                naver_client, store, dart_client, refresh_seconds=int(operational["news_refresh_seconds"]),
                now_provider=server_now,
                prioritize_stock=(news_job_runner.set_priority_stock if news_job_runner is not None else None),
                enqueue_ai_jobs=(getattr(store, "enqueue_news_ai_jobs", None)
                                 if news_job_runner is not None else None),
                query_set_enabled=bool(operational["news_query_set_enabled"]),
                naver_api_enabled=bool(operational["news_naver_api_enabled"]),
                naver_stock_enabled=bool(operational["news_naver_stock_enabled"]),
                read_only_search=True,
                query_set=tuple(str(value) for value in operational["news_query_set"]),
                query_set_refresh_seconds=int(operational["news_query_set_refresh_seconds"]),
                request_hard_limit=active.news_request_hard_limit,
                watchlist_request_limit=active.news_watchlist_request_limit,
                query_set_request_limit=active.news_query_set_request_limit,
                market_feed_enabled=True,
                processing_excluded_providers=tuple(
                    str(value) for value in operational["news_processing_excluded_providers"]
                ),
                **diagnostic_inputs.get("news", {}),
            )
            news_service.update_operational_settings(
                refresh_seconds=int(operational["news_refresh_seconds"]),
                dart_enabled=bool(operational["dart_enabled"]),
                query_set_enabled=bool(operational["news_query_set_enabled"]),
                naver_api_enabled=bool(operational["news_naver_api_enabled"]),
                naver_stock_enabled=bool(operational["news_naver_stock_enabled"]),
                naver_market_enabled=bool(operational["news_naver_market_enabled"]),
                naver_stock_url=str(operational["news_naver_stock_url"]),
                naver_flash_url=str(operational["news_naver_flash_url"]),
                naver_world_url=str(operational["news_naver_world_url"]),
                query_set=tuple(str(value) for value in operational["news_query_set"]),
                query_set_refresh_seconds=int(operational["news_query_set_refresh_seconds"]),
                processing_excluded_providers=tuple(
                    str(value) for value in operational["news_processing_excluded_providers"]
                ),
            )
        if credential_runtime is not None:
            from .news_credentials import NaverCredentialOwner, DartCredentialOwner
            naver_owner = NaverCredentialOwner(news_service, credential_vault, now_provider=server_now)
            credential_runtime.register("naver", naver_owner.hooks())
            dart_owner = DartCredentialOwner(news_service, credential_vault, Path(active.dart_cache_path), now_provider=server_now)
            credential_runtime.register("dart", dart_owner.hooks())
        if active.parsed_external_market_symbols():
            external_market_service = YahooDelayedMarketCollector(
                store, active.parsed_external_market_symbols(),
                now_provider=server_now,
                poll_seconds=int(operational["external_market_poll_seconds"]),
                auto_roll_enabled=bool(operational["external_market_auto_roll_enabled"]),
                roll_confirmations=int(operational["external_market_roll_confirmations"]),
                **diagnostic_inputs.get("external_market", {}),
            )
        from .chatgpt_sessions import nas_chatgpt_auth
        chatgpt_auth = nas_chatgpt_auth(active.credential_directory)
        if active.ai_configured or credential_runtime is not None or chatgpt_auth is not None:
            from .ai_service import CentralAIService
            ai_service = CentralAIService(active, store, chatgpt_auth=chatgpt_auth, now_provider=server_now)
            ai_service.update_operational_settings(
                provider=str(operational["ai_provider"]), model=str(operational["ai_model"]),
                daily_limit=int(operational["ai_daily_limit"]),
            )
            if credential_runtime is not None:
                from .ai_credentials import AICredentialOwner
                for provider in ("openai", "gemini", "claude"):
                    owner = AICredentialOwner(ai_service, credential_vault, provider, now_provider=server_now)
                    credential_runtime.register(provider, owner.hooks())
        from .morning_market_brief import MorningMarketBrief, create_morning_market_brief_router
        morning_brief = MorningMarketBrief(store, ai_service, clock=server_now,
                                          **diagnostic_inputs.get("morning_brief", {}))
        if bool(operational["shadow_candidate_enabled"]):
            from .candidate_monitor import CandidateMonitor
            candidate_monitor = CandidateMonitor.from_settings(
                store, operational,
                now_provider=server_now,
                poll_seconds=float(operational["shadow_candidate_poll_seconds"]),
                universe_max_age_seconds=int(operational["shadow_candidate_universe_max_age_seconds"]),
                **diagnostic_inputs.get("candidate", {}),
            )
        from .ram_shadow_selection_session import RamShadowSelectionSession
        def theme_context_source(cutoff):
            return store.load_theme_snapshots(available_at=cutoff, limit=1)
        def position_account_source(account_ref):
            observation=None
            if mock_owner is not None:
                observation=mock_owner.position_shadow_observation(account_ref)
            elif mock_bundle is not None and mock_bundle.binding is not None and not mock_bundle._closing:
                if mock_bundle.binding.scope.account_ref == account_ref and mock_bundle.binding.scope.environment.value == "mock":
                    observation=mock_bundle.monitor.latest_position_observation
            return (mock_automation_supervisor.position_shadow_observation(observation)
                    if observation is not None and mock_automation_supervisor is not None else observation)
        if mock_account_monitor is not None:
            mock_account_monitor.set_position_observation_listener(
                lambda account_ref=mock_account_monitor._runtime.account_ref: account_position_changed(account_ref))
        def configure_position_order_observation(account_ref):
            if mock_owner is not None:
                mock_owner.set_position_order_observation_account(account_ref)
            elif mock_bundle is not None:
                mock_bundle.set_position_order_observation_account(account_ref)
        if market_theme_shadow_bindings and collector is None:
            raise ValueError("RAM shadow bindings require the existing realtime collector")
        from .operational_settings_routes import OperationalSettingsState, create_operational_settings_routers
        operational_state = OperationalSettingsState(
            operational, int(operational.get("revision", 0)), candidate_monitor)
        if collector is not None:
            def bind_document_baseline(monitor):
                if monitor is not None:
                    monitor.set_minute_commit_provider(lambda: collector.minute_read_status()['minute_finalizations_committed_at'])
            bind_document_baseline(operational_state.candidate_monitor)
            def observe_leader_breakout(payload, received_at, **source):
                monitor = operational_state.candidate_monitor
                if monitor is not None:
                    monitor.observe_trade(payload, received_at, **source)
                if mock_automation_supervisor is not None:
                    mock_automation_supervisor.observe_leader_trade(payload, received_at, **source)
            collector.set_breakout_trade_listener(observe_leader_breakout)
        def publish_ram_runtime(runtime):
            # Compatibility observation only; the selection session owns the pointer.
            app.state.market_theme_shadow_runtime = runtime

        ram_shadow_session = RamShadowSelectionSession(store, realtime_hub, collector,
            logger=logger, now_provider=server_now,
            ranking_source=top20_service.latest_membership_snapshot if top20_service is not None else None,
            theme_source=theme_context_source, position_account_source=position_account_source,
            configure_position_order_observation=configure_position_order_observation,
            publish_runtime=publish_ram_runtime,
            explicit_bindings=market_theme_shadow_bindings, explicit_recording=market_theme_shadow_recording)
        if news_service is not None:
            news_service.set_ai_service(ai_service)
        if news_job_runner is not None:
            news_job_runner.set_ai_service(ai_service)
        if mock_owner is not None:
            from .mock_automation_supervisor import MockAutomationSupervisor
            from kiwoom_monitor.domain.strategy_contract import shadow_candidate_config_for_spec
            def leader_context_source(spec_id=None):
                return {"policy": shadow_candidate_config_for_spec(operational_state.settings, spec_id),
                    "enabled": operational_state.settings.get('shadow_candidate_enabled') is True,
                    "revision": operational_state.applied_revision}
            mock_automation_supervisor = MockAutomationSupervisor(store, mock_owner,
                now_provider=server_now,
                leader_context_source=leader_context_source,
                expected_implementation_hash=(selected_research_artifact.expected_implementation_hash
                    if selected_research_artifact is not None else lambda _profile: None),
                price_source=collector.automation_price_observation if collector is not None else None)
            if collector is not None:
                collector.set_automation_price_listener(mock_automation_supervisor.observe_automation_price)

        mock_bundle_close_error: BaseException | None = None

        async def start_services(_app: Any):
            nonlocal account_broker, main_binding, main_identity, mock_bundle
            nonlocal mock_account_monitor, mock_account_realtime, mock_order_gateway
            nonlocal mock_bundle_close_error
            if diagnostic_tools is not None:
                await asyncio.to_thread(diagnostic_tools.trace.recover_interrupted)
            if broker is not None:
                await broker.start()
            verified_bindings = []
            if real_owner is not None:
                await real_owner.start()
                verified_bindings.extend(real_owner.account_bindings())
            if main_identity_reader is not None:
                from kiwoom_monitor.application.account_identity import bind_verified_account_identity
                main_identity = await main_identity_reader.verify()
                main_binding = bind_verified_account_identity(
                    main_identity, store,
                    credential_profile_id=("nas-main-mock-default"
                        if credential_vault is not None and active.kiwoom_environment == "mock"
                        else f"nas-{active.kiwoom_environment}-default"),
                )
                verified_bindings.append(main_binding)
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
                    try:
                        await mock_bundle.close()
                    except BaseException as close_error:
                        # A failed close is not safe to retry or treat as a disabled bundle.
                        mock_bundle_close_error = close_error
                        raise error from None
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
            await ram_shadow_session.start()
            if collector is not None:
                await collector.start()
            if news_job_runner is not None:
                await news_job_runner.start()
            if news_service is not None:
                await news_service.start()
            await morning_brief.start()
            if external_market_service is not None and bool(operational["external_market_enabled"]):
                await external_market_service.start()
            if operational_state.candidate_monitor is not None:
                await operational_state.candidate_monitor.start()
        async def close_services(_app: Any):
            ram_shadow_session.begin_close()
            status = _app.state.shutdown_status
            status["stage"] = "diagnostic_runs"
            if diagnostic_runs is not None:
                await asyncio.to_thread(diagnostic_runs.close, timeout=None)
            status["stage"] = "diagnostic_trace"
            if diagnostic_tools is not None:
                await asyncio.to_thread(diagnostic_tools.trace.stop, "server_shutdown", timeout=None)
            status["stage"] = "credentials"
            if credential_runtime is not None:
                await credential_runtime.close()
            status["stage"] = "mock_automation"
            if mock_automation_supervisor is not None:
                await mock_automation_supervisor.close()
            status["stage"] = "real_accounts"
            if real_owner is not None:
                await real_owner.close()
            status["stage"] = "mock_accounts"
            if mock_owner is not None:
                await mock_owner.close()
            elif mock_bundle is not None:
                if mock_bundle_close_error is not None:
                    raise mock_bundle_close_error
                await mock_bundle.close()
            status["stage"] = "candidate"
            async with operational_state.lock:
                if operational_state.candidate_monitor is not None:
                    await operational_state.candidate_monitor.close()
            status["stage"] = "ram_shadow"
            await ram_shadow_session.close()
            status["stage"] = "external_market"
            if external_market_service is not None:
                await external_market_service.close()
            status["stage"] = "news"
            news_close_error = None
            try:
                if news_service is not None:
                    await news_service.close()
            except BaseException as error:
                news_close_error = error
                raise
            finally:
                if news_job_runner is not None:
                    if news_close_error is None:
                        status["stage"] = "news_jobs"
                    try:
                        await news_job_runner.close()
                    except BaseException as error:
                        if news_close_error is None:
                            raise
                        news_close_error.add_note("NEWS_RUNNER_CLOSE_FAILED:" + type(error).__name__)
            status["stage"] = "ai"
            await morning_brief.close()
            if ai_service is not None:
                await ai_service.close()
            status["stage"] = "top20"
            if top20_service is not None:
                await top20_service.close()
            status["stage"] = "collector"
            if collector is not None:
                await collector.close()
            status["stage"] = "market_events"
            if market_event_service is not None:
                await market_event_service.close()
            status["stage"] = "account_queries"
            if account_query_manager is not None:
                await account_query_manager.close()
            status["stage"] = "broker"
            if broker is not None:
                await broker.close()
            status["stage"] = "vault"
            if credential_vault is not None:
                credential_vault.close()
            status["stage"] = "store"
            store.close()

        @asynccontextmanager
        async def lifespan(_app: Any):
            primary_error: BaseException | None = None
            _app.state.shutdown_status = {"state": "pending", "stage": "startup"}
            try:
                _app.state.diagnostic_loop = asyncio.get_running_loop()
                if credential_runtime is not None:
                    await credential_runtime.start()
                await start_services(_app)
                yield
            except BaseException as error:
                primary_error = error
                raise
            finally:
                _app.state.shutdown_status["state"] = "running"
                shutdown = asyncio.create_task(close_services(_app), name="central-server-shutdown")
                cancelled: asyncio.CancelledError | None = None
                shutdown_error: BaseException | None = None
                while True:
                    try:
                        await asyncio.shield(shutdown)
                        break
                    except asyncio.CancelledError as error:
                        if shutdown.cancelled():
                            shutdown_error = error
                            break
                        cancelled = error
                    except BaseException as error:
                        shutdown_error = error
                        break
                if shutdown_error is not None:
                    status = _app.state.shutdown_status
                    status["state"] = "failed"
                    logger.error("SERVER_SHUTDOWN_FAILED stage=%s", status["stage"])
                    if primary_error is not None:
                        primary_error.add_note("SERVER_SHUTDOWN_FAILED:" + status["stage"])
                    elif cancelled is not None:
                        cancelled.add_note("SERVER_SHUTDOWN_FAILED:" + status["stage"])
                        raise cancelled from None
                    else:
                        raise shutdown_error
                else:
                    _app.state.shutdown_status["state"] = "completed"
                if cancelled is not None and primary_error is None:
                    raise cancelled

        app = FastAPI(title="Kiwoom Monitor Personal Server", version="1", lifespan=lifespan)
        app.state.credential_statuses = credential_statuses
        app.state.credential_runtime = credential_runtime
        app.state.realtime_hub = realtime_hub
        app.state.realtime_collector = collector
        app.state.ram_shadow_selection_session = ram_shadow_session
        app.state.market_theme_shadow_runtime = ram_shadow_session.runtime
        app.state.autonomous_top20_service = top20_service
        app.state.market_event_service = market_event_service
        app.state.external_market_collector = external_market_service
        app.state.morning_market_brief = morning_brief
        app.state.candidate_monitor = operational_state.candidate_monitor
        app.state.mock_account_monitor = mock_account_monitor
        app.state.mock_account_realtime = mock_account_realtime
        app.state.mock_order_gateway = mock_order_gateway
        app.state.mock_account_bundle = mock_bundle
        app.state.mock_credential_owner = mock_owner
        app.state.mock_automation_supervisor = mock_automation_supervisor
        app.state.real_credential_owner = real_owner
        app.state.mock_account_startup_error = ""
        app.state.verified_account_bindings = ()

        class AccountTarget(BaseModel):
            model_config = ConfigDict(extra="forbid")
            broker: str = Field(pattern=r"^kiwoom$")
            environment: str = Field(pattern=r"^(mock|real)$")
            account_ref: str = Field(min_length=36, max_length=36)

        def authorize_read(authorization: str = Header(default="")) -> str:
            scheme, _, supplied = authorization.partition(" ")
            if scheme.casefold() == "bearer":
                if _token_matches(supplied, active.access_token):
                    return "admin"
                if active.query_access_token and _token_matches(supplied, active.query_access_token):
                    return "query"
            raise HTTPException(status_code=401, detail="유효한 서버 접속 토큰이 필요합니다.")

        def authorize(authorization: str = Header(default="")) -> None:
            if authorize_read(authorization) != "admin":
                raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

        def authorize_research_input_read(authorization: str = Header(default="")) -> str:
            scheme, _, supplied = authorization.partition(" ")
            if scheme.casefold() == "bearer" and supplied:
                for grant in active.research_input_grants:
                    if _token_matches(supplied, grant.token):
                        return "research_input"
            return authorize_read(authorization)

        def authorize_research_input(authorization: str = Header(default="")) -> None:
            if authorize_research_input_read(authorization) == "query":
                raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

        def authorize_research_result(
            authorization: str = Header(default=""),
            x_kiwoom_expected_result_owner: str = Header(default=""),
        ) -> str:
            from kiwoom_monitor.domain.research_contract import decode_reported_result_owner

            scheme, _, supplied = authorization.partition(" ")
            if scheme.casefold() == "bearer" and supplied:
                for grant in active.research_result_grants:
                    if _token_matches(supplied, grant.token):
                        try:
                            expected_owner = decode_reported_result_owner(x_kiwoom_expected_result_owner)
                        except ValueError:
                            raise HTTPException(status_code=409, detail="연구 결과 대상 소유자를 확인하세요.") from None
                        if expected_owner != grant.owner:
                            raise HTTPException(status_code=409, detail="연구 결과 대상 소유자가 변경됐습니다.")
                        return grant.owner
            raise HTTPException(status_code=401, detail="전용 연구 결과 접속 토큰이 필요합니다.")

        def authorize_news_body(
            authorization: str = Header(default=""),
            x_kiwoom_expected_result_owner: str = Header(default=""),
        ) -> str:
            from kiwoom_monitor.domain.research_contract import decode_reported_result_owner
            scheme, _, supplied = authorization.partition(" ")
            if scheme.casefold() == "bearer" and supplied:
                for grant in active.news_body_grants:
                    if _token_matches(supplied, grant.token):
                        try:
                            owner = decode_reported_result_owner(x_kiwoom_expected_result_owner)
                        except ValueError:
                            raise HTTPException(status_code=409, detail="뉴스 BODY 대상 소유자를 확인하세요.") from None
                        if owner != grant.owner:
                            raise HTTPException(status_code=409, detail="뉴스 BODY 대상 소유자가 변경됐습니다.")
                        return grant.owner
            raise HTTPException(status_code=401, detail="전용 뉴스 BODY 접속 토큰이 필요합니다.")

        def valid_token(supplied: str) -> bool:
            return bool(supplied) and _token_matches(supplied, active.access_token)

        def valid_query_token(supplied: str) -> bool:
            return bool(active.query_access_token) and _token_matches(supplied, active.query_access_token)

        app.include_router(create_morning_market_brief_router(morning_brief, authorize))
        install_credential_routes(app, credential_runtime, authorize, active.credential_trusted_proxies, mock_owner, real_owner)

        from .account_settings_routes import create_account_settings_router
        app.include_router(create_account_settings_router(store, authorize, real_owner, mock_owner))

        app.add_middleware(
            _HttpOperationContextMiddleware, server_now=server_now, access_token=active.access_token,
        )

        @app.get("/health")
        async def health() -> dict[str, object]:
            document = health_document(now=server_now())
            document["server_build"] = SERVER_BUILD
            document["operator_state_version"] = OPERATOR_STATE_VERSION
            document["logging"] = logging_sink_failure_snapshot()
            document["historical_news_archive"] = {
                "state": historical_archive_state,
                "dataset_id": historical_archive.dataset_id if historical_archive else None,
            }
            document["historical_news_pc_scopes"] = ["market", "search", "legacy_backlog"]
            document["realtime_connection"] = collector.credential_connection_status() if collector is not None else None
            realtime_status = collector.operational_status() if collector is not None else None
            monitor = operational_state.candidate_monitor
            candidate_status = monitor.operational_status() if monitor is not None else None
            degraded = (
                realtime_status is not None and realtime_status["persistence"]["state"] == "degraded"
            ) or (
                candidate_status is not None and (
                    candidate_status.get("universe", {}).get("state") in {"missing", "stale"}
                    or candidate_status["last_evaluation"]["status"] in {"STALE", "ERROR"}
                )
            )
            document["operational"] = {
                "state": "degraded" if degraded else "unknown",
                "coverage": "partial", "realtime": realtime_status,
                "candidate_inputs": candidate_status,
            }
            document["mock_account_available"] = (
                mock_owner.has_started_monitor()
                if mock_owner is not None else mock_account_monitor is not None
            )
            if mock_owner is not None and mock_owner.errors.get("nas-mock-default"):
                document["mock_account_error"] = mock_owner.errors["nas-mock-default"]
            if _app_error := str(getattr(app.state, "mock_account_startup_error", "")):
                document["mock_account_error"] = _app_error
            return document

        @app.get("/api/v1/operator/state", dependencies=[Depends(authorize)])
        async def operator_state() -> dict[str, object]:
            if diagnostic_tools is None:
                # This assembly installed no tool factories, input hooks or routes.
                # A disabled installed tool always uses its actual owners below.
                facts = {key: {"state": "not_created"} for key in ("control", "trace", "runs")}
                composition = "not_installed"
            else:
                from .observation_frame_recovery import drain_thread_call
                facts = await drain_thread_call(diagnostic_tools.operator_state_facts, diagnostic_runs)
                composition = "installed"
            return operator_state_document(now=server_now(), composition=composition, **facts)

        @app.get("/api/v1/capabilities")
        def capabilities(access_scope: str = Depends(authorize_read)) -> dict[str, object]:
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
            document["capabilities"]["historical_news_archive_v1"] = (
                historical_archive is not None and historical_archive.schema_version == 1
            )
            document["capabilities"]["historical_news_archive_v2"] = (
                historical_archive is not None and historical_archive.schema_version == 2
            )
            document["historical_news_archive_dataset_id"] = (
                historical_archive.dataset_id if historical_archive else None
            )
            if access_scope == "query":
                readable = {"kiwoom_rest", "realtime_stream", "news_archive", "ai_analysis_archive", "themes",
                            "combined_minute_bars", "trade_value_comparisons", "planned_reconnect_v1",
                            "historical_news_archive_v1", "historical_news_archive_v2"}
                document["capabilities"] = {key: bool(value) and key in readable
                                            for key, value in document["capabilities"].items()}
                document["access_scope"] = "query"
            return document

        from .account_query_routes import create_account_contexts_router, create_account_query_router, scoped_context

        def current_legacy_account_query():
            # Owners publish or remove these values after composition and key changes.
            return main_binding, account_query_manager

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

        def current_mock_order_gateway():
            bundle = mock_owner.bundle() if mock_owner is not None else None
            return bundle.gateway if bundle is not None else (mock_order_gateway if mock_owner is None else None)

        from .mock_order_routes import create_mock_order_routers
        mock_order_router, scoped_mock_order_router = create_mock_order_routers(
            authorize, AccountTarget, current_mock_order_gateway, selected_mock)
        app.include_router(mock_order_router)

        app.include_router(create_account_contexts_router(
            store, authorize, mock_owner, real_owner, current_legacy_account_query))

        app.include_router(scoped_mock_order_router)

        def publish_candidate_monitor(monitor):
            if collector is not None:
                bind_document_baseline(monitor)
            app.state.candidate_monitor = monitor

        operational_read_router, operational_update_router = create_operational_settings_routers(
            store, authorize, operational_state, external_market_service,
            ai_service, news_service, publish_candidate_monitor, logger,
            now_provider=server_now,
            ram_shadow_session=ram_shadow_session,
            candidate_workload_paused=diagnostic_inputs.get("candidate", {}).get("workload_paused"),
            candidate_capture_scope=diagnostic_inputs.get("candidate", {}).get("capture_scope"))
        app.include_router(operational_read_router)

        # Connection/resource inspection belongs to the product even when the
        # detailed recording and diagnostic tools are not installed.
        @app.get("/api/v1/diagnostics/resources", dependencies=[Depends(authorize)])
        async def diagnostics_resources() -> dict[str, object]:
            from .resource_usage import resource_usage
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

        if diagnostic_tools is not None:
            diagnostic_runs = diagnostic_tools.install_application_routes(
                app, store, authorize, active.database_url, top20_service, news_service,
                external_market_service, lambda: operational_state.candidate_monitor,
                historical_archive, morning_brief=morning_brief,
                current_news_job_runner=lambda: news_job_runner,
                server_build=SERVER_BUILD, logger=logger)
        app.state.diagnostic_runs = diagnostic_runs

        app.include_router(operational_update_router)

        from .market_query_routes import create_market_query_router
        app.include_router(create_market_query_router(store, authorize, broker, collector, now_provider=server_now,
                                                     read_authorize=authorize_read))

        app.include_router(create_account_query_router(
            authorize, AccountTarget, current_legacy_account_query, selected_account))

        from .news_service_routes import create_news_analysis_router, create_news_search_router

        app.include_router(create_news_search_router(news_service, authorize,
                                                   read_authorize=authorize_read, store=store))

        from .historical_news_archive_routes import create_historical_news_archive_router

        app.include_router(create_historical_news_archive_router(
            historical_archive, authorize_read, **diagnostic_inputs.get("historical_archive", {})))

        app.include_router(create_news_analysis_router(ai_service, authorize))
        from .chatgpt_sessions import create_chatgpt_session_router
        app.include_router(create_chatgpt_session_router(ai_service, authorize))

        from .historical_news_processing_routes import create_historical_news_processing_router

        app.include_router(create_historical_news_processing_router(store, authorize))

        from .news_read_routes import create_news_read_router

        app.include_router(create_news_read_router(store, authorize, read_authorize=authorize_read))

        from .market_read_routes import (
            create_market_live_read_router, create_market_event_read_router, create_market_archive_read_router,
        )

        app.include_router(create_market_live_read_router(store, authorize_read, lambda: app.state.realtime_collector))

        app.include_router(create_market_event_read_router(store, authorize_read))

        app.include_router(create_market_archive_read_router(store, authorize, now_provider=server_now,
                                                           read_authorize=authorize_read))

        from .research_read_routes import create_research_read_router
        if active.news_body_grants:
            from .news_body_routes import create_news_body_router
            app.include_router(create_news_body_router(store, authorize_news_body, now_provider=server_now))
        app.include_router(create_research_read_router(store, authorize, lambda: operational_state.candidate_monitor,
            input_authorize=authorize_research_input,
            result_authorize=authorize_research_result,
            get_market_theme_shadow_runtime=lambda: ram_shadow_session.runtime,
            get_realtime_collector=lambda: app.state.realtime_collector,
            now_provider=server_now,
            check_stock_thesis_sources=ram_shadow_session.check_stock_thesis_sources))

        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        from .mock_publication_routes import create_mock_publication_router
        app.include_router(create_mock_publication_router(
            store, ForwardEvaluationRepository(store), authorize,
            expected_implementation_hash=(selected_research_artifact.expected_implementation_hash
                if selected_research_artifact is not None else lambda _profile: None),
        ))

        from .mock_automation_routes import create_mock_automation_router
        app.include_router(create_mock_automation_router(mock_automation_supervisor, authorize))

        from .selected_order_recovery_routes import create_selected_order_recovery_router
        from .selected_order_broker_evidence import read_kiwoom_selected_order_evidence
        app.include_router(create_selected_order_recovery_router(store, mock_owner, authorize,
            broker_evidence_provider=read_kiwoom_selected_order_evidence, supervisor=mock_automation_supervisor))

        from .market_dataset_read_routes import create_market_dataset_read_router
        app.include_router(create_market_dataset_read_router(store, authorize_read, top20_service))

        from .content_routes import create_content_router
        app.include_router(create_content_router(store, authorize,
            read_authorize=authorize_read,
            theme_history_authorize=authorize_research_input_read,
            on_theme_changed=lambda: context_changed("theme"),
            theme_notifications_enabled=lambda: ram_shadow_session.runtime is not None))

        from .realtime_routes import create_realtime_router
        app.include_router(create_realtime_router(realtime_hub, store, valid_token, collector,
                                                valid_query_token=valid_query_token))

        return app
    except BaseException as construction_error:
        # Only construction-acquired resources exist before lifespan ownership.
        # If vault release fails, retain its metadata dependency and original cause.
        try:
            if credential_vault is not None:
                credential_vault.close()
            store.close()
        except BaseException as cleanup_error:
            raise cleanup_error from construction_error
        raise

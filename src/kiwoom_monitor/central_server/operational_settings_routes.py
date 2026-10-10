"""Operational settings HTTP policy and saved/applied state; native services retain their work."""
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING
from urllib.parse import urlsplit

from .candidate_monitor import CandidateMonitor

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .database_observation_readers import ObservationRevisionPage
    from .market_events import MarketEventService
    from .external_market_collector import YahooDelayedMarketCollector
    from .news_service import CentralNewsService
    from .ai_service import CentralAIService


class OperationalSettingsStore(Protocol):
    """Settings persistence plus native candidate construction and frame recovery."""

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]: ...
    def load_observation_revision_page(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
        *, through_sequence: int | None = None,
    ) -> "ObservationRevisionPage": ...
    def load_observation_bootstrap(
        self, kinds: tuple[str, ...], per_kind_limit: int = 5000,
    ) -> "ObservationRevisionPage": ...
    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None: ...
    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None: ...
    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None: ...


@dataclass
class OperationalSettingsState:
    """Per-app settings revision and candidate pointer shared with the app lifecycle."""
    settings: dict[str, Any]
    applied_revision: int
    candidate_monitor: CandidateMonitor | None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def create_operational_settings_routers(
    store: "OperationalSettingsStore", authorize: Callable[..., None], state: OperationalSettingsState,
    market_event_service: "MarketEventService | None",
    external_market_service: "YahooDelayedMarketCollector | None",
    ai_service: "CentralAIService | None", news_service: "CentralNewsService | None",
    publish_candidate_monitor: Callable[[CandidateMonitor | None], None], logger: Any,
) -> tuple["APIRouter", "APIRouter"]:
    """Keep save-before-apply and retry without incrementing a persisted revision."""
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field

    read_router, update_router = APIRouter(), APIRouter()
    operational = state.settings

    class OperationalSettingsUpdate(BaseModel):
        expected_revision: int | None = Field(default=None, ge=0)
        ai_provider: str | None = Field(default=None, pattern=r"^(none|openai|gemini|claude)$")
        ai_model: str | None = Field(default=None, max_length=100)
        ai_daily_limit: int | None = Field(default=None, ge=0, le=1_000_000)
        news_refresh_seconds: int | None = Field(default=None, ge=60, le=86_400)
        news_naver_api_enabled: bool | None = Field(default=None, strict=True)
        news_naver_stock_enabled: bool | None = Field(default=None, strict=True)
        news_naver_market_enabled: bool | None = Field(default=None, strict=True)
        news_naver_stock_url: str | None = Field(default=None, max_length=500, strict=True)
        news_naver_flash_url: str | None = Field(default=None, max_length=500, strict=True)
        news_naver_world_url: str | None = Field(default=None, max_length=500, strict=True)
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

    def operational_document() -> dict[str, object]:
        revision = int(operational["revision"])
        condition = market_event_service.condition_status() if market_event_service is not None else None
        return {
            **operational, "applied_revision": state.applied_revision,
            "apply_status": "ACTIVE" if revision == state.applied_revision and
                (condition is None or condition["apply_status"] != "RECOVERY_REQUIRED") else "RECOVERY_REQUIRED",
            "condition_runtime_supported": market_event_service is not None,
            "condition_status": condition,
        }

    @read_router.get("/api/v1/settings/operations", dependencies=[Depends(authorize)])
    async def get_operational_settings() -> dict[str, object]:
        return operational_document()

    @update_router.put("/api/v1/settings/operations", dependencies=[Depends(authorize)])
    async def put_operational_settings(values: OperationalSettingsUpdate) -> dict[str, object]:
        async with state.lock:
            changes = values.model_dump(exclude_none=True, exclude={"expected_revision"})
            if values.expected_revision is not None and values.expected_revision != int(operational["revision"]):
                raise HTTPException(status_code=409, detail="OPERATIONAL_SETTINGS_REVISION_CONFLICT")
            proposed = {**operational, **changes}
            for field in ("news_naver_stock_url", "news_naver_flash_url", "news_naver_world_url"):
                endpoint = str(proposed[field]).strip()
                parsed = urlsplit(endpoint)
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                        or parsed.password or parsed.query or parsed.fragment or not parsed.path):
                    raise HTTPException(status_code=422, detail=f"{field}: HTTPS API 주소를 입력하세요.")
                proposed[field] = endpoint
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
            recovery_required = int(operational["revision"]) != state.applied_revision
            condition_changed = condition_changed or (recovery_required and market_event_service is not None)
            external_changed = recovery_required or any(
                name in changes and proposed[name] != operational[name] for name in external_fields)
            shadow_changed = recovery_required or any(
                name in changes and proposed[name] != operational[name] for name in shadow_fields
            )
            replacement = state.candidate_monitor
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
                        naver_api_enabled=bool(proposed["news_naver_api_enabled"]),
                        naver_stock_enabled=bool(proposed["news_naver_stock_enabled"]),
                        naver_market_enabled=bool(proposed["news_naver_market_enabled"]),
                        naver_stock_url=str(proposed["news_naver_stock_url"]),
                        naver_flash_url=str(proposed["news_naver_flash_url"]),
                        naver_world_url=str(proposed["news_naver_world_url"]),
                        dart_enabled=bool(proposed["dart_enabled"]),
                        query_set_enabled=bool(proposed["news_query_set_enabled"]),
                        query_set=tuple(str(value) for value in proposed["news_query_set"]),
                        query_set_refresh_seconds=int(proposed["news_query_set_refresh_seconds"]),
                        processing_excluded_providers=tuple(
                            str(value) for value in proposed["news_processing_excluded_providers"]
                        ),
                    )
                if shadow_changed:
                    previous = state.candidate_monitor
                    if previous is not None:
                        await previous.close()
                    state.candidate_monitor = replacement
                    publish_candidate_monitor(replacement)
                    if replacement is not None:
                        await replacement.start()
                state.applied_revision = int(proposed["revision"])
            except Exception as error:
                logger.exception("operational settings runtime apply failed")
                raise HTTPException(status_code=503, detail="OPERATIONAL_SETTINGS_APPLY_PENDING") from error
            return operational_document()

    return read_router, update_router

"""Content synchronization HTTP policy; native persistence stays in the app-owned store."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field


class ContentStore(Protocol):
    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]: ...

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...

    def resolve_account_scope(self, broker: str, environment: str, account_ref: str) -> dict[str, Any]: ...

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]: ...


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


def create_content_router(store: ContentStore, authorize: Callable[..., None]) -> APIRouter:
    """Keep collection policy, journal identity checks, and request models together."""
    router = APIRouter()
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

    @router.get("/api/v1/content/{collection}", dependencies=[Depends(authorize)])
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

    @router.post("/api/v1/content/{collection}", dependencies=[Depends(authorize)])
    async def upsert_content(collection: str, batch: DocumentBatch) -> dict[str, object]:
        if collection not in content_collections:
            raise HTTPException(status_code=404, detail="지원하지 않는 중앙 자료 종류입니다.")
        values = [value.model_dump() for value in batch.documents]
        for value in values:
            validate_journal_document(collection, value)
        await asyncio.to_thread(store.upsert_documents, collection, values)
        return {"collection": collection, "saved": len(values)}

    @router.put("/api/v1/content/{collection}", dependencies=[Depends(authorize)])
    async def replace_content(collection: str, snapshot: DocumentSnapshot) -> dict[str, object]:
        # 삭제·이름 변경도 정확히 전파해야 하는 작은 설정 컬렉션에만 허용한다.
        if collection not in {"theme_profile", "theme_stock", "theme_metadata"}:
            raise HTTPException(status_code=405, detail="전체 교체를 지원하지 않는 중앙 자료 종류입니다.")
        values = [value.model_dump() for value in snapshot.documents]
        await asyncio.to_thread(store.replace_documents, collection, values)
        return {"collection": collection, "saved": len(values)}

    @router.get("/api/v1/themes/history", dependencies=[Depends(authorize)])
    async def theme_history(
        as_of: float | None = Query(default=None, ge=0.0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        values = await asyncio.to_thread(
            store.load_theme_snapshots, available_at=as_of, limit=limit,
        )
        return {"as_of": as_of, "known": bool(values), "snapshots": values}

    return router

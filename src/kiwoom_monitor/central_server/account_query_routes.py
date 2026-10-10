"""Account query HTTP contracts; credential owners retain admission and session lifetime."""
from collections.abc import Callable
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .account_query import AccountQuerySessionManager
    from .mock_runtime import MockCredentialOwner
    from .real_runtime import RealCredentialOwner
    from kiwoom_monitor.domain.order_contract import AccountBinding


class AccountQueryProfileStore(Protocol):
    def list_credential_profiles(self, provider: str = "") -> list[dict[str, Any]]: ...


def scoped_context(binding):
    return {**binding.scope.to_dict(), "credential_profile_id": binding.credential_profile_id,
        "binding_revision": binding.binding_revision, "verified_at": binding.verified_at.isoformat(),
        "verification_method": binding.verification_method}


def create_account_contexts_router(
    store: AccountQueryProfileStore, authorize: Callable[..., None],
    mock_owner: "MockCredentialOwner | None", real_owner: "RealCredentialOwner | None",
    current_legacy_account_query: Callable[[], tuple["AccountBinding | None", "AccountQuerySessionManager | None"]],
) -> "APIRouter":
    from fastapi import APIRouter, Depends

    router = APIRouter()

    @router.get("/api/v3/kiwoom/accounts", dependencies=[Depends(authorize)])
    def account_contexts():
        bindings = list(mock_owner.account_bindings()) if mock_owner is not None else []
        if real_owner is not None:
            bindings.extend(real_owner.account_bindings())
        main_binding, account_query_manager = current_legacy_account_query()
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

    return router


def create_account_query_router(
    authorize: Callable[..., None], AccountTarget: type,
    current_legacy_account_query: Callable[[], tuple["AccountBinding | None", "AccountQuerySessionManager | None"]],
    selected_account: Callable[..., tuple[Any, Any, Any]],
) -> "APIRouter":
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field, ConfigDict

    router = APIRouter()

    class AccountQueryRequest(BaseModel):
        api_id: str = Field(pattern=r"^(kt00007|kt00015)$")
        path: str = Field(min_length=1, max_length=100)
        body: dict[str, Any] = Field(default_factory=dict)
        batch_id: str = Field(default="", max_length=64)
        page_index: int = Field(default=0, ge=0, le=20)
        next_key: str = Field(default="", max_length=500)

    class ScopedAccountQueryRequest(AccountQueryRequest):
        model_config = ConfigDict(extra="forbid")
        account_scope: AccountTarget
        credential_profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
        expected_binding_revision: int = Field(strict=True, ge=1)

    @router.post("/api/v2/kiwoom/account-query", dependencies=[Depends(authorize)])
    async def account_query(query: AccountQueryRequest) -> dict[str, object]:
        main_binding, account_query_manager = current_legacy_account_query()
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

    @router.post("/api/v3/kiwoom/account-query", dependencies=[Depends(authorize)])
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

    return router

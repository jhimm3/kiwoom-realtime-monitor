"""Account preferences and market role HTTP policy; admission stays with runtime owners."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .real_runtime import RealCredentialOwner
    from .mock_runtime import MockCredentialOwner


class AccountSettingsReadStore(Protocol):
    def load_market_profile_settings(self) -> dict[str, Any]: ...

    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]: ...


def create_account_settings_router(
    store: AccountSettingsReadStore, authorize: Callable[..., None],
    real_owner: RealCredentialOwner | None, mock_owner: MockCredentialOwner | None,
) -> APIRouter:
    """Read stored preferences and delegate role changes to the same admitted owner."""
    from fastapi import APIRouter, Depends, HTTPException, Query

    router = APIRouter()

    @router.get("/api/v1/settings/market-profile", dependencies=[Depends(authorize)])
    async def market_profile_settings() -> dict[str, object]:
        try:
            document = await asyncio.to_thread(store.load_market_profile_settings)
        except ValueError:
            raise HTTPException(409, detail="MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED") from None
        # Persisted role intent is not proof of a completed live transport change.
        return {"settings": document, "applied_revision":
                real_owner.applied_market_role_revision() if real_owner is not None else None}

    @router.put("/api/v1/settings/market-profile", dependencies=[Depends(authorize)])
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

    @router.get("/api/v1/settings/accounts/{account_ref}", dependencies=[Depends(authorize)])
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

    return router

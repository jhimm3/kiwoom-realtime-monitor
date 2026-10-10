"""Mock automation control HTTP policy; the app owns the existing supervisor."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .mock_automation_supervisor import MockAutomationSupervisor


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


def create_mock_automation_router(
    mock_automation_supervisor: MockAutomationSupervisor | None,
    authorize: Callable[..., None],
) -> APIRouter:
    """Keep strict requests and operation error translation beside their control routes."""
    router = APIRouter()

    def require_mock_automation_supervisor():
        if mock_automation_supervisor is None:
            raise HTTPException(status_code=503, detail="MOCK_AUTOMATION_RUNTIME_UNAVAILABLE")
        return mock_automation_supervisor

    @router.get(
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

    @router.post("/api/v1/mock-automation/start", dependencies=[Depends(authorize)])
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

    @router.post("/api/v1/mock-automation/stop", dependencies=[Depends(authorize)])
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

    @router.post("/api/v1/mock-automation/resume", dependencies=[Depends(authorize)])
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

    return router

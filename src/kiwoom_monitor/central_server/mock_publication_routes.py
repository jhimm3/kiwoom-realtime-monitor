"""Candidate/specification publication policy; existing evidence storage is injected."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Protocol, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository


class MockPublicationEvidenceStore(Protocol):
    def load_account_bindings(self) -> list[dict[str, Any]]: ...

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]: ...


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

def create_mock_publication_router(
    store: MockPublicationEvidenceStore, repository: ForwardEvaluationRepository,
    authorize: Callable[..., None],
) -> APIRouter:
    """Repository holds only the same store reference; no execution/lifecycle owner is added."""
    router = APIRouter()

    @router.post(
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
                repository.publish_mock_automation_candidate,
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

    @router.get(
        "/api/v1/research/mock-automation-candidates/{account_ref}",
        dependencies=[Depends(authorize)],
    )
    async def list_mock_automation_candidates(
        account_ref: str,
        credential_profile_id: str = Query(
            min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$",
        ),
    ) -> dict[str, object]:
        binding = await asyncio.to_thread(current_mock_binding, credential_profile_id)
        if binding is None or str(binding.get("account_ref", "")) != account_ref:
            raise HTTPException(status_code=409, detail="current verified mock binding mismatch")
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

    @router.post(
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

    @router.get(
        "/api/v1/research/mock-automation-specs/{account_ref}",
        dependencies=[Depends(authorize)],
    )
    async def list_mock_automation_specs(account_ref: str) -> dict[str, object]:
        from kiwoom_monitor.domain.execution_activation import assess_mock_automation_readiness
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

    return router

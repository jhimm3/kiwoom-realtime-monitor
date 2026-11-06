"""Explicit selected-order assessment and closed installation; neither grants authority."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .observation_frame_recovery import drain_thread_call


class SelectedOrderRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credential_profile_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,96}$')
    credential_revision: int = Field(strict=True, ge=1)
    account_ref: str = Field(min_length=36, max_length=36)
    binding_revision: int = Field(strict=True, ge=1)
    packet_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_identity_proof: dict | None = None

    @field_validator('source_identity_proof')
    @classmethod
    def signed_fields_only(cls, value):
        from kiwoom_monitor.application.selected_order_recovery import validate_source_identity_proof
        return validate_source_identity_proof(value)


class SelectedOrderManagementRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credential_profile_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,96}$')
    credential_revision: int = Field(strict=True, ge=1)
    account_ref: str = Field(min_length=36, max_length=36)
    binding_revision: int = Field(strict=True, ge=1)
    packet_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_settings_revision: int = Field(strict=True, ge=1)


class SelectedOrderManagementAcceptance(SelectedOrderManagementRequest):
    expected_control_revision: int = Field(strict=True, ge=1)


def create_selected_order_recovery_router(store, credential_owner, authorize, *, broker_evidence_provider=None,
                                          supervisor=None):
    """The optional dated detail provider is server-owned. None is explicitly UNKNOWN."""
    router = APIRouter()

    @router.get('/api/v1/mock-automation/selected-order-evidence/{operation_id}/management',
                dependencies=[Depends(authorize)])
    async def management_status(operation_id: str):
        from kiwoom_monitor.domain.selected_order_evidence import COLLECTION, OWNER
        from .selected_order_bootstrap import INSTALLATIONS, CURRENT_MANAGEMENT
        def read():
            with store.mock_automation_read_scope() as reader:
                preserved = store.load_document(COLLECTION, OWNER, operation_id, _reader=reader)
                if preserved is None:
                    return None
                packet = preserved['document']['packet']
                installed = store.load_document(INSTALLATIONS, packet['account_ref'],
                    packet['order']['intent_id'], _reader=reader)
                current = (store.load_document(CURRENT_MANAGEMENT, installed['document']['target_account_ref'],
                    installed['document']['target_account_ref'], _reader=reader) if installed else None)
                management = current['document'] if current and current['document']['context']['operation_id'] == operation_id else None
                control = store.load_mock_automation_control(packet['account_ref'], _reader=reader)
                from kiwoom_monitor.infrastructure.persistence.execution_repository import (
                    _record_from_document, execution_remaining_quantity)
                native_order = store.load_execution_intent(packet['order']['intent_id'], _reader=reader)
                record = _record_from_document(native_order) if native_order else None
                head = (store.load_document('execution_mock_plan_positions', packet['account_ref'],
                    packet['order']['plan']['plan_id'], _reader=reader) if installed else None)
                summary = None
                if record is not None:
                    summary = dict(state=record.state.value, filled_quantity=record.detailed_filled_quantity,
                        working_quantity=(None if record.state.value in {'SUBMISSION_UNKNOWN', 'CANCEL_PENDING'}
                            or record.amendment is not None and record.amendment.reconciliation_pending
                            else execution_remaining_quantity(record)),
                        owned_quantity=head['document'].get('quantity') if head else None)
                lease_matches, read_settings_match, settings_match = False, False, False
                if management is not None:
                    lease_matches, read_settings_match, settings_match = store.load_selected_management_fences(management, _reader=reader)
                return dict(version='selected-order-management-status/v1', operation_id=operation_id,
                    packet_sha256=preserved['document']['receipt']['sha256'], symbol=packet['order']['symbol'],
                    original_quantity=packet['order']['quantity'], account_ref=packet['account_ref'],
                    installation=installed['document'] if installed else None,
                    management=management, control=control, current_order=summary,
                    management_ready=False, reconciliation_ready=False, new_orders_enabled=False,
                    _current_lease_matches=lease_matches, _current_read_settings_match=read_settings_match,
                    _current_settings_match=settings_match)
        value = await drain_thread_call(read)
        if value is None:
            raise HTTPException(404, 'SELECTED_ORDER_EVIDENCE_NOT_FOUND')
        if supervisor is not None:
            profile = ((value.get('management') or {}).get('context') or value.get('installation') or {}).get('credential_profile_id', '')
            value['supervisor'] = await drain_thread_call(supervisor.status, value['account_ref'], credential_profile_id=profile)
        management = value.get('management') or {}
        context = management.get('context') or {}
        bundle = credential_owner.bundle(context.get('credential_profile_id', '')) if credential_owner else None
        live_context = getattr(bundle, 'selected_management_context', None)
        runner = (value.get('supervisor') or {}).get('runner') or {}
        control = value.get('control') or {}
        value['reconciliation_ready'] = bool(bundle is not None and live_context is not None and live_context.to_dict() == context
            and not credential_owner._closing and not bundle._closing and bundle.runtime._active
            and credential_owner.active_revision(context['credential_profile_id']) == context['credential_revision']
            and getattr(credential_owner, '_settings_revisions', {}).get(context['credential_profile_id']) == management.get('settings_revision')
            and value['_current_lease_matches'] and value['_current_read_settings_match'])
        value['management_ready'] = bool(value['reconciliation_ready'] and management.get('status') == 'MANAGEMENT_READY'
            and value['_current_settings_match']
            and control.get('desired_state') == 'RUNNING' and control.get('control_revision') == management.get('accepted_control_revision')
            and (runner.get('spec_id'), runner.get('execution_run_id')) == (context.get('spec_id'), context.get('execution_run_id'))
            and runner.get('state') not in {'ERROR', 'BLOCKED'})
        value.pop('_current_lease_matches', None)
        value.pop('_current_read_settings_match', None)
        value.pop('_current_settings_match', None)
        return value

    async def evaluate(operation_id, request, *, install=False):
        if credential_owner is None:
            raise HTTPException(503, 'SELECTED_RECOVERY_IDENTITY_OWNER_UNAVAILABLE')
        from kiwoom_monitor.application.selected_order_recovery import assess_selected_order_recovery
        from kiwoom_monitor.domain.selected_order_evidence import COLLECTION, OWNER
        preserved = await drain_thread_call(store.load_document, COLLECTION, OWNER, operation_id)
        if preserved is None:
            raise HTTPException(404, 'SELECTED_ORDER_EVIDENCE_NOT_FOUND')
        preserved = preserved['document']
        if (preserved['receipt']['sha256'] != request.packet_sha256
                or preserved['receipt']['order_authority'] is not False):
            raise HTTPException(409, 'SELECTED_ORDER_EVIDENCE_RECEIPT_CONFLICT')
        try:
            if install:
                async def accept(identity, broker_evidence, current_owner):
                    return await credential_owner.install_verified_selected_order(preserved['packet'],
                        identity=identity, source_proof=request.source_identity_proof,
                        broker_evidence=broker_evidence, current_owner=current_owner)
                return await credential_owner.verify_selected_order_identity(
                    preserved['packet'], request.packet_sha256, request.source_identity_proof,
                    credential_profile_id=request.credential_profile_id, credential_revision=request.credential_revision,
                    account_ref=request.account_ref, binding_revision=request.binding_revision,
                    broker_evidence_provider=broker_evidence_provider, on_verified=accept)
            identity, broker_evidence = await credential_owner.verify_selected_order_identity(
                preserved['packet'], request.packet_sha256, request.source_identity_proof,
                credential_profile_id=request.credential_profile_id, credential_revision=request.credential_revision,
                account_ref=request.account_ref, binding_revision=request.binding_revision,
                broker_evidence_provider=broker_evidence_provider)
            return assess_selected_order_recovery(preserved['packet'], request.packet_sha256, identity,
                request.source_identity_proof, broker_evidence)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from None
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None

    @router.post('/api/v1/mock-automation/selected-order-evidence/{operation_id}/assessment', dependencies=[Depends(authorize)])
    async def assess(operation_id: str, request: SelectedOrderRecoveryRequest):
        return await evaluate(operation_id, request)

    @router.post('/api/v1/mock-automation/selected-order-evidence/{operation_id}/bootstrap', dependencies=[Depends(authorize)])
    async def bootstrap(operation_id: str, request: SelectedOrderRecoveryRequest):
        return await evaluate(operation_id, request, install=True)

    @router.post('/api/v1/mock-automation/selected-order-evidence/{operation_id}/prepare-management',
                 dependencies=[Depends(authorize)])
    async def prepare_management(operation_id: str, request: SelectedOrderManagementRequest):
        if credential_owner is None:
            raise HTTPException(503, 'SELECTED_RECOVERY_IDENTITY_OWNER_UNAVAILABLE')
        try:
            return await credential_owner.prepare_selected_order_management(operation_id,
                **request.model_dump(), broker_evidence_provider=broker_evidence_provider)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from None
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None

    @router.post('/api/v1/mock-automation/selected-order-evidence/{operation_id}/accept-management',
                 dependencies=[Depends(authorize)])
    async def accept_management(operation_id: str, request: SelectedOrderManagementAcceptance):
        if supervisor is None:
            raise HTTPException(503, 'SELECTED_MANAGEMENT_SUPERVISOR_UNAVAILABLE')
        try:
            return await supervisor.accept_selected_order_management(operation_id,
                **request.model_dump(), broker_evidence_provider=broker_evidence_provider)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from None
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None

    return router

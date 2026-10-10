"""Mock order HTTP contracts; runtime and repository retain order ownership."""
import asyncio
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from .account_query_routes import scoped_context

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .execution_runtime import ManualMockOrderGateway


def create_mock_order_routers(
    authorize: Callable[..., None], AccountTarget: type,
    current_mock_order_gateway: Callable[[], "ManualMockOrderGateway | None"],
    selected_mock: Callable[..., Any],
) -> tuple["APIRouter", "APIRouter"]:
    """Capture one gateway per request; preserve account and run fences."""
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel, ConfigDict, Field

    legacy_router, scoped_router = APIRouter(), APIRouter()

    class MockOrderRequest(BaseModel):
        request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
        symbol: str = Field(pattern=r"^\d{6}$")
        side: str = Field(pattern=r"^(BUY|SELL)$")
        quantity: int = Field(ge=1, le=1_000_000)
        limit_price: int = Field(ge=1)
        expires_seconds: int = Field(default=120, ge=10, le=600)

    class MockCancelRequest(BaseModel):
        quantity: int = Field(default=0, ge=0, le=1_000_000)

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

    @legacy_router.post("/api/v1/mock/orders", dependencies=[Depends(authorize)])
    async def submit_mock_order(command: MockOrderRequest) -> dict[str, object]:
        gateway = current_mock_order_gateway()
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

    @legacy_router.get("/api/v1/mock/orders/{intent_id}", dependencies=[Depends(authorize)])
    async def get_mock_order(intent_id: str) -> dict[str, object]:
        gateway = current_mock_order_gateway()
        if gateway is None:
            raise HTTPException(status_code=503, detail="모의주문 전송이 활성화되지 않았습니다.")
        try:
            record = await asyncio.to_thread(gateway.load, intent_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return await mock_order_document(record, gateway)

    @legacy_router.post("/api/v1/mock/orders/{intent_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel_mock_order(
        intent_id: str, command: MockCancelRequest,
    ) -> dict[str, object]:
        gateway = current_mock_order_gateway()
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

    async def scoped_order_record(bundle, intent_id):
        record = await asyncio.to_thread(bundle.repository.load, intent_id)
        if (record is None or record.intent.environment != "mock"
                or record.intent.account_ref != bundle.account_ref or record.intent.run_id != bundle.run_id):
            raise HTTPException(404, detail="MOCK_ORDER_NOT_FOUND")
        return record

    @scoped_router.post("/api/v2/mock/accounts/{account_ref}/orders", dependencies=[Depends(authorize)])
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

    @scoped_router.get("/api/v2/mock/accounts/{account_ref}/orders/{intent_id}", dependencies=[Depends(authorize)])
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

    @scoped_router.get("/api/v2/mock/accounts/{account_ref}/execution-events", dependencies=[Depends(authorize)])
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

    @scoped_router.post("/api/v2/mock/accounts/{account_ref}/orders/{intent_id}/cancel", dependencies=[Depends(authorize)])
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

    return legacy_router, scoped_router

"""Client realtime WebSocket policy; hub, collector and storage stay app-owned."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .realtime_hub import RealtimeHub


class RealtimeSnapshotReader(Protocol):
    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]: ...


class RealtimeInitialCollector(Protocol):
    def credential_connection_status(self) -> dict[str, Any]: ...

    def initial_realtime_snapshots(
        self, snapshots: list[dict[str, Any]], codes: list[str],
    ) -> list[dict[str, Any]]: ...


def create_realtime_router(
    realtime_hub: RealtimeHub, store: RealtimeSnapshotReader,
    valid_token: Callable[[str], bool], collector: RealtimeInitialCollector | None,
) -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/v1/realtime")
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
            reported_drops = 0
            while True:
                event = await subscriber.queue.get()
                if subscriber.dropped_events != reported_drops:
                    lost = subscriber.dropped_events - reported_drops
                    reported_drops = subscriber.dropped_events
                    await websocket.send_json({"type": "realtime_gap", "dropped_events": lost})
                await websocket.send_json(event)

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
                    snapshots = await asyncio.to_thread(
                        store.load_realtime_snapshots, sorted(subscriber.codes),
                    )
                    if collector is not None:
                        snapshots = collector.initial_realtime_snapshots(
                            snapshots, sorted(subscriber.codes),
                        )
                    for snapshot in snapshots:
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


    return router

"""Server subscription session lifecycle, separate from paid credential owners."""
import asyncio
import os
import time
from pathlib import Path
from threading import Event

from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTAuth, ChatGPTCredentialStore, ChatGPTPlanError, storage_path_for
from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient


def server_chatgpt_auth(secret_dir: str, *, local_config_path: str = "", now_provider=None):
    if os.name == "nt" and local_config_path:
        path = storage_path_for(Path(local_config_path))
        return ChatGPTAuth(ChatGPTCredentialStore(path), now_provider=now_provider)
    if os.name != "posix" or not secret_dir:
        return None
    from kiwoom_monitor.infrastructure.chatgpt_linux_store import LinuxChatGPTCredentialStore
    return ChatGPTAuth(LinuxChatGPTCredentialStore(Path(secret_dir) / "chatgpt-plan" / "session.enc"),
                       now_provider=now_provider)


def create_chatgpt_session_router(ai_service, authorize, *, local_login: bool = False):
    from fastapi import APIRouter, Depends, HTTPException, Request
    from pydantic import BaseModel, ConfigDict, Field
    router = APIRouter(dependencies=[Depends(authorize)])
    changes = asyncio.Lock()
    login_cancel: Event | None = None

    def service():
        if ai_service is None or ai_service.chatgpt_auth is None:
            raise HTTPException(503, "CHATGPT_SESSION_UNAVAILABLE")
        return ai_service

    def rejected(error):
        return HTTPException(503, detail=str(error))

    @router.get("/api/v1/news/chatgpt/status")
    async def status():
        selected = service()
        try:
            value = await asyncio.to_thread(selected.chatgpt_auth.status)
            pause_code = value.get("pause_code") or selected.chatgpt_failure
            return {**value, "paused": bool(pause_code),
                    "error_code": pause_code, "connection_method": "local" if local_login else "ssh"}
        except ChatGPTPlanError as error:
            raise rejected(error) from None

    @router.get("/api/v1/news/chatgpt/models")
    async def models():
        try:
            values = await asyncio.to_thread(ChatGPTPlanClient(service().chatgpt_auth).models)
            return {"models": [{"label": label, "slug": slug} for label, slug in values]}
        except ChatGPTPlanError as error:
            raise rejected(error) from None

    class AccountSelection(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")

    class LocalLogin(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_id: str = Field(default="", max_length=256, pattern=r"^[A-Za-z0-9_-]*$")

    async def change(action, account_id="", cancel: Event | None = None):
        selected = service()
        async with changes:
            try:
                await selected.pause_credentials("chatgpt_plan", require_idle=action == "connect")
            except ChatGPTPlanError as error:
                raise rejected(error) from None
            try:
                if action == "disconnect":
                    return await asyncio.to_thread(selected.chatgpt_auth.disconnect)
                if action == "connect":
                    value = await asyncio.to_thread(selected.chatgpt_auth.connect, cancel, account_id=account_id)
                elif action == "select":
                    def select():
                        auth = selected.chatgpt_auth
                        with auth.store.locked():
                            value = auth.store.load()
                            if account_id not in value["accounts"]:
                                raise ChatGPTPlanError("not_connected")
                            value["active"] = account_id
                            value["generation"] = time.time_ns()
                            auth.store.save(value)
                        return auth.status()
                    value = await asyncio.to_thread(select)
                else:
                    # Catalogue validates direct permission/refresh before resuming.
                    await asyncio.to_thread(ChatGPTPlanClient(selected.chatgpt_auth).models)
                    value = await asyncio.to_thread(selected.chatgpt_auth.status)
                await asyncio.to_thread(selected.chatgpt_auth.pause_analysis)
                selected.resume_chatgpt()
                return {**value, "pause_code": ""} if action == "connect" else value
            except ChatGPTPlanError as error:
                raise rejected(error) from None
            finally:
                selected.resume_credentials("chatgpt_plan")

    async def owned_change(action, account_id="", cancel: Event | None = None):
        # Caller disconnect does not release the drain or credential lock early.
        task = asyncio.create_task(change(action, account_id, cancel))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                if cancel is not None:
                    cancel.set()
        result = task.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    @router.post("/api/v1/news/chatgpt/select")
    async def select(values: AccountSelection):
        return await owned_change("select", values.account_id)

    @router.post("/api/v1/news/chatgpt/disconnect")
    async def disconnect():
        return await owned_change("disconnect")

    @router.post("/api/v1/news/chatgpt/resume")
    async def resume():
        return await owned_change("resume")

    if local_login:
        def require_loopback(request: Request) -> None:
            if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
                    or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}):
                raise HTTPException(403, "LOOPBACK_REQUIRED")

        @router.post("/api/v1/news/chatgpt/connect", dependencies=[Depends(require_loopback)])
        async def connect(values: LocalLogin):
            nonlocal login_cancel
            if login_cancel is not None or changes.locked():
                raise HTTPException(409, "CHATGPT_LOGIN_BUSY")
            login_cancel = Event()
            try:
                return await owned_change("connect", values.account_id, login_cancel)
            finally:
                login_cancel = None

        @router.post("/api/v1/news/chatgpt/connect/cancel", dependencies=[Depends(require_loopback)])
        async def cancel_connect():
            if login_cancel is not None:
                login_cancel.set()
            return {"cancel_requested": login_cancel is not None}

    return router

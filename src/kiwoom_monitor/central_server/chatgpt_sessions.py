"""NAS subscription session lifecycle, separate from paid credential owners."""
import asyncio
import os
import time
from pathlib import Path

from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTAuth, ChatGPTPlanError
from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient


def nas_chatgpt_auth(secret_dir: str):
    if os.name != "posix" or not secret_dir:
        return None
    from kiwoom_monitor.infrastructure.chatgpt_linux_store import LinuxChatGPTCredentialStore
    return ChatGPTAuth(LinuxChatGPTCredentialStore(Path(secret_dir) / "chatgpt-plan" / "session.enc"))


def create_chatgpt_session_router(ai_service, authorize):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, ConfigDict, Field
    router = APIRouter(dependencies=[Depends(authorize)])
    changes = asyncio.Lock()

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
            pause_code = value.get("pause_code") or selected._plan_failure
            return {**value, "paused": bool(pause_code),
                    "error_code": pause_code, "connection_method": "ssh"}
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

    async def change(action, account_id=""):
        selected = service()
        async with changes:
            await selected.pause_credentials("chatgpt_plan")
            try:
                if action == "disconnect":
                    return await asyncio.to_thread(selected.chatgpt_auth.disconnect)
                if action == "select":
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
                return value
            except ChatGPTPlanError as error:
                raise rejected(error) from None
            finally:
                selected.resume_credentials("chatgpt_plan")

    async def owned_change(action, account_id=""):
        # Caller disconnect does not release the drain or credential lock early.
        task = asyncio.create_task(change(action, account_id))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
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

    return router

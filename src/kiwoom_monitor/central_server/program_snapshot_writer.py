"""Own TOP20 program-flow batches, failed pending data, and actual save drain."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from kiwoom_monitor.domain.market_data_contract import MarketDataObservation

from .diagnostic_replay_runtime import owned_create_task, owned_to_thread


# Keep the operational log category used before moving the save owner.
logger = logging.getLogger("kiwoom_monitor.central_server.autonomous_top20")


class ProgramSnapshotStore(Protocol):
    def save_dataset_snapshots(
        self,
        values: list[tuple[
            str, str, str, dict[str, Any], MarketDataObservation[object] | None,
        ]],
    ) -> None: ...


class ProgramSnapshotWriter:
    """Latest pending value per stock; a detached batch has one owned save task."""

    def __init__(self, store: ProgramSnapshotStore) -> None:
        self._store = store
        self._pending: dict[str, dict[str, Any]] = {}
        self._save_task: asyncio.Task[None] | None = None

    def enqueue(self, snapshot: dict[str, Any]) -> None:
        self._pending[snapshot["subject"]] = snapshot

    async def flush(self) -> None:
        task = self._save_task
        if task is None or task.done():
            if not self._pending:
                return
            task = owned_create_task(self._save_pending(), name="nas-top20-program-save")
            self._save_task = task
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None,
            )
        # Cancelling a waiter does not abandon or overlap a detached DB batch.
        await asyncio.shield(task)

    async def drain(self) -> None:
        """After producers stop, join the old batch before flushing pending input."""
        if self._save_task is not None:
            try:
                await asyncio.shield(self._save_task)
            except Exception as error:
                logger.warning("NAS TOP20 프로그램수급 종료 저장 재시도: %s", error)
        await self.flush()

    async def _save_pending(self) -> None:
        pending, self._pending = self._pending, {}
        if not pending:
            return
        try:
            await owned_to_thread(self._write, tuple(pending.values()))
        except Exception:
            for code, value in pending.items():
                self._pending.setdefault(code, value)
            raise

    def _write(self, values: tuple[dict[str, Any], ...]) -> None:
        self._store.save_dataset_snapshots([
            (
                "program_flow", str(value["subject"]), str(value["snapshot_key"]),
                dict(value["payload"]), None,
            )
            for value in values
        ])

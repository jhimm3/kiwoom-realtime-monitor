"""Bounded, frames-only repair of an unversioned observation checkpoint.

The old execution cursor/state remain authoritative. Scratch frames are never
published until their complete prefix and the protocol share a successful native
checkpoint commit. This does not re-evaluate missed historical decisions.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame, KrxMinuteBarFrame,
    replay_candidate_universe, replay_krx_minute_bars,
)

logger = logging.getLogger(__name__)


def trim_bar_frames(bars, code: str, keep_count: int) -> dict:
    rows = sorted(
        (frame for frame in bars.values() if frame.code == code),
        key=lambda frame: (frame.bar_start, frame.available_at, frame.revision_id),
        reverse=True,
    )
    keep = {(frame.code, frame.observation_key) for frame in rows[:keep_count]}
    return {key: frame for key, frame in bars.items() if frame.code != code or key in keep}


def trim_universe_frames(universes, latest_at: str, horizon: int, *, use_maximum: bool) -> list:
    latest = datetime.fromisoformat(latest_at).astimezone(timezone.utc)
    if use_maximum and universes:
        latest = max(datetime.fromisoformat(frame.available_at).astimezone(timezone.utc)
                     for frame in universes)
    return [frame for frame in universes
            if (latest - datetime.fromisoformat(frame.available_at).astimezone(timezone.utc))
            .total_seconds() <= horizon][-5000:]


@dataclass
class ObservationFrameRecovery:
    target_cursor: int
    session_profile: str
    keep_bars: int
    universe_horizon: int
    universe_uses_maximum: bool
    after_sequence: int = 0
    rows_read: int = 0
    pages_read: int = 0
    elapsed_ms: float = 0.0
    complete: bool = False
    reason: str = "waiting_safe_prefix"
    bars: dict[tuple[str, str], KrxMinuteBarFrame] = field(default_factory=dict)
    universes: list[CandidateUniverseFrame] = field(default_factory=list)

    def advance(self, store: Any, kinds, limit: int) -> bool:
        if self.complete:
            return True  # Retry only the final checkpoint after a failed acknowledgement.
        started = perf_counter()
        page = store.load_observation_revision_page(
            self.after_sequence, kinds, max(1, int(limit)), through_sequence=self.target_cursor,
        )
        self.elapsed_ms += (perf_counter() - started) * 1000
        if not page.ready:
            self.reason = page.reason or "waiting_safe_prefix"
            return False
        if page.safe_through < self.target_cursor:
            raise RuntimeError("legacy_recovery_prefix_not_safe")
        if not page.rows and not page.exhausted:
            raise RuntimeError("legacy_recovery_page_has_no_progress")
        # Parse and validate the whole page before changing scratch progress.
        parsed = []
        previous = self.after_sequence
        for row in page.rows:
            sequence = int(row.get("accepted_sequence", 0))
            if not previous < sequence <= self.target_cursor or row.get("kind") not in kinds:
                raise RuntimeError("legacy_recovery_page_out_of_order_or_scope")
            previous = sequence
            universes = replay_candidate_universe((row,))
            bars = replay_krx_minute_bars((row,), strict=True, session_profile=self.session_profile)
            parsed.append((universes, bars))
        for universes, bars in parsed:
            if universes:
                self.universes.append(universes[0])
                self.universes = trim_universe_frames(
                    self.universes, universes[0].available_at, self.universe_horizon,
                    use_maximum=self.universe_uses_maximum,
                )
            if bars:
                frame = bars[0]
                self.bars[(frame.code, frame.observation_key)] = frame
                self.bars = trim_bar_frames(self.bars, frame.code, self.keep_bars)
        self.after_sequence = previous
        self.rows_read += len(page.rows)
        self.pages_read += 1
        self.complete = page.exhausted
        self.reason = "frames_rebuilt" if self.complete else "reading_prefix"
        return self.complete

    def receipt(self) -> dict[str, Any]:
        return {
            "kind": "legacy_frames_recovery", "target_cursor": self.target_cursor,
            "last_read_sequence": self.after_sequence, "rows_read": self.rows_read,
            "pages_read": self.pages_read, "reader_elapsed_ms": round(self.elapsed_ms, 3),
            "bar_frames": len(self.bars), "universe_frames": len(self.universes),
            "historical_decisions_recomputed": False, "complete": self.complete,
            "reason": self.reason,
        }


async def drain_thread_call(function, *args, **kwargs):
    """Cancellation must not let close outlive an owned native checkpoint call."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated close/cancel requests must not cancel the actual native work.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            logger.error("cancelled observation operation failed while draining",
                         exc_info=(type(error), error, error.__traceback__))
        raise

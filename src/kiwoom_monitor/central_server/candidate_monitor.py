"""NAS observation revisions to an order-free shadow candidate ledger."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    KRX_REGULAR_RESEARCH_PROFILE,
    SUPPORTED_RESEARCH_SESSION_PROFILES,
)
from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyState,
    evaluate_breakout_bar,
)
from kiwoom_monitor.application.research_families import (
    BREAKOUT_FAMILY_ID,
    shadow_monitor_id_for_config,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
    replay_candidate_universe,
    replay_krx_minute_bars,
)


logger = logging.getLogger(__name__)
INPUT_KINDS = ("top20_membership", "minute_bar")


class CandidateMonitor:
    """Consumes immutable central observations without creating fills or orders."""

    def __init__(
        self,
        store: Any,
        config: BreakoutStrategyConfig,
        *,
        poll_seconds: float,
        universe_max_age_seconds: int,
        session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
    ) -> None:
        if universe_max_age_seconds <= 0:
            raise ValueError("universe_max_age_seconds must be positive")
        self._store = store
        self._config = config
        self._poll_seconds = max(0.5, float(poll_seconds))
        self._universe_max_age_seconds = int(universe_max_age_seconds)
        if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
            raise ValueError(f"unsupported research session profile: {session_profile}")
        self._session_profile = session_profile
        self.monitor_id = shadow_monitor_id_for_config(
            BREAKOUT_FAMILY_ID, config, session_profile,
        )
        self._cursor = 0
        self._state = StrategyState()
        self._bars: dict[tuple[str, str], KrxMinuteBarFrame] = {}
        self._universes: list[CandidateUniverseFrame] = []
        self._quality: dict[str, Any] = {
            "status": "WARMUP", "reason": "not_started", "last_processed_sequence": 0,
        }
        self._task: asyncio.Task[None] | None = None
        self._restore_or_bootstrap()

    @classmethod
    def from_json(
        cls, store: Any, config_json: str, *, poll_seconds: float,
        universe_max_age_seconds: int,
        session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
    ) -> "CandidateMonitor":
        try:
            document = json.loads(config_json)
            if not isinstance(document, dict):
                raise ValueError("strategy config must be an object")
            config = BreakoutStrategyConfig(**document)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid shadow candidate strategy config: {error}") from error
        return cls(
            store, config, poll_seconds=poll_seconds,
            universe_max_age_seconds=universe_max_age_seconds,
            session_profile=session_profile,
        )

    @property
    def quality(self) -> dict[str, Any]:
        return {**self._quality, "monitor_id": self.monitor_id}

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="shadow-candidate-monitor")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception as error:  # keep the NAS collector alive; quality exposes the failure
                logger.exception("shadow candidate monitor iteration failed")
                self._quality = {
                    **self._quality, "status": "ERROR", "reason": str(error),
                    "last_error_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await asyncio.to_thread(self._save_checkpoint)
                except Exception:
                    logger.exception("shadow candidate monitor checkpoint failed")
            await asyncio.sleep(self._poll_seconds)

    def run_once(self, *, limit: int = 1000) -> int:
        observations = self._store.load_observation_revisions_after(
            self._cursor, INPUT_KINDS, limit,
        )
        for observation in observations:
            self._consume(observation)
            self._cursor = max(self._cursor, int(observation.get("accepted_sequence", 0)))
        if observations:
            self._save_checkpoint()
        return len(observations)

    def _consume(self, observation: Mapping[str, Any]) -> None:
        if observation.get("kind") == "top20_membership":
            frames = replay_candidate_universe((observation,))
            if frames:
                self._universes.append(frames[0])
                self._trim_universes(frames[0].available_at)
            return
        frames = replay_krx_minute_bars(
            (observation,), strict=True, session_profile=self._session_profile,
        )
        if not frames:
            if observation.get("kind") == "minute_bar":
                self._quality = {
                    **self._quality,
                    "status": "READY",
                    "reason": (
                        "outside_krx_regular_strategy_session"
                        if self._session_profile == KRX_REGULAR_RESEARCH_PROFILE
                        else f"outside_research_session:{self._session_profile}"
                    ),
                    "last_processed_sequence": int(observation.get("accepted_sequence", 0)),
                }
            return
        evaluation_bar = frames[0]
        self._bars[(evaluation_bar.code, evaluation_bar.observation_key)] = evaluation_bar
        self._trim_bars(evaluation_bar.code)
        usable_universes, freshness = self._fresh_universes(evaluation_bar.available_at)
        evaluation = evaluate_breakout_bar(
            run_id=self.monitor_id,
            evaluation_bar=evaluation_bar,
            bar_history=tuple(self._bars.values()),
            universe_frames=usable_universes,
            config=self._config,
            state=self._state,
        )
        self._state = evaluation.state
        candidate = evaluation.candidate_event.to_dict() if evaluation.candidate_event else None
        if candidate is not None:
            candidate["decision_reasons"] = list(evaluation.decision.reasons)
        expires_at = self._state.candidate_expires_at if candidate is not None else ""
        self._store.save_shadow_evaluation(
            self.monitor_id, evaluation.decision.to_dict(), candidate, expires_at,
        )
        self._quality = {
            "status": "READY" if freshness == "fresh" else "STALE",
            "reason": freshness if freshness != "fresh" else ";".join(evaluation.decision.reasons),
            "last_processed_sequence": int(observation.get("accepted_sequence", 0)),
            "last_decision_at": evaluation.decision.decided_at,
            "last_symbol": evaluation.decision.symbol,
            "strategy_state": self._state.status,
        }

    def _fresh_universes(self, at: str) -> tuple[tuple[CandidateUniverseFrame, ...], str]:
        cutoff = datetime.fromisoformat(at).astimezone(timezone.utc)
        eligible = [
            frame for frame in self._universes
            if datetime.fromisoformat(frame.available_at).astimezone(timezone.utc) <= cutoff
        ]
        if not eligible:
            return (), "candidate_universe_missing"
        latest = max(
            eligible,
            key=lambda frame: (
                datetime.fromisoformat(frame.available_at).astimezone(timezone.utc),
                frame.accepted_sequence,
                frame.revision_id,
            ),
        )
        age = (cutoff - datetime.fromisoformat(latest.available_at).astimezone(timezone.utc)).total_seconds()
        if age > self._universe_max_age_seconds:
            return (), f"candidate_universe_stale:{int(age)}s"
        return tuple(eligible), "fresh"

    def _restore_or_bootstrap(self) -> None:
        document = self._store.load_shadow_monitor_state(self.monitor_id)
        if document:
            try:
                if document.get("session_profile", KRX_REGULAR_RESEARCH_PROFILE) != self._session_profile:
                    raise ValueError("shadow checkpoint session profile mismatch")
                self._cursor = max(0, int(document.get("cursor", 0)))
                state = dict(document.get("strategy_state", {}))
                state["emitted_candidate_keys"] = tuple(state.get("emitted_candidate_keys", ()))
                self._state = StrategyState(**state)
                self._universes = [CandidateUniverseFrame(**value) for value in document.get("universes", [])]
                self._bars = {
                    (frame.code, frame.observation_key): frame
                    for frame in (KrxMinuteBarFrame(**value) for value in document.get("bars", []))
                }
                self._quality = dict(document.get("quality", self._quality))
                return
            except (TypeError, ValueError):
                logger.warning("invalid shadow checkpoint ignored", exc_info=True)
        seed = []
        for kind in INPUT_KINDS:
            seed.extend(self._store.load_observation_revisions(kind, limit=5000))
        if seed:
            self._cursor = max(int(value.get("accepted_sequence", 0)) for value in seed)
            self._universes = list(replay_candidate_universe(seed))
            for frame in replay_krx_minute_bars(
                seed, strict=True, session_profile=self._session_profile,
            ):
                self._bars[(frame.code, frame.observation_key)] = frame
            for code in {frame.code for frame in self._bars.values()}:
                self._trim_bars(code)
            if self._universes:
                self._trim_universes(self._universes[-1].available_at)
        self._quality = {
            "status": "WARMUP", "reason": "bootstrapped_without_historical_alerts",
            "last_processed_sequence": self._cursor,
        }
        self._save_checkpoint()

    def _trim_bars(self, code: str) -> None:
        rows = sorted(
            (frame for frame in self._bars.values() if frame.code == code),
            key=lambda frame: (frame.bar_start, frame.available_at, frame.revision_id),
            reverse=True,
        )
        keep = {(frame.code, frame.observation_key) for frame in rows[: self._config.lookback_bars + 2]}
        self._bars = {
            key: value for key, value in self._bars.items()
            if value.code != code or key in keep
        }

    def _trim_universes(self, latest_at: str) -> None:
        latest = max(
            datetime.fromisoformat(frame.available_at).astimezone(timezone.utc)
            for frame in self._universes
        ) if self._universes else datetime.fromisoformat(latest_at).astimezone(timezone.utc)
        horizon = max(
            self._universe_max_age_seconds,
            int(self._config.rank_window_seconds or 0) + int(self._config.rank_max_gap_seconds or 0),
        )
        self._universes = [
            frame for frame in self._universes
            if (latest - datetime.fromisoformat(frame.available_at).astimezone(timezone.utc)).total_seconds()
            <= horizon
        ][-5000:]

    def _save_checkpoint(self) -> None:
        self._store.save_shadow_monitor_state(self.monitor_id, {
            "schema_version": 1,
            "session_profile": self._session_profile,
            "cursor": self._cursor,
            "strategy_config": self._config.to_dict(),
            "strategy_state": self._state.to_dict(),
            "universes": [asdict(value) for value in self._universes],
            "bars": [asdict(value) for value in self._bars.values()],
            "quality": self._quality,
        })

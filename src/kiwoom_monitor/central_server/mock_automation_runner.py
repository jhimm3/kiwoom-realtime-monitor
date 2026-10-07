"""Persistent NAS runner for one admitted automatic mock-account strategy."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import StrategyState
from kiwoom_monitor.application.mock_automation_execution import (
    dispatch_mock_automation_decision_from_risk,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.application.research_families import get_research_family, parse_strategy_config
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
    replay_candidate_universe,
    replay_krx_minute_bars,
)
from kiwoom_monitor.domain.execution_activation import MockAutomationOperatingSpec
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


logger = logging.getLogger(__name__)
RUNNER_CHECKPOINT_VERSION = "mock_automation_runner_checkpoint/v1"
INPUT_KINDS = ("top20_membership", "minute_bar")


class MockAutomationRunner:
    """Consume new NAS observations once and pass actionable Decisions to O1.

    The strategy's open position is reconstructed only from detailed O1 FILL
    events.  A signal or an accepted order never creates a synthetic position.
    """

    def __init__(
        self,
        store: Any,
        bundle: Any,
        spec: MockAutomationOperatingSpec,
        *,
        poll_seconds: float = 1.0,
    ) -> None:
        context = bundle.automation_context
        if (
            context is None
            or bundle.account_ref != spec.account_scope.account_ref
            or bundle.run_id != context.execution_run_id
            or context.spec_id != spec.spec_id
        ):
            raise ValueError("MOCK_AUTOMATION_RUNNER_CONTEXT_MISMATCH")
        self._store = store
        self._bundle = bundle
        self._spec = spec
        self._repository = ForwardEvaluationRepository(store)
        package = self._repository.load_mock_automation_candidate_package(
            spec.strategy_ref, spec.candidate_package_hash,
        )
        if package is None:
            raise ValueError("MOCK_AUTOMATION_CANDIDATE_MISSING")
        candidate = package.candidate_spec
        family_id = str(candidate.get("family", ""))
        self._family = get_research_family(family_id)
        self._config = parse_strategy_config(family_id, candidate.get("parameters", {}))
        if spec.session_profile != str(candidate.get("session_profile", {}).get("profile", "")):
            raise ValueError("MOCK_AUTOMATION_SESSION_PROFILE_MISMATCH")
        self._poll_seconds = max(0.25, float(poll_seconds))
        self._cursor = 0
        self._fill_cursor = 0
        self._checkpoint_revision = 0
        self._state = StrategyState()
        self._bars: dict[tuple[str, str], KrxMinuteBarFrame] = {}
        self._universes: list[CandidateUniverseFrame] = []
        self._seen_fill_ids: set[str] = set()
        self._status: dict[str, Any] = {
            "state": "WARMUP", "reason": "not_started", "orders_enabled": False,
        }
        self._latency: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._restore_or_bootstrap()

    @property
    def status(self) -> dict[str, Any]:
        return {
            "version": "mock_automation_runner_status/v1",
            "account_ref": self._bundle.account_ref,
            "spec_id": self._spec.spec_id,
            "execution_run_id": self._bundle.run_id,
            "input_cursor": self._cursor,
            "fill_cursor": self._fill_cursor,
            "strategy_state": self._state.status,
            **self._status,
            "latency": dict(self._latency) if self._latency is not None else None,
        }

    async def start(self) -> None:
        if self._closing:
            raise RuntimeError("MOCK_AUTOMATION_RUNNER_CLOSED")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="mock-automation-runner")

    async def close(self) -> None:
        self._closing = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await asyncio.to_thread(self._save_checkpoint)

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("automatic mock runner iteration failed")
                self._status = {
                    "state": "ERROR", "reason": type(error).__name__,
                    "detail": str(error), "orders_enabled": False,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await asyncio.to_thread(self._save_checkpoint)
                except Exception:
                    logger.exception("automatic mock runner checkpoint failed")
            await asyncio.sleep(self._poll_seconds)

    async def run_once(self, *, limit: int = 1000) -> int:
        poll_started = perf_counter()
        await asyncio.to_thread(self._apply_new_fills)
        control = await asyncio.to_thread(
            self._repository.load_mock_automation_control, self._bundle.account_ref,
        )
        if control is None or control.active_spec_id != self._spec.spec_id:
            status = {
                "state": "BLOCKED", "reason": "automation_control_missing_or_replaced",
                "orders_enabled": False,
            }
            if status != self._status:
                self._status = status
                await asyncio.to_thread(self._save_checkpoint)
            return 0
        if control.desired_state.value != "RUNNING":
            status = {
                "state": "STOPPED", "reason": "user_stopped", "orders_enabled": False,
            }
            if status != self._status:
                self._status = status
                await asyncio.to_thread(self._save_checkpoint)
            return 0

        load_started = perf_counter()
        observations = await asyncio.to_thread(
            self._store.load_observation_revisions_after,
            self._cursor, INPUT_KINDS, limit,
        )
        load_ms = round((perf_counter() - load_started) * 1000)
        max_processing_ms = 0
        max_bar_age_ms: int | None = None
        bar_count = 0
        future_bar_count = 0
        last_bar_age_ms = self._latency.get("last_bar_age_ms") if self._latency else None
        last_bar_processed_at = self._latency.get("last_bar_processed_at") if self._latency else None
        for observation in observations:
            observation_started = perf_counter()
            await self._consume(observation)
            self._cursor = max(self._cursor, int(observation.get("accepted_sequence", 0)))
            await asyncio.to_thread(self._save_checkpoint)
            max_processing_ms = max(
                max_processing_ms, round((perf_counter() - observation_started) * 1000),
            )
            if observation.get("kind") == "minute_bar":
                bar_count += 1
                processed_at = datetime.now(timezone.utc)
                last_bar_processed_at = processed_at.isoformat()
                try:
                    available_at = datetime.fromisoformat(str(observation.get("available_at", "")))
                    if available_at.tzinfo is None:
                        raise ValueError("minute bar availability lacks timezone")
                    age_ms = round((processed_at - available_at).total_seconds() * 1000)
                except ValueError:
                    age_ms = -1
                if age_ms < 0:
                    future_bar_count += 1
                    last_bar_age_ms = None
                else:
                    last_bar_age_ms = age_ms
                    max_bar_age_ms = max(age_ms, max_bar_age_ms or 0)
        self._latency = {
            "last_poll_completed_at": datetime.now(timezone.utc).isoformat(),
            "last_poll_duration_ms": round((perf_counter() - poll_started) * 1000),
            "load_duration_ms": load_ms,
            "observation_count": len(observations),
            "minute_bar_count": bar_count,
            "max_observation_processing_ms": max_processing_ms,
            "max_minute_bar_age_ms": max_bar_age_ms,
            "future_or_invalid_bar_time_count": future_bar_count,
            "last_bar_age_ms": last_bar_age_ms,
            "last_bar_processed_at": last_bar_processed_at,
        }
        return len(observations)

    async def _consume(self, observation: Mapping[str, Any]) -> None:
        if observation.get("kind") == "top20_membership":
            frames = replay_candidate_universe((observation,))
            if frames:
                self._universes.append(frames[0])
                self._trim_universes(frames[0].available_at)
            return
        frames = replay_krx_minute_bars(
            (observation,), strict=True, session_profile=str(self._spec.session_profile),
        )
        if not frames:
            return
        evaluation_bar = frames[0]
        self._bars[(evaluation_bar.code, evaluation_bar.observation_key)] = evaluation_bar
        self._trim_bars(evaluation_bar.code)
        await asyncio.to_thread(self._apply_new_fills)
        active = await asyncio.to_thread(
            self._bundle.repository.active_intents,
            "mock", self._bundle.account_ref, self._bundle.run_id,
        )
        if active:
            self._status = {
                "state": "WAITING_ORDER", "reason": "active_intent_reconciliation",
                "pending_intent_id": active[0].intent.intent_id, "orders_enabled": False,
                "updated_at": evaluation_bar.available_at,
            }
            return
        evaluation = self._family.evaluate_bar(
            run_id=self._bundle.run_id,
            evaluation_bar=evaluation_bar,
            bar_history=tuple(self._bars.values()),
            universe_frames=tuple(self._universes),
            config=self._config,
            state=self._state,
        )
        self._state = evaluation.state
        decision = evaluation.decision
        if decision.final_action not in {"ENTER", "EXIT"}:
            self._status = {
                "state": "RUNNING", "reason": ";".join(decision.reasons),
                "last_decision_id": decision.decision_id, "orders_enabled": False,
                "updated_at": decision.decided_at,
            }
            return

        recovery = await self._bundle.monitor.refresh_recovery()
        risk = await asyncio.to_thread(
            self._repository.load_latest_mock_automation_risk, self._bundle.account_ref,
        )
        if risk is None:
            raise RuntimeError("MOCK_AUTOMATION_RISK_MISSING")
        if not self._state_matches_risk(risk):
            self._status = {
                "state": "BLOCKED", "reason": "strategy_state_risk_mismatch",
                "last_decision_id": decision.decision_id, "orders_enabled": False,
                "updated_at": decision.decided_at,
            }
            return
        await asyncio.to_thread(
            record_mock_automation_recovery_from_risk,
            self._repository, self._bundle.runtime, recovery, risk,
            account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
        )
        gate, record, _ = await asyncio.to_thread(
            dispatch_mock_automation_decision_from_risk,
            self._repository, self._bundle.runtime, decision, recovery, risk,
            account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id,
            strategy_ref=self._spec.strategy_ref,
        )
        self._status = {
            "state": "ORDER_SUBMITTED" if record is not None else "BLOCKED",
            "reason": ";".join(gate.reasons) if gate.reasons else "approved",
            "last_decision_id": decision.decision_id,
            "last_gate_id": gate.gate_id,
            "pending_intent_id": record.intent.intent_id if record is not None else "",
            "orders_enabled": False,
            "updated_at": decision.decided_at,
        }

    def _apply_new_fills(self) -> None:
        while True:
            page = self._bundle.repository.account_events(
                "mock", self._bundle.account_ref,
                after_sequence=self._fill_cursor, limit=1000,
            )
            for event in page.events:
                self._fill_cursor = max(self._fill_cursor, event.accepted_sequence)
                if (
                    event.run_id != self._bundle.run_id
                    or event.event_type != "FILL"
                    or not event.broker_execution_id
                    or event.broker_execution_id in self._seen_fill_ids
                    or event.quantity <= 0
                    or event.price <= 0
                ):
                    continue
                self._seen_fill_ids.add(event.broker_execution_id)
                if event.side == "BUY":
                    old_quantity = (
                        int(self._state.position_quantity or 0)
                        if self._state.status == "open" and self._state.symbol == event.symbol else 0
                    )
                    old_price = int(self._state.entry_price or 0)
                    quantity = old_quantity + event.quantity
                    price = ((old_price * old_quantity) + (event.price * event.quantity)) // quantity
                    self._state = StrategyState(
                        status="open", symbol=event.symbol, entry_price=price,
                        position_quantity=quantity,
                        opened_at=(self._state.opened_at if old_quantity else event.occurred_at.isoformat()),
                        emitted_candidate_keys=self._state.emitted_candidate_keys,
                    )
                elif self._state.status == "open" and self._state.symbol == event.symbol:
                    remaining = max(0, int(self._state.position_quantity or 0) - event.quantity)
                    if remaining:
                        self._state = StrategyState(
                            status="open", symbol=self._state.symbol,
                            entry_price=self._state.entry_price,
                            position_quantity=remaining, opened_at=self._state.opened_at,
                            emitted_candidate_keys=self._state.emitted_candidate_keys,
                        )
                    else:
                        self._state = StrategyState(
                            status="cooldown",
                            cooldown_until=(event.occurred_at + timedelta(
                                seconds=int(self._config.cooldown_seconds),
                            )).isoformat(),
                            emitted_candidate_keys=self._state.emitted_candidate_keys,
                        )
            if not page.has_more:
                break

    def _state_matches_risk(self, risk: Any) -> bool:
        owned = {symbol: quantity for symbol, quantity in risk.owned_position_quantities}
        if self._state.status == "open":
            return owned == {self._state.symbol: int(self._state.position_quantity or 0)}
        return not owned

    def _restore_or_bootstrap(self) -> None:
        rows = self._store.load_documents(
            "execution_mock_automation_runner_current", self._bundle.account_ref, 1,
        )
        document = rows[0].get("document") if rows else None
        if isinstance(document, Mapping) and (
            document.get("version") == RUNNER_CHECKPOINT_VERSION
            and document.get("spec_id") == self._spec.spec_id
            and document.get("execution_run_id") == self._bundle.run_id
            and document.get("candidate_package_hash") == self._spec.candidate_package_hash
        ):
            try:
                self._cursor = max(0, int(document.get("input_cursor", 0)))
                self._fill_cursor = max(0, int(document.get("fill_cursor", 0)))
                self._checkpoint_revision = max(0, int(document.get("checkpoint_revision", 0)))
                state = dict(document.get("strategy_state", {}))
                state["emitted_candidate_keys"] = tuple(state.get("emitted_candidate_keys", ()))
                self._state = StrategyState(**state)
                self._bars = {
                    (frame.code, frame.observation_key): frame
                    for frame in (KrxMinuteBarFrame(**value) for value in document.get("bars", ()))
                }
                self._universes = [
                    CandidateUniverseFrame(**value) for value in document.get("universes", ())
                ]
                self._seen_fill_ids = set(str(value) for value in document.get("seen_fill_ids", ()))
                self._status = dict(document.get("status", self._status))
                return
            except (TypeError, ValueError):
                logger.warning("invalid automatic runner checkpoint ignored", exc_info=True)
        seed: list[dict[str, Any]] = []
        for kind in INPUT_KINDS:
            seed.extend(self._store.load_observation_revisions(kind, limit=5000))
        if seed:
            self._cursor = max(int(value.get("accepted_sequence", 0)) for value in seed)
            self._universes = list(replay_candidate_universe(seed))
            for frame in replay_krx_minute_bars(
                seed, strict=True, session_profile=str(self._spec.session_profile),
            ):
                self._bars[(frame.code, frame.observation_key)] = frame
            for code in {frame.code for frame in self._bars.values()}:
                self._trim_bars(code)
            if self._universes:
                self._trim_universes(self._universes[-1].available_at)
        self._apply_new_fills()
        self._status = {
            "state": "WARMUP", "reason": "bootstrapped_without_historical_orders",
            "orders_enabled": False,
        }
        self._save_checkpoint()

    def _trim_bars(self, code: str) -> None:
        rows = sorted(
            (value for value in self._bars.values() if value.code == code),
            key=lambda value: (value.bar_start, value.available_at, value.revision_id),
            reverse=True,
        )
        keep = {
            (value.code, value.observation_key)
            for value in rows[: int(self._config.lookback_bars) + 2]
        }
        self._bars = {
            key: value for key, value in self._bars.items()
            if value.code != code or key in keep
        }

    def _trim_universes(self, latest_at: str) -> None:
        latest = datetime.fromisoformat(latest_at).astimezone(timezone.utc)
        horizon = max(
            int(self._spec.maximum_data_gap_seconds or 0),
            int(self._config.rank_window_seconds or 0)
            + int(self._config.rank_max_gap_seconds or 0),
        )
        self._universes = [
            value for value in self._universes
            if (latest - datetime.fromisoformat(value.available_at).astimezone(timezone.utc)).total_seconds()
            <= horizon
        ][-5000:]

    def _save_checkpoint(self) -> None:
        self._checkpoint_revision += 1
        document = {
            "version": RUNNER_CHECKPOINT_VERSION,
            "account_ref": self._bundle.account_ref,
            "spec_id": self._spec.spec_id,
            "candidate_package_hash": self._spec.candidate_package_hash,
            "execution_run_id": self._bundle.run_id,
            "checkpoint_revision": self._checkpoint_revision,
            "input_cursor": self._cursor,
            "fill_cursor": self._fill_cursor,
            "pending_intent_id": str(self._status.get("pending_intent_id", "")),
            "strategy_state": self._state.to_dict(),
            "seen_fill_ids": sorted(self._seen_fill_ids),
            "universes": [asdict(value) for value in self._universes],
            "bars": [asdict(value) for value in self._bars.values()],
            "status": self._status,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        self._store.upsert_documents(
            "execution_mock_automation_runner_current",
            [{"owner": self._bundle.account_ref, "key": "current", "document": document}],
        )

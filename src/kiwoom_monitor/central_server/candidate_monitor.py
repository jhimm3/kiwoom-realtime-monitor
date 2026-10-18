"""NAS observation revisions to an order-free shadow candidate ledger."""

from __future__ import annotations

from kiwoom_monitor.domain.time_basis import kst_now, as_kst

from .diagnostic_replay_contract import captured_workload

from .diagnostic_workloads import is_paused
from .database_observation_readers import OBSERVATION_DELIVERY_PROTOCOL, ObservationRevisionPage
from .observation_frame_recovery import (
    ObservationFrameRecovery, drain_thread_call, trim_bar_frames, trim_universe_frames,
)

import asyncio
from collections import deque, OrderedDict
from contextlib import ExitStack
from copy import deepcopy
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from threading import RLock
from typing import Any, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    KRX_REGULAR_RESEARCH_PROFILE,
    SUPPORTED_RESEARCH_SESSION_PROFILES,
)
from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyState,
)
from kiwoom_monitor.application.leader_breakout import (
    LeaderBreakoutPolicy, evaluate_leader_breakout_trade, _id,
    document_breakout_reference, document_breakout_observation,
)
from kiwoom_monitor.application.pullback_reacceleration_strategy import PullbackReaccelerationConfig
from kiwoom_monitor.application.research_families import (
    BREAKOUT_FAMILY_ID,
    family_for_config,
    parse_strategy_config,
    shadow_monitor_id_for_config,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
    replay_candidate_universe,
    replay_krx_minute_bars,
)
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_from_document


logger = logging.getLogger(__name__)
INPUT_KINDS = ("top20_membership", "minute_bar")
SUPPORTED_INPUTS = ("minute_bar/KRX/closed/complete", "top20_membership/as_available")


class CandidateMonitor:
    """Consumes immutable central observations without creating fills or orders."""

    def __init__(
        self,
        store: Any,
        config: BreakoutStrategyConfig | PullbackReaccelerationConfig | LeaderBreakoutPolicy,
        *,
        poll_seconds: float,
        universe_max_age_seconds: int,
        session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
        now_provider=None,
    ) -> None:
        if universe_max_age_seconds <= 0:
            raise ValueError("universe_max_age_seconds must be positive")
        self._store = store
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
        self._config = config
        self._leader_policy = config if isinstance(config, LeaderBreakoutPolicy) else None
        if self._leader_policy is not None:
            self.monitor_id = _id("leader-breakout-monitor", config.to_dict())
            self._poll_seconds = max(0.5, float(poll_seconds))
            self._iteration_lock = RLock()
            self._leader_states = {}
            self._leader_symbols = frozenset(c.symbol for c in config.contexts)
            self._leader_pending = deque()
            self._leader_signatures = {}
            self._leader_accepting = False
            self._document_inputs = OrderedDict()
            self._document_references = {}
            self._document_observations = {}
            self._document_recorded = {}
            self._minute_commit_provider = lambda: None
            self._frame_recovery = None
            self._task = None
            self._quality = {"status": "BLOCKED" if config.missing("0B") else "WARMUP",
                "reason": ";".join("unconfigured:"+key for key in config.missing("0B")) or "awaiting_0B",
                "input_mode": "0B", "last_processed_sequence": 0}
            return
        self._family = family_for_config(config)
        if tuple(self._family.contract["required_inputs"]) != SUPPORTED_INPUTS:
            raise ValueError("unsupported shadow candidate input contract")
        self._poll_seconds = max(0.5, float(poll_seconds))
        self._universe_max_age_seconds = int(universe_max_age_seconds)
        if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
            raise ValueError(f"unsupported research session profile: {session_profile}")
        self._session_profile = session_profile
        self.monitor_id = shadow_monitor_id_for_config(
            self._family.family_id, config, session_profile,
        )
        self._cursor = 0
        self._state = StrategyState()
        self._bars: dict[tuple[str, str], KrxMinuteBarFrame] = {}
        self._universes: list[CandidateUniverseFrame] = []
        self._quality: dict[str, Any] = {
            "status": "WARMUP", "reason": "not_started", "last_processed_sequence": 0,
        }
        self._task: asyncio.Task[None] | None = None
        self._checkpoint_pending = False
        self._bootstrap_pending = True
        self._delivery_protocol: str | None = None
        self._frame_recovery: ObservationFrameRecovery | None = None
        self._frame_recovery_receipt: dict[str, Any] | None = None
        self._iteration_lock = RLock()
        self._restore_or_bootstrap()

    @classmethod
    def from_json(
        cls, store: Any, config_json: str, *, poll_seconds: float,
        universe_max_age_seconds: int,
        session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
        family_id: str = BREAKOUT_FAMILY_ID,
        now_provider=None,
    ) -> "CandidateMonitor":
        try:
            document = json.loads(config_json)
            if not isinstance(document, dict):
                raise ValueError("strategy config must be an object")
            config = (LeaderBreakoutPolicy.from_dict(document) if "policy_id" in document
                      else parse_strategy_config(family_id, document))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid shadow candidate strategy config: {error}") from error
        return cls(
            store, config, poll_seconds=poll_seconds,
            universe_max_age_seconds=universe_max_age_seconds,
            session_profile=session_profile,
            now_provider=now_provider,
        )

    @property
    def required_inputs(self) -> tuple[str, ...]:
        """The registered strategy contract; this reader accepts complete KRX minute bars."""
        return (("kiwoom-websocket-0B/KRX", "explicit_leader_structure_evidence") if self._leader_policy
                else tuple(self._family.contract["required_inputs"]))

    @property
    def quality(self) -> dict[str, Any]:
        value = {**self._quality, "monitor_id": self.monitor_id}
        if self._leader_policy is not None and self._leader_policy.reference_level is not None:
            with self._iteration_lock:
                value['document_baseline_observations'] = deepcopy(tuple(self._document_observations.values()))
        if self._frame_recovery is not None:
            value.update(status="WARMUP", reason="legacy_frames_recovery",
                         input_recovery=self._frame_recovery.receipt())
        return value

    def set_minute_commit_provider(self, provider):
        """Reuse the existing native close marker; this does not own its writer."""
        self._minute_commit_provider = provider

    def _refresh_document_observations(self):
        if self._leader_policy.reference_level is None or not self._leader_accepting:
            return
        with self._iteration_lock:
            pending = tuple(self._document_inputs.items())
        token = self._minute_commit_provider()
        for symbol, item in pending:
            if not self._leader_accepting:
                break
            cut = datetime.fromisoformat(item['available_at'])
            # Same native two-second close fence as the existing minute reader.
            end = (cut-timedelta(seconds=2)).replace(second=0,microsecond=0)
            key = (cut.date().isoformat(),end,token)
            cached = self._document_references.get(symbol)
            if cached is None or cached[0] != key:
                seconds = self._leader_policy.reference_level['window_seconds']
                if seconds and seconds % 60 == 0:
                    # Reference selection uses cut.floor, independently of the cache's close fence.
                    window_end = cut.replace(second=0, microsecond=0)
                    window_start = max(window_end - timedelta(seconds=seconds),
                                       window_end.replace(hour=0, minute=0))
                    rows = self._store.load_minute_bars(
                        symbol, cut.date().isoformat(), 'KRX',
                        start_minute=window_start.strftime('%H:%M'),
                        end_minute=window_end.strftime('%H:%M'),
                    ) if window_start < window_end else ()
                    reference_rows = []
                    for row in rows:
                        if isinstance(row.get('metadata'), dict):
                            metadata = market_metadata_from_document(row['metadata'])
                            row = {**row, 'completeness': metadata.completeness.value,
                                   'value_kind': metadata.value_kind.value, 'source': metadata.source,
                                   'available_at': (as_kst(metadata.available_at).timestamp()
                                                    if metadata.available_at is not None else None)}
                        reference_rows.append(row)
                    rows = reference_rows
                else:
                    rows = ()
                reference = document_breakout_reference(self._leader_policy,rows,symbol=symbol,
                    decision_time=cut.isoformat(),mode='0B')
                cached = (key,reference)
                with self._iteration_lock:
                    current = self._document_inputs.get(symbol)
                    if current is None or current['source'] != item['source'] or not self._leader_accepting:
                        continue
                    self._document_references[symbol] = cached
            reference = cached[1]
            # The cut can move across a minute while the worker is loading.
            if reference['window_end'] != cut.replace(second=0,microsecond=0).isoformat():
                continue
            with self._iteration_lock:
                current = self._document_inputs.get(symbol)
                if not self._leader_accepting or current is None or current['source'] != item['source']:
                    continue
                self._observe_document_baseline(symbol,current,reference)

    def _observe_document_baseline(self,symbol,item,reference):
        """Existing monitor lock/queue own facts and transition records, never orders."""
        if reference['window_end'] != datetime.fromisoformat(item['available_at']).replace(second=0,microsecond=0).isoformat():
            return
        observation = document_breakout_observation(self._leader_policy,reference,
            **{name:item[name] for name in ('price','observed_at','available_at','input_ref','previous','source','turnover_won')},mode='0B')
        self._document_observations[symbol] = observation
        signature = (observation['state'],reference['reference_ref'],observation['source'],tuple(observation['configuration_missing']))
        if self._document_recorded.get(symbol) == signature:
            return
        if len(self._leader_pending)>=1000:
            self._document_observations[symbol] = {**observation,'state':'DATA_BLOCKED',
                'reasons':[*observation['reasons'],'candidate_publication_backpressure']}
            return
        decision = {**item['decision'],'baseline_observation':observation}
        decision['decision_id'] = _id('document-baseline-decision',[self._leader_policy.fingerprint,decision])
        self._leader_pending.append((decision,None,''))
        self._document_recorded[symbol] = signature

    async def start(self) -> None:
        if self._leader_policy:
            self._leader_accepting = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="shadow-candidate-monitor")

    def operational_status(self, *, now: datetime | None = None) -> dict[str, object]:
        """Report universe freshness now, separately from last evaluation quality."""
        at = as_kst(now or self._now())
        if self._leader_policy:
            return {"scope": "leader_breakout_0B", "checked_at": at.isoformat(),
                "configuration_missing": list(self._leader_policy.missing("0B")),
                "last_evaluation": dict(self._quality), "current_all_inputs_fresh": None}
        # The worker publishes list replacements/appends; snapshot before inspecting.
        frames = tuple(self._universes)
        eligible = [datetime.fromisoformat(frame.available_at).astimezone(timezone.utc)
                    for frame in frames
                    if datetime.fromisoformat(frame.available_at).astimezone(timezone.utc) <= at]
        latest = max(eligible, default=None)
        age = (at - latest).total_seconds() if latest is not None else None
        quality = dict(self._quality)
        return {
            "scope": "shadow_candidate_inputs", "coverage": "partial",
            "checked_at": at.isoformat(),
            "universe": {
                "state": "missing" if latest is None else
                         "stale" if age > self._universe_max_age_seconds else "fresh",
                "latest_available_at": latest.isoformat() if latest is not None else None,
                "age_seconds": age, "max_age_seconds": self._universe_max_age_seconds,
            },
            "last_evaluation": {
                "status": "WARMUP" if self._frame_recovery is not None else quality.get("status", "UNKNOWN"),
                "at": quality.get("last_decision_at"),
                "last_processed_sequence": quality.get("last_processed_sequence", 0),
            },
            "current_all_inputs_fresh": None,
        }

    async def close(self) -> None:
        if self._leader_policy:
            self._leader_accepting = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        if self._leader_policy:
            await drain_thread_call(self.run_once)

    def project_leader_candidate_status(self, event):
        if self._leader_policy is None:
            return None
        with self._iteration_lock:
            if event.get("run_id") != self.monitor_id:
                return "SUPERSEDED"
            state = self._leader_states.get(event.get("symbol"))
            return "ACTIVE" if self._leader_accepting and state and state.episode_id == event.get("event_id") else "INVALIDATED"

    def observe_trade(self, payload, received_at, *, source, approved):
        """Pure bounded 0B judgment on the collector loop; persistence is deferred."""
        if self._leader_policy is None or not self._leader_accepting:
            return
        if payload.get("market", "KRX") != "KRX":
            return
        symbol = str(payload.get("code", ""))
        document_mode = self._leader_policy.reference_level is not None
        if symbol not in self._leader_symbols and not document_mode:
            return
        if len(symbol) != 6 or not symbol.isdigit():
            return
        with self._iteration_lock:
            if not approved or is_paused("candidate_monitor"):
                self._leader_states.pop(symbol, None)
                self._document_inputs.pop(symbol,None)
                self._document_observations.pop(symbol,None)
                self._document_references.pop(symbol,None)
                self._document_recorded.pop(symbol,None)
                self._quality = {**self._quality, "status": "BLOCKED", "reason": "0B_source_not_ready_or_paused"}
                return
            try:
                decision, state = evaluate_leader_breakout_trade(self._leader_policy, payload,
                    received_at=received_at, source=source, state=self._leader_states.get(symbol))
            except (ValueError, TypeError, KeyError) as error:
                self._leader_states.pop(symbol, None)
                self._quality = {**self._quality, "status": "BLOCKED", "reason": "invalid_0B:"+str(error)}
                return
            self._leader_states[symbol] = state
            if document_mode:
                previous = self._document_inputs.get(symbol)
                if previous is not None and previous['source'] == source+':'+received_at.date().isoformat() and (
                        datetime.fromisoformat(decision['available_at']) <= datetime.fromisoformat(previous['available_at'])
                        or datetime.fromisoformat(decision['observed_at']) < datetime.fromisoformat(previous['observed_at'])):
                    return
                if type(decision['signal_reference_price']) is not int or decision['signal_reference_price'] <= 0:
                    return
                current = {'price':decision['signal_reference_price'],'observed_at':decision['observed_at'],
                    'available_at':decision['available_at'],'input_ref':decision['input_ref'],
                    'source':source+':'+received_at.date().isoformat(),'turnover_won':decision['turnover_won']}
                current['previous'] = ({name:previous[name] for name in ('price','observed_at','available_at','source')}
                    if previous is not None else None)
                current['decision'] = decision
                self._document_inputs[symbol] = current
                self._document_inputs.move_to_end(symbol)
                while len(self._document_inputs)>200:
                    removed,_=self._document_inputs.popitem(last=False)
                    self._leader_states.pop(removed,None)
                    self._leader_signatures.pop(removed,None)
                    self._document_observations.pop(removed,None)
                    self._document_references.pop(removed,None)
                    self._document_recorded.pop(removed,None)
                cached = self._document_references.get(symbol)
                if cached is not None:
                    self._observe_document_baseline(symbol,current,cached[1])
            self._quality = {**self._quality, "status": decision["status"], "reason": ";".join(decision["reasons"]),
                "last_decision_at": decision["decided_at"], "last_symbol": symbol}
            signature = (decision["status"], decision["proposal"], decision["context_ref"], tuple(decision["reasons"]))
            if not decision["new_candidate"] and self._leader_signatures.get(symbol) == signature:
                return
            if len(self._leader_pending) >= 1000:
                self._leader_pending.clear()
                self._leader_states.clear()
                self._leader_signatures.clear()
                self._document_inputs.clear()
                self._document_observations.clear()
                self._document_references.clear()
                self._document_recorded.clear()
                self._quality = {**self._quality, "status": "BLOCKED", "reason": "candidate_publication_backpressure"}
                return
            candidate = None
            if decision["new_candidate"]:
                candidate = {"event_id": decision["episode_id"], "dedup_key": decision["episode_id"],
                    "run_id": self.monitor_id, "symbol": symbol, "strategy_id": "leader_breakout",
                    "strategy_version": "v1", "setup": "explicit_leader_structure_cross",
                    "reference_revision_id": decision["context_ref"], "transition": "observed_cross",
                    "quantity": None, "signal_reference_price": decision["signal_reference_price"],
                    "available_at": decision["available_at"], "snapshot_id": decision["input_ref"],
                    "decision_id": decision["decision_id"], "order_authorized": False}
                from kiwoom_monitor.application.leader_breakout import LeaderBreakoutConfig
                context = next(c for c in self._leader_policy.contexts if c.evidence_ref == decision["context_ref"])
                candidate.update(evidence_version="leader_shadow_evidence/v1",
                    rules_hash=LeaderBreakoutConfig.from_dict(self._leader_policy.to_dict()).rules_hash,
                    context_hash=_id("leader-context/v1", asdict(context)), policy=self._leader_policy.to_dict(),
                    observation={"payload": deepcopy(payload), "received_at": received_at.isoformat(), "source": source,
                                 "approved": approved}, decision=deepcopy(decision))
            self._leader_pending.append((decision, candidate, decision["valid_until"] if candidate else ""))
            self._leader_signatures[symbol] = signature

    @captured_workload("shadow", "candidate_monitor")
    async def _run(self) -> None:
        while True:
            try:
                if not is_paused("candidate_monitor"):
                    await drain_thread_call(self.run_once)
            except Exception as error:  # keep the NAS collector alive; quality exposes the failure
                logger.exception("shadow candidate monitor iteration failed")
                self._quality = {
                    **self._quality, "status": "ERROR", "reason": str(error),
                    "last_error_at": self._now().isoformat(),
                }
                if not self._leader_policy:
                    try:
                        await drain_thread_call(self._save_checkpoint, reason="error")
                    except Exception:
                        logger.exception("shadow candidate monitor checkpoint failed")
            await asyncio.sleep(self._poll_seconds)

    def run_once(self, *, limit: int = 1000) -> int:
        if self._leader_policy:
            self._refresh_document_observations()
            saved = 0
            for _ in range(limit):
                with self._iteration_lock:
                    if not self._leader_pending:
                        break
                    item = self._leader_pending.popleft()
                try:
                    self._store.save_shadow_evaluation(self.monitor_id, *item)
                except Exception:
                    with self._iteration_lock:
                        self._leader_pending.appendleft(item)
                    raise
                saved += 1
            return saved
        with self._iteration_lock:
            if self._bootstrap_pending:
                self._bootstrap()
                return 0  # Initial history is seeded without historical decisions.
            if self._frame_recovery is not None:
                recovery = self._frame_recovery
                if recovery.advance(self._store, INPUT_KINDS, limit):
                    self._save_checkpoint(reason="legacy_frames_recovery", recovery=recovery)
                    self._bars, self._universes = recovery.bars, recovery.universes
                    self._delivery_protocol = OBSERVATION_DELIVERY_PROTOCOL
                    self._frame_recovery_receipt = recovery.receipt()
                    self._frame_recovery = None
                return 0  # Repair input frames only, never re-run historical strategy decisions.
            observations = self._store.load_observation_revisions_after(
                self._cursor, INPUT_KINDS, limit,
            )
            return self._consume_observations(observations)

    def consume_observation_page(
        self, page: ObservationRevisionPage, *, after_sequence: int, source_store: Any,
        _frame_cache: dict | None = None,
    ) -> int:
        """Consume a native safe-prefix page supplied in RAM, without reading the store."""
        with self._iteration_lock:
            if source_store is not self._store:
                raise ValueError("shadow page belongs to another source store")
            if self._bootstrap_pending or self._frame_recovery is not None:
                raise RuntimeError("shadow input recovery must finish before shared delivery")
            if not isinstance(page, ObservationRevisionPage):
                raise TypeError("shadow delivery requires a native observation page")
            if not 0 <= after_sequence <= self._cursor:
                raise ValueError("shadow page starts after the consumer cursor")
            page = deepcopy(page)
            if len(page.rows) > 5000:
                raise ValueError("shadow page exceeds the input budget")
            if not page.ready:
                if page.rows:
                    raise ValueError("unsafe shadow page must not contain observations")
                return self._consume_observations(())
            if page.safe_through < self._cursor:
                raise ValueError("shadow cursor is beyond the source safe prefix")
            previous = after_sequence
            for row in page.rows:
                sequence = int(row.get("accepted_sequence", 0))
                if not previous < sequence <= page.safe_through or row.get("kind") not in INPUT_KINDS:
                    raise ValueError("shadow page is out of order or source scope")
                previous = sequence
            if not page.rows and not page.exhausted:
                raise ValueError("shadow page has no progress")
            rows = tuple(row for row in page.rows if int(row["accepted_sequence"]) > self._cursor)
            return self._consume_observations(rows, _frame_cache=_frame_cache)

    def _consume_observations(self, observations, *, _frame_cache=None) -> int:
        for observation in observations:
            if _frame_cache is None:
                self._consume(observation)
            else:
                self._consume(observation, _frame_cache=_frame_cache)
            self._cursor = max(self._cursor, int(observation.get("accepted_sequence", 0)))
        if observations or self._checkpoint_pending:
            self._save_checkpoint(reason="processed" if observations else "retry")
        return len(observations)

    def _consume(self, observation: Mapping[str, Any], *, _frame_cache=None) -> None:
        # Only the shared native-page round supplies this cache. Its sequence keys
        # refer to that one validated page; immutable frames outlive no round.
        kind = observation.get("kind")
        key = (int(observation.get("accepted_sequence", 0)),
               self._session_profile if kind != "top20_membership" else "top20_membership")
        if _frame_cache is not None and key in _frame_cache:
            frames = _frame_cache[key]
        elif kind == "top20_membership":
            frames = tuple(replay_candidate_universe((observation,)))
        else:
            frames = tuple(replay_krx_minute_bars(
                (observation,), strict=True, session_profile=self._session_profile,
            ))
        if _frame_cache is not None:
            _frame_cache[key] = frames
        if observation.get("kind") == "top20_membership":
            if frames:
                self._universes.append(frames[0])
                self._trim_universes(frames[0].available_at)
            return
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
        evaluation = self._family.evaluate_bar(
            run_id=self.monitor_id,
            evaluation_bar=evaluation_bar,
            bar_history=tuple(self._bars.values()),
            universe_frames=usable_universes,
            config=self._config,
            state=self._state,
        )
        candidate = evaluation.candidate_event.to_dict() if evaluation.candidate_event else None
        if candidate is not None:
            candidate["decision_reasons"] = list(evaluation.decision.reasons)
        expires_at = evaluation.state.candidate_expires_at if candidate is not None else ""
        self._store.save_shadow_evaluation(
            self.monitor_id, evaluation.decision.to_dict(), candidate, expires_at,
        )
        self._state = evaluation.state
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
            if document.get("delivery_protocol") not in (None, OBSERVATION_DELIVERY_PROTOCOL):
                raise RuntimeError("unsupported_shadow_observation_delivery_protocol")
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
                self._delivery_protocol = document.get("delivery_protocol")
                self._frame_recovery_receipt = document.get("input_recovery")
                self._bootstrap_pending = False
                if self._delivery_protocol is None:
                    self._frame_recovery = ObservationFrameRecovery(
                        self._cursor, self._session_profile, self._config.lookback_bars + 2,
                        max(self._universe_max_age_seconds,
                            int(self._config.rank_window_seconds or 0)
                            + int(self._config.rank_max_gap_seconds or 0)), True,
                    )
                return
            except (TypeError, ValueError):
                logger.warning("invalid shadow checkpoint ignored", exc_info=True)
        self._bootstrap()

    def _bootstrap(self) -> bool:
        page = self._store.load_observation_bootstrap(INPUT_KINDS, per_kind_limit=5000)
        if not page.ready:
            self._quality = {
                **self._quality, "status": "WARMUP",
                "reason": "bootstrap_pending:" + page.reason,
            }
            return False
        seed = page.rows
        self._cursor = max((int(value.get("accepted_sequence", 0)) for value in seed), default=0)
        self._universes = list(replay_candidate_universe(seed))
        self._bars = {}
        if seed:
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
        self._delivery_protocol = OBSERVATION_DELIVERY_PROTOCOL
        self._bootstrap_pending = False
        try:
            self._save_checkpoint(reason="bootstrap")
        except Exception:
            self._bootstrap_pending = True
            raise
        return True

    def _trim_bars(self, code: str) -> None:
        self._bars = trim_bar_frames(self._bars, code, self._config.lookback_bars + 2)

    def _trim_universes(self, latest_at: str) -> None:
        horizon = max(
            self._universe_max_age_seconds,
            int(self._config.rank_window_seconds or 0) + int(self._config.rank_max_gap_seconds or 0),
        )
        self._universes = trim_universe_frames(self._universes, latest_at, horizon, use_maximum=True)

    def _save_checkpoint(self, *, reason: str = "manual", recovery=None) -> None:
        from .postgres_access import db_call_source

        if self._bootstrap_pending or (self._frame_recovery is not None and recovery is None):
            return  # A waiting or failed seed must never become a completed checkpoint.
        if recovery is not None and not recovery.complete:
            raise RuntimeError("cannot_checkpoint_partial_frame_recovery")
        # A failed final batch must retry even when no later observation arrives.
        self._checkpoint_pending = True
        # Replay frames are frozen and contain scalars or immutable tuples;
        # copying their field dictionaries avoids dataclasses.asdict deep copies.
        document = {
            "schema_version": 1,
            "session_profile": self._session_profile,
            "cursor": self._cursor,
            "strategy_config": self._config.to_dict(),
            "strategy_state": self._state.to_dict(),
            "universes": [vars(value).copy() for value in
                          (recovery.universes if recovery is not None else self._universes)],
            "bars": [vars(value).copy() for value in
                     (recovery.bars if recovery is not None else self._bars).values()],
            "quality": self._quality,
        }
        if recovery is not None:
            document["delivery_protocol"] = OBSERVATION_DELIVERY_PROTOCOL
            document["input_recovery"] = recovery.receipt()
        elif self._delivery_protocol is not None:
            document["delivery_protocol"] = self._delivery_protocol
            if self._frame_recovery_receipt is not None:
                document["input_recovery"] = self._frame_recovery_receipt
        with db_call_source(f"candidate_monitor.{reason}"):
            self._store.save_shadow_monitor_state(self.monitor_id, document)
        self._checkpoint_pending = False


def run_shadow_monitors_once(monitors, *, limit: int = 1000) -> dict[str, Any]:
    """Read one safe page and fan it out; no task, writer, or shared strategy state."""
    monitors = tuple(monitors)
    if not 1 <= len(monitors) <= 32 or not 1 <= limit <= 5000:
        raise ValueError("shared shadow delivery requires 1..32 consumers and a 1..5000 row budget")
    if any(not isinstance(monitor, CandidateMonitor) for monitor in monitors):
        raise TypeError("shared shadow delivery requires candidate monitors")
    if any(monitor._leader_policy is not None for monitor in monitors):
        raise ValueError("0B leader monitors do not consume historical observation pages")
    store = monitors[0]._store
    if any(monitor._store is not store for monitor in monitors):
        raise ValueError("shared shadow consumers must use the same source store")
    if len({monitor.monitor_id for monitor in monitors}) != len(monitors):
        raise ValueError("shared shadow consumers must have distinct monitor IDs")
    result = {"processed": {}, "errors": {}, "deferred": [], "checkpoint_pending": {},
              "cursors": {}, "shared_reads": 0,
              "rows_read": 0, "after_sequence": None, "safe_through": None, "read_state": "idle"}
    with ExitStack() as locks:
        for monitor in sorted(monitors, key=lambda monitor: monitor.monitor_id):
            locks.enter_context(monitor._iteration_lock)
        if any(monitor._task is not None and not monitor._task.done() for monitor in monitors):
            raise RuntimeError("individual shadow worker must stop before shared delivery")
        ready = []
        for monitor in monitors:
            result["processed"][monitor.monitor_id] = 0
            if monitor._bootstrap_pending or monitor._frame_recovery is not None:
                result["deferred"].append(monitor.monitor_id)
                try:
                    monitor.run_once(limit=limit)
                except Exception as error:
                    result["errors"][monitor.monitor_id] = f"{type(error).__name__}: {error}"
                result["checkpoint_pending"][monitor.monitor_id] = monitor._checkpoint_pending
                result["cursors"][monitor.monitor_id] = monitor._cursor
            else:
                ready.append(monitor)
        if not ready:
            return result
        after = min(monitor._cursor for monitor in ready)
        page = deepcopy(store.load_observation_revision_page(after, INPUT_KINDS, limit))
        result.update(shared_reads=1, rows_read=len(page.rows), after_sequence=after,
                      safe_through=page.safe_through,
                      read_state="ready" if page.ready else page.reason or "waiting_safe_prefix")
        frame_cache = {}
        for monitor in ready:
            previous_cursor = monitor._cursor
            try:
                result["processed"][monitor.monitor_id] = monitor.consume_observation_page(
                    page, after_sequence=after, source_store=store, _frame_cache=frame_cache,
                )
            except Exception as error:
                result["errors"][monitor.monitor_id] = f"{type(error).__name__}: {error}"
                result["processed"][monitor.monitor_id] = sum(
                    previous_cursor < int(row["accepted_sequence"]) <= monitor._cursor for row in page.rows
                )
            result["checkpoint_pending"][monitor.monitor_id] = monitor._checkpoint_pending
            result["cursors"][monitor.monitor_id] = monitor._cursor
    return result

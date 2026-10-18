"""Shared RAM evaluation with bounded native minute-history reads; no broker polling."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import math
import json
import uuid

from kiwoom_monitor.application.market_research_features import MarketFeatureParameters, MarketMember, MarketResearchFrame
from kiwoom_monitor.application.market_theme_shadow import (
    MarketThemeShadowInputs, StockThesisShadowRequest,
    MarketPriceTradeShadowInputs,
    evaluate_market_theme_shadow, evaluate_stock_thesis_shadow, evaluate_market_theme_shadow_batch, _same_shadow_scope,
    _attach_price_candidate_value, compute_minute_trade_candidates,
)
from kiwoom_monitor.application.theme_leadership import ThemeLeadershipParameters, ThemeLeadershipState
from kiwoom_monitor.domain.ranking import normalize_stock_code
from .observation_frame_recovery import drain_thread_call

logger = logging.getLogger(__name__)
_RAM_INPUT_EVENTS = frozenset({"trade", "stock_reference", "context", "source_context", "connection_opened", "subscription_ready", "codes_added", "error"})
SHADOW_RESULT_COLLECTION = "ram_shadow_diagnostics"
SHADOW_RESULT_OWNER = "market_theme"
SHADOW_SELECTION_COLLECTION = "ram_shadow_selection"


def _copy_shadow_inputs(inputs):
    """Keep immutable market frames common; preserve the copy boundary otherwise.

    Frozen dataclasses may still contain caller-supplied lists. Only the exact
    frame/member types with tuple members/themes and scalar fields are shared.
    Validated immutable 0B point tuples are also shared; mutable state/mappings retain the copy fence.
    """
    if not isinstance(inputs, MarketThemeShadowInputs):
        return deepcopy(inputs)
    frames = inputs.market_frames
    scalar_types = (str, int, type(None))
    immutable = type(frames) is tuple and all(
        type(frame) is MarketResearchFrame and type(frame.members) is tuple
        and all(type(value) in scalar_types for key, value in vars(frame).items() if key != "members")
        and all(type(member) is MarketMember and type(member.themes) is tuple
            and all(type(theme) is str for theme in member.themes)
            and all(type(value) in scalar_types for key, value in vars(member).items() if key != "themes")
            for member in frame.members)
        for frame in frames)
    return deepcopy(inputs, {id(frames): frames} if immutable else None)


def validate_ram_shadow_selection(value):
    """Explicit bounded observation and optional research snapshot; no order authority."""
    required = {
        "version", "revision", "enabled", "theme_ids", "leadership_venue", "recording"
    }
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"stock_thesis", "position_comparison", "position_account_live", "position_allocation"}:
        raise ValueError("invalid RAM shadow selection fields")
    if value["version"] != "ram-shadow-selection/v1" or type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("invalid RAM shadow selection version or revision")
    if type(value["enabled"]) is not bool or type(value["recording"]) is not bool:
        raise ValueError("RAM shadow enabled/recording must be boolean")
    themes = value["theme_ids"]
    if (not isinstance(themes, list) or len(themes) > 32 or
        any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 120 for v in themes) or
        len(set(themes)) != len(themes)):
        raise ValueError("select at most 32 distinct nonempty theme IDs")
    if value["leadership_venue"] not in {"KRX", "NXT"}:
        raise ValueError("RAM shadow requires a KRX/NXT venue")
    request = _selected_stock_thesis(value)
    if request is not None and request.entry_thesis.theme_id not in themes:
        raise ValueError("selected research thesis theme must be an observed theme")
    document = value.get("position_comparison")
    live_account = value.get("position_account_live", False)
    if type(live_account) is not bool or (live_account and document is None):
        raise ValueError("live position observation requires explicit comparison inputs")
    if document is not None:
        from kiwoom_monitor.application.position_target import POSITION_ACTION_COMPARISON_SHADOW_VERSION
        if not isinstance(document, dict) or document.get("schema") != POSITION_ACTION_COMPARISON_SHADOW_VERSION:
            raise ValueError("position comparison requires a fixed input document")
        if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 256 * 1024:
            raise ValueError("position comparison input exceeds 256 KiB")
        if value["leadership_venue"] != "KRX":
            raise ValueError("position comparison currently supports mock KRX only")
    result = deepcopy(value)
    allocation=selected_position_allocation(value)
    if allocation is not None:
        if value['leadership_venue']!='KRX':raise ValueError('native allocation supports KRX only')
        if document is not None and document['requests'][0]['account_ref']!=allocation['policy'].account_ref:
            raise ValueError('shared position comparison/allocation must use one account')
    else:
        result.pop('position_allocation',None)
    if value["enabled"] and not themes and (not live_account or allocation is not None
            or position_comparison_requires_theme_context(document)):
        raise ValueError("RAM shadow requires explicit themes unless a theme-independent native R4 policy is selected")
    if result.get("stock_thesis") is None:
        result.pop("stock_thesis", None)
    if result.get("position_comparison") is None:
        result.pop("position_comparison", None)
    if not live_account:
        result.pop("position_account_live", None)
    return result


def position_comparison_requires_theme_context(document):
    """Only an explicitly validated v2 policy may omit the theme startup dependency."""
    if document is None:
        return True
    from kiwoom_monitor.application.position_target import (
        PositionActionCapabilitySelectionPolicy, position_action_comparison_inputs_from_document,
    )
    try:
        parameters = position_action_comparison_inputs_from_document(document)[0]
    except (KeyError, AttributeError) as error:
        raise ValueError("position comparison input is incomplete") from error
    policy = parameters.selection_policy
    return not (isinstance(policy, PositionActionCapabilitySelectionPolicy)
                and not {"stock_thesis", "theme_context"}.intersection(policy.required_data_capabilities))


def selected_position_allocation(selection):
    document=selection.get('position_allocation')
    if document is None:return None
    from .market_theme_shadow_inputs import compile_dynamic_allocation_ram_selection
    return compile_dynamic_allocation_ram_selection(document)


def selected_position_comparison(selection):
    """Compile supplied fixed inputs once during selection preparation, never on ticks/reads."""
    document = selection.get("position_comparison")
    if document is None:
        return None
    from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_document
    try:
        result = evaluate_position_action_comparison_document(document)
    except (KeyError, AttributeError) as error:
        raise ValueError("position comparison input is incomplete") from error
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 256 * 1024:
        raise ValueError("position comparison result exceeds 256 KiB")
    return result


def _selected_stock_thesis(selection, *, decision_time=None):
    from .market_theme_shadow_inputs import persisted_stock_thesis_shadow_request_from_evidence
    value = selection.get("stock_thesis")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"evidence", "thesis_policy_version"}:
        raise ValueError("research thesis selection requires evidence and an explicit policy")
    evidence = value["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"thesis", "leadership", "run_status", "stored_identity"}:
        raise ValueError("invalid selected research thesis evidence fields")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("selected research evidence exceeds one MiB")
    try:
        thesis = evidence["thesis"]
        return persisted_stock_thesis_shadow_request_from_evidence(evidence,
            run_id=thesis["run_id"], thesis_id=thesis["thesis_id"],
            thesis_policy_version=value["thesis_policy_version"], leadership_venue=selection["leadership_venue"],
            decision_time=decision_time or thesis["available_at"])
    except (KeyError, AttributeError) as error:
        raise ValueError("selected research thesis evidence is incomplete") from error


def ram_shadow_bindings_from_snapshot(selection, snapshot, decision_time):
    """Use one native theme cut and explicit saved thesis; no new data/order source."""
    from kiwoom_monitor.application.theme_leadership import theme_memberships_from_snapshot
    selection = validate_ram_shadow_selection(selection)
    if not selection["enabled"] or not selection["theme_ids"]:
        return ()
    if snapshot is None:
        raise ValueError("selected theme snapshot is not available")
    memberships = {m.theme_id: m for m in theme_memberships_from_snapshot(snapshot)}
    if any(theme not in memberships for theme in selection["theme_ids"]):
        raise ValueError("selected theme is missing or has fewer than two members")
    if len({code for theme in selection["theme_ids"] for code in memberships[theme].member_codes}) > 200:
        raise ValueError("selected theme union exceeds 200 codes")
    request = _selected_stock_thesis(selection, decision_time=decision_time)
    return tuple((MarketThemeShadowMonitor(theme_id=theme, leadership_venue=selection["leadership_venue"],
        stock_thesis=request if request is not None and request.entry_thesis.theme_id == theme else None),
        MarketThemeShadowInputs(decision_time=decision_time, market_venue="COMBINED",
            market_window="cumulative_session", leadership_venue=selection["leadership_venue"],
            market_frames=(), theme_membership=memberships[theme], second_points=(), continuity_status="UNAVAILABLE"))
        for theme in selection["theme_ids"])


class MarketThemeShadowMonitor:
    def __init__(
        self, *, theme_id: str, leadership_venue: str,
        market_parameters: MarketFeatureParameters = MarketFeatureParameters(),
        leadership_parameters: ThemeLeadershipParameters = ThemeLeadershipParameters(),
        stock_thesis: StockThesisShadowRequest | None = None, retry_seconds: float = 1.0,
    ) -> None:
        if not theme_id or leadership_venue not in {"KRX", "NXT"}:
            raise ValueError("shadow monitor requires an explicit theme and venue")
        if not math.isfinite(retry_seconds) or retry_seconds < 0.5:
            raise ValueError("failure retry delay must be finite and at least 0.5 seconds")
        if stock_thesis is not None and stock_thesis.thesis_venue != leadership_venue:
            raise ValueError("entry thesis and monitor venue differ")
        self._theme_id = theme_id
        self._venue = leadership_venue
        self._market_parameters = market_parameters
        self._leadership_parameters = leadership_parameters
        self._stock_thesis = stock_thesis
        self._retry_seconds = retry_seconds
        self._state = None
        self._result = None
        self._market_changes_receipt = None
        self._pending = None
        self._last_offered = None
        self._last_error = ""
        self._evaluations = 0
        self._coalesced = 0
        self._task = None
        self._close_task = None
        self._closing = False
        self._closed = False
        self._shared_running = False
        self._wake = asyncio.Event()
        self._iteration_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def last_result(self):
        return deepcopy(self._result)

    @property
    def status(self):
        result = self._result
        running = self._task is not None and not self._task.done()
        return {
            "state": "STOPPED" if self._closed else "CLOSING" if self._closing else
                     "ERROR" if self._last_error else "RUNNING" if running else
                     "READY" if result is not None else "WARMUP",
            "evaluations": self._evaluations, "pending_snapshots": int(self._pending is not None),
            "coalesced_snapshots": self._coalesced, "coverage": "latest_snapshot",
            "last_error": self._last_error,
            "last_result_ref": (result.get("decision_id") or result.get("revision_id")) if result else None,
            "last_valid_until": (result.get("valid_until") or result.get("expires_at")) if result else None,
            "durable": False, "order_authorized": False,
        }

    async def offer_inputs(self, inputs: MarketThemeShadowInputs) -> bool:
        """Accept a bounded current snapshot, retaining at most one waiting snapshot."""
        async with self._lifecycle_lock:
            if self._closing or self._closed:
                raise RuntimeError("market/theme shadow monitor is closed")
            self._validate_inputs(inputs)
            if inputs == self._last_offered:
                return False
            if self._pending is not None:
                self._coalesced += 1
            self._pending = _copy_shadow_inputs(inputs)
            self._last_offered = self._pending
            self._wake.set()
            return True

    def _validate_inputs(self, inputs, *, check_cut=True):
        if inputs.leadership_venue != self._venue or (
            inputs.theme_membership is not None and inputs.theme_membership.theme_id != self._theme_id
        ):
            raise ValueError("RAM inputs belong to another monitor theme or venue")
        if (len(inputs.market_frames) > 1000 or len(inputs.second_points) + len(getattr(inputs, "observation_points", ())) > 10000 or
            len(inputs.upper_limits) > 200 or any(len(frame.members) > 20 for frame in inputs.market_frames) or
            (inputs.theme_membership is not None and len(inputs.theme_membership.member_codes) > 200)):
            raise ValueError("RAM shadow snapshot exceeds the input budget")
        if check_cut and self._last_offered is not None and datetime.fromisoformat(inputs.decision_time) < \
            datetime.fromisoformat(self._last_offered.decision_time):
            raise ValueError("RAM shadow cut must not move backwards")

    async def start(self):
        async with self._lifecycle_lock:
            if self._closing or self._closed:
                raise RuntimeError("market/theme shadow monitor is closed")
            if self._shared_running:
                raise RuntimeError("shared shadow calculation must finish before starting an individual worker")
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="market-theme-shadow-monitor")

    async def run_once(self):
        async with self._iteration_lock:
            if self._closing or self._closed:
                raise RuntimeError("market/theme shadow monitor is closed")
            await self._consume_pending()
            return self.last_result

    async def _consume_pending(self):
        inputs = self._pending
        self._pending = None
        self._wake.clear()
        if inputs is None:
            return
        try:
            await drain_thread_call(self._evaluate_ram, inputs)
        except Exception:
            if self._pending is None:
                self._pending = inputs
            else:
                self._coalesced += 1
            self._wake.set()
            raise

    def _evaluate_ram(self, inputs):
        try:
            if self._state is not None:
                inputs = replace(inputs, previous_leadership_state=self._state)
            parameters = {"market_parameters": self._market_parameters,
                          "leadership_parameters": self._leadership_parameters}
            if self._stock_thesis:
                result = evaluate_stock_thesis_shadow(inputs, self._stock_thesis, **parameters)
            else:
                result = evaluate_market_theme_shadow(inputs, **parameters)
            self._publish_result(result)
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            raise
    def _publish_result(self, result):
        context = result["context"] if self._stock_thesis else result
        state = context["next_leadership_state"]
        next_state = ThemeLeadershipState(**state) if state is not None else None
        self._state = next_state
        self._result = result
        self._market_changes_receipt = None
        self._evaluations += 1
        self._last_error = ""

    async def _run(self):
        while True:
            await self._wake.wait()
            try:
                await self.run_once()
            except Exception:
                logger.exception("RAM market/theme shadow evaluation failed")
                await asyncio.sleep(self._retry_seconds)

    async def close(self):
        async with self._lifecycle_lock:
            if self._close_task is None:
                self._closing = True
                self._close_task = asyncio.create_task(self._close_owned(), name="market-theme-shadow-close")
            close_task = self._close_task
        await join_owned_close(close_task)

    async def _close_owned(self):
        try:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            async with self._iteration_lock:
                await self._consume_pending()
        finally:
            self._task = None
            self._closed = True


async def run_market_theme_shadow_monitors_once(monitors, *, include_market_changes=False):
    """Share identical cut/scope/parameters/previous-state calculation for one round.

    Uses each monitor's existing pending slot and iteration ownership; no worker,
    store or cross-round cache is introduced. Result/error slots follow input order.
    """
    monitors = tuple(monitors)
    if type(include_market_changes) is not bool:
        raise ValueError("include_market_changes must be boolean")
    if not 1 <= len(monitors) <= 32 or len({id(m) for m in monitors}) != len(monitors):
        raise ValueError("shared RAM shadow calculation requires 1..32 distinct monitors")
    if any(not isinstance(m, MarketThemeShadowMonitor) for m in monitors):
        raise TypeError("shared RAM shadow calculation requires market/theme monitors")
    report = {"evaluated": [], "errors": {}, "results": ()}
    async with AsyncExitStack() as locks:
        for monitor in sorted(monitors, key=id):
            await locks.enter_async_context(monitor._iteration_lock)
        if any(m._closing or m._closed or (m._task is not None and not m._task.done()) for m in monitors):
            raise RuntimeError("individual shadow workers must stop and monitors must be open")
        taken, groups, outcomes, observations = {}, [], {}, {}
        for index, monitor in enumerate(monitors):
            inputs = monitor._pending
            if inputs is None:
                continue
            taken[index] = inputs
            if monitor._state is not None:
                inputs = replace(inputs, previous_leadership_state=monitor._state)
            scope = (inputs, monitor._market_parameters, monitor._leadership_parameters)
            group = next((group for group in groups if _same_shadow_scope(group[0], scope)), None)
            if group is None:
                group = (scope, [])
                groups.append(group)
            group[1].append(index)

        def calculate():
            market_cache = []
            try:
                for (inputs, market_parameters, leadership_parameters), indices in groups:
                    try:
                        changes = [] if include_market_changes else None
                        results = evaluate_market_theme_shadow_batch(
                            inputs, tuple(monitors[i]._stock_thesis for i in indices),
                            market_parameters=market_parameters, leadership_parameters=leadership_parameters,
                            _market_cache=market_cache,
                            **({"_observed_changes": changes} if changes is not None else {}),
                        )
                        outcomes.update(zip(indices, results))
                        if changes:
                            observations.update((index, deepcopy(changes[0])) for index in indices)
                    except Exception as error:
                        outcomes.update((index, error) for index in indices)
            finally:
                market_cache.clear()

        for monitor in monitors:
            monitor._shared_running = True
            monitor._pending = None
            monitor._wake.clear()
        try:
            if taken:
                await drain_thread_call(calculate)
        finally:
            # drain_thread_call also joins on cancellation. Publish successful slots
            # and restore failed inputs before releasing iteration ownership.
            for index, inputs in taken.items():
                monitor = monitors[index]
                try:
                    result = outcomes[index]
                    if isinstance(result, Exception):
                        raise result
                    monitor._publish_result(result)
                    monitor._market_changes_receipt = ((monitor._result, observations[index])
                                                       if index in observations else None)
                    report["evaluated"].append(index)
                except Exception as error:
                    monitor._last_error = f"{type(error).__name__}: {error}"
                    report["errors"][index] = monitor._last_error
                    if monitor._pending is None:
                        monitor._pending = inputs
                    else:
                        monitor._coalesced += 1
                    monitor._wake.set()
            for monitor in monitors:
                monitor._shared_running = False
        report["results"] = tuple(m.last_result for m in monitors)
    return report


class MarketThemeShadowRuntime:
    """One owned Hub consumer for explicitly supplied order-free RAM bindings.

    Reads the Collector's canonical aggregate once per coalesced arrival, reuses
    common inputs/calculations, and owns all bound monitors through shutdown.
    Market/theme/reference context updates come from their existing owners.
    """
    def __init__(self, hub, collector, bindings, *, ranking_source=None, theme_source=None,
                  record_store=None, record_interval_seconds=30.0, position_comparison=None,
                  position_document=None, position_account_source=None, position_allocation=None):
        if not math.isfinite(record_interval_seconds) or record_interval_seconds < 0.5:
            raise ValueError("shadow recording interval must be finite and at least 0.5 seconds")
        self._bindings = tuple((monitor, _copy_shadow_inputs(context)) for monitor, context in bindings)
        self._position_comparison = deepcopy(position_comparison)
        self._position_account_source = position_account_source if position_document is not None else None
        self._allocation = deepcopy(position_allocation)
        self._allocation_account_source = position_account_source if position_allocation is not None else None
        if position_allocation is not None and position_account_source is None:raise ValueError('allocation requires native account RAM source')
        self._allocation_receipt = None
        self._allocation_models = self._allocation_model_document = None
        self._allocation_model_generation = 0
        self._allocation_reads = self._allocation_evaluations = 0
        self._account_cut_cache = None
        self._position_pending = position_account_source is not None
        self._position_inputs = None
        self._position_receipt = None
        self._position_reads = self._position_evaluations = 0
        if self._position_account_source is not None:
            from kiwoom_monitor.application.position_target import position_action_comparison_inputs_from_document
            self._position_inputs = position_action_comparison_inputs_from_document(deepcopy(position_document))
            self._position_comparison = None
            self._position_receipt = {"input_mode": "owner_ram_snapshot", "is_live_account": True,
                "result": None, "quality": {"status": "UNAVAILABLE", "reasons": ["account_observation_not_ready"]}}
        monitors = tuple(monitor for monitor, _ in self._bindings)
        r4_only = (not monitors and self._position_inputs is not None and self._allocation is None
                   and not position_comparison_requires_theme_context(position_document))
        if (not monitors and not r4_only) or len(monitors) > 32 or len({id(m) for m in monitors}) != len(monitors):
            raise ValueError("RAM runtime requires 1..32 distinct monitor bindings")
        for monitor, context in self._bindings:
            if not isinstance(monitor, MarketThemeShadowMonitor) or not isinstance(context, MarketThemeShadowInputs):
                raise TypeError("RAM runtime requires typed monitor/context bindings")
            monitor._validate_inputs(context)
            if context.theme_membership is None or monitor._closing or monitor._closed:
                raise ValueError("RAM runtime requires an open monitor and explicit theme membership")
            if not 1 <= monitor._leadership_parameters.window_seconds <= 30:
                raise ValueError("RAM leadership window exceeds the retention budget")
        self._monitors = monitors
        self._hub, self._collector = hub, collector
        self._subscriber = None
        self._task = self._close_task = None
        self._closing = self._closed = False
        self._retention_owned = False
        self._pending_context = True
        self._ranking_source = ranking_source if monitors else None
        self._theme_source = theme_source if monitors else None
        self._ranking_pending = self._ranking_source is not None
        self._theme_pending = self._theme_source is not None
        self._theme_refreshing = False
        self._ranking_ref = self._ranking_available = self._theme_ref = None
        self._deferred_ranking = None
        self._theme_snapshots = ()
        self._theme_reads = self._ranking_reads = 0
        self._consumer_gap_at = None
        self._snapshot_reads = self._dispatches = self._coalesced_events = 0
        self._last_error = ""
        self._record_store = record_store
        self._record_interval = record_interval_seconds
        self._record_task = None
        self._record_wake = asyncio.Event()
        self._record_lock = asyncio.Lock()
        self._record_pending = None
        self._record_signature = None
        self._record_last_attempt = None
        self._record_error = ""
        self._record_writes = self._record_attempts = self._record_coalesced = 0
        self._session_id = uuid.uuid4().hex
        self._source_provenance = {}
        self._market_minute_cache = None
        self._market_minute_reads = 0
        self._market_minute_read_task = None
        self._market_minute_targets = {}
        self._dropped = 0
        self._retention = max((2 * m._leadership_parameters.window_seconds for m in monitors), default=1)
        if self._allocation is not None:self._retention=max(self._retention,self._allocation['policy'].capacity_window_seconds)
        if len(self._codes()) > 200:
            raise ValueError("RAM runtime source union exceeds 200 codes")

    def _codes(self):
        return tuple(sorted({code for _, context in self._bindings if context.theme_membership is not None
                             for code in context.theme_membership.member_codes}
                            | ({self._position_inputs[1][0].symbol} if self._position_inputs is not None else set())
                            | (set(self._allocation['symbols']) if self._allocation else set())))

    @property
    def status(self):
        return {"state": "STOPPED" if self._closed else "CLOSING" if self._closing else
                "ERROR" if self._last_error else "RUNNING" if self._task is not None else "READY",
                "snapshot_reads": self._snapshot_reads, "dispatches": self._dispatches,
                "position_observation_reads": self._position_reads, "position_evaluations": self._position_evaluations,
                "coalesced_events": self._coalesced_events, "dropped_events": self._dropped,
                "ranking_context_reads": self._ranking_reads, "theme_context_reads": self._theme_reads,
                "context_pending": self._theme_pending or self._ranking_pending or self._theme_refreshing or self._deferred_ranking is not None,
                "last_error": self._last_error, "durable": False, "order_authorized": False,
                "recording": {"enabled": self._record_store is not None,
                              "pending": self._record_pending is not None,
                              "writes": self._record_writes, "attempts": self._record_attempts,
                              "coalesced": self._record_coalesced, "last_error": self._record_error,
                              "interval_seconds": self._record_interval}}

    @property
    def latest_results(self):
        """Copied bounded live results; shared context appears once in the envelope."""
        contexts, results, coverage, provenance, changes = {}, [], {}, {}, {}
        for index, monitor in enumerate(self._monitors):
            result = monitor.last_result
            if result is None:
                continue
            context = result.pop("context") if "context" in result else result
            ref = context["revision_id"]
            contexts[ref] = context
            source = self._source_provenance.get(index)
            if source is not None and source[0] is monitor._result:
                if ref not in provenance:
                    provenance[ref] = deepcopy(source[1])
                if source[1]["coverage_ref"] not in coverage:
                    coverage[source[1]["coverage_ref"]] = deepcopy(source[2])
            results.append({"binding": index, "theme_id": monitor._theme_id, "leadership_venue": monitor._venue,
                            "status": monitor.status,
                            "context_ref": ref,
                            "decision": result if monitor._stock_thesis is not None else None})
            receipt = monitor._market_changes_receipt
            if receipt is not None and receipt[0] is monitor._result:
                observation = deepcopy(receipt[1])
                change_ref = observation["revision_id"]
                # Expire the projection only; the original evidence/lease is retained.
                expires = observation.get("expires_at")
                if observation.get("observed_at") and expires and datetime.fromisoformat(expires) <= self._collector._now_provider():
                    observation = {**observation, "pattern": "UNKNOWN", "candidates": (), "changes": {},
                        "lookup_change": {**(observation.get("lookup_change") or {}), "state": "UNKNOWN"},
                        "rank_price_trade_relation": {"lookup_first_is_candidate": None,
                            "rank_changed_candidate_retained": False, "candidate_changed_rank_retained": False},
                        "projection": "expired",
                        "classification": None, "theme_evidence": {"status": "STALE"},
                        "quality": {**observation["quality"], "status": "STALE",
                            "reasons": tuple(dict.fromkeys((*observation["quality"].get("reasons", ()),
                                                           "market_frame_stale")))}}
                price = observation.get("price_trade_leadership")
                if (price and price["quality"]["status"] == "COMPLETE"
                        and datetime.fromisoformat(price["expires_at"]) <= self._collector._now_provider()):
                    observation["price_trade_leadership"] = {**price, "state": "UNKNOWN", "projection": "expired",
                        "current_candidates": (), "previous_candidates": (), "retained_candidates": (),
                        "entered_candidates": (), "left_candidates": (),
                        **({"five_minute_confirmation": {"status": "UNAVAILABLE", "confirmed_candidates": ()}}
                           if price.get("primary_timeframe") == "1m" else {}),
                        **({"timeframes": {key: {**frame, "state": "UNKNOWN", "projection": "expired",
                            "current_candidates": (), "previous_candidates": (), "retained_candidates": (),
                            "entered_candidates": (), "left_candidates": (),
                            "quality": {**frame["quality"], "status": "STALE"}}
                            for key, frame in price.get("timeframes", {}).items()}}
                           if price.get("primary_timeframe") == "1m" else {}),
                        "quality": {**price["quality"], "status": "STALE", "reasons": ("price_trade_observation_expired",)}}
                    observation["rank_price_trade_relation"] = {"lookup_first_is_candidate": None,
                        "rank_changed_candidate_retained": False, "candidate_changed_rank_retained": False}
                auxiliary = observation.get("second_trade_auxiliary")
                if (auxiliary and auxiliary["quality"]["status"] == "COMPLETE"
                        and datetime.fromisoformat(auxiliary["expires_at"]) <= self._collector._now_provider()):
                    observation["second_trade_auxiliary"] = {**auxiliary, "state": "UNKNOWN", "projection": "expired",
                        "current_candidates": (), "quality": {**auxiliary["quality"], "status": "STALE"}}
                changes[change_ref] = observation
                results[-1]["market_observed_changes_ref"] = change_ref
        envelope = {"schema": "ram-shadow-diagnostics/v1", "session_id": self._session_id,
                "coverage": "latest_snapshot", "contexts": contexts, "results": results,
                "source_coverage": coverage, "input_provenance": provenance,
                "source_durability": "not_verified", "replay_eligible": False,
                "order_authorized": False}
        if changes:
            envelope["market_observed_changes"] = changes
        if self._position_account_source is not None:
            envelope["position_comparison"] = deepcopy(self._position_receipt)
        elif self._position_comparison is not None:
            envelope["position_comparison"] = {"input_mode": "supplied_fixed_snapshot",
                "is_live_account": False, "result": deepcopy(self._position_comparison)}
        if self._allocation is not None:
            receipt=deepcopy(self._allocation_receipt)
            if receipt and datetime.fromisoformat(receipt['valid_until'])<=self._collector._now_provider():
                receipt['quality']={'status':'BLOCKED','reasons':sorted(set(receipt['quality']['reasons']+['native_allocation_receipt_expired']))}
                receipt['result']=None
                receipt.pop('lower_targets',None)
                receipt.pop('input_bundle',None)
                comparison=receipt.get('r4_action_comparison',{})
                if comparison.get('version')=='position-study-current-goal-binding/v2':
                    comparison.pop('goals',None)
                    comparison['reasons']=sorted(set(comparison['reasons']+['current_study_goal_expired']))
            envelope['position_allocation']=receipt
            envelope['position_allocation_metrics']={'account_reads':self._allocation_reads,'evaluations':self._allocation_evaluations}
            if self._allocation_models is not None:
                envelope['position_allocation_models']={
                    'publication_ref':self._allocation_models['publication_ref'],'generation':self._allocation_model_generation,
                    'available_at':self._allocation_models['available_at'].isoformat(),
                    'valid_until':self._allocation_models['valid_until'].isoformat(),
                    'source_validation':'PC_READONLY_REPLAY_REPORTED','source_durability':'not_verified','order_authorized':False}
        return envelope

    def _select_record(self):
        if self._record_store is None:
            return
        selected = []
        for index, monitor in enumerate(self._monitors):
            result = monitor._result
            if result is None:
                continue
            context = result["context"] if monitor._stock_thesis is not None else result
            def quality(value):
                return {key: value.get(key) for key in ("status", "reasons", "missing_inputs")}
            selected.append({"binding": index, "scope": context["scope"],
                "parameters": context["parameters"], "policy_version": context["policy_version"],
                "market": (context["market"]["inferences"], quality(context["market"]["quality"])),
                "leadership": (context["leadership"]["inferences"], quality(context["leadership"]["quality"])),
                "theme_revision": context["leadership"]["facts"].get("theme_revision_id"),
                "decision": {key: result.get(key) for key in (
                    "run_id", "symbol", "thesis_ref", "thesis_policy_version", "thesis_state",
                    "proposal", "action", "reasons")} if monitor._stock_thesis is not None else None})
        if (selected or (not self._monitors and self._position_receipt
                and self._position_receipt.get("decision_time"))) and self._position_receipt is not None:
            result = self._position_receipt.get("result")
            selected.append({"position_quality": self._position_receipt["quality"],
                "targets": {key: {name: value.get(name) for name in (
                    "actual_quantity", "pending_buy_quantity", "pending_sell_quantity", "projected_quantity",
                    "target_quantity", "incremental_quantity", "incremental_side", "quality")}
                    for key, value in result["targets"].items()} if result else None,
                "actions": [{name: row.get(name) for name in ("action", "ev_status", "paired_sample_count",
                    "estimate", "paired_delta", "reasons")} for row in result["actions"]] if result else None})
            if result and 'action_selection' in result:
                selected[-1]['action_selection'] = {name: result['action_selection'].get(name) for name in (
                    'action', 'status', 'reasons', 'scores', 'risk_constraint', 'risk_input_ref', 'risk_path_required')}
                if 'action_input_rejections' in result['action_selection']:
                    selected[-1]['action_selection']['action_input_rejections']=result['action_selection']['action_input_rejections']
        if not selected:
            return
        if self._allocation_receipt is not None:
            selected.append({'position_allocation':{key:self._allocation_receipt.get(key) for key in
                ('policy_ref','risk_disposition','protection_required','quality','missing_producers')}})
            if self._allocation_receipt.get('model_publication_ref'):
                selected[-1]['position_allocation']['model_publication_ref']=self._allocation_receipt['model_publication_ref']
                result=self._allocation_receipt.get('result')
                selected[-1]['position_allocation']['goals']=[] if result is None else [
                    {k:row[k] for k in ('symbol','target_notional_won','new_buy_quantity')} for row in result['allocations']]
        signature = json.dumps(selected, sort_keys=True, ensure_ascii=False)
        pending_signature = self._record_pending[0] if self._record_pending else self._record_signature
        if signature == pending_signature:
            return
        snapshot = self.latest_results
        snapshot["coverage"] = "selected_latest_transition"
        snapshot["selection"] = "first_result_or_categorical_state_change"
        snapshot["selected_at"] = max((c["decision_time"] for c in snapshot["contexts"].values()), default=None)
        if self._position_receipt and self._position_receipt.get("decision_time"):
            snapshot["selected_at"] = max(filter(None, (snapshot["selected_at"], self._position_receipt["decision_time"])),
                                           key=datetime.fromisoformat)
        if self._allocation_receipt and self._allocation_receipt.get('decision_time'):
            snapshot['selected_at']=max(snapshot['selected_at'],self._allocation_receipt['decision_time'],key=datetime.fromisoformat)
        if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            snapshot.pop("market_observed_changes", None)
            for slot in snapshot["results"]:
                slot.pop("market_observed_changes_ref", None)
        if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            # Optional diagnostic metadata must not displace a previously valid
            # selected decision at the existing recording budget boundary.
            snapshot.pop("source_coverage", None)
            snapshot.pop("input_provenance", None)
        if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            raise ValueError("selected shadow result exceeds the 1 MiB recording budget")
        if self._record_pending is not None:
            self._record_coalesced += 1
        self._record_pending = (signature, snapshot)
        self._record_wake.set()
        if self._record_task is None and not self._closing:
            self._record_task = asyncio.create_task(self._record_loop(), name="market-theme-shadow-record")

    async def _record_once(self):
        async with self._record_lock:
            pending = self._record_pending
            if pending is None:
                return
            if pending[0] == self._record_signature:
                self._record_pending = None
                return
            self._record_attempts += 1
            self._record_last_attempt = asyncio.get_running_loop().time()
            settled = []
            def write():
                self._record_store.upsert_documents(SHADOW_RESULT_COLLECTION,
                    [{"owner": SHADOW_RESULT_OWNER, "key": "latest", "document": pending[1]}])
                settled.append(True)
            try:
                await drain_thread_call(write)
            except BaseException as error:
                if not settled:
                    self._record_error = f"{type(error).__name__}: {error}"
                raise
            finally:
                # Cancellation joins the native write, including a successful ACK.
                if settled:
                    self._record_signature = pending[0]
                    self._record_writes += 1
                    self._record_error = ""
                    if self._record_pending is pending:
                        self._record_pending = None

    async def _record_loop(self):
        while True:
            await self._record_wake.wait()
            self._record_wake.clear()
            while self._record_pending is not None:
                if self._record_last_attempt is not None:
                    delay = self._record_interval - (asyncio.get_running_loop().time() - self._record_last_attempt)
                    if delay > 0:
                        await asyncio.sleep(delay)
                try:
                    await self._record_once()
                except Exception:
                    logger.exception("selected RAM shadow result recording failed")

    def context_changed(self, kind):
        if self._closing or self._closed:
            return
        if kind == "account":
            if self._position_account_source is not None or self._allocation_account_source is not None:
                self._position_pending = True
                self._wake("account")
            return
        if kind == "ranking":
            if self._ranking_source is None:
                return
            self._ranking_pending = True
        elif kind == "theme":
            if self._theme_source is None:
                return
            self._theme_pending = True
        else:
            raise ValueError("unknown market context source")
        self._wake("source_context")

    def allocation_publication_duplicate(self, document):
        if self._closing or self._closed or self._allocation is None:raise RuntimeError('ALLOCATION_RUNTIME_NOT_READY')
        encoded=json.dumps(document,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        if len(encoded.encode())>4*1024*1024:raise ValueError('allocation publication budget exceeded')
        if self._allocation_model_document!=encoded:return None
        if self._collector._now_provider()>=self._allocation_models['valid_until']:
            raise ValueError('allocation model publication expired')
        return {'publication_ref':self._allocation_models['publication_ref'],'generation':self._allocation_model_generation,
            'duplicate':True,'order_authorized':False,'source_durability':'not_verified'}

    def offer_allocation_models(self, document, decoded):
        duplicate=self.allocation_publication_duplicate(document)
        if duplicate is not None:return duplicate
        cut=self._collector._now_provider()
        if not decoded['available_at']<=cut<decoded['valid_until']:raise ValueError('allocation model publication not current')
        self._allocation_models=deepcopy(decoded)
        self._allocation_model_document=json.dumps(document,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        self._allocation_model_generation+=1;self._allocation_receipt=None
        self.context_changed('account')
        return {'publication_ref':decoded['publication_ref'],'generation':self._allocation_model_generation,
            'duplicate':False,'order_authorized':False,'source_durability':'not_verified'}

    async def _refresh_sources(self, cutoff, *, ranking_snapshots=None):
        changed = False
        if self._theme_pending:
            self._theme_pending = False
            self._theme_refreshing = True
            received = []
            def read():
                received.append(self._theme_source(cutoff if self._closing else None))
            try:
                self._theme_reads += 1
                await drain_thread_call(read)
            except BaseException:
                if not received:
                    self._theme_pending = True
                raise
            finally:
                self._theme_refreshing = False
                if received:
                    try:
                        changed = self._apply_theme_snapshot(received[0]) or changed
                    except Exception:
                        self._theme_pending = True
                        raise
        if self._ranking_pending or ranking_snapshots is not None or self._deferred_ranking is not None:
            read_current = self._ranking_pending or ranking_snapshots is not None
            self._ranking_pending = False
            try:
                if read_current:
                    self._ranking_reads += 1
                    snapshot = ranking_snapshots[0] if ranking_snapshots is not None else self._ranking_source()
                else:
                    snapshot = self._deferred_ranking
                self._deferred_ranking = None
                if snapshot is not None and float(snapshot["saved_at"]) <= cutoff:
                    changed = self._apply_ranking(snapshot) or changed
                elif snapshot is not None and not self._closing:
                    self._deferred_ranking = deepcopy(snapshot)
            except Exception:
                self._ranking_pending = True
                raise
        return changed

    def _apply_theme_snapshot(self, snapshots):
        from kiwoom_monitor.application.theme_leadership import theme_memberships_from_snapshot
        snapshot = snapshots[0] if snapshots else None
        ref = snapshot["snapshot_id"] if snapshot else "missing"
        if ref == self._theme_ref:
            return False
        memberships = {value.theme_id: value for value in theme_memberships_from_snapshot(snapshot)} if snapshot else {}
        updated = tuple((monitor, replace(context, theme_membership=memberships.get(monitor._theme_id)))
                        for monitor, context in self._bindings)
        if len({code for _, value in updated if value.theme_membership for code in value.theme_membership.member_codes}) > 200:
            raise ValueError("accepted theme source exceeds the RAM scope budget")
        self._bindings = updated
        self._theme_snapshots = (*self._theme_snapshots, deepcopy(snapshot))[-2:] if snapshot else ()
        self._theme_ref = ref
        return True

    def _apply_ranking(self, snapshot):
        from .market_theme_shadow_inputs import live_membership_ref, market_frame_from_live_membership
        if not any((context.market_venue, context.market_window) == ("COMBINED", "cumulative_session")
                   for _, context in self._bindings):
            return False
        ref = live_membership_ref(snapshot)
        available = float(snapshot["saved_at"])
        if ref == self._ranking_ref or (self._ranking_available is not None and available < self._ranking_available):
            return False
        frame = market_frame_from_live_membership(snapshot, self._theme_snapshots)
        updated = []
        for monitor, context in self._bindings:
            if (context.market_venue, context.market_window) == (frame.venue, frame.window):
                frames = tuple(value for value in context.market_frames if value.revision_id != ref)
                context = replace(context, market_frames=(*frames, frame)[-1000:])
            updated.append((monitor, context))
        self._bindings = tuple(updated)
        self._ranking_ref, self._ranking_available = ref, available
        return True

    async def offer_context(self, context):
        if self._closing or self._closed:
            raise RuntimeError("RAM runtime is closed")
        updated = []
        matched = False
        for monitor, previous in self._bindings:
            if (context.leadership_venue == monitor._venue and context.theme_membership is not None
                    and context.theme_membership.theme_id == monitor._theme_id):
                monitor._validate_inputs(context, check_cut=False)
                if datetime.fromisoformat(context.decision_time) < datetime.fromisoformat(previous.decision_time):
                    raise ValueError("RAM context cut must not move backwards")
                updated.append((monitor, _copy_shadow_inputs(context)))
                matched = True
            else:
                updated.append((monitor, previous))
        if not matched:
            raise ValueError("RAM context does not belong to a configured scope")
        if len({code for _, value in updated if value.theme_membership is not None for code in value.theme_membership.member_codes}) > 200:
            raise ValueError("RAM runtime source union exceeds 200 codes")
        self._bindings = tuple(updated)
        self._pending_context = True
        self._wake("context")

    def _wake(self, kind):
        if self._subscriber is not None and not self._subscriber.queue.full():
            self._subscriber.queue.put_nowait({"type": kind})

    async def start(self):
        if self._closing or self._closed:
            raise RuntimeError("RAM runtime is closed")
        if self._task is not None:
            return
        if any(m._task is not None and not m._task.done() for m in self._monitors):
            raise RuntimeError("RAM runtime owns shared execution; individual workers must stop")
        self._collector.set_ram_observation_retention(self._retention, owner=self)
        self._retention_owned = True
        # Empty observer scope adds no codes/TR/REG to the Hub subscription union.
        self._subscriber = self._hub.connect()
        self._wake("context")
        self._task = asyncio.create_task(self._run(), name="market-theme-shadow-runtime")

    def _snapshot(self, *, all_sources=False):
        kwargs={'include_trade_references':True} if self._allocation is not None or self._position_account_source is not None else {}
        if self._allocation_models is not None and any(row.get('position_study',{}).get('study') is not None
                and row['position_study']['study'].version in {'position-action-study/v3','position-action-study/v4'}
                for row in self._allocation_models['rows'].values()):
            kwargs['include_closed_minutes']=True
        codes = self._codes()
        # Retain the existing sources first; read already collected TOP20 RAM only
        # within the same 200-code budget. This adds no REG or collector owner.
        market_codes = set()
        cutoff = self._collector._now_provider()
        for _, context in self._bindings:
            eligible = [frame for frame in context.market_frames if frame.venue == context.market_venue
                        and frame.window == context.market_window and datetime.fromisoformat(frame.available_at) <= cutoff]
            if eligible:
                frame = max(eligible, key=lambda value: (datetime.fromisoformat(value.available_at), value.revision_id))
                market_codes.update(normalize_stock_code(row.code) for row in frame.members)
        codes = (*codes, *sorted(market_codes - set(codes))[:max(0, 200 - len(codes))])
        snapshot = self._collector.ram_observation_snapshot(None if all_sources else codes, seconds=self._retention,**kwargs)
        self._snapshot_reads += 1
        dropped = self._subscriber.dropped_events if self._subscriber else self._dropped
        snapshot["collector_gap_at"] = snapshot["gap_at"]
        if dropped > self._dropped:
            self._consumer_gap_at = datetime.fromisoformat(snapshot["decision_time"])
            self._dropped = dropped
        if self._consumer_gap_at is not None:
            gap = snapshot["gap_at"]
            snapshot["gap_at"] = max(gap, self._consumer_gap_at) if gap else self._consumer_gap_at
            snapshot["generation"] = f"{snapshot['generation']}:delivery-{self._dropped}"
        snapshot["delivery_gap_at"] = self._consumer_gap_at
        snapshot["dropped_events"] = self._dropped
        if snapshot.get('input_resolution') == '0B':
            from .market_theme_shadow_inputs import prepare_ram_trade_observations
            snapshot['_trade_cut'] = prepare_ram_trade_observations(snapshot)
        return snapshot

    def _market_minute_key(self, snapshot):
        """Existing native live-minute reader, shared per minute/cohort/commit.

        Only closed 10-minute history is cached in this runtime owner, using
        its single owned reader task and existing Collector read boundary.
        No minute accumulators, writers or subscriptions are added.
        """
        cutoff = datetime.fromisoformat(snapshot["decision_time"])
        end = (cutoff - timedelta(seconds=2)).replace(second=0, microsecond=0)
        wanted = set()
        for monitor, context in self._bindings:
            frames = [frame for frame in context.market_frames if frame.venue == context.market_venue
                and frame.window == context.market_window and datetime.fromisoformat(frame.available_at) <= cutoff]
            if frames:
                frame = max(frames, key=lambda value: (datetime.fromisoformat(value.available_at), value.revision_id))
                if (len(frame.members) == 20 and frame.capture_quality == "complete"
                        and frame.trading_date == cutoff.astimezone(timezone(timedelta(hours=9))).date().isoformat()
                        and cutoff < datetime.fromisoformat(frame.available_at) + timedelta(seconds=monitor._market_parameters.max_gap_seconds)):
                    wanted.update((normalize_stock_code(row.code), context.leadership_venue) for row in frame.members)
        wanted = tuple(sorted(wanted))[:200]
        commits = getattr(self._collector, "_save_success_at", {})
        # A late native close/commit refreshes the bounded read without a timer,
        # retry loop or changes to any writer's receipt/transaction ownership.
        key = (cutoff.astimezone(timezone(timedelta(hours=9))).date().isoformat(), end, wanted,
               commits.get("minute_bars"), commits.get("minute_finalizations"))
        return key


    async def _market_minutes(self, snapshot, *, key=None):
        key = key or self._market_minute_key(snapshot)
        end, wanted = key[1:3]
        if self._market_minute_cache is not None and self._market_minute_cache[0] == key:
            return self._market_minute_cache[1:]
        day_start = datetime.fromisoformat(key[0]).replace(tzinfo=timezone(timedelta(hours=9)))
        # Intersect the existing datetime window with the requested KST day.
        # At midnight the two-second fence can still end in the previous day.
        start_minute = max(end - timedelta(minutes=10), day_start).astimezone(day_start.tzinfo).strftime('%H:%M')
        end_minute = max(end, day_start).astimezone(day_start.tzinfo).strftime('%H:%M')
        rows, errors = [], {}
        for code, venue in wanted:
            try:
                loaded = await self._collector.load_live_minute_bars(code, key[0], venue,
                    start_minute=start_minute, end_minute=end_minute)
                self._market_minute_reads += 1
                for row in loaded or ():
                    at = datetime.fromisoformat(f"{row['trading_date']}T{row['minute']}").replace(tzinfo=timezone(timedelta(hours=9)))
                    if end - timedelta(minutes=10) <= at < end:
                        rows.append(deepcopy(row))
            except Exception as error:
                errors[(code, venue)] = type(error).__name__
        self._market_minute_cache = (key, tuple(rows), errors)
        return tuple(rows), errors


    async def _read_market_minute_history(self, snapshot, key):
        try:
            await self._market_minutes(snapshot, key=key)
        except Exception as error:
            self._market_minute_cache = (key, (), {source: type(error).__name__ for source in key[2]})
        if not self._closing:
            self._attach_ready_market_minutes()


    def _attach_ready_market_minutes(self):
        """Refresh only minute sidecars; never re-evaluate or delay protection."""
        from .market_theme_shadow_inputs import market_minute_trade_inputs_from_rows
        cache = self._market_minute_cache
        if cache is None:
            return
        calculated = []
        for monitor, (core, inputs, token) in tuple(self._market_minute_targets.items()):
            receipt = monitor._market_changes_receipt
            if token is cache or monitor._result is not core or receipt is None or receipt[0] is not core:
                continue
            try:
                old = inputs.market_minute_trade_inputs
                rows = (*(old.rows if old is not None else ()), *cache[1])
                minute_inputs = market_minute_trade_inputs_from_rows({"decision_time": inputs.decision_time}, inputs, rows,
                    max_membership_age_seconds=monitor._market_parameters.max_gap_seconds)
                error = next((value for (code, venue), value in cache[2].items() if minute_inputs is not None
                    and code in minute_inputs.member_codes and venue == minute_inputs.venue), "")
                scope = (inputs.decision_time, minute_inputs, error, monitor._market_parameters.top_k)
                found = next((value for key, value in calculated if _same_shadow_scope(key, scope)), None)
                if found is None:
                    if error or minute_inputs is None:
                        raise ValueError("minute_history_unavailable")
                    found = compute_minute_trade_candidates(minute_inputs, as_of=datetime.fromisoformat(inputs.decision_time),
                        top_k=monitor._market_parameters.top_k)
                    found["published_at"] = self._collector._now_provider().isoformat()
                    calculated.append((scope, found))
            except Exception as error:
                found = {"policy_version": "minute-price-trade-candidates/v1", "primary_timeframe": "1m", "state": "UNKNOWN",
                    "expires_at": inputs.decision_time, "quality": {"status": "UNAVAILABLE",
                    "reasons": ("minute_history_unavailable",), "error_type": type(error).__name__},
                    "order_authorized": False}
            observation = deepcopy(receipt[1])
            _attach_price_candidate_value(observation, found)
            monitor._market_changes_receipt = (core, observation)
            self._market_minute_targets[monitor] = (core, inputs, cache)


    async def _dispatch(self, snapshot):
        from .market_theme_shadow_inputs import market_theme_shadow_inputs_from_ram, market_price_trade_inputs_from_ram, market_minute_trade_inputs_from_rows
        # Keep existing minute context beside the shared individual 0B cut.
        # The owned read never blocks H1/H2, account or protective evaluation.
        key = self._market_minute_key(snapshot)
        if not key[2] or self._collector._store is None:
            self._market_minute_cache = (key, (), {})
        elif (not self._closing and (self._market_minute_cache is None or self._market_minute_cache[0] != key)
                and (self._market_minute_read_task is None or self._market_minute_read_task.done())):
            self._market_minute_read_task = asyncio.create_task(self._read_market_minute_history(snapshot, key), name="market-minute-history-read")
        token = self._market_minute_cache
        minute_rows, minute_errors = token[1:] if token is not None else ((), {})
        self._pending_context = False
        prepared, offered, price_scopes = [], {}, []
        for monitor, context in self._bindings:
            key = (context, monitor._leadership_parameters.window_seconds, monitor._leadership_parameters.max_data_age_seconds,
                   monitor._market_parameters.max_gap_seconds)
            cached = next((value for scope, value in prepared if scope == key), None)
            if cached is None:
                cached = market_theme_shadow_inputs_from_ram(snapshot, context, window_seconds=key[1])
                price_scope = (context.market_venue, context.market_window, context.market_frames, context.leadership_venue,
                               key[1], key[2], key[3])
                found = next((value for scope, value in price_scopes if _same_shadow_scope(scope, price_scope)), None)
                if found is None:
                    try:
                        found = (market_price_trade_inputs_from_ram(snapshot, context, window_seconds=key[1],
                            max_data_age_seconds=key[2], max_membership_age_seconds=key[3]), "")
                    except Exception as error:
                        found = (None, type(error).__name__)
                    price_scopes.append((price_scope, found))
                try:
                    minute_inputs = (market_minute_trade_inputs_from_rows(snapshot, context, minute_rows,
                        max_membership_age_seconds=key[3]) if token is not None else None)
                    minute_error = next((error for (code, venue), error in minute_errors.items()
                        if minute_inputs is not None and code in minute_inputs.member_codes and venue == minute_inputs.venue), "")
                except Exception as error:
                    minute_inputs, minute_error = None, type(error).__name__
                cached = MarketPriceTradeShadowInputs(**{**vars(cached), "market_price_trade_inputs": found[0],
                    "market_price_trade_input_error": found[1], "market_primary_timeframe": "1m",
                    "market_minute_trade_inputs": minute_inputs, "market_minute_trade_input_error": minute_error})
                prepared.append((key, cached))
            await monitor.offer_inputs(cached)
            offered[monitor] = cached
        self._dispatches += 1
        before = tuple(monitor._result for monitor in self._monitors)
        source = self._source_coverage(snapshot, offered)
        try:
            report = (await run_market_theme_shadow_monitors_once(self._monitors, include_market_changes=True) if self._monitors
                      else {"evaluated": [], "errors": {}, "results": ()})
        finally:
            # The existing shared executor publishes successful slots even while
            # draining cancellation. Bind only those exact results to this cut;
            # failed slots retain their own earlier receipt, never a newer one.
            for index, monitor in enumerate(self._monitors):
                result = monitor._result
                if result is None or result is before[index]:
                    continue
                context = result["context"] if monitor._stock_thesis is not None else result
                inputs = offered[monitor]
                if (monitor._last_offered != inputs
                        or datetime.fromisoformat(context["decision_time"]) != datetime.fromisoformat(snapshot["decision_time"])):
                    self._source_provenance.pop(index, None)
                    continue
                membership = inputs.theme_membership
                provenance = {"coverage_ref": source["coverage_ref"],
                    "continuity_status": inputs.continuity_status,
                    "window_seconds": monitor._leadership_parameters.window_seconds,
                    "theme_revision_id": membership.revision_id if membership else None,
                    "member_codes": list(membership.member_codes) if membership else [],
                    "leadership_venue": inputs.leadership_venue,
                    "observation_count": len(getattr(inputs,"observation_points",())), "input_resolution": "0B",
                    "supplied_market_frame_refs": [frame.revision_id for frame in inputs.market_frames],
                    "supplied_upper_limit_refs": [limit.input_ref for limit in inputs.upper_limits]}
                self._source_provenance[index] = (result, provenance, source)
                self._market_minute_targets[monitor] = (result, inputs, token)
            self._attach_ready_market_minutes()
        await self._refresh_position_inputs(snapshot, tuple(offered.values()))
        self._select_record()
        return report

    def _borrow_position_account(self,account_ref,source):
        if self._account_cut_cache is None:return source(account_ref)
        if account_ref not in self._account_cut_cache:
            try:self._account_cut_cache[account_ref]=(True,source(account_ref))
            except Exception as error:self._account_cut_cache[account_ref]=(False,error)
        ok,value=self._account_cut_cache[account_ref]
        if not ok:raise value
        return value

    async def _refresh_position_inputs(self,snapshot,inputs=()):
        self._position_pending=False
        self._account_cut_cache={}
        try:
            self._refresh_position_comparison(snapshot,inputs)
            if self._allocation is not None:
                from .market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
                generation=self._allocation_model_generation
                try:
                    self._allocation_reads+=int(self._allocation['policy'].account_ref not in self._account_cut_cache)
                    observation=self._borrow_position_account(self._allocation['policy'].account_ref,self._allocation_account_source)
                    contexts={}
                    for monitor in self._monitors:
                        result=monitor._result
                        if result is not None:
                            context=result.get('context',result)
                            contexts[context['revision_id']]=context
                    self._allocation_evaluations+=1
                    generation=self._allocation_model_generation
                    kwargs={'models':self._allocation_models} if self._allocation_models is not None else {}
                    if 0 in self._allocation['policy'].context_weights:
                        kwargs['theme_memberships']=tuple(context.theme_membership for _,context in self._bindings
                            if context.theme_membership is not None)
                    receipt=await drain_thread_call(dynamic_allocation_inputs_from_ram,self._allocation,snapshot,tuple(contexts.values()),observation,**kwargs)
                    if generation==self._allocation_model_generation and not self._closing:
                        self._allocation_receipt=receipt
                except Exception as error:
                    if generation==self._allocation_model_generation and not self._closing:
                        self._allocation_receipt={'decision_time':snapshot['decision_time'],'valid_until':snapshot['decision_time'],
                            'result':None,'quality':{'status':'ERROR','reasons':['native_allocation_input_error:'+type(error).__name__]},
                            'source_durability':'not_verified','order_authorized':False}
        finally:self._account_cut_cache=None

    def _refresh_position_comparison(self, snapshot, inputs=()):
        """One common owner RAM read/evaluation; no account or market I/O."""
        if self._position_account_source is None:
            return
        self._position_pending = False
        from kiwoom_monitor.application.position_target import (
            PositionAccountObservation, evaluate_position_action_comparison_shadow)
        cut = datetime.fromisoformat(snapshot["decision_time"])
        receipt = {"input_mode": "owner_ram_snapshot", "is_live_account": True, "decision_time": cut.isoformat(),
            "result": None, "quality": {"status": "UNAVAILABLE", "reasons": ["account_observation_not_ready"]}}
        self._position_receipt = receipt
        parameters, requests, _, _, outcomes = self._position_inputs
        first = requests[0]
        try:
            self._position_reads += 1
            observation = self._borrow_position_account(first.account_ref,self._position_account_source)
            if observation is None:
                return
            if not isinstance(observation, PositionAccountObservation) or observation.account.account_ref != first.account_ref:
                raise ValueError("account observation scope mismatch")
            if observation.observed_at > cut:
                raise ValueError("account observation from future")
            # Reuse the Collector's approved quote in this same cut. Theme/second
            # windows describe strategy context, not the availability of a price.
            quotes = [value for value in snapshot.get('trade_references', ())
                      if value.get('code') == first.symbol and value.get('market') == first.venue == 'KRX']
            if len(quotes) != 1 or (first.symbol, first.venue) not in snapshot["approved_sources"]:
                receipt["quality"]["reasons"] = ["ram_reference_price_unavailable"]
                return
            quote = quotes[0]; price_at = quote['available_at']
            if (snapshot['durable'] is not False or quote.get('durable') is not False
                    or type(quote.get('price_won')) is not int or quote['price_won'] <= 0
                    or quote.get('market_status') != 'TRADABLE'
                    or not isinstance(quote.get('input_ref'), str)
                    or not quote['input_ref'].startswith(f"collector-trade:{snapshot['source_id']}:")
                    or not isinstance(price_at, datetime) or price_at.tzinfo is None or price_at > cut):
                receipt["quality"]["reasons"] = ["ram_reference_price_unavailable"]
                return
            since = snapshot['continuous_from'].get((first.symbol, first.venue))
            gap = snapshot['gap_at']
            if since is None or since > price_at or gap is not None and gap > price_at:
                receipt["quality"]["reasons"] = ["ram_price_continuity_incomplete"]
                return
            if cut >= price_at + timedelta(seconds=first.max_snapshot_age_seconds):
                receipt["quality"]["reasons"] = ["ram_reference_price_expired"]
                return
            live_requests = tuple(replace(request, decision_time=cut, reference_price_won=quote['price_won'],
                reference_price_available_at=price_at, account_input_ref=observation.input_ref,
                orders_input_ref=observation.input_ref, reconciliation_complete=observation.reconciliation_complete,
                open_orders_complete=observation.open_orders_complete) for request in requests)
            self._position_evaluations += 1
            native_inputs = {}
            if parameters.selection_policy is not None and parameters.selection_policy.input_mode in {'owner_ram_risk_and_thesis','owner_ram_risk'}:
                candidates = {m._result['decision_id']: m._result for m in self._monitors
                    if parameters.selection_policy.thesis_ref and m._stock_thesis is not None and m._result is not None
                    and m._result.get('thesis_ref') == parameters.selection_policy.thesis_ref
                    and m._result.get('symbol') == first.symbol}
                native_inputs = {'risk_snapshot': observation.risk_snapshot,
                    'stock_thesis_result': next(iter(candidates.values())) if len(candidates) == 1 else None}
            result = evaluate_position_action_comparison_shadow(parameters, live_requests,
                observation.account, observation.open_orders, outcomes, broker_open_orders=observation.broker_open_orders,
                **native_inputs)
            reasons = list(observation.reasons)
            validity = [observation.account.as_of + timedelta(seconds=first.max_snapshot_age_seconds),
                        price_at + timedelta(seconds=first.max_snapshot_age_seconds)]
            validity.extend(value.as_of + timedelta(seconds=first.max_snapshot_age_seconds)
                            for value in observation.broker_open_orders or ())
            if first.cost_model is not None and first.cost_model.valid_to:
                validity.append(datetime.fromisoformat(first.cost_model.valid_to))
            validity.extend(request.target_policy.valid_until for request in requests
                            if request.target_policy is not None)
            if parameters.selection_policy is not None:
                validity.append(parameters.selection_policy.valid_until)
            if result.get('selection_inputs', {}).get('expires_at'):
                validity.append(datetime.fromisoformat(result['selection_inputs']['expires_at']))
            if native_inputs and observation.risk_snapshot is not None:
                validity.append(observation.risk_snapshot.observed_at + timedelta(seconds=first.max_snapshot_age_seconds))
            receipt.update(result=result, account_input_ref=observation.input_ref, price_input_ref=quote['input_ref'],
                account_as_of=observation.account.as_of.isoformat(),
                account_orders_complete=observation.open_orders_complete,
                account_reconciliation_complete=observation.reconciliation_complete,
                valid_until=min(validity).isoformat(),
                unmapped_broker_order_ids=list(observation.unmapped_broker_order_ids),
                quality={"status": "PARTIAL" if reasons else "AVAILABLE", "reasons": reasons})
        except Exception as error:
            receipt["quality"] = {"status": "ERROR", "reasons": [f"{type(error).__name__}: {error}"]}

    def _source_coverage(self, snapshot, offered):
        """One compact receipt for the already-read common RAM cut, no raw values.

        This records the Collector's continuity claim, not durable capture or
        native revision approval. It never enters scientific IDs or the record
        transition signature and is retained only beside current result slots.
        """
        pairs = sorted({(code, inputs.leadership_venue) for inputs in offered.values()
                        if inputs.theme_membership for code in inputs.theme_membership.member_codes}
                       | ({(self._position_inputs[1][0].symbol, self._position_inputs[1][0].venue)}
                          if self._position_inputs is not None else set()))
        approved = set(snapshot["approved_sources"])
        def stamp(value):
            return value.isoformat() if value is not None else None
        return {"version": "ram-source-coverage/v1", "coverage_ref": f"ram-coverage:{uuid.uuid4().hex}",
            "source_id": snapshot["source_id"], "source_generation": snapshot["generation"],
            "input_resolution": snapshot.get('input_resolution','seconds'),
            "decision_time": snapshot["decision_time"],
            "retention_since": stamp(snapshot["retention_since"]),
            "collector_gap_at": stamp(snapshot.get("collector_gap_at", snapshot["gap_at"])),
            "effective_gap_at": stamp(snapshot["gap_at"]),
            "delivery_gap_at": stamp(snapshot.get("delivery_gap_at")), "dropped_events": snapshot.get("dropped_events", 0),
            "sources": [{"code": code, "venue": venue, "approved": (code, venue) in approved,
                         "continuous_from": stamp(snapshot["continuous_from"].get((code, venue)))}
                        for code, venue in pairs],
            "raw_values_recorded": False, "capture_lineage": "unverified",
            "capture_anchor": deepcopy(snapshot.get("capture_anchor")),
            "source_durability": "not_verified", "replay_eligible": False, "order_authorized": False}

    async def _run(self):
        retry_kind = None
        while True:
            event = await self._subscriber.queue.get()
            events = [event]
            while not self._subscriber.queue.empty():
                events.append(self._subscriber.queue.get_nowait())
            kinds = {event.get("type") for event in events}
            account_changed = "account" in kinds or self._position_pending
            kinds.discard("account")
            if self._pending_context:
                kinds.add("context")
            if self._theme_pending or self._ranking_pending or self._deferred_ranking is not None:
                kinds.add("source_context")
            elif retry_kind is not None:
                kinds.add(retry_kind)
            if not kinds & (_RAM_INPUT_EVENTS | {"retry"}) and not account_changed:
                continue
            self._coalesced_events += len(events) - 1
            report = None
            try:
                changed = await self._refresh_sources(self._collector._now_provider().timestamp())
                if account_changed and not kinds & _RAM_INPUT_EVENTS:
                    await self._refresh_position_inputs(self._snapshot())
                    self._select_record()
                    continue
                if kinds <= {"source_context"} and not changed:
                    continue
                report = (await run_market_theme_shadow_monitors_once(self._monitors)
                          if self._monitors and not kinds & _RAM_INPUT_EVENTS else await self._dispatch(self._snapshot()))
                if not kinds & _RAM_INPUT_EVENTS:
                    self._select_record()
                self._last_error = str(report["errors"]) if report["errors"] else ""
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
            if self._last_error:
                retry_kind = "retry" if report is not None and report["errors"] else "context"
                await asyncio.sleep(0.5)
                self._wake(retry_kind)
            else:
                retry_kind = None

    async def close(self):
        if self._close_task is None:
            self._closing = True
            # Freeze the final accepted canonical cut before yielding/disconnecting.
            snapshot, source_error = None, None
            ranking_snapshots = None
            try:
                kinds = set()
                if self._subscriber is not None:
                    while not self._subscriber.queue.empty():
                        kinds.add(self._subscriber.queue.get_nowait().get("type"))
                    if self._pending_context or self._theme_pending or self._theme_refreshing or self._ranking_pending or self._position_pending or kinds & _RAM_INPUT_EVENTS or "account" in kinds:
                        snapshot = self._snapshot(all_sources=self._theme_source is not None)
                        if self._theme_refreshing or (self._theme_snapshots and
                                float(self._theme_snapshots[-1]["available_at"]) > datetime.fromisoformat(snapshot["decision_time"]).timestamp()):
                            self._theme_pending = True
                        if self._ranking_source is not None:
                            ranking_snapshots = (self._ranking_source(),)
            except Exception as error:
                source_error = error
            if self._subscriber is not None:
                self._hub.disconnect(self._subscriber)
            self._close_task = asyncio.create_task(self._close_owned(snapshot, source_error, ranking_snapshots), name="market-theme-shadow-runtime-close")
        await join_owned_close(self._close_task)

    async def _close_owned(self, snapshot, source_error, ranking_snapshots):
        try:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            if self._market_minute_read_task is not None:
                await join_owned_close(self._market_minute_read_task)
            if snapshot is not None:
                await self._refresh_sources(datetime.fromisoformat(snapshot["decision_time"]).timestamp(), ranking_snapshots=ranking_snapshots)
                await self._dispatch(snapshot)
            elif self._retention_owned and self._monitors:
                await run_market_theme_shadow_monitors_once(self._monitors)
                self._select_record()
            if source_error is not None:
                raise source_error
        finally:
            try:
                results = await asyncio.gather(*(monitor.close() for monitor in self._monitors), return_exceptions=True)
                failures = [result for result in results if isinstance(result, BaseException)]
                if failures:
                    raise failures[0]
            finally:
                try:
                    if self._record_task is not None:
                        self._record_task.cancel()
                        try:
                            await self._record_task
                        except asyncio.CancelledError:
                            pass
                    self._select_record()
                    await self._record_once()
                finally:
                    if self._retention_owned:
                        self._collector.set_ram_observation_retention(0, owner=self)
                    self._task = self._subscriber = self._record_task = None
                    self._market_minute_cache = None
                    self._market_minute_read_task = None
                    self._market_minute_targets.clear()
                    self._closed = True


async def join_owned_close(task):
    """Join the existing async owner even if its close caller is repeatedly cancelled."""
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise

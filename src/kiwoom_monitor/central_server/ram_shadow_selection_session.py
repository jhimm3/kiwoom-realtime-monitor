"""Own saved/applied RAM Shadow selection and its runtime replacement lifetime."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import json

from .observation_frame_recovery import drain_thread_call, join_owned_close
from .ram_shadow_selection_config import (
    SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER, SHADOW_SELECTION_COLLECTION,
    _validate_selection_header, validate_ram_shadow_selection,
    position_comparison_requires_theme_context, _validate_position_comparison_input_shapes,
    selected_position_allocation, _selected_stock_thesis,
)


_OPTIONAL_KERNEL_MODULES = frozenset({
    'kiwoom_monitor.application.market_theme_shadow',
    'kiwoom_monitor.application.research_factors',
})


def _optional_kernel_absent(error):
    return isinstance(error, ModuleNotFoundError) and error.name in _OPTIONAL_KERNEL_MODULES


def _selection_error(error):
    original = error
    while error is not None:
        if _optional_kernel_absent(error):
            return f"{type(error).__name__}: {error}"
        error = error.__cause__
    return f"{type(original).__name__}: {original}"


def _validate_unavailable_selection(value):
    """Only conclude common shape errors; absent compute is never validation."""
    _validate_selection_header(value)
    live = value.get('position_account_live', False)
    document = value.get('position_comparison')
    if type(live) is not bool or (live and document is None):
        raise ValueError('live position observation requires explicit comparison inputs')
    if document is not None:
        if not isinstance(document, dict):
            raise ValueError('position comparison requires a fixed input document')
        if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 256 * 1024:
            raise ValueError('position comparison input exceeds 256 KiB')
        _validate_position_comparison_input_shapes(document)
    thesis = value.get('stock_thesis')
    if thesis is not None:
        if not isinstance(thesis, dict) or set(thesis) != {'evidence', 'thesis_policy_version'}:
            raise ValueError('research thesis selection requires evidence and an explicit policy')
        if not isinstance(thesis['thesis_policy_version'], str):
            raise ValueError('research thesis policy requires a string')
        evidence = thesis['evidence']
        if not isinstance(evidence, dict) or set(evidence) != {'thesis', 'leadership', 'run_status', 'stored_identity'}:
            raise ValueError('invalid selected research thesis evidence fields')
        if len(json.dumps(thesis, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 1024 * 1024:
            raise ValueError('selected research evidence exceeds one MiB')
        if any(not isinstance(evidence[key], dict) for key in ('thesis', 'leadership', 'stored_identity')):
            raise ValueError('selected research thesis evidence requires objects')
        if any(not isinstance(evidence['thesis'].get(key), (list, tuple)) for key in ('factor_refs', 'hypothesis_refs')):
            raise ValueError('selected research thesis references require arrays')
    allocation = value.get('position_allocation')
    if allocation is not None:
        keys = {'schema','policy','symbols','expected_execution_run_id','expected_binding_revision',
                'data_path','maximum_daily_loss_won','cost_model'}
        if not isinstance(allocation, dict) or set(allocation) != keys:
            raise ValueError('invalid dynamic allocation RAM selection')
        if len(json.dumps(allocation, allow_nan=False).encode()) > 64 * 1024:
            raise ValueError('invalid dynamic allocation RAM selection')
        if (allocation['schema'] != 'dynamic-position-allocation-ram/v1'
                or type(allocation['expected_binding_revision']) is not int or allocation['expected_binding_revision'] <= 0
                or type(allocation['maximum_daily_loss_won']) is not int or allocation['maximum_daily_loss_won'] <= 0
                or type(allocation['expected_execution_run_id']) is not str or not 0 < len(allocation['expected_execution_run_id']) <= 256
                or allocation['data_path'] not in {'nas', 'direct'}):
            raise ValueError('explicit native account/Risk binding and research loss limit required')
        symbols = allocation['symbols']
        if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 200
                or any(not isinstance(symbol, str) for symbol in symbols) or len(set(symbols)) != len(symbols)):
            raise ValueError('allocation requires 1..200 distinct symbols')
        if (not isinstance(allocation['policy'], dict)
                or not isinstance(allocation['policy'].get('context_weights'), (list, tuple))
                or (allocation['cost_model'] is not None and not isinstance(allocation['cost_model'], dict))):
            raise ValueError('allocation policy/cost requires objects and an explicit weight array')


class RamShadowSelectionConflict(RuntimeError):
    """The caller's saved selection revision has already changed."""


class RamShadowSelectionUnsupported(ValueError):
    """Explicit bindings or the missing collector prevent the requested selection."""


class RamShadowSelectionSession:
    """Keep native save/settle/apply and runtime lifetime under one owner.

    The session owns the mutation fence for selection replacement, model
    publication and close. General operational settings use their own fence.
    Account observation callbacks retain their existing synchronous boundary.
    Runtime still owns Hub admission, calculation, model slots and native drain.
    """

    def __init__(self, store, hub, collector, *, logger, now_provider,
                 ranking_source=None, theme_source=None, position_account_source=None,
                 configure_position_order_observation, publish_runtime,
                 explicit_bindings=(), explicit_recording=False):
        self._store = store
        self._hub = hub
        self._collector = collector
        self._now = now_provider
        self._fence = asyncio.Lock()
        self._logger = logger
        self._ranking_source = ranking_source
        self._theme_source = theme_source
        self._position_account_source = position_account_source
        self._configure_position_order_observation = configure_position_order_observation
        self._publish_runtime = publish_runtime
        self._explicit_bindings = explicit_bindings
        self._runtime = None
        self._selection = {"version": "ram-shadow-selection/v1", "revision": 0, "enabled": False,
                           "theme_ids": [], "leadership_venue": "KRX", "recording": False}
        self._error = ""
        self._closing = False
        self._position_order_observation_account = None
        self._mutation_task = None
        self._close_task = None
        if explicit_bindings:
            try:
                from .market_theme_shadow_monitor import MarketThemeShadowRuntime
                self._runtime = MarketThemeShadowRuntime(hub, collector, explicit_bindings, now_provider=now_provider,
                    ranking_source=ranking_source, theme_source=theme_source,
                    record_store=store if explicit_recording else None)
            except ModuleNotFoundError as error:
                if not _optional_kernel_absent(error):
                    raise
                self._error = _selection_error(error)
        saved = store.load_document(SHADOW_SELECTION_COLLECTION, "global", "current")
        if saved is not None:
            try:
                try:
                    self._selection = validate_ram_shadow_selection(saved["document"])
                except ModuleNotFoundError as error:
                    if not _optional_kernel_absent(error):
                        raise
                    _validate_unavailable_selection(saved["document"])
                    self._selection = deepcopy(saved["document"])
                    self._error = _selection_error(error)
                    logger.warning("saved RAM shadow selection cannot be validated without its kernel", exc_info=True)
            except (KeyError, TypeError, ValueError):
                self._error = "INVALID_SAVED_RAM_SHADOW_SELECTION"
                logger.warning("invalid saved RAM shadow selection remains disabled", exc_info=True)
        selection = self._selection
        self._applied_revision = -1 if selection["enabled"] or self._error else selection["revision"]
        if not self._error and selection["enabled"] and (selection.get("position_account_live") or selection.get("position_allocation")):
            try:
                account_ref = (selection["position_allocation"]["policy"]["account_ref"] if selection.get("position_allocation")
                               else selection["position_comparison"]["requests"][0]["account_ref"])
                if not isinstance(account_ref, str) or not account_ref.strip():
                    raise ValueError("invalid saved position account reference")
                self._configure_observation(account_ref)
            except (KeyError, IndexError, TypeError, ValueError):
                self._error = "INVALID_SAVED_RAM_SHADOW_SELECTION"

    @property
    def runtime(self):
        return self._runtime

    def selection(self):
        selection = self._selection
        return {**deepcopy(selection), "theme_ids": list(selection["theme_ids"]),
                "applied_revision": self._applied_revision,
                "apply_status": "ACTIVE" if self._applied_revision == selection["revision"] and not self._error else "RECOVERY_REQUIRED",
                "last_error": self._error, "runtime_supported": self._collector is not None and not self._explicit_bindings,
                "selection_supported": not self._explicit_bindings,
                "mode": "explicit_bindings" if self._explicit_bindings else "research_thesis" if selection.get("stock_thesis")
                        else "position_context" if selection.get("position_account_live") and not selection["theme_ids"] else "theme_context",
                "runtime": self._runtime.status if self._runtime is not None else {"state": "DISABLED"}}

    def context_changed(self, kind):
        if self._runtime is not None:
            self._runtime.context_changed(kind)

    def account_position_changed(self, account_ref):
        if account_ref == self._position_order_observation_account:
            self.context_changed("account")

    def _configure_observation(self, account_ref):
        self._position_order_observation_account = account_ref
        self._configure_position_order_observation(account_ref)

    async def check_stock_thesis_sources(self, expected_revision, lookback_seconds, minute_research=False):
        try:
            from .market_theme_shadow_inputs import read_stock_thesis_source_check, read_minute_stock_thesis_source_check
        except ModuleNotFoundError as error:
            if not _optional_kernel_absent(error):
                raise
            raise RuntimeError("RAM_SHADOW_SELECTION_PENDING") from error
        # Explicit bounded diagnostic only; native reads drain before the fence releases.
        async with self._fence:
            if self._closing:
                raise RuntimeError("RAM_SHADOW_SERVER_CLOSING")
            if expected_revision != self._selection["revision"]:
                raise RuntimeError("RAM_SHADOW_SELECTION_REVISION_CONFLICT")
            selected = deepcopy(self._selection.get("stock_thesis"))
            if self._explicit_bindings or selected is None:
                raise RuntimeError("RAM_SHADOW_RESEARCH_THESIS_REQUIRED")
            reader = read_minute_stock_thesis_source_check if minute_research else read_stock_thesis_source_check
            budget = {"lookback_minutes" if minute_research else "lookback_seconds": lookback_seconds}
            report = await drain_thread_call(reader, self._store, selected["evidence"],
                thesis_policy_version=selected["thesis_policy_version"],
                leadership_venue=self._selection["leadership_venue"], **budget)
            return {**report, "selection_revision": expected_revision}

    async def _prepare(self, selection):
        try:
            comparison = None
            if selection.get("position_comparison") is not None:
                from .market_theme_shadow_monitor import selected_position_comparison
                comparison = selected_position_comparison(selection)
            allocation = selected_position_allocation(selection)
            if not selection["enabled"]:
                return (), comparison
            if self._collector is None:
                raise ValueError("RAM shadow selection requires the existing realtime collector")
            from .market_theme_shadow_monitor import ram_shadow_bindings_from_snapshot
            cut = self._now()
            snapshots = (await drain_thread_call(self._store.load_theme_snapshots, available_at=cut.timestamp(), limit=1)
                         if selection["theme_ids"] else ())
            bindings = ram_shadow_bindings_from_snapshot(selection, snapshots[0] if snapshots else None, cut.isoformat())
            if allocation is not None:
                union = set(allocation["symbols"]) | {code for _, context in bindings for code in context.theme_membership.member_codes}
                if len(union) > 200:
                    raise ValueError("allocation/shared source union exceeds 200 codes")
            if comparison is not None and position_comparison_requires_theme_context(selection["position_comparison"]) and not any(
                    comparison["symbol"] in context.theme_membership.member_codes for _, context in bindings):
                raise ValueError("position comparison symbol must belong to an observed theme")
            return (bindings, comparison, allocation) if allocation is not None else (bindings, comparison)
        except ModuleNotFoundError as error:
            if not _optional_kernel_absent(error):
                raise
            raise RuntimeError("RAM_SHADOW_SELECTION_PENDING") from error

    async def publish_allocation_models(self, document):
        async with self._fence:
            runtime = self._runtime
            if (self._closing or runtime is None or not self._selection["enabled"]
                    or self._applied_revision != self._selection["revision"]):
                raise RuntimeError("ALLOCATION_RUNTIME_NOT_READY")
            revision = self._selection["revision"]
            decoded, duplicate = await runtime.prepare_allocation_models(document,
                expected_selection_revision=revision)
            if (self._closing or self._runtime is not runtime or self._selection["revision"] != revision
                    or self._applied_revision != revision):
                raise RuntimeError("ALLOCATION_RUNTIME_NOT_READY")
            if duplicate is not None:
                return duplicate
            return runtime.offer_allocation_models(document, decoded)

    async def _apply(self, selection, prepared):
        bindings, comparison = prepared[:2]
        allocation = prepared[2] if len(prepared) > 2 else None
        try:
            self._configure_observation(allocation["policy"].account_ref if selection["enabled"] and allocation is not None
                else selection["position_comparison"]["requests"][0]["account_ref"]
                if selection["enabled"] and selection.get("position_account_live") else None)
            if self._runtime is not None:
                await self._runtime.close()
            self._runtime = None
            self._publish_runtime(None)
            if selection["enabled"]:
                from .market_theme_shadow_monitor import MarketThemeShadowRuntime
                self._runtime = MarketThemeShadowRuntime(self._hub, self._collector, bindings, now_provider=self._now,
                    ranking_source=self._ranking_source, theme_source=self._theme_source,
                    record_store=self._store if selection["recording"] else None,
                    position_comparison=comparison, position_document=selection.get("position_comparison"),
                    position_account_source=self._position_account_source if selection.get("position_account_live") or allocation is not None else None,
                    position_allocation=allocation)
                self._publish_runtime(self._runtime)
                await self._runtime.start()
            self._applied_revision = selection["revision"]
            self._error = ""
        except Exception as error:
            self._error = _selection_error(error)
            raise RuntimeError("RAM_SHADOW_SELECTION_APPLY_PENDING") from error

    async def update(self, expected_revision, changes):
        async with self._fence:
            current = self._selection
            if expected_revision != current["revision"]:
                raise RamShadowSelectionConflict("RAM_SHADOW_SELECTION_REVISION_CONFLICT")
            if self._explicit_bindings or (self._collector is None and changes.get("enabled", current["enabled"])):
                raise RamShadowSelectionUnsupported("RAM_SHADOW_SELECTION_NOT_SUPPORTED")
            self._mutation_task = asyncio.create_task(self._update(changes), name="ram-shadow-selection-apply")
            try:
                await join_owned_close(self._mutation_task)
            finally:
                self._mutation_task = None
            return self.selection()

    async def _update(self, changes):
        if self._closing:
            raise RuntimeError("RAM_SHADOW_SERVER_CLOSING")
        if "position_comparison" in changes and changes["position_comparison"] is None and "position_account_live" not in changes:
            changes = {**changes, "position_account_live": False}
        try:
            proposed = validate_ram_shadow_selection({**self._selection, **changes})
        except ModuleNotFoundError as error:
            if not _optional_kernel_absent(error):
                raise
            raise RuntimeError("RAM_SHADOW_SELECTION_PENDING") from error
        changed = proposed != self._selection
        if not changed and self._applied_revision == self._selection["revision"] and not self._error:
            return
        prepared = await self._prepare(proposed)
        repair_invalid = self._error == "INVALID_SAVED_RAM_SHADOW_SELECTION"
        proposed["revision"] += int(changed or repair_invalid)
        if changed or repair_invalid:
            try:
                await drain_thread_call(self._store.upsert_documents, SHADOW_SELECTION_COLLECTION,
                    [{"owner": "global", "key": "current", "document": proposed}])
            except Exception as error:
                # COMMIT with a lost ACK exposes the saved revision while the old runtime survives.
                settled = await drain_thread_call(self._store.load_document, SHADOW_SELECTION_COLLECTION, "global", "current")
                if settled is not None and settled["document"] == proposed:
                    self._selection.clear()
                    self._selection.update(proposed)
                    self._error = "RAM_SHADOW_SELECTION_SAVE_ACK_LOST"
                raise RuntimeError("RAM_SHADOW_SELECTION_SAVE_FAILED") from error
            self._selection.clear()
            self._selection.update(proposed)
        await self._apply(proposed, prepared)

    async def start(self):
        if not self._explicit_bindings and self._selection["enabled"]:
            async with self._fence:
                try:
                    await self._apply(self._selection, await self._prepare(self._selection))
                except Exception as error:
                    self._error = _selection_error(error)
                    self._logger.exception("saved RAM shadow selection requires recovery")
        elif self._runtime is not None:
            await self._runtime.start()

    def begin_close(self):
        self._closing = True

    async def close(self):
        self.begin_close()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="ram-shadow-selection-close")
        await join_owned_close(self._close_task)

    async def _close(self):
        async with self._fence:
            if self._runtime is not None:
                await self._runtime.close()

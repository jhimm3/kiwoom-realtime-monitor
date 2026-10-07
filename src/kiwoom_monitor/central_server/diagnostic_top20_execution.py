"""Native TOP20 lifecycle core, offline and opt-in.

The owned database/file experiment entry point must preflight full source
closure and restore its baseline before calling this core. This module neither
opens a lease nor publishes whole-workload/performance acceptance.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
from datetime import datetime, timedelta
from threading import Event

from .autonomous_top20 import AutonomousTop20Service
from .diagnostic_replay_contract import freeze_payload, validate_collector_message
from .diagnostic_replay_runtime import ReplayRuntimeScope, close_top20_replay_resources, owned_create_task
from .diagnostic_rest_input import RequestTapeClient, TaskTapeBindings
from .diagnostic_top20_lifecycle_input import SubscriptionTape
from .diagnostic_top20_seed import Top20FixtureClock, _cold_state, _empty, _scalars
from .diagnostic_top20_transport import Top20SubscriptionTransport
from .market_ingest import MarketDataIngestor
from .realtime_collector import CentralRealtimeCollector
from .realtime_hub import RealtimeHub
from .rest_broker import CentralRestBroker


def _store_clock_is_bound(store, clock):
    """Recognize the owned cache clock without replacing its retirement fence."""
    from .diagnostic_replay_baseline import ReplayDatabaseLease, _ReplayStore
    if type(store) is _ReplayStore:
        lease = store._lease
        if type(lease) is not ReplayDatabaseLease:
            return False
        with lease._condition:
            return bool(lease.baseline_version == 2 and lease._cache_clock is clock
                and lease._active and lease._run_ready and store._generation == lease._generation
                and store._query_cache_wall_time == store._owned_query_cache_wall_time)
    from .database import SQLiteQueryStore
    return type(store) is SQLiteQueryStore and getattr(store, "_query_cache_wall_time", None) == clock.wall_time


def prepare_top20_source_inputs(inputs, *, clock, started_mono_ns, end_seconds):
    """Validate a bounded decoded cause tape before any lifecycle/native write.

    Historical READY, source approvals, delivery queues and DB results are not
    input kinds here. Subscription ACK is matched separately to a new intent.
    """
    if (type(inputs) is not list or len(inputs) > 50000 or type(clock) is not Top20FixtureClock
            or clock.armed or type(started_mono_ns) is not int or started_mono_ns < 0
            or type(end_seconds) not in (int, float) or not math.isfinite(end_seconds)
            or not 0 < end_seconds <= 600):
        raise ValueError("top20_session_source_limits_invalid")
    prepared, previous, ids, charge = [], (-1, -1), set(), 0
    for item in inputs:
        if (type(item) is not dict or set(item) != {"input_id", "kind", "entered_mono_ns", "seq", "payload"}
                or type(item["input_id"]) is not str or not item["input_id"] or len(item["input_id"]) > 160
                or item["input_id"] in ids or type(item["entered_mono_ns"]) is not int
                or item["entered_mono_ns"] < started_mono_ns or type(item["seq"]) is not int or item["seq"] < 1):
            raise ValueError("top20_session_source_identity_invalid")
        order = item["entered_mono_ns"], item["seq"]
        offset = (order[0] - started_mono_ns) / 1e9
        if order <= previous or offset >= end_seconds:
            raise ValueError("top20_session_source_order_or_window_invalid")
        ids.add(item["input_id"])
        previous = order
        kind, value = item["kind"], item["payload"]
        if type(value) is not dict:
            raise ValueError("top20_session_source_payload_invalid")
        charge += freeze_payload(value, maximum_bytes=min(8 * 1024 * 1024,
                                      32 * 1024 * 1024 - charge)).charge
        at = value.get("source_time")
        expected = clock.origin + timedelta(seconds=offset)
        if not isinstance(at, datetime) or at.tzinfo is None or abs((at-expected).total_seconds()) > .1:
            raise ValueError("top20_session_source_clock_mismatch")
        if kind == "message":
            if (set(value) - {"source_time", "message", "message_id", "row_offset"}
                    or not {"source_time", "message"}.issubset(value)
                    or ("message_id" in value) != ("row_offset" in value)
                    or "message_id" in value and (type(value["message_id"]) is not str
                        or not value["message_id"] or len(value["message_id"]) > 160
                        or type(value["row_offset"]) is not int or not 0 <= value["row_offset"] <= 10000)):
                raise ValueError("top20_session_source_payload_invalid")
            validate_collector_message(value["message"])
        elif kind == "market_operation":
            tick = value.get("tick")
            if (set(value) != {"source_time", "tick"} or type(tick) is not dict
                    or set(tick) != {"status_code", "trade_time", "remaining_time"}
                    or type(tick["status_code"]) is not str or not tick["status_code"]
                    or len(tick["status_code"]) > 8 or any(tick[key] is not None and
                        (type(tick[key]) is not str or len(tick[key]) > 32)
                        for key in ("trade_time", "remaining_time"))):
                raise ValueError("top20_session_market_operation_invalid")
        elif kind == "capture_gap":
            if set(value) != {"source_time", "clear_continuous"} or type(value["clear_continuous"]) is not bool:
                raise ValueError("top20_session_capture_gap_invalid")
        else:
            raise ValueError("top20_session_historical_effect_or_unknown_input")
        prepared.append({**copy.deepcopy(item), "offset_seconds": offset})
    return prepared


def build_top20_session(store, clock, client, bindings, *, outbox_path, minute_backfill_enabled=True):
    """Native composition; external transport/catalog are the only substitutes."""
    if (type(clock) is not Top20FixtureClock or type(client) is not RequestTapeClient
            or type(bindings) is not TaskTapeBindings or bindings.client is not client
            or not _store_clock_is_bound(store, clock)):
        raise ValueError("top20_session_fixture_resources_invalid")
    hub = RealtimeHub()
    ingestor = MarketDataIngestor(store, now_provider=clock.now)
    broker = CentralRestBroker(client, store, ingestor.ingest,
                               ranking_reservation=True, wall_time=clock.wall_time)
    service = AutonomousTop20Service(broker, hub, store, now_provider=clock.now,
        catalog_loader=bindings.catalog_loader, outbox_path=outbox_path,
        minute_backfill_enabled=minute_backfill_enabled)
    ingestor._on_daily_change = service.notify_daily_bars_changed
    def forbidden_token():
        raise RuntimeError("top20_session_external_token_forbidden")
    collector = CentralRealtimeCollector(forbidden_token, "real", hub, clock.now, store=store,
                                        snapshot_sleep=clock.sleep)
    return service, broker, collector


def _require_fresh_collector(collector):
    """Explicit cold inventory; never copy a previous collector's RAM state."""
    _empty(collector, {name: dict for name in (
        "_pending_snapshots", "_latest_snapshots", "_pending_account_entry_symbols",
        "_pending_stock_references", "_pending_market_history", "_pending_minute_retries",
        "_pending_minute_finalizations", "_pending_second_retries", "_continuous_from")})
    _empty(collector, {"_inflight_minute_bars": list, "_approved_trade_sources": set,
        "_regular_close_notified": set, "_full_day_close_notified": set,
        "_token_tasks": set, "_flush_tasks": set, "_credential_drain_task": None,
        "_credential_resume_task": None, "_boundary_task": None, "_reconnect_expiry_task": None})
    _scalars(collector, {"_credential_phase": "IDLE", "_credential_paused": False,
        "_credential_shutdown": False, "_connection_generation": 0, "_planned_reconnect": False,
        "_connected_once": False, "_abnormal_disconnects": 0, "_reconnects": 0,
        "_last_registration_sent_at": 0.0, "_last_trade_observed_at": None,
        "_last_trade_observed_clock": None, "_reconnect_started_at": None})
    _empty(collector._minute_bars, {"_bars": dict, "_operation_ids": dict, "_cumulative": dict,
                                 "_dirty": set, "_open_windows": dict})
    _scalars(collector._minute_bars, {"_trading_date": ""})
    _empty(collector._second_trades, {"_bars": dict, "_dirty": set,
                                    "_cumulative_volume": dict, "_seen": dict})
    _scalars(collector._second_trades, {"_latest_second": None, "_cumulative_date": ""})
    if collector._flush_lock.locked():
        raise ValueError("top20_session_collector_flush_in_progress")


async def _stop_subscription_driver(runtime, worker, *, timeout):
    """Repeated waiter cancellation cannot skip actual input shutdown."""
    if worker is None:
        return False
    worker.cancel()
    joining = asyncio.gather(worker, return_exceptions=True)
    cancelled = False
    deadline = asyncio.get_running_loop().time() + timeout
    while not joining.done():
        try:
            await asyncio.wait_for(asyncio.shield(joining),
                                  max(.001, deadline - asyncio.get_running_loop().time()))
        except asyncio.CancelledError:
            if not cancelled:
                runtime._failure("input_stop_waiter_cancelled", "CancelledError")
            cancelled = True
        except TimeoutError:
            runtime._failure("input_stop_timeout", "TimeoutError")
            with runtime._lock:
                runtime._timed_out = True
                runtime._phase = "quarantined"
            raise RuntimeError("replay_runtime_not_drained") from None
    joining.result()
    return cancelled


async def execute_top20_session(service, broker, collector, *, clock, runtime, bindings,
        subscription_tape, source_hub_component, source_component, prepared_inputs,
        started_mono_ns, end_seconds, window_start_seconds=0.0, include_top20=True, drain_timeout=60.0,
        outbox_fixture=None, peer_events=None, peer_operation_ids=(), peer_concurrency=8):
    """Start/receive/native stop+drain; caller retains DB/file ownership throughout.

    An excluded TOP20 contributes no subscription. The collector may still
    receive tape inputs, but filters them by the new native requested universe.
    No past TOP20/store result is executed to fill that excluded state.
    """
    if (type(service) is not AutonomousTop20Service or type(broker) is not CentralRestBroker
            or type(collector) is not CentralRealtimeCollector or type(runtime) is not ReplayRuntimeScope
            or type(clock) is not Top20FixtureClock
            or type(bindings) is not TaskTapeBindings or type(subscription_tape) is not SubscriptionTape
            or service._broker is not broker or service._hub is not collector._hub
            or service._store is not broker._store or service._store is not collector._store
            or service._now != clock.now or collector._now_provider != clock.now
            or broker._source_wall_time != clock.wall_time or type(include_top20) is not bool
            or not _store_clock_is_bound(service._store, clock)
            or type(window_start_seconds) not in (int, float) or not math.isfinite(window_start_seconds)
            or type(end_seconds) not in (int, float) or not math.isfinite(end_seconds)
            or not 0 <= window_start_seconds < end_seconds <= 600
            or type(drain_timeout) not in (int, float) or not math.isfinite(drain_timeout)
            or not 0 < drain_timeout <= 300
            or collector._input_replay_only or collector._task or collector._snapshot_task
            or collector._close_task or collector._market_events is not None
            or collector._account_scope_resolver is not None or collector._account_event_handler is not None
            or broker._client is not bindings.client
            or not source_component or not source_hub_component):
        raise ValueError("top20_session_resources_not_fresh_or_bound")
    if (runtime.status()["phase"] != "running" or runtime.status()["pending_tasks"]
            or runtime.status()["pending_threads"] or runtime.status()["error_count"]):
        raise ValueError("top20_session_runtime_not_fresh")
    _cold_state(service, clock, outbox_fixture)
    _require_fresh_collector(collector)
    # Decode/validation belongs to prepare_top20_source_inputs, never to a timer
    # after the first DB write. Reject direct unchecked input to the core.
    clean_inputs = [{key: item[key] for key in ("input_id", "kind", "entered_mono_ns", "seq", "payload")}
                    for item in prepared_inputs]
    if prepare_top20_source_inputs(clean_inputs, clock=clock, started_mono_ns=started_mono_ns,
                                  end_seconds=end_seconds) != prepared_inputs:
        raise ValueError("top20_session_prepared_inputs_changed")
    if (type(peer_operation_ids) is not tuple or type(peer_concurrency) is not int
            or any(type(value) is not str or not value for value in peer_operation_ids)
            or not 1 <= peer_concurrency <= 16 or peer_events is None and peer_operation_ids):
        raise ValueError("top20_session_peer_resources_invalid")
    if peer_events is not None:
        # The owned entry preflights source closure. Recheck the native peer
        # signatures before arm/start too, never after the first native write.
        from dataclasses import replace
        from .diagnostic_recorded_execution import _prepare
        from .diagnostic_replay_contract import compile_recorded_plan
        peer_plan = compile_recorded_plan(peer_events, started_mono_ns=started_mono_ns,
            window_start_seconds=0, window_end_seconds=end_seconds)
        if (len(set(peer_operation_ids)) != len(peer_operation_ids)
                or set(peer_operation_ids) - set(peer_plan.operation_ids)):
            raise ValueError("top20_session_peer_frontier_invalid")
        _prepare(service._store, peer_events, replace(peer_plan, operation_ids=peer_operation_ids))
    if outbox_fixture is not None:
        service._outbox = outbox_fixture.begin_run(runtime)
    live_component = f"autonomous_top20:{id(service):x}"
    inputs_report, worker, transport, peer_worker = [], None, None, None
    peer_stop, peer_report = Event(), None
    error = None
    with runtime.activate(), bindings.activate():
        clock.arm(start_seconds=0)
        try:
            if peer_operation_ids:
                from .diagnostic_recorded_execution import _execute_recorded_operations
                peer_worker = owned_create_task(_execute_recorded_operations(service._store, peer_events,
                    started_mono_ns=started_mono_ns, window_start_seconds=0, window_end_seconds=end_seconds,
                    clock=clock, _shared_runtime=runtime, _peer_operation_ids=peer_operation_ids,
                    concurrency=peer_concurrency, stop=peer_stop), name="top20-replay-peer-scheduler")
            await collector.start_input_replay()
            transport = Top20SubscriptionTransport(collector, subscription_tape,
                source_hub_component=source_hub_component,
                component_map={live_component: source_component},
                exclude_components=() if include_top20 else (source_component,))
            if include_top20:
                await service.start()
            async def subscriptions():
                while True:
                    await transport.update()
                    await asyncio.sleep(.01)
            worker = owned_create_task(subscriptions(), name="top20-replay-subscriptions")
            for item in prepared_inputs:
                await asyncio.sleep(max(0, item["offset_seconds"] - clock.elapsed()))
                if worker.done():
                    worker.result()
                if peer_worker is not None and peer_worker.done():
                    peer_report = peer_worker.result()
                    if peer_report["state"] != "complete":
                        raise RuntimeError("top20_session_peer_execution_incomplete")
                value, kind = item["payload"], item["kind"]
                counts = None
                if kind == "message":
                    counts = transport.registered_message(value["message"])
                elif kind == "capture_gap":
                    transport.gap(clear_continuous=value["clear_continuous"])
                elif kind == "market_operation":
                    # Use the native 0s parser/publisher, not a past hub queue.
                    # No public/account payload can be smuggled through here.
                    if transport.connection_approved and any("0s" in part["type"]
                            for parts in transport.groups.values() for part in parts):
                        tick = value["tick"]
                        collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0s", "item": "",
                            "values": {"215": tick["status_code"], "20": tick["trade_time"],
                                       "214": tick["remaining_time"]}}]})
                inputs_report.append({"source_input_id": item["input_id"], "kind": kind,
                    "source_message_id": value.get("message_id"), "source_row_offset": value.get("row_offset"),
                    "phase": "prefix" if clock.elapsed() < window_start_seconds else "measurement",
                    "lag_ms": round(max(0, clock.elapsed() - item["offset_seconds"]) * 1000, 3),
                    "received_rows": counts[0] if counts else None,
                    "delivered_rows": counts[1] if counts else None})
            await asyncio.sleep(max(0, end_seconds - clock.elapsed()))
            if worker.done():
                worker.result()
        except BaseException as failure:
            error = failure
        finally:
            # Stop incoming external input before native producer/DB shutdown.
            peer_stop.set()
            cancelled = await _stop_subscription_driver(runtime, worker, timeout=drain_timeout)
            if cancelled and error is None:
                error = asyncio.CancelledError()
            measurement_finished = clock.elapsed()
            shutdown_started = time.monotonic()
            await close_top20_replay_resources(runtime, service, broker, collector=collector,
                                                timeout=drain_timeout)
            if peer_worker is not None:
                peer_report = peer_worker.result()
    if error is not None:
        raise error
    report = {"scope": "native_top20_lifecycle_core", "fixture": "controlled_fixture",
        "source_state_equivalent": False, "include_top20": include_top20,
        "native_membership": service.latest_membership_snapshot(),
        "native_stage_ready": [(code, stage, day) for (code, stage), day in sorted(service._entry_stage_ready.items())],
        "source_inputs": inputs_report, "rest": bindings.client.report(), "task_bindings": bindings.report(),
        "subscription": transport.report(), "runtime": runtime.status(),
        "peer_execution": peer_report,
        "measurement_finished_seconds": measurement_finished,
        "shutdown_flush_ms": round((time.monotonic()-shutdown_started)*1000, 3),
        "historical_outputs_injected": False, "full_experiment_acceptance": False}
    report["execution_succeeded"] = bool(report["runtime"]["execution_succeeded"]
        and not report["rest"]["failure"] and not report["subscription"]["failure"]
        and (peer_report is None or peer_report["state"] == "complete"))
    return report

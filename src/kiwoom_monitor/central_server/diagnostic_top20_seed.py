"""Offline cold-fixture identity gate for the future TOP20 session runner.

This is deliberately not a live RAM checkpoint, DB restore, or executor. A new
native service must match the explicit cold state on every run. Warm/in-flight
objects cannot be made replayable by copying their implementation dictionaries.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from .autonomous_top20 import AutonomousTop20Service, KST
from .diagnostic_replay_baseline import DEFAULT_CONFIG, TABLES, TABLES_V2, ReplayDatabaseLease
from .realtime_hub import RealtimeHub
from .rest_broker import CentralRestBroker


SEED_VERSION = "top20-cold-fixture/v1"
CLOCK_POLICY = "source_wall_real_elapsed/v1"

# Explicit semantic inventory, not __dict__/pickle or object serialization.
# v1 intentionally supports only a fresh, unsubscribed controlled fixture.
_SERVICE_EMPTY = {
    "preparation": {
        "_fundamentals_ready": dict, "_entry_stage_ready": dict, "_entry_data_ready": dict,
        "_daily_input_versions": dict, "_daily_verified_versions": dict,
        "_daily_stage_versions": dict, "_daily_history_locks": dict,
    },
    "catalog_subscription": {
        "_markets": dict, "_nxt_eligible": dict, "_nxt_checked_on": dict,
    },
    "ranking_entrants": {
        "_last_aux_ranking_slots": dict, "_entrant_first_seen": dict,
        "_entrant_persisted_codes": set, "_account_entry_codes": tuple,
        "_backfill_entrant_persisted_codes": set,
    },
    "backfill_calendar": {
        "_krx_trading_day_cache": dict, "_calendar_unknown_logged_days": set,
        "_backfill_completed_steps": dict,
    },
    "pending_storage": {"_pending_index_records": dict, "_pending_program_snapshots": dict},
}
_SERVICE_SCALARS = {
    "_observed_dropped_events": 0, "_market_catalog_day": "", "_subscription_revision": 0,
    "_subscription_day": "", "_last_ranking_slot": "", "_last_backfill_day": "",
    "_backfill_retry_day": "", "_backfill_retry_at": 0.0, "_backfill_attempts": 0,
    "_backfill_progress_day": "", "_entrants_day": "",
    "_backfill_entrant_first_seen": None, "_latest_membership_snapshot": None,
}
_SERVICE_LIFECYCLE = {
    "_tasks": list, "_fundamentals_pending": set, "_fundamentals_tasks": set,
    "_subscriber": None, "_market_catalog_task": None, "_subscription_task": None,
    "_backfill_task": None, "_program_save_task": None, "_close_task": None,
}
_COLLECTOR_EMPTY = {
    "active_codes": tuple, "next_codes": tuple, "samples": list,
    "segment_baselines": dict, "minute_codes": set, "cohort_segments": list,
}
_COLLECTOR_SCALARS = {
    "next_activation": None, "minute": None, "minute_complete": False,
    "last_observed_at": None, "segment_accumulated": (0.0, 0.0, 0.0),
}
_AGGREGATOR_EMPTY = (
    "_bars", "_last_cumulative_volume", "_last_cumulative_trade_value",
    "_estimated_since_cumulative", "_source_mode_by_code", "_query_completed_minutes",
)
_BROKER_EMPTY = {
    "_inflight": dict, "_flow_inflight": dict, "_lookup_tasks": set, "_cache": dict,
}
_BROKER_SCALARS = {
    "_worker": None, "_persist_worker": None, "_credential_paused": False,
    "_credential_generation": 0, "_drain_task": None, "_activation_task": None,
    "_activating_credentials": None, "_resume_task": None, "_close_task": None,
}


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


class Top20FixtureClock:
    """Freeze construction/preflight time; arm once at native service start.

    After arming, source wall time advances with real monotonic elapsed time.
    This does not change any existing service/broker timer or global clock.
    """
    policy = CLOCK_POLICY

    def __init__(self, origin: datetime):
        if not isinstance(origin, datetime) or origin.tzinfo is None or origin.utcoffset() is None:
            raise ValueError("top20_seed_source_clock_invalid")
        self._origin = origin.astimezone(KST)
        self._started = None
        self._start_seconds = 0.0

    @property
    def origin(self):
        return self._origin

    @property
    def armed(self):
        return self._started is not None

    def arm(self, *, start_seconds=0.0):
        if self.armed:
            raise ValueError("top20_seed_clock_already_armed")
        if (type(start_seconds) not in (int, float) or not math.isfinite(start_seconds)
                or start_seconds < 0):
            raise ValueError("top20_seed_clock_start_invalid")
        self._start_seconds = float(start_seconds)
        self._started = time.monotonic()

    def elapsed(self):
        return 0.0 if not self.armed else self._start_seconds + time.monotonic() - self._started

    def now(self):
        return self.origin + timedelta(seconds=self.elapsed())

    def wall_time(self):
        return self.now().timestamp()

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class Top20ColdFixtureSeed:
    ram_seed_id: str
    document: bytes


def _empty(owner, fields):
    result = {}
    for name, kind in fields.items():
        value = getattr(owner, name, object())
        if kind is None:
            valid = value is None
        else:
            valid = isinstance(value, kind) and len(value) == 0
        if not valid:
            raise ValueError(f"top20_seed_non_cold_or_missing:{name}")
        result[name] = "empty_" + kind.__name__ if kind is not None else None
    return result


def _scalars(owner, fields):
    for name, expected in fields.items():
        value = getattr(owner, name, object())
        if type(value) is not type(expected) or value != expected:
            raise ValueError(f"top20_seed_non_cold_or_missing:{name}")
    return fields


def _cold_state(service, clock, outbox_fixture=None):
    if type(service) is not AutonomousTop20Service or type(clock) is not Top20FixtureClock:
        raise ValueError("top20_seed_native_fixture_required")
    if clock.armed or service._now != clock.now:
        raise ValueError("top20_seed_clock_not_frozen_or_bound")
    broker, hub = service._broker, service._hub
    if type(broker) is not CentralRestBroker or type(hub) is not RealtimeHub:
        raise ValueError("top20_seed_native_resources_required")
    # Persistent caching must not be silently switched off for an experiment.
    if broker._store is None or broker._store is not service._store:
        raise ValueError("top20_seed_persistent_cache_store_missing_or_shared")
    outbox_identity = None
    if service._outbox is not None or outbox_fixture is not None:
        from .diagnostic_top20_outbox import Top20ReplayOutbox
        from .persistent_outbox import JsonRecordOutbox
        if (type(outbox_fixture) is not Top20ReplayOutbox or type(service._outbox) is not JsonRecordOutbox
                or service._outbox._path != outbox_fixture.path):
            raise ValueError("top20_seed_durable_outbox_adapter_missing")
        outbox_identity = outbox_fixture.identity()
    state = {name: _empty(service, fields) for name, fields in _SERVICE_EMPTY.items()}
    state["service_markers"] = _scalars(service, _SERVICE_SCALARS)
    state["lifecycle"] = _empty(service, _SERVICE_LIFECYCLE)
    for owner, names in ((service, ("_collector_lock", "_index_outbox_lock", "_daily_input_lock")),
                         (broker, ("_guard",))):
        if any(getattr(owner, name).locked() for name in names):
            raise ValueError("top20_seed_lock_in_use")
    collector, aggregator = service._collector, service._trade_values
    from kiwoom_monitor.application.top20_trade_value_collector import Top20TradeValueCollector
    from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
    from collections import deque
    if type(collector) is not Top20TradeValueCollector or type(aggregator) is not MinuteTradeValueAggregator:
        raise ValueError("top20_seed_native_aggregation_required")
    state["cohort"] = {**_empty(collector, _COLLECTOR_EMPTY), **_scalars(collector, _COLLECTOR_SCALARS)}
    if (not isinstance(collector.completed, deque) or collector.completed
            or collector.completed.maxlen != 1440 or aggregator._max_minutes != 1440):
        raise ValueError("top20_seed_aggregation_retention_mismatch")
    state["trade_values"] = _empty(aggregator, dict.fromkeys(_AGGREGATOR_EMPTY, dict))
    state["broker"] = {**_empty(broker, _BROKER_EMPTY), **_scalars(broker, _BROKER_SCALARS)}
    for name in ("_queue", "_persist_queue"):
        queue = getattr(broker, name)
        if not queue.empty() or queue._unfinished_tasks:
            raise ValueError("top20_seed_broker_queue_not_drained")
    if hub.client_count or hub._upstream_codes or hub.upstream_ready:
        raise ValueError("top20_seed_shared_or_live_subscription")
    if type(service._minute_backfill_enabled) is not bool:
        raise ValueError("top20_seed_service_config_invalid")
    state["configuration"] = {
        "minute_backfill_enabled": service._minute_backfill_enabled,
        "completed_retention": 1440, "trade_retention": 1440,
        "durable_outbox": outbox_identity or "absent_in_cold_fixture",
        "broker_namespace": broker._namespace, "ranking_reservation": broker._ranking_reservation,
        "allowed_endpoints": dict(broker._allowed_endpoints), "persistent_cache_enabled": True,
    }
    return state


def _baseline_identity(baseline):
    if type(baseline) is not dict or type(baseline.get("manifest")) is not dict:
        raise ValueError("top20_seed_baseline_manifest_missing")
    manifest = baseline["manifest"]
    if baseline.get("baseline_id") != _digest(manifest):
        raise ValueError("top20_seed_baseline_hash_mismatch")
    if (type(manifest.get("version")) is not int or manifest["version"] not in (1, 2)
            or manifest.get("origin") != "controlled_fixture"
            or manifest.get("source_state_equivalent") is not False
            or type(manifest.get("tables")) is not dict
            or set(manifest["tables"]) != set(TABLES if manifest["version"] == 1 else TABLES_V2)
            or type(manifest.get("config")) is not dict or set(manifest["config"]) != set(DEFAULT_CONFIG)
            or any(type(value) is not bool for value in manifest["config"].values())):
        raise ValueError("top20_seed_controlled_baseline_contract_mismatch")
    return {"baseline_id": baseline["baseline_id"], "version": manifest["version"],
            "config": dict(manifest["config"]), "tables": sorted(manifest["tables"])}


def seal_top20_cold_fixture(service, clock, *, baseline, source_manifest, outbox_fixture=None):
    """Bind a fresh native fixture to an immutable DB proof and input identity.

    The caller still needs a verified ReplayDatabaseLease restore. This function
    performs no DB/file/network operation and does not certify a source snapshot.
    """
    state = _cold_state(service, clock, outbox_fixture)
    if (type(source_manifest) is not dict
            or type(source_manifest.get("started_at")) not in (int, float)
            or not math.isfinite(source_manifest["started_at"])):
        raise ValueError("top20_seed_source_clock_missing")
    try:
        source_origin = datetime.fromtimestamp(source_manifest["started_at"], KST)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("top20_seed_source_clock_invalid") from error
    if source_origin != clock.origin:
        raise ValueError("top20_seed_source_clock_mismatch")
    baseline_identity = _baseline_identity(baseline)
    if baseline_identity['version'] == 1 and service._broker._source_wall_time is not None:
        raise ValueError('top20_seed_cache_clock_requires_v2')
    if baseline_identity['version'] == 2:
        store = service._store
        lease = getattr(store, '_lease', None)
        if (type(lease) is not ReplayDatabaseLease or lease.baseline_version != 2
                or lease._cache_clock is not clock or not lease._active or not lease._run_ready
                or store._generation != lease._generation
                or lease._baseline_id != baseline['baseline_id']
                or service._broker._source_wall_time != clock.wall_time
                or baseline['manifest'].get('cache_clock') != lease._read_clock_contract()):
            raise ValueError('top20_seed_broker_cache_clock_or_baseline_not_bound')
        state['configuration']['broker_cache_clock'] = CLOCK_POLICY
    document = _encoded({
        "version": SEED_VERSION if outbox_fixture is None else "top20-cold-fixture/v2",
        "fixture": "cold_controlled_fixture",
        "source_state_equivalent": False, "baseline": baseline_identity,
        "input_manifest_hash": _digest(source_manifest),
        "clock": {"policy": CLOCK_POLICY, "origin": clock.origin.isoformat()},
        "ram_state": state,
    })
    return Top20ColdFixtureSeed(hashlib.sha256(document).hexdigest(), document)


def preflight_top20_cold_fixture(seed, service, clock, *, baseline, source_manifest, frontier,
                                 outbox_fixture=None):
    """Check the same seed, input, and mask before any executor/reset is called."""
    if (type(seed) is not Top20ColdFixtureSeed or type(seed.document) is not bytes
            or hashlib.sha256(seed.document).hexdigest() != seed.ram_seed_id):
        raise ValueError("top20_seed_document_changed")
    current = seal_top20_cold_fixture(service, clock, baseline=baseline, source_manifest=source_manifest,
                                      outbox_fixture=outbox_fixture)
    if current != seed:
        raise ValueError("top20_seed_fixture_identity_mismatch")
    manifest_hash = _digest(source_manifest)
    if (frontier.get("source_integrity") != "checksummed_capture"
            or frontier.get("input_manifest_hash") != manifest_hash
            or frontier.get("scope") != "top20_queue_frontier_preflight_only"
            or frontier.get("top20_session_execution_ready") is not False):
        raise ValueError("top20_seed_verified_window_frontier_required")
    coverage = frontier.get("coverage")
    if (type(coverage) is not dict or coverage.get("trace_id") != source_manifest.get("trace_id")
            or type(coverage.get("component")) is not str or not coverage["component"]):
        raise ValueError("top20_seed_source_role_mapping_missing")
    selection = frontier.get("fixture_selection")
    if type(selection) is not dict or set(selection) != {
        "window_start_seconds", "window_end_seconds", "include_workloads", "exclude_workloads",
    }:
        raise ValueError("top20_seed_selection_missing")
    start, end = selection["window_start_seconds"], selection["window_end_seconds"]
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= 7200
            or end - start > 600):
        raise ValueError("top20_seed_window_invalid")
    masks = []
    for key in ("include_workloads", "exclude_workloads"):
        values = selection[key]
        if (type(values) not in (list, tuple) or any(type(value) is not str or not value for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("top20_seed_selection_invalid")
        masks.append(sorted(values))
    if set(masks[0]) & set(masks[1]):
        raise ValueError("top20_seed_selection_invalid")
    # Same mask applies from the seed through warm-up and measurement. This is
    # not a full-workload warm-up followed by an exclusion-only measurement.
    identity = {"baseline_id": baseline["baseline_id"], "ram_seed_id": seed.ram_seed_id,
                "input_manifest_hash": manifest_hash,
                "source_role_mapping": {coverage["component"]: "top20_service"},
                "selection": {"window_start_seconds": start, "window_end_seconds": end,
                              "include_workloads": masks[0], "exclude_workloads": masks[1],
                              "mask_applies_from_seconds": 0},
                "clock": {"policy": CLOCK_POLICY, "origin": clock.origin.isoformat()}}
    if outbox_fixture is not None:
        identity["outbox"] = outbox_fixture.identity()
    return {**identity, "experiment_id": _digest(identity), "seed_identity_verified": True,
            "fixture": "cold_controlled_fixture", "source_state_equivalent": False,
            "database_restore_verified": False, "warm_state_equivalent": False,
            "top20_session_execution_ready": False, "execution_authorized": False,
            "blockers": (
                *(("persistent_query_cache_outside_baseline_v1",
                   "broker_and_service_wall_clock_adapters_pending")
                  if baseline['manifest']['version'] == 1 else ("service_wall_clock_audit_pending",)),
                "rest_catalog_subscription_inputs_and_service_runner_pending",
                "dedicated_lease_restore_and_durable_outbox_fixture_pending",
            )}


def recorded_top20_cold_fixture_preflight(trace_id, seed, service, clock, *, baseline,
                                          outbox_fixture=None, **selection):
    """Bind the checked long-capture reader to the offline seed gate, no execution."""
    from .diagnostic_trace import recorded_top20_window_frontier, status
    manifest = status(trace_id)
    # Reject changed/active RAM before expensive capture reads, still no reset.
    if seal_top20_cold_fixture(service, clock, baseline=baseline, source_manifest=manifest,
                              outbox_fixture=outbox_fixture) != seed:
        raise ValueError("top20_seed_fixture_identity_mismatch")
    frontier = recorded_top20_window_frontier(trace_id, **selection)
    proof = preflight_top20_cold_fixture(seed, service, clock, baseline=baseline,
                                        source_manifest=manifest, frontier=frontier,
                                        outbox_fixture=outbox_fixture)
    return {**frontier, "fixture_preflight": proof}

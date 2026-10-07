from __future__ import annotations

import copy
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.diagnostic_replay_baseline import DEFAULT_CONFIG, TABLES, TABLES_V2, ReplayDatabaseLease
from kiwoom_monitor.central_server.diagnostic_top20_seed import (
    Top20ColdFixtureSeed, Top20FixtureClock, preflight_top20_cold_fixture, seal_top20_cold_fixture,
)
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ForbiddenIO:
    def __getattr__(self, name):
        raise AssertionError(f"preflight must not access IO: {name}")


class Top20FixtureSeedTests(unittest.TestCase):
    def setUp(self):
        manifest = {"version": 1, "origin": "controlled_fixture", "source_state_equivalent": False,
                    "config": dict(DEFAULT_CONFIG), "tables": {name: {"rows": 0} for name in TABLES}}
        self.baseline = {"baseline_id": digest(manifest), "manifest": manifest}
        self.origin = datetime(2026, 10, 6, 8, 55, tzinfo=timezone(timedelta(hours=9)))
        self.manifest = {"trace_id": "fixture", "state": "complete", "schema_version": 3,
                         "started_at": self.origin.timestamp()}
        self.frontier = {"scope": "top20_queue_frontier_preflight_only",
            "source_integrity": "checksummed_capture", "input_manifest_hash": digest(self.manifest),
            "coverage": {"trace_id": "fixture", "component": "autonomous_top20:fixture"},
            "top20_session_execution_ready": False, "fixture_selection": {
                "window_start_seconds": 300.0, "window_end_seconds": 360.0,
                "include_workloads": ("top20", "collector"), "exclude_workloads": ()}}

    def fixture(self):
        clock = Top20FixtureClock(self.origin)
        store = ForbiddenIO()
        broker = CentralRestBroker(ForbiddenIO(), store, ranking_reservation=True)
        service = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=clock.now,
                                         catalog_loader=ForbiddenIO())
        return service, clock

    def seal(self, service, clock):
        return seal_top20_cold_fixture(service, clock, baseline=self.baseline, source_manifest=self.manifest)

    def preflight(self, seed, service, clock, **options):
        arguments = {"baseline": self.baseline, "source_manifest": self.manifest, "frontier": self.frontier}
        arguments.update(options)
        return preflight_top20_cold_fixture(seed, service, clock, **arguments)

    def test_three_new_native_fixtures_share_seed_identity_without_io_or_runtime_objects(self):
        seeds, reports = [], []
        for _ in range(3):
            service, clock = self.fixture()
            seed = self.seal(service, clock)
            seeds.append(seed)
            reports.append(self.preflight(seed, service, clock))
            self.assertEqual([], service._tasks)
            self.assertEqual(0, service._hub.client_count)
            self.assertIsNone(service._broker._worker)
        self.assertEqual([seeds[0]] * 3, seeds)
        self.assertEqual([reports[0]] * 3, reports)
        self.assertTrue(reports[0]["seed_identity_verified"])
        self.assertFalse(reports[0]["source_state_equivalent"])
        self.assertFalse(reports[0]["execution_authorized"])
        self.assertFalse(reports[0]["database_restore_verified"])
        self.assertFalse(reports[0]["top20_session_execution_ready"])
        self.assertIn("persistent_query_cache_outside_baseline_v1", reports[0]["blockers"])
        self.assertEqual("cold_controlled_fixture", json.loads(seeds[0].document)["fixture"])

    def test_v2_seed_requires_one_shared_broker_store_clock_and_restored_generation(self):
        from tests.unit.test_replay_cache_clock import URL, TOKEN
        service, clock = self.fixture()
        service._broker._source_wall_time = clock.wall_time
        with self.assertRaisesRegex(ValueError, 'requires_v2'):
            self.seal(service, clock)
        service._broker._source_wall_time = None
        lease = ReplayDatabaseLease(URL, TOKEN, baseline_version=2, cache_clock=clock)
        manifest = {**self.baseline['manifest'], 'version': 2,
                    'tables': {name: {'rows': 0} for name in TABLES_V2},
                    'cache_clock': lease._clock_contract}
        proof = {'baseline_id': digest(manifest), 'manifest': manifest}
        lease._active, lease._run_ready, lease._generation = True, True, 1
        lease._baseline_id = proof['baseline_id']
        store = lease.store()
        service._store = store
        service._broker._store = store
        with self.assertRaisesRegex(ValueError, 'not_bound'):
            seal_top20_cold_fixture(service, clock, baseline=proof, source_manifest=self.manifest)
        service._broker._source_wall_time = clock.wall_time
        seed = seal_top20_cold_fixture(service, clock, baseline=proof, source_manifest=self.manifest)
        report = self.preflight(seed, service, clock, baseline=proof)
        self.assertNotIn('persistent_query_cache_outside_baseline_v1', report['blockers'])
        self.assertIn('service_wall_clock_audit_pending', report['blockers'])
        self.assertFalse(report['execution_authorized'])
        self.assertFalse(report['database_restore_verified'])
        other_clock = Top20FixtureClock(clock.origin)
        service._broker._source_wall_time = other_clock.wall_time
        with self.assertRaisesRegex(ValueError, 'not_bound'):
            seal_top20_cold_fixture(service, clock, baseline=proof, source_manifest=self.manifest)
        service._broker._source_wall_time = clock.wall_time
        lease._generation += 1
        with self.assertRaisesRegex(ValueError, 'not_bound'):
            seal_top20_cold_fixture(service, clock, baseline=proof, source_manifest=self.manifest)

    def test_warm_markers_cohort_aggregation_or_pending_writes_cannot_be_cold_seeded(self):
        changes = (
            lambda s: s._entry_stage_ready.update({("005930", "daily"): "2026-10-06"}),
            lambda s: s._daily_input_versions.update({("005930", "KRX"): 1}),
            lambda s: setattr(s._collector, "active_codes", ("005930",)),
            lambda s: s._trade_values._last_cumulative_volume.update({("005930", "SOR"): 100}),
            lambda s: s._pending_program_snapshots.update({"p": {"value": 1}}),
            lambda s: setattr(s, "_last_ranking_slot", "2026-10-06T08:55:00"),
            lambda s: s._fundamentals_pending.add("005930"),
        )
        for change in changes:
            with self.subTest(change=change):
                service, clock = self.fixture()
                seed = self.seal(service, clock)
                change(service)
                with self.assertRaisesRegex(ValueError, "non_cold"):
                    self.preflight(seed, service, clock)

    def test_completed_task_close_lock_or_shared_subscription_is_not_a_safe_cold_start(self):
        changes = (
            lambda s: setattr(s, "_close_task", object()),
            lambda s: s._daily_input_lock.acquire(),
            lambda s: s._hub.connect(),
            lambda s: setattr(s._hub, "_upstream_ready", True),
            lambda s: setattr(s._broker, "_worker", object()),
            lambda s: s._broker._queue.put_nowait((1, 1, None)),
            lambda s: s._broker._cache.update({"cached": (1.0, object())}),
        )
        for change in changes:
            with self.subTest(change=change):
                service, clock = self.fixture()
                change(service)
                with self.assertRaises(ValueError):
                    self.seal(service, clock)

    def test_persistent_cache_or_outbox_cannot_be_silently_disabled_or_ignored(self):
        service, clock = self.fixture()
        service._broker._store = None
        with self.assertRaisesRegex(ValueError, "persistent_cache_store_missing"):
            self.seal(service, clock)
        service, clock = self.fixture()
        service._outbox = ForbiddenIO()
        with self.assertRaisesRegex(ValueError, "durable_outbox_adapter_missing"):
            self.seal(service, clock)

    def test_seed_baseline_input_and_service_configuration_are_bound_before_execution(self):
        service, clock = self.fixture()
        seed = self.seal(service, clock)
        broken = Top20ColdFixtureSeed(seed.ram_seed_id, seed.document + b" ")
        with self.assertRaisesRegex(ValueError, "document_changed"):
            self.preflight(broken, service, clock)
        baseline = copy.deepcopy(self.baseline)
        baseline["manifest"]["config"]["observation_history_enabled"] = False
        with self.assertRaisesRegex(ValueError, "baseline_hash_mismatch"):
            self.preflight(seed, service, clock, baseline=baseline)
        baseline["baseline_id"] = digest(baseline["manifest"])
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            self.preflight(seed, service, clock, baseline=baseline)
        changed_input = {**self.manifest, "bytes_written": 9}
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            self.preflight(seed, service, clock, source_manifest=changed_input)
        service._minute_backfill_enabled = False
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            self.preflight(seed, service, clock)

    def test_masks_use_same_seed_but_distinct_experiment_identity_from_warmup_start(self):
        service, clock = self.fixture()
        seed = self.seal(service, clock)
        full = self.preflight(seed, service, clock)
        excluded = copy.deepcopy(self.frontier)
        excluded["fixture_selection"].update(include_workloads=("top20",), exclude_workloads=("collector",))
        partial = self.preflight(seed, service, clock, frontier=excluded)
        self.assertEqual(full["ram_seed_id"], partial["ram_seed_id"])
        self.assertNotEqual(full["experiment_id"], partial["experiment_id"])
        self.assertEqual(0, partial["selection"]["mask_applies_from_seconds"])
        self.assertEqual(["collector"], partial["selection"]["exclude_workloads"])
        reordered = copy.deepcopy(self.frontier)
        reordered["fixture_selection"]["include_workloads"] = ("collector", "top20")
        self.assertEqual(full["experiment_id"], self.preflight(seed, service, clock,
                                                              frontier=reordered)["experiment_id"])
        other_owner = copy.deepcopy(self.frontier)
        other_owner["coverage"]["component"] = "autonomous_top20:peer"
        self.assertNotEqual(full["experiment_id"], self.preflight(seed, service, clock,
                                                                 frontier=other_owner)["experiment_id"])

    def test_wrong_frontier_or_selection_is_rejected_instead_of_silent_partial_replay(self):
        service, clock = self.fixture()
        seed = self.seal(service, clock)
        for field, value in (("input_manifest_hash", "0" * 64), ("source_integrity", "manifest_and_events"),
                             ("top20_session_execution_ready", True)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "verified_window_frontier"):
                    self.preflight(seed, service, clock, frontier={**self.frontier, field: value})
        for update in ({"window_start_seconds": True}, {"window_end_seconds": float("nan")},
                       {"include_workloads": ("top20", "top20")}, {"exclude_workloads": ("top20",)}):
            with self.subTest(update=update):
                changed = copy.deepcopy(self.frontier)
                changed["fixture_selection"].update(update)
                with self.assertRaises(ValueError):
                    self.preflight(seed, service, clock, frontier=changed)

    def test_source_clock_freezes_until_arm_then_uses_real_elapsed_and_cannot_be_reset(self):
        service, clock = self.fixture()
        with patch("kiwoom_monitor.central_server.diagnostic_top20_seed.time.monotonic", side_effect=[10, 11.25]):
            self.assertEqual(self.origin, service._now())
            self.assertEqual(self.origin, service._now())
            clock.arm()
            self.assertEqual(self.origin + timedelta(seconds=1.25), service._now())
        with self.assertRaisesRegex(ValueError, "already_armed"):
            clock.arm()
        with self.assertRaisesRegex(ValueError, "not_frozen_or_bound"):
            self.seal(service, clock)
        with self.assertRaisesRegex(ValueError, "source_clock_invalid"):
            Top20FixtureClock(datetime(2026, 10, 6, 9))

    def test_different_clock_origin_or_unbound_native_now_provider_is_rejected(self):
        service, clock = self.fixture()
        clock = Top20FixtureClock(self.origin + timedelta(seconds=30))
        service._now = clock.now
        with self.assertRaisesRegex(ValueError, "source_clock_mismatch"):
            self.seal(service, clock)
        service, clock = self.fixture()
        service._now = lambda: self.origin
        with self.assertRaisesRegex(ValueError, "not_frozen_or_bound"):
            self.seal(service, clock)


if __name__ == "__main__":
    unittest.main()

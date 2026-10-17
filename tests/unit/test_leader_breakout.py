from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from kiwoom_monitor.application.leader_breakout import (
    LeaderBreakoutContext, LeaderBreakoutPolicy, evaluate_leader_breakout,
    evaluate_leader_breakout_trade, evaluate_leader_breakout_minutes,
)
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor

AT = datetime.fromisoformat("2026-10-16T09:10:00+09:00")


def policy():
    # Test hypotheses only; none of these values are product defaults.
    return LeaderBreakoutPolicy(live_confirmation="observed_price_cross",
        research_confirmation="completed_minute_close_cross", minimum_confirmation_observations=1,
        persistence_seconds=0, breakout_buffer_bps=0, max_data_age_seconds=3,
        signal_valid_seconds=2, minimum_turnover_won=100_000_000,
        live_turnover_basis="0B_session_cumulative", research_turnover_basis="completed_minute_window",
        research_turnover_lookback_bars=1, structural_exit_rule="price_below_invalidation",
        hard_risk_profile_ref="test-explicit-risk", contexts=(LeaderBreakoutContext(
            "005930", "test-explicit-structure", (AT-timedelta(minutes=10)).isoformat(),
            (AT+timedelta(hours=1)).isoformat(), True, "test-leader-rule", 1000,
            "test-structure-rule", 980, 950),))


def trade(price, *, at=AT, **changes):
    return {"code": "005930", "market": "KRX", "trade_time": at.strftime("%H%M%S"),
        "current_price": price, "cumulative_trade_value": 100, **changes}


def tick(p, price, state=None, *, offset=0, source="test-source:1", **kwargs):
    at = AT+timedelta(seconds=offset)
    return evaluate_leader_breakout_trade(p, trade(price, at=at), received_at=at,
        source=source, state=state, **kwargs)


def bar(index, close, *, source="kiwoom-websocket-0B", value_kind="actual", sequence=None):
    start = AT+timedelta(minutes=index)
    return {"kind": "minute_bar", "venue": "KRX", "source": source, "value_kind": value_kind,
        "accepted_sequence": index+1 if sequence is None else sequence,
        "revision_id": f"minute:{index}:{source}:{close}", "completeness": "complete",
        "available_at": (start+timedelta(hours=6)).isoformat(),
        "payload": {"market": "KRX", "code": "005930", "bar_start": start.isoformat(),
            "bar_end": (start+timedelta(minutes=1)).isoformat(), "window_closed": True,
            "capture_quality": "complete", "open": 990, "high": 1100, "low": 970,
            "close": close, "trade_value_million_won": 100}}


def execution_config():
    from kiwoom_monitor.application.leader_breakout import LeaderBreakoutConfig
    raw = policy().to_dict()
    raw["execution_contract"] = {"version": "leader_breakout_execution/v1", "sizing_mode": "fixed_quantity",
        "quantity": 2, "capital_won": 10000, "order_type": "LIMIT", "session_exit_policy": "hold_across_sessions",
        "session_exit_buffer_seconds": 0, "exit_quantity_rule": "all_owned",
        "research_exit_observation": "completed_minute_close", "leader_rule_ref": "test-leader-rule",
        "structure_rule_ref": "test-structure-rule", "hard_risk_profile": {"version": "leader_hard_risk/v1",
        "profile_ref": "test-explicit-risk", "rule": "pinned_absolute_price"}}
    return LeaderBreakoutConfig.from_dict(raw)


def write_leader_dataset(root, prices=(990, 1010, 1020, 940, 930)):
    dataset = root/"dataset"
    dataset.mkdir()
    rows = [bar(i, price, source="kiwoom-ka10080", value_kind="estimated") for i, price in enumerate(prices)]
    for i, row in enumerate(rows, 1):
        row["ordinal"] = i
        row["subject"] = "005930:KRX"
        row["observation_key"] = row["payload"]["bar_start"]
        row["payload"].update(volume=100, open=row["payload"]["close"],
            high=max(1100, row["payload"]["close"]), low=min(900, row["payload"]["close"]))
    encoded = ("\n".join(json.dumps(row) for row in rows)+"\n").encode()
    (dataset/"observations.jsonl").write_bytes(encoded)
    (dataset/"manifest.json").write_text(json.dumps({"dataset_id": "leader-contract-fixture", "revision_count": len(rows),
        "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest(),
        "observations_file": "observations.jsonl", "observations_file_hash": hashlib.sha256(encoded).hexdigest()}), encoding="utf-8")
    return dataset


class LeaderBreakoutExecutionContractTests(unittest.TestCase):
    def test_new_frozen_input_is_handed_to_same_decision_campaign_without_relabeling_policy(self):
        from kiwoom_monitor.research_process import load_research_process_request, register_campaign_request, discover_campaign_inputs, execute_campaign_cycle
        from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); baseline = write_leader_dataset(root)
            watch = root/"prepared"; watch.mkdir()
            candidate = write_leader_dataset(watch, prices=(995, 1010, 1020, 940, 930))
            for directory in (baseline, candidate):
                path = directory/"manifest.json"; doc = json.loads(path.read_text())
                doc.update(captured_range={"start":AT.isoformat(), "end":(AT+timedelta(minutes=6)).isoformat()}, kinds=["minute_bar"], subject="005930:KRX")
                path.write_text(json.dumps(doc), encoding="utf-8")
            path = root/"request.json"; path.write_text(json.dumps({"mode":"leader_breakout", "dataset":"dataset",
                "policy":LeaderBreakoutPolicy().to_dict(), "session_profile":"krx-regular/v1",
                "database":"research.sqlite3", "runs_dir":"runs"}), encoding="utf-8")
            request = load_research_process_request(path); repo = ResearchRepository(request.database)
            repo.create_campaign("new-input", "fixture", ResearchCampaignPolicy())
            first = register_campaign_request(repo, "new-input", request)
            repo.save_campaign_input_source("new-input", first["job_id"], watch)
            repo.set_campaign_desired_state("new-input", "RUNNING")
            claim = repo.claim_campaign_worker("new-input", owner_token="fixture-worker", lease_seconds=3600)
            scanned = discover_campaign_inputs(repo, "new-input", claim)
            self.assertEqual(1, scanned["registered"], scanned)
            jobs = repo.load_campaign_jobs("new-input")
            self.assertEqual(2, len(jobs))
            self.assertEqual(jobs[0]["request"]["policies"], jobs[1]["request"]["policies"])
            self.assertNotEqual(jobs[0]["request"]["dataset_hash"], jobs[1]["request"]["dataset_hash"])
            for _ in range(2):
                self.assertEqual("COMPLETED", execute_campaign_cycle(repo, "new-input", request.runs_dir, worker_claim=claim)["cycle_outcome"])

    def test_decision_campaign_outputs_cannot_write_into_frozen_inputs(self):
        from kiwoom_monitor.research_process import load_research_process_request, register_campaign_request
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); dataset = write_leader_dataset(root)
            before = {p.name:p.read_bytes() for p in dataset.iterdir()}
            path = root/"request.json"
            base = {"mode":"leader_breakout", "dataset":"dataset", "session_profile":"krx-regular/v1",
                "policy":LeaderBreakoutPolicy().to_dict(), "database":"research.sqlite3", "runs_dir":"runs"}
            for field in ("database", "runs_dir"):
                path.write_text(json.dumps({**base, field:"dataset/unsafe-output"}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "must not overwrite"):
                    load_research_process_request(path)
            path.write_text(json.dumps(base), encoding="utf-8")
            request = load_research_process_request(path)
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                register_campaign_request(Mock(), "fixture", replace(request, database=dataset/"unsafe.sqlite3"))
            self.assertEqual(before, {p.name:p.read_bytes() for p in dataset.iterdir()})

    def test_minute_step_retains_only_explicit_window_and_missing_turnover_is_not_zero(self):
        from kiwoom_monitor.application.leader_breakout import evaluate_leader_breakout_bar_step, leader_research_rows
        from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        config = replace(execution_config(), research_turnover_lookback_bars=2)
        rows = [bar(i, p) for i, p in enumerate((990, 990, 1010, 1015))]
        for row in rows:
            row["payload"]["volume"] = 100
        projected = leader_research_rows(rows, session_profile="krx-regular/v1")
        frames = replay_krx_minute_bars(projected, strict=False, session_profile="krx-regular/v1")
        projected[1]["payload"].pop("trade_value_million_won")
        minute_state = None
        results = []
        for frame, original in zip(frames, projected):
            result, minute_state = evaluate_leader_breakout_bar_step(run_id="missing-turnover", evaluation_bar=frame,
                config=config, state=StrategyState(), minute_state=minute_state, original_observation=original)
            self.assertLessEqual(len(minute_state[1]), 2)
            results.append(result)
        self.assertIn("turnover_unavailable", results[2].decision.reasons)
        self.assertFalse(any(r.decision.final_action == "ENTER" for r in results))

    def test_decision_campaign_resume_skips_completed_policy_and_keeps_blocked_results(self):
        from kiwoom_monitor.research_process import load_research_process_request, register_campaign_request, execute_campaign_cycle
        from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from scripts.run_research import ResearchRunCancelled
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); write_leader_dataset(root)
            path = root/"decision.json"
            path.write_text(json.dumps({"mode":"leader_breakout", "dataset":"dataset", "session_profile":"krx-regular/v1",
                "policies":[LeaderBreakoutPolicy().to_dict(), policy().to_dict()],
                "database":"research.sqlite3", "runs_dir":"runs"}), encoding="utf-8")
            request = load_research_process_request(path); repo = ResearchRepository(request.database)
            repo.create_campaign("decision-resume", "fixture", ResearchCampaignPolicy())
            identity = register_campaign_request(repo, "decision-resume", request)
            repo.set_campaign_desired_state("decision-resume", "RUNNING")
            with patch("kiwoom_monitor.application.leader_breakout.evaluate_leader_breakout_minutes",
                    side_effect=[evaluate_leader_breakout_minutes(LeaderBreakoutPolicy(), (), session_profile="krx-regular/v1"), ResearchRunCancelled("fixture stop")]):
                interrupted = execute_campaign_cycle(repo, "decision-resume", request.runs_dir)
            self.assertEqual("INTERRUPTED", interrupted["cycle_outcome"])
            retained = repo.load_search_job(identity["job_id"])["result"]["policy_results"][0]
            with patch("kiwoom_monitor.application.leader_breakout.evaluate_leader_breakout_minutes", wraps=evaluate_leader_breakout_minutes) as evaluate:
                completed = execute_campaign_cycle(repo, "decision-resume", request.runs_dir)
            self.assertEqual("COMPLETED", completed["cycle_outcome"], completed)
            self.assertEqual(1, evaluate.call_count)
            result = repo.load_search_job(identity["job_id"])["result"]
            self.assertEqual(retained, result["policy_results"][0])
            self.assertEqual(2, result["completed_policy_count"])
            self.assertFalse(result["performance_verified"])

    def test_independent_final_and_publication_keep_v2_and_undecided_criteria_blocked(self):
        from tests.unit.test_research_final_preparation import FinalPreparationTests
        from kiwoom_monitor import research_process as rp
        from scripts import prepare_mock_automation_candidate as cli
        fixture = FinalPreparationTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        config = execution_config()
        config = replace(config, minimum_turnover_won=1_000_000, contexts=(replace(config.contexts[0],
            available_at="2026-09-14T09:00:00+09:00", valid_until="2026-09-14T10:00:00+09:00"),))
        request = replace(fixture.request, family="leader_breakout/v1", strategy=config)
        batch = fixture.make_batch((request,))
        path = fixture.root/"new-final-request.json"
        doc = json.loads((fixture.root/"request.json").read_text())
        doc.pop("development_partition", None)
        doc.update(family=request.family, strategy=request.strategy.to_dict(), dataset=str(request.dataset),
            database=str(request.database), runs_dir=str(request.runs_dir), evaluation=request.evaluation.to_dict())
        path.write_text(json.dumps(doc), encoding="utf-8")
        policy_path = fixture.root/"new-policy.json"
        cli.freeze_policy(path, policy_path, strategy_ref="new-explicit-fixture")
        prepared = fixture.prepare(batch=batch, candidates=(request,))
        result = rp.execute_final_holdout_evaluation(prepared, owner_token="new-final-fixture")
        row = result["candidates"][0]
        self.assertEqual("COMPLETED", row["state"], row)
        output = fixture.root/"new-publication.json"
        published = cli.prepare_publication(path, policy_path, output, account_ref="fixture-account",
            final_batch_id=prepared.batch.batch_id, final_run_id=row["run_id"])
        self.assertEqual("final_candidate/v2", published["package"]["candidate_spec"]["version"])
        self.assertEqual("BLOCKED", published["eligibility_receipt"]["status"])
        self.assertTrue(any(r.startswith("TBD:") for r in published["eligibility_receipt"]["reasons"]))

    def test_explicit_regular_exit_uses_current_completed_close_and_no_max_hold(self):
        from kiwoom_monitor.application.breakout_strategy import StrategyState, REGULAR_SESSION_EXIT_POLICY
        from kiwoom_monitor.application.leader_breakout import evaluate_leader_breakout_bar, leader_research_rows
        from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
        config = execution_config()
        config = replace(config, execution_contract=replace(config.execution_contract,
            session_exit_policy=REGULAR_SESSION_EXIT_POLICY, session_exit_buffer_seconds=120))
        row = bar(0, 1010, source="kiwoom-ka10080", value_kind="estimated")
        row["payload"].update(bar_start=AT.replace(hour=15, minute=17).isoformat(),
            bar_end=AT.replace(hour=15, minute=18).isoformat(), volume=100)
        frame = replay_krx_minute_bars(leader_research_rows([row], session_profile="krx-regular/v1"),
            strict=False, session_profile="krx-regular/v1")[0]
        state = StrategyState(status="open", symbol="005930", position_quantity=2,
            entry_price=1001, opened_at=AT.isoformat())
        value = evaluate_leader_breakout_bar(run_id="session-fixture", evaluation_bar=frame,
            bar_history=(frame,), universe_frames=(), config=config, state=state, position_context=config.contexts[0])
        self.assertEqual("EXIT", value.decision.final_action)
        self.assertIn("session_exit_due", value.decision.reasons)
        self.assertNotIn("max_hold_reached", value.decision.reasons)

    def test_performance_campaign_executes_persists_resumes_and_exports_v2_without_live_contexts(self):
        from tests.unit.research_test_support import research_request_document
        from kiwoom_monitor.research_process import (load_research_process_request, execute_process_request,
            register_campaign_request, execute_campaign_cycle, final_candidate_spec_document, final_candidate_spec_hash)
        from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from kiwoom_monitor.application.mock_automation_candidate import _validate_candidate_spec
        from kiwoom_monitor.application.research_implementation import research_implementation_hash
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); dataset = write_leader_dataset(root)
            manifest = json.loads((dataset/"manifest.json").read_text())
            doc = research_request_document()
            doc.update(mode="limited_search", family="leader_breakout/v1", strategy=execution_config().to_dict(),
                session_profile="krx-regular/v1", database="research.sqlite3", runs_dir="runs")
            doc["execution"]["cost_model"].update(valid_from="2026-10-16T00:00:00+09:00", valid_to="2026-10-17T00:00:00+09:00")
            doc["evaluation"]["folds"] = [{"name":"train", "role":"TRAIN", "start":AT.isoformat(), "end":(AT+timedelta(minutes=6)).isoformat()}]
            doc["search"] = {"version":"limited_search/v2", "hypothesis_refs":["explicit-test-only"],
                "dataset_id":manifest["dataset_id"], "dataset_hash":manifest["revision_ids_hash"],
                "family_allowlist":["leader_breakout/v1"], "factor_allowlist":["leader_structure/v1"],
                "parameter_space":{"breakout_buffer_bps":[0, 5]}, "objective":{"net_pnl_won":"maximize"},
                "constraints":{}, "max_trials":2, "max_seconds":60, "seed":1, "include_no_trade_baseline":False,
                "resource_budget":{"max_concurrent_trials":1, "cpu_duty_percent":100, "memory_mb":512}}
            path = root/"performance.json"; path.write_text(json.dumps(doc), encoding="utf-8")
            request = load_research_process_request(path)
            repo = ResearchRepository(request.database); repo.create_campaign("performance", "fixture", ResearchCampaignPolicy())
            identity = register_campaign_request(repo, "performance", request)
            repo.set_campaign_desired_state("performance", "RUNNING")
            result = execute_campaign_cycle(repo, "performance", request.runs_dir)
            self.assertEqual("COMPLETED", result["cycle_outcome"], result)
            trials = repo.load_search_trials(request.search.experiment_id)
            self.assertEqual(2, len(trials))
            self.assertTrue(all(t["outcome"]["run_id"] for t in trials))
            fills = [e for t in trials for e in repo.load_execution_events(t["outcome"]["run_id"]) if e["event_type"] == "FILL"]
            self.assertEqual({"BUY", "SELL"}, {e["side"] for e in fills})
            self.assertEqual(0, execute_process_request(request)["attempted_now"])
            code_hash = research_implementation_hash(request.session_profile)
            final_request = replace(request, mode="single_run", search=None)
            candidate = final_candidate_spec_document(final_request, code_hash)
            self.assertEqual(("final_candidate/v2", []), (candidate["version"], candidate["parameters"]["contexts"]))
            _validate_candidate_spec(candidate, final_candidate_spec_hash(final_request, code_hash))
            self.assertTrue(request.strategy.contexts)
            doc["search"]["ablations"] = ["rank_persistence"]
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no registered rank ablation"):
                load_research_process_request(path)

    def test_registered_config_roundtrip_and_null_does_not_size(self):
        from kiwoom_monitor.application.research_families import parse_strategy_config
        config = execution_config()
        self.assertEqual(config.to_dict(), parse_strategy_config("leader_breakout/v1", json.loads(json.dumps(config.to_dict()))).to_dict())
        self.assertEqual((), config.execution_missing("1m"))
        draft = parse_strategy_config("leader_breakout/v1", {})
        self.assertIsNone(draft.quantity)
        self.assertIn("execution_contract", draft.execution_missing("0B"))
        self.assertNotIn("stop_loss_bps", config.to_dict())

    def test_rule_identity_excludes_context_but_not_rules(self):
        config = execution_config()
        self.assertEqual(config.rules_hash, replace(config, contexts=()).rules_hash)
        self.assertNotEqual(config.rules_hash, replace(config, minimum_turnover_won=200_000_000).rules_hash)

    def test_real_shadow_proof_is_recomputed_and_tampering_rejected(self):
        from kiwoom_monitor.application.leader_breakout import validate_leader_shadow_event
        store = Mock()
        monitor = CandidateMonitor(store, policy(), poll_seconds=1, universe_max_age_seconds=60, now_provider=lambda: AT)
        monitor._leader_accepting = True
        monitor.observe_trade(trade(990), AT, source="s", approved=True)
        monitor.observe_trade(trade(1010), AT+timedelta(milliseconds=1), source="s", approved=True)
        monitor.run_once()
        event = next(call.args[2] for call in store.save_shadow_evaluation.call_args_list if call.args[2])
        validate_leader_shadow_event(replace(execution_config(), contexts=()), event)
        bad = deepcopy(event); bad["observation"]["payload"]["current_price"] = 500
        with self.assertRaises(ValueError):
            validate_leader_shadow_event(execution_config(), bad)
        with self.assertRaisesRegex(ValueError, "monitor identity"):
            validate_leader_shadow_event(execution_config(), {**event, "run_id":"foreign-monitor"})

    def test_expired_entry_context_still_protects_actual_position(self):
        config = execution_config()
        expired = replace(config.contexts[0], valid_until=(AT+timedelta(seconds=1)).isoformat())
        raw, _ = evaluate_leader_breakout_trade(config.policy, trade(940, at=AT+timedelta(seconds=2)),
            received_at=AT+timedelta(seconds=2), source="s", position_open=True, position_context=expired)
        self.assertEqual(("EXIT", "HARD_EXIT"), (raw["proposal"], raw["status"]))

    def test_performance_uses_final_query_close_then_next_open_and_no_intrabar_stop(self):
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine, SimulationExecutionConfig, SimulationCostModel
        from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
        from kiwoom_monitor.application.leader_breakout import leader_research_rows, evaluate_leader_breakout_bar
        config = execution_config()
        rows = [bar(i, price, source="kiwoom-ka10080", value_kind="estimated") for i, price in enumerate((990,1010,1020,940,930))]
        for row in rows:
            row["payload"].update(open=row["payload"]["close"], volume=100, low=900)
        projected = leader_research_rows(rows, session_profile="krx-regular/v1")
        frames = replay_krx_minute_bars(projected, strict=False, session_profile="krx-regular/v1")
        cost = SimulationCostModel("fixed_bps/v1", 0, 0, 0, "model_estimate", "explicit fixture", "2026-10-16T00:00:00+09:00", "2026-10-17T00:00:00+09:00")
        engine = PaperExecutionEngine("leader-fixture", SimulationExecutionConfig("next_tradable_bar_open/v1",
            "conservative_with_optimistic_bound/v1", 10000, cost), config, "krx-regular/v1")
        events = []
        for index, frame in enumerate(frames):
            events.extend(engine.process_bar(frame))
            value = evaluate_leader_breakout_bar(run_id="leader-fixture", evaluation_bar=frame,
                bar_history=frames[:index+1], universe_frames=(), config=config, state=engine.strategy_state)
            events.extend(engine.process_decision(value.decision, value.state, source_bar=frame))
        fills = [e for e in events if e.event_type == "FILL"]
        self.assertEqual([("BUY",1020), ("SELL",930)], [(e.side,e.price) for e in fills])
        self.assertFalse(any(e.event_type == "RISK_FILL" for e in events))
        self.assertEqual("flat", engine.strategy_state.status)

    def test_decision_campaign_runs_restores_and_deduplicates_without_performance(self):
        from kiwoom_monitor.research_process import load_research_process_request, register_campaign_request, execute_campaign_cycle
        from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); write_leader_dataset(root)
            request_path = root/"request.json"
            request_path.write_text(json.dumps({"mode": "leader_breakout", "dataset": "dataset",
                "policy": LeaderBreakoutPolicy().to_dict(), "session_profile": "krx-regular/v1",
                "database": "research.sqlite3", "runs_dir": "runs"}), encoding="utf-8")
            request = load_research_process_request(request_path)
            repo = ResearchRepository(request.database)
            repo.create_campaign("leader", "fixture", ResearchCampaignPolicy())
            first = register_campaign_request(repo, "leader", request)
            self.assertFalse(register_campaign_request(repo, "leader", request)["registered"])
            repo.set_campaign_desired_state("leader", "RUNNING")
            result = execute_campaign_cycle(repo, "leader", root/"runs")
            self.assertEqual("COMPLETED", result["cycle_outcome"], result)
            restored = ResearchRepository(request.database).load_search_job(first["job_id"])
            self.assertEqual("completed", restored["status"])
            self.assertEqual("BLOCKED", restored["result"]["readiness"])
            self.assertNotIn("net_pnl_won", restored["result"])
            self.assertEqual((), repo.load_candidate_cards(first["experiment_id"]))


class LeaderBreakoutCampaignUiTests(unittest.TestCase):
    def test_windows_registers_finite_policies_through_child_and_restores_typed_campaign(self):
        from io import StringIO
        from PySide6.QtCore import QSettings, QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.presentation.research_dialog import ResearchDialog
        from kiwoom_monitor import research_process as rp
        qt = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); write_leader_dataset(root)
            request_path = root/"decision.json"
            request_path.write_text(json.dumps({"mode":"leader_breakout", "dataset":"dataset",
                "policies":[LeaderBreakoutPolicy().to_dict(), policy().to_dict()],
                "session_profile":"krx-regular/v1", "database":"research.sqlite3", "runs_dir":"runs"}), encoding="utf-8")
            preferences = QSettings(str(root/"settings.ini"), QSettings.Format.IniFormat)
            with patch("kiwoom_monitor.presentation.research_dialog.QSettings", return_value=preferences), \
                    patch("kiwoom_monitor.presentation.research_dialog.QTimer.singleShot"):
                dialog = ResearchDialog(root/"state")
                process = Mock(); process.poll.return_value = None
                try:
                    dialog._request_path.setText(str(request_path))
                    self.assertTrue(dialog._preview_request())
                    def launch(*args, **kwargs):
                        dialog._manager._process = process
                        return process
                    with patch.object(dialog._manager, "start", side_effect=launch), \
                            patch.object(rp, "load_research_input", side_effect=AssertionError("GUI must not load input")):
                        dialog._register_campaign_request()
                    registration = dialog._registration
                    self.assertIsNotNone(registration, dialog._campaign_status.text())
                    frozen = json.loads(registration["request"].read_text())
                    self.assertEqual(2, len(frozen["policies"]))
                    with patch("sys.stdout", new=StringIO()):
                        code = rp.main(["--request",str(registration["request"]), "--register-campaign",registration["selection"]["campaign_id"],
                            "--result",str(registration["result"]), "--cancel",str(registration["cancel"])])
                    self.assertEqual(0, code)
                    process.returncode = code
                    process.poll.return_value = 0
                    with patch.object(rp, "load_research_input", side_effect=AssertionError("GUI must not reload input")):
                        dialog._poll_process()
                    self.assertIsNone(dialog._registration)
                    self.assertIn("등록 완료", dialog._status.text())
                    self.assertTrue(dialog._campaign_run.isEnabled())
                finally:
                    process.poll.return_value = 0
                    dialog.stop(); dialog.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); qt.processEvents()


class LeaderBreakoutAutomationTests(unittest.IsolatedAsyncioTestCase):
    """Actual existing mock gates/SQLite/runtime, with a broker that only records calls."""
    def setUp(self):
        from tests.unit import test_mock_automation_specification as support
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        self._rank = support.MockAutomationWorkflowTests._rank.__get__(self)
        self._bar = support.MockAutomationWorkflowTests._bar.__get__(self)
        self._refresh_recovery = support.MockAutomationWorkflowTests._refresh_recovery.__get__(self)
        self._fill_pending = support.MockAutomationWorkflowTests._fill_pending.__get__(self)
        config = execution_config()
        config = replace(config, contexts=(replace(config.contexts[0], available_at=(support.NOW-timedelta(hours=1)).isoformat(),
            valid_until=(support.NOW+timedelta(days=1)).isoformat()),),
            execution_contract=replace(config.execution_contract, quantity=1))
        factory = support.mock_specification_inputs
        with patch.object(support, "mock_specification_inputs", side_effect=lambda store, **kw: factory(store, leader_config=config, **kw)):
            support.MockAutomationWorkflowTests.setUp(self)
        self.live = {"revision": 1, "policy": config.policy.to_dict()}
        self.store.upsert_documents("server_operational_settings", [{"owner": "global", "key": "current",
            "document": {"revision": 1, "shadow_candidate_config": self.live["policy"]}}])
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec, now_provider=lambda: self.clock,
            leader_context_source=lambda: self.live)
        self.addAsyncCleanup(self.runner.close)

    async def deliver(self, price, *, source="actual-0B-fixture:1", approved=True):
        from kiwoom_monitor.application.market_session_schedule import KST
        self.clock += timedelta(milliseconds=100)
        self.runtime.heartbeat()
        payload = {"code": "005930", "market": "KRX", "current_price": price,
                   "trade_time": self.clock.astimezone(KST).strftime("%H%M%S"), "cumulative_trade_value": 100}
        self.runner.observe_leader_trade(payload, self.clock, source=source, approved=approved)
        return await self.runner.run_once()

    async def test_actual_0b_runs_existing_approval_and_claim_then_fill_exit_restart(self):
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        with patch.object(self.store, "load_observation_bootstrap", side_effect=AssertionError("no minute bootstrap")), \
                patch.object(self.store, "load_observation_revisions_after", side_effect=AssertionError("no minute/second polling")):
            await self.deliver(990)
            await self.deliver(1001)
            self.assertEqual(["BUY"], [c[1] for c in self.transport.calls], self.runner.status)
            self.assertEqual("candidate", self.runner.status["strategy_state"])
            self._fill_pending("BUY", 1001)
            await self.runner.close()
            self.runner = MockAutomationRunner(self.store, self.bundle, self.spec, now_provider=lambda: self.clock,
                leader_context_source=lambda: self.live)
            self.addAsyncCleanup(self.runner.close)
            self.live = None  # Entry evidence loss cannot erase pinned position protection.
            await self.deliver(990)
            self.assertEqual("open", self.runner.status["strategy_state"])
            await self.deliver(940)
            self.assertEqual(["BUY", "SELL"], [c[1] for c in self.transport.calls], self.runner.status)
            self._fill_pending("SELL", 940)
            await self.runner.run_once()
            self.assertEqual("flat", self.runner.status["strategy_state"])
            await self.runner.run_once()
            self.assertEqual(2, len(self.transport.calls))

    async def test_generation_or_unapproved_gap_cannot_bridge_entry(self):
        await self.deliver(990, source="one")
        await self.deliver(1001, source="two")
        self.assertEqual([], self.transport.calls)
        await self.deliver(990, source="two", approved=False)
        await self.deliver(1001, source="two")
        self.assertEqual([], self.transport.calls)
        await self.deliver(990, source="two")
        await self.deliver(1001, source="two")
        self.assertEqual(1, len(self.transport.calls), self.runner.status)

    async def test_pending_order_does_not_create_position_or_duplicate_entry(self):
        await self.deliver(990); await self.deliver(1001)
        await self.deliver(1002)
        self.assertNotEqual("open", self.runner.status["strategy_state"])
        self.assertEqual(1, len(self.transport.calls))

    async def test_overflow_and_dequeue_staleness_do_not_bridge_a_cross(self):
        await self.deliver(990)
        payload = trade(1001, at=self.clock)
        for _ in range(1001):
            self.runner.observe_leader_trade(payload, self.clock, source="overflow", approved=True)
        await self.runner.run_once()
        self.assertEqual([], self.transport.calls)
        await self.deliver(990, source="overflow")
        self.runner.observe_leader_trade(trade(1001, at=self.clock), self.clock, source="overflow", approved=True)
        self.clock += timedelta(seconds=4)
        await self.runner.run_once()
        self.assertEqual([], self.transport.calls)
        await self.deliver(1001, source="overflow")
        self.assertEqual([], self.transport.calls)

    async def test_absolute_stop_proof_uses_native_fill_basis_and_pinned_boundary(self):
        from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
        await self.deliver(990); await self.deliver(1001)
        self._fill_pending("BUY", 1001)
        await self.runner.run_once(); await self._refresh_recovery()
        risk = self.repository.load_latest_mock_automation_risk(self.bundle.account_ref)
        pin = self.runner._leader_position_proof
        proof = {"version":"leader_absolute_stop/v1", "symbol":"005930", "quantity":1,
            "entry_price_won":1001, "price_won":940, "available_at":self.clock.isoformat(),
            "input_ref":"0B:fixture", "hard_stop_price":950, "position_proof":pin,
            "rules_hash":self.runner._config.rules_hash, "package_hash":self.spec.candidate_package_hash}
        validate_mock_stop_loss_proof(proof, self.spec, risk, leader_config=self.runner._config, position_proof=pin)
        with self.assertRaises(ValueError):
            validate_mock_stop_loss_proof({**proof, "hard_stop_price":980}, self.spec, risk,
                leader_config=self.runner._config, position_proof=pin)
        with self.assertRaises(ValueError):
            validate_mock_stop_loss_proof({**proof, "quantity":2}, self.spec, risk,
                leader_config=self.runner._config, position_proof=pin)

    async def test_transaction_recomputes_0b_evidence_and_rejects_tampering(self):
        native = self.store.upsert_documents
        def tamper(collection, rows):
            if collection == "execution_leader_decisions":
                rows = deepcopy(rows)
                rows[0]["document"]["observation"]["payload"]["current_price"] = 500
            return native(collection, rows)
        await self.deliver(990)
        with patch.object(self.store, "upsert_documents", side_effect=tamper):
            with self.assertRaisesRegex(RuntimeError, "LEADER_0B_DECISION_PROOF_CHANGED"):
                await self.deliver(1001)
        self.assertEqual([], self.transport.calls)

    async def test_current_context_change_before_queue_claim_rejects_entry(self):
        native = self.store.upsert_documents
        def changed(collection, rows):
            result = native(collection, rows)
            if collection == "execution_leader_decisions":
                raw = deepcopy(self.live["policy"])
                raw["contexts"] = []
                native("server_operational_settings", [{"owner": "global", "key": "current",
                    "document": {"revision": 2, "shadow_candidate_config": raw}}])
            return result
        await self.deliver(990)
        with patch.object(self.store, "upsert_documents", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "LEADER_CURRENT_ENTRY_CONTEXT_CHANGED"):
                await self.deliver(1001)
        self.assertEqual([], self.transport.calls)


class LeaderBreakoutNativeProtectionTests(unittest.IsolatedAsyncioTestCase):
    """The new absolute proof passes the original native account/queued-claim path."""
    from tests.unit.test_mock_automation_specification import MockFirstProtectionExitTests as _fixture
    unfilled_execution_policy = _fixture.unfilled_execution_policy
    setUp = LeaderBreakoutAutomationTests.setUp
    deliver = LeaderBreakoutAutomationTests.deliver

    async def asyncSetUp(self):
        from tests.unit.test_mock_automation_specification import MockNativeRiskProtectionTests
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
        await MockNativeRiskProtectionTests.asyncSetUp(self)
        await self.runner.close()
        self.collector = CentralRealtimeCollector(lambda:"unused", "real", RealtimeHub(), lambda:self.clock)
        self.collector._approved_trade_sources = {("005930","KRX")}
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec, now_provider=lambda:self.clock,
            leader_context_source=lambda:self.live, price_source=self.collector.automation_price_observation)
        self.collector.set_breakout_trade_listener(lambda payload, at, **source:self.runner.observe_leader_trade(payload, at, **source))
        self.bundle.gateway = ManualMockOrderGateway(self.runtime, self.execution, self.monitor.refresh_account, now_provider=lambda:self.clock)

    async def asyncTearDown(self):
        await self.runner.close(); await self.monitor.close(); await self.bundle.gateway.close()

    def hard_tick(self):
        from kiwoom_monitor.application.market_session_schedule import KST
        self.clock += timedelta(milliseconds=20)
        self.collector._publish_parsed({"trnm":"REAL", "data":[{"type":"0B", "item":"005930",
            "values":{"10":"940", "14":"100", "15":"1", "20":self.clock.astimezone(KST).strftime("%H%M%S")}}]})

    async def test_absolute_native_stop_submits_once_and_unknown_ack_survives_restart(self):
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        await self.deliver(990); await self.deliver(1001)
        self._fill_pending("BUY", 1001)
        await self.runner.run_once(); await self.monitor.refresh_recovery()
        self.hard_tick()
        notice = self.runner._protection_snapshot()
        self.assertEqual("leader_absolute_stop/v1", notice["risk_trigger"]["stop_loss"]["version"])
        await self.monitor.refresh_recovery()
        submit = self.transport.submit
        def unknown(intent):
            submit(intent)
            raise SubmissionUnknown("fixture lost ACK")
        with patch.object(self.transport, "submit", side_effect=unknown), \
                patch.object(self.runner, "_prepare_native_unfilled_evidence", side_effect=AssertionError("no EV wait")):
            await self.runner.run_once()
        self.assertEqual(["BUY","SELL"], [c[1] for c in self.transport.calls], self.runner.status)
        self.assertEqual("PROTECTIVE_EXIT", self.runner._unfilled_registration["purpose"])
        await self.runner.close()
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec, now_provider=lambda:self.clock,
            leader_context_source=lambda:None, price_source=self.collector.automation_price_observation)
        for _ in range(3):
            self.hard_tick(); await self.runner.run_once()
        self.assertEqual(2, len(self.transport.calls))


class DocumentLeaderBreakoutTests(unittest.TestCase):
    def draft(self,seconds=120):
        from kiwoom_monitor.application.leader_breakout import document_leader_breakout_policy
        value=document_leader_breakout_policy()
        return replace(value,reference_level={**value.reference_level,'window_seconds':seconds})

    def native_rows(self):
        return [dict(trading_date='2026-10-16',minute=minute,code='005930',market='KRX',
            high=high,completeness='complete',source='kiwoom-websocket-0B',value_kind='actual',
            available_at=(AT-timedelta(seconds=1)).timestamp())
            for minute,high in (('09:08',995),('09:09',1000),('09:10',99999))]

    def test_document_draft_fills_only_named_rules_and_preserves_original_v1_serialization(self):
        from kiwoom_monitor.application.leader_breakout import document_leader_breakout_policy
        draft=document_leader_breakout_policy()
        self.assertEqual('prior_window_high',draft.reference_level['type'])
        self.assertIsNone(draft.reference_level['window_seconds'])
        self.assertIsNone(draft.minimum_turnover_won)
        self.assertIsNone(draft.persistence_seconds)
        self.assertIsNone(draft.minimum_confirmation_observations)
        self.assertIsNone(draft.hard_risk_profile_ref)
        from kiwoom_monitor.application.leader_breakout import document_baseline_missing
        self.assertNotIn('breakout_buffer_bps',document_baseline_missing(draft,'0B'))
        self.assertNotIn('signal_valid_seconds',document_baseline_missing(draft,'0B'))
        self.assertNotIn('reference_level',policy().to_dict())
        self.assertEqual(policy().fingerprint,LeaderBreakoutPolicy.from_dict(policy().to_dict()).fingerprint)

    def test_prior_window_high_excludes_current_interval_future_receipt_and_incomplete_minutes(self):
        from kiwoom_monitor.application.leader_breakout import document_breakout_reference
        rows=self.native_rows();original=deepcopy(rows)
        result=document_breakout_reference(self.draft(),rows,symbol='005930',decision_time=AT,mode='0B')
        self.assertEqual(1000,result['reference_price'])
        self.assertEqual(2,len(result['input_refs']))
        self.assertEqual(original,rows)
        for changes in ({'completeness':'in_progress'},{'available_at':(AT+timedelta(hours=1)).timestamp()},
                        {'minute':'09:09:30'},{'market':'NXT'}):
            with self.subTest(changes=changes):
                altered=[rows[0],{**rows[1],**changes},rows[2]]
                self.assertIsNone(document_breakout_reference(self.draft(),altered,symbol='005930',decision_time=AT,mode='0B')['reference_price'])
        self.assertIsNone(document_breakout_reference(self.draft(30),rows,symbol='005930',decision_time=AT,mode='0B')['reference_price'])

    def test_approved_live_prices_observe_without_external_leader_and_without_callback_db_reads(self):
        store=Mock();store.load_minute_bars.return_value=self.native_rows()
        monitor=CandidateMonitor(store,self.draft(),poll_seconds=1,universe_max_age_seconds=90)
        monitor._leader_accepting=True
        at=AT+timedelta(seconds=3)
        with patch.object(store,'load_minute_bars',side_effect=AssertionError('0B callback DB read')):
            monitor.observe_trade(trade(990,at=at),at,source='source:1',approved=True)
        monitor.run_once()
        self.assertEqual(1,store.load_minute_bars.call_count)
        at+=timedelta(seconds=1)
        with patch.object(store,'load_minute_bars',side_effect=AssertionError('0B callback DB read')):
            monitor.observe_trade(trade(1010,at=at),at,source='source:1',approved=True)
        observation=monitor.quality['document_baseline_observations'][0]
        self.assertEqual(1000,observation['reference']['reference_price'])
        self.assertTrue(observation['observed_price_cross'])
        self.assertFalse(observation['confirmation_verified'])
        self.assertFalse(observation['order_authorized'])
        self.assertIsNone(observation['leader_eligible'])
        monitor.run_once()
        self.assertEqual(1,store.load_minute_bars.call_count)
        decisions=[call.args[1] for call in store.save_shadow_evaluation.call_args_list]
        self.assertTrue(any(d.get('baseline_observation',{}).get('observed_price_cross') for d in decisions))
        self.assertTrue(all(call.args[2] is None for call in store.save_shadow_evaluation.call_args_list))
        monitor.observe_trade(trade(900,at=at),at,source='source:1',approved=False)
        self.assertFalse(monitor.quality['document_baseline_observations'])

    def test_live_document_scope_and_state_are_bounded_and_null_window_never_reads_db(self):
        store=Mock();monitor=CandidateMonitor(store,self.draft(None),poll_seconds=1,universe_max_age_seconds=90)
        monitor._leader_accepting=True
        for index in range(201):
            at=AT+timedelta(milliseconds=index+1)
            monitor.observe_trade(trade(1000,at=at,code=f'{index+1:06d}'),at,source='scope',approved=True)
        self.assertEqual(200,len(monitor._document_inputs))
        self.assertLessEqual(len(monitor._leader_states),200)
        monitor.run_once()
        store.load_minute_bars.assert_not_called()
        self.assertEqual(200,len(monitor.quality['document_baseline_observations']))

    def test_minute_research_observes_same_reference_rule_without_ticks_fills_or_entry(self):
        observations=[bar(0,990),bar(1,990),bar(2,1010)]
        for index,row in enumerate(observations):
            row['payload']['high']=1000 if index<2 else 99999
        result=evaluate_leader_breakout_minutes(self.draft(),observations,session_profile='krx-regular/v1')
        value=result['decisions'][-1]['baseline_observation']
        self.assertEqual(1000,value['reference']['reference_price'])
        self.assertTrue(value['observed_price_cross'])
        self.assertEqual('1m',value['input_mode'])
        self.assertEqual(0,result['candidate_count'])
        self.assertFalse(result['fills_simulated'])
        self.assertFalse(result['order_authorized'])

    def test_document_observation_contract_cannot_be_activated_as_mock_execution(self):
        config=replace(execution_config(),reference_level=self.draft().reference_level)
        self.assertIn('document_baseline_observation_only',config.execution_missing('0B'))
        _,state=tick(config.policy,990)
        decision,_=tick(config.policy,1010,state,offset=1)
        self.assertFalse(decision['new_candidate'])
        self.assertEqual('WAIT',decision['proposal'])

    def test_document_profile_executes_through_frozen_research_request_and_result_formatter(self):
        from kiwoom_monitor.research_process import load_research_process_request,execute_process_request
        from kiwoom_monitor.presentation.research_dialog import format_research_result_rows
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);write_leader_dataset(root)
            request=root/'request.json'
            request.write_text(json.dumps({'mode':'leader_breakout','dataset':'dataset',
                'session_profile':'krx-regular/v1','policy':self.draft(60).to_dict()}),encoding='utf-8')
            result=execute_process_request(load_research_process_request(request))
            self.assertEqual(5,result['bar_count'])
            self.assertTrue(all('baseline_observation' in row for row in result['decisions']))
            self.assertIn('00종합 기준선',format_research_result_rows(result)[-1][-1])
            self.assertEqual(0,result['candidate_count'])
            self.assertFalse(result['fills_simulated'])

    def test_close_stops_new_native_reference_reads_and_commit_token_refreshes_once(self):
        import threading
        store=Mock();store.load_minute_bars.return_value=self.native_rows()
        monitor=CandidateMonitor(store,self.draft(),poll_seconds=1,universe_max_age_seconds=90)
        monitor._leader_accepting=True;token=['one']
        monitor.set_minute_commit_provider(lambda:token[0])
        at=AT+timedelta(seconds=3)
        monitor.observe_trade(trade(990,at=at),at,source='s',approved=True)
        monitor.run_once();monitor.run_once()
        self.assertEqual(1,store.load_minute_bars.call_count)
        token[0]='two';monitor.run_once()
        self.assertEqual(2,store.load_minute_bars.call_count)
        at+=timedelta(seconds=1)
        monitor.observe_trade(trade(990,at=at,code='000660'),at,source='s',approved=True)
        entered=threading.Event();release=threading.Event()
        def block(*args):
            entered.set();self.assertTrue(release.wait(5));return self.native_rows()
        store.load_minute_bars.side_effect=block
        token[0]='three'
        worker=threading.Thread(target=monitor.run_once)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            monitor._leader_accepting=False
        finally:
            release.set();worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(3,store.load_minute_bars.call_count)


class LeaderBreakoutTests(unittest.TestCase):
    def test_draft_blocks_without_legacy_five_bar_or_exit_defaults(self):
        p = LeaderBreakoutPolicy()
        self.assertIsNone(p.minimum_turnover_won)
        self.assertNotIn("stop_loss_bps", p.to_dict())
        self.assertNotIn("max_hold_minutes", p.to_dict())
        result, _ = tick(p, 1010)
        self.assertEqual(("BLOCKED", False), (result["status"], result["new_candidate"]))

    def test_real_0b_cross_can_happen_twice_within_same_second_without_second_bar(self):
        p = policy()
        _, state = tick(p, 999, offset=.1)
        result, state = tick(p, 1001, state, offset=.2)
        self.assertTrue(result["new_candidate"])
        self.assertEqual("0B", result["input_mode"])
        self.assertEqual(100_000_000, result["turnover_won"])
        repeated, _ = tick(p, 1002, state, offset=.3)
        self.assertFalse(repeated["new_candidate"])
        self.assertEqual(result["episode_id"], repeated["episode_id"])
        self.assertFalse(result["order_authorized"])

    def test_first_price_already_above_is_not_a_proven_cross(self):
        result, _ = tick(policy(), 1010)
        self.assertFalse(result["new_candidate"])
        self.assertIn("awaiting_observed_cross", result["reasons"])

    def test_unspecified_confirmation_count_or_persistence_does_not_mean_one_or_zero(self):
        for field in ("minimum_confirmation_observations", "persistence_seconds", "breakout_buffer_bps"):
            p = replace(policy(), **{field: None})
            _, state = tick(p, 990)
            result, _ = tick(p, 1010, state, offset=1)
            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("unconfigured:"+field, result["reasons"])

    def test_explicit_price_buffer_is_used_in_the_cross_comparison(self):
        p = replace(policy(), breakout_buffer_bps=100)
        _, state = tick(p, 1000)
        result, state = tick(p, 1010, state, offset=1)
        self.assertFalse(result["new_candidate"])
        result, _ = tick(p, 1011, state, offset=2)
        self.assertTrue(result["new_candidate"])

    def test_explicit_confirmation_observations_and_duration_are_both_enforced(self):
        p = replace(policy(), minimum_confirmation_observations=2, persistence_seconds=2)
        _, state = tick(p, 990)
        result, state = tick(p, 1010, state, offset=1)
        self.assertEqual("CONFIRMING", result["status"])
        result, state = tick(p, 1010, state, offset=2)
        self.assertFalse(result["new_candidate"])
        result, _ = tick(p, 1010, state, offset=3)
        self.assertTrue(result["new_candidate"])

    def test_connection_generation_change_cannot_bridge_old_price(self):
        _, state = tick(policy(), 990)
        result, _ = tick(policy(), 1010, state, offset=1, source="test-source:2")
        self.assertFalse(result["new_candidate"])

    def test_leader_is_not_inferred_from_rank_or_price(self):
        p = policy()
        p = replace(p, contexts=(replace(p.contexts[0], leader_eligible=None),))
        result, _ = tick(p, 1010)
        self.assertIn("unconfigured:leader_eligible", result["reasons"])
        self.assertEqual("BLOCKED", result["status"])

    def test_explicit_nonleader_does_not_enter(self):
        p = policy(); p = replace(p, contexts=(replace(p.contexts[0], leader_eligible=False),))
        _, state = tick(p, 990)
        result, _ = tick(p, 1010, state, offset=1)
        self.assertFalse(result["new_candidate"])
        self.assertIn("leader_not_eligible", result["reasons"])

    def test_missing_structure_does_not_fall_back_to_rolling_high(self):
        p = policy(); p = replace(p, contexts=(replace(p.contexts[0], reference_price=None),))
        result, _ = tick(p, 1010)
        self.assertIn("unconfigured:reference_price", result["reasons"])

    def test_future_leader_evidence_cannot_be_used(self):
        p = policy(); p = replace(p, contexts=(replace(p.contexts[0], available_at=(AT+timedelta(minutes=1)).isoformat()),))
        result, _ = tick(p, 1010)
        self.assertEqual("BLOCKED", result["status"])

    def test_out_of_order_input_resets_continuity_and_does_not_bridge_next_cross(self):
        _, state = tick(policy(), 990, offset=2)
        result, after = tick(policy(), 1010, state, offset=1)
        self.assertIn("out_of_order_observation", result["reasons"])
        self.assertIsNone(after.last_price)
        fresh, _ = tick(policy(), 1010, after, offset=3)
        self.assertFalse(fresh["new_candidate"])

    def test_same_delivery_does_not_create_a_new_cross(self):
        _, state = tick(policy(), 990)
        result, _ = tick(policy(), 1010, state)
        self.assertIn("duplicate_or_old_delivery", result["reasons"])

    def test_stale_trade_does_not_use_fresh_receipt_to_pass(self):
        result, _ = evaluate_leader_breakout_trade(policy(), trade(1010), received_at=AT+timedelta(seconds=10), source="s")
        self.assertIn("input_stale", result["reasons"])

    def test_missing_turnover_does_not_become_zero(self):
        result, _ = evaluate_leader_breakout_trade(policy(), trade(1010, cumulative_trade_value=None), received_at=AT, source="s")
        self.assertIsNone(result["turnover_won"])
        self.assertIn("turnover_unavailable", result["reasons"])

    def test_turnover_decline_does_not_force_exit_of_open_position(self):
        p = policy()
        result, _ = evaluate_leader_breakout_trade(p, trade(1010, cumulative_trade_value=1), received_at=AT, source="s", position_open=True)
        self.assertEqual("HOLD", result["proposal"])

    def test_explicit_structural_exit_uses_price_not_fixed_percent_or_elapsed_time(self):
        result, _ = tick(policy(), 970, position_open=True)
        self.assertEqual(("EXIT", "INVALIDATED"), (result["proposal"], result["status"]))

    def test_hard_exit_has_precedence_even_when_entry_confirmation_unconfigured(self):
        result, _ = tick(replace(policy(), live_confirmation=None), 940, position_open=True)
        self.assertEqual(("EXIT", "HARD_EXIT"), (result["proposal"], result["status"]))

    def test_invalidated_candidate_is_not_a_synthetic_position_exit(self):
        _, state = tick(policy(), 990)
        _, state = tick(policy(), 1010, state, offset=1)
        result, _ = tick(policy(), 970, state, offset=2)
        self.assertEqual(("WAIT", "INVALIDATED"), (result["proposal"], result["status"]))

    def test_minutes_use_close_cross_not_intraminute_high_or_synthetic_ticks(self):
        result = evaluate_leader_breakout_minutes(policy(), (bar(0, 990), bar(1, 995), bar(2, 1010)), session_profile="krx-regular/v1")
        self.assertEqual(1, result["candidate_count"])
        self.assertFalse(result["decisions"][1]["new_candidate"])
        self.assertTrue(result["decisions"][2]["new_candidate"])
        self.assertEqual("final_completed_minute", result["decisions"][2]["availability_basis"])
        self.assertFalse(result["fills_simulated"])

    def test_query_final_minute_wins_even_if_later_realtime_revision_differs(self):
        query = bar(1, 1010, source="kiwoom-ka10080;trade_value=ohlcv_estimate", value_kind="estimated", sequence=3)
        rows = (bar(0, 990), query, bar(1, 995, sequence=4))
        frozen = deepcopy(rows)
        result = evaluate_leader_breakout_minutes(policy(), rows, session_profile="krx-regular/v1")
        self.assertEqual((2, 1), (result["bar_count"], result["candidate_count"]))
        self.assertEqual(rows, frozen)

    def test_explicit_minute_turnover_window_with_missing_prefix_blocks(self):
        result = evaluate_leader_breakout_minutes(replace(policy(), research_turnover_lookback_bars=3),
            (bar(0, 990), bar(1, 1010)), session_profile="krx-regular/v1")
        self.assertEqual(0, result["candidate_count"])
        self.assertIn("turnover_unavailable", result["decisions"][-1]["reasons"])

    def test_research_draft_produces_blocked_rows_instead_of_legacy_trades(self):
        result = evaluate_leader_breakout_minutes(LeaderBreakoutPolicy(), (bar(0, 1010),), session_profile="krx-regular/v1")
        self.assertEqual("BLOCKED", result["decisions"][0]["status"])
        self.assertFalse(result["performance_verified"])

    def test_json_roundtrip_keeps_all_unconfigured_values_null(self):
        p = LeaderBreakoutPolicy()
        self.assertEqual(p, LeaderBreakoutPolicy.from_dict(json.loads(json.dumps(p.to_dict()))))
        with self.assertRaises(ValueError):
            LeaderBreakoutPolicy(max_data_age_seconds=True)

    def test_live_monitor_judges_0b_without_db_read_and_defers_writes(self):
        store = Mock()
        monitor = CandidateMonitor(store, policy(), poll_seconds=1, universe_max_age_seconds=90)
        monitor._leader_accepting = True
        monitor.observe_trade(trade(990), AT, source="s", approved=True)
        monitor.observe_trade(trade(1010), AT+timedelta(milliseconds=1), source="s", approved=True)
        self.assertEqual("CANDIDATE", monitor.quality["status"])
        store.assert_not_called()
        self.assertEqual([], store.mock_calls)
        monitor.run_once()
        candidates = [call.args[2] for call in store.save_shadow_evaluation.call_args_list if call.args[2] is not None]
        self.assertEqual(1, len(candidates))
        self.assertIsNone(candidates[0]["quantity"])
        self.assertFalse(candidates[0]["order_authorized"])
        store.load_observation_revisions_after.assert_not_called()

    def test_lost_source_cannot_bridge_old_price_on_reapproval(self):
        monitor = CandidateMonitor(Mock(), policy(), poll_seconds=1, universe_max_age_seconds=90)
        monitor._leader_accepting = True
        monitor.observe_trade(trade(990), AT, source="s", approved=True)
        monitor.observe_trade(trade(990), AT, source="s", approved=False)
        monitor.observe_trade(trade(1010), AT+timedelta(seconds=1), source="s", approved=True)
        self.assertEqual("WATCH", monitor.quality["status"])

    def test_minute_request_is_loaded_as_new_policy_not_legacy_family(self):
        from kiwoom_monitor.research_process import load_research_process_request
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/"dataset").mkdir()
            request = root/"request.json"
            request.write_text(json.dumps({"mode": "leader_breakout", "dataset": "dataset",
                "session_profile": "krx-regular/v1", "policy": policy().to_dict()}), encoding="utf-8")
            loaded = load_research_process_request(request)
            self.assertEqual("leader_breakout/v1", loaded.family)
            self.assertEqual(policy(), loaded.strategy)

    def test_non_krx_updates_do_not_erase_valid_krx_cross_state(self):
        monitor = CandidateMonitor(Mock(), policy(), poll_seconds=1, universe_max_age_seconds=90)
        monitor._leader_accepting = True
        monitor.observe_trade(trade(990), AT, source="s", approved=True)
        monitor.observe_trade(trade(1010, market="SOR"), AT+timedelta(milliseconds=1), source="s", approved=True)
        monitor.observe_trade(trade(1010), AT+timedelta(milliseconds=2), source="s", approved=True)
        self.assertEqual("CANDIDATE", monitor.quality["status"])

    def test_unknown_freshness_does_not_trigger_hard_exit_from_unchecked_price(self):
        result, _ = tick(replace(policy(), max_data_age_seconds=None), 940, position_open=True)
        self.assertEqual("BLOCKED", result["status"])

    def test_missing_completed_minutes_cannot_bridge_a_cross(self):
        result = evaluate_leader_breakout_minutes(policy(), (bar(0, 990), bar(3, 1010)), session_profile="krx-regular/v1")
        self.assertEqual(0, result["candidate_count"])
        self.assertIn("minute_observation_gap_no_cross_bridge", result["decisions"][-1]["reasons"])

    def test_research_command_cannot_write_an_error_result_into_frozen_input(self):
        from kiwoom_monitor.research_process import main
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/"dataset").mkdir()
            protected = root/"dataset/observations.jsonl"; protected.write_text("original", encoding="utf-8")
            request = root/"request.json"
            request.write_text(json.dumps({"mode": "leader_breakout", "dataset": "dataset",
                "session_profile": "krx-regular/v1", "policy": policy().to_dict()}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--request", str(request), "--result", str(protected)])
            self.assertEqual("original", protected.read_text(encoding="utf-8"))

    def test_both_screens_use_new_policy_without_inserting_legacy_parameters(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog
        from kiwoom_monitor.presentation.research_dialog import ResearchDialog
        app = QApplication.instance() or QApplication([])
        class Preferences:
            def __init__(self): self.data = {}
            def value(self, key, default=None, **kwargs): return self.data.get(key, default)
            def setValue(self, key, value): self.data[key] = value
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"), \
                patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=Preferences()):
            dialog = CandidateMonitorDialog(Mock())
            dialog._apply_operational_settings({"revision": 7, "shadow_candidate_config": LeaderBreakoutPolicy().to_dict()})
            with patch.object(dialog, "_start_settings_request") as save:
                dialog._save_operational_settings()
                config = save.call_args.args[0]["shadow_candidate_config"]
                self.assertEqual("leader_breakout/v1", config["policy_id"])
                self.assertIsNone(config["minimum_turnover_won"])
                self.assertNotIn("stop_loss_bps", config)
            dialog.stop(); dialog.deleteLater()
        with tempfile.TemporaryDirectory() as temp, patch("kiwoom_monitor.presentation.research_dialog.QSettings", return_value=Preferences()):
            root = Path(temp); (root/"dataset").mkdir()
            request = root/"request.json"
            request.write_text(json.dumps({"mode": "leader_breakout", "dataset": "dataset",
                "session_profile": "krx-regular/v1", "policy": LeaderBreakoutPolicy().to_dict()}), encoding="utf-8")
            dialog = ResearchDialog(root)
            dialog._request_path.setText(str(request))
            self.assertTrue(dialog._preview_request())
            self.assertEqual("분봉 돌파 판단 실행", dialog._run.text())
            self.assertIn("설정 미완료", dialog._summary.text())
            dialog.stop(); dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    def test_stopped_monitor_does_not_accept_a_late_delivery(self):
        monitor = CandidateMonitor(Mock(), policy(), poll_seconds=1, universe_max_age_seconds=90)
        monitor.observe_trade(trade(990), AT, source="s", approved=True)
        self.assertEqual({}, monitor._leader_states)

    def test_failed_candidate_write_is_retried_with_same_immutable_id(self):
        store = Mock(); store.save_shadow_evaluation.side_effect = [OSError("write failed"), None, None]
        monitor = CandidateMonitor(store, policy(), poll_seconds=1, universe_max_age_seconds=90)
        monitor._leader_accepting = True
        monitor.observe_trade(trade(990), AT, source="s", approved=True)
        monitor.observe_trade(trade(1010), AT+timedelta(milliseconds=1), source="s", approved=True)
        with self.assertRaises(OSError):
            monitor.run_once()
        first_id = store.save_shadow_evaluation.call_args.args[1]["decision_id"]
        self.assertEqual(2, monitor.run_once())
        self.assertEqual(first_id, store.save_shadow_evaluation.call_args_list[1].args[1]["decision_id"])

    def test_existing_implicit_five_bar_profile_is_retired_without_rewriting_saved_document(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"db.sqlite3"; store = SQLiteQueryStore(path); store.initialize()
            original = {"shadow_candidate_enabled": True, "shadow_candidate_config": default_shadow_breakout_config().to_dict()}
            store.upsert_documents("server_operational_settings", [{"owner": "global", "key": "current", "document": original}])
            with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
                app = create_app(CentralServerSettings("sqlite:///"+str(path), "test-token"))
            self.assertIsInstance(app.state.candidate_monitor._config, LeaderBreakoutPolicy)
            self.assertEqual("BLOCKED", app.state.candidate_monitor.quality["status"])
            self.assertEqual(original, store.load_document("server_operational_settings", "global", "current")["document"])
            store.close()

    def test_actual_research_process_uses_frozen_minutes_and_never_constructs_paper_engine(self):
        from kiwoom_monitor.research_process import load_research_process_request, execute_process_request
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); dataset = root/"dataset"; dataset.mkdir()
            rows = [{**row, "ordinal": n} for n, row in enumerate((bar(0, 990), bar(1, 1010)), 1)]
            encoded = ("\n".join(json.dumps(row) for row in rows)+"\n").encode()
            (dataset/"observations.jsonl").write_bytes(encoded)
            (dataset/"manifest.json").write_text(json.dumps({"dataset_id": "test-minutes", "revision_count": 2,
                "revision_ids_hash": hashlib.sha256("\n".join(row["revision_id"] for row in rows).encode()).hexdigest(),
                "observations_file": "observations.jsonl", "observations_file_hash": hashlib.sha256(encoded).hexdigest()}), encoding="utf-8")
            request = root/"request.json"
            request.write_text(json.dumps({"mode": "leader_breakout", "dataset": "dataset",
                "session_profile": "krx-regular/v1", "policy": policy().to_dict()}), encoding="utf-8")
            frozen = (dataset/"observations.jsonl").read_bytes()
            with patch("kiwoom_monitor.research_process.execute_research", side_effect=AssertionError("no legacy fills")):
                result = execute_process_request(load_research_process_request(request))
            self.assertEqual((2, 1), (result["bar_count"], result["candidate_count"]))
            self.assertFalse(result["performance_verified"])
            self.assertEqual(frozen, (dataset/"observations.jsonl").read_bytes())
            self.assertFalse((root/"research.sqlite3").exists())


class LeaderBreakoutSelectionTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog
        self.app = QApplication.instance() or QApplication([])
        class Preferences:
            def value(self, key, default=None): return default
            def setValue(self, key, value): pass
        for name, kwargs in (("CandidatePollWorker.start", {}),
                ("QSettings", {"return_value": Preferences()})):
            patcher = patch("kiwoom_monitor.presentation.candidate_monitor_dialog." + name, **kwargs)
            patcher.start(); self.addCleanup(patcher.stop)
        self.dialog = CandidateMonitorDialog(Mock())
        self.addCleanup(self.close_dialog)

    def close_dialog(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        self.dialog.stop(); self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def legacy_settings(self):
        from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
        return {"revision": 12, "shadow_candidate_enabled": True,
                "shadow_candidate_config": {**default_shadow_breakout_config().to_dict(), "lookback_bars": 17}}

    def test_initial_selector_hides_old_defaults_before_server_response(self):
        self.assertEqual("leader_breakout/v1", self.dialog._strategy_selector.currentData())
        self.assertTrue(self.dialog._lookback_bars.isHidden())
        self.assertFalse(self.dialog._leader_policy_editor.isHidden())
        self.assertFalse(self.dialog._settings_group.isEnabled())

    def test_old_server_profile_does_not_select_old_strategy_or_claim_new_applied(self):
        saved = self.legacy_settings(); original = deepcopy(saved)
        self.dialog._apply_operational_settings(saved)
        self.assertEqual(original, saved)
        self.assertEqual("leader_breakout/v1", self.dialog._strategy_selector.currentData())
        self.assertIn("기존 완료봉 비교", self.dialog._server_policy_status.text())
        self.assertIn("아직 전환되지", self.dialog._settings_status.text())
        self.assertFalse(self.dialog._candidate_enabled.isChecked())
        with patch.object(self.dialog, "_start_settings_request") as save:
            self.dialog._save_operational_settings()
        changes = save.call_args.args[0]
        self.assertEqual(12, changes["expected_revision"])
        from kiwoom_monitor.application.leader_breakout import document_leader_breakout_policy
        self.assertEqual(document_leader_breakout_policy().to_dict(), changes["shadow_candidate_config"])
        self.assertNotIn("stop_loss_bps", changes["shadow_candidate_config"])

    def test_legacy_is_explicit_and_preserves_its_saved_parameters_and_revision(self):
        saved = self.legacy_settings()
        self.dialog._apply_operational_settings(saved)
        self.dialog._strategy_selector.setCurrentIndex(1)
        self.assertEqual(17, self.dialog._lookback_bars.value())
        self.assertTrue(self.dialog._leader_policy_editor.isHidden())
        with patch.object(self.dialog, "_start_settings_request") as save:
            self.dialog._save_operational_settings()
        changes = save.call_args.args[0]
        self.assertEqual(saved["shadow_candidate_config"], changes["shadow_candidate_config"])
        self.assertEqual(12, changes["expected_revision"])

    def test_policy_edit_survives_comparison_switch_without_inheriting_legacy_numbers(self):
        self.dialog._apply_operational_settings(self.legacy_settings())
        self.dialog._leader_policy_editor.setPlainText(json.dumps(policy().to_dict()))
        self.dialog._strategy_selector.setCurrentIndex(1)
        self.dialog._strategy_selector.setCurrentIndex(0)
        with patch.object(self.dialog, "_start_settings_request") as save:
            self.dialog._save_operational_settings()
        self.assertEqual(policy().to_dict(), save.call_args.args[0]["shadow_candidate_config"])

    def test_missing_server_config_uses_unconfigured_new_policy(self):
        self.dialog._apply_operational_settings({"revision": 5})
        from kiwoom_monitor.application.leader_breakout import document_leader_breakout_policy
        self.assertEqual(document_leader_breakout_policy().to_dict(), self.dialog._strategy_config)
        self.assertFalse(self.dialog._strategy_selector.model().item(1).isEnabled())
        self.assertIn("미확인", self.dialog._server_policy_status.text())

    def test_failed_save_keeps_server_identity_and_does_not_fallback_to_legacy(self):
        self.dialog._apply_operational_settings(self.legacy_settings())
        self.dialog._show_settings_failure("unsupported policy")
        self.assertIn("기존 완료봉 비교", self.dialog._server_policy_status.text())
        self.assertIn("unsupported policy", self.dialog._settings_status.text())
        self.assertEqual("leader_breakout/v1", self.dialog._strategy_selector.currentData())
        self.dialog._leader_policy_editor.setPlainText('{"policy_id":"unknown"}')
        with patch.object(self.dialog, "_start_settings_request") as save:
            self.dialog._save_operational_settings()
        save.assert_not_called()

    def test_candidates_keep_their_actual_policy_and_old_hold_selector_is_separate(self):
        self.dialog._append_event({"strategy_id": "leader_breakout", "symbol": "005930"})
        self.dialog._append_event({"strategy_id": "breakout", "symbol": "005930"})
        self.assertIn("0B", self.dialog._table.item(0, 6).text())
        self.assertIn("기존 비교", self.dialog._table.item(1, 6).text())
        self.assertTrue(all(self.dialog._ram_thesis_policy.itemText(i).startswith("기존 보유 비교")
            for i in range(self.dialog._ram_thesis_policy.count())))
        self.assertEqual(-1, self.dialog._ram_thesis_policy.currentIndex())
        self.dialog._tabs.setCurrentIndex(1)
        self.dialog._ram_open_breakout.click()
        self.assertEqual(0, self.dialog._tabs.currentIndex())

    def test_status_refresh_updates_existing_candidate_without_alert_or_racing_new_candidate(self):
        event = {"event_id": "old", "symbol": "005930", "strategy_id": "leader_breakout", "status": "ACTIVE"}
        self.dialog._apply_page({"events": [event], "initial_sync": True})
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QApplication.beep") as beep:
            self.dialog._alert_enabled.setChecked(True)
            self.dialog._apply_page({"status_only": True, "high_watermark": 99, "events": [
                {**event, "status": "INVALIDATED"}, {**event, "event_id": "racing-new"}]})
            self.assertEqual(1, self.dialog._table.rowCount())
            self.assertEqual("INVALIDATED", self.dialog._table.item(0, 5).text())
            beep.assert_not_called()
            # The forward poll still owns first delivery and the new alert.
            self.dialog._apply_page({"events": [{**event, "event_id": "racing-new"}]})
            self.assertEqual(2, self.dialog._table.rowCount())
            beep.assert_called_once()

    def test_failed_or_bounded_status_refresh_never_leaves_an_unchecked_active_claim(self):
        event = {"event_id": "old", "symbol": "005930", "status": "ACTIVE"}
        self.dialog._apply_page({"events": [event], "initial_sync": True})
        self.dialog._apply_page({"status_only": True, "events": [], "status_error": "unreachable"})
        self.assertEqual("현재 미확인", self.dialog._table.item(0, 5).text())
        self.assertIn("unreachable", self.dialog._table.item(0, 5).toolTip())
        self.dialog._apply_page({"status_only": True, "events": [{**event, "status": "SUPERSEDED"}]})
        self.assertEqual("SUPERSEDED", self.dialog._table.item(0, 5).text())

    def test_worker_refresh_does_not_advance_forward_cursor_or_consume_racing_alert(self):
        from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidatePollWorker
        client = Mock(); calls = []
        worker = CandidatePollWorker(client)
        def read(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                worker.requestInterruption()
            return {"events": [], "next_cursor": 11 if len(calls) == 1 else 12,
                    "high_watermark": 11 if len(calls) == 1 else 12, "has_more": False}
        client.load_candidate_events.side_effect = read
        pages = []; worker.pageReceived.connect(pages.append)
        worker.set_polling_enabled(True)
        with patch.object(worker, "isInterruptionRequested", side_effect=lambda: len(calls) >= 2):
            worker.run()
        self.assertEqual(2, len(calls))
        self.assertEqual(11, worker._cursor)
        self.assertNotIn("status_only", pages[0])
        self.assertTrue(pages[1]["status_only"])

    def test_order_screens_do_not_present_legacy_execution_as_the_new_0b_strategy(self):
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationDialog
        from kiwoom_monitor.presentation.mock_automation_candidate_dialog import MockAutomationCandidateDialog
        preferences = Mock(); preferences.value.return_value = None
        with patch("kiwoom_monitor.presentation.mock_automation_dialog.QSettings", return_value=preferences):
            execution = MockAutomationDialog(Mock(), Mock())
        publication = MockAutomationCandidateDialog(Mock(), {"profile_id": "fixture", "account_ref": "fixture-account"}, {})
        try:
            self.assertIn("실제 0B", execution._strategy_scope.text())
            self.assertIn("미정 조건", execution._strategy_scope.text())
            self.assertIn("분봉 성과", publication._strategy_scope.text())
            self.assertIn("미정이면 차단", publication._strategy_scope.text())
        finally:
            execution.deleteLater(); publication.deleteLater()

    def test_new_candidate_cannot_be_used_as_old_family_order_evidence(self):
        from tests.unit.test_mock_automation_candidate import _package
        from kiwoom_monitor.application.mock_automation_specification import matching_shadow_events
        from kiwoom_monitor.application.research_families import (
            get_research_family, parse_strategy_config, shadow_monitor_id_for_config)
        package = _package(); spec = package.candidate_spec
        config = parse_strategy_config(spec["family"], spec["parameters"])
        legacy = {"event_id": "legacy", "available_at": AT.isoformat(), "quantity": 1,
            "strategy_id": spec["family"].rsplit("/", 1)[0],
            "strategy_version": spec["family"].rsplit("/", 1)[1], "transition": "flat->candidate",
            "run_id": shadow_monitor_id_for_config(spec["family"], config, "krx-regular/v1")}
        document = {"package": package.to_dict()}
        self.assertEqual((legacy,), matching_shadow_events(document, (legacy,)))
        self.assertEqual((), matching_shadow_events(document, ({**legacy,
            "strategy_id": "leader_breakout", "quantity": None},)))
        self.assertEqual("leader_breakout/v1", get_research_family("leader_breakout/v1").family_id)


class LeaderBreakoutLiveApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_collector_0b_to_existing_candidate_api_and_replacement_fence(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"db.sqlite3"; store = SQLiteQueryStore(path)
            clock = [AT]
            supervisor = Mock()
            with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store), \
                    patch("kiwoom_monitor.central_server.mock_automation_supervisor.MockAutomationSupervisor", return_value=supervisor), \
                    patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient.server_now", side_effect=lambda: clock[0]):
                app = create_app(CentralServerSettings("sqlite:///"+str(path), "test-token",
                    kiwoom_app_key="fixture", kiwoom_secret_key="fixture",
                    credential_directory=str(Path(temp)/"vault"), account_identity_registry_enabled=True,
                    account_identity_hmac_key="fixture-only-key-with-32-characters-minimum",
                    autonomous_top20_enabled=False, market_event_collection_enabled=False))
                self.assertIs(app.state.mock_automation_supervisor, supervisor)
                collector = app.state.realtime_collector
                collector._approved_trade_sources = {("005930", "KRX")}
                headers = {"Authorization": "Bearer test-token"}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    self.assertEqual(401, (await client.put("/api/v1/settings/operations", json={})).status_code)
                    # Start from the actual Windows selector and existing settings
                    # document; its captured payload must activate the 0B owner.
                    from PySide6.QtWidgets import QApplication
                    from PySide6.QtCore import QCoreApplication, QEvent
                    from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog
                    qt = QApplication.instance() or QApplication([])
                    preferences = Mock(); preferences.value.side_effect = lambda key, default=None: default
                    with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"), \
                            patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=preferences):
                        dialog = CandidateMonitorDialog(Mock())
                        saved = (await client.get("/api/v1/settings/operations", headers=headers)).json()
                        dialog._apply_operational_settings(saved)
                        dialog._leader_policy_editor.setPlainText(json.dumps(policy().to_dict()))
                        dialog._candidate_enabled.setChecked(True)
                        with patch.object(dialog, "_start_settings_request") as save:
                            dialog._save_operational_settings()
                        changes = save.call_args.args[0]
                        dialog.stop(); dialog.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); qt.processEvents()
                    enabled = await client.put("/api/v1/settings/operations", headers=headers, json=changes)
                    self.assertEqual(200, enabled.status_code)
                    first = app.state.candidate_monitor
                    try:
                        with patch.object(store, "load_observation_revisions_after", side_effect=AssertionError("no minute polling")), \
                                patch.object(collector, "ram_observation_snapshot", side_effect=AssertionError("leader uses direct 0B listener")):
                            for price in (990, 1010):
                                collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0B", "item": "005930",
                                    "values": {"20": "091000", "10": str(price), "14": "100", "15": "1", "13": "1000"}}]})
                                clock[0] += timedelta(milliseconds=1)
                            first.run_once()
                        page = (await client.get("/api/v1/research/candidates", headers=headers)).json()
                        events = [event for event in page["events"] if event["strategy_id"] == "leader_breakout"]
                        self.assertEqual(1, len(events))
                        self.assertEqual("ACTIVE", events[0]["status"])
                        collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0B", "item": "005930",
                            "values": {"20": "091000", "10": "970", "14": "100", "15": "1", "13": "1001"}}]})
                        first.run_once()
                        invalidated = (await client.get("/api/v1/research/candidates", headers=headers)).json()
                        self.assertEqual("INVALIDATED", invalidated["events"][0]["status"])
                        replacement = await client.put("/api/v1/settings/operations", headers=headers, json={
                            "expected_revision": enabled.json()["revision"], "shadow_candidate_config": DocumentLeaderBreakoutTests().draft().to_dict()})
                        self.assertEqual(200, replacement.status_code)
                        self.assertFalse(first._leader_accepting)
                        self.assertEqual("BLOCKED", app.state.candidate_monitor.quality["status"])
                        baseline = app.state.candidate_monitor
                        with patch.object(store,'load_minute_bars',return_value=DocumentLeaderBreakoutTests().native_rows()):
                            for price in (990,1010):
                                clock[0] += timedelta(milliseconds=1)
                                collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930',
                                    'values':{'20':'091000','10':str(price),'14':'100','15':'1','13':str(2000+price)}}]})
                                baseline.run_once()
                        baseline_page=(await client.get('/api/v1/research/candidates',headers=headers)).json()
                        self.assertTrue(baseline_page['quality']['document_baseline_observations'][0]['observed_price_cross'])
                        self.assertFalse(baseline_page['quality']['document_baseline_observations'][0]['order_authorized'])
                        retained = deepcopy(store.load_shadow_candidates(0, 1000))
                        disabled = await client.put("/api/v1/settings/operations", headers=headers, json={
                            "expected_revision": replacement.json()["revision"], "shadow_candidate_enabled": False})
                        self.assertEqual(200, disabled.status_code)
                        before = supervisor.observe_leader_trade.call_count
                        collector._publish_parsed({"trnm":"REAL", "data":[{"type":"0B", "item":"005930",
                            "values":{"20":"091000", "10":"990", "14":"100", "15":"1", "13":"1002"}}]})
                        self.assertEqual(before+1, supervisor.observe_leader_trade.call_count)
                        self.assertEqual(990, supervisor.observe_leader_trade.call_args.args[0]["current_price"])
                        page = (await client.get("/api/v1/research/candidates", headers=headers)).json()
                        self.assertEqual("DISABLED", page["quality"]["status"])
                        self.assertEqual("SUPERSEDED", page["events"][0]["status"])
                        from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
                        legacy = await client.put("/api/v1/settings/operations", headers=headers, json={
                            "expected_revision": disabled.json()["revision"], "shadow_candidate_enabled": True,
                            "shadow_candidate_config": default_shadow_breakout_config().to_dict()})
                        self.assertEqual(200, legacy.status_code)
                        page = (await client.get("/api/v1/research/candidates", headers=headers)).json()
                        self.assertEqual("SUPERSEDED", page["events"][0]["status"])
                        self.assertEqual(retained, store.load_shadow_candidates(0, 1000))
                    finally:
                        if app.state.candidate_monitor is not None:
                            await app.state.candidate_monitor.close()
                        app.state.credential_runtime.vault.close()
            store.close()

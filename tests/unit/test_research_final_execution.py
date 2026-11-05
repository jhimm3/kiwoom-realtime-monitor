from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import hashlib
import sqlite3
from pathlib import Path
from threading import Barrier
import unittest
from unittest.mock import patch

from tests.unit import test_research_final_preparation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
from kiwoom_monitor import research_run as runner
from scripts import prepare_mock_automation_candidate as candidate_cli
from kiwoom_monitor import research_candidate as candidate_artifacts
from kiwoom_monitor.application.mock_automation_candidate import (
    CandidateEligibilityStatus,
    MockAutomationEligibilityPolicy,
)
from datetime import datetime, timedelta


class CandidateFinalPreparationReviewTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_historical_baseline_requests import DailyDevelopmentRequestTests
        DailyDevelopmentRequestTests.setUp(self)

    def prepare(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        prepare_daily_requests(self.selection, self.source, self.output)
        plan = json.loads((self.output / "daily-plan.json").read_text())
        self.development_end = max(datetime.fromisoformat(fold["end"]) for fold in plan["evaluation"]["folds"])

    def test_input_and_preparation_reuse_each_raw_plan_request_once(self):
        self.prepare()
        from pathlib import Path
        paths = [self.output / 'daily-plan.json', *(self.output / 'requests' / f'daily-{family}.json'
                  for family in ('breakout', 'pullback'))]
        originals = {path.resolve(): path.read_bytes() for path in paths}
        original_open, original_loads = Path.open, json.loads
        for operation in (candidate_artifacts.inspect_daily_development_inputs,
                          candidate_artifacts.inspect_final_preparation):
            with self.subTest(operation=operation.__name__):
                reads = dict.fromkeys(originals, 0)
                parses = dict.fromkeys(originals, 0)
                def opened(path, *args, **kwargs):
                    resolved = Path(path).resolve()
                    if resolved in reads:
                        reads[resolved] += 1
                    return original_open(path, *args, **kwargs)
                def loads(encoded, *args, **kwargs):
                    for path, raw in originals.items():
                        if encoded == raw:
                            parses[path] += 1
                    return original_loads(encoded, *args, **kwargs)
                with patch.object(Path, 'open', new=opened), patch.object(json, 'loads', new=loads):
                    result = operation(self.output)
                self.assertEqual([1, 1, 1], list(reads.values()))
                self.assertEqual([1, 1, 1], list(parses.values()))
                self.assertEqual('REVIEW_REQUIRED', result['status'])
                self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        self.assertFalse((self.output / 'research.sqlite3').exists())

    def test_reused_development_document_keeps_raw_size_and_strict_encoding_boundary(self):
        self.prepare()
        path = self.output / 'requests/daily-breakout.json'
        document = json.loads(path.read_bytes())
        document['request']['note'] = 'raw-surrogate-marker'
        text = json.dumps(document)
        encoded = text.encode('utf-8')
        maximum = 1024 * 1024
        variants = {
            'utf8': encoded,
            'utf8_bom': b'\xef\xbb\xbf' + encoded,
            'utf16_bom': text.encode('utf-16'),
            'utf16_le': text.encode('utf-16-le'),
            'utf32_le': text.encode('utf-32-le'),
            'raw_surrogate': encoded.replace(b'raw-surrogate-marker', b'\xed\xa0\x80'),
            'escaped_surrogate': encoded.replace(b'raw-surrogate-marker', b'\\ud800'),
            'exact_1MiB': encoded + b' ' * (maximum - len(encoded)),
            'over_1MiB': encoded + b' ' * (maximum + 1 - len(encoded)),
        }
        def expected(raw):
            if len(raw) > maximum:
                raise ValueError('development validation request exceeds 1 MiB')
            return json.loads(raw.decode('utf-8-sig'))
        def outcome(operation):
            try:
                operation()
                return ('value',)
            except (ValueError, OSError) as error:
                return ('error', type(error), str(error))
        for name, raw in variants.items():
            with self.subTest(case=name):
                path.write_bytes(raw)
                self.assertIsInstance(json.loads(raw), dict)
                self.assertEqual(outcome(lambda: expected(raw)),
                    outcome(lambda: candidate_artifacts.inspect_daily_development_inputs(self.output)))
                self.assertEqual(raw, path.read_bytes())

    def test_missing_choices_are_not_defaults_and_no_policy_source_or_ledger_is_written(self):
        self.prepare()
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with (patch.object(candidate_artifacts, "ResearchRepository", side_effect=AssertionError("no DB")),
              patch.object(rp, "load_research_input", side_effect=AssertionError("no OOS"))):
            report = candidate_cli.inspect_final_preparation(self.output)
        self.assertEqual("REVIEW_REQUIRED", report["status"])
        self.assertTrue(all(value is None for value in report["criteria"].values()))
        self.assertIsNone(report["final_window"]["start"])
        self.assertEqual(7, sum(reason.startswith("TBD:") for reason in report["reasons"]))
        self.assertFalse(report["criteria_frozen"])
        self.assertFalse(report["final_request_created"])
        self.assertFalse(report["candidate_eligibility_evaluated"])
        self.assertFalse(report["oos_queried"])
        self.assertNotIn("policy_id", report)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertIn("완료 거래 최소(건): 미정", candidate_cli.final_preparation_text(report))

    def test_all_choices_and_adjacent_window_do_not_approve_final_or_costs(self):
        self.prepare()
        report = candidate_cli.inspect_final_preparation(self.output, strategy_ref=" breakout-1 ",
            minimum_closed_trades=1, minimum_active_days=1, minimum_net_realized_pnl_won=0,
            maximum_drawdown_ppm=10000, final_start=self.development_end.isoformat(),
            final_end=(self.development_end + timedelta(days=1)).isoformat())
        self.assertEqual("breakout-1", report["strategy_ref"])
        self.assertTrue(report["final_window"]["after_development_checked"])
        self.assertFalse(any(reason.startswith("TBD:") for reason in report["reasons"]))
        self.assertIn("FINAL_INPUT_AND_UNUSED_WINDOW_UNCHECKED", report["reasons"])
        self.assertIn("ACTUAL_BROKER_COSTS_UNVERIFIED", report["reasons"])
        self.assertEqual("REVIEW_REQUIRED", report["status"])
        self.assertFalse(report["orders_started"])

    def test_overlap_naive_or_reversed_window_is_rejected_before_source_load(self):
        self.prepare()
        for start, end in (("2024-10-04T00:00:00+09:00", "2024-10-06T00:00:00+09:00"),
                           (None, self.development_end.isoformat()),
                           ("2024-10-06T00:00:00+09:00", "2024-10-05T00:00:00+09:00"),
                           ("2024-10-06T00:00:00", None)):
            with (self.subTest(start=start, end=end),
                  patch.object(candidate_artifacts, "_inspect_daily_development_inputs", side_effect=AssertionError("no input")),
                  self.assertRaises(ValueError)):
                candidate_cli.inspect_final_preparation(self.output, final_start=start, final_end=end)

    def test_existing_criteria_validation_is_reused_before_source_load(self):
        self.prepare()
        for values in ({"minimum_active_days": True}, {"minimum_closed_trades": -1},
                       {"minimum_net_realized_pnl_won": 0.1}, {"maximum_drawdown_ppm": 1000001}):
            with (self.subTest(values=values),
                  patch.object(candidate_artifacts, "_inspect_daily_development_inputs", side_effect=AssertionError("no input")),
                  self.assertRaises(ValueError)):
                candidate_cli.inspect_final_preparation(self.output, **values)

    def test_cost_validity_and_unsupported_shadow_family_remain_reasons(self):
        self.prepare()
        report = candidate_cli.inspect_final_preparation(self.output, family="pullback",
            final_start="2028-01-01T00:00:00+09:00", final_end="2028-01-02T00:00:00+09:00")
        self.assertIn("COST_VALIDITY_OUTSIDE_FINAL_WINDOW", report["reasons"])
        self.assertIn("MOCK_SHADOW_FAMILY_NOT_SUPPORTED", report["reasons"])
        self.assertIn("현재 모의 Shadow는 돌파 전략만 지원", candidate_cli.final_preparation_text(report))

    def test_window_change_keeps_scientific_identity_but_is_never_a_batch_binding(self):
        self.prepare()
        first = candidate_cli.inspect_final_preparation(self.output, final_start="2024-10-06T00:00:00+09:00")
        second = candidate_cli.inspect_final_preparation(self.output, final_start="2024-10-07T00:00:00+09:00")
        self.assertEqual(first["candidate_spec_hash"], second["candidate_spec_hash"])
        self.assertNotEqual(first["final_window"], second["final_window"])
        self.assertFalse(second["final_window"]["after_development_checked"])
        self.assertIn("TBD:final_end", second["reasons"])
        self.assertNotIn("batch_id", second)

    def test_cancellation_and_tampered_source_do_not_produce_a_partial_preview(self):
        self.prepare()
        def cancelled():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            candidate_cli.inspect_final_preparation(self.output, checkpoint=cancelled)
        manifest = json.loads((self.source / "manifest.json").read_text())
        path = self.source / manifest["observations_file"]
        path.write_text(path.read_text() + "\n")
        with self.assertRaises(ValueError):
            candidate_cli.inspect_final_preparation(self.output)

    def test_cli_readonly_preparation_prints_json_without_output_file(self):
        import io
        from contextlib import redirect_stdout
        self.prepare()
        captured = io.StringIO()
        with redirect_stdout(captured):
            result = candidate_cli.main(["inspect-preparation", "--development-root", str(self.output)])
        self.assertEqual(0, result)
        self.assertEqual("mock_candidate_final_preparation/v1", json.loads(captured.getvalue())["version"])
        self.assertFalse((self.output / "research.sqlite3").exists())

    def test_fixed_request_checks_identity_without_reading_its_final_input(self):
        self.prepare()
        document = json.loads((self.output / "requests/daily-breakout.json").read_text())["request"]
        document["dataset"] = str(self.root / "unavailable-final-input")
        (self.root / "unavailable-final-input").mkdir()
        path = self.root / "fixed-request.json"
        path.write_text(json.dumps(document))
        with patch.object(rp, "load_research_input", side_effect=AssertionError("no final bytes")):
            report = candidate_cli.inspect_final_preparation(self.output, request_path=path)
        self.assertTrue(report["fixed_request"]["candidate_spec_matches"])
        self.assertFalse(report["fixed_request"]["source_and_period_admitted"])
        self.assertEqual([], list((self.root / "unavailable-final-input").iterdir()))

    def test_other_request_strategy_is_rejected_before_loading_development_input(self):
        self.prepare()
        document = json.loads((self.output / "requests/daily-breakout.json").read_text())["request"]
        document["strategy"]["buffer_bps"] += 1
        path = self.root / "different-request.json"
        path.write_text(json.dumps(document))
        with (patch.object(candidate_artifacts, "_inspect_daily_development_inputs", side_effect=AssertionError("no input")),
              self.assertRaisesRegex(ValueError, "설정이 다릅니다")):
            candidate_cli.inspect_final_preparation(self.output, request_path=path)


class FinalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinalPreparationTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.prepared = self.fixture.prepare()
        self.request = self.prepared.candidates[0]
        self.repo = ResearchRepository(self.request.database)
        self.owner = 'final-owner'

    def identity(self, request=None):
        request = request or self.request
        candidate_hash = rp.final_candidate_spec_hash(request, self.prepared.implementation_hash)
        run_id, spec = runner.research_run_identity(self.prepared.dataset, request.strategy, request.execution,
            request.evaluation, session_profile=request.session_profile,
            execution_scope='independent_final_holdout/v1')
        return candidate_hash, run_id, spec

    def claim(self, owner=None):
        candidate_hash, run_id, spec = self.identity()
        state = self.repo.claim_final_holdout_execution(self.prepared.batch,
            candidate_spec_hash=candidate_hash, run_id=run_id, spec=spec,
            input_manifest=self.prepared.dataset.manifest, owner_token=owner or self.owner)
        return state, candidate_hash, run_id, spec

    def execute(self, **kwargs):
        return rp.execute_final_holdout_evaluation(self.prepared, owner_token=self.owner, **kwargs)

    def test_section12_close_final_locks_calendar_and_preserves_overnight_paper_fills(self):
        self._verify_section12_final('close_betting/v1', 'listing_day')

    def test_ipo_final_candidates_bind_both_variants_and_listing_phases_without_order_authority(self):
        for family in ('ipo_close/v1', 'ipo_breakout/v1'):
            for phase in ('listing_day', 'after_listing_day'):
                with self.subTest(family=family, phase=phase):
                    self._verify_section12_final(family, phase)

    def _verify_section12_final(self, family, phase):
        from kiwoom_monitor.infrastructure.research_data_source import load_research_input, write_frozen_research_bundle

        root = self.fixture.root / (family.split('/')[0] + '-' + phase)
        root.mkdir()
        source = root / 'close-source'
        friday, monday = '2026-09-18', '2026-09-21'

        def stamp(day, clock):
            return f'{day}T{clock}:00+09:00'

        def bars(day, times_and_prices):
            rows = fixtures.rows_for(datetime.fromisoformat(day).date(), len(times_and_prices))
            rows[0].update(available_at=stamp(day, '08:58'), observation_key=stamp(day, '08:58'))
            for row, (clock, price) in zip(rows[1:], times_and_prices):
                start = stamp(day, clock)
                end = (datetime.fromisoformat(start) + timedelta(minutes=1)).isoformat()
                row.update(observation_key=start, available_at=end)
                row['payload'].update(bar_start=start, bar_end=end, open=price, high=price, low=price, close=price)
            return rows

        paths = (
            fixtures.child(source, datetime.fromisoformat(friday).date(), bars(friday, (
                ('15:14', 100), ('15:15', 100), ('15:16', 105), ('15:17', 105),
                ('15:18', 106), ('15:19', 106), ('15:29', 110),
            ))),
            fixtures.child(source, datetime.fromisoformat(monday).date(), bars(monday, (
                ('10:00', 103), ('10:01', 102), ('10:02', 89), ('10:03', 89),
            ))),
        )
        write_frozen_research_bundle(source, paths)
        manifest = load_research_input(source, session_profile=fixtures.PROFILE).manifest

        def session(day, start, end, name='KRX_REGULAR', phase='CONTINUOUS'):
            return {'starts_at': stamp(day, start), 'ends_at': stamp(day, end), 'session': name,
                    'phase': phase, 'support': 'SUPPORTED', 'schedule_version': 'fixture/v1'}

        calendar = [
            {'venue': 'KRX', 'trading_date': day, 'available_at': stamp(friday, '08:00'),
             'revision_id': day, 'evidence_ref': 'fixture-calendar', 'sessions': windows}
            for day, windows in (
                (friday, [session(friday, '09:00', '15:20'),
                          session(friday, '15:29', '15:30', 'KRX_CLOSING_AUCTION', 'AUCTION_ORDER_ENTRY')]),
                ('2026-09-19', []), ('2026-09-20', []), (monday, [session(monday, '10:00', '15:20')]),
            )
        ]
        calendar.append({**calendar[0], 'revision_id': 'after-close', 'available_at': stamp(friday, '16:00'),
                         'sessions': []})
        reference = root / 'close-calendar.json'
        listing = {'symbol': '005930', 'listing_date': friday if phase == 'listing_day' else '2026-09-17',
                   'listing_type': 'ipo', 'available_at': stamp(friday, '08:00'), 'source': 'fixture-listing',
                   'revision': 'listed', 'evidence_id': 'listing-before-entry', 'completeness': 'complete'}
        correction = {**listing, 'listing_type': 'spac', 'available_at': stamp(friday, '15:18'),
                      'revision': 'corrected', 'evidence_id': 'listing-correction-after-entry'}
        encoded = json.dumps({'version': 'strategy-reference/v1', 'calendar': calendar,
                              'listings': [] if family == 'close_betting/v1' else [listing, correction]}).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        reference.write_bytes(encoded)

        candidate_path, _, _ = self.candidate_cli_paths()
        document = json.loads(candidate_path.read_text(encoding='utf-8'))
        document.update(family=family, dataset=str(source), database=str(root / 'close.sqlite3'),
                        runs_dir=str(root / 'close-runs'), strategy_references={'path': str(reference), 'sha256': digest},
                        strategy={'capital_won': 1000, 'policy': {
                            'quantity': 4, 'entry_window_minutes': 10, 'lookback_bars': 2,
                            'minimum_close_return_bps': 200, 'adverse_gap_bps': 500, 'protection_loss_bps': 1000,
                            'exit_minutes_after_open': 8, 'ipo_reduction_quantity': None, 'listing_phases': [],
                        }})
        if family == 'ipo_close/v1':
            document['strategy']['policy'].update(ipo_reduction_quantity=2, listing_phases=[phase])
        elif family == 'ipo_breakout/v1':
            document['strategy']['policy'] = {'quantity': 4, 'lookback_bars': 2, 'breakout_buffer_bps': 100,
                'protection_loss_bps': 1000, 'exit_minutes_before_close': 1, 'listing_phases': [phase]}
        document['execution'].update(version='completed_close_then_next_open/v1', same_bar_path_version='no_intrabar_fill/v1')
        document['execution']['cost_model'].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
                                                  valid_from=stamp(friday, '08:00'), valid_to=stamp(monday, '16:00'))
        document['evaluation']['folds'][0].update(start=stamp(friday, '09:00'), end=stamp(monday, '16:00'))
        document['evaluation'].update(minimum_closed_trades=1, minimum_active_days=1)
        candidate = rp.research_process_request_from_document(document, root)
        implementation_hash = rp.research_implementation_hash(fixtures.PROFILE)
        candidate_hash = rp.final_candidate_spec_hash(candidate, implementation_hash)
        self.assertNotEqual(candidate_hash, rp.final_candidate_spec_hash(
            replace(candidate, strategy_references=(reference, 'a' * 64)), implementation_hash))
        self.assertEqual(candidate_hash, rp.final_candidate_spec_hash(
            replace(candidate, strategy_references=(root / 'copied.json', digest)), implementation_hash))
        batch = rp.FinalHoldoutBatchSpec('final_holdout_batch/v1', manifest['dataset_id'], manifest['revision_ids_hash'],
                                        fixtures.PROFILE, (candidate_hash,), candidate.evaluation)
        request = rp.FinalHoldoutExecutionRequest(batch, (candidate,), 'close-access', stamp(monday, '17:00'), self.owner)
        request_path = root / 'close-final.json'
        request_path.write_text(json.dumps(request.to_dict()), encoding='utf-8')
        parsed = rp.load_final_holdout_execution_request(request_path)
        self.assertEqual(request.to_dict(), parsed.to_dict())
        reference.write_bytes(encoded + b' ')
        with (patch.object(rp, 'load_research_input', side_effect=AssertionError('no OOS bytes')),
              self.assertRaisesRegex(ValueError, 'reference.*hash')):
            rp.execute_final_holdout_request(parsed)
        self.assertFalse(candidate.database.exists())
        reference.write_bytes(encoded)
        result = rp.execute_final_holdout_request(parsed)['candidates'][0]
        self.assertEqual('COMPLETED', result['state'], result['reason'])
        repo = ResearchRepository(candidate.database)
        self.assertEqual(digest, repo.load_run(result['run_id'])['spec']['strategy_references']['sha256'])
        fills = [row for row in repo.load_execution_events(result['run_id']) if row['event_type'] == 'FILL']
        expected = {'close_betting/v1': [('BUY', 4, 105), ('SELL', 4, 102)],
                    'ipo_close/v1': [('BUY', 4, 105), ('SELL', 2, 102), ('SELL', 2, 89)],
                    'ipo_breakout/v1': [('BUY', 4, 105), ('SELL', 4, 106)]}[family]
        self.assertEqual(expected, [(row['side'], row['quantity'], row['price']) for row in fills])
        self.assertEqual([friday, friday] if family == 'ipo_breakout/v1' else
                         [friday] + [monday] * (len(expected) - 1), [row['occurred_at'][:10] for row in fills])
        if family != 'close_betting/v1':
            with closing(sqlite3.connect(candidate.database)) as connection:
                snapshots = {identity: json.loads(value)['feature_values'][0]['setup']
                    for identity, value in connection.execute(
                        'SELECT snapshot_id,document_json FROM research_feature_snapshots WHERE run_id=?',
                        (result['run_id'],))}
            entry, = repo.load_candidate_events(result['run_id'])
            original_setup = snapshots[entry['snapshot_id']]
            self.assertEqual((phase, ['listing-before-entry']),
                             (original_setup['listing_phase'], original_setup['listing_refs']))
            managed = [decision for decision in repo.load_evaluations(result['run_id'])
                       if decision['final_action'] in {'HOLD', 'REDUCE', 'EXIT'}]
            self.assertTrue(managed)
            self.assertTrue(all(snapshots[decision['snapshot_id']] == original_setup for decision in managed))
        repeated = rp.execute_final_holdout_request(parsed)['candidates'][0]
        self.assertEqual(('CACHED', result['run_id'], result['logical_result_hash']),
                         (repeated['state'], repeated['run_id'], repeated['logical_result_hash']))
        self.assertEqual(1, len(repo.load_final_holdout_executions(batch.batch_id)))
        policy = MockAutomationEligibilityPolicy('close-test', candidate_hash,
            datetime.fromisoformat(stamp(friday, '08:00')), 1, 1, -100, 1_000_000)
        self._verify_close_final_publication(root, repo, candidate, implementation_hash, batch, result, policy)

    def _verify_close_final_publication(self, root, repo, candidate, implementation_hash, batch, result, policy):
        from copy import deepcopy
        from dataclasses import asdict
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kiwoom_monitor.application.market_session_schedule import session_window_at
        from kiwoom_monitor.application.mock_automation_specification import (
            build_mock_automation_publication_documents, matching_shadow_events,
        )
        from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.mock_publication_routes import create_mock_publication_router
        from kiwoom_monitor.domain.strategy_contract import MINUTE_SHADOW_VERSION
        from tests.unit.test_close_and_ipo import CloseAndIpoTests
        from tests.unit.test_mock_automation_specification import ACCOUNT_REF, PROFILE_ID, mock_specification_inputs

        documents = rp.prepare_mock_automation_candidate_publication(repo, candidate,
            implementation_hash=implementation_hash, strategy_ref=policy.strategy_ref,
            account_ref=ACCOUNT_REF, final_batch_id=batch.batch_id, final_run_id=result['run_id'],
            eligibility_policy=policy)
        self.assertEqual('ELIGIBLE', documents['eligibility_receipt']['status'], documents['package']['evaluation_evidence'])
        self.assertEqual(result['logical_result_hash'], documents['package']['source_final']['result_hash'])
        wrong = deepcopy(repo.load_run(result['run_id']))
        wrong['spec']['strategy_references']['sha256'] = 'b' * 64
        with patch.object(repo, 'load_run', return_value=wrong), self.assertRaisesRegex(ValueError, 'source run differs'):
            rp.prepare_mock_automation_candidate_publication(repo, candidate,
                implementation_hash=implementation_hash, strategy_ref=policy.strategy_ref,
                account_ref=ACCOUNT_REF, final_batch_id=batch.batch_id, final_run_id=result['run_id'],
                eligibility_policy=policy)

        fixture = CloseAndIpoTests()
        fixture.setUp()
        if candidate.family != 'close_betting/v1':
            phase, = candidate.strategy.policy.listing_phases
            fixture.listing = replace(fixture.listing, listing_date=fixture.friday if phase.value == 'listing_day'
                                      else fixture.friday - timedelta(days=1))
        cut = datetime.fromisoformat(fixture.signal.available_at)
        entry = replace(fixture.entry, schedule_version=session_window_at(cut, venue='KRX').schedule_version)
        calendar = (replace(fixture.calendar[0], sessions=(entry, fixture.auction)), *fixture.calendar[1:])
        config = {
            'version': MINUTE_SHADOW_VERSION, 'family': candidate.family, 'signal_valid_seconds': 30,
            'symbols': [fixture.symbol], 'strategy': candidate.strategy.to_dict(),
            'references': {'version': 'strategy-reference/v1', 'calendar': [asdict(day) for day in calendar],
                           'listings': [] if candidate.family == 'close_betting/v1' else [asdict(fixture.listing)]},
        }
        factory = getattr(self, '_native_final_publication_factory',
                          lambda path, now: SQLiteQueryStore(path, now_provider=now))
        with closing(factory(root / 'close-publication.sqlite3', lambda: cut)) as store:
            store.initialize()
            inputs = mock_specification_inputs(store, publish_candidate=False)
            app = FastAPI()
            app.include_router(create_mock_publication_router(store, inputs.repository, lambda: None,
                expected_implementation_hash=lambda _profile: implementation_hash))
            body = {'account_ref': ACCOUNT_REF, 'credential_profile_id': PROFILE_ID,
                    'expected_binding_revision': inputs.binding['binding_revision'], **documents}
            client = TestClient(app)
            response = client.post('/api/v1/research/mock-automation-candidates', json=body)
            self.assertEqual((200, 'saved', False),
                (response.status_code, response.json().get('status'), response.json().get('orders_started')))
            repeated = client.post('/api/v1/research/mock-automation-candidates', json=body)
            self.assertEqual('unchanged', repeated.json()['status'])
            publication, = client.get(f'/api/v1/research/mock-automation-candidates/{ACCOUNT_REF}',
                params={'credential_profile_id': PROFILE_ID}).json()['candidates']
            self.assertEqual(documents, publication)
            monitor = CandidateMonitor.from_json(store, json.dumps(config, default=lambda value: value.isoformat()),
                poll_seconds=2, universe_max_age_seconds=180, now_provider=lambda: cut)
            monitor.run_once()
            for index, frame in enumerate((*fixture.prior, fixture.signal)):
                start = datetime.fromisoformat(frame.bar_start)
                key = {'trading_date': start.date().isoformat(), 'minute': start.strftime('%H:%M'),
                       'code': fixture.symbol, 'market': 'KRX'}
                store.save_minute_bars([{**key, 'open': frame.open, 'high': frame.high, 'low': frame.low,
                    'close': frame.close, 'volume': frame.volume, 'trade_value_million_won': frame.trade_value_million_won,
                    'updated_at': start.timestamp(), 'operation_id': f'write-{index}'}])
                store.finalize_minute_bars([{**key, 'available_at': datetime.fromisoformat(frame.available_at).timestamp(),
                    'capture_quality': 'complete', 'finalization_source': 'timer', 'operation_id': f'finalize-{index}'}])
            monitor.run_once()
            event, = store.load_shadow_candidates()['events']
            matched = matching_shadow_events(publication, (event,))
            self.assertEqual((event,), matched)
            self.assertNotEqual(candidate.strategy_references[1],
                hashlib.sha256(json.dumps(config['references'], default=lambda value: value.isoformat()).encode()).hexdigest())
            for changed in (
                {**event, 'quantity': True}, {**event, 'shadow_scope': 'historical-replay'},
                {**event, 'close_ipo_entry_setup': {**event['close_ipo_entry_setup'], 'policy': asdict(fixture.close)}},
                {**event, 'close_ipo_entry_setup': {**event['close_ipo_entry_setup'], 'next_open_at': event['available_at']}},
            ):
                with self.subTest(changed=changed):
                    self.assertEqual((), matching_shadow_events(publication, (changed,)))
            if candidate.family != 'close_betting/v1':
                for key, value in (('listing_refs', ['unknown-listing']), ('listing_phase', 'unknown')):
                    changed = {**event, 'close_ipo_entry_setup': {**event['close_ipo_entry_setup'], key: value}}
                    self.assertEqual((), matching_shadow_events(publication, (changed,)))
            with self.assertRaisesRegex(ValueError, 'explicit CLOSE execution policy' if candidate.family == 'close_betting/v1'
                                        else 'IPO READY publication requires its native'):
                build_mock_automation_publication_documents(candidate_publication=publication,
                    binding=inputs.binding, credential_profile_id=PROFILE_ID, shadow_event=event,
                    evaluation_start=inputs.profile.evaluation_start, evaluation_end=inputs.profile.evaluation_end,
                    frozen_at=inputs.spec.frozen_at, criteria=inputs.profile.criteria, limits={})
            self.assertEqual((), inputs.repository.load_stage_revisions(policy.strategy_ref))
            if candidate.family != 'close_betting/v1':
                from kiwoom_monitor.application.mock_automation_specification import publish_ready_mock_automation_spec
                from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE, CloseMockExecutionPolicy

                attempted = replace(inputs.spec, strategy_ref=policy.strategy_ref,
                    candidate_package_hash=documents['package']['package_hash'], execution_plan_mode=CLOSE_NATIVE_PLAN_MODE,
                    close_execution_policy=CloseMockExecutionPolicy(100, 200, 'acknowledged_0B_bounded_by_signal/v1',
                        'cancel_at_entry_deadline/v1', 'acknowledged_0B_bounded_slippage/v1'))
                with self.assertRaisesRegex(ValueError, 'IPO READY publication requires its native'):
                    publish_ready_mock_automation_spec(inputs.repository, spec=attempted,
                        profile=inputs.profile, stage_revisions=inputs.stages, shadow_event=event,
                        current_binding=inputs.binding)
                self.assertIsNone(inputs.repository.load_mock_automation_spec(ACCOUNT_REF, attempted.spec_id))
            if isinstance(store, SQLiteQueryStore):
                with store._connection() as connection:
                    self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM central_execution_intents').fetchone()[0])
            else:
                with store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM central_execution_intents')
                    self.assertEqual(0, cursor.fetchone()[0])

    def candidate_cli_paths(self):
        root = self.fixture.root
        document = json.loads((root / 'request.json').read_text(encoding='utf-8'))
        document.pop('development_partition', None)
        document.update(
            family=self.request.family, strategy=self.request.strategy.to_dict(),
            dataset=str(self.request.dataset), database=str(self.request.database),
            runs_dir=str(self.request.runs_dir), evaluation=self.request.evaluation.to_dict(),
            execution=self.request.execution.to_dict(), session_profile=self.request.session_profile,
        )
        request_path = root / 'candidate-request.json'
        request_path.write_text(json.dumps(document), encoding='utf-8')
        return request_path, root / 'candidate-policy.json', root / 'candidate-publication.json'

    def test_candidate_cli_freezes_tbd_without_loading_input_or_creating_runs(self):
        request_path, policy_path, _ = self.candidate_cli_paths()
        with (patch.object(rp, 'load_research_input', side_effect=AssertionError('no OOS input read')),
              patch.object(candidate_artifacts, 'ResearchRepository', side_effect=AssertionError('no database access'))):
            policy = candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        self.assertEqual(4, len(policy.tbd_fields))
        self.assertEqual(rp.final_candidate_spec_hash(self.request, self.prepared.implementation_hash), policy.candidate_spec_hash)
        before = policy_path.read_bytes()
        with patch.object(candidate_artifacts, 'kst_now', side_effect=AssertionError('must reuse original freeze time')) as clock:
            repeated = candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1', now_provider=clock)
        clock.assert_not_called()
        self.assertEqual(policy.policy_id, repeated.policy_id)
        self.assertEqual(before, policy_path.read_bytes())
        self.assertFalse(self.request.runs_dir.exists())

    def test_candidate_cli_refuses_replacing_frozen_thresholds_or_strategy(self):
        request_path, policy_path, _ = self.candidate_cli_paths()
        candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        before = policy_path.read_bytes()
        with self.assertRaisesRegex(ValueError, '기존 동결'):
            candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1', minimum_closed_trades=1)
        document = json.loads(request_path.read_text(encoding='utf-8'))
        document['strategy']['buffer_bps'] += 1
        request_path.write_text(json.dumps(document), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '기존 동결'):
            candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        self.assertEqual(before, policy_path.read_bytes())

    def test_candidate_cli_completed_final_exports_blocked_evidence_readonly(self):
        request_path, policy_path, output = self.candidate_cli_paths()
        policy = candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        result = self.execute()
        row = result['candidates'][0]
        before = hashlib.sha256(self.request.database.read_bytes()).hexdigest()
        with patch.object(rp, 'load_research_input', side_effect=AssertionError('no OOS reread')):
            documents = candidate_artifacts.prepare_publication(
                request_path, policy_path, output, account_ref='account-ref-1',
                final_batch_id=self.prepared.batch.batch_id, final_run_id=row['run_id'],
            )
        self.assertEqual(documents, json.loads(output.read_text(encoding='utf-8')))
        self.assertEqual(before, hashlib.sha256(self.request.database.read_bytes()).hexdigest())
        self.assertEqual(row['logical_result_hash'], documents['package']['source_final']['result_hash'])
        self.assertEqual('BLOCKED', documents['eligibility_receipt']['status'])
        self.assertTrue(set('TBD:' + name for name in policy.tbd_fields).issubset(documents['eligibility_receipt']['reasons']))
        self.assertNotIn('POLICY_FROZEN_AFTER_FINAL_STARTED', documents['eligibility_receipt']['reasons'])

    def test_candidate_cli_cannot_promote_development_without_final_ledger(self):
        request_path, policy_path, output = self.candidate_cli_paths()
        candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        with self.assertRaisesRegex(ValueError, 'final holdout ledger'):
            candidate_artifacts.prepare_publication(
                request_path, policy_path, output, account_ref='account-ref-1',
                final_batch_id=self.prepared.batch.batch_id, final_run_id='development-run',
            )
        self.assertFalse(output.exists())
        self.assertEqual((), self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))

    def test_candidate_cli_late_policy_is_not_backdated(self):
        result = self.execute()
        row = result['candidates'][0]
        run = self.repo.load_run(row['run_id'])
        request_path, policy_path, output = self.candidate_cli_paths()
        later = datetime.fromisoformat(run['started_at']) + timedelta(days=1)
        policy = candidate_artifacts.freeze_policy(
            request_path, policy_path, strategy_ref='strategy-1', now_provider=lambda: later,
            minimum_closed_trades=0, minimum_active_days=0,
            minimum_net_realized_pnl_won=0, maximum_drawdown_ppm=1_000_000,
        )
        documents = candidate_artifacts.prepare_publication(
            request_path, policy_path, output, account_ref='account-ref-1',
            final_batch_id=self.prepared.batch.batch_id, final_run_id=row['run_id'],
        )
        self.assertEqual(later, policy.frozen_at)
        self.assertEqual('BLOCKED', documents['eligibility_receipt']['status'])
        self.assertIn('POLICY_FROZEN_AFTER_FINAL_STARTED', documents['eligibility_receipt']['reasons'])

    def test_candidate_cli_changed_code_is_rejected_before_reading_final(self):
        request_path, policy_path, output = self.candidate_cli_paths()
        candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        with (patch.object(candidate_artifacts, 'research_implementation_hash', return_value='a' * 64),
              patch.object(candidate_artifacts, 'ResearchRepository', side_effect=AssertionError('must fail before reads'))):
            with self.assertRaisesRegex(ValueError, '현재 전략'):
                candidate_artifacts.prepare_publication(request_path, policy_path, output,
                    account_ref='account-ref-1', final_batch_id='batch', final_run_id='run')
        self.assertFalse(output.exists())

    def test_candidate_cli_output_cannot_modify_frozen_input(self):
        request_path, _, _ = self.candidate_cli_paths()
        target = self.request.dataset / 'policy.json'
        with self.assertRaisesRegex(ValueError, '동결 연구 입력'):
            candidate_artifacts.freeze_policy(request_path, target, strategy_ref='strategy-1')
        self.assertFalse(target.exists())

    def test_candidate_cli_atomic_publication_has_one_winner_and_no_partial_file(self):
        output = self.fixture.root / 'publication.json'
        barrier = Barrier(2)
        def write(value):
            barrier.wait(timeout=5)
            try:
                candidate_artifacts._write_immutable(output, {'value': value})
                return 'written'
            except ValueError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(['conflict', 'written'], sorted(executor.map(write, (1, 2))))
        self.assertIn(json.loads(output.read_text(encoding='utf-8'))['value'], (1, 2))
        self.assertEqual([], list(output.parent.glob(f'.{output.name}.*.tmp')))
        before = output.read_bytes()
        with self.assertRaisesRegex(ValueError, '동결 파일'):
            candidate_artifacts._write_immutable(output, {'value': 3})
        self.assertEqual(before, output.read_bytes())

    def test_candidate_cli_failed_publication_leaves_no_partial_artifact(self):
        output = self.fixture.root / 'publication.json'
        with patch.object(candidate_artifacts.os, 'link', side_effect=PermissionError('fixture refusal')):
            with self.assertRaises(PermissionError):
                candidate_artifacts._write_immutable(output, {'value': 1})
        self.assertFalse(output.exists())
        self.assertEqual([], list(output.parent.glob(f'.{output.name}.*.tmp')))

    def test_candidate_cli_preserves_nonconflict_filesystem_file_exists_errors(self):
        parent = self.fixture.root / 'regular-parent'
        parent.write_bytes(b'original-parent')
        with self.assertRaises(FileExistsError) as raised:
            candidate_artifacts._write_immutable(parent / 'output.json', {'value': 1})
        self.assertIs(type(raised.exception), FileExistsError)
        self.assertEqual(b'original-parent', parent.read_bytes())
        output = self.fixture.root / 'publication.json'
        original = FileExistsError('fixture temporary creation failed')
        with patch.object(candidate_artifacts.tempfile, 'NamedTemporaryFile', side_effect=original):
            with self.assertRaises(FileExistsError) as raised:
                candidate_artifacts._write_immutable(output, {'value': 1})
        self.assertIs(original, raised.exception)
        self.assertFalse(output.exists())
        output.write_bytes(b'original-output')
        original = FileExistsError('fixture original read failed')
        with patch.object(Path, 'read_bytes', side_effect=original):
            with self.assertRaises(FileExistsError) as raised:
                candidate_artifacts._write_immutable(output, {'value': 2})
        self.assertIs(original, raised.exception)
        self.assertEqual(b'original-output', output.read_bytes())

    def test_candidate_cli_cleanup_oracle_detects_actual_retained_temporary(self):
        output = self.fixture.root / 'publication.json'
        unlink = Path.unlink
        def retain(path, *args, **kwargs):
            if path.name.startswith(f'.{output.name}.') and path.suffix == '.tmp':
                return None
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', retain):
            candidate_artifacts._write_immutable(output, {'value': 1})
        retained = list(output.parent.glob(f'.{output.name}.*.tmp'))
        self.assertEqual(1, len(retained))
        self.assertEqual(output.read_bytes(), retained[0].read_bytes())
        retained[0].unlink()
        self.assertEqual([], list(output.parent.glob(f'.{output.name}.*.tmp')))

    def test_candidate_cli_main_reports_tbd_and_error_without_promoting_state(self):
        from contextlib import redirect_stdout
        import io
        request_path, policy_path, output = self.candidate_cli_paths()
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = candidate_cli.main(['freeze-policy', '--request', str(request_path),
                '--output', str(policy_path), '--strategy-ref', 'strategy-1'])
        self.assertEqual(0, result)
        self.assertEqual('BLOCKED', json.loads(stream.getvalue())['status'])
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = candidate_cli.main(['prepare-publication', '--request', str(request_path),
                '--policy', str(policy_path), '--output', str(output), '--account-ref', 'account-ref-1',
                '--final-batch-id', self.prepared.batch.batch_id, '--final-run-id', 'no-final'])
        self.assertEqual(1, result)
        self.assertEqual('ERROR', json.loads(stream.getvalue())['status'])
        self.assertFalse(output.exists())

    def test_real_final_execution_uses_fresh_engine_and_completes_owned_candidate(self):
        initial = []
        original = runner.PaperExecutionEngine
        class RecordingEngine(original):
            def __init__(engine, *args, **kwargs):
                super().__init__(*args, **kwargs)
                initial.append((engine.portfolio.cash_won, engine.strategy_state.status))
        with patch.object(runner, 'PaperExecutionEngine', RecordingEngine):
            result = self.execute()
        row = result['candidates'][0]
        self.assertEqual(('COMPLETED','COMPLETED'), (result['batch_status'], row['state']))
        self.assertEqual([(self.request.execution.initial_cash_won, 'flat')], initial)
        self.assertEqual('completed', self.repo.load_run(row['run_id'])['status'])
        execution = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        self.assertEqual(('COMPLETED',row['logical_result_hash']),
                         (execution['state'],execution['logical_result_hash']))
        report = self.repo.load_research_report(row['run_id'])
        self.assertEqual(('OOS',1), (report['fold_reports'][0]['role'],len(report['fold_reports'])))
        output = json.loads((self.request.runs_dir/row['run_id']/'manifest.json').read_text(encoding='utf-8'))
        self.assertIn('final_holdout_partition', output); self.assertNotIn('development_partition', output)
        self.assertIn('no_cross_candidate_selection', result['limitations'])

    def test_completed_final_evidence_builds_bounded_candidate_publication(self):
        result = self.execute()
        row = result['candidates'][0]
        run = self.repo.load_run(row['run_id'])
        candidate_hash = rp.final_candidate_spec_hash(
            self.request, self.prepared.implementation_hash,
        )
        policy = MockAutomationEligibilityPolicy(
            strategy_ref='strategy-final-1', candidate_spec_hash=candidate_hash,
            frozen_at=datetime.fromisoformat(run['started_at']) - timedelta(seconds=1),
            minimum_closed_trades=1_000_000, minimum_active_days=1,
            minimum_net_realized_pnl_won=0, maximum_drawdown_ppm=100_000,
        )
        publication = rp.prepare_mock_automation_candidate_publication(
            self.repo, self.request, implementation_hash=self.prepared.implementation_hash,
            strategy_ref='strategy-final-1', account_ref='account-ref-1',
            final_batch_id=self.prepared.batch.batch_id, final_run_id=row['run_id'],
            eligibility_policy=policy,
        )
        self.assertEqual(candidate_hash, publication['package']['candidate_spec_hash'])
        self.assertEqual(row['logical_result_hash'], publication['package']['source_final']['result_hash'])
        self.assertEqual(
            CandidateEligibilityStatus.BLOCKED.value,
            publication['eligibility_receipt']['status'],
        )

    def test_completed_candidate_is_cached_only_after_report_and_manifest_validation(self):
        first = self.execute(); run_id = first['candidates'][0]['run_id']
        with patch.object(rp, 'execute_research', side_effect=AssertionError('must not execute')):
            second = self.execute()
        self.assertEqual(('COMPLETED','CACHED'), (second['batch_status'],second['candidates'][0]['state']))
        (self.request.runs_dir/run_id/'manifest.json').unlink()
        with patch.object(rp, 'execute_research', side_effect=AssertionError('immutable completion cannot rerun')):
            invalid = self.execute()
        self.assertEqual(('PARTIAL','CACHE_INVALID'), (invalid['batch_status'],invalid['candidates'][0]['state']))

    def test_final_scope_without_repository_claim_is_rejected_before_engine(self):
        candidate_hash, _, _ = self.identity()
        with patch.object(runner, 'PaperExecutionEngine', side_effect=AssertionError('no engine')):
            with self.assertRaisesRegex(ValueError, 'repository-owned claim'):
                runner.execute_research(self.prepared.dataset, self.repo, self.request.runs_dir,
                    self.request.strategy, self.request.execution, self.request.evaluation,
                    session_profile=self.request.session_profile,
                    execution_scope='independent_final_holdout/v1',
                    expected_code_hash=self.prepared.implementation_hash)
            with self.assertRaisesRegex(ValueError, 'not owned'):
                runner.execute_research(self.prepared.dataset, self.repo, self.request.runs_dir,
                    self.request.strategy, self.request.execution, self.request.evaluation,
                    session_profile=self.request.session_profile,
                    execution_scope='independent_final_holdout/v1',
                    expected_code_hash=self.prepared.implementation_hash,
                    final_execution={'batch_id':self.prepared.batch.batch_id,
                                     'candidate_spec_hash':candidate_hash,'owner_token':self.owner})

    def test_atomic_claim_has_one_owner_and_running_candidate_is_not_retried(self):
        candidate_hash, run_id, spec = self.identity(); barrier = Barrier(2)
        def claim(owner):
            repository = ResearchRepository(self.request.database); barrier.wait(timeout=5)
            return repository.claim_final_holdout_execution(self.prepared.batch,
                candidate_spec_hash=candidate_hash, run_id=run_id, spec=spec,
                input_manifest=self.prepared.dataset.manifest, owner_token=owner)
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(['busy','claimed'], sorted(executor.map(claim, ('one','two'))))
        with patch.object(rp, 'execute_research', side_effect=AssertionError('busy cannot run')):
            result = self.execute()
        self.assertEqual(('PARTIAL','BUSY'), (result['batch_status'],result['candidates'][0]['state']))

    def test_claim_rejects_unlocked_candidate_identity_or_unowned_existing_run(self):
        candidate_hash, run_id, spec = self.identity()
        for changed in ('candidate','run','scope','manifest'):
            kwargs = dict(candidate_spec_hash=candidate_hash,run_id=run_id,spec=spec,
                          input_manifest=self.prepared.dataset.manifest,owner_token=self.owner)
            if changed == 'candidate': kwargs['candidate_spec_hash'] = 'f'*64
            elif changed == 'run': kwargs['run_id'] = 'run_'+'f'*64
            elif changed == 'scope': kwargs['spec'] = {**spec,'execution_scope':'other'}
            else: kwargs['input_manifest'] = {**self.prepared.dataset.manifest,'runtime_input_version':'other'}
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.repo.claim_final_holdout_execution(self.prepared.batch, **kwargs)
        self.repo.start_run(run_id, spec, self.prepared.dataset.manifest)
        with self.assertRaisesRegex(ValueError, 'without final ownership'): self.claim()

    def test_exposed_window_cannot_claim(self):
        self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='expose-final',
            exposed_at=self.fixture.at, reason='used to change development strategy')
        with self.assertRaisesRegex(ValueError, 'cannot execute'): self.claim()

    def test_running_candidate_blocks_development_exposure_until_terminal(self):
        _, candidate_hash, run_id, _ = self.claim()
        with self.assertRaisesRegex(ValueError, 'running final execution'):
            self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='too-early',
                exposed_at=self.fixture.at, reason='premature development use')
        self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
            run_id=run_id,owner_token=self.owner,outcome='CANCELLED',reason='fixture cancellation')
        self.assertTrue(self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='after-terminal',
            exposed_at=self.fixture.at, reason='reviewed terminal final evidence'))

    def test_execution_failure_is_terminal_and_never_automatically_retried(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('engine fixture')) as execute:
            first = self.execute()
        self.assertEqual(('FAILED','failed'), (first['candidates'][0]['state'],
                         self.repo.load_run(first['candidates'][0]['run_id'])['status']))
        with patch.object(rp, 'execute_research', side_effect=AssertionError('terminal failure cannot retry')) as retry:
            second = self.execute()
        self.assertEqual(('PARTIAL','FAILED'), (second['batch_status'],second['candidates'][0]['state']))
        self.assertEqual((1,0), (execute.call_count,retry.call_count))

    def test_cancellation_is_terminal_and_leaves_later_candidates_not_started(self):
        separate = fixtures.FinalPreparationTests(); separate.setUp(); self.addCleanup(separate.doCleanups)
        request = separate.request
        other = replace(request, strategy=replace(request.strategy,
                        buffer_bps=request.strategy.buffer_bps+1))
        batch = separate.make_batch((request,other))
        prepared = rp.prepare_final_holdout_evaluation(batch, (request,other),
            request_id='two-candidates', accessed_at=separate.at)
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 2
        result = rp.execute_final_holdout_evaluation(prepared, owner_token=self.owner,
                                                     cancel_requested=cancelled)
        self.assertEqual(['CANCELLED','NOT_STARTED'], [row['state'] for row in result['candidates']])
        repo = ResearchRepository(request.database)
        self.assertEqual('cancelled', repo.load_run(result['candidates'][0]['run_id'])['status'])
        self.assertEqual(1,len(repo.load_final_holdout_executions(batch.batch_id)))

    def test_two_fixed_candidates_each_receive_fresh_engine_without_combined_selection(self):
        separate = fixtures.FinalPreparationTests(); separate.setUp(); self.addCleanup(separate.doCleanups)
        request = separate.request
        other = replace(request, strategy=replace(request.strategy,
                        buffer_bps=request.strategy.buffer_bps+1))
        batch = separate.make_batch((request,other))
        prepared = rp.prepare_final_holdout_evaluation(batch,(request,other),
            request_id='two-complete',accessed_at=separate.at)
        initial = []
        original = runner.PaperExecutionEngine
        class RecordingEngine(original):
            def __init__(engine,*args,**kwargs):
                super().__init__(*args,**kwargs)
                initial.append((engine.portfolio.cash_won,engine.strategy_state.status))
        progress = []
        with patch.object(runner,'PaperExecutionEngine',RecordingEngine):
            result = rp.execute_final_holdout_evaluation(prepared,owner_token=self.owner,
                progress_callback=lambda snapshot: progress.append(snapshot))
        self.assertEqual(('COMPLETED',['COMPLETED','COMPLETED']),
                         (result['batch_status'],[row['state'] for row in result['candidates']]))
        self.assertEqual([(request.execution.initial_cash_won,'flat')]*2,initial)
        self.assertEqual(batch.candidate_spec_hashes,tuple(row['candidate_spec_hash'] for row in result['candidates']))
        self.assertNotIn('comparison',result)
        self.assertEqual([
            ['NOT_STARTED', 'NOT_STARTED'], ['RUNNING', 'NOT_STARTED'],
            ['COMPLETED', 'NOT_STARTED'], ['COMPLETED', 'RUNNING'],
            ['COMPLETED', 'COMPLETED'],
        ], [[row['state'] for row in snapshot['candidates']] for snapshot in progress])

    def test_cancel_before_first_claim_creates_no_execution_or_run(self):
        result = self.execute(cancel_requested=lambda: True)
        self.assertEqual(('PARTIAL','CANCELLED'), (result['batch_status'],result['candidates'][0]['state']))
        self.assertEqual((),self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))
        with closing(sqlite3.connect(self.request.database)) as connection:
            self.assertEqual(0,connection.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0])

    def test_invalid_owner_paths_or_mutated_input_are_rejected_before_claim(self):
        for owner in ('',True,'x'*257,'bad\x00owner'):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                rp.execute_final_holdout_evaluation(self.prepared,owner_token=owner,
                                                    cancel_requested=lambda: True)
        changed_request = replace(self.request,runs_dir=self.fixture.root/'different-runs')
        changed = replace(self.prepared,candidates=(changed_request,))
        with self.assertRaisesRegex(ValueError,'shared absolute storage paths'):
            rp.execute_final_holdout_evaluation(changed,owner_token=self.owner)
        malformed = replace(self.prepared,dataset=FrozenResearchDataset(
            {**self.prepared.dataset.manifest,'runtime_input_version':'other'},self.prepared.dataset.observations))
        with self.assertRaisesRegex(ValueError,'isolated final input'):
            rp.execute_final_holdout_evaluation(malformed,owner_token=self.owner)
        self.assertEqual((),self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))

    def test_owner_and_terminal_completion_are_fenced_and_immutable(self):
        state, candidate_hash, run_id, _ = self.claim(); self.assertEqual('claimed',state)
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.repo.assert_final_holdout_execution_owner(run_id,batch_id=self.prepared.batch.batch_id,
                candidate_spec_hash=candidate_hash,owner_token='wrong')
        self.repo.cancel_run(run_id,'cancelled fixture')
        self.assertTrue(self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
            run_id=run_id,owner_token=self.owner,outcome='CANCELLED',reason='cancelled fixture'))
        self.assertFalse(self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
            run_id=run_id,owner_token=self.owner,outcome='CANCELLED',reason='cancelled fixture'))
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
                run_id=run_id,owner_token=self.owner,outcome='FAILED',reason='changed')
        self.assertEqual('cancelled',self.claim()[0])

    def test_same_batch_can_be_prepared_again_but_terminal_candidate_cannot_run_again(self):
        first = self.execute()
        again = self.fixture.prepare()
        with patch.object(rp, 'execute_research', side_effect=AssertionError('no rerun')):
            second = rp.execute_final_holdout_evaluation(again,owner_token='new-owner')
        self.assertEqual(('COMPLETED','CACHED'), (second['batch_status'],second['candidates'][0]['state']))
        self.assertEqual(first['candidates'][0]['run_id'],second['candidates'][0]['run_id'])

    def test_output_publication_failure_is_durable_failed_not_completed(self):
        with patch.object(runner, '_write_immutable_json', side_effect=OSError('publish fixture')):
            result = self.execute()
        row = result['candidates'][0]
        self.assertEqual(('FAILED','failed'), (row['state'],self.repo.load_run(row['run_id'])['status']))
        self.assertEqual('FAILED',self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]['state'])

    def test_failed_candidate_requires_explicit_audited_recovery_and_reuses_identity(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('recoverable fixture')):
            failed = self.execute()
        candidate_hash, run_id, _ = self.identity()
        recovered = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner',
            recoveries={candidate_hash: {'request_id': 'recover-failed-1',
                                         'reason': 'reviewed deterministic fixture failure'}})
        self.assertEqual(('FAILED', 'COMPLETED'),
                         (failed['candidates'][0]['state'], recovered['candidates'][0]['state']))
        self.assertEqual(run_id, recovered['candidates'][0]['run_id'])
        execution = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        recovery = self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id)[0]
        self.assertEqual(('COMPLETED', 2, 'recovery-owner'),
                         (execution['state'], execution['generation'], execution['owner_token']))
        self.assertEqual(('CLAIMED', 2, 'recover-failed-1'),
                         (recovery['state'], recovery['generation'], recovery['request_id']))

    def test_cancelled_candidate_can_be_explicitly_recovered(self):
        with patch.object(rp, 'execute_research', side_effect=runner.ResearchRunCancelled('fixture cancel')):
            cancelled = self.execute()
        candidate_hash, _, _ = self.identity()
        recovered = rp.execute_final_holdout_evaluation(self.prepared, owner_token='cancel-recovery-owner',
            recoveries={candidate_hash: {'request_id': 'recover-cancelled-1',
                                         'reason': 'operator reviewed cancellation'}})
        self.assertEqual(('CANCELLED', 'COMPLETED'),
                         (cancelled['candidates'][0]['state'], recovered['candidates'][0]['state']))

    def test_output_publication_failure_can_recover_existing_immutable_rows(self):
        with patch.object(runner, '_write_immutable_json', side_effect=OSError('publish fixture')):
            failed = self.execute()
        candidate_hash, run_id, _ = self.identity()
        self.assertIsNotNone(self.repo.load_research_report(run_id))
        recovered = rp.execute_final_holdout_evaluation(self.prepared, owner_token='publish-recovery-owner',
            recoveries={candidate_hash: {'request_id': 'recover-publish-1',
                                         'reason': 'confirmed manifest publication failure'}})
        self.assertEqual(('FAILED', 'COMPLETED'),
                         (failed['candidates'][0]['state'], recovered['candidates'][0]['state']))
        self.assertTrue((self.request.runs_dir/run_id/'manifest.json').is_file())

    def test_existing_output_manifest_blocks_recovery_before_audit_write(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        candidate_hash, run_id, _ = self.identity()
        path = self.request.runs_dir/run_id/'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manual review'):
            rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner',
                recoveries={candidate_hash: {'request_id': 'unsafe-output',
                                             'reason': 'must not accept existing artifact'}})
        self.assertEqual((), self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id))
        self.assertEqual('FAILED', self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]['state'])

    def test_recovery_request_is_idempotent_but_request_id_collision_is_rejected(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        candidate_hash, _, _ = self.identity()
        arguments = dict(candidate_spec_hash=candidate_hash, request_id='recovery-idempotent',
                         owner_token='recovery-owner', reason='reviewed failure evidence')
        self.assertTrue(self.repo.request_final_holdout_recovery(self.prepared.batch, **arguments))
        self.assertFalse(self.repo.request_final_holdout_recovery(self.prepared.batch, **arguments))
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.repo.request_final_holdout_recovery(self.prepared.batch,
                **{**arguments, 'reason': 'different reason'})
        with self.assertRaisesRegex(ValueError, 'generation already has'):
            self.repo.request_final_holdout_recovery(self.prepared.batch,
                **{**arguments, 'request_id': 'premature-next-recovery'})

    def test_concurrent_recovery_claim_has_one_owner(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        candidate_hash, run_id, spec = self.identity()
        self.repo.request_final_holdout_recovery(self.prepared.batch, candidate_spec_hash=candidate_hash,
            request_id='recovery-race', owner_token='recovery-owner', reason='reviewed race fixture')
        barrier = Barrier(2)
        def claim():
            repository = ResearchRepository(self.request.database); barrier.wait(timeout=5)
            return repository.claim_final_holdout_execution(self.prepared.batch,
                candidate_spec_hash=candidate_hash, run_id=run_id, spec=spec,
                input_manifest=self.prepared.dataset.manifest, owner_token='recovery-owner',
                recovery_request_id='recovery-race')
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(['busy', 'claimed'], sorted(executor.map(lambda _: claim(), range(2))))
        self.assertEqual(2, self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]['generation'])

    def test_each_additional_terminal_retry_requires_a_new_audited_request(self):
        candidate_hash, _, _ = self.identity()
        with patch.object(rp, 'execute_research', side_effect=ValueError('initial failure')):
            self.execute()
        with patch.object(rp, 'execute_research', side_effect=ValueError('recovery failure')):
            second = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner-1',
                recoveries={candidate_hash: {'request_id': 'recovery-generation-2',
                                             'reason': 'first reviewed recovery'}})
        self.assertEqual('FAILED', second['candidates'][0]['state'])
        with patch.object(rp, 'execute_research', side_effect=AssertionError('claimed request cannot be reused')) as retry:
            same = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner-1',
                recoveries={candidate_hash: {'request_id': 'recovery-generation-2',
                                             'reason': 'first reviewed recovery'}})
        self.assertEqual(('FAILED', 0), (same['candidates'][0]['state'], retry.call_count))
        completed = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner-2',
            recoveries={candidate_hash: {'request_id': 'recovery-generation-3',
                                         'reason': 'second reviewed recovery'}})
        self.assertEqual('COMPLETED', completed['candidates'][0]['state'])
        execution = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        recoveries = self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id)
        self.assertEqual(3, execution['generation'])
        self.assertEqual([2, 3], [row['generation'] for row in recoveries])

    def test_recovery_rejects_nonterminal_unknown_or_exposed_candidate_before_claim(self):
        candidate_hash, _, _ = self.identity()
        with self.assertRaisesRegex(ValueError, 'failed or cancelled'):
            rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner',
                recoveries={candidate_hash: {'request_id': 'not-run', 'reason': 'not terminal'}})
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='expose-after-failure',
            exposed_at=self.fixture.at, reason='reviewed failure for development')
        with self.assertRaisesRegex(ValueError, 'exposed'):
            self.repo.request_final_holdout_recovery(self.prepared.batch, candidate_spec_hash=candidate_hash,
                request_id='after-exposure', owner_token='recovery-owner', reason='too late')

    def test_malformed_recovery_map_is_rejected_without_ledger_changes(self):
        candidate_hash, _, _ = self.identity()
        malformed = (
            {'f'*64: {'request_id': 'unknown', 'reason': 'unknown candidate'}},
            {candidate_hash: {'request_id': '', 'reason': 'missing ID'}},
            {candidate_hash: {'request_id': 'extra', 'reason': 'extra field', 'other': 'x'}},
        )
        for recoveries in malformed:
            with self.subTest(recoveries=recoveries), self.assertRaises(ValueError):
                rp.execute_final_holdout_evaluation(self.prepared, owner_token=self.owner,
                                                    recoveries=recoveries)
        self.assertEqual((), self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))
        self.assertEqual((), self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id))

    def test_implementation_or_prepared_policy_drift_rejected_before_claim(self):
        with patch.object(rp, 'research_implementation_hash', return_value='f'*64):
            with self.assertRaisesRegex(ValueError, 'changed before execution'): self.execute()
        changed = replace(self.prepared, candidates=(replace(self.request,
                          evaluation=replace(
                              self.request.evaluation,
                              warmup_seconds=self.request.evaluation.warmup_seconds + 1,
                          )),))
        with self.assertRaises(ValueError):
            rp.execute_final_holdout_evaluation(changed,owner_token=self.owner)
        self.assertEqual((),self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))


    def test_candidate_request_raw_utf8_surrogate_is_rejected_before_conversion_or_policy(self):
        request_path, policy_path, _ = self.candidate_cli_paths()
        document = json.loads(request_path.read_bytes())
        document['note'] = 'raw-surrogate-marker'
        raw = json.dumps(document).encode('utf-8').replace(b'raw-surrogate-marker', b'\xed\xa0\x80')
        request_path.write_bytes(raw)
        self.assertIsInstance(json.loads(raw), dict)
        with self.assertRaises(UnicodeDecodeError) as expected:
            raw.decode('utf-8')
        with patch.object(candidate_artifacts, 'research_process_request_from_document',
                          wraps=rp.research_process_request_from_document) as converter:
            with self.assertRaises(UnicodeDecodeError) as actual:
                candidate_artifacts.freeze_policy(request_path, policy_path, strategy_ref='strict-utf8')
        self.assertEqual(expected.exception.args, actual.exception.args)
        converter.assert_not_called()
        self.assertFalse(policy_path.exists())
        self.assertEqual(raw, request_path.read_bytes())

    def test_candidate_request_one_read_and_parse_preserve_original_spec_and_raw_bytes(self):
        from pathlib import Path
        from contextlib import ExitStack
        request_path, _, _ = self.candidate_cli_paths()
        raw = request_path.read_bytes()
        original_open = Path.open
        original_loads = json.loads
        def captured_load(operation):
            reads = []
            def opened(path, *args, **kwargs):
                if Path(path).resolve() == request_path.resolve():
                    reads.append(args[0] if args else kwargs.get('mode', 'r'))
                return original_open(path, *args, **kwargs)
            with ExitStack() as stack:
                stack.enter_context(patch.object(Path, 'open', new=opened))
                decoded = stack.enter_context(patch.object(json, 'loads', wraps=original_loads))
                value = operation()
            return value, len(reads), decoded.call_count
        def original_contract():
            candidate_artifacts._read_object(request_path, maximum_bytes=4 * 1024 * 1024)
            return rp.load_research_process_request(request_path)
        before, old_reads, old_parses = captured_load(original_contract)
        after, new_reads, new_parses = captured_load(lambda: candidate_artifacts._request(request_path))
        self.assertEqual(before, after)
        self.assertEqual((2, 2), (old_reads, old_parses))
        self.assertEqual((1, 1), (new_reads, new_parses))
        self.assertEqual(raw, request_path.read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(request_path.read_bytes()).hexdigest())

    def test_candidate_request_byte_limits_encodings_and_errors_match_original_contract(self):
        request_path, _, _ = self.candidate_cli_paths()
        original = request_path.read_bytes()
        text = original.decode('utf-8')
        maximum = 4 * 1024 * 1024
        cases = {
            'utf8': original,
            'utf8_crlf': text.replace('\n', '\r\n').encode('utf-8'),
            'utf8_bom': b'\xef\xbb\xbf' + original,
            'utf16_bom': text.encode('utf-16'),
            'utf16_le': text.encode('utf-16-le'),
            'utf16_be': text.encode('utf-16-be'),
            'utf32_bom': text.encode('utf-32'),
            'utf32_le': text.encode('utf-32-le'),
            'utf32_be': text.encode('utf-32-be'),
            'non_object': b'[]',
            'non_object_bom': b'\xef\xbb\xbf[]',
            'malformed': b'{',
            'malformed_bom': b'\xef\xbb\xbf{',
            'invalid_utf8': b'{"bad":"\xff"}',
            'empty': b'',
            'exact_maximum': original + b' ' * (maximum - len(original)),
            'oversize': original + b' ' * (maximum + 1 - len(original)),
        }
        def outcome(operation):
            try:
                return ('value', operation())
            except (OSError, UnicodeError, ValueError) as error:
                return ('error', type(error), str(error))
        def original_contract(path):
            candidate_artifacts._read_object(path, maximum_bytes=maximum)
            return rp.load_research_process_request(path)
        for name, raw in cases.items():
            with self.subTest(case=name):
                request_path.write_bytes(raw)
                expected = outcome(lambda: original_contract(request_path))
                actual = outcome(lambda: candidate_artifacts._request(request_path))
                self.assertEqual(expected, actual)
                self.assertEqual(raw, request_path.read_bytes())
        for path in (request_path.with_name('missing-request.json'), request_path.parent):
            with self.subTest(path=str(path)):
                self.assertEqual(outcome(lambda: original_contract(path)),
                                 outcome(lambda: candidate_artifacts._request(path)))


if __name__ == '__main__':
    unittest.main()

"""EX-PANIC frozen final -> immutable candidate -> original native Shadow lineage."""
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


def prepare_panic_final(case, variant='P0', *, unresolved=False, native_factory=None):
    from tests.unit.test_research_final_execution import FinalExecutionTests, fixtures
    from tests.unit.test_mock_automation_specification import ACCOUNT_REF, PROFILE_ID, mock_specification_inputs
    from kiwoom_monitor import research_process as rp
    from kiwoom_monitor.application.mock_automation_candidate import (
        MockAutomationEligibilityPolicy, candidate_package_from_dict, eligibility_policy_from_dict, eligibility_receipt_from_dict,
    )
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
    from kiwoom_monitor.infrastructure.research_data_source import load_research_input, write_frozen_research_bundle

    fixture = FinalExecutionTests(); fixture.setUp(); case.addCleanup(fixture.doCleanups)
    root = fixture.fixture.root / ('panic-' + variant); root.mkdir()
    day = date(2026, 9, 25)
    stamp = lambda clock: f'{day}T{clock}:00+09:00'
    prices = (1000, 990, 980, 900, 910, 920, 925, 950, 960, 960)
    rows = fixtures.rows_for(day, len(prices))
    for row, price in zip(rows[1:], prices):
        row['payload'].update(open=price, high=price + 5, low=price - 5, close=price)
    source = root / 'source'
    relative = fixtures.child(source, day, rows)
    write_frozen_research_bundle(source, (relative,))
    references = {'version': 'strategy-reference/v1', 'listings': [], 'calendar': [{
        'venue': 'KRX', 'trading_date': day.isoformat(), 'available_at': stamp('08:00'),
        'revision_id': 'panic-calendar', 'evidence_ref': 'explicit-test-calendar', 'sessions': [{
            'starts_at': stamp('09:00'), 'ends_at': stamp('15:20'), 'session': 'KRX_REGULAR',
            'phase': 'CONTINUOUS', 'support': 'SUPPORTED', 'schedule_version': 'fixture/v1'}]}]}
    reference = root / 'references.json'; raw = json.dumps(references).encode(); reference.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    base, _, _ = fixture.candidate_cli_paths()
    document = json.loads(base.read_text(encoding='utf-8'))
    parameters = {'capital_won': 4000, 'quantity': 4, 'policy': {
        'variant': variant, 'lookback_minutes': 3, 'decline_bps': 1000, 'minimum_turnover_won': 3_000_000,
        'max_input_age_seconds': 10, 'rebound_target_bps': 500, 'max_hold_minutes': 5},
        'news_proofs': [] if unresolved else [{
            'symbol': '005930', 'source': 'explicit-test-news', 'assessment_id': 'panic-news', 'revision': 'news-r1',
            'effective_at': stamp('08:50'), 'available_at': stamp('08:55'),
            'disposition': 'supported_nonfundamental', 'evidence_refs': ['article-r1', 'body-r1', 'rule-r1']}]}
    if unresolved: parameters['panic_allow_unresolved_news'] = True
    document.update(family='panic_rebound/v1', dataset=str(source), database=str(root / 'research.sqlite3'),
        runs_dir=str(root / 'runs'), strategy=parameters, strategy_references={'path': str(reference), 'sha256': digest})
    document['execution'].update(version='completed_close_then_next_open/v1', same_bar_path_version='no_intrabar_fill/v1')
    document['execution']['cost_model'].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
        valid_from=stamp('08:00'), valid_to=stamp('16:00'))
    document['evaluation']['folds'][0].update(start=stamp('09:02'), end=stamp('16:00'))
    document['evaluation'].update(minimum_closed_trades=1, minimum_active_days=1)
    request = rp.research_process_request_from_document(document, root)
    implementation = rp.research_implementation_hash(fixtures.PROFILE)
    spec_hash = rp.final_candidate_spec_hash(request, implementation)
    manifest = load_research_input(source, session_profile=fixtures.PROFILE).manifest
    batch = rp.FinalHoldoutBatchSpec('final_holdout_batch/v1', manifest['dataset_id'], manifest['revision_ids_hash'],
                                    fixtures.PROFILE, (spec_hash,), request.evaluation)
    final = rp.FinalHoldoutExecutionRequest(batch, (request,), 'panic-final-access', stamp('17:00'), fixture.owner)
    result = rp.execute_final_holdout_request(final)['candidates'][0]
    case.assertEqual('COMPLETED', result['state'], result['reason'])
    repo = ResearchRepository(request.database)
    fills = [event for event in repo.load_execution_events(result['run_id']) if event['event_type'] == 'FILL']
    # These bars arrive two seconds after close: that minute's open predates
    # the decision, so the first available subsequent open is index5/index6.
    case.assertEqual([('BUY', 4, 920 if variant == 'P0' else 925), ('SELL', 4, 960)],
        [(event['side'], event['quantity'], event['price']) for event in fills])
    enter, = [event for event in repo.load_evaluations(result['run_id']) if event['final_action'] == 'ENTER']
    case.assertLess(datetime.fromisoformat(enter['decided_at']), datetime.fromisoformat(fills[0]['occurred_at']))
    repeated = rp.execute_final_holdout_request(final)['candidates'][0]
    case.assertEqual(('CACHED', result['run_id'], result['logical_result_hash']),
        (repeated['state'], repeated['run_id'], repeated['logical_result_hash']))
    policy = MockAutomationEligibilityPolicy('panic-test', spec_hash, datetime.fromisoformat(stamp('08:00')), 1, 1, -100, 1_000_000)
    publication = rp.prepare_mock_automation_candidate_publication(repo, request, implementation_hash=implementation,
        strategy_ref=policy.strategy_ref, account_ref=ACCOUNT_REF, final_batch_id=batch.batch_id,
        final_run_id=result['run_id'], eligibility_policy=policy)
    report = repo.load_research_report(result['run_id'])
    case.assertEqual(('ELIGIBLE', parameters), (publication['eligibility_receipt']['status'], publication['package']['candidate_spec']['parameters']),
        {key: report.get(key) for key in ('status', 'reasons', 'input_readiness', 'folds')})
    clock = [datetime.fromisoformat(stamp('09:00'))]
    store = native_factory(root / 'native.sqlite3', lambda: clock[0]) if native_factory else SQLiteQueryStore(root / 'native.sqlite3', now_provider=lambda: clock[0])
    store.initialize(); case.addCleanup(store.close)
    inputs = mock_specification_inputs(store, publish_candidate=False)
    inputs.repository.publish_mock_automation_candidate(candidate_package_from_dict(publication['package']),
        eligibility_policy_from_dict(publication['eligibility_policy']), eligibility_receipt_from_dict(publication['eligibility_receipt']),
        credential_profile_id=PROFILE_ID, expected_binding_revision=inputs.binding['binding_revision'])
    events = []
    if not unresolved:
        config = {'version': 'section12-minute-shadow/v1', 'family': request.family, 'signal_valid_seconds': 30,
                  'symbols': ['005930'], 'strategy': parameters}
        monitor = CandidateMonitor.from_json(store, json.dumps(config), poll_seconds=2,
            universe_max_age_seconds=180, now_provider=lambda: clock[0])
        monitor.run_once()
        for index, row in enumerate(rows[1:5 if variant == 'P0' else 6]):
            clock[0] = datetime.fromisoformat(row['available_at'])
            payload = row['payload']; at = datetime.fromisoformat(payload['bar_start'])
            key = dict(trading_date=at.date().isoformat(), minute=at.strftime('%H:%M'), code='005930', market='KRX')
            store.save_minute_bars([{**key, **{name: payload[name] for name in
                ('open', 'high', 'low', 'close', 'volume', 'trade_value_million_won')},
                'updated_at': clock[0].timestamp(), 'operation_id': f'panic-write-{index}'}])
            store.finalize_minute_bars([{**key, 'available_at': clock[0].timestamp(), 'capture_quality': 'complete',
                'finalization_source': 'timer', 'operation_id': f'panic-final-{index}'}])
            monitor.run_once()
        events = store.load_shadow_candidates()['events']
        case.assertEqual(1, len(events))
    with closing(store._connect()) as connection:
        for table in ('central_execution_intents', 'central_execution_events', 'central_execution_runtime_leases'):
            reader = connection.cursor(); reader.execute('SELECT COUNT(*) FROM ' + table)
            case.assertEqual(0, reader.fetchone()[0]); reader.close()
    return SimpleNamespace(root=root, repository=repo, run=result, request=request, publication=publication,
        parameters=parameters, events=events, store=store, inputs=inputs, clock=clock, final=final,
        implementation=implementation, batch=batch)


class PanicFinalPublicationTests(unittest.TestCase):
    def test_p0_final_publishes_original_setup_and_matches_native_shadow_without_orders(self):
        self.verify('P0')

    def test_p1_final_preserves_trigger_and_confirmation_and_matches_native_shadow_without_orders(self):
        self.verify('P1')

    def verify(self, variant, native_factory=None):
        from kiwoom_monitor.application.mock_automation_specification import matching_shadow_events
        from kiwoom_monitor.application.panic_rebound import matches_panic_shadow
        ctx = prepare_panic_final(self, variant, native_factory=native_factory)
        event, = matching_shadow_events(ctx.publication, tuple(ctx.events))
        self.assertEqual((900, 895, 945), tuple(event['panic_entry_setup'][name] for name in ('trigger_close', 'failure_price', 'target_price')))
        self.assertEqual(900 if variant == 'P0' else 910, event['signal_reference_price'])
        for change in ('target', 'quantity', 'variant', 'missing_news'):
            changed = deepcopy(event)
            if change == 'target': changed['panic_entry_setup']['target_price'] += 1
            elif change == 'quantity': changed['quantity'] += 1
            elif change == 'variant': changed['strategy_variant'] = 'P1' if variant == 'P0' else 'P0'
            else: changed['input_revision_refs'].remove('article-r1')
            with self.subTest(change=change): self.assertFalse(matches_panic_shadow(ctx.parameters, changed))
        later = deepcopy(ctx.parameters)
        adverse = {**later['news_proofs'][0], 'revision': 'adverse-after-cut', 'disposition': 'fundamental_adverse',
            'available_at': (datetime.fromisoformat(event['available_at']) + timedelta(seconds=1)).isoformat()}
        later['news_proofs'].append(adverse)
        self.assertTrue(matches_panic_shadow(later, event))
        adverse['available_at'] = event['available_at']
        self.assertFalse(matches_panic_shadow(later, event))
        from unittest.mock import patch
        from kiwoom_monitor import research_process as rp
        from kiwoom_monitor.application.mock_automation_candidate import eligibility_policy_from_dict
        wrong = deepcopy(ctx.repository.load_run(ctx.run['run_id']))
        wrong['spec']['parameters']['quantity'] += 1
        with patch.object(ctx.repository, 'load_run', return_value=wrong), self.assertRaisesRegex(ValueError, 'source run differs'):
            rp.prepare_mock_automation_candidate_publication(ctx.repository, ctx.request, implementation_hash=ctx.implementation,
                strategy_ref='panic-test', account_ref=ctx.publication['eligibility_receipt']['account_ref'],
                final_batch_id=ctx.batch.batch_id, final_run_id=ctx.run['run_id'],
                eligibility_policy=eligibility_policy_from_dict(ctx.publication['eligibility_policy']))
        return ctx

    def test_unresolved_news_final_remains_researchable_without_becoming_a_native_shadow_authorization(self):
        from kiwoom_monitor.application.mock_automation_specification import matching_shadow_events
        ctx = prepare_panic_final(self, unresolved=True)
        reasons = [reason for event in ctx.repository.load_evaluations(ctx.run['run_id']) for reason in event['reasons']]
        self.assertIn('panic_price_setup_news_unresolved', reasons)
        from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
        config = {'version': 'section12-minute-shadow/v1', 'family': ctx.request.family,
            'signal_valid_seconds': 30, 'symbols': ['005930'], 'strategy': ctx.parameters}
        with self.assertRaises(ValueError):
            CandidateMonitor.from_json(ctx.store, json.dumps(config), poll_seconds=2,
                universe_max_age_seconds=180, now_provider=lambda: ctx.clock[0])
        self.assertEqual((), matching_shadow_events(ctx.publication,
            tuple(ctx.repository.load_candidate_events(ctx.run['run_id']))))

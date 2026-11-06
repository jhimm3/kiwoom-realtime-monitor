"""Current required inputs cannot authorize a stale native PANIC entry.

All market/news observations and the broker are explicit isolated test inputs.
The genuine final/READY, current Shadow and native account owner are reused.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch


def append_partial_trigger_correction(case, ctx, *, future_seconds=0):
    """Retain the complete original plus an actually stored later partial cut."""
    trigger = ctx.event['panic_entry_setup']['trigger_revision_id']
    subject = ctx.event['symbol'] + ':KRX'
    rows = ctx.store.load_observation_revisions('minute_bar', subject)
    original, = [row for row in rows if row['revision_id'] == trigger]
    payload = original['payload']
    start = datetime.fromisoformat(payload['bar_start'])
    ctx.clock[0] += timedelta(milliseconds=1)
    key = dict(trading_date=start.date().isoformat(), minute=start.strftime('%H:%M'),
               code=ctx.event['symbol'], market='KRX')
    ctx.store.save_minute_bars([{**key, **{name: payload[name] for name in (
        'open', 'high', 'low', 'close', 'volume', 'trade_value_million_won')},
        'updated_at': ctx.clock[0].timestamp(), 'operation_id': 'rep8-later-trigger-partial-write'}])
    ctx.store.finalize_minute_bars([{**key, 'available_at': (ctx.clock[0]+timedelta(seconds=future_seconds)).timestamp(),
        'capture_quality': 'partial', 'finalization_source': 'timer',
        'operation_id': 'rep8-later-trigger-partial-finalize'}])
    retained = ctx.store.load_observation_revisions('minute_bar', subject)
    case.assertIn(original, retained)
    corrected, = [row for row in retained if row['observation_key'] == original['observation_key']
                  and row['revision_id'] != trigger]
    case.assertEqual('partial', corrected['completeness'])
    case.assertEqual(payload['close'], corrected['payload']['close'])
    case.assertGreater(datetime.fromisoformat(corrected['available_at']),
                       datetime.fromisoformat(original['available_at']))
    return original, corrected


def verify_late_news_native_entry_fence(case, native_factory=None):
    from tests.unit.test_panic_mock_execution import prepare_panic_native, panic_entry
    ctx = prepare_panic_native(case, native_factory=native_factory, ready_http=native_factory is None)
    historical = deepcopy(ctx.publication)
    original = deepcopy(ctx.event)
    submit = ctx.runtime.submit_automation_intent
    def news_arrives_before_claim(intent, account, **kwargs):
        ctx.clock[0] += timedelta(milliseconds=1)
        current = deepcopy(ctx.settings)
        current['revision'] += 1
        proofs = current['shadow_candidate_config']['strategy']['news_proofs']
        proofs.append({**proofs[0], 'assessment_id': 'rep8-new-adverse-assessment',
            'revision': 'rep8-new-adverse-news', 'available_at': ctx.clock[0].isoformat(),
            'disposition': 'fundamental_adverse', 'evidence_refs': ['rep8-late-article-original']})
        ctx.store.upsert_documents('server_operational_settings',
            [dict(owner='global', key='current', document=current)])
        return submit(intent, account, **kwargs)
    with patch.object(ctx.runtime, 'submit_automation_intent', side_effect=news_arrives_before_claim):
        with case.assertRaisesRegex(RuntimeError, 'PANIC_CURRENT_SHADOW_CHANGED'):
            panic_entry(ctx)
    case.assertEqual([], ctx.transport.calls)
    case.assertEqual((), ctx.runtime.load_active_intents())
    case.assertEqual(historical, ctx.publication)
    case.assertEqual(original, ctx.event)
    current = ctx.store.load_document('server_operational_settings', 'global', 'current')['document']
    case.assertEqual(2, len(current['shadow_candidate_config']['strategy']['news_proofs']))
    case.assertEqual('supported_nonfundamental', current['shadow_candidate_config']['strategy']['news_proofs'][0]['disposition'])


def verify_partial_correction_native_entry_fence(case, native_factory=None):
    from tests.unit.test_panic_mock_execution import prepare_panic_native, panic_entry
    ctx = prepare_panic_native(case, native_factory=native_factory)
    submit = ctx.runtime.submit_automation_intent
    def correction_arrives_before_claim(intent, account, **kwargs):
        append_partial_trigger_correction(case, ctx)
        return submit(intent, account, **kwargs)
    with patch.object(ctx.runtime, 'submit_automation_intent', side_effect=correction_arrives_before_claim):
        with case.assertRaisesRegex(RuntimeError, 'PANIC_CURRENT_.*REVIS|PANIC_ENTRY_.*REVIS'):
            panic_entry(ctx)
    case.assertEqual([], ctx.transport.calls)
    case.assertEqual((), ctx.runtime.load_active_intents())


def verify_future_correction_keeps_current_knowledge(case, native_factory=None):
    from tests.unit.test_panic_mock_execution import prepare_panic_native, panic_entry, refresh_panic_cut
    ctx = prepare_panic_native(case, native_factory=native_factory)
    _, future = append_partial_trigger_correction(case, ctx, future_seconds=60)
    ctx.quote['available_at'] = ctx.clock[0]
    refresh_panic_cut(ctx, {})  # Obtain the actual current account/Risk cut after source delivery.
    case.assertGreater(datetime.fromisoformat(future['available_at']), ctx.clock[0])
    gate, buy, _ = panic_entry(ctx)
    case.assertIsNotNone(buy, gate)
    case.assertEqual([('BUY', 4)], [(row[1], row[3]) for row in ctx.transport.calls])


async def verify_held_protection_after_late_inputs(case, native_factory=None):
    from tests.unit.test_panic_mock_execution import verify_panic_runner
    retained = {}
    async def later_inputs(ctx, runner):
        retained['entry'] = deepcopy(ctx.event)
        retained['rows'] = append_partial_trigger_correction(case, ctx)
        current = deepcopy(ctx.settings)
        current['revision'] += 1
        proofs = current['shadow_candidate_config']['strategy']['news_proofs']
        proofs.append({**proofs[0], 'assessment_id': 'rep8-held-new-adverse-assessment',
            'revision': 'rep8-held-late-adverse-news', 'available_at': ctx.clock[0].isoformat(),
            'disposition': 'fundamental_adverse', 'evidence_refs': ['rep8-held-late-article']})
        ctx.store.upsert_documents('server_operational_settings',
            [dict(owner='global', key='current', document=current)])
        ctx.settings = current  # The real DB restart must retain these later settings.
        from kiwoom_monitor.application.minute_order_management import minute_current_shadow_matches
        case.assertFalse(minute_current_shadow_matches(ctx.spec, ctx.event, current))
    ctx = await verify_panic_runner(case, native_factory, after_partial_fill=later_inputs)
    case.assertEqual(retained['entry'], ctx.event)
    rows = ctx.store.load_observation_revisions('minute_bar', ctx.event['symbol']+':KRX')
    case.assertTrue(all(row in rows for row in retained['rows']))
    current = ctx.store.load_document('server_operational_settings', 'global', 'current')['document']
    case.assertEqual('rep8-held-late-adverse-news',
        current['shadow_candidate_config']['strategy']['news_proofs'][-1]['revision'])
    # Project the real native detail stream, then use the production screen
    # query and renderer with independent broker summaries of the same fills.
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication, QTableWidget
    from tests.unit.test_mock_automation_specification import _ExecutionPageSource
    from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
    from kiwoom_monitor.application.trade_history_query_service import TradeHistoryQueryService
    from kiwoom_monitor.application.trade_history_service import TradeFill
    from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
    from kiwoom_monitor.journal_process import JournalWindow
    scope = ctx.spec.account_scope
    journal = JournalRepository(ctx.root/'representative8-journal.sqlite3')
    projected = sync_journal_execution_projection(_ExecutionPageSource(ctx.execution, ctx.runtime.account_ref),
        journal, scope, now=ctx.clock[0])
    case.assertEqual(3, projected.detailed_fill_count)
    summaries = tuple(TradeFill(row['number'], ctx.event['symbol'], 'synthetic retained PANIC',
        '매수' if row['side'].value == 'BUY' else '매도', fill.occurred_at.replace(tzinfo=None),
        fill.quantity, fill.price, origin_scope=scope)
        for row in ctx.book.rows.values() for fill in row['fills'])
    journal.upsert_fills(summaries, account_scope=scope)
    at = datetime.fromisoformat(retained['entry']['available_at']).replace(tzinfo=None)
    visible = TradeHistoryQueryService(JournalRepository(ctx.root/'representative8-journal.sqlite3')).load(
        at.replace(hour=0, minute=0, second=0), at+timedelta(days=1), scope)
    summary_key = lambda fill: (fill.order_no, fill.stock_code, fill.side, fill.quantity, fill.price,
                               fill.filled_at.replace(microsecond=0))
    case.assertEqual({summary_key(fill) for fill in summaries}, {summary_key(fill) for fill in visible.fills})
    case.assertEqual({'BUY': 3, 'SELL': 3}, {side: sum(row['quantity'] for row in
        journal.load_execution_fill_projections(scope) if row['side'] == side) for side in ('BUY', 'SELL')})
    case.assertEqual({ctx.buy.intent.plan.plan_id}, {plan.plan_id for plan in visible.execution_plans.values()})
    case._rep8_application = QApplication.instance() or QApplication([])
    screen = SimpleNamespace(_fills_table=QTableWidget(0, 9), _select_journal_cell=lambda *_: None,
        _visible_execution_plans=visible.execution_plans,
        _visible_execution_members=getattr(visible, 'execution_members', {}))
    JournalWindow._render_fills(screen, visible.fills)
    for index, fill in enumerate(visible.fills):
        case.assertEqual(fill.order_no, screen._fills_table.item(index, 8).text())
        case.assertIn(ctx.buy.intent.plan.plan_id, screen._fills_table.item(index, 8).toolTip())


class CurrentInputAvailabilityTests(unittest.TestCase):
    def test_late_current_news_after_shadow_before_native_claim_cannot_authorize_buy(self):
        verify_late_news_native_entry_fence(self)

    def test_partial_trigger_correction_after_shadow_before_native_claim_cannot_authorize_buy(self):
        verify_partial_correction_native_entry_fence(self)

    def test_future_available_correction_is_retained_without_becoming_current_knowledge(self):
        verify_future_correction_keeps_current_knowledge(self)


class HeldInputAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_news_and_partial_correction_keep_first_protection_original_plan_and_screen_journal(self):
        await verify_held_protection_after_late_inputs(self)


if __name__ == '__main__':
    unittest.main()

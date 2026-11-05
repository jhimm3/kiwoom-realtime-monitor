"""CLOSE plan workflow borrowed by the existing automatic-account Episode."""
from __future__ import annotations

from datetime import datetime, timedelta

from kiwoom_monitor.application.close_and_ipo import matches_close_shadow
from kiwoom_monitor.application.close_mock_execution import (
    build_close_exit_evidence, dispatch_close_entry, dispatch_close_exit,
    frozen_close_candidate, prepare_close_entry_deadline_claim,
    close_current_shadow_matches, prepare_close_order_management_claim,
)
from kiwoom_monitor.application.mock_automation_execution import mock_automation_intent_id
from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES, OrderSide, OrderState
from kiwoom_monitor.domain.execution_activation import allows_stopped_close_management
from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs
from .observation_frame_recovery import drain_thread_call


CLOSE_INPUT_PROTOCOL = 'close-shadow-native-plan/v1'


class ClosePlanManagement:
    """Own the Shadow cursor and retained plan inputs, without a worker or order authority."""

    def __init__(self, store, bundle, spec, repository, *, parameters, price_source, now_provider,
                 family='close_betting/v1'):
        self._store, self._bundle, self._spec, self._repository = store, bundle, spec, repository
        self._parameters, self._price, self._now = parameters, price_source, now_provider
        self._family = family
        self._pending_entry = None
        self._cursor = 0
        self._plans = {}
        self.status = {'state': 'WARMUP', 'reason': 'awaiting_new_close_shadow', 'orders_enabled': False}

    def checkpoint_fields(self):
        return {'version': CLOSE_INPUT_PROTOCOL, 'shadow_cursor': self._cursor}

    def active_candidate(self, symbol=None):
        """Borrow the original RAM setup even during the first in-flight BUY dispatch."""
        events = (context['event'] for context in self._plans.values())
        return next((event for event in (*events, self._pending_entry)
                     if event is not None and (symbol is None or event['symbol'] == symbol)), None)

    def prepare(self, document):
        if document is not None:
            if (document.get('version') != CLOSE_INPUT_PROTOCOL
                    or type(document.get('shadow_cursor')) is not int or document['shadow_cursor'] < 0):
                raise ValueError('invalid CLOSE Shadow checkpoint')
            self._cursor = document['shadow_cursor']
        else:
            self._cursor = self._store.load_shadow_candidates(0, 1)['high_watermark']
        offset = 0
        while True:
            rows = self._store.load_documents('execution_mock_plan_positions', self._bundle.account_ref, 1000, offset)
            for row in rows:
                head = row['document']
                if head['run_id'] != self._bundle.run_id:
                    continue
                context = self._load_context(head['plan_id'])
                if context is not None:
                    root, position = self._read_plan(context)
                    if position.quantity or root.state not in TERMINAL_ORDER_STATES:
                        self._plans[head['plan_id']] = context
            if len(rows) < 1000:
                break
            offset += len(rows)

    def _load_context(self, plan_id):
        proof = self._repository.load_close_entry_evidence(self._bundle.account_ref, plan_id)
        if proof is None or proof['snapshot']['execution_run_id'] != self._bundle.run_id:
            return None
        event = proof['snapshot'].get('candidate')
        if event is None:
            # Only the previous v1 prototype lacks a retained event. Preserve its
            # immutable source rather than guessing the current Shadow setup.
            cursor = 0
            while True:
                page = self._store.load_shadow_candidates(cursor, 1000)
                event = next((row for row in page['events']
                              if row['event_id'] == proof['snapshot']['candidate_event_id']), None)
                if event is not None or not page['events']:
                    break
                cursor = page['events'][-1]['accepted_sequence']
                if cursor >= page['high_watermark']:
                    break
        if event is None:
            raise ValueError('original CLOSE candidate is unavailable')
        root_id = mock_automation_intent_id(self._spec.spec_id, proof['decision']['decision_id'])
        return {'proof': proof, 'event': frozen_close_candidate(event), 'root_id': root_id}

    def _read_plan(self, context):
        execution = self._bundle.repository
        root = execution.load(context['root_id'])
        if root is None:
            raise ValueError('original CLOSE order is unavailable')
        return root, execution.load_plan_position(self._bundle.account_ref, self._bundle.run_id,
                                                  root.intent.symbol, root.intent.plan)

    async def _fresh_cut(self):
        recovery = await self._bundle.monitor.refresh_recovery()
        risk = await drain_thread_call(self._repository.load_latest_mock_automation_risk, self._bundle.account_ref)
        if risk is None:
            raise ValueError('CLOSE current native Risk is unavailable')
        await drain_thread_call(record_mock_automation_recovery_from_risk, self._repository, self._bundle.runtime,
            recovery, risk, account_ref=self._bundle.account_ref, spec_id=self._spec.spec_id)
        return recovery, risk

    def _management_input(self, record, control, quote, enabled, notice):
        cut = self._bundle.monitor.latest_automation_execution_cut
        risk = cut['risk'] if cut is not None else None
        material_risk = ((risk.daily_net_pnl_won, risk.owned_position_quantities, risk.pending_sell_quantities,
            risk.reconciliation_complete, risk.submission_unknown_count, risk.balance_mismatch_count, risk.reconnect_count)
            if risk is not None else None)
        return (record.state, record.filled_quantity, tuple((leg.broker_order_id, leg.limit_price, leg.remaining_quantity, leg.state)
                for leg in execution_broker_legs(record)), control.control_revision, enabled,
            quote.get('price_won') if quote is not None else None,
            quote.get('input_ref') if quote is not None and record.amendment is not None
                and record.amendment.status == 'REJECTED' else None,
            notice.get('revision') if notice is not None else None, material_risk,
            self._now() >= min(datetime.fromisoformat(self._plans[record.intent.plan.plan_id]['event']['expires_at']),
                              datetime.fromisoformat(self._plans[record.intent.plan.plan_id]['event']['close_ipo_entry_setup']['entry_deadline'])))

    async def _manage_current_order(self, record, context, control, notice):
        terms = self._spec.close_execution_policy
        if terms.accepted_order_management != 'reassess_then_amend_price/v1':
            return False
        if control.desired_state.value != 'RUNNING' and not allows_stopped_close_management(self._spec):
            self.status = {'state': 'STOPPED', 'reason': 'original_plan_management_disabled', 'orders_enabled': False}
            return True
        if record.state is OrderState.SUBMISSION_UNKNOWN:
            self._bundle.monitor.request_automation_execution_recovery()
            self.status = {'state': 'WAITING_ORDER', 'reason': record.state.value,
                           'pending_intent_id': record.intent.intent_id, 'orders_enabled': False}
            return True
        if record.state is OrderState.CANCEL_PENDING:
            self._bundle.monitor.request_automation_execution_recovery()
        settings = await drain_thread_call(self._store.load_document, 'server_operational_settings', 'global', 'current')
        enabled = (record.intent.side is OrderSide.BUY and control.desired_state.value == 'RUNNING' and not control.entry_resume_required
                   and close_current_shadow_matches(self._spec, context['event'], settings['document'] if settings else None))
        quote = self._price(record.intent.symbol) if self._price is not None else None
        token = self._management_input(record, control, quote, enabled, notice)
        if context.get('management_input') == token:
            return True
        outcome = {}
        def prepare(current, cut):
            current_quote = self._price(current.intent.symbol) if self._price is not None else None
            claim = prepare_close_order_management_claim(self._repository, self._bundle.runtime, self._bundle.repository,
                self._spec, current, parameters=self._parameters, event=context['event'], quote=current_quote,
                recovery=cut['recovery'], risk=cut['risk'], entry_enabled=enabled, protection_notice=notice)
            if claim is not None:
                outcome.update(claim.assessment)
            return claim
        updated = await self._bundle.gateway.manage_close_order(record, spec_id=self._spec.spec_id,
            control_revision=control.control_revision, prepare_management=prepare)
        if outcome.get('action') in {'AMEND', 'CANCEL'}:
            context.pop('management_input', None)  # Observe the command result/any unmoved physical remainder first.
        else:
            context['management_input'] = self._management_input(updated, control, quote, enabled, notice)
        self.status = {'state': 'WAITING_ORDER', 'reason': outcome.get('reason', updated.state.value),
                       'pending_intent_id': updated.intent.intent_id, 'orders_enabled': False}
        return True

    async def _manage(self, plan_id, context, control, protection_notice):
        execution = self._bundle.repository
        root, head = await drain_thread_call(self._read_plan, context)
        event, now = context['event'], self._now()
        if root.state is OrderState.QUEUED and now >= root.intent.expires_at:
            await drain_thread_call(self._bundle.runtime.cancel, root.intent.intent_id)
        elif root.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
            if await self._manage_current_order(root, context, control, protection_notice):
                return
            deadline = datetime.fromisoformat(event['close_ipo_entry_setup']['entry_deadline'])
            if now >= deadline or protection_notice is not None:
                def claim(record, cut):
                    return prepare_close_entry_deadline_claim(self._repository, self._bundle.runtime, execution,
                        self._spec, record, parameters=self._parameters, event=event,
                        recovery=cut['recovery'], risk=cut['risk'], protection_notice=protection_notice)

                root = await self._bundle.gateway.cancel_automation_intent(root, spec_id=self._spec.spec_id,
                    control_revision=control.control_revision, prepare_plan_cancel=claim)
        elif root.state in TERMINAL_ORDER_STATES:
            active = await drain_thread_call(execution.active_intents, 'mock', self._bundle.account_ref, self._bundle.run_id)
            pending = [row for row in active if getattr(row.intent, 'plan', None) is not None and row.intent.plan.plan_id == plan_id]
            if pending:
                if await self._manage_current_order(pending[0], context, control, None):
                    return
                self.status = {'state': 'WAITING_ORDER', 'reason': 'close_exit_reconciliation', 'orders_enabled': False}
                return
            if not head.quantity:
                del self._plans[plan_id]
                return
            quote = self._price(root.intent.symbol) if self._price is not None else None
            if quote is None:
                self.status = {'state': 'MANAGING', 'reason': 'awaiting_current_0B', 'orders_enabled': False}
                return
            setup = event['close_ipo_entry_setup']
            next_open = datetime.fromisoformat(setup['next_open_at']) if setup['next_open_at'] is not None else None
            policy = self._parameters['policy']
            gap = None
            if (next_open is not None
                    and next_open + timedelta(minutes=1) <= now < next_open + timedelta(minutes=policy['exit_minutes_after_open'])
                    and setup.get('next_session') is not None):
                auction = setup['close_auction']
                rows = await drain_thread_call(self._store.load_close_gap_observations, root.intent.symbol,
                    datetime.fromisoformat(auction['starts_at']), datetime.fromisoformat(auction['ends_at']), next_open, now)
                if rows:
                    gap = list(rows)
            args = dict(parameters=self._parameters, event=event, trigger_quote=quote, quote=quote,
                        gap_observations=gap, protection_notice=protection_notice)
            stop_risk = (await drain_thread_call(self._repository.load_mock_automation_risk_snapshot,
                self._bundle.account_ref, protection_notice['risk_trigger']['snapshot_id'])
                if protection_notice is not None else None)
            prepared = build_close_exit_evidence(self._spec, run_id=self._bundle.run_id, entry_evidence=context['proof'],
                root=root, head=head, fill_events=await drain_thread_call(execution.events, root.intent.intent_id),
                decided_at=now, now=now, stop_risk=stop_risk, **args)
            if prepared is None:
                self.status = {'state': 'MANAGING', 'reason': 'ipo_intraday_hold' if self._family == 'ipo_breakout/v1'
                               else 'planned_overnight_hold', 'orders_enabled': False}
                return
            recovery, risk = await self._fresh_cut()
            # Broker reconciliation can advance the plan revision even when
            # its detailed quantity is unchanged. Submit against that fresh head.
            _, head = await drain_thread_call(self._read_plan, context)
            gate, sell, _ = await drain_thread_call(dispatch_close_exit, self._repository, self._bundle.runtime,
                execution, self._spec, head.plan, recovery=recovery, risk=risk, **args)
            self.status = {'state': 'ORDER_SUBMITTED' if sell is not None else 'BLOCKED',
                'reason': ';'.join(gate.reasons) if gate.reasons else 'close_exit_submitted', 'orders_enabled': False,
                'pending_intent_id': sell.intent.intent_id if sell is not None else ''}
            return
        elif root.state in {OrderState.CANCEL_PENDING, OrderState.SUBMISSION_UNKNOWN}:
            if await self._manage_current_order(root, context, control, protection_notice):
                return
        self.status = {'state': 'WAITING_ORDER', 'reason': root.state.value,
                       'pending_intent_id': root.intent.intent_id, 'orders_enabled': False}

    async def advance(self, control, *, limit, allow_entry, protection_notice=None):
        stop = (protection_notice if protection_notice is not None and protection_notice.get('state') == 'PENDING'
                and protection_notice.get('risk_trigger', {}).get('stop_loss') is not None else None)
        for plan_id, context in tuple(self._plans.items()):
            try:
                await self._manage(plan_id, context, control, stop)
            except ValueError as error:
                self.status = {'state': 'BLOCKED', 'reason': str(error), 'orders_enabled': False}
        if self._plans:
            return 0
        if not allow_entry() or control.desired_state.value != 'RUNNING':
            self.status = {'state': 'STOPPED' if control.desired_state.value != 'RUNNING' else 'PROTECTION_REVIEW_REQUIRED',
                           'reason': 'new_close_entries_disabled', 'orders_enabled': False}
            return 0
        self.status = {'state': 'RUNNING', 'reason': 'awaiting_new_close_shadow', 'orders_enabled': False}
        page = await drain_thread_call(self._store.load_shadow_candidates, self._cursor, limit)
        consumed = 0
        for event in page['events']:
            try:
                if matches_close_shadow(self._parameters, event, family=self._family):
                    now = self._now()
                    if datetime.fromisoformat(event['available_at']) <= now < datetime.fromisoformat(event['expires_at']):
                        quote = self._price(event['symbol']) if self._price is not None else None
                        if quote is None:
                            self.status = {'state': 'WARMUP', 'reason': 'awaiting_current_0B', 'orders_enabled': False}
                            break
                        if quote is not None:
                            recovery, risk = await self._fresh_cut()
                            if not allow_entry():
                                break
                            self._pending_entry = frozen_close_candidate(event)
                            try:
                                gate, record, _ = await drain_thread_call(dispatch_close_entry, self._repository,
                                    self._bundle.runtime, self._spec, parameters=self._parameters, event=event,
                                    quote=quote, recovery=recovery, risk=risk)
                                if record is not None:
                                    self._plans[record.intent.plan.plan_id] = await drain_thread_call(
                                        self._load_context, record.intent.plan.plan_id)
                            finally:
                                self._pending_entry = None
                            self.status = {'state': 'ORDER_SUBMITTED' if record is not None else 'BLOCKED',
                                'reason': ';'.join(gate.reasons) if gate.reasons else 'close_entry_submitted',
                                'pending_intent_id': record.intent.intent_id if record is not None else '', 'orders_enabled': False}
            except ValueError as error:
                self.status = {'state': 'BLOCKED', 'reason': str(error), 'orders_enabled': False}
            self._cursor = event['accepted_sequence']
            consumed += 1
            if self._plans:
                break
        return consumed

"""Replay existing strategy inputs through the isolated local order owner."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace

from kiwoom_monitor.application.close_and_ipo import CloseIpoPosition, IpoBreakoutPolicy, evaluate_close_and_ipo
from kiwoom_monitor.application.leader_breakout import (
    LeaderBreakoutState, evaluate_leader_breakout_trade, evaluate_leader_position_step,
    leader_position_step_key, leader_reentry_enabled, leader_setup_key,
)
from kiwoom_monitor.application.market_session_schedule import MarketPhase, MarketSession, decode_strategy_references
from kiwoom_monitor.application.panic_rebound import PanicReboundDecision, assess_panic_news, evaluate_panic_rebound
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.application.section12_minute_research import (
    CLOSE_FAMILY, IPO_BREAKOUT_FAMILY, IPO_CLOSE_FAMILY, PANIC_FAMILY, MinuteExperiment,
)
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot, BrokerFill, BrokerOrderSnapshot, OrderSide, OrderState, TERMINAL_ORDER_STATES,
)
from kiwoom_monitor.domain.strategy_contract import (
    LEADER_BREAKOUT_FAMILY_ID, LeaderBreakoutConfig, LeaderBreakoutStagedExecutionContract,
)
from kiwoom_monitor.domain.time_basis import aware_iso_kst, kst_now
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery, BrokerOrderDetails
from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_remaining_quantity
from .local_order_rehearsal import LocalOrderRehearsal, RehearsalConditions


FAMILY_LABELS = {LEADER_BREAKOUT_FAMILY_ID: '돌파', CLOSE_FAMILY: '종가베팅',
                 IPO_BREAKOUT_FAMILY: '신규주 돌파', IPO_CLOSE_FAMILY: '신규주 종가베팅', PANIC_FAMILY: '낙주'}


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('양수 정수 가격·수량·한도가 필요합니다.')
    return value


class _ReplayBroker:
    """Independent fake ledger. Fills come from tape instructions, never price touch or the app ledger."""

    environment = 'real'

    def __init__(self, path, account, symbols, venue, capital, now, destinations):
        self.path, self.account, self.symbols, self.venue = path, account, tuple(symbols), venue
        self.capital, self.now = capital, now
        self.destinations = destinations

    def reload(self):
        self.state = (json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists()
                      else {'cursor': 0, 'prepared': -1, 'orders': {}, 'commands': [], 'entry_stopped': False})

    def save(self):
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as out:
            temp = Path(out.name)
            json.dump(self.state, out, separators=(',', ':'))
        try:
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)

    def _symbol(self, order):
        return order.get('symbol', self.symbols[0] if len(self.symbols) == 1 else None)

    def prepare(self, index, fills_by_symbol):
        if self.state['prepared'] == index:
            return
        if self.state['prepared'] != index - 1:
            raise ValueError('가짜 체결 재생 순서가 바뀌었습니다.')
        orders = self.state['orders']
        operations = []
        for symbol, fills in fills_by_symbol.items():
            for side, quantity in fills.items():
                matches = [(number, row) for number, row in orders.items()
                           if self._symbol(row) == symbol and row['side'] == side and row['remaining'] > 0]
                if not matches:
                    continue  # An execution instruction has no working order on this route.
                if len(matches) != 1 or quantity > matches[0][1]['remaining']:
                    raise ValueError('가짜 체결 수량이 현재 종목 주문 잔량과 맞지 않습니다.')
                operations.append((*matches[0], quantity))
        for number, row, quantity in operations:
            row['remaining'] -= quantity
            row['fills'].append([f'{index}:{number}', quantity, row['price'], self.now().isoformat()])
            row['state'] = 'PARTIALLY_FILLED' if row['remaining'] else 'FILLED'
        self.state['prepared'] = index
        self.save()

    def request_once(self, api_id, path, body):
        symbol = body['stk_cd']
        if path != '/api/dostk/ordr' or symbol not in self.symbols:
            raise ValueError('가짜 주문의 거래소 또는 종목이 바뀌었습니다.')
        commands, orders = self.state['commands'], self.state['orders']
        number = f'{len(commands) + 1:07d}'
        physical = self.destinations[symbol]
        response = {'ord_no': number, 'dmst_stex_tp': physical, 'return_code': 0}
        if api_id in {'kt10000', 'kt10001'}:
            if body['dmst_stex_tp'] != self.venue:
                raise ValueError('가짜 신규 주문의 요청 거래소가 바뀌었습니다.')
            side = 'BUY' if api_id == 'kt10000' else 'SELL'
            quantity, price, parent = int(body['ord_qty']), int(body['ord_uv']), None
        elif api_id in {'kt10002', 'kt10003'}:
            parent = body['orig_ord_no']
            original = orders[parent]
            if self._symbol(original) != symbol:
                raise ValueError('다른 종목의 원주문을 변경할 수 없습니다.')
            if body['dmst_stex_tp'] != original.get('venue', self.venue):
                raise ValueError('원주문이 접수된 거래소에서만 변경할 수 있습니다.')
            if original['remaining'] <= 0:
                return {'return_code': 1, 'return_msg': '확인된 잔량이 없습니다.'}
            side = original['side']
            if api_id == 'kt10002':
                quantity, price = int(body['mdfy_qty']), int(body['mdfy_uv'])
                if quantity != original['remaining']:
                    return {'return_code': 1, 'return_msg': '정정 잔량이 바뀌었습니다.'}
                original['state'] = 'FILLED' if original['fills'] else 'CANCELLED'
                response.update(base_orig_ord_no=parent, mdfy_qty=str(quantity))
            else:
                quantity, price = 0, original['price']
                if int(body['cncl_qty']) != 0:
                    raise ValueError('가짜 브로커의 취소는 확인된 전량을 대상으로 합니다.')
                original['state'] = 'CANCELLED'
            original['remaining'] = 0
        else:
            raise ValueError('가짜 주문의 요청 종류를 확인할 수 없습니다.')
        if quantity:
            orders[number] = dict(symbol=symbol, side=side, quantity=quantity, remaining=quantity, price=price,
                                  parent=parent, venue=physical,
                                  state='ACCEPTED', fills=[])
        commands.append({'api_id': api_id, 'body': body, 'order_id': number, 'frame': self.state['prepared']})
        self.save()
        return response

    def read_recovery(self):
        now, cash, positions, reserved = self.now(), self.capital, {}, 0
        orders, details = [], []
        for number, row in self.state['orders'].items():
            symbol = self._symbol(row)
            if symbol not in self.symbols:
                raise ValueError('저장된 가짜 주문의 종목 범위가 바뀌었습니다.')
            fills = tuple(BrokerFill(ref, qty, price, aware_iso_kst(at)) for ref, qty, price, at in row['fills'])
            quantity, notional = sum(fill.quantity for fill in fills), sum(fill.quantity * fill.price for fill in fills)
            sign = 1 if row['side'] == 'BUY' else -1
            positions[symbol] = positions.get(symbol, 0) + sign * quantity
            cash -= sign * notional
            if sign > 0:
                reserved += row['remaining'] * row['price']
            orders.append(BrokerOrderSnapshot(number, self.account, symbol, OrderState(row['state']),
                                               quantity, row['remaining'], now, fills))
            details.append(BrokerOrderDetails(number, row['quantity'], OrderSide(row['side']), row['price'],
                                              row.get('venue', self.venue),
                                              original_broker_order_id=row['parent']))
        return AccountRecovery(AccountSnapshot(self.account, cash, reserved, {k: v for k, v in positions.items() if v}, now),
                               tuple(orders), tuple(details))


class _StrategyPlan:
    def __init__(self, replay, document):
        self.replay = replay
        self.family, self.symbol = document['family'], document['symbol']
        if self.family not in FAMILY_LABELS or not isinstance(self.symbol, str):
            raise ValueError('지원하는 전략과 종목을 지정하세요.')
        self.calendar, self.listings = decode_strategy_references(document['references'])
        settings = document['parameters']
        self.capital = _positive(settings['capital_won'])
        self.stages = {}
        if self.family == LEADER_BREAKOUT_FAMILY_ID:
            self.config = LeaderBreakoutConfig.from_dict(settings['policy'])
            self.policy, self.quantity, self.news = self.config.policy, _positive(settings['quantity']), ()
            terms = self.config.execution_contract
            if replay.portfolio and isinstance(terms, LeaderBreakoutStagedExecutionContract):
                if self.config.execution_missing('0B') or terms.quantity != self.quantity:
                    raise ValueError('돌파 단계 정책과 최초 수량이 일치해야 합니다.')
                self.capital = min(self.capital, terms.capital_won)
                self.stages = {f'stage-{index}': (index, step) for index, step in enumerate(terms.position_steps or ())}
        else:
            config = MinuteExperiment.from_document(self.family, settings)
            self.policy, self.quantity, self.news = config.policy, config.quantity, config.news_proofs
        self.ipo_reduce = replay.portfolio and self.family == IPO_CLOSE_FAMILY and self.policy.ipo_reduction_quantity is not None
        self.slots = {'BUY': OrderSide.BUY, 'SELL': OrderSide.SELL}
        self.slots.update({slot: OrderSide.BUY if step.action == 'ADD' else OrderSide.SELL
                           for slot, (_, step) in self.stages.items()})
        if self.ipo_reduce:
            self.slots['ipo-reduce'] = OrderSide.SELL
        self.bars, self.entries = [], {}
        self.leader_state, self.armed = None, None
        self.last_observation = (-1, None)
        self.reason = ''

    def observe(self, index, row, now):
        if self.last_observation[0] == index:
            return self.last_observation[1]
        bar = KrxMinuteBarFrame(**row['bar']) if 'bar' in row else None
        if bar is not None:
            self.bars.append(bar)
        if self.family == LEADER_BREAKOUT_FAMILY_ID and 'trade' in row:
            decision, self.leader_state = evaluate_leader_breakout_trade(
                self.policy, row['trade'], received_at=now, source='local-rehearsal', state=self.leader_state,
            )
            context = next((item for item in self.policy.contexts if item.evidence_ref == decision['context_ref']), None)
            if decision['proposal'] == 'ENTER':
                self.entries[now] = (context, min(now + self.replay.signal_age, aware_iso_kst(decision['valid_until'])),
                                     decision['signal_reference_price'])
            self.reason = decision['reasons'][-1] if decision['reasons'] else decision['status']
        elif self.family != LEADER_BREAKOUT_FAMILY_ID and bar is not None:
            if self.family == PANIC_FAMILY:
                decision = evaluate_panic_rebound(evaluation_bar=bar, bar_history=self.bars, rules=self.policy,
                                                   news_proofs=self.news, setup=self.armed)
                self.armed = decision.setup if decision.action == 'ARM' else None
            else:
                decision = evaluate_close_and_ipo(self.policy, bar=bar, history=self.bars, calendar=self.calendar,
                                                   listings=self.listings, position=None)
            self.reason = decision.reason
            if decision.action == 'ENTER':
                deadline = getattr(decision.setup, 'entry_deadline', now + self.replay.signal_age)
                self.entries[now] = (decision.setup, min(now + self.replay.signal_age, deadline), bar.close)
        self.last_observation = index, bar
        return bar

    def _entry_valid(self, entry, market_price, route_price):
        if entry is None:
            return None
        setup, deadline, anchor = entry
        if self.replay.now >= deadline or self.replay.broker.state['entry_stopped']:
            return False
        if market_price is None or route_price is None:
            return None
        if route_price * 10_000 > anchor * (10_000 + self.replay.slippage_bps):
            return False
        if self.family == LEADER_BREAKOUT_FAMILY_ID:
            return (setup.leader_eligible and self.replay.now < aware_iso_kst(setup.valid_until)
                    and market_price >= setup.invalidation_price
                    and market_price * 10_000 > setup.reference_price * (10_000 + self.policy.breakout_buffer_bps))
        if self.family == PANIC_FAMILY:
            news = assess_panic_news(PanicReboundDecision('ENTER', 'reassess_entry', setup, setup.input_refs),
                                     self.symbol, self.replay.now, self.news, False)
            return setup.failure_price < market_price < setup.target_price and news.action == 'ENTER'
        if isinstance(self.policy, IpoBreakoutPolicy):
            return market_price * 10_000 > setup.breakout_level * (10_000 + self.policy.breakout_buffer_bps)
        return True

    def _position(self, records):
        buy_records = {slot: record for slot, record in records.items() if record.intent.side is OrderSide.BUY}
        buy_fills = [event for slot in buy_records for event in self.replay.orders.read_events(
            OrderSide.BUY, symbol=self.symbol, slot=slot) if event['event_type'] == 'FILL']
        bought = sum(event['quantity'] for event in buy_fills)
        sold = sum(record.detailed_filled_quantity for record in records.values() if record.intent.side is OrderSide.SELL)
        complete = (bought == sum(record.detailed_filled_quantity for record in buy_records.values())
                    and all(record.filled_quantity == record.detailed_filled_quantity for record in records.values()))
        notional = sum(event['price'] * event['quantity'] for event in buy_fills)
        opened = min((aware_iso_kst(event['occurred_at']) for event in buy_fills), default=None)
        return bought - sold, sold, notional, notional // bought if bought else None, opened, complete

    def _position_action(self, position, bar, entry, market_price):
        held, reduced, _, price, opened, complete = position
        if not complete or held <= 0 or entry is None or market_price is None:
            return 'HOLD', 0
        setup = entry[0]
        if self.family == LEADER_BREAKOUT_FAMILY_ID:
            exit_due = market_price <= setup.hard_stop_price or market_price < setup.invalidation_price
        elif self.family == PANIC_FAMILY:
            exit_due = (market_price <= setup.failure_price or market_price >= setup.target_price
                        or self.replay.now >= opened + timedelta(minutes=self.policy.max_hold_minutes))
        else:
            exit_due = market_price * 10_000 <= price * (10_000 - self.policy.protection_loss_bps)
            if not exit_due and bar is not None:
                decision = evaluate_close_and_ipo(self.policy, bar=bar, history=self.bars, calendar=self.calendar,
                    listings=self.listings, position=CloseIpoPosition(setup, held, price, reduced))
                self.reason = decision.reason
                return decision.action, decision.quantity
        return ('EXIT', held) if exit_due else ('HOLD', 0)

    @staticmethod
    def _stage_price_valid(step, market_price, buffer_bps):
        if step.action == 'ADD':
            return market_price * 10_000 > step.reference_price * (10_000 + buffer_bps)
        if step.trigger == 'price_at_or_above':
            return market_price >= step.reference_price
        return market_price <= step.reference_price

    def _leader_proposal(self, records, row, entry, progress, market_price):
        if 'trade' not in row or not self.stages or market_price is None:
            return None
        missing = next((slot for slot in self.stages if slot not in records), None)
        if missing is None or any(slot in records and not records[slot].broker_order_id for slot in self.stages):
            return None
        proof = progress.get('stage_proofs', {}).get(missing)
        if proof is not None and proof['at'] == self.replay.now.isoformat():
            return missing
        context = entry[0]
        raw, _ = evaluate_leader_breakout_trade(self.policy, row['trade'], received_at=self.replay.now,
            source='local-rehearsal', position_open=True, position_context=context)
        setup_keys = (leader_setup_key(context),) if leader_reentry_enabled(self.config) else ()
        emitted = (*setup_keys, *(leader_position_step_key(self.config, index, step, context=context)
                                  for slot, (index, step) in self.stages.items() if slot in records))
        state = SimpleNamespace(status='open', emitted_candidate_keys=emitted)
        observations = progress.setdefault('stage_observations', {})
        prior = observations.get(missing)
        if prior is not None and prior['last_available_at'] == self.replay.now.isoformat():
            return None
        proposal, after = evaluate_leader_position_step(self.config, raw, state, context, source='local-rehearsal',
            step_state=LeaderBreakoutState(**prior) if prior else None,
            entry_enabled=not self.replay.broker.state['entry_stopped'])
        if after is None:
            observations.pop(missing, None)
        else:
            observations[missing] = asdict(after)
        if proposal.get('position_step') is None:
            return None
        confirmation = proposal['position_step'].get('confirmation')
        deadline = self.replay.now + self.replay.signal_age
        if confirmation is not None:
            deadline = min(deadline, aware_iso_kst(confirmation['valid_until']))
        progress.setdefault('stage_proofs', {})[missing] = dict(
            at=self.replay.now.isoformat(), deadline=deadline.isoformat(), anchor=market_price,
        )
        return missing

    def _working_valid(self, slot, entry, market_price, route_price, action, progress, bar, trade):
        if slot == 'BUY':
            return self._entry_valid(entry, market_price, route_price)
        if slot == 'ipo-reduce':
            if bar is not None and market_price is not None:
                if action == 'WAIT':
                    return None
                if action != 'REDUCE':
                    return False
                progress['ipo_reduce_cut'] = self.replay.now.isoformat()
            if route_price is None or market_price is None or 'ipo_reduce_cut' not in progress:
                return None
            cut = aware_iso_kst(progress['ipo_reduce_cut'])
            if self.replay.now >= cut + self.replay.signal_age:
                return None
            return True if route_price * 10_000 >= progress['ipo_reduce_anchor'] * (
                10_000 - self.replay.protective_slippage_bps) else None
        _, step = self.stages[slot]
        proof = progress['stage_proofs'][slot]
        if self.replay.now >= aware_iso_kst(proof['deadline']):
            return False
        if step.action == 'ADD' and self.replay.broker.state['entry_stopped']:
            return False
        if market_price is None or route_price is None:
            return None
        if not self._stage_price_valid(step, market_price, self.policy.breakout_buffer_bps):
            return False
        if step.action == 'ADD':
            if trade is not None:
                turnover = trade.get('cumulative_trade_value')
                if type(turnover) is not int:
                    return None
                if abs(turnover) * 1_000_000 < self.policy.minimum_turnover_won:
                    return False
            return route_price * 10_000 <= proof['anchor'] * (10_000 + self.replay.slippage_bps)
        return route_price * 10_000 >= proof['anchor'] * (10_000 - self.replay.protective_slippage_bps)

    def _send(self, slot, valid, quote, records, position, quantity=None):
        side = self.slots[slot]
        price = quote['price'] if quote else None
        held, _, notional, _, _, complete = position
        if valid is True and not complete:
            valid = None
        if valid is True and side is OrderSide.BUY and price is not None:
            record = records.get(slot)
            remaining = execution_remaining_quantity(record) if record else quantity
            if remaining and notional + remaining * price > self.capital:
                valid = False
        if valid is None or (valid is False and slot not in records) or (valid is True and price is None):
            return
        if side is OrderSide.SELL and quantity is not None and quantity >= held:
            return
        self.replay.orders.step(side, symbol=self.symbol, slot=slot,
            entry_quantity=quantity if side is OrderSide.BUY else None,
            exit_quantity=quantity if side is OrderSide.SELL else None,
            current_conditions=lambda _: RehearsalConditions(self.symbol, self.replay.venue,
                aware_iso_kst(quote['available_at']) if quote else self.replay.now,
                MarketPhase(quote['phase']) if quote else MarketPhase.UNKNOWN,
                quote['eligible'] if quote else None, valid, price))

    def advance(self, row, bar):
        replay = self.replay
        records = replay.orders.read_plan(self.symbol)
        position = self._position(records)
        buy = records.get('BUY')
        entry = self.entries.get(buy.intent.created_at if buy else replay.now)
        progress = replay.plan_progress(self.symbol)
        quote, market = replay.quote(row, replay.destinations[self.symbol]), replay.quote(row, 'KRX')
        market_price, route_price = market['price'] if market else None, quote['price'] if quote else None
        action, quantity = self._position_action(position, bar, entry, market_price)
        if action == 'EXIT' and not progress.get('exit_observed_at'):
            progress['exit_observed_at'] = replay.now.isoformat()
            replay.broker.save()
        active = [slot for slot, record in records.items() if record.state not in TERMINAL_ORDER_STATES]
        if len(active) > 1:
            raise ValueError('한 종목에 여러 주문이 살아 있어 재생을 중지했습니다.')
        if progress.get('exit_observed_at'):
            if active and active[0] != 'SELL':
                self._send(active[0], False, quote, records, position)
            elif position[0] > 0 and route_price is not None:
                if 'exit_route_anchor' not in progress:
                    progress['exit_route_anchor'] = route_price
                    replay.broker.save()
                anchor = progress['exit_route_anchor']
                valid = route_price * 10_000 >= anchor * (10_000 - replay.protective_slippage_bps)
                self._send('SELL', True if valid else None, quote, records, position)
        elif active:
            slot = active[0]
            if slot == 'ipo-reduce' and not position[-1]:
                return
            valid = self._working_valid(slot, entry, market_price, route_price, action, progress, bar, row.get('trade'))
            self._send(slot, valid, quote, records, position, self.quantity if slot == 'BUY' else None)
        elif buy is None:
            self._send('BUY', self._entry_valid(entry, market_price, route_price), quote, records, position, self.quantity)
        elif position[0] > 0 and position[-1] and entry is not None:
            if self.ipo_reduce and action == 'REDUCE' and 'ipo-reduce' not in records and route_price is not None:
                progress.update(ipo_reduce_anchor=route_price, ipo_reduce_cut=replay.now.isoformat())
                replay.broker.save()
                self._send('ipo-reduce', True, quote, records, position, quantity)
            elif (slot := self._leader_proposal(records, row, entry, progress, market_price)) is not None:
                if quote is None:
                    return
                replay.broker.save()
                valid = self._working_valid(slot, entry, market_price, route_price, action, progress, bar, row.get('trade'))
                self._send(slot, valid, quote, records, position, self.stages[slot][1].quantity)


class LocalStrategyRehearsal:
    """Replay explicitly selected strategy plans against a single fake account per venue."""

    def __init__(self, source: Path, root: Path, venue: str, *, lease_now=kst_now, expected_tape_hash=None):
        with source.open('rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('로컬 리허설 입력이 4 MiB를 초과합니다.')
        tape_hash = sha256(raw).hexdigest()
        if expected_tape_hash is not None and tape_hash != expected_tape_hash:
            raise ValueError('격리 시세를 준비한 뒤 리허설 입력이 바뀌었습니다.')
        tape = json.loads(raw)
        self.venue = venue
        self.portfolio = tape['version'] == 'local-strategy-rehearsal/v2'
        if tape['version'] not in {'local-strategy-rehearsal/v1', 'local-strategy-rehearsal/v2'} or venue not in {'KRX', 'NXT', 'SOR'}:
            raise ValueError('지원하는 로컬 전략·거래소 입력을 선택하세요.')
        declarations = tape['plans'] if self.portfolio else [tape]
        self.plans = {plan.symbol: plan for plan in (_StrategyPlan(self, row) for row in declarations)}
        if not self.plans or len(self.plans) != len(declarations):
            raise ValueError('종목마다 하나의 전략 계획이 필요합니다.')
        self.destinations = tape.get('sor_destinations') if venue == 'SOR' else dict.fromkeys(self.plans, venue)
        if (not isinstance(self.destinations, dict) or self.destinations.keys() != self.plans.keys()
                or any(value not in {'KRX', 'NXT'} for value in self.destinations.values())):
            raise ValueError('모든 종목의 가짜 SOR 접수 거래소를 지정하세요.')
        first = next(iter(self.plans.values()))
        self.family, self.symbol = first.family, first.symbol
        self.capital = _positive(tape['capital_won']) if self.portfolio else first.capital
        frames = tape['frames']
        if not isinstance(frames, list) or not frames:
            raise ValueError('재생할 관측이 없습니다.')
        self.times = tuple(aware_iso_kst(row['at']) for row in frames)
        if any(a >= b for a, b in zip(self.times, self.times[1:])):
            raise ValueError('관측 시각은 중복 없이 순서대로 필요합니다.')
        self.frames = tuple(row['observations'] if self.portfolio else {self.symbol: row} for row in frames)
        self.signal_age = timedelta(seconds=_positive(tape['signal_valid_seconds']))
        self.maximum_quote_age = timedelta(seconds=_positive(tape['maximum_quote_age_seconds']))
        self.slippage_bps = tape['maximum_adverse_slippage_bps']
        self.protective_slippage_bps = tape['protective_slippage_bps']
        if any(type(value) is not int or not 0 <= value <= 10_000
               for value in (self.slippage_bps, self.protective_slippage_bps)):
            raise ValueError('명시한 최대 가격 이탈 범위가 필요합니다.')
        self._validate_frames()
        identity = f'{tape_hash}-{venue}'
        directory = root / identity
        directory.mkdir(parents=True, exist_ok=True)
        self.now = self.times[0]
        account = f'local-{identity[:16]}-{venue}'
        self.broker = _ReplayBroker(directory / 'fake-broker.json', account, self.plans, venue, self.capital,
                                    lambda: self.now, self.destinations)
        scope = {'plan_slots': {symbol: plan.slots for symbol, plan in self.plans.items()}} if self.portfolio else {'symbol': self.symbol}
        self.orders = LocalOrderRehearsal(directory / 'orders', self.broker, account_ref=account, run_id=identity,
            venue=venue, sessions=tuple(MarketSession(value) for value in tape['sessions'][venue]),
            maximum_age=self.maximum_quote_age, now_provider=lambda: self.now, lease_now_provider=lease_now, **scope)
        try:
            self.broker.reload()
            if not 0 <= self.cursor <= len(self.frames):
                raise ValueError('저장된 재생 위치를 확인할 수 없습니다.')
            if self.cursor:
                self.now = self.times[self.cursor - 1]
            for index in range(self.cursor):
                for symbol, row in self.frames[index].items():
                    self.plans[symbol].observe(index, row, self.times[index])
        except BaseException:
            self.close()
            raise

    def _validate_frames(self):
        for frame, cut in zip(self.frames, self.times):
            if not isinstance(frame, dict) or not frame or set(frame) - self.plans.keys():
                raise ValueError('등록한 종목의 관측만 재생할 수 있습니다.')
            for symbol, row in frame.items():
                for key, quote in row['quotes'].items():
                    available = aware_iso_kst(quote['available_at'])
                    if (key not in {'KRX', 'NXT'} or available > cut
                            or (quote['eligible'] is not None and type(quote['eligible']) is not bool)):
                        raise ValueError('거래소 호가의 시점 또는 대상 확인값이 잘못됐습니다.')
                    _positive(quote['price'])
                    MarketPhase(quote['phase'])
                for side, quantity in row.get('fills', {}).items():
                    OrderSide(side)
                    _positive(quantity)
                if 'bar' in row:
                    bar = KrxMinuteBarFrame(**row['bar'])
                    if bar.code != symbol or aware_iso_kst(bar.available_at) != cut:
                        raise ValueError('전략 봉의 종목과 가용 시각이 현재 관측과 맞지 않습니다.')
                if 'trade' in row and (row['trade'].get('code'), row['trade'].get('market')) != (symbol, 'KRX'):
                    raise ValueError('돌파 관측에는 같은 종목의 원래 KRX 체결이 필요합니다.')

    @property
    def cursor(self):
        return self.broker.state['cursor']

    @property
    def done(self):
        return self.cursor >= len(self.frames)

    def close(self):
        self.orders.__exit__(None, None, None)

    def status(self):
        account = self.broker.read_recovery().account
        plans = {}
        for symbol, plan in self.plans.items():
            records = self.orders.read_plan(symbol)
            plans[symbol] = dict(family=plan.family, held=account.positions.get(symbol, 0), reason=plan.reason,
                BUY=records['BUY'].state.value if 'BUY' in records else None,
                SELL=records['SELL'].state.value if 'SELL' in records else None,
                stages={slot: dict(state=record.state.value, filled=record.detailed_filled_quantity,
                    action='부분 청산' if record.intent.side is OrderSide.SELL else '추가 매수')
                        for slot, record in records.items() if slot not in {'BUY', 'SELL'}})
        result = dict(cursor=self.cursor, total=len(self.frames), venue=self.venue,
            commands=len(self.broker.state['commands']), entry_stopped=self.broker.state['entry_stopped'], plans=plans,
            available_cash=account.available_cash_won, reserved_cash=account.reserved_open_buy_won)
        if not self.portfolio:
            result.update(plans[self.symbol])
        return result

    def quote(self, row, venue):
        quote = row['quotes'].get(venue)
        if quote is not None and self.now - aware_iso_kst(quote['available_at']) <= self.maximum_quote_age:
            return quote
        return None

    def plan_progress(self, symbol):
        if not self.portfolio:
            return self.broker.state
        return self.broker.state.setdefault('plans', {}).setdefault(symbol, {})

    def step(self):
        if self.done:
            return self.status()
        self.orders.renew_ownership()
        index = self.cursor
        frame, self.now = self.frames[index], self.times[index]
        self.broker.prepare(index, {symbol: row.get('fills', {}) for symbol, row in frame.items()})
        _, covered = self.orders.reconcile()
        sent = {command['body']['stk_cd'] for command in self.broker.state['commands'] if command.get('frame') == index}
        for symbol, plan in self.plans.items():
            row = frame.get(symbol)
            if row is None:
                continue
            bar = plan.observe(index, row, self.now)
            if covered and symbol not in sent:
                plan.advance(row, bar)
        self.broker.state['cursor'] = index + 1
        self.broker.save()
        return self.status()

    def stop_entry(self):
        self.orders.renew_ownership()
        self.broker.state['entry_stopped'] = True
        self.broker.save()

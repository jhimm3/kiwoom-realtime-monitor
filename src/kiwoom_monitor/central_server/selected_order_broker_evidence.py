"""Server-owned dated order/detail comparison through the existing mock broker.

kt00007 supplies dated original order facts; kt00009 supplies actual execution
IDs and times. ka10075/76 supply a current aggregate cross-check, never missing
execution IDs or an historical order date. No authority or background work.
"""
from __future__ import annotations

from datetime import datetime, time
import hashlib
import json
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.selected_order_recovery import DatedBrokerEvidence, DatedBrokerOrderEvidence
from kiwoom_monitor.domain.order_contract import BrokerFill
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.domain.selected_order_evidence import selected_physical_order_facts, PLAN_MANAGEMENT_VERSION
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import (
    KiwoomMockAccountReader, parse_unfilled_orders, parse_filled_orders, merge_order_snapshots,
    UNFILLED_COLUMNS, FILLED_COLUMNS,
)


KST = ZoneInfo('Asia/Seoul')
DETAIL_COLUMNS = ('ord_no', 'stk_cd', 'trde_tp', 'crd_tp', 'ord_qty', 'ord_uv', 'cnfm_qty', 'acpt_tp',
    'rsrv_tp', 'ord_tm', 'ori_ord', 'stk_nm', 'io_tp_nm', 'loan_dt', 'cntr_qty', 'cntr_uv', 'ord_remnq',
    'comm_ord_tp', 'mdfy_cncl', 'cnfm_tm', 'dmst_stex_tp', 'cond_uv')
FILL_COLUMNS = ('stk_bond_tp', 'ord_no', 'stk_cd', 'trde_tp', 'io_tp_nm', 'ord_qty', 'ord_uv',
    'cnfm_qty', 'rsrv_oppo', 'cntr_no', 'acpt_tp', 'orig_ord_no', 'stk_nm', 'setl_tp', 'crd_deal_tp',
    'cntr_qty', 'cntr_uv', 'comm_ord_tp', 'mdfy_cncl_tp', 'cntr_tm', 'dmst_stex_tp', 'cond_uv')


def _rows(pages, name, columns):
    result = []
    for page in pages:
        if page.get('return_code') not in (None, 0, '0'):
            raise ValueError('dated broker API did not accept this query')
        values = page.get(name)
        if not isinstance(values, list): raise ValueError('dated broker table is incomplete')
        for value in values:
            if isinstance(value, dict): result.append(value)
            elif isinstance(value, (list, tuple)) and len(value) == len(columns):
                result.append(dict(zip(columns, value, strict=True)))
            else: raise ValueError('dated broker row shape is incomplete')
    return result


def _number(value, *, positive=False):
    text = str(value if value is not None else '').strip().replace(',', '')
    if not text or not text.lstrip('+').isdigit(): raise ValueError('dated broker numeric fact is unavailable')
    number = int(text)
    if number < int(positive): raise ValueError('dated broker numeric fact is unavailable')
    return number


def _parent(value):
    text = str(value or '').strip()
    return text if text and set(text) != {'0'} else None


def _basis(row, parent):
    symbol = normalize_stock_code(row.get('stk_cd'))
    raw_side = str(row.get('io_tp_nm') or '').strip().lstrip('+-')
    side = 'BUY' if '매수' in raw_side else 'SELL' if '매도' in raw_side else raw_side if raw_side in {'BUY', 'SELL'} else None
    venue = str(row.get('dmst_stex_tp') or '').strip()
    if len(symbol) != 6 or side is None or venue not in {'KRX', 'NXT', 'SOR'}:
        raise ValueError('dated broker original order basis is unavailable')
    return (_parent(row.get(parent)), symbol, venue, side,
        _number(row.get('ord_qty'), positive=True), _number(row.get('ord_uv'), positive=True))


def _occurred(value, day):
    text = str(value or '').strip().replace(':', '')
    if len(text) != 6 or not text.isdigit(): raise ValueError('dated broker execution time is unavailable')
    return datetime.combine(day, time(int(text[:2]), int(text[2:4]), int(text[4:6])), KST)


async def read_kiwoom_selected_order_evidence(*, bundle, identity, packet, source_evidence=None):
    """Consume only owner-verified source dates and this exact current broker epoch."""
    started = bundle.runtime.server_now()
    reasons, orders = [], []
    def result(coverage):
        completed = bundle.runtime.server_now()
        basis = dict(coverage=coverage, dates=[day.isoformat() for day in days],
            orders=[order.to_dict() for order in orders], reasons=sorted(set(reasons)))
        digest = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return DatedBrokerEvidence(identity.target_account_ref, identity.credential_profile_id,
            identity.credential_revision, identity.binding_revision, started, completed, tuple(days), coverage,
            'kiwoom-dated-order-evidence/v1:' + ','.join(sorted(set(reasons))) + ':' + digest, tuple(orders))
    expected = (selected_physical_order_facts(packet) if packet.get('version') == PLAN_MANAGEMENT_VERSION
        else packet['order'].get('broker_legs') or [packet['order']])
    selected = {leg['broker_order_id'] for leg in expected}
    transfers = {leg['broker_order_id']: leg.get('transferred_quantity', 0) for leg in expected}
    days = []
    if not isinstance(source_evidence, DatedBrokerEvidence) or source_evidence.account_ref != packet['account_ref']:
        reasons.append('SOURCE_DATED_SCOPE_UNAVAILABLE')
        return result('UNAVAILABLE')
    days = sorted({order.trading_date for order in source_evidence.orders if order.broker_order_id in selected
        and order.trading_date is not None and order.trading_date in source_evidence.covered_dates})
    if not days:
        reasons.append('SOURCE_DATES_UNAVAILABLE')
        return result('UNAVAILABLE')
    reader = KiwoomMockAccountReader(bundle.broker, environment='mock', account_ref=identity.target_account_ref,
        now_provider=bundle.runtime.server_now)
    try:
        open_pages, open_at, filled_pages, filled_at = await reader.read_order_pages(strict_continuation=True)
        _rows(open_pages, 'oso', UNFILLED_COLUMNS)
        _rows(filled_pages, 'cntr', FILLED_COLUMNS)
        details = []
        open_orders = tuple(order for page in open_pages for order in parse_unfilled_orders(page, identity.target_account_ref, open_at,
            _details=details, _default_venue='KRX'))
        filled_orders = tuple(order for page in filled_pages for order in parse_filled_orders(page, identity.target_account_ref, filled_at,
            _details=details, _default_venue='KRX'))
        current = merge_order_snapshots(open_orders, filled_orders)
        current = {order.broker_order_id: order for order in current}
        explicit_aggregates = {order.broker_order_id: order.filled_quantity for order in merge_order_snapshots(filled_orders)}
        for day in days:
            summaries = _rows(await reader.read_dated_order_pages('kt00007', day, symbol=packet['order']['symbol']),
                'acnt_ord_cntr_prps_dtl', DETAIL_COLUMNS)
            executions = _rows(await reader.read_dated_order_pages('kt00009', day, symbol=packet['order']['symbol']),
                'acnt_ord_cntr_prst_array', FILL_COLUMNS)
            for row in executions:
                if (str(row.get('ord_no') or '').strip() not in selected
                        and _parent(row.get('orig_ord_no')) in selected):
                    cancellation = '취소' in str(row.get('mdfy_cncl_tp') or '')
                    if not cancellation or _number(row.get('cntr_qty')) > 0:
                        reasons.append('UNSELECTED_CHILD_ORDER_PRESENT')
            grouped = {}
            for row in summaries:
                number = str(row.get('ord_no') or '').strip()
                if number in selected: grouped.setdefault(number, []).append(row)
                elif _parent(row.get('ori_ord')) in selected:
                    cancellation = '취소' in str(row.get('mdfy_cncl') or '')
                    has_execution = any(str(item.get('ord_no') or '').strip() == number
                        and _number(item.get('cntr_qty')) > 0 for item in executions)
                    if not cancellation or has_execution: reasons.append('UNSELECTED_CHILD_ORDER_PRESENT')
            for number, rows in grouped.items():
                bases = {_basis(row, 'ori_ord') for row in rows}
                if len(bases) != 1:
                    reasons.append('DATED_ORIGINAL_ORDER_CONFLICT')
                    continue
                parent, symbol, venue, side, quantity, price = next(iter(bases))
                fills = []
                for row in executions:
                    if str(row.get('ord_no') or '').strip() != number: continue
                    if _basis(row, 'orig_ord_no') != (parent, symbol, venue, side, quantity, price):
                        reasons.append('DATED_EXECUTION_ORDER_BASIS_CONFLICT')
                        continue
                    amount = _number(row.get('cntr_qty'))
                    if not amount: continue
                    execution_id = str(row.get('cntr_no') or '').strip()
                    if not execution_id:
                        reasons.append('EXECUTION_ID_UNAVAILABLE')
                        continue
                    fills.append(BrokerFill(execution_id, amount, _number(row.get('cntr_uv'), positive=True),
                        _occurred(row.get('cntr_tm'), day)))
                snapshot = current.get(number)
                if snapshot is not None:
                    if number in explicit_aggregates: reported = explicit_aggregates[number]
                    elif len(rows) == 1: reported = _number(rows[0].get('cntr_qty'))
                    else:
                        reasons.append('DATED_AGGREGATE_UNAVAILABLE')
                        continue
                    remaining = snapshot.remaining_quantity
                    converted = dict(ord_no=number, stk_cd=symbol, ord_qty=str(quantity),
                        cntr_qty=str(reported), oso_qty=str(remaining))
                    state = (snapshot.state.value if snapshot.state.value in {'CANCELLED', 'CANCEL_PENDING', 'REJECTED'}
                        else parse_filled_orders({'cntr': [converted]}, identity.target_account_ref, filled_at)[0].state.value)
                    matching = [item for item in details if item.broker_order_id == number]
                    if (day != started.astimezone(KST).date() or snapshot.symbol != symbol or not matching or any(
                            (item.original_broker_order_id, symbol, item.venue, item.side.value if item.side else None,
                                item.ordered_quantity, item.limit_price) != (parent, symbol, venue, side, quantity, price)
                            for item in matching)):
                        state = 'UNVERIFIED'
                        reasons.append('CURRENT_ORDER_INCARNATION_UNVERIFIED')
                    if (state in {'ACCEPTED', 'PARTIALLY_FILLED'}
                            and reported + remaining + transfers.get(number, 0) != quantity):
                        state = 'UNVERIFIED'
                        reasons.append('CURRENT_ORDER_ALLOCATION_UNVERIFIED')
                    if sum(fill.quantity for fill in fills) > reported and len({fill.execution_id for fill in fills}) == len(fills):
                        reasons.append('ORDER_CUT_CHANGED_DURING_QUERY')
                        continue
                elif len(rows) == 1:
                    reported, remaining = _number(rows[0].get('cntr_qty')), _number(rows[0].get('ord_remnq'))
                    converted = dict(ord_no=number, stk_cd=symbol, ord_qty=str(quantity), cntr_qty=str(reported),
                        oso_qty=str(remaining), ord_stt=str(rows[0].get('acpt_tp') or '') + str(rows[0].get('mdfy_cncl') or ''))
                    state = parse_filled_orders({'cntr': [converted]}, identity.target_account_ref, filled_at)[0].state.value
                    if remaining or (not reported and '취소' not in converted['ord_stt'] and '거부' not in converted['ord_stt']):
                        state = 'UNVERIFIED'
                        reasons.append('CURRENT_ORDER_STATE_UNAVAILABLE')
                else:
                    reasons.append('DATED_AGGREGATE_UNAVAILABLE')
                    continue
                if len({fill.execution_id for fill in fills}) != len(fills):
                    reasons.append('DUPLICATE_EXECUTION_ID')
                orders.append(DatedBrokerOrderEvidence(number, day, parent, symbol, venue, side,
                    quantity, price, reported, remaining, state, tuple(fills)))
    except (ValueError, TypeError, KeyError, AttributeError):
        reasons.append('BROKER_DATED_COVERAGE_UNAVAILABLE')
        return result('PARTIAL' if orders else 'UNAVAILABLE')
    # Transport/API refusal must retain UNKNOWN rather than changing environment
    # or pretending a missing historical response is an empty complete day.
    except Exception:
        reasons.append('BROKER_DATED_QUERY_UNAVAILABLE')
        return result('PARTIAL' if orders else 'UNAVAILABLE')
    if {order.broker_order_id for order in orders} != selected: reasons.append('SELECTED_ORDER_COVERAGE_INCOMPLETE')
    return result('PARTIAL' if reasons else 'FULL')

"""Command-level causal evidence for incremental registrations; no live owner."""
from __future__ import annotations

from functools import wraps

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_registration import (
    RegistrationCommand, RegistrationPlan, RegistrationProgress, all_atoms,
    encode_groups, group_atoms, ready_codes,
)
from .diagnostic_replay_contract import freeze_payload, thaw_payload

VERSION = 'top20-realtime-tape/v2'
PUBLIC_TYPES = {'0B', '0w', '0g', '0J', '0U', '0s', '1h'}


def observer_only(function):
    @wraps(function)
    def run(collector, *args, **kwargs):
        try:
            from . import diagnostic_trace as trace
            if trace.input_token('top20_inputs') is None or collector._input_replay_only:
                return None
            return function(collector, *args, **kwargs)
        except Exception:
            from .diagnostic_top20_lifecycle_input import reject
            reject(collector, 'top20_v2_observer_invalid')
            collector._recorded_v2_complete = False
            return None
    return run


def _receipt(collector, receipt, *, complete=True):
    from .realtime_hub import CapturedControlReceipt
    collector._recorded_v2_complete = bool(
        getattr(collector, '_recorded_v2_complete', True) and complete and receipt and receipt.complete)
    if receipt and not collector._recorded_v2_complete:
        return CapturedControlReceipt(receipt.trace_id,receipt.producer_component,
            receipt.input_id,receipt.kind,False)
    return receipt


def public_groups(groups):
    return {group: [part for part in parts if not set(part['type']) & {'00', '04'}]
            for group, parts in groups.items()}


def public_command(command):
    packet = command.packet()
    packet['data'] = public_groups({command.group: packet['data']})[command.group]
    packet['excluded_types'] = sorted({kind for kind, _ in command.atoms} & {'00', '04'})
    return packet


@observer_only
def begin_batch(collector, request, plan, revision):
    from .diagnostic_top20_lifecycle_input import _emit, contributions, hub_initial
    from .realtime_collector import REALTIME_REG_INTERVAL_SECONDS, SUBSCRIPTION_ACK_TIMEOUT_SECONDS
    hub_initial(collector._hub)
    collector._recorded_v2_complete = True
    return _receipt(collector, _emit(collector, 'subscription_start', {
        **request, 'hub_component': f'realtime_hub:{id(collector._hub):x}',
        'generation': collector._connection_generation, 'revision': revision,
        'registration_interval_seconds': REALTIME_REG_INTERVAL_SECONDS,
        'ack_timeout_seconds': SUBSCRIPTION_ACK_TIMEOUT_SECONDS,
        'subscriptions': contributions(collector._hub),
        'previous_groups': public_groups(plan.previous), 'groups': public_groups(plan.groups),
        'commands': [public_command(command) for command in plan.commands],
    }))


@observer_only
def command_event(collector, kind, batch, command, ordinal, *, parent=None, message=None, sent_mono_ns=None):
    from .diagnostic_top20_lifecycle_input import _emit
    if batch is None:
        return None
    value = {'subscription_input_id': batch.input_id, 'ordinal': ordinal,
             'command': public_command(command),
             'command_input_id': parent.input_id if parent else ''}
    if kind == 'command_sent':
        value['send_completed_mono_ns'] = sent_mono_ns
    if message is not None:
        code = message.get('return_code')
        value.update({'trnm': str(message.get('trnm', '')).upper(),
                      'return_code': code if type(code) in {int, str} else None,
                      'group_no': str(message.get('grp_no', '')),
                      'accepted': type(code) is int and code == 0 or type(code) is str and code == '0'})
    return _receipt(collector, _emit(collector, kind, value),
                    complete=batch.complete and (parent is None or parent.complete))


@observer_only
def end_batch(collector, batch, groups, *, outcome='returned', error_type=''):
    from .diagnostic_top20_lifecycle_input import _emit
    if batch is None:
        return None
    return _receipt(collector, _emit(collector, 'batch_end', {'subscription_input_id': batch.input_id,
                 'outcome': outcome, 'groups': public_groups(groups), 'error_type': error_type}))


@observer_only
def projection(collector, batch, groups, codes, *, desired, previous=None):
    from .diagnostic_top20_lifecycle_input import _emit, contributions
    if batch is None:
        return None
    return _receipt(collector, _emit(collector, 'approval_projection', {
        'subscription_input_id': batch.input_id, 'previous_approval_input_id': previous.input_id if previous else '',
        'groups': public_groups(groups), 'codes': list(codes),
        'desired_groups':public_groups(desired),'subscriptions':contributions(collector._hub)}),
        complete=batch.complete and bool(previous and previous.complete))


def _validate_groups(groups):
    if type(groups) is not dict or len(groups) > 100:
        raise ValueError('top20_v2_groups_invalid')
    for group,parts in groups.items():
        if type(group) is not str or type(parts) is not list:
            raise ValueError('top20_v2_groups_invalid')
        for part in parts:
            if (type(part) is not dict or set(part) != {'item','type'}
                    or type(part['item']) is not list or len(part['item'])>100
                    or type(part['type']) is not list or not part['type']
                    or any(type(kind) is not str or kind not in PUBLIC_TYPES for kind in part['type'])
                    or any(type(item) is not str or len(item)>32 for item in part['item'])):
                raise ValueError('top20_v2_groups_invalid')
    atoms = all_atoms(groups)
    if any(kind not in PUBLIC_TYPES or item is not None and
           (type(item) is not str or len(item)>32) for kind,item in atoms):
        raise ValueError('top20_v2_groups_invalid')
    group_atoms(groups)


def _packet_command(packet):
    if (type(packet) is not dict or packet.get('trnm') not in {'REG', 'REMOVE'}
            or packet.get('refresh') != ('1' if packet['trnm'] == 'REG' else None)
            or set(packet) - {'trnm', 'grp_no', 'data', 'refresh', 'excluded_types'}
            or type(packet.get('excluded_types')) is not list
            or set(packet['excluded_types']) - {'00', '04'}):
        raise ValueError('top20_v2_command_invalid')
    _validate_groups({packet['grp_no']:packet['data']})
    atoms = group_atoms({packet['grp_no']: packet['data']}).get(packet['grp_no'], set())
    if any(kind not in PUBLIC_TYPES for kind, _ in atoms):
        raise ValueError('top20_v2_command_private_type')
    return RegistrationCommand(packet['trnm'], packet['grp_no'], frozenset(atoms))


def read_v2(events):
    from .diagnostic_top20_lifecycle_input import (
        EVENT, MAX_EVENTS, MAX_BYTES, _codes, _validate_contributions, CONTROL_EVENTS,
    )
    if not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError('top20_realtime_event_limit')
    records, subscriptions, initial, topology, ready = {}, {}, {}, {}, {}
    previous_seq, charge, epochs = 0, 0, set()
    active, state, generations, approvals = {}, {}, {}, {}
    current_ready, used_approvals = {}, set()
    for row in events:
        if type(row) is not dict or type(row.get('seq')) is not int or row['seq'] <= previous_seq:
            raise ValueError('top20_realtime_sequence_invalid')
        previous_seq = row['seq']
        if row.get('event_type') == 'input_rejected':
            raise ValueError('top20_realtime_rejected_input')
        if row.get('event_type') != EVENT:
            continue
        identifier, component = row.get('input_id'), row.get('producer_component')
        if (row.get('input_version') != VERSION or type(identifier) is not str or not identifier
                or identifier in records or type(component) is not str or not component
                or type(row.get('trace_id')) is not str or not row['trace_id']
                or type(row.get('entered_mono_ns')) is not int or row['entered_mono_ns'] < 0):
            raise ValueError('top20_realtime_identity_invalid')
        epochs.add(row['trace_id'])
        charge += freeze_payload(row.get('payload'), maximum_bytes=MAX_BYTES-charge).charge
        value = thaw_payload(row.get('payload'))
        if type(value) is not dict:
            raise ValueError('top20_realtime_payload_invalid')
        kind = row.get('input_kind')
        records[identifier] = {'row': row, 'value': value}
        if kind in {'hub_initial', 'subscription_intent', 'subscription_start'}:
            _validate_contributions(value.get('subscriptions'))
        if kind == 'hub_initial':
            if component in initial or type(value.get('upstream_ready')) is not bool:
                raise ValueError('top20_realtime_initial_invalid')
            _codes(value.get('upstream_codes', ()))
            initial[component], topology[component] = identifier, value['subscriptions']
        elif kind == 'subscription_intent':
            if component not in initial or value.get('action') not in {'connect','update','disconnect'}:
                raise ValueError('top20_realtime_intent_without_initial')
            before = {item['subscriber_id']: item for item in topology[component]}
            after = {item['subscriber_id']: item for item in value['subscriptions']}
            subscriber, action = value.get('subscriber_id'), value['action']
            if (type(subscriber) is not str
                    or any(before.get(key) != after.get(key) for key in (set(before)|set(after))-{subscriber})
                    or action == 'connect' and (subscriber in before or subscriber not in after)
                    or action == 'update' and (subscriber not in before or subscriber not in after)
                    or action == 'disconnect' and (subscriber not in before or subscriber in after)
                    or after.get(subscriber,before.get(subscriber,{})).get('component') != value.get('component')):
                raise ValueError('top20_realtime_intent_transition_invalid')
            topology[component] = value['subscriptions']
        elif kind == 'subscription_start':
            hub = value.get('hub_component')
            if (hub not in initial or value['subscriptions'] != topology[hub]
                    or value.get('session') not in {'KRX','NXT'}
                    or type(value.get('generation')) is not int or value['generation'] < 0
                    or type(value.get('revision')) is not int or value['revision'] < 1
                    or component in active):
                raise ValueError('top20_realtime_subscription_initial_missing')
            for key in ('codes','nxt_codes','program_codes','priority_codes'):
                _codes(value.get(key, ()))
            before, after = value.get('previous_groups'), value.get('groups')
            _validate_groups(before)
            _validate_groups(after)
            if tuple(value['codes']) != ready_codes(after,all_atoms(after),value['codes']):
                raise ValueError('top20_v2_required_trade_sources_missing')
            previous_generation = generations.get(component)
            if previous_generation == value['generation'] and state.get(component) != before:
                raise ValueError('top20_v2_previous_state_mismatch')
            if previous_generation != value['generation'] and all_atoms(before):
                raise ValueError('top20_v2_prefix_unavailable')
            packets = value.get('commands')
            if (type(value.get('registration_interval_seconds')) not in {int,float}
                    or not 0 <= value['registration_interval_seconds'] <= 60
                    or type(value.get('ack_timeout_seconds')) not in {int,float}
                    or not 0 < value['ack_timeout_seconds'] <= 60):
                raise ValueError('top20_v2_timing_policy_invalid')
            if type(packets) is not list or not 1 <= len(packets) <= 100:
                raise ValueError('top20_v2_commands_invalid')
            commands = tuple(_packet_command(packet) for packet in packets)
            # Public account-only commands still have a real ordinal/ACK boundary.
            progress = RegistrationProgress(RegistrationPlan(before, after, commands))
            subscriptions[identifier] = {'row':row, 'start':value, 'acks':[], 'commands':[],
                'progress':progress, 'pending':None, 'end':None, 'completed_ids':set(),
                'last_sent_ns':None, 'started_ns':None}
            active[component] = identifier
            generations[component] = value['generation']
        elif kind in {'command_start','command_sent','command_ack'}:
            batch = subscriptions.get(value.get('subscription_input_id'))
            if not batch or active.get(component) != value['subscription_input_id']:
                raise ValueError('top20_v2_command_without_batch')
            progress = batch['progress']
            if (progress.done or type(value.get('ordinal')) is not int or value['ordinal'] != progress.index+1
                    or value.get('command') != batch['start']['commands'][progress.index]):
                raise ValueError('top20_v2_command_order_invalid')
            if kind == 'command_start':
                previous_sent = batch['last_sent_ns']
                if (row['entered_mono_ns'] < batch['row']['entered_mono_ns']
                        or previous_sent is not None and row['entered_mono_ns']-previous_sent
                           < batch['start']['registration_interval_seconds']*1e9):
                    raise ValueError('top20_v2_pacing_invalid')
                progress.start()
                batch['started_ns'] = row['entered_mono_ns']
                batch['pending'] = identifier
                batch['commands'].append(identifier)
            else:
                if value.get('command_input_id') != batch['pending']:
                    raise ValueError('top20_v2_command_parent_invalid')
                if row['entered_mono_ns'] < batch['started_ns']:
                    raise ValueError('top20_v2_command_time_invalid')
                if kind == 'command_sent':
                    sent_ns = value.get('send_completed_mono_ns')
                    if type(sent_ns) is not int or not batch['started_ns'] <= sent_ns <= row['entered_mono_ns']:
                        raise ValueError('top20_v2_send_time_invalid')
                    batch['last_sent_ns'] = sent_ns
                    committed = progress.mark_sent()
                else:
                    if value.get('accepted') and row['entered_mono_ns']-batch['started_ns'] > batch['start']['ack_timeout_seconds']*1e9:
                        raise ValueError('top20_v2_ack_timeout')
                    batch['acks'].append(identifier)
                    message = {'trnm': value.get('trnm'), 'return_code': value.get('return_code')}
                    if value.get('group_no'):
                        message['grp_no'] = value['group_no']
                    try:
                        committed = progress.accept_ack(message)
                    except RuntimeError:
                        if value.get('accepted') is not False:
                            raise ValueError('top20_v2_ack_invalid')
                        batch['failed'] = True
                        committed = False
                    if type(value.get('accepted')) is not bool or value['accepted'] != progress.acked and not committed:
                        if not batch.get('failed'):
                            raise ValueError('top20_v2_ack_invalid')
                if committed:
                    batch['completed_ids'].add(batch['pending'])
                    batch['pending'] = None
        elif kind == 'batch_end':
            batch = subscriptions.get(value.get('subscription_input_id'))
            if not batch or active.get(component) != value['subscription_input_id'] or batch['end']:
                raise ValueError('top20_v2_batch_end_invalid')
            outcome = value.get('outcome')
            if outcome not in {'returned','failed','superseded'}:
                raise ValueError('top20_v2_batch_outcome_invalid')
            if outcome in {'returned','superseded'}:
                if not batch['progress'].done or batch.get('failed') or value.get('groups') != batch['start']['groups']:
                    raise ValueError('top20_v2_batch_incomplete')
                state[component] = value['groups']
            batch['end'], batch['end_row'] = value, row
            active.pop(component)
            if outcome == 'returned':
                approvals[identifier] = (batch['start']['hub_component'], batch['start']['codes'])
        elif kind == 'approval_projection':
            batch = subscriptions.get(value.get('subscription_input_id'))
            if (not batch or batch['row']['producer_component'] != component
                    or active.get(component) != value['subscription_input_id']):
                raise ValueError('top20_v2_projection_invalid')
            parent = approvals.get(value.get('previous_approval_input_id'))
            retained = all_atoms(batch['start']['previous_groups']) & all_atoms(batch['start']['groups'])
            _validate_groups(value.get('groups',{}))
            _validate_groups(value.get('desired_groups'))
            _validate_contributions(value.get('subscriptions'))
            if (not parent or parent[0] != batch['start']['hub_component']
                    or value['subscriptions'] != topology[parent[0]]
                    or not all_atoms(value.get('groups', {})).issubset(retained)
                    or tuple(value.get('codes',())) != ready_codes(value['desired_groups'],all_atoms(value['groups']),value.get('codes',()))
                    or not set(_codes(value.get('codes', ()))).issubset(parent[1])):
                raise ValueError('top20_v2_projection_without_approval')
            approvals[identifier] = (parent[0], value['codes'])
        elif kind == 'upstream_ready':
            if component not in initial or type(value.get('ready')) is not bool:
                raise ValueError('top20_realtime_ready_initial_missing')
            _codes(value.get('codes', ()))
            if value['ready'] and (not value.get('approval_complete')
                    or value.get('approval_input_id') in used_approvals
                    or approvals.get(value.get('approval_input_id')) != (component,value['codes'])):
                raise ValueError('top20_realtime_ready_without_approval')
            if value['ready']:
                used_approvals.add(value['approval_input_id'])
            ready[identifier] = value
            current_ready[component] = identifier
        elif kind == 'hub_event':
            event = value.get('event')
            if type(event) is not dict or event.get('type') not in CONTROL_EVENTS:
                raise ValueError('top20_realtime_control_event_invalid')
            if 'source_resets' in event:
                if (type(event['source_resets']) is not list or any(type(source) is not dict
                        or set(source) != {'code','market'} or type(source['code']) is not str
                        or not source['code'] or len(source['code'])>32
                        or source['market'] not in {'KRX','NXT','SOR'} for source in event['source_resets'])):
                    raise ValueError('top20_v2_source_reset_invalid')
            if event['type'] in {'subscription_ready','codes_added','connection_opened'}:
                parent = ready.get(value.get('ready_input_id'))
                if not parent or not parent['ready'] or current_ready.get(component) != value['ready_input_id']:
                    raise ValueError('top20_realtime_control_without_ready')
        elif kind in {'market_operation','capture_gap'}:
            from datetime import datetime
            if not isinstance(value.get('source_time'), datetime):
                raise ValueError('top20_realtime_source_time_invalid')
            if kind == 'capture_gap' and type(value.get('clear_continuous')) is not bool:
                raise ValueError('top20_realtime_gap_invalid')
            if kind == 'market_operation':
                tick = value.get('tick')
                if (type(tick) is not dict or set(tick) != {'status_code','trade_time','remaining_time'}
                        or type(tick['status_code']) is not str or not tick['status_code']
                        or any(tick[key] is not None and type(tick[key]) is not str
                               for key in ('trade_time','remaining_time'))):
                    raise ValueError('top20_realtime_market_operation_invalid')
        else:
            raise ValueError('top20_realtime_kind_unsupported')
    if len(epochs) != 1 or active or not records:
        raise ValueError('top20_realtime_incomplete_or_epoch_mismatch')
    for batch in subscriptions.values():
        batch.pop('progress')
        batch.pop('completed_ids')
    return {'version': VERSION, 'trace_id': epochs.pop(), 'records': records,
            'subscriptions':subscriptions, 'initial':initial, 'top20_session_execution_ready':False}

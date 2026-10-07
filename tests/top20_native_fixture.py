"""Small native source recording for offline correctness gates only."""
import asyncio
import copy
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
from threading import Lock
import time
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner, freeze_payload
from kiwoom_monitor.central_server.diagnostic_replay_runtime import (
    ReplayRuntimeScope, close_top20_replay_resources, owned_create_task, owned_to_thread,
)
from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import record_ack
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector, _registered_trade_sources
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker


class FixtureSocket:
    async def send(self, value):
        pass


class FixtureClient:
    def __init__(self, origin):
        self.origin = origin

    def request_with_continuation(self, api_id, path, body, **kwargs):
        if api_id == 'ka00198':
            slot = self.origin.replace(second=self.origin.second // 30 * 30)
            payload = {'item_inq_rank': [{'dt': slot.strftime('%Y%m%d'), 'tm': slot.strftime('%H%M%S'),
                'stk_cd': f'{100000+rank:06d}', 'stk_nm': f'fixture-{rank}', 'bigd_rank': str(rank)}
                for rank in range(1, 21)]}
        elif api_id == 'ka10100':
            payload = {'nxtEnable': 'N'}
        elif api_id == 'ka10001':
            payload = {'mac': '1000', 'dstr_rt': '40'}
        else:
            payload = {}  # Recorded failure/empty preparation remains empty.
        return payload, False, ''


async def capture_native_top20_fixture(store, origin, *, capture_store=False, capture_seconds=0,
                                       include_peer=False, preparation_timeout=25):
    """Caller owns initialized source store; never uses a network transport."""
    events, roles, lock = [], {}, Lock()
    def scalar(token, kind, fields):
        with lock:
            events.append({'seq': len(events)+1, 'mono_ns': time.monotonic_ns(),
                           'event_type': kind, **copy.deepcopy(fields)})
        return True
    def emit(token, kind, fields, value):
        if kind == 'rest_input' and fields.get('input_kind') in {'logical_start', 'catalog_start'}:
            role, lane = asyncio.current_task().get_name(), value['lane']
            if roles.get(role, lane) != lane:
                raise AssertionError('fixture task role claimed multiple lanes')
            roles[role] = lane
        return scalar(token, kind, {**fields, 'payload': freeze_payload(value).value})
    clock, hub = Top20FixtureClock(origin), RealtimeHub()
    store._query_cache_wall_time = clock.wall_time
    ingestor = MarketDataIngestor(store, now_provider=clock.now)
    broker = CentralRestBroker(FixtureClient(origin), store, ingestor.ingest,
                              wall_time=clock.wall_time, ranking_reservation=True)
    service = AutonomousTop20Service(broker, hub, store, now_provider=clock.now,
        catalog_loader=lambda: tuple((f'{100000+i:06d}', f'fixture-{i}', 'KOSPI') for i in range(1, 21)),
        minute_backfill_enabled=False)
    ingestor._on_daily_change = service.notify_daily_bars_changed
    collector = CentralRealtimeCollector(lambda: '', 'real', hub, clock.now, store=store)
    runtime = ReplayRuntimeScope()
    enabled = {'top20_inputs', 'collector_inputs'} | ({'store_inputs'} if capture_store else set())
    with ExitStack() as stack:
        stack.enter_context(patch.object(trace, 'input_token', side_effect=lambda kind:
            'native-session-fixture' if kind in enabled else None))
        stack.enter_context(patch.object(trace, 'emit_payload', side_effect=emit))
        stack.enter_context(patch.object(trace, 'emit', side_effect=scalar))
        stack.enter_context(patch.object(trace, 'reject_input',
                                        side_effect=lambda token, fields: scalar(token, 'input_rejected', fields)))
        stack.enter_context(patch('kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS', 0))
        with runtime.activate():
            started = time.monotonic_ns()
            clock.arm()
            try:
                collector._snapshot_task = owned_create_task(collector._save_snapshots(),
                                                             name='kiwoom-realtime-snapshots')
                await service.start()
                async with asyncio.timeout(5):
                    while not hub.requested_codes()[0]:
                        await asyncio.sleep(.005)
                codes, nxt = hub.requested_codes()
                groups = await collector._send_subscription(FixtureSocket(), 'KRX', codes, nxt,
                    hub.requested_program_codes(), hub.requested_priority_codes())
                pending = len(groups)
                for group in groups:
                    ack = {'trnm': 'REG', 'grp_no': group, 'return_code': 0}
                    approval = record_ack(collector, ack)
                    pending = collector._accept_registration_ack(ack, pending, session='KRX', subscribed=codes,
                        sources=_registered_trade_sources(groups), added=codes, reset_all=True, approval=approval)
                if capture_store:
                    # Controlled correctness fixture: leave a real quiet setup
                    # prefix before the trade. Never shift a recorded input at
                    # replay time to hide a slow/missing native subscription.
                    await asyncio.sleep(max(0, 1-clock.elapsed()))
                collector._publish_parsed({'trnm': 'REAL', 'data': [
                    {'type': '0s', 'item': '', 'values': {'215': '3', '20': origin.strftime('%H%M%S'), '214': '0'}},
                    {'type': '0B', 'item': codes[0], 'values': {'20': origin.strftime('%H%M%S'), '10': '100', '15': '10'}}]})
                if include_peer:
                    # Same collection as a TOP20 sink, distinct causal owner.
                    with capture_owner('shadow', 'peer:fixture', 'peer:actor'):
                        await owned_to_thread(store.upsert_documents, 'top20_daily_entrants',
                            [{'owner': 'peer-fixture', 'key': 'peer', 'document': {'code': '200001'}}])
                async with asyncio.timeout(preparation_timeout):
                    while service._fundamentals_tasks:
                        await asyncio.sleep(.005)
                await asyncio.sleep(max(.03, capture_seconds - clock.elapsed()))
            finally:
                await close_top20_replay_resources(runtime, service, broker, collector=collector, timeout=30)
    if not runtime.status()['execution_succeeded']:
        raise AssertionError(runtime.status())
    finished = time.monotonic_ns()
    rejected = [(row.get('method'), row.get('collection'), row.get('reason'))
                for row in events if row['event_type'] == 'input_rejected']
    if rejected:
        raise AssertionError({'fixture_input_rejected': rejected})
    encoded = b''.join((json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n').encode() for row in events)
    counts = Counter(row.get('workload_id', 'unsupported') for row in events if 'payload' in row)
    manifest = {'trace_id': 'native-session-fixture', 'state': 'complete', 'schema_version': 3,
        'coverage': 'observed_paths_only', 'input_capture_censored': False,
        'payload_capture': {'store_inputs': capture_store, 'collector_inputs': True, 'top20_inputs': True},
        'accepted': len(events), 'written': len(events), 'last_seq': len(events), 'known_dropped': 0,
        'input_rejected': 0, 'payload_accepted': sum(counts.values()), 'queued': 0, 'pending_events': 0,
        'copy_reserved_bytes': 0, 'bytes_written': len(encoded), 'drop_reasons': {}, 'input_rejected_reasons': {},
        'started_mono_ns': started, 'finished_mono_ns': finished, 'started_at': origin.timestamp(),
        'input_coverage': {key: {'accepted': count, 'rejected': 0} for key, count in counts.items()},
        'chunks': [{'name': '000001.jsonl', 'count': len(events), 'first_seq': 1, 'last_seq': len(events),
                    'bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()}]}
    return {'events': events, 'manifest': manifest, 'started': started,
        'hub': f'realtime_hub:{id(hub):x}', 'component': f'autonomous_top20:{id(service):x}',
        'collector': f'realtime_collector:{id(collector):x}',
        'roles': [{'task_name': role, 'spawn_ordinal': 1, 'source_lane': lane} for role, lane in roles.items()]}

from __future__ import annotations

import asyncio
import copy
import json
import time
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI

from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import freeze_payload, thaw_payload
from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import (
    read_realtime_tape, SubscriptionTape,
)
from kiwoom_monitor.central_server.diagnostic_top20_transport import Top20SubscriptionTransport
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.realtime_routes import create_realtime_router
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker

NOW = datetime.fromisoformat('2026-10-12T11:00:00+09:00')
A, B, C = '005930', '000660', '047810'


def trade(code):
    return {'trnm':'REAL', 'data':[{'type':'0B', 'item':code,
            'values':{'10':'1000','20':'110001','15':'1','13':'10','14':'10000'}}]}


async def until(predicate, timeout=3):
    deadline = time.monotonic()+timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('fixture did not reach expected state')
        await asyncio.sleep(.002)


class Socket:
    def __init__(self):
        self.responses = asyncio.Queue()
        self.sent = []
        self.pause_remove = False
        self.remove_started = asyncio.Event()
        self.release = asyncio.Event()
        self.ack_before_send = True

    async def send(self, raw):
        packet = json.loads(raw)
        self.sent.append(packet)
        if packet['trnm'] == 'LOGIN':
            self.responses.put_nowait({'trnm':'LOGIN','return_code':0})
        elif packet['trnm'] in {'REG','REMOVE'}:
            ack = {'trnm':packet['trnm'], 'return_code':0}  # Official response may omit grp_no.
            if self.ack_before_send:
                self.responses.put_nowait(ack)
            if packet['trnm'] == 'REMOVE' and self.pause_remove:
                self.pause_remove = False
                self.remove_started.set()
                await self.release.wait()
            if not self.ack_before_send:
                self.responses.put_nowait(ack)

    async def recv(self):
        return json.dumps(await self.responses.get())

    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False


class IncrementalReceiveTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, central, *, record=False, incomplete_receipt=False, remove_only=False, coalesce=False):
        socket, received, controls, events, received_times = Socket(), [], [], [], []
        activations = []
        def receive(code):
            received.append(code)
            received_times.append((code,time.monotonic()))
        with ExitStack() as stack:
            def emit(token, kind, fields, value):
                events.append({'seq':len(events)+1, 'event_type':kind,
                               **copy.deepcopy(fields), 'payload':freeze_payload(value).value})
                return not (incomplete_receipt and fields.get('input_kind')=='command_sent')
            stack.enter_context(patch.object(trace, 'input_token', side_effect=lambda kind:
                'incremental-fixture' if record and kind == 'top20_inputs' else None))
            stack.enter_context(patch.object(trace, 'emit_payload', side_effect=emit))
            stack.enter_context(patch.object(trace, 'reject_input', side_effect=lambda token, fields:
                events.append({'seq':len(events)+1,'event_type':'input_rejected',**fields})))
            if central:
                hub = RealtimeHub()
                owner = CentralRealtimeCollector(lambda:'fixture', 'real', hub, lambda:NOW)
                subscriber = hub.connect(capture_component='source:top20')
                def update(codes):
                    hub.update_subscription(subscriber, list(codes), [], program_codes=list(codes), priority_codes=list(codes))
                publish = hub.publish
                def observe(event, code=''):
                    if event['type'] == 'trade': receive(event['payload']['code'])
                    if event['type'] in {'connection_opened','subscription_ready','codes_added'}:
                        controls.append(dict(event))
                    if event['type']=='codes_added' and event.get('source_resets'):
                        activations.append(tuple((row['code'],row['market']) for row in event['source_resets']))
                    return publish(event, code)
                stack.enter_context(patch.object(hub,'publish',side_effect=observe))
                stack.enter_context(patch('kiwoom_monitor.central_server.realtime_collector.connect',return_value=socket))
                stack.enter_context(patch('kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS',.02))
                ready = lambda: len([event for event in controls if event['type']=='subscription_ready'])
            else:
                owner = RealtimeTradeWorker(lambda:'fixture', 'real', (A,B), lambda:NOW)
                update = lambda codes: owner.update_codes(tuple(codes))
                owner.connection_opened.connect(lambda codes: controls.append({'type':'connection_opened','codes':codes}))
                owner.codes_added.connect(lambda codes: controls.append({'type':'codes_added','codes':codes}))
                owner.sources_activated.connect(activations.append)
                owner.trade_received.connect(lambda tick: receive(tick.code))
                stack.enter_context(patch('kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker.connect',return_value=socket))
                stack.enter_context(patch('kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker.REALTIME_REG_INTERVAL_SECONDS',.02))
                ready = lambda: len([event for event in controls if event['type']=='connection_opened'])
            update((A,B))
            before_tasks = set(asyncio.all_tasks())
            receiving = asyncio.create_task(owner._receive('KRX'))
            try:
                await until(lambda:ready()==1)
                count = len(socket.sent)
                update((B,A))
                await asyncio.sleep(.13)
                self.assertEqual(count,len(socket.sent),'reorder must send nothing')
                socket.pause_remove = True
                update((B,C))
                await asyncio.wait_for(socket.remove_started.wait(),2)
                delivered_start = time.monotonic()
                socket.responses.put_nowait(trade(B))
                socket.responses.put_nowait(trade(C))
                await until(lambda:B in received)
                self.assertNotIn(C,received,'new source cannot bypass pending approval')
                self.assertFalse(socket.release.is_set(),'retained dispatch must happen during blocked send')
                self.fixture_measurements = {'route':'NAS' if central else 'PC',
                    'retained_tick_dispatch_ms':round((next(at for code,at in received_times if code==B)-delivered_start)*1000,3),
                    'dispatched_before_send_release':True, 'reorder_packets':len(socket.sent)-count-1}
                activation_count = len(activations)
                if coalesce:
                    update((B,A))
                socket.release.set()
                if coalesce:
                    await until(lambda:len(activations)>activation_count)
                    self.assertIn((A,'KRX'),activations[-1])
                    self.assertFalse(any(C in event.get('codes',()) for event in controls if event['type']=='codes_added'))
                else:
                    await until(lambda: any(C in event.get('codes',()) for event in controls if event['type']=='codes_added'))
                final_code = A if coalesce else C
                socket.responses.put_nowait(trade(final_code))
                await until(lambda:final_code in received)
                self.assertEqual(1,len([event for event in controls if event['type']=='connection_opened']))
                delta = socket.sent[count:]
                self.assertTrue(all(packet.get('refresh','1')=='1' for packet in delta))
                for packet in delta:
                    for row in packet['data']:
                        self.assertNotIn(B,row['item'])
                        self.assertNotIn(f'{B}_AL',row['item'])
                        self.assertFalse(set(row['type']) & {'00','04','0J','0U','0s'})
                if remove_only:
                    before_remove = len(socket.sent)
                    update((B,))
                    await until(lambda:ready()==3)
                    self.assertTrue(socket.sent[before_remove:])
                    self.assertTrue(all(packet['trnm']=='REMOVE' for packet in socket.sent[before_remove:]))
                    self.assertEqual(1,len([event for event in controls if event['type']=='connection_opened']))
                if record:
                    if incomplete_receipt:
                        with self.assertRaisesRegex(ValueError,'without_approval'):
                            read_realtime_tape(events)
                        return
                    tape = read_realtime_tape(events)
                    self.assertEqual('top20-realtime-tape/v2',tape['version'])
                    self.assertEqual(3 if coalesce else 2,len(tape['subscriptions']))
                    kinds = [event['input_kind'] for event in events]
                    ack = next(index for index,kind in enumerate(kinds) if kind=='command_ack'
                               and thaw_payload(events[index]['payload'])['command']['trnm']=='REMOVE')
                    sent = next(index for index,kind in enumerate(kinds) if kind=='command_sent'
                                and thaw_payload(events[index]['payload'])['command']['trnm']=='REMOVE')
                    self.assertLess(ack,sent,'actual early ACK must retain its causal order')
                    return tape, f'realtime_hub:{id(hub):x}', events
            finally:
                socket.release.set()
                receiving.cancel()
                await asyncio.gather(receiving,return_exceptions=True)
                self.assertLessEqual(set(asyncio.all_tasks()),before_tasks)

    async def test_pc_receives_retained_ticks_during_send_and_ack_wait(self):
        await self.exercise(False)

    async def test_nas_receives_retained_ticks_and_v2_records_early_ack(self):
        await self.exercise(True,record=True)

    async def test_incomplete_recorder_receipt_does_not_change_live_subscription(self):
        await self.exercise(True,record=True,incomplete_receipt=True)

    async def test_nas_removal_only_completes_without_synthetic_reg_ack(self):
        await self.exercise(True,remove_only=True)

    async def test_latest_intent_supersedes_inflight_batch_without_false_ready(self):
        for central in (False,True):
            with self.subTest(central=central):
                await self.exercise(central,coalesce=True,record=central)

    async def test_continuous_real_cannot_hide_registration_ack_timeout(self):
        socket = Socket()
        original_send = socket.send
        async def without_ack(raw):
            packet = json.loads(raw)
            if packet['trnm']=='LOGIN':
                await original_send(raw)
            else:
                socket.sent.append(packet)
        socket.send = without_ack
        worker = RealtimeTradeWorker(lambda:'fixture','real',(A,),lambda:NOW)
        received = []
        worker.trade_received.connect(received.append)
        async def producer():
            while True:
                socket.responses.put_nowait(trade(A))
                await asyncio.sleep(.001)
        with patch('kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker.connect',return_value=socket), \
                patch('kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker.SUBSCRIPTION_ACK_TIMEOUT_SECONDS',.04):
            producing = asyncio.create_task(producer())
            try:
                with self.assertRaisesRegex(RuntimeError,'승인 시간 초과'):
                    await asyncio.wait_for(worker._receive('KRX'),.5)
                self.assertEqual([],received)
            finally:
                producing.cancel()
                await asyncio.gather(producing,return_exceptions=True)

    async def test_missing_or_forged_v2_command_evidence_is_rejected(self):
        _,_,events = await self.exercise(True,record=True)
        without_ack = [event for event in events if event.get('input_kind')!='command_ack']
        with self.assertRaises((ValueError,RuntimeError)):
            read_realtime_tape(without_ack)
        changed = copy.deepcopy(events)
        ending = next(event for event in changed if event.get('input_kind')=='batch_end')
        value = thaw_payload(ending['payload'])
        value['groups'] = {}
        ending['payload'] = freeze_payload(value).value
        with self.assertRaisesRegex(ValueError,'batch_incomplete'):
            read_realtime_tape(changed)

    async def test_v2_native_replay_dispatches_retained_data_before_new_approval(self):
        tape, source_hub, _ = await self.exercise(True,record=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(trace,'input_token',return_value=None):
            store = SQLiteQueryStore(Path(directory)/'replay.sqlite3')
            store.initialize()
            hub = RealtimeHub()
            collector = CentralRealtimeCollector(lambda:self.fail('token requested by offline replay'),
                'real',hub,lambda:NOW,store=store)
            await collector.start_input_replay()
            subscriber = hub.connect(capture_component='live:top20')
            def update(codes):
                hub.update_subscription(subscriber,list(codes),[],program_codes=list(codes),priority_codes=list(codes))
            transport = Top20SubscriptionTransport(collector,SubscriptionTape(tape),
                source_hub_component=source_hub,component_map={'live:top20':'source:top20'})
            updating = None
            try:
                update((A,B))
                await transport.update()
                update((B,C))
                updating = asyncio.create_task(transport.update())
                await until(lambda:('0B',A) not in transport._approved_atoms)
                self.assertEqual((1,1),transport.registered_message(trade(B)))
                self.assertEqual((1,0),transport.registered_message(trade(C)))
                await updating
                self.assertEqual((1,1),transport.registered_message(trade(C)))
                self.assertTrue(hub.upstream_ready)
                self.assertEqual([],transport.tape.report()['unused_subscriptions'])
            finally:
                if updating and not updating.done():
                    updating.cancel()
                    await asyncio.gather(updating,return_exceptions=True)
                await collector.close()
                store.close()

    async def test_client_subscription_update_does_not_repeat_connection_opened(self):
        hub, app = RealtimeHub(), FastAPI()
        hub.set_upstream_ready((A,B,C),True)
        app.include_router(create_realtime_router(hub,
            SimpleNamespace(load_realtime_snapshots=lambda codes:[]),lambda token:token=='fixture',None))
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        scope = {'type':'websocket','asgi':{'version':'3.0','spec_version':'2.4'},
            'scheme':'ws','path':'/api/v1/realtime','raw_path':b'/api/v1/realtime',
            'query_string':b'token=fixture','root_path':'','http_version':'1.1','headers':[],
            'client':('127.0.0.1',1),'server':('127.0.0.1',8787),'state':{}}
        incoming.put_nowait({'type':'websocket.connect'})
        running = asyncio.create_task(app(scope,incoming.get,outgoing.put))
        async def read():
            frame = await asyncio.wait_for(outgoing.get(),2)
            return json.loads(frame['text']) if frame['type']=='websocket.send' else frame
        async def subscribe(codes):
            incoming.put_nowait({'type':'websocket.receive','text':json.dumps({'type':'subscribe','codes':codes})})
            self.assertEqual('subscribed',(await read())['type'])
            self.assertEqual('central_ready',(await read())['type'])
            return await read()
        try:
            self.assertEqual('websocket.accept',(await read())['type'])
            self.assertEqual('ready',(await read())['type'])
            self.assertEqual('connection_opened',(await subscribe([A,B]))['type'])
            changed = await subscribe([B,C])
            self.assertEqual('subscription_ready',changed['type'])
            self.assertEqual('client',changed['scope'])
            self.assertEqual(sorted([B,C]),changed['codes'])
        finally:
            incoming.put_nowait({'type':'websocket.disconnect','code':1000})
            await asyncio.wait_for(running,2)
        self.assertEqual(0,hub.client_count)

    def test_source_reset_preserves_other_venue_cumulative_baseline(self):
        aggregator = MinuteTradeValueAggregator()
        aggregator._last_cumulative_trade_value = {(B,'KRX'):10,(B,'NXT'):20,(A,'KRX'):30}
        aggregator.reset_cumulative_sources(((B,'NXT'),))
        self.assertEqual({(B,'KRX'):10,(A,'KRX'):30},aggregator._last_cumulative_trade_value)

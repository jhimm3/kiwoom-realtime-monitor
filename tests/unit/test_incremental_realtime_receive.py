from __future__ import annotations

from kiwoom_monitor.central_server.diagnostic_replay_contract import validate_collector_message

import asyncio
import copy
import json
import time
import tempfile
import unittest
from kiwoom_monitor.central_server import diagnostic_top20_lifecycle_input as hub_capture
from pathlib import Path
from contextlib import ExitStack
from datetime import datetime, timedelta
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
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import parse_order_book_ticks
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_registration import all_atoms
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
    async def test_subscription_receipt_state_is_owned_per_recorder_and_resets_for_new_batch(self):
        from kiwoom_monitor.central_server.diagnostic_top20_subscription_v2 import SubscriptionRecorder
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_registration import plan_registration

        hub_observer = hub_capture.HubInputRecorder()
        hub = RealtimeHub(now_provider=lambda: NOW, observer=hub_observer)
        other_hub_observer = hub_capture.HubInputRecorder()
        other_hub = RealtimeHub(now_provider=lambda: NOW, observer=other_hub_observer)
        recorder = SubscriptionRecorder(hub_observer=hub_observer)
        other = SubscriptionRecorder(hub_observer=other_hub_observer)
        collector = CentralRealtimeCollector(lambda: 'unused', 'real', hub, lambda: NOW,
                                              subscription_observer=recorder)
        groups = {'100': [{'item': [A], 'type': ['0B']}]}
        plan = plan_registration(groups, {})
        request = {'session': 'KRX', 'codes': [A], 'nxt_codes': [],
                   'program_codes': [A], 'priority_codes': [A]}
        with patch.object(trace, 'input_token', return_value='recorder-owner-fixture'), \
                patch.object(trace, 'emit_payload', return_value=False) as emit, \
                patch.object(trace, 'reject_input'):
            receipt = recorder.begin_batch(collector, request, plan, 1)
            self.assertIsNotNone(receipt)
            self.assertFalse(receipt.complete)
            self.assertFalse(recorder.complete)
            self.assertTrue(other.complete)
            self.assertNotIn('_recorded_v2_complete', vars(collector))
            self.assertEqual(set(), collector._approved_trade_sources)
            emit.return_value = True
            receipt = recorder.begin_batch(collector, request, plan, 2)
            self.assertTrue(receipt.complete)
            self.assertTrue(recorder.complete)
            # A tool callback failure marks its own receipt state and returns;
            # it cannot approve a native command or modify another recorder.
            self.assertIsNone(recorder.command_event(collector, 'command_start', receipt, object(), 1))
            self.assertFalse(recorder.complete)
            self.assertTrue(other.complete)
            self.assertNotIn('_recorded_v2_complete', vars(collector))
            self.assertEqual(set(), collector._approved_trade_sources)
            self.assertIs(hub._observer, recorder.hub_observer)
            self.assertEqual('recorder-owner-fixture', hub._observer.lifecycle_epoch)
            self.assertIsNone(other_hub._observer.lifecycle_epoch)
            hub.connect()
            self.assertEqual(1, sum(call.args[2].get('input_kind') == 'hub_initial'
                                    for call in emit.call_args_list))

    async def test_actual_receive_works_when_subscription_recorder_source_is_physically_absent(self):
        import hashlib
        import shutil
        import subprocess
        import sys
        import textwrap

        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / 'src' / 'kiwoom_monitor'
            shutil.copytree(root / 'src' / 'kiwoom_monitor', copied,
                            ignore=shutil.ignore_patterns('__pycache__', 'diagnostic_top20_subscription_v2.py'))
            self.assertFalse((copied / 'central_server' / 'diagnostic_top20_subscription_v2.py').exists())
            expected = hashlib.sha256((root / 'src/kiwoom_monitor/central_server/realtime_collector.py').read_bytes()).hexdigest()
            program = r'''
import asyncio, hashlib, json, os, runpy, sys
from pathlib import Path
runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
copied, root, expected = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(copied.parent), str(root), str(root / 'tests' / 'unit')]
from tests.unit.test_incremental_realtime_receive import IncrementalReceiveTests
from kiwoom_monitor.central_server import realtime_collector
actual = Path(realtime_collector.__file__).resolve()
assert actual == (copied / 'central_server' / 'realtime_collector.py').resolve()
assert hashlib.sha256(actual.read_bytes()).hexdigest() == expected
assert not (copied / 'central_server' / 'diagnostic_top20_subscription_v2.py').exists()
assert 'kiwoom_monitor.central_server.diagnostic_top20_subscription_v2' not in sys.modules
# Reuse the original native REG/REMOVE, early ACK, retained trade delivery,
# removed/new-code filtering, no false readiness and owned-task cleanup oracle.
asyncio.run(IncrementalReceiveTests().exercise(True, remove_only=True))
assert 'kiwoom_monitor.central_server.diagnostic_top20_subscription_v2' not in sys.modules
print(json.dumps({'actual_origin': str(actual), 'subscription_tool_source_absent': True,
                  'original_native_receive_oracle': 'passed'}))
'''
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, '-I', '-B', '-X', 'utf8', '-c', textwrap.dedent(program),
                 str(copied), str(root), expected], capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            proof = json.loads(result.stdout.strip())
            self.assertTrue(proof['subscription_tool_source_absent'])
            self.assertEqual('passed', proof['original_native_receive_oracle'])

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
                hub_observer = hub_capture.HubInputRecorder()
                hub = RealtimeHub(now_provider=lambda: NOW, observer=hub_observer)
                if record:
                    from kiwoom_monitor.central_server.diagnostic_top20_subscription_v2 import SubscriptionRecorder
                    observer = SubscriptionRecorder(hub_observer=hub_observer)
                else:
                    observer = None
                owner = CentralRealtimeCollector(lambda:'fixture', 'real', hub, lambda:NOW,
                                                  subscription_observer=observer)
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

    async def test_explicit_quote_fixture_obeys_ack_and_item_scope_on_the_existing_receive_path(self):
        socket = Socket()
        observed_at = NOW + timedelta(seconds=2)
        hub = RealtimeHub(now_provider=lambda: observed_at)
        subscriber = hub.connect(allowed_event_types=frozenset({"trade"}))
        hub.update_subscription(subscriber, [A], [])
        collector = CentralRealtimeCollector(lambda: "fixture", "mock", hub, lambda: observed_at)
        native_groups = collector._subscription_groups
        native_approve = collector._approve_incremental_subscription
        native_publish = collector._publish_parsed
        native_send = socket.send
        quote_group = "4500"
        quote_atom = ("0D", A)
        quote_group_rows = [{"item": [A], "type": ["0D"]}]
        self.assertFalse(any(kind == "0D" for kind, _ in all_atoms(native_groups("KRX", (A,), (), (), ()))))

        requested_quote = False
        quote_commands, approvals, admitted_frames = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()

        async def send(raw):
            packet = json.loads(raw)
            if packet.get("grp_no") == quote_group:
                socket.sent.append(packet)
                quote_commands.put_nowait(packet)  # The test releases only this ACK.
            else:
                await native_send(raw)

        def groups(*args, **kwargs):
            result = native_groups(*args, **kwargs)
            if requested_quote:
                self.assertNotIn(quote_group, result)
                result[quote_group] = quote_group_rows
            return result

        def approve(groups, *args, **kwargs):
            native_approve(groups, *args, **kwargs)
            approvals.put_nowait(all_atoms(groups))

        def publish(admitted):
            received_rows = [(row["type"], row["item"]) for row in admitted["data"]]
            admitted_frames.put_nowait((received_rows, parse_order_book_ticks(admitted, subscription_item=A)))
            native_publish(admitted)

        def acknowledge(packet):
            socket.responses.put_nowait({"trnm": packet["trnm"], "grp_no": quote_group, "return_code": 0})

        async def get(queue):
            return await asyncio.wait_for(queue.get(), timeout=3)

        async def receive_probe():
            quote = {"type": "0D", "item": A, "values": {
                "21": "110001", "41": "1010", "61": "14", "51": "1000", "71": "28", "23": "1010", "24": "3",
            }}
            socket.responses.put_nowait({"trnm": "REAL", "data": [quote, quote | {"item": f"{A}_NX"}, *trade(A)["data"]]})
            observed = await get(admitted_frames)
            tick = await get(subscriber.queue)
            self.assertEqual(("trade", A, "KRX"), (tick["type"], tick["payload"]["code"], tick["payload"]["market"]))
            return observed

        before_tasks = set(asyncio.all_tasks())
        with patch("kiwoom_monitor.central_server.realtime_collector.connect", return_value=socket) as connect, \
                patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", .01), \
                patch.object(socket, "send", side_effect=send), \
                patch.object(collector, "_subscription_groups", side_effect=groups), \
                patch.object(collector, "_approve_incremental_subscription", side_effect=approve), \
                patch.object(collector, "_publish_parsed", side_effect=publish):
            receiving = asyncio.create_task(collector._receive("KRX"))
            try:
                base = await get(approvals)
                self.assertIn(("0B", A), base)
                self.assertNotIn(quote_atom, base)

                requested_quote = True
                added = await get(quote_commands)
                self.assertEqual({"trnm": "REG", "grp_no": quote_group, "refresh": "1", "data": quote_group_rows}, added)
                self.assertEqual(([("0B", A)], ()), await receive_probe())
                acknowledge(added)
                self.assertEqual(base | {quote_atom}, await get(approvals))

                received_rows, (book,) = await receive_probe()
                # This is the admitted array order, with no exchange-order inference.
                self.assertEqual([("0D", A), ("0B", A)], received_rows)
                self.assertEqual((A, "KRX", "110001", "second"),
                                 (book.code, book.subscription_market, book.quote_time, book.time_precision))
                self.assertEqual((14, 28, 1010), (book.asks[0].quantity, book.bids[0].quantity, book.indicative_price_won))

                requested_quote = False
                removed = await get(quote_commands)
                self.assertEqual({"trnm": "REMOVE", "grp_no": quote_group, "data": quote_group_rows}, removed)
                self.assertEqual(([("0B", A)], ()), await receive_probe())
                acknowledge(removed)
                self.assertEqual(base, await get(approvals))
                self.assertEqual({(A, "KRX")}, collector._approved_trade_sources)
                self.assertTrue(subscriber.queue.empty())
                connect.assert_called_once()
            finally:
                receiving.cancel()
                await asyncio.gather(receiving, return_exceptions=True)
                hub.disconnect(subscriber)
                await collector.close()
        self.assertLessEqual(set(asyncio.all_tasks()), before_tasks)

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
            hub = RealtimeHub(now_provider=lambda: NOW, observer=hub_capture.HubInputRecorder())
            collector = CentralRealtimeCollector(lambda:self.fail('token requested by offline replay'),
                'real',hub,lambda:NOW,store=store)
            await collector.start_input_replay(message_validator=validate_collector_message)
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
        hub, app = RealtimeHub(now_provider=lambda: NOW, observer=hub_capture.HubInputRecorder()), FastAPI()
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

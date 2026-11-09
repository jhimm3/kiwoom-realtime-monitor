"""Run a synthetic market day through the installed collection, rehearsal and archive owners."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager, closing
from datetime import time
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import socket
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException
import uvicorn

from kiwoom_monitor.domain.time_basis import aware_iso_kst
from kiwoom_monitor.domain.research_contract import encode_reported_result_owner
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import MarketOperationTick
from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_venue, execution_remaining_quantity
from .autonomous_top20 import AutonomousTop20Service
from .database import SQLiteQueryStore
from .local_strategy_rehearsal import LocalStrategyRehearsal
from .market_ingest import MarketDataIngestor
from .observation_archive_routes import create_observation_archive_router
from .program_flow_archive import ProgramFlowArchive
from .realtime_hub import RealtimeHub
from .rest_broker import BrokerResult


STREAM = 'isolated_sor_pc'


class _SyntheticMarket:
    def __init__(self, tape, store, at):
        self.now = at
        self.ingestor = MarketDataIngestor(store, now_provider=lambda: self.now)
        plans = [row['symbol'] for row in tape['plans']]
        self.codes = tuple((plans + [f'{n:06d}' for n in range(800000, 800020) if f'{n:06d}' not in plans])[:20])
        self.nxt = frozenset(tape['synthetic_nxt_codes'])
        if (not 1 <= len(plans) <= 20 or len(set(plans)) != len(plans)
                or not self.nxt.issubset(plans)):
            raise ValueError('one isolated day requires 1–20 distinct plans and their synthetic NXT codes')
        self.prices = defaultdict(list)
        for frame in tape['frames']:
            for symbol, row in frame['observations'].items():
                for venue, quote in row['quotes'].items():
                    at = aware_iso_kst(frame['at'])
                    self.prices[symbol, venue].append((at.strftime('%H%M'), quote['price']))
        self.calls = []

    def _bars(self, code, venue):
        grouped = defaultdict(list)
        for minute, price in self.prices.get((code, venue), [('09:00', 1000)]):
            grouped[minute].append(price)
        return grouped

    @staticmethod
    def _ohlc(prices):
        return {'open_pric': str(prices[0]), 'high_pric': str(max(prices)), 'low_pric': str(min(prices)),
                'cur_prc': str(prices[-1]), 'trde_qty': '1'}

    async def request_unrecorded(self, api_id, path, body, *, cont_yn='N', next_key=''):
        if cont_yn != 'N' or next_key:
            raise ValueError('synthetic REST responses have no continuation')
        self.calls.append((api_id, dict(body)))
        day, stamp = self.now.strftime('%Y%m%d'), self.now.strftime('%H%M%S')
        if api_id == 'ka10099':
            market = body['mrkt_tp']
            codes = self.codes[:-1] if market == '0' else self.codes[-1:] if market == '10' else ()
            payload = {'list': [{'code': code, 'name': code, 'marketCode': market,
                                 'nxtEnable': 'Y' if code in self.nxt else 'N'} for code in codes]}
        elif api_id == 'ka00198':
            payload = {'item_inq_rank': [{'stk_cd': code, 'stk_nm': code, 'dt': day, 'tm': stamp,
                                         'bigd_rank': str(rank)} for rank, code in enumerate(self.codes, 1)]}
        elif api_id in {'ka10080', 'ka10081'}:
            code, _, suffix = body['stk_cd'].partition('_')
            bars = self._bars(code, 'NXT' if suffix == 'NX' else 'KRX')
            if api_id == 'ka10080':
                payload = {'stk_min_pole_chart_qry': [{**self._ohlc(prices),
                    'cntr_tm': day + minute.replace(':', '') + '00'} for minute, prices in reversed(bars.items())]}
            else:
                payload = {'stk_dt_pole_chart_qry': [{**self._ohlc([p for values in bars.values() for p in values]),
                                                      'dt': day}]}
        elif api_id == 'ka20005':
            payload = {'inds_min_pole_qry': [{'cntr_tm': day + '090000', 'cur_prc': '1000'}]}
        elif api_id == 'ka20006':
            payload = {'inds_dt_pole_qry': [{'dt': day, 'cur_prc': '1000'}]}
        elif api_id == 'ka90008':
            payload = {'stk_tm_prm_trde_trnsn': [{'tm': '100000', 'dt': day, 'stex_tp': '통합',
                                                'prm_netprps_amt': '12', 'cur_prc': '1000'}]}
        else:
            raise ValueError(f'no synthetic market response for {api_id}')
        return BrokerResult(payload, False, '')

    async def request(self, api_id, path, body, **continuation):
        result = await self.request_unrecorded(api_id, path, body, **continuation)
        self.ingestor.ingest(api_id, body, result.payload)
        return result


def _open_run(path, raw):
    path = path.absolute()
    if path.resolve() != path:
        raise ValueError('the isolated run directory must not be redirected')
    path.mkdir(parents=True, exist_ok=True)
    marker = path / 'isolated-sor-day.json'
    identity = {'version': 'isolated-sor-day/v1', 'tape_sha256': sha256(raw).hexdigest()}
    if not marker.exists():
        if any(path.iterdir()):
            raise ValueError('a new isolated run requires an empty directory')
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(identity, stream)
    for child in path.rglob('*'):
        if child.resolve() != child:
            raise ValueError('redirected files are not allowed in an isolated run')
    if json.loads(marker.read_bytes()) != identity:
        raise ValueError('the saved run belongs to a different synthetic tape')
    return path


@asynccontextmanager
async def _receiver(root):
    connection = root / 'archive-connection.json'
    with closing(SQLiteQueryStore(root / 'archive.sqlite3')) as store, socket.socket() as listener:
        store.initialize()
        store.prepare_observation_archive()
        if connection.exists():
            # Only recover the pinned local port and synthetic token. Never use a URL from the tape.
            saved = DataSourceConfig(connection).load()
            url = urlsplit(saved.server_url)
            port = url.port
            if port is None or saved.server_url != f'http://127.0.0.1:{port}':
                raise ValueError('isolated archive receiver must use IPv4 loopback')
            token = saved.access_token
        else:
            port, token = 0, secrets.token_hex(24)
        listener.bind(('127.0.0.1', port))
        listener.listen()
        port = listener.getsockname()[1]
        DataSourceConfig(connection).save(DataSourceSettings(mode='local_server',
            server_url=f'http://127.0.0.1:{port}', access_token=token))
        def authorize(authorization: str = Header(default=''), x_kiwoom_expected_archive_stream: str = Header(default='')):
            if (authorization != f'Bearer {token}'
                    or x_kiwoom_expected_archive_stream != encode_reported_result_owner(STREAM)):
                raise HTTPException(401)
            return STREAM

        app = FastAPI()
        app.include_router(create_observation_archive_router(store, authorize))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError('isolated archive receiver exited during startup')
                    await asyncio.sleep(.025)
            yield
        finally:
            server.should_exit = True
            await task


async def run_day(source: Path, root: Path, through: int | None) -> dict:
    with source.open('rb') as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('synthetic tape exceeds 4 MiB')
    tape = json.loads(raw)
    first = aware_iso_kst(tape['frames'][0]['at'])
    if (tape['version'] != 'local-strategy-rehearsal/v2' or first.weekday() >= 5
            or any(aware_iso_kst(frame['at']).date() != first.date() or
                   not time(9) <= aware_iso_kst(frame['at']).time() < time(15, 20) for frame in tape['frames'])):
        raise ValueError('isolated SOR day requires a single regular trading day')
    root = _open_run(root, raw)
    day = first.date().isoformat()
    premarket = first.replace(hour=7, minute=0, second=0, microsecond=0)
    outbox = root / 'outbox'
    with closing(SQLiteQueryStore(root / 'market.sqlite3', observation_history_enabled=True)) as store:
        store.initialize()
        market = _SyntheticMarket(tape, store, premarket)
        archive = ProgramFlowArchive(store, outbox, root / 'archive-connection.json', STREAM)
        service = AutonomousTop20Service(market, RealtimeHub(), store, now_provider=lambda: market.now,
            catalog_loader=lambda: (), program_flow_archive=archive)
        try:
            await service._prepare_nxt_catalog(day)
            flags = await service._load_nxt_catalog(day)
            routes = tape['sor_destinations']
            if flags is None or any(code not in flags or (venue == 'NXT' and flags[code] is not True)
                                    for code, venue in routes.items()):
                raise ValueError('SOR routing requires the saved premarket eligibility')
            if not store.load_dataset_snapshots('top20_membership', day, 1):
                market.now = premarket.replace(hour=8)
                await market.request('ka00198', '/api/dostk/stkinfo', {'qry_tp': '1'})
                if await service.refresh_ranking_once() != market.codes:
                    raise RuntimeError('synthetic TOP20 could not be collected')
            if not store.load_documents('krx_trading_day_observations', day, 1):
                market.now = premarket.replace(hour=9)
                service._save_market_operation_day(day, MarketOperationTick('3', '090000', None))
            replay = LocalStrategyRehearsal(source, root / 'orders', 'SOR', expected_tape_hash=sha256(raw).hexdigest())
            try:
                initial_cursor = replay.cursor
                initial_commands = len(replay.broker.state['commands'])
                stop = len(replay.frames) if through is None else through
                if not initial_cursor <= stop <= len(replay.frames):
                    raise ValueError('checkpoint must be at or after the saved cursor and inside the tape')
                while replay.cursor < stop:
                    replay.step()
                records = {symbol: {slot: {'state': record.state.value, 'number': record.broker_order_id,
                            'physical_venue': execution_broker_venue(record), 'filled': record.detailed_filled_quantity,
                            'remaining': execution_remaining_quantity(record)}
                            for slot, record in replay.orders.read_plan(symbol).items()} for symbol in replay.plans}
                commands = replay.broker.state['commands']
            finally:
                replay.close()
            receipts = []
            if stop == len(tape['frames']):
                market.now = premarket.replace(hour=20, minute=5)
                async with _receiver(root):
                    archive.start()
                    try:
                        if not await service.after_close.run_day(day):
                            raise RuntimeError('synthetic after-close backfill did not complete')
                        batches = sorted(outbox.rglob('*.batch'))
                        if not batches:
                            raise RuntimeError('completed isolated day produced no archive batches')
                        acknowledgements = [batch.with_name(batch.name + '.ack.json') for batch in batches]
                        async with asyncio.timeout(25):
                            while not all(path.is_file() for path in acknowledgements):
                                await asyncio.sleep(.05)
                        receipts = [json.loads(path.read_bytes())['receipt'] for path in acknowledgements]
                    finally:
                        await archive.close()
            return {'day': day, 'initial_cursor': initial_cursor, 'cursor': stop, 'initial_commands': initial_commands,
                    'catalog': flags, 'requests': market.calls, 'orders': records, 'commands': commands,
                    'archive_receipts': receipts}
        finally:
            await service.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='KiwoomMonitor --isolated-sor-day')
    parser.add_argument('--tape', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--through', type=int, help='absolute count of market frames to replay before closing')
    args = parser.parse_args(argv)
    # The archive connection is the one generated loopback endpoint, including when a system proxy is configured.
    os.environ['no_proxy'] = os.environ['NO_PROXY'] = '127.0.0.1'
    result = asyncio.run(run_day(args.tape, args.run_dir, args.through))
    (args.run_dir / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0

"""Fixed observation cuts to durable SQLite archive and the existing research reader."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing, contextmanager, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx

from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings, ProducerGrant
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.market_observations import bar_observation_key, minute_bar_observation, ranking_observation
from kiwoom_monitor.central_server.program_flow_archive import ProgramFlowArchive
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult, CentralRestBroker
from kiwoom_monitor.domain.market_data_contract import DataCompleteness, DataValueKind, ObservationOrigin
from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, OBSERVATION_ARCHIVE_PATH, ObservationArchiveBatch, ObservationArchiveLimit, SQLITE_ARCHIVE_DURABILITY,
    archive_json, archive_sha256,
)
from kiwoom_monitor.domain.research_contract import encode_reported_result_owner
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, _result_urlopen
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_research_export
from kiwoom_monitor.observation_archive import (
    deliver_observation_archive, inspect_program_flow, main, prepare_observation_archive, restore_observation_archive,
)
from tests.unit.test_query_authority import ADMIN, QUERY, query_app, wire_server
from tests.unit.test_research_input_authority import BODY, INPUT, RESULT, input_settings


STREAM, ARCHIVE_TOKEN = "execution_pc_v1", "archive-stream-fixture"
OTHER_STREAM, OTHER_TOKEN = "other_pc_v1", "archive-other-stream-fixture"
START = datetime(2026, 10, 26, 7, 55, tzinfo=KST)
CUT = START + timedelta(minutes=4)
END = START + timedelta(minutes=10)


def receiver_settings(database_url):
    return replace(input_settings(database_url), observation_archive_grants=(
        ProducerGrant(STREAM, ARCHIVE_TOKEN), ProducerGrant(OTHER_STREAM, OTHER_TOKEN),
    ))


def save_connection(path, url, token):
    DataSourceConfig(path).save(DataSourceSettings(mode="local_server", server_url=url, access_token=token))


def fixture_batch(batch_id="c65be620-cd3a-432f-8c0e-6614cf742817", *, source_stream=STREAM, code="005930"):
    """Literal producer contract used when the test concerns receiver transaction faults only."""
    revision_id = "bf449a3c-5730-4d22-9574-69a6250aa4b1"
    observations = archive_json({
        "kind": "ranking", "subject": "5", "ordinal": 1, "accepted_sequence": 17, "schema_version": 1,
        "revision_id": revision_id, "revision_of": None, "observation_key": "2026-10-26T07:56:00",
        "source_id": "kiwoom-ka00198", "source_session_id": "source-session-v1", "source_sequence": "141",
        "effective_at": "2026-10-26T07:56:00+09:00", "received_at": "2026-10-26T07:56:01+09:00",
        "available_at": "2026-10-25T22:56:01+00:00", "payload_hash": "f" * 64,
        "payload": {"query_type": "5", "items": [{"stk_cd": code}]},
    }) + b"\n"
    manifest = archive_json({
        "dataset_id": batch_id, "fixed_watermark": batch_id, "schema_version": 1, "kinds": ["ranking"], "subject": "5",
        "captured_range": {"start": START.isoformat(), "end": CUT.isoformat()}, "exported_at": CUT.isoformat(),
        "observations_file": "observations.jsonl", "observations_file_hash": archive_sha256(observations),
        "revision_count": 1, "revision_ids_hash": archive_sha256(revision_id.encode("utf-8")),
        "theme_snapshots_file": "theme_snapshots.jsonl", "theme_snapshots_file_hash": archive_sha256(b""),
        "theme_snapshot_count": 0,
    })
    return ObservationArchiveBatch.from_artifact(source_stream, manifest, observations)


def isolated_sor_tape():
    from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID
    from tests.unit.test_leader_breakout import AT, execution_config, policy, trade

    nxt, krx = '005930', '000660'
    references = {'version': 'strategy-reference/v1', 'calendar': [], 'listings': []}
    second = policy()
    second = replace(second, signal_valid_seconds=10,
                     contexts=(replace(second.contexts[0], symbol=krx, evidence_ref='isolated-symbol'),))
    first = execution_config().to_dict()
    first['signal_valid_seconds'] = 10
    frames = []
    for index, (price, fills) in enumerate((
        (995, {}), (1010, {}), (1010, {'BUY': 1}), (1014, {}), (1014, {'BUY': 3}),
        (945, {}), (945, {'SELL': 4}),
    )):
        now = AT + timedelta(seconds=index)
        frames.append({'at': now.isoformat(), 'observations': {
            code: {'trade': trade(price, at=now, code=code), 'fills': fills,
                   'quotes': {venue: {'price': price + delta, 'available_at': now.isoformat(),
                                     'eligible': True, 'phase': 'CONTINUOUS'}
                              for venue, delta in (('KRX', 0), ('NXT', 1))}}
            for code in (nxt, krx)
        }})
    return {'version': 'local-strategy-rehearsal/v2', 'capital_won': 20000, 'frames': frames,
            'plans': [{'family': LEADER_BREAKOUT_FAMILY_ID, 'symbol': code, 'references': references,
                       'parameters': {'policy': rules, 'quantity': 4, 'capital_won': 10000}}
                      for code, rules in ((nxt, first), (krx, second.to_dict()))],
            'signal_valid_seconds': 10, 'maximum_quote_age_seconds': 3, 'maximum_adverse_slippage_bps': 300,
            'protective_slippage_bps': 300, 'sessions': {'SOR': ['KRX_REGULAR']},
            'sor_destinations': {nxt: 'NXT', krx: 'KRX'}, 'synthetic_nxt_codes': [nxt]}


class ObservationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source = self.root / "source.sqlite3"
        self.target = self.root / "target.sqlite3"
        self.source_connection = self.root / "source-connection.json"
        self.target_connection = self.root / "target-connection.json"

    @staticmethod
    def _url(path):
        return "sqlite:///" + path.as_posix()

    def test_isolated_sor_day_resumes_physical_orders_and_archives_one_day(self):
        from kiwoom_monitor.central_server.isolated_sor_day import run_day

        tape = self.root / 'tape.json'
        tape.write_text(json.dumps(isolated_sor_tape()), encoding='utf-8')
        run = self.root / 'isolated'
        with patch.dict(os.environ, {'no_proxy': '127.0.0.1'}):
            partial = asyncio.run(run_day(tape, run, 3))
            completed = asyncio.run(run_day(tape, run, None))
        for code, venue, order_id, price in (
            ('005930', 'NXT', '0000001', '1015'), ('000660', 'KRX', '0000002', '1014'),
        ):
            self.assertEqual({'state': 'PARTIALLY_FILLED', 'number': order_id, 'physical_venue': venue,
                              'filled': 1, 'remaining': 3}, partial['orders'][code]['BUY'])
            amendment, = (row['body'] for row in completed['commands']
                          if row['api_id'] == 'kt10002' and row['body']['stk_cd'] == code)
            self.assertEqual((venue, order_id, '3', price), tuple(amendment[key] for key in
                ('dmst_stex_tp', 'orig_ord_no', 'mdfy_qty', 'mdfy_uv')))
            self.assertEqual((4, 4), tuple(completed['orders'][code][side]['filled'] for side in ('BUY', 'SELL')))
        self.assertEqual((3, 2), (completed['initial_cursor'], completed['initial_commands']))
        self.assertEqual(['SOR', 'SOR'], [row['body']['dmst_stex_tp'] for row in completed['commands']
                                        if row['api_id'] == 'kt10000'])
        self.assertEqual({'ka00198', 'ka10099'}, {api for api, _ in partial['requests']})
        self.assertNotIn('ka10099', {api for api, _ in completed['requests']})
        self.assertEqual({'ranking', 'minute_bar', 'daily_bar', 'top20_membership', 'program_flow'},
                         {row['kind'] for row in completed['archive_receipts']})
        self.assertEqual(5, len(completed['archive_receipts']))

    def test_pc_completed_program_flow_restarts_pending_and_nas_reuses_first_receipt(self):
        day = "2026-10-26"
        now = [datetime.fromisoformat(day + "T19:59:00+09:00")]
        code = "005930"
        outbox = self.source.with_name(self.source.name + ".program-archive")
        target = query_app(receiver_settings(self._url(self.target)))
        settings = replace(input_settings(self._url(self.source)), kiwoom_app_key="isolated-key",
            kiwoom_secret_key="isolated-secret", autonomous_top20_enabled=True,
            program_flow_archive_target_file=str(self.target_connection), program_flow_archive_source_stream=STREAM,
            top20_outbox_path=str(self.root / "index-outbox.json"))
        lost_get = Event()
        flow = BrokerResult({"stk_tm_prm_trde_trnsn": [
            {"tm": "100030", "dt": "20261026", "stex_tp": "통합", "prm_netprps_amt": "12", "cur_prc": "100"},
        ]}, False, "")

        def lose_first_receipt(request, **kwargs):
            with _result_urlopen(request, **kwargs) as response:
                response.read()
            if request.method == "GET":
                lost_get.set()
            raise TimeoutError("isolated loss after native archive commit")

        async def exercise(target_url, recovery):
            source = create_app(settings)
            service = source.state.autonomous_top20_service
            workflow = service.after_close
            service._excluded_product_codes = frozenset({"069500"})
            service._stock_master_day = day
            # The unrelated chart TRs are covered elsewhere. Drive real program recovery and SQLite here.
            workflow._backfill_market_indexes = AsyncMock()
            workflow._nxt_enabled = AsyncMock(return_value=False)
            workflow._backfill_daily = AsyncMock()
            workflow._backfill_minutes = AsyncMock()
            workflow.reconcile = AsyncMock(side_effect=[RuntimeError("incomplete close"), {}])
            live = {"market": "KRX", "rows": [{"trade_time": "195900", "source": "kiwoom_realtime_0w",
                                               "net_buy_quantity": 20, "available": True}]}
            service._store.save_dataset_snapshot("program_flow", code, "20261025:REALTIME:195900", live)
            service._store.upsert_documents("candidate_flow_finalization", [{
                "owner": f"{day}:{code}", "key": "gap:100000:100100",
                "document": {"start": "100000", "end": "100100"},
            }])
            service._store.upsert_documents("top20_daily_entrants", [{
                "owner": day, "key": code, "document": {"first_seen": day + "T09:00:00+09:00"},
            }])
            service._program_snapshots.enqueue({"subject": code, "snapshot_key": "20261026:REALTIME:195900", "payload": live})
            original_items = [{"stk_cd": symbol, "stk_nm": name, "dt": "20261026", "tm": "195900"}
                              for symbol, name in ((code, "삼성전자"), ("069500", "KODEX 200"))]
            ingestor = MarketDataIngestor(service._store, now_provider=lambda: now[0])
            original_rank = [{**row, "tm": "080000"} for row in original_items]
            MarketDataIngestor(service._store, now_provider=lambda: now[0].replace(hour=8, minute=0)).ingest(
                "ka00198", {"qry_tp": "1"}, {"item_inq_rank": original_rank},
            )
            observed = now[0].isoformat()
            members = {"observed_at": observed, "codes": [code, "069500"], "items": original_items}
            service._store.save_dataset_snapshot("top20_membership", day, observed, members,
                observation=ranking_observation(day, observed, members, now[0], source="nas-autonomous-ka00198"))
            async with source.router.lifespan_context(source):
                self.assertFalse(await workflow.run_day(day))
                self.assertFalse(outbox.exists())
                now[0] = datetime.fromisoformat(day + "T20:05:00+09:00")
                self.assertFalse(await workflow.run_day(day))
                self.assertFalse(outbox.exists())
                bars = {"open_pric": "100", "high_pric": "110", "low_pric": "90", "cur_prc": "105", "trde_qty": "1200"}
                ingestor.ingest("ka10080", {"stk_cd": code, "tic_scope": "1"},
                                {"stk_min_pole_chart_qry": [{**bars, "cntr_tm": "20261026155900"}]})
                ingestor.ingest("ka10081", {"stk_cd": code, "upd_stkpc_tp": "1"},
                                {"stk_dt_pole_chart_qry": [{**bars, "dt": "20261026"}]})
                with patch("kiwoom_monitor.infrastructure.central_content_client._result_urlopen",
                           side_effect=lose_first_receipt):
                    workflow.schedule(day)
                    await workflow.join()
                    self.assertEqual(day, workflow._last_backfill_day)
                    self.assertTrue(await asyncio.to_thread(lost_get.wait, 8))
                    [batch_path] = (outbox / day).glob("*.batch")
                    value = ObservationArchiveBatch.parse(batch_path.read_bytes())
                    ack_path = batch_path.with_name(batch_path.name + ".ack.json")
                    self.assertFalse(ack_path.exists())
                    archive_paths = tuple((outbox / "observations" / day).glob("*.batch"))
                    archives = {batch.kind: batch for batch in
                                (ObservationArchiveBatch.parse(path.read_bytes()) for path in archive_paths)}
                    self.assertEqual({"ranking", "minute_bar", "daily_bar", "top20_membership"}, set(archives))
            self.assertEqual("completed", source.state.shutdown_status["state"])
            recovery.assert_awaited_once()
            self.assertEqual("ka90008", recovery.await_args.args[0])
            [live_row, restored_row] = map(json.loads, value.document()["observations"].splitlines())
            self.assertEqual(live, live_row["payload"])
            self.assertEqual("20261026:RECOVERY:SOR", restored_row["snapshot_key"])
            self.assertEqual(["100030"], [row["trade_time"] for row in restored_row["payload"]["rows"]])
            self.assertNotIn("cur_prc", restored_row["payload"]["rows"][0])
            client = CentralContentClient.for_observation_archive(target_url, STREAM, ARCHIVE_TOKEN)
            first = await asyncio.to_thread(client.load_observation_archive, value.batch_id, source_stream=STREAM)
            self.assertEqual(("program_flow", 2, 1), (first["kind"], first["revision_count"], first["receipt_sequence"]))
            self.assertIsNone(first["first_source_accepted_sequence"])
            restart = create_app(settings)
            async with restart.router.lifespan_context(restart):
                async with asyncio.timeout(8):
                    while not (ack_path.is_file() and all(path.with_name(path.name + ".ack.json").is_file()
                                                          for path in archive_paths)):
                        await asyncio.sleep(0.02)
                self.assertEqual(first, json.loads(ack_path.read_bytes())["receipt"])
                self.assertEqual(value.raw, batch_path.read_bytes())
                restored = self.root / "restored-program-flow"
                receipt = await asyncio.to_thread(restore_observation_archive, self.target_connection, STREAM,
                                                  value.batch_id, restored)
                self.assertEqual(first, receipt)
                self.assertEqual(value.document()["observations"], (restored / "observations.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(3, len(restart.state.autonomous_top20_service._store.load_dataset_snapshots("program_flow", code)))
                for kind, batch in archives.items():
                    restored_path = self.root / ("restored-" + kind)
                    await asyncio.to_thread(restore_observation_archive, self.target_connection, STREAM,
                                            batch.batch_id, restored_path)
                    [row] = load_frozen_research_export(restored_path).observations
                    native = restart.state.autonomous_top20_service._store.load_observation_revisions(kind, limit=1)[0]
                    self.assertEqual(native, {key: value for key, value in row.items() if key != "ordinal"})
                    if kind in {"ranking", "top20_membership"}:
                        self.assertEqual(original_rank if kind == "ranking" else original_items, row["payload"]["items"])
            self.assertEqual([], target.state.query_authority_store.load_dataset_snapshots("program_flow", code))
            recovery.assert_awaited_once()

        with wire_server(target) as (target_url, _), \
                patch.object(KiwoomRestClient, "server_now", side_effect=lambda: now[0]), \
                patch.object(CentralRealtimeCollector, "start", new_callable=AsyncMock), \
                patch.object(AutonomousTop20Service, "_schedule_loop", new_callable=AsyncMock), \
                patch.object(CentralRestBroker, "request_unrecorded", new_callable=AsyncMock, return_value=flow) as recovery:
            save_connection(self.target_connection, target_url, ARCHIVE_TOKEN)
            asyncio.run(exercise(target_url, recovery))

    def test_program_flow_freeze_partitions_a_day_and_keeps_the_original_cut(self):
        store = SQLiteQueryStore(self.source, now_provider=lambda: CUT)
        store.initialize()
        self.addCleanup(store.close)
        outbox = self.root / "cut"
        archive = ProgramFlowArchive(store, outbox, self.target_connection, STREAM)
        for clock in ("140000", "140001", "140002"):
            store.save_dataset_snapshot("program_flow", "005930", f"20261026:REALTIME:{clock}", {"rows": [{"trade_time": clock}]})
        with patch("kiwoom_monitor.central_server.program_flow_archive._MAX_ENCODED_SNAPSHOTS_BYTES", 350):
            asyncio.run(archive.freeze_day("2026-10-26", CUT))
        paths = sorted(outbox.glob("2026-10-26/*.batch"))
        frozen = {path.name: path.read_bytes() for path in paths}
        self.assertEqual(2, len(frozen))
        self.assertEqual({"140000", "140001", "140002"}, {
            json.loads(row)["payload"]["rows"][0]["trade_time"] for raw in frozen.values()
            for row in ObservationArchiveBatch.parse(raw).document()["observations"].splitlines()
        })
        store.save_dataset_snapshot("program_flow", "005930", "20261026:REALTIME:140003", {"rows": []})
        asyncio.run(archive.freeze_day("2026-10-26", END))
        self.assertEqual(frozen, {path.name: path.read_bytes() for path in outbox.glob("2026-10-26/*.batch")})

    def test_program_flow_inspection_keeps_only_exact_recorded_samples_and_their_provenance(self):
        day = "2026-10-26"
        cutoff = datetime.fromisoformat(day + "T09:01:00+09:00")
        now = [cutoff]
        store = SQLiteQueryStore(self.source, now_provider=lambda: now[0])
        store.initialize()
        self.addCleanup(store.close)
        outbox = self.root / "cut"
        archive = ProgramFlowArchive(store, outbox, self.target_connection, STREAM)

        def save(code, clock, market="SOR", *, source="kiwoom_realtime_0w", key=None, row_market=None):
            row = {"market": row_market or market, "trade_time": clock, "source": source,
                   "available": True, "net_buy_quantity": -12, "net_buy_amount_million_won": None}
            store.save_dataset_snapshot("program_flow", code, key or f"20261026:REALTIME:{clock}",
                                        {"market": market, "rows": [row]})
            return row

        first = save("005930", "090000")
        last = save("005930", "090100")
        save("005930", "090010", "KRX")
        save("005930", "090020", row_market="NXT")
        save("005930", "090030", source="ka90008_after_close", key="20261026:RECOVERY:SOR")
        save("005930", "090040", key="20261026:REALTIME:090041")
        save("005930", "090200")
        save("000660", "090045")
        now[0] += timedelta(seconds=1)
        save("005930", "090050")
        asyncio.run(archive.freeze_day(day, cutoff.replace(hour=20, minute=5)))
        [path] = (outbox / day).glob("*.batch")
        raw = path.read_bytes()
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["inspect-program-flow", "--batch", str(path), "--symbol", "005930", "--market", "SOR",
                              "--recorded-through", "2026-10-26T00:01:00+00:00"])

        self.assertEqual(0, exit_code)
        result = json.loads(output.getvalue())
        self.assertEqual(("inspected", "RECORDED", "UNKNOWN", "UNVERIFIED"), (result["status"], result["state"],
                         result["coverage"], result["point_in_time_availability"]))
        self.assertEqual(cutoff.isoformat(), result["recorded_through"])
        self.assertEqual(archive_sha256(raw), result["source"]["content_sha256"])
        self.assertEqual([first, last], [sample["row"] for sample in result["samples"]])
        self.assertEqual(["2026-10-26T09:00:00+09:00", cutoff.isoformat()],
                         [sample["effective_at"] for sample in result["samples"]])
        self.assertEqual([cutoff.isoformat()] * 2, [sample["saved_at"] for sample in result["samples"]])
        missing = inspect_program_flow(path, symbol="005930", market="NXT", recorded_through=cutoff)
        self.assertEqual(("UNKNOWN", []), (missing["state"], missing["samples"]))
        with self.assertRaisesRegex(ValueError, "KST trading date"):
            inspect_program_flow(path, symbol="005930", market="SOR", recorded_through=cutoff + timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            inspect_program_flow(path, symbol="005930", market="SOR", recorded_through=cutoff.replace(tzinfo=None))
        self.assertEqual(raw, path.read_bytes())

    def test_automatic_cut_includes_next_morning_repairs_and_excludes_other_dates_and_product_bars(self):
        day = "2026-10-26"
        now = datetime.fromisoformat("2026-10-27T07:10:00+09:00")
        store = SQLiteQueryStore(self.source, now_provider=lambda: now)
        store.initialize()
        self.addCleanup(store.close)
        ingestor = MarketDataIngestor(store, now_provider=lambda: now)
        for code, when in (("005930", "20261026153000"), ("069500", "20261026153000"),
                           ("000660", "20261027100000")):
            ingestor.ingest("ka10080", {"stk_cd": code, "tic_scope": "1"}, {"stk_min_pole_chart_qry": [
                {"open_pric": "100", "high_pric": "110", "low_pric": "90", "cur_prc": "105",
                 "trde_qty": "1200", "cntr_tm": when},
            ]})
        outbox = self.root / "cut"
        archive = ProgramFlowArchive(store, outbox, self.target_connection, STREAM)
        asyncio.run(archive.freeze_observations(day, now, excluded_codes=frozenset({"069500"})))
        [path] = (outbox / "observations" / day).glob("*.batch")
        batch = ObservationArchiveBatch.parse(path.read_bytes())
        [row] = map(json.loads, batch.document()["observations"].splitlines())
        self.assertEqual(("minute_bar", "005930:KRX", now),
                         (batch.kind, row["subject"], datetime.fromisoformat(row["available_at"])))
        self.assertEqual(day, datetime.fromisoformat(row["effective_at"]).astimezone(KST).date().isoformat())
        self.assertTrue(store.load_observation_revisions("minute_bar", "069500:KRX"))

    def test_actual_ingest_export_append_reopen_and_research_restore_preserve_late_correction(self):
        source_app = query_app(input_settings(self._url(self.source)))
        source = source_app.state.query_authority_store
        target_app = query_app(receiver_settings(self._url(self.target)))
        first_file, second_file = self.root / "first.batch", self.root / "later.batch"
        # The broker's raw rank capture runs from 08:00 until 08:06 KST.
        start, cut, end = (moment + timedelta(minutes=5) for moment in (START, CUT, END))

        def ingest(at, stock, raw_time):
            MarketDataIngestor(source, now_provider=lambda: at).ingest("ka00198", {"qry_tp": "5"}, {
                "item_inq_rank": [{"stk_cd": stock, "stk_nm": "한글 종목", "dt": "20261026", "tm": raw_time}],
            })

        with wire_server(source_app) as (source_url, _), wire_server(target_app) as (target_url, _):
            save_connection(self.source_connection, source_url, INPUT)
            save_connection(self.target_connection, target_url, ARCHIVE_TOKEN)
            ingest(start + timedelta(minutes=1), "005930", "080100")
            ingest(start + timedelta(minutes=2), "000660", "080200")
            too_large = self.root / "not-published.batch"
            with patch("kiwoom_monitor.observation_archive.MAX_OBSERVATION_ARCHIVE_BYTES", 256), self.assertRaises(ObservationArchiveLimit):
                prepare_observation_archive(self.source_connection, STREAM, start, cut, too_large, kind="ranking", subject="5")
            self.assertFalse(too_large.exists())
            self.assertEqual([], list(self.root.glob("observation-export-*")))
            self.assertEqual(2, len(source.load_observation_revisions("ranking", "5")))
            first_id = prepare_observation_archive(self.source_connection, STREAM, start, cut, first_file, kind="ranking", subject="5")["batch_id"]
            original = first_file.read_bytes()
            first = deliver_observation_archive(first_file, self.target_connection)["receipt"]
            self.assertEqual((1, 1, 2, 2, SQLITE_ARCHIVE_DURABILITY), (first["receipt_sequence"],
                first["first_source_accepted_sequence"], first["last_source_accepted_sequence"],
                first["revision_count"], first["durability"]))
            ack = first_file.with_name(first_file.name + ".ack.json")
            first_ack = ack.read_bytes()
            with patch.object(CentralContentClient, "_request", side_effect=AssertionError("unexpected resend after first ACK")):
                self.assertEqual(first, deliver_observation_archive(first_file, self.target_connection)["receipt"])
            self.assertEqual(first_ack, ack.read_bytes())

            # Correction arrives later, while its provider event time stays in the original cut.
            ingest(cut + timedelta(minutes=1), "035420", "080100")
            second_id = prepare_observation_archive(self.source_connection, STREAM, cut, end, second_file, kind="ranking", subject="5")["batch_id"]
            second = deliver_observation_archive(second_file, self.target_connection)["receipt"]
            self.assertNotEqual(first_id, second_id)
            self.assertEqual((2, 3, 3, 1), (second["receipt_sequence"], second["first_source_accepted_sequence"],
                second["last_source_accepted_sequence"], second["revision_count"]))
            self.assertEqual(original, first_file.read_bytes())
            self.assertEqual(3, len(source.load_observation_revisions("ranking", "5")))
            self.assertEqual([], target_app.state.query_authority_store.load_observation_revisions("ranking", "5"))

        # A fresh application/native owner must see the same two first receipts and bytes.
        with wire_server(query_app(receiver_settings(self._url(self.target)))) as (reopened_url, _):
            restore_connection = self.root / "reopened-connection.json"
            save_connection(restore_connection, reopened_url, ARCHIVE_TOKEN)
            out_first, out_second = self.root / "frozen-first", self.root / "frozen-correction"
            self.assertEqual(first, restore_observation_archive(restore_connection, STREAM, first_id, out_first))
            self.assertEqual(second, restore_observation_archive(restore_connection, STREAM, second_id, out_second))
            for directory, source_bytes in ((out_first, original), (out_second, second_file.read_bytes())):
                packed = json.loads(source_bytes)
                self.assertEqual(packed["manifest"].encode("utf-8"), (directory / "manifest.json").read_bytes())
                self.assertEqual(packed["observations"].encode("utf-8"), (directory / "observations.jsonl").read_bytes())
            first_row = load_frozen_research_export(out_first).observations[0]
            revised, = load_frozen_research_export(out_second).observations
            self.assertEqual("005930", first_row["payload"]["items"][0]["stk_cd"])
            self.assertEqual("035420", revised["payload"]["items"][0]["stk_cd"])
            self.assertEqual(first_row["revision_id"], revised["revision_of"])
            self.assertEqual(first_row["effective_at"], revised["effective_at"])
            self.assertNotEqual(first_row["available_at"], revised["available_at"])

    def test_minute_daily_and_top20_actual_writers_round_trip_without_upgrading_partial_or_unknown(self):
        source_app = query_app(input_settings(self._url(self.source)))
        source = source_app.state.query_authority_store
        target_app = query_app(receiver_settings(self._url(self.target)))
        observed = datetime(2026, 10, 26, 9, 0, tzinfo=KST)
        query_at = observed + timedelta(seconds=30)
        day, symbol = "2026-10-26", "005930"

        async def write_top20():
            rows = [{"dt": "20261026", "tm": "090000", "stk_cd": f"{100000 + rank:06d}",
                     "stk_nm": f"종목{rank}", "bigd_rank": str(rank)} for rank in range(1, 21)]
            request = AsyncMock(return_value=BrokerResult({"item_inq_rank": rows}, False, ""))
            service = AutonomousTop20Service(SimpleNamespace(request=request), RealtimeHub(), source,
                                             now_provider=lambda: observed)
            # These unrelated asynchronous side paths would talk to a catalog or subscriptions.
            with patch.object(service, "_schedule_market_catalog"), patch.object(service, "_schedule_subscription_update"):
                self.assertEqual(tuple(row["stk_cd"] for row in rows), await service.refresh_ranking_once(observed))
            request.assert_awaited_once_with("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})

        ingestor = MarketDataIngestor(source, now_provider=lambda: query_at)
        bars = {"open_pric": "70000", "high_pric": "71000", "low_pric": "69000",
                "cur_prc": "70500", "trde_qty": "1234"}
        ingestor.ingest("ka10080", {"stk_cd": symbol}, {"stk_min_pole_chart_qry": [
            {**bars, "cntr_tm": "20261026090000"},
        ]})
        ingestor.ingest("ka10081", {"stk_cd": symbol, "upd_stkpc_tp": "1"}, {"stk_dt_pole_chart_qry": [
            {**bars, "dt": "20261026", "trde_prica": "9876"}, {**bars, "dt": "20261027", "trde_prica": "8765"},
        ]})
        partial_at = observed + timedelta(minutes=2)
        live = {"trading_date": day, "minute": "09:01", "code": symbol, "market": "KRX",
                "open": 70100, "high": 70200, "low": 70000, "close": 70100, "volume": 33,
                "trade_value_million_won": 2, "updated_at": partial_at.timestamp(), "operation_id": "archive-partial-open"}
        observation = minute_bar_observation(live, origin=ObservationOrigin.REALTIME,
            completeness=DataCompleteness.IN_PROGRESS, source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL)
        source.save_minute_bars([live], observations=[(bar_observation_key(observation), observation)])
        source.finalize_minute_bars([{**{key: live[key] for key in ("trading_date", "minute", "code", "market")},
            "available_at": partial_at.timestamp(), "capture_quality": "partial", "finalization_source": "timer",
            "operation_id": "archive-partial-close"}])
        asyncio.run(write_top20())

        with wire_server(source_app) as (source_url, _), wire_server(target_app) as (target_url, _):
            save_connection(self.source_connection, source_url, INPUT)
            save_connection(self.target_connection, target_url, ARCHIVE_TOKEN)
            for target_sequence, (kind, subject) in enumerate((
                ("minute_bar", f"{symbol}:KRX"), ("daily_bar", f"{symbol}:KRX"), ("top20_membership", day),
            ), start=1):
                with self.subTest(kind=kind):
                    batch_path, output = self.root / f"{kind}.batch", self.root / kind
                    original = {row["revision_id"]: row for row in source.load_observation_revisions(kind, subject)}
                    self.assertTrue(original)
                    batch = prepare_observation_archive(self.source_connection, STREAM, observed,
                        observed + timedelta(minutes=5), batch_path, kind=kind, subject=subject)
                    receipt = deliver_observation_archive(batch_path, self.target_connection)["receipt"]
                    self.assertEqual((kind, target_sequence, len(original)),
                        (receipt["kind"], receipt["receipt_sequence"], receipt["revision_count"]))
                    self.assertEqual(receipt, restore_observation_archive(self.target_connection, STREAM, batch["batch_id"], output))
                    restored = load_frozen_research_export(output).observations
                    self.assertEqual(original, {row["revision_id"]: {key: value for key, value in row.items() if key != "ordinal"}
                                                for row in restored})
                    if kind == "minute_bar":
                        partial = next(row for row in restored if row["completeness"] == "partial")
                        self.assertEqual(("unknown", "partial", partial_at), (partial["unit"],
                            partial["payload"]["capture_quality"], datetime.fromisoformat(partial["available_at"])))
                    elif kind == "daily_bar":
                        self.assertEqual({"in_progress", "unconfirmed"}, {row["completeness"] for row in restored})
                        self.assertEqual({query_at}, {datetime.fromisoformat(row["available_at"]) for row in restored})
                    else:
                        member, = restored
                        self.assertEqual({"kind": "ranking", "subject": "5", "observation_key": "2026-10-26T09:00:00"},
                                         member["source_ref"])
                        self.assertEqual(20, len(member["payload"]["codes"]))

    def test_lost_response_retains_pending_and_original_then_reuses_first_receipt(self):
        app = query_app(receiver_settings(self._url(self.target)))
        batch_path = self.root / "sender.batch"
        value = fixture_batch()
        batch_path.write_bytes(value.raw)
        ack_path = batch_path.with_name(batch_path.name + ".ack.json")
        target_path = batch_path.with_name(batch_path.name + ".target.json")

        def lose_responses(request, **kwargs):
            with _result_urlopen(request, **kwargs) as accepted:
                accepted.read()  # The actual HTTP handler already waited for COMMIT.
            raise TimeoutError("test transport discarded response")

        with wire_server(app) as (target_url, _):
            save_connection(self.target_connection, target_url, ARCHIVE_TOKEN)
            with patch("kiwoom_monitor.infrastructure.central_content_client._result_urlopen", side_effect=lose_responses) as calls:
                self.assertEqual("pending", deliver_observation_archive(batch_path, self.target_connection)["status"])
                self.assertEqual(["POST", "GET"], [entry.args[0].method for entry in calls.call_args_list])
            self.assertTrue(target_path.is_file())
            self.assertNotIn(ARCHIVE_TOKEN, target_path.read_text(encoding="utf-8"))
            self.assertFalse(ack_path.exists())
            self.assertEqual(value.raw, batch_path.read_bytes())
            first = CentralContentClient(target_url, ARCHIVE_TOKEN).load_observation_archive(value.batch_id, source_stream=STREAM)
            self.assertEqual(1, first["receipt_sequence"])
            child = subprocess.run([sys.executable, "-B", "-m", "kiwoom_monitor.observation_archive", "send",
                "--batch", str(batch_path), "--target-connection", str(self.target_connection)],
                capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(0, child.returncode, child.stderr)
            self.assertEqual({"status": "delivered", "receipt": first}, json.loads(child.stdout))
            self.assertEqual(first, json.loads(ack_path.read_bytes())["receipt"])
            self.assertEqual(value.raw, batch_path.read_bytes())

            def lose_only_post(request, **kwargs):
                if request.method == "GET":
                    return _result_urlopen(request, **kwargs)
                return lose_responses(request, **kwargs)

            next_path = self.root / "second-sender.batch"
            next_value = fixture_batch("e922c3e3-0a79-4044-8c46-d8e3c96c93f0")
            next_path.write_bytes(next_value.raw)
            with patch("kiwoom_monitor.infrastructure.central_content_client._result_urlopen", side_effect=lose_only_post) as calls:
                recovered = deliver_observation_archive(next_path, self.target_connection)
                self.assertEqual(["POST", "GET"], [entry.args[0].method for entry in calls.call_args_list])
            self.assertEqual("delivered", recovered["status"])
            self.assertEqual(2, recovered["receipt"]["receipt_sequence"])

    def test_authority_incomplete_and_conflicting_uploads_cannot_write(self):
        app = query_app(receiver_settings(self._url(self.target)))
        value = fixture_batch()
        own = {"Authorization": "Bearer " + ARCHIVE_TOKEN,
               "X-Kiwoom-Expected-Archive-Stream": encode_reported_result_owner(STREAM)}
        query = {"sha256": value.content_sha256}
        with wire_server(app) as (base, _), httpx.Client(base_url=base, timeout=5) as wire:
            for token in (ADMIN, QUERY, INPUT, RESULT, BODY):
                self.assertEqual(401, wire.post(OBSERVATION_ARCHIVE_PATH, params=query,
                    content=value.raw, headers={**own, "Authorization": "Bearer " + token}).status_code)
            self.assertEqual(409, wire.post(OBSERVATION_ARCHIVE_PATH, params=query,
                content=value.raw, headers={**own, "X-Kiwoom-Expected-Archive-Stream": encode_reported_result_owner(OTHER_STREAM)}).status_code)
            cross_stream = fixture_batch(source_stream=OTHER_STREAM)
            self.assertEqual(409, wire.post(OBSERVATION_ARCHIVE_PATH, params={"sha256": cross_stream.content_sha256},
                content=cross_stream.raw, headers=own).status_code)
            mixed = value.document()
            mixed["manifest"] = archive_json({**json.loads(mixed["manifest"]), "kinds": ["ranking", "daily_bar"]}).decode("utf-8")
            mixed_raw = archive_json(mixed)
            self.assertEqual(400, wire.post(OBSERVATION_ARCHIVE_PATH, params={"sha256": archive_sha256(mixed_raw)},
                content=mixed_raw, headers=own).status_code)
            for path in ("/api/v1/settings/credentials", "/api/v3/kiwoom/accounts", "/api/v1/capabilities"):
                self.assertEqual(401, wire.get(path, headers=own).status_code)

            # An actual stream disconnect before its declared body length reaches the server.
            port = urlsplit(base).port
            with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                raw.sendall((f"POST {OBSERVATION_ARCHIVE_PATH}?sha256={value.content_sha256} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\nContent-Length: {len(value.raw)}\r\n"
                    f"Authorization: Bearer {ARCHIVE_TOKEN}\r\n"
                    f"X-Kiwoom-Expected-Archive-Stream: {encode_reported_result_owner(STREAM)}\r\n\r\n"
                    ).encode("ascii") + value.raw[:len(value.raw) // 2])
            self.assertEqual(400, wire.post(OBSERVATION_ARCHIVE_PATH, params=query,
                content=value.raw[:len(value.raw) // 2], headers=own).status_code)
            chunk = b"x" * (1024 * 1024)
            over_budget = (chunk if index < MAX_OBSERVATION_ARCHIVE_BYTES // len(chunk) else b"x"
                           for index in range(MAX_OBSERVATION_ARCHIVE_BYTES // len(chunk) + 1))
            self.assertEqual(413, wire.post(OBSERVATION_ARCHIVE_PATH, params=query, content=over_budget, headers=own).status_code)
            receipt_path = f"{OBSERVATION_ARCHIVE_PATH}/{value.batch_id}/receipt"
            self.assertEqual(404, wire.get(receipt_path, headers=own).status_code)
            first = wire.post(OBSERVATION_ARCHIVE_PATH, params=query, content=value.raw, headers=own)
            self.assertEqual(200, first.status_code, first.text)
            conflict = fixture_batch(code="000660")
            self.assertEqual(409, wire.post(OBSERVATION_ARCHIVE_PATH, params={"sha256": conflict.content_sha256},
                content=conflict.raw, headers=own).status_code)
            self.assertEqual(404, wire.get(receipt_path, headers={"Authorization": "Bearer " + OTHER_TOKEN,
                "X-Kiwoom-Expected-Archive-Stream": encode_reported_result_owner(OTHER_STREAM)}).status_code)
            self.assertEqual(first.json(), wire.get(receipt_path, headers=own).json())
            with closing(sqlite3.connect(self.target)) as connection:
                self.assertEqual([(value.raw,)], connection.execute("SELECT batch_body FROM central_observation_archive_batches").fetchall())

    def test_receiver_opt_in_default_schema_and_sqlite_precommit_failure(self):
        default = query_app(input_settings(self._url(self.target)))
        with wire_server(default) as (base, _), httpx.Client(base_url=base, timeout=5) as wire:
            self.assertEqual(404, wire.get(f"{OBSERVATION_ARCHIVE_PATH}/{fixture_batch().batch_id}").status_code)
            with closing(sqlite3.connect(self.target)) as connection:
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='central_observation_archive_batches'").fetchone())
        store = SQLiteQueryStore(self.target, now_provider=lambda: END)
        self.addCleanup(store.close)
        store.prepare_observation_archive()
        store.prepare_observation_archive()
        original = store._connection

        @contextmanager
        def fail_before_commit():
            with original() as connection:
                yield connection
                self.assertEqual(("wal",), connection.execute("PRAGMA journal_mode").fetchone())
                self.assertEqual((2,), connection.execute("PRAGMA synchronous").fetchone())
                self.assertEqual((1,), connection.execute("SELECT count(*) FROM central_observation_archive_batches").fetchone())
                raise OSError("fixture interruption before COMMIT")

        value = fixture_batch()
        with patch.object(store, "_connection", fail_before_commit), self.assertRaises(OSError):
            store.accept_observation_archive(STREAM, value.raw, value.content_sha256)
        self.assertIsNone(store.load_observation_archive(STREAM, value.batch_id))
        receipt = store.accept_observation_archive(STREAM, value.raw, value.content_sha256)
        self.assertEqual((1, END.isoformat()), (receipt["receipt_sequence"], receipt["accepted_at"]))
        self.assertEqual(receipt, store.load_observation_archive(STREAM, value.batch_id))

    def test_archive_grant_environment_disjoint_and_secret_free(self):
        env = {"MONITOR_SERVER_ACCESS_TOKEN": ADMIN, "MONITOR_SERVER_OBSERVATION_ARCHIVE_GRANTS_JSON":
            json.dumps([{"owner": STREAM, "token": ARCHIVE_TOKEN}])}
        self.assertFalse(input_settings().program_flow_archive_target_file)
        env.update(MONITOR_SERVER_PROGRAM_ARCHIVE_TARGET_FILE=str(self.target_connection),
                   MONITOR_SERVER_PROGRAM_ARCHIVE_SOURCE_STREAM=STREAM)
        with patch.dict(os.environ, env, clear=True):
            active = CentralServerSettings.from_environment()
        self.assertEqual((ProducerGrant(STREAM, ARCHIVE_TOKEN),), active.observation_archive_grants)
        self.assertEqual(str(self.target_connection), active.program_flow_archive_target_file)
        self.assertEqual(STREAM, active.program_flow_archive_source_stream)
        self.assertNotIn(str(self.target_connection), repr(active))
        with self.assertRaises(ValueError):
            replace(active, program_flow_archive_source_stream="")
        self.assertNotIn(ARCHIVE_TOKEN, repr(active))
        for reused in (ADMIN, QUERY, INPUT, RESULT, BODY):
            with self.subTest(reused=reused), self.assertRaises(ValueError):
                replace(input_settings(), observation_archive_grants=(ProducerGrant(STREAM, reused),))

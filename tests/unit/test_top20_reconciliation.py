from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import unittest
import threading
from unittest.mock import patch

from kiwoom_monitor.domain.top20_reconciliation import (
    RECONCILED_KIND, Top20ReconciliationInput, bar_fingerprint, build_top20_reconciliation,
    cohort_weights, display_statistics, metadata_fingerprint, minute_keys, select_top20_series,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult

DAY = "2026-10-01"
MINUTE = DAY + "T09:00"
NOW = datetime.fromisoformat(DAY + "T20:06:00+09:00")


def pair(code="A", amount=10000, *, v2=True, venue="KRX"):
    bar = {"trading_date": DAY, "minute": "09:00", "code": code, "market": venue,
           "open": 100000, "high": 100000, "low": 100000, "close": 100000,
           "volume": amount * 10, "trade_value_million_won": amount}
    meta = {"venue": venue, "unit": "million_won", "value_kind": "estimated", "origin": "query",
            "completeness": "complete", "source": "kiwoom-ka10080;trade_value=ohlcv_estimate"}
    result = {"bars": {"09:00": bar}, "metadata": {"09:00": meta}, "coverage": {
        "kind": "minute", "scope": "full_day", "as_of": DAY, "window_closed": True, "session_finalized": True}}
    if v2:
        result["coverage"].update(schema_version="minute_backfill_coverage/v2", expected_minutes=["09:00"],
            bars_fingerprint=bar_fingerprint(result["bars"]), metadata_fingerprint=metadata_fingerprint(result["metadata"]))
    return result


def inputs(*, full=False, v2=True):
    cohorts = {key: {"cohort_segments": [[key + ":00", ["A"]]], "capture_state": "partial"}
               for key in (minute_keys(DAY) if full else [MINUTE])}
    return Top20ReconciliationInput(DAY, cohorts, {"A:KRX": pair(v2=v2)}, {"A": False}, {"A": "KOSPI"}, NOW.isoformat())


class AllocationTests(unittest.TestCase):
    def test_half_entry_exit_and_full_membership(self):
        data = inputs()
        data.cohorts[MINUTE]["cohort_segments"] = [[MINUTE + ":00", ["A", "B"]], [MINUTE + ":30", ["A", "C"]]]
        data.pairs.update({"B:KRX": pair("B", 8000), "C:KRX": pair("C", 4000)})
        data.eligibility.update(B=False, C=False)
        result = build_top20_reconciliation(data, available_at=NOW.isoformat())
        row = next(row for row in result["rows"] if row["minute"] == MINUTE)
        self.assertEqual(160, row["total"])
        self.assertEqual("derived_from_estimate", result["value_kind"])

    def test_same_boundary_last_wins_and_duplicate_codes_count_once(self):
        self.assertEqual({"B": 1}, cohort_weights(MINUTE, {"cohort_segments": [
            [MINUTE + ":00", ["A"]], [MINUTE + ":00", ["B", "B"]]]}))

    def test_invalid_boundaries_and_first_half_are_not_filled(self):
        for segments in ([[MINUTE + ":30", ["A"]]], [[MINUTE + ":32", ["A"]]],
                         [[DAY + "T08:59:30", ["A"]]], [[MINUTE + ":30", ["A"]], [MINUTE + ":00", ["A"]]]):
            with self.subTest(segments=segments), self.assertRaises(ValueError):
                cohort_weights(MINUTE, {"cohort_segments": segments})

    def test_partial_live_capture_does_not_invalidate_valid_cohorts(self):
        result = build_top20_reconciliation(inputs(full=True), available_at=NOW.isoformat())
        self.assertEqual(720, result["coverage"]["full_day"]["complete"])
        self.assertEqual("complete", result["status"])

    def test_old_receipt_cannot_prove_absent_minutes_zero(self):
        result = build_top20_reconciliation(inputs(full=True, v2=False), available_at=NOW.isoformat())
        self.assertEqual(1, result["coverage"]["full_day"]["complete"])
        self.assertIsNone(result["rows"][0]["total"])

    def test_changed_bar_or_metadata_revokes_zero_evidence(self):
        for target, key, changed in (("bars", "trade_value_million_won", 20000), ("metadata", "origin", "realtime")):
            data = inputs(full=True)
            data.pairs["A:KRX"][target]["09:00"][key] = changed
            result = build_top20_reconciliation(data, available_at=NOW.isoformat())
            self.assertEqual("unconfirmed", result["rows"][0]["status"])

    def test_unknown_nxt_is_not_false_and_sor_is_ignored(self):
        data = inputs()
        data.eligibility.clear()
        data.pairs["A:SOR"] = pair(venue="SOR")
        result = build_top20_reconciliation(data, available_at=NOW.isoformat())
        self.assertEqual(0, result["coverage"]["regular"]["complete"])

    def test_nxt_bar_is_positive_venue_evidence(self):
        data = inputs()
        data.eligibility.clear()
        data.pairs["A:NXT"] = pair(amount=3000, venue="NXT")
        result = build_top20_reconciliation(data, available_at=NOW.isoformat())
        self.assertEqual(130, next(row["total"] for row in result["rows"] if row["minute"] == MINUTE))

    def test_zero_or_unverified_nxt_bar_does_not_infer_eligibility(self):
        for amount, metadata in ((0, True), (3000, False)):
            data = inputs()
            data.eligibility.clear()
            data.pairs["A:NXT"] = pair(amount=amount, venue="NXT")
            if not metadata:
                data.pairs["A:NXT"]["metadata"].clear()
            self.assertEqual(0, build_top20_reconciliation(data, available_at=NOW.isoformat())["coverage"]["regular"]["complete"])

    def test_corrupt_derived_quality_is_not_selected(self):
        value = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        for field, bad in (("coverage", {}), ("rows", [None]), ("rows", [])):
            corrupted = dict(value, **{field: bad})
            self.assertEqual("realtime", select_top20_series(DAY, [], corrupted)["basis"])
        value["rows"][60]["total"] = 999
        self.assertEqual("realtime", select_top20_series(DAY, [], value)["basis"])

    def test_nan_negative_and_metadata_missing_are_not_complete(self):
        for amount in (-1, float("nan")):
            data = inputs(v2=False)
            data.pairs["A:KRX"]["bars"]["09:00"]["trade_value_million_won"] = amount
            if amount != amount:
                with self.assertRaises(ValueError):
                    build_top20_reconciliation(data, available_at=NOW.isoformat())
            else:
                self.assertEqual(0, build_top20_reconciliation(data, available_at=NOW.isoformat())["coverage"]["regular"]["complete"])
        data = inputs(v2=False)
        data.pairs["A:KRX"]["metadata"] = {}
        self.assertEqual(0, build_top20_reconciliation(data, available_at=NOW.isoformat())["coverage"]["regular"]["complete"])

    def test_unknown_listing_market_keeps_total_in_unknown_bucket(self):
        data = inputs()
        data.markets.clear()
        result = build_top20_reconciliation(data, available_at=NOW.isoformat())
        self.assertEqual([0, 0, 100], next(row["market_values"] for row in result["rows"] if row["minute"] == MINUTE))

    def test_generation_ignores_receipt_refresh_time(self):
        data = inputs()
        first = build_top20_reconciliation(data, available_at=NOW.isoformat())
        data.pairs["A:KRX"]["coverage"]["completed_at"] = "new time"
        second = build_top20_reconciliation(data, available_at="later")
        self.assertEqual(first["generation"], second["generation"])

    def test_partial_total_is_not_daily_total_and_zero_minutes_are_samples(self):
        partial = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        comparison = display_statistics(partial, {})["comparisons"][0]
        self.assertIsNone(comparison["top20_eok"])
        self.assertEqual(100, comparison["known_top20_eok"])
        full = build_top20_reconciliation(inputs(full=True), available_at=NOW.isoformat())
        statistics = display_statistics(full, {})
        self.assertEqual(100, statistics["comparisons"][0]["top20_eok"])
        self.assertEqual(60, next(row["sample_count"] for row in statistics["hourly"] if row["hour"] == "08:00"))

    def test_selection_keeps_realtime_and_derived_separate(self):
        derived = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        raw = [{"minute": MINUTE, "capture_state": "realtime_complete", "market_values": [999, 0, 0]}]
        self.assertIs(derived, select_top20_series(DAY, raw, derived))
        raw_series = select_top20_series(DAY, raw, derived, basis="realtime")
        self.assertEqual(999, next(row["total"] for row in raw_series["rows"] if row["minute"] == MINUTE))


class NativeStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:"))
        self.store.initialize()

    def tearDown(self):
        self.store.close()

    def seed(self):
        ingest = MarketDataIngestor(self.store, now_provider=lambda: NOW)
        ingest.ingest("ka10080", {"stk_cd": "A", "base_dt": DAY.replace("-", ""), "tic_scope": "1"}, {
            "stk_min_pole_chart_qry": [{"cntr_tm": "20261001090000", "open_pric": "100000",
                "high_pric": "100000", "low_pric": "100000", "cur_prc": "100000", "trde_qty": "100000"}]})
        self.store.save_dataset_snapshot("nxt_eligibility", "A", DAY, {"observed_at": NOW.isoformat(), "enabled": False})
        self.store.upsert_documents("stock_catalog", [{"owner": "krx", "key": "A", "document": {"as_of": DAY, "market": "KOSPI"}}])
        self.store.save_dataset_snapshot("top20_index", DAY, MINUTE, {
            "minute": MINUTE, "capture_state": "partial", "market_values": [1, 0, 0],
            "cohort_segments": [[MINUTE + ":00", ["A"]]]})
        self.store.upsert_documents("market_data_coverage", [{"owner": DAY + ":A:KRX", "key": "complete", "document": pair()["coverage"]}])

    def test_native_input_read_keeps_values_metadata_and_historical_classification(self):
        self.seed()
        data = self.store.load_top20_reconciliation_inputs(DAY)
        result = build_top20_reconciliation(data, available_at=NOW.isoformat())
        self.assertEqual(100, next(row["total"] for row in result["rows"] if row["minute"] == MINUTE))
        self.assertEqual(False, data.eligibility["A"])
        self.assertEqual("KOSPI", data.markets["A"])

    def test_coverage_inputs_batch_queries_and_keep_sparse_venue_documents(self):
        from datetime import timedelta
        from kiwoom_monitor.central_server.database_top20_reconciliation import _inputs

        class PostgresShapeCursor:
            """Exercise the shared %s SQL shape against SQLite, not a live PG server."""
            def __init__(self, cursor):
                self.cursor = cursor
                self.statements = []

            def execute(self, statement, parameters):
                self.statements.append((statement, parameters))
                translated = statement.replace("b.trading_date::text", "b.trading_date").replace(
                    "to_char(b.minute,'HH24:MI')", "b.minute"
                ).replace("%s", "?")
                return self.cursor.execute(translated, parameters)

            def fetchall(self):
                return self.cursor.fetchall()

        # This is the bounded-read contract, including the existing 200-code boundary.
        for count, select_budget in ((0, 1), (3, 5), (20, 5), (200, 5), (201, 9)):
            with self.subTest(codes=count):
                store = SQLiteQueryStore(Path(":memory:"))
                store.initialize()
                try:
                    codes = [f"C{number:04}" for number in range(count)]
                    expected_cohorts, expected_pairs = {}, {}
                    for offset in range(0, count, 20):
                        minute = (datetime.fromisoformat(MINUTE) + timedelta(minutes=offset // 20)).isoformat(timespec="minutes")
                        cohort = {"cohort_segments": [[minute + ":00", codes[offset:offset + 20]]]}
                        expected_cohorts[minute] = cohort
                        store.save_dataset_snapshot("top20_index", DAY, minute, cohort)
                    documents = []
                    for number, code in enumerate(codes):
                        for venue in ("KRX", "NXT"):
                            if venue == "NXT" and number % 3:
                                continue
                            document = {} if venue == "NXT" and number % 6 == 0 else {
                                "as_of": DAY, "code": code, "venue": venue, "row": number,
                            }
                            expected_pairs[f"{code}:{venue}"] = {"bars": {}, "metadata": {}, "coverage": document}
                            documents.append({"owner": f"{DAY}:{code}:{venue}", "key": "complete", "document": document})
                        documents.extend((
                            {"owner": f"{DAY}:{code}:SOR", "key": "complete", "document": {"wrong": "venue"}},
                            {"owner": f"1999-01-01:{code}:KRX", "key": "complete", "document": {"wrong": "day"}},
                            {"owner": f"{DAY}:{code}:KRX", "key": "partial", "document": {"wrong": "key"}},
                        ))
                    if documents:
                        store.upsert_documents("market_data_coverage", documents)
                    statements = []
                    original_connect = store._connect

                    def connect():
                        connection = original_connect()
                        connection.set_trace_callback(statements.append)
                        return connection

                    with patch.object(store, "_connect", side_effect=connect) as connections, \
                            patch("kiwoom_monitor.central_server.database_top20_reconciliation.environment_value", return_value=NOW.isoformat()):
                        data = store.load_top20_reconciliation_inputs(DAY)
                    connections.assert_called_once_with()
                    normalized = [statement.lstrip().upper() for statement in statements]
                    self.assertEqual(select_budget, sum(statement.startswith("SELECT") for statement in normalized))
                    self.assertEqual(1, normalized.count("BEGIN"))
                    self.assertEqual(1, normalized.count("COMMIT"))
                    self.assertFalse(any(statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in normalized))
                    self.assertEqual(expected_cohorts, data.cohorts)
                    self.assertEqual(expected_pairs, data.pairs)
                    self.assertEqual(({}, {}, []), (data.eligibility, data.markets, data.sources))
                    self.assertEqual(NOW.isoformat(), data.input_as_of)
                    with store._connection() as connection, \
                            patch("kiwoom_monitor.central_server.database_top20_reconciliation.environment_value", return_value=NOW.isoformat()):
                        connection.execute("BEGIN")
                        cursor = PostgresShapeCursor(connection.cursor())
                        shaped = _inputs(cursor, DAY, "%s")
                    self.assertEqual(data, shaped)
                    self.assertEqual(select_budget, len(cursor.statements))
                    self.assertTrue(all("?" not in statement for statement, _ in cursor.statements))
                    self.assertTrue(all(len(parameters) <= 400 for _, parameters in cursor.statements))
                finally:
                    store.close()

    def test_coverage_read_failure_closes_transaction_and_preserves_published_day(self):
        import sqlite3

        self.seed()
        service = AutonomousTop20Service(object(), RealtimeHub(), self.store, now_provider=lambda: NOW)
        published = asyncio.run(service.reconcile_top20_day(DAY))
        with self.store._connection() as connection:
            connection.execute("UPDATE central_documents SET document_json='[]' "
                               "WHERE collection='market_data_coverage' AND owner=? AND document_key='complete'",
                               (DAY + ":A:KRX",))
        statements, connections = [], []
        original_connect = self.store._connect

        def connect():
            connection = original_connect()
            connection.set_trace_callback(statements.append)
            connections.append(connection)
            return connection

        with patch.object(self.store, "_connect", side_effect=connect), \
                self.assertRaisesRegex(ValueError, "TOP20 stored document is not an object"):
            asyncio.run(service.reconcile_top20_day(DAY))
        self.assertEqual(1, len(connections))
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        self.assertNotIn("COMMIT", [statement.upper() for statement in statements])
        self.assertEqual(published, self.store.load_dataset_snapshots(RECONCILED_KIND, DAY)[0]["payload"])
        self.store.upsert_documents("market_data_coverage", [{
            "owner": DAY + ":A:KRX", "key": "complete", "document": pair()["coverage"],
        }])
        self.assertEqual(published, asyncio.run(service.reconcile_top20_day(DAY)))

    def test_atomic_projection_cache_invalidation_and_raw_preservation(self):
        self.seed()
        raw_before = self.store.load_dataset_snapshots("top20_index", DAY)
        with patch("kiwoom_monitor.central_server.database_top20_reconciliation._top20_today", return_value=datetime(2026, 10, 12).date()):
            initial = self.store.load_top20_display_statistics(DAY, DAY)
            self.assertEqual(0, initial["comparisons"][0]["known_top20_eok"])
            self.assertTrue(self.store.load_dataset_snapshots("top20_statistics_display_day", DAY))
            value = build_top20_reconciliation(self.store.load_top20_reconciliation_inputs(DAY), available_at=NOW.isoformat())
            self.store.save_dataset_snapshot(RECONCILED_KIND, DAY, DAY, value)
            self.assertEqual([], self.store.load_dataset_snapshots("top20_statistics_display_day", DAY))
            current = self.store.load_top20_display_statistics(DAY, DAY)
            self.assertEqual(100, current["comparisons"][0]["known_top20_eok"])
            self.assertIsNone(current["comparisons"][0]["top20_eok"])
        self.assertEqual(raw_before, self.store.load_dataset_snapshots("top20_index", DAY))
        self.assertEqual("after_close_estimate", self.store.load_top20_series(DAY)["basis"])
        self.assertEqual("realtime", self.store.load_top20_series(DAY, basis="realtime")["basis"])

    def test_raw_and_denominator_writes_invalidate_display_cache(self):
        for kind, subject, key in (("top20_index", DAY, MINUTE), ("market_index_chart", "20261001:kospi", "daily")):
            with self.subTest(kind=kind):
                self.store.save_dataset_snapshot("top20_statistics_display_day", DAY, DAY, {})
                self.store.save_dataset_snapshot(kind, subject, key, {})
                self.assertEqual([], self.store.load_dataset_snapshots("top20_statistics_display_day", DAY))

    def test_published_after_close_day_does_not_append_research_revisions(self):
        value = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        self.store.save_dataset_snapshot(RECONCILED_KIND, DAY, DAY, value)
        self.assertEqual([], self.store.load_observation_revisions(RECONCILED_KIND, DAY))

    def test_derived_and_raw_calls_are_captured_without_changing_native_results(self):
        from kiwoom_monitor.central_server import diagnostic_trace
        value = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        with patch.object(diagnostic_trace, "input_token", return_value="test-trace"), \
                patch.object(diagnostic_trace, "emit_payload") as captured, \
                patch.object(diagnostic_trace, "emit"), patch.object(diagnostic_trace, "reject_input") as rejected:
            self.store.save_dataset_snapshot(RECONCILED_KIND, DAY, DAY, value)
            self.store.load_dataset_snapshots(RECONCILED_KIND, DAY)
            self.store.load_top20_series(DAY)
            self.store.load_top20_reconciliation_inputs(DAY)
            self.store.load_top20_display_statistics(DAY, DAY)
            self.assertEqual(5, captured.call_count)
            self.assertEqual(0, rejected.call_count)
            self.store.save_dataset_snapshot("top20_index", DAY, MINUTE, {"minute":MINUTE})
            self.assertEqual(6, captured.call_count)
            self.assertEqual(0, rejected.call_count)

    def test_shutdown_joins_cancelled_publication_before_returning(self):
        self.seed()
        entered, release = threading.Event(), threading.Event()
        original = self.store.save_dataset_snapshot
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("publication gate timed out")
            original(*args, **kwargs)
        service = AutonomousTop20Service(object(), RealtimeHub(), self.store, now_provider=lambda: NOW)
        async def run():
            parent = asyncio.create_task(service.reconcile_top20_day(DAY))
            service._fundamentals_tasks.add(parent)
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            close = asyncio.create_task(service.close())
            try:
                await asyncio.sleep(.05)
                self.assertFalse(close.done())
            finally:
                release.set()
                await close
            self.assertTrue(parent.cancelled())
        with patch.object(self.store, "save_dataset_snapshot", side_effect=blocked):
            asyncio.run(run())
        self.assertEqual("after_close_estimate", self.store.load_top20_series(DAY)["basis"])

    def test_service_publish_is_idempotent_and_failed_write_keeps_previous(self):
        self.seed()
        service = AutonomousTop20Service(object(), RealtimeHub(), self.store, now_provider=lambda: NOW)
        first = asyncio.run(service.reconcile_top20_day(DAY))
        before = self.store.load_dataset_snapshots(RECONCILED_KIND, DAY)
        with patch.object(self.store, "save_dataset_snapshot", side_effect=AssertionError("unchanged writes")):
            self.assertEqual(first, asyncio.run(service.reconcile_top20_day(DAY)))
        self.store.upsert_documents("stock_catalog", [{"owner": "krx", "key": "A", "document": {"as_of": DAY, "market": "KOSDAQ"}}])
        with patch.object(self.store, "save_dataset_snapshot", side_effect=RuntimeError("write failed")), self.assertRaises(RuntimeError):
            asyncio.run(service.reconcile_top20_day(DAY))
        self.assertEqual(before, self.store.load_dataset_snapshots(RECONCILED_KIND, DAY))

    def test_service_diagnostic_pause_and_open_window(self):
        service = AutonomousTop20Service(object(), RealtimeHub(), self.store, now_provider=lambda: NOW)
        with patch("kiwoom_monitor.central_server.autonomous_top20.is_paused", return_value=True):
            self.assertIsNone(asyncio.run(service.reconcile_top20_day(DAY)))
        service._now = lambda: NOW.replace(hour=19)
        with self.assertRaises(RuntimeError):
            asyncio.run(service.reconcile_top20_day(DAY))

    def test_pause_started_during_calculation_prevents_publication(self):
        service = AutonomousTop20Service(object(), RealtimeHub(), self.store, now_provider=lambda: NOW)
        paused = [False]
        def calculate(*args, **kwargs):
            paused[0] = True
            return build_top20_reconciliation(*args, **kwargs)
        with patch("kiwoom_monitor.central_server.autonomous_top20.is_paused", side_effect=lambda *_: paused[0]), \
                patch("kiwoom_monitor.central_server.autonomous_top20.build_top20_reconciliation", side_effect=calculate):
            self.assertIsNone(asyncio.run(service.reconcile_top20_day(DAY)))
        self.assertEqual([], self.store.load_dataset_snapshots(RECONCILED_KIND, DAY))

    def test_backfill_receipt_checks_actual_ingest_and_absent_minute(self):
        self.seed()
        store = self.store

        class Broker:
            async def request(self, api_id, path, body, **kwargs):
                payload = {"stk_min_pole_chart_qry": [{"cntr_tm": "20261001090000", "open_pric": "100000",
                    "high_pric": "100000", "low_pric": "100000", "cur_prc": "100000", "trde_qty": "100000"}]}
                MarketDataIngestor(store, now_provider=lambda: NOW).ingest(api_id, body, payload)
                return BrokerResult(payload, False, "", recording_succeeded=True)

        # Remove the old completion shortcut for this actual query test.
        self.store.upsert_documents("market_data_coverage", [{"owner": DAY + ":A:KRX", "key": "complete", "document": {}}])
        service = AutonomousTop20Service(Broker(), RealtimeHub(), self.store, now_provider=lambda: NOW)
        asyncio.run(service._backfill_minutes("A", DAY, "KRX"))
        receipt = self.store.load_documents("market_data_coverage", DAY + ":A:KRX", 1)[0]["document"]
        self.assertEqual("minute_backfill_coverage/v2", receipt["schema_version"])
        self.assertEqual(["09:00"], receipt["expected_minutes"])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_asgi_api_auth_basis_validation_and_legacy_default(self):
        import httpx
        from fastapi import FastAPI, Header, HTTPException
        from kiwoom_monitor.central_server.market_dataset_read_routes import create_market_dataset_read_router
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        app = FastAPI()

        def authorize(authorization: str = Header(default="")):
            if authorization != "Bearer test":
                raise HTTPException(401)

        app.include_router(create_market_dataset_read_router(store, authorize, None))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                target = "/api/v1/market/top20-series?trading_date=" + DAY
                self.assertEqual(401, (await client.get(target)).status_code)
                headers = {"Authorization": "Bearer test"}
                response = await client.get(target, headers=headers)
                self.assertEqual(200, response.status_code)
                self.assertEqual(720, len(response.json()["rows"]))
                self.assertEqual(422, (await client.get(target + "&basis=bad", headers=headers)).status_code)
                self.assertEqual(422, (await client.get("/api/v1/market/top20-series?trading_date=bad", headers=headers)).status_code)
                stats = "/api/v1/market/top20-statistics?start_date=" + DAY + "&end_date=" + DAY
                self.assertEqual([], (await client.get(stats, headers=headers)).json()["comparisons"])
                display = (await client.get(stats + "&basis=display", headers=headers)).json()
                self.assertIsNone(display["comparisons"][0]["top20_eok"])
        finally:
            store.close()


class ClientTests(unittest.TestCase):
    def test_legacy_statistics_cannot_claim_display_quality(self):
        import json
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient, CentralFeatureUnavailable
        calls = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps({"hourly": [], "comparisons": []}).encode()

        def opener(request, **kwargs):
            calls.append(request.full_url)
            return Response()

        client = RemoteKiwoomRestClient("http://nas", "token", opener=opener)
        self.assertEqual([], client.load_stored_top20_statistics(DAY, DAY)["comparisons"])
        self.assertNotIn("basis=", calls[0])
        with self.assertRaises(CentralFeatureUnavailable):
            client.load_stored_top20_statistics(DAY, DAY, basis="display")
        self.assertIn("basis=display", calls[1])

    def test_legacy_server_404_falls_back_only_to_stored_data(self):
        import io
        import json
        from urllib.error import HTTPError
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
        calls = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps({"snapshots": []}).encode()

        def opener(request, **kwargs):
            calls.append(request.full_url)
            if "top20-series" in request.full_url:
                raise HTTPError(request.full_url, 404, "unsupported", {}, io.BytesIO(b'{}'))
            return Response()

        client = RemoteKiwoomRestClient("http://nas", "token", opener=opener)
        self.assertEqual("realtime", client.load_stored_top20_series(DAY)["basis"])
        self.assertEqual(2, len(calls))
        self.assertFalse(any("kiwoom/query" in url for url in calls))

    def test_auth_and_server_failure_do_not_use_legacy_fallback(self):
        import io
        from urllib.error import HTTPError
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomApiError
        for status in (401, 500, 503):
            calls = []
            def opener(request, **kwargs):
                calls.append(request.full_url)
                raise HTTPError(request.full_url, status, "failure", {}, io.BytesIO(b'{}'))
            with self.subTest(status=status), self.assertRaises(KiwoomApiError):
                RemoteKiwoomRestClient("http://nas", "token", opener=opener).load_stored_top20_series(DAY)
            self.assertEqual(1, len(calls))


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_axis_begins_at_eight_with_explicit_missing_slots(self):
        from kiwoom_monitor.presentation.top20_trade_value import Top20TradeValueChart
        chart = Top20TradeValueChart()
        chart.set_series(build_top20_reconciliation(inputs(), available_at=NOW.isoformat()))
        self.assertEqual("08:00", chart._visible_bars()[0][0].strftime("%H:%M"))
        self.assertEqual(DAY, chart._minute_text)
        self.assertEqual("구성 이력 없음", chart._missing_labels[datetime.fromisoformat(DAY + "T08:00")])
        self.assertIn("장후 분봉 재계산", chart._status_text)
        chart.resize(900, 320)
        self.assertFalse(chart.grab().isNull())
        chart.close()

    def test_daily_missing_value_and_read_failure_are_not_local_or_zero_values(self):
        from types import SimpleNamespace
        from kiwoom_monitor.presentation.main_window import MainWindow
        from kiwoom_monitor.presentation.top20_trade_value import Top20TradeValueChart
        chart = Top20TradeValueChart()
        view = SimpleNamespace(_top20_view_mode="daily", _top20_trade_value_chart=chart)
        MainWindow._show_top20_nas_daily(view, {"comparisons": [{
            "trade_date": DAY, "top20_market_values": None, "coverage": {"complete": 12},
        }]})
        moment = datetime.fromisoformat(DAY)
        self.assertIn("12/391분", chart._missing_labels[moment])
        MainWindow._top20_daily_read_failed(view, "인증 실패")
        self.assertEqual([], chart._completed)
        self.assertIn("인증 실패", chart._status_text)
        view._top20_view_mode = "minute"
        chart._status_text = "다른 날짜 화면"
        MainWindow._top20_daily_read_failed(view, "지연된 오류")
        MainWindow._show_top20_nas_daily(view, {"comparisons": []})
        self.assertEqual("다른 날짜 화면", chart._status_text)
        chart.close()

    def test_larger_bucket_does_not_draw_partial_sum_as_complete(self):
        from kiwoom_monitor.presentation.top20_trade_value import Top20TradeValueChart
        chart = Top20TradeValueChart()
        value = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        for interval in (5, 60):
            chart.set_series(value, interval)
            self.assertIn(datetime.fromisoformat(MINUTE), chart._missing_labels)
            self.assertEqual(0, next(row[1] for row in chart._completed if row[0] == datetime.fromisoformat(MINUTE)))
        chart.close()

    def test_live_kst_clock_and_after_close_source_do_not_mix(self):
        from kiwoom_monitor.presentation.top20_trade_value import Top20TradeValueChart
        chart = Top20TradeValueChart()
        minute = datetime.fromisoformat(MINUTE + ":00+09:00")
        chart.set_series(select_top20_series(DAY, [], None), current_minute=minute, current_values=(5, 0, 0))
        self.assertEqual(datetime.fromisoformat(MINUTE), chart._current_minute)
        chart._view_end = 70
        self.assertEqual(1, sum(row[4] for row in chart._visible_bars()))
        chart.set_series(build_top20_reconciliation(inputs(), available_at=NOW.isoformat()),
                         current_minute=minute, current_values=(999, 0, 0))
        self.assertIsNone(chart._current_minute)
        self.assertEqual(720, len(chart._completed))
        chart.close()

    def test_stale_date_result_never_changes_chart_or_live_collector(self):
        from types import SimpleNamespace
        from kiwoom_monitor.presentation.main_window import MainWindow
        from kiwoom_monitor.presentation.top20_trade_value import Top20TradeValueChart
        value = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        collector = SimpleNamespace(completed=["live"])
        view = SimpleNamespace(_top20_view_date=NOW.date(), _top20_view_mode="minute",
                               _top20_trade_value_chart=Top20TradeValueChart(), _top20_collector=collector)
        MainWindow._display_top20_payload(view, datetime(2026, 10, 2).date(), value)
        self.assertFalse(hasattr(view, "_top20_selected_series"))
        MainWindow._display_top20_payload(view, NOW.date(), value)
        self.assertEqual(["live"], collector.completed)
        self.assertEqual(value, view._top20_selected_series)
        view._top20_trade_value_chart.close()

    def test_partial_statistics_display_known_sum_without_ratio(self):
        from PySide6.QtWidgets import QTextEdit
        from kiwoom_monitor.presentation.main_window import MainWindow
        content = QTextEdit()
        data = display_statistics(build_top20_reconciliation(inputs(), available_at=NOW.isoformat()),
                                  {"kospi_eok": 1000})
        MainWindow._render_top20_statistics(content, 7, data, "NAS")
        text = content.toPlainText()
        self.assertIn("확인 구간 합계", text)
        self.assertIn("1/391분", text)
        self.assertNotIn("10.00%", text)
        content.close()

    def test_one_missing_market_denominator_does_not_show_ratio(self):
        from PySide6.QtWidgets import QTextEdit
        from kiwoom_monitor.presentation.main_window import MainWindow
        content = QTextEdit()
        data = display_statistics(build_top20_reconciliation(inputs(full=True), available_at=NOW.isoformat()),
                                  {"kospi_eok": 1000})
        MainWindow._render_top20_statistics(content, 7, data, "NAS")
        self.assertIn("전체시장 자료 없음", content.toPlainText())
        self.assertNotIn("10.00%", content.toPlainText())
        content.close()


if __name__ == "__main__":
    unittest.main()

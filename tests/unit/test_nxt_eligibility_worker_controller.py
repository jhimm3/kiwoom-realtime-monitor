from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService

from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import (
    NxtEligibilityWorker,
)
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.journal_bar_repository import JournalBarRepositoryMixin
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.presentation.nxt_eligibility_worker_controller import (
    NxtEligibilityWorkerController,
)


class FakeNxtEligibilityWorker(NxtEligibilityWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class NxtEligibilityWorkerControllerTests(unittest.TestCase):
    def test_direct_premarket_worker_saves_bulk_flags_and_restart_uses_them_without_tr(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "monitor.sqlite3"
        database = Database(path)
        database.initialize()
        settings = database.settings
        stocks = StockRepository(path)
        stocks.upsert_many((("005930", "삼성전자옛표기", "KOSPI"), ("088980", "맥쿼리인프라", "KOSPI")))
        client = Mock()
        client.request_with_continuation.side_effect = [
            ({"list": [
                {"code": "005930", "name": "삼성전자", "marketCode": "0", "nxtEnable": "Y"},
                {"code": "069500", "marketCode": "8", "nxtEnable": "Y"},
            ]}, True, "page-2"),
            ({"list": [
                {"code": "000660", "name": "SK하이닉스", "marketCode": "0", "nxtEnable": "N"},
                {"code": "580001", "marketCode": "60", "nxtEnable": "N"},
            ]}, False, ""),
            ({"list": [
                {"code": "348370", "name": "엔켐", "marketCode": "10", "nxtEnable": ""},
                {"code": "247540", "name": "에코프로비엠", "marketCode": "10", "nxtEnable": "Y"},
                {"code": "035420", "nxtEnable": "Y"},
            ]}, False, ""),
        ]
        now = [datetime.fromisoformat("2026-11-09T06:59:59+09:00")]
        service = NxtEligibilityService(client, catalog_settings=settings, catalog_store=stocks)
        self.assertFalse(service.preparation_due(now[0]))
        now[0] += timedelta(seconds=1)
        self.assertTrue(service.preparation_due(now[0]))
        NxtEligibilityWorker(service, (), lambda: now[0]).run()
        self.assertFalse(service.preparation_due(now[0]))
        self.assertEqual(["0", "0", "10"], [call.args[2]["mrkt_tp"] for call in client.request_with_continuation.call_args_list])
        self.assertEqual("page-2", client.request_with_continuation.call_args_list[1].kwargs["next_key"])

        now[0] = now[0].replace(hour=9)
        restored = NxtEligibilityService(client, catalog_settings=settings, catalog_store=stocks)
        self.assertTrue(restored.preparation_due(now[0]))
        restored.prepare(lambda: now[0], lambda: False)
        self.assertFalse(restored.preparation_due(now[0]))
        self.assertIs(restored.stored_flag("005930", lambda: now[0]), True)
        self.assertIs(restored.stored_flag("000660", lambda: now[0]), False)
        self.assertIs(restored.stored_flag("247540", lambda: now[0]), True)
        self.assertIsNone(restored.stored_flag("348370", lambda: now[0]))
        self.assertIsNone(restored.stored_flag("035420", lambda: now[0]))
        self.assertIsNone(restored.stored_flag("069500", lambda: now[0]))
        self.assertIsNone(restored.stored_flag("580001", lambda: now[0]))
        now[0] += timedelta(days=1)
        self.assertIsNone(restored.stored_flag("005930", lambda: now[0]))
        self.assertFalse(restored.preparation_due(now[0].replace(hour=20)))
        self.assertEqual(3, client.request_with_continuation.call_count)
        client.request.assert_not_called()
        self.assertEqual({
            ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("348370", "엔켐"), ("247540", "에코프로비엠"),
        }, set(JournalBarRepositoryMixin().load_monitor_stock_catalog(path)))
        self.assertEqual("088980", stocks.find_code_by_name("맥쿼리인프라"))

    def test_failed_second_market_keeps_current_list_then_next_complete_list_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            stocks = StockRepository(path)
            stocks.save_premarket_catalog("2026-11-09", {
                "stocks": {"304840": ("피플바이오", "KOSDAQ"), "005110": ("한창", "KOSPI")},
                "flags": {"304840": True},
            })
            stocks.save_alias("한창옛이름", "005110")
            now = datetime.fromisoformat("2026-11-10T08:00:00+09:00")
            client = Mock()
            kospi = ({"list": [
                {"code": "005935", "name": "삼성전자우", "marketCode": "0", "nxtEnable": "N"},
            ]}, False, "")
            client.request_with_continuation.side_effect = [
                kospi, TimeoutError("KOSDAQ"),
                kospi, ({"list": [
                    {"code": "304840", "name": "타임X AI", "marketCode": "10", "nxtEnable": "Y"},
                    {"code": "0147B0", "name": "래블업", "marketCode": "10"},
                ]}, False, ""),
            ]
            repo = JournalBarRepositoryMixin()
            service = NxtEligibilityService(client, catalog_settings=database.settings, catalog_store=stocks)
            with self.assertRaises(TimeoutError):
                service.prepare(lambda: now, lambda: False)
            self.assertEqual({("304840", "피플바이오"), ("005110", "한창")}, set(repo.load_monitor_stock_catalog(path)))
            self.assertIsNone(stocks.load_premarket_flags("2026-11-10"))

            self.assertFalse(service.preparation_due(now))
            now += timedelta(minutes=5)
            with patch("kiwoom_monitor.application.nxt_eligibility_service.monotonic",
                       return_value=service._retry_at + 1):
                service.prepare(lambda: now, lambda: False)
            stocks.cache_ranking_stocks((("304840", "피플바이오", "KOSDAQ"), ("005110", "한창", "KOSPI")))
            self.assertEqual({
                ("304840", "타임X AI"), ("005935", "삼성전자우"), ("0147B0", "래블업"),
            }, set(repo.load_monitor_stock_catalog(path)))
            self.assertEqual({"304840": True, "005935": False}, stocks.load_premarket_flags("2026-11-10"))
            self.assertIs(service.stored_flag("304840", lambda: now), True)
            self.assertIsNone(service.stored_flag("0147B0", lambda: now))
            self.assertEqual((("0147B0", "래블업"),), stocks.find_stock_candidates("래블업"))
            self.assertEqual((), stocks.find_stock_candidates("한창"))
            self.assertEqual("005110", stocks.find_code_by_name("한창옛이름"))
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual(("한창",), con.execute(
                    "SELECT name FROM stocks WHERE code='005110'"
                ).fetchone())
                self.assertEqual([("피플바이오", "타임X AI", "KIWOOM")], con.execute(
                    "SELECT old_name,new_name,source FROM stock_name_history WHERE stock_code='304840'"
                ).fetchall())
            self.assertEqual(4, client.request_with_continuation.call_count)

    def test_remote_worker_mirrors_central_catalog_without_kiwoom_tr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            client = Mock()
            catalog = {
                "stocks": {"005935": ("삼성전자우", "KOSPI"), "0147B0": ("래블업", "KOSDAQ")},
                "flags": {"005935": False},
            }
            now = datetime.fromisoformat("2026-11-10T09:00:00+09:00")
            service = NxtEligibilityService(client, catalog_store=stocks)
            client.load_stored_premarket_catalog.side_effect = [None, catalog]
            NxtEligibilityWorker(service, (), lambda: now).run()
            self.assertIsNone(stocks.load_premarket_flags("2026-11-10"))
            self.assertFalse(service.preparation_due(now))
            with patch("kiwoom_monitor.application.nxt_eligibility_service.monotonic",
                       return_value=service._retry_at + 1):
                NxtEligibilityWorker(service, (), lambda: now).run()
            self.assertEqual({("005935", "삼성전자우"), ("0147B0", "래블업")},
                             set(JournalBarRepositoryMixin().load_monitor_stock_catalog(path)))
            self.assertEqual({"005935": False}, stocks.load_premarket_flags("2026-11-10"))
            self.assertFalse(service.preparation_due(now))
            restarted = NxtEligibilityService(client, catalog_store=stocks)
            restarted.prepare(lambda: now, lambda: False)
            self.assertEqual(["2026-11-10", "2026-11-10"],
                             [call.args[0] for call in client.load_stored_premarket_catalog.call_args_list])
            client.request_with_continuation.assert_not_called()

    def test_missing_nas_document_fails_without_caching_negative_and_retries(self) -> None:
        client = Mock()
        client.load_stored_nxt_eligibility.side_effect = [{}, {"nxtEnable": "Y"}]
        worker = NxtEligibilityWorker(NxtEligibilityService(client), ("348370",))
        received, failures = [], []
        worker.received.connect(lambda code, enabled: received.append((code, enabled)))
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual([], received)
        self.assertEqual(1, len(failures))
        worker.run()
        self.assertEqual([("348370", True)], received)
        client.request.assert_not_called()

    def test_explicit_y_and_n_are_the_only_completed_eligibility_values(self) -> None:
        for raw, expected in (("Y", True), ("N", False), (" y ", True), ("n", False)):
            with self.subTest(raw=raw):
                client = Mock()
                client.load_stored_nxt_eligibility.return_value = {"nxtEnable": raw}
                self.assertEqual(expected, NxtEligibilityService(client).is_enabled("348370"))
                client.request.assert_not_called()
        for payload in ({}, {"nxtEnable": ""}, {"nxtEnable": None}, {"nxtEnable": "UNKNOWN"}):
            with self.subTest(payload=payload):
                client = Mock()
                client.load_stored_nxt_eligibility.return_value = payload
                with self.assertRaises(ValueError):
                    NxtEligibilityService(client).is_enabled("348370")
                client.request.assert_not_called()

    def test_start_forwards_result_failure_and_finished(self) -> None:
        worker = FakeNxtEligibilityWorker()
        controller = NxtEligibilityWorkerController(worker_factory=lambda _codes: worker)
        received: list[tuple[str, bool]] = []
        failures: list[str] = []
        finished: list[bool] = []
        controller.received.connect(lambda code, enabled: received.append((code, enabled)))
        controller.failed.connect(failures.append)
        controller.finished.connect(lambda: finished.append(True))

        self.assertTrue(controller.start(("005930",)))
        worker.received.emit("005930", True)
        worker.failed.emit("failed")
        worker.running = False
        worker.finished.emit()

        self.assertEqual([("005930", True)], received)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)
        self.assertIsNone(controller.worker)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeNxtEligibilityWorker()
        calls: list[tuple[str, ...]] = []
        controller = NxtEligibilityWorkerController(
            worker_factory=lambda codes: calls.append(codes) or worker
        )
        self.assertTrue(controller.start(("005930",)))
        self.assertTrue(controller.start(("000660",)))
        self.assertEqual([("005930",)], calls)

    def test_missing_or_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = NxtEligibilityWorkerController()
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(("005930",)))

        controller.set_factory(lambda _codes: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(("005930",)))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()

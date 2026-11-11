"""Native Qt receiver ownership, duplicate delivery, restart and close/drain."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.presentation.app_controller import AppController
from kiwoom_monitor.presentation.main_window import MainWindow
from kiwoom_monitor.presentation.top20_trade_value import Top20NasDataWorker


class _HeldStoredRead:
    def __init__(self, *, fail=False):
        self.entered, self.release = Event(), Event()
        self.calls = 0
        self.fail = fail
        self.result = {'rows': [{'minute': '2026-10-22T09:00:00+09:00', 'value': 7}]}

    def load_stored_top20_series(self, day):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(4):
            raise RuntimeError('held read was not released')
        if self.fail:
            raise RuntimeError('original-read-failure')
        return self.result


class Top20NasCallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'monitor.sqlite3'
        database = Database(self.path)
        database.initialize()
        StockRepository(self.path).upsert('005930', 'fixture-stock')
        now = datetime.fromisoformat('2026-10-22T20:06:00+09:00')
        with patch.object(AppController, 'start_initial_ranking'):
            self.window = MainWindow(database.settings, query_only=True,
                monitor_database_path=self.path, ranking_loader=SimpleNamespace(server_now=lambda: now))
            self.window.activate_startup()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.release.set()
        if isValid(self.window):
            self.window.close()
            self.wait_until(lambda: self.window._app_controller._shutdown_stage == 'ready')
            self.window.deleteLater()
            QCoreApplication.sendPostedEvents(self.window, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.directory.cleanup()

    def wait_until(self, condition):
        # Match the existing AppController native Qt fixture's four-second bound.
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), 'Qt callback/drain did not complete')

    def hold(self, *, fail=False):
        client = _HeldStoredRead(fail=fail)
        self.clients.append(client)
        self.window._app_controller.market_data_client = client
        return client

    def test_owned_sender_gui_delivery_duplicate_consumption_and_request_restart(self):
        client = self.hold()
        observations = []
        class DuplicateWorker(Top20NasDataWorker):
            def run(self):
                super().run()
                self.completed.emit(self._request, client.result)
        with patch('kiwoom_monitor.presentation.main_window.Top20NasDataWorker', DuplicateWorker):
            self.window._start_top20_nas_request(('date', '2026-10-22'),
                lambda result: observations.append((result, self.window.sender(), QThread.currentThread())))
            self.assertTrue(client.entered.wait(1))
            worker = next(iter(self.window._top20_nas_workers))
            self.window._start_top20_nas_request(('date', '2026-10-22'), lambda _: self.fail('duplicate request started'))
            self.assertEqual(1, len(self.window._top20_nas_workers))
            client.release.set()
            self.wait_until(lambda: not self.window._top20_nas_workers)
        self.assertEqual(1, client.calls)
        self.assertEqual(1, len(observations))
        self.assertIs(client.result, observations[0][0])
        self.assertIs(worker, observations[0][1])
        self.assertIs(self.window.thread(), observations[0][2])
        self.assertEqual({}, self.window._top20_nas_callbacks)
        self.window._start_top20_nas_request(('date', '2026-10-22'),
            lambda result: observations.append((result, self.window.sender(), QThread.currentThread())))
        self.wait_until(lambda: not self.window._top20_nas_workers)
        self.assertEqual(2, client.calls)
        self.assertEqual(2, len(observations))
        self.assertIsNot(worker, observations[1][1])

    def test_failure_keeps_original_message_and_uses_registered_gui_receiver(self):
        client = self.hold(fail=True)
        observed = []
        self.window._start_top20_nas_request(('date', '2026-10-22'),
            lambda _: self.fail('failed read completed'),
            lambda message: observed.append((message, self.window.sender(), QThread.currentThread())))
        self.assertTrue(client.entered.wait(1))
        worker = next(iter(self.window._top20_nas_workers))
        client.release.set()
        self.wait_until(lambda: not self.window._top20_nas_workers)
        self.assertEqual(1, len(observed))
        self.assertEqual('original-read-failure', observed[0][0])
        self.assertIs(worker, observed[0][1])
        self.assertIs(self.window.thread(), observed[0][2])
        self.assertEqual({}, self.window._top20_nas_callbacks)

    def test_result_waits_for_native_thread_exit_before_gui_callback_and_release(self):
        client = self.hold()
        result_ready, native_exit = Event(), Event()
        observed = []

        class HeldAfterResultWorker(Top20NasDataWorker):
            def run(self):
                super().run()
                result_ready.set()
                native_exit.wait(4)

        with patch('kiwoom_monitor.presentation.main_window.Top20NasDataWorker', HeldAfterResultWorker):
            self.window._start_top20_nas_request(('date', '2026-10-22'),
                lambda result: observed.append((result, worker.isRunning(), self.window.sender())))
            worker = next(iter(self.window._top20_nas_workers))
            client.release.set()
            try:
                self.assertTrue(result_ready.wait(1))
                self.assertTrue(worker.isRunning())
                self.app.processEvents()
                self.assertEqual([], observed)
                self.assertIn(worker, self.window._top20_nas_workers)
                self.assertIn(worker, self.window._top20_nas_callbacks)
            finally:
                native_exit.set()
                self.wait_until(lambda: not self.window._top20_nas_workers)
        self.assertEqual([(client.result, False, worker)], observed)

    def test_missing_sender_cannot_consume_an_owned_pending_callback(self):
        client = self.hold()
        observed = []
        self.window._start_top20_nas_request(('date', '2026-10-22'), observed.append)
        self.assertTrue(client.entered.wait(1))
        with self.assertLogs('kiwoom_monitor.presentation.main_window', level='WARNING'):
            self.window._release_top20_nas_worker()
        self.assertEqual(1, len(self.window._top20_nas_workers))
        self.assertEqual(1, len(self.window._top20_nas_callbacks))
        self.assertEqual([], observed)
        client.release.set()
        self.wait_until(lambda: not self.window._top20_nas_workers)
        self.assertEqual([client.result], observed)

    def test_close_waits_for_late_worker_and_real_native_price_ack(self):
        client = self.hold()
        observed = []
        controller = self.window._app_controller
        self.window._start_top20_nas_request(('date', '2026-10-22'), observed.append,
            lambda _: self.fail('late success became failure'))
        self.assertTrue(client.entered.wait(1))
        controller.queue_price_cache('005930', price=70100)
        self.assertFalse(self.window.close())
        self.assertTrue(controller.closing)
        self.assertTrue(controller.market_cache_writer.isRunning())
        client.release.set()
        self.wait_until(lambda: controller._shutdown_stage == 'ready')
        self.assertEqual([], observed)
        self.assertEqual({}, self.window._top20_nas_callbacks)
        self.assertFalse(self.window._top20_nas_workers)
        self.assertFalse(controller.market_cache_writer.isRunning())
        self.assertFalse(controller.pending_prices)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(70100, connection.execute("SELECT last_price FROM stocks WHERE code='005930'").fetchone()[0])
        self.window._start_top20_nas_request(('date', '2026-10-22'), observed.append)
        self.assertFalse(self.window._top20_nas_workers)


if __name__ == '__main__':
    unittest.main()

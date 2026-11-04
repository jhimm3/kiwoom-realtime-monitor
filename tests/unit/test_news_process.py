from __future__ import annotations

import json
import ast
import inspect
import logging
import os
import subprocess
import sys
import tempfile
import time
import threading
import sqlite3
import unittest
from pathlib import Path
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
from urllib.error import URLError

from kiwoom_monitor import news_process
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.domain.order_contract import AccountScope, AccountEnvironment
from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease, StrictRestoreError

from kiwoom_monitor.news_process import (
    _apply_show_command,
    _command_account_scopes,
    _file_signature,
    _news_content_signature,
    _parent_is_alive,
)


class _NativeSyncFixture:
    """Execute the current scheduling closure with real threads/native stores and fake HTTP."""

    def __init__(self, root: Path, *, catalog: bool = True, scoped: bool = False):
        self.root, self.catalog = root, catalog
        self.news = root / "news.sqlite3"
        self.now = datetime.fromisoformat("2030-01-02T09:00:00+09:00")
        if scoped:
            repository = StockNewsRepository(self.news, now_provider=lambda: self.now)
            repository.set_journal_link("group", "005930", "article", True, account_scope=AccountScope(
                "kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111",
            ))
        else:
            with closing(sqlite3.connect(self.news)) as con:
                con.executescript("""
                CREATE TABLE stock_news(stock_code,identity,title,PRIMARY KEY(stock_code,identity));
                INSERT INTO stock_news VALUES('005930','article','initial');
                CREATE TABLE stock_news_ai(stock_code,identity,summary,PRIMARY KEY(stock_code,identity));
                INSERT INTO stock_news_ai VALUES('005930','article','initial');
                CREATE TABLE journal_news_links(group_id,stock_code,identity,linked_at,
                    PRIMARY KEY(group_id,stock_code,identity));
                INSERT INTO journal_news_links VALUES('group','005930','article','2030-01-02T09:00:00+09:00');
                """)
        self.store = SQLiteQueryStore(root / "central.sqlite3", now_provider=lambda: self.now)
        self.store.initialize()
        self.requests, self.jobs = [], []
        self.hook, self.hook_method, self.hook_collection = None, "POST", "news_article"
        self.fail_post = False
        self.fail_pull = False
        self.v2 = False
        self.client = CentralContentClient("https://fixture.invalid", "fixture", opener=self.open)
        self.service = CentralContentSyncService(self.client)

    def edit(self, collection: str, value: str):
        table, column = {
            "news_article": ("stock_news", "title"),
            "news_ai": ("stock_news_ai", "summary"),
            "journal_news_link": ("journal_news_links", "linked_at"),
        }[collection]
        with closing(sqlite3.connect(self.news)) as con, con:
            con.execute(f"UPDATE {table} SET {column}=?", (value,))

    def open(self, request, **_kwargs):
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        collection = parsed.path.rsplit("/", 1)[-1]
        body = json.loads(request.data) if request.data else None
        self.requests.append((method, collection, body))
        if collection == "capabilities":
            result = {"capabilities": {"journal_news_links_v2": self.v2}}
        elif method == "POST":
            if self.fail_post is True or self.fail_post == collection:
                raise URLError("upload fixture failure")
            self.store.upsert_documents(collection, body["documents"])
            result = {"saved": len(body["documents"])}
        else:
            if self.fail_pull:
                raise URLError("pull fixture failure")
            query = parse_qs(parsed.query)
            result = {"documents": self.store.load_documents(
                collection, owner=query["owner"][0] if query.get("owner") else "",
                limit=int(query["limit"][0]), offset=int(query["offset"][0]),
                updated_after=float(query["updated_after"][0]),
            )}
        if self.hook is not None and method == self.hook_method and collection == self.hook_collection:
            callback, self.hook = self.hook, None
            callback()
        payload = json.dumps(result).encode()
        return _HttpResponse(payload, self.now.isoformat())

    def scheduler(self):
        tree = ast.parse(inspect.getsource(news_process))
        owner = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_news_process")
        state = [node for node in owner.body if isinstance(node, ast.AnnAssign)
                 and isinstance(node.target, ast.Name)
                 and node.target.id in {"content_sync_thread", "last_content_signature"}]
        state.extend(node for node in owner.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "stopping" for target in node.targets))
        mode = next(node for node in ast.walk(owner) if isinstance(node, ast.If)
                    and any(isinstance(item, ast.FunctionDef) and item.name == "schedule_content_sync" for item in node.body))
        setup = [node for node in mode.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "last_content_signature" for target in node.targets)]
        schedule = next(node for node in mode.body if isinstance(node, ast.FunctionDef) and node.name == "schedule_content_sync")
        factory = ast.parse("def build(content_sync, database_path, main_database_path, sync_news_catalog):\n    pass\n").body[0]
        factory.body = [*state, *setup, schedule, *ast.parse(
            "return schedule_content_sync, lambda: last_content_signature, lambda: content_sync_thread is not None and content_sync_thread.is_alive()"
        ).body]
        def thread(*args, **kwargs):
            job = threading.Thread(*args, **kwargs)
            self.jobs.append(job)
            return job
        namespace = {
            "_news_content_signature": _news_content_signature,
            "threading": SimpleNamespace(Thread=thread),
            "logger": logging.getLogger(__name__), "sqlite3": sqlite3,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[])),
                     inspect.getsourcefile(news_process), "exec"), namespace)
        return namespace["build"](self.service, self.news, self.root / "missing-main", self.catalog)

    def tick(self, scheduler):
        self.now += timedelta(seconds=1)
        scheduler[0]()
        for job in self.jobs:
            job.join(15)
            if job.is_alive():
                raise AssertionError("owned content worker did not settle")
        if scheduler[2]():
            raise AssertionError("content sync thread did not exit")

    def close(self):
        for job in self.jobs:
            job.join(15)
            if job.is_alive():
                raise AssertionError("owned content worker leaked")
        self.store.close()


class _HttpResponse:
    def __init__(self, payload: bytes, now: str):
        self.payload = payload
        self.headers = {"X-Kiwoom-Server-Time": now}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def _news_lifetime_child(root: Path, scenario: str) -> None:
    """Exercise the actual process owner/Qt loop/thread/lease with fixture windows and IO."""
    from PySide6.QtCore import QTimer as NativeTimer
    from PySide6.QtWidgets import QApplication as NativeApplication, QWidget
    from kiwoom_monitor.presentation import market_news_window

    events, callbacks, timers = [], {}, []
    counts = {"push": 0, "pull": 0, "stock_shutdown": 0, "market_shutdown": 0, "show": 0}
    release = root / "release"

    def record(name, **details):
        events.append({"name": name, **details})

    class Signal:
        def __init__(self, signal):
            self.signal = signal

        def connect(self, callback):
            callbacks[callback.__name__] = callback
            self.signal.connect(callback)

    class Timer:
        def __init__(self):
            self.native = NativeTimer()
            self.timeout = Signal(self.native.timeout)
            timers.append(self)

        def setInterval(self, interval):
            self.native.setInterval(interval)

        def start(self):
            self.native.start()

        def stop(self):
            self.native.stop()

        singleShot = staticmethod(NativeTimer.singleShot)

    class Application(NativeApplication):
        def exec(self):
            result = super().exec()
            record("qt_exec_returned")
            if scenario == "exec_failure":
                raise RuntimeError("fixture Qt exec failure")
            return result

    class StockWindow(QWidget):
        def __init__(self, *_args):
            super().__init__()

        def set_stock(self, *_args, **_kwargs):
            counts["show"] += 1

        def shutdown(self):
            counts["stock_shutdown"] += 1
            if counts["stock_shutdown"] != 1:
                return
            record("shutdown_started", active_timers=[timer.native.interval() for timer in timers if timer.native.isActive()])
            (root / "closing").touch()
            # Model callbacks already queued before timers were stopped and a repeated close.
            for request in ({"request_id": 2, "action": "shutdown"},
                            {"request_id": 3, "action": "show", "code": "005930", "name": "fixture"}):
                (root / "news_command.json").write_text(json.dumps(request), encoding="utf-8")
                callbacks["poll_command"]()
            callbacks["stop_if_parent_exited"]()
            callbacks["sync_parent_on_news_activation"]()
            callbacks["schedule_content_sync"]()

    class MarketWindow(QWidget):
        def __init__(self, *_args):
            super().__init__()

        def shutdown(self):
            counts["market_shutdown"] += 1

    class ContentSync:
        def __init__(self, _client):
            pass

        def push(self, _main, news, **_kwargs):
            counts["push"] += 1
            record("sync_entered")
            (root / "entered").touch()
            deadline = time.monotonic() + 8
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise AssertionError("fixture release did not arrive")
                time.sleep(.01)
            # A real native write/connection must settle before the shared lease is released.
            with closing(sqlite3.connect(news)) as connection, connection:
                connection.execute("CREATE TABLE IF NOT EXISTS lifetime_receipt (value TEXT)")
                connection.execute("INSERT INTO lifetime_receipt VALUES ('accepted')")
            record("sync_native_closed")
            (root / "native_done").touch()
            if scenario == "sync_failure":
                raise OSError("fixture sync failure; local receipt retained")
            return SimpleNamespace(pending_collections=())

        def pull(self, *_args, **_kwargs):
            counts["pull"] += 1
            return SimpleNamespace(total=0)

    original_close = AppDataLease.close

    def close(lease):
        # A late result callback after an idle worker must not create another job.
        callbacks["schedule_content_sync"]()
        record("lease_close", active_threads=[thread.name for thread in threading.enumerate()
                                              if thread.name == "news-central-content-sync"])
        original_close(lease)
        (root / "lease_released").touch()

    news_process.QTimer = Timer
    news_process.QApplication = Application
    news_process.StockNewsWindow = StockWindow
    market_news_window.MarketNewsWindow = MarketWindow
    news_process.CentralContentSyncService = ContentSync
    news_process._parent_is_alive = lambda _pid: counts["stock_shutdown"] == 0 and not (root / "parent_exited").exists()
    AppDataLease.close = close
    (root / "data_source.json").write_text(json.dumps({
        "mode": "local_server", "server_url": "https://fixture.invalid", "access_token": "fixture",
    }), encoding="utf-8")
    if scenario == "initial_shutdown":
        (root / "news_command.json").write_text(json.dumps({"request_id": 1, "action": "shutdown"}), encoding="utf-8")
    error, result = None, None
    try:
        result = news_process.main([
            "--config", str(root / "naver_news.dat"), "--database", str(root / "news.sqlite3"),
            "--command-file", str(root / "news_command.json"), "--parent-pid", str(os.getppid()),
        ])
    except RuntimeError as caught:
        error = str(caught)
    finally:
        (root / "lifetime_result.json").write_text(json.dumps({
            "result": result, "error": error, "counts": counts, "events": events,
            "source": str(Path(news_process.__file__).resolve()),
            "active_threads": [thread.name for thread in threading.enumerate()
                               if thread.name == "news-central-content-sync"],
        }), encoding="utf-8")


class NewsProcessTests(unittest.TestCase):
    def _assert_content_lifetime(self, scenario: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "child.log").open("wb") as log:
                process = subprocess.Popen([
                    sys.executable, "-c",
                    "from pathlib import Path; import sys; from tests.unit.test_news_process import _news_lifetime_child; "
                    "_news_lifetime_child(Path(sys.argv[1]), sys.argv[2])", str(root), scenario,
                ], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

                def wait_file(name):
                    deadline = time.monotonic() + 8
                    while not (root / name).exists() and time.monotonic() < deadline and process.poll() is None:
                        time.sleep(.01)
                    self.assertTrue((root / name).exists(), (root / "child.log").read_text(encoding="utf-8", errors="replace"))

                try:
                    if scenario != "initial_shutdown":
                        wait_file("entered")
                    if scenario == "settled":
                        (root / "release").touch()
                        wait_file("native_done")
                    if scenario == "parent_exit":
                        (root / "parent_exited").touch()
                    elif scenario != "initial_shutdown":
                        (root / "news_command.json").write_text(json.dumps({"request_id": 1, "action": "shutdown"}), encoding="utf-8")
                    wait_file("closing")
                    if scenario not in {"settled", "initial_shutdown"}:
                        self.assertIsNone(process.poll(), "accepted sync must keep its owner alive")
                        with self.assertRaises(StrictRestoreError):
                            with AppDataLease(root, exclusive=True, timeout=.05):
                                pass
                        self.assertFalse((root / "lease_released").exists())
                    (root / "release").touch()
                    self.assertEqual(0, process.wait(timeout=12))
                    report = json.loads((root / "lifetime_result.json").read_text(encoding="utf-8"))
                    print("news_lifetime_receipt=" + json.dumps(report))
                    self.assertEqual(str(Path(news_process.__file__).resolve()), report["source"])
                    self.assertEqual({"push": 0 if scenario == "initial_shutdown" else 1,
                                      "pull": 0 if scenario in {"sync_failure", "initial_shutdown"} else 1,
                                      "stock_shutdown": 1, "market_shutdown": 1, "show": 0}, report["counts"])
                    self.assertEqual([], report["active_threads"])
                    self.assertEqual([], next(event["active_threads"] for event in report["events"] if event["name"] == "lease_close"))
                    self.assertEqual([], next(event["active_timers"] for event in report["events"] if event["name"] == "shutdown_started"))
                    names = [event["name"] for event in report["events"]]
                    if scenario != "initial_shutdown":
                        self.assertLess(names.index("sync_native_closed"), names.index("lease_close"))
                    self.assertEqual(None if scenario == "exec_failure" else 0, report["result"])
                    self.assertEqual("fixture Qt exec failure" if scenario == "exec_failure" else None, report["error"])
                    with AppDataLease(root, exclusive=True, timeout=.05):
                        pass
                    if scenario != "initial_shutdown":
                        with closing(sqlite3.connect(root / "news.sqlite3")) as connection:
                            self.assertEqual([("accepted",)], connection.execute("SELECT value FROM lifetime_receipt").fetchall())
                    if scenario == "sync_failure":
                        self.assertIn("fixture sync failure; local receipt retained", (root / "child.log").read_text(encoding="utf-8"))
                finally:
                    (root / "release").touch()
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)

    def test_content_sync_holds_child_lease_until_native_exit_during_shutdown(self) -> None:
        self._assert_content_lifetime("success")

    def test_failed_content_sync_settles_before_child_lease_release(self) -> None:
        self._assert_content_lifetime("sync_failure")

    def test_qt_exec_failure_preserves_error_and_joins_content_sync(self) -> None:
        self._assert_content_lifetime("exec_failure")

    def test_settled_content_sync_rejects_repeated_close_and_late_callbacks(self) -> None:
        self._assert_content_lifetime("settled")

    def test_parent_exit_joins_content_sync_before_child_lease_release(self) -> None:
        self._assert_content_lifetime("parent_exit")

    def test_initial_shutdown_rejects_sync_before_entering_qt_loop(self) -> None:
        self._assert_content_lifetime("initial_shutdown")

    def test_sync_retries_unsupported_scoped_link_without_new_local_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _NativeSyncFixture(Path(directory), catalog=False, scoped=True)
            try:
                scheduler = fixture.scheduler()
                fixture.edit("journal_news_link", "2030-01-02T09:01:00+09:00")
                fixture.tick(scheduler)
                self.assertEqual([], fixture.store.load_documents("journal_v2_news_links"))
                fixture.tick(scheduler)
                fixture.v2 = True
                fixture.tick(scheduler)
                self.assertEqual(1, len(fixture.store.load_documents("journal_v2_news_links")))
                sent = sum(method == "POST" for method, _, _ in fixture.requests)
                fixture.tick(scheduler)
                self.assertEqual(sent, sum(method == "POST" for method, _, _ in fixture.requests))
            finally:
                fixture.close()

    def test_sync_keeps_edits_during_push_pull_and_final_ack_for_next_tick(self) -> None:
        cases = ((True, "news_article", "POST"), (True, "news_ai", "GET"),
                 (True, "journal_news_link", "after_pull"), (False, "journal_news_link", "POST"))
        for catalog, collection, phase in cases:
            with self.subTest(catalog=catalog, collection=collection, phase=phase), tempfile.TemporaryDirectory() as directory:
                fixture = _NativeSyncFixture(Path(directory), catalog=catalog)
                try:
                    fixture.service.push(fixture.root / "missing-main", fixture.news, sync_news_catalog=catalog)
                    scheduler = fixture.scheduler()
                    fixture.edit(collection, "2030-01-02T09:01:00+09:00" if collection == "journal_news_link" else "sent")
                    final_value = "2030-01-02T09:02:00+09:00" if collection == "journal_news_link" else "pending edit"
                    if phase == "after_pull":
                        original_pull = fixture.service.pull
                        def pull(*args, **kwargs):
                            result = original_pull(*args, **kwargs)
                            fixture.edit(collection, final_value)
                            fixture.service.pull = original_pull
                            return result
                        fixture.service.pull = pull
                    else:
                        fixture.hook = lambda: fixture.edit(collection, final_value)
                        fixture.hook_method, fixture.hook_collection = phase, collection
                    fixture.tick(scheduler)
                    fixture.tick(scheduler)
                    column = {"news_article": "title", "news_ai": "summary", "journal_news_link": "linked_at"}[collection]
                    self.assertEqual(final_value, fixture.store.load_documents(collection)[0]["document"][column])
                    sent_before = len([request for request in fixture.requests if request[0] == "POST"])
                    fixture.tick(scheduler)
                    self.assertEqual(sent_before, len([request for request in fixture.requests if request[0] == "POST"]))
                    self.assertTrue((fixture.root / "central_content_push_manifest.json").is_file())
                    self.assertTrue((fixture.root / "central_content_cursor.json").is_file())
                    if not catalog:
                        self.assertFalse(any(method == "POST" and name in {"news_article", "news_ai"}
                                             for method, name, _ in fixture.requests))
                finally:
                    fixture.close()

    def test_sync_restart_failure_retry_and_pull_echo_keep_native_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _NativeSyncFixture(Path(directory))
            try:
                fixture.service.push(fixture.root / "missing-main", fixture.news)
                fixture.edit("news_article", "unsent before restart")
                scheduler = fixture.scheduler()
                fixture.fail_post = True
                with self.assertLogs(__name__, level="WARNING"):
                    fixture.tick(scheduler)
                self.assertEqual("initial", fixture.store.load_documents("news_article")[0]["document"]["title"])
                fixture.fail_post = False
                fixture.service = CentralContentSyncService(fixture.client)
                scheduler = fixture.scheduler()
                fixture.tick(scheduler)
                self.assertEqual("unsent before restart", fixture.store.load_documents("news_article")[0]["document"]["title"])
                fixture.edit("news_article", "partial accepted")
                fixture.edit("news_ai", "partial pending")
                fixture.fail_post = "news_ai"
                with self.assertLogs(__name__, level="WARNING"):
                    fixture.tick(scheduler)
                self.assertEqual("partial accepted", fixture.store.load_documents("news_article")[0]["document"]["title"])
                self.assertNotEqual("partial pending", fixture.store.load_documents("news_ai")[0]["document"]["summary"])
                article_posts = sum(method == "POST" and name == "news_article" for method, name, _ in fixture.requests)
                fixture.fail_post = False
                fixture.tick(scheduler)
                self.assertEqual("partial pending", fixture.store.load_documents("news_ai")[0]["document"]["summary"])
                self.assertEqual(article_posts, sum(method == "POST" and name == "news_article" for method, name, _ in fixture.requests))
                fixture.edit("news_ai", "local AI before pull failure")
                fixture.fail_pull = True
                with self.assertLogs(__name__, level="WARNING"):
                    fixture.tick(scheduler)
                fixture.fail_pull = False
                fixture.tick(scheduler)
                self.assertEqual("local AI before pull failure", fixture.store.load_documents("news_ai")[0]["document"]["summary"])
                remote = fixture.store.load_documents("news_ai")[0]
                remote["document"]["summary"] = "remote edit"
                fixture.now += timedelta(seconds=1)
                fixture.store.upsert_documents("news_ai", [remote])
                sent_before = len([request for request in fixture.requests if request[0] == "POST"])
                fixture.tick(scheduler)
                fixture.tick(scheduler)
                fixture.tick(scheduler)
                self.assertEqual(sent_before, len([request for request in fixture.requests if request[0] == "POST"]))
                with closing(sqlite3.connect(fixture.news)) as con:
                    self.assertEqual("remote edit", con.execute("SELECT summary FROM stock_news_ai").fetchone()[0])
            finally:
                fixture.close()

    def test_scope_free_command_is_legacy_but_partial_or_malformed_scope_is_rejected(self) -> None:
        legacy = _command_account_scopes({"code": "005930", "name": "삼성전자"})
        self.assertIsNotNone(legacy)
        assert legacy is not None
        self.assertEqual(legacy[0], legacy[1])
        self.assertEqual("legacy-unassigned", legacy[0].account_ref)
        self.assertIsNone(_command_account_scopes({"origin_scope": {"broker": "kiwoom"}}))
        self.assertIsNone(_command_account_scopes({
            "origin_scope": {
                "broker": "kiwoom", "environment": "real",
                "account_ref": "11111111-1111-4111-8111-111111111111",
            },
            "account_scope": {
                "broker": "kiwoom", "environment": "mock",
                "account_ref": "22222222-2222-4222-8222-222222222222",
            },
        }))

    def test_receiver_does_not_replace_previous_context_for_malformed_late_command(self) -> None:
        calls: list[dict[str, object]] = []

        class Window:
            def set_stock(self, _code: str, _name: str, **kwargs: object) -> None:
                calls.append((_code, _name, kwargs))

        valid = {
            "code": "005930", "name": "삼성전자",
            "origin_scope": {
                "broker": "kiwoom", "environment": "real",
                "account_ref": "11111111-1111-4111-8111-111111111111",
            },
            "account_scope": {
                "broker": "kiwoom", "environment": "real",
                "account_ref": "11111111-1111-4111-8111-111111111111",
            },
        }
        self.assertTrue(_apply_show_command(Window(), valid))  # type: ignore[arg-type]
        self.assertFalse(_apply_show_command(Window(), {
            "code": "000660", "name": "SK하이닉스",
            "origin_scope": valid["origin_scope"],
        }))  # type: ignore[arg-type]
        self.assertEqual(1, len(calls))
        self.assertEqual(("005930", "삼성전자"), calls[0][:2])

    def test_file_signature_changes_when_database_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "news.sqlite3"
            self.assertEqual(_file_signature(path), (0, 0))
            path.write_bytes(b"first")
            first = _file_signature(path)
            path.write_bytes(b"second-version")
            self.assertNotEqual(first, _file_signature(path))

    def test_news_content_signature_ignores_unrelated_monitor_database_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            monitor = root / "monitor.sqlite3"
            news = root / "news.sqlite3"
            monitor.write_bytes(b"ranking-before")
            news.write_bytes(b"news-before")
            initial = _news_content_signature(news)

            monitor.write_bytes(b"ranking-after-with-more-bytes")

            self.assertEqual(initial, _news_content_signature(news))
            news.write_bytes(b"news-after-with-more-bytes")
            self.assertNotEqual(initial, _news_content_signature(news))

    def test_current_process_is_reported_as_alive(self) -> None:
        self.assertTrue(_parent_is_alive(os.getpid()))

    def test_process_receives_stock_command_and_shuts_down(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command_path = root / "news_command.json"
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "offscreen"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "kiwoom_monitor.news_process",
                    "--config",
                    str(root / "naver_news.dat"),
                    "--database",
                    str(root / "news.sqlite3"),
                    "--command-file",
                    str(command_path),
                    "--parent-pid",
                    str(os.getpid()),
                ],
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            def wait_visible(expected):
                deadline = time.monotonic() + 5
                state = {}
                import psutil
                while time.monotonic() < deadline:
                    try:
                        state = json.loads((root / 'news_window_state.json').read_text(encoding='utf-8'))
                        owned = {process.pid, *(child.pid for child in psutil.Process(process.pid).children(recursive=True))}
                        if state.get('visible') is expected and state.get('pid') in owned:
                            return
                    except (OSError, ValueError):
                        pass
                    time.sleep(.02)
                self.fail(f'child visibility receipt: expected={expected} actual={state}')
            try:
                deadline = time.monotonic() + 8
                while not (root / "news.sqlite3").is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((root / "news.sqlite3").is_file())
                # 첫 종목 명령이 오기 전에도 뉴스 전용 프로세스가 대기해야 한다.
                # 부모 생존 타이머(1초)가 한 번 이상 실행된 뒤에도 유지되어야 한다.
                time.sleep(1.3)
                self.assertIsNone(process.poll())
                command_path.write_text(
                    json.dumps({"request_id": 1, "action": "show", "code": "005930", "name": "삼성전자"}),
                    encoding="utf-8",
                )
                wait_visible(True)
                command_path.write_text(
                    json.dumps({
                        "request_id": 2, "action": "sync", "window_mode": "linked",
                        "main_geometry": [100, 100, 800, 600],
                    }),
                    encoding="utf-8",
                )
                time.sleep(0.15)
                self.assertIsNone(process.poll())
                command_path.write_text(
                    json.dumps({"request_id": 3, "action": "minimize"}),
                    encoding="utf-8",
                )
                wait_visible(False)
                command_path.write_text(
                    json.dumps({"request_id": 4, "action": "restore"}),
                    encoding="utf-8",
                )
                wait_visible(True)
                command_path.write_text(
                    json.dumps({"request_id": 5, "action": "shutdown"}),
                    encoding="utf-8",
                )
                self.assertEqual(0, process.wait(timeout=12))
            finally:
                if process.poll() is None:
                    import psutil
                    children = psutil.Process(process.pid).children(recursive=True)
                    for child in reversed(children):
                        child.terminate()
                    process.terminate()
                    process.wait(timeout=5)
                    _, alive = psutil.wait_procs(children, timeout=5)
                    for child in alive:
                        child.kill()
                    psutil.wait_procs(alive, timeout=5)


def _news_acquisition_child(root: Path, scenario: str) -> None:
    from PySide6.QtCore import QSettings, QTimer as NativeTimer
    from PySide6.QtWidgets import QApplication as NativeApplication
    from kiwoom_monitor.presentation import market_news_window, stock_news_window
    from kiwoom_monitor.presentation.news_workers import NewsPrepareWorker, NewsSearchWorker
    from kiwoom_monitor.presentation.historical_news_archive_dialog import HistoricalNewsArchiveDialog
    from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker
    from kiwoom_monitor.presentation import news_settings_dialog

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root / "qt-settings"))
    windows, timers, workers, callbacks, events, slot_errors = [], [], [], {}, [], []
    counts = {"stock": 0, "market": 0, "content": 0, "thread_start": 0}
    release = root / "release"
    NativeThread = threading.Thread
    owner_cases = {"settings_save", "settings_load", "settings_plan", "settings_result_tail", "cleanup_timer", "cleanup_stock_geometry",
                   "cleanup_market_geometry", "cleanup_archive", "cleanup_combined", "cleanup_only"}
    primary = None
    dialogs = []

    def record(name, **values):
        events.append({"name": name, **values})

    if scenario.startswith("settings_"):
        original_apply = news_settings_dialog.apply_to_local_news
        def apply_settings(config, values):
            original_apply(config, values)
            if any(item["name"] == "settings_ack_returned" for item in events):
                record("settings_mirror_written")
        news_settings_dialog.apply_to_local_news = apply_settings

    def accepted_native(kind):
        (root / "entered").touch()
        record(kind + "_entered")
        deadline = time.monotonic() + (20 if scenario.endswith("_timeout") else 8)
        while not release.exists():
            if time.monotonic() >= deadline:
                raise AssertionError("acquisition fixture release missing")
            time.sleep(.01)
        with closing(sqlite3.connect(root / "news.sqlite3")) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS acquisition_receipt (kind TEXT)")
            connection.execute("INSERT INTO acquisition_receipt VALUES (?)", (kind,))
        record(kind + "_native_closed")

    def prepare(worker):
        if scenario == "stock_search_timeout":
            return
        workers.append(worker)
        accepted_native("prepare")

    def tracked_run(kind):
        def run(worker):
            workers.append(worker)
            accepted_native(kind)
        return run

    for worker_type in (NewsPrepareWorker, NewsSearchWorker, market_news_window.SettingsRequestWorker,
                        market_news_window.MarketNewsLoadWorker):
        native_wait = worker_type.wait
        def wait(worker, *args, native_wait=native_wait):
            started = time.monotonic()
            result = native_wait(worker, *args)
            if scenario.endswith("_timeout") and args and result is False:
                record("bounded_wait_false", milliseconds=args[0], elapsed_seconds=time.monotonic() - started)
                (root / "bounded_wait_false").touch()
            return result
        worker_type.wait = wait

    class Stock(stock_news_window.StockNewsWindow):
        def __init__(self, *args):
            super().__init__(*args)
            windows.append(self)
            if scenario in {"cleanup_archive", "cleanup_combined"}:
                class Archive(HistoricalNewsArchiveDialog):
                    def shutdown(self):
                        super().shutdown()
                        raise RuntimeError("cleanup archive")
                self._historical_archive_dialog = Archive(SimpleNamespace(), self)

        def _save_window_geometry(self):
            super()._save_window_geometry()
            if scenario in {"cleanup_stock_geometry", "cleanup_combined"} and not getattr(self, "_geometry_faulted", False):
                self._geometry_faulted = True
                raise RuntimeError("cleanup stock geometry")

        def shutdown(self):
            counts["stock"] += 1
            (root / "cleanup_started").touch()
            super().shutdown()
            record("stock_shutdown_returned")

    class Market(market_news_window.MarketNewsWindow):
        def __init__(self, *args):
            if scenario == "market_constructor":
                raise RuntimeError("fixture market_constructor")
            super().__init__(*args)
            windows.append(self)
            if scenario in {"cleanup_market_geometry", "cleanup_combined"}:
                class Geometry:
                    failed = False
                    def __init__(self, original):
                        self.original = original
                    def setValue(self, *values):
                        self.original.setValue(*values)
                        if not self.failed:
                            self.failed = True
                            raise RuntimeError("cleanup market geometry")
                    def __getattr__(self, name):
                        return getattr(self.original, name)
                self._window_settings = Geometry(self._window_settings)

        def show_news(self):
            if scenario == "market_load_timeout":
                self._configure_tabs(("flash",))
                self.show()
                return
            super().show_news()

        def shutdown(self):
            counts["market"] += 1
            super().shutdown()
            record("market_shutdown_returned")

    class ConnectedSignal:
        def __init__(self, native):
            self.native = native

        def connect(self, callback):
            callbacks[callback.__name__] = callback
            self.native.connect(callback)

    class Timer:
        def __init__(self):
            self.native = NativeTimer()
            self.timeout = ConnectedSignal(self.native.timeout)
            timers.append(self)

        def setInterval(self, value):
            self.native.setInterval(value)

        def start(self):
            self.native.start()
            if scenario == "timer_start" and len(timers) == 3:
                raise RuntimeError("fixture timer_start")

        def stop(self):
            self.native.stop()
            if (scenario in {"cleanup_timer", "cleanup_combined", "cleanup_only"}
                    and self.native.interval() == 80 and not getattr(self, "_stop_faulted", False)):
                self._stop_faulted = True
                raise RuntimeError("cleanup timer")

        singleShot = staticmethod(NativeTimer.singleShot)

    class Content:
        def __init__(self, _client):
            if scenario == "content_constructor":
                raise RuntimeError("fixture content_constructor")

        def push(self, *_args, **_kwargs):
            counts["content"] += 1
            if scenario.startswith("settings_"):
                return SimpleNamespace(pending_collections=())
            accepted_native("content")
            return SimpleNamespace(pending_collections=())

        def pull(self, *_args, **_kwargs):
            return SimpleNamespace(total=0)

    class StartFailure(NativeThread):
        def start(self):
            counts["thread_start"] += 1
            raise RuntimeError("fixture thread_start")

    class Application(NativeApplication):
        def exec(self):
            nonlocal primary
            if scenario.startswith("settings_"):
                class SettingsClient:
                    values = {"ai_provider": "gemini", "ai_model": "gemini-3.5-flash-lite",
                              "ai_daily_limit": 17, "dart_enabled": False}
                    def load(self):
                        if scenario == "settings_load":
                            accepted_native("settings_load")
                        return dict(self.values)
                    def update(self, changes):
                        accepted_native("settings_ack")
                        self.values.update(changes)
                        record("settings_ack_returned", changes=changes)
                        return dict(self.values)
                    def chatgpt_status(self):
                        accepted_native("settings_plan")
                        return {"accounts": [], "active": ""}
                windows[0]._central_operational_client = SettingsClient()
                windows[0]._open_settings()
                dialog = windows[0]._settings_dialog
                dialogs.append(dialog)
                if scenario != "settings_load":
                    deadline = time.monotonic() + 3
                    while dialog._operations_worker is not None and time.monotonic() < deadline:
                        self.processEvents()
                        time.sleep(.002)
                    if dialog._operations_worker is not None:
                        raise AssertionError("initial settings load did not settle")
                    if scenario == "settings_result_tail":
                        record("settings_field_cleared_before_exit", running=sum(worker.isRunning() for worker in workers),
                               child_count=len(dialog.findChildren(SettingsRequestWorker)))
                    elif scenario == "settings_save":
                        dialog._ai_limit.setValue(321)
                        dialog._save()
                    else:
                        dialog._plan_run("status")
                primary = RuntimeError("fixture " + scenario)
                (root / "exec_raise").touch()
                raise primary
            if scenario in owner_cases:
                if scenario in {"cleanup_timer", "cleanup_combined"}:
                    callbacks["schedule_content_sync"]()
                if scenario == "cleanup_only":
                    NativeTimer.singleShot(50, self.quit)
                    return super().exec()
                primary = RuntimeError("fixture " + scenario)
                (root / "exec_raise").touch()
                raise primary
            if scenario == "stock_search_timeout":
                windows[0].refresh(force=True)
            if scenario in {"exec_entry", "prepare_timeout", "stock_search_timeout",
                            "market_settings_timeout", "market_load_timeout"}:
                (root / "exec_raise").touch()
                raise RuntimeError("fixture " + scenario)
            if scenario == "exec_content":
                callbacks["schedule_content_sync"]()
                (root / "exec_raise").touch()
                raise RuntimeError("fixture exec_content")
            if scenario == "independent_quit":
                NativeTimer.singleShot(50, self.quit)
            return super().exec()

    def slot_error(kind, value, _traceback):
        slot_errors.append({"type": kind.__name__, "message": str(value)})
        NativeApplication.instance().quit()

    original_close = AppDataLease.close

    def close(lease):
        record("lease_close", active_timers=[timer.native.interval() for timer in timers if timer.native.isActive()],
               active_prepare=sum(worker.isRunning() for worker in workers),
               active_content=sum(thread.name == "news-central-content-sync" for thread in threading.enumerate()))
        original_close(lease)
        (root / "lease_released").touch()

    news_process.StockNewsWindow = Stock
    market_news_window.MarketNewsWindow = Market
    NewsPrepareWorker.run = prepare
    NewsSearchWorker.run = tracked_run("search")
    if not scenario.startswith("settings_"):
        market_news_window.SettingsRequestWorker.run = tracked_run("market_settings")
    else:
        native_start = SettingsRequestWorker.start
        def settings_start(worker):
            workers.append(worker)
            native_start(worker)
        SettingsRequestWorker.start = settings_start
        if scenario == "settings_result_tail":
            native_run = SettingsRequestWorker.run
            def settings_tail(worker):
                native_run(worker)
                accepted_native("settings_tail")
            SettingsRequestWorker.run = settings_tail
    market_news_window.MarketNewsLoadWorker.run = tracked_run("market_load")
    news_process.QTimer = Timer
    news_process.QApplication = Application
    news_process.CentralContentSyncService = Content
    AppDataLease.close = close
    if scenario == "thread_start":
        news_process.threading.Thread = StartFailure
        sys.excepthook = slot_error
    (root / "data_source.json").write_text(json.dumps({
        "mode": "local_server", "server_url": "https://fixture.invalid", "access_token": "fixture",
    }), encoding="utf-8")
    if scenario in {"exec_entry", "independent_quit", "prepare_timeout", "stock_search_timeout",
                    "market_settings_timeout", "market_load_timeout", "cleanup_timer", "cleanup_stock_geometry",
                    "cleanup_market_geometry", "cleanup_archive", "cleanup_combined", "cleanup_only"}:
        (root / "news_command.json").write_text(json.dumps({
            "request_id": 1, "action": "market_news" if scenario.startswith("market_") else "show",
            "code": "005930", "name": "fixture", "activate": False,
        }), encoding="utf-8")
    error, result, notes, primary_preserved = None, None, [], None
    try:
        result = news_process.main([
            "--config", str(root / "naver_news.dat"), "--database", str(root / "news.sqlite3"),
            "--command-file", str(root / "news_command.json"), "--parent-pid", str(os.getppid()),
        ])
    except RuntimeError as caught:
        error = str(caught)
        notes = list(getattr(caught, "__notes__", ()))
        primary_preserved = caught is primary if primary is not None else None
    finally:
        snapshot = {"error": error, "result": result, "notes": notes, "primary_preserved": primary_preserved,
                    "settings_limit": windows[0]._config.load_ai().daily_limit if windows else None,
                    "counts": counts.copy(), "events": list(events),
                    "slot_errors": slot_errors, "active_prepare": sum(worker.isRunning() for worker in workers),
                    "source": str(Path(news_process.__file__).resolve())}
        (root / "acquisition_result.json").write_text(json.dumps(snapshot), encoding="utf-8")
        # Preserve failure receipts above, then release only fixture-owned objects safely.
        for worker in workers:
            if worker.isRunning():
                worker.wait(8000)
        for thread in threading.enumerate():
            if thread.name == "news-central-content-sync":
                thread.join(8)
        for timer in timers:
            try:
                timer.stop()
            except RuntimeError:
                pass
        for window in windows:
            window.close()


class NewsAcquisitionTests(unittest.TestCase):
    def _assert_acquisition(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "child.log").open("wb") as log:
                process = subprocess.Popen([
                    sys.executable, "-c", "from pathlib import Path; import sys; "
                    "from tests.unit.test_news_process import _news_acquisition_child; "
                    "_news_acquisition_child(Path(sys.argv[1]), sys.argv[2])", str(root), scenario,
                ], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

                def wait_any(*names):
                    deadline = time.monotonic() + (15 if scenario.endswith("_timeout") else 8)
                    while not any((root / name).exists() for name in names) and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(any((root / name).exists() for name in names), (root / "child.log").read_text(encoding="utf-8", errors="replace"))

                try:
                    if (scenario in {"exec_entry", "exec_content", "independent_quit"}
                            or scenario.endswith("_timeout") or scenario.startswith(("settings_", "cleanup_"))):
                        wait_any("entered")
                        if scenario.endswith("_timeout"):
                            wait_any("bounded_wait_false")
                        else:
                            wait_any("exec_raise", "cleanup_started", "lease_released")
                        with self.assertRaises(StrictRestoreError):
                            with AppDataLease(root, exclusive=True, timeout=.05):
                                pass
                        self.assertFalse((root / "lease_released").exists())
                    (root / "release").touch()
                    self.assertEqual(0, process.wait(timeout=12), (root / "child.log").read_text(encoding="utf-8", errors="replace"))
                    report = json.loads((root / "acquisition_result.json").read_text(encoding="utf-8"))
                    print("news_acquisition_receipt=" + json.dumps({"scenario": scenario, **report}))
                    self.assertEqual(str(Path(news_process.__file__).resolve()), report["source"])
                    self.assertEqual(1, report["counts"]["stock"])
                    self.assertEqual(0 if scenario == "market_constructor" else 1, report["counts"]["market"])
                    self.assertEqual(0, report["active_prepare"])
                    lease_event = next(item for item in report["events"] if item["name"] == "lease_close")
                    self.assertEqual([], lease_event["active_timers"])
                    self.assertEqual(0, lease_event["active_prepare"])
                    self.assertEqual(0, lease_event["active_content"])
                    if scenario.endswith("_timeout"):
                        expected_ms = {"prepare_timeout": 3000, "stock_search_timeout": 10000,
                                       "market_settings_timeout": 1000, "market_load_timeout": 10000}[scenario]
                        self.assertEqual([expected_ms], [item["milliseconds"] for item in report["events"]
                                                        if item["name"] == "bounded_wait_false"])
                    if scenario == "thread_start":
                        self.assertEqual([{"type": "RuntimeError", "message": "fixture thread_start"}], report["slot_errors"])
                        self.assertEqual(1, report["counts"]["thread_start"])
                        self.assertEqual(0, report["counts"]["content"])
                    expected_error = (None if scenario in {"thread_start", "independent_quit"}
                                      else "cleanup timer" if scenario == "cleanup_only" else "fixture " + scenario)
                    self.assertEqual(expected_error, report["error"])
                    self.assertEqual(0 if scenario in {"thread_start", "independent_quit"} else None, report["result"])
                    if scenario.startswith(("settings_", "cleanup_")) and scenario != "cleanup_only":
                        self.assertTrue(report["primary_preserved"])
                    if scenario == "settings_save":
                        self.assertEqual(321, report["settings_limit"])
                        names = [item["name"] for item in report["events"]]
                        self.assertLess(names.index("settings_ack_returned"), names.index("settings_mirror_written"))
                        self.assertLess(names.index("settings_mirror_written"), names.index("lease_close"))
                    if scenario == "settings_result_tail":
                        tail = next(item for item in report["events"] if item["name"] == "settings_field_cleared_before_exit")
                        self.assertEqual(1, tail["running"])
                        self.assertEqual(1, tail["child_count"])
                    cleanup_messages = {"cleanup_timer": ["cleanup timer"], "cleanup_stock_geometry": ["cleanup stock geometry"],
                                        "cleanup_market_geometry": ["cleanup market geometry"], "cleanup_archive": ["cleanup archive"],
                                        "cleanup_combined": ["cleanup timer", "cleanup stock geometry", "cleanup market geometry", "cleanup archive"]}
                    for message in cleanup_messages.get(scenario, ()):
                        self.assertIn(message, "\n".join(report["notes"]))
                finally:
                    (root / "release").touch()
                    if process.poll() is None:
                        process.wait(timeout=10)

    def test_market_constructor_failure_closes_acquired_stock_window(self):
        self._assert_acquisition("market_constructor")

    def test_content_constructor_failure_stops_acquired_timers_and_windows(self):
        self._assert_acquisition("content_constructor")

    def test_timer_start_failure_stops_the_already_started_timer(self):
        self._assert_acquisition("timer_start")

    def test_exec_entry_failure_joins_real_window_worker_before_lease_release(self):
        self._assert_acquisition("exec_entry")

    def test_independent_about_to_quit_joins_real_window_worker(self):
        self._assert_acquisition("independent_quit")

    def test_independent_exec_failure_joins_accepted_content_thread(self):
        self._assert_acquisition("exec_content")

    def test_qt_slot_thread_start_failure_preserves_error_without_joining_unstarted_thread(self):
        self._assert_acquisition("thread_start")

    def test_prepare_native_past_original_3000ms_retains_child_lease(self):
        self._assert_acquisition("prepare_timeout")

    def test_search_native_past_original_10000ms_retains_child_lease(self):
        self._assert_acquisition("stock_search_timeout")

    def test_market_settings_native_past_original_1000ms_retains_child_lease(self):
        self._assert_acquisition("market_settings_timeout")

    def test_market_load_native_past_original_10000ms_retains_child_lease(self):
        self._assert_acquisition("market_load_timeout")


class NewsOwnerCloseTests(unittest.TestCase):
    _assert_acquisition = NewsAcquisitionTests._assert_acquisition

    def test_settings_save_native_finishes_local_mirror_before_lease_release(self):
        self._assert_acquisition("settings_save")

    def test_settings_load_native_finishes_before_lease_release(self):
        self._assert_acquisition("settings_load")

    def test_settings_plan_native_finishes_before_lease_release(self):
        self._assert_acquisition("settings_plan")

    def test_cleared_settings_field_keeps_qobject_owned_native_until_actual_exit(self):
        self._assert_acquisition("settings_result_tail")

    def test_timer_cleanup_error_preserves_primary_and_drains_all_owners(self):
        self._assert_acquisition("cleanup_timer")

    def test_stock_geometry_error_preserves_primary_and_native_drain(self):
        self._assert_acquisition("cleanup_stock_geometry")

    def test_market_geometry_error_preserves_primary_and_native_drain(self):
        self._assert_acquisition("cleanup_market_geometry")

    def test_archive_close_error_preserves_primary_and_other_native_drain(self):
        self._assert_acquisition("cleanup_archive")

    def test_combined_cleanup_errors_are_all_observed_after_native_drain(self):
        self._assert_acquisition("cleanup_combined")

    def test_cleanup_error_without_primary_remains_a_failure_after_drain(self):
        self._assert_acquisition("cleanup_only")


if __name__ == "__main__":
    unittest.main()

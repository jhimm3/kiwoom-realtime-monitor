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
        initial = next(node for node in owner.body if isinstance(node, ast.AnnAssign)
                       and isinstance(node.target, ast.Name) and node.target.id == "last_content_signature")
        mode = next(node for node in owner.body if isinstance(node, ast.If)
                    and any(isinstance(item, ast.FunctionDef) and item.name == "schedule_content_sync" for item in node.body))
        setup = [node for node in mode.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "last_content_signature" for target in node.targets)]
        schedule = next(node for node in mode.body if isinstance(node, ast.FunctionDef) and node.name == "schedule_content_sync")
        factory = ast.parse("def build(content_sync, database_path, main_database_path, sync_news_catalog, content_sync_running):\n    pass\n").body[0]
        factory.body = [initial, *setup, schedule, *ast.parse(
            "return schedule_content_sync, lambda: last_content_signature, content_sync_running"
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
        return namespace["build"](self.service, self.news, self.root / "missing-main", self.catalog, threading.Event())

    def tick(self, scheduler):
        self.now += timedelta(seconds=1)
        scheduler[0]()
        for job in self.jobs:
            job.join(15)
            if job.is_alive():
                raise AssertionError("owned content worker did not settle")
        if scheduler[2].is_set():
            raise AssertionError("content_sync_running was not cleared")

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


class NewsProcessTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

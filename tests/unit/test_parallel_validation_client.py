from __future__ import annotations

import json
import queue
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import ParallelValidationClient
from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import RealtimeValidationRecorder
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def request_with_continuation(self, *args, **kwargs):
        self.calls += 1
        return self.result

    def server_now(self):
        return None

    def get_access_token(self):
        return "token"


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.wall = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.value

    def now(self) -> datetime:
        return self.wall + timedelta(seconds=self.value - 100.0)

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ParallelValidationClientTests(unittest.TestCase):
    def test_order_api_never_enters_parallel_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            central = FakeClient(({}, False, ""))
            local = FakeClient(({}, False, ""))
            client = ParallelValidationClient(central, local, Path(directory) / "report.jsonl")
            with self.assertRaisesRegex(ValueError, "order APIs"):
                client.request("kt10000", "/api/dostk/ordr", {})
        self.assertEqual(0, central.calls)
        self.assertEqual(0, local.calls)
    def test_returns_central_immediately_and_records_background_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "comparison.jsonl"
            central = FakeClient(({"price": 100, "return_msg": "ok"}, False, ""))
            local = FakeClient(({"price": 101, "return_msg": "different text"}, False, ""))
            import threading
            entered, release, returned = threading.Event(), threading.Event(), threading.Event()
            original = local.request_with_continuation
            def delayed(*args, **kwargs):
                entered.set()
                release.wait(2)
                return original(*args, **kwargs)
            local.request_with_continuation = delayed
            client = ParallelValidationClient(central, local, report)
            results = []
            def request():
                results.append(client.request_with_continuation("ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"}))
                returned.set()
            caller = threading.Thread(target=request)
            caller.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(returned.wait(.5))
            finally:
                release.set()
                caller.join(2)
            self.assertFalse(caller.is_alive())
            result = results[0]
            self.assertEqual(100, result[0]["price"])
            client._jobs.join()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertFalse(row["equal"])
        self.assertEqual("NOT_COMPARABLE", row["status"])
        self.assertEqual(["price"], row["different_fields"])
        self.assertEqual(1, local.calls)

    def test_query_value_mismatch_requires_shared_immutable_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "comparison.jsonl"
            central = FakeClient(({"revision_id": "r1", "price": 100}, False, ""))
            local = FakeClient(({"revision_id": "r1", "price": 101}, False, ""))
            client = ParallelValidationClient(central, local, report)
            client.request_with_continuation(
                "ka10080", "/api/dostk/chart", {"stk_cd": "005930"},
            )
            client._jobs.join()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual("VALUE_MISMATCH", row["status"])
        self.assertEqual(["r1"], row["source_refs"]["shared"])

    def test_full_query_queue_is_counted_without_replacing_primary_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            central = FakeClient(({"price": 100}, False, ""))
            client = ParallelValidationClient(
                central, FakeClient(({"price": 100}, False, "")),
                Path(directory) / "comparison.jsonl",
            )
            with patch.object(client._jobs, "put_nowait", side_effect=queue.Full):
                result = client.request_with_continuation(
                    "ka10001", "/api/dostk/stkinfo", {"stk_cd": "005930"},
                )
            summary = client.validation_summary()
        self.assertEqual(100, result[0]["price"])
        self.assertEqual(1, summary["queue_skip_count"])
        self.assertEqual(1, summary["status_counts"]["MISSING"])

    def test_realtime_events_are_paired_by_stock_time_and_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report)
            first = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            second = TradeTick("005930", 70100, 120, 8400000, 20, 70200, "101010")
            recorder.observe_central("trade", first)
            recorder.observe_central("trade", second)
            recorder.observe_local("trade", first)
            recorder.observe_local("trade", second)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["equal"] for row in rows))
        self.assertEqual(["MATCH", "NOT_COMPARABLE"], [row["status"] for row in rows])

    def test_unmatched_event_expires_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(
                report, match_timeout_seconds=2, monotonic_provider=clock.monotonic,
                now_provider=clock.now,
            )
            tick = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            recorder.observe_central("trade", tick)
            clock.advance(3)
            self.assertEqual(1, recorder.flush_expired())
            recorder.wait_for_writes()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual("MISSING", row["status"])
        self.assertEqual("direct", row["missing_side"])
        self.assertEqual(1, row["metrics"]["unmatched_count"])

    def test_pending_limit_eviction_is_counted_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report, pending_limit=100)
            for index in range(101):
                recorder.observe_central("trade", TradeTick(
                    f"{index:06d}", 70000, 100, 7000000, 10, 70100, "101010",
                ))
            summary = recorder.validation_summary()
            recorder.wait_for_writes()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual("pending_limit_eviction", row["reason"])
        self.assertEqual(1, summary["eviction_count"])
        self.assertEqual(100, summary["pending_nas_count"])

    def test_full_report_queue_is_counted_without_blocking_observer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RealtimeValidationRecorder(
                Path(directory) / "realtime.jsonl", report_queue_limit=1,
            )
            with patch.object(recorder._report_jobs, "put_nowait", side_effect=queue.Full):
                recorder.observe_central("trade", {"code": "005930"})
            summary = recorder.validation_summary()
        self.assertEqual(1, summary["queue_skip_count"])
        self.assertEqual(1, summary["status_counts"]["NOT_COMPARABLE"])
        self.assertEqual(1, summary["status_counts"]["MISSING"])

    def test_late_and_out_of_order_are_separate_from_value_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(
                report, late_threshold_ms=500, monotonic_provider=clock.monotonic,
                now_provider=clock.now,
            )
            current = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            earlier = TradeTick("005930", 69900, 90, 6200000, 5, 70100, "101009")
            recorder.observe_central("trade", current)
            clock.advance(0.8)
            recorder.observe_local("trade", current)
            recorder.observe_central("trade", earlier)
            recorder.observe_local("trade", earlier)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual("LATE", rows[0]["status"])
        self.assertEqual("OUT_OF_ORDER", rows[1]["status"])
        self.assertEqual(800.0, rows[1]["metrics"]["arrival_gap_ms"]["max"])

    def test_source_clock_skew_and_revision_refs_are_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(
                report, monotonic_provider=clock.monotonic, now_provider=clock.now,
            )
            tick = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
            recorder.observe_central("trade", tick, metadata={
                "revision_id": "nas-r1", "received_at": "2026-09-14T09:00:00+00:00",
            })
            recorder.observe_local("trade", tick, metadata={
                "received_at": "2026-09-14T09:00:00.250000+00:00",
            })
            recorder.wait_for_writes()
            row = json.loads(report.read_text(encoding="utf-8").strip())
        self.assertEqual(["nas-r1"], row["source_refs"]["nas"])
        self.assertEqual(250.0, row["timing"]["source_clock_skew_ms"])

    def test_repeated_identical_provider_event_is_duplicate(self) -> None:
        event = {
            "execution_no": "exec-1", "code": "005930", "name": "삼성전자",
            "side": "매수", "price": 70000, "quantity": 1,
            "trade_time": "101010", "market": "KRX",
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report)
            recorder.observe_central("order_execution", event)
            recorder.observe_local("order_execution", event)
            recorder.observe_central("order_execution", event)
            recorder.observe_local("order_execution", event)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["MATCH", "DUPLICATE"], [row["status"] for row in rows])

    def test_duplicate_provider_event_before_counterpart_does_not_replace_pending(self) -> None:
        event = {
            "order_no": "order-1", "execution_no": "exec-1", "code": "005930",
            "name": "삼성전자", "side": "매수", "price": 70000, "quantity": 1,
            "trade_time": "101010", "market": "KRX",
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "realtime.jsonl"
            recorder = RealtimeValidationRecorder(report)
            recorder.observe_central("order_execution", event)
            recorder.observe_central("order_execution", event)
            recorder.observe_local("order_execution", event)
            recorder.wait_for_writes()
            rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["DUPLICATE", "MATCH"], [row["status"] for row in rows])
        self.assertEqual(0, recorder.validation_summary()["pending_nas_count"])


class _ComparisonProbe:
    def __init__(self, action=None):
        self.action = action
        self.calls = []
        self.close_calls = 0
        self.now = datetime(2026, 10, 28, 9, tzinfo=timezone(timedelta(hours=9)))

    def request_with_continuation(self, api_id, path, body, **kwargs):
        # Old product code has no stop API. This fake-service-only operation
        # terminates that real thread after the independent oracle was observed.
        if api_id == "__test_cleanup__":
            raise SystemExit()
        self.calls.append((api_id, dict(body)))
        if self.action is not None:
            return self.action(api_id, path, body, **kwargs)
        return ({"revision_id": api_id, "value": body.get("n", 0)}, False, "")

    def server_now(self):
        return self.now

    def get_access_token(self):
        return "fixture-token"

    def load_stored_fundamentals(self, code):
        return {"code": code, "source": "borrowed-primary"}

    def close(self):
        self.close_calls += 1


def _exercise_pending_native_start(case, owner_type, make_owner, thread_name, *,
                                   register=None, interrupt_native_join=False):
    """CPython 3.13 only: two actual child gates precede test-owned cleanup."""
    import _thread
    import socket
    import sys
    from types import SimpleNamespace

    case.assertEqual(("cpython", 3, 13),
                     (sys.implementation.name, *sys.version_info[:2]),
                     "This native-handle oracle is scoped to CPython 3.13")
    real_thread = threading.Thread
    real_start = real_thread.start
    real_inner = real_thread._bootstrap_inner
    real_finish = owner_type.finish_comparison_shutdown
    native_entered, allow_ident = threading.Event(), threading.Event()
    native_tail_entered, allow_native_exit = threading.Event(), threading.Event()
    cleanup_entered, constructor_returned = threading.Event(), threading.Event()
    c_join_entered, c_join_returned = threading.Event(), threading.Event()
    start_error = OSError("H2 actual start wait interrupted after native spawn")
    first_wait_error = InterruptedError("H2 first native join interruption")
    second_wait_error = OSError("H2 second native join interruption")
    pending_wait_errors = ([first_wait_error, second_wait_error]
                           if interrupt_native_join else [])
    captures, constructor_errors, constructor_values = [], [], []
    child_errors, finish_results, join_attempts, real_join_returns = [], [], [], []
    caller = None

    class ObservedNativeHandle:
        def __init__(self, actual):
            self.actual = actual
            self.actual_join = actual.join

        def __getattr__(self, name):
            return getattr(self.actual, name)

        def join(self, *args, **kwargs):
            # Installed only AFTER the real C start consumed this exact handle.
            # Identity/is_done are delegated unchanged; no stop flag is forged.
            join_attempts.append((self.actual, args, dict(kwargs)))
            if pending_wait_errors:
                raise pending_wait_errors.pop(0)
            c_join_entered.set()
            result = self.actual_join(*args, **kwargs)
            real_join_returns.append(self.actual)
            c_join_returned.set()
            return result

    def find_capture(thread):
        return next((item for item in captures if item.thread is thread), None)

    def inner(thread):
        record = find_capture(thread)
        if record is None:
            return real_inner(thread)
        record.native_entry_ident = _thread.get_ident()
        native_entered.set()
        try:
            try:
                if not allow_ident.wait(5):
                    raise TimeoutError("H2 pre-ident native gate was not released")
                real_inner(thread)
                record.python_inner_returned = True
            finally:
                # _delete has returned, but C thread_run is still inside its
                # Python bootstrap call. No _set_done/is_done proof is used.
                native_tail_entered.set()
                if not allow_native_exit.wait(5):
                    raise TimeoutError("H2 native-tail gate was not released")
                record.native_python_exit.set()
        except BaseException as error:
            child_errors.append(error)
            raise

    def start(thread):
        if thread.name != thread_name:
            return real_start(thread)
        owner = thread._target.__self__
        record = SimpleNamespace(owner=owner, thread=thread,
            actual_handle=thread._handle, proxy=None, native_entry_ident=None,
            python_inner_returned=False, native_python_exit=threading.Event())
        captures.append(record)  # Capture the real object before any assertion/start.
        if register is not None:
            register(owner, thread)
        case.assertEqual(1, len(captures))
        actual_started_wait = thread._started.wait

        def cancelled_started_wait(*_args, **_kwargs):
            # Thread.start reaches this exact call only after native C spawn.
            # C retained actual_handle before this Python-only proxy is installed.
            case.assertIs(record.actual_handle, thread._handle)
            case.assertEqual("_ThreadHandle", type(record.actual_handle).__name__)
            case.assertNotEqual(0, record.actual_handle.ident)
            record.proxy = ObservedNativeHandle(record.actual_handle)
            thread._handle = record.proxy
            if not native_entered.wait(2):
                raise TimeoutError("H2 actual native child did not enter its gate")
            raise start_error

        thread._started.wait = cancelled_started_wait
        try:
            return real_start(thread)
        finally:
            thread._started.wait = actual_started_wait

    def finish(owner):
        captured = any(item.owner is owner for item in captures)
        if captured:
            cleanup_entered.set()
        receipt = real_finish(owner)
        if captured:
            finish_results.append(receipt)
        return receipt

    def construct():
        try:
            constructor_values.append(make_owner())
        except BaseException as error:
            constructor_errors.append(error)
        finally:
            constructor_returned.set()

    try:
        with patch.object(real_thread, "start", start), \
                patch.object(real_thread, "_bootstrap_inner", inner), \
                patch.object(owner_type, "finish_comparison_shutdown", finish), \
                patch.object(Path, "open", side_effect=AssertionError("H2 unexpected report I/O")) as file_io, \
                patch.object(socket, "socket", side_effect=AssertionError("H2 unexpected network I/O")) as network_io:
            caller = real_thread(target=construct, name="h2-constructor-caller")
            caller.start()
            case.assertTrue(native_entered.wait(2))
            case.assertTrue(cleanup_entered.wait(2))
            case.assertEqual(1, len(captures))
            record = captures[0]
            case.assertIsNone(record.thread.ident)
            case.assertFalse(record.thread._started.is_set())
            case.assertEqual(record.native_entry_ident, record.actual_handle.ident)
            case.assertNotEqual(0, record.native_entry_ident)
            # First old-source failure: the ACTUAL child is still held before
            # ident publication, not a fake is_alive result or cleanup snapshot.
            case.assertFalse(constructor_returned.wait(2),
                "constructor returned while its actual native child was held before ident")
            case.assertTrue(c_join_entered.is_set())
            case.assertFalse(c_join_returned.is_set())
            case.assertIsNone(record.thread.ident)
            allow_ident.set()
            case.assertTrue(native_tail_entered.wait(2))
            case.assertTrue(record.python_inner_returned)
            case.assertIsNotNone(record.thread.ident)
            case.assertFalse(constructor_returned.wait(2),
                "constructor returned after Python bootstrap but before actual native tail exit")
            case.assertFalse(c_join_returned.is_set())
            allow_native_exit.set()
            case.assertTrue(constructor_returned.wait(2))
            caller.join(3)
            case.assertFalse(caller.is_alive())
            case.assertEqual([], constructor_values)
            case.assertEqual(1, len(constructor_errors))
            case.assertIs(start_error, constructor_errors[0])
            case.assertIs(first_wait_error if interrupt_native_join else None,
                          start_error.__cause__)
            case.assertEqual([], child_errors)
            case.assertTrue(record.native_python_exit.is_set())
            case.assertTrue(c_join_returned.is_set())
            case.assertEqual([record.actual_handle], real_join_returns)
            case.assertEqual(3 if interrupt_native_join else 1, len(join_attempts))
            for actual, args, kwargs in join_attempts:
                case.assertIs(record.actual_handle, actual)
                case.assertEqual((), args)
                case.assertEqual({}, kwargs)
            case.assertEqual(1, len(finish_results))
            receipt = finish_results[0]
            case.assertIs(record.owner, receipt.owner)
            case.assertIs(True, receipt.native_completed)
            case.assertIs(first_wait_error if interrupt_native_join else None, receipt.wait_error)
            join_count = len(join_attempts)
            case.assertIs(receipt, record.owner.finish_comparison_shutdown())
            case.assertIs(receipt, record.owner.finish_comparison_shutdown())
            expected_close_error = (start_error if owner_type is RealtimeValidationRecorder
                                    else first_wait_error if interrupt_native_join else None)
            for _ in range(2):
                if expected_close_error is None:
                    case.assertIsNone(record.owner.close())
                else:
                    with case.assertRaises(type(expected_close_error)) as repeated:
                        record.owner.close()
                    case.assertIs(expected_close_error, repeated.exception)
            case.assertEqual(join_count, len(join_attempts),
                             "terminal receipt reuse started another native join")
            fact = record.owner.comparison_lifecycle_state()
            case.assertIs(type(fact.active_requests), int)
            case.assertEqual((True, 0, True,
                "thread_start" if owner_type is RealtimeValidationRecorder else None),
                (fact.admission_closed, fact.active_requests, fact.worker_stopped, fact.failure_stage))
            file_io.assert_not_called()
            network_io.assert_not_called()
            return record.owner, receipt
    finally:
        # Root's old source can fail at the first oracle. Release BOTH native
        # gates before any shutdown/join, while the fixture TEMP still exists.
        allow_ident.set()
        allow_native_exit.set()
        cleanup_errors = []
        for record in captures:
            try:
                record.owner.close()
            except BaseException:
                # Original constructor/wait errors are asserted above; this is
                # safety cleanup only and cannot make a missing product join PASS.
                pass
            try:
                if record.actual_handle.ident:
                    record.actual_handle.join(3)
            except BaseException as error:
                cleanup_errors.append(error)
            finally:
                record.thread._handle = record.actual_handle
        if caller is not None:
            try:
                if caller.ident is not None:
                    caller.join(3)
                elif caller._handle.ident:
                    caller._handle.join(3)
            except BaseException as error:
                cleanup_errors.append(error)
        # Complete every cleanup attempt before reporting a teardown failure.
        for record in captures:
            if record.actual_handle.ident:
                case.assertTrue(record.native_python_exit.is_set())
                case.assertTrue(record.actual_handle.is_done())
        if caller is not None and caller.ident is not None:
            case.assertFalse(caller.is_alive(), "H2 constructor caller survived test cleanup")
        case.assertEqual([], cleanup_errors)


class ParallelValidationLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.clients = []

    def tearDown(self):
        self.finish_clients()

    @contextmanager
    def temporary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            try:
                yield directory
            finally:
                # Stop the old product's daemon before deleting its report root.
                self.finish_clients()

    def finish_clients(self):
        clients, self.clients = self.clients, []
        for client, thread in clients:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    # The failure test asserts the original exception itself.
                    # Cleanup never converts a failed close into a PASS oracle.
                    pass
            elif thread.is_alive():
                client._jobs.put(("__test_cleanup__", "/", {}, "N", "",
                                  ({}, False, ""), "", 0.0, 0.0), timeout=2)
            thread.join(2)
            self.assertFalse(thread.is_alive(), "fixture left a real comparison thread alive")

    def make_client(self, primary, local, report, **kwargs):
        previous = set(threading.enumerate())
        client = ParallelValidationClient(primary, local, report, **kwargs)
        created = [thread for thread in threading.enumerate()
                   if thread not in previous and thread.name == "central-local-validation"]
        self.assertEqual(1, len(created))
        self.clients.append((client, created[0]))
        return client, created[0]

    def close_client(self, client):
        close = getattr(client, "close", None)
        self.assertTrue(callable(close), "owned comparison thread requires a public close")
        close()

    def test_close_ends_idle_thread_rejects_new_comparison_and_keeps_borrowed_delegates(self):
        from dataclasses import FrozenInstanceError
        with self.temporary_directory() as directory:
            primary, local = _ComparisonProbe(), _ComparisonProbe()
            report = Path(directory) / "report.jsonl"
            client, thread = self.make_client(primary, local, report)
            lifecycle = getattr(client, "comparison_lifecycle_state", None)
            self.assertTrue(callable(lifecycle), "owned comparison thread requires public lifecycle facts")
            opened = lifecycle()
            self.assertEqual((False, 0, False, None), (
                opened.admission_closed, opened.active_requests, opened.worker_stopped, opened.failure_stage))
            self.assertTrue(thread.is_alive())
            with self.assertRaises(FrozenInstanceError):
                opened.active_requests = 99
            self.close_client(client)
            self.assertFalse(thread.is_alive())
            stopped = lifecycle()
            self.assertIsNot(opened, stopped)
            self.assertEqual((True, 0, True, None), (
                stopped.admission_closed, stopped.active_requests, stopped.worker_stopped, stopped.failure_stage))
            self.assertEqual((False, 0, False, None), (
                opened.admission_closed, opened.active_requests, opened.worker_stopped, opened.failure_stage))
            self.close_client(client)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                client.request("ka10001", "/fixture", {})
            with self.assertRaisesRegex(ValueError, "order APIs"):
                client.request("kt10000", "/api/dostk/ordr", {})
            self.assertIs(local.now, client.server_now())
            self.assertEqual("fixture-token", client.get_access_token())
            self.assertEqual({"code": "005930", "source": "borrowed-primary"},
                             client.load_stored_fundamentals("005930"))
            self.assertEqual([], primary.calls)
            self.assertEqual([], local.calls)
            self.assertEqual((0, 0), (primary.close_calls, local.close_calls))
            self.assertFalse(report.exists())

    def test_close_drains_accepted_fifo_once_and_does_not_wait_in_primary_request(self):
        entered, release, returned, closing, closed = (threading.Event() for _ in range(5))
        warning_entered, warning_release, third_returned, fourth_returned = (
            threading.Event() for _ in range(4))
        failures, results = [], []
        warnings = []

        with self.temporary_directory() as directory:
            primary, local = _ComparisonProbe(), _ComparisonProbe()
            report = Path(directory) / "report.jsonl"
            client, thread = self.make_client(primary, local, report, queue_limit=1)
            original_open = Path.open

            def slow_report(path, *args, **kwargs):
                if path == report and not entered.is_set():
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("fixture report release")
                return original_open(path, *args, **kwargs)

            def slow_first_warning(message, api_id):
                warnings.append((message, api_id))
                if not warning_entered.is_set():
                    warning_entered.set()
                    if not warning_release.wait(3):
                        raise TimeoutError("fixture warning release")

            def request(api_id, number, done):
                try:
                    results.append((number, client.request(api_id, "/fixture", {"n": number})))
                except BaseException as error:
                    failures.append(error)
                finally:
                    done.set()

            def close():
                closing.set()
                try:
                    self.close_client(client)
                except BaseException as error:
                    failures.append(error)
                finally:
                    closed.set()

            caller = threading.Thread(target=request, args=("ka10080", 2, returned))
            third = threading.Thread(target=request, args=("ka10081", 3, third_returned))
            fourth = threading.Thread(target=request, args=("ka10002", 4, fourth_returned))
            closer = threading.Thread(target=close)
            with patch.object(Path, "open", slow_report), patch(
                    "kiwoom_monitor.infrastructure.kiwoom_rest.validation_client.logger.warning",
                    side_effect=slow_first_warning):
                try:
                    self.assertEqual(1, client.request("ka10001", "/fixture", {"n": 1})["value"])
                    self.assertTrue(entered.wait(2))
                    caller.start()
                    self.assertTrue(returned.wait(0.5), "primary result waited for report I/O")
                    self.assertEqual(2, dict(results)[2]["value"])
                    # The first report is blocked; the one-slot queue now owns
                    # job2. Only the first queue.Full warning is held by a gate.
                    third.start()
                    self.assertTrue(warning_entered.wait(2))
                    self.assertFalse(third_returned.is_set())
                    fourth.start()
                    self.assertTrue(fourth_returned.wait(0.5), "primary admission waited for warning I/O")
                    self.assertEqual(4, dict(results)[4]["value"])
                    self.assertFalse(release.is_set())
                    self.assertFalse(warning_release.is_set())
                    closer.start()
                    self.assertTrue(closing.wait(2))
                    self.assertFalse(closed.wait(0.05))
                finally:
                    warning_release.set()
                    release.set()
                    for participant in (caller, third, fourth, closer):
                        if participant.ident is not None:
                            participant.join(3)
            self.assertFalse(caller.is_alive())
            self.assertFalse(third.is_alive())
            self.assertFalse(fourth.is_alive())
            self.assertFalse(closer.is_alive())
            self.assertEqual([], failures)
            self.assertFalse(thread.is_alive())
            self.close_client(client)
            expected = [("ka10001", {"n": 1}), ("ka10080", {"n": 2})]
            self.assertEqual(expected + [("ka10081", {"n": 3}), ("ka10002", {"n": 4})], primary.calls)
            self.assertEqual(expected, local.calls)
            self.assertEqual([(2, 2), (3, 3), (4, 4)],
                             sorted((number, result["value"]) for number, result in results))
            self.assertEqual(["ka10081", "ka10002"], [api_id for _message, api_id in warnings])
            summary = client.validation_summary()
            self.assertEqual(2, summary["queue_skip_count"])
            self.assertEqual(2, summary["status_counts"]["MISSING"])
            rows = [json.loads(row) for row in report.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["ka10001", "ka10080"], [row["api_id"] for row in rows])
            self.assertEqual(["MATCH", "MATCH"], [row["status"] for row in rows])

    def test_close_waits_for_request_admitted_before_cut_including_existing_fallback(self):
        import time
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import CentralServerUnavailable
        for fallback in (False, True):
            with self.subTest(fallback=fallback), self.temporary_directory() as directory:
                entered, release, closing, closed = (threading.Event() for _ in range(4))
                results, failures = [], []

                def delayed(api_id, _path, body, **_kwargs):
                    if fallback:
                        raise CentralServerUnavailable("fixture unavailable")
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("fixture primary release")
                    return ({"revision_id": api_id, "value": body["n"]}, False, "")

                def delayed_fallback(api_id, _path, body, **_kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("fixture fallback release")
                    return ({"revision_id": api_id, "value": body["n"]}, False, "")

                primary = _ComparisonProbe(delayed)
                local = _ComparisonProbe(delayed_fallback if fallback else None)
                report = Path(directory) / "report.jsonl"
                client, worker = self.make_client(primary, local, report, fallback_on_unavailable=fallback)
                lifecycle = getattr(client, "comparison_lifecycle_state", None)
                self.assertTrue(callable(lifecycle), "owned comparison thread requires public lifecycle facts")

                def request():
                    try:
                        results.append(client.request("ka10001", "/fixture", {"n": 7}))
                    except BaseException as error:
                        failures.append(error)

                def close():
                    closing.set()
                    try:
                        self.close_client(client)
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        closed.set()

                caller, closer = threading.Thread(target=request), threading.Thread(target=close)
                try:
                    caller.start()
                    self.assertTrue(entered.wait(2))
                    admitted = lifecycle()
                    self.assertEqual((False, 1, False, None), (
                        admitted.admission_closed, admitted.active_requests, admitted.worker_stopped, admitted.failure_stage))
                    closer.start()
                    self.assertTrue(closing.wait(2))
                    self.assertFalse(closed.wait(0.05))
                    # closing is set before close(). Observe the actual public
                    # admission cut while the original primary gate stays held.
                    deadline = time.monotonic() + 2
                    cut = lifecycle()
                    while not cut.admission_closed and time.monotonic() < deadline:
                        closed.wait(0.005)
                        cut = lifecycle()
                    self.assertEqual((True, 1, False, None), (
                        cut.admission_closed, cut.active_requests, cut.worker_stopped, cut.failure_stage))
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
                    caller.join(3)
                    if closer.ident is not None:
                        closer.join(3)
                self.assertFalse(caller.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual([], failures)
                self.assertEqual([7], [result["value"] for result in results])
                self.assertFalse(worker.is_alive())
                stopped = lifecycle()
                self.assertEqual((True, 0, True, None), (
                    stopped.admission_closed, stopped.active_requests, stopped.worker_stopped, stopped.failure_stage))
                self.close_client(client)
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    client.request("ka10080", "/fixture", {"n": 8})
                self.assertEqual([("ka10001", {"n": 7})], primary.calls)
                self.assertEqual([("ka10001", {"n": 7})], local.calls)
                if fallback:
                    self.assertFalse(report.exists())
                else:
                    rows = [json.loads(row) for row in report.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(["ka10001"], [row["api_id"] for row in rows])

    def test_report_failure_is_not_retried_and_close_preserves_job_result_remaining_and_error(self):
        entered, release = threading.Event(), threading.Event()
        log_entered, log_release, fact_returned = (threading.Event() for _ in range(3))
        log_facts, fact_errors = [], []
        sentinel = PermissionError("fixture report denied")
        writes = []

        def delayed(api_id, _path, body, **_kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("fixture local release")
            return ({"revision_id": api_id, "value": body["n"]}, False, "")

        with self.temporary_directory() as directory:
            primary, local = _ComparisonProbe(), _ComparisonProbe(delayed)
            report = Path(directory) / "report.jsonl"
            client, thread = self.make_client(primary, local, report)
            lifecycle = getattr(client, "comparison_lifecycle_state", None)
            self.assertTrue(callable(lifecycle), "owned comparison thread requires public lifecycle facts")
            original_open = Path.open

            def denied(path, *args, **kwargs):
                if path == report:
                    writes.append(path)
                    raise sentinel
                return original_open(path, *args, **kwargs)

            def held_error(*_args, **_kwargs):
                log_entered.set()
                if not log_release.wait(3):
                    raise TimeoutError("fixture error log release")

            def read_fact():
                try:
                    log_facts.append(lifecycle())
                except BaseException as error:
                    fact_errors.append(error)
                finally:
                    fact_returned.set()

            reader = threading.Thread(target=read_fact)
            with patch.object(Path, "open", denied), patch(
                    "kiwoom_monitor.infrastructure.kiwoom_rest.validation_client.logger.error",
                    side_effect=held_error) as errors:
                try:
                    try:
                        client.request("ka10001", "/fixture", {"n": 1})
                        self.assertTrue(entered.wait(2))
                        client.request("ka10080", "/fixture", {"n": 2})
                    finally:
                        release.set()
                    self.assertTrue(log_entered.wait(2))
                    reader.start()
                    self.assertTrue(fact_returned.wait(0.5), "public fact read waited for error log I/O")
                    self.assertEqual([], fact_errors)
                    self.assertEqual(1, len(log_facts))
                    during_log = log_facts[0]
                    self.assertEqual((False, 0, False, "report"), (
                        during_log.admission_closed, during_log.active_requests, during_log.worker_stopped, during_log.failure_stage))
                    self.assertTrue(thread.is_alive())
                    self.assertFalse(log_release.is_set())
                finally:
                    log_release.set()
                    if reader.ident is not None:
                        reader.join(3)
                self.assertFalse(reader.is_alive())
                thread.join(3)
                self.assertFalse(thread.is_alive())
                stopped = lifecycle()
                self.assertEqual((False, 0, True, "report"), (
                    stopped.admission_closed, stopped.active_requests, stopped.worker_stopped, stopped.failure_stage))
                self.assertEqual([report], writes, "a failed report must not be rewritten as local failure")
                # An optional report failure does not turn a later successful
                # primary query into a new foreground failure policy.
                self.assertEqual(3, client.request("ka10081", "/fixture", {"n": 3})["value"])
                for _ in range(2):
                    with self.assertRaises(PermissionError) as caught:
                        self.close_client(client)
                    self.assertIs(sentinel, caught.exception)
                    cut = lifecycle()
                    self.assertEqual((True, 0, True, "report"), (
                        cut.admission_closed, cut.active_requests, cut.worker_stopped, cut.failure_stage))
                errors.assert_called_once_with(
                    "중앙·로컬 비교 처리 실패: stage=%s api_id=%s error_type=%s",
                    "report", "ka10001", "PermissionError",
                )
                self.assertNotIn(str(sentinel), str(errors.call_args))
            self.assertEqual([("ka10001", {"n": 1})], local.calls)
            self.assertEqual(2, client._jobs.qsize())
            job, result, local_error = client._failed_job
            self.assertEqual(("ka10001", {"n": 1}), (job[0], job[2]))
            self.assertEqual(({"revision_id": "ka10001", "value": 1}, False, ""), result)
            self.assertEqual("", local_error)
            self.assertFalse(report.exists())

    def test_constructor_start_failure_preserves_error_and_joins_if_thread_started(self):
        for started in (False, True):
            with self.subTest(started=started), self.temporary_directory() as directory:
                sentinel = OSError("fixture start failure")
                original_start = threading.Thread.start
                captured = []

                def failed_start(thread):
                    client = thread._target.__self__
                    captured.append((client, thread))
                    if started:
                        original_start(thread)
                    raise sentinel

                with patch.object(threading.Thread, "start", failed_start):
                    with self.assertRaises(OSError) as caught:
                        ParallelValidationClient(_ComparisonProbe(), _ComparisonProbe(),
                                                 Path(directory) / "report.jsonl")
                self.assertIs(sentinel, caught.exception)
                self.assertEqual(1, len(captured))
                client, thread = captured[0]
                if started:
                    self.clients.append((client, thread))
                self.assertFalse(thread.is_alive(), "constructor did not return an owner for this thread")

    def test_constructor_cancel_after_native_spawn_joins_preident_and_native_tail(self):
        for interrupted_join in (False, True):
            with self.subTest(interrupted_join=interrupted_join), self.temporary_directory() as directory:
                primary, local = _ComparisonProbe(), _ComparisonProbe()
                report = Path(directory) / "pending-start.jsonl"

                def create():
                    return ParallelValidationClient(primary, local, report)

                owner, receipt = _exercise_pending_native_start(
                    self, ParallelValidationClient, create, "central-local-validation",
                    register=lambda client, thread: self.clients.append((client, thread)),
                    interrupt_native_join=interrupted_join,
                )
                self.assertEqual([], primary.calls)
                self.assertEqual([], local.calls)
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    owner.request_with_continuation("ka10001", "/", {"n": 7})
                self.assertEqual([], primary.calls, "closed comparison admitted a primary request")
                self.assertEqual([], local.calls, "closed comparison admitted a local job")
                self.assertIs(local.now, owner.server_now())
                self.assertEqual("fixture-token", owner.get_access_token())
                self.assertEqual({"code": "005930", "source": "borrowed-primary"},
                                 owner.load_stored_fundamentals("005930"))
                self.assertIs(receipt, owner.finish_comparison_shutdown())
                self.assertEqual((0, 0), (primary.close_calls, local.close_calls))
                self.assertFalse(report.exists())




class _RecorderReportSink:
    """Controlled report stream for real writer-thread lifecycle tests only."""
    def __init__(self, *, blocked=False, first_error=None):
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.first_error = first_error
        self.attempted = []
        self.written = []
        self.stop = False

    @property
    def parent(self):
        return self

    def mkdir(self, **_kwargs):
        pass

    def open(self, *_args, **_kwargs):
        if self.stop:
            raise SystemExit()  # Old-source test-only idle-daemon cleanup.
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, text):
        self.attempted.append(json.loads(text))
        if len(self.attempted) == 1:
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError("controlled Recorder report I/O gate")
            if self.first_error is not None:
                raise self.first_error
        self.written.append(text)
        return len(text)


class RealtimeRecorderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.resources = []

    def _new_recorder(self, sink, *, clock=None, start_error=None, after_start=False):
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client as module
        real_thread = threading.Thread
        def thread_factory(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            owner = kwargs["target"].__self__
            self.resources.append((owner, thread, sink))
            if start_error is not None:
                real_start = thread.start
                def fail_start():
                    if after_start:
                        real_start()
                    raise start_error
                thread.start = fail_start
            return thread
        clock = clock or FakeClock()
        with patch.object(module.threading, "Thread", side_effect=thread_factory):
            recorder = RealtimeValidationRecorder(sink, report_queue_limit=4,
                monotonic_provider=clock.monotonic, now_provider=clock.now)
        return recorder, self.resources[-1][1]

    def tearDown(self):
        for recorder, thread, sink in self.resources:
            sink.release.set()
            if not thread.is_alive():
                continue
            close = getattr(recorder, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    # Expected first report/start errors are asserted in the
                    # test; cleanup must still verify actual thread exit.
                    pass
            else:
                # Retain the old-source failure oracle, then stop only this
                # captured idle native daemon. No product stop API is invented.
                recorder.wait_for_writes()
                sink.stop = True
                recorder._report_jobs.put_nowait({})
            thread.join(2)
            self.assertFalse(thread.is_alive(), "owned Recorder writer survived test cleanup")

    def test_recorder_close_joins_accepted_observer_and_fifo_without_blocking_primary_on_report_io(self):
        from dataclasses import asdict
        from contextlib import nullcontext
        from PySide6.QtCore import Qt
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client as module
        sink = _RecorderReportSink(blocked=True)
        recorder, writer = self._new_recorder(sink)
        worker = CentralRealtimeWorker(DataSourceSettings("personal_server", "https://nas.example", "token"),
            ("005930", "000660"), validation_recorder=recorder)
        emitted = []
        worker.trade_received.connect(emitted.append, Qt.ConnectionType.DirectConnection)
        first = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
        second = TradeTick("000660", 71000, 120, 8400000, 20, 71200, "101010")
        unmatched = TradeTick("035420", 72000, 140, 9800000, 30, 72200, "101010")
        recorder.observe_central("trade", unmatched)
        recorder.observe_local("trade", first)
        worker._dispatch({"type": "trade", "payload": asdict(first)})
        self.assertTrue(sink.entered.wait(1))
        self.assertEqual([first], emitted)
        recorder.observe_local("trade", second)
        input_entered, input_release, producer_returned, close_returned = (threading.Event() for _ in range(4))
        close_waiting, writer_join_entered = threading.Event(), threading.Event()
        errors = []
        original_payload = module._event_payload
        def payload(event):
            if getattr(event, "code", None) == "000660":
                input_entered.set()
                if not input_release.wait(5):
                    raise TimeoutError("controlled accepted Recorder observation")
            return original_payload(event)
        def produce():
            try:
                worker._dispatch({"type": "trade", "payload": asdict(second)})
            except BaseException as error:
                errors.append(error)
            finally:
                producer_returned.set()
        def finish():
            try:
                close = getattr(recorder, "close", None)
                if callable(close):
                    close()
            except BaseException as error:
                errors.append(error)
            finally:
                close_returned.set()
        producer = threading.Thread(target=produce)
        closer = threading.Thread(target=finish)
        state = getattr(recorder, "_state", None)
        original_wait = state.wait if state is not None else None
        original_writer_join = writer.join
        def admission_wait(*args, **kwargs):
            if threading.current_thread() is closer:
                close_waiting.set()
            return original_wait(*args, **kwargs)
        def writer_join(*args, **kwargs):
            if threading.current_thread() is closer:
                writer_join_entered.set()
            return original_writer_join(*args, **kwargs)
        state_wait_patch = (patch.object(state, "wait", side_effect=admission_wait)
                            if state is not None else nullcontext())
        try:
            with patch.object(module, "_event_payload", side_effect=payload), \
                    patch.object(writer, "join", side_effect=writer_join), state_wait_patch:
                producer.start()
                self.assertTrue(input_entered.wait(1))
                closer.start()
                if state is not None:
                    self.assertTrue(close_waiting.wait(1))
                else:
                    # Old source has no close/admission contract. Observe the
                    # exact attempted close return before the original failure
                    # assertion, rather than reaching a missing-fact exception.
                    self.assertTrue(close_returned.wait(1))
                self.assertFalse(close_returned.wait(.1))
                fact = recorder.comparison_lifecycle_state()
                self.assertEqual((True, 1, False, None),
                    (fact.admission_closed, fact.active_requests, fact.worker_stopped, fact.failure_stage))
                input_release.set()
                self.assertTrue(producer_returned.wait(1))
                self.assertEqual([first, second], emitted)
                self.assertTrue(writer_join_entered.wait(1))
                self.assertFalse(close_returned.wait(.1))
                self.assertEqual(0, recorder.comparison_lifecycle_state().active_requests)
                self.assertTrue(writer.is_alive())
                sink.release.set()
                producer.join(2); closer.join(2)
            self.assertFalse(producer.is_alive())
            self.assertFalse(closer.is_alive())
            self.assertFalse(writer.is_alive())
            self.assertFalse(errors)
            self.assertEqual(["005930", "000660"], [row["identity"][1] for row in sink.attempted])
            self.assertEqual(["MATCH", "MATCH"], [row["status"] for row in sink.attempted])
            self.assertEqual(2, len(sink.written))
            fact = recorder.comparison_lifecycle_state()
            self.assertEqual((True, 0, True, None),
                (fact.admission_closed, fact.active_requests, fact.worker_stopped, fact.failure_stage))
            summary = recorder.validation_summary()
            self.assertEqual(1, summary["pending_nas_count"])
            self.assertEqual(0, summary["status_counts"]["MISSING"])
            recorder.observe_local("trade", unmatched)
            self.assertEqual(0, recorder.flush_expired())
            self.assertEqual(summary, recorder.validation_summary())
            recorder.close()
        finally:
            input_release.set(); sink.release.set()
            if producer.ident is not None: producer.join(2)
            if closer.ident is not None: closer.join(2)
            worker.stop()

    def test_recorder_report_oserror_keeps_continue_metrics_first_error_row_and_actual_join(self):
        sentinel = OSError("controlled Recorder report error")
        sink = _RecorderReportSink(first_error=sentinel)
        recorder, writer = self._new_recorder(sink)
        first = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
        second = TradeTick("000660", 71000, 120, 8400000, 20, 71200, "101010")
        for tick in (first, second):
            recorder.observe_central("trade", tick)
            recorder.observe_local("trade", tick)
        recorder.wait_for_writes()
        self.assertEqual(["005930", "000660"], [row["identity"][1] for row in sink.attempted])
        self.assertEqual(1, len(sink.written))
        summary = recorder.validation_summary()
        self.assertEqual(1, summary["queue_skip_count"])
        self.assertEqual(1, summary["status_counts"]["MISSING"])
        close = getattr(recorder, "close", None)
        self.assertTrue(callable(close), "Recorder owner has no public close contract")
        with self.assertRaises(OSError) as caught:
            close()
        self.assertIs(sentinel, caught.exception)
        self.assertFalse(writer.is_alive())
        self.assertEqual(sink.attempted[0]["identity"], recorder._failed_report["identity"])
        self.assertEqual(sink.attempted[0]["checked_at"], recorder._failed_report["checked_at"])
        fact = recorder.comparison_lifecycle_state()
        self.assertEqual((True, 0, True, "report"),
            (fact.admission_closed, fact.active_requests, fact.worker_stopped, fact.failure_stage))
        with self.assertRaises(OSError) as repeated:
            close()
        self.assertIs(sentinel, repeated.exception)
        self.assertEqual(2, len(sink.attempted))

    def test_recorder_start_error_before_or_after_real_start_preserves_error_and_joins_created_thread(self):
        for after_start in (False, True):
            with self.subTest(after_start=after_start):
                sentinel = OSError("controlled Recorder start error")
                with self.assertRaises(OSError) as caught:
                    self._new_recorder(_RecorderReportSink(), start_error=sentinel, after_start=after_start)
                self.assertIs(sentinel, caught.exception)
                recorder, writer, _sink = self.resources[-1]
                self.assertFalse(writer.is_alive(), "constructor error abandoned a started Recorder writer")
                fact = getattr(recorder, "comparison_lifecycle_state", None)
                if callable(fact):
                    state = fact()
                    self.assertEqual((True, 0, True, "thread_start"),
                        (state.admission_closed, state.active_requests, state.worker_stopped, state.failure_stage))

    def test_recorder_constructor_cancel_after_native_spawn_joins_preident_and_native_tail(self):
        for interrupted_join in (False, True):
            with self.subTest(interrupted_join=interrupted_join):
                sink = _RecorderReportSink()

                def create():
                    return self._new_recorder(sink)

                owner, receipt = _exercise_pending_native_start(
                    self, RealtimeValidationRecorder, create, "realtime-validation-writer",
                    interrupt_native_join=interrupted_join,
                )
                before = owner.validation_summary()
                tick = TradeTick("005930", 70000, 100, 7000000, 10, 70100, "101010")
                owner.observe_central("trade", tick)
                owner.observe_local("trade", tick)
                self.assertEqual(0, owner.flush_expired())
                self.assertEqual(before, owner.validation_summary())
                self.assertEqual([], sink.attempted)
                self.assertEqual([], sink.written)
                self.assertFalse(sink.entered.is_set())
                self.assertIs(receipt, owner.finish_comparison_shutdown())


if __name__ == "__main__":
    unittest.main()

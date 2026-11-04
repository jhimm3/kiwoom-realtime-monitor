from __future__ import annotations

import unittest
from unittest import mock
from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from kiwoom_monitor.presentation.journal_workers import (
    AnalysisEnrichmentWorker, BackfillWorker, ConfirmWorker, DailyChartWorker, HistoryWorker,
    MarketIndexBackfillWorker,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryContext
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE


class JournalWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_confirm_worker_uses_after_close_day_and_filters_unfinished_bar(self) -> None:
        class Service:
            def load_today(self, code, target):
                self.called = (code, target)
                return (SimpleNamespace(minute=datetime(2026, 9, 8, 15, 29)),
                        SimpleNamespace(minute=datetime(2026, 9, 8, 20, 0)))
        service = Service(); received = []; saved = []
        def save_bars(*values):
            self.assertFalse(received)
            saved.append(values)
        worker = ConfirmWorker(service, "005930", date(2026, 9, 8),
            save_bars=save_bars, now_provider=lambda: datetime(2026, 9, 8, 20, 0))
        worker.completed.connect(lambda *values: received.append(values))
        worker.run()
        self.assertEqual("005930", service.called[0])
        self.assertEqual("after_close_confirmed", received[0][3])
        self.assertEqual(1, len(received[0][1]))
        self.assertEqual([("005930", (SimpleNamespace(minute=datetime(2026, 9, 8, 15, 29)),),
            "after_close_confirmed", datetime(2026, 9, 8, 20, 0))], saved)


    def test_confirm_worker_normalizes_injected_instant_and_ignores_host_clock_for_cutoff(self) -> None:
        from datetime import timedelta, timezone
        from kiwoom_monitor.domain import time_basis
        expected = datetime(2026, 9, 8, 20, 0)
        providers = (expected, expected.replace(tzinfo=timezone(timedelta(hours=9))),
                     datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc))
        for supplied in providers:
            for host_offset in (-9, 9):
                with self.subTest(supplied=supplied, host_offset=host_offset):
                    accepted = SimpleNamespace(minute=datetime(2026, 9, 8, 19, 59))
                    current = SimpleNamespace(minute=expected)
                    future = SimpleNamespace(minute=datetime(2026, 9, 8, 20, 1))
                    called, saved, received = [], [], []
                    service = SimpleNamespace(load_today=lambda code, target:
                        called.append((code, target)) or (accepted, current, future))
                    clock = mock.Mock(return_value=supplied)
                    worker = ConfirmWorker(service, "005930", date(2026, 9, 8),
                        save_bars=lambda *values: saved.append(values), now_provider=clock)
                    worker.completed.connect(lambda *values: received.append(values))
                    # Model a host nine hours on either side without changing
                    # the supplied broker instant or the host's real clock.
                    with mock.patch.object(time_basis, "datetime", wraps=datetime) as host_datetime:
                        host_datetime.now.return_value = expected + timedelta(hours=host_offset)
                        worker.run()
                        host_datetime.now.assert_not_called()
                    self.assertEqual([mock.call(), mock.call()], clock.call_args_list)
                    self.assertEqual([("005930", datetime(2026, 9, 8))], called)
                    self.assertEqual([("005930", (accepted,), "after_close_confirmed", expected)], saved)
                    self.assertEqual([("005930", (accepted,), expected, "after_close_confirmed")], received)


    def test_confirm_worker_observes_delayed_result_without_changing_request_cut_or_session(self) -> None:
        started = datetime(2026, 9, 8, 19, 59, 58)
        observed = datetime(2026, 9, 8, 20, 1, 2, 654321)
        accepted = SimpleNamespace(minute=datetime(2026, 9, 8, 19, 58))
        current = SimpleNamespace(minute=datetime(2026, 9, 8, 19, 59))
        future = SimpleNamespace(minute=datetime(2026, 9, 8, 20, 0))
        clock, saved, received, requested = [started], [], [], []
        def load_recent(code, target):
            requested.append((code, target))
            clock[0] = observed  # The result becomes known after the initial cut.
            return accepted, current, future
        worker = ConfirmWorker(SimpleNamespace(load_recent=load_recent), "005930",
            save_bars=lambda *values: saved.append(values), now_provider=lambda: clock[0])
        worker.completed.connect(lambda *values: received.append(values))
        worker.run()
        self.assertEqual([("005930", started)], requested)
        self.assertEqual([("005930", (accepted,), "api_confirmed", observed)], saved)
        self.assertEqual([("005930", (accepted,), observed, "api_confirmed")], received)

    def test_confirm_worker_does_not_backdate_receipt_when_corrected_clock_moves_back(self) -> None:
        started = datetime(2026, 9, 8, 20, 0, 1, 234567)
        corrected = datetime(2026, 9, 8, 19, 59, 59)
        accepted = SimpleNamespace(minute=datetime(2026, 9, 8, 19, 59))
        current = SimpleNamespace(minute=datetime(2026, 9, 8, 20, 0))
        saved, received, requested = [], [], []
        def load_today(code, target):
            requested.append((code, target))
            return accepted, current
        clock = mock.Mock(side_effect=(started, corrected))
        worker = ConfirmWorker(SimpleNamespace(load_today=load_today), "005930",
            save_bars=lambda *values: saved.append(values), now_provider=clock)
        worker.completed.connect(lambda *values: received.append(values))
        worker.run()
        self.assertEqual([("005930", started)], requested)
        self.assertEqual([mock.call(), mock.call()], clock.call_args_list)
        self.assertEqual([("005930", (accepted,), "after_close_confirmed", started)], saved)
        self.assertEqual([("005930", (accepted,), started, "after_close_confirmed")], received)

    def test_market_index_worker_loads_both_markets_and_daily_rows(self) -> None:
        class Service:
            def load(self, market, target): return {market: (target,)}
            def load_daily(self, market, target): return (market, target)
        received = []; saved = []
        def save_history(minutes, daily):
            self.assertFalse(received)
            saved.append((minutes, daily))
        worker = MarketIndexBackfillWorker(Service(), date(2026, 9, 8), save_history=save_history)
        worker.completed.connect(received.append); worker.run()
        combined, daily = received[0]
        self.assertEqual({"kospi", "kosdaq"}, set(combined))
        self.assertEqual({"kospi", "kosdaq"}, set(daily))
        self.assertEqual([(combined, daily)], saved)


    def test_history_worker_skips_weekend_and_keeps_fills_when_cost_fails(self) -> None:
        class History:
            def __init__(self): self.days = []
            def load_day_batch(self, day):
                self.days.append(day)
                return SimpleNamespace(
                    fills=(str(day),),
                    context=AccountQueryContext(LEGACY_ACCOUNT_SCOPE, "legacy-unverified", 0, "legacy"),
                )
        class Costs:
            def load_period_batch(self, start, end): raise RuntimeError("cost unavailable")
        history = History(); received = []
        worker = HistoryWorker(history, Costs(), date(2026, 9, 4), date(2026, 9, 7))
        worker.completed.connect(lambda *values: received.append(values)); worker.run()
        self.assertEqual([date(2026, 9, 7), date(2026, 9, 4)], history.days)
        fills, costs, error = received[0][0]
        self.assertEqual(2, len(fills)); self.assertEqual((), costs); self.assertEqual("cost unavailable", error)

    def test_history_worker_skips_unsupported_cost_query_for_mock_account(self) -> None:
        mock_scope = AccountScope(
            "kiwoom", AccountEnvironment.MOCK, "11111111-1111-4111-8111-111111111111",
        )
        context = AccountQueryContext(mock_scope, "mock-profile", 1, "nas")

        class History:
            def load_day_batch(self, _day):
                return SimpleNamespace(fills=(), context=context)

        costs = SimpleNamespace(load_period_batch=mock.Mock())
        received = []
        worker = HistoryWorker(
            History(), costs, date(2026, 9, 21), date(2026, 9, 21),
            account_scope=mock_scope,
        )
        worker.completed.connect(lambda *values: received.append(values))

        worker.run()

        costs.load_period_batch.assert_not_called()
        self.assertEqual(((), (), ""), received[0][0])

    def test_backfill_worker_counts_success_and_failure(self) -> None:
        class Service:
            def load_today(self, code, target):
                return () if code == "EMPTY" else (SimpleNamespace(minute=datetime.combine(target.date(), datetime.min.time())),)
        completed = []; failed = []; started = []; worker = BackfillWorker(Service(), (("OK", date(2026, 9, 8)), ("EMPTY", date(2026, 9, 8))))
        worker.item_started.connect(lambda *values: started.append(values))
        worker.item_failed.connect(lambda *values: failed.append(values)); worker.completed.connect(lambda *values: completed.append(values)); worker.run()
        self.assertEqual((1, 1), completed[0]); self.assertEqual("EMPTY", failed[0][0])
        self.assertEqual((("OK", date(2026, 9, 8)), ("EMPTY", date(2026, 9, 8))), tuple(started))

    def test_backfill_worker_reports_incomplete_central_coverage_as_failure(self) -> None:
        class Service:
            def load_today_with_completion(self, _code, target):
                return ((SimpleNamespace(minute=datetime.combine(target.date(), datetime.min.time())),), False)

        items = []; totals = []
        worker = BackfillWorker(Service(), (("001210", date(2026, 9, 14)),))
        worker.item_completed.connect(lambda *values: items.append(values))
        worker.completed.connect(lambda *values: totals.append(values))
        worker.run()

        self.assertFalse(items[0][3])
        self.assertEqual((0, 1), totals[0])

    def test_daily_chart_worker_preserves_target(self) -> None:
        class Service:
            def load(self, code, target): return ((code, target.date()),)
        received = []; worker = DailyChartWorker(Service(), "005930", date(2026, 9, 8), "detached")
        worker.completed.connect(lambda *values: received.append(values)); worker.run()
        self.assertEqual(("005930", (("005930", date(2026, 9, 8)),), "detached"), received[0])

    def test_analysis_enrichment_worker_keeps_each_group_result_independent(self) -> None:
        class Service:
            def prepare(self, episode, rows, **kwargs):
                if episode.group_id == "bad":
                    raise RuntimeError("invalid data")
                return SimpleNamespace(analysis_revision_id="revision-1")
        tasks = (
            (SimpleNamespace(group_id="ok"), (("bar",),)),
            (SimpleNamespace(group_id="bad"), ()),
        )
        started = []; completed = []; failed = []; totals = []
        worker = AnalysisEnrichmentWorker(
            Service(), tasks, active_pack=object(), strategy_packs=(), result_mode="together",
        )
        worker.item_started.connect(started.append)
        worker.item_completed.connect(lambda *values: completed.append(values))
        worker.item_failed.connect(lambda *values: failed.append(values))
        worker.completed.connect(lambda *values: totals.append(values))

        worker.run()

        self.assertEqual(["ok", "bad"], started)
        self.assertEqual("ok", completed[0][0])
        self.assertEqual(("bad", "invalid data"), failed[0])
        self.assertEqual((1, 1), totals[0])


if __name__ == "__main__":
    unittest.main()

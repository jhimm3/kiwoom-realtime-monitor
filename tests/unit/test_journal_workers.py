from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from kiwoom_monitor.presentation.journal_workers import (
    BackfillWorker, ConfirmWorker, DailyChartWorker, HistoryWorker, MarketIndexBackfillWorker,
)


class JournalWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_confirm_worker_uses_after_close_day_and_filters_unfinished_bar(self) -> None:
        class Service:
            def load_today(self, code, target):
                self.called = (code, target)
                return (SimpleNamespace(minute=datetime(2026, 9, 8, 15, 29)),)
        service = Service(); received = []
        worker = ConfirmWorker(service, "005930", date(2026, 9, 8))
        worker.completed.connect(lambda *values: received.append(values)); worker.run()
        self.assertEqual("005930", service.called[0])
        self.assertEqual("after_close_confirmed", received[0][3])
        self.assertEqual(1, len(received[0][1]))

    def test_market_index_worker_loads_both_markets_and_daily_rows(self) -> None:
        class Service:
            def load(self, market, target): return {market: (target,)}
            def load_daily(self, market, target): return (market, target)
        received = []; worker = MarketIndexBackfillWorker(Service(), date(2026, 9, 8))
        worker.completed.connect(received.append); worker.run()
        combined, daily = received[0]
        self.assertEqual({"kospi", "kosdaq"}, set(combined))
        self.assertEqual({"kospi", "kosdaq"}, set(daily))

    def test_history_worker_skips_weekend_and_keeps_fills_when_cost_fails(self) -> None:
        class History:
            def __init__(self): self.days = []
            def load_day(self, day): self.days.append(day); return (str(day),)
        class Costs:
            def load_period(self, start, end): raise RuntimeError("cost unavailable")
        history = History(); received = []
        worker = HistoryWorker(history, Costs(), date(2026, 9, 4), date(2026, 9, 7))
        worker.completed.connect(lambda *values: received.append(values)); worker.run()
        self.assertEqual([date(2026, 9, 7), date(2026, 9, 4)], history.days)
        fills, costs, error = received[0][0]
        self.assertEqual(2, len(fills)); self.assertEqual((), costs); self.assertEqual("cost unavailable", error)

    def test_backfill_worker_counts_success_and_failure(self) -> None:
        class Service:
            def load_today(self, code, target):
                return () if code == "EMPTY" else (SimpleNamespace(minute=datetime.combine(target.date(), datetime.min.time())),)
        completed = []; failed = []; worker = BackfillWorker(Service(), (("OK", date(2026, 9, 8)), ("EMPTY", date(2026, 9, 8))))
        worker.item_failed.connect(lambda *values: failed.append(values)); worker.completed.connect(lambda *values: completed.append(values)); worker.run()
        self.assertEqual((1, 1), completed[0]); self.assertEqual("EMPTY", failed[0][0])

    def test_daily_chart_worker_preserves_target(self) -> None:
        class Service:
            def load(self, code, target): return ((code, target.date()),)
        received = []; worker = DailyChartWorker(Service(), "005930", date(2026, 9, 8), "detached")
        worker.completed.connect(lambda *values: received.append(values)); worker.run()
        self.assertEqual(("005930", (("005930", date(2026, 9, 8)),), "detached"), received[0])


if __name__ == "__main__":
    unittest.main()

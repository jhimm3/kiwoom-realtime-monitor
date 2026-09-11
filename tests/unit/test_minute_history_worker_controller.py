from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import (
    MinuteHistoryWorker,
)
from kiwoom_monitor.presentation.minute_history_worker_controller import (
    MinuteHistoryWorkerController,
)


class FakeMinuteHistoryWorker(MinuteHistoryWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class MinuteHistoryWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_data_status_failure_and_finish_context(self) -> None:
        worker = FakeMinuteHistoryWorker()
        controller = MinuteHistoryWorkerController(
            worker_factory=lambda _codes: worker
        )
        histories: list[tuple[str, object]] = []
        statuses: list[str] = []
        failures: list[str] = []
        finished: list[tuple[object, bool]] = []
        controller.history_received.connect(
            lambda code, bars: histories.append((code, bars))
        )
        controller.status_changed.connect(statuses.append)
        controller.failed.connect(failures.append)
        controller.finished.connect(
            lambda codes, forced: finished.append((codes, forced))
        )

        self.assertTrue(
            controller.start(
                ("005930",), followup_codes=("005930", "000660"), forced=True
            )
        )
        worker.history_received.emit("005930", ("bar",))
        worker.status_changed.emit("loading")
        worker.failed.emit("failed")
        worker.finished.emit()

        self.assertEqual([("005930", ("bar",))], histories)
        self.assertEqual(["loading"], statuses)
        self.assertEqual(["failed"], failures)
        self.assertEqual([(("005930", "000660"), True)], finished)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeMinuteHistoryWorker()
        calls: list[tuple[str, ...]] = []
        controller = MinuteHistoryWorkerController(
            worker_factory=lambda codes: calls.append(codes) or worker
        )
        self.assertTrue(controller.start(("005930",), followup_codes=(), forced=False))
        self.assertTrue(controller.start(("000660",), followup_codes=(), forced=False))
        self.assertEqual([("005930",)], calls)

    def test_missing_or_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = MinuteHistoryWorkerController()
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(("005930",), followup_codes=(), forced=False))

        controller.set_factory(lambda _codes: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(("005930",), followup_codes=(), forced=False))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()

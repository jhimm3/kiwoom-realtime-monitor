from __future__ import annotations

import unittest
from unittest.mock import Mock

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService

from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import (
    NxtEligibilityWorker,
)
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
        worker.finished.emit()

        self.assertEqual([("005930", True)], received)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)

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

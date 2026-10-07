from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.presentation.news_execution import (
    ai_progress_text,
    ai_start_block_reason,
    automatic_ai_run_allowed,
    dispose_finished_worker,
)


class _Worker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def wait(self) -> bool:
        self.calls.append("wait")
        return True

    def deleteLater(self) -> None:
        self.calls.append("deleteLater")


class NewsExecutionTests(unittest.TestCase):
    def test_finished_worker_keeps_wait_then_delete_order(self) -> None:
        worker = _Worker()

        dispose_finished_worker(worker)
        dispose_finished_worker(None)

        self.assertEqual(["wait", "deleteLater"], worker.calls)

    def test_ai_start_respects_empty_groups_and_daily_limit(self) -> None:
        settings = NewsAISettings("gemini", "key", "model", 3)

        self.assertEqual("empty", ai_start_block_reason(settings, 0, 0))
        self.assertEqual("daily_limit", ai_start_block_reason(settings, 3, 1))
        self.assertEqual("", ai_start_block_reason(settings, 2, 1))
        self.assertEqual("3/3", ai_progress_text(settings, 2))

    def test_automatic_run_accepts_server_key_and_manual_queue(self) -> None:
        server_settings = NewsAISettings("gemini", "", "model", 0, auto_analyze=True)
        manual_settings = NewsAISettings("gemini", "key", "model", 0, auto_analyze=False)

        self.assertTrue(automatic_ai_run_allowed(
            server_settings, manual_queue=False, central_client_available=True, used_requests=0,
        ))
        self.assertFalse(automatic_ai_run_allowed(
            server_settings, manual_queue=False, central_client_available=False, used_requests=0,
        ))
        self.assertTrue(automatic_ai_run_allowed(
            manual_settings, manual_queue=True, central_client_available=False, used_requests=0,
        ))


if __name__ == "__main__":
    unittest.main()

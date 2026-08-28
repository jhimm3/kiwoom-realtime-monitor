from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from kiwoom_monitor.news_process import _parent_is_alive


class NewsProcessTests(unittest.TestCase):
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
                time.sleep(0.2)
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
                time.sleep(0.15)
                command_path.write_text(
                    json.dumps({"request_id": 4, "action": "restore"}),
                    encoding="utf-8",
                )
                time.sleep(0.15)
                command_path.write_text(
                    json.dumps({"request_id": 5, "action": "shutdown"}),
                    encoding="utf-8",
                )
                self.assertEqual(0, process.wait(timeout=12))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

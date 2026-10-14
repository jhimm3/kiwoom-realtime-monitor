"""Small opt-in suites used to exercise subprocess result handling in the test runner."""
from __future__ import annotations

import atexit
import os
import json
import subprocess
import sys
import time
import unittest
import tempfile
from pathlib import Path


_MODE = os.environ.get("REGRESSION_FIXTURE_MODE", "pass")


def _record_process(role: str) -> None:
    (Path(os.environ["REGRESSION_FIXTURE_DIR"]) / f"{role}.json").write_text(
        json.dumps({"pid": os.getpid(), "role": role}), encoding="utf-8")


if __name__ == "__main__" and len(sys.argv) == 2:
    role = sys.argv[1]
    if role == "child":
        subprocess.Popen([sys.executable, __file__, "grandchild"], close_fds=True)
    _record_process(role)
    time.sleep(60)
    raise SystemExit(0)


if _MODE != "empty":
    class RegressionWorkerFixtureTests(unittest.TestCase):
        def test_selected_worker_outcome(self) -> None:
            if _MODE == "temp-env":
                (Path(os.environ["REGRESSION_FIXTURE_DIR"]) / "temp-env.json").write_text(json.dumps({
                    "TEMP": os.environ["TEMP"], "TMP": os.environ["TMP"],
                    "TMPDIR": os.environ["TMPDIR"], "default": tempfile.gettempdir(),
                }), encoding="utf-8")
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "source"
                    source.mkdir()
                    (source / "child.txt").write_text("fixture")
                    source.rename(Path(directory) / "published")
            if _MODE.startswith("tree-"):
                if "REGRESSION_FIXTURE_RUNNER_PID" in os.environ:
                    (Path(os.environ["REGRESSION_FIXTURE_DIR"]) / "runner.json").write_text(
                        json.dumps({"pid": int(os.environ["REGRESSION_FIXTURE_RUNNER_PID"])}), encoding="utf-8")
                _record_process("worker")
                subprocess.Popen([sys.executable, __file__, "child"], close_fds=True)
                if _MODE == "tree-leak":
                    # Allow the parent test to retain handles before this worker exits.
                    time.sleep(1)
                    return
                time.sleep(60)
            if _MODE == "skip":
                self.skipTest("fixture skip")
            if _MODE == "fail":
                self.fail("fixture failure")
            if _MODE == "crash-after-result":
                atexit.register(os._exit, 23)
            if _MODE == "timeout":
                time.sleep(20)
            if _MODE == "expected-failure":
                self.fail("fixture expected failure")


if _MODE == "expected-failure":
    RegressionWorkerFixtureTests.test_selected_worker_outcome = unittest.expectedFailure(
        RegressionWorkerFixtureTests.test_selected_worker_outcome,
    )
elif _MODE == "unexpected-success":
    RegressionWorkerFixtureTests.test_selected_worker_outcome = unittest.expectedFailure(
        lambda self: None,
    )

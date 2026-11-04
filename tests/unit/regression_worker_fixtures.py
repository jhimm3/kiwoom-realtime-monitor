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
            if _MODE == "registry-native":
                import importlib.util
                import uuid
                import winreg
                from scripts.regression_registry import join_from_environment
                scope = join_from_environment()
                organization = "KiwoomMonitorRegressionProbe-" + str(uuid.uuid4())
                location = "Software\\" + organization + "\\Native"
                for branch, value in (("User", "user-canary"), ("Unrelated", "unrelated-canary")):
                    with winreg.CreateKeyEx(scope.root, branch + "\\" + location) as key:
                        winreg.SetValueEx(key, "probe", 0, winreg.REG_SZ, value)
                child = r'''
import json, os, runpy, sys
from pathlib import Path
scope = runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
# -I/-S ignore PYTHONPATH/sitecustomize; only the installed native Qt runtime is added.
sys.path.append(sys.argv[1])
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
settings = QSettings(sys.argv[2], 'Native')
assert settings.format() == QSettings.Format.NativeFormat
assert settings.value('probe') == sys.argv[3]
settings.setValue('probe', sys.argv[4]); settings.sync()
assert settings.status() == QSettings.Status.NoError
app = QApplication([]); widget = QWidget(); widget.show(); app.processEvents(); widget.close()
print(json.dumps({'native_format': True, 'user_scope': True, 'HKLM_fallback': 'outside_scope',
                  'qt_application': True, 'scope': scope.description['key']}))
'''
                runtime = str(Path(importlib.util.find_spec("PySide6").origin).parent.parent)
                results = []
                user = "user-canary"
                for index, flags in enumerate(([], ["-I"], ["-S"])):
                    next_user = "user-" + str(index)
                    result = subprocess.run([sys.executable, *flags, "-c", child, runtime,
                        organization, user, next_user],
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    results.append({"flags": flags, **json.loads(result.stdout.strip())})
                    for branch, expected in (("User", next_user), ("Unrelated", "unrelated-canary")):
                        with winreg.OpenKey(scope.root, branch + "\\" + location) as key:
                            self.assertEqual(expected, winreg.QueryValueEx(key, "probe")[0])
                    user = next_user
                (Path(os.environ["REGRESSION_FIXTURE_DIR"]) / "registry-native.json").write_text(
                    json.dumps(results), encoding="utf-8")
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

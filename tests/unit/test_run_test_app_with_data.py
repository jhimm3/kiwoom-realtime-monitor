from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RunTestAppWithDataTests(unittest.TestCase):
    def test_runner_exposes_src_then_repository_to_current_and_child_processes(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        runner = repository / "scripts" / "run_test_app_with_data.py"
        code = (
            "import json,os,runpy,sys;"
            f"runpy.run_path({str(runner)!r},run_name='test_runner');"
            "import kiwoom_monitor.research_process,scripts.run_research;"
            "print(json.dumps({'sys_path':sys.path[:2],"
            "'pythonpath':os.environ['PYTHONPATH'].split(os.pathsep)[:2],"
            "'research_script':scripts.run_research.__file__}))"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(tempfile.gettempdir()) / "runner-sentinel")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", code], cwd=directory, env=environment,
                capture_output=True, text=True, timeout=30, check=True,
            )

        values = json.loads(result.stdout.strip().splitlines()[-1])
        expected = [str(repository / "src"), str(repository)]
        self.assertEqual(expected, values["sys_path"])
        self.assertEqual(expected, values["pythonpath"])
        self.assertEqual(
            repository / "scripts" / "run_research.py",
            Path(values["research_script"]).resolve(),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.probe_historical_backfill import _write_news_heartbeat  # noqa: E402


class HistoricalNewsHeartbeatTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows file sharing regression")
    def test_search_state_lock_and_failed_final_report_do_not_abort_collector(self) -> None:
        source = (Path(__file__).resolve().parents[2] / "scripts/run_historical_news_collector.ps1").read_text(encoding="utf-8-sig")
        state_function = source[source.index("function Write-State"):source.index("function Write-Log")]
        final_report = source[source.index("    # Status publication is auxiliary;"):source.index('    Write-Log "collector finished')]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            script = folder / "check.ps1"
            script.write_text("""param([string]$TestRoot)
$ErrorActionPreference='Stop'
$projectRoot=$TestRoot; $Database='db.sqlite3'; $Jobs=100
$stateFile=Join-Path $TestRoot 'state.json'; $logFile='test'; $diagnosticLog='test'
$script:messages=@()
function Write-Log([string]$Message) { $script:messages += $Message }
function MockReporter { Write-Error 'report failed' -ErrorAction Continue; $global:LASTEXITCODE=7 }
""" + state_function + """
Write-State 'running' 1
$locked=[IO.File]::Open($stateFile,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try { Write-State 'running' 2 } finally { $locked.Dispose() }
if ((Get-Content $stateFile -Raw | ConvertFrom-Json).finished_this_run -ne 1) { throw 'locked state was corrupted' }
if (-not ($script:messages -match 'state update failed after retry')) { throw 'lock failure not recorded' }
$python='MockReporter'; $completed=3; $NasProject=$TestRoot
""" + final_report + """
$state=Get-Content $stateFile -Raw | ConvertFrom-Json
if ($state.status -ne 'complete' -or $state.finished_this_run -ne 3) { throw 'report error aborted completion' }
if (Get-ChildItem $TestRoot -Filter '*.tmp*') { throw 'temporary files leaked' }
Write-Output 'OK'
""", encoding="utf-8-sig")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(folder)], capture_output=True, text=True, errors="replace", timeout=20)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("OK", result.stdout)

    def test_concurrent_writes_leave_valid_heartbeat_and_no_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news-job-heartbeat.json"
            job = SimpleNamespace(code="005930", target_date="2026-09-23", query_text="삼성전자")
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(
                    lambda page: _write_news_heartbeat(path, job, "search_page", page=page),
                    range(80),
                ))
            heartbeat = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["code"], "005930")
            self.assertEqual(heartbeat["phase"], "search_page")
            self.assertFalse(list(path.parent.glob(".news-job-heartbeat.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()

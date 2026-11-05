from __future__ import annotations

import os
import ast
import inspect
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.research_dialog import (
    ResearchDialog,
    _research_process_environment,
    format_market_regime_summary,
    format_research_result_rows,
)


class ResearchDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_comparison_rows_keep_sealed_values_as_na(self) -> None:
        rows = format_research_result_rows({
            "kind": "rank_comparison",
            "comparison": {"fold_comparisons": [{
                "name": "final", "status": "SEALED",
                "baseline_net_pnl_won": None, "variant_net_pnl_won": None,
                "net_pnl_delta_won": None, "avoided_loss_won": None,
                "missed_profit_won": None,
            }]},
        })
        self.assertEqual(("final", "SEALED", "N/A", "N/A", "N/A", "N/A", "N/A"), rows[0])

    def test_single_run_uses_variant_column_without_inventing_comparison(self) -> None:
        rows = format_research_result_rows({
            "kind": "single_run",
            "report": {"fold_reports": [{
                "name": "train", "status": "ELIGIBLE", "net_realized_pnl_won": 1234,
            }]},
        })
        self.assertEqual("1,234원", rows[0][3])
        self.assertEqual("", rows[0][2])

    def test_evidence_request_preview_and_result_keep_partial_holding_visible(self) -> None:
        import hashlib
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "micro.json"
            source.write_bytes(b"{}")
            request = root / "request.json"
            request.write_text(json.dumps({
                "mode": "micro_experiment", "dataset": source.name, "dataset_sha256": hashlib.sha256(b"{}").hexdigest(),
                "policy": {}, "database": "research.sqlite3", "runs_dir": "runs",
            }), encoding="utf-8")
            dialog = ResearchDialog(root)
            try:
                dialog.activate_startup()
                dialog._request_path.setText(str(request))
                self.assertTrue(dialog._preview_request())
                self.assertIn("micro.json", dialog._summary.text())
                self.assertTrue(dialog._run.isEnabled())
                dialog._apply_result({"status": "ok", "kind": "micro_experiment",
                                      "result": {"status": "OPEN", "position_quantity": 2, "reason": "partial"}})
                self.assertEqual("보유 수량", dialog._table.horizontalHeaderItem(2).text())
                self.assertEqual(("OPEN", "2"), tuple(dialog._table.item(0, index).text() for index in (1, 2)))
            finally:
                dialog.stop()
                dialog.deleteLater()

    def test_market_regime_summary_keeps_unknown_reason_visible(self) -> None:
        text = format_market_regime_summary({
            "value": {
                "market_type": "UNKNOWN",
                "reasons": ["mature_breakout_sample_insufficient"],
            },
        })
        self.assertEqual(
            "시장 UNKNOWN (mature_breakout_sample_insufficient)", text,
        )

    def test_limited_search_keeps_drawdown_sample_and_reason_visible(self) -> None:
        rows = format_research_result_rows({
            "kind": "limited_search",
            "candidate_cards": [{
                "ordinal": 0, "variant": "baseline", "status": "INELIGIBLE",
                "net_pnl_won": 100, "max_drawdown_won": 30,
                "closed_trade_count": 2, "active_day_count": 1,
                "reasons": ["sample_insufficient"],
            }],
        })
        self.assertEqual(
            ("#1 baseline", "INELIGIBLE", "30원", "100원", "2", "1", "sample_insufficient"),
            rows[0],
        )

    def test_pending_automatic_resume_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            dialog.activate_startup()
            dialog._continuous.setChecked(True)
            dialog._resume_timer.start()
            dialog._cancel.setEnabled(True)
            dialog._request_cancel()
            self.assertFalse(dialog._resume_timer.isActive())
            self.assertTrue(dialog._cancel_path.is_file())
            self.assertFalse(dialog._cancel.isEnabled())
            dialog.stop()

    def test_unchecking_continuous_mode_stops_pending_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            dialog.activate_startup()
            dialog._continuous.setChecked(True)
            dialog._resume_timer.start()
            dialog._continuous.setChecked(False)
            self.assertFalse(dialog._resume_timer.isActive())
            dialog.stop()

    def test_single_run_passes_the_explicit_feature_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            selected_environment = {'PATH': 'fixture-runtime', 'TEMP': 'fixture-temp'}
            try:
                with patch.object(dialog, '_preview_request', return_value=True), \
                     patch('kiwoom_monitor.presentation.research_dialog.os', SimpleNamespace(environ={'fixture': 'source'})), \
                     patch('kiwoom_monitor.presentation.research_dialog._research_process_environment', return_value=selected_environment), \
                     patch.object(dialog._manager, 'start') as launch:
                    dialog._start()
                self.assertEqual(1, launch.call_count)
                self.assertIs(selected_environment, launch.call_args.kwargs['env'])
                self.assertTrue(launch.call_args.kwargs['below_normal_priority'])
            finally:
                dialog.stop()
                dialog.deleteLater()


    def test_stop_blocks_already_scheduled_startup_restore_and_preview(self):
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            dialog._request_path.setText(str(Path(directory) / "request.json"))
            dialog._campaign_selection = {"database": str(Path(directory) / "research.sqlite3"),
                                          "runs_dir": directory, "campaign_id": "fixture"}
            repository = Mock()
            with patch.object(dialog, "_campaign_repository", return_value=repository), \
                 patch("kiwoom_monitor.presentation.research_dialog.load_research_process_request") as load_request, \
                 patch.object(dialog, "_launch_campaign_worker") as launch:
                dialog.activate_startup()
                dialog.stop()
                repository.reset_mock()  # Only callbacks after the completed owner stop are observed.
                self.app.processEvents()
                load_request.assert_not_called()
                repository.load_campaign.assert_not_called()
                launch.assert_not_called()
            dialog.deleteLater()
            self.app.processEvents()


class ResearchProcessEnvironmentTests(unittest.TestCase):
    def test_environment_removes_unused_credentials_and_preserves_runtime_and_isolation(self):
        retained = {
            'PATH': 'runtime-path', 'PYTHONPATH': 'source-path', 'PYTHONHOME': 'runtime-home',
            'PYTHONUTF8': '1', 'LANG': 'ko_KR.UTF-8', 'LC_ALL': 'ko_KR.UTF-8',
            'SystemRoot': 'system-root', 'HOME': 'home', 'APPDATA': 'app-data',
            'TEMP': 'owned-temp', 'TMP': 'owned-temp', 'TMPDIR': 'owned-temp',
            'KIWOOM_DESKTOP_DATA_DIR': 'explicit-desktop-root',
            'KIWOOM_REGRESSION_REGISTRY_SCOPE': 'fixture-owned-registry-scope',
            'MONITOR_SERVER_CUSTOM_RUNTIME_SETTING': 'preserved-unknown-server-setting',
            'CUSTOM_RUNTIME_SETTING': 'preserved',
        }
        excluded = {
            'KIWOOM_APP_KEY', 'KIWOOM_SECRET_KEY', 'KIWOOM_MOCK_APP_KEY', 'KIWOOM_MOCK_SECRET_KEY',
            'ACCOUNT_IDENTITY_HMAC_KEY', 'MOCK_ACCOUNT_REF', 'MOCK_EXECUTION_RUN_ID',
            'CREDENTIAL_TRUSTED_PROXIES', 'NAVER_NEWS_CLIENT_ID', 'NAVER_NEWS_CLIENT_SECRET',
            'DART_API_KEY', 'OPENAI_API_KEY', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY',
            'MONITOR_SERVER_ACCESS_TOKEN', 'KIWOOM_SERVER_ACCESS_TOKEN', 'MONITOR_SERVER_QUERY_ACCESS_TOKEN',
            'MONITOR_SERVER_RESEARCH_INPUT_GRANTS_JSON', 'MONITOR_SERVER_RESEARCH_RESULT_GRANTS_JSON',
            'MONITOR_SERVER_NEWS_BODY_GRANTS_JSON', 'KIWOOM_SERVER_DATABASE_URL', 'KIWOOM_SERVER_SECRET_DIR',
            'monitor_server_access_token',
        }
        original = {**retained, **{key: 'synthetic-credential' for key in excluded}}
        unchanged = dict(original)
        filtered = _research_process_environment(MappingProxyType(original))
        self.assertEqual(retained, filtered)
        self.assertEqual(unchanged, original)
        self.assertIsNot(original, filtered)
        empty = {}
        self.assertEqual({}, _research_process_environment(empty))
        self.assertEqual({}, empty)

    def test_all_seven_research_launches_select_the_feature_environment(self):
        tree = ast.parse(Path(inspect.getsourcefile(ResearchDialog)).read_text(encoding='utf-8'))
        launches = {}
        for owner in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method in (node for node in owner.body if isinstance(node, ast.FunctionDef)):
                for call in ast.walk(method):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == 'start' and isinstance(call.func.value, ast.Attribute)
                            and call.func.value.attr == '_manager'):
                        launches[owner.name + '.' + method.name] = call
        expected = {
            'ResearchDialog._start_campaign_registration', 'ResearchDialog._launch_campaign_worker',
            'ResearchDialog._start', 'DevelopmentValidationDialog._launch', 'FinalHoldoutDialog._launch',
            'FinalHoldoutDialog._expose_to_development', 'IndependentComparisonDialog._start',
        }
        self.assertEqual(expected, set(launches))
        selected_environment = ast.parse('_research_process_environment(os.environ)', mode='eval').body
        for name, call in launches.items():
            with self.subTest(caller=name):
                environment = [keyword.value for keyword in call.keywords if keyword.arg == 'env']
                self.assertEqual(1, len(environment))
                self.assertEqual(ast.dump(selected_environment), ast.dump(environment[0]))


if __name__ == "__main__":
    unittest.main()

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

    def test_research_results_show_breakout_pair_and_partial_risk_valuation(self) -> None:
        comparison = {
            "status": "ok", "kind": "breakout_filter_comparison",
            "section12": {"family": "breakout_study/v1", "variants": ["B0", "B1", "B2", "B3"],
                          "execution_scope": "paper", "source_kind": "historical_reconstruction",
                          "reconstructed_market_proxy": {"scope": "daishin_creon:StockChart:U001", "sha256": "a" * 64,
                                                         "covered_minutes": 2, "regular_minutes": 380}},
            "runs": [{"variant": "B2", "run_id": "run-b2", "candidate_count": 1},
                     {"variant": "B3", "run_id": "run-b3", "candidate_count": 0}],
            "pairs": [{"baseline": "B2", "variant": "B3", "comparison": {"fold_comparisons": [
                {"name": "validation", "status": "ELIGIBLE"},
            ]}}],
        }
        comparisons = format_research_result_rows(comparison)
        self.assertEqual("B2 → B3 · validation", comparisons[0][0])
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            try:
                dialog._apply_result(comparison)
                self.assertEqual("변형·구간", dialog._table.horizontalHeaderItem(0).text())
                self.assertIn("B2/B3 복원 시장비율 daishin_creon:StockChart:U001 · 공급자 원단위 상대값", dialog._summary.text())
                self.assertIn("시장 근거 2/380분", dialog._summary.text())
                self.assertIn("Paper 체결 모의 · 계좌 주문 없음", dialog._summary.text())
                self.assertIn("복원 시장비율 SHA256 " + "a" * 64, dialog._summary.toolTip())
                self.assertIn("B3 실행 ID run-b3 · 후보 0", dialog._summary.toolTip())
            finally:
                dialog.stop()
                dialog.deleteLater()
        risk = format_research_result_rows({"kind": "account_risk_experiment", "result": {
            "cash_won": 70, "reserved_cash_won": 10, "valuation": {"status": "PARTIAL", "equity_won": None,
            "maximum_drawdown_won": None, "known_drawdown_lower_bound_won": 20},
        }})
        self.assertEqual(("PARTIAL", "70원", "10원", "N/A", "N/A", "20원"), risk[0][1:])

    def test_section12_request_and_completed_result_keep_frozen_identity_and_scope(self) -> None:
        import hashlib
        import json

        from kiwoom_monitor.research_process import execute_process_request, load_research_process_request
        from research_test_support import research_request_document, write_empty_dataset

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_empty_dataset(root)
            path = root / "dataset/manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["captured_range"] = {"start": "2026-09-12T00:00:00+00:00", "end": "2026-09-12T01:00:00+00:00"}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            references = b'{"version":"strategy-reference/v1","listings":[],"calendar":[]}'
            digest = hashlib.sha256(references).hexdigest()
            (root / "references.json").write_bytes(references)
            document = research_request_document()
            document.update(mode="single_run", family="breakout_study/v1", session_profile="krx-regular/v1",
                            strategy_references={"path": "references.json", "sha256": digest})
            document["strategy"] = {"capital_won": 1000, "policy": {
                "variant": "B0", "quantity": 1, "exit_at_kst": "15:18", "theme_id": None,
                "theme_role": None, "exit_on_dependency_loss": False,
            }}
            document["execution"].update(version="completed_close_then_next_open/v1", same_bar_path_version="no_intrabar_fill/v1")
            request_path = root / "request.json"
            dialog = ResearchDialog(root)
            try:
                dialog.activate_startup()
                for shadow, scope, label in ((False, "paper", "Paper 체결 모의"), (True, "shadow_only", "Shadow 재생")):
                    with self.subTest(scope=scope):
                        request_path.write_text(json.dumps({**document, "shadow_only": shadow}), encoding="utf-8")
                        dialog._request_path.setText(str(request_path))
                        self.assertTrue(dialog._preview_request())
                        self.assertIn("breakout_study/v1", dialog._summary.text())
                        self.assertIn("B0", dialog._summary.text())
                        self.assertIn(label, dialog._summary.text())
                        self.assertIn(digest, dialog._summary.toolTip())
                        result = execute_process_request(load_research_process_request(request_path))
                        context = result["section12"]
                        self.assertEqual(("breakout_study/v1", "B0", scope, "empty", manifest["revision_ids_hash"], digest), (
                            context["family"], context["variant"], context["execution_scope"], context["input_dataset_id"],
                            context["input_revision_ids_hash"], context["strategy_reference_sha256"],
                        ))
                        dialog._apply_result(result)
                        self.assertIn(label, dialog._summary.text())
                        self.assertIn("기록된 관측 가용시각", dialog._summary.text())
                        self.assertIn("2026-09-12 09:00:00 KST", dialog._summary.text())
                        self.assertIn("자료 품질 INELIGIBLE", dialog._summary.text())
                        self.assertIn(manifest["revision_ids_hash"], dialog._summary.toolTip())
                        self.assertIn(result["run_id"], dialog._summary.toolTip())
                        self.assertEqual("미완료·거절", dialog._table.horizontalHeaderItem(4).text())
                document.update(family="panic_rebound/v1", strategy={
                    "capital_won": 1000, "quantity": 1, "news_proofs": [],
                    "panic_allow_unresolved_news": True,
                    "policy": {"variant": "P0", "lookback_minutes": 3, "decline_bps": 1000,
                               "minimum_turnover_won": 3_000_000, "max_input_age_seconds": 10,
                               "rebound_target_bps": 500, "max_hold_minutes": 5},
                })
                request_path.write_text(json.dumps(document), encoding="utf-8")
                self.assertTrue(dialog._preview_request())
                self.assertIn("뉴스 미확인 표본 허용", dialog._summary.text())
                result = execute_process_request(load_research_process_request(request_path))
                dialog._apply_result(result)
                self.assertIn("뉴스 미확인 표본 허용", dialog._summary.text())
            finally:
                dialog.stop()
                dialog.deleteLater()

    def test_section12_rows_do_not_turn_sealed_or_shadow_counts_into_fills(self) -> None:
        report = {"fold_reports": [
            {"name": "train", "role": "TRAIN", "status": "INELIGIBLE", "submitted_order_count": 3,
             "filled_order_count": 1, "censored_order_count": 2, "rejected_order_count": 0,
             "no_trade_reasons": {"candidate_universe_stale": 2}, "reasons": ["sample_insufficient"]},
            {"name": "final", "role": "OOS", "status": "SEALED", "submitted_order_count": 0},
        ]}
        paper = {"kind": "single_run", "section12": {"execution_scope": "paper"}, "report": report}
        rows = format_research_result_rows(paper)
        self.assertEqual(("train · TRAIN", "INELIGIBLE", "N/A", "3/1"), rows[0][:4])
        self.assertIn("미완료 2 · 거절 0 · 미지원 N/A", rows[0][4])
        self.assertEqual(("candidate_universe_stale 2", "sample_insufficient"), rows[0][5:])
        self.assertEqual(("N/A",) * 4, rows[1][2:6])
        shadow = {**paper, "section12": {"execution_scope": "shadow_only"}}
        rows = format_research_result_rows(shadow)
        self.assertEqual(("N/A", "N/A", "N/A", "candidate_universe_stale 2"), rows[0][2:6])

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
                self.assertIn("micro_execution/v1", dialog._summary.text())
                self.assertTrue(dialog._run.isEnabled())
                dialog._apply_result({"status": "ok", "kind": "micro_experiment",
                                      "result": {"status": "OPEN", "position_quantity": 2, "reason": "partial",
                                                 "variant": "VI_FLAT_DOWN_RISE", "pending_order": {"order_id": "fixture-pending"},
                                                 "broker_issue": "missing_terminal_receipt"}})
                self.assertEqual("보유 수량", dialog._table.horizontalHeaderItem(2).text())
                self.assertEqual(("OPEN", "2"), tuple(dialog._table.item(0, index).text() for index in (1, 2)))
                self.assertEqual(("micro_execution/v1 · VI_FLAT_DOWN_RISE", "fixture-pending", "missing_terminal_receipt"),
                                 tuple(dialog._table.item(0, index).text() for index in (0, 3, 4)))
            finally:
                dialog.stop()
                dialog.deleteLater()

    def test_dd_native_setup_displays_frozen_evidence_without_order_or_holding_columns(self):
        result = {
            "status": "ok", "kind": "daily_decline_experiment", "run_id": "dd-run",
            "section12": {"family": "daily_decline/v1", "source_kind": "frozen_native_daily_observations",
                          "execution_scope": "native_setup_assessment", "frozen_dataset_id": "daily-input",
                          "frozen_manifest_sha256": "frozen-hash", "revision_ids_hash": "revision-hash"},
            "result": {"symbol": "005930", "setup": {"status": "QUALIFIED", "reason": "decline_confirmed",
                       "drawdown_bps": 2500, "relative_decline_bps": 1618, "volatility_bps": 909}},
            "order_authorized": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            try:
                dialog._apply_result(result)
                self.assertIn("DD 셋업 평가", dialog._summary.text())
                self.assertIn("계좌 주문 없음", dialog._summary.text())
                self.assertIn("동결 자료 ID daily-input", dialog._summary.toolTip())
                self.assertEqual(("절대 낙폭", "시장 대비 낙폭", "변동성"),
                                 tuple(dialog._table.horizontalHeaderItem(i).text() for i in (2, 3, 4)))
                self.assertEqual(("QUALIFIED", "2,500bp", "1,618bp", "909bp", "동결 일봉", "decline_confirmed"),
                                 tuple(dialog._table.item(0, i).text() for i in range(1, 7)))
            finally:
                dialog.stop()
                dialog.deleteLater()

    def test_frozen_dd_micro_show_declared_sources_costs_and_unknowns_as_research(self):
        provenance = {"source_kind": "historical_reconstruction", "source_ref": "case-17", "source_version": "frozen/v2"}
        cases = (
            ("daily_decline_experiment", "frozen_daily_execution_research", {
                "setup": {"status": "QUALIFIED"}, "summary": {"owned_quantity": 2},
                "state": {"fills": [{"fee_won": 7}, {"fee_won": 5}]},
                "evidence_provenance": {"execution": provenance}}, "상세 체결 비용 12원"),
            ("micro_experiment", "frozen_micro_execution_research", {
                "status": "UNKNOWN", "position_quantity": 0, "evidence_provenance": provenance,
                "historical_kind_halt": {"state": "CONFIRMED_FULL_DAY_HALT", "scope": "retrospective_only"},
                "input_assessment": {"missing_inputs": ["cross_stream_order"], "native_confirmation": False}},
                "미확인: cross_stream_order"),
            ("micro_experiment", "frozen_micro_execution_research", {
                "status": "COMPLETED", "position_quantity": 0, "evidence_provenance": provenance,
                "total_cost_won": 44, "realized_pnl_won": -1044,
                "default_cost_model": {"source": "agreed_research_cost/v1"}},
                "상세 체결 비용 44원 · 실현손익 -1,044원 · 기본 추정 비용 포함"),
        )
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            try:
                for kind, scope, trace, detail in cases:
                    result = {"status": "ok", "kind": kind, "result": trace, "order_authorized": False,
                        "section12": {"source_kind": "frozen_research_evidence", "execution_scope": scope}}
                    row, = format_research_result_rows(result)
                    self.assertEqual("동결 복원", row[5])
                    self.assertIn("case-17 · frozen/v2", row[4])
                    self.assertIn(detail, row[4])
                    dialog._apply_result(result)
                    self.assertIn("동결 연구 근거 · 운영 확인 아님", dialog._summary.text())
                    self.assertIn("계좌 주문 없음", dialog._summary.text())
                    self.assertEqual("동결 복원", dialog._table.item(0, 5).text())
                    self.assertIn('"source_version": "frozen/v2"', dialog._table.item(0, 4).toolTip())
                    if "historical_kind_halt" in trace:
                        self.assertIn("KIND 종일 거래정지 확인", row[4])
                        self.assertIn('"scope": "retrospective_only"', dialog._table.item(0, 4).toolTip())
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
            'MONITOR_SERVER_NEWS_BODY_GRANTS_JSON', 'MONITOR_SERVER_OBSERVATION_ARCHIVE_GRANTS_JSON',
            'MONITOR_SERVER_PROGRAM_ARCHIVE_TARGET_FILE', 'MONITOR_SERVER_PROGRAM_ARCHIVE_SOURCE_STREAM',
            'KIWOOM_SERVER_DATABASE_URL', 'KIWOOM_SERVER_SECRET_DIR',
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
        selected_environment = ast.parse('_research_process_environment(os.environ, self._state_dir)', mode='eval').body
        for name, call in launches.items():
            with self.subTest(caller=name):
                environment = [keyword.value for keyword in call.keywords if keyword.arg == 'env']
                self.assertEqual(1, len(environment))
                self.assertEqual(ast.dump(selected_environment), ast.dump(environment[0]))


if __name__ == "__main__":
    unittest.main()

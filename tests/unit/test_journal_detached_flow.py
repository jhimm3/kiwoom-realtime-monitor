from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.journal_process import JournalWindow
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes


class JournalDetachedFlowTests(unittest.TestCase):
    def test_multiday_episode_daily_chart_uses_latest_available_day(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 9, 7, 9, 10), 1, 100, "", "KRX"),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 9, 9, 10, 20), 1, 110, "", "KRX"),
        )
        episode = group_trade_episodes(fills)[0]
        requested: list[tuple[str, date, str]] = []
        item = SimpleNamespace(data=lambda _role: episode)
        chart = SimpleNamespace(
            clear_daily_rows=lambda: None, set_fills=lambda _fills: None,
            set_rows=lambda _rows: None, focus_time=lambda _time: None,
        )
        owner = SimpleNamespace(
            _summary_table=SimpleNamespace(currentRow=lambda: 0, item=lambda _row, _column: item),
            _active_episode=None,
            _load_active_review=lambda: None,
            _history_chart=chart,
            _history_daily_chart=SimpleNamespace(clear_daily_rows=lambda: None, set_fills=lambda _fills: None),
            _history_interval=SimpleNamespace(currentText=lambda: "일봉"),
            _dual_history_chart=SimpleNamespace(isChecked=lambda: False),
            _load_selected_chart_rows=lambda: (),
            _apply_history_chart_state=lambda *_args: None,
            _render_fills=lambda _fills: None,
            _show_trade_analysis=lambda *_args: None,
            _sync_detached_charts=lambda: None,
            _request_incomplete_trade_days=lambda _fills: None,
            _request_previous_chart_day=lambda *_args: None,
            _start_daily_chart=lambda *args: requested.append(args),
        )

        JournalWindow._show_selected_summary(owner)

        self.assertEqual([("005930", date.today(), "history")], requested)

    def test_backfill_result_refreshes_selected_chart_only_after_atomic_save(self) -> None:
        day = date(2026, 9, 8)
        bars = (SimpleNamespace(minute=datetime(2026, 9, 8, 9, 1)),)
        calls: list[object] = []
        owner = SimpleNamespace(
            _repo=SimpleNamespace(
                save_bar_backfill_result=lambda code, saved_day, saved_bars, checked_at: calls.append(
                    (code, saved_day, saved_bars, isinstance(checked_at, datetime))
                ) or True,
            ),
            _selected_history_code="005930",
            _selected_history_range=(day, day),
            _refresh_selected_history_after_bars=lambda code, analyze_any_active=False: calls.append(
                ("refresh", code, analyze_any_active)
            ),
        )

        JournalWindow._backfill_received(owner, "005930", day, bars)

        self.assertEqual(("005930", day, bars, True), calls[0])
        self.assertEqual(("refresh", "005930", True), calls[1])

    def test_history_result_uses_one_repository_write_before_refresh(self) -> None:
        fills = (SimpleNamespace(order_no="1"),)
        costs = (SimpleNamespace(total_cost=200),)
        calls: list[tuple[str, object, object]] = []
        statuses: list[str] = []
        owner = SimpleNamespace(
            _repo=SimpleNamespace(
                upsert_history_sync=lambda saved_fills, saved_costs: calls.append(
                    ("save", saved_fills, saved_costs)
                ),
            ),
            _journal_settings=SimpleNamespace(setValue=lambda *_args: None),
            _status=SimpleNamespace(setText=statuses.append),
            reload_history=lambda: calls.append(("reload", None, None)),
        )

        JournalWindow._history_received(
            owner,
            (fills, costs, ""),
            date(2026, 9, 8),
            date(2026, 9, 8),
        )

        self.assertEqual([("save", fills, costs), ("reload", None, None)], calls)
        self.assertEqual(["조회 완료 · 체결 1건 · 실제비용 1묶음"], statuses)

    def test_open_active_news_uses_shared_command_channel_contract(self) -> None:
        documents: list[dict[str, object]] = []
        messages: list[str] = []
        episode = SimpleNamespace(
            group_id="group-1",
            summary=SimpleNamespace(
                stock_code="005930",
                stock_name="삼성전자",
                trade_date=date(2026, 9, 8),
            ),
        )
        owner = SimpleNamespace(
            _active_episode=episode,
            _journal_news_channel=SimpleNamespace(send=lambda document: documents.append(document)),
            _review_saved=SimpleNamespace(setText=messages.append),
        )

        JournalWindow._open_active_news(owner)

        self.assertEqual(
            [{
                "code": "005930",
                "name": "삼성전자",
                "group_id": "group-1",
                "trade_date": "2026-09-08",
            }],
            documents,
        )
        self.assertEqual(["뉴스창에서 대표 기사를 선택해 매매일지에 추가할 수 있습니다."], messages)

    def test_market_index_result_uses_one_repository_write_then_refreshes_charts(self) -> None:
        minute_rows = {("kospi", datetime(2026, 9, 8, 9, 1)): (1.0, 2.0, 0.5, 1.5, None)}
        daily_rows = {"kospi": (("2026-09-08T00:00", 1, 2, 1, 2, 10, 20.0),)}
        saved: list[tuple[object, object]] = []
        statuses: list[str] = []
        refreshed: list[bool] = []
        owner = SimpleNamespace(
            _monitor_db="monitor.sqlite3",
            _market_index_active_day=date(2026, 9, 8),
            _market_index_requested_days=set(),
            _status=SimpleNamespace(setText=statuses.append),
            _sync_detached_charts=lambda: refreshed.append(True),
        )

        with patch("kiwoom_monitor.journal_process.MinuteBarRepository") as repository_type:
            repository_type.return_value.replace_market_index_history.side_effect = (
                lambda minutes, daily: saved.append((minutes, daily))
            )
            JournalWindow._market_index_backfill_completed(owner, (minute_rows, daily_rows))

        self.assertEqual([(minute_rows, daily_rows)], saved)
        self.assertEqual({date(2026, 9, 8)}, owner._market_index_requested_days)
        self.assertEqual(["코스피·코스닥 분봉·일봉 보완 · 1개"], statuses)
        self.assertEqual([True], refreshed)

    def test_running_market_index_worker_keeps_only_latest_other_day(self) -> None:
        class RunningWorker:
            @staticmethod
            def isRunning() -> bool:
                return True

        active_day = date(2026, 9, 8)
        owner = SimpleNamespace(
            _market_index_requested_days=set(),
            _market_index_worker=RunningWorker(),
            _market_index_active_day=active_day,
            _pending_market_index_day=None,
        )

        JournalWindow._start_market_index_backfill(owner, active_day)
        JournalWindow._start_market_index_backfill(owner, date(2026, 9, 9))
        JournalWindow._start_market_index_backfill(owner, date(2026, 9, 10))

        self.assertEqual(date(2026, 9, 10), owner._pending_market_index_day)

    def test_finished_market_index_worker_starts_pending_day_once(self) -> None:
        pending = date(2026, 9, 9)
        started: list[date] = []
        owner = SimpleNamespace(
            _market_index_worker=object(),
            _market_index_active_day=date(2026, 9, 8),
            _pending_market_index_day=pending,
            _start_market_index_backfill=started.append,
        )

        JournalWindow._market_index_backfill_finished(owner)

        self.assertIsNone(owner._market_index_worker)
        self.assertIsNone(owner._market_index_active_day)
        self.assertIsNone(owner._pending_market_index_day)
        self.assertEqual([pending], started)

    def test_market_index_save_failure_does_not_mark_day_as_completed(self) -> None:
        target = date(2026, 9, 8)
        statuses: list[str] = []
        owner = SimpleNamespace(
            _monitor_db="monitor.sqlite3",
            _market_index_active_day=target,
            _market_index_requested_days=set(),
            _status=SimpleNamespace(setText=statuses.append),
            _sync_detached_charts=lambda: self.fail("failed save must not refresh charts"),
        )

        with patch("kiwoom_monitor.journal_process.MinuteBarRepository") as repository_type:
            repository_type.return_value.replace_market_index_history.side_effect = sqlite3.Error("locked")
            JournalWindow._market_index_backfill_completed(owner, ({}, {}))

        self.assertNotIn(target, owner._market_index_requested_days)
        self.assertEqual(["시장지수 저장 실패 · locked"], statuses)

    def test_running_daily_worker_keeps_only_latest_pending_request(self) -> None:
        class RunningWorker:
            @staticmethod
            def isRunning() -> bool:
                return True

        owner = SimpleNamespace(
            _repo=SimpleNamespace(
                import_monitor_daily_bars=lambda *_args: 0,
                load_daily_bars=lambda *_args: (),
            ),
            _monitor_db="monitor.sqlite3",
            _daily_chart_worker=RunningWorker(),
            _pending_daily_chart=None,
        )

        JournalWindow._start_daily_chart(owner, "A", date(2026, 9, 8), "history")
        JournalWindow._start_daily_chart(owner, "B", date(2026, 9, 9), "detached:B")

        self.assertEqual(("B", date(2026, 9, 9), "detached:B"), owner._pending_daily_chart)

    def test_finished_daily_worker_starts_pending_request_once(self) -> None:
        started: list[tuple[object, ...]] = []
        owner = SimpleNamespace(
            _daily_chart_worker=object(),
            _pending_daily_chart=("B", date(2026, 9, 9), "detached:B"),
            _start_daily_chart=lambda *request: started.append(request),
        )

        JournalWindow._daily_chart_finished(owner)

        self.assertIsNone(owner._daily_chart_worker)
        self.assertIsNone(owner._pending_daily_chart)
        self.assertEqual([("B", date(2026, 9, 9), "detached:B")], started)

    def test_daily_rows_are_applied_to_history_with_analysis_only_for_fresh_result(self) -> None:
        daily_rows = (("2026-09-08T00:00", 1, 2, 1, 2, 10, 0.1),)
        minute_rows = (("2026-09-08T09:00", 1, 2, 1, 2, 10, 0.1),)
        history_values: list[object] = []
        dual_values: list[object] = []
        analyses: list[tuple[object, object]] = []
        active_episode = SimpleNamespace(summary=SimpleNamespace(stock_code="A"))
        owner = SimpleNamespace(
            _selected_history_code="A",
            _live_code="B",
            _active_episode=active_episode,
            _history_chart=SimpleNamespace(set_daily_rows=history_values.append),
            _history_daily_chart=SimpleNamespace(set_daily_rows=dual_values.append),
            _chart=SimpleNamespace(set_daily_rows=lambda _rows: None),
            _focus_selected_history=lambda: None,
            _sync_detached_charts=lambda: None,
            _load_selected_chart_rows=lambda: minute_rows,
            _show_trade_analysis=lambda episode, rows: analyses.append((episode, rows)),
        )

        JournalWindow._apply_daily_chart_rows(owner, "A", daily_rows, "history", refresh_analysis=False)
        JournalWindow._apply_daily_chart_rows(owner, "A", daily_rows, "history", refresh_analysis=True)

        self.assertEqual([daily_rows, daily_rows], history_values)
        self.assertEqual([daily_rows, daily_rows], dual_values)
        self.assertEqual([(active_episode, minute_rows)], analyses)

    def test_selected_history_refresh_reuses_one_bar_query_for_chart_and_analysis(self) -> None:
        rows = (("2026-09-08T09:00:00", 1, 2, 1, 2, 10, 0.1),)
        loaded: list[bool] = []
        chart_values: list[object] = []
        analyses: list[tuple[object, object]] = []
        active_episode = SimpleNamespace(summary=SimpleNamespace(stock_code="005930"))
        owner = SimpleNamespace(
            _selected_history_day=datetime(2026, 9, 8).date(),
            _selected_history_code="005930",
            _active_episode=active_episode,
            _history_chart=SimpleNamespace(set_rows=lambda value: chart_values.append(value)),
            _load_selected_chart_rows=lambda: loaded.append(True) or rows,
            _focus_selected_history=lambda: None,
            _show_trade_analysis=lambda episode, value: analyses.append((episode, value)),
        )

        JournalWindow._refresh_selected_history_after_bars(owner, "005930")

        self.assertEqual([True], loaded)
        self.assertEqual([rows], chart_values)
        self.assertEqual([(active_episode, rows)], analyses)

    def test_stale_empty_comparison_request_can_be_retried(self) -> None:
        code = "005930"
        started = datetime(2026, 9, 8, 9, 0)
        ended = datetime(2026, 9, 9, 15, 0)
        day = ended.date()
        daily_requests: list[tuple[str, object, str]] = []
        minute_requests: list[tuple[str, object, bool]] = []
        owner = SimpleNamespace(
            _detached_chart_window=SimpleNamespace(selected_stock_codes=lambda: (code,)),
            _active_episode=SimpleNamespace(started_at=started, ended_at=ended),
            _compare_stock_requested={(code, day)},
            _worker=None,
            _repo=SimpleNamespace(load_chart_bars_range=lambda *_args: ()),
            _status=SimpleNamespace(setText=lambda _text: None),
            _sync_detached_charts=lambda: None,
            _start_daily_chart=lambda *args: daily_requests.append(args),
            _start_bar_confirmation=lambda stock_code, target_day, include_previous=False: minute_requests.append(
                (stock_code, target_day, include_previous)
            ),
        )

        JournalWindow._detached_source_changed(owner)

        self.assertEqual([(code, date.today(), f"detached:{code}")], daily_requests)
        self.assertEqual([(code, day, True)], minute_requests)
        self.assertIn((code, day), owner._compare_stock_requested)


if __name__ == "__main__":
    unittest.main()

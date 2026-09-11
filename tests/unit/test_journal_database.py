from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    MarketDatasetKind,
    ObservationOrigin,
)
from kiwoom_monitor.application.trade_journal_summary import TradeReview
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository, TradeEntrySnapshot
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    MarketDataMetadataRepository,
)
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.application.personal_trade_rules import StructuredTradeRule


class JournalRepositoryTests(unittest.TestCase):
    def test_loads_market_index_bars_from_monitor_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            monitor = Path(directory) / "monitor.sqlite3"; Database(monitor).initialize()
            MinuteBarRepository(monitor).upsert_market_index_minutes({
                ("kosdaq", datetime(2026, 8, 31, 9, 1)): (900.0, 902.0, 899.0, 901.0, 300.0),
            })
            rows = JournalRepository(Path(directory) / "journal.sqlite3").load_monitor_market_index_bars(
                monitor, "kosdaq", date(2026, 8, 31), date(2026, 8, 31),
            )
            self.assertEqual(1, len(rows)); self.assertEqual(901.0, rows[0][4])

    def test_entry_snapshot_round_trip_and_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            captured = TradeEntrySnapshot(
                "2026-08-31:005930:order-1:fill-1", "order-1", "005930", "삼성전자", "매수",
                datetime(2026, 8, 31, 9, 4, 12), 70_000, 3, "KRX", 2,
                12.5, 44.2, ("반도체",), {"반도체": 1}, 0.7,
                market_state={"visible_stock_count": 20}, capture_state="realtime_core",
            )
            repository.save_entry_snapshot(captured)
            loaded = repository.load_entry_snapshots(
                "005930", datetime(2026, 8, 31), datetime(2026, 9, 1),
            )
            self.assertEqual(1, len(loaded))
            self.assertEqual(captured.execution_key, loaded[0].execution_key)
            self.assertEqual(captured.themes, loaded[0].themes)
            self.assertEqual(captured.theme_ranks, loaded[0].theme_ranks)
            self.assertEqual(captured.market_state, loaded[0].market_state)
            metadata = MarketDataMetadataRepository(Path(directory) / "journal.sqlite3")
            rank = metadata.load(
                MarketDatasetKind.ENTRY_CONTEXT,
                "005930:rank",
                captured.execution_key,
            )
            news = metadata.load(
                MarketDatasetKind.ENTRY_CONTEXT,
                "005930:news",
                captured.execution_key,
            )
            trade_value = metadata.load(
                MarketDatasetKind.ENTRY_CONTEXT,
                "005930:trade_value_1m",
                captured.execution_key,
            )
            assert rank is not None and news is not None and trade_value is not None
            self.assertEqual(ObservationOrigin.REALTIME, rank.origin)
            self.assertEqual(DataCompleteness.COMPLETE, rank.completeness)
            self.assertEqual(DataCompleteness.MISSING, news.completeness)
            self.assertEqual(DataValueKind.UNKNOWN, trade_value.value_kind)

    def test_provisional_snapshot_does_not_overwrite_backfilled_investor_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            captured = TradeEntrySnapshot(
                "2026-08-31:005930:order-1:fill-1", "order-1", "005930", "삼성전자", "매수",
                datetime(2026, 8, 31, 9, 4, 12), 70_000, 3, "KRX",
                investor_flow={
                    "available": True,
                    "foreign_net_buy_quantity": 1_200,
                    "institution_net_buy_quantity": -300,
                    "status": "confirmed",
                    "backfilled": True,
                },
            )
            repository.save_entry_snapshot(captured)
            repository.save_entry_snapshot(replace(captured, investor_flow={
                "foreign_net_buy_quantity": 0,
                "institution_net_buy_quantity": -200,
                "status": "confirmed",
            }))
            loaded = repository.load_entry_snapshots(
                "005930", datetime(2026, 8, 31), datetime(2026, 9, 1),
            )
            self.assertEqual(1_200, loaded[0].investor_flow["foreign_net_buy_quantity"])
            self.assertEqual(-300, loaded[0].investor_flow["institution_net_buy_quantity"])
            metadata = MarketDataMetadataRepository(Path(directory) / "journal.sqlite3").load(
                MarketDatasetKind.ENTRY_CONTEXT,
                "005930:investor_flow",
                captured.execution_key,
            )
            assert metadata is not None
            self.assertEqual(ObservationOrigin.BACKFILLED, metadata.origin)

    def test_news_added_after_execution_is_marked_as_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            captured = TradeEntrySnapshot(
                "2026-08-31:005930:order-1:fill-1", "order-1", "005930", "삼성전자", "매수",
                datetime(2026, 8, 31, 9, 4, 12), 70_000, 3, "KRX",
            )
            repository.save_entry_snapshot(captured)

            repository.save_snapshot_news_backfill(
                captured.execution_key, ({"title": "장중 기사", "published_at": "2026-08-31T09:00:00"},),
            )

            loaded = repository.load_entry_snapshots(
                "005930", datetime(2026, 8, 31), datetime(2026, 9, 1),
            )
            self.assertTrue(loaded[0].news[0]["backfilled"])
            metadata = MarketDataMetadataRepository(Path(directory) / "journal.sqlite3").load(
                MarketDatasetKind.ENTRY_CONTEXT,
                "005930:news",
                captured.execution_key,
            )
            assert metadata is not None
            self.assertEqual(ObservationOrigin.BACKFILLED, metadata.origin)
            self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
            self.assertGreater(metadata.available_at, metadata.effective_at)

    def test_entry_snapshot_and_metadata_are_rolled_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            captured = TradeEntrySnapshot(
                "execution-rollback", "order-1", "005930", "삼성전자", "매수",
                datetime(2026, 8, 31, 9, 4, 12), 70_000, 3, "KRX", rank=2,
            )
            with patch(
                "kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository.upsert_market_data_metadata",
                side_effect=RuntimeError("metadata failed"),
            ), self.assertRaisesRegex(RuntimeError, "metadata failed"):
                repository.save_entry_snapshot(captured)

            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute(
                    "SELECT count(*) FROM trade_entry_snapshots"
                ).fetchone()[0]
            self.assertEqual(0, count)

    def test_daily_bar_with_missing_trade_value_does_not_close_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_daily_bars("005930", (
                ("2026-08-29T00:00", 100, 110, 90, 105, 1000, None),
            ))
            rows = repository.load_daily_bars("005930", date(2026, 8, 29))
            self.assertEqual(1, len(rows))
            self.assertEqual(0.0, rows[0][6])

    def test_loads_prior_entry_only_for_stocks_traded_in_selected_period(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_fills((
                TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 27, 15, 20), 10, 100),
                TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 28, 9, 10), 10, 105),
                TradeFill("3", "000660", "SK하이닉스", "매수", datetime(2026, 8, 27, 10), 1, 200),
                TradeFill("4", "000660", "SK하이닉스", "매도", datetime(2026, 8, 27, 11), 1, 210),
            ))
            fills = repository.load_fills_with_entry_context(
                datetime(2026, 8, 28), datetime(2026, 8, 29),
            )
            self.assertEqual(("005930", "005930"), tuple(fill.stock_code for fill in fills))
            self.assertEqual(("매도", "매수"), tuple(fill.side for fill in fills))

    def test_trade_setup_and_manual_override_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            automatic = TradeSetupClassification("돌파", 84, ("직전 고점 돌파",))
            repository.save_trade_setup("group-1", automatic, "눌림")
            loaded = JournalRepository(path).load_trade_setup("group-1")
            self.assertIsNotNone(loaded)
            self.assertEqual("돌파", loaded[0].setup_type)
            self.assertEqual("눌림", loaded[1])

    def test_trade_setup_cycle_overrides_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            repository.save_trade_setup_cycle_override("group-1", 0, "돌파")
            repository.save_trade_setup_cycle_override("group-1", 1, "과대낙폭")
            self.assertEqual({0: "돌파", 1: "과대낙폭"}, JournalRepository(path).load_trade_setup_cycle_overrides("group-1"))
            repository.save_trade_setup_cycle_override("group-1", 0, "")
            self.assertEqual({1: "과대낙폭"}, repository.load_trade_setup_cycle_overrides("group-1"))

    def test_trade_summary_support_data_can_be_loaded_in_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.save_trade_setup(
                "group-1", TradeSetupClassification("돌파", 84, ("직전 고점 돌파",)), "눌림",
            )
            repository.save_trade_setup_cycle_override("group-1", 1, "과대낙폭")
            repository.save_review(
                TradeReview("group-1", "돌파 확인", "추격 진입", "돌파", "보통", "복기 완료")
            )

            setups = repository.load_trade_setups(("group-1", "missing"))
            overrides = repository.load_trade_setup_cycle_overrides_many(("group-1", "missing"))
            reviews = repository.load_reviews(("group-1", "missing"))

            self.assertEqual("돌파", setups["group-1"][0].setup_type)
            self.assertEqual("눌림", setups["group-1"][1])
            self.assertEqual({1: "과대낙폭"}, overrides["group-1"])
            self.assertEqual("복기 완료", reviews["group-1"].status)
            self.assertNotIn("missing", setups)
            self.assertNotIn("missing", overrides)
            self.assertNotIn("missing", reviews)

    def test_trade_analysis_setup_and_legacy_override_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            original = TradeSetupClassification("기타", 1, ())
            repository.save_trade_setup("group-1", original, "돌파")
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TRIGGER reject_cycle_override BEFORE INSERT ON trade_setup_cycle_overrides "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
            connection.commit()
            connection.close()

            with self.assertRaises(sqlite3.IntegrityError):
                repository.save_trade_analysis_setup(
                    "group-1", TradeSetupClassification("돌파", 80, ("근거",)), (0, "돌파")
                )

            loaded = repository.load_trade_setup("group-1")
            self.assertIsNotNone(loaded)
            self.assertEqual("기타", loaded[0].setup_type)
            self.assertEqual("돌파", loaded[1])
            self.assertEqual({}, repository.load_trade_setup_cycle_overrides("group-1"))

    def test_extracted_personal_rules_survive_without_original_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            JournalRepository(path).save_personal_rules(("급등 추격매수 금지", "손절 3%"))
            self.assertEqual(
                ("급등 추격매수 금지", "손절 3%"),
                JournalRepository(path).load_personal_rules(),
            )

    def test_structured_personal_rules_preserve_lesson_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            rule = StructuredTradeRule(3, "테마주 돌파매매", "4. 매수 원칙", "", "core_rule", "테마주 돌파매매", "분할 매수한다")
            JournalRepository(path).save_structured_personal_rules((rule,))
            self.assertEqual((rule,), JournalRepository(path).load_structured_personal_rules())

    def test_trade_group_assignments_can_be_saved_and_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.assign_group(("fill-1", "fill-2"), "manual:one", datetime(2026, 8, 29, 10))
            self.assertEqual({"fill-1": "manual:one", "fill-2": "manual:one"}, repository.load_group_overrides())
            repository.clear_group_assignments(("fill-1",))
            self.assertEqual({"fill-2": "manual:one"}, repository.load_group_overrides())

    def test_trade_review_is_persisted_by_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            repository.save_review(TradeReview("group-1", "돌파 확인", "추격 진입은 아쉬움", "돌파,추격", "아쉬움", "복기 완료"))
            loaded = JournalRepository(path).load_review("group-1")
            self.assertEqual("돌파 확인", loaded.reason)
            self.assertEqual("추격 진입은 아쉬움", loaded.review)
            self.assertEqual("복기 완료", loaded.status)

    def test_actual_trade_cost_is_persisted_by_fill_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_trade_costs((DailyTradeCost(
                datetime(2026, 8, 26).date(), datetime(2026, 8, 28).date(),
                "005930", "매도", 100_000, 99_800, 20, 180, 200,
            ),))
            values = repository.load_trade_costs(datetime(2026, 8, 26), datetime(2026, 8, 27))
            self.assertEqual(1, len(values))
            self.assertEqual(200, values[0].total_cost)

    def test_history_sync_saves_fills_and_costs_in_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            fill = TradeFill(
                "1", "005930", "삼성전자", "매도", datetime(2026, 9, 8, 10, 0), 1, 100_000,
            )
            cost = DailyTradeCost(
                date(2026, 9, 8), date(2026, 9, 10), "005930", "매도",
                100_000, 99_800, 20, 180, 200,
            )

            repository.upsert_history_sync((fill,), (cost,), now=datetime(2026, 9, 8, 20, 5))

            self.assertEqual((fill,), repository.load_fills(datetime(2026, 9, 8), datetime(2026, 9, 9)))
            self.assertEqual((cost,), repository.load_trade_costs(datetime(2026, 9, 8), datetime(2026, 9, 9)))

    def test_history_sync_rolls_back_fills_when_cost_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            fill = TradeFill(
                "1", "005930", "삼성전자", "매도", datetime(2026, 9, 8, 10, 0), 1, 100_000,
            )
            cost = DailyTradeCost(
                date(2026, 9, 8), date(2026, 9, 10), "005930", "매도",
                100_000, 99_800, 20, 180, 200,
            )
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute(
                        "CREATE TRIGGER reject_trade_cost BEFORE INSERT ON daily_trade_costs "
                        "BEGIN SELECT RAISE(ABORT, 'cost failed'); END"
                    )

            with self.assertRaisesRegex(sqlite3.Error, "cost failed"):
                repository.upsert_history_sync((fill,), (cost,))

            with closing(sqlite3.connect(path)) as connection:
                fill_count = connection.execute("SELECT count(*) FROM trade_fills").fetchone()[0]
                cost_count = connection.execute("SELECT count(*) FROM daily_trade_costs").fetchone()[0]
            self.assertEqual((0, 0), (fill_count, cost_count))

    def test_repairs_legacy_cost_code_that_lost_internal_letter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            repository.upsert_fills((TradeFill(
                "1", "0039P0", "매드업", "매도", datetime(2026, 8, 6, 10, 26), 38, 9_280,
            ),))
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO daily_trade_costs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("2026-08-06", "2026-08-10", "00390", "매도", 719_590, 718_053, 100, 1_437, 1_537, "2026-08-30T00:00:00"),
                    )
            JournalRepository(path)
            values = JournalRepository(path).load_trade_costs(datetime(2026, 8, 6), datetime(2026, 8, 7))
            self.assertEqual("0039P0", values[0].stock_code)

    def test_live_copy_does_not_replace_api_confirmed_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor = root / "monitor.sqlite3"
            with closing(sqlite3.connect(monitor)) as connection:
                with connection:
                    connection.execute(
                        "CREATE TABLE minute_bars (trade_date TEXT, stock_code TEXT, minute TEXT, "
                        "open_price INTEGER, high_price INTEGER, low_price INTEGER, close_price INTEGER, "
                        "volume INTEGER, trade_value_eok REAL)"
                    )
                    connection.execute(
                        "INSERT INTO minute_bars VALUES "
                        "('2026-08-29','005930','2026-08-29T10:00',90,110,80,100,10,1.0)"
                    )
            repository = JournalRepository(root / "journal.sqlite3")
            checked = MinuteOhlcv(datetime(2026, 8, 29, 10, 0), 100, 120, 90, 110, 20, 2.0)
            repository.upsert_bars("005930", (checked,), "api_confirmed", datetime(2026, 8, 29, 10, 1))
            repository.import_live_bars(monitor, "005930", datetime(2026, 8, 29, 10, 2))
            row = repository.load_bars("005930", datetime(2026, 8, 29, 10, 2))[0]
            self.assertEqual(120, row[2])
            self.assertEqual("api_confirmed", row[7])

            metadata = MarketDataMetadataRepository(root / "journal.sqlite3").load(
                MarketDatasetKind.MINUTE_BAR, "005930", "2026-08-29T10:00"
            )
            assert metadata is not None
            self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
            self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
            self.assertEqual(DataValueKind.ESTIMATED, metadata.value_kind)

    def test_journal_partial_and_daily_bars_store_observation_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            minute = datetime(2026, 9, 10, 10, 0)
            repository.upsert_bars(
                "005930",
                (MinuteOhlcv(minute, 100, 110, 90, 105, 20, 1.0),),
                "realtime_partial",
            )
            repository.upsert_daily_bars(
                "005930",
                (("2026-09-09T00:00", 90, 110, 80, 105, 200, 3.5),),
                now=datetime(2026, 9, 10, 10, 1),
            )

            metadata = MarketDataMetadataRepository(path)
            partial = metadata.load(
                MarketDatasetKind.MINUTE_BAR, "005930", "2026-09-10T10:00"
            )
            daily = metadata.load(MarketDatasetKind.DAILY_BAR, "005930", "2026-09-09")

            assert partial is not None and daily is not None
            self.assertEqual(DataCompleteness.PARTIAL, partial.completeness)
            self.assertEqual(DataValueKind.UNKNOWN, partial.value_kind)
            self.assertEqual(ObservationOrigin.REALTIME, partial.origin)
            self.assertEqual(DataCompleteness.COMPLETE, daily.completeness)
            self.assertEqual(ObservationOrigin.QUERY, daily.origin)

    def test_chart_bars_include_target_and_previous_available_day_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_bars("005930", (
                MinuteOhlcv(datetime(2026, 8, 27, 15), 90, 100, 80, 95, 10),
                MinuteOhlcv(datetime(2026, 8, 28, 15), 100, 110, 90, 105, 10),
                MinuteOhlcv(datetime(2026, 8, 31, 10), 110, 120, 100, 115, 20),
            ), "api_confirmed")
            rows = repository.load_chart_bars("005930", datetime(2026, 8, 31))
            self.assertEqual(["2026-08-28", "2026-08-31"], sorted({str(row[0])[:10] for row in rows}))

    def test_chart_range_includes_every_day_between_buy_and_sell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_bars("010170", (
                MinuteOhlcv(datetime(2026, 8, 25, 15), 90, 100, 80, 95, 10),
                MinuteOhlcv(datetime(2026, 8, 26, 9, 5), 100, 110, 90, 105, 10),
                MinuteOhlcv(datetime(2026, 8, 27, 9, 3), 105, 115, 100, 112, 20),
            ), "api_confirmed")
            rows = repository.load_chart_bars_range("010170", date(2026, 8, 26), date(2026, 8, 27))
            self.assertEqual(
                ["2026-08-25", "2026-08-26", "2026-08-27"],
                sorted({str(row[0])[:10] for row in rows}),
            )

    def test_bar_backfill_state_tracks_missing_partial_failure_and_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            day = date(2026, 8, 28)
            self.assertEqual("미조회", repository.bar_backfill_state("005930", day).state)

            partial = MinuteOhlcv(datetime(2026, 8, 28, 10), 100, 110, 90, 105, 20)
            repository.upsert_bars("005930", (partial,), "realtime_partial")
            self.assertEqual("일부", repository.bar_backfill_state("005930", day).state)

            repository.mark_bar_backfill("005930", day, "실패", "일시 오류")
            failed = repository.bar_backfill_state("005930", day)
            self.assertEqual("실패", failed.state)
            self.assertEqual("일시 오류", failed.message)

            confirmed = MinuteOhlcv(datetime(2026, 8, 28, 10, 1), 105, 115, 100, 110, 30)
            repository.upsert_bars("005930", (confirmed,), "after_close_confirmed")
            repository.mark_bar_backfill("005930", day, "확정")
            state = repository.bar_backfill_state("005930", day)
            self.assertEqual("확정", state.state)
            self.assertEqual(2, state.bar_count)

    def test_bar_backfill_states_matches_single_item_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            partial_day = date(2026, 8, 27)
            failed_day = date(2026, 8, 28)
            repository.upsert_bars(
                "005930",
                (MinuteOhlcv(datetime(2026, 8, 27, 10), 100, 110, 90, 105, 20),),
                "realtime_partial",
            )
            repository.mark_bar_backfill("000660", failed_day, "실패", "일시 오류")

            states = repository.bar_backfill_states((
                ("005930", partial_day),
                ("000660", failed_day),
                ("035420", failed_day),
            ))

            self.assertEqual("일부", states[("005930", partial_day)].state)
            self.assertEqual("실패", states[("000660", failed_day)].state)
            self.assertEqual("일시 오류", states[("000660", failed_day)].message)
            self.assertEqual("미조회", states[("035420", failed_day)].state)

    def test_bar_backfill_result_saves_only_target_day_and_confirms_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            day = date(2026, 8, 28)
            target = MinuteOhlcv(datetime(2026, 8, 28, 10), 100, 110, 90, 105, 20)
            other = MinuteOhlcv(datetime(2026, 8, 27, 10), 90, 100, 80, 95, 10)

            saved = repository.save_bar_backfill_result(
                "005930", day, (other, target), datetime(2026, 8, 28, 20, 5),
            )

            state = repository.bar_backfill_state("005930", day)
            self.assertTrue(saved)
            self.assertEqual("확정", state.state)
            self.assertEqual(1, state.bar_count)

    def test_empty_bar_backfill_result_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            day = date(2026, 8, 28)

            saved = repository.save_bar_backfill_result("005930", day, ())

            state = repository.bar_backfill_state("005930", day)
            self.assertFalse(saved)
            self.assertEqual("실패", state.state)
            self.assertEqual("해당 거래일의 분봉이 반환되지 않았습니다.", state.message)

    def test_bar_backfill_result_rolls_back_bars_and_state_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            day = date(2026, 8, 28)
            bar = MinuteOhlcv(datetime(2026, 8, 28, 10), 100, 110, 90, 105, 20)

            with patch(
                "kiwoom_monitor.infrastructure.persistence.journal_bar_repository.upsert_market_data_metadata",
                side_effect=RuntimeError("metadata failed"),
            ), self.assertRaisesRegex(RuntimeError, "metadata failed"):
                repository.save_bar_backfill_result("005930", day, (bar,))

            self.assertEqual("미조회", repository.bar_backfill_state("005930", day).state)

    def test_backfill_candidates_are_based_on_saved_trade_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_fills((
                TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 27, 10), 1, 70_000, "", ""),
                TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 27, 11), 1, 71_000, "", ""),
                TradeFill("3", "000660", "SK하이닉스", "매수", datetime(2026, 8, 28, 10), 1, 300_000, "", ""),
            ))
            self.assertEqual(
                (("005930", date(2026, 8, 27)),),
                repository.bar_backfill_candidates(date(2026, 8, 28)),
            )

    def test_daily_chart_bars_are_cached_and_loaded_before_selected_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            repository.upsert_daily_bars("005930", (
                ("2026-08-27T00:00", 100, 110, 90, 105, 200, 3.5, "daily_confirmed"),
                ("2026-08-28T00:00", 105, 115, 100, 112, 300, 5.0, "daily_confirmed"),
            ))
            rows = repository.load_daily_bars("005930", date(2026, 8, 27))
            self.assertEqual(1, len(rows))
            self.assertEqual((100, 110, 90, 105, 200, 3.5), rows[0][1:7])


if __name__ == "__main__":
    unittest.main()

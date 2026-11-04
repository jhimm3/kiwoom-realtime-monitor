from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from kiwoom_monitor.application.pc_market_session import PcMarketSession
from kiwoom_monitor.domain.realtime_gap import GapRange, RealtimeGap
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    MarketIndexTick, ProgramTradeTick, TradeTick,
)


class PcMarketSessionTests(unittest.TestCase):
    def test_live_observation_state_preserves_non_none_fields_and_gap_scope(self):
        session = PcMarketSession()
        now = datetime.fromisoformat("2026-10-16T10:15:10+09:00")
        session.accept_tick(TradeTick(
            "A", 100, None, None, 10, None, "101510",
            execution_strength=123, session_type="NXT",
        ), now)
        session.accept_tick(TradeTick(
            "A", None, None, None, None, None, "101511",
            execution_strength=0, session_type="",
        ), now + timedelta(seconds=1))
        pressure = session.execution_pressure_at_entry("A", now + timedelta(seconds=1))
        self.assertEqual(0, pressure["execution_strength"])
        self.assertEqual("NXT", pressure["session_type"])
        self.assertEqual((10, 0, 100.), (pressure["buy_execution_volume"],
            pressure["sell_execution_volume"], pressure["buy_share_percent"]))
        self.assertEqual({"program_trade": {"available": False, "backfill_pending": True}},
            session.investor_flow_at_entry("missing"))
        session.accept_program_trade(ProgramTradeTick("A", "101510", 40, -2, 1200, -3), now)
        session.accept_program_trade(ProgramTradeTick("B", "101509", -30, 1, -600, 5), now)
        original = session.investor_flow_at_entry("A")
        original["program_trade"]["net_buy_quantity"] = 999
        self.assertEqual(40, session.investor_flow_at_entry("A")["program_trade"]["net_buy_quantity"])
        session.mark_observation_gap(RealtimeGap(1, (
            GapRange("program_trade", "A", "KRX", now, now),
        ), False), affected_trade_codes=("A",), subscribed_codes=("C",), observed_at=now)
        self.assertEqual({"program_trade": {"available": False, "source": "subscriber-gap", "completeness": "partial"}},
            session.investor_flow_at_entry("A"))
        self.assertEqual(-30, session.investor_flow_at_entry("B")["program_trade"]["net_buy_quantity"])
        self.assertFalse(session.execution_pressure_at_entry("A", now)["available"])
        self.assertTrue(session.execution_pressure_at_entry("C", now).get("available", True))
        session.mark_observation_gap(RealtimeGap(2), affected_trade_codes=(),
            subscribed_codes=("A", "C"), observed_at=now)
        for code in ("A", "B"):
            self.assertEqual({"program_trade": {"available": False, "source": "subscriber-gap", "completeness": "unconfirmed"}},
                session.investor_flow_at_entry(code))
        self.assertFalse(session.execution_pressure_at_entry("C", now)["available"])
        self.assertTrue(session.execution_pressure_at_entry("B", now).get("available", True))
        self.assertTrue(session.execution_pressure_at_entry("A", now + timedelta(seconds=60)).get("available", True))
        session.accept_program_trade(ProgramTradeTick("A", None, 0, None, 0, None), now)
        self.assertEqual({"program_trade": {
            "available": True, "source": "kiwoom_realtime_0w", "observed_at": "2026-10-16T10:15:10+09:00",
            "trade_time": None, "market": "KRX", "net_buy_quantity": 0, "net_buy_quantity_change": None,
            "net_buy_amount_million_won": 0, "net_buy_amount_change_million_won": None,
        }}, session.investor_flow_at_entry("A"))

    def test_market_observed_at_uses_supplied_clock_and_keeps_original_trade_time(self):
        session = PcMarketSession()
        now = datetime.fromisoformat("2026-10-16T10:15:10+09:00")
        session.accept_market_index(MarketIndexTick(
            "KOSPI", "153059", 2500., 1.5, 1200, 300, 200, 10,
        ), now)
        first = session.market_state_at_entry()
        self.assertEqual({
            "KOSPI": {"index": 2500., "change_rate": 1.5,
                "trade_value_million_won": 1200, "trade_value_eok": 12.,
                "advancing_count": 300, "declining_count": 200, "flat_count": 10},
            "source": "kiwoom_realtime_0J_0U", "observed_at": "2026-10-16T10:15:10+09:00",
            "trade_time": "153059",
        }, first)
        first["KOSPI"]["index"] = 999
        session.accept_market_index(MarketIndexTick(
            "KOSPI", "153100", change_rate=0., advancing_count=0,
        ), now + timedelta(seconds=1))
        self.assertEqual({
            "KOSPI": {"index": 2500., "change_rate": 0.,
                "trade_value_million_won": 1200, "trade_value_eok": 12.,
                "advancing_count": 0, "declining_count": 200, "flat_count": 10},
            "source": "kiwoom_realtime_0J_0U", "observed_at": "2026-10-16T10:15:11+09:00",
            "trade_time": "153100",
        }, session.market_state_at_entry())
        self.assertEqual(1.5, first["KOSPI"]["change_rate"])

    def test_history_request_cut_and_commit_receipts_keep_the_existing_generation_owner(self):
        from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv, MinuteTradeValueAggregator
        now = datetime.fromisoformat("2026-10-24T10:15:10+09:00")
        minute = now.replace(minute=14, second=0, microsecond=0)
        aggregator = MinuteTradeValueAggregator()
        session = PcMarketSession(aggregator)
        session.ensure_minute_day(now, flush=lambda: None, retain=None)
        gap = RealtimeGap(1, (GapRange("trade", "A", "KRX", minute, minute),), False)
        restores = []
        def restore(codes, day):
            restores.append((codes, day))
            return {"A": (minute,)} if codes else {}
        self.assertEqual(("A",), session.prepare_history(("A",), now, force=True, gap_loader=restore))
        generation = aggregator.gap_generation("A")
        bars = (MinuteOhlcv(minute, 100, 120, 90, 110, 10),)
        # A new gap arrives after dispatch and before this request responds.
        aggregator.mark_gap(gap, ("A",), now)
        update = session.accept_history("A", bars, now, client=object(), flush=lambda: None, retain=None)
        self.assertEqual(((), generation, None), (update.bars, update.generation, update.high))
        self.assertFalse(session.history_loaded("A"))
        session.history_committed("A", generation, (minute,))
        self.assertEqual("recovery_required", aggregator.recovery_state("A"))
        self.assertEqual(("A",), session.prepare_history(("A",), now, force=True, gap_loader=restore))
        update = session.accept_history("A", bars, now, client=object(), flush=lambda: None, retain=None)
        self.assertEqual((bars, 120), (update.bars, update.high))
        self.assertEqual("recovering", aggregator.recovery_state("A"))
        self.assertGreater(update.generation, generation)
        self.assertEqual([(("A",), now.date()), ((), now.date())], restores)
        self.assertTrue(session.history_quality_saved("A", {"generation": update.generation, "minutes": (minute,)},))
        self.assertEqual("recovered", aggregator.recovery_state("A"))
        session.history_cache_saved("A")
        self.assertEqual(frozenset({"A"}), session.history_codes)
        self.assertEqual((), session.prepare_history(("A",), now))

    def test_runtime_rollback_selects_original_aggregator_without_rolling_back_observed_facts(self):
        from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import RealtimeSourceRoute, RealtimeSourceTransition
        now = datetime.fromisoformat("2026-10-24T10:15:10+09:00")
        original = MinuteTradeValueAggregator()
        session = PcMarketSession(original)
        session.source_changed(RealtimeSourceTransition(RealtimeSourceRoute.LOCAL_FALLBACK, 4, True))
        session.history_cache_saved("A")
        session.ensure_high_date(now.date())
        session.restore_highs({"A": 120})
        session.accept_ranking_rate("A", 3.5)
        restore = session.capture_runtime()
        session.apply_runtime("personal_server")
        current = session.minute_aggregator
        self.assertIsNot(original, current)
        self.assertEqual("central_waiting", session.source_route)
        self.assertEqual(frozenset(), session.history_codes)
        session.accept_tick(TradeTick("A", 110, None, None, 10, 130, "101510", change_rate=4.5), now)
        session.accept_high("A", 130)
        restore()
        self.assertIs(original, session.minute_aggregator)
        self.assertEqual("local_fallback", session.source_route)
        self.assertEqual(frozenset({"A"}), session.history_codes)
        self.assertEqual((130, 4.5), (session.current_high("A"), session.change_rate("A")))
        self.assertEqual(10, session.execution_pressure_at_entry("A", now)["buy_execution_volume"])
        self.assertTrue(session.source_changed(RealtimeSourceTransition(RealtimeSourceRoute.CENTRAL, 5, True)))
        self.assertEqual(frozenset(), session.history_codes)

    def test_entry_cut_reuses_selected_code_values_and_preserves_missing_rates_and_theme_order(self):
        from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv, MinuteTradeValueAggregator
        from unittest.mock import patch
        now = datetime.fromisoformat("2026-10-24T10:15:10+09:00")
        aggregator = MinuteTradeValueAggregator()
        for code, value in (("A", 3.), ("B", 5.)):
            aggregator.seed(code, (MinuteOhlcv(now.replace(minute=14, second=0, microsecond=0), 100, 110, 90, 105, 10,
                trade_value_eok_override=value),), now)
        session = PcMarketSession(aggregator)
        session.accept_ranking_rate("A", "2.5")
        session.accept_ranking_rate("B", "invalid")
        session.accept_market_index(MarketIndexTick("KOSPI", "101510", 2500.), now)
        with patch.object(aggregator, "trade_value_eok", wraps=aggregator.trade_value_eok) as values:
            fields = session.entry_market_fields("A", "A 회사", 100, 125, now,
                {"B": 1, "A": 2}, {"A": "A 회사", "B": "B 회사"},
                {"A회사": "반도체, AI", "B회사": "ai"}, {"AI": 2})
        self.assertEqual([(("B", 1), {}), (("B", 5), {}), (("A", 1), {}), (("A", 5), {})], values.call_args_list)
        self.assertEqual((3., 3., ("반도체", "AI"), {"반도체": 1, "AI": 2}, 20.),
            (fields["trade_value_1m_eok"], fields["trade_value_5m_eok"], fields["themes"], fields["theme_ranks"], fields["high_distance_percent"]))
        ranking = fields["market_state"]["ranking_table"]
        self.assertEqual((2, 1, 0, 0, 2.5, 8., 8.), (ranking["visible_stock_count"], ranking["advancing_count"],
            ranking["declining_count"], ranking["flat_count"], ranking["average_change_rate"],
            ranking["visible_trade_value_1m_eok"], ranking["visible_trade_value_5m_eok"]))
        self.assertEqual("2026-10-24T10:15:10+09:00", fields["market_state"]["observed_at"])
        self.assertIsNone(session.change_rate("B"))

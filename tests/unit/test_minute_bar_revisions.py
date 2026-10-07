from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key,
    minute_bar_observation,
)
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
)


KST = timezone(timedelta(hours=9))


def _delta(operation_id: str, *, price: int, volume: int, at: datetime) -> dict[str, object]:
    return {
        "trading_date": "2026-09-12", "minute": "10:00", "code": "005930",
        "market": "KRX", "open": price, "high": price, "low": price, "close": price,
        "volume": volume, "trade_value_million_won": volume,
        "updated_at": at.timestamp(), "operation_id": operation_id,
    }


def _observation(value: dict[str, object]):
    observation = minute_bar_observation(
        value, origin=ObservationOrigin.REALTIME,
        completeness=DataCompleteness.IN_PROGRESS,
        source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
    )
    return bar_observation_key(observation), observation


class MinuteBarRevisionTests(unittest.TestCase):
    def test_deltas_are_merged_once_and_final_revision_uses_actual_close_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            first = _delta("op-1", price=100, volume=3, at=datetime(2026, 9, 12, 10, 0, 10, tzinfo=KST))
            second = _delta("op-2", price=105, volume=5, at=datetime(2026, 9, 12, 10, 0, 50, tzinfo=KST))
            store.save_minute_bars([first], observations=[_observation(first)])
            store.save_minute_bars([second], observations=[_observation(second)])
            # 저장은 성공했지만 응답만 유실된 상황의 동일 operation 재시도다.
            store.save_minute_bars([second], observations=[_observation(second)])
            closed_at = datetime(2026, 9, 12, 10, 1, 2, tzinfo=KST)
            closure = {
                "trading_date": "2026-09-12", "minute": "10:00", "code": "005930",
                "market": "KRX", "available_at": closed_at.timestamp(),
                "capture_quality": "complete", "finalization_source": "timer",
                "operation_id": "close-1",
            }
            with self.assertRaisesRegex(ValueError, "before bar_end"):
                store.finalize_minute_bars([{
                    **closure,
                    "available_at": datetime(2026, 9, 12, 10, 0, 59, tzinfo=KST).timestamp(),
                }])
            store.finalize_minute_bars([closure])
            store.finalize_minute_bars([closure])
            bars = store.load_minute_bars("005930", "2026-09-12", "KRX")
            revisions = list(reversed(store.load_observation_revisions("minute_bar", "005930:KRX")))
            manifest = store.create_observation_export(
                datetime(2026, 9, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 12, 3, tzinfo=timezone.utc),
                ("minute_bar",), "005930:KRX",
            )
            page = store.load_observation_export_page(manifest["fixed_watermark"], 0, 1000)
            store.close()

        self.assertEqual((100, 105, 100, 105), tuple(bars[0][key] for key in ("open", "high", "low", "close")))
        self.assertEqual(8, bars[0]["volume"])
        self.assertEqual(3, len(revisions))
        self.assertEqual([3, 8, 8], [row["payload"]["volume"] for row in revisions])
        self.assertFalse(revisions[1]["payload"]["window_closed"])
        self.assertTrue(revisions[2]["payload"]["window_closed"])
        self.assertEqual(closed_at.astimezone(timezone.utc).isoformat(), revisions[2]["available_at"])
        self.assertGreaterEqual(
            datetime.fromisoformat(revisions[2]["available_at"]),
            datetime.fromisoformat(revisions[2]["payload"]["bar_end"]).astimezone(timezone.utc),
        )
        [strict_bar] = replay_krx_minute_bars(page["observations"])
        self.assertEqual(105, strict_bar.close)

    def test_operation_id_cannot_be_reused_for_different_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            now = datetime(2026, 9, 12, 10, 0, 10, tzinfo=KST)
            first = _delta("same", price=100, volume=3, at=now)
            changed = _delta("same", price=101, volume=4, at=now)
            store.save_minute_bars([first], observations=[_observation(first)])
            with self.assertRaisesRegex(ValueError, "payload changed"):
                store.save_minute_bars([changed], observations=[_observation(changed)])
            bar = store.load_minute_bars("005930", "2026-09-12", "KRX")[0]
            store.close()
        self.assertEqual(3, bar["volume"])
        self.assertEqual(100, bar["close"])

    def test_revision_failure_rolls_back_delta_and_operation_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            value = _delta(
                "rollback-op", price=100, volume=3,
                at=datetime(2026, 9, 12, 10, 0, 10, tzinfo=KST),
            )
            invalid = MarketDataObservation(
                MarketDatasetKind.MINUTE_BAR, "005930:KRX", value,
                MarketDataMetadata(
                    datetime(2026, 9, 12, 10), datetime(2026, 9, 12, 10),
                    source="fixture",
                ),
            )
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                store.save_minute_bars(
                    [value], observations=[("2026-09-12T10:00", invalid)],
                )
            self.assertEqual([], store.load_minute_bars("005930", "2026-09-12", "KRX"))
            store.save_minute_bars([value], observations=[_observation(value)])
            self.assertEqual(3, store.load_minute_bars("005930", "2026-09-12", "KRX")[0]["volume"])
            store.close()

    def test_reader_uses_latest_revision_available_by_virtual_clock(self) -> None:
        base = {
            "kind": "minute_bar", "subject": "005930:KRX", "venue": "KRX",
            "observation_key": "2026-09-12T10:00", "value_kind": "actual",
            "completeness": "complete",
            "payload": {
                "market": "KRX", "code": "005930", "bar_start": "2026-09-12T10:00:00+09:00",
                "bar_end": "2026-09-12T10:01:00+09:00", "window_closed": True,
                "capture_quality": "complete", "finalization_source": "timer",
                "open": 100, "high": 105, "low": 99, "close": 102, "volume": 8,
                "trade_value_million_won": 1,
            },
        }
        first = {**base, "accepted_sequence": 1, "revision_id": "first",
                 "available_at": "2026-09-12T01:01:02+00:00"}
        correction = {
            **base, "accepted_sequence": 2, "revision_id": "late",
            "available_at": "2026-09-12T01:02:00+00:00",
            "payload": {**base["payload"], "close": 104},
        }
        in_progress = {
            **base, "accepted_sequence": 3, "revision_id": "forming",
            "completeness": "in_progress",
            "available_at": "2026-09-12T01:01:30+00:00",
            "payload": {**base["payload"], "close": 103, "window_closed": False},
        }
        nxt = {
            **base, "accepted_sequence": 4, "revision_id": "nxt",
            "subject": "005930:NXT", "venue": "NXT",
            "available_at": "2026-09-12T01:01:03+00:00",
            "payload": {**base["payload"], "market": "NXT"},
        }
        cutoff = datetime(2026, 9, 12, 1, 1, 30, tzinfo=timezone.utc)
        frames = replay_krx_minute_bars((correction, in_progress, nxt, first), as_of=cutoff)
        self.assertEqual(1, len(frames))
        self.assertEqual("first", frames[0].revision_id)
        self.assertEqual(102, frames[0].close)


if __name__ == "__main__":
    unittest.main()

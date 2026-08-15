from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import GoogleClock


class FakeResponse:
    headers = {"Date": "Fri, 14 Aug 2026 08:30:00 GMT"}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class GoogleClockTests(unittest.TestCase):
    def test_converts_google_utc_time_to_korea_time_and_caches_it(self) -> None:
        ticks = iter((100.0, 100.0, 160.0, 160.0))
        clock = GoogleClock(opener=lambda *_args, **_kwargs: FakeResponse(), monotonic=lambda: next(ticks))
        self.assertEqual(17, clock.korea_now().hour)
        self.assertEqual(17, clock.korea_now().hour)

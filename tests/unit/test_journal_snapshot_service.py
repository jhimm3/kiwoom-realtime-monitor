from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import TradeEntrySnapshot
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_service import (
    load_episode_entry_snapshots,
    news_at_execution,
)


def snapshot(key: str, executed_at: datetime, news: tuple[dict[str, object], ...] = ()) -> TradeEntrySnapshot:
    return TradeEntrySnapshot(
        key, key, "000001", "테스트", "매수", executed_at, 1000, 1, "KRX", news=news,
    )


class FakeSnapshotRepository:
    def __init__(self, values: tuple[TradeEntrySnapshot, ...] = (), *, fail: bool = False) -> None:
        self.values = values
        self.fail = fail
        self.loads: list[tuple[str, datetime, datetime]] = []
        self.saved: list[tuple[str, tuple[dict[str, object], ...]]] = []

    def load_entry_snapshots(
        self, code: str, start: datetime, end: datetime,
    ) -> tuple[TradeEntrySnapshot, ...]:
        self.loads.append((code, start, end))
        if self.fail:
            raise sqlite3.OperationalError("locked")
        return self.values

    def save_snapshot_news_backfill(
        self, execution_key: str, news: tuple[dict[str, object], ...],
    ) -> None:
        self.saved.append((execution_key, news))


class JournalSnapshotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.started = datetime(2026, 9, 9, 9, 10)
        self.ended = self.started + timedelta(minutes=5)

    def test_loads_one_minute_around_episode(self) -> None:
        value = snapshot("one", self.started)
        repository = FakeSnapshotRepository((value,))

        result = load_episode_entry_snapshots(
            repository, None, "000001", self.started, self.ended,
        )

        self.assertEqual((value,), result)
        self.assertEqual(
            ("000001", self.started - timedelta(minutes=1), self.ended + timedelta(minutes=1)),
            repository.loads[0],
        )

    def test_existing_news_is_preserved_and_missing_news_is_backfilled(self) -> None:
        existing = snapshot("old", self.started, ({"title": "당시 기사"},))
        missing = snapshot("new", self.started + timedelta(minutes=1))
        repository = FakeSnapshotRepository((existing, missing))
        news_repository = object()
        calls: list[str] = []

        def loader(_repository: object, value: TradeEntrySnapshot) -> tuple[dict[str, object], ...]:
            calls.append(value.execution_key)
            return ({"title": "장후 연결 기사"},)

        result = load_episode_entry_snapshots(
            repository, news_repository, "000001", self.started, self.ended,
            news_loader=loader,  # type: ignore[arg-type]
        )

        self.assertEqual(["new"], calls)
        self.assertEqual("당시 기사", result[0].news[0]["title"])
        self.assertEqual(True, result[1].news[0]["backfilled"])
        self.assertEqual([("new", ({"title": "장후 연결 기사"},))], repository.saved)

    def test_empty_news_result_does_not_write(self) -> None:
        value = snapshot("one", self.started)
        repository = FakeSnapshotRepository((value,))

        result = load_episode_entry_snapshots(
            repository, object(), "000001", self.started, self.ended,
            news_loader=lambda _repository, _snapshot: (),  # type: ignore[arg-type]
        )

        self.assertEqual((value,), result)
        self.assertEqual([], repository.saved)

    def test_sqlite_read_error_matches_previous_empty_result(self) -> None:
        repository = FakeSnapshotRepository(fail=True)

        self.assertEqual(
            (),
            load_episode_entry_snapshots(repository, None, "000001", self.started, self.ended),
        )

    def test_news_selection_keeps_execution_window_and_caps_ten(self) -> None:
        assessment = SimpleNamespace(relevant=True, category="공시", outlook="긍정", reason="재료")
        items = [
            SimpleNamespace(
                title=f"기사 {index}", original_link="", link=f"https://example/{index}",
                published_at=self.started - timedelta(hours=index), assessment=assessment,
            )
            for index in range(12)
        ]
        items.extend((
            SimpleNamespace(
                title="너무 오래됨", original_link="", link="old",
                published_at=self.started - timedelta(hours=49), assessment=assessment,
            ),
            SimpleNamespace(
                title="체결 뒤", original_link="", link="future",
                published_at=self.started + timedelta(minutes=6), assessment=assessment,
            ),
        ))
        news_repository = SimpleNamespace(load=lambda _code, limit: tuple(items[:limit]))

        result = news_at_execution(news_repository, snapshot("one", self.started))

        self.assertEqual(10, len(result))
        self.assertEqual("기사 0", result[0]["title"])
        self.assertEqual("https://example/0", result[0]["link"])
        self.assertTrue(all(item["title"] not in ("너무 오래됨", "체결 뒤") for item in result))

    def test_news_read_failure_is_non_blocking(self) -> None:
        def fail(_code: str, limit: int) -> tuple[object, ...]:
            raise sqlite3.OperationalError("locked")

        self.assertEqual(
            (),
            news_at_execution(SimpleNamespace(load=fail), snapshot("one", self.started)),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.domain.market_data_contract import DataCompleteness, MarketDatasetKind, ObservationOrigin
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
    TradeEntrySnapshot,
    scoped_snapshot_execution_key,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.presentation.trade_review_formatting import format_entry_snapshot_blocks
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


class NativeJournalSnapshotNewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_episode_reads_native_news_once_and_keeps_each_snapshot_window_scope_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            at = datetime(2026, 9, 9, 9, 10)
            receipt = as_kst(at + timedelta(days=1))
            news_repository = StockNewsRepository(Path(directory) / "news.sqlite3", now_provider=lambda: receipt)
            assessment = NewsAssessment(True, "공시", "긍정", "original-reason", 7, 3)
            items = tuple(StockNewsItem(
                f"article-{index}", "original-description", f"https://example/{index}",
                f"https://original/{index}", as_kst(at + timedelta(minutes=index * 10 - 5)), assessment,
            ) for index in range(13)) + (
                StockNewsItem("boundary-old", "", "old", "", as_kst(at - timedelta(hours=48)), assessment),
                StockNewsItem("outside-old", "", "outside", "", as_kst(at - timedelta(hours=48, seconds=1)), assessment),
                StockNewsItem("utc", "", "utc", "", as_kst(at).astimezone(timezone.utc), assessment),
                StockNewsItem("unpublished", "", "unknown", "", None, assessment),
            )
            news_repository.upsert("000001", items, checked_at=receipt)
            ordered = news_repository.load("000001", limit=50)
            scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
            other_scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
            values = tuple(replace(
                snapshot(scoped_snapshot_execution_key(f"execution-{index}", scope), at + timedelta(minutes=index * 10)),
                origin_scope=scope,
            ) for index in range(10))
            existing = replace(values[0], execution_key=scoped_snapshot_execution_key("existing", scope),
                               news=({"title": "original-observed-news"},))
            other = replace(values[0], execution_key=scoped_snapshot_execution_key("execution-0", other_scope),
                            origin_scope=other_scope, news=({"title": "other-account-news"},))
            for value in (*values, existing, other):
                repository.save_entry_snapshot(value, now=at, account_scope=value.origin_scope)
            with patch.object(news_repository, "load", wraps=news_repository.load) as load, patch(
                "kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository.kst_wall_now", return_value=receipt,
            ):
                result = load_episode_entry_snapshots(repository, news_repository, "000001", at,
                                                     at + timedelta(minutes=100), account_scope=scope)
            self.assertEqual(1, load.call_count)
            self.assertEqual([("000001",)], [call.args for call in load.call_args_list])
            self.assertEqual({"limit": 50}, load.call_args.kwargs)
            by_key = {value.execution_key: value for value in result}
            self.assertEqual(10, max(len(value.news) for value in result))
            self.assertEqual(existing.news, by_key[existing.execution_key].news)
            for value in values:
                cut = as_kst(value.executed_at)
                expected = [item for item in ordered if item.published_at is not None
                            and cut - timedelta(hours=48) <= as_kst(item.published_at) <= cut + timedelta(minutes=5)][:10]
                saved = by_key[value.execution_key]
                self.assertEqual([item.title for item in expected], [item["title"] for item in saved.news])
                self.assertEqual([item.original_link or item.link for item in expected], [item["link"] for item in saved.news])
                self.assertTrue(all(item["backfilled"] and item["reason"] == "original-reason" for item in saved.news))
                metadata = MarketDataMetadataRepository(path).load(MarketDatasetKind.ENTRY_CONTEXT, "000001:news", value.execution_key)
                self.assertEqual(ObservationOrigin.BACKFILLED, metadata.origin)
                self.assertEqual(receipt, metadata.available_at)
                self.assertEqual(scope, saved.origin_scope)
            self.assertEqual(other.news, repository.load_entry_snapshots("000001", at, at + timedelta(minutes=100), other_scope)[0].news)
            with patch.object(news_repository, "load", wraps=news_repository.load) as second_load:
                self.assertEqual(result, load_episode_entry_snapshots(repository, news_repository, "000001", at,
                                                                     at + timedelta(minutes=100), account_scope=scope))
            self.assertEqual(0, second_load.call_count)

    def test_episode_native_news_read_failure_is_local_and_backfill_write_failure_still_propagates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            at = datetime(2026, 9, 9, 9, 10)
            news_repository = StockNewsRepository(Path(directory) / "news.sqlite3", now_provider=lambda: as_kst(at))
            values = tuple(snapshot(f"execution-{index}", at + timedelta(seconds=index)) for index in range(3))
            for value in values:
                repository.save_entry_snapshot(value, now=at)
            item = StockNewsItem("original-article", "", "https://example", "", as_kst(at),
                                 NewsAssessment(True, "공시", "긍정", "original", 7, 3))
            news_repository.upsert("000001", (item,), checked_at=as_kst(at))
            with patch.object(news_repository, "load", side_effect=sqlite3.OperationalError("051 read fault")) as load:
                result = load_episode_entry_snapshots(repository, news_repository, "000001", at, at + timedelta(minutes=1))
            self.assertEqual(1, load.call_count)
            self.assertTrue(all(not value.news for value in result))
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TRIGGER reject_news BEFORE UPDATE ON trade_entry_snapshots "
                                   "WHEN NEW.news_json<>OLD.news_json "
                                   "BEGIN SELECT RAISE(ABORT,'051 backfill write fault'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "051 backfill write fault"):
                load_episode_entry_snapshots(repository, news_repository, "000001", at, at + timedelta(minutes=1))
            self.assertTrue(all(not value.news for value in repository.load_entry_snapshots("000001", at, at + timedelta(minutes=1))))
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP TRIGGER reject_news")
            with patch.object(news_repository, "load", wraps=news_repository.load) as retry_load:
                recovered = load_episode_entry_snapshots(repository, news_repository, "000001", at, at + timedelta(minutes=1))
            self.assertEqual(1, retry_load.call_count)
            self.assertTrue(all(value.news[0]["title"] == "original-article" for value in recovered))

    def test_duplicate_writer_keeps_observed_news_for_episode_consumer_and_account(self) -> None:
        for backfilled in (False, True):
            for enrich_investor in (False, True):
                with self.subTest(backfilled=backfilled, enrich_investor=enrich_investor), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "journal.sqlite3"
                    repository = JournalRepository(path)
                    at = datetime(2026, 10, 19, 10, 30)
                    scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
                    other_scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
                    source_key = "same-broker-execution"
                    original_news = ({"title": "original", "link": "https://example/original",
                                      "published_at": "2026-10-19T10:20:00+09:00"},)
                    base = replace(snapshot(scoped_snapshot_execution_key(source_key, scope), at),
                                   origin_scope=scope, orderbook={"source": "original-0B"})
                    other = replace(base, execution_key=scoped_snapshot_execution_key(source_key, other_scope),
                                    origin_scope=other_scope, news=({"title": "other-account"},))
                    repository.save_entry_snapshot(other, now=at, account_scope=other_scope)
                    repository.save_entry_snapshot(replace(base, news=() if backfilled else original_news),
                                                   now=at, account_scope=scope)
                    if backfilled:
                        with patch(
                            "kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository.kst_wall_now",
                            return_value=as_kst(at + timedelta(hours=1)),
                        ):
                            load_episode_entry_snapshots(
                                repository, object(), base.stock_code, at, at + timedelta(minutes=1),
                                news_loader=lambda _repo, _snapshot: original_news,
                                account_scope=scope,
                            )
                    expected = repository.load_entry_snapshots(base.stock_code, at, at + timedelta(seconds=1), scope)[0]
                    expected_news_metadata = MarketDataMetadataRepository(path).load(
                        MarketDatasetKind.ENTRY_CONTEXT, f"{base.stock_code}:news", base.execution_key,
                    )
                    investor = {"available": True, "backfilled": True, "source": "confirmed-investor"}
                    program = {"available": True, "backfilled": True, "source": "confirmed-0w"}
                    repository.save_investor_flow_backfill(base.execution_key, investor)
                    repository.save_program_trade_backfill(base.execution_key, program)
                    loader = (lambda _code, _at: {"available": True, "source": "new-investor"}) if enrich_investor else None
                    if enrich_investor:
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute(
                                "CREATE TRIGGER fail_enriched BEFORE INSERT ON trade_entry_snapshots "
                                "WHEN NEW.capture_state='realtime_enriched' "
                                "BEGIN SELECT RAISE(ABORT,'064 enrichment fault'); END"
                            )
                            connection.commit()
                    writer = EntrySnapshotWriter(path, investor_loader=loader)
                    try:
                        writer.enqueue(base)
                        writer.start()
                        writer.requestInterruption()
                        self.assertTrue(writer.wait(5000))
                        if enrich_investor:
                            self.assertTrue(writer.has_pending_work())
                            with closing(sqlite3.connect(path)) as connection:
                                connection.execute("DROP TRIGGER fail_enriched")
                                connection.commit()
                            writer.start()
                            writer.requestInterruption()
                            self.assertTrue(writer.wait(5000))
                        self.assertFalse(writer.has_pending_work())
                    finally:
                        writer.requestInterruption()
                        self.assertTrue(writer.wait(5000))
                    loads: list[str] = []

                    def unexpected_reload(_repository: object, value: TradeEntrySnapshot) -> tuple[dict[str, object], ...]:
                        loads.append(value.execution_key)
                        return ()

                    rows = load_episode_entry_snapshots(
                        repository, object(), base.stock_code, at, at + timedelta(minutes=1),
                        news_loader=unexpected_reload, account_scope=scope,
                    )
                    self.assertEqual(expected.news, rows[0].news)
                    self.assertEqual([], loads)
                    self.assertTrue(any("관련 뉴스 1건" in block for block in format_entry_snapshot_blocks(rows[0])))
                    self.assertEqual(base.execution_key, rows[0].execution_key)
                    self.assertEqual(scope, rows[0].origin_scope)
                    self.assertEqual(base.executed_at, rows[0].executed_at)
                    self.assertEqual(base.orderbook, rows[0].orderbook)
                    self.assertEqual("confirmed-investor", rows[0].investor_flow["source"])
                    self.assertEqual(program, rows[0].investor_flow["program_trade"])
                    self.assertEqual(other.news, repository.load_entry_snapshots(
                        base.stock_code, at, at + timedelta(seconds=1), other_scope,
                    )[0].news)
                    metadata = MarketDataMetadataRepository(path).load(
                        MarketDatasetKind.ENTRY_CONTEXT, f"{base.stock_code}:news", base.execution_key,
                    )
                    self.assertIsNotNone(metadata)
                    self.assertEqual(ObservationOrigin.BACKFILLED if backfilled else ObservationOrigin.REALTIME, metadata.origin)
                    self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
                    self.assertEqual(expected_news_metadata, metadata)

    def test_native_news_updates_remain_explicit_and_atomic_on_failure_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            at = datetime(2026, 10, 19, 10, 30)
            base = snapshot("native-news-retry", at)
            repository.save_entry_snapshot(base, now=at)
            original = ({"title": "original", "published_at": "2026-10-19T10:20:00+09:00"},)
            available_at = as_kst(at + timedelta(hours=1))
            with patch(
                "kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository.kst_wall_now",
                return_value=available_at,
            ):
                repository.save_snapshot_news_backfill(base.execution_key, original)
            metadata_repository = MarketDataMetadataRepository(path)
            expected_metadata = metadata_repository.load(
                MarketDatasetKind.ENTRY_CONTEXT, f"{base.stock_code}:news", base.execution_key,
            )

            def stored_rows() -> tuple[tuple[object, ...], tuple[tuple[object, ...], ...]]:
                with closing(sqlite3.connect(path)) as connection:
                    return (
                        connection.execute("SELECT * FROM trade_entry_snapshots WHERE execution_key=?", (base.execution_key,)).fetchone(),
                        tuple(connection.execute("SELECT * FROM market_data_observation_meta WHERE observation_key=? ORDER BY subject", (base.execution_key,)).fetchall()),
                    )

            previous = stored_rows()
            for operation in ("duplicate", "nonempty", "backfill"):
                with self.subTest(operation=operation):
                    with patch(
                        "kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository.upsert_market_data_metadata",
                        side_effect=sqlite3.OperationalError("064 metadata fault"),
                    ), self.assertRaisesRegex(sqlite3.OperationalError, "064 metadata fault"):
                        if operation == "backfill":
                            repository.save_snapshot_news_backfill(base.execution_key, ({"title": "replacement"},))
                        else:
                            repository.save_entry_snapshot(replace(base, news=({"title": "replacement"},) if operation == "nonempty" else ()),
                                                           now=at + timedelta(seconds=1))
                    self.assertEqual(previous, stored_rows())
            repository.save_entry_snapshot(base, now=at + timedelta(seconds=1))
            self.assertEqual(({**original[0], "backfilled": True},), repository.load_entry_snapshots(
                base.stock_code, at, at + timedelta(seconds=1),
            )[0].news)
            for receipt in (at - timedelta(minutes=1), available_at, available_at + timedelta(hours=1)):
                repository.save_entry_snapshot(base, now=receipt)
                metadata = metadata_repository.load(
                    MarketDatasetKind.ENTRY_CONTEXT, f"{base.stock_code}:news", base.execution_key,
                )
                self.assertEqual(expected_metadata, metadata)
                self.assertFalse(metadata.was_available_by(as_kst(at + timedelta(minutes=30))))
                self.assertTrue(metadata.was_available_by(available_at))
            correction = ({"title": "explicit-correction", "published_at": "2026-10-19T10:21:00+09:00"},)
            repository.save_entry_snapshot(replace(base, news=correction), now=at + timedelta(seconds=2))
            self.assertEqual(correction, repository.load_entry_snapshots(base.stock_code, at, at + timedelta(seconds=1))[0].news)
            repository.save_snapshot_news_backfill(base.execution_key, ())
            self.assertEqual((), repository.load_entry_snapshots(base.stock_code, at, at + timedelta(seconds=1))[0].news)
            repository.save_entry_snapshot(replace(base, news=correction), now=at + timedelta(seconds=3))
            with self.assertRaises(TypeError):
                repository.save_entry_snapshot(replace(base, news=None), now=at + timedelta(seconds=4))
            self.assertEqual(correction, repository.load_entry_snapshots(base.stock_code, at, at + timedelta(seconds=1))[0].news)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DELETE FROM market_data_observation_meta WHERE subject=? AND observation_key=?",
                                   (f"{base.stock_code}:news", base.execution_key))
                connection.commit()
            repository.save_entry_snapshot(base, now=at + timedelta(seconds=5))
            self.assertIsNone(metadata_repository.load(
                MarketDatasetKind.ENTRY_CONTEXT, f"{base.stock_code}:news", base.execution_key,
            ))
            self.assertEqual(correction, repository.load_entry_snapshots(base.stock_code, at, at + timedelta(seconds=1))[0].news)


if __name__ == "__main__":
    unittest.main()

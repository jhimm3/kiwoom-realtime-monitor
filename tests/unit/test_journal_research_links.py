from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.journal_enrichment import (
    journal_analysis_revision,
    journal_research_link,
    news_evidence_timing,
)
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


class JournalResearchLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repository = JournalRepository(Path(self.directory.name) / "journal.sqlite3")
        self.now = datetime(2026, 9, 13, 9, 10)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_historical_execution_allows_null_decision_and_link_is_immutable(self) -> None:
        link = journal_research_link(
            "order|005930|2026-09-13T09:10:00|매수",
            run_id="run-1", snapshot_id="snapshot-1",
            evidence_timing="unverified", now=self.now,
        )

        first = self.repository.save_journal_research_link(link)
        second = self.repository.save_journal_research_link(link)

        self.assertIsNone(first.decision_id)
        self.assertEqual(first, second)
        self.assertEqual((link,), self.repository.load_journal_research_links((link.execution_ref,)))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.save_journal_research_link(replace(link, run_id="different-run"))

    def test_analysis_revision_changes_only_when_input_or_version_changes(self) -> None:
        first = journal_analysis_revision(
            "group-1", "trade_setup", {"bars": "revision-1"}, "analysis/v1",
            {"type": "돌파"}, now=self.now,
        )
        same = journal_analysis_revision(
            "group-1", "trade_setup", {"bars": "revision-1"}, "analysis/v1",
            {"type": "돌파"}, now=self.now + timedelta(seconds=30),
        )
        changed = journal_analysis_revision(
            "group-1", "trade_setup", {"bars": "revision-2"}, "analysis/v1",
            {"type": "돌파"}, now=self.now + timedelta(minutes=1),
        )

        self.repository.save_journal_analysis_revision(first)
        self.repository.save_journal_analysis_revision(same)
        self.repository.save_journal_analysis_revision(changed)

        loaded = self.repository.load_journal_analysis_revisions("group-1")
        self.assertEqual((first, changed), loaded)

    def test_analysis_and_research_links_do_not_cross_account_scope(self) -> None:
        real = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        real_canonical = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        mock = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
        real_revision = journal_analysis_revision(
            "same-group", "trade_setup", {"bars": "same"}, "analysis/v1", {"type": "돌파"},
            now=self.now, account_scope=real, canonical_scope=real_canonical,
        )
        mock_revision = journal_analysis_revision(
            "same-group", "trade_setup", {"bars": "same"}, "analysis/v1", {"type": "돌파"},
            now=self.now, account_scope=mock,
        )
        real_link = journal_research_link(
            "same-execution", run_id="run-1", now=self.now, account_scope=real,
            canonical_scope=real_canonical,
        )
        mock_link = journal_research_link(
            "same-execution", run_id="run-1", now=self.now, account_scope=mock,
        )
        self.repository.save_journal_analysis_revision(real_revision)
        self.repository.save_journal_analysis_revision(mock_revision)
        self.repository.save_journal_research_link(real_link)
        self.repository.save_journal_research_link(mock_link)

        self.assertEqual(
            (real_revision,), self.repository.load_journal_analysis_revisions(
                "same-group", real_canonical,
            ),
        )
        self.assertEqual(
            (mock_revision,), self.repository.load_journal_analysis_revisions("same-group", mock),
        )
        self.assertEqual(
            (real_link,), self.repository.load_journal_research_links(
                ("same-execution",), real_canonical,
            ),
        )
        self.assertEqual(
            (mock_link,), self.repository.load_journal_research_links(("same-execution",), mock),
        )

    def test_news_requires_revision_and_available_at_for_at_execution_claim(self) -> None:
        executed = datetime(2026, 9, 13, 9, 10, tzinfo=timezone(timedelta(hours=9)))

        self.assertEqual(
            "unverified",
            news_evidence_timing({"published_at": "2026-09-13T09:00:00+09:00"}, executed),
        )
        self.assertEqual(
            "at_execution",
            news_evidence_timing({
                "revision_id": "news-r1", "available_at": "2026-09-13T09:09:00+09:00",
            }, executed),
        )
        self.assertEqual(
            "post_trade",
            news_evidence_timing({
                "revision_id": "news-r2", "available_at": "2026-09-13T09:11:00+09:00",
            }, executed),
        )


if __name__ == "__main__":
    unittest.main()

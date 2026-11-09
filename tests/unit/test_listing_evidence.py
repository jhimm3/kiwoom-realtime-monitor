from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.listing_evidence import KindListingEvidence
from kiwoom_monitor.domain.market_data_contract import DataCompleteness, ListingEvidence, ListingType
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_strategy_references
from scripts.prepare_kind_listing_references import prepare_kind_listing_references


class ListingEvidenceTests(unittest.TestCase):
    def test_live_cut_uses_native_restart_and_late_correction_overrides_catalog_type(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        now = datetime.fromisoformat("2026-10-15T08:00:00+09:00")
        published = now - timedelta(days=1)
        original = ListingEvidence("250030", now.date(), ListingType.UNKNOWN, published,
            "krx-kind-listing-disclosure", "first", "first-url", DataCompleteness.PARTIAL)
        typed = replace(original, listing_type=ListingType.KONEX_TO_KOSDAQ, available_at=now,
            revision="first/kind-type:02:konex:1", evidence_id="origin-url#kosdaq-listing-first",
            completeness=DataCompleteness.COMPLETE)
        broken = replace(original, available_at=published + timedelta(hours=3), listing_date=None,
            revision="correction", evidence_id="correction-url#unverified-body")
        recovered = replace(broken, listing_date=now.date(), evidence_id="correction-url")
        relisting = replace(recovered, listing_type=ListingType.RELISTING, available_at=now + timedelta(hours=1),
            revision="correction/kind-type:03:2", evidence_id="correction-url#typed", completeness=DataCompleteness.COMPLETE)

        class Provider:
            rows = (original, typed)

            def listing_evidence_for(self, _codes, **_kwargs):
                return self.rows

        database = root / "pc.sqlite3"
        store = SQLiteQueryStore(database, now_provider=lambda: now)
        self.addCleanup(store.close)
        store.initialize()
        provider = Provider()
        cache = KindListingEvidence(store, provider, lambda: now)
        cache.refresh(now)
        self.assertEqual((), cache.for_symbol("250030", now - timedelta(seconds=1)))
        self.assertEqual((original, typed), cache.for_symbol("250030", now))

        now += timedelta(minutes=30)
        provider.rows = (original, broken)
        cache.refresh(now)
        self.assertEqual((broken,), cache.for_symbol("250030", now))
        now += timedelta(minutes=30)
        provider.rows = (original, recovered, relisting)
        cache.refresh(now)
        restarted = KindListingEvidence(store, provider, lambda: now)
        restarted.restore()
        self.assertEqual((recovered, relisting), restarted.for_symbol("250030", now))
        self.assertEqual((original, typed), restarted.for_symbol("250030", typed.available_at))

        base, output = root / "base.json", root / "frozen.json"
        base.write_text(json.dumps({"version": "strategy-reference/v1", "calendar": [], "listings": []}), encoding="utf-8")
        digest = prepare_kind_listing_references(database, base, output, typed.available_at)
        frozen = load_frozen_strategy_references(output, expected_sha256=digest)
        self.assertEqual({original, typed}, set(frozen.listings))

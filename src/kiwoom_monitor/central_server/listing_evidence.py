"""Publish KIND listing evidence after local durable acceptance."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from kiwoom_monitor.application.market_session_schedule import decode_strategy_references, listing_evidence_document
from kiwoom_monitor.domain.market_data_contract import ListingEvidence
from kiwoom_monitor.domain.time_basis import as_kst


logger = logging.getLogger(__name__)


class KindListingEvidence:
    def __init__(self, store, provider, now_provider) -> None:
        self._store = store
        self._provider = provider
        self._now = now_provider
        self._entries: dict[str, tuple[ListingEvidence, datetime]] | None = None
        self._by_symbol: dict[str, tuple[tuple[ListingEvidence, datetime], ...]] = {}
        self._next_refresh: datetime | None = None

    def for_symbol(self, symbol: str, cutoff: datetime) -> tuple[ListingEvidence, ...]:
        cutoff = as_kst(cutoff)
        rows = tuple(row for row, observed in self._by_symbol.get(symbol, ()) if observed <= cutoff
                     and (row.available_at is None or row.available_at <= cutoff))
        # Automatic input covers the listing day. Existing pinned evidence still
        # determines any user-approved after-listing scope.
        if not any(row.listing_date == cutoff.date() for row in rows):
            return ()
        raw_by_receipt: dict[str, list[ListingEvidence]] = {}
        for row in rows:
            if "/kind-type:" not in row.revision:
                raw_by_receipt.setdefault(row.revision, []).append(row)
        raw = tuple(row for versions in raw_by_receipt.values() for row in _best_raw_versions(versions))
        unknown = tuple(row for row in raw if row.available_at is None)
        if unknown:
            return unknown
        if not raw:
            return ()
        latest = max(row.available_at for row in raw)
        latest_rows = tuple(row for row in raw if row.available_at == latest)
        if len(latest_rows) != 1:
            return latest_rows
        receipt = latest_rows[0].revision
        # A catalog observation about an older filing can never supersede a newer
        # official correction, even if that correction was discovered late.
        return (*latest_rows, *(row for row in rows if row.revision.startswith(receipt + "/kind-type:")))

    def refresh(self, now: datetime) -> None:
        now = as_kst(now)
        if not self.refresh_due(now):
            return
        self._next_refresh = now + timedelta(minutes=5)
        try:
            self.restore()
            rows = self._provider.listing_evidence_for(None, trading_date=now.date(), as_of=now)
            observed = as_kst(self._now())
            existing = self._entries
            assert existing is not None
            additions = {row.evidence_id: (row, observed) for row in rows if row.evidence_id not in existing}
            if additions:
                self._store.upsert_documents("listing_evidence", [{
                    "owner": "kind", "key": key, "document": {
                        "evidence": listing_evidence_document(row), "observed_at": seen.isoformat(),
                    },
                } for key, (row, seen) in additions.items()])
                self._publish({**existing, **additions})
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            logger.warning("KIND 신규상장 근거 갱신 실패: %s", type(error).__name__)
            return
        self._next_refresh = now + timedelta(minutes=30)

    def refresh_due(self, now: datetime) -> bool:
        return self._next_refresh is None or as_kst(now) >= self._next_refresh

    def restore(self) -> None:
        if self._entries is not None:
            return
        entries = {}
        offset = 0
        while True:
            stored = self._store.load_documents("listing_evidence", "kind", 1000, offset)
            for item in stored:
                document = item["document"]
                _, (row,) = decode_strategy_references({
                    "version": "strategy-reference/v1", "calendar": [], "listings": [document["evidence"]],
                })
                entries[row.evidence_id] = (row, as_kst(datetime.fromisoformat(document["observed_at"])))
            if len(stored) < 1000:
                break
            offset += len(stored)
        self._publish(entries)

    def _publish(self, entries: dict[str, tuple[ListingEvidence, datetime]]) -> None:
        by_symbol: dict[str, list[tuple[ListingEvidence, datetime]]] = {}
        for row, observed in entries.values():
            by_symbol.setdefault(row.symbol, []).append((row, observed))
        self._entries = entries
        self._by_symbol = {symbol: tuple(rows) for symbol, rows in by_symbol.items()}


def _best_raw_versions(rows: list[ListingEvidence]) -> tuple[ListingEvidence, ...]:
    # A retry can repair an unavailable body or timestamp for the same receipt.
    quality = [(row.available_at is not None, row.listing_date is not None) for row in rows]
    best = max(quality)
    return tuple(row for row, score in zip(rows, quality, strict=True) if score == best)

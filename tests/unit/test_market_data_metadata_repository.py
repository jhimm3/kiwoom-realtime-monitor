from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
)
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.journal_schema import initialize_journal_database
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    MarketDataMetadataRepository,
)


class MarketDataMetadataRepositoryTests(unittest.TestCase):
    def test_round_trip_preserves_time_and_source_meaning_in_main_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            effective_at = datetime(2026, 9, 10, 9, 1, tzinfo=UTC)
            metadata = MarketDataMetadata(
                effective_at=effective_at,
                available_at=effective_at + timedelta(seconds=2),
                venue=TradingVenue.COMBINED,
                unit=DataUnit.EOK_WON,
                value_kind=DataValueKind.DERIVED_FROM_ACTUAL,
                completeness=DataCompleteness.COMPLETE,
                origin=ObservationOrigin.REALTIME,
                source="kiwoom-0B",
                candidate_universe=CandidateUniverse.RANKING_TOP20,
            )
            observation = MarketDataObservation(
                kind=MarketDatasetKind.TOP20_INDEX,
                subject="KOSPI+KOSDAQ",
                value={"trade_value": 123.4},
                metadata=metadata,
            )

            repository = MarketDataMetadataRepository(path)
            repository.save("2026-09-10T09:01", observation)

            self.assertEqual(
                metadata,
                repository.load(
                    MarketDatasetKind.TOP20_INDEX,
                    "KOSPI+KOSDAQ",
                    "2026-09-10T09:01",
                ),
            )

    def test_same_observation_key_is_corrected_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            initialize_journal_database(path)
            repository = MarketDataMetadataRepository(path)
            initial = MarketDataObservation(
                MarketDatasetKind.MINUTE_BAR,
                "005930",
                None,
                MarketDataMetadata(None, None),
            )
            corrected = MarketDataObservation(
                MarketDatasetKind.MINUTE_BAR,
                "005930",
                None,
                MarketDataMetadata(
                    datetime(2026, 9, 10, 9, 1),
                    datetime(2026, 9, 10, 9, 2),
                    completeness=DataCompleteness.COMPLETE,
                    origin=ObservationOrigin.BACKFILLED,
                ),
            )
            repository.save("2026-09-10/09:01", initial)
            repository.save("2026-09-10/09:01", corrected)

            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute(
                    "SELECT count(*) FROM market_data_observation_meta"
                ).fetchone()[0]
            self.assertEqual(1, count)
            self.assertEqual(
                corrected.metadata,
                repository.load(
                    MarketDatasetKind.MINUTE_BAR, "005930", "2026-09-10/09:01"
                ),
            )

    def test_unknown_stored_values_are_not_promoted_to_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    """
                    INSERT INTO market_data_observation_meta VALUES
                    ('daily_bar','005930','2026-09-10',NULL,NULL,
                     'legacy','legacy','legacy','legacy','legacy','legacy','legacy')
                    """
                )
                connection.commit()
            stored = MarketDataMetadataRepository(path).load(
                MarketDatasetKind.DAILY_BAR, "005930", "2026-09-10"
            )
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(DataCompleteness.UNCONFIRMED, stored.completeness)
            self.assertEqual(ObservationOrigin.UNKNOWN, stored.origin)
            self.assertFalse(stored.was_available_by(datetime.now(UTC)))

    def test_blank_observation_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            observation = MarketDataObservation(
                MarketDatasetKind.UNKNOWN, "subject", None, MarketDataMetadata(None, None)
            )
            with self.assertRaisesRegex(ValueError, "observation_key"):
                MarketDataMetadataRepository(path).save(" ", observation)


if __name__ == "__main__":
    unittest.main()

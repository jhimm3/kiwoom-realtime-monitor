"""NAS 서버 컨테이너 안에서 PostgreSQL 저장 경계를 실제로 검증한다."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime
from time import time

from kiwoom_monitor.central_server.central_schema import CENTRAL_SCHEMA_VERSION
from kiwoom_monitor.central_server.database import PostgresQueryStore, StoredQuery
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


def main() -> int:
    database_url = os.environ.get("KIWOOM_SERVER_DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        print(json.dumps({"status": "error", "reason": "postgres_url_required"}))
        return 2
    marker = f"integration-{uuid.uuid4().hex}"
    rollback_marker = f"rollback-{uuid.uuid4().hex}"
    now = time()
    observed_at = datetime.now(UTC).replace(microsecond=0)
    store = PostgresQueryStore(database_url)
    try:
        store.initialize()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT version,name FROM central_schema_migrations ORDER BY version")
            migrations = [(int(row[0]), str(row[1])) for row in cursor.fetchall()]
        if not migrations or migrations[-1][0] != CENTRAL_SCHEMA_VERSION:
            raise RuntimeError(f"schema_version={migrations[-1][0] if migrations else 0}")

        checks: dict[str, bool] = {}
        store.save_query(marker, "integration", now + 60, StoredQuery({"marker": marker}, False, ""))
        checks["query_cache"] = store.load_query(marker) == StoredQuery({"marker": marker}, False, "")

        store.save_realtime_snapshots([{
            "event_type": "integration_check", "item_key": marker,
            "received_at": now, "event": {"type": "integration_check", "code": marker},
        }])
        checks["realtime"] = any(
            value.get("code") == marker for value in store.load_realtime_snapshots([marker])
        )

        minute_bar = {
            "trading_date": observed_at.date().isoformat(), "minute": "09:01",
            "code": marker, "market": "KRX", "open": 100, "high": 110,
            "low": 90, "close": 105, "volume": 10,
            "trade_value_million_won": 1, "updated_at": now,
        }
        metadata = MarketDataObservation(
            MarketDatasetKind.MINUTE_BAR, marker, minute_bar,
            MarketDataMetadata(
                observed_at, observed_at, TradingVenue.KRX, DataUnit.MILLION_WON,
                DataValueKind.ACTUAL, DataCompleteness.COMPLETE,
                ObservationOrigin.REALTIME, "postgres-integration",
                CandidateUniverse.RANKING_TOP20,
            ),
        )
        store.replace_minute_bars([minute_bar], observations=[(marker, metadata)])
        checks["minute_bars"] = store.load_minute_bars(
            marker, observed_at.date().isoformat(), "KRX",
        )[0]["close"] == 105
        loaded_metadata = store.load_market_data_metadata(
            MarketDatasetKind.MINUTE_BAR, marker, marker,
        )
        checks["metadata"] = bool(loaded_metadata and loaded_metadata.source == "postgres-integration")

        daily_bar = {
            "trading_date": observed_at.date().isoformat(), "code": marker, "market": "KRX",
            "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10,
            "trade_value_million_won": 1, "updated_at": now,
        }
        store.replace_daily_bars([daily_bar])
        checks["daily_bars"] = store.load_daily_bars(marker, "KRX", 1)[0]["close"] == 105

        store.save_dataset_snapshot(
            "integration_check", marker, marker, {"marker": marker},
        )
        checks["dataset_snapshots"] = store.load_dataset_snapshots(
            "integration_check", marker, 1,
        )[0]["payload"]["marker"] == marker

        store.upsert_documents("integration_check", [{
            "owner": "postgres", "key": marker,
            "document": {"value": 1, "marker": marker},
        }])
        loaded = store.load_documents("integration_check", "postgres", 10)
        checks["documents"] = any(value.get("key") == marker for value in loaded)

        store.save_external_bars([{
            "provider": "integration", "instrument": marker, "contract": marker,
            "timeframe": "5m", "bar_time": observed_at.isoformat(), "open": 1.0,
            "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0,
            "updated_at": now,
        }])
        checks["external_bars"] = store.load_external_bars(marker, "5m", 1)[0]["close"] == 1.5
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            raise RuntimeError(f"repository_round_trip_failed={','.join(failed)}")

        connection = store._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s)",
                ("integration_check", "postgres", rollback_marker, 0.0, json.dumps({"value": 2})),
            )
            connection.rollback()
        finally:
            connection.close()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM central_documents WHERE collection=%s AND owner=%s "
                "AND document_key=%s", ("integration_check", "postgres", rollback_marker),
            )
            if int(cursor.fetchone()[0]) != 0:
                raise RuntimeError("rollback_failed")
        print(json.dumps({
            "status": "ok", "schema_version": migrations[-1][0],
            "checks": checks, "rollback": True,
        }))
        return 0
    finally:
        try:
            with store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM central_documents WHERE collection=%s AND owner=%s "
                    "AND document_key IN (%s,%s)",
                    ("integration_check", "postgres", marker, rollback_marker),
                )
                cursor.execute("DELETE FROM central_api_query_cache WHERE cache_key=%s", (marker,))
                cursor.execute("DELETE FROM central_realtime_latest WHERE item_key=%s", (marker,))
                cursor.execute("DELETE FROM central_minute_bars WHERE code=%s", (marker,))
                cursor.execute("DELETE FROM central_daily_bars WHERE code=%s", (marker,))
                cursor.execute(
                    "DELETE FROM central_dataset_snapshots WHERE kind=%s AND subject=%s",
                    ("integration_check", marker),
                )
                cursor.execute(
                    "DELETE FROM central_market_data_observation_meta WHERE subject=%s",
                    (marker,),
                )
                cursor.execute("DELETE FROM central_external_bars WHERE instrument=%s", (marker,))
        except Exception:
            pass
        store.close()


if __name__ == "__main__":
    sys.exit(main())

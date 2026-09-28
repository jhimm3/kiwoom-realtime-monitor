"""Run prepared-news import checks only against the dedicated PostgreSQL DB."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def main() -> int:
    import psycopg

    live = os.environ["KIWOOM_SERVER_DATABASE_URL"]
    parts = urlsplit(live)
    target_name = "kiwoom_monitor_diagnostic_test"
    target = urlunsplit((parts.scheme, parts.netloc, f"/{target_name}",
                         parts.query, parts.fragment))
    with psycopg.connect(live, connect_timeout=5,
                         options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)",
                           (target_name,))
            current, exists = cursor.fetchone()
    if current == target_name or not exists:
        raise RuntimeError("dedicated test database is unavailable or live URL points to it")
    os.environ["KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL"] = target
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests" / "integration"), pattern="test_prepared_news_import_postgres.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

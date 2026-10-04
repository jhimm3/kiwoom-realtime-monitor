"""Run PostgreSQL access pilot tests using the server's dedicated test database."""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


TEST_DATABASE_NAME = "kiwoom_monitor_diagnostic_test"


def _dedicated_url(live_url: str) -> str:
    parts = urlsplit(live_url)
    if not parts.scheme.startswith("postgres") or not parts.netloc or not parts.path:
        raise RuntimeError("KIWOOM_SERVER_DATABASE_URL is not a PostgreSQL URL")
    if parts.path.lstrip("/") == TEST_DATABASE_NAME:
        raise RuntimeError("KIWOOM_SERVER_DATABASE_URL points at the diagnostic test DB")
    return urlunsplit((parts.scheme, parts.netloc, f"/{TEST_DATABASE_NAME}",
                       parts.query, parts.fragment))


def _explicit_dedicated_url(database_url: str) -> str:
    parts = urlsplit(database_url)
    if not parts.scheme.startswith("postgres") or not parts.netloc or not parts.path:
        raise RuntimeError("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL is not a PostgreSQL URL")
    if parts.path.lstrip("/") != TEST_DATABASE_NAME:
        raise RuntimeError("diagnostic DB URL must target kiwoom_monitor_diagnostic_test")
    return database_url


def _resolve_target_url(live_url: str, explicit_url: str) -> str:
    if explicit_url:
        return _explicit_dedicated_url(explicit_url)
    if not live_url:
        raise RuntimeError(
            "KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL or KIWOOM_SERVER_DATABASE_URL is required"
        )
    return _dedicated_url(live_url)


def _install_project_import_paths(root: Path) -> None:
    """Prefer the candidate source tree, including when root is a zipapp path."""
    source = str(root / "src")
    project = str(root)
    for entry in (project, source):
        while entry in sys.path:
            sys.path.remove(entry)
    sys.path[:0] = [source, project]


def main() -> int:
    import psycopg

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test",
        action="append",
        default=[],
        help="fully qualified unittest name; repeat to run a grouped pilot batch",
    )
    arguments = parser.parse_args()

    live_url = os.environ.get("KIWOOM_SERVER_DATABASE_URL", "").strip()
    explicit_url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "").strip()
    target_url = _resolve_target_url(live_url, explicit_url)

    # An explicit URL is verified against its connected database name without
    # touching the operational DSN. Legacy NAS runs derive the target URL from
    # the operational DSN and use that DSN only for a read-only existence check.
    preflight_url = target_url if explicit_url else live_url
    with psycopg.connect(
        preflight_url,
        connect_timeout=5,
        autocommit=True,
        options="-c default_transaction_read_only=on",
    ) as connection:
        with connection.cursor() as cursor:
            if explicit_url:
                cursor.execute("SELECT current_database()")
                current_database = cursor.fetchone()[0]
                if current_database != TEST_DATABASE_NAME:
                    raise RuntimeError("connected database is not the dedicated diagnostic database")
            else:
                cursor.execute(
                    "SELECT current_database(), EXISTS("
                    "SELECT 1 FROM pg_database WHERE datname = %s)",
                    (TEST_DATABASE_NAME,),
                )
                current_database, test_database_exists = cursor.fetchone()
                if current_database == TEST_DATABASE_NAME or not test_database_exists:
                    raise RuntimeError("dedicated diagnostic PostgreSQL database is unavailable")

    os.environ["KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL"] = target_url
    os.environ["KIWOOM_DIAGNOSTIC_REQUIRE_EXISTING_CACHE_SCHEMA"] = "1"

    root = Path(__file__).resolve().parents[1]
    _install_project_import_paths(root)
    loader = unittest.defaultTestLoader
    suite = (
        unittest.TestSuite(loader.loadTestsFromName(name) for name in arguments.test)
        if arguments.test
        else loader.discover(str(root / "tests" / "integration"),
                             pattern="test_postgres_access_postgres.py")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

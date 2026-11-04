"""Pure dedicated PostgreSQL test-target policy shared by tool adapters.

This validates URL identity only; connected-database and lease checks remain
with the caller before any test or replay operation.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


TEST_DATABASE_NAME = "kiwoom_monitor_diagnostic_test"


def dedicated_url(live_url: str) -> str:
    parts = urlsplit(live_url)
    if parts.scheme not in {"postgres", "postgresql"} or not parts.netloc or not parts.path:
        raise RuntimeError("KIWOOM_SERVER_DATABASE_URL is not a PostgreSQL URL")
    if parts.path.lstrip("/") == TEST_DATABASE_NAME:
        raise RuntimeError("KIWOOM_SERVER_DATABASE_URL points at the diagnostic test DB")
    return urlunsplit((parts.scheme, parts.netloc, f"/{TEST_DATABASE_NAME}",
                       parts.query, parts.fragment))


def explicit_dedicated_url(database_url: str) -> str:
    parts = urlsplit(database_url)
    if parts.scheme not in {"postgres", "postgresql"} or not parts.netloc or not parts.path:
        raise RuntimeError("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL is not a PostgreSQL URL")
    if parts.path.lstrip("/") != TEST_DATABASE_NAME:
        raise RuntimeError("diagnostic DB URL must target kiwoom_monitor_diagnostic_test")
    return database_url


def resolve_target_url(live_url: str, explicit_url: str) -> str:
    if explicit_url:
        return explicit_dedicated_url(explicit_url)
    if not live_url:
        raise RuntimeError(
            "KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL or KIWOOM_SERVER_DATABASE_URL is required"
        )
    return dedicated_url(live_url)



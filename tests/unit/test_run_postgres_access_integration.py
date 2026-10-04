from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_postgres_access_integration import (
    _dedicated_url,
    _explicit_dedicated_url,
    _install_project_import_paths,
    _resolve_target_url,
)


class DedicatedPostgresUrlTests(unittest.TestCase):
    def test_candidate_src_precedes_installed_packages_and_test_root(self) -> None:
        root = Path("candidate.zip")
        with patch.object(sys, "path", ["existing"]):
            _install_project_import_paths(root)
            self.assertEqual(
                [str(root / "src"), str(root), "existing"],
                sys.path,
            )

    def test_replaces_database_and_preserves_connection_options(self) -> None:
        source = "postgresql://user:secret@database:5432/kiwoom_monitor?sslmode=prefer"
        self.assertEqual(
            "postgresql://user:secret@database:5432/kiwoom_monitor_diagnostic_test?sslmode=prefer",
            _dedicated_url(source),
        )

    def test_rejects_diagnostic_database_as_source(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "points at the diagnostic test DB"):
            _dedicated_url(
                "postgresql://user:secret@database:5432/kiwoom_monitor_diagnostic_test"
            )

    def test_rejects_non_postgresql_url(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not a PostgreSQL URL"):
            _dedicated_url("sqlite:///monitor.db")

    def test_accepts_explicit_diagnostic_url_without_live_database_url(self) -> None:
        target = "postgresql://user:secret@test-host:5432/kiwoom_monitor_diagnostic_test?sslmode=require"
        self.assertEqual(target, _resolve_target_url("", target))

    def test_explicit_url_must_name_the_dedicated_database(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must target kiwoom_monitor_diagnostic_test"):
            _explicit_dedicated_url("postgresql://user:secret@test-host/kiwoom_monitor")

    def test_explicit_url_rejects_invalid_driver_or_missing_host(self) -> None:
        for value in ("sqlite:///monitor.db", "postgresql:///kiwoom_monitor_diagnostic_test"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "not a PostgreSQL URL"):
                    _explicit_dedicated_url(value)

    def test_without_explicit_url_still_derives_from_live_database_url(self) -> None:
        live = "postgresql://user:secret@test-host:5432/kiwoom_monitor?sslmode=require"
        self.assertEqual(_dedicated_url(live), _resolve_target_url(live, ""))


if __name__ == "__main__":
    unittest.main()

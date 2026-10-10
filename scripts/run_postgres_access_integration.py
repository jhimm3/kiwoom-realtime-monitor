"""Run PostgreSQL access pilot tests using the server's dedicated test database."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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
    parser.add_argument("--ci-group", choices=("postgres_required", "postgres_extended"),
                        help="run one reviewed PostgreSQL group from tests/ci_groups.json")
    parser.add_argument("--report", type=Path,
                        help="write a machine-readable result without database credentials")
    arguments = parser.parse_args()
    if arguments.ci_group and arguments.test:
        parser.error("choose --ci-group or --test")

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
    from scripts.run_regression import _code_tree_identity

    source_start = _code_tree_identity()
    names = list(arguments.test)
    if arguments.ci_group:
        catalog = json.loads((root / "tests" / "ci_groups.json").read_text(encoding="utf-8"))
        if catalog.get("version") != 1 or not isinstance(catalog.get(arguments.ci_group), list):
            raise RuntimeError("invalid PostgreSQL CI group")
        names = catalog[arguments.ci_group]
        if not names or len(names) != len(set(names)) or any(
            not isinstance(name, str) or not name.startswith("tests.integration.test_")
            for name in names
        ):
            raise RuntimeError("invalid PostgreSQL CI group modules")
    loader = unittest.defaultTestLoader
    if names:
        modules = [(name, loader.loadTestsFromName(name)) for name in names]
    else:
        modules = [("tests.integration.test_postgres_access_postgres", loader.discover(
            str(root / "tests" / "integration"), pattern="test_postgres_access_postgres.py"))]
    discovered = {name: module_suite.countTestCases() for name, module_suite in modules}
    suite = unittest.TestSuite(module_suite for _, module_suite in modules)
    planned = suite.countTestCases()
    if planned == 0 or any(count == 0 for count in discovered.values()):
        print(f"PostgreSQL integration: no tests discovered: {discovered}", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    source_end = _code_tree_identity()
    complete = (
        result.wasSuccessful()
        and result.testsRun == planned
        and not result.skipped
        and not result.expectedFailures
        and not result.unexpectedSuccesses
        and source_start["lf_sha256"] == source_end["lf_sha256"]
    )
    if not complete:
        print(
            "PostgreSQL integration incomplete: "
            f"planned={planned} ran={result.testsRun} failures={len(result.failures)} "
            f"errors={len(result.errors)} skipped={len(result.skipped)} "
            f"expected_failures={len(result.expectedFailures)} "
            f"unexpected_successes={len(result.unexpectedSuccesses)}",
            file=sys.stderr,
        )
    if arguments.report:
        if arguments.report.exists():
            raise FileExistsError(f"Refusing to overwrite existing report: {arguments.report}")
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps({
            "status": "passed" if complete else "failed",
            "group": arguments.ci_group or "explicit" if names else "postgres-access-default",
            "modules": list(discovered), "discovered": discovered,
            "planned": planned, "ran": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                text=True, check=True,
            ).stdout.strip(),
            "source_root": str(root / "src"),
            "source_start": source_start, "source_end": source_end,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())

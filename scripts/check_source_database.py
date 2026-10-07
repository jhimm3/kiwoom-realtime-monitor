"""Read-only source compatibility check, or offline known checkpoint downgrade.

Run under the newest reviewed source, with the server's normal DB environment.
The shell deployment owner must stop the server before invoking --offline.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))


def supported_schema(source: Path) -> int:
    """Inspect the target without importing or starting its application."""
    path = source / "src/kiwoom_monitor/central_server/central_schema.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    versions = [node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "CentralSchemaMigration" and node.args
                and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is int]
    if not versions or sorted(versions) != list(range(1, max(versions) + 1)):
        raise RuntimeError("target source migration plan cannot be verified")
    return max(versions)


def check_database(cursor, target: int) -> dict:
    cursor.execute("SELECT COALESCE(MAX(version),0) FROM central_schema_migrations")
    current = int(cursor.fetchone()[0])
    if current <= target:
        return {"database_schema": current, "target_schema": target, "compatible": True}
    from kiwoom_monitor.central_server.shadow_checkpoint import MIGRATION_VERSION
    if current == MIGRATION_VERSION and target == MIGRATION_VERSION - 1:
        return {"database_schema": current, "target_schema": target, "compatible": False,
                "offline_checkpoint_downgrade_required": True}
    raise RuntimeError("database schema is newer than target; no known safe downgrade")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-source", type=Path, required=True)
    parser.add_argument("--offline", action="store_true",
                        help="deployment owner has stopped all checkpoint writers; permit known downgrade")
    args = parser.parse_args()
    target = supported_schema(args.target_source.resolve())
    from kiwoom_monitor.central_server.postgres_access import DBWriterContext, open_observed_connection
    from kiwoom_monitor.central_server.shadow_checkpoint import downgrade_schema
    import psycopg

    url = os.environ["KIWOOM_SERVER_DATABASE_URL"]
    context = DBWriterContext(
        "maintenance.source_compatibility", "shadow_checkpoint_downgrade" if args.offline else "source_schema",
        "downgrade_source" if args.offline else "check_source", access_mode="write" if args.offline else "read",
    )
    # A deployment check never runs schema initialization or other migrations.
    with open_observed_connection(lambda: psycopg.connect(url, connect_timeout=5), context) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='10s'")
            cursor.execute("SET LOCAL statement_timeout='60s'")
            if not args.offline:
                cursor.execute("SET TRANSACTION READ ONLY")
            result = check_database(cursor, target)
            if not result["compatible"] and args.offline:
                converted = downgrade_schema(cursor)
                result = {**check_database(cursor, target), "checkpoints_materialized": converted}
    print(json.dumps(result))
    return 0 if result["compatible"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

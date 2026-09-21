from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_DATABASE = Path("data/historical_intelligence.sqlite3")
DEFAULT_NAS_PROJECT = Path(r"X:\kiwoom-monitor")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)
    with closing(sqlite3.connect(f"file:{destination.resolve().as_posix()}?mode=ro", uri=True)) as check:
        result = str(check.execute("PRAGMA integrity_check").fetchone()[0])
    if result != "ok":
        raise RuntimeError(f"SQLite snapshot integrity check failed: {result}")


def _database_summary(path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        market = connection.execute(
            """
            SELECT provider, code, interval_seconds, venue, session_scope, adjustment_mode,
                   COUNT(*), MIN(bar_time), MAX(bar_time)
            FROM market_bars
            GROUP BY provider, code, interval_seconds, venue, session_scope, adjustment_mode
            ORDER BY provider, code, interval_seconds
            """
        ).fetchall()
        news_status = connection.execute(
            """
            SELECT article_fetch_status, training_eligible, COUNT(*)
            FROM news_articles
            GROUP BY article_fetch_status, training_eligible
            ORDER BY article_fetch_status, training_eligible
            """
        ).fetchall()
        attempts = connection.execute(
            """
            SELECT url_role, status, COUNT(*)
            FROM news_article_fetch_attempts
            GROUP BY url_role, status
            ORDER BY url_role, status
            """
        ).fetchall()
        jobs = connection.execute(
            "SELECT state, COUNT(*) FROM news_backfill_jobs GROUP BY state ORDER BY state"
        ).fetchall()
    return {
        "market_bars": [list(row) for row in market],
        "news_articles": [list(row) for row in news_status],
        "news_fetch_attempts": [list(row) for row in attempts],
        "news_jobs": [list(row) for row in jobs],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="완결된 과거자료 SQLite 스냅샷과 원응답을 NAS 버전 디렉터리에 게시합니다."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--nas-project", type=Path, default=DEFAULT_NAS_PROJECT)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args()

    database = args.database.resolve(strict=True)
    nas_project = args.nas_project.resolve(strict=True)
    expected = DEFAULT_NAS_PROJECT.resolve(strict=True)
    if os.path.normcase(str(nas_project)) != os.path.normcase(str(expected)):
        raise SystemExit(f"Refusing unexpected NAS project root: {nas_project}")
    if not (nas_project / "AGENTS.md").is_file():
        raise SystemExit(f"NAS project sentinel is missing: {nas_project}")

    published_at = datetime.now(UTC)
    stamp = published_at.strftime("%Y%m%dT%H%M%SZ")
    destination_root = (
        nas_project / "deploy" / "synology" / "server-data"
        / "historical-intelligence" / "v1"
    )
    runs_root = destination_root / "runs"
    backup_root = nas_project / ".codex-backups" / "historical-intelligence"
    runs_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="historical-intelligence-") as directory:
        local_snapshot = Path(directory) / "historical_intelligence.sqlite3"
        _snapshot_database(database, local_snapshot)
        database_hash = _sha256(local_snapshot)
        run_name = f"{stamp}-{database_hash[:12]}"
        final_run = runs_root / run_name
        if final_run.exists():
            raise SystemExit(f"NAS run already exists: {final_run}")
        staging = runs_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            target_database = staging / local_snapshot.name
            shutil.copy2(local_snapshot, target_database)
            artifacts: list[dict[str, object]] = []
            artifact_root = staging / "raw"
            for source in args.artifact:
                resolved = source.resolve(strict=True)
                artifact_root.mkdir(exist_ok=True)
                target = artifact_root / resolved.name
                shutil.copy2(resolved, target)
                artifacts.append({
                    "name": resolved.name,
                    "bytes": target.stat().st_size,
                    "sha256": _sha256(target),
                })
            manifest = {
                "schema": "historical-intelligence-nas/v1",
                "published_at": published_at.isoformat(),
                "run": run_name,
                "database": {
                    "name": target_database.name,
                    "bytes": target_database.stat().st_size,
                    "sha256": database_hash,
                    "integrity_check": "ok",
                },
                "artifacts": artifacts,
                "summary": _database_summary(local_snapshot),
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(staging, final_run)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    latest = destination_root / "latest.json"
    if latest.exists():
        shutil.copy2(latest, backup_root / f"{stamp}-latest.json")
    latest_temp = destination_root / f".latest-{uuid.uuid4().hex}.json"
    latest_document = {
        "schema": "historical-intelligence-latest/v1",
        "published_at": published_at.isoformat(),
        "run": run_name,
        "manifest": f"runs/{run_name}/manifest.json",
        "database_sha256": database_hash,
    }
    latest_temp.write_text(
        json.dumps(latest_document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(latest_temp, latest)
    print(json.dumps({
        "status": "published",
        "run": str(final_run),
        "latest": str(latest),
        "database_sha256": database_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

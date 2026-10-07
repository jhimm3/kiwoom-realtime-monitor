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
            # This is a disposable local snapshot. Skip rollback journaling and
            # sync per page; the full integrity check below gates NAS publication.
            destination_connection.execute("PRAGMA journal_mode=OFF")
            destination_connection.execute("PRAGMA synchronous=OFF")
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


def _context_summary(path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        indexes = connection.execute(
            "SELECT code,interval_seconds,COUNT(*),MIN(bar_time),MAX(bar_time) "
            "FROM historical_index_bars GROUP BY code,interval_seconds ORDER BY code,interval_seconds"
        ).fetchall()
        fundamentals = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT code),MIN(trade_date),MAX(trade_date) "
            "FROM historical_stock_fundamentals"
        ).fetchone()
        has_adjustments = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_stock_adjustments'"
        ).fetchone() is not None
        adjustments = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT code),MIN(adjustment_date),MAX(adjustment_date) "
            "FROM historical_stock_adjustments"
        ).fetchone() if has_adjustments else (0, 0, None, None)
        classifications = connection.execute(
            "SELECT classification_status,COUNT(*) FROM historical_stock_adjustment_classifications "
            "GROUP BY classification_status ORDER BY classification_status"
        ).fetchall() if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_stock_adjustment_classifications'"
        ).fetchone() else []
        candidate_event_jobs = connection.execute(
            "SELECT event_kind,state,COUNT(*) FROM historical_candidate_event_jobs "
            "GROUP BY event_kind,state ORDER BY event_kind,state"
        ).fetchall() if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_candidate_event_jobs'"
        ).fetchone() else []
        exchange_jobs = connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_exchange_jobs "
            "GROUP BY state ORDER BY state"
        ).fetchall() if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_candidate_exchange_jobs'"
        ).fetchone() else []
        effective_document_states = connection.execute(
            "SELECT state,COUNT(*) FROM historical_exchange_document_fetch "
            "GROUP BY state ORDER BY state"
        ).fetchall() if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_exchange_document_fetch'"
        ).fetchone() else []
        effective_event_kinds = connection.execute(
            "SELECT kind,COUNT(*) FROM historical_exchange_effective_events "
            "GROUP BY kind ORDER BY kind"
        ).fetchall() if effective_document_states else []
        jobs = connection.execute(
            "SELECT kind,state,COUNT(*) FROM context_jobs GROUP BY kind,state ORDER BY kind,state"
        ).fetchall()
        has_vi = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='krx_vi_imports'"
        ).fetchone() is not None
        vi_events = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT trade_date),MIN(trade_date),MAX(trade_date) "
            "FROM historical_vi_events"
        ).fetchone() if has_vi else (0, 0, None, None)
        vi_imports = connection.execute(
            "SELECT COUNT(*),SUM(source_rows),SUM(inserted_rows) FROM krx_vi_imports"
        ).fetchone() if has_vi else (0, 0, 0)
    return {"index_bars": [list(row) for row in indexes],
            "stock_fundamentals": list(fundamentals), "jobs": [list(row) for row in jobs],
            "stock_adjustments": list(adjustments),
            "adjustment_classifications": [list(row) for row in classifications],
            "candidate_event_jobs": [list(row) for row in candidate_event_jobs],
            "candidate_exchange_jobs": [list(row) for row in exchange_jobs],
            "exchange_effective_documents": [list(row) for row in effective_document_states],
            "exchange_effective_events": [list(row) for row in effective_event_kinds],
            "vi_events": list(vi_events), "vi_imports": list(vi_imports)}


def _context_raw_paths(database: Path) -> list[tuple[Path, Path]]:
    raw_root = database.parent / "historical_collection"
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        records = [(raw_file, "context") for (raw_file,) in connection.execute(
            "SELECT DISTINCT raw_file FROM context_jobs "
            "WHERE state IN ('complete','unavailable','excluded') "
            "AND raw_file<>'' ORDER BY raw_file"
        )]
        has_vi = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='krx_vi_imports'"
        ).fetchone() is not None
        if has_vi:
            records.extend((raw_file, "krx_vi") for (raw_file,) in connection.execute(
                "SELECT DISTINCT source_file FROM krx_vi_imports ORDER BY source_file"
            ))
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='adjustment_disclosure_jobs'"
        ).fetchone():
            records.extend((raw_file, "dart_adjustment") for (raw_file,) in connection.execute(
                "SELECT DISTINCT raw_file FROM adjustment_disclosure_jobs "
                "WHERE state='complete' AND raw_file<>'' ORDER BY raw_file"
            ))
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_candidate_event_jobs'"
        ).fetchone():
            records.extend((raw_file, "dart_candidate_events") for (raw_file,) in connection.execute(
                "SELECT DISTINCT raw_file FROM historical_candidate_event_jobs "
                "WHERE state IN ('complete','empty') AND raw_file<>'' ORDER BY raw_file"
            ))
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_candidate_exchange_jobs'"
        ).fetchone():
            records.extend((raw_file, "dart_exchange") for (raw_file,) in connection.execute(
                "SELECT DISTINCT raw_file FROM historical_candidate_exchange_jobs "
                "WHERE state IN ('complete','empty') AND raw_file<>'' ORDER BY raw_file"
            ))
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_exchange_document_fetch'"
        ).fetchone():
            records.extend((raw_file, "dart_effective") for (raw_file,) in connection.execute(
                "SELECT DISTINCT raw_file FROM historical_exchange_document_fetch "
                "WHERE state IN ('complete','parsed_no_explicit_transition') "
                "AND raw_file<>'' ORDER BY raw_file"
            ))
    paths = []
    for raw_file, category in records:
        source = Path(raw_file).resolve(strict=True)
        category_root = (
            raw_root / "context" / "dart_adjustment" if category == "dart_adjustment" else
            raw_root / "context" / "dart_candidate_events" if category == "dart_candidate_events" else
            raw_root / "context" / "dart_exchange" if category == "dart_exchange" else
            raw_root / "context" / "dart_effective" if category == "dart_effective" else
            raw_root / category
        )
        relative = source.relative_to(category_root.resolve(strict=True))
        relative = Path(category) / relative
        paths.append((source, relative))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="완결된 과거자료 SQLite 스냅샷과 원응답을 NAS 버전 디렉터리에 게시합니다."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--context-database", type=Path,
                        help="Optional separately collected index/fundamentals/VI database")
    parser.add_argument("--nas-project", type=Path, default=DEFAULT_NAS_PROJECT)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args()

    database = args.database.resolve(strict=True)
    context_database = args.context_database.resolve(strict=True) if args.context_database else None
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
        context_snapshot = None
        context_hash = None
        if context_database is not None:
            context_snapshot = Path(directory) / "historical_market_context.sqlite3"
            _snapshot_database(context_database, context_snapshot)
            context_hash = _sha256(context_snapshot)
        run_name = f"{stamp}-{database_hash[:12]}"
        final_run = runs_root / run_name
        if final_run.exists():
            raise SystemExit(f"NAS run already exists: {final_run}")
        staging = runs_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            target_database = staging / local_snapshot.name
            shutil.copy2(local_snapshot, target_database)
            if target_database.stat().st_size != local_snapshot.stat().st_size or _sha256(target_database) != database_hash:
                raise RuntimeError("NAS historical database copy differs from the verified local snapshot")
            context_manifest = None
            if context_snapshot is not None and context_hash is not None:
                target_context = staging / context_snapshot.name
                shutil.copy2(context_snapshot, target_context)
                if target_context.stat().st_size != context_snapshot.stat().st_size or _sha256(target_context) != context_hash:
                    raise RuntimeError("NAS market context copy differs from the verified local snapshot")
                context_raw = []
                for source, relative in _context_raw_paths(context_database):
                    target_raw = staging / "raw" / relative
                    target_raw.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target_raw)
                    context_raw.append({
                        "name": (Path("raw") / relative).as_posix(),
                        "bytes": target_raw.stat().st_size,
                        "sha256": _sha256(target_raw),
                    })
                context_manifest = {
                    "name": target_context.name,
                    "bytes": target_context.stat().st_size,
                    "sha256": context_hash,
                    "integrity_check": "ok",
                    "summary": _context_summary(context_snapshot),
                    "raw_artifacts": context_raw,
                }
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
            if context_manifest is not None:
                manifest["context_database"] = context_manifest
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

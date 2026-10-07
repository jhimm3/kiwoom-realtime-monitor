"""Publish a verified, immutable historical index/fundamentals/VI snapshot to NAS."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from publish_historical_intelligence_to_nas import (
    _context_raw_paths, _context_summary, _sha256, _snapshot_database,
)
from collect_candidate_exchange_effective_dates import selected_filings


DEFAULT_DATABASE = Path("data/historical_market_context.sqlite3")
DEFAULT_NAS_PROJECT = Path(r"X:\kiwoom-monitor")


def assert_ready(database: Path, expected_vi_files: int) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        job_states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM context_jobs GROUP BY state"
        ).fetchall())
        vi_files = connection.execute("SELECT COUNT(*) FROM krx_vi_imports").fetchone()[0]
        adjustments = connection.execute(
            "SELECT COUNT(*) FROM historical_stock_adjustments"
        ).fetchone()[0] if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_stock_adjustments'"
        ).fetchone() else 0
        classified = connection.execute(
            "SELECT COUNT(*) FROM historical_stock_adjustment_classifications"
        ).fetchone()[0] if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_stock_adjustment_classifications'"
        ).fetchone() else 0
        disclosure_states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM adjustment_disclosure_jobs GROUP BY state"
        ).fetchall()) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='adjustment_disclosure_jobs'"
        ).fetchone() else {}
        candidate_event_states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_event_jobs GROUP BY state"
        ).fetchall()) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_candidate_event_jobs'"
        ).fetchone() else {}
        candidate_event_missing_raw = connection.execute(
            "SELECT COUNT(*) FROM historical_candidate_event_jobs WHERE "
            "state IN ('complete','empty','reused_existing') AND raw_file=''"
        ).fetchone()[0] if candidate_event_states else 0
        exchange_states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_exchange_jobs GROUP BY state"
        ).fetchall()) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_candidate_exchange_jobs'"
        ).fetchone() else {}
        exchange_missing_raw = connection.execute(
            "SELECT COUNT(*) FROM historical_candidate_exchange_jobs WHERE "
            "state IN ('complete','empty') AND raw_file=''"
        ).fetchone()[0] if exchange_states else 0
        has_effective_documents = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_exchange_document_fetch'"
        ).fetchone() is not None
        effective_states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_exchange_document_fetch GROUP BY state"
        )) if has_effective_documents else {}
        effective_expected = len(selected_filings(connection)) if has_effective_documents else 0
        effective_missing_raw = connection.execute(
            "SELECT COUNT(*) FROM historical_exchange_document_fetch WHERE "
            "state IN ('complete','parsed_no_explicit_transition') AND raw_file=''"
        ).fetchone()[0] if has_effective_documents else 0
    if job_states.get("pending", 0) or job_states.get("running", 0) or job_states.get("failed", 0):
        raise RuntimeError(f"Context jobs are not all complete: {job_states}")
    if vi_files != expected_vi_files:
        raise RuntimeError(f"Expected {expected_vi_files} KRX VI files, found {vi_files}")
    if adjustments != classified or any(disclosure_states.get(key, 0) for key in ("pending", "running", "failed")):
        raise RuntimeError(
            "Adjustment DART enrichment is incomplete: "
            f"adjustments={adjustments}, classified={classified}, jobs={disclosure_states}"
        )
    if any(candidate_event_states.get(key, 0) for key in
           ("pending", "running", "failed", "rate_limited", "unmapped")):
        raise RuntimeError(f"Candidate-event DART collection is incomplete: {candidate_event_states}")
    if candidate_event_missing_raw:
        raise RuntimeError(f"Candidate-event DART raw references are missing: {candidate_event_missing_raw}")
    if any(exchange_states.get(key, 0) for key in
           ("pending", "running", "failed", "rate_limited", "unmapped")):
        raise RuntimeError(f"Candidate exchange DART collection is incomplete: {exchange_states}")
    if exchange_missing_raw:
        raise RuntimeError(f"Candidate exchange DART raw references are missing: {exchange_missing_raw}")
    if has_effective_documents and sum(effective_states.values()) != effective_expected:
        raise RuntimeError("Exchange effective-date document collection is incomplete: "
                           f"{sum(effective_states.values())}/{effective_expected}")
    if any(effective_states.get(key, 0) for key in ("api_limit", "retryable")):
        raise RuntimeError(f"Exchange effective-date documents need retry: {effective_states}")
    if effective_missing_raw:
        raise RuntimeError(f"Exchange effective-date raw references are missing: {effective_missing_raw}")
    return {"job_states": job_states, "vi_files": vi_files,
            "adjustments": adjustments, "adjustment_classifications": classified,
            "adjustment_disclosure_states": disclosure_states,
            "candidate_event_states": candidate_event_states,
            "candidate_exchange_states": exchange_states,
            "exchange_effective_document_states": effective_states,
            "exchange_effective_expected": effective_expected,
            "coverage_complete": (
                job_states.get("unavailable", 0) == 0
                and effective_states.get("unavailable", 0) == 0
            )}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--nas-project", type=Path, default=DEFAULT_NAS_PROJECT)
    parser.add_argument("--expected-vi-files", type=int, default=8)
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    nas_project = args.nas_project.resolve(strict=True)
    expected = DEFAULT_NAS_PROJECT.resolve(strict=True)
    if os.path.normcase(str(nas_project)) != os.path.normcase(str(expected)):
        raise SystemExit(f"Refusing unexpected NAS root: {nas_project}")
    if not (nas_project / "AGENTS.md").is_file():
        raise SystemExit("NAS project sentinel is missing")
    if not (nas_project / "deploy" / "synology" / "server-data").is_dir():
        raise SystemExit("NAS server-data directory is missing")
    readiness = assert_ready(database, args.expected_vi_files)
    published_at = datetime.now(UTC)
    stamp = published_at.strftime("%Y%m%dT%H%M%SZ")
    destination = nas_project / "deploy" / "synology" / "server-data" / "historical-market-context" / "v1"
    runs = destination / "runs"
    backup = nas_project / ".codex-backups" / "historical-market-context"
    runs.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="historical-market-context-") as temporary:
        snapshot = Path(temporary) / "historical_market_context.sqlite3"
        _snapshot_database(database, snapshot)
        snapshot_hash = _sha256(snapshot)
        run_name = f"{stamp}-{snapshot_hash[:12]}"
        final = runs / run_name
        staging = runs / f".staging-{uuid.uuid4().hex}"
        if final.exists():
            raise SystemExit(f"NAS run already exists: {final}")
        staging.mkdir()
        target = staging / snapshot.name
        shutil.copy2(snapshot, target)
        raw_artifacts = []
        for source, relative in _context_raw_paths(database):
            raw_target = staging / "raw" / relative
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, raw_target)
            copied_hash = _sha256(raw_target)
            if source.parent.name == "krx_vi" and source.stem != copied_hash:
                raise RuntimeError(f"KRX raw hash mismatch: {source}")
            raw_artifacts.append({"name": (Path("raw") / relative).as_posix(),
                                  "bytes": raw_target.stat().st_size,
                                  "sha256": copied_hash})
        manifest = {
            "schema": "historical-market-context-nas/v1",
            "published_at": published_at.isoformat(),
            "run": run_name,
            "database": {"name": target.name, "bytes": target.stat().st_size,
                         "sha256": snapshot_hash, "integrity_check": "ok"},
            "readiness": readiness,
            "summary": _context_summary(snapshot),
            "raw_artifacts": raw_artifacts,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not staging.resolve().is_relative_to(runs.resolve()) or not final.resolve().is_relative_to(runs.resolve()):
            raise RuntimeError("NAS run path escaped the intended directory")
        os.replace(staging, final)
    latest = destination / "latest.json"
    if latest.exists():
        shutil.copy2(latest, backup / f"{stamp}-latest.json")
    latest_temp = destination / f".latest-{uuid.uuid4().hex}.json"
    latest_temp.write_text(json.dumps({
        "schema": "historical-market-context-latest/v1",
        "published_at": published_at.isoformat(), "run": run_name,
        "manifest": f"runs/{run_name}/manifest.json", "database_sha256": snapshot_hash,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(latest_temp, latest)
    print(json.dumps({"status": "published", "run": str(final),
                      "database_sha256": snapshot_hash,
                      "raw_artifacts": len(raw_artifacts)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

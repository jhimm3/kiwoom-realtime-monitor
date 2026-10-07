"""Read-only gate for an unfinished PC historical-news archive.

This command reports blockers.  It never changes build_state or seals a file.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any


SEARCH_SCOPE = "historical_backfill"


def audit(path: Path, *, full_integrity_check: bool = False) -> dict[str, Any]:
    path = path.resolve(strict=True)
    blockers: list[str] = []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        manifests = {
            key: json.loads(value)
            for key, value in db.execute("SELECT key,value_json FROM archive_build_manifest")
        }
        report = manifests.get("report", {})
        if report.get("build_state") != "building":
            blockers.append("archive_is_not_in_building_state")
        if not report.get("dataset_id"):
            blockers.append("dataset_id_not_assigned")

        staged = manifests.get("prepared_search_inputs", {})
        if staged.get("state") != "complete":
            blockers.append("search_input_staging_incomplete")
        rows = dict(db.execute(
            "SELECT progress_state,COUNT(*) FROM archive_input_progress "
            "WHERE scope=? GROUP BY progress_state", (SEARCH_SCOPE,),
        ))
        staged_rows = staged.get("staged_rows")
        if staged_rows != sum(rows.values()) or staged_rows != staged.get("expected_rows"):
            blockers.append("search_input_progress_count_mismatch")
        for state in ("pending", "article_body_done", "assessment_done"):
            if rows.get(state, 0):
                blockers.append(f"search_{state}_remaining")
        unknown = set(rows) - {"pending", "article_body_done", "assessment_done",
                               "event_done", "source_failed"}
        if unknown:
            blockers.append("unknown_search_progress_state")

        failures = dict(db.execute(
            "SELECT error,COUNT(*) FROM archive_input_progress "
            "WHERE scope=? AND progress_state='source_failed' GROUP BY error",
            (SEARCH_SCOPE,),
        ))
        if rows.get("source_failed", 0):
            # A separate per-input source-failure review is required before a
            # complete_with_source_failures seal can be accepted.
            blockers.append("source_failures_require_explicit_review")
        if sum(failures.values()) != rows.get("source_failed", 0):
            blockers.append("source_failure_count_mismatch")

        verification = manifests.get("prepared_search_rule_verification")
        events = manifests.get("prepared_search_events")
        projection = manifests.get("prepared_search_projection", {})
        market = manifests.get("prepared_market_projection", {})
        coverage = manifests.get("archive_coverage", {})
        if verification is None:
            blockers.append("search_rule_verification_missing")
        if events is None:
            blockers.append("search_event_manifest_missing")
        elif events.get("verification") != verification or events.get("worklist_count") != rows.get("event_done", 0):
            blockers.append("search_event_manifest_count_or_generation_mismatch")
        if projection.get("state") != "complete" or projection.get("projected") != projection.get("sources"):
            blockers.append("search_projection_incomplete")
        if market.get("state") != "complete":
            blockers.append("market_projection_incomplete")
        if coverage.get("state") not in {"complete", "complete_with_source_failures"}:
            blockers.append("archive_coverage_unverified")

        tables = {name for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        verified_count = 0
        mismatched_count = 0
        unverified_count = 0
        if "archive_verified_rule_inputs" in tables:
            verified_count, mismatched_count = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(outcome<>'verified'),0) "
                "FROM archive_verified_rule_inputs WHERE scope=?", (SEARCH_SCOPE,)
            ).fetchone()
            unverified_count = db.execute(
                "SELECT COUNT(*) FROM archive_input_progress p "
                "LEFT JOIN archive_verified_rule_inputs v ON "
                "v.scope=p.scope AND v.stock_code=p.stock_code AND v.identity=p.identity "
                "WHERE p.scope=? AND p.progress_state IN ('assessment_done','event_done') "
                "AND (v.scope IS NULL OR v.outcome<>'verified' OR v.input_hash<>p.input_hash "
                "OR v.article_revision_id<>p.article_revision_id "
                "OR v.body_revision_id<>p.body_revision_id)", (SEARCH_SCOPE,),
            ).fetchone()[0]
        else:
            unverified_count = rows.get("assessment_done", 0) + rows.get("event_done", 0)
        if verified_count != rows.get("event_done", 0) + rows.get("assessment_done", 0):
            blockers.append("search_rule_verification_count_mismatch")
        if mismatched_count or unverified_count:
            blockers.append("search_rule_verification_mismatch")

        resolution_count = conflict_count = 0
        if "archive_event_resolution" in tables:
            resolution_count, conflict_count = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(outcome='conflict'),0) "
                "FROM archive_event_resolution"
            ).fetchone()
        if resolution_count != rows.get("event_done", 0):
            blockers.append("search_event_resolution_count_mismatch")
        if conflict_count:
            blockers.append("search_event_conflict")

        if "archive_search_projection" in tables:
            projected_rows = db.execute("SELECT COUNT(*) FROM archive_search_projection").fetchone()[0]
            if projected_rows != projection.get("projected"):
                blockers.append("search_projection_row_count_mismatch")
        else:
            projected_rows = 0
            if projection.get("state") == "complete":
                blockers.append("search_projection_table_missing")
        if "archive_search_projection_sources" in tables:
            source_rows = db.execute(
                "SELECT COUNT(*) FROM archive_search_projection_sources"
            ).fetchone()[0]
            if source_rows != projection.get("sources"):
                blockers.append("search_projection_source_count_mismatch")
        elif projection.get("state") == "complete":
            blockers.append("search_projection_source_table_missing")

        integrity = None
        if full_integrity_check:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                blockers.append("sqlite_integrity_check_failed")

    return {
        "archive": str(path), "read_only": True,
        "build_state": report.get("build_state"), "dataset_id": report.get("dataset_id"),
        "search_progress": rows, "search_expected_rows": staged.get("expected_rows"),
        "source_failure_reasons": failures,
        "verified_rule_rows": verified_count, "rule_mismatches": mismatched_count,
        "unverified_rule_inputs": unverified_count,
        "event_resolutions": resolution_count, "event_conflicts": conflict_count,
        "search_projection_rows": projected_rows,
        "market_projection_state": market.get("state"),
        "coverage_state": coverage.get("state"),
        "integrity_check": integrity, "blockers": sorted(set(blockers)),
        "ready_for_seal_review": not blockers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--full-integrity-check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(args.archive, full_integrity_check=args.full_integrity_check),
                     ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()

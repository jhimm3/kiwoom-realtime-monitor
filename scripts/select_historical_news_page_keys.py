"""Freeze the source-page keys relevant to the one-off PC news archive.

The large raw SQLite file is read, never copied.  Its source_pages rows are
append-only in the collector; fixed rowid cutoffs bound this build input.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import time


MATCHED_KEYS_SQL = (
    "SELECT o.code,o.source_date,o.query_text,o.start,COUNT(*) "
    "FROM news_search_observations o "
    "JOIN news_articles a ON a.provider=o.provider AND a.office_id=o.office_id "
    "AND a.article_id=o.article_id "
    "JOIN prepared.prepared_news p ON p.scope='historical_backfill' "
    "AND p.stock_code=o.code AND p.identity=COALESCE(NULLIF(a.original_url,''),"
    "NULLIF(a.portal_url,''),'naver:'||a.office_id||':'||a.article_id) "
    "WHERE o.rowid<=? GROUP BY o.code,o.source_date,o.query_text,o.start"
)


def _request_fields(request_key: str) -> tuple[str, str, str, str, int]:
    # The collector concatenates the raw query text without URL escaping it.
    # Split only on its fixed outer fields; a query may contain '&', '+' or '%'.
    match = re.fullmatch(
        r"code=([^&]+)&(?:date=([^&]+)|from=([^&]+)&to=([^&]+))"
        r"&query=(.*)&start=([0-9]+)", request_key,
    )
    if match is None:
        raise ValueError(f"invalid source request key: {request_key[:200]}")
    code, exact_date, from_date, to_date, query, start_text = match.groups()
    start = int(start_text)
    if exact_date:
        return code, exact_date, exact_date, query, start
    first, last = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if last < first or (last - first).days > 366:
        raise ValueError("invalid source request date range")
    return code, first.isoformat(), last.isoformat(), query, start


def _page_observation_keys(request_key: str) -> list[tuple[str, str, str, int]]:
    code, first_text, last_text, query, start = _request_fields(request_key)
    first, last = date.fromisoformat(first_text), date.fromisoformat(last_text)
    return [(code, (first + timedelta(days=day)).isoformat(), query, start)
            for day in range((last - first).days + 1)]


def select_keys(raw_path: Path, prepared_path: Path, output: Path) -> dict[str, object]:
    raw_path = raw_path.resolve(strict=True)
    prepared_path = prepared_path.resolve(strict=True)
    output = output.resolve()
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise FileExistsError("page-key output or partial already exists")
    if not output.parent.is_dir():
        raise FileNotFoundError("page-key parent directory does not exist")
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(f"file:{raw_path.as_posix()}?mode=ro", uri=True)) as raw:
            raw.execute("ATTACH DATABASE ? AS prepared",
                        (f"file:{prepared_path.as_posix()}?mode=ro",))
            raw.execute("BEGIN")
            pages_cutoff = int(raw.execute("SELECT COALESCE(MAX(rowid),0) FROM source_pages")
                                .fetchone()[0])
            observations_cutoff = int(raw.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM news_search_observations").fetchone()[0])
            snapshot_at = datetime.now(timezone.utc).isoformat()
            with closing(sqlite3.connect(partial)) as target:
                target.execute("CREATE TABLE selected_request_keys ("
                               "request_key TEXT PRIMARY KEY,observation_count INTEGER NOT NULL)")
                target.execute("CREATE TABLE input_manifest ("
                               "key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
                reader = raw.execute(MATCHED_KEYS_SQL, (observations_cutoff,))
                required: dict[tuple[str, str, str, int], int] = {}
                while batch := reader.fetchmany(500):
                    for code, source_date, query, start, count in batch:
                        required[(str(code), str(source_date), str(query), int(start))] = int(count)
                matched: set[tuple[str, str, str, int]] = set()
                page_reader = raw.execute(
                    "SELECT DISTINCT request_key FROM source_pages "
                    "WHERE provider='naver_historical_search' AND rowid<=?",
                    (pages_cutoff,),
                )
                while batch := page_reader.fetchmany(500):
                    inserts = []
                    for (request_key,) in batch:
                        overlapping = [key for key in _page_observation_keys(str(request_key))
                                       if key in required]
                        if overlapping:
                            inserts.append((request_key, sum(required[key] for key in overlapping)))
                            matched.update(overlapping)
                    target.executemany("INSERT INTO selected_request_keys VALUES(?,?)", inserts)
                target.commit()
            raw.rollback()  # release the source snapshot before the page-index count
        with closing(sqlite3.connect(f"file:{raw_path.as_posix()}?mode=ro", uri=True)) as raw:
            raw.execute("ATTACH DATABASE ? AS selected",
                        (f"file:{partial.as_posix()}?mode=ro",))
            page_versions = int(raw.execute(
                "SELECT COUNT(*) FROM selected.selected_request_keys k "
                "JOIN source_pages p ON p.provider='naver_historical_search' "
                "AND p.request_key=k.request_key WHERE p.rowid<=?",
                (pages_cutoff,),
            ).fetchone()[0])
        with closing(sqlite3.connect(partial)) as target:
            request_keys, covered_observations = target.execute(
                "SELECT COUNT(*),COALESCE(SUM(observation_count),0) "
                "FROM selected_request_keys").fetchone()
            report = {"schema": "historical-news-page-keys/v2",
                      "raw_path": str(raw_path), "prepared_path": str(prepared_path),
                      "snapshot_at_utc": snapshot_at,
                      "source_pages_max_rowid": pages_cutoff,
                      "source_observations_max_rowid": observations_cutoff,
                      "request_keys": int(request_keys),
                      "required_observation_keys": len(required),
                      "matched_observations": sum(required.values()),
                      "covered_observation_keys": len(matched),
                      "missing_observation_keys": len(required) - len(matched),
                      "page_overlapping_observations": int(covered_observations),
                      "page_versions": page_versions,
                      "elapsed_seconds": round(time.monotonic() - started, 3)}
            target.execute("INSERT INTO input_manifest VALUES('report',?)",
                           (json.dumps(report, ensure_ascii=False, sort_keys=True),))
            target.commit()
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("page-key database integrity check failed")
        partial.replace(output)
        return report
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(select_keys(args.raw, args.prepared, args.output),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

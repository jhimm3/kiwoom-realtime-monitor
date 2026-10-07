"""Export the historical news reference closure without writing to PostgreSQL.

The resulting SQLite file is a PC build input, not a serving database.  It
keeps each PostgreSQL row (including IDs and accepted_sequence) as JSON and
marks rows reached only through references as support_only.  The finalizer
must validate and transform these rows before publishing an archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable


HISTORICAL_SCOPES = (
    "historical_backfill",
    "historical_market_backfill",
    "historical_news_pc_backfill",
    "historical_market_pc_backfill",
)
TABLE_KEYS = {
    "central_news_article_revisions": ("article_revision_id",),
    "central_news_body_revisions": ("body_revision_id",),
    "central_news_ai_revisions": ("analysis_revision_id",),
    "central_news_event_revisions": ("event_revision_id",),
    "central_news_event_membership_revisions": ("membership_revision_id",),
    "central_news_source_observations": ("observation_id",),
    "central_news_source_runs": ("run_revision_id",),
    "central_news_article_target_revisions": ("target_revision_id",),
    "central_news_jobs": ("job_key",),
    "central_documents": ("collection", "owner", "document_key"),
}
BATCH_SIZE = 500


def _chunks(values: Iterable[str], size: int = BATCH_SIZE) -> Iterable[list[str]]:
    ordered = sorted(set(values))
    for start in range(0, len(ordered), size):
        yield ordered[start:start + size]


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SeedExport:
    def __init__(self, source: Any, destination: sqlite3.Connection, *, max_rows: int) -> None:
        self.source = source
        self.destination = destination
        self.max_rows = max_rows
        self.count = 0
        self.query_number = 0
        self.owned_articles: set[str] = set()
        self.article_ids: set[str] = set()
        self.pending_articles: set[str] = set()
        self.processed_articles: set[str] = set()
        self.event_ids: set[str] = set()
        self.processed_events: set[str] = set()
        self.run_ids: set[str] = set()
        self.target_parent_ids: set[str] = set()
        self.processed_target_parents: set[str] = set()

    def query(self, table: str, column: str, values: Iterable[str],
              *, batch_size: int = BATCH_SIZE) -> Iterable[dict[str, Any]]:
        if table not in TABLE_KEYS:
            raise ValueError(f"unlisted table: {table}")
        allowed = {"collection_scope", "article_revision_id", "revision_of", "event_id",
                   "run_id", "target_revision_id", "document_key"}
        if column not in allowed:
            raise ValueError(f"unlisted filter: {column}")
        for batch in _chunks(values, batch_size):
            self.query_number += 1
            # Only the fixed identifiers above are interpolated.  Data is bound.
            predicate = f"{column} = ANY(%s)"
            parameters: tuple[Any, ...] = (batch,)
            if table == "central_documents":
                predicate += " AND collection = ANY(%s)"
                parameters += (["news_assessment", "news_original_publication"],)
            with self.source.cursor(name=f"news_seed_{self.query_number}") as cursor:
                cursor.execute(
                    f"SELECT row_to_json(t)::text FROM {table} AS t WHERE {predicate}",
                    parameters,
                )
                while rows := cursor.fetchmany(BATCH_SIZE):
                    for (raw,) in rows:
                        yield json.loads(raw)

    def save(self, table: str, row: dict[str, Any], *, owned: bool = False) -> bool:
        keys = TABLE_KEYS[table]
        if any(key not in row or row[key] is None for key in keys):
            raise ValueError(f"missing export key in {table}")
        identity = json.dumps([row[key] for key in keys], ensure_ascii=False, separators=(",", ":"))
        payload = _canonical(row)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        role = "historical" if owned else "support_only"
        previous = self.destination.execute(
            "SELECT payload_hash,role FROM seed_rows WHERE table_name=? AND row_key=?",
            (table, identity),
        ).fetchone()
        if previous:
            if previous[0] != digest:
                raise ValueError(f"same ID changed within snapshot: {table}:{identity}")
            if owned and previous[1] != role:
                self.destination.execute(
                    "UPDATE seed_rows SET role=? WHERE table_name=? AND row_key=?",
                    (role, table, identity),
                )
            return False
        if self.count >= self.max_rows:
            raise ValueError(f"seed closure exceeded max_rows={self.max_rows}")
        self.destination.execute(
            "INSERT INTO seed_rows(table_name,row_key,payload_json,payload_hash,role) VALUES(?,?,?,?,?)",
            (table, identity, payload, digest, role),
        )
        self.count += 1
        return True

    def article(self, row: dict[str, Any], *, owned: bool = False) -> None:
        article_id = str(row["article_revision_id"])
        self.save("central_news_article_revisions", row, owned=owned)
        if owned:
            self.owned_articles.add(article_id)
        if article_id not in self.article_ids:
            self.article_ids.add(article_id)
            self.pending_articles.add(article_id)
        if parent := row.get("revision_of"):
            if str(parent) not in self.article_ids:
                self.pending_articles.add(str(parent))

    def _article_children(self, ids: set[str]) -> None:
        for table in (
            "central_news_body_revisions", "central_news_ai_revisions",
            "central_news_article_target_revisions", "central_news_jobs",
            "central_news_source_observations", "central_news_event_revisions",
        ):
            for row in self.query(table, "article_revision_id", ids):
                self.save(table, row, owned=str(row["article_revision_id"]) in self.owned_articles)
                if table == "central_news_event_revisions":
                    self.event_ids.add(str(row["event_id"]))
                elif table == "central_news_source_observations":
                    self.run_ids.add(str(row["run_id"]))
                elif table == "central_news_article_target_revisions" and row.get("revision_of"):
                    self.target_parent_ids.add(str(row["revision_of"]))
        for row in self.query("central_documents", "document_key", ids):
            if row.get("collection") not in {"news_assessment", "news_original_publication"}:
                continue
            self.save("central_documents", row, owned=str(row["document_key"]) in self.owned_articles)

    def _events(self, ids: set[str]) -> None:
        for table in ("central_news_event_revisions", "central_news_event_membership_revisions"):
            for row in self.query(table, "event_id", ids):
                self.save(table, row, owned=str(row["article_revision_id"]) in self.owned_articles)
                article_id = str(row["article_revision_id"])
                if article_id not in self.article_ids:
                    self.pending_articles.add(article_id)
                if row.get("event_id"):
                    self.event_ids.add(str(row["event_id"]))

    def run(self) -> dict[str, Any]:
        for row in self.query("central_news_article_revisions", "collection_scope", HISTORICAL_SCOPES):
            self.article(row, owned=True)
        if not self.owned_articles:
            raise ValueError("no historical articles found; refusing an empty seed")

        while True:
            needed = self.pending_articles - self.article_ids
            self.pending_articles.clear()
            if needed:
                found: set[str] = set()
                for row in self.query("central_news_article_revisions", "article_revision_id", needed):
                    found.add(str(row["article_revision_id"]))
                    self.article(row)
                missing = needed - found
                if missing:
                    raise ValueError(f"article references missing: {len(missing)}")
            new_articles = self.article_ids - self.processed_articles
            if new_articles:
                self._article_children(new_articles)
                self.processed_articles.update(new_articles)
            new_events = self.event_ids - self.processed_events
            if new_events:
                self._events(new_events)
                self.processed_events.update(new_events)
            new_target_parents = self.target_parent_ids - self.processed_target_parents
            if new_target_parents:
                found_targets: set[str] = set()
                for row in self.query("central_news_article_target_revisions", "target_revision_id", new_target_parents):
                    found_targets.add(str(row["target_revision_id"]))
                    self.save("central_news_article_target_revisions", row)
                    if str(row["article_revision_id"]) not in self.article_ids:
                        self.pending_articles.add(str(row["article_revision_id"]))
                    if row.get("revision_of"):
                        self.target_parent_ids.add(str(row["revision_of"]))
                missing = new_target_parents - found_targets
                if missing:
                    raise ValueError(f"target revision references missing: {len(missing)}")
                self.processed_target_parents.update(new_target_parents)
            if not (self.pending_articles or self.event_ids - self.processed_events
                    or self.target_parent_ids - self.processed_target_parents
                    or self.article_ids - self.processed_articles):
                break

        # run_id has no dedicated index in the current schema.  A single
        # bounded-result scan avoids one full scan per 500 observed run IDs.
        for row in self.query("central_news_source_runs", "run_id", self.run_ids,
                              batch_size=max(1, len(self.run_ids))):
            self.save("central_news_source_runs", row)
        captured_runs = {
            str(json.loads(row[0])["run_id"]) for row in self.destination.execute(
                "SELECT payload_json FROM seed_rows WHERE table_name='central_news_source_runs'")
        }
        missing_runs = self.run_ids - captured_runs
        if missing_runs:
            raise ValueError(f"source run references missing: {len(missing_runs)}")

        references = {
            "central_news_article_revisions": "article_revision_id",
            "central_news_body_revisions": "body_revision_id",
            "central_news_event_revisions": "event_revision_id",
            "central_news_event_membership_revisions": "membership_revision_id",
            "central_news_article_target_revisions": "target_revision_id",
            "central_news_ai_revisions": "analysis_revision_id",
        }
        known = {
            field: {json.loads(payload)[field] for (payload,) in self.destination.execute(
                "SELECT payload_json FROM seed_rows WHERE table_name=?", (table,))}
            for table, field in references.items()
        }
        for table, fields in (
            ("central_news_body_revisions", (("article_revision_id", "article_revision_id"),)),
            ("central_news_ai_revisions", (("article_revision_id", "article_revision_id"),
                                           ("body_revision_id", "body_revision_id"))),
            ("central_news_event_revisions", (("article_revision_id", "article_revision_id"),
                                              ("body_revision_id", "body_revision_id"),
                                              ("revision_of", "event_revision_id"))),
            ("central_news_event_membership_revisions", (("article_revision_id", "article_revision_id"),
                                                         ("body_revision_id", "body_revision_id"),
                                                         ("event_revision_id", "event_revision_id"),
                                                         ("revision_of", "membership_revision_id"))),
            ("central_news_source_observations", (("article_revision_id", "article_revision_id"),)),
            ("central_news_article_target_revisions", (("article_revision_id", "article_revision_id"),
                                                       ("revision_of", "target_revision_id"))),
            ("central_news_article_revisions", (("revision_of", "article_revision_id"),)),
        ):
            for (payload,) in self.destination.execute(
                "SELECT payload_json FROM seed_rows WHERE table_name=?", (table,)
            ):
                row = json.loads(payload)
                for column, target in fields:
                    ref = row.get(column)
                    if ref and ref not in known[target]:
                        raise ValueError(f"unresolved {table}.{column} reference")
        for (payload,) in self.destination.execute(
            "SELECT payload_json FROM seed_rows WHERE table_name='central_news_jobs'"
        ):
            job = json.loads(payload)
            if job["article_revision_id"] not in known["article_revision_id"]:
                raise ValueError("unresolved news job article reference")
            if job.get("state") != "COMPLETED" or not job.get("output_ref"):
                continue
            ref = str(job["output_ref"])
            if job.get("stage") == "RULE" and ref.startswith("ignored:"):
                ref = ref.removeprefix("ignored:")
                target = "body_revision_id"
            else:
                target = {"BODY": "body_revision_id", "RULE": "event_revision_id",
                          "AI": "analysis_revision_id"}.get(str(job.get("stage")))
            if target and ref not in known[target]:
                raise ValueError(f"unresolved completed {job['stage']} job output")

        counts = dict(self.destination.execute(
            "SELECT table_name,COUNT(*) FROM seed_rows GROUP BY table_name ORDER BY table_name"
        ))
        roles = dict(self.destination.execute(
            "SELECT role,COUNT(*) FROM seed_rows GROUP BY role ORDER BY role"
        ))
        return {"owned_articles": len(self.owned_articles), "support_articles": len(self.article_ids)
                - len(self.owned_articles), "rows": self.count, "tables": counts, "roles": roles}


def export_seed(database_url: str, output: Path, *, max_rows: int = 1_000_000) -> dict[str, Any]:
    import psycopg
    from psycopg import IsolationLevel

    output = output.resolve()
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise FileExistsError("output or partial file already exists")
    if not output.parent.is_dir():
        raise FileNotFoundError("output parent directory does not exist")
    destination = sqlite3.connect(partial)
    try:
        destination.execute("CREATE TABLE seed_rows (table_name TEXT NOT NULL,row_key TEXT NOT NULL,"
                            "payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,role TEXT NOT NULL,"
                            "PRIMARY KEY(table_name,row_key))")
        destination.execute("CREATE TABLE seed_manifest (key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
        with psycopg.connect(database_url, connect_timeout=10,
                             options="-c statement_timeout=30000") as source:
            source.read_only = True
            source.isolation_level = IsolationLevel.REPEATABLE_READ
            with source.cursor() as cursor:
                cursor.execute("SELECT current_database(),current_setting('server_version_num'),"
                               "transaction_timestamp()::text")
                database_name, server_version, snapshot_started_at = cursor.fetchone()
            exporter = SeedExport(source, destination, max_rows=max_rows)
            report = exporter.run()
            report.update({"schema": "historical-news-seed/v1", "database": database_name,
                           "postgres_server_version": server_version,
                           "snapshot_started_at": snapshot_started_at})
        destination.execute("INSERT INTO seed_manifest VALUES(?,?)",
                            ("report", json.dumps(report, ensure_ascii=False, sort_keys=True)))
        destination.commit()
        integrity = destination.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"seed database integrity failed: {integrity}")
        destination.close()
        partial.replace(output)
        return report
    except BaseException:
        destination.close()
        if partial.exists():
            partial.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database-url-env", default="KIWOOM_SERVER_DATABASE_URL")
    parser.add_argument("--max-rows", type=int, default=1_000_000)
    args = parser.parse_args()
    if args.max_rows < 1:
        parser.error("--max-rows must be positive")
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        parser.error("database URL environment variable is missing")
    print(json.dumps(export_seed(database_url, args.output, max_rows=args.max_rows),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

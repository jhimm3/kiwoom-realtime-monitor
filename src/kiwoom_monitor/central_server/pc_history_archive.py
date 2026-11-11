"""After-close sweep of saved PC history using the existing durable archive transport."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from kiwoom_monitor.domain.observation_archive import (
    MAX_OBSERVATION_ARCHIVE_BYTES, ObservationArchiveBatch, archive_json, archive_sha256,
)
from kiwoom_monitor.domain.pc_history_archive import CENTRAL_TABLES, MARKET_DOCUMENTS, MARKET_SNAPSHOTS, RESEARCH_FILE_SOURCE
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.domain.time_basis import KST, as_kst
from kiwoom_monitor.infrastructure.observation_archive_delivery import archive_connection, deliver_observation_archive
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes

from .database_pc_history import HistorySource, history_sources, read_pc_history_file_tables
from .pc_research_files import archived_research_rows, discover_research_sources


class PCHistoryArchive:
    def __init__(self, store, directory: Path, journal: Path, connection: Path, stream: str,
                 *, compact_before: str = "", news_enabled: bool = False) -> None:
        self._store = store
        self._directory = directory
        self._journal = journal
        self._source_paths = {"journal": journal}
        self._file_tables: dict[str, set[str]] = {}
        self._sources = history_sources()
        self._connection = connection
        self._stream = stream
        self._compact_before = compact_before
        self._news_enabled = news_enabled
        self._cut = ""
        self._target = ""
        self._excluded_products: frozenset[str] = frozenset()
        self._source_index = self._rowid = 0
        self._counts: dict[str, dict[str, int]] = {}
        self._research = None
        self._research_row = None
        self._research_files: list[dict] = []
        self._research_issues: list[str] = []
        self._store.set_pc_history_reader(self.read)

    def schedule(self, completed_at: datetime, excluded_codes: frozenset[str]) -> None:
        cut = as_kst(completed_at)
        write_immutable_bytes(self._directory / f"{int(cut.timestamp() * 1_000_000)}.cut",
                              archive_json({"cut": cut.isoformat(), "excluded_products": sorted(excluded_codes)}))

    def close(self) -> None:
        if self._research is not None:
            self._research.close()
            self._research = None
        self._research_row = None
        self._cut = ""

    def _counts_for(self, source: str) -> dict[str, int]:
        return self._counts.setdefault(source, dict(
            scanned=0, already_verified=0, received=0, compacted=0, oversized=0, excluded_products=0))

    def _cleanup_cut(self) -> str:
        if not self._compact_before:
            return ""
        # The existing chart path can use 30 calendar days to find five recent trading days.
        recent = datetime.fromisoformat(self._cut).date() - timedelta(days=30)
        return min(self._compact_before, recent.isoformat())

    def step(self, write_batch) -> str:
        cuts = sorted(self._directory.glob("*.cut"))
        if not cuts:
            return "idle"
        if cuts[-1].with_suffix(".report.json").is_file():
            for path in cuts:
                path.unlink()
            return "idle"
        schedule = json.loads(cuts[-1].read_bytes())
        cut = schedule["cut"]
        if self._cut != cut:
            self.close()
            target, _ = archive_connection(self._connection, self._stream)
            self._store.require_pc_history_target(target)
            self._target = target
            self._cut = cut
            self._excluded_products = frozenset(schedule["excluded_products"])
            self._source_paths = self._load_source_paths()
            self._source_index = self._rowid = 0
            self._counts = {}
        sources = self._sources
        pending = next(iter(self._directory.glob("*.batch")), None)
        if pending is not None:
            return self._deliver(pending, sources)
        if self._source_index == len(sources):
            self._write_report(cuts[-1].with_suffix(".report.json"), sources)
            for path in cuts:
                path.unlink()
            return "idle"

        source = sources[self._source_index]
        counts = self._counts_for(source.name)
        if source.name == RESEARCH_FILE_SOURCE:
            return self._archive_research_step(write_batch)
        page = self._store.scan_pc_history_page(source, self._source_paths, self._rowid,
                                                compact_before=self._cleanup_cut(), target=self._target)
        if not page:
            self._source_index += 1
            self._rowid = 0
            return "progress"
        lines, size, scanned, already, oversized, excluded = [], 0, 0, 0, 0, 0
        last = self._rowid
        for row, rowid, needs_transfer in page:
            if source.name in {
                "central_minute_bars", "central_daily_bars", "central_five_minute_bars",
                "central_second_trade_bars", "snapshot/program_flow",
            } and normalize_stock_code(row["subject"]) in self._excluded_products:
                excluded += 1
                scanned += 1
                last = rowid
                continue
            if not needs_transfer:
                scanned += 1
                already += 1
                last = rowid
                continue
            line = archive_json(row)
            wire_size = len(archive_json(line.decode() + "\n"))
            if wire_size > MAX_OBSERVATION_ARCHIVE_BYTES - 8192:
                scanned += 1
                oversized += 1
                last = rowid
                continue
            if size + wire_size > MAX_OBSERVATION_ARCHIVE_BYTES - 8192:
                break
            lines.append(line)
            size += wire_size
            scanned += 1
            last = rowid
        if lines:
            self._spool(write_batch, source.name, lines)
        self._rowid = last
        counts["scanned"] += scanned
        counts["already_verified"] += already
        counts["oversized"] += oversized
        counts["excluded_products"] += excluded
        return "progress"

    def _spool(self, write_batch, source: str, lines: list[bytes]) -> None:
        start = datetime.combine(datetime.fromisoformat(self._cut).date(), time(), KST)
        batch_id = str(uuid4())
        write_batch(self._directory, {
            "dataset_id": batch_id, "fixed_watermark": batch_id, "schema_version": 1,
            "kinds": ["pc_history"], "subject": source,
            "captured_range": {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
            "exported_at": self._cut, "revision_count": len(lines),
            "revision_ids_hash": archive_sha256("\n".join(map(archive_sha256, lines)).encode()),
        }, lines)

    def _archive_research_step(self, write_batch) -> str:
        if self._research_row is None and self._research is not None:
            self._research_row = next(self._research, None)
        if self._research_row is None:
            self._source_index += 1
            return "progress"
        source, row = self._research_row
        counts = self._counts_for(source)
        if self._store.pc_history_record_verified(source, row, self._target):
            counts["already_verified"] += 1
        else:
            self._spool(write_batch, source, [archive_json(row)])
        counts["scanned"] += 1
        self._research_row = None
        return "progress"

    def _deliver(self, path: Path, sources) -> str:
        result = deliver_observation_archive(path, self._connection)
        if result["status"] != "delivered":
            return "pending"
        value = ObservationArchiveBatch.parse(path.read_bytes())
        document = value.document()
        source_name = json.loads(document["manifest"])["subject"]
        source = next((source for source in sources if source.name == source_name), None)
        if source is None and source_name.startswith(("research/", "desktop_market/")):
            # An already spooled detached source can disappear before its retry.
            # Only central sources can compact, so these receipts need no source file.
            source = HistorySource(source_name, "", file_role="detached")
        if source is None:
            raise ValueError("pending PC history source is unavailable")
        rows = [json.loads(line) for line in document["observations"].split("\n") if line]
        ack_path = path.with_name(path.name + ".ack.json")
        target = json.loads(ack_path.read_bytes())["target"]
        url, client = archive_connection(self._connection, self._stream)
        if (url != target or url != self._target
                or value.verify_receipt(result["receipt"]) != json.loads(ack_path.read_bytes())["receipt"]):
            raise ValueError("PC history archive receipt or destination changed")
        remote = client.load_pc_archive_history(self._stream, source_name, batch_id=value.batch_id, limit=len(rows))
        expected = {row["key"]: archive_sha256(archive_json(row)) for row in rows}
        actual = {item["record"]["key"]: archive_sha256(archive_json(item["record"])) for item in remote
                  if item["batch_id"] == value.batch_id}
        if expected != actual or len(remote) != len(rows):
            raise ValueError("PC history readback does not match the accepted source records")
        removed = self._store.acknowledge_pc_history(source, rows, target=url, batch_id=value.batch_id,
                                                     compact_before=self._cleanup_cut())
        counts = self._counts_for(source_name)
        counts["received"] += len(rows)
        counts["compacted"] += removed
        path.unlink()
        ack_path.unlink(missing_ok=True)
        path.with_name(path.name + ".target.json").unlink(missing_ok=True)
        return "progress"

    def read(self, source: str, *, expected_targets: set[str], **filters) -> list[dict]:
        url, client = archive_connection(self._connection, self._stream)
        if expected_targets != {url}:
            raise ValueError("PC history is pinned to a different archive destination")
        return [row["record"] for row in client.load_pc_archive_history(self._stream, source, **filters)]

    def _load_source_paths(self) -> dict[str, Path]:
        self._research_files = []
        self._research_issues = []
        paths = {"journal": self._journal, "desktop_news": self._journal.with_name("news.sqlite3")}
        research = discover_research_sources(self._journal.parent.resolve(), self._research_issues)
        owned = {}
        for family, databases in (("research", research.runs),
                                  ("desktop_market", research.desktop_databases)):
            for database in databases:
                namespace = archive_sha256(str(database).encode())
                role = f"{family}:{namespace}"
                paths[role] = database
                if family == "research":
                    owned[role] = research.runs[database]
        self._file_tables = {}
        for role, database in paths.items():
            try:
                self._file_tables[role] = read_pc_history_file_tables(database)
                if ":" in role and not self._file_tables[role]:
                    self._research_issues.append(f"archive_database_unavailable:{database}")
            except (OSError, sqlite3.Error):
                self._research_issues.append(f"archive_database_unreadable:{database}")
        self._research = archived_research_rows(
            research, self._directory / "verified_research_files", f"{self._target}/{self._stream}",
            self._research_files, self._research_issues,
        )
        self._sources = history_sources(self._file_tables, owned)
        return paths

    def _write_report(self, path: Path, sources) -> None:
        tables, snapshots, documents = self._store.pc_history_inventory()
        owned = CENTRAL_TABLES | {"central_dataset_snapshots", "central_documents"}
        operational = {name for name in tables - owned if name.startswith(
            ("central_account_", "central_credential_", "central_execution_", "central_shadow_monitor_"))}
        housekeeping = tables & {
            "pc_history_archive_receipts", "central_schema_migrations", "central_api_query_cache",
            "central_realtime_latest", "central_minute_bar_operations",
        }
        maintained_pc = sorted(operational | housekeeping)
        delegated_news = sorted(name for name in tables - owned if name.startswith("central_news_"))
        excluded_documents = sorted(name for name in documents - MARKET_DOCUMENTS if name.startswith(
            ("credential_", "execution_", "account_", "server_operational_", "news_", "morning_market_brief_settings")))
        unknown = {
            "tables": sorted(tables - owned - set(maintained_pc) - set(delegated_news)),
            "snapshot_kinds": sorted(snapshots - MARKET_SNAPSHOTS - {
                "top20_statistics_day", "top20_statistics_display_day",
            }),
            "collections": sorted(documents - MARKET_DOCUMENTS - set(excluded_documents)),
            "separate_storage": list(self._research_issues),
        }
        if not self._journal.is_file():
            unknown["separate_storage"].append("journal_database_unavailable")
        file_tables = self._file_tables
        file_only = {
            "journal": {"journal_schema_migrations", "journal_settings", "journal_sync_states", "journal_legacy_imports",
                        "journal_bar_backfill", "journal_enrichment_tasks"},
            "desktop_news": {"news_schema_migrations", "stock_news_sync", "news_ai_requests"},
            "research": {"research_schema_migrations"},
            "desktop_market": {
                "schema_migrations", "settings", "column_settings", "central_setting_versions",
                "central_column_setting_versions", "minute_history_sync_log", "daily_bar_sync_log",
                "top20_statistics_daily_cache", "legacy_news_transfer_migrations",
            },
        }
        unknown["separate_storage"].extend(
            f"{role}/{table}" for role, names in file_tables.items()
            for table in sorted(names - file_only.get(role.split(":", 1)[0], set()) -
                                {source.table for source in sources if source.file_role == role}))
        missing = sorted(source.name for source in sources if source.file_role != "artifact" and (
            source.table not in file_tables[source.file_role] if source.file_role in file_tables
            else not source.file_role and source.table not in tables))
        counts = {source.name: self._counts_for(source.name) for source in sources}
        unmatched = sorted(name for name, row in counts.items()
                           if row["scanned"] != (row["already_verified"] + row["received"]
                                                  + row["oversized"] + row["excluded_products"]))
        news_directory = self._directory.parent / "news"
        news_pending = bool(next(news_directory.glob("*.cut"), None) or next(news_directory.glob("*.batch"), None))
        report = {
            "cut": self._cut, "sources": counts, "unclassified": unknown,
            "pc_only_tables": maintained_pc, "other_document_owners": excluded_documents,
            "missing_source_tables": missing, "count_differences": unmatched,
            "news": {"enabled": self._news_enabled, "pending": news_pending, "separate_tables": delegated_news},
            "research_files": self._research_files,
            "separate_databases": {role: str(db) for role, db in self._source_paths.items() if ":" in role},
            "research_unique_bytes": sum({file["sha256"]: file["bytes"] for file in self._research_files}.values()),
            "status": ("incomplete" if news_pending or missing or unmatched or any(unknown.values())
                       or any(row["oversized"] for row in counts.values())
                       else "verified"),
        }
        write_immutable_bytes(path, archive_json(report))

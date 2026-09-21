"""Build immutable, explicitly post-hoc research inputs from historical sources."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


CONTRACT_VERSION = "historical_reconstruction/v1"
POPULATION_ID = "posthoc_candidate_days/v1"


@dataclass(frozen=True)
class HistoricalReconstructionDataset:
    manifest: dict[str, Any]
    records: tuple[dict[str, Any], ...]

    def records_of_kind(self, kind: str) -> tuple[dict[str, Any], ...]:
        return tuple(row for row in self.records if row.get("kind") == kind)


def export_historical_reconstruction(
    candidate_database: Path,
    intelligence_database: Path,
    output: Path,
    *,
    dates: Iterable[str],
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze selected candidate days with the bars and news currently collected.

    Candidate rows are a post-session, post-hoc population.  Their availability is
    therefore the export time, never the historical session time.  Bar and news
    rows retain the time at which the collector actually observed them.
    """
    selected_dates = tuple(sorted({_iso_date(value) for value in dates}))
    if not selected_dates:
        raise ValueError("at least one reconstruction date is required")
    output = Path(output)
    if output.exists():
        raise ValueError("historical reconstruction export is immutable and cannot be overwritten")
    created = created_at or datetime.now(UTC)
    if created.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    created_text = created.astimezone(UTC).isoformat()

    candidate_path = Path(candidate_database).expanduser().resolve(strict=True)
    intelligence_path = Path(intelligence_database).expanduser().resolve(strict=True)
    records: list[dict[str, Any]] = []
    resolution_counts = {"60": 0, "300": 0, "unavailable": 0}

    with closing(_open_readonly(candidate_path, immutable=True)) as candidates, closing(
        _open_readonly(intelligence_path, immutable=False)
    ) as intelligence:
        candidates.row_factory = sqlite3.Row
        intelligence.row_factory = sqlite3.Row
        candidates.execute("BEGIN")
        intelligence.execute("BEGIN")
        for selected_date in selected_dates:
            candidate_rows = candidates.execute(
                """
                SELECT c.dt, c.code, COALESCE(s.name, '') AS name, c.score,
                       COALESCE(c.reasons, '') AS reasons, c.rank_value, c.rank_gain,
                       c.rank_high, c.rank_volume_ratio, c.gain_pct, c.high_pct,
                       c.volume_ratio, c.trading_value
                FROM candidate_days AS c
                LEFT JOIN stocks AS s ON s.code = c.code
                WHERE c.dt = ?
                ORDER BY c.score DESC, c.code
                """,
                (selected_date,),
            ).fetchall()
            for row in candidate_rows:
                code = str(row["code"])
                candidate_payload = {
                    "population_id": POPULATION_ID,
                    "selection_timing": "post_session_posthoc",
                    "not_contemporaneous_top20": True,
                    "date": selected_date,
                    "code": code,
                    "name": str(row["name"] or ""),
                    "score": float(row["score"]),
                    "reasons": str(row["reasons"] or ""),
                    "rank_value": row["rank_value"],
                    "rank_gain": row["rank_gain"],
                    "rank_high": row["rank_high"],
                    "rank_volume_ratio": row["rank_volume_ratio"],
                    "gain_pct": row["gain_pct"],
                    "high_pct": row["high_pct"],
                    "volume_ratio": row["volume_ratio"],
                    "trading_value": row["trading_value"],
                }
                records.append(_record(
                    "historical_candidate", f"{code}:{selected_date}",
                    selected_date, created_text, candidate_payload,
                ))

                interval = _preferred_interval(intelligence, code, selected_date)
                resolution_counts[str(interval) if interval else "unavailable"] += 1
                if interval:
                    bar_rows = intelligence.execute(
                        """
                        SELECT provider, code, venue, session_scope, interval_seconds,
                               adjustment_mode, bar_time, bar_time_semantics,
                               raw_date, raw_time, open, high, low, close, volume,
                               trading_value, observed_at, available_at
                        FROM market_bars
                        WHERE code = ? AND substr(bar_time, 1, 10) = ?
                          AND interval_seconds = ?
                        ORDER BY bar_time, provider, venue, adjustment_mode
                        """,
                        (code, selected_date, interval),
                    ).fetchall()
                    for bar in bar_rows:
                        payload = {key: bar[key] for key in bar.keys()}
                        payload["market"] = "KRX" if bar["venue"] in {"K", "KRX"} else str(bar["venue"])
                        payload["trading_value_unit"] = "won"
                        records.append(_record(
                            "historical_market_bar", f"{code}:{bar['bar_time']}:{interval}",
                            str(bar["bar_time"]), str(bar["available_at"]), payload,
                        ))

            news_rows = intelligence.execute(
                """
                SELECT o.provider, o.office_id, o.article_id, o.code, o.source_date,
                       o.query_text, o.start, o.position, o.observed_at,
                       a.published_at, a.published_precision, a.published_at_source,
                       a.office_name, a.title, a.summary, a.article_url,
                       a.original_url, a.portal_url, a.publication_source_url,
                       a.article_fetch_status, a.article_fetched_at,
                       a.training_eligible, a.training_exclusion_reason,
                       a.first_observed_at, a.last_observed_at
                FROM news_search_observations AS o
                JOIN news_articles AS a
                  ON a.provider=o.provider AND a.office_id=o.office_id
                 AND a.article_id=o.article_id
                WHERE o.source_date = ?
                ORDER BY o.code, o.provider, o.office_id, o.article_id, o.query_text
                """,
                (selected_date,),
            ).fetchall()
            for news in news_rows:
                payload = {key: news[key] for key in news.keys()}
                payload["publication_time_verified"] = bool(
                    news["training_eligible"] and news["published_at"]
                )
                effective = str(news["published_at"] or news["source_date"])
                available = str(news["first_observed_at"] or news["observed_at"])
                records.append(_record(
                    "historical_news_evidence",
                    f"{news['code']}:{news['provider']}:{news['office_id']}:{news['article_id']}:{news['query_text']}",
                    effective, available, payload,
                ))

        records.sort(key=lambda row: (
            str(row["effective_at"]), str(row["kind"]), str(row["subject"]),
            str(row["revision_id"]),
        ))
        for ordinal, row in enumerate(records, start=1):
            row["ordinal"] = ordinal

        source_snapshot = {
            "candidate_database": _source_descriptor(candidate_path),
            "intelligence_database": _source_descriptor(intelligence_path),
            "candidate_rows": sum(row["kind"] == "historical_candidate" for row in records),
            "market_bar_rows": sum(row["kind"] == "historical_market_bar" for row in records),
            "news_evidence_rows": sum(row["kind"] == "historical_news_evidence" for row in records),
        }
        candidates.rollback()
        intelligence.rollback()

    encoded = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in records
    )
    revision_hash = hashlib.sha256(
        "\n".join(str(row["revision_id"]) for row in records).encode("utf-8")
    ).hexdigest()
    identity = {
        "contract_version": CONTRACT_VERSION,
        "population_id": POPULATION_ID,
        "selected_dates": selected_dates,
        "revision_ids_hash": revision_hash,
    }
    news_records = [row for row in records if row["kind"] == "historical_news_evidence"]
    news_status_counts = Counter(
        str(row["payload"].get("article_fetch_status", "")) for row in news_records
    )
    manifest = {
        "schema_version": 1,
        "dataset_kind": "historical_reconstruction",
        "contract_version": CONTRACT_VERSION,
        "dataset_id": "historical-reconstruction-" + _hash_document(identity),
        "created_at": created_text,
        "selected_dates": list(selected_dates),
        "population": {
            "id": POPULATION_ID,
            "selection_timing": "post_session_posthoc",
            "not_contemporaneous_top20": True,
            "generalization_scope": "selected_candidate_population_only",
        },
        "availability_policy": {
            "candidate": "export_time_because_original_generation_time_is_unavailable",
            "market_bar": "collector_observed_at",
            "news": "collector_first_observed_at",
            "historical_effective_time_is_not_availability": True,
        },
        "consumer_contract": {
            "supported_mode": "historical_reconstruction",
            "strict_top20_replay_supported": False,
            "five_minute_bars_are_not_expanded_to_one_minute": True,
        },
        "resolution_by_candidate_day": resolution_counts,
        "news_quality": {
            "relation_count": len(news_records),
            "publication_time_verified_relations": sum(
                bool(row["payload"].get("publication_time_verified")) for row in news_records
            ),
            "article_fetch_status_counts": dict(sorted(news_status_counts.items())),
        },
        "source_snapshot": source_snapshot,
        "record_count": len(records),
        "revision_ids_hash": revision_hash,
        "records_file": "records.jsonl",
        "records_file_hash": hashlib.sha256(encoded).hexdigest(),
    }
    _write_immutable_export(output, encoded, manifest)
    return manifest


def load_historical_reconstruction(path: Path) -> HistoricalReconstructionDataset:
    root = Path(path)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical reconstruction manifest cannot be read") from exc
    if not isinstance(manifest, dict) or manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported historical reconstruction contract")
    population = manifest.get("population")
    consumer = manifest.get("consumer_contract")
    if not isinstance(population, dict) or population.get("not_contemporaneous_top20") is not True:
        raise ValueError("historical reconstruction population provenance is missing")
    if not isinstance(consumer, dict) or consumer.get("strict_top20_replay_supported") is not False:
        raise ValueError("historical reconstruction consumer boundary is missing")
    file_name = str(manifest.get("records_file", ""))
    if not file_name or Path(file_name).name != file_name:
        raise ValueError("historical reconstruction records_file must be local")
    try:
        encoded = (root / file_name).read_bytes()
    except OSError as exc:
        raise ValueError("historical reconstruction records cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != manifest.get("records_file_hash"):
        raise ValueError("historical reconstruction records hash does not match manifest")
    records: list[dict[str, Any]] = []
    revision_ids: list[str] = []
    for ordinal, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"historical reconstruction record {ordinal} is invalid") from exc
        if not isinstance(row, dict) or row.get("ordinal") != ordinal or not row.get("revision_id"):
            raise ValueError("historical reconstruction ordinal/revision contract is invalid")
        records.append(row)
        revision_ids.append(str(row["revision_id"]))
    actual_revision_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    if len(records) != int(manifest.get("record_count", -1)):
        raise ValueError("historical reconstruction record count does not match manifest")
    if actual_revision_hash != manifest.get("revision_ids_hash"):
        raise ValueError("historical reconstruction revision hash does not match manifest")
    return HistoricalReconstructionDataset(dict(manifest), tuple(records))


def _preferred_interval(connection: sqlite3.Connection, code: str, selected_date: str) -> int:
    values = {
        int(row[0]) for row in connection.execute(
            "SELECT DISTINCT interval_seconds FROM market_bars "
            "WHERE code=? AND substr(bar_time,1,10)=? AND interval_seconds IN (60,300)",
            (code, selected_date),
        )
    }
    return 60 if 60 in values else 300 if 300 in values else 0


def _record(kind: str, subject: str, effective_at: str, available_at: str,
            payload: Mapping[str, Any]) -> dict[str, Any]:
    scientific = {
        "kind": kind,
        "subject": subject,
        "effective_at": effective_at,
        "available_at": available_at,
        "payload": dict(payload),
    }
    return {"revision_id": _hash_document(scientific), **scientific}


def _open_readonly(path: Path, *, immutable: bool) -> sqlite3.Connection:
    suffix = "&immutable=1" if immutable else ""
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro{suffix}", uri=True, timeout=30,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def _source_descriptor(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _write_immutable_export(output: Path, encoded: bytes, manifest: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        (temporary / "records.jsonl").write_bytes(encoded)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8",
        )
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _hash_document(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iso_date(value: str) -> str:
    return date.fromisoformat(str(value)).isoformat()

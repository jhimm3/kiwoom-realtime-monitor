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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.market_session_schedule import research_session_profile_document


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
    outcome_end_date: str = "",
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
    outcome_end = _iso_date(outcome_end_date) if outcome_end_date else selected_dates[-1]
    if outcome_end < selected_dates[-1]:
        raise ValueError("outcome_end_date must not precede a selected date")
    output = Path(output)
    if output.exists():
        raise ValueError("historical reconstruction export is immutable and cannot be overwritten")
    created = created_at or datetime.now(UTC)
    if created.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    created_text = created.astimezone(UTC).isoformat()
    case_outcome_ends: dict[str, str] = {}

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
            next_session = candidates.execute(
                "SELECT MIN(dt) FROM candidate_days WHERE dt > ? AND dt <= ?",
                (selected_date, outcome_end),
            ).fetchone()[0]
            case_outcome_ends[selected_date] = str(next_session or outcome_end)
        for selected_date in selected_dates:
            case_outcome_end = case_outcome_ends[selected_date]
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

                source_job_state = _market_job_state(intelligence, code)
                range_start = f"{selected_date}T00:00:00"
                range_end = f"{(date.fromisoformat(case_outcome_end) + timedelta(days=1)).isoformat()}T00:00:00"
                case_dates = [str(value[0]) for value in intelligence.execute(
                    "SELECT DISTINCT substr(bar_time,1,10) FROM market_bars "
                    "WHERE provider='daishin_creon' AND code=? "
                    "AND venue IN ('K','KRX') AND session_scope='regular' "
                    "AND adjustment_mode='raw' AND bar_time>=? AND bar_time<? ORDER BY 1",
                    (code, range_start, range_end),
                )]
                selected_interval = _preferred_interval(intelligence, code, selected_date)
                resolution_counts[str(selected_interval) if selected_interval else "unavailable"] += 1
                for bar_date in case_dates:
                    interval = _preferred_interval(intelligence, code, bar_date)
                    bar_rows = intelligence.execute(
                        """
                        SELECT provider, code, venue, session_scope, interval_seconds,
                               adjustment_mode, bar_time, bar_time_semantics,
                               raw_date, raw_time, open, high, low, close, volume,
                               trading_value, observed_at, available_at
                        FROM market_bars
                        WHERE provider='daishin_creon' AND code = ?
                          AND venue IN ('K','KRX') AND session_scope='regular'
                          AND adjustment_mode='raw'
                          AND bar_time >= ? AND bar_time < ?
                          AND interval_seconds = ?
                        ORDER BY bar_time, provider, venue, adjustment_mode
                        """,
                        (code, f"{bar_date}T00:00:00",
                         f"{(date.fromisoformat(bar_date) + timedelta(days=1)).isoformat()}T00:00:00",
                         interval),
                    ).fetchall()
                    for bar in bar_rows:
                        payload = {key: bar[key] for key in bar.keys()}
                        payload["market"] = "KRX" if bar["venue"] in {"K", "KRX"} else str(bar["venue"])
                        payload["trading_value_unit"] = "won"
                        payload["case_date"] = selected_date
                        payload["phase"] = "selection_input" if bar_date == selected_date else "outcome"
                        payload["source_job_state"] = source_job_state
                        records.append(_record(
                            "historical_market_bar",
                            f"{selected_date}:{code}:{bar['bar_time']}:{interval}",
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
        "temporal_split": {
            "selection_dates": list(selected_dates),
            "outcome_end_date": outcome_end,
            "case_windows": [
                {
                    "selection_date": selected_date,
                    "outcome_start_policy": "dates_strictly_after_selection_date",
                    "outcome_end_date": case_outcome_ends[selected_date],
                }
                for selected_date in selected_dates
            ],
            "selection_available_after_session": True,
            "strategy_entry_phase": "bar dates strictly after each selection date",
        },
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


def adapt_historical_reconstruction_for_research(
    dataset: HistoricalReconstructionDataset,
    *,
    selected_date: str | None = None,
    selected_dates: Iterable[str] = (),
) -> "FrozenResearchDataset":
    """Create a one- or multi-case strategy input without changing source availability.

    The derived replay clock is the historical bar close.  Every minute observation
    also carries ``source_available_at`` so it cannot be mistaken for a record that
    was actually available at that historical time.
    """
    from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset

    requested = tuple(_iso_date(value) for value in selected_dates)
    if selected_date is not None:
        if requested:
            raise ValueError("use selected_date or selected_dates, not both")
        requested = (_iso_date(selected_date),)
    requested = tuple(sorted(set(requested)))
    if not requested:
        raise ValueError("at least one selected date is required")
    source_dates = tuple(str(value) for value in dataset.manifest.get("selected_dates", []))
    if any(value not in source_dates for value in requested):
        raise ValueError("selected date is not present in the reconstruction dataset")

    observations: list[dict[str, Any]] = []
    included_cases: list[dict[str, Any]] = []
    excluded_cases: list[dict[str, str]] = []
    for case_date in requested:
        candidates = tuple(
            row for row in dataset.records_of_kind("historical_candidate")
            if row.get("payload", {}).get("date") == case_date
        )
        if not candidates:
            raise ValueError("historical reconstruction case has no candidates")
        codes = tuple(dict.fromkeys(
            str(row["payload"].get("code", "")) for row in candidates
            if str(row["payload"].get("code", ""))
        ))
        source_bars = tuple(
            row for row in dataset.records_of_kind("historical_market_bar")
            if row.get("payload", {}).get("case_date") == case_date
            and row.get("payload", {}).get("phase") == "outcome"
            and int(row.get("payload", {}).get("interval_seconds", 0)) == 60
            and row.get("payload", {}).get("source_job_state") == "complete"
        )
        if not source_bars:
            excluded_cases.append({
                "selection_date": case_date,
                "reason": "complete_one_minute_outcome_bars_missing",
            })
            continue
        first_start = min(
            (_aware_datetime(row["payload"]["bar_time"]) - timedelta(minutes=1)).isoformat()
            for row in source_bars
        )
        observations.append({
            "accepted_sequence": 0,
            "revision_id": _hash_document({
                "kind": "historical_candidate_population",
                "case_date": case_date,
                "codes": codes,
                "activation": first_start,
            }),
            "kind": "historical_candidate_population",
            "subject": case_date,
            "observation_key": case_date,
            "effective_at": first_start,
            "available_at": first_start,
            "payload": {
                "codes": list(codes),
                "population_id": POPULATION_ID,
                "selection_date": case_date,
                "activation_policy": "first_collected_session_after_selection_close",
                "source_available_at": max(str(row["available_at"]) for row in candidates),
                "not_contemporaneous_top20": True,
            },
        })
        ordered_bars = sorted(source_bars, key=lambda row: (
            str(row["payload"]["bar_time"]), str(row["payload"]["code"]),
            str(row["revision_id"]),
        ))
        included_cases.append({
            "selection_date": case_date,
            "candidate_count": len(codes),
            "minute_bar_count": len(ordered_bars),
        })
        for source in ordered_bars:
            payload = source["payload"]
            bar_end = _aware_datetime(payload["bar_time"])
            bar_start = bar_end - timedelta(minutes=1)
            trading_value = int(payload.get("trading_value") or 0)
            observation_payload = {
                "market": "KRX",
                "code": str(payload["code"]),
                "bar_start": bar_start.isoformat(),
                "bar_end": bar_end.isoformat(),
                "open": int(payload["open"]),
                "high": int(payload["high"]),
                "low": int(payload["low"]),
                "close": int(payload["close"]),
                "volume": int(payload.get("volume") or 0),
                "trade_value_million_won": trading_value // 1_000_000,
                "window_closed": True,
                "capture_quality": "complete",
                "finalization_source": "daishin_completed_history_job",
                "source_revision_id": str(source["revision_id"]),
                "source_available_at": str(source["available_at"]),
                "replay_clock_policy": "historical_bar_close",
                "historical_case_date": case_date,
            }
            scientific = {
                "kind": "minute_bar",
                "subject": f"{payload['code']}:KRX",
                "venue": "KRX",
                "observation_key": bar_start.isoformat(),
                "effective_at": bar_end.isoformat(),
                "available_at": bar_end.isoformat(),
                "completeness": "complete",
                "value_kind": "actual",
                "payload": observation_payload,
            }
            observations.append({
                "accepted_sequence": 0,
                "revision_id": _hash_document(scientific),
                **scientific,
            })
    if not included_cases:
        raise ValueError("historical reconstruction cases have no complete one-minute outcome bars")
    observations.sort(key=lambda row: (
        str(row["available_at"]),
        0 if row["kind"] == "historical_candidate_population" else 1,
        str(row["revision_id"]),
    ))
    for ordinal, row in enumerate(observations, start=1):
        row["accepted_sequence"] = ordinal
        row["ordinal"] = ordinal
    revision_hash = hashlib.sha256(
        "\n".join(str(row["revision_id"]) for row in observations).encode("utf-8")
    ).hexdigest()
    identity = {
        "source_dataset_id": dataset.manifest["dataset_id"],
        "selected_dates": requested,
        "revision_ids_hash": revision_hash,
    }
    manifest = {
        "schema_version": 1,
        "runtime_input_version": "historical_reconstruction_strategy/v1",
        "dataset_id": "historical-strategy-" + _hash_document(identity),
        "revision_count": len(observations),
        "revision_ids_hash": revision_hash,
        "source_dataset_id": dataset.manifest["dataset_id"],
        "source_revision_ids_hash": dataset.manifest["revision_ids_hash"],
        "selected_date": requested[0] if len(requested) == 1 else "",
        "selected_dates": list(requested),
        "included_cases": included_cases,
        "excluded_cases": excluded_cases,
        "population_id": POPULATION_ID,
        "not_contemporaneous_top20": True,
        "simulation_clock": "historical_bar_close",
        "source_availability_preserved_in_payload": True,
        "strategy_entry_phase": "sessions_after_selection_date",
        "supported_interval_seconds": 60,
        "excluded_interval_seconds": [300],
        "research_session_profile": research_session_profile_document("krx-regular/v1"),
    }
    return FrozenResearchDataset(manifest, tuple(observations))


def write_historical_research_input(dataset: "FrozenResearchDataset", output: Path) -> dict[str, Any]:
    """Write the derived adapter result using the existing frozen export file contract."""
    output = Path(output)
    if output.exists():
        raise ValueError("historical strategy input is immutable and cannot be overwritten")
    observations = tuple(
        {**row, "ordinal": ordinal}
        for ordinal, row in enumerate(dataset.observations, start=1)
    )
    encoded = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in observations
    )
    manifest = {
        **dataset.manifest,
        "observations_file": "observations.jsonl",
        "observations_file_hash": hashlib.sha256(encoded).hexdigest(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        (temporary / "observations.jsonl").write_bytes(encoded)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8",
        )
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def _preferred_interval(connection: sqlite3.Connection, code: str, selected_date: str) -> int:
    range_end = f"{(date.fromisoformat(selected_date) + timedelta(days=1)).isoformat()}T00:00:00"
    values = {
        int(row[0]) for row in connection.execute(
            "SELECT DISTINCT interval_seconds FROM market_bars "
            "WHERE provider='daishin_creon' AND code=? "
            "AND venue IN ('K','KRX') AND session_scope='regular' "
            "AND adjustment_mode='raw' AND bar_time>=? AND bar_time<? "
            "AND interval_seconds IN (60,300)",
            (code, f"{selected_date}T00:00:00", range_end),
        )
    }
    return 60 if 60 in values else 300 if 300 in values else 0


def _market_job_state(connection: sqlite3.Connection, code: str) -> str:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_backfill_jobs'"
    ).fetchone()
    if table is None:
        return "unknown"
    row = connection.execute(
        "SELECT state FROM market_backfill_jobs WHERE code=?", (code,),
    ).fetchone()
    return str(row[0]) if row is not None else "unknown"


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("historical reconstruction timestamps must be timezone-aware")
    return parsed


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

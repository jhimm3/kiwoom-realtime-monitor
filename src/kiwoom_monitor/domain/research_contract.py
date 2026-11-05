"""연구 재현에 필요한 최소 관측 이력 계약."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from kiwoom_monitor.domain.market_data_contract import MarketDataObservation


THEME_BACKUP_FORMAT = "kiwoom-realtime-monitor-theme-db"
THEME_BACKUP_VERSION = 1
OBSERVATION_REVISION_SCHEMA_VERSION = 1
DATASET_SNAPSHOT_RESEARCH_KINDS = frozenset({"ranking", "top20_membership", "minute_bar"})
RESEARCH_OBSERVATION_KINDS = DATASET_SNAPSHOT_RESEARCH_KINDS | {"daily_bar"}
DATASET_SNAPSHOT_HISTORY_KINDS = DATASET_SNAPSHOT_RESEARCH_KINDS | {'closing_auction_price'}
OBSERVATION_REVISION_KINDS = RESEARCH_OBSERVATION_KINDS | DATASET_SNAPSHOT_HISTORY_KINDS
REPORTED_RESEARCH_RESULT_KIND = "research-run-report"
REPORTED_RESEARCH_RESULT_COLLECTION = "reported_research_results"
CLOSE_FINAL_CANDIDATE_VERSION = "final_research_candidate/v1"
SECTION12_EVIDENCE_EXPERIMENT_FORMAT = "kiwoom-monitor-evidence-experiment"
SECTION12_EVIDENCE_EXPERIMENT_FAMILIES = {
    "micro_experiment": "micro_execution/v1",
    "daily_decline_experiment": "daily_decline/v1",
    "account_risk_experiment": "account_risk_study/v1",
}
# Reuse the existing bounded research-publication document contract (256 KiB).
MAX_REPORTED_RESEARCH_RESULT_BYTES = 256 * 1024

# The historical scientific recipe is raw bytes, including their original line endings.
# This metadata contract remains available on receivers without the research engine.
RESEARCH_IMPLEMENTATION_RECIPE = "ordered-utf8-path-nul-raw-bytes-nul/sha256/v1"
RESEARCH_IMPLEMENTATION_PATHS = (
    "src/kiwoom_monitor/application/market_session_schedule.py",
    "src/kiwoom_monitor/application/research_factors.py",
    "src/kiwoom_monitor/application/research_replay.py",
    "src/kiwoom_monitor/application/market_research_features.py",
    "src/kiwoom_monitor/application/market_theme_shadow.py",
    "src/kiwoom_monitor/application/theme_leadership.py",
    "src/kiwoom_monitor/application/breakout_strategy.py",
    "src/kiwoom_monitor/application/leader_breakout.py",
    "src/kiwoom_monitor/application/breakout_theme_research.py",
    "src/kiwoom_monitor/application/account_risk_research.py",
    "src/kiwoom_monitor/application/close_and_ipo.py",
    "src/kiwoom_monitor/application/panic_rebound.py",
    "src/kiwoom_monitor/application/daily_decline.py",
    "src/kiwoom_monitor/application/micro_execution_evidence.py",
    "src/kiwoom_monitor/application/section12_minute_research.py",
    "src/kiwoom_monitor/application/pullback_reacceleration_strategy.py",
    "src/kiwoom_monitor/application/research_families.py",
    "src/kiwoom_monitor/infrastructure/persistence/research_repository.py",
    "src/kiwoom_monitor/infrastructure/research_data_source.py",
    "src/kiwoom_monitor/application/research_execution.py",
    "src/kiwoom_monitor/application/position_target.py",
    "src/kiwoom_monitor/application/research_evaluation.py",
    "src/kiwoom_monitor/application/research_splits.py",
    "src/kiwoom_monitor/domain/ranking.py",
    "src/kiwoom_monitor/research_run.py",
    "src/kiwoom_monitor/domain/strategy_contract.py",
    "src/kiwoom_monitor/domain/simulation_contract.py",
)


def research_implementation_digest(read_bytes: Callable[[str], bytes]) -> str:
    digest = hashlib.sha256()
    for path in RESEARCH_IMPLEMENTATION_PATHS:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(read_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def research_artifact_metadata_hash(document: Mapping[str, Any]) -> str:
    encoded = json.dumps({key: value for key, value in document.items() if key != "metadata_hash"},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SelectedResearchArtifact:
    """An operator-selected producer identity, never a client or order authority."""

    artifact_kind: str
    artifact_ref: str
    artifact_sha256: str
    source_ref: str
    profiles: tuple[str, ...]
    implementation_hash: str
    file_hashes: tuple[str, ...]
    metadata_hash: str

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "SelectedResearchArtifact":
        if not isinstance(document, Mapping) or set(document) != {
            "version", "kind", "artifact_kind", "artifact_ref", "artifact_sha256", "source_ref",
            "profiles", "recipe", "files", "implementation_hash", "metadata_hash",
        }:
            raise ValueError("invalid selected research artifact fields")
        if (type(document["version"]) is not int or document["version"] != 1
                or document["kind"] != "research-producer-artifact"
                or document["artifact_kind"] not in ("wheel", "source-zip")
                or document["recipe"] != RESEARCH_IMPLEMENTATION_RECIPE):
            raise ValueError("unsupported selected research artifact version or recipe")
        for key in ("artifact_ref", "source_ref"):
            value = document[key]
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError("selected research artifact requires " + key)
        profiles, file_rows = document["profiles"], document["files"]
        if (not isinstance(profiles, list) or not profiles
                or any(not isinstance(value, str) or not value or value != value.strip() for value in profiles)
                or len(set(profiles)) != len(profiles)
                or not isinstance(file_rows, list) or len(file_rows) != len(RESEARCH_IMPLEMENTATION_PATHS)
                or any(not isinstance(row, dict) or set(row) != {"path", "sha256"} for row in file_rows)
                or tuple(row["path"] for row in file_rows) != RESEARCH_IMPLEMENTATION_PATHS):
            raise ValueError("invalid selected research artifact profiles or ordered files")
        hashes = [document[key] for key in ("artifact_sha256", "implementation_hash", "metadata_hash")]
        hashes.extend(row["sha256"] for row in file_rows)
        if any(not isinstance(value, str) or len(value) != 64
               or any(character not in "0123456789abcdef" for character in value) for value in hashes):
            raise ValueError("invalid selected research artifact SHA256")
        if research_artifact_metadata_hash(document) != document["metadata_hash"]:
            raise ValueError("selected research artifact metadata hash conflict")
        return cls(document["artifact_kind"], document["artifact_ref"], document["artifact_sha256"],
                   document["source_ref"], tuple(profiles), document["implementation_hash"],
                   tuple(row["sha256"] for row in file_rows), document["metadata_hash"])

    def expected_implementation_hash(self, profile: str) -> str | None:
        return self.implementation_hash if profile in self.profiles else None


def encode_reported_result_owner(owner: str) -> str:
    """ASCII HTTP assertion: canonical padded base64url of the UTF-8 owner."""
    if not isinstance(owner, str) or not owner or owner != owner.strip():
        raise ValueError("expected result owner is required")
    return "utf8-b64:" + base64.urlsafe_b64encode(owner.encode("utf-8")).decode("ascii")


def decode_reported_result_owner(value: str) -> str:
    try:
        if not value.startswith("utf8-b64:"):
            raise ValueError
        owner = base64.b64decode(value[len("utf8-b64:"):].encode("ascii"), altchars=b"-_", validate=True).decode("utf-8")
        if encode_reported_result_owner(owner) != value:
            raise ValueError
        return owner
    except (UnicodeError, ValueError, binascii.Error):
        raise ValueError("invalid expected result owner assertion") from None


class ResearchResultConflict(ValueError):
    """An authenticated owner's immutable result ID already has other bytes."""


def is_section12_evidence_manifest(payload: Mapping[str, Any]) -> bool:
    """Recognize the original report shape without loading its calculation engine."""
    if (set(payload) != {"format", "version", "run_id", "spec", "logical_result_hash", "result", "order_authorized"}
            or payload["format"] != SECTION12_EVIDENCE_EXPERIMENT_FORMAT
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["order_authorized"] is not False
            or not isinstance(payload["result"], dict)
            or payload["result"].get("order_authorized", False) is not False
            or not isinstance(payload["spec"], dict)):
        return False
    spec = payload["spec"]
    mode = spec.get("mode")
    if not isinstance(mode, str) or mode not in SECTION12_EVIDENCE_EXPERIMENT_FAMILIES:
        return False
    fields = {"family", "mode", "code_hash", "parameters", "dataset_sha256", "source_kind"}
    if mode == "daily_decline_experiment" and spec.get("source_kind") == "frozen_native_daily_observations":
        fields |= {"frozen_manifest_sha256", "frozen_dataset_id", "revision_ids_hash"}
    elif spec.get("source_kind") != "synthetic":
        return False
    return set(spec) == fields and spec["family"] == SECTION12_EVIDENCE_EXPERIMENT_FAMILIES[mode]


@dataclass(frozen=True)
class ReportedResearchResult:
    """Reported bytes only; neither scientific validation nor execution admission."""

    result_id: str
    content_hash: str
    document: dict[str, Any]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ReportedResearchResult":
        if not isinstance(document, Mapping) or set(document) != {
            "version", "kind", "result_id", "produced_at", "payload",
        }:
            raise ValueError("invalid reported research result fields")
        if type(document["version"]) is not int or document["version"] != 1 or document["kind"] != REPORTED_RESEARCH_RESULT_KIND:
            raise ValueError("unsupported reported research result version or kind")
        result_id, produced_at, payload = document["result_id"], document["produced_at"], document["payload"]
        if not isinstance(result_id, str) or not result_id.strip() or result_id != result_id.strip() or len(result_id) > 2000 or "/" in result_id:
            raise ValueError("invalid reported research result ID")
        try:
            moment = datetime.fromisoformat(produced_at)
            if moment.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("reported producer timestamp must be timezone-aware") from None
        if not isinstance(payload, dict) or payload.get("run_id") != result_id:
            raise ValueError("reported payload must retain its original research run manifest")
        legacy = (payload.get("format") == "kiwoom-monitor-research-run"
                  and type(payload.get("version")) is int and payload["version"] == 3)
        if not legacy and not is_section12_evidence_manifest(payload):
            raise ValueError("reported payload must retain its original research run manifest")
        encoded = json.dumps(dict(document), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_REPORTED_RESEARCH_RESULT_BYTES:
            raise ValueError("reported research result exceeds 256 KiB")
        return cls(result_id, hashlib.sha256(encoded).hexdigest(), json.loads(encoded))


@dataclass(frozen=True)
class ObservationRevisionSource:
    """기존 시장 관측을 불변 연구 이력에 넣기 위한 최소 저장 계약."""

    kind: str
    subject: str
    observation_key: str
    source_id: str
    effective_at: datetime | None
    available_at: datetime | None
    venue: str
    unit: str
    value_kind: str
    completeness: str
    origin: str
    candidate_universe: str
    payload_json: str
    payload_hash: str
    source_ref_json: str

    @classmethod
    def from_observation(
        cls,
        kind: str,
        subject: str,
        observation_key: str,
        payload: Mapping[str, Any],
        observation: MarketDataObservation[object],
    ) -> "ObservationRevisionSource":
        normalized_kind = str(kind).strip()
        if normalized_kind not in OBSERVATION_REVISION_KINDS:
            raise ValueError(f"unsupported research observation kind: {normalized_kind}")
        key = str(observation_key).strip()
        if not key:
            raise ValueError("observation_key is required")
        normalized_subject = str(subject).strip()
        if normalized_subject != observation.subject:
            raise ValueError("snapshot subject and observation subject must match")
        metadata = observation.metadata
        effective_at = _as_utc(metadata.effective_at)
        available_at = _as_utc(metadata.available_at)
        payload_json = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        fingerprint = {
            "schema_version": OBSERVATION_REVISION_SCHEMA_VERSION,
            "kind": normalized_kind,
            "subject": observation.subject,
            "observation_key": key,
            "source_id": metadata.source,
            "effective_at": effective_at.isoformat() if effective_at else None,
            "venue": metadata.venue.value,
            "unit": metadata.unit.value,
            "value_kind": metadata.value_kind.value,
            "completeness": metadata.completeness.value,
            "origin": metadata.origin.value,
            "candidate_universe": metadata.candidate_universe.value,
            "payload": json.loads(payload_json),
        }
        source_ref = (
            {"kind": "ranking", "subject": "5", "observation_key": key}
            if normalized_kind == "top20_membership" else {}
        )
        encoded = json.dumps(
            fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            kind=normalized_kind,
            subject=normalized_subject,
            observation_key=key,
            source_id=str(metadata.source or "unknown"),
            effective_at=effective_at,
            available_at=available_at,
            venue=metadata.venue.value,
            unit=metadata.unit.value,
            value_kind=metadata.value_kind.value,
            completeness=metadata.completeness.value,
            origin=metadata.origin.value,
            candidate_universe=metadata.candidate_universe.value,
            payload_json=payload_json,
            payload_hash=hashlib.sha256(encoded).hexdigest(),
            source_ref_json=json.dumps(source_ref, ensure_ascii=False, separators=(",", ":")),
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("research observation timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class ThemeGenerationConflict(ValueError):
    """A publication cannot claim the current generation or replace its identity."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ThemeSnapshotSource:
    """중앙 서버가 불변 이력으로 받아들일 수 있는 완전한 테마 문서."""

    profile_id: str
    content_hash: str
    effective_at: str | None
    origin_device: str
    document: dict[str, Any]

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any],
        *,
        effective_at: object = None,
        origin_device: object = None,
    ) -> "ThemeSnapshotSource":
        copied = dict(document)
        if (
            copied.get("format") != THEME_BACKUP_FORMAT
            or copied.get("version") != THEME_BACKUP_VERSION
            or not isinstance(copied.get("profiles"), list)
        ):
            raise ValueError("완전한 테마 프로필 백업 문서만 이력으로 기록할 수 있습니다.")
        canonical = dict(copied)
        # export_document()의 생성 시각은 재전송 때마다 달라질 수 있다. 실제
        # 프로필·관계가 같은 재전송은 하나의 관측으로 취급한다.
        canonical.pop("created_at", None)
        encoded = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        supplied_effective = str(effective_at or "").strip()
        document_effective = str(copied.get("created_at") or "").strip()
        return cls(
            profile_id=str(copied.get("active_profile") or "").strip(),
            content_hash=hashlib.sha256(encoded).hexdigest(),
            effective_at=supplied_effective or document_effective or None,
            origin_device=str(origin_device or "unknown").strip() or "unknown",
            document=copied,
        )

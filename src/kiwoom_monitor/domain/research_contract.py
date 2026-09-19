"""연구 재현에 필요한 최소 관측 이력 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from kiwoom_monitor.domain.market_data_contract import MarketDataObservation


THEME_BACKUP_FORMAT = "kiwoom-realtime-monitor-theme-db"
THEME_BACKUP_VERSION = 1
OBSERVATION_REVISION_SCHEMA_VERSION = 1
RESEARCH_OBSERVATION_KINDS = frozenset({"ranking", "top20_membership", "minute_bar"})


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
        if normalized_kind not in RESEARCH_OBSERVATION_KINDS:
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

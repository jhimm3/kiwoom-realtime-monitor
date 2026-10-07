"""Completeness gate for historical development inputs before strategy comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.infrastructure.research_data_source import (
    DEVELOPMENT_INPUT_VERSION,
    FrozenResearchDataset,
    research_observation_order,
)


READINESS_VERSION = "historical_development_readiness/v1"


@dataclass(frozen=True)
class HistoricalDevelopmentReadiness:
    version: str
    status: str
    partition_role: str
    candidate_code_count: int
    codes_with_bars: int
    codes_with_continuous_minute_pair: int
    bar_coverage_ppm: int
    continuous_pair_coverage_ppm: int
    missing_bar_codes: tuple[str, ...]
    missing_continuous_pair_codes: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_historical_development_readiness(
    dataset: FrozenResearchDataset,
) -> HistoricalDevelopmentReadiness:
    manifest = dataset.manifest
    descriptor = manifest.get("development_partition")
    if manifest.get("runtime_input_version") != DEVELOPMENT_INPUT_VERSION:
        raise ValueError("readiness requires an independent development input")
    if not isinstance(descriptor, Mapping) or descriptor.get("universe_kind") != "historical_candidate_population":
        raise ValueError("readiness requires a historical candidate population")

    seed = descriptor.get("universe_seed")
    seed_revision_id = str(seed.get("revision_id")) if isinstance(seed, Mapping) else ""
    populations = sorted(
        (
            row for row in dataset.observations
            if row.get("kind") == "historical_candidate_population"
            and (not seed_revision_id or row.get("revision_id") != seed_revision_id)
        ),
        key=research_observation_order,
    )
    if not populations:
        raise ValueError("historical development input has no candidate population")
    candidates_by_case: dict[str, tuple[str, ...]] = {}
    for population in populations:
        payload = population.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(payload.get("codes"), list):
            raise ValueError("historical candidate population has no code list")
        case_date = str(payload.get("selection_date") or "")
        candidates_by_case[case_date] = tuple(dict.fromkeys(
            code for code in (normalize_stock_code(value) for value in payload["codes"]) if code
        ))
    candidates = {(day, code) for day, codes in candidates_by_case.items() for code in codes}
    if not candidates:
        raise ValueError("historical candidate population is empty")

    starts_by_case_code: dict[tuple[str, str], list[datetime]] = {}
    for row in dataset.observations:
        if row.get("kind") != "minute_bar" or row.get("venue") != "KRX":
            continue
        bar = row.get("payload")
        if not isinstance(bar, Mapping):
            continue
        code = normalize_stock_code(bar.get("code"))
        case_date = str(bar.get("historical_case_date") or bar.get("selection_date") or "")
        key = (case_date, code)
        if key not in candidates:
            continue
        try:
            start = datetime.fromisoformat(str(bar.get("bar_start", "")))
        except ValueError:
            continue
        if start.tzinfo is None:
            continue
        starts_by_case_code.setdefault(key, []).append(start.astimezone(timezone.utc))

    with_bars = set(starts_by_case_code)
    with_pairs: set[tuple[str, str]] = set()
    for key, starts in starts_by_case_code.items():
        ordered = sorted(set(starts))
        if any(
            int((current - previous).total_seconds()) == 60
            for previous, current in zip(ordered, ordered[1:])
        ):
            with_pairs.add(key)
    missing_bars = tuple(sorted(_case_code_label(key) for key in candidates - with_bars))
    missing_pairs = tuple(sorted(_case_code_label(key) for key in candidates - with_pairs))
    reasons: list[str] = []
    if missing_bars:
        reasons.append("candidate_codes_without_minute_bars")
    if missing_pairs:
        reasons.append("candidate_codes_without_continuous_minute_pair")
    role = str(descriptor.get("spec", {}).get("fold_name", ""))
    evaluation = descriptor.get("evaluation")
    if isinstance(evaluation, Mapping):
        folds = evaluation.get("folds")
        if isinstance(folds, list) and len(folds) == 1 and isinstance(folds[0], Mapping):
            role = str(folds[0].get("role", role))
    total = len(candidates)
    return HistoricalDevelopmentReadiness(
        version=READINESS_VERSION,
        status="READY" if not reasons else "BLOCKED",
        partition_role=role,
        candidate_code_count=total,
        codes_with_bars=len(candidates & with_bars),
        codes_with_continuous_minute_pair=len(candidates & with_pairs),
        bar_coverage_ppm=len(candidates & with_bars) * 1_000_000 // total,
        continuous_pair_coverage_ppm=len(candidates & with_pairs) * 1_000_000 // total,
        missing_bar_codes=missing_bars,
        missing_continuous_pair_codes=missing_pairs,
        reasons=tuple(reasons),
    )


def _case_code_label(key: tuple[str, str]) -> str:
    day, code = key
    return f"{day}:{code}" if day else code

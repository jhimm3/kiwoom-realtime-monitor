"""Completeness gate for historical development inputs before strategy comparison."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Callable

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


def _read_day_raw(path: Path, day: str, *, checkpoint: Callable[[], None] = lambda: None) -> tuple[dict[str, int], int]:
    bars: dict[str, int] = {}
    conflicts = 0
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            checkpoint()
            page = json.loads(line)
            if not isinstance(page, dict):
                raise ValueError("원응답 페이지 형식이 잘못됐습니다.")
            if page.get("record_type") != "page" or page.get("interval_seconds") != 60:
                continue
            if not isinstance(page.get("bars", []), list):
                raise ValueError("원응답 봉 목록 형식이 잘못됐습니다.")
            for bar in page.get("bars", []):
                if not isinstance(bar, dict):
                    raise ValueError("원응답 봉 형식이 잘못됐습니다.")
                bar_time = str(bar["bar_time"])
                if not bar_time.startswith(day + "T"):
                    continue
                volume = int(bar["volume"])
                if bar_time in bars and bars[bar_time] != volume:
                    conflicts += 1
                bars[bar_time] = volume
    return bars, conflicts


def audit_development_gap_provenance(dataset, review: dict, archived_report: Path, *,
                                    archive_root: Path | None = None,
                                    checkpoint: Callable[[], None] = lambda: None) -> dict:
    """현재 개발 봉과 과거 감사 구간·원응답을 대조한다. 과거 판정을 승계하지 않는다."""
    with Path(archived_report).open("rb") as stream:
        encoded = stream.read(4 * 1024 * 1024 + 1)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("원응답 감사 파일 크기 상한을 초과했습니다.")
    archived = json.loads(encoded)
    if (not isinstance(archived, dict)
            or archived.get("version") != "historical_monthly_selected_gap_raw_provenance/v1"
            or not isinstance(archived.get("stock_days"), list) or not isinstance(archived.get("gaps"), list)):
        raise ValueError("지원하지 않는 원응답 감사 형식입니다.")
    allowed_root = (archive_root if archive_root is not None else
                    Path(__file__).resolve().parents[3] / "data/historical_collection/daishin").resolve()
    def stamp(value):
        result = datetime.fromisoformat(str(value))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("원응답 근거 시각에는 시간대가 필요합니다.")
        return result.astimezone(timezone.utc)
    cases, previous_gaps = {}, set()
    for row in archived["stock_days"]:
        if (not isinstance(row, dict) or any(not isinstance(row.get(name), str) or not row[name]
                for name in ("code", "outcome_date", "raw_file", "raw_sha256"))
                or len(row["raw_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in row["raw_sha256"])):
            raise ValueError("원응답 감사의 종목·일 근거가 잘못됐습니다.")
        key = row["code"], row["outcome_date"]
        if key in cases:
            raise ValueError("원응답 감사의 종목·일이 중복됩니다.")
        cases[key] = row
    for row in archived["gaps"]:
        if (not isinstance(row, dict) or any(not isinstance(row.get(name), str) or not row[name]
                for name in ("code", "outcome_date", "from_bar", "to_bar"))):
            raise ValueError("원응답 감사의 공백 참조가 잘못됐습니다.")
        left, right = stamp(row["from_bar"]), stamp(row["to_bar"])
        key = row["code"], row["outcome_date"], left, right
        if key in previous_gaps or type(row.get("gap_seconds")) is not int or row["gap_seconds"] != (right - left).total_seconds() or right <= left:
            raise ValueError("원응답 감사의 공백 구간이 잘못됐습니다.")
        previous_gaps.add(key)
    long_gaps = [{"code": row["code"], "outcome_date": row["outcome_date"], **gap}
                 for row in review["stock_days"] for gap in row["internal_gaps"] if gap["unobserved_minutes"] >= 10]
    selected = {(row["code"], row["outcome_date"]) for row in long_gaps}
    source_bars, source_ends, conflicts = {}, {}, Counter()
    from kiwoom_monitor.application.market_session_schedule import KST
    for row in dataset.observations:
        checkpoint()
        if row["kind"] != "minute_bar" or row.get("venue") != "KRX":
            continue
        bar = row["payload"]
        end, start = stamp(bar["bar_end"]), stamp(bar["bar_start"])
        # 원응답은 봉 종료시각으로 표기한다. +1분을 추측해서 적용하지 않는다.
        # 날짜는 report의 KST 결과일로 정규화한다.
        key = bar["code"], end.astimezone(KST).date().isoformat()
        if key not in selected:
            continue
        volume = bar["volume"]
        if type(volume) is not int or volume < 0:
            raise ValueError("개발 봉 거래량이 잘못됐습니다.")
        saved = source_bars.setdefault(key, {})
        if end in saved and saved[end] != volume:
            conflicts[key] += 1
        saved[end] = volume
        end_key = (*key, start)
        if end_key in source_ends and source_ends[end_key] != end:
            raise ValueError("개발 봉 시작·종료 참조가 모호합니다.")
        source_ends[end_key] = end
    checked_cases = []
    for key in sorted(selected):
        checkpoint()
        past = cases.get(key)
        item = {"code": key[0], "outcome_date": key[1], "current_raw_input_match": False,
                "status": "ARCHIVED_CASE_UNAVAILABLE", "current_input_volume_conflicts": conflicts[key]}
        if past is not None:
            path = Path(past["raw_file"]).resolve()
            if not path.is_relative_to(allowed_root) or path.name != "1m.ndjson":
                raise ValueError("원응답 경로가 허용된 보관 폴더 밖입니다.")
            item.update(raw_file=str(path), expected_raw_sha256=past["raw_sha256"])
            try:
                before = path.stat()
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        checkpoint()
                        digest.update(chunk)
                item["actual_raw_sha256"] = digest.hexdigest()
                if digest.hexdigest() != past["raw_sha256"]:
                    item["status"] = "RAW_HASH_MISMATCH"
                else:
                    try:
                        raw, raw_conflicts = _read_day_raw(path, key[1], checkpoint=checkpoint)
                    except (KeyError, TypeError) as error:
                        raise ValueError("원응답 봉 형식이 잘못됐습니다.") from error
                    normalized = {}
                    for ts, volume in raw.items():
                        normalized_time = stamp(ts)
                        if normalized_time in normalized and normalized[normalized_time] != volume:
                            raw_conflicts += 1
                        normalized[normalized_time] = volume
                    saved = source_bars.get(key, {})
                    missing, extra = set(normalized) - set(saved), set(saved) - set(normalized)
                    differences = sum(saved[ts] != normalized[ts] for ts in set(saved) & set(normalized))
                    matched = not (raw_conflicts or conflicts[key] or missing or extra or differences)
                    item.update(raw_duplicate_volume_conflicts=raw_conflicts,
                                raw_bars_absent_from_input=len(missing), input_bars_absent_from_raw=len(extra),
                                volume_mismatches=differences, current_raw_input_match=matched,
                                status="CURRENT_RAW_INPUT_MATCH" if matched else "CURRENT_RAW_INPUT_MISMATCH")
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("대조 중 원응답 파일이 변경됐습니다.")
            except OSError:
                item["status"] = "RAW_SOURCE_UNAVAILABLE"
                item["current_raw_input_match"] = False
        checked_cases.append(item)
    checked_gaps = []
    for gap in long_gaps:
        checkpoint()
        key = gap["code"], gap["outcome_date"]
        left = source_ends.get((*key, stamp(gap["previous_bar_start"])))
        right = source_ends.get((*key, stamp(gap["next_bar_start"])))
        if left is None or right is None:
            raise ValueError("현재 개발 입력에서 공백 양끝의 종료시각을 확인할 수 없습니다.")
        checked_gaps.append({**gap, "source_from_bar_end": left.isoformat(), "source_to_bar_end": right.isoformat(),
                             "archived_gap_matches": (*key, left, right) in previous_gaps, "cause_verified": False})
    return {"version": "historical_development_gap_provenance/v1",
            "archived_report_sha256": hashlib.sha256(encoded).hexdigest(),
            "source_observations_file_hash": dataset.manifest["observations_file_hash"],
            "counts": {"long_gap_count": len(checked_gaps), "stock_day_count": len(checked_cases),
                       "archived_gap_match_count": sum(row["archived_gap_matches"] for row in checked_gaps),
                       "current_raw_input_match_count": sum(row["current_raw_input_match"] for row in checked_cases),
                       "raw_unavailable_stock_days": sum(row["status"] in {"RAW_SOURCE_UNAVAILABLE", "ARCHIVED_CASE_UNAVAILABLE"} for row in checked_cases)},
            "stock_days": checked_cases, "gaps": checked_gaps, "full_session_completeness_verified": False,
            "limitations": ["archived_report_is_support_only_without_current_raw_bytes",
                            "raw_input_equality_does_not_prove_no_market_trades", "no_bar_or_case_modified"]}

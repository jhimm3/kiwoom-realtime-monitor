"""고정 watermark의 중앙 관측 페이지를 검증해 한 데이터셋으로 조립한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, Callable

from kiwoom_monitor.application.market_session_schedule import (
    research_session_profile_document,
)
from kiwoom_monitor.application.research_splits import DevelopmentPartitionSpec, ResearchEvaluationSpec, FinalHoldoutBatchSpec, warmup_start


class ResearchObservationClient(Protocol):
    def load_research_observations_page(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], *, subject: str = "",
        watermark: str = "", cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FrozenResearchDataset:
    manifest: dict[str, Any]
    observations: tuple[dict[str, Any], ...]
    theme_snapshots: tuple[dict[str, Any], ...] = ()


BUNDLE_VERSION = "research_dataset_bundle/v1"
BUNDLE_BOUNDARY_POLICY = "continuous_no_forced_close/v1"
DEVELOPMENT_INPUT_VERSION = 'independent_development_input/v1'
FINAL_INPUT_VERSION = 'independent_final_holdout_input/v1'


def prepare_development_partition(dataset: FrozenResearchDataset, partition: DevelopmentPartitionSpec,
                                  evaluation: ResearchEvaluationSpec, *, checkpoint=lambda: None) -> FrozenResearchDataset:
    """Copy only one development window; never carry aggregate source identity/quality."""
    if dataset.manifest.get('runtime_input_version') == FINAL_INPUT_VERSION or 'final_holdout_partition' in dataset.manifest:
        raise ValueError('final input cannot be recycled into development')
    selected = partition.evaluation_for(evaluation)
    return _prepare_independent_partition(dataset, selected, partition.to_dict(),
        'development_partition', DEVELOPMENT_INPUT_VERSION, 'development-', checkpoint)


def prepare_final_holdout_partition(dataset: FrozenResearchDataset, batch: FinalHoldoutBatchSpec,
                                   *, checkpoint=lambda: None) -> FrozenResearchDataset:
    """Project a verified full source; this private preparation is not an execution permit."""
    if not isinstance(batch, FinalHoldoutBatchSpec):
        raise ValueError('a locked final batch is required')
    if (dataset.manifest.get('dataset_id'), dataset.manifest.get('revision_ids_hash')) != (batch.dataset_id, batch.dataset_hash):
        raise ValueError('final source identity differs from the locked batch')
    if dataset.manifest.get('research_session_profile') != research_session_profile_document(batch.session_profile):
        raise ValueError('final source session profile differs from the locked batch')
    if (dataset.manifest.get('runtime_input_version') not in (None, 'continuous_bundle_replay/v1')
            or 'development_partition' in dataset.manifest or 'final_holdout_partition' in dataset.manifest):
        raise ValueError('final preparation requires the full frozen source')
    # Full source/candidate bindings belong to the outer batch ledger, never to engine input identity.
    policy = {'version': 'final_holdout_partition/v1', 'session_profile': batch.session_profile,
              'evaluation': batch.to_dict()['evaluation']}
    selected = ResearchEvaluationSpec.from_dict(policy['evaluation'])
    return _prepare_independent_partition(dataset, selected, policy,
        'final_holdout_partition', FINAL_INPUT_VERSION, 'final-', checkpoint)


def _prepare_independent_partition(dataset, selected, policy_document, descriptor_key,
                                   runtime_version, dataset_prefix, checkpoint):
    fold = selected.folds[0]
    lower = warmup_start(selected)
    end = datetime.fromisoformat(fold.end).astimezone(timezone.utc)
    historical_reconstruction = (
        dataset.manifest.get('runtime_input_version') == 'historical_reconstruction_strategy/v1'
    )
    universe_kind = (
        'historical_candidate_population' if historical_reconstruction else 'top20_membership'
    )
    rows, universe_seed = [], None
    for row in dataset.observations:
        checkpoint()
        available = research_observation_order(row)[0]
        if available < lower and row.get('kind') == universe_kind:
            if universe_seed is None or research_observation_order(row) > research_observation_order(universe_seed):
                universe_seed = row
        if not lower <= available < end:
            continue
        if row.get('kind') == 'minute_bar':
            payload = row.get('payload')
            if not isinstance(payload, dict):
                raise ValueError('development minute bar payload is invalid')
            start, bar_end = (_partition_time(payload.get(key)) for key in ('bar_start', 'bar_end'))
            if start < lower or bar_end > end:
                continue
        rows.append(json.loads(json.dumps(row)))
    if universe_seed is not None:
        rows.append(json.loads(json.dumps(universe_seed)))
    observations = tuple(sorted(rows, key=research_observation_order))
    earlier, themes = [], []
    for theme in dataset.theme_snapshots:
        checkpoint()
        raw = theme.get('available_at')
        if raw is None:
            continue  # Unknown availability cannot be used as historical evidence.
        available = _partition_time(raw)
        if available < lower:
            earlier.append((available, str(theme.get('snapshot_id', '')), theme))
        elif available < end:
            themes.append(theme)
    if earlier:
        themes.append(max(earlier, key=lambda item: item[:2])[2])
    copied_themes = tuple(json.loads(json.dumps(theme)) for theme in sorted(
        themes, key=lambda theme: (_partition_time(theme['available_at']), str(theme.get('snapshot_id', '')))))
    descriptor = {'spec': policy_document, 'evaluation': selected.to_dict(),
                  'warmup_start': lower.isoformat(), 'active_start': _partition_time(fold.start).isoformat(),
                  'end': end.isoformat(),
                  'universe_kind': universe_kind,
                  'universe_seed': {key: universe_seed.get(key) for key in ('revision_id', 'available_at', 'observation_key')} if universe_seed is not None else None}
    # Explicit whitelist: no source children, global IDs, watermarks, quality, or future counts.
    scientific = {key: dataset.manifest.get(key) for key in (
        'schema_version', 'research_session_profile', 'kinds', 'subject', 'universe_rule', 'order_policy_version')}
    scientific['kinds'] = sorted({str(row.get('kind', '')) for row in observations})
    if historical_reconstruction:
        scientific.update(
            source_runtime_input_version='historical_reconstruction_strategy/v1',
            population_id=dataset.manifest.get('population_id'),
            not_contemporaneous_top20=True,
            source_availability_preserved_in_payload=bool(
                dataset.manifest.get('source_availability_preserved_in_payload')
            ),
        )
    scientific = json.loads(json.dumps(scientific))
    identity = {'partition': descriptor, 'context': scientific, 'observations': observations, 'themes': copied_themes}
    checkpoint()
    manifest = {**scientific, 'dataset_id': dataset_prefix + _document_hash(identity),
                'revision_count': len(observations),
                'revision_ids_hash': hashlib.sha256('\n'.join(str(row['revision_id']) for row in observations).encode()).hexdigest(),
                'captured_range': {'start': lower.isoformat(), 'end': end.isoformat()},
                'quality_summary': {'recording_gap': 'unknown', 'flags': ['partition_coverage_not_evaluated']},
                'theme_snapshot_history_may_be_truncated': True,
                'theme_snapshot_count': len(copied_themes),
                'runtime_input_version': runtime_version, descriptor_key: descriptor}
    checkpoint()
    return FrozenResearchDataset(manifest, observations, copied_themes)


def _partition_time(raw):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    value = datetime.fromisoformat(str(raw))
    if value.tzinfo is None:
        raise ValueError('development partition timestamp must be timezone-aware')
    return value.astimezone(timezone.utc)


def development_partition_start(dataset: FrozenResearchDataset, evaluation: ResearchEvaluationSpec | None,
                                *, checkpoint=lambda: None) -> datetime | None:
    """Reject wrong/untagged execution policy before opening a run."""
    if dataset.manifest.get('runtime_input_version') == FINAL_INPUT_VERSION or 'final_holdout_partition' in dataset.manifest:
        raise ValueError('final holdout input requires the gated final evaluator')
    descriptor = dataset.manifest.get('development_partition')
    if descriptor is None:
        if dataset.manifest.get('runtime_input_version') == DEVELOPMENT_INPUT_VERSION:
            raise ValueError('development input is missing its partition policy')
        return None
    if dataset.manifest.get('runtime_input_version') != DEVELOPMENT_INPUT_VERSION or not isinstance(descriptor, dict):
        raise ValueError('development input version is invalid')
    partition = DevelopmentPartitionSpec.from_dict(descriptor['spec'])
    selected = ResearchEvaluationSpec.from_dict(descriptor['evaluation'])
    if evaluation != selected or partition.evaluation_for(selected) != selected:
        raise ValueError('development input requires its matching single-fold evaluation')
    return _validate_independent_partition(dataset, selected, descriptor, partition, checkpoint, 'development')


def final_holdout_partition_start(dataset: FrozenResearchDataset, evaluation: ResearchEvaluationSpec | None,
                                  *, checkpoint=lambda: None) -> datetime:
    """Validate the isolated final input; ownership is checked by the repository/runner."""
    descriptor = dataset.manifest.get('final_holdout_partition')
    if (dataset.manifest.get('runtime_input_version') != FINAL_INPUT_VERSION
            or not isinstance(descriptor, dict) or 'development_partition' in dataset.manifest):
        raise ValueError('final execution requires its isolated final input')
    selected = ResearchEvaluationSpec.from_dict(descriptor['evaluation'])
    policy = descriptor.get('spec')
    canonical = (FinalHoldoutBatchSpec('final_holdout_batch/v1', 'validation-source', 'a' * 64,
                 str(policy.get('session_profile')) if isinstance(policy, dict) else '', ('b' * 64,), evaluation).to_dict()['evaluation']
                 if evaluation is not None else None)
    if (not isinstance(policy, dict) or set(policy) != {'version', 'session_profile', 'evaluation'}
            or policy['version'] != 'final_holdout_partition/v1'
            or policy['evaluation'] != selected.to_dict()
            or canonical != selected.to_dict()
            or research_session_profile_document(policy['session_profile']) != dataset.manifest.get('research_session_profile')):
        raise ValueError('final input requires its matching single OOS evaluation')
    if len(selected.folds) != 1 or selected.folds[0].role != 'OOS':
        raise ValueError('final input requires exactly one OOS fold')
    return _validate_independent_partition(dataset, selected, descriptor, None, checkpoint, 'final')


def _validate_independent_partition(dataset, selected, descriptor, partition, checkpoint, label):
    lower, active, end = warmup_start(selected), _partition_time(selected.folds[0].start), _partition_time(selected.folds[0].end)
    if (descriptor.get('warmup_start'), descriptor.get('active_start'), descriptor.get('end')) != (lower.isoformat(), active.isoformat(), end.isoformat()):
        raise ValueError(f'{label} partition bounds do not match the frozen policy')
    seed = descriptor.get('universe_seed')
    universe_kind = str(descriptor.get('universe_kind', 'top20_membership'))
    if universe_kind not in {'top20_membership', 'historical_candidate_population'}:
        raise ValueError(f'{label} input has an invalid universe kind')
    seed_seen = False
    for row in dataset.observations:
        checkpoint()
        available = research_observation_order(row)[0]
        if available < lower and isinstance(seed, dict) and row.get('kind') == universe_kind and all(row.get(key) == seed.get(key) for key in ('revision_id', 'available_at', 'observation_key')) and not seed_seen:
            seed_seen = True
            continue  # Last previously observed universe; preserve its original timestamp/ID.
        if not lower <= available < end:
            raise ValueError(f'{label} input includes observations outside its partition')
        if row.get('kind') == 'minute_bar':
            if partition is not None and partition.symbol_partition is not None:
                partition.symbol_partition.bucket_for(row['payload'].get('code'))
            if _partition_time(row['payload']['bar_start']) < lower or _partition_time(row['payload']['bar_end']) > end:
                raise ValueError(f'{label} input includes a bar outside its partition')
    if seed is not None and not seed_seen:
        raise ValueError(f'{label} input is missing its declared universe seed')
    for theme in dataset.theme_snapshots:
        checkpoint()
        if _partition_time(theme.get('available_at')) >= end:
            raise ValueError(f'{label} input includes a future theme snapshot')
    return active


@dataclass(frozen=True)
class FrozenResearchBundle:
    """Verified daily inputs with their original manifests and ordinals."""

    manifest: dict[str, Any]
    datasets: tuple[FrozenResearchDataset, ...]


class CentralResearchDataSource:
    def __init__(self, client: ResearchObservationClient) -> None:
        self._client = client

    def load(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], *, subject: str = "",
        page_size: int = 1000, session_profile: str | None = None,
        initial_page: dict[str, Any] | None = None, checkpoint: Callable[[], None] = lambda: None,
        max_encoded_bytes: int | None = None,
    ) -> FrozenResearchDataset:
        watermark = ""
        cursor = 0
        manifest: dict[str, Any] | None = None
        observations: list[dict[str, Any]] = []
        revision_ids: list[str] = []
        seen_revision_ids: set[str] = set()
        encoded_bytes = 0
        while True:
            checkpoint()
            page = initial_page if initial_page is not None else self._client.load_research_observations_page(
                start, end, kinds, subject=subject, watermark=watermark,
                cursor=cursor, limit=max(1, min(int(page_size), 1000)),
            )
            initial_page = None
            checkpoint()
            current_manifest = page.get("manifest")
            current_watermark = str(page.get("watermark", ""))
            values = page.get("observations")
            if not isinstance(current_manifest, dict) or not current_watermark or not isinstance(values, list):
                raise ValueError("research observation page contract is invalid")
            if manifest is None:
                manifest = dict(current_manifest)
                watermark = current_watermark
            elif current_watermark != watermark or current_manifest != manifest:
                raise ValueError("research export watermark changed during pagination")
            for value in values:
                checkpoint()
                if not isinstance(value, dict) or int(value.get("ordinal", 0)) != len(observations) + 1:
                    raise ValueError("research export page has a missing or duplicate ordinal")
                revision_id = str(value.get("revision_id", ""))
                if not revision_id or revision_id in seen_revision_ids:
                    raise ValueError("research export page has a missing or duplicate revision id")
                observations.append(dict(value))
                if max_encoded_bytes is not None:
                    encoded_bytes += len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                    if encoded_bytes > max_encoded_bytes:
                        raise ValueError('research_export_encoded_byte_limit_exceeded')
                revision_ids.append(revision_id)
                seen_revision_ids.add(revision_id)
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                break
            next_value = int(next_cursor)
            if next_value <= cursor:
                raise ValueError("research export cursor did not advance")
            cursor = next_value
        assert manifest is not None
        actual_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
        if len(observations) != int(manifest.get("revision_count", -1)):
            raise ValueError("research export revision count does not match manifest")
        if actual_hash != manifest.get("revision_ids_hash"):
            raise ValueError("research export revision hash does not match manifest")
        if session_profile is not None:
            manifest["research_session_profile"] = research_session_profile_document(session_profile)
        return FrozenResearchDataset(manifest, tuple(observations))


def load_frozen_research_export(path: Path, *, checkpoint: Callable[[], None] = lambda: None) -> FrozenResearchDataset:
    """D2가 만든 불변 디렉터리를 hash와 ordinal까지 다시 검증해 읽는다."""
    root = Path(path)
    checkpoint()
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research manifest cannot be read") from exc
    if not isinstance(manifest, dict):
        raise ValueError("research manifest must be an object")
    if manifest.get("bundle_version") is not None:
        raise ValueError("research bundle requires the bundle reader or load_research_input")
    file_name = str(manifest.get("observations_file", ""))
    if not file_name or Path(file_name).name != file_name:
        raise ValueError("research observations_file must be a local file name")
    observations_path = root / file_name
    try:
        encoded = observations_path.read_bytes()
    except OSError as exc:
        raise ValueError("research observations file cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != manifest.get("observations_file_hash"):
        raise ValueError("research observations file hash does not match manifest")
    observations: list[dict[str, Any]] = []
    revision_ids: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        if line_number % 128 == 0:
            checkpoint()
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"research observation line {line_number} is invalid") from exc
        if not isinstance(value, dict) or int(value.get("ordinal", 0)) != line_number:
            raise ValueError("research export has a missing or duplicate ordinal")
        revision_id = str(value.get("revision_id", ""))
        if not revision_id or revision_id in seen:
            raise ValueError("research export has a missing or duplicate revision id")
        observations.append(value)
        revision_ids.append(revision_id)
        seen.add(revision_id)
    actual_ids_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    if len(observations) != int(manifest.get("revision_count", -1)):
        raise ValueError("research export revision count does not match manifest")
    if actual_ids_hash != manifest.get("revision_ids_hash"):
        raise ValueError("research export revision hash does not match manifest")
    profile = manifest.get("research_session_profile")
    if profile is not None:
        if not isinstance(profile, dict) or not str(profile.get("profile", "")):
            raise ValueError("research manifest session profile contract is invalid")
        if profile != research_session_profile_document(str(profile["profile"])):
            raise ValueError("research manifest session profile contract does not match its version")
    checkpoint()
    theme_snapshots = _load_optional_theme_snapshots(root, manifest, checkpoint=checkpoint)
    return FrozenResearchDataset(dict(manifest), tuple(observations), theme_snapshots)


def _load_optional_theme_snapshots(
    root: Path, manifest: dict[str, Any], *, checkpoint: Callable[[], None] = lambda: None,
) -> tuple[dict[str, Any], ...]:
    file_name = str(manifest.get("theme_snapshots_file", ""))
    if not file_name:
        return ()
    if Path(file_name).name != file_name:
        raise ValueError("research theme_snapshots_file must be a local file name")
    try:
        encoded = (root / file_name).read_bytes()
    except OSError as exc:
        raise ValueError("research theme snapshots file cannot be read") from exc
    if hashlib.sha256(encoded).hexdigest() != manifest.get("theme_snapshots_file_hash"):
        raise ValueError("research theme snapshots file hash does not match manifest")
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(encoded.decode("utf-8").splitlines(), start=1):
        if line_number % 128 == 0:
            checkpoint()
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"research theme snapshot line {line_number} is invalid") from exc
        if not isinstance(value, dict):
            raise ValueError("research theme snapshot must be an object")
        snapshot_id = str(value.get("snapshot_id", ""))
        if not snapshot_id or snapshot_id in seen:
            raise ValueError("research theme snapshot id is missing or duplicated")
        seen.add(snapshot_id)
        values.append(value)
    if len(values) != int(manifest.get("theme_snapshot_count", -1)):
        raise ValueError("research theme snapshot count does not match manifest")
    return tuple(values)


def _document_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bundle_child_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if not relative or path.anchor or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError("research bundle child must be a local relative directory")
    child = (root / path).resolve()
    if child == root.resolve() or not child.is_relative_to(root.resolve()):
        raise ValueError("research bundle child escapes its directory")
    return child


def _bundle_manifest(children: list[tuple[str, FrozenResearchDataset]], *,
                     checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    if not children:
        raise ValueError("research bundle requires at least one daily export")
    entries: list[dict[str, Any]] = []
    revisions: dict[str, str] = {}
    themes: dict[str, str] = {}
    previous_end: datetime | None = None
    dates: set[str] = set()
    contract: tuple[Any, ...] | None = None
    for relative, dataset in children:
        manifest = dataset.manifest
        try:
            captured = manifest["captured_range"]
            start = datetime.fromisoformat(captured["start"])
            end = datetime.fromisoformat(captured["end"])
            raw_kinds = manifest["kinds"]
            if not isinstance(raw_kinds, list) or not raw_kinds or not all(
                kind in {"ranking", "top20_membership", "minute_bar"} for kind in raw_kinds
            ):
                raise ValueError("unsupported bundle observation kinds")
            kinds = tuple(sorted(raw_kinds))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("research bundle child range/kinds contract is invalid") from exc
        if start.tzinfo is None or end.tzinfo is None or not 0 < (end - start).total_seconds() <= 86400:
            raise ValueError("research bundle child must have a timezone-aware daily range")
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        selected_date = start.astimezone(timezone(timedelta(hours=9))).date().isoformat()
        if selected_date in dates or (previous_end is not None and start < previous_end):
            raise ValueError("research bundle children overlap or are not in date order")
        dates.add(selected_date)
        previous_end = end
        profile = manifest.get("research_session_profile")
        if not isinstance(profile, dict) or profile != research_session_profile_document(str(profile.get("profile", ""))):
            raise ValueError("research bundle requires an explicit supported session profile")
        current_contract = (kinds, manifest.get("subject", ""), profile, manifest.get("universe_rule"),
                            manifest.get("order_policy_version"))
        if contract is None:
            contract = current_contract
        elif current_contract != contract:
            raise ValueError("research bundle child input contracts differ")
        if manifest.get("schema_version") != 1 or not manifest.get("dataset_id") or not manifest.get("fixed_watermark"):
            raise ValueError("research bundle child dataset id is missing")
        if manifest.get("theme_snapshot_history_may_be_truncated"):
            raise ValueError("research bundle child theme history may be truncated")
        for observation in dataset.observations:
            checkpoint()
            revision_id = str(observation["revision_id"])
            scientific_row = {key: value for key, value in observation.items() if key != "ordinal"}
            fingerprint = _document_hash(scientific_row)
            if revision_id in revisions and revisions[revision_id] != fingerprint:
                raise ValueError("research bundle revision id has conflicting content")
            revisions[revision_id] = fingerprint
        for theme in dataset.theme_snapshots:
            checkpoint()
            snapshot_id = str(theme["snapshot_id"])
            fingerprint = _document_hash(theme)
            if snapshot_id in themes and themes[snapshot_id] != fingerprint:
                raise ValueError("research bundle theme id has conflicting content")
            themes[snapshot_id] = fingerprint
        entries.append({"path": relative, "selected_date": selected_date,
                        "manifest_hash": _document_hash(manifest), "dataset_id": manifest["dataset_id"],
                        "captured_range": captured, "revision_count": len(dataset.observations),
                        "theme_snapshot_count": len(dataset.theme_snapshots),
                        "quality_summary": manifest.get("quality_summary", {})})
    scientific_entries = [{key: value for key, value in entry.items() if key != "path"} for entry in entries]
    identity = {"bundle_version": BUNDLE_VERSION, "boundary_policy": BUNDLE_BOUNDARY_POLICY,
                "children": scientific_entries}
    return {**identity, "children": entries, "bundle_id": "bundle-" + _document_hash(identity),
            "child_count": len(entries), "unique_revision_count": len(revisions),
            "unique_theme_snapshot_count": len(themes),
            "coverage_policy": "child_quality_preserved_gaps_unknown/v1"}


def write_frozen_research_bundle(root: Path, child_paths: tuple[str, ...], *, reserve_bytes=lambda count: None) -> FrozenResearchBundle:
    """Write only the index; original daily files and IDs remain untouched."""
    root = Path(root)
    children = [(relative, load_frozen_research_export(_bundle_child_path(root, relative)))
                for relative in child_paths]
    manifest = _bundle_manifest(children)
    path = root / "manifest.json"
    if path.exists():
        existing = load_frozen_research_bundle(root)
        if existing.manifest != manifest:
            raise ValueError("research bundle is immutable and cannot be overwritten")
        return existing
    # Exclusive creation also protects a concurrent index writer.
    from kiwoom_monitor.infrastructure.research_storage import research_storage_text_bytes
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
    reserve_bytes(research_storage_text_bytes(text))
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
    return FrozenResearchBundle(manifest, tuple(dataset for _, dataset in children))


def load_frozen_research_bundle(root: Path, *, checkpoint: Callable[[], None] = lambda: None) -> FrozenResearchBundle:
    root = Path(root)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research bundle manifest cannot be read") from exc
    if not isinstance(manifest, dict) or manifest.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError("research bundle version is unsupported")
    entries = manifest.get("children")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("research bundle children contract is invalid")
    children = [(str(entry.get("path", "")), load_frozen_research_export(
        _bundle_child_path(root, str(entry.get("path", ""))), checkpoint=checkpoint)) for entry in entries]
    if _bundle_manifest(children, checkpoint=checkpoint) != manifest:
        raise ValueError("research bundle manifest does not match its daily inputs")
    return FrozenResearchBundle(manifest, tuple(dataset for _, dataset in children))


def research_observation_order(value: dict[str, Any]) -> tuple[datetime, int, str]:
    """Observation availability, then the recorded ingest tie-breakers (never ordinal)."""
    try:
        available = datetime.fromisoformat(str(value.get("available_at", "")))
        if available.tzinfo is None:
            raise ValueError("naive availability")
        return (available.astimezone(timezone.utc), int(value.get("accepted_sequence", 0)),
                str(value["revision_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("research observation availability/order contract is invalid") from exc


def load_research_input(root: Path, *, session_profile: str | None = None,
                        checkpoint: Callable[[], None] = lambda: None) -> FrozenResearchDataset:
    """Adapt a verified bundle to the existing continuous run; never rewrite daily files."""
    try:
        manifest = json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research manifest cannot be read") from exc
    if not isinstance(manifest, dict):
        raise ValueError("research manifest must be an object")
    if manifest.get("bundle_version") is None:
        return load_frozen_research_export(root, checkpoint=checkpoint)
    bundle = load_frozen_research_bundle(root, checkpoint=checkpoint)
    first = bundle.datasets[0]
    if session_profile is None or research_session_profile_document(session_profile) != first.manifest["research_session_profile"]:
        raise ValueError("research bundle requires a matching explicit session profile")
    # Validate even a one-day bundle, but preserve its exact historical identity/results.
    for dataset in bundle.datasets:
        for row in dataset.observations:
            research_observation_order(row)
            checkpoint()
    if len(bundle.datasets) == 1:
        return first
    revisions: dict[str, dict[str, Any]] = {}
    themes: dict[str, dict[str, Any]] = {}
    for dataset in bundle.datasets:
        for row in dataset.observations:
            revisions.setdefault(str(row["revision_id"]), row)
        for theme in dataset.theme_snapshots:
            themes.setdefault(str(theme["snapshot_id"]), theme)
    observations = tuple(sorted(revisions.values(), key=research_observation_order))
    checkpoint()
    entries = [{key: value for key, value in entry.items() if key != "path"}
               for entry in bundle.manifest["children"]]
    runtime_manifest = {
        **{key: value for key, value in bundle.manifest.items() if key != "children"},
        "children": entries,
        "dataset_id": bundle.manifest["bundle_id"],
        "revision_count": len(observations),
        "revision_ids_hash": hashlib.sha256("\n".join(
            str(row["revision_id"]) for row in observations).encode("utf-8")).hexdigest(),
        "captured_range": {"start": entries[0]["captured_range"]["start"],
                           "end": entries[-1]["captured_range"]["end"]},
        "research_session_profile": first.manifest["research_session_profile"],
        "kinds": first.manifest["kinds"], "subject": first.manifest.get("subject", ""),
        "universe_rule": first.manifest.get("universe_rule"),
        "order_policy_version": first.manifest.get("order_policy_version"),
        "quality_summary": {"flags": ["child_quality_preserved", "coverage_not_evaluated"],
                            "recording_gap": "unknown"},
        "runtime_input_version": "continuous_bundle_replay/v1",
    }
    return FrozenResearchDataset(runtime_manifest, observations, tuple(themes.values()))


def campaign_input_scope(dataset: FrozenResearchDataset) -> dict[str, Any]:
    """Fixed study bounds; discovery never shifts evaluation or trading dates."""
    manifest = dataset.manifest
    captured = manifest.get('captured_range', {})
    if not captured.get('start') or not captured.get('end') or not manifest.get('kinds'):
        raise ValueError('automatic input discovery requires an explicit captured range and kinds')
    return {key: manifest.get(key) for key in ('captured_range', 'kinds', 'subject', 'research_session_profile', 'universe_rule', 'order_policy_version')}


def campaign_input_fingerprint(dataset: FrozenResearchDataset, *, checkpoint=lambda: None) -> str:
    """Hash evidence, excluding export watermark/id and transport ordinal."""
    digest = hashlib.sha256()
    for collection in (dataset.observations, dataset.theme_snapshots):
        digest.update(b'\ncollection\n')
        for index, row in enumerate(collection):
            if index % 128 == 0:
                checkpoint()
            if collection is dataset.observations and (row.get('kind') not in ('ranking', 'top20_membership', 'minute_bar') or (row.get('kind') == 'minute_bar' and row.get('venue') != 'KRX')):
                continue
            digest.update(json.dumps({key: value for key, value in row.items() if key != 'ordinal'}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
            digest.update(b'\n')
    return digest.hexdigest()


def research_input_encoded_bytes(root: Path) -> int:
    """Inspect bounded manifests/file sizes before decoding the frozen dataset."""
    seen: set[Path] = set()
    def size(directory: Path, child=False):
        path = directory / 'manifest.json'
        if path.stat().st_size > 1024 * 1024:
            from kiwoom_monitor.application.research_resources import ResearchResourceBlocked
            raise ResearchResourceBlocked('input_manifest_too_large')
        document = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(document, dict):
            raise ValueError('research manifest must be an object')
        if document.get('bundle_version') is not None:
            if child:
                raise ValueError('nested research bundles are unsupported')
            return sum(size(_bundle_child_path(directory, str(entry['path'])), True)
                       for entry in document['children'])
        total = path.stat().st_size
        for field in ('observations_file', 'theme_snapshots_file'):
            name = str(document.get(field, ''))
            if not name:
                continue
            file = (directory / name).resolve()
            if Path(name).name != name or not file.is_relative_to(directory.resolve()):
                raise ValueError('research input file must be local')
            if file not in seen:
                seen.add(file)
                total += file.stat().st_size
        return total
    return size(Path(root))

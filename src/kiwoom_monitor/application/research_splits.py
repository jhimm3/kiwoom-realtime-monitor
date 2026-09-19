"""D7 시간순 연구 구간과 경계 제외 규칙."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.application.market_session_schedule import SUPPORTED_RESEARCH_SESSION_PROFILES


SPLIT_SPEC_VERSION = "chronological_holdout/v1"
FOLD_STATE_POLICY = "continuous_state_and_cash/v1"
PERIOD_END_POSITION_POLICY = "censor_open_position/v1"
FIT_POLICY = "no_fitted_parameters/v1"
DEVELOPMENT_PARTITION_VERSION = 'development_partition/v2'
PARTITION_STATE_POLICY = 'reset_state_and_cash_per_partition/v1'
SYMBOL_DEVELOPMENT_PARTITION_VERSION = 'development_partition/v3'
SYMBOL_HASH_PARTITION_VERSION = 'stock_hash_partition/v1'
_ROLE_ORDER = {"TRAIN": 0, "VALIDATION": 1, "OOS": 2}


@dataclass(frozen=True)
class ResearchFoldSpec:
    name: str
    role: str
    start: str
    end: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("research fold name is required")
        if self.role not in _ROLE_ORDER:
            raise ValueError(f"unsupported research fold role: {self.role}")
        if _aware_datetime(self.end) <= _aware_datetime(self.start):
            raise ValueError("research fold end must be after start")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchEvaluationSpec:
    version: str
    folds: tuple[ResearchFoldSpec, ...]
    warmup_seconds: int
    gap_seconds: int
    purge_seconds: int
    minimum_closed_trades: int
    minimum_active_days: int
    fold_state_policy: str = FOLD_STATE_POLICY
    period_end_position_policy: str = PERIOD_END_POSITION_POLICY
    fit_policy: str = FIT_POLICY
    final_holdout_accessed_at: str = ""
    final_holdout_access_reason: str = ""

    def __post_init__(self) -> None:
        if self.version != SPLIT_SPEC_VERSION:
            raise ValueError(f"unregistered research split spec: {self.version}")
        if not self.folds:
            raise ValueError("at least one chronological research fold is required")
        if len({fold.name for fold in self.folds}) != len(self.folds):
            raise ValueError("research fold names must be unique")
        if min(
            self.warmup_seconds, self.gap_seconds, self.purge_seconds,
            self.minimum_closed_trades, self.minimum_active_days,
        ) < 0:
            raise ValueError("research split durations and eligibility limits must not be negative")
        if self.fold_state_policy != FOLD_STATE_POLICY:
            raise ValueError(f"unregistered fold state policy: {self.fold_state_policy}")
        if self.period_end_position_policy != PERIOD_END_POSITION_POLICY:
            raise ValueError(
                f"unregistered period-end position policy: {self.period_end_position_policy}"
            )
        if self.fit_policy != FIT_POLICY:
            raise ValueError(f"unregistered fit policy: {self.fit_policy}")
        previous: ResearchFoldSpec | None = None
        previous_role = -1
        for fold in self.folds:
            role_order = _ROLE_ORDER[fold.role]
            if role_order < previous_role:
                raise ValueError("research folds must follow TRAIN, VALIDATION, OOS order")
            if previous is not None:
                gap = _aware_datetime(fold.start) - _aware_datetime(previous.end)
                if gap < timedelta(seconds=self.gap_seconds):
                    raise ValueError("research folds overlap or do not satisfy the configured gap")
            previous = fold
            previous_role = role_order
        if self.final_holdout_accessed_at:
            accessed = _aware_datetime(self.final_holdout_accessed_at)
            if not self.final_holdout_access_reason.strip():
                raise ValueError("final holdout access requires a reason")
            oos = tuple(fold for fold in self.folds if fold.role == "OOS")
            if oos and accessed.astimezone(timezone.utc) < _aware_datetime(oos[-1].end).astimezone(timezone.utc):
                raise ValueError("final holdout cannot be accessed before its fold ends")
        elif self.final_holdout_access_reason.strip():
            raise ValueError("final holdout access reason requires accessed_at")

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["folds"] = [fold.to_dict() for fold in self.folds]
        return document

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchEvaluationSpec":
        raw_folds = value.get("folds")
        if not isinstance(raw_folds, list):
            raise ValueError("evaluation spec folds must be a list")
        if any(not isinstance(row, Mapping) for row in raw_folds):
            raise ValueError("each evaluation fold must be an object")
        return cls(
            version=str(value.get("version", "")),
            folds=tuple(ResearchFoldSpec(
                name=str(row.get("name", "")), role=str(row.get("role", "")),
                start=str(row.get("start", "")), end=str(row.get("end", "")),
            ) for row in raw_folds if isinstance(row, Mapping)),
            warmup_seconds=int(value.get("warmup_seconds", 0)),
            gap_seconds=int(value.get("gap_seconds", 0)),
            purge_seconds=int(value.get("purge_seconds", 0)),
            minimum_closed_trades=int(value.get("minimum_closed_trades", 0)),
            minimum_active_days=int(value.get("minimum_active_days", 0)),
            fold_state_policy=str(value.get("fold_state_policy", FOLD_STATE_POLICY)),
            period_end_position_policy=str(
                value.get("period_end_position_policy", PERIOD_END_POSITION_POLICY)
            ),
            fit_policy=str(value.get("fit_policy", FIT_POLICY)),
            final_holdout_accessed_at=str(value.get("final_holdout_accessed_at", "")),
            final_holdout_access_reason=str(value.get("final_holdout_access_reason", "")),
        )


@dataclass(frozen=True)
class FinalHoldoutBatchSpec:
    """Locked scientific candidate hashes and one strict-KRX final window."""

    version: str
    dataset_id: str
    dataset_hash: str
    session_profile: str
    candidate_spec_hashes: tuple[str, ...]
    evaluation: ResearchEvaluationSpec

    def __post_init__(self) -> None:
        if self.version != 'final_holdout_batch/v1':
            raise ValueError('unregistered final holdout batch')
        if not isinstance(self.dataset_id, str) or not self.dataset_id.strip() or len(self.dataset_id) > 256:
            raise ValueError('final dataset identity is required')
        if not isinstance(self.dataset_hash, str) or re.fullmatch('[0-9a-f]{64}', self.dataset_hash) is None:
            raise ValueError('final dataset hash must be SHA256')
        if self.session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
            raise ValueError('final session profile is unsupported')
        hashes = self.candidate_spec_hashes
        if (not isinstance(hashes, tuple) or not 1 <= len(hashes) <= 200
                or any(not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None for value in hashes)
                or tuple(sorted(set(hashes))) != hashes):
            raise ValueError('final candidate hashes must be immutable, unique and sorted (1 to 200)')
        if (not isinstance(self.evaluation, ResearchEvaluationSpec) or len(self.evaluation.folds) != 1
                or self.evaluation.folds[0].role != 'OOS' or self.evaluation.final_holdout_accessed_at):
            raise ValueError('final batch requires one unexposed OOS fold')
        if (not isinstance(self.evaluation.folds, tuple)
                or any(type(getattr(self.evaluation, key)) is not int for key in (
                    'warmup_seconds', 'gap_seconds', 'purge_seconds', 'minimum_closed_trades', 'minimum_active_days'))):
            raise ValueError('final evaluation policy must be immutable with integer limits')

    @property
    def start(self) -> str:
        return _aware_datetime(self.evaluation.folds[0].start).astimezone(timezone.utc).isoformat(timespec='microseconds')

    @property
    def end(self) -> str:
        return _aware_datetime(self.evaluation.folds[0].end).astimezone(timezone.utc).isoformat(timespec='microseconds')

    @property
    def window_id(self) -> str:
        # A new file/revision/profile does not make overlapping KRX data unseen.
        encoded = json.dumps(['strict_krx_final_window/v1', self.start, self.end], separators=(',', ':'))
        return 'final_window_' + hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def batch_id(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return 'final_batch_' + hashlib.sha256(encoded.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        evaluation = self.evaluation.to_dict()
        evaluation['folds'][0].update(start=self.start, end=self.end)
        return {'version': self.version, 'dataset_id': self.dataset_id, 'dataset_hash': self.dataset_hash,
                'session_profile': self.session_profile, 'candidate_spec_hashes': list(self.candidate_spec_hashes),
                'evaluation': evaluation}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'FinalHoldoutBatchSpec':
        fields = {'version', 'dataset_id', 'dataset_hash', 'session_profile', 'candidate_spec_hashes', 'evaluation'}
        if not isinstance(value, Mapping) or set(value) != fields or not isinstance(value['candidate_spec_hashes'], list):
            raise ValueError('final batch fields are invalid')
        policy = value['evaluation']
        policy_fields = {'version', 'folds', 'warmup_seconds', 'gap_seconds', 'purge_seconds', 'minimum_closed_trades',
                         'minimum_active_days', 'fold_state_policy', 'period_end_position_policy', 'fit_policy',
                         'final_holdout_accessed_at', 'final_holdout_access_reason'}
        if (not isinstance(policy, Mapping) or set(policy) != policy_fields
                or any(type(policy[key]) is not int for key in ('warmup_seconds', 'gap_seconds', 'purge_seconds', 'minimum_closed_trades', 'minimum_active_days'))):
            raise ValueError('final evaluation fields or integer limits are invalid')
        return cls(value['version'], value['dataset_id'], value['dataset_hash'], value['session_profile'],
                   tuple(value['candidate_spec_hashes']), ResearchEvaluationSpec.from_dict(policy))


@dataclass(frozen=True)
class DevelopmentSymbolPartitionSpec:
    """Stable target assignment; peer market context is not filtered or re-ranked."""

    version: str
    salt: str
    bucket_count: int
    bucket: int

    def __post_init__(self) -> None:
        if self.version != SYMBOL_HASH_PARTITION_VERSION:
            raise ValueError('unregistered stock hash partition version')
        if not isinstance(self.salt, str) or not self.salt.strip() or len(self.salt) > 128 or '\x00' in self.salt:
            raise ValueError('stock partition salt must be a nonempty string of at most 128 characters')
        if type(self.bucket_count) is not int or not 2 <= self.bucket_count <= 20:
            raise ValueError('stock partition bucket_count must be an integer from 2 to 20')
        if type(self.bucket) is not int or not 0 <= self.bucket < self.bucket_count:
            raise ValueError('stock partition bucket must be an integer within bucket_count')

    def bucket_for(self, stock_code: str) -> int:
        if not isinstance(stock_code, str):
            raise ValueError('stock partition requires a canonical six-character stock code')
        code = normalize_stock_code(stock_code)
        if re.fullmatch(r'[0-9A-Z]{6}', code) is None:
            raise ValueError('stock partition requires a canonical six-character stock code')
        encoded = json.dumps([self.version, self.salt, code], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return int.from_bytes(hashlib.sha256(encoded).digest(), 'big') % self.bucket_count

    def allows(self, stock_code: str) -> bool:
        return self.bucket_for(stock_code) == self.bucket

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'DevelopmentSymbolPartitionSpec':
        if not isinstance(value, Mapping) or set(value) != {'version', 'salt', 'bucket_count', 'bucket'}:
            raise ValueError('unknown or missing stock hash partition fields')
        return cls(value['version'], value['salt'], value['bucket_count'], value['bucket'])


@dataclass(frozen=True)
class DevelopmentPartitionSpec:
    """Explicit independent development selection; legacy folds retain their meaning."""

    version: str
    fold_name: str
    state_policy: str = PARTITION_STATE_POLICY
    symbol_partition: DevelopmentSymbolPartitionSpec | None = None

    def __post_init__(self) -> None:
        valid_version = ((self.version == DEVELOPMENT_PARTITION_VERSION and self.symbol_partition is None)
                         or (self.version == SYMBOL_DEVELOPMENT_PARTITION_VERSION
                             and isinstance(self.symbol_partition, DevelopmentSymbolPartitionSpec)))
        if not valid_version or self.state_policy != PARTITION_STATE_POLICY:
            raise ValueError('unregistered development partition policy')
        if not self.fold_name.strip():
            raise ValueError('development partition fold name is required')

    def evaluation_for(self, evaluation: ResearchEvaluationSpec) -> ResearchEvaluationSpec:
        if evaluation.final_holdout_accessed_at:
            raise ValueError('exposed final policy cannot be used as an unused development partition policy')
        fold = next((fold for fold in evaluation.folds if fold.name == self.fold_name), None)
        if fold is None or fold.role not in ('TRAIN', 'VALIDATION'):
            raise ValueError('development partition must select a TRAIN or VALIDATION fold; final access is disabled')
        return ResearchEvaluationSpec(
            SPLIT_SPEC_VERSION, (fold,), evaluation.warmup_seconds, 0,
            evaluation.purge_seconds, evaluation.minimum_closed_trades, evaluation.minimum_active_days,
        )

    def to_dict(self) -> dict[str, Any]:
        document = {'version': self.version, 'fold_name': self.fold_name, 'state_policy': self.state_policy}
        if self.symbol_partition is not None:
            document['symbol_partition'] = self.symbol_partition.to_dict()
        return document

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'DevelopmentPartitionSpec':
        if (set(value) - {'version', 'fold_name', 'state_policy', 'symbol_partition'}
                or value.get('version') == DEVELOPMENT_PARTITION_VERSION and 'symbol_partition' in value):
            raise ValueError('unknown development partition policy fields')
        symbol = DevelopmentSymbolPartitionSpec.from_dict(value['symbol_partition']) if 'symbol_partition' in value else None
        return cls(str(value.get('version', '')), str(value.get('fold_name', '')),
                   str(value.get('state_policy', PARTITION_STATE_POLICY)), symbol)


@dataclass(frozen=True)
class FoldAssignment:
    fold_name: str
    status: str
    reason: str


def assign_interval_to_fold(
    spec: ResearchEvaluationSpec,
    started_at: str,
    ended_at: str,
) -> FoldAssignment:
    """시작과 결과가 같은 평가 구간 안에 있을 때만 표본을 포함한다."""
    started = _aware_datetime(started_at).astimezone(timezone.utc)
    ended = _aware_datetime(ended_at).astimezone(timezone.utc)
    if ended < started:
        raise ValueError("research result end must not precede start")
    for fold in spec.folds:
        fold_start = _aware_datetime(fold.start).astimezone(timezone.utc)
        fold_end = _aware_datetime(fold.end).astimezone(timezone.utc)
        eligible_end = fold_end - timedelta(seconds=spec.purge_seconds)
        if fold_start <= started < fold_end:
            if ended >= eligible_end:
                return FoldAssignment(fold.name, "PURGED", "result_crosses_fold_purge_boundary")
            return FoldAssignment(fold.name, "INCLUDED", "")
    return FoldAssignment("", "OUTSIDE", "sample_start_outside_evaluation_folds")


def warmup_start(spec: ResearchEvaluationSpec) -> datetime:
    first = _aware_datetime(spec.folds[0].start).astimezone(timezone.utc)
    return first - timedelta(seconds=spec.warmup_seconds)


def _aware_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid research split timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError("research split timestamps must be timezone-aware")
    return parsed

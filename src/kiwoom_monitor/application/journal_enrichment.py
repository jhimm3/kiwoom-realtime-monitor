"""매매일지 자동보완 작업의 식별자와 재시도 정책."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.order_contract import AccountScope, LEGACY_ACCOUNT_SCOPE


ENRICHMENT_STATES = (
    "pending",
    "running",
    "complete",
    "partial",
    "retryable_failed",
    "unavailable",
    "cancelled",
)

ENRICHMENT_KINDS = (
    "fills",
    "costs",
    "minute_bars",
    "daily_bars",
    "market_index",
    "post_trade_news",
    "investor_flow",
    "derived_analysis",
)

JOURNAL_EXECUTION_PROJECTION = "central_mock_execution/v1"
_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class EnrichmentTask:
    task_id: str
    target_type: str
    target_ref: str
    kind: str
    input_fingerprint: str
    policy_version: str
    state: str = "pending"
    attempts: int = 0
    next_retry_at: datetime | None = None
    owner: str = ""
    result: Mapping[str, object] | None = None
    last_error: str = ""
    origin_scope: AccountScope = LEGACY_ACCOUNT_SCOPE
    canonical_scope: AccountScope | None = None

    def __post_init__(self) -> None:
        _validate_canonical_scope(self.origin_scope, self.canonical_scope)

    @property
    def effective_scope(self) -> AccountScope:
        return self.canonical_scope or self.origin_scope


@dataclass(frozen=True)
class JournalAnalysisRevision:
    revision_id: str
    group_id: str
    analysis_kind: str
    input_fingerprint: str
    analysis_version: str
    content: Mapping[str, object]
    created_at: datetime
    origin_scope: AccountScope = LEGACY_ACCOUNT_SCOPE
    canonical_scope: AccountScope | None = None

    def __post_init__(self) -> None:
        _validate_canonical_scope(self.origin_scope, self.canonical_scope)

    @property
    def effective_scope(self) -> AccountScope:
        return self.canonical_scope or self.origin_scope


@dataclass(frozen=True)
class JournalResearchLink:
    link_id: str
    execution_ref: str
    run_id: str | None = None
    decision_id: str | None = None
    snapshot_id: str | None = None
    entry_thesis_id: str | None = None
    evidence_timing: str = "unverified"
    available_at: datetime | None = None
    created_at: datetime | None = None
    origin_scope: AccountScope = LEGACY_ACCOUNT_SCOPE
    canonical_scope: AccountScope | None = None

    def __post_init__(self) -> None:
        _validate_canonical_scope(self.origin_scope, self.canonical_scope)

    @property
    def effective_scope(self) -> AccountScope:
        return self.canonical_scope or self.origin_scope


@dataclass(frozen=True)
class JournalExecutionProjectionEvent:
    source_event_id: str
    accepted_sequence: int
    event_type: str
    trading_date: str
    intent_id: str
    run_id: str
    decision_id: str
    broker_order_id: str
    broker_execution_id: str
    stock_code: str
    venue: str
    side: str
    occurred_at: datetime
    received_at: datetime
    quantity: int
    price: int
    broker_as_of: datetime | None
    fill_identity: str | None
    content_hash: str


@dataclass(frozen=True)
class JournalExecutionProjectionPage:
    projection_name: str
    from_cursor: int
    next_cursor: int
    has_more: bool
    events: tuple[JournalExecutionProjectionEvent, ...]
    origin_scope: AccountScope
    canonical_scope: AccountScope | None = None

    def __post_init__(self) -> None:
        _validate_canonical_scope(self.origin_scope, self.canonical_scope)

    @property
    def effective_scope(self) -> AccountScope:
        return self.canonical_scope or self.origin_scope


@dataclass(frozen=True)
class JournalExecutionProjectionResult:
    cursor: int
    inserted_event_count: int
    detailed_fill_count: int
    aggregate_event_count: int
    unresolved_quantity_by_intent: Mapping[str, int]


class JournalEnrichmentRepository(Protocol):
    def ensure_enrichment_task(
        self, task: EnrichmentTask, now: datetime | None = None,
    ) -> EnrichmentTask: ...

    def load_enrichment_task(self, task_id: str) -> EnrichmentTask | None: ...

    def transition_enrichment_task(
        self,
        task_id: str,
        *,
        expected_states: tuple[str, ...],
        state: str,
        now: datetime,
        owner: str = "",
        next_retry_at: datetime | None = None,
        result: Mapping[str, object] | None = None,
        error: str = "",
        increment_attempts: bool = False,
    ) -> bool: ...

    def recover_running_enrichment_tasks(self, now: datetime) -> int: ...


class JournalExecutionProjectionSource(Protocol):
    def load_mock_execution_events(
        self, *, after_sequence: int = 0, limit: int = 500,
    ) -> Mapping[str, object]: ...


class JournalExecutionProjectionRepository(Protocol):
    def load_execution_projection_cursor(
        self, projection_name: str, account_scope: AccountScope,
    ) -> int: ...

    def project_execution_event_page(
        self, page: JournalExecutionProjectionPage, now: datetime | None = None,
    ) -> JournalExecutionProjectionResult: ...


class JournalEnrichmentService:
    """기존 보완 worker 앞에서 중복·완료·재시도 상태만 조정한다."""

    POLICY_VERSION = "journal-enrichment/v1"
    MAX_ATTEMPTS = 3

    def __init__(self, repository: JournalEnrichmentRepository, owner: str) -> None:
        self._repository = repository
        self._owner = owner

    def register(
        self,
        target_type: str,
        target_ref: str,
        kind: str,
        inputs: Mapping[str, object],
        *,
        now: datetime | None = None,
        account_scope: AccountScope = LEGACY_ACCOUNT_SCOPE,
        canonical_scope: AccountScope | None = None,
    ) -> EnrichmentTask:
        if kind not in ENRICHMENT_KINDS:
            raise ValueError(f"지원하지 않는 매매일지 보완 종류입니다: {kind}")
        fingerprint = enrichment_fingerprint(inputs)
        task = EnrichmentTask(
            enrichment_task_id(
                target_type, target_ref, kind, fingerprint, self.POLICY_VERSION, account_scope,
            ),
            target_type,
            target_ref,
            kind,
            fingerprint,
            self.POLICY_VERSION,
            origin_scope=account_scope,
            canonical_scope=canonical_scope,
        )
        return self._repository.ensure_enrichment_task(task, now)

    def can_start(
        self, task: EnrichmentTask, now: datetime | None = None, *, force: bool = False,
    ) -> bool:
        if task.state in {"complete", "unavailable", "cancelled", "running"}:
            return False
        current = now or datetime.now()
        return force or task.next_retry_at is None or task.next_retry_at <= current

    def start(self, task_id: str, now: datetime | None = None, *, force: bool = False) -> bool:
        current = now or datetime.now()
        task = self._repository.load_enrichment_task(task_id)
        if task is None or not self.can_start(task, current, force=force):
            return False
        return self._repository.transition_enrichment_task(
            task_id,
            expected_states=("pending", "partial", "retryable_failed"),
            state="running",
            now=current,
            owner=self._owner,
            increment_attempts=True,
        )

    def complete(
        self, task_id: str, result: Mapping[str, object], now: datetime | None = None,
    ) -> bool:
        return self._finish(task_id, "complete", result, now=now)

    def partial(
        self,
        task_id: str,
        result: Mapping[str, object],
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now()
        return self._repository.transition_enrichment_task(
            task_id,
            expected_states=("running",),
            state="partial",
            now=current,
            next_retry_at=current + timedelta(minutes=30),
            result=result,
            error=reason,
        )

    def fail(self, task_id: str, error: str, now: datetime | None = None) -> bool:
        current = now or datetime.now()
        task = self._repository.load_enrichment_task(task_id)
        if task is None or task.state != "running":
            return False
        terminal = task.attempts >= self.MAX_ATTEMPTS
        return self._repository.transition_enrichment_task(
            task_id,
            expected_states=("running",),
            state="unavailable" if terminal else "retryable_failed",
            now=current,
            next_retry_at=None if terminal else current + _retry_delay(task.attempts),
            error=error,
        )

    def unavailable(
        self,
        task_id: str,
        reason: str,
        result: Mapping[str, object] | None = None,
        now: datetime | None = None,
    ) -> bool:
        return self._repository.transition_enrichment_task(
            task_id,
            expected_states=("running",),
            state="unavailable",
            now=now or datetime.now(),
            result=result or {},
            error=reason,
        )

    def recover_interrupted(self, now: datetime | None = None) -> int:
        return self._repository.recover_running_enrichment_tasks(now or datetime.now())

    def _finish(
        self,
        task_id: str,
        state: str,
        result: Mapping[str, object],
        *,
        now: datetime | None,
    ) -> bool:
        return self._repository.transition_enrichment_task(
            task_id,
            expected_states=("running",),
            state=state,
            now=now or datetime.now(),
            result=result,
        )


def enrichment_fingerprint(inputs: Mapping[str, object]) -> str:
    payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enrichment_task_id(
    target_type: str,
    target_ref: str,
    kind: str,
    input_fingerprint: str,
    policy_version: str,
    account_scope: AccountScope = LEGACY_ACCOUNT_SCOPE,
) -> str:
    parts = (target_type, target_ref, kind, input_fingerprint, policy_version)
    if account_scope != LEGACY_ACCOUNT_SCOPE:
        parts = (*parts, account_scope.broker, account_scope.environment.value, account_scope.account_ref)
    value = "\x1f".join(parts)
    return "jenr-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def journal_analysis_revision(
    group_id: str,
    analysis_kind: str,
    inputs: Mapping[str, object],
    analysis_version: str,
    content: Mapping[str, object],
    *,
    now: datetime | None = None,
    account_scope: AccountScope = LEGACY_ACCOUNT_SCOPE,
    canonical_scope: AccountScope | None = None,
) -> JournalAnalysisRevision:
    fingerprint = enrichment_fingerprint(inputs)
    identity_parts = (group_id, analysis_kind, fingerprint, analysis_version)
    if account_scope != LEGACY_ACCOUNT_SCOPE:
        identity_parts = (*identity_parts, account_scope.broker, account_scope.environment.value, account_scope.account_ref)
    identity = "\x1f".join(identity_parts)
    normalized_content = json.loads(json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    return JournalAnalysisRevision(
        "jarev-" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        group_id,
        analysis_kind,
        fingerprint,
        analysis_version,
        dict(normalized_content),
        (now or datetime.now()).replace(microsecond=0),
        account_scope,
        canonical_scope,
    )


def journal_research_link(
    execution_ref: str,
    *,
    run_id: str | None = None,
    decision_id: str | None = None,
    snapshot_id: str | None = None,
    entry_thesis_id: str | None = None,
    evidence_timing: str = "unverified",
    available_at: datetime | None = None,
    now: datetime | None = None,
    account_scope: AccountScope = LEGACY_ACCOUNT_SCOPE,
    canonical_scope: AccountScope | None = None,
) -> JournalResearchLink:
    if not execution_ref:
        raise ValueError("체결 참조가 필요합니다.")
    if not any((run_id, decision_id, snapshot_id, entry_thesis_id)):
        raise ValueError("연결할 연구 근거 참조가 하나 이상 필요합니다.")
    if evidence_timing not in {"at_execution", "post_trade", "unverified"}:
        raise ValueError(f"지원하지 않는 근거 시점입니다: {evidence_timing}")
    identity_parts = (
        execution_ref, run_id or "", decision_id or "", snapshot_id or "",
        entry_thesis_id or "", evidence_timing,
    )
    if account_scope != LEGACY_ACCOUNT_SCOPE:
        identity_parts = (*identity_parts, account_scope.broker, account_scope.environment.value, account_scope.account_ref)
    identity = "\x1f".join(identity_parts)
    return JournalResearchLink(
        "jrlink-" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        execution_ref,
        run_id,
        decision_id,
        snapshot_id,
        entry_thesis_id,
        evidence_timing,
        available_at,
        (now or datetime.now()).replace(microsecond=0),
        account_scope,
        canonical_scope,
    )


def news_evidence_timing(
    news: Mapping[str, object], executed_at: datetime,
) -> str:
    """N1 revision과 실제 가용시각이 모두 있을 때만 당시 근거로 인정한다."""
    revision_id = news.get("revision_id") or news.get("article_revision_id")
    available_value = news.get("available_at")
    if not revision_id or available_value in {None, ""}:
        return "unverified"
    try:
        if isinstance(available_value, (int, float)):
            available = datetime.fromtimestamp(float(available_value), tz=executed_at.tzinfo)
        else:
            available = datetime.fromisoformat(str(available_value))
        if available.tzinfo is None and executed_at.tzinfo is not None:
            available = available.replace(tzinfo=executed_at.tzinfo)
        comparable_execution = executed_at
        if comparable_execution.tzinfo is None and available.tzinfo is not None:
            comparable_execution = comparable_execution.replace(tzinfo=available.tzinfo)
        return "at_execution" if available <= comparable_execution else "post_trade"
    except (TypeError, ValueError, OSError):
        return "unverified"


def journal_execution_projection_page(
    source_page: Mapping[str, object],
    *,
    from_cursor: int,
    account_scope: AccountScope,
    canonical_scope: AccountScope | None = None,
) -> JournalExecutionProjectionPage:
    """Validate one central ledger page and retain only fill evidence rows."""
    if account_scope == LEGACY_ACCOUNT_SCOPE or account_scope.environment.value != "mock":
        raise ValueError("execution projection requires a verified mock account scope")
    _validate_canonical_scope(account_scope, canonical_scope)
    if type(from_cursor) is not int or from_cursor < 0:
        raise ValueError("execution projection cursor is invalid")
    next_cursor = source_page.get("next_cursor")
    has_more = source_page.get("has_more")
    source_events = source_page.get("events")
    if (type(next_cursor) is not int or next_cursor < from_cursor
            or type(has_more) is not bool or not isinstance(source_events, list)):
        raise ValueError("execution projection page is invalid")
    normalized: list[JournalExecutionProjectionEvent] = []
    sequences: list[int] = []
    for source in source_events:
        if not isinstance(source, Mapping):
            raise ValueError("execution projection event must be an object")
        sequence = source.get("accepted_sequence")
        if type(sequence) is not int or sequence <= from_cursor:
            raise ValueError("execution projection sequence is invalid")
        sequences.append(sequence)
        if source.get("environment") != "mock" or source.get("account_ref") != account_scope.account_ref:
            raise ValueError("execution projection event crossed account scope")
        event_type = str(source.get("event_type") or "")
        if event_type not in {"FILL", "BROKER_FILL_AGGREGATE"}:
            continue
        normalized.append(_journal_execution_projection_event(source, event_type, account_scope))
    if sequences != sorted(set(sequences)):
        raise ValueError("execution projection sequences must be strictly increasing")
    if sequences:
        if next_cursor != sequences[-1]:
            raise ValueError("execution projection cursor does not match the last event")
    elif next_cursor != from_cursor:
        raise ValueError("an empty execution projection page cannot advance its cursor")
    return JournalExecutionProjectionPage(
        JOURNAL_EXECUTION_PROJECTION,
        from_cursor,
        next_cursor,
        has_more,
        tuple(normalized),
        account_scope,
        canonical_scope,
    )


def sync_journal_execution_projection(
    source: JournalExecutionProjectionSource,
    repository: JournalExecutionProjectionRepository,
    account_scope: AccountScope,
    *,
    canonical_scope: AccountScope | None = None,
    page_limit: int = 500,
    max_pages: int = 20,
    now: datetime | None = None,
) -> JournalExecutionProjectionResult:
    """Pull bounded account pages and atomically advance the local projection cursor."""
    if not 1 <= page_limit <= 1000 or not 1 <= max_pages <= 100:
        raise ValueError("execution projection page bounds are invalid")
    cursor = repository.load_execution_projection_cursor(
        JOURNAL_EXECUTION_PROJECTION, account_scope,
    )
    inserted = detailed = aggregate = 0
    unresolved: dict[str, int] = {}
    for _ in range(max_pages):
        source_page = source.load_mock_execution_events(
            after_sequence=cursor, limit=page_limit,
        )
        page = journal_execution_projection_page(
            source_page,
            from_cursor=cursor,
            account_scope=account_scope,
            canonical_scope=canonical_scope,
        )
        result = repository.project_execution_event_page(page, now)
        cursor = result.cursor
        inserted += result.inserted_event_count
        detailed += result.detailed_fill_count
        aggregate += result.aggregate_event_count
        unresolved.update(result.unresolved_quantity_by_intent)
        if not page.has_more:
            return JournalExecutionProjectionResult(
                cursor, inserted, detailed, aggregate, unresolved,
            )
        if page.next_cursor == page.from_cursor:
            raise RuntimeError("execution projection source did not advance its cursor")
    raise RuntimeError("execution projection exceeded the bounded page count")


def _journal_execution_projection_event(
    source: Mapping[str, object], event_type: str, account_scope: AccountScope,
) -> JournalExecutionProjectionEvent:
    source_event_id = str(source.get("source_event_id") or "")
    intent_id = str(source.get("intent_id") or "")
    run_id = str(source.get("run_id") or "")
    decision_id = str(source.get("decision_id") or "")
    broker_order_id = str(source.get("broker_order_id") or "")
    broker_execution_id = str(source.get("broker_execution_id") or "")
    stock_code = str(source.get("symbol") or "")
    venue = str(source.get("venue") or "")
    side = str(source.get("side") or "")
    if not all((source_event_id, intent_id, run_id, decision_id, broker_order_id, stock_code, side)):
        raise ValueError("execution projection identity is incomplete")
    if event_type == "FILL" and not broker_execution_id:
        raise ValueError("detailed fill requires a broker execution id")
    occurred_at = datetime.fromisoformat(str(source.get("occurred_at") or ""))
    received_at = datetime.fromisoformat(str(source.get("received_at") or ""))
    broker_as_of_value = source.get("broker_as_of")
    broker_as_of = datetime.fromisoformat(str(broker_as_of_value)) if broker_as_of_value else None
    if occurred_at.tzinfo is None or received_at.tzinfo is None or (
        broker_as_of is not None and broker_as_of.tzinfo is None
    ):
        raise ValueError("execution projection timestamps must be timezone-aware")
    quantity = int(source.get("quantity") or 0)
    price = int(source.get("price") or 0)
    if quantity <= 0 or (event_type == "FILL" and price <= 0):
        raise ValueError("execution projection quantity or price is invalid")
    trading_date = occurred_at.astimezone(_KST).date().isoformat()
    fill_identity = None
    if event_type == "FILL":
        fill_identity = "jfill-" + hashlib.sha256("\x1f".join((
            account_scope.broker,
            account_scope.environment.value,
            account_scope.account_ref,
            trading_date,
            broker_order_id,
            broker_execution_id,
        )).encode("utf-8")).hexdigest()
    content = {
        "event_type": event_type,
        "trading_date": trading_date,
        "broker_order_id": broker_order_id,
        "broker_execution_id": broker_execution_id,
        "stock_code": stock_code,
        "venue": venue,
        "side": side,
        "occurred_at": occurred_at.isoformat(),
        "quantity": quantity,
        "price": price,
    }
    content_hash = hashlib.sha256(json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return JournalExecutionProjectionEvent(
        source_event_id,
        int(source["accepted_sequence"]),
        event_type,
        trading_date,
        intent_id,
        run_id,
        decision_id,
        broker_order_id,
        broker_execution_id,
        stock_code,
        venue,
        side,
        occurred_at,
        received_at,
        quantity,
        price,
        broker_as_of,
        fill_identity,
        content_hash,
    )


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(minutes=min(60, 5 * (2 ** max(0, attempts - 1))))


def _validate_canonical_scope(
    origin: AccountScope, canonical: AccountScope | None,
) -> None:
    if canonical is not None and (
        canonical.broker != origin.broker or canonical.environment != origin.environment
    ):
        raise ValueError("canonical journal scope cannot cross broker or environment")

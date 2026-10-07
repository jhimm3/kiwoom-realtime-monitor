"""매매일지 기간 조회와 목록 필터를 저장소·Qt 세부사항 밖에서 조정한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import (
    TradeEpisode,
    TradeReview,
    group_trade_episodes,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


class TradeHistoryQueryRepository(Protocol):
    def load_fills_with_entry_context(
        self, start: datetime, end: datetime, lookback_days: int = 365,
        account_scope: AccountScope | None = None,
    ) -> tuple[TradeFill, ...]: ...

    def load_trade_costs(
        self, start: datetime, end: datetime, account_scope: AccountScope | None = None,
    ) -> tuple[DailyTradeCost, ...]: ...

    def load_group_overrides(self) -> dict[str, str]: ...

    def load_review(self, group_id: str) -> TradeReview: ...

    def load_execution_fill_projections(
        self,
        account_scope: AccountScope,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True)
class TradeHistoryQueryResult:
    fills: tuple[TradeFill, ...]
    costs: tuple[DailyTradeCost, ...]
    episodes: tuple[TradeEpisode, ...]


@dataclass(frozen=True)
class TradeHistorySelection:
    stock_code: str
    selected_day: date
    focus: datetime | None
    date_range: tuple[date, date]
    fills: tuple[TradeFill, ...]


FILL_RECONCILIATION_STATUSES = (
    "EXACT",
    "DETAIL_ONLY",
    "SUMMARY_ONLY",
    "PARTIAL",
    "CONFLICT",
)
_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class ExecutionFillEvidence:
    """중앙 실행 원장의 상세 체결 한 건과 연구 계보."""

    source_event_id: str
    broker_execution_id: str
    intent_id: str
    run_id: str
    decision_id: str
    fill: TradeFill


@dataclass(frozen=True)
class FillReconciliationGroup:
    """kt00007 묶음과 중앙 상세 체결 묶음의 주문 단위 대조 결과."""

    account_scope: AccountScope
    trading_date: date
    broker_order_id: str
    stock_code: str
    side: str
    status: str
    summary_fills: tuple[TradeFill, ...]
    detailed_fills: tuple[ExecutionFillEvidence, ...]
    selected_fills: tuple[TradeFill, ...]
    summary_quantity: int
    detailed_quantity: int
    summary_amount: int
    detailed_amount: int
    reason: str

    @property
    def fill_confirmed(self) -> bool:
        return self.status in {"EXACT", "DETAIL_ONLY"}


@dataclass(frozen=True)
class TradeFillReconciliation:
    """손익 계산자가 출처를 합산하지 않고 사용할 단일 체결 projection."""

    fills: tuple[TradeFill, ...]
    groups: tuple[FillReconciliationGroup, ...]

    @property
    def has_unconfirmed(self) -> bool:
        return any(not group.fill_confirmed for group in self.groups)


@dataclass(frozen=True)
class TradeHistoryReconciliationResult:
    reconciliation: TradeFillReconciliation
    costs: tuple[DailyTradeCost, ...]


def trade_episode_selection(episode: TradeEpisode) -> TradeHistorySelection:
    fills = tuple(sorted(episode.fills, key=lambda value: value.filled_at))
    date_range = (
        min(fill.filled_at.date() for fill in fills),
        max(fill.filled_at.date() for fill in fills),
    ) if fills else (episode.summary.trade_date, episode.summary.trade_date)
    return TradeHistorySelection(
        episode.summary.stock_code,
        episode.summary.trade_date,
        fills[0].filled_at if fills else None,
        date_range,
        fills,
    )


def trade_fill_selection(
    fill: TradeFill,
    *,
    visible_fills: tuple[TradeFill, ...],
    active_episode: TradeEpisode | None,
) -> TradeHistorySelection:
    fills = (
        tuple(sorted(active_episode.fills, key=lambda value: value.filled_at))
        if active_episode is not None and fill in active_episode.fills
        else tuple(sorted(
            (
                value for value in visible_fills
                if value.effective_scope == fill.effective_scope
                and value.stock_code == fill.stock_code
                and value.filled_at.date() == fill.filled_at.date()
            ),
            key=lambda value: value.filled_at,
        ))
    )
    date_range = (
        min(value.filled_at.date() for value in fills),
        max(value.filled_at.date() for value in fills),
    ) if fills else (fill.filled_at.date(), fill.filled_at.date())
    return TradeHistorySelection(
        fill.stock_code,
        fill.filled_at.date(),
        fill.filled_at,
        date_range,
        fills,
    )


class TradeHistoryQueryService:
    def __init__(self, repository: TradeHistoryQueryRepository) -> None:
        self._repository = repository

    def load(
        self, start: datetime, end: datetime, account_scope: AccountScope | None = None,
    ) -> TradeHistoryQueryResult:
        """과거 진입은 원가 연결에만 쓰고 선택 기간과 겹치는 회차만 반환한다."""
        if account_scope is None:
            fills = self._repository.load_fills_with_entry_context(start, end)
            costs = self._repository.load_trade_costs(start - timedelta(days=365), end)
        else:
            fills = self._repository.load_fills_with_entry_context(
                start, end, account_scope=account_scope,
            )
            costs = self._repository.load_trade_costs(
                start - timedelta(days=365), end, account_scope=account_scope,
            )
        grouped = group_trade_episodes(fills, self._repository.load_group_overrides())
        episodes = tuple(
            episode for episode in grouped
            if any(start <= fill.filled_at < end for fill in episode.fills)
        )
        return TradeHistoryQueryResult(fills, costs, episodes)

    def load_reconciled(
        self, start: datetime, end: datetime, account_scope: AccountScope,
    ) -> TradeHistoryReconciliationResult:
        """계좌 체결 요약과 중앙 상세 체결을 더하지 않고 한 벌로 읽는다.

        화면의 기존 체결·수동 묶음·복기 ID는 건드리지 않는다. 이 결과는 후속
        FeedbackEvidence가 체결 품질을 검사한 뒤 손익을 계산하기 위한 읽기 경계다.
        """
        summary_fills = self._repository.load_fills_with_entry_context(
            start, end, account_scope=account_scope,
        )
        context_start = start - timedelta(days=365)
        detail_rows = self._repository.load_execution_fill_projections(
            account_scope, context_start, end,
        )
        costs = self._repository.load_trade_costs(
            context_start, end, account_scope=account_scope,
        )
        return TradeHistoryReconciliationResult(
            reconcile_trade_fills(summary_fills, detail_rows, account_scope),
            costs,
        )

    def filter(
        self,
        episodes: tuple[TradeEpisode, ...],
        *,
        query: str = "",
        result_filter: str = "전체 손익",
        review_filter: str = "전체 복기",
    ) -> tuple[TradeEpisode, ...]:
        normalized_query = query.strip().lower()
        values: list[TradeEpisode] = []
        reviews: dict[str, TradeReview] = {}
        if review_filter != "전체 복기":
            batch_loader = getattr(self._repository, "load_reviews", None)
            if callable(batch_loader):
                reviews = batch_loader(tuple(episode.group_id for episode in episodes))
        for episode in episodes:
            summary = episode.summary
            if (
                normalized_query
                and normalized_query not in summary.stock_name.lower()
                and normalized_query not in summary.stock_code.lower()
            ):
                continue
            if result_filter == "수익" and summary.realized_profit <= 0:
                continue
            if result_filter == "손실" and summary.realized_profit >= 0:
                continue
            if result_filter == "보합" and summary.realized_profit != 0:
                continue
            if review_filter != "전체 복기":
                review = reviews.get(episode.group_id)
                if review is None:
                    review = self._repository.load_review(episode.group_id)
                if review.status != review_filter:
                    continue
            values.append(episode)
        return tuple(values)


def history_backfill_cutoff(now: datetime) -> date:
    """20시 전에는 오늘을 제외하고, 이후에는 오늘까지 보완 대상으로 삼는다."""
    return now.date() if now.hour < 20 else now.date() + timedelta(days=1)


def select_history_backfill_tasks(
    candidates: tuple[tuple[str, date], ...],
    states: Mapping[tuple[str, date], str],
    *,
    failed_only: bool = False,
) -> tuple[tuple[str, date], ...]:
    tasks: list[tuple[str, date]] = []
    for candidate in candidates:
        state = states.get(candidate, "미조회")
        if state == "확정":
            continue
        if failed_only and state != "실패":
            continue
        tasks.append(candidate)
    return tuple(tasks)


def summarize_episode_bar_state(states: tuple[str, ...]) -> str:
    if states and all(state == "확정" for state in states):
        return "확정"
    failed = sum(1 for state in states if state == "실패")
    confirmed = sum(1 for state in states if state == "확정")
    if failed:
        return f"실패 {failed}건"
    if confirmed:
        return f"일부 {confirmed}/{len(states)}"
    if any(state == "일부" for state in states):
        return "일부"
    return "미조회"


def incomplete_trade_day_tasks(
    stock_code: str,
    fills: tuple[TradeFill, ...],
    bars_by_day: Mapping[date, tuple[tuple[object, ...], ...]],
) -> tuple[tuple[str, date], ...]:
    """저장 분봉이 체결 시각 범위를 완전히 덮지 못한 거래일만 반환한다."""
    tasks: list[tuple[str, date]] = []
    for day in sorted({fill.filled_at.date() for fill in fills}):
        day_fills = tuple(fill for fill in fills if fill.filled_at.date() == day)
        rows = bars_by_day.get(day, ())
        if not rows:
            tasks.append((stock_code, day))
            continue
        first_bar = datetime.fromisoformat(str(rows[0][0]))
        last_bar = datetime.fromisoformat(str(rows[-1][0])) + timedelta(minutes=1)
        if first_bar > min(fill.filled_at for fill in day_fills) or last_bar <= max(fill.filled_at for fill in day_fills):
            tasks.append((stock_code, day))
    return tuple(tasks)


def backfill_affects_history_selection(
    code: str,
    day: date,
    *,
    selected_code: str,
    selected_range: tuple[date, date] | None,
) -> bool:
    return (
        code == selected_code
        and selected_range is not None
        and selected_range[0] <= day <= selected_range[1]
    )


def reconcile_trade_fills(
    summary_fills: tuple[TradeFill, ...],
    detail_rows: tuple[Mapping[str, object], ...],
    account_scope: AccountScope,
) -> TradeFillReconciliation:
    """두 출처를 주문 단위로 대조하고 선택 체결을 정확히 한 벌만 반환한다."""
    if any(fill.effective_scope != account_scope for fill in summary_fills):
        raise ValueError("summary fill crossed the reconciliation account scope")

    summaries: dict[tuple[object, ...], list[TradeFill]] = {}
    for fill in summary_fills:
        key = _summary_reconciliation_key(fill)
        summaries.setdefault(key, []).append(fill)

    details: dict[tuple[object, ...], list[ExecutionFillEvidence]] = {}
    identities: dict[tuple[object, ...], ExecutionFillEvidence] = {}
    conflicting_keys: set[tuple[object, ...]] = set()
    for row in detail_rows:
        evidence = _execution_fill_evidence(row, account_scope)
        fill = evidence.fill
        identity = (
            fill.effective_scope,
            fill.filled_at.date(),
            fill.order_no,
            evidence.broker_execution_id,
        )
        existing = identities.get(identity)
        if existing is not None:
            if _evidence_content(existing) != _evidence_content(evidence):
                conflicting_keys.add(_detail_reconciliation_key(existing.fill))
                conflicting_keys.add(_detail_reconciliation_key(fill))
            continue
        identities[identity] = evidence
        details.setdefault(_detail_reconciliation_key(fill), []).append(evidence)

    groups: list[FillReconciliationGroup] = []
    all_keys = sorted(
        set(summaries) | set(details),
        key=lambda value: (value[1], str(value[3]), str(value[4]), str(value[2])),
    )
    for key in all_keys:
        summary_group = tuple(sorted(
            summaries.get(key, ()), key=lambda value: value.filled_at,
        ))
        detail_group = tuple(sorted(
            details.get(key, ()),
            key=lambda value: (value.fill.filled_at, value.broker_execution_id),
        ))
        summary_quantity, summary_amount = _fill_totals(summary_group)
        detailed_quantity, detailed_amount = _fill_totals(tuple(
            evidence.fill for evidence in detail_group
        ))
        status, reason = _reconciliation_status(
            summary_group,
            detail_group,
            summary_quantity,
            detailed_quantity,
            summary_amount,
            detailed_amount,
            key in conflicting_keys,
        )
        selected = (
            tuple(evidence.fill for evidence in detail_group)
            if detail_group else summary_group
        )
        groups.append(FillReconciliationGroup(
            account_scope=key[0],
            trading_date=key[1],
            broker_order_id=str(key[2]).split("\x1f", 1)[0],
            stock_code=str(key[3]),
            side=str(key[4]),
            status=status,
            summary_fills=summary_group,
            detailed_fills=detail_group,
            selected_fills=selected,
            summary_quantity=summary_quantity,
            detailed_quantity=detailed_quantity,
            summary_amount=summary_amount,
            detailed_amount=detailed_amount,
            reason=reason,
        ))
    selected_fills = tuple(sorted(
        (fill for group in groups for fill in group.selected_fills),
        key=lambda value: value.filled_at,
        reverse=True,
    ))
    return TradeFillReconciliation(selected_fills, tuple(groups))


def _summary_reconciliation_key(fill: TradeFill) -> tuple[object, ...]:
    order_id = fill.order_no.strip()
    if not order_id:
        # 주문번호가 없으면 상세 원장에 임의 연결하지 않는다.
        order_id = "\x1f" + fill.filled_at.isoformat(timespec="seconds")
    return (
        fill.effective_scope,
        fill.filled_at.date(),
        order_id,
        fill.stock_code,
        fill.side,
    )


def _detail_reconciliation_key(fill: TradeFill) -> tuple[object, ...]:
    return (
        fill.effective_scope,
        fill.filled_at.date(),
        fill.order_no,
        fill.stock_code,
        fill.side,
    )


def _execution_fill_evidence(
    row: Mapping[str, object], account_scope: AccountScope,
) -> ExecutionFillEvidence:
    required = (
        "source_event_id", "broker_execution_id", "broker_order_id", "stock_code",
        "side", "occurred_at", "quantity", "price",
    )
    if any(not str(row.get(name) or "").strip() for name in required):
        raise ValueError("execution fill evidence is incomplete")
    origin = AccountScope(
        str(row.get("origin_broker") or account_scope.broker),
        AccountEnvironment(str(row.get("origin_environment") or account_scope.environment.value)),
        str(row.get("origin_account_ref") or account_scope.account_ref),
    )
    canonical_ref = str(row.get("canonical_account_ref") or account_scope.account_ref)
    canonical = None if canonical_ref == origin.account_ref else AccountScope(
        origin.broker, origin.environment, canonical_ref,
    )
    effective = canonical or origin
    if effective != account_scope:
        raise ValueError("execution fill crossed the reconciliation account scope")
    side = {"BUY": "매수", "SELL": "매도"}.get(str(row["side"]).upper())
    if side is None:
        raise ValueError("execution fill side is invalid")
    occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
    if occurred_at.tzinfo is None:
        raise ValueError("execution fill timestamp must be timezone-aware")
    local_time = occurred_at.astimezone(_KST).replace(tzinfo=None)
    quantity = int(row["quantity"])
    price = int(row["price"])
    if quantity <= 0 or price <= 0:
        raise ValueError("execution fill quantity or price is invalid")
    fill = TradeFill(
        order_no=str(row["broker_order_id"]),
        stock_code=str(row["stock_code"]),
        stock_name=str(row.get("stock_name") or row["stock_code"]),
        side=side,
        filled_at=local_time,
        quantity=quantity,
        price=price,
        market=str(row.get("venue") or ""),
        origin_scope=origin,
        canonical_scope=canonical,
    )
    return ExecutionFillEvidence(
        source_event_id=str(row["source_event_id"]),
        broker_execution_id=str(row["broker_execution_id"]),
        intent_id=str(row.get("intent_id") or ""),
        run_id=str(row.get("run_id") or ""),
        decision_id=str(row.get("decision_id") or ""),
        fill=fill,
    )


def _evidence_content(evidence: ExecutionFillEvidence) -> tuple[object, ...]:
    fill = evidence.fill
    return (
        fill.stock_code, fill.side, fill.filled_at, fill.quantity, fill.price, fill.market,
    )


def _fill_totals(fills: tuple[TradeFill, ...]) -> tuple[int, int]:
    return (
        sum(fill.quantity for fill in fills),
        sum(fill.quantity * fill.price for fill in fills),
    )


def _reconciliation_status(
    summaries: tuple[TradeFill, ...],
    details: tuple[ExecutionFillEvidence, ...],
    summary_quantity: int,
    detailed_quantity: int,
    summary_amount: int,
    detailed_amount: int,
    identity_conflict: bool,
) -> tuple[str, str]:
    if identity_conflict:
        return "CONFLICT", "같은 broker 체결번호의 내용이 서로 다릅니다."
    if not summaries:
        return "DETAIL_ONLY", "중앙 원장의 상세 체결만 확인됐습니다."
    if not details:
        return "SUMMARY_ONLY", "kt00007 요약만 있어 상세 체결을 확인할 수 없습니다."
    if summary_quantity != detailed_quantity:
        return "PARTIAL", "두 출처의 체결수량이 달라 상세 체결만 사용하고 손익은 확정하지 않습니다."
    if summary_amount != detailed_amount:
        return "CONFLICT", "두 출처의 체결금액이 달라 상세 체결만 사용하고 손익은 확정하지 않습니다."
    return "EXACT", "체결수량과 체결금액이 일치합니다."

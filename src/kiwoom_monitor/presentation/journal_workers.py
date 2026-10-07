"""매매일지의 키움 조회·보완 백그라운드 worker."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QThread, Signal

from kiwoom_monitor.application.market_index_chart_service import MarketIndexChartService
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.trade_chart import DailyTradeChartService
from kiwoom_monitor.application.trade_cost_service import TradeCostService
from kiwoom_monitor.application.trade_history_service import TradeFill, TradeHistoryService
from kiwoom_monitor.application.trade_analysis_preparation_service import TradeAnalysisPreparationService
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountScopeMismatchError
from kiwoom_monitor.domain.order_contract import AccountEnvironment, LEGACY_ACCOUNT_SCOPE


class ConfirmWorker(QThread):
    completed = Signal(str, object, object, str)
    failed = Signal(str)

    def __init__(self, service: MinuteChartService, code: str, target_day: date | None = None, include_previous: bool = False) -> None:
        super().__init__()
        self._service, self._code, self._target_day, self._include_previous = service, code, target_day, include_previous

    def run(self) -> None:
        try:
            now = datetime.now()
            target = datetime.combine(self._target_day, time()) if self._target_day else now
            after_close = target.date() < now.date() or now.hour >= 20
            bars = (
                self._service.load_two_trading_days(self._code, target)
                if self._include_previous else
                (self._service.load_today(self._code, target) if after_close else self._service.load_recent(self._code, target))
            )
            current = now.replace(second=0, microsecond=0)
            self.completed.emit(
                self._code, tuple(bar for bar in bars if bar.minute < current), now,
                "after_close_confirmed" if after_close else "api_confirmed",
            )
        except Exception as error:
            self.failed.emit(str(error))


class MarketIndexBackfillWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: MarketIndexChartService, day: date) -> None:
        super().__init__()
        self._service, self._day = service, day

    def run(self) -> None:
        try:
            combined = {}
            daily = {}
            for market in ("kospi", "kosdaq"):
                combined.update(self._service.load(market, datetime.combine(self._day, time())))
                daily[market] = self._service.load_daily(market, datetime.combine(self._day, time()))
            self.completed.emit((combined, daily))
        except Exception as error:
            self.failed.emit(str(error))


class HistoryWorker(QThread):
    completed = Signal(object, object, object)
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, service: TradeHistoryService, cost_service: TradeCostService, start: date, end: date,
                 *, account_client=None, account_scope=None) -> None:
        super().__init__()
        self._service, self._cost_service, self._start, self._end = service, cost_service, start, end
        self._account_client, self._expected_scope = account_client, account_scope

    def run(self) -> None:
        try:
            fills: list[TradeFill] = []
            fills_context = None
            service, cost_service = self._service, self._cost_service
            if self._account_client is not None:
                selector = getattr(self._account_client, "for_account_scope", None)
                if callable(selector):
                    client = selector(self._expected_scope)
                    service, cost_service = TradeHistoryService(client), TradeCostService(client)
                elif self._expected_scope != LEGACY_ACCOUNT_SCOPE:
                    raise AccountScopeMismatchError("검증 계좌 조회 연결을 먼저 선택하세요.")
            day = self._end
            while day >= self._start:
                if self.isInterruptionRequested():
                    return
                if day.weekday() < 5:
                    self.progress.emit(f"과거 체결 조회 중 · {day:%Y-%m-%d}")
                    history_batch = service.load_day_batch(day)
                    if self._expected_scope is not None and history_batch.context.scope != self._expected_scope:
                        raise AccountScopeMismatchError("선택 계좌와 체결 조회 계좌가 다릅니다.")
                    if fills_context is None:
                        fills_context = history_batch.context
                    elif history_batch.context != fills_context:
                        raise AccountScopeMismatchError("체결 조회 도중 계좌 context가 변경되었습니다.")
                    fills.extend(history_batch.fills)
                day -= timedelta(days=1)
            cost_error = ""
            if (
                self._expected_scope is not None
                and self._expected_scope.environment is AccountEnvironment.MOCK
            ):
                # kt00015는 실계좌의 일자별 실제 정산 자료다. 모의계좌에서는
                # 현재 중앙 조회가 실패하므로, 체결만 저장하고 매매일지의
                # 계좌별 예상 비용률을 사용한다.
                costs = ()
                self.progress.emit("모의투자 체결 확인 완료 · 비용은 예상값 사용")
            else:
                self.progress.emit("실제 수수료·세금 확인 중…")
                try:
                    cost_batch = cost_service.load_period_batch(self._start, self._end)
                    if self._expected_scope is not None and cost_batch.context.scope != self._expected_scope:
                        raise AccountScopeMismatchError("선택 계좌와 비용 조회 계좌가 다릅니다.")
                    if fills_context is not None and cost_batch.context != fills_context:
                        raise AccountScopeMismatchError("체결과 비용 조회의 계좌 context가 다릅니다.")
                    costs = cost_batch.costs
                    if fills_context is None: fills_context = cost_batch.context
                except AccountScopeMismatchError:
                    raise
                except Exception as error:
                    costs = ()
                    cost_error = str(error)
            if self.isInterruptionRequested(): return
            result = (tuple(fills), costs, cost_error)
            if self._account_client is not None: result += (fills_context,)
            self.completed.emit(result, self._start, self._end)
        except Exception as error:
            self.failed.emit(str(error))


class BackfillWorker(QThread):
    progress = Signal(str)
    item_started = Signal(str, object)
    item_completed = Signal(str, object, object, bool)
    item_failed = Signal(str, object, str)
    completed = Signal(int, int)

    def __init__(self, service: MinuteChartService, tasks: tuple[tuple[str, date], ...]) -> None:
        super().__init__()
        self._service, self._tasks = service, tasks

    def run(self) -> None:
        success = failed = 0
        total = len(self._tasks)
        for index, (code, day) in enumerate(self._tasks, 1):
            if self.isInterruptionRequested():
                break
            self.progress.emit(f"분봉 보완 {index}/{total} · {day:%Y-%m-%d} · {code}")
            self.item_started.emit(code, day)
            try:
                target = datetime.combine(day, time())
                loader = getattr(self._service, "load_today_with_completion", None)
                loaded, coverage_complete = (
                    loader(code, target) if callable(loader)
                    else (self._service.load_today(code, target), True)
                )
                bars = tuple(
                    bar for bar in loaded
                    if bar.minute.date() == day
                )
                if not bars:
                    raise ValueError("해당 거래일의 분봉이 반환되지 않았습니다.")
                self.item_completed.emit(code, day, bars, bool(coverage_complete))
                if coverage_complete:
                    success += 1
                else:
                    failed += 1
            except Exception as error:
                self.item_failed.emit(code, day, str(error))
                failed += 1
        self.completed.emit(success, failed)


class DailyChartWorker(QThread):
    completed = Signal(str, object, str)
    failed = Signal(str)

    def __init__(self, service: DailyTradeChartService, code: str, day: date, target: str) -> None:
        super().__init__()
        self._service, self._code, self._day, self._target = service, code, day, target

    def run(self) -> None:
        try:
            rows = self._service.load(self._code, datetime.combine(self._day, time()))
            self.completed.emit(self._code, rows, self._target)
        except Exception as error:
            self.failed.emit(str(error))


class AnalysisEnrichmentWorker(QThread):
    """화면에서 선택하지 않은 매매 회차도 저장 자료만으로 분석 보완한다."""

    item_started = Signal(str)
    item_completed = Signal(str, object)
    item_failed = Signal(str, str)
    completed = Signal(int, int)

    def __init__(
        self,
        service: TradeAnalysisPreparationService,
        tasks: tuple[tuple[object, tuple[tuple[object, ...], ...]], ...],
        *,
        active_pack: object,
        strategy_packs: tuple[object, ...],
        result_mode: str,
    ) -> None:
        super().__init__()
        self._service = service
        self._tasks = tasks
        self._active_pack = active_pack
        self._strategy_packs = strategy_packs
        self._result_mode = result_mode

    def run(self) -> None:
        success = failed = 0
        for episode, minute_rows in self._tasks:
            if self.isInterruptionRequested():
                break
            group_id = str(getattr(episode, "group_id", ""))
            self.item_started.emit(group_id)
            try:
                prepared = self._service.prepare(
                    episode,
                    minute_rows,
                    active_pack=self._active_pack,
                    strategy_packs=self._strategy_packs,
                    result_mode=self._result_mode,
                )
                self.item_completed.emit(group_id, prepared)
                success += 1
            except Exception as error:
                self.item_failed.emit(group_id, str(error))
                failed += 1
        self.completed.emit(success, failed)

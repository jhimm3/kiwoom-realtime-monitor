from __future__ import annotations

from typing import Protocol

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings


class FinishedWorker(Protocol):
    def wait(self) -> bool: ...
    def deleteLater(self) -> None: ...


def dispose_finished_worker(worker: FinishedWorker | None) -> None:
    """finished 신호를 받은 Qt worker의 기존 정리 순서를 한곳에 유지한다."""
    if worker is None:
        return
    worker.wait()
    worker.deleteLater()


def ai_start_block_reason(settings: NewsAISettings, used_requests: int, group_count: int) -> str:
    if group_count <= 0:
        return "empty"
    if settings.daily_limit > 0 and used_requests >= settings.daily_limit:
        return "daily_limit"
    return ""


def automatic_ai_run_allowed(
    settings: NewsAISettings,
    *,
    manual_queue: bool,
    central_client_available: bool,
    used_requests: int,
) -> bool:
    if not settings.auto_analyze and not manual_queue:
        return False
    if settings.provider == "none":
        return False
    if not settings.api_key and not central_client_available:
        return False
    if settings.daily_limit > 0 and used_requests >= settings.daily_limit:
        return False
    return True


def ai_progress_text(settings: NewsAISettings, used_requests: int) -> str:
    if settings.daily_limit > 0:
        return f"{used_requests + 1}/{settings.daily_limit}"
    return f"{used_requests + 1}/무제한"

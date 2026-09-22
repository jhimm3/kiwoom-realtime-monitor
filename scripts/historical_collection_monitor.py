from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / "data" / "historical_collection"
DATABASE = PROJECT_ROOT / "data" / "historical_intelligence.sqlite3"
NEWS_STATE = STATE_ROOT / "news-collector-state.json"
DAISHIN_STATE = STATE_ROOT / "daishin-collector-state.json"
NEWS_HEARTBEAT = STATE_ROOT / "news-job-heartbeat.json"
DAISHIN_HEARTBEAT = STATE_ROOT / "daishin-job-heartbeat.json"
NEWS_STOP = STATE_ROOT / "STOP_NEWS"
DAISHIN_STOP = STATE_ROOT / "STOP_DAISHIN"
LOG_ROOT = STATE_ROOT / "logs"
NAS_STATUS_PATHS = (
    Path(r"X:\kiwoom-monitor\deploy\synology\server-data\historical-intelligence\v1\STATUS.md"),
    Path(r"\\192.168.0.5\docker\kiwoom-monitor\deploy\synology\server-data\historical-intelligence\v1\STATUS.md"),
)
CREATE_NO_WINDOW = 0x08000000


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _age_seconds(value: object) -> int | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0, round((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()))


def _local_time(value: object) -> str:
    parsed = _parse_time(value)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S") if parsed else "-"


def _process_alive(pid: object) -> bool:
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _database_snapshot() -> dict[str, object]:
    result: dict[str, object] = {
        "news_counts": {}, "market_counts": {}, "news_current": {}, "market_current": {},
    }
    if not DATABASE.is_file():
        return result
    uri = f"file:{DATABASE.resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            connection.row_factory = sqlite3.Row
            result["news_counts"] = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state,COUNT(*) AS count FROM news_backfill_jobs GROUP BY state"
                )
            }
            result["market_counts"] = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state,COUNT(*) AS count FROM market_backfill_jobs GROUP BY state"
                )
            }
            news = connection.execute(
                "SELECT code,target_date,query_text,attempts,pages_observed,items_observed,updated_at "
                "FROM news_backfill_jobs WHERE state='running' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if news is None and connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_range_jobs'"
            ).fetchone():
                news = connection.execute(
                    "SELECT code,start_date AS target_date,query_text,attempts,"
                    "pages_observed,items_observed,updated_at "
                    "FROM news_range_jobs WHERE state='running' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            market = connection.execute(
                "SELECT code,attempts,one_minute_bars,five_minute_bars,updated_at "
                "FROM market_backfill_jobs WHERE state='running' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            result["news_current"] = dict(news) if news else {}
            result["market_current"] = dict(market) if market else {}
    except sqlite3.Error as error:
        result["database_error"] = str(error)
    return result


def _collector_health(
    state: dict[str, object], heartbeat: dict[str, object], current: dict[str, object],
) -> tuple[str, str]:
    state_name = str(state.get("status") or "missing")
    if state_name == "failed":
        return "오류로 정지", "#b42318"
    if state_name in {"complete", "stopped"}:
        return "정지", "#475467"
    heartbeat_age = _age_seconds(heartbeat.get("updated_at"))
    current_age = _age_seconds(current.get("updated_at"))
    heartbeat_alive = (
        heartbeat_age is not None and heartbeat_age < 180
        and _process_alive(heartbeat.get("pid"))
    )
    current_fresh = current_age is not None and current_age < 600
    state_alive = _process_alive(state.get("pid"))
    if state_name == "running" and (heartbeat_alive or current_fresh):
        return "정상 실행 중", "#067647"
    state_age = _age_seconds(state.get("updated_at"))
    if state_name == "running" and state_alive and state_age is not None and state_age < 180:
        return "시작 중", "#b54708"
    if state_name == "running":
        return "응답 없음", "#b42318"
    return "정지", "#475467"


def _counts_text(counts: dict[str, int]) -> tuple[str, float]:
    total = sum(counts.values())
    complete = counts.get("complete", 0) + counts.get("truncated", 0)
    percent = 100 * complete / total if total else 0.0
    detail = " · ".join(
        f"{name} {counts.get(name, 0):,}"
        for name in ("complete", "truncated", "running", "pending", "grouped", "failed")
        if counts.get(name, 0)
    )
    return f"완료 {complete:,} / {total:,} ({percent:.2f}%)   {detail}", percent


class CollectorPanel(ttk.LabelFrame):
    def __init__(self, master: tk.Misc, title: str, start_command, stop_command) -> None:
        super().__init__(master, text=title, padding=12)
        self.columnconfigure(0, weight=1)
        self.status = tk.Label(self, text="확인 중", anchor="w", font=("맑은 고딕", 14, "bold"))
        self.status.grid(row=0, column=0, sticky="ew")
        self.progress_text = ttk.Label(self, text="-")
        self.progress_text.grid(row=1, column=0, sticky="ew", pady=(8, 3))
        self.progress = ttk.Progressbar(self, maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew")
        self.current = ttk.Label(self, text="현재 작업: -")
        self.current.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.heartbeat = ttk.Label(self, text="마지막 응답: -")
        self.heartbeat.grid(row=4, column=0, sticky="ew")
        self.error = ttk.Label(self, text="", foreground="#b42318", wraplength=760)
        self.error.grid(row=5, column=0, sticky="ew", pady=(3, 0))
        buttons = ttk.Frame(self)
        buttons.grid(row=6, column=0, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="시작 / 재시작", command=start_command).pack(side="left")
        ttk.Button(buttons, text="다음 작업 후 정지", command=stop_command).pack(side="left", padx=8)

    def update_view(
        self, health: tuple[str, str], counts: dict[str, int], current: str,
        heartbeat: str, error: str,
    ) -> None:
        self.status.configure(text=health[0], foreground=health[1])
        progress_text, percent = _counts_text(counts)
        self.progress_text.configure(text=progress_text)
        self.progress.configure(value=percent)
        self.current.configure(text=f"현재 작업: {current}")
        self.heartbeat.configure(text=f"마지막 응답: {heartbeat}")
        self.error.configure(text=(f"마지막 오류: {error[:500]}" if error else ""))


class HistoricalCollectionMonitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("과거자료 수집기 모니터")
        self.geometry("900x650")
        self.minsize(780, 570)
        self.option_add("*Font", ("맑은 고딕", 10))
        self.columnconfigure(0, weight=1)
        self._refresh_after: str | None = None

        heading = ttk.Frame(self, padding=(16, 14, 16, 6))
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading, text="과거자료 수집기", font=("맑은 고딕", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.admin_label = ttk.Label(
            heading, text="관리자 권한" if _is_admin() else "일반 권한",
        )
        self.admin_label.grid(row=0, column=1, sticky="e")

        self.news_panel = CollectorPanel(self, "네이버 뉴스", self.start_news, self.stop_news)
        self.news_panel.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        self.daishin_panel = CollectorPanel(
            self, "대신증권 CREON 분봉", self.start_daishin, self.stop_daishin,
        )
        self.daishin_panel.grid(row=2, column=0, sticky="ew", padx=16, pady=8)

        footer = ttk.Frame(self, padding=(16, 6, 16, 12))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.nas_status = ttk.Label(footer, text="NAS 상태: 확인 중")
        self.nas_status.grid(row=0, column=0, sticky="w")
        self.database_status = ttk.Label(footer, text="")
        self.database_status.grid(row=1, column=0, sticky="w", pady=(4, 0))
        controls = ttk.Frame(footer)
        controls.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(controls, text="지금 새로고침", command=self.refresh).pack(side="left")
        ttk.Button(controls, text="로그 폴더 열기", command=self.open_logs).pack(side="left", padx=8)
        ttk.Button(controls, text="종료", command=self.destroy).pack(side="left")
        self.after(200, self.refresh)

    def _launch(self, script_name: str, jobs: int) -> None:
        script = PROJECT_ROOT / "scripts" / script_name
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script), "-Jobs", str(jobs),
            ],
            cwd=PROJECT_ROOT,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
        self.after(1200, self.refresh)

    def start_news(self) -> None:
        state = _read_json(NEWS_STATE)
        snapshot = _database_snapshot()
        current = snapshot.get("news_current", {})
        assert isinstance(current, dict)
        if _collector_health(state, _read_json(NEWS_HEARTBEAT), current)[0] in {
            "정상 실행 중", "시작 중",
        }:
            messagebox.showinfo("뉴스 수집기", "뉴스 수집기가 이미 실행 중입니다.")
            return
        NEWS_STOP.unlink(missing_ok=True)
        self._launch("run_historical_news_collector.ps1", 100000)

    def start_daishin(self) -> None:
        if not _is_admin():
            messagebox.showerror(
                "관리자 권한 필요",
                "대신 수집기는 CREON과 같은 관리자 권한이 필요합니다.\n"
                "모니터를 닫고 수집기_모니터.cmd로 다시 실행하세요.",
            )
            return
        state = _read_json(DAISHIN_STATE)
        snapshot = _database_snapshot()
        current = snapshot.get("market_current", {})
        assert isinstance(current, dict)
        if _collector_health(state, _read_json(DAISHIN_HEARTBEAT), current)[0] in {
            "정상 실행 중", "시작 중",
        }:
            messagebox.showinfo("대신 수집기", "대신 수집기가 이미 실행 중입니다.")
            return
        DAISHIN_STOP.unlink(missing_ok=True)
        self._launch("run_daishin_collector.ps1", 10000)

    def stop_news(self) -> None:
        NEWS_STOP.parent.mkdir(parents=True, exist_ok=True)
        NEWS_STOP.touch()
        messagebox.showinfo("뉴스 수집기", "현재 작업을 마친 뒤 정지하도록 요청했습니다.")

    def stop_daishin(self) -> None:
        DAISHIN_STOP.parent.mkdir(parents=True, exist_ok=True)
        DAISHIN_STOP.touch()
        messagebox.showinfo("대신 수집기", "현재 종목을 마친 뒤 정지하도록 요청했습니다.")

    def open_logs(self) -> None:
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        os.startfile(LOG_ROOT)

    def refresh(self) -> None:
        if self._refresh_after is not None:
            try:
                self.after_cancel(self._refresh_after)
            except tk.TclError:
                pass
            self._refresh_after = None
        snapshot = _database_snapshot()
        news_state = _read_json(NEWS_STATE)
        news_heartbeat = _read_json(NEWS_HEARTBEAT)
        news_current = snapshot.get("news_current", {})
        assert isinstance(news_current, dict)
        news_counts = snapshot.get("news_counts", {})
        assert isinstance(news_counts, dict)
        news_label = "-"
        if news_current:
            news_label = (
                f"{news_current.get('code')} · {news_current.get('target_date')} · "
                f"{news_current.get('query_text')} · 시도 {news_current.get('attempts')}"
            )
        news_heartbeat_text = (
            f"{_local_time(news_heartbeat.get('updated_at'))} · "
            f"{news_heartbeat.get('phase', '-')} · 페이지 {news_heartbeat.get('page', 0)}"
        )
        self.news_panel.update_view(
            _collector_health(news_state, news_heartbeat, news_current),
            news_counts, news_label, news_heartbeat_text, str(news_state.get("error") or ""),
        )

        market_state = _read_json(DAISHIN_STATE)
        market_heartbeat = _read_json(DAISHIN_HEARTBEAT)
        market_current = snapshot.get("market_current", {})
        assert isinstance(market_current, dict)
        market_counts = snapshot.get("market_counts", {})
        assert isinstance(market_counts, dict)
        market_label = "-"
        if market_current:
            market_label = (
                f"{market_current.get('code')} · 시도 {market_current.get('attempts')} · "
                f"1분 {int(market_current.get('one_minute_bars') or 0):,} · "
                f"5분 {int(market_current.get('five_minute_bars') or 0):,}"
            )
        market_heartbeat_text = (
            f"{_local_time(market_heartbeat.get('updated_at'))} · "
            f"{market_heartbeat.get('phase', '-')} · {market_heartbeat.get('code', '')}"
        )
        self.daishin_panel.update_view(
            _collector_health(market_state, market_heartbeat, market_current),
            market_counts, market_label, market_heartbeat_text,
            str(market_state.get("error") or market_heartbeat.get("error") or ""),
        )

        visible_nas = next((path for path in NAS_STATUS_PATHS if path.is_file()), None)
        if visible_nas:
            changed = datetime.fromtimestamp(visible_nas.stat().st_mtime).astimezone()
            age = round((datetime.now().astimezone() - changed).total_seconds())
            self.nas_status.configure(
                text=f"NAS 상태: {changed:%Y-%m-%d %H:%M:%S} 갱신 · {age:,}초 전"
            )
        else:
            self.nas_status.configure(text="NAS 상태: 현재 권한에서 경로가 보이지 않습니다.")
        database_error = str(snapshot.get("database_error") or "")
        self.database_status.configure(
            text=(f"DB 읽기 오류: {database_error}" if database_error else f"DB: {DATABASE}")
        )
        self._refresh_after = self.after(3000, self.refresh)


def main() -> int:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    app = HistoricalCollectionMonitor()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

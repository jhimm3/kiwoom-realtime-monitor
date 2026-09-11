from __future__ import annotations

import json
import logging
import queue
import threading
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .remote_client import CentralServerUnavailable, RemoteKiwoomRestClient


logger = logging.getLogger(__name__)


class ParallelValidationClient:
    """중앙 응답을 반환하고 같은 조회의 로컬 결과는 백그라운드에서 비교한다."""

    def __init__(
        self, primary: RemoteKiwoomRestClient, local: Any, report_path: Path, *,
        fallback_on_unavailable: bool = False, queue_limit: int = 30,
    ) -> None:
        self._primary = primary
        self._local = local
        self._report_path = report_path
        self._fallback_on_unavailable = fallback_on_unavailable
        self._jobs: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=max(1, queue_limit))
        self._writer_lock = threading.Lock()
        threading.Thread(target=self._work, name="central-local-validation", daemon=True).start()

    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload, _, _ = self.request_with_continuation(api_id, path, body)
        return payload

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = "",
    ) -> tuple[dict[str, Any], bool, str]:
        try:
            central = self._primary.request_with_continuation(
                api_id, path, body, cont_yn=cont_yn, next_key=next_key,
            )
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return self._local.request_with_continuation(
                api_id, path, body, cont_yn=cont_yn, next_key=next_key,
            )
        try:
            self._jobs.put_nowait((api_id, path, dict(body), cont_yn, next_key, central))
        except queue.Full:
            logger.warning("중앙·로컬 비교 대기열이 가득 차 %s 비교를 건너뜁니다.", api_id)
        return central

    def _work(self) -> None:
        while True:
            api_id, path, body, cont_yn, next_key, central = self._jobs.get()
            try:
                local = self._local.request_with_continuation(
                    api_id, path, body, cont_yn=cont_yn, next_key=next_key,
                )
                self._record(api_id, path, body, central, local)
            except Exception as error:
                self._record(api_id, path, body, central, None, str(error))
            finally:
                self._jobs.task_done()

    def _record(
        self, api_id: str, path: str, body: dict[str, Any], central: object,
        local: object | None, error: str = "",
    ) -> None:
        equal = not error and _canonical(central) == _canonical(local)
        row = {
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "api_id": api_id, "path": path, "request_fields": sorted(str(key) for key in body),
            "equal": equal, "error": error,
            "different_fields": _different_top_level_fields(central, local) if not error else [],
        }
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        with self._writer_lock, self._report_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        if not equal:
            logger.warning("중앙·로컬 응답 차이: %s %s", api_id, row["different_fields"] or error)

    def server_now(self):
        return self._local.server_now()

    def get_access_token(self) -> str:
        return self._local.get_access_token()


def _canonical(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in {"return_msg"}}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _different_top_level_fields(central: object, local: object | None) -> list[str]:
    if not isinstance(central, (tuple, list)) or not isinstance(local, (tuple, list)) or not central or not local:
        return ["response"]
    left, right = central[0], local[0]
    if not isinstance(left, dict) or not isinstance(right, dict):
        return ["response"]
    ignored = {"return_msg"}
    return sorted(
        str(key) for key in set(left) | set(right)
        if str(key) not in ignored and _canonical(left.get(key)) != _canonical(right.get(key))
    )


class RealtimeValidationRecorder:
    """중앙·로컬 실시간 이벤트를 같은 종목·시각끼리 짝지어 기록한다."""

    def __init__(self, report_path: Path, *, pending_limit: int = 2_000) -> None:
        self._report_path = report_path
        self._pending_limit = max(100, pending_limit)
        self._central: OrderedDict[tuple[str, str, str, int], dict[str, object]] = OrderedDict()
        self._local: OrderedDict[tuple[str, str, str, int], dict[str, object]] = OrderedDict()
        self._counts: OrderedDict[tuple[str, str, str, str], int] = OrderedDict()
        self._lock = threading.Lock()

    def observe_central(self, event_type: str, event: object) -> None:
        self._observe("central", event_type, event)

    def observe_local(self, event_type: str, event: object) -> None:
        self._observe("local", event_type, event)

    def _observe(self, side: str, event_type: str, event: object) -> None:
        payload = _event_payload(event)
        base_key = _realtime_key(event_type, payload)
        if base_key is None:
            return
        with self._lock:
            count_key = (side, *base_key)
            sequence = self._counts.get(count_key, 0) + 1
            self._counts[count_key] = sequence
            self._counts.move_to_end(count_key)
            if len(self._counts) > self._pending_limit * 2:
                self._counts.popitem(last=False)
            key = (*base_key, sequence)
            own, other = (self._central, self._local) if side == "central" else (self._local, self._central)
            matched = other.pop(key, None)
            if matched is None:
                own[key] = payload
                own.move_to_end(key)
                while len(own) > self._pending_limit:
                    own.popitem(last=False)
                return
        central, local = (payload, matched) if side == "central" else (matched, payload)
        ignored = {"received_at", "updated_at"}
        different = sorted(
            field for field in set(central) | set(local)
            if field not in ignored and _canonical(central.get(field)) != _canonical(local.get(field))
        )
        row = {
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "event_type": event_type, "identity": list(key[1:]),
            "equal": not different, "different_fields": different,
        }
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        with self._report_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _event_payload(event: object) -> dict[str, object]:
    if is_dataclass(event) and not isinstance(event, type):
        return {str(key): value for key, value in asdict(event).items()}
    if isinstance(event, dict):
        return {str(key): value for key, value in event.items()}
    return dict(vars(event)) if hasattr(event, "__dict__") else {}


def _realtime_key(event_type: str, payload: dict[str, object]) -> tuple[str, str, str] | None:
    code = str(payload.get("code") or payload.get("index_code") or payload.get("market") or "")
    event_time = str(payload.get("trade_time") or payload.get("execution_time") or payload.get("time") or "")
    if not code or not event_time:
        return None
    return event_type, code, event_time

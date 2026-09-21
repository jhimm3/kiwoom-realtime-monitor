from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .remote_client import CentralServerUnavailable, RemoteKiwoomRestClient
from .mock_execution import ORDER_API_IDS


logger = logging.getLogger(__name__)


VALIDATION_STATUSES = frozenset({
    "MATCH", "VALUE_MISMATCH", "LATE", "MISSING", "DUPLICATE",
    "OUT_OF_ORDER", "NOT_COMPARABLE",
})
_COMPARABLE_STATUSES = frozenset({"MATCH", "VALUE_MISMATCH", "LATE"})


class _ValidationMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status_counts = {status: 0 for status in sorted(VALIDATION_STATUSES)}
        self._arrival_gaps_ms: deque[float] = deque(maxlen=10_000)
        self._queue_skips = 0
        self._evictions = 0

    def record(
        self, status: str, *, arrival_gap_ms: float | None = None,
        queue_skip: bool = False, eviction: bool = False,
    ) -> None:
        if status not in VALIDATION_STATUSES:
            raise ValueError(f"unsupported validation status: {status}")
        with self._lock:
            self._status_counts[status] += 1
            if arrival_gap_ms is not None:
                self._arrival_gaps_ms.append(max(0.0, float(arrival_gap_ms)))
            if queue_skip:
                self._queue_skips += 1
            if eviction:
                self._evictions += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counts = dict(self._status_counts)
            gaps = sorted(self._arrival_gaps_ms)
            total = sum(counts.values())
            comparable = sum(counts[status] for status in _COMPARABLE_STATUSES)
            return {
                "status_counts": counts,
                "total_observations": total,
                "comparable_count": comparable,
                "matched_count": counts["MATCH"],
                "unmatched_count": counts["MISSING"],
                "queue_skip_count": self._queue_skips,
                "eviction_count": self._evictions,
                "arrival_gap_ms": {
                    "p50": _percentile(gaps, 50),
                    "p95": _percentile(gaps, 95),
                    "p99": _percentile(gaps, 99),
                    "max": round(gaps[-1], 3) if gaps else None,
                    "sample_count": len(gaps),
                },
            }


class ParallelValidationClient:
    """중앙 응답을 반환하고 같은 조회의 로컬 결과는 백그라운드에서 비교한다."""

    def __init__(
        self, primary: RemoteKiwoomRestClient, local: Any, report_path: Path, *,
        fallback_on_unavailable: bool = False, queue_limit: int = 30,
        late_threshold_ms: float = 2_000.0,
        monotonic_provider: Callable[[], float] = time.monotonic,
        now_provider: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._primary = primary
        self._local = local
        self._report_path = report_path
        self._fallback_on_unavailable = fallback_on_unavailable
        self._jobs: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=max(1, queue_limit))
        self._late_threshold_ms = max(0.0, float(late_threshold_ms))
        self._monotonic = monotonic_provider
        self._now = now_provider
        self._metrics = _ValidationMetrics()
        self._writer_lock = threading.Lock()
        threading.Thread(target=self._work, name="central-local-validation", daemon=True).start()

    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload, _, _ = self.request_with_continuation(api_id, path, body)
        return payload

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = "",
    ) -> tuple[dict[str, Any], bool, str]:
        if api_id in ORDER_API_IDS:
            raise ValueError("order APIs cannot use the parallel validation client")
        primary_started = self._monotonic()
        requested_at = self._now().isoformat()
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
        primary_finished = self._monotonic()
        try:
            self._jobs.put_nowait((
                api_id, path, dict(body), cont_yn, next_key, central,
                requested_at, primary_started, primary_finished,
            ))
        except queue.Full:
            self._metrics.record("MISSING", queue_skip=True)
            logger.warning("중앙·로컬 비교 대기열이 가득 차 %s 비교를 건너뜁니다.", api_id)
        return central

    def load_stored_minute_bars(
        self, code: str, trading_date: str, market: str = "",
    ) -> tuple[dict[str, Any], ...] | None:
        """저장 자료 조회는 Kiwoom TR 병행 비교 대상이 아니므로 중앙값만 사용한다."""
        try:
            return self._primary.load_stored_minute_bars(code, trading_date, market)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_minute_bars_with_coverage(
        self, code: str, trading_date: str, market: str = "",
    ):
        try:
            return self._primary.load_stored_minute_bars_with_coverage(code, trading_date, market)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_recent_minute_bars(
        self, code: str, end_date: str, market: str = "", trading_days: int = 2,
    ):
        try:
            return self._primary.load_stored_recent_minute_bars(
                code, end_date, market, trading_days,
            )
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_ranking(self, query_type: str = "5") -> dict[str, Any] | None:
        try:
            return self._primary.load_stored_ranking(query_type)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_daily_bars(
        self, code: str, market: str = "", limit: int = 250,
    ) -> tuple[dict[str, Any], ...] | None:
        try:
            return self._primary.load_stored_daily_bars(code, market, limit)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_investor_flow(self, code: str, trading_date: str):
        try:
            return self._primary.load_stored_investor_flow(code, trading_date)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_program_flow(self, code: str, trading_date: str):
        try:
            return self._primary.load_stored_program_flow(code, trading_date)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_new_highs(self, periods: tuple[int, ...]):
        try:
            return self._primary.load_stored_new_highs(periods)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_historical_high(self, code: str):
        try:
            return self._primary.load_stored_historical_high(code)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_stored_fundamentals(self, code: str):
        return self._stored_primary("load_stored_fundamentals", code)

    def load_stored_nxt_eligibility(self, code: str):
        return self._stored_primary("load_stored_nxt_eligibility", code)

    def load_stored_market_index(self, market: str, trading_date: str):
        return self._stored_primary("load_stored_market_index", market, trading_date)

    def load_stored_top20_index(self, trading_date: str):
        return self._stored_primary("load_stored_top20_index", trading_date)

    def load_stored_top20_statistics(self, start_date: str, end_date: str):
        return self._stored_primary("load_stored_top20_statistics", start_date, end_date)

    def load_stored_market_caps(self, codes: tuple[str, ...]):
        return self._stored_primary("load_stored_market_caps", codes)

    def _stored_primary(self, method: str, *args):
        try:
            return getattr(self._primary, method)(*args)
        except CentralServerUnavailable:
            if not self._fallback_on_unavailable:
                raise
            return None

    def load_account_contexts(self):
        return self._primary.load_account_contexts()

    def for_account_scope(self, scope):
        return self._primary.for_account_scope(scope)

    def query_account_pages(self, api_id: str, path: str, body: dict[str, Any], *, max_pages: int = 20):
        """계좌 자료는 계좌 context가 없는 병행 비교 경로로 보내지 않는다."""
        return self._primary.query_account_pages(api_id, path, body, max_pages=max_pages)

    def _work(self) -> None:
        while True:
            (
                api_id, path, body, cont_yn, next_key, central,
                requested_at, primary_started, primary_finished,
            ) = self._jobs.get()
            local_started = self._monotonic()
            try:
                local = self._local.request_with_continuation(
                    api_id, path, body, cont_yn=cont_yn, next_key=next_key,
                )
                self._record(
                    api_id, path, body, central, local,
                    requested_at=requested_at,
                    primary_latency_ms=(primary_finished - primary_started) * 1_000,
                    queue_wait_ms=(local_started - primary_finished) * 1_000,
                    local_latency_ms=(self._monotonic() - local_started) * 1_000,
                )
            except Exception as error:
                self._record(
                    api_id, path, body, central, None, str(error),
                    requested_at=requested_at,
                    primary_latency_ms=(primary_finished - primary_started) * 1_000,
                    queue_wait_ms=(local_started - primary_finished) * 1_000,
                    local_latency_ms=(self._monotonic() - local_started) * 1_000,
                )
            finally:
                self._jobs.task_done()

    def _record(
        self, api_id: str, path: str, body: dict[str, Any], central: object,
        local: object | None, error: str = "",
        *, requested_at: str = "", primary_latency_ms: float = 0.0,
        queue_wait_ms: float = 0.0, local_latency_ms: float = 0.0,
    ) -> None:
        equal = not error and _canonical(central) == _canonical(local)
        late = max(primary_latency_ms, queue_wait_ms + local_latency_ms) > self._late_threshold_ms
        nas_refs = _extract_source_refs(central)
        direct_refs = _extract_source_refs(local)
        shared_refs = sorted(set(nas_refs) & set(direct_refs))
        status = (
            "MISSING" if error else
            "VALUE_MISMATCH" if not equal and shared_refs else
            "NOT_COMPARABLE" if not equal else
            "LATE" if late else
            "MATCH"
        )
        arrival_gap_ms = abs(primary_latency_ms - local_latency_ms) if not error else None
        self._metrics.record(status, arrival_gap_ms=arrival_gap_ms)
        row = {
            "checked_at": self._now().isoformat(timespec="seconds"),
            "requested_at": requested_at,
            "api_id": api_id, "path": path, "request_fields": sorted(str(key) for key in body),
            "status": status, "equal": equal, "error": error,
            "different_fields": _different_top_level_fields(central, local) if not error else [],
            "source_refs": {
                "nas": nas_refs, "direct": direct_refs, "shared": shared_refs,
            },
            "timing": {
                "nas_response_ms": round(primary_latency_ms, 3),
                "comparison_queue_ms": round(queue_wait_ms, 3),
                "direct_response_ms": round(local_latency_ms, 3),
                "arrival_gap_ms": round(arrival_gap_ms, 3) if arrival_gap_ms is not None else None,
                "source_clock_skew_ms": _source_clock_skew_ms(central, local),
            },
            "comparison_scope": "same-provider delivery/transform/storage",
            "reason": (
                "responses differ without a shared immutable source reference"
                if status == "NOT_COMPARABLE" else ""
            ),
            "metrics": self._metrics.snapshot(),
        }
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        with self._writer_lock, self._report_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        if not equal:
            logger.warning("중앙·로컬 응답 차이: %s %s", api_id, row["different_fields"] or error)

    def validation_summary(self) -> dict[str, object]:
        return self._metrics.snapshot()

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
    """주 입력에 영향 없이 두 실시간 경로의 전달·변환 결과만 비교한다."""

    def __init__(
        self, report_path: Path, *, pending_limit: int = 2_000,
        report_queue_limit: int = 2_000,
        match_timeout_seconds: float = 5.0, late_threshold_ms: float = 1_000.0,
        monotonic_provider: Callable[[], float] = time.monotonic,
        now_provider: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._report_path = report_path
        self._pending_limit = max(100, pending_limit)
        self._match_timeout_seconds = max(0.001, float(match_timeout_seconds))
        self._late_threshold_ms = max(0.0, float(late_threshold_ms))
        self._monotonic = monotonic_provider
        self._now = now_provider
        self._central: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._local: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._counts: OrderedDict[tuple[object, ...], int] = OrderedDict()
        self._last_payload_hash: OrderedDict[tuple[str, str, str, str], str] = OrderedDict()
        self._last_event_clock: OrderedDict[tuple[str, str, str, str], int] = OrderedDict()
        self._metrics = _ValidationMetrics()
        self._lock = threading.Lock()
        self._report_jobs: queue.Queue[dict[str, object]] = queue.Queue(
            maxsize=max(1, report_queue_limit),
        )
        threading.Thread(
            target=self._write_loop, name="realtime-validation-writer", daemon=True,
        ).start()

    def observe_central(
        self, event_type: str, event: object, *, metadata: Mapping[str, object] | None = None,
    ) -> None:
        self._observe("nas", event_type, event, metadata=metadata)

    def observe_local(
        self, event_type: str, event: object, *, metadata: Mapping[str, object] | None = None,
    ) -> None:
        self._observe("direct", event_type, event, metadata=metadata)

    def _observe(
        self, side: str, event_type: str, event: object, *,
        metadata: Mapping[str, object] | None,
    ) -> None:
        payload = _event_payload(event)
        base_key = _realtime_key(event_type, payload)
        if base_key is None:
            self._record_uncomparable(side, event_type, payload, "event_identity_missing")
            return
        received_monotonic = self._monotonic()
        received_at = self._now().isoformat()
        source_ref = _provider_event_ref(event_type, payload, None)
        source_refs = tuple(dict.fromkeys((
            *(_extract_source_refs(payload)), *(_extract_source_refs(metadata or {})),
            *([source_ref] if source_ref else []),
        )))
        pending_rows: list[dict[str, object]] = []
        with self._lock:
            pending_rows.extend(self._expire_locked(received_monotonic))
            if source_ref:
                key = ("source_ref", event_type, source_ref)
                sequence = 1
            else:
                count_key = (side, *base_key)
                sequence = self._counts.get(count_key, 0) + 1
                self._counts[count_key] = sequence
                self._counts.move_to_end(count_key)
                _trim_ordered(self._counts, self._pending_limit * 4)
                key = ("clock_sequence", *base_key, sequence)
            stream_key = (side, event_type, base_key[1], base_key[2])
            payload_hash = json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True)
            duplicate = self._last_payload_hash.get(stream_key) == payload_hash
            self._last_payload_hash[stream_key] = payload_hash
            self._last_payload_hash.move_to_end(stream_key)
            event_clock = _event_clock_value(base_key[3])
            last_clock = self._last_event_clock.get(stream_key)
            out_of_order = event_clock is not None and last_clock is not None and event_clock < last_clock
            if event_clock is not None:
                self._last_event_clock[stream_key] = max(event_clock, last_clock or event_clock)
                self._last_event_clock.move_to_end(stream_key)
            _trim_ordered(self._last_payload_hash, self._pending_limit * 4)
            _trim_ordered(self._last_event_clock, self._pending_limit * 4)
            observation = {
                "payload": payload, "received_monotonic": received_monotonic,
                "received_at": received_at, "source_ref": source_ref,
                "source_refs": source_refs,
                "sequence": sequence, "duplicate": duplicate,
                "out_of_order": out_of_order, "metadata": dict(metadata or {}),
            }
            own, other = (self._central, self._local) if side == "nas" else (self._local, self._central)
            matched = other.pop(key, None)
            if matched is None:
                if key in own:
                    self._metrics.record("DUPLICATE")
                    pending_rows.append(self._single_status_row(
                        side, event_type, key, observation, "DUPLICATE",
                        "provider event reference repeated before counterpart arrival",
                    ))
                else:
                    own[key] = observation
                    own.move_to_end(key)
                    while len(own) > self._pending_limit:
                        evicted_key, evicted = own.popitem(last=False)
                        pending_rows.append(self._missing_row(
                            "nas" if own is self._central else "direct",
                            evicted_key, evicted, "pending_limit_eviction", eviction=True,
                        ))
            else:
                nas, direct = (
                    (observation, matched) if side == "nas" else (matched, observation)
                )
                pending_rows.append(self._comparison_row(event_type, key, nas, direct))
        for row in pending_rows:
            self._write_row(row)

    def _comparison_row(
        self, event_type: str, key: tuple[object, ...],
        nas: Mapping[str, object], direct: Mapping[str, object],
    ) -> dict[str, object]:
        central = dict(nas["payload"])
        local = dict(direct["payload"])
        ignored = {"received_at", "updated_at", "available_at"}
        different = sorted(
            field for field in set(central) | set(local)
            if field not in ignored and _canonical(central.get(field)) != _canonical(local.get(field))
        )
        gap_ms = abs(
            float(nas["received_monotonic"]) - float(direct["received_monotonic"])
        ) * 1_000
        ambiguous = key[0] == "clock_sequence" and max(
            int(nas["sequence"]), int(direct["sequence"]),
        ) > 1
        if bool(nas["out_of_order"]) or bool(direct["out_of_order"]):
            status = "OUT_OF_ORDER"
        elif bool(nas["duplicate"]) or bool(direct["duplicate"]):
            status = "DUPLICATE"
        elif ambiguous:
            status = "NOT_COMPARABLE"
        elif different:
            status = "VALUE_MISMATCH"
        elif gap_ms > self._late_threshold_ms:
            status = "LATE"
        else:
            status = "MATCH"
        self._metrics.record(status, arrival_gap_ms=gap_ms)
        return {
            "checked_at": self._now().isoformat(timespec="seconds"),
            "event_type": event_type, "identity": list(key[1:]),
            "status": status, "equal": not different,
            "different_fields": different,
            "source_refs": {
                "nas": list(nas["source_refs"]), "direct": list(direct["source_refs"]),
            },
            "venue": str(central.get("market") or local.get("market") or "UNKNOWN"),
            "unit": _event_unit(event_type),
            "aggregation_scope": "single_provider_event" if key[0] == "source_ref" else "same-clock arrival sequence",
            "comparable_aggregate_fields": _aggregate_fields(event_type, central, local),
            "timing": {
                "nas_received_at": nas["received_at"],
                "direct_received_at": direct["received_at"],
                "arrival_gap_ms": round(gap_ms, 3),
                "source_clock_skew_ms": _source_clock_skew_ms(
                    nas.get("metadata"), direct.get("metadata"),
                ),
            },
            "reason": "same-clock events lack a provider event id" if ambiguous else "",
            "comparison_scope": "same-provider delivery/transform/storage",
            "metrics": self._metrics.snapshot(),
        }

    def _missing_row(
        self, side: str, key: tuple[object, ...], observation: Mapping[str, object],
        reason: str, *, eviction: bool = False,
    ) -> dict[str, object]:
        age_ms = max(0.0, (self._monotonic() - float(observation["received_monotonic"])) * 1_000)
        self._metrics.record("MISSING", arrival_gap_ms=age_ms, eviction=eviction)
        payload = dict(observation["payload"])
        return {
            "checked_at": self._now().isoformat(timespec="seconds"),
            "event_type": str(key[1] if key and key[0] == "source_ref" else key[1]),
            "identity": list(key[1:]), "status": "MISSING", "equal": False,
            "different_fields": [], "missing_side": "direct" if side == "nas" else "nas",
            "source_refs": {side: list(observation["source_refs"])},
            "venue": str(payload.get("market") or "UNKNOWN"),
            "unit": _event_unit(str(key[1])), "aggregation_scope": "unmatched_event",
            "timing": {"unmatched_age_ms": round(age_ms, 3)},
            "reason": reason, "comparison_scope": "same-provider delivery/transform/storage",
            "metrics": self._metrics.snapshot(),
        }

    def _expire_locked(self, now_monotonic: float) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for side, pending in (("nas", self._central), ("direct", self._local)):
            while pending:
                key, observation = next(iter(pending.items()))
                if now_monotonic - float(observation["received_monotonic"]) < self._match_timeout_seconds:
                    break
                pending.popitem(last=False)
                rows.append(self._missing_row(side, key, observation, "match_timeout"))
        return rows

    def flush_expired(self) -> int:
        with self._lock:
            rows = self._expire_locked(self._monotonic())
        for row in rows:
            self._write_row(row)
        return len(rows)

    def validation_summary(self) -> dict[str, object]:
        summary = self._metrics.snapshot()
        with self._lock:
            summary["pending_nas_count"] = len(self._central)
            summary["pending_direct_count"] = len(self._local)
        summary["pending_report_count"] = self._report_jobs.qsize()
        return summary

    def wait_for_writes(self) -> None:
        """테스트와 명시적 진단 종료에서만 현재 대기 중인 파일 기록을 기다린다."""
        self._report_jobs.join()

    def _record_uncomparable(
        self, side: str, event_type: str, payload: Mapping[str, object], reason: str,
    ) -> None:
        self._metrics.record("NOT_COMPARABLE")
        self._write_row({
            "checked_at": self._now().isoformat(timespec="seconds"),
            "event_type": event_type, "identity": [], "status": "NOT_COMPARABLE",
            "equal": False, "different_fields": [], "observed_side": side,
            "source_refs": {side: _extract_source_refs(payload)},
            "venue": str(payload.get("market") or "UNKNOWN"),
            "unit": _event_unit(event_type), "aggregation_scope": "none",
            "reason": reason, "comparison_scope": "same-provider delivery/transform/storage",
            "metrics": self._metrics.snapshot(),
        })

    def _single_status_row(
        self, side: str, event_type: str, key: tuple[object, ...],
        observation: Mapping[str, object], status: str, reason: str,
    ) -> dict[str, object]:
        payload = dict(observation["payload"])
        return {
            "checked_at": self._now().isoformat(timespec="seconds"),
            "event_type": event_type, "identity": list(key[1:]),
            "status": status, "equal": False, "different_fields": [],
            "observed_side": side,
            "source_refs": {side: list(observation["source_refs"])},
            "venue": str(payload.get("market") or "UNKNOWN"),
            "unit": _event_unit(event_type), "aggregation_scope": "single_provider_event",
            "reason": reason, "comparison_scope": "same-provider delivery/transform/storage",
            "metrics": self._metrics.snapshot(),
        }

    def _write_row(self, row: Mapping[str, object]) -> None:
        try:
            self._report_jobs.put_nowait(dict(row))
        except queue.Full:
            self._metrics.record("MISSING", queue_skip=True)
            logger.warning("실시간 대조 기록 대기열이 가득 차 비교 결과를 건너뜁니다.")

    def _write_loop(self) -> None:
        while True:
            row = self._report_jobs.get()
            try:
                self._report_path.parent.mkdir(parents=True, exist_ok=True)
                with self._report_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            except OSError:
                self._metrics.record("MISSING", queue_skip=True)
                logger.exception("실시간 대조 결과를 기록하지 못했습니다.")
            finally:
                self._report_jobs.task_done()


def _event_payload(event: object) -> dict[str, object]:
    if is_dataclass(event) and not isinstance(event, type):
        return {str(key): value for key, value in asdict(event).items()}
    if isinstance(event, dict):
        return {str(key): value for key, value in event.items()}
    return dict(vars(event)) if hasattr(event, "__dict__") else {}


def _realtime_key(event_type: str, payload: dict[str, object]) -> tuple[str, str, str, str] | None:
    code = str(payload.get("code") or payload.get("index_code") or payload.get("market") or "")
    venue = str(payload.get("market") or payload.get("exchange") or "UNKNOWN")
    event_time = str(payload.get("trade_time") or payload.get("execution_time") or payload.get("time") or "")
    if not code or not event_time:
        return None
    return event_type, code, venue, event_time


def _provider_event_ref(
    event_type: str, payload: Mapping[str, object], metadata: Mapping[str, object] | None,
) -> str:
    if event_type == "order_execution" and payload.get("execution_no") not in {None, ""}:
        return f"{payload.get('order_no', '')}:{payload['execution_no']}"
    candidates: tuple[object, ...] = (
        payload.get("event_id"), payload.get("revision_id"), payload.get("source_ref"),
    )
    return next((str(value) for value in candidates if value is not None and value != ""), "")


def _extract_source_refs(value: object) -> list[str]:
    found: list[str] = []

    def visit(item: object, depth: int = 0) -> None:
        if depth > 3 or len(found) >= 50:
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                name = str(key)
                if name in {
                    "revision_id", "source_ref", "event_id", "observation_key",
                    "accepted_sequence", "dataset_id",
                } \
                        or name.endswith("_revision_id"):
                    if child is not None and child != "":
                        found.append(str(child))
                else:
                    visit(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, depth + 1)

    visit(value)
    return list(dict.fromkeys(found))


def _source_clock_skew_ms(left: object, right: object) -> float | None:
    left_time = _source_timestamp(left)
    right_time = _source_timestamp(right)
    if left_time is None or right_time is None:
        return None
    return round(abs((left_time - right_time).total_seconds()) * 1_000, 3)


def _source_timestamp(value: object) -> datetime | None:
    if isinstance(value, (tuple, list)) and value:
        value = value[0]
    if not isinstance(value, Mapping):
        return None
    raw = value.get("received_at") or value.get("available_at")
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (OSError, TypeError, ValueError):
        return None


def _event_clock_value(value: str) -> int | None:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) < 6:
        return None
    try:
        return int(digits[-6:])
    except ValueError:
        return None


def _event_unit(event_type: str) -> str:
    return {
        "trade": "price=KRW,volume=shares,trade_value=KRW",
        "order_execution": "price=KRW,quantity=shares",
        "market_state": "index=points,trade_value=million_KRW",
        "program_trade": "quantity=shares,amount=million_KRW",
    }.get(event_type, "UNKNOWN")


def _aggregate_fields(
    event_type: str, left: Mapping[str, object], right: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    names = {
        "trade": ("current_price", "cumulative_volume", "cumulative_trade_value"),
        "order_execution": ("price", "quantity"),
        "market_state": ("index_value", "cumulative_trade_value_million_won"),
        "program_trade": ("net_buy_quantity", "net_buy_amount_million_won"),
    }.get(event_type, ())
    return {
        name: {"nas": left.get(name), "direct": right.get(name)}
        for name in names if name in left or name in right
    }


def _trim_ordered(values: OrderedDict[Any, Any], limit: int) -> None:
    while len(values) > max(1, limit):
        values.popitem(last=False)


def _percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, (len(values) * percent + 99) // 100 - 1))
    return round(float(values[index]), 3)

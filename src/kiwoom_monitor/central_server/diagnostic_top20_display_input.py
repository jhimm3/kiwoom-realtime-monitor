"""Operation-owned TOP20 display environment observations and result receipts.

Only scalar values actually consumed by the native display methods are recorded.
Replay never changes a global clock or reads the operating pause control.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import fields
from datetime import date, datetime
import hashlib
import json
import math
from threading import BoundedSemaphore

from kiwoom_monitor.domain.top20_reconciliation import Top20ReconciliationInput

VERSION = 'top20-display-input/v1'
METHODS = frozenset({'load_top20_series', 'load_top20_reconciliation_inputs', 'load_top20_display_statistics'})
MAX_VALUES = 50_000
_SCOPE = ContextVar('top20_display_environment', default=None)
_RESULT_COPY_SLOTS = BoundedSemaphore(2)


def _validate(tag, value):
    valid = False
    if tag == 'today':
        try:
            valid = type(value) is str and date.fromisoformat(value).isoformat() == value
        except ValueError:
            pass
    elif tag == 'computed_at':
        try:
            instant = datetime.fromisoformat(value) if type(value) is str else None
            valid = instant is not None and instant.utcoffset() is not None
        except ValueError:
            pass
    elif tag == 'cache_write_paused':
        valid = type(value) is bool
    elif tag == 'saved_at':
        valid = type(value) in (float, int) and math.isfinite(value) and value >= 0
    if not valid:
        raise ValueError('top20_display_environment_value_invalid')


def _encoded(ordinal, tag, value):
    return json.dumps([ordinal, tag, value], ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8') + b'\n'


def environment_value(tag, producer):
    scope = _SCOPE.get()
    return producer() if scope is None else scope.consume(tag, producer)


@contextmanager
def environment_scope(scope):
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


class DisplayCapture:
    def __init__(self, trace_id, operation_id):
        self.trace_id, self.operation_id = trace_id, operation_id
        self.count, self.digest = 0, hashlib.sha256()

    def consume(self, tag, producer):
        value = producer()
        from . import diagnostic_trace as trace
        try:
            _validate(tag, value)
            if self.count >= MAX_VALUES:
                raise ValueError('top20_display_environment_limit')
            self.count += 1
            self.digest.update(_encoded(self.count, tag, value))
            trace.emit(self.trace_id, 'top20_display_environment', {
                'operation_id': self.operation_id, 'input_operation_id': self.operation_id,
                'display_input_version': VERSION,
                'ordinal': self.count, 'tag': tag, 'value': value})
        except Exception:
            # Observability must preserve the original native return/exception.
            try:
                trace.reject_input(self.trace_id, {'operation_id': self.operation_id,
                    'reason': 'top20_display_environment_capture_failed'})
            except Exception:
                pass
        return value

    def receipt(self):
        return {'display_environment_count': self.count,
                'display_environment_sha256': self.digest.hexdigest()}


def prepare_environment(start, ending, events):
    if (start.get('display_input_version') != VERSION
            or ending.get('display_input_version') != VERSION
            or start.get('display_result_profile') not in {'store-input/v1', 'large-store-input/v1'}
            or start['display_result_profile'] != ending.get('display_result_profile')):
        raise ValueError('top20_display_input_contract_missing')
    rows = [row for row in events if row.get('event_type') == 'top20_display_environment'
            and row.get('operation_id') == start['operation_id']]
    digest = hashlib.sha256()
    values = []
    if len(rows) > MAX_VALUES:
        raise ValueError('top20_display_environment_limit')
    for ordinal, row in enumerate(rows, 1):
        if (row.get('display_input_version') != VERSION or type(row.get('ordinal')) is not int
                or row['ordinal'] != ordinal or not start['seq'] < row['seq'] < ending['seq']):
            raise ValueError('top20_display_environment_order_invalid')
        _validate(row.get('tag'), row.get('value'))
        digest.update(_encoded(ordinal, row['tag'], row['value']))
        values.append((row['tag'], row['value']))
    if (type(ending.get('display_environment_count')) is not int
            or ending['display_environment_count'] != len(values)
            or ending.get('display_environment_sha256') != digest.hexdigest()):
        raise ValueError('top20_display_environment_receipt_mismatch')
    if ending.get('outcome') == 'returned' and (
            type(ending.get('display_result_sha256')) is not str
            or len(ending['display_result_sha256']) != 64
            or any(ch not in '0123456789abcdef' for ch in ending['display_result_sha256'])):
        raise ValueError('top20_display_result_receipt_missing')
    return tuple(values)


class DisplayReplay:
    def __init__(self, values):
        self.values, self.index = values, 0

    def consume(self, tag, producer):
        if self.index >= len(self.values) or self.values[self.index][0] != tag:
            raise ValueError('top20_display_environment_consumption_mismatch')
        value = self.values[self.index][1]
        self.index += 1
        return value

    def finish(self):
        if self.index != len(self.values):
            raise ValueError('top20_display_environment_unconsumed')


def result_digest(value, *, profile='store-input/v1'):
    from .diagnostic_replay_contract import freeze_payload, thaw_payload
    from .diagnostic_replay_contract import MAX_COPY_BYTES, LARGE_MAX_COPY_BYTES
    if profile not in {'store-input/v1', 'large-store-input/v1'}:
        raise ValueError('top20_display_result_profile_invalid')
    maximum_bytes = LARGE_MAX_COPY_BYTES if profile == 'large-store-input/v1' else MAX_COPY_BYTES
    # Do not multiply the 64MiB transient copy by the executor's thread count.
    # Saturation rejects observation without delaying or changing a native return.
    if not _RESULT_COPY_SLOTS.acquire(blocking=False):
        raise ValueError('top20_display_result_copy_busy')
    try:
        if type(value) is Top20ReconciliationInput:
            value = {field.name: getattr(value, field.name) for field in fields(Top20ReconciliationInput)}
        # Bounded codec checks secrets, types, nodes and memory before JSON hashing.
        value = thaw_payload(freeze_payload(value, maximum_bytes=maximum_bytes).value, maximum_bytes=maximum_bytes)
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    finally:
        _RESULT_COPY_SLOTS.release()

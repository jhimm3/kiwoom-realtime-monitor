"""Bounded archive identity index; no connection, native writes or authority policy.

Canonical bodies are derived only from supplied recorded native arguments.
This module cannot read a production DB or dispatch a native operation.
"""
import hashlib
import json
import math

COLLECTIONS = ('real_account_event', 'real_account_recovery')
CHUNK_ROWS = 256
MAX_CHUNK_CHARS = 65536


def iter_index(groups):
    """Validate strict key ordering across chunks with one decoded chunk retained."""
    previous = None
    if type(groups) is not list:
        raise ValueError('account_context_archive_index_invalid')
    for group in groups:
        if (type(group) is not dict or set(group) != {'collection', 'owner', 'entries'}
                or group['collection'] not in COLLECTIONS
                or type(group['owner']) is not str or not 1 <= len(group['owner']) <= 200
                or type(group['entries']) is not str
                or len(group['entries']) > MAX_CHUNK_CHARS):
            raise ValueError('account_context_archive_index_invalid')
        try:
            entries = json.loads(group['entries'])
        except ValueError:
            raise ValueError('account_context_archive_index_invalid') from None
        if type(entries) is not list or not 1 <= len(entries) <= CHUNK_ROWS:
            raise ValueError('account_context_archive_index_invalid')
        for pair in entries:
            if (type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str
                    or len(pair[0]) != 64 or any(c not in '0123456789abcdef' for c in pair[0])
                    or type(pair[1]) not in (int, float) or not math.isfinite(pair[1])):
                raise ValueError('account_context_archive_index_invalid')
            identity = group['collection'], group['owner'], pair[0]
            if previous is not None and identity <= previous:
                raise ValueError('account_context_archive_index_order_invalid')
            previous = identity
            yield identity, pair[1]


def pack_index(rows, *, maximum_rows, maximum_bytes):
    """Consume numeric/key-only SQL rows, accounting retained chunks and builder."""
    from .diagnostic_replay_contract import freeze_payload
    result, pending, current = [], [], None
    charge = count = 0

    def flush():
        nonlocal charge
        if pending:
            group = {'collection': current[0], 'owner': current[1],
                     'entries': json.dumps(pending, separators=(',', ':'), allow_nan=False)}
            charge += freeze_payload(group).charge + 1024
            # Bound one 256-row builder plus its JSON/frozen temporary copies.
            if charge + 256 * 1024 > maximum_bytes:
                raise ValueError('account_context_byte_limit')
            result.append(group)
            pending.clear()

    for collection, owner, key, updated in rows:
        count += 1
        if count > maximum_rows:
            raise ValueError('account_context_row_limit')
        if current != (collection, owner) or len(pending) == CHUNK_ROWS:
            flush()
            current = collection, owner
        pending.append([key, updated])
    flush()
    # Validate while retaining only one additional small decoded chunk.
    for _ in iter_index(result):
        pass
    return result, count, charge


def prepare_seed(index, owner_rows, *, maximum_bytes):
    """Restore only initial keys actually present in the recorded frontier."""
    from .diagnostic_replay_contract import thaw_operation_arguments
    from .database_account_settings import (
        _real_account_recovery_document, _real_account_event_document, _account_settings_owner,
    )
    if type(owner_rows) not in (list, tuple) or len(owner_rows) > 4096:
        raise ValueError('account_context_archive_frontier_invalid')
    candidates, total = {}, 0
    for row in owner_rows:
        method = row.get('method')
        if method not in {'save_real_account_recovery', 'save_real_account_event'}:
            continue
        args = thaw_operation_arguments(row)
        try:
            if method == 'save_real_account_recovery':
                scope, body = _real_account_recovery_document(**args)
                collection = 'real_account_recovery'
            else:
                scope, body = _real_account_event_document(**args)
                collection = 'real_account_event'
        except ValueError as error:
            if str(error) not in {'REAL_ACCOUNT_RECOVERY_INVALID', 'REAL_ACCOUNT_EVENT_INVALID', 'ACCOUNT_CONTEXT_MISMATCH'}:
                raise
            continue  # Invalid native input cannot create an archive row.
        identity = collection, _account_settings_owner(scope), hashlib.sha256(body.encode()).hexdigest()
        if identity not in candidates:
            total += 4 * len(body) + 1024
            if total > maximum_bytes:
                raise ValueError('account_context_archive_seed_limit')
            candidates[identity] = body
    result = []
    for identity, updated in iter_index(index):
        if identity in candidates:
            collection, owner, key = identity
            result.append({'collection': collection, 'owner': owner, 'document_key': key,
                           'updated_at': updated, 'document_json': json.loads(candidates.pop(identity))})
    return result

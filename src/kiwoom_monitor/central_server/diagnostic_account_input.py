"""Explicit account-input projection; authority tokens never enter trace payloads.

This module observes native arguments. It neither dispatches orders nor grants
replay access to a DB. A source owner context supplies the actual run ID.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

VERSION = 'account-store-input/v1'
METHODS = frozenset({
    'save_real_account_recovery', 'save_real_account_event',
    'save_execution_account_snapshot', 'acquire_execution_runtime',
    'release_execution_runtime', 'create_execution_intent', 'append_execution_event',
})
LEASE_METHODS = frozenset({'acquire_execution_runtime', 'release_execution_runtime'})
NATIVE_ERROR_CODES = frozenset({
    'ACCOUNT_SETTINGS_SCOPE_INVALID', 'ACCOUNT_IDENTITY_UNVERIFIED',
    'REAL_ACCOUNT_RECOVERY_INVALID', 'REAL_ACCOUNT_EVENT_INVALID', 'ACCOUNT_CONTEXT_MISMATCH',
    'EXECUTION_OWNER_SCOPE_MISMATCH', 'EXECUTION_OWNERSHIP_LOST', 'MOCK_AUTOMATION_CONTROL_CHANGED',
})
_OWNER = ContextVar('recorded_execution_source_owner', default=None)


def native_error_receipt(method, error):
    if method in METHODS and type(error) in {ValueError, RuntimeError} and str(error) in NATIVE_ERROR_CODES:
        return {'native_error_code': str(error)}
    return {}  # Arbitrary exception text can contain tokens or raw account data.


@contextmanager
def source_execution_owner(owner_key, run_id):
    # The repository knows these values; splitting a token cannot recover them.
    token = _OWNER.set((owner_key, run_id))
    try:
        yield
    finally:
        _OWNER.reset(token)


class AccountInputProjection:
    def __init__(self, trace_id):
        self.trace_id = trace_id
        self._key = secrets.token_bytes(32)

    def _alias(self, token, owner_key, run_id):
        if type(token) is not str or not token or len(token) > 4096:
            raise ValueError('account_owner_token_invalid')
        if (type(owner_key) is not str or not owner_key or len(owner_key) > 1024
                or run_id is not None and (type(run_id) is not str or not run_id or len(run_id) > 1024)):
            raise ValueError('account_owner_context_invalid')
        alias = hmac.new(self._key, token.encode('utf-8'), hashlib.sha256).hexdigest()
        binding = {'owner_alias': alias, 'owner_key': owner_key, 'run_id': run_id,
                   'prefix_matches': token.startswith(run_id + ':') if run_id is not None else None}
        # HMAC is deterministic within the session. Retaining an alias registry
        # here would duplicate already charged event metadata in uncharged RAM.
        return alias, binding

    def project_initial_lease(self, owner_key, owner_token):
        # A DB row does not reveal its run ID. Ignore any unrelated caller
        # context and preserve this initial owner as an opaque authority alias.
        alias, binding = self._alias(owner_token, owner_key, None)
        return alias, {'method': 'release_execution_runtime', 'account_input_version': VERSION,
                       'account_alias_domain': self.trace_id, 'execution_owner_binding': binding}

    def project(self, method, arguments):
        if method not in METHODS or type(arguments) is not dict:
            raise ValueError('account_input_method_invalid')
        projected = dict(arguments)
        metadata = {'account_input_version': VERSION, 'account_alias_domain': self.trace_id}
        if method in LEASE_METHODS:
            context = _OWNER.get()
            run_id = None
            if context is not None:
                if context[0] != arguments.get('owner_key'):
                    raise ValueError('account_owner_context_mismatch')
                run_id = context[1]
            alias, binding = self._alias(arguments.get('owner_token'), arguments.get('owner_key'), run_id)
            projected.pop('owner_token')
            projected['owner_alias'] = alias
            metadata['execution_owner_binding'] = binding
        elif arguments.get('ownership') is not None:
            ownership = arguments['ownership']
            if type(ownership) is not dict:
                raise ValueError('account_ownership_invalid')
            alias, binding = self._alias(ownership.get('owner_token'), ownership.get('owner_key'),
                                         ownership.get('run_id'))
            projected['ownership'] = {key: value for key, value in ownership.items() if key != 'owner_token'}
            projected['ownership']['owner_alias'] = alias
            metadata['execution_owner_binding'] = binding
        return projected, metadata


@dataclass(frozen=True)
class ResolvedOwnerAlias:
    token: str
    contexts: frozenset[tuple[str, str | None, bool | None]]


def resolve_owner_bindings(rows):
    """Preserve one authority identity across the whole selected input frontier.

    A run ID is opaque and may itself contain colons. Multiple observed valid
    prefixes must form one prefix chain; invalid prefixes must stay invalid.
    This resolves metadata only and does not touch a DB or infer missing runs.
    """
    grouped = {}
    for row in rows:
        if row.get('event_type') not in (None, 'operation_start') or 'execution_owner_binding' not in row:
            continue
        if (row.get('account_input_version') != VERSION or row.get('method') not in METHODS
                or type(row.get('account_alias_domain')) is not str
                or not 1 <= len(row['account_alias_domain']) <= 128):
            raise ValueError('recorded_account_envelope_invalid')
        binding = row['execution_owner_binding']
        _validate_owner_binding(binding)
        key = row['account_alias_domain'], binding['owner_alias']
        grouped.setdefault(key, set()).add((binding['owner_key'], binding['run_id'], binding['prefix_matches']))
        if len(grouped) > 4096:
            raise ValueError('recorded_account_alias_limit')
    result = {}
    for (domain, alias), contexts in grouped.items():
        valid = [run for _, run, matches in contexts if matches is True]
        padding = '!' * (max(len(run or '') for _, run, _ in contexts) + 1)
        prefix = max(valid, key=len) + ':' + padding if valid else padding
        token = prefix + ':trace-test-' + domain + '-' + alias
        if any(run is not None and token.startswith(run + ':') != matches for _, run, matches in contexts):
            raise ValueError('recorded_account_owner_prefix_conflict')
        result[(domain, alias)] = ResolvedOwnerAlias(token, frozenset(contexts))
    return result


def _validate_owner_binding(binding):
    if (type(binding) is not dict
            or set(binding) != {'owner_alias', 'owner_key', 'run_id', 'prefix_matches'}
            or type(binding['owner_alias']) is not str or len(binding['owner_alias']) != 64
            or any(char not in '0123456789abcdef' for char in binding['owner_alias'])
            or type(binding['owner_key']) is not str or not 1 <= len(binding['owner_key']) <= 1024
            or binding['run_id'] is None and binding['prefix_matches'] is not None
            or binding['run_id'] is not None and (
                type(binding['run_id']) is not str or not 1 <= len(binding['run_id']) <= 1024
                or type(binding['prefix_matches']) is not bool)):
        raise ValueError('recorded_account_owner_binding_invalid')


def restore_account_arguments(row, arguments, *, bindings=None):
    """Reconstruct only a test authority value; native replay needs separate gates."""
    if (row.get('account_input_version') != VERSION or row.get('method') not in METHODS
            or type(arguments) is not dict or type(row.get('account_alias_domain')) is not str
            or not row['account_alias_domain']):
        raise ValueError('recorded_account_envelope_invalid')
    result = dict(arguments)
    method = row['method']
    ownership = result.get('ownership')
    if method not in LEASE_METHODS and ownership is None:
        if 'execution_owner_binding' in row:
            raise ValueError('recorded_account_unexpected_owner')
        return result
    if method not in LEASE_METHODS and type(ownership) is not dict:
        raise ValueError('recorded_account_ownership_invalid')
    target = result if method in LEASE_METHODS else dict(ownership)
    if 'owner_token' in target:
        raise ValueError('recorded_account_raw_owner_token')
    alias = target.get('owner_alias')
    binding = row.get('execution_owner_binding')
    _validate_owner_binding(binding)
    if binding['owner_alias'] != alias or binding['owner_key'] != target.get('owner_key'):
        raise ValueError('recorded_account_owner_binding_invalid')
    run_id = binding['run_id']
    if method not in LEASE_METHODS and (run_id is None or run_id != target.get('run_id')):
        raise ValueError('recorded_account_owner_run_unresolved')
    target.pop('owner_alias')
    suffix = 'trace-test-' + row['account_alias_domain'] + '-' + alias
    # An invalid original prefix must remain invalid, including unusual run IDs.
    target['owner_token'] = (run_id + ':' + suffix if binding['prefix_matches'] is True
                             else '!' * (len(run_id or '') + 1) + ':' + suffix)
    if bindings is not None:
        resolved = bindings.get((row['account_alias_domain'], alias))
        context = binding['owner_key'], run_id, binding['prefix_matches']
        if (type(resolved) is not ResolvedOwnerAlias or context not in resolved.contexts
                or run_id is not None and resolved.token.startswith(run_id + ':') != binding['prefix_matches']):
            raise ValueError('recorded_account_owner_resolution_invalid')
        target['owner_token'] = resolved.token
    if method not in LEASE_METHODS:
        result['ownership'] = target
    return result

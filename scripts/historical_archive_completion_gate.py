"""Orchestrate the locked full seal gate once; verify its receipt.

Construction rules and the lock/transaction boundary remain unchanged. A
separately fingerprinted offline verifier may reduce equivalent read work;
its identity is preserved in the actual stored gate. An external receipt alone
never establishes completion, and recovery requires its full archive checksum.
"""
from contextlib import closing
import json
import hashlib
import importlib.util
from pathlib import Path
import sqlite3


def _read_sealed(path, receipt, dataset, digest, verification_code=None):
    from scripts.prepared_historical_archive_v2 import report
    publication = json.loads(receipt.read_text(encoding='utf-8'))
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        value = report(db)
    gate = value.get('seal_gate', {})
    if verification_code is not None and gate.get('verification_code') != verification_code:
        raise ValueError('sealed verifier provenance mismatch')
    if (publication.get('schema') != 'historical-news-publication/v2' or
            publication.get('dataset_id') != dataset or
            Path(publication.get('archive', '')).resolve() != path or
            publication.get('bytes') != path.stat().st_size or
            value.get('dataset_id') != dataset or
            value.get('builder_code_digest') != digest or
            value.get('build_state') != 'sealed' or
            publication.get('sealed_at') != value.get('sealed_at') or
            publication.get('coverage_state') != value.get('coverage_state') or
            gate.get('dataset_id') != dataset or
            gate.get('ready_for_seal') is not True or gate.get('blockers') != [] or
            gate.get('integrity_check') != 'ok'):
        raise ValueError('publication receipt or locked seal gate mismatch')
    for suffix in ('-wal', '-journal'):
        extra = Path(str(path)+suffix)
        if extra.exists() and extra.stat().st_size:
            raise ValueError('sealed archive has journal content')
    return publication, gate


def _verifier(verification_code, digest):
    """Load only explicit immutable offline verifier files; keep constructor imports."""
    if verification_code is None:
        return None, None
    from scripts import prepared_historical_archive_v2 as construction
    if verification_code.get('construction_digest') != digest or construction.code_digest() != digest:
        raise ValueError('verifier construction binding mismatch')
    modules = []
    for kind in ('audit', 'seal'):
        path = Path(verification_code[kind+'_path']).resolve(strict=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != verification_code[kind+'_sha256']:
            raise ValueError('verifier source checksum mismatch')
        spec = importlib.util.spec_from_file_location('_archive_checked_'+kind, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    auditor, sealer = modules
    if auditor.code_digest is not construction.code_digest or sealer._writer is not construction._writer:
        raise ValueError('verifier imports changed construction owner')
    def checked_audit(*args, **kwargs):
        if kwargs.get('full_integrity_check') is not True:
            raise ValueError('full verification required')
        result = auditor.audit(*args, **kwargs)
        return {**result, 'verification_code': dict(verification_code)}
    return sealer.seal, checked_audit


def verified_publication(path: Path, receipt: Path, dataset: str, digest: str, *, verification_code=None):
    """Read-only reuse/recovery of an actual seal, guarded by full file hash."""
    from scripts.prepared_historical_archive_v2 import _exclusive_lock, code_digest
    from scripts.stage_prepared_historical_news_archive import _sha256
    path = path.resolve(strict=True); receipt = receipt.resolve(strict=True)
    if path == receipt or code_digest() != digest:
        raise ValueError('publication paths or frozen code digest mismatch')
    _verifier(verification_code, digest)
    with _exclusive_lock(path):
        publication, gate = _read_sealed(path, receipt, dataset, digest, verification_code)
        if _sha256(path) != publication.get('sha256'):
            raise ValueError('sealed archive checksum mismatch')
    return publication, gate


def audit_and_seal(path: Path, seed: Path, receipt: Path, dataset: str, digest: str, *, verification_code=None):
    """The selected sealer owns the lock, full audit and seal transaction."""
    from scripts.prepared_historical_archive_v2 import code_digest, report
    from scripts.seal_prepared_historical_archive_v2 import seal
    path = path.resolve(strict=True); seed = seed.resolve(strict=True); receipt = receipt.resolve()
    if code_digest() != digest:
        raise ValueError('frozen code digest mismatch')
    selected_seal, selected_audit = _verifier(verification_code, digest)
    if receipt.exists():
        publication, gate = verified_publication(path, receipt, dataset, digest, verification_code=verification_code)
    else:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            before = report(db)
        if before.get('dataset_id') != dataset or before.get('builder_code_digest') != digest:
            raise ValueError('construction identity mismatch')
        publication = (seal(path, seed, receipt) if selected_seal is None else
                       selected_seal(path, seed, receipt, auditor=selected_audit))
        # This receipt was just emitted by the selected sealer after its own
        # checksum. Re-read both manifests; subsequent receipt reuse hashes it.
        stored, gate = _read_sealed(path, receipt, dataset, digest, verification_code)
        if stored != publication:
            raise ValueError('fresh seal receipt changed')
    return {**gate, 'publication_receipt': publication,
            'full_audit_owned_by_sealer': True}

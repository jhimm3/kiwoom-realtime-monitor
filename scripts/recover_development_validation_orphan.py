"""Preview or cancel owned sequential research runs after their UI child exits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.presentation.process_control import process_identity_state
from kiwoom_monitor.research_process import load_development_validation_request
from scripts.inspect_research_operation_receipts import load_valid_receipt


REASON = 'sequential validation UI child exited before completing this run; manual resume required'


def recover_receipt(path: Path, *, execute: bool = False) -> dict[str, object]:
    receipt = load_valid_receipt(path)
    operation = str(receipt['operation'])
    if receipt['kind'] != 'development_validation' or not operation.startswith('development_validation_'):
        raise ValueError('only sequential validation UI receipts can be recovered')
    operation_id = operation.removeprefix('development_validation_')
    if len(operation_id) != 32 or any(char not in '0123456789abcdef' for char in operation_id):
        raise ValueError('sequential validation operation ID is invalid')
    owner_token = 'development-ui-owner-' + operation_id
    batch = load_development_validation_request(Path(receipt['request']))
    if process_identity_state(receipt['pid'], receipt['start_token']) != 'exited':
        raise ValueError('sequential validation child exit is not confirmed')
    result_path = Path(receipt['result'])
    if result_path.is_symlink():
        raise ValueError('validation result path is a symlink')
    if result_path.exists():
        if result_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('validation result exceeds 16 MiB')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if (not isinstance(result, dict) or result.get('kind') != 'independent_development_validation'
                or result.get('database') != str(batch.request.database.resolve())
                or result.get('fold_names') != list(batch.fold_names)):
            raise ValueError('validation result does not match the frozen request')
    repository = ResearchRepository(batch.request.database, read_only=True)
    if repository.schema_version() < 24:
        raise ValueError('this database predates independent run ownership; old RUNNING runs remain manual review')
    rows = []
    for owner in repository.load_independent_run_owners(owner_token):
        if owner['status'] != 'running':
            continue
        run_id = owner['run_id']
        run = repository.load_run(run_id)
        manifest = batch.request.runs_dir / run_id / 'manifest.json'
        if (run is None or run['spec'].get('execution_scope') != 'independent_development_validation/v1'
                or manifest.exists() or manifest.is_symlink()):
            rows.append({'run_id': run_id, 'state': 'needs_review'})
            continue
        state = 'eligible'
        if execute:
            if process_identity_state(receipt['pid'], receipt['start_token']) != 'exited':
                raise ValueError('sequential validation child exit is no longer confirmed')
            if manifest.exists() or manifest.is_symlink():
                raise ValueError('research run artifact appeared during recovery')
            writable = ResearchRepository(batch.request.database)
            changed = writable.cancel_exited_independent_run(
                run_id, owner_token=owner_token, generation=owner['generation'], reason=REASON)
            state = 'cancelled' if changed else 'already_cancelled'
        rows.append({'run_id': run_id, 'state': state})
    return {'mode': 'execute' if execute else 'preview', 'operation': operation, 'runs': rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--execute', action='store_true', help='cancel eligible RUNNING runs atomically')
    args = parser.parse_args(argv)
    print(json.dumps(recover_receipt(args.receipt, execute=args.execute), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

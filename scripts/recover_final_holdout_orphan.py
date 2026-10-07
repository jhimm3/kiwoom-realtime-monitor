"""Preview or CAS-cancel final candidates owned by one verified exited UI child."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.presentation.process_control import process_identity_state
from kiwoom_monitor.research_process import load_final_holdout_execution_request
from scripts.inspect_research_operation_receipts import load_valid_receipt


REASON = 'final UI child exited before completing this candidate; explicit recovery required'


def recover_receipt(path: Path, *, execute: bool = False) -> dict[str, object]:
    receipt = load_valid_receipt(path)
    operation = str(receipt['operation'])
    if receipt['kind'] != 'final_holdout' or not operation.startswith('final_holdout_'):
        raise ValueError('only final UI ownership receipts can be recovered')
    request = load_final_holdout_execution_request(Path(receipt['request']))
    if request.owner_token != 'final-ui-owner-' + operation.removeprefix('final_holdout_'):
        raise ValueError('final request owner is not bound to this UI operation')
    if process_identity_state(receipt['pid'], receipt['start_token']) != 'exited':
        raise ValueError('final child exit is not confirmed')
    result_path = Path(receipt['result'])
    if result_path.is_symlink():
        raise ValueError('result path is a symlink')
    if result_path.exists():
        if result_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('final result exceeds 16 MiB')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if (not isinstance(result, dict) or result.get('status') != 'running'
                or result.get('kind') != 'independent_final_holdout'
                or result.get('version') != 'independent_final_holdout_result/v1'
                or result.get('batch_id') != request.batch.batch_id
                or result.get('window_id') != request.batch.window_id):
            raise ValueError('final child has a terminal or invalid result; inspect manually')
    first = request.candidates[0]
    repository = ResearchRepository(first.database)
    window = repository.load_final_holdout_window(request.batch.window_id)
    if (window is None or window['state'] != 'FINAL_RESERVED'
            or window['batch_id'] != request.batch.batch_id
            or window['spec'] != request.batch.to_dict()):
        raise ValueError('locked final batch is missing, changed, or exposed')
    rows = []
    for row in repository.load_final_holdout_executions(request.batch.batch_id):
        if row['owner_token'] != request.owner_token or row['state'] != 'RUNNING':
            continue
        run = repository.load_run(row['run_id'])
        manifest = first.runs_dir / row['run_id'] / 'manifest.json'
        if (run is None or run['status'] != 'running'
                or run['spec'].get('execution_scope') != 'independent_final_holdout/v1'
                or manifest.exists() or manifest.is_symlink()):
            rows.append({'candidate_spec_hash': row['candidate_spec_hash'],
                         'run_id': row['run_id'], 'state': 'needs_review'})
            continue
        state = 'eligible'
        if execute:
            if process_identity_state(receipt['pid'], receipt['start_token']) != 'exited':
                raise ValueError('final child exit is no longer confirmed')
            if manifest.exists() or manifest.is_symlink():
                raise ValueError('final run artifact appeared during recovery')
            changed = repository.cancel_exited_final_holdout_execution(
                request.batch, candidate_spec_hash=row['candidate_spec_hash'],
                run_id=row['run_id'], owner_token=request.owner_token,
                generation=row['generation'], reason=REASON)
            state = 'cancelled' if changed else 'already_cancelled'
        rows.append({'candidate_spec_hash': row['candidate_spec_hash'],
                     'run_id': row['run_id'], 'state': state})
    return {'mode': 'execute' if execute else 'preview', 'operation': operation,
            'candidates': rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--execute', action='store_true', help='cancel eligible RUNNING candidates atomically')
    args = parser.parse_args(argv)
    print(json.dumps(recover_receipt(args.receipt, execute=args.execute), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Read-only inspection of sequential/final research child ownership receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from kiwoom_monitor.presentation.process_control import process_identity_state


PREFIXES = ('development_validation_', 'final_holdout_')


def load_valid_receipt(path: Path) -> dict[str, object]:
    """Validate one local receipt and its frozen request before trusting its PID."""
    path = Path(path).absolute()
    identifier = path.name.removesuffix('.owner.json')
    if (not path.name.endswith('.owner.json') or not identifier.startswith(PREFIXES)
            or path.is_symlink() or path.stat().st_size > 8192):
        raise ValueError('receipt path or size is invalid')
    root = path.parent
    kind = 'development_validation' if identifier.startswith('development_validation_') else 'final_holdout'
    expected = {
        'request': root / (identifier + '.json'),
        'result': root / (identifier + '.result.json'),
        'cancel': root / (identifier + '.cancel'),
    }
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict)
            or set(data) != {'version', 'kind', 'pid', 'start_token', 'request',
                             'request_sha256', 'result', 'cancel'}
            or data['version'] != 'research_operation_owner/v1' or data['kind'] != kind
            or any(data[key] != str(target) for key, target in expected.items())
            or type(data['pid']) is not int or data['pid'] <= 0
            or not isinstance(data['start_token'], str) or not data['start_token']
            or not isinstance(data['request_sha256'], str)
            or len(data['request_sha256']) != 64):
        raise ValueError('receipt identity or paths are invalid')
    request = expected['request']
    if request.is_symlink() or not request.is_file() or request.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('frozen request is missing or oversized')
    if hashlib.sha256(request.read_bytes()).hexdigest() != data['request_sha256']:
        raise ValueError('frozen request hash changed')
    return {**data, 'operation': identifier}


def inspect_receipts(state_dir: Path) -> list[dict[str, object]]:
    root = Path(state_dir).resolve()
    if not root.is_dir():
        return []
    receipts = sorted(path for path in root.glob('*.owner.json')
                      if path.name.startswith(PREFIXES))
    if len(receipts) > 1000:
        raise ValueError('research owner receipt count exceeds 1000')
    results = []
    for path in receipts:
        identifier = path.name.removesuffix('.owner.json')
        record: dict[str, object] = {'operation': identifier}
        try:
            data = load_valid_receipt(path)
            record.update(kind=data['kind'], owner_state=process_identity_state(data['pid'], data['start_token']),
                          result_exists=Path(data['result']).is_file())
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            record.update(owner_state='unknown', error=str(exc))
        results.append(record)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state_dir', type=Path)
    args = parser.parse_args(argv)
    print(json.dumps({'receipts': inspect_receipts(args.state_dir)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

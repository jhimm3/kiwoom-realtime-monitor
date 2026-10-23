"""Incremental test review; static checks never import or run the product/tests.

The audit is historical evidence. The adoption snapshot is only a mechanical
starting point, not approval of concurrent work or closure of audit findings.
"""
from __future__ import annotations

import argparse
import ast
import copy
import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'tests/test_review_baseline.json.gz'
LEDGER = 'tests/test_review_changes.jsonl'
AUDIT = 'docs/workstreams/test-case-review-20261018'
REVIEW_FIELDS = ('reason', 'requirement', 'connection', 'oracle', 'overlap')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def tree_key(node: ast.AST) -> str:
    return digest(ast.dump(node, include_attributes=False).encode())


def file_tests(relative: str, source: str) -> dict:
    """Same definition identity as the baseline, restricted to one diff file."""
    tree = ast.parse(source, filename=relative)
    tests = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith('test'):
                identity = f'{relative[:-3].replace("/", ".")}.{node.name}.{method.name}'
                if identity in tests:
                    raise ValueError(f'Duplicate test definitions: {identity}')
                tests[identity] = tree_key(method), [ast.unparse(d) for d in [*node.decorator_list, *method.decorator_list]]
    return tests


def inventory(root: Path) -> dict:
    """Definitions, fixture support and code, without unittest discovery side effects."""
    result = {'tests': {}, 'support': {}, 'products': {}, 'duplicates': []}
    for path in sorted((root / 'tests').rglob('test_*.py')):
        relative = path.relative_to(root).as_posix()
        module = relative[:-3].replace('/', '.')
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
        # Hashing only reads descendants; copy the roots whose fields change.
        support = copy.copy(tree)
        support.body = [copy.copy(node) if isinstance(node, ast.ClassDef) else node for node in tree.body]
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or not method.name.startswith('test'):
                    continue
                case_id = f'{module}.{node.name}.{method.name}'
                if case_id in result['tests']:
                    result['duplicates'].append(case_id)
                normalized = copy.copy(method)
                normalized.name = 'test_identity'
                result['tests'][case_id] = {
                    'file': relative, 'body': tree_key(method), 'identity_free_body': tree_key(normalized),
                    'decorators': [ast.unparse(d) for d in [*node.decorator_list, *method.decorator_list]],
                }
        for node in support.body:
            if isinstance(node, ast.ClassDef):
                node.body = [method for method in node.body if not (
                    isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith('test'))] or [ast.Pass()]
        result['support'][relative] = tree_key(support)
    for directory in ('src', 'scripts'):
        for path in sorted((root / directory).rglob('*.py')):
            relative = path.relative_to(root).as_posix()
            result['products'][relative] = tree_key(ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative))
    for relative in ('tests/regression_profiles.json', 'tests/ci_groups.json', 'requirements.lock.txt',
                     '.github/workflows/dependency-regression.yml'):
        path = root / relative
        if path.exists():
            result['products'][relative] = digest(path.read_bytes().replace(b'\r\n', b'\n'))
    return result


def read_json(path: Path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == '.gz' else data)


def audit_sources(root: Path) -> dict:
    names = ('cases.jsonl.gz', 'summary.json', 'unresolved.json', 'retired.json',
             'findings.json.gz', 'followup-cases.json.gz', 'followup-verification.json')
    return {f'{AUDIT}/{name}': digest((root / AUDIT / name).read_bytes()) for name in names}


def audit_sources_match(root: Path, expected: dict) -> bool:
    actual = audit_sources(root)
    if actual.keys() != expected.keys():
        return False
    for name, original in expected.items():
        allowed = {actual[name]}
        if not name.endswith('.gz'):
            lf = (root / name).read_bytes().replace(b'\r\n', b'\n')
            allowed.update((digest(lf), digest(lf.replace(b'\n', b'\r\n'))))
        if original not in allowed:
            return False
    return True


def delta(expected: dict, current: dict) -> dict:
    changes = {}
    for kind in ('tests', 'support', 'products'):
        changes[kind] = {
            name: {'before': expected[kind].get(name), 'after': current[kind].get(name)}
            for name in sorted(expected[kind].keys() | current[kind].keys())
            if expected[kind].get(name) != current[kind].get(name)
        }
    changes['counts'] = {'before': len(expected['tests']), 'after': len(current['tests']),
                         **{label: sum((item['before'] is None if label == 'added' else
                                        item['after'] is None if label == 'deleted' else
                                        item['before'] is not None and item['after'] is not None)
                                       for item in changes['tests'].values())
                            for label in ('added', 'deleted', 'modified')}}
    return changes


def load_state(root: Path) -> tuple[dict, list[dict], dict]:
    seed = read_json(root / BASELINE)
    if seed.get('version') != 1 or not audit_sources_match(root, seed.get('audit_sources', {})):
        raise ValueError('Original audit/followup evidence changed; preserve it rather than resetting the baseline')
    original = read_json(root / AUDIT / 'summary.json')
    if original['decisions'] != {'retain': 5208, 'strengthen': 216, 'fix_expectation': 19, 'retire': 12, 'unresolved': 49}:
        raise ValueError('Original audit decisions differ')
    state = copy.deepcopy(seed['inventory'])
    records = [json.loads(line) for line in (root / LEDGER).read_text(encoding='utf-8').splitlines() if line.strip()]
    seen = set()
    findings = {}
    audit_findings = json.loads(gzip.decompress((root / AUDIT / 'findings.json.gz').read_bytes()))
    known = {f"audit:{row.get('owner', 'unknown')}:{row.get('id') or row.get('case_id') or index}"
             for index, row in enumerate(audit_findings)}
    known |= {f"unresolved:{row['baseline_id']}" for row in read_json(root / AUDIT / 'unresolved.json')}
    for record in records:
        if not record.get('id') or record['id'] in seen:
            raise ValueError('Missing or duplicate incremental review ID')
        seen.add(record['id'])
        if record.get('version') != 1:
            raise ValueError('Unsupported incremental review version')
        previous_count = len(state['tests'])
        for kind in ('tests', 'support', 'products'):
            for name, item in record[kind].items():
                if item['before'] != state[kind].get(name):
                    raise ValueError(f'Stale review or abnormal identity change: {record["id"]}: {name}')
                fields = REVIEW_FIELDS
                if any(not isinstance(item.get(field), str) or not item[field].strip() for field in fields):
                    raise ValueError(f'Unreviewed {kind} change: {name}; required={fields}')
                if kind == 'products' and not item.get('tests'):
                    raise ValueError(f'Product change has no related test selection: {name}')
                if item['after'] is None:
                    state[kind].pop(name, None)
                else:
                    state[kind][name] = item['after']
        changed_modules = {name[:-3].replace('/', '.') for name in record['support']}
        changed_modules |= {item['after']['file'][:-3].replace('/', '.') if item['after'] else
                            item['before']['file'][:-3].replace('/', '.') for item in record['tests'].values()}
        counts = record.get('module_counts', {})
        if (set(counts) != changed_modules or any(type(value) is not int or value < 0 for value in counts.values())):
            raise ValueError(f'Review needs expected discovery counts for changed modules: {sorted(changed_modules)}')
        actual = {'before': previous_count, 'after': len(state['tests']),
                  **{label: sum((item['before'] is None if label == 'added' else
                                 item['after'] is None if label == 'deleted' else
                                 item['before'] is not None and item['after'] is not None)
                                for item in record['tests'].values()) for label in ('added', 'deleted', 'modified')}}
        if actual != record.get('counts'):
            raise ValueError(f'Unexpected test count change: {record["id"]}')
        removed = {key: item for key, item in record['tests'].items() if item['after'] is None}
        added = {key: item for key, item in record['tests'].items() if item['before'] is None}
        renames = record.get('renames', {})
        for old, new in renames.items():
            if old not in removed or new not in added or list(renames.values()).count(new) != 1:
                raise ValueError(f'Invalid test rename/move: {old} -> {new}')
        for old, item in removed.items():
            same_body = [name for name, new in added.items()
                         if new['after']['identity_free_body'] == item['before']['identity_free_body']]
            if same_body and old not in renames:
                raise ValueError(f'Unlinked test ID change: {old}; candidates={same_body}')
        for issue in record.get('findings', []):
            identity = issue['id']
            if identity not in known and identity not in findings and not issue.get('new'):
                raise ValueError(f'Unknown finding; link an existing audit item or explicitly mark new: {identity}')
            if issue.get('new') and (identity in known or identity in findings):
                raise ValueError(f'Finding already exists: {identity}')
            if not issue.get('reason') or not issue.get('evidence'):
                raise ValueError(f'Finding needs independent reason and evidence: {identity}')
            if issue.get('status') not in {'open', 'investigating', 'resolved', 'deferred'}:
                raise ValueError(f'Unknown finding status: {identity}')
            if issue['status'] == 'resolved' and not all(issue.get(field) for field in ('requirement', 'fix', 'tests')):
                raise ValueError(f'New test alone does not resolve finding: {identity}')
            findings[identity] = issue
    return state, records, {'audit_finding_keys': sorted(known), 'explicit_updates': findings}


def make_plan(root: Path) -> dict:
    expected, _, _ = load_state(root)
    current = inventory(root)
    if current['duplicates']:
        raise ValueError(f'Duplicate test definitions: {current["duplicates"]}')
    result = {'version': 1, 'id': '', **delta(expected, current), 'renames': {}, 'findings': []}
    changed_files = set(result['support']) | {item['after']['file'] if item['after'] else item['before']['file']
                                            for item in result['tests'].values()}
    result['module_counts'] = {name[:-3].replace('/', '.'): None for name in sorted(changed_files)}
    from scripts.select_ci_groups import related_test_modules
    result['related_module_suggestions'] = related_test_modules(sorted(changed_files | set(result['products'])), root)
    for kind in ('tests', 'support', 'products'):
        for item in result[kind].values():
            item.update({field: '' for field in REVIEW_FIELDS})
            if kind == 'products':
                item['tests'] = []
            elif kind == 'support':
                item['tests'] = []
    return result


def changed_sources(root: Path, base_ref: str, filters: list[str]) -> tuple[list[str], dict]:
    """Include staged, unstaged and untracked files; deleted imports retain before evidence."""
    if not base_ref or set(base_ref) == {'0'}:
        base_ref = 'HEAD'
    subprocess.check_output(['git', 'rev-parse', '--verify', f'{base_ref}^{{commit}}'], cwd=root, stderr=subprocess.PIPE)
    names = subprocess.check_output(['git', 'diff', '--name-only', '-z', '--no-renames', '--no-ext-diff', base_ref], cwd=root, stderr=subprocess.PIPE)
    names += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=root, stderr=subprocess.PIPE)
    paths = sorted({value.decode('utf-8') for value in names.split(b'\0') if value})
    if filters:
        for prefix in filters:
            if prefix.startswith('/') or '..' in Path(prefix).parts or ':' in prefix:
                raise ValueError('Selection paths must stay repository-relative')
        paths = [path for path in paths if any(path == prefix or path.startswith(prefix.rstrip('/') + '/') for prefix in filters)]
        if not paths:
            raise ValueError('No changed files match the requested scope')
    before = {}
    for path in paths:
        if path.endswith('.py'):
            result = subprocess.run(['git', 'show', f'{base_ref}:{path}'], cwd=root, capture_output=True)
            if result.returncode == 0:
                before[path] = result.stdout.decode('utf-8-sig')
    return paths, before


def _audit_query_cache(root: Path):
    """Disposable indexed read cache, not a second ledger; originals are opened once per change."""
    sources = [root / AUDIT / name for name in ('cases.jsonl.gz', 'findings.json.gz', 'followup-cases.json.gz')]
    stamp = json.dumps([(str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
                        for path in sources if path.exists()])
    cache = root / 'tmp/test-review-query.sqlite3'
    cache.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(cache, timeout=30)
    try:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(stamp TEXT);
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, case_id TEXT, baseline_id TEXT,
                module TEXT, file TEXT, kind TEXT, document TEXT);
            CREATE TABLE IF NOT EXISTS refs(id TEXT, file TEXT);
            CREATE INDEX IF NOT EXISTS record_module ON records(module);
            CREATE INDEX IF NOT EXISTS record_file ON records(file);
            CREATE INDEX IF NOT EXISTS record_case ON records(case_id);
            CREATE INDEX IF NOT EXISTS record_baseline ON records(baseline_id);
            CREATE INDEX IF NOT EXISTS ref_file ON refs(file);
        ''')
        if connection.execute('SELECT stamp FROM metadata').fetchone() == (stamp,):
            return connection
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT stamp FROM metadata').fetchone() != (stamp,):
            connection.execute('DELETE FROM records')
            connection.execute('DELETE FROM refs')
            def insert(identity, row, kind):
                case = row.get('case_id') or row.get('baseline_id') or ''
                connection.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?)',
                    (identity, case, row.get('baseline_id'), case.rsplit('.', 2)[0], row.get('file'), kind,
                     json.dumps(row, ensure_ascii=False)))
                evidence = row.get('production_evidence', [])
                if isinstance(evidence, str):
                    evidence = [evidence]
                for item in evidence:
                    file = item.split(':', 1)[0] if isinstance(item, str) else item.get('file') if isinstance(item, dict) else None
                    if file:
                        connection.execute('INSERT INTO refs VALUES(?,?)', (identity, file))
            if sources[0].exists():
                with gzip.open(sources[0], 'rt', encoding='utf-8') as stream:
                    for line in stream:
                        row = json.loads(line)
                        if row.get('decision') in {'strengthen', 'unresolved', 'fix_expectation'}:
                            key = f"unresolved:{row['baseline_id']}" if row['decision'] == 'unresolved' else f"audit:{row.get('review_owner', 'unknown')}:{row['case_id']}"
                            insert(key, row, 'audit')
            if sources[1].exists():
                for index, row in enumerate(read_json(sources[1])):
                    insert(f"audit:{row.get('owner', 'unknown')}:{row.get('id') or row.get('case_id') or index}", row, 'audit')
            if sources[2].exists():
                for row in read_json(sources[2]):
                    insert('followup:' + row['baseline_id'], row, 'followup')
            connection.execute('DELETE FROM metadata')
            connection.execute('INSERT INTO metadata VALUES(?)', (stamp,))
        connection.commit()
        return connection
    except BaseException:
        connection.close()
        raise


def related_audit(root: Path, paths: list[str], modules: list[str], changes: dict) -> list[dict]:
    """Query indexed relevant gaps/strengthening/defects only; never re-review historical meaning."""
    identities = set().union(*(set(values) for values in changes.values()))
    updates, renames = {}, {}
    ledger = root / LEDGER
    if ledger.exists():
        for line in ledger.read_text(encoding='utf-8').splitlines():
            if line.strip():
                record = json.loads(line)
                renames.update(record.get('renames', {}))
                updates.update({issue['id']: issue for issue in record.get('findings', [])})
    for old, new in reversed(list(renames.items())):
        if new in identities or any(new.startswith(module + '.') for module in modules):
            identities.add(old)
    matches = {}
    connection = _audit_query_cache(root)
    try:
        def select(field, values, followup=False):
            values = sorted(set(values))
            for offset in range(0, len(values), 200):
                block = values[offset:offset + 200]
                condition = f'{field} IN ({",".join("?" for _ in block)})'
                if field == 'refs.file':
                    condition = f'id IN (SELECT id FROM refs WHERE {condition})'
                yield from connection.execute(f'SELECT id, document FROM records WHERE kind=? AND {condition}',
                                              ['followup' if followup else 'audit', *block])
        for field, values in [('case_id', identities), ('module', modules), ('file', paths), ('refs.file', paths)]:
            for key, document in select(field, values):
                matches[key] = {'id': key, 'original': json.loads(document), 'explicit_update': updates.get(key)}
        baseline_ids = {row['original'].get('baseline_id') or row['original'].get('case_id') for row in matches.values()}
        followups = {json.loads(document)['baseline_id']: json.loads(document)
                     for _, document in select('baseline_id', baseline_ids - {None}, followup=True)}
        for match in matches.values():
            followup = followups.get(match['original'].get('baseline_id') or match['original'].get('case_id'))
            if followup:
                match['followup'] = followup
    finally:
        connection.close()
    for key, issue in updates.items():
        if key not in matches and any(identity in identities or any(identity.startswith(module + '.') for module in modules)
                                      for identity in issue.get('tests', [])):
            matches[key] = {'id': key, 'explicit_update': issue}
    return list(matches.values())


def query_changes(root: Path, base_ref: str, filters: list[str]) -> dict:
    from scripts import run_regression, select_ci_groups
    filters = [value.replace('\\', '/') for value in filters]
    paths, before = changed_sources(root, base_ref, filters)
    changes = {'added': [], 'modified': [], 'deleted': []}
    for path in paths:
        if not path.startswith('tests/') or not Path(path).name.startswith('test_') or not path.endswith('.py'):
            continue
        old = file_tests(path, before.get(path, ''))
        file = root / path
        current = file_tests(path, file.read_text(encoding='utf-8-sig')) if file.exists() else {}
        changes['added'].extend(sorted(current.keys() - old.keys()))
        changes['deleted'].extend(sorted(old.keys() - current.keys()))
        changes['modified'].extend(sorted(key for key in old.keys() & current.keys() if old[key] != current[key]))
    impact = select_ci_groups.test_impact(paths, root, before)
    return {'version': 1, 'kind': 'incremental_test_selection', 'base_ref': base_ref or 'HEAD',
            'scope_filters': filters, 'changed_paths': paths, 'changed_tests': changes, **impact,
            'source': run_regression._code_tree_identity(),
            'audit_items': related_audit(root, paths, impact['selected_modules'], changes),
            'note': 'Static selection is not semantic approval; full_required is never executed automatically.'}


def query_summary(result: dict, limit: int = 20, offset: int = 0) -> dict:
    def preview(item):
        return {**{key: value[:3] if isinstance(value, list) else value for key, value in item.items()},
                **{f'more_{key}': len(value) - 3 for key, value in item.items() if isinstance(value, list) and len(value) > 3}}
    details = ([{'uncertainty': preview(item)} for item in result['uncertainties']]
               + [{'change': kind, 'test': identity} for kind, ids in result['changed_tests'].items() for identity in ids]
               + [{'module': module, 'reasons': result['reasons'][module][:2],
                   'more_reasons': max(0, len(result['reasons'][module]) - 2)} for module in result['selected_modules']]
               + [{'audit_id': row['id'], 'decision': row.get('original', {}).get('decision'),
                   'gap': row.get('original', {}).get('gap'), 'update': row.get('explicit_update'),
                   'followup': {key: row.get('followup', {}).get(key) for key in ('status', 'phase', 'current_related_execution')}}
                  for row in result['audit_items']])
    return {'status': result['status'], 'changed_paths': len(result['changed_paths']),
            'changed_tests': {key: len(values) for key, values in result['changed_tests'].items()},
            'selected_modules': len(result['selected_modules']), 'related_audit_items': len(result['audit_items']),
            'uncertainties': len(result['uncertainties']), 'external_modules': len(result['external_modules']),
            'scope_filters': result['scope_filters'], 'details': details[offset:offset + limit],
            'more_details': max(0, len(details) - offset - limit), 'source': result['source']['lf_sha256']}


def execution_ids(root: Path, paths: list[Path], *, validated_reports: list[dict] | None = None) -> set[str]:
    """Read existing runner evidence, never invoke a test runner here."""
    from scripts.run_regression import _code_tree_identity
    current_source = _code_tree_identity()['lf_sha256']
    covered = set()
    for path in paths:
        raw_report = report = read_json(path)
        if 'source_start' in report:  # Existing Linux/PG report, same identity vocabulary.
            if not report.get('planned') or report.get('planned') != report.get('ran'):
                raise ValueError(f'Platform discovery/execution mismatch: {path}')
            if any(report.get(key) != 0 for key in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes')):
                raise ValueError(f'Platform report did not pass: {path}')
            report = {'status': report.get('status'), 'code_tree_start': report['source_start'],
                      'code_tree_end': report['source_end'], 'batch_results': [{
                          'status': report.get('status'), 'worker_exit_confirmed': True,
                          'process_tree_exit_confirmed': True, 'test_count': report.get('ran'),
                          'planned_test_ids': report.get('planned_test_ids'),
                          'executed_test_ids': report.get('executed_test_ids')}]}
        if (report.get('status') != 'passed' or report.get('source_modified_during_run')
                or report.get('code_tree_end', {}).get('lf_sha256') != current_source
                or report.get('code_tree_start', {}).get('lf_sha256') != current_source
                or report.get('unrun_batches')):
            raise ValueError(f'Stale/incomplete execution evidence: {path}')
        all_ids = []
        for worker in report['batch_results']:
            if (worker.get('status') != 'passed' or not worker.get('worker_exit_confirmed')
                    or not worker.get('process_tree_exit_confirmed') or any(worker.get(key) for key in (
                    'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))):
                raise ValueError(f'Worker did not pass: {path}')
            ids = worker.get('executed_test_ids', [])
            if not isinstance(ids, list) or len(ids) != worker['test_count'] or ids != worker.get('planned_test_ids'):
                raise ValueError(f'Missing/mismatched execution IDs: {path}')
            all_ids.extend(ids)
        duplicates = [name for name, count in Counter(all_ids).items() if count > 1]
        if duplicates:
            raise ValueError(f'Duplicate executed test IDs: {duplicates}')
        covered.update(all_ids)
        if validated_reports is not None:
            validated_reports.append(raw_report)
    return covered


def validate_history(root: Path, base_ref: str) -> str:
    if not base_ref or set(base_ref) == {'0'}:
        base_ref = 'HEAD'
    subprocess.check_output(['git', 'rev-parse', '--verify', f'{base_ref}^{{commit}}'], cwd=root, stderr=subprocess.PIPE)
    for name in (BASELINE, LEDGER):
        previous = subprocess.run(['git', 'show', f'{base_ref}:{name}'], cwd=root, capture_output=True)
        if previous.returncode:
            continue  # Explicit initial adoption; its prior-work limit is reported.
        current = (root / name).read_bytes().replace(b'\r\n', b'\n')
        old = previous.stdout.replace(b'\r\n', b'\n')
        if (name == BASELINE and current != old) or (name == LEDGER and not current.startswith(old)):
            raise ValueError(f'Baseline reset or rewritten review history: {name}')
    return base_ref


def check(root: Path, *, executions: list[Path] | None = None, base_ref: str = '') -> dict:
    if base_ref:
        base_ref = validate_history(root, base_ref)
    expected, records, findings = load_state(root)
    current = inventory(root)
    difference = delta(expected, current)
    if current['duplicates']:
        raise ValueError(f'Duplicate test definitions: {current["duplicates"]}')
    if any(difference[kind] for kind in ('tests', 'support', 'products')):
        raise ValueError('Unreviewed changes; run --plan and review tests/fixtures/products: ' +
                         json.dumps({kind: list(difference[kind]) for kind in ('tests', 'support', 'products')}))
    # Reuse the existing module/CI routing validator rather than a second catalog.
    from scripts import run_regression
    errors = run_regression._catalog_errors(run_regression._load_manifest(), run_regression._load_ci_groups(),
                                           run_regression._test_modules_under('unit'), run_regression._test_modules_under('integration'))
    if errors:
        raise ValueError('; '.join(errors))
    code_reviews = {path: item for record in records for path, item in record['products'].items()}
    selected = {}
    if executions is not None:
        if not base_ref:
            raise ValueError('Execution checks require --base-ref (comparison only; tests are never launched)')
        # The initial adoption includes already-dirty work. Only changes after the
        # mechanical adoption anchor are incremental, never retroactive approval.
        names = subprocess.check_output(['git', 'diff', '--name-only', base_ref], cwd=root, text=True).splitlines()
        names += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=root, text=True).splitlines()
        validated_reports = []
        covered = execution_ids(root, executions, validated_reports=validated_reports)
        for name in names:
            if name in code_reviews:
                selected[name] = code_reviews[name]['tests']
                missing = set(selected[name]) - covered
                if missing:
                    raise ValueError(f'Related tests not executed for {name}: {sorted(missing)}')
        test_reviews = {identity: item for record in records for identity, item in record['tests'].items()}
        support_reviews = {name: item for record in records for name, item in record['support'].items()}
        for identity, item in test_reviews.items():
            if item['after'] and item['after']['file'] in names:
                missing = set(item.get('executed_as', [identity])) - covered
                if missing:
                    raise ValueError(f'Changed tests not executed: {sorted(missing)}')
        for name, item in support_reviews.items():
            if name in names and item['after'] is not None and (not item.get('tests') or set(item['tests']) - covered):
                raise ValueError(f'Fixture change has no executed consumers: {name}')
        previous = subprocess.run(['git', 'show', f'{base_ref}:{LEDGER}'], cwd=root, capture_output=True)
        old_count = len([line for line in previous.stdout.splitlines() if line.strip()]) if previous.returncode == 0 else 0
        for record in records[old_count:]:
            for issue in record.get('findings', []):
                if issue['status'] == 'resolved' and set(issue['tests']) - covered:
                    raise ValueError(f'Finding resolution tests not executed: {issue["id"]}')
        observed = {}
        for report in validated_reports:
            observed.update(report.get('discovered', {}))
            for row in report.get('module_test_counts', []):
                observed[row['module']] = row['test_count']
            for worker in report.get('batch_results', []):
                for row in worker.get('module_test_counts', []):
                    observed[row['module']] = row['test_count']
        expected_counts = {module: count for record in records for module, count in record.get('module_counts', {}).items()}
        for module, count in expected_counts.items():
            required = module in observed or (module.replace('.', '/') + '.py' in names and count > 0)
            if required and observed.get(module) != count:
                raise ValueError(f'Unexpected runtime discovery count: {module}: expected {count}, observed {observed.get(module)}')
    return {'status': 'passed', 'static_case_definitions': len(current['tests']), 'records': len(records),
            'execution_checked': executions is not None, 'related_product_tests': selected,
            'audit_decisions_preserved': True, 'original_unresolved': 49,
            'finding_updates': findings['explicit_updates'], 'scope': 'incremental; no semantic auto-approval'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, help='write a review draft; does not approve changes')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--findings', action='store_true', help='list existing audit/gap identities before adding an issue')
    parser.add_argument('--execution', action='append', type=Path, default=None)
    parser.add_argument('--base-ref', default='')
    parser.add_argument('--query', action='store_true', help='query diff/impact/relevant audit only; never execute')
    parser.add_argument('--path', action='append', default=[], help='explicit changed-file/directory scope; recorded in report')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--offset', type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.limit < 1 or args.offset < 0:
            raise ValueError('Positive limit and nonnegative offset required')
        if args.query:
            result = query_changes(ROOT, args.base_ref or 'HEAD', args.path)
        elif args.path:
            raise ValueError('--path requires --query')
        elif args.findings:
            _, _, result = load_state(ROOT)
        elif args.plan:
            result = make_plan(ROOT)
            if args.plan.exists():
                raise ValueError('Review draft exists; preserve it or choose another path')
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        else:
            result = check(ROOT, executions=args.execution, base_ref=args.base_ref)
    except (ValueError, OSError, KeyError, SyntaxError, sqlite3.Error, subprocess.CalledProcessError) as error:
        result = {'status': 'failed', 'reason': str(error)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(query_summary(result, args.limit, args.offset) if args.query and 'changed_tests' in result else result, ensure_ascii=False))
    return 2 if result.get('status') == 'full_required' else int(result.get('status') == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())

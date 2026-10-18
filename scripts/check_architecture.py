"""Static architecture delta guard; never imports product code or opens a DB.

Counts are source call sites, not runtime costs. Existing debt is frozen, not
approved. Runtime cost/lifecycle contracts remain in the existing regressions.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from scripts.select_ci_groups import _dependency_graph

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'docs/architecture/guardrails.json'
REVIEW_FIELDS = ('reason', 'connection', 'validation')
DB_METHODS = {'execute', 'executemany', 'executescript', 'commit', 'rollback'}
DRIVERS = {'sqlite3.connect', 'psycopg.connect', 'psycopg2.connect'}


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def in_package(module, prefix):
    package = prefix.rstrip('.')
    return module == package or module.startswith(package + '.')


def cycle_edges(graph):
    """Tarjan components; compare edges, so enlarging an old cycle also fails."""
    indices, low, stack, active, components = {}, {}, [], set(), []

    def visit(node):
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            group = set()
            while True:
                target = stack.pop()
                active.remove(target)
                group.add(target)
                if target == node:
                    break
            components.append(group)

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return {(source, target) for group in components if len(group) > 1
            for source in group for target in graph[source] if target in group}


def inspect(root, rules):
    dependencies, _, files = _dependency_graph(root)
    modules = {module for module, file in files.items() if file.startswith('src/kiwoom_monitor/')}
    graph = {module: dependencies[module] & modules - {module} for module in modules}
    counts, evidence, watched = Counter(), {}, set()

    def record(key, file, line):
        counts[key] += 1
        evidence.setdefault(key, {'file': file, 'line': line})

    hot = rules['ram_paths']
    for module in sorted(modules):
        relative = files[module]
        tree = ast.parse((root / relative).read_text(encoding='utf-8-sig'), filename=relative)
        aliases = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                aliases.update({alias.asname or alias.name.split('.')[0]: alias.name if alias.asname else alias.name.split('.')[0] for alias in node.names})
            elif isinstance(node, ast.ImportFrom) and not node.level:
                aliases.update({alias.asname or alias.name: f'{node.module}.{alias.name}' for alias in node.names})

        class Calls(ast.NodeVisitor):
            def __init__(self):
                self.scope = []
                self.aliases = dict(aliases)

            def visit_Import(self, node):
                self.aliases.update({alias.asname or alias.name.split('.')[0]: alias.name if alias.asname else alias.name.split('.')[0] for alias in node.names})

            def visit_ImportFrom(self, node):
                if not node.level:
                    self.aliases.update({alias.asname or alias.name: f'{node.module}.{alias.name}' for alias in node.names})

            def visit_ClassDef(self, node):
                self.enter(node)

            def visit_FunctionDef(self, node):
                self.enter(node)

            visit_AsyncFunctionDef = visit_FunctionDef

            def enter(self, node):
                outer_aliases = self.aliases
                self.aliases = dict(outer_aliases)
                self.scope.append(node.name)
                if f'{relative}|{".".join(self.scope)}' in hot:
                    watched.add(f'{relative}|{".".join(self.scope)}')
                self.generic_visit(node)
                self.scope.pop()
                self.aliases = outer_aliases

            def visit_Call(self, node):
                scope = '.'.join(self.scope) or '<module>'
                call = ast.unparse(node.func)
                first, dot, rest = call.partition('.')
                resolved = self.aliases.get(first, first) + (dot + rest if dot else '')
                method = node.func.attr if isinstance(node.func, ast.Attribute) else call
                if resolved in DRIVERS or method in DB_METHODS:
                    record(f'db|{relative}|{scope}|{resolved}', relative, node.lineno)
                if method in {'import_module', '__import__', 'exec'} or resolved in {'importlib.import_module', 'builtins.__import__', 'builtins.exec'}:
                    if method == 'exec' or resolved == 'builtins.exec' or not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                        record(f'dynamic|{relative}|{scope}|{resolved}', relative, node.lineno)
                    else:
                        # Supplement the impact graph's name-only literal lookup
                        # with resolved aliases before checking boundaries/cycles.
                        dependencies[module].add(node.args[0].value)
                ram_scope = any(f'{relative}|{".".join(self.scope[:n])}' in hot
                                for n in range(1, len(self.scope) + 1))
                if ram_scope and (resolved in DRIVERS or method in DB_METHODS or
                                  method.startswith(('load_', 'save_', 'upsert_', 'fetch_')) or
                                  method in {'open', 'read_text', 'read_bytes', 'write_text', 'write_bytes', 'sleep'} or
                                  resolved.startswith(('requests.', 'httpx.', 'urllib.request.'))):
                    record(f'ram_io|{relative}|{scope}|{resolved}', relative, node.lineno)
                self.generic_visit(node)

        Calls().visit(tree)
    graph = {module: dependencies[module] & modules - {module} for module in modules}
    for source in sorted(modules):
        for target in sorted(dependencies[source] & modules):
            if any(in_package(source, rule['source']) and any(in_package(target, prefix) for prefix in rule['forbidden'])
                   for rule in rules['dependency_rules']):
                record(f'boundary|{source}|{target}', files[source], 1)
    for source, target in sorted(cycle_edges(graph)):
        record(f'cycle|{source}|{target}', files[source], 1)
    missing = sorted(set(hot) - watched)
    return dict(sorted(counts.items())), evidence, missing


def check(root, contract, previous=None):
    errors = []
    if contract.get('version') != 1 or contract.get('adoption_status') != 'mechanical_inventory_not_debt_approval':
        raise ValueError('Unknown contract version/adoption status')
    limits = dict(contract['adopted_limits'])
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in limits.values()):
        raise ValueError('Invalid adopted source-site limit')
    reviews = contract['reviews']
    if previous is not None:
        for key in ('version', 'adoption_status', 'adopted_limits'):
            if contract[key] != previous[key]:
                errors.append({'kind': 'adoption_rewritten', 'field': key})
        if reviews[:len(previous['reviews'])] != previous['reviews']:
            errors.append({'kind': 'review_history_rewritten'})
        if contract['rules'] != previous['rules']:
            wanted = {'from': canonical_digest(previous['rules']), 'to': canonical_digest(contract['rules'])}
            if not any(r.get('rules_change') == wanted and all(r.get(k) for k in REVIEW_FIELDS)
                       for r in reviews[len(previous['reviews']):]):
                errors.append({'kind': 'rules_change_without_review', **wanted})
    seen = set()
    for review in reviews:
        identity = review.get('id')
        if not identity or identity in seen or not all(isinstance(review.get(k), str) and review[k].strip() for k in REVIEW_FIELDS):
            errors.append({'kind': 'invalid_review', 'id': identity})
            continue
        seen.add(identity)
        for key, limit in review.get('limits', {}).items():
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                errors.append({'kind': 'invalid_limit', 'key': key})
            else:
                limits[key] = limit
    current, evidence, missing = inspect(root, contract['rules'])
    errors.extend({'kind': 'missing_ram_path', 'path': path} for path in missing)
    errors.extend({'kind': key.split('|')[0], 'key': key, 'actual': count,
                   'limit': limits.get(key, 0), **evidence[key]}
                  for key, count in current.items() if count > limits.get(key, 0))
    # Removed operations must be ratcheted to zero in a later R3 review; show only
    # their number, rather than dumping every unchanged accepted site.
    return {'status': 'fail' if errors else 'pass', 'violations': errors,
            'source_site_keys': len(current), 'retirable_limits': sum(current.get(k, 0) < v for k, v in limits.items()),
            'scope': 'static import/call-site deltas; not runtime I/O counts or full connection acceptance'}


def previous_contract(root, ref):
    if not ref:
        return None
    valid = subprocess.run(['git', 'rev-parse', '--verify', f'{ref}^{{commit}}'], cwd=root, capture_output=True)
    if valid.returncode:
        raise ValueError(f'Invalid comparison commit: {ref}')
    present = subprocess.run(['git', 'ls-tree', '--name-only', ref, '--', CONTRACT], cwd=root, capture_output=True, text=True, check=True)
    if not present.stdout.strip():
        return None  # first adoption, never a silent retry after git/show failure
    content = subprocess.run(['git', 'show', f'{ref}:{CONTRACT}'], cwd=root, capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(content.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-ref', help='CI comparison commit; protects adoption and review history')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    try:
        contract = json.loads((ROOT / CONTRACT).read_text(encoding='utf-8'))
        report = check(ROOT, contract, previous_contract(ROOT, args.base_ref))
    except (ValueError, OSError, SyntaxError, subprocess.CalledProcessError) as error:
        report = {'status': 'error', 'error': str(error), 'violations': []}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({**report, 'violations': report['violations'][:8]}, ensure_ascii=False))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Mutation fixtures for the structural guard; product regressions stay separate."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from scripts.check_architecture import check, canonical_digest, inspect


class ArchitectureGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.rules = {
            'dependency_rules': [{'source': 'kiwoom_monitor.domain.',
                                  'forbidden': ['kiwoom_monitor.infrastructure.']}],
            'ram_paths': [],
        }

    def source(self, relative, value):
        file = self.root / 'src/kiwoom_monitor' / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value, encoding='utf-8')

    def contract(self):
        current, _, missing = inspect(self.root, self.rules)
        self.assertFalse(missing)
        return {'version': 1, 'adoption_status': 'mechanical_inventory_not_debt_approval',
                'rules': copy.deepcopy(self.rules), 'adopted_limits': current, 'reviews': []}

    def test_allowed_dependency_and_comment_only_change_pass(self):
        self.source('domain/model.py', 'VALUE = 1\n')
        self.source('application/logic.py', 'from ..domain.model import VALUE\n')
        contract = self.contract()
        self.source('application/logic.py', '# explanatory comment\nfrom ..domain.model import VALUE\n')
        self.assertEqual('pass', check(self.root, contract)['status'])

    def test_relative_boundary_bypass_is_rejected(self):
        self.source('infrastructure/store.py', 'VALUE = 1\n')
        self.source('domain/model.py', 'VALUE = 1\n')
        contract = self.contract()
        self.source('domain/model.py', 'from ..infrastructure.store import VALUE\n')
        report = check(self.root, contract)
        self.assertEqual(['boundary'], [v['kind'] for v in report['violations']])

    def test_new_cycle_and_edge_inside_existing_component_are_rejected(self):
        self.source('application/a.py', 'from . import b\n')
        self.source('application/b.py', 'from . import c\n')
        self.source('application/c.py', 'from . import a\n')
        contract = self.contract()
        self.assertEqual('pass', check(self.root, contract)['status'])
        self.source('application/a.py', 'from . import b, c\n')
        report = check(self.root, contract)
        self.assertEqual(1, len(report['violations']))
        self.assertEqual('cycle', report['violations'][0]['kind'])

    def test_alias_driver_connection_and_extra_commit_are_rejected(self):
        self.source('infrastructure/store.py', 'def save(c):\n c.commit()\n')
        contract = self.contract()
        self.source('infrastructure/store.py', 'from sqlite3 import connect as database\ndef save(c):\n database(":memory:")\n c.commit()\n c.commit()\n')
        report = check(self.root, contract)
        self.assertEqual(2, len(report['violations']))
        self.assertEqual({2, 1}, {v['actual'] for v in report['violations']})
        self.assertEqual({'db'}, {v['kind'] for v in report['violations']})

    def test_ram_read_candidate_and_nested_blocking_io_are_rejected(self):
        self.source('application/hot.py', 'def consume(store):\n return ()\n')
        self.rules['ram_paths'] = ['src/kiwoom_monitor/application/hot.py|consume']
        contract = self.contract()
        self.source('application/hot.py', 'def consume(store):\n def work():\n  return open("file")\n return store.load_quotes(), work()\n')
        report = check(self.root, contract)
        self.assertEqual(2, len(report['violations']))
        self.assertEqual({'ram_io'}, {v['kind'] for v in report['violations']})

    def test_watched_function_move_cannot_silently_disable_guard(self):
        self.source('application/hot.py', 'def consume(): return 1\n')
        self.rules['ram_paths'] = ['src/kiwoom_monitor/application/hot.py|consume']
        contract = self.contract()
        self.source('application/hot.py', 'def moved(): return 1\n')
        self.assertEqual('missing_ram_path', check(self.root, contract)['violations'][0]['kind'])

    def test_nonliteral_dynamic_import_alias_requires_review(self):
        self.source('application/logic.py', 'def run(name): return name\n')
        contract = self.contract()
        self.source('application/logic.py', 'from importlib import import_module as load\ndef run(name): return load(name)\n')
        self.assertEqual('dynamic', check(self.root, contract)['violations'][0]['kind'])

    def test_package_initializer_and_literal_import_alias_cannot_bypass_boundary(self):
        self.source('infrastructure/__init__.py', 'VALUE = 1\n')
        self.source('infrastructure/store.py', 'VALUE = 1\n')
        self.source('domain/__init__.py', '')
        contract = self.contract()
        self.source('domain/__init__.py', 'import kiwoom_monitor.infrastructure\n')
        self.assertEqual('boundary', check(self.root, contract)['violations'][0]['kind'])
        self.source('domain/__init__.py', 'from importlib import import_module as load\nload("kiwoom_monitor.infrastructure.store")\n')
        report = check(self.root, contract)
        self.assertEqual('boundary', report['violations'][0]['kind'])

    def test_nested_alias_and_dotted_import_do_not_hide_driver_connection(self):
        self.source('infrastructure/store.py', '')
        contract = self.contract()
        self.source('infrastructure/store.py',
                    'import sqlite3\nimport sqlite3.dbapi2\nfrom sqlite3 import connect as db\n'
                    'def unrelated():\n from other import connect as db\n return db()\n'
                    'def save():\n sqlite3.connect(":memory:")\n db(":memory:")\n')
        report = check(self.root, contract)
        self.assertEqual(1, len(report['violations']))
        self.assertEqual('db', report['violations'][0]['kind'])
        self.assertEqual(2, report['violations'][0]['actual'])

    def test_justified_cost_increase_and_retirement_use_exact_reviewed_limits(self):
        self.source('infrastructure/store.py', 'def save(c):\n c.commit()\n')
        contract = self.contract()
        old = copy.deepcopy(contract)
        key = next(iter(contract['adopted_limits']))
        contract['reviews'].append({'id': 'necessary-two-phase', 'reason': 'two durable phases',
                                    'connection': 'writer to reader and retry', 'validation': 'native rollback/ack regression',
                                    'limits': {key: 2}})
        self.source('infrastructure/store.py', 'def save(c):\n c.commit()\n c.commit()\n')
        self.assertEqual('pass', check(self.root, contract, old)['status'])
        contract['reviews'].append({'id': 'retired-path', 'reason': 'R3 removes this writer',
                                    'connection': 'consumer uses existing owner', 'validation': 'readback and shutdown regression',
                                    'limits': {key: 0}})
        self.assertEqual('fail', check(self.root, contract, old)['status'])
        self.source('infrastructure/store.py', 'def save(c): return None\n')
        self.assertEqual('pass', check(self.root, contract, old)['status'])

    def test_adoption_or_review_history_rewrite_and_empty_review_fail(self):
        self.source('infrastructure/store.py', 'def save(c): c.commit()\n')
        old = self.contract()
        contract = copy.deepcopy(old)
        contract['adopted_limits'] = {}
        self.assertEqual('adoption_rewritten', check(self.root, contract, old)['violations'][0]['kind'])
        contract = copy.deepcopy(old)
        contract['reviews'] = [{'id': 'vague', 'reason': 'ok', 'connection': '', 'validation': ''}]
        self.assertEqual('invalid_review', check(self.root, contract, old)['violations'][0]['kind'])
        old = copy.deepcopy(contract)
        contract['reviews'] = []
        self.assertEqual('review_history_rewritten', check(self.root, contract, old)['violations'][0]['kind'])

    def test_justified_rules_change_uses_exact_before_after_identity(self):
        self.source('domain/model.py', 'VALUE = 1\n')
        old = self.contract()
        contract = copy.deepcopy(old)
        contract['rules']['dependency_rules'] = []
        self.assertEqual('rules_change_without_review', check(self.root, contract, old)['violations'][0]['kind'])
        contract['reviews'].append({'id': 'boundary-moved', 'reason': 'approved new responsibility',
                                   'connection': 'entry through new owner, old route removed',
                                   'validation': 'integration lifecycle regression',
                                   'rules_change': {'from': canonical_digest(old['rules']), 'to': canonical_digest(contract['rules'])}})
        self.assertEqual('pass', check(self.root, contract, old)['status'])

    def test_parse_error_is_not_a_clean_inventory(self):
        self.source('domain/model.py', 'VALUE = 1\n')
        contract = self.contract()
        self.source('domain/model.py', 'def invalid(:\n')
        with self.assertRaises(SyntaxError):
            check(self.root, contract)


if __name__ == '__main__':
    unittest.main()

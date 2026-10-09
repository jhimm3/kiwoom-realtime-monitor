from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.audit_query_store_consumers import (
    _site_identity,
    _forward_identity,
    compare_identities,
    inventory,
    main,
)


ROOT = Path(__file__).resolve().parents[2]


class QueryStoreConsumerAuditTests(unittest.TestCase):
    def test_repository_connections_match_reviewed_baseline(self) -> None:
        result = inventory(ROOT)

        self.assertEqual("pass", result["status"])
        self.assertEqual(0, result["counts"]["parse_errors"])
        self.assertEqual([], result["reviewed_site_delta"]["added"])
        self.assertEqual([], result["reviewed_site_delta"]["removed"])
        self.assertEqual([], result["all_candidate_delta"]["added"])
        self.assertEqual([], result["all_candidate_delta"]["removed"])
        self.assertGreater(result["counts"]["reviewed_store_sites"], 0)
        self.assertGreater(result["counts"]["forwarding_edges"], 0)
        self.assertEqual(4, result["counts"]["store_internal_delegate_sites"])
        # Similar method names are retained for review rather than asserted as DB calls.
        self.assertGreater(result["counts"]["unresolved_same_name_candidates"], 0)
        self.assertTrue(all(
            site["status"] == "reviewed_store_binding"
            for site in result["reviewed_store_sites"]
        ))

    def test_owned_thread_dispatch_requires_its_import_and_respects_shadowing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            central = root / "src/kiwoom_monitor/central_server"
            central.mkdir(parents=True)
            (central / "database.py").write_text(
                "class QueryStore:\n def load_documents(self, key): ...\n"
                "class SQLiteQueryStore(QueryStore): pass\n"
                "class PostgresQueryStore(QueryStore): pass\n", encoding="utf-8")
            (central / "app.py").write_text(
                "from .diagnostic_replay_runtime import owned_to_thread as dispatch\n"
                "from . import diagnostic_replay_runtime as runtime\n"
                "from other import owned_to_thread\n"
                "from .diagnostic_replay_runtime import owned_to_thread as rebound\n"
                "from other import owned_to_thread as rebound\n"
                "async def alias(): await dispatch(store.load_documents, 'one')\n"
                "async def module_alias(): await runtime.owned_to_thread(store.load_documents, 'two')\n"
                "async def unrelated(): await owned_to_thread(store.load_documents, 'three')\n"
                "async def parameter_shadow(dispatch): await dispatch(store.load_documents, 'four')\n"
                "async def local_shadow():\n dispatch = other\n await dispatch(store.load_documents, 'five')\n"
                "async def import_shadow(): await rebound(store.load_documents, 'six')\n"
                "def helper(store): return store.load_documents('helper')\n"
                "async def forward(): await dispatch(helper, store=store)\n", encoding="utf-8")
            approvals = root / "approvals.json"
            approvals.write_text(json.dumps({"reviewed_bindings": [{
                "id": "forward", "file": "src/kiwoom_monitor/central_server/app.py",
                "owner": "forward", "receiver": "store", "backend_scope": "fixture",
            }]}), encoding="utf-8")
            result = inventory(root, approvals)
            sites = {row["owner"]: row for row in result["unresolved_same_name_candidates"]}
            canonical = "kiwoom_monitor.central_server.diagnostic_replay_runtime.owned_to_thread"
            for owner in ("alias", "module_alias"):
                with self.subTest(owner=owner):
                    self.assertEqual("callable_argument", sites[owner]["reference_kind"])
                    self.assertEqual(canonical, sites[owner]["dispatch"])
            for owner in ("unrelated", "parameter_shadow", "local_shadow", "import_shadow"):
                with self.subTest(owner=owner):
                    self.assertEqual("bound_reference", sites[owner]["reference_kind"])
                    self.assertEqual("", sites[owner]["dispatch"])
            forwards = result["store_forwarding_edges"]
            self.assertEqual(1, len(forwards))
            self.assertEqual("helper", forwards[0]["helper"])
            self.assertEqual("keyword:store", forwards[0]["argument_index"])
            self.assertEqual(canonical, forwards[0]["dispatch"])

    def test_synthetic_routes_dispatches_and_signature_drift_are_distinguished(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            (root / "src/kiwoom_monitor/central_server").mkdir(parents=True)
            (root / "docs/db_refactoring").mkdir(parents=True)
            (root / "src/kiwoom_monitor/central_server/database.py").write_text(
                "class QueryStore:\n"
                " def load_documents(self, key: str) -> list[str]: ...\n"
                "class SQLiteQueryStore(QueryStore): pass\n"
                "class PostgresQueryStore(QueryStore): pass\n",
                encoding="utf-8",
            )
            app = root / "src/kiwoom_monitor/central_server/app.py"
            app.write_text(
                "import asyncio\n"
                "class Router:\n"
                " def get(self, path): return lambda fn: fn\n"
                "router = Router()\n"
                "@router.get('/one')\n"
                "def first():\n"
                " def same_name(): return store.load_documents('one')\n"
                " return same_name()\n"
                "@router.get('/two')\n"
                "def second():\n"
                " def same_name(): return store.load_documents('two')\n"
                " return same_name()\n"
                "def helper(store): return store.load_documents('helper')\n"
                "def use_store():\n"
                " helper(store)\n"
                " return helper(store=store)\n"
                "async def background():\n"
                " await asyncio.to_thread(store.load_documents, 'bg')\n"
                "async def background_helper():\n"
                " await asyncio.to_thread(helper, store=store)\n"
                "def optional():\n"
                " return getattr(store, 'load_documents')('optional')\n"
                "def getter_reference():\n"
                " return getattr(store, 'load_documents')\n"
                "def supported():\n"
                " return hasattr(store, 'load_documents')\n",
                encoding="utf-8",
            )
            approvals_path = root / "docs/db_refactoring/approvals.json"
            approvals_path.write_text(json.dumps({"reviewed_bindings": [
                {"id": "first-route", "file": "src/kiwoom_monitor/central_server/app.py",
                 "owner_prefix": "first", "receiver": "store", "backend_scope": "fixture"},
                {"id": "store-forward", "file": "src/kiwoom_monitor/central_server/app.py",
                 "owner_prefix": "use_store", "receiver": "store", "backend_scope": "fixture"},
                {"id": "thread-forward", "file": "src/kiwoom_monitor/central_server/app.py",
                 "owner_prefix": "background_helper", "receiver": "store", "backend_scope": "fixture"},
            ]}), encoding="utf-8")

            before = inventory(root, approvals_path)
            first = next(site for site in before["reviewed_store_sites"]
                         if site["owner"] == "first.same_name")
            second = next(site for site in before["unresolved_same_name_candidates"]
                          if site["owner"] == "second.same_name")
            threaded = next(site for site in before["unresolved_same_name_candidates"]
                            if site["reference_kind"] == "callable_argument")
            dynamic = next(site for site in before["unresolved_same_name_candidates"]
                           if site["reference_kind"] == "getattr_invocation")
            getter = next(site for site in before["unresolved_same_name_candidates"]
                          if site["reference_kind"] == "getattr_reference")
            probe = next(site for site in before["unresolved_same_name_candidates"]
                         if site["reference_kind"] == "hasattr_probe")
            self.assertEqual({"method": "GET", "path": "/one"}, first["route"])
            self.assertEqual({"method": "GET", "path": "/two"}, second["route"])
            self.assertEqual("asyncio.to_thread", threaded["dispatch"])
            self.assertIsNone(dynamic["route"])
            self.assertEqual("getattr_reference", getter["reference_kind"])
            self.assertEqual("hasattr", probe["dispatch"])
            self.assertTrue(any(edge["helper"] == "helper"
                                and edge["argument_index"] == "keyword:store"
                                for edge in before["store_forwarding_edges"]))
            self.assertTrue(any(edge["helper"] == "helper"
                                and edge["dispatch"] == "asyncio.to_thread"
                                and edge["argument_index"] == "keyword:store"
                                for edge in before["store_forwarding_edges"]))

            baseline_path = root / "docs/db_refactoring/baseline.json"
            baseline_path.write_text(json.dumps({
                "contracts": before["contract_signatures"],
                "site_identities": [list(_site_identity(site)) for site in (
                    before["reviewed_store_sites"] + before["unresolved_same_name_candidates"]
                    + before["store_internal_delegates"]
                )],
                "forwarding_identities": [list(_forward_identity(edge))
                                           for edge in before["store_forwarding_edges"]],
            }), encoding="utf-8")
            self.assertEqual("pass", inventory(root, approvals_path, baseline_path)["status"])
            baseline_content = baseline_path.read_text(encoding="utf-8")
            with patch("sys.argv", ["audit_query_store_consumers.py", "--root", str(root),
                                     "--approvals", str(approvals_path), "--baseline",
                                     str(baseline_path), "--write-baseline"]):
                with self.assertRaises(SystemExit):
                    main()
            self.assertEqual(baseline_content, baseline_path.read_text(encoding="utf-8"))

            app.write_text(app.read_text(encoding="utf-8").replace(
                "return helper(store=store)",
                "return helper(store=None)",
            ), encoding="utf-8")
            forwarding_drift = inventory(root, approvals_path, baseline_path)
            self.assertEqual("review_required", forwarding_drift["status"])
            self.assertEqual(1, len(forwarding_drift["forwarding_delta"]["removed"]))

            (root / "src/kiwoom_monitor/central_server/database.py").write_text(
                "class QueryStore:\n"
                " def load_documents(self, key: str) -> tuple[str, ...]: ...\n"
                "class SQLiteQueryStore(QueryStore): pass\n"
                "class PostgresQueryStore(QueryStore): pass\n",
                encoding="utf-8",
            )
            drift = inventory(root, approvals_path, baseline_path)
            self.assertEqual("review_required", drift["status"])
            self.assertTrue(drift["contract_signatures_changed"])

    def test_identity_diff_ignores_source_line_moves_and_keeps_multiplicity(self) -> None:
        identity = ("src/a.py", "route", "store", "load_documents", "direct_call",
                    "store.load_documents", "GET", "/a", "reviewed_store_binding")

        same = compare_identities([identity], [list(identity)])
        self.assertEqual([], same["added"])
        self.assertEqual([], same["removed"])
        extra = compare_identities([identity, identity], [list(identity)])
        self.assertEqual(2, extra["added"][0]["current_count"])
        self.assertEqual(1, extra["added"][0]["baseline_count"])


if __name__ == "__main__":
    unittest.main()

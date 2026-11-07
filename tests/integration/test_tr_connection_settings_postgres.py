from __future__ import annotations

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.infrastructure.persistence.postgres_test_target import explicit_dedicated_url
from tests.integration.postgres_test_support import isolated_observation_schema


class PostgresTrConnectionSettingsTests(unittest.TestCase):
    def test_native_cas_preserves_account_bindings_and_original_market_role(self):
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated diagnostic PostgreSQL URL required")
        with isolated_observation_schema(explicit_dedicated_url(url)) as dsn, ExitStack() as stack:
            stores = [PostgresQueryStore(dsn), PostgresQueryStore(dsn)]
            for store in stores:
                stack.callback(store.close)
            first, second = stores
            first.initialize()
            now = "2026-11-06T09:31:15+09:00"

            def target(environment):
                ref = first.register_account_identity({"broker": "kiwoom", "environment": environment,
                    "identity_fingerprint": uuid.uuid4().hex * 2, "created_at": now})
                profile = str(uuid.uuid4())
                first.finalize_credential_activation({"provider": "kiwoom_" + environment, "environment": environment,
                    "profile_id": profile, "account_ref": ref, "run_id": str(uuid.uuid4()),
                    "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
                    "request_digest": uuid.uuid4().hex * 2, "credential_revision": 1, "committed_at": now})
                return {"scope": {"broker": "kiwoom", "environment": environment, "account_ref": ref},
                        "credential_profile_id": profile, "binding_revision": 1}

            execution, data = target("mock"), target("real")
            before = (first.load_market_profile_settings(), first.load_account_bindings(),
                      first.load_documents("server_account_settings"))
            self.assertEqual(0, first.load_tr_connection_settings()["revision"])
            self.assertEqual([], first.load_documents("server_tr_connection_settings"))
            values = ({"mode": "integrated", "execution_target": execution, "data_target": execution},
                      {"mode": "separated", "execution_target": execution, "data_target": data})
            barrier = threading.Barrier(2)

            def write(store, value):
                barrier.wait(timeout=5)
                try:
                    return store.save_tr_connection_settings(value, expected_revision=0)
                except ValueError as error:
                    return str(error)

            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = [executor.submit(write, store, value) for store, value in zip(stores, values, strict=True)]
                results = [item.result(timeout=15) for item in pending]
            self.assertEqual(1, results.count("TR_CONNECTION_SETTINGS_REVISION_CONFLICT"))
            winner = next(result for result in results if isinstance(result, dict))
            self.assertEqual(1, winner["revision"])
            self.assertEqual(winner, second.load_tr_connection_settings())
            with self.assertRaisesRegex(ValueError, "ACCOUNT_CONTEXT_MISMATCH"):
                second.save_tr_connection_settings({**values[1], "data_target": {**data, "binding_revision": 2}},
                                                   expected_revision=1)
            self.assertEqual(winner, first.load_tr_connection_settings())
            self.assertEqual(before, (first.load_market_profile_settings(), first.load_account_bindings(),
                                      first.load_documents("server_account_settings")))

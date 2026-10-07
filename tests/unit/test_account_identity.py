from __future__ import annotations

import tempfile
import unittest
import uuid
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.account_identity import (
    link_local_scope_to_verified_binding,
    verify_and_bind_account,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import (
    ACCOUNT_ID_API,
    ACCOUNT_PATH,
    KiwoomAccountIdentityReader,
)


NOW = datetime(2026, 9, 13, 1, 2, tzinfo=UTC)


@dataclass(frozen=True)
class _Result:
    payload: dict


class _Broker:
    def __init__(self, account_number: object) -> None:
        self._account_number = account_number
        self.calls = []

    async def request(self, api_id, path, body, *, cont_yn="N", next_key=""):
        self.calls.append((api_id, path, body, cont_yn, next_key))
        return _Result({"acctNo": self._account_number, "return_code": 0})


class AccountIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_verified_account_reuses_ref_and_records_binding_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            broker = _Broker("12345678-01")
            reader = KiwoomAccountIdentityReader(
                broker, environment=AccountEnvironment.MOCK, hmac_key=b"k" * 32,
                now_provider=lambda: NOW,
            )
            first = await verify_and_bind_account(reader, store, credential_profile_id="mock-default")
            second = await verify_and_bind_account(reader, store, credential_profile_id="mock-default")
            bindings = store.load_account_bindings()
            store.close()
            raw_database = path.read_bytes()
        self.assertEqual(first.scope.account_ref, second.scope.account_ref)
        self.assertEqual((1, 2), tuple(row["binding_revision"] for row in bindings))
        self.assertEqual((ACCOUNT_ID_API, ACCOUNT_PATH, {}, "N", ""), broker.calls[0])
        self.assertNotIn(b"12345678", raw_database)

    async def test_real_and_mock_accounts_cannot_share_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            real = await verify_and_bind_account(
                KiwoomAccountIdentityReader(
                    _Broker("12345678-01"), environment=AccountEnvironment.REAL,
                    hmac_key=b"k" * 32, now_provider=lambda: NOW,
                ), store, credential_profile_id="real-default",
            )
            mock = await verify_and_bind_account(
                KiwoomAccountIdentityReader(
                    _Broker("12345678-01"), environment=AccountEnvironment.MOCK,
                    hmac_key=b"k" * 32, now_provider=lambda: NOW,
                ), store, credential_profile_id="mock-default",
            )
            store.close()
        self.assertNotEqual(real.scope.account_ref, mock.scope.account_ref)

    async def test_invalid_or_missing_identity_is_rejected_before_storage(self) -> None:
        for value in (None, "", "123"):
            reader = KiwoomAccountIdentityReader(
                _Broker(value), environment=AccountEnvironment.MOCK,
                hmac_key=b"k" * 32, now_provider=lambda: NOW,
            )
            with self.assertRaisesRegex(ValueError, "ka00001"):
                await reader.verify()

    def test_hmac_key_must_be_recoverable_strength(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 32 bytes"):
            KiwoomAccountIdentityReader(
                _Broker("12345678-01"), environment=AccountEnvironment.MOCK,
                hmac_key=b"short",
            )

    def test_concurrent_registration_reuses_one_account_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            value = {
                "broker": "kiwoom", "environment": "mock",
                "identity_fingerprint": "a" * 64, "created_at": NOW.isoformat(),
            }
            with ThreadPoolExecutor(max_workers=4) as executor:
                refs = tuple(executor.map(
                    lambda _: store.register_account_identity(value), range(8),
                ))
            store.close()
        self.assertEqual(1, len(set(refs)))

    async def test_verified_binding_links_local_scope_and_resolution_preserves_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            binding = await verify_and_bind_account(
                KiwoomAccountIdentityReader(
                    _Broker("12345678-01"), environment=AccountEnvironment.MOCK,
                    hmac_key=b"k" * 32, now_provider=lambda: NOW,
                ), store, credential_profile_id="mock-default",
            )
            origin = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
            alias = link_local_scope_to_verified_binding(
                store, origin_scope=origin, binding=binding, verified_at=NOW,
            )
            resolved = store.resolve_account_scope("kiwoom", "mock", origin.account_ref)
            canonical = store.resolve_account_scope(
                "kiwoom", "mock", binding.scope.account_ref,
            )
            with closing(sqlite3.connect(Path(directory) / "central.sqlite3")) as connection:
                connection.execute(
                    "UPDATE central_account_registry SET status='inactive' WHERE account_ref=?",
                    (binding.scope.account_ref,),
                )
                connection.commit()
            inactive = store.resolve_account_scope("kiwoom", "mock", origin.account_ref)
            store.close()

        self.assertEqual(origin, alias.origin_scope)
        self.assertEqual(binding.scope, alias.canonical_scope)
        self.assertEqual(origin.account_ref, resolved["origin_account_ref"])
        self.assertEqual(binding.scope.account_ref, resolved["canonical_account_ref"])
        self.assertTrue(resolved["verified"])
        self.assertEqual(binding.scope.account_ref, canonical["canonical_account_ref"])
        self.assertTrue(canonical["verified"])
        self.assertEqual(origin.account_ref, inactive["canonical_account_ref"])
        self.assertFalse(inactive["verified"])

    async def test_alias_rejects_unverified_target_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            binding = await verify_and_bind_account(
                KiwoomAccountIdentityReader(
                    _Broker("12345678-01"), environment=AccountEnvironment.REAL,
                    hmac_key=b"k" * 32, now_provider=lambda: NOW,
                ), store, credential_profile_id="real-default",
            )
            origin_ref = str(uuid.uuid4())
            value = {
                "origin_account_ref": origin_ref,
                "canonical_account_ref": binding.scope.account_ref,
                "broker": "kiwoom", "environment": "real",
                "credential_profile_id": "real-default",
                "binding_revision": binding.binding_revision,
                "verified_at": NOW.isoformat(), "verification_method": "ka00001",
            }
            first = store.register_account_scope_alias(value)
            second = store.register_account_scope_alias({**value, "verified_at": NOW.isoformat()})
            self.assertEqual(first, second)
            with self.assertRaisesRegex(ValueError, "immutable"):
                store.register_account_scope_alias({
                    **value, "canonical_account_ref": str(uuid.uuid4()),
                })
            with self.assertRaisesRegex(ValueError, "recorded verified binding"):
                store.register_account_scope_alias({
                    **value, "origin_account_ref": str(uuid.uuid4()),
                    "binding_revision": 999,
                })
            store.close()

    async def test_alias_cannot_cross_environment_or_reuse_canonical_as_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            binding = await verify_and_bind_account(
                KiwoomAccountIdentityReader(
                    _Broker("12345678-01"), environment=AccountEnvironment.MOCK,
                    hmac_key=b"k" * 32, now_provider=lambda: NOW,
                ), store, credential_profile_id="mock-default",
            )
            with self.assertRaisesRegex(ValueError, "cross broker or environment"):
                link_local_scope_to_verified_binding(
                    store,
                    origin_scope=AccountScope(
                        "kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()),
                    ),
                    binding=binding,
                    verified_at=NOW,
                )
            with self.assertRaisesRegex(ValueError, "canonical account"):
                store.register_account_scope_alias({
                    "origin_account_ref": binding.scope.account_ref,
                    "canonical_account_ref": str(uuid.uuid4()),
                    "broker": "kiwoom", "environment": "mock",
                    "credential_profile_id": "mock-default",
                    "binding_revision": binding.binding_revision,
                    "verified_at": NOW.isoformat(), "verification_method": "ka00001",
                })
            store.close()


if __name__ == "__main__":
    unittest.main()

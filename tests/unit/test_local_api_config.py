from __future__ import annotations

import base64
import ctypes
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.kiwoom_rest import local_secret_keychain as keychain
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import ApiProfiles, LocalApiConfig, LocalSecretUnavailable
from kiwoom_monitor.infrastructure.kiwoom_rest.local_account_binding import LocalAccountBindingConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig, NaverNewsCredentials, NewsAISettings, OfficialNewsSettings


class LocalApiConfigTests(unittest.TestCase):
    def test_darwin_native_keychain_first_write_race_reopen_and_read_ownership(self) -> None:
        items = {}
        buffers = []
        keychain_flags = 7
        find_error = 0

        def default(keychain_out):
            ctypes.cast(keychain_out, ctypes.POINTER(ctypes.c_void_p))[0] = 42
            return 0

        def status(handle, flags_out):
            self.assertEqual(42, handle.value)
            ctypes.cast(flags_out, ctypes.POINTER(ctypes.c_uint32))[0] = keychain_flags
            return 0

        def find(handle, service_len, service, account_len, account, length, pointer, _item):
            self.assertEqual((42, len(service), len(account)), (handle.value, service_len, account_len))
            if find_error:
                return find_error
            if account not in items:
                return -25300
            buffer = ctypes.create_string_buffer(items[account], 32)
            buffers.append(buffer)
            ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = 32
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(buffer)
            return 0

        def add(_handle, _service_len, _service, _account_len, account, length, pointer, _item):
            self.assertEqual(32, length)
            candidate = ctypes.string_at(pointer, length)
            if not items:
                items[account] = b'concurrent winner key' + bytes(11)
                self.assertNotEqual(candidate, items[account])
                return -25299
            items[account] = candidate
            return 0

        security = SimpleNamespace(SecKeychainCopyDefault=Mock(side_effect=default),
                                   SecKeychainGetStatus=Mock(side_effect=status),
                                   SecKeychainFindGenericPassword=Mock(side_effect=find),
                                   SecKeychainAddGenericPassword=Mock(side_effect=add),
                                   SecKeychainItemFreeContent=Mock(return_value=0))
        core = SimpleNamespace(CFRelease=Mock())
        keychain._frameworks.cache_clear()
        try:
            with tempfile.TemporaryDirectory() as directory, patch('sys.platform', 'darwin'), patch.object(
                keychain.ctypes, 'CDLL', side_effect=[security, core],
            ) as load:
                root = Path(directory)
                config = LocalApiConfig(root / 'api.env')
                profile = ApiProfiles('fixture-app', 'fixture-secret')
                config.save_profiles(profile)
                stored = (root / 'api.env').read_text(encoding='utf-8')
                self.assertNotIn('fixture-secret', stored)
                envelope = base64.b64decode(stored.split('=', 1)[1])
                self.assertTrue(envelope.startswith(b'KRM-MAC-KEYCHAIN\x01'))
                self.assertEqual(profile, LocalApiConfig(root / 'api.env').load_profiles())
                config.save_profiles(profile)
                self.assertNotEqual(stored, (root / 'api.env').read_text(encoding='utf-8'))

                news = LocalNaverNewsConfig(root / 'naver_news.dat')
                news.save(NaverNewsCredentials('fixture-id', 'fixture-naver'))
                self.assertEqual('fixture-naver', news.load().client_secret)

                current = (root / 'api.env').read_bytes()
                foreign_path = root / 'copy' / 'api.env'
                foreign_path.parent.mkdir()
                foreign_path.write_bytes(current)
                with self.assertRaises(LocalSecretUnavailable):
                    LocalApiConfig(foreign_path).load_profiles()
                for flags, error in ((6, 0), (7, -25308), (7, -25300)):
                    keychain_flags, find_error = flags, error
                    with self.subTest(flags=flags, status=error), self.assertRaises(LocalSecretUnavailable):
                        config.save_profiles(profile)
                    self.assertEqual(current, (root / 'api.env').read_bytes())
                    if error != -25300:
                        new_path = root / 'unavailable.env'
                        with self.assertRaises(LocalSecretUnavailable):
                            LocalApiConfig(new_path).save_profiles(profile)
                        self.assertFalse(new_path.exists())
                self.assertEqual(2, security.SecKeychainAddGenericPassword.call_count)
                self.assertEqual(2, load.call_count)
                self.assertEqual(ctypes.c_int32, security.SecKeychainFindGenericPassword.restype)
                self.assertEqual(len(buffers), security.SecKeychainItemFreeContent.call_count)
                self.assertEqual(security.SecKeychainCopyDefault.call_count, core.CFRelease.call_count)
        finally:
            keychain._frameworks.cache_clear()

    def test_darwin_existing_missing_tampered_or_foreign_secret_is_never_replaced(self) -> None:
        profile = ApiProfiles('fixture-app', 'fixture-secret')
        with tempfile.TemporaryDirectory() as directory, patch('sys.platform', 'darwin'):
            path = Path(directory) / 'api.env'
            config = LocalApiConfig(path)
            with patch.object(keychain, '_keychain_key', return_value=bytes(32)):
                config.save_profiles(profile)
            good = path.read_bytes()
            with patch.object(keychain, '_keychain_key', side_effect=LocalSecretUnavailable('locked')) as native:
                for operation in (config.load_profiles, lambda: config.save_profiles(profile)):
                    with self.assertRaises(LocalSecretUnavailable):
                        operation()
                self.assertTrue(all(call.kwargs == {'create': False} for call in native.call_args_list))
            self.assertEqual(good, path.read_bytes())

            prefix = b'KIWOOM_CONFIG_ENCRYPTED='
            payload = bytearray(base64.b64decode(good.removeprefix(prefix)))
            payload[-1] ^= 1
            for raw in (prefix + base64.b64encode(payload), prefix + base64.b64encode(b'foreign-dpapi')):
                path.write_bytes(raw)
                with patch.object(keychain, '_keychain_key', return_value=bytes(32)):
                    with self.assertRaises(LocalSecretUnavailable):
                        config.save_profiles(profile)
                self.assertEqual(raw, path.read_bytes())

            path.write_bytes(good)
            with patch('sys.platform', 'linux'):
                with self.assertRaises(LocalSecretUnavailable):
                    config.save_profiles(profile)
            self.assertEqual(good, path.read_bytes())

    def test_credential_repr_masks_values_without_changing_data_fields(self) -> None:
        sentinel = 'fixture-key-that-must-not-be-in-repr'
        for record, fields in (
            (ApiProfiles(*[sentinel] * 4), ('mock_app_key', 'mock_secret_key', 'real_app_key', 'real_secret_key')),
            (KiwoomSettings(sentinel, sentinel, 'mock'), ('app_key', 'secret_key')),
            (NaverNewsCredentials(sentinel, sentinel), ('client_id', 'client_secret')),
            (NewsAISettings(api_key=sentinel), ('api_key',)),
            (OfficialNewsSettings(dart_api_key=sentinel), ('dart_api_key',)),
        ):
            with self.subTest(record=type(record).__name__):
                self.assertNotIn(sentinel, repr(record))
                self.assertEqual([sentinel] * len(fields), [record.__dict__[name] for name in fields])
                self.assertEqual(record, type(record)(**record.__dict__))

    def test_returns_empty_profiles_when_configuration_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = LocalApiConfig(Path(directory) / "data" / "api.env")

            self.assertEqual(ApiProfiles(), config.load_profiles())
            self.assertEqual("", config.load().app_key)

    def test_encrypts_and_restores_api_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "api.env"
            config = LocalApiConfig(path)
            config.save_profiles(ApiProfiles("mock-app", "mock-secret", "real-app", "real-secret", "real"))

            stored = path.read_text(encoding="utf-8")
            self.assertIn("KIWOOM_CONFIG_ENCRYPTED=", stored)
            self.assertNotIn("mock-app", stored)
            self.assertNotIn("real-secret", stored)

            loaded = config.load()
            self.assertEqual("real-app", loaded.app_key)
            self.assertEqual("real-secret", loaded.secret_key)
            self.assertEqual("real", loaded.environment)

    def test_account_binding_mirror_is_protected_and_keeps_latest_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "account-bindings.dat"
            config = LocalAccountBindingConfig(path)
            account_ref = str(uuid.uuid4())
            first = AccountBinding(
                "mock-default", AccountScope("kiwoom", AccountEnvironment.MOCK, account_ref),
                1, datetime(2026, 9, 13, tzinfo=UTC),
            )
            latest = AccountBinding(
                "mock-default", first.scope, 2, datetime(2026, 9, 13, 1, tzinfo=UTC),
            )
            config.save_binding(first)
            config.save_binding(latest)

            raw = path.read_text(encoding="utf-8")
            loaded = config.load_bindings()
            self.assertIn("KIWOOM_ACCOUNT_BINDINGS_ENCRYPTED=", raw)
            self.assertNotIn(account_ref, raw)
            self.assertEqual((latest,), loaded)
            with self.assertRaisesRegex(ValueError, "older revision"):
                config.save_binding(first)

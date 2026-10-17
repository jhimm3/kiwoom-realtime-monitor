"""Actual POSIX ownership, encryption and inter-process session lock gate."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
from kiwoom_monitor.infrastructure.chatgpt_linux_store import LinuxChatGPTCredentialStore


@unittest.skipUnless(os.name == "posix", "Linux private permissions/flock gate")
class NASChatGPTLinuxStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LinuxChatGPTCredentialStore(Path(self.temp.name) / "private" / "session.enc")

    def save(self):
        with self.store.locked(): self.store.save({"host_id": "urn:uuid:test", "active": "account",
            "accounts": {"account": {"access_token": os.urandom(12).hex()}}})

    def test_encryption_private_permissions_and_reopen(self):
        self.save()
        value = self.store.load()
        self.assertNotIn(value["accounts"]["account"]["access_token"].encode(), self.store.path.read_bytes())
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(LinuxChatGPTCredentialStore(self.store.path).load()["active"], "account")

    def test_unsafe_modes_and_symlinks_rejected(self):
        self.save()
        self.store.key_path.chmod(0o644)
        with self.assertRaises(ChatGPTPlanError): self.store.load()
        self.store.key_path.chmod(0o600)
        self.store.path.unlink()
        self.store.path.symlink_to(self.store.key_path)
        with self.assertRaises(ChatGPTPlanError): self.store.load()

    def test_inner_exception_preserved_and_ownership_released(self):
        with self.assertRaisesRegex(ValueError, "operation failed"):
            with self.store.locked(): raise ValueError("operation failed")
        with self.store.locked(): self.store.save({"host_id": "", "active": "", "accounts": {}})

    def test_cross_process_lock_rejects_second_owner(self):
        code = "from pathlib import Path;from kiwoom_monitor.infrastructure.chatgpt_linux_store import LinuxChatGPTCredentialStore;from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError;import sys\ntry:\n with LinuxChatGPTCredentialStore(Path(sys.argv[1])).locked(): print('unexpected')\nexcept ChatGPTPlanError as e: print(e.code)"
        with self.store.locked():
            reply = subprocess.run([sys.executable, "-c", code, str(self.store.path)],
                capture_output=True, text=True, timeout=10, check=True,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")})
        self.assertEqual(reply.stdout.strip(), "storage_busy")

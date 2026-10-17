import copy
from contextlib import contextmanager
import json
import os
import unittest

from scripts.move_chatgpt_session_to_nas import move
from scripts.nas_chatgpt_session import validate_import, operate
from scripts.install_nas_chatgpt_runtime import replacement_body, check_replacement, reviewed_index, network_identities
from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError, DIRECT_SCOPE, ISSUER


class Store:
    def __init__(self):
        self.value = {"host_id": "urn:uuid:pc", "active": "client", "accounts": {"client": {
            "client_id": "client", "host_id": "urn:uuid:pc", "issuer": ISSUER, "subject": "test",
            "access_token": os.urandom(12).hex(), "refresh_token": os.urandom(12).hex(),
            "id_token": "", "token_type": "Bearer", "scopes": [DIRECT_SCOPE], "expires_at": 9999999999}}}
    @contextmanager
    def locked(self): yield
    def load(self): return copy.deepcopy(self.value)
    def save(self, value): self.value = copy.deepcopy(value)


class ChatGPTNASTransferTests(unittest.TestCase):
    def test_import_binds_nas_host_and_pc_is_retired_only_after_durable_ack(self):
        source = Store()
        calls = []
        def transport(action, data=None):
            calls.append(action)
            if action == "prepare": return {"host_id": "urn:uuid:nas", "empty": True}
            self.assertTrue(source.load()["accounts"]["client"]["refresh_pending"])
            record = validate_import(json.loads(data), "urn:uuid:nas")
            self.assertEqual(record["host_id"], "urn:uuid:nas")
            self.assertEqual(record["client_id"], "client")
            return {"host_id": "urn:uuid:nas", "imported": True}
        self.assertTrue(move(source, transport)["pc_session_retired"])
        account = source.load()["accounts"]["client"]
        self.assertEqual(account["access_token"], "")
        self.assertEqual(account["refresh_token"], "")
        self.assertEqual(calls, ["prepare", "import"])

    def test_lost_ack_preserves_durable_pc_fence_and_never_retries(self):
        source = Store()
        count = 0
        def transport(action, data=None):
            nonlocal count
            if action == "prepare": return {"host_id": "urn:uuid:nas", "empty": True}
            count += 1
            raise ChatGPTPlanError("network_error")
        with self.assertRaises(ChatGPTPlanError): move(source, transport)
        self.assertEqual(count, 1)
        self.assertTrue(source.load()["accounts"]["client"]["refresh_pending"])
        with self.assertRaises(ChatGPTPlanError): move(source, transport)
        self.assertEqual(count, 1)

    def test_import_rejects_wrong_host_wrong_scope_and_rotation_in_progress(self):
        payload = {"host_id": "urn:uuid:nas", "account": Store().load()["accounts"]["client"]}
        with self.assertRaises(ChatGPTPlanError): validate_import(payload, "urn:uuid:other")
        payload["account"]["scopes"] = []
        with self.assertRaises(ChatGPTPlanError): validate_import(payload, "urn:uuid:nas")
        payload["account"]["scopes"] = [DIRECT_SCOPE]
        payload["account"]["refresh_pending"] = True
        with self.assertRaises(ChatGPTPlanError): validate_import(payload, "urn:uuid:nas")

    def test_reconnect_preserves_nas_host_and_registration_after_disconnect(self):
        store = Store()
        store.value["host_id"] = "urn:uuid:nas"
        for key in ("access_token", "refresh_token", "id_token"):
            store.value["accounts"]["client"][key] = ""
        prepared = operate(store, "prepare")
        self.assertTrue(prepared["empty"])
        self.assertEqual(prepared["client_id"], "client")
        fresh = Store().load()["accounts"]["client"]
        self.assertTrue(operate(store, "import", {"host_id": "urn:uuid:nas", "account": fresh})["imported"])
        self.assertEqual(store.load()["host_id"], "urn:uuid:nas")
        self.assertEqual(store.load()["active"], "client")

    def test_runtime_replacement_preserves_env_mounts_and_networks(self):
        original = {"Id": "a" * 64, "State": {"Running": True, "Status": "running"},
                    "Config": {"Image": "old", "Env": ["fixture=" + os.urandom(8).hex()], "Cmd": ["python"], "Labels": {}},
                    "HostConfig": {"Binds": ["/fixed:/app/data"]},
                    "Mounts": [{"Type": "bind", "Source": "/fixed", "Destination": "/app/data", "RW": True}],
                    "NetworkSettings": {"Networks": {"app": {"NetworkID": "network", "Aliases": ["server"]}}}}
        body = replacement_body(original, "new")
        self.assertEqual(body["Env"], original["Config"]["Env"])
        actual = copy.deepcopy(original); actual["Config"]["Image"] = "new"
        check_replacement(original, actual, "new")
        actual["Mounts"][0]["Source"] = "/other"
        with self.assertRaises(RuntimeError): check_replacement(original, actual, "new")

    def network_fixture(self):
        original = {"Id": "a" * 64, "State": {"Running": True, "Status": "running"},
                    "Config": {"Image": "old"}, "HostConfig": {"NetworkMode": "app"}, "Mounts": [],
                    "NetworkSettings": {"Networks": {"app": {
                        "NetworkID": "network", "Aliases": ["server", "a" * 12],
                        "Links": None, "IPAMConfig": None}}}}
        actual = copy.deepcopy(original)
        actual.update(Id="b" * 64, State={"Running": False, "Status": "created"})
        actual["Config"]["Image"] = "new"
        actual["NetworkSettings"]["Networks"]["app"]["NetworkID"] = ""
        return original, actual

    def test_created_network_matches_live_nas_only_with_resolved_original_identity(self):
        original, actual = self.network_fixture()
        check_replacement(original, actual, "new", created=True, resolved_networks={"app": "network"})
        for resolved in (None, {}, {"app": "other"}):
            with self.subTest(resolved=resolved), self.assertRaisesRegex(RuntimeError, 'replacement_network_changed'):
                check_replacement(original, actual, "new", created=True, resolved_networks=resolved)
        actual['State'] = {'Running': False, 'Status': 'exited'}
        with self.assertRaises(RuntimeError):
            check_replacement(original, actual, 'new', created=True, resolved_networks={'app': 'network'})

    def test_started_replacement_requires_exact_network_and_preserved_aliases(self):
        original, actual = self.network_fixture()
        actual['State'] = {'Running': True, 'Status': 'running'}
        endpoint = actual['NetworkSettings']['Networks']['app']
        for identity in ('', 'other'):
            endpoint['NetworkID'] = identity
            with self.subTest(identity=identity), self.assertRaisesRegex(RuntimeError, 'replacement_network_changed'):
                check_replacement(original, actual, 'new')
        endpoint['NetworkID'] = 'network'
        endpoint['Aliases'].append('b' * 12)
        check_replacement(original, actual, 'new')
        for aliases in (['server', 'b' * 12], ['server', 'a' * 12, 'unexpected']):
            endpoint['Aliases'] = aliases
            with self.subTest(aliases=aliases), self.assertRaisesRegex(RuntimeError, 'replacement_network_aliases_changed'):
                check_replacement(original, actual, 'new')

    def test_created_replacement_rejects_network_and_endpoint_drift(self):
        original, actual = self.network_fixture()
        changes = [lambda v: v['NetworkSettings']['Networks'].update(other={}),
                   lambda v: v['NetworkSettings']['Networks']['app'].update(NetworkID='other'),
                   lambda v: v['NetworkSettings']['Networks']['app'].update(IPAMConfig={'IPv4Address': '172.18.0.9'}),
                   lambda v: v['NetworkSettings']['Networks']['app'].update(Links=['other:db']),
                   lambda v: v['HostConfig'].update(NetworkMode='other'),
                   lambda v: v['Config'].update(Env=['changed']),
                   lambda v: v['Mounts'].append({'Type': 'bind', 'Source': '/other', 'Destination': '/app/data', 'RW': True})]
        for change in changes:
            changed = copy.deepcopy(actual); change(changed)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                check_replacement(original, changed, 'new', created=True, resolved_networks={'app': 'network'})

    def test_network_resolution_uses_exact_names_and_detects_missing_or_duplicate_networks(self):
        original, unused = self.network_fixture()
        class Operator:
            def docker(inner, args):
                self.assertEqual(args, ['network', 'inspect', 'app'])
                return json.dumps(inner.networks)
        op = Operator(); op.networks = [{'Name': 'app', 'Id': 'network'}]
        self.assertEqual(network_identities(op, original), {'app': 'network'})
        for networks in ([], [{'Name': 'other', 'Id': 'network'}], op.networks * 2):
            op.networks = networks
            with self.subTest(networks=networks), self.assertRaises(RuntimeError):
                network_identities(op, original)

    def test_runtime_review_keeps_original_revocation_backups_and_updates_only_config_hash(self):
        original = {"state": "complete", "entries": [
            {"path": "/fixed/operator.json", "installed_sha256": "before", "installed_mode": 0o600, "sha256": "backup", "existed": False},
            {"path": "/fixed/launcher", "installed_sha256": "launcher", "installed_mode": 0o755}]}
        updated = reviewed_index(original, "/fixed/operator.json", b"new config")
        self.assertEqual(original["entries"][0]["installed_sha256"], "before")
        self.assertEqual(updated["entries"][0]["sha256"], "backup")
        self.assertEqual(updated["entries"][1], original["entries"][1])
        with self.assertRaises(RuntimeError): reviewed_index(original, "/other/operator.json", b"config")

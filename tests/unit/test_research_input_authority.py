"""Input-export grants exercise only their two actual preparation HTTP paths."""
from __future__ import annotations

import json
import os
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from fastapi.routing import iter_route_contexts

from kiwoom_monitor.central_server.config import CentralServerSettings, ProducerGrant
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from tests.unit.test_query_authority import ADMIN, QUERY, query_app, wire_server


INPUT = "input-only-preparation-fixture"
RESULT = "result-only-preparation-fixture"
BODY = "body-only-preparation-fixture"
START = datetime(2026, 9, 12, 9, tzinfo=KST)
END = START + timedelta(hours=1)
PARAMS = {"start": START.isoformat(), "end": END.isoformat(), "kinds": "ranking", "subject": "5"}


def input_settings(database="sqlite:///:memory:", grants=None):
    return CentralServerSettings(database, ADMIN, query_access_token=QUERY,
        research_input_grants=(ProducerGrant("preparation", INPUT),) if grants is None else grants,
        research_result_grants=(ProducerGrant("reporter", RESULT),),
        news_body_grants=(ProducerGrant("body", BODY),),
        news_naver_api_enabled=False, news_naver_stock_enabled=False,
        news_naver_market_enabled=False, news_query_set_enabled=False,
        news_history_jobs_enabled=False, autonomous_top20_enabled=False,
        market_event_collection_enabled=False,
        external_market_enabled=False)


def seed_ranking(store, offset):
    observed = START + timedelta(seconds=offset)
    key = observed.replace(tzinfo=None).isoformat()
    payload = {"query_type": "5", "items": [{"stk_cd": f"{offset + 1:06d}"}]}
    store.save_dataset_snapshot("ranking", "5", key, payload,
        observation=ranking_observation("5", key, payload, observed, source="input-authority-fixture"))


class ResearchInputAuthorityTests(unittest.TestCase):
    def test_input_grants_are_opt_in_immutable_secret_free_and_role_disjoint(self):
        self.assertEqual((), CentralServerSettings("sqlite:///:memory:", ADMIN).research_input_grants)
        self.assertNotIn(INPUT, repr(input_settings()))
        self.assertNotIn(INPUT, repr(ProducerGrant("preparation", INPUT)))
        invalid = [[], (ProducerGrant("a", ""),), (ProducerGrant("a", " leading"),),
            (ProducerGrant("a", "trailing "),), (ProducerGrant("bad owner", INPUT),),
            (ProducerGrant("", INPUT),), (ProducerGrant("a" * 97, INPUT),),
            (ProducerGrant("a", ADMIN),), (ProducerGrant("a", QUERY),),
            (ProducerGrant("a", RESULT),), (ProducerGrant("a", BODY),),
            (ProducerGrant("a", INPUT), ProducerGrant("b", INPUT)),
            (ProducerGrant("a", INPUT), ProducerGrant("a", "different-fixture"))]
        for grants in invalid:
            with self.subTest(grants=repr(grants)), self.assertRaises(ValueError):
                input_settings(grants=grants)
        encoded = json.dumps([{"owner": "preparation", "token": INPUT}])
        with patch.dict(os.environ, {"MONITOR_SERVER_ACCESS_TOKEN": ADMIN,
                "MONITOR_SERVER_RESEARCH_INPUT_GRANTS_JSON": encoded}, clear=True):
            self.assertEqual((ProducerGrant("preparation", INPUT),),
                CentralServerSettings.from_environment().research_input_grants)
        for encoded in ("{}", "null", "not-json", '[{"owner":"preparation"}]',
                '[{"owner":"preparation","token":"fixture","extra":true}]'):
            with self.subTest(encoded=encoded), patch.dict(os.environ,
                    {"MONITOR_SERVER_ACCESS_TOKEN": ADMIN,
                     "MONITOR_SERVER_RESEARCH_INPUT_GRANTS_JSON": encoded}, clear=True):
                with self.assertRaisesRegex(ValueError, "MONITOR_SERVER_RESEARCH_INPUT_GRANTS_JSON"):
                    CentralServerSettings.from_environment()

    def test_wire_input_export_pages_and_theme_history_preserve_fixed_native_input(self):
        app = query_app(input_settings())
        store = app.state.query_authority_store
        with wire_server(app) as (base, _), httpx.Client(base_url=base, timeout=5) as wire:
            seed_ranking(store, 0)
            seed_ranking(store, 1)
            original = store.load_observation_revisions("ranking", "5")
            source = CentralContentClient(base, INPUT)
            first = source.load_research_observations_page(START, END, ("ranking",), subject="5", limit=1)
            watermark = first["watermark"]
            self.assertEqual(2, first["manifest"]["revision_count"])
            self.assertEqual(1, first["next_cursor"])
            seed_ranking(store, 2)
            second = source.load_research_observations_page(START, END, ("ranking",),
                subject="5", watermark=watermark, cursor=first["next_cursor"], limit=1)
            self.assertEqual(first["manifest"], second["manifest"])
            self.assertIsNone(second["next_cursor"])
            rows = first["observations"] + second["observations"]
            self.assertEqual({row["revision_id"] for row in original}, {row["revision_id"] for row in rows})
            self.assertEqual(store.load_observation_export_page(watermark, 0, 1000)["observations"], rows)
            self.assertEqual(original, [row for row in store.load_observation_revisions("ranking", "5")
                if row["revision_id"] in {value["revision_id"] for value in original}])
            store.replace_documents("theme_metadata", [{"owner": "default", "key": "full",
                "document": {"format": THEME_BACKUP_FORMAT, "version": THEME_BACKUP_VERSION,
                    "profiles": [], "active_profile": "default"}}])
            history = source.load_theme_history(limit=1000)
            self.assertTrue(history["known"])
            self.assertEqual(store.load_theme_snapshots(limit=1000), history["snapshots"])
            cutoff = history["snapshots"][0]["available_at"]
            self.assertEqual(store.load_theme_snapshots(available_at=cutoff, limit=1000),
                source.load_theme_history(as_of=cutoff, limit=1000)["snapshots"])
            self.assertEqual(store.load_theme_snapshots(available_at=END.timestamp(), limit=1000),
                source.load_theme_history(as_of=END.timestamp(), limit=1000)["snapshots"])
            headers = {"Authorization": "Bearer " + INPUT}
            for changed, expected in (({"watermark": watermark, "subject": "other"}, 409),
                    ({"watermark": "unknown-input-fixture"}, 404), ({"cursor": 1}, 400),
                    ({"start": START.replace(tzinfo=None).isoformat()}, 400),
                    ({"end": (START + timedelta(days=2)).isoformat()}, 400)):
                response = wire.get("/api/v1/research/observations", params={**PARAMS, **changed}, headers=headers)
                self.assertEqual(expected, response.status_code, response.text)
            self.assertEqual(first, CentralContentClient(base, ADMIN).load_research_observations_page(
                START, END, ("ranking",), subject="5", watermark=watermark, limit=1))
            self.assertEqual(200, wire.get("/api/v1/themes/history",
                headers={"Authorization": "Bearer " + QUERY}).status_code)

    def test_wire_query_and_result_tokens_stop_before_export_and_page_handlers(self):
        app = query_app(input_settings())
        store = app.state.query_authority_store
        with wire_server(app) as (base, _), httpx.Client(base_url=base, timeout=5) as wire:
            route = next(route for route in iter_route_contexts(app.routes) if getattr(route, "path", "") == "/api/v1/research/observations")
            spy = Mock(side_effect=AssertionError("unauthorized export handler entered"))
            with patch.object(route.dependant, "call", spy), \
                    patch.object(store, "create_observation_export") as create, \
                    patch.object(store, "load_observation_export_page") as read:
                for token, expected in ((QUERY, 403), (RESULT, 401), (BODY, 401), ("invalid", 401), ("", 401)):
                    for extra in ({}, {"watermark": "unread-fixed-fixture"}):
                        response = wire.get("/api/v1/research/observations", params={**PARAMS, **extra},
                            headers={"Authorization": "Bearer " + token} if token else {})
                        self.assertEqual(expected, response.status_code, response.text)
                spy.assert_not_called()
                create.assert_not_called()
                read.assert_not_called()

    def test_wire_input_token_cannot_enter_other_read_result_order_control_or_credential_handlers(self):
        app = query_app(input_settings())
        denied = [("GET", "/api/v1/capabilities"), ("GET", "/api/v1/settings/credentials"),
            ("GET", "/api/v1/settings/credential-operations/{operation_id}"),
            ("GET", "/api/v3/kiwoom/accounts"), ("GET", "/api/v1/mock/orders/{intent_id}"),
            ("GET", "/api/v1/settings/operations"), ("POST", "/api/v1/mock-automation/start"),
            ("POST", "/api/v1/mock-automation/stop"),
            ("POST", "/api/v1/research/mock-automation-candidates"),
            ("GET", "/api/v1/research/candidates"), ("GET", "/api/v1/research/ram-shadow"),
            ("GET", "/api/v1/themes/current-snapshot"), ("GET", "/api/v1/content/{collection}"),
            ("POST", "/api/v1/content/{collection}"),
            ("POST", "/api/v1/research/reported-results"),
            ("GET", "/api/v1/research/reported-results/{result_id}")]
        with wire_server(app) as (base, _), httpx.Client(base_url=base, timeout=5) as wire, ExitStack() as stack:
            spies = {}
            for route in iter_route_contexts(app.routes):
                for method, path in denied:
                    if getattr(route, "path", "") == path and method in getattr(route, "methods", ()):
                        spy = Mock(side_effect=AssertionError("input token entered a forbidden handler"))
                        stack.enter_context(patch.object(route.dependant, "call", spy))
                        spies[(method, path)] = spy
            self.assertEqual(set(denied), set(spies))
            for method, path in denied:
                path = path.format(operation_id="00000000-0000-0000-0000-000000000001",
                    intent_id="input-denied", result_id="input-denied", collection="theme_profile")
                response = wire.request(method, path, json={} if method == "POST" else None,
                    headers={"Authorization": "Bearer " + INPUT})
                self.assertEqual(401, response.status_code, (path, response.text))
            for spy in spies.values():
                spy.assert_not_called()

    def test_restart_without_input_grant_revokes_input_routes_and_preserves_admin_fixed_export(self):
        with TemporaryDirectory() as directory:
            database = "sqlite:///" + str(Path(directory) / "input.sqlite3")
            app = query_app(input_settings(database))
            with wire_server(app) as (base, _):
                seed_ranking(app.state.query_authority_store, 0)
                first = CentralContentClient(base, INPUT).load_research_observations_page(
                    START, END, ("ranking",), subject="5", limit=1)
            restarted = query_app(input_settings(database, grants=()))
            with wire_server(restarted) as (base, _), httpx.Client(base_url=base, timeout=5) as wire, ExitStack() as stack:
                spies = []
                for route in iter_route_contexts(restarted.routes):
                    if getattr(route, "path", "") in ("/api/v1/research/observations", "/api/v1/themes/history"):
                        spy = Mock(side_effect=AssertionError("revoked input handler entered"))
                        stack.enter_context(patch.object(route.dependant, "call", spy))
                        spies.append(spy)
                self.assertEqual(2, len(spies))
                for path in ("/api/v1/research/observations", "/api/v1/themes/history"):
                    response = wire.get(path, params={**PARAMS, "watermark": first["watermark"]}
                        if path.endswith("observations") else {}, headers={"Authorization": "Bearer " + INPUT})
                    self.assertEqual(401, response.status_code, response.text)
                for spy in spies:
                    spy.assert_not_called()
                stack.close()
                self.assertEqual(first, CentralContentClient(base, ADMIN).load_research_observations_page(
                    START, END, ("ranking",), subject="5", watermark=first["watermark"], limit=1))
                self.assertEqual(first["observations"], CentralContentClient(base, ADMIN).load_research_observations_page(
                    START, END, ("ranking",), subject="5", limit=1)["observations"])
                self.assertEqual(403, wire.get("/api/v1/research/observations", params=PARAMS,
                    headers={"Authorization": "Bearer " + QUERY}).status_code)
                self.assertEqual(200, wire.get("/api/v1/themes/history",
                    headers={"Authorization": "Bearer " + QUERY}).status_code)

    def test_headless_discovery_uses_selected_input_connection_and_publishes_exact_native_revisions(self):
        import hashlib
        from datetime import UTC

        from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from kiwoom_monitor.infrastructure.research_data_source import load_research_input
        from kiwoom_monitor.research_process import discover_campaign_inputs
        from research_test_support import write_campaign_request

        with TemporaryDirectory() as directory:
            root = Path(directory)
            request_path, request = write_campaign_request(root)
            scope = {"captured_range": {"start": "2026-09-12T00:00:00+00:00", "end": "2026-09-12T01:00:00+00:00"},
                "kinds": ["ranking"], "subject": "5", "universe_rule": "top20_membership-v1",
                "order_policy_version": "available_at-ingest_sequence-revision_id-v1"}
            template_manifest = request.dataset / "manifest.json"
            template_manifest.write_text(json.dumps({**json.loads(template_manifest.read_text(encoding="utf-8")),
                **scope}), encoding="utf-8")
            watch = root / "prepared"
            watch.mkdir()
            repository = ResearchRepository(request.database)
            repository.create_campaign("input-http", "input authority", ResearchCampaignPolicy())
            repository.enqueue_campaign_experiment("input-http", request.search, request.dataset)
            template_job = repository.load_campaign_jobs("input-http")[0]["job_id"]
            app = query_app(input_settings())
            store = app.state.query_authority_store
            requests = []

            @app.middleware("http")
            async def observe_input_requests(incoming, call_next):
                if incoming.url.path in ("/api/v1/research/observations", "/api/v1/themes/history"):
                    requests.append((incoming.url.path, incoming.headers.get("authorization", ""),
                        dict(incoming.query_params)))
                return await call_next(incoming)

            with wire_server(app) as (base, _):
                seed_ranking(store, 0)
                seed_ranking(store, 1)
                original = store.load_observation_revisions("ranking", "5")
                self.assertEqual(2, len(original))
                ordered = sorted(original, key=lambda row: (datetime.fromisoformat(row["available_at"]),
                    row["accepted_sequence"], row["revision_id"]))
                expected_rows = [dict(row, ordinal=index) for index, row in enumerate(ordered, start=1)]
                expected_ids = [row["revision_id"] for row in ordered]
                expected_ids_hash = hashlib.sha256("\n".join(expected_ids).encode()).hexdigest()
                fingerprint = hashlib.sha256(b"\ncollection\n")
                for row in ordered:
                    fingerprint.update(json.dumps(row, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode() + b"\n")
                fingerprint.update(b"\ncollection\n")
                expected_fingerprint = fingerprint.hexdigest()
                self.assertEqual(["2026-09-12T00:00:00+00:00", "2026-09-12T00:00:01+00:00"],
                    [row["available_at"] for row in ordered])
                self.assertEqual([{"query_type": "5", "items": [{"stk_cd": "000001"}]},
                    {"query_type": "5", "items": [{"stk_cd": "000002"}]}], [row["payload"] for row in ordered])
                connection = root / "research-input.json"
                operator_connection = root / "data_source.json"
                selected = DataSourceSettings(mode="local_server", server_url=base, access_token=INPUT)
                DataSourceConfig(connection).save(selected)
                DataSourceConfig(operator_connection).save(
                    DataSourceSettings(mode="local_server", server_url=base, access_token=ADMIN))
                self.assertEqual(selected, DataSourceConfig(connection).load())
                source_id = repository.save_campaign_input_source("input-http", template_job, watch,
                    nas_auto_prepare=True, nas_config_path=connection)
                source_before = repository.load_campaign_input_sources("input-http")[0]
                self.assertEqual(str(connection.resolve()), source_before["nas_config_path"])
                protected = {path: path.read_bytes() for path in
                    (request_path, template_manifest, request.dataset / "observations.jsonl", connection, operator_connection)}
                repository.set_campaign_desired_state("input-http", "RUNNING")
                claim = repository.claim_campaign_worker("input-http", owner_token="input-http-worker", lease_seconds=3600)
                self.assertIsNotNone(claim)
                try:
                    result = discover_campaign_inputs(repository, "input-http", claim, now=datetime.now(UTC))
                    self.assertEqual([], result["errors"])
                    self.assertEqual(1, result["nas_prepared"])
                    self.assertEqual(1, result["registered"])
                    self.assertEqual(0, result["out_of_scope"])
                    self.assertEqual(["/api/v1/research/observations", "/api/v1/themes/history",
                        "/api/v1/research/observations"], [path for path, _, _ in requests])
                    self.assertEqual({"Bearer " + INPUT}, {authorization for _, authorization, _ in requests})
                    self.assertEqual("", requests[0][2]["watermark"])
                    self.assertEqual("1", requests[0][2]["limit"])
                    self.assertEqual(str(END.timestamp()), requests[1][2]["as_of"])
                    self.assertEqual("1", requests[2][2]["cursor"])
                    jobs = repository.load_campaign_jobs("input-http")
                    self.assertEqual(2, len(jobs))
                    published_jobs = [job for job in jobs if job["source_kind"] == "new_data"]
                    self.assertEqual(1, len(published_jobs))
                    published_job = published_jobs[0]
                    published = Path(published_job["input_path"])
                    self.assertEqual(watch.resolve(), published.parent)
                    self.assertEqual("nas-" + source_id[:12] + "-" + expected_fingerprint, published.name)
                    dataset = load_research_input(published)
                    self.assertEqual(expected_rows, list(dataset.observations))
                    self.assertEqual((), dataset.theme_snapshots)
                    manifest = dataset.manifest
                    self.assertEqual(expected_ids_hash, manifest["revision_ids_hash"])
                    self.assertEqual(2, manifest["revision_count"])
                    self.assertEqual(["input-authority-fixture"], manifest["source_ids"])
                    self.assertEqual(manifest["dataset_id"], manifest["fixed_watermark"])
                    self.assertEqual(manifest["fixed_watermark"], requests[2][2]["watermark"])
                    for key, value in scope.items():
                        self.assertEqual(value, manifest[key])
                    for file_key, hash_key in (("observations_file", "observations_file_hash"),
                            ("theme_snapshots_file", "theme_snapshots_file_hash")):
                        self.assertEqual(hashlib.sha256((published / manifest[file_key]).read_bytes()).hexdigest(),
                            manifest[hash_key])
                    self.assertEqual(hashlib.sha256(b"").hexdigest(), manifest["theme_snapshots_file_hash"])
                    native_page = CentralContentClient(base, INPUT).load_research_observations_page(
                        START, END, ("ranking",), subject="5", watermark=manifest["fixed_watermark"])
                    self.assertEqual(expected_rows, native_page["observations"])
                    for key, value in native_page["manifest"].items():
                        self.assertEqual(value, manifest[key])
                    acceptances = repository.load_campaign_input_acceptances(source_id)
                    self.assertEqual(2, len(acceptances))
                    accepted = [row for row in acceptances if row["input_path"] == str(published)]
                    self.assertEqual(1, len(accepted))
                    self.assertEqual(expected_fingerprint, accepted[0]["fingerprint"])
                    self.assertEqual(published_job["job_id"], accepted[0]["job_id"])
                    self.assertEqual(hashlib.sha256((published / "manifest.json").read_bytes()).hexdigest(),
                        accepted[0]["manifest_hash"])
                    operations = repository.load_campaign_storage_operations(source_id)
                    self.assertEqual(1, len(operations))
                    self.assertEqual("PUBLISHED", operations[0]["state"])
                    self.assertEqual(str(published), operations[0]["input_path"])
                    source_after = repository.load_campaign_input_sources("input-http")[0]
                    self.assertTrue(source_after["remote_signature"])
                    for key in ("source_id", "template_job_id", "root", "enabled", "nas_auto_prepare", "nas_config_path"):
                        self.assertEqual(source_before[key], source_after[key])
                    reopened = ResearchRepository(request.database)
                    self.assertEqual(jobs, reopened.load_campaign_jobs("input-http"))
                    self.assertEqual(acceptances, reopened.load_campaign_input_acceptances(source_id))
                    self.assertEqual(source_after, reopened.load_campaign_input_sources("input-http")[0])
                    self.assertEqual(dataset, load_research_input(published))
                    self.assertEqual(original, store.load_observation_revisions("ranking", "5"))
                    self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                    self.assertEqual([], list(watch.glob(".nas-preparing-*")))
                finally:
                    repository.set_campaign_desired_state("input-http", "PAUSED")
                    repository.finish_campaign_worker("input-http", owner_token=claim["owner_token"],
                        generation=claim["generation"], outcome="EXPECTED_EXIT")

from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentClient,
    CentralContentHttpError,
    CentralContentUnavailableError,
    is_missing_collection_error,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class CentralContentClientTests(unittest.TestCase):
    def test_clock_uses_existing_response_and_preserves_timezone_instant(self):
        calls = []
        def opener(request, **kwargs):
            calls.append(request)
            response = Response(b'{"status":"ok"}')
            response.headers = {'X-Kiwoom-Server-Time': '2026-10-15T05:21:03+00:00'}
            return response
        client = CentralContentClient('https://nas.test', 'fixture', opener=opener)
        with patch('kiwoom_monitor.infrastructure.central_content_client.monotonic', return_value=10):
            client.load_health()
        with patch('kiwoom_monitor.infrastructure.central_content_client.monotonic', return_value=12):
            self.assertEqual(client.server_now().isoformat(), '2026-10-15T14:21:05+09:00')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get_header('X-kiwoom-clock'), '1')

    def test_source_check_is_explicit_authenticated_and_rejects_invalid_budget_without_io(self):
        calls = []
        def opener(request, **kwargs):
            calls.append((request, kwargs))
            return Response(b'{"order_authorized":false,"replay_eligible":false}')
        client = CentralContentClient("https://nas.test", "token", opener=opener)
        report = client.check_ram_shadow_sources(expected_revision=7, lookback_seconds=30)
        self.assertFalse(report["order_authorized"])
        self.assertEqual("GET", calls[0][0].method)
        self.assertTrue(calls[0][0].full_url.endswith("source-check?expected_revision=7&lookback_seconds=30"))
        self.assertEqual("Bearer token", calls[0][0].get_header("Authorization"))
        self.assertEqual(10.0, calls[0][1]["timeout"])
        for kwargs in ({"expected_revision": -1}, {"expected_revision": True},
                       {"expected_revision": 7, "lookback_seconds": 61}):
            with self.assertRaises(ValueError):
                client.check_ram_shadow_sources(**kwargs)
        self.assertEqual(1, len(calls))

    def test_ram_shadow_default_is_memory_only_and_recorded_read_is_explicit(self):
        calls = []
        def opener(request, **kwargs):
            calls.append((request, kwargs))
            return Response(b'{"live":null,"recorded":null}')
        client = CentralContentClient("https://nas.test", "token", opener=opener, timeout_seconds=30)
        client.load_ram_shadow()
        client.load_ram_shadow(include_recorded=True)
        client.load_health()
        self.assertTrue(calls[0][0].full_url.endswith("include_recorded=false"))
        self.assertTrue(calls[1][0].full_url.endswith("include_recorded=true"))
        self.assertEqual([10, 10, 30], [kwargs["timeout"] for _, kwargs in calls])
        self.assertEqual("Bearer token", calls[0][0].get_header("Authorization"))

    def test_historical_pc_claim_scopes_and_health(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            if request.full_url.endswith("/health"):
                return Response(b'{"historical_news_pc_scopes":["market","search","legacy_backlog"]}')
            return Response(b'{"job":null}')

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        self.assertIn("legacy_backlog", client.load_health()["historical_news_pc_scopes"])
        self.assertIsNone(client.claim_historical_news_job("BODY", scope="pc")["job"])
        self.assertIsNone(client.claim_historical_news_job("RULE", scope="pc_search")["job"])
        self.assertIn("scope=pc", requests[1].full_url)
        self.assertIn("scope=pc_search", requests[2].full_url)

    def test_upsert_and_load_use_authenticated_content_api(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            if request.method == "POST":
                return Response(json.dumps({"saved": 1}).encode())
            return Response(json.dumps({"documents": [{"key": "one"}]}).encode())

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        self.assertEqual(1, client.upsert("news_article", [{"owner": "005930", "key": "one", "document": {}}]))
        self.assertEqual("one", client.load("news_article", "005930")[0]["key"])
        self.assertEqual("Bearer token", requests[0].get_header("Authorization"))

    def test_load_all_reads_every_page(self) -> None:
        requested_urls: list[str] = []

        def opener(request, **_kwargs):
            requested_urls.append(request.full_url)
            offset = len(requested_urls) - 1
            documents = [{"key": str(offset)}] if offset < 2 else []
            return Response(json.dumps({"documents": documents}).encode())

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        loaded = client.load_all("news_article", page_size=1)

        self.assertEqual(["0", "1"], [value["key"] for value in loaded])
        self.assertIn("offset=2", requested_urls[-1])

    def test_http_error_keeps_safe_server_detail(self) -> None:
        def opener(_request, **_kwargs):
            raise HTTPError(
                "https://nas.test/api/v1/news/analyze", 429, "limited", {},
                io.BytesIO(json.dumps({"detail": "AI 공급자 호출 한도"}).encode()),
            )

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        with self.assertRaisesRegex(CentralContentHttpError, "HTTP 429 · AI 공급자 호출 한도") as caught:
            client.load("news_article")
        self.assertEqual(429, caught.exception.status_code)
        self.assertFalse(is_missing_collection_error(caught.exception))

    def test_http_404_is_explicitly_classified_as_missing_collection(self) -> None:
        def opener(_request, **_kwargs):
            raise HTTPError(
                "https://nas.test/api/v1/content/new", 404, "missing", {},
                io.BytesIO(json.dumps({"detail": "지원하지 않는 중앙 자료 종류입니다."}).encode()),
            )

        with self.assertRaises(CentralContentHttpError) as caught:
            CentralContentClient("https://nas.test", "token", opener=opener).load("new")
        self.assertEqual(404, caught.exception.status_code)
        self.assertTrue(is_missing_collection_error(caught.exception))

    def test_network_and_timeout_errors_are_not_classified_as_missing_collection(self) -> None:
        for error in (URLError("offline"), TimeoutError("slow")):
            def opener(_request, **_kwargs):
                raise error

            with self.subTest(error=type(error).__name__):
                with self.assertRaises(CentralContentUnavailableError) as caught:
                    CentralContentClient("https://nas.test", "token", opener=opener).load(
                        "journal_news_link"
                    )
                self.assertFalse(is_missing_collection_error(caught.exception))

    def test_load_theme_history_uses_as_of_without_content_projection_query(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"known": False, "snapshots": []}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_theme_history(as_of=123.5, limit=3)

        self.assertFalse(result["known"])
        self.assertIn("/api/v1/themes/history?", requests[0].full_url)
        self.assertIn("as_of=123.5", requests[0].full_url)
        self.assertIn("limit=3", requests[0].full_url)

    def test_load_news_history_uses_revision_endpoint(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"known": True, "revisions": [{"identity": "a"}]}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_news_history("article", target="005930", identity="a", as_of=123.5, limit=3,
                            before_revision_id="revision&2")

        self.assertTrue(result["known"])
        self.assertIn("/api/v1/news/history/article?", requests[0].full_url)
        self.assertIn("target=005930", requests[0].full_url)
        self.assertIn("identity=a", requests[0].full_url)
        self.assertIn("as_of=123.5", requests[0].full_url)
        self.assertIn("before_revision_id=revision%262", requests[0].full_url)

    def test_news_history_url_encodes_stock_and_article_identity(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"known": False, "revisions": []}).encode())

        CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_news_history(
            "article", target="A&B 종목", identity="https://news.test/a?x=1&y=한글",
        )

        self.assertIn("target=A%26B+%EC%A2%85%EB%AA%A9", requests[0].full_url)
        self.assertIn("identity=https%3A%2F%2Fnews.test%2Fa%3Fx%3D1%26y%3D%ED%95%9C%EA%B8%80", requests[0].full_url)

    def test_research_page_uses_fixed_watermark_and_cursor(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"watermark": "fixed", "observations": []}).encode())

        CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_research_observations_page(
            datetime(2026, 9, 12, tzinfo=timezone.utc),
            datetime(2026, 9, 13, tzinfo=timezone.utc),
            ("ranking", "top20_membership"), subject="5", watermark="fixed", cursor=100,
        )

        self.assertIn("/api/v1/research/observations?", requests[0].full_url)
        self.assertIn("watermark=fixed", requests[0].full_url)
        self.assertIn("cursor=100", requests[0].full_url)
        self.assertIn("kinds=ranking%2Ctop20_membership", requests[0].full_url)

    def test_candidate_page_uses_local_consumption_cursor(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"high_watermark": 8, "events": []}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_candidate_events(after_sequence=7, limit=50)

        self.assertEqual(8, result["high_watermark"])
        self.assertIn("/api/v1/research/candidates?", requests[0].full_url)
        self.assertIn("after_sequence=7", requests[0].full_url)
        self.assertIn("limit=50", requests[0].full_url)

    def test_mock_automation_spec_publication_preserves_frozen_documents(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"status": "saved", "orders_started": False}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).publish_mock_automation_spec(
            account_ref="account-ref", credential_profile_id="mock-profile",
            expected_binding_revision=3, shadow_event_id="shadow-event",
            forward_profile={"profile_id": "forward-1"},
            stage_revisions=[{"revision_id": str(index)} for index in range(3)],
            operating_spec={"spec_id": "spec-1"},
        )

        body = json.loads(requests[0].data.decode())
        self.assertEqual("saved", result["status"])
        self.assertEqual("POST", requests[0].method)
        self.assertTrue(requests[0].full_url.endswith("/api/v1/research/mock-automation-specs"))
        self.assertEqual("shadow-event", body["shadow_event_id"])
        self.assertEqual(["0", "1", "2"], [row["revision_id"] for row in body["stage_revisions"]])
        self.assertFalse(result["orders_started"])

    def test_mock_automation_candidate_list_is_account_and_profile_scoped(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"candidates": []}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_mock_automation_candidates(
            "account/ref", credential_profile_id="mock profile",
        )

        self.assertEqual([], result["candidates"])
        self.assertIn(
            "/api/v1/research/mock-automation-candidates/account%2Fref?"
            "credential_profile_id=mock+profile",
            requests[0].full_url,
        )

    def test_mock_automation_read_and_control_paths_are_account_scoped(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"ok": True}).encode())

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        client.load_mock_automation_specs("account/ref")
        client.load_mock_automation_status(
            "account/ref", credential_profile_id="mock profile",
        )
        client.start_mock_automation(
            account_ref="account-ref", credential_profile_id="mock-profile",
            spec_id="spec-1", expected_settings_revision=4, credential_revision=5,
        )
        client.stop_mock_automation(
            account_ref="account-ref", credential_profile_id="mock-profile",
            spec_id="spec-1", expected_control_revision=6, reason="user stop",
        )
        client.resume_mock_automation(
            account_ref="account-ref", credential_profile_id="mock-profile",
            spec_id="spec-1", expected_control_revision=7,
            expected_settings_revision=8, credential_revision=9, reason="user resume",
        )

        self.assertIn(
            "/api/v1/research/mock-automation-specs/account%2Fref", requests[0].full_url,
        )
        self.assertIn(
            "/api/v1/mock-automation/accounts/account%2Fref?credential_profile_id=mock+profile",
            requests[1].full_url,
        )
        self.assertEqual(
            ["/api/v1/mock-automation/start", "/api/v1/mock-automation/stop",
             "/api/v1/mock-automation/resume"],
            [request.full_url.removeprefix("https://nas.test") for request in requests[2:]],
        )
        self.assertEqual(6, json.loads(requests[3].data.decode())["expected_control_revision"])
        self.assertEqual(9, json.loads(requests[4].data.decode())["credential_revision"])


class NativeUnfilledPublicationClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from datetime import timedelta
        from unittest.mock import patch
        from tests.unit.test_entry_thesis import NativeUnfilledOutcomeTests
        from kiwoom_monitor.application.research_execution import SimulationExecutionConfig,SimulationCostModel
        # Both recorded availability and the native mock clock must use the same
        # explicit regular-session time, independent of when CI is executed.
        cls.mock_now=datetime(2026,9,21,2,tzinfo=timezone.utc)
        fixture_now=cls.mock_now
        class RepositoryClock(datetime):
            @classmethod
            def now(clock_cls,tz=None):
                return fixture_now.astimezone(tz) if tz else fixture_now.replace(tzinfo=None)
        with patch('kiwoom_monitor.infrastructure.persistence.research_repository.datetime',RepositoryClock):
            cls.native=NativeUnfilledOutcomeTests();cls.native.setUp()
            cls.addClassCleanup(cls.native.doCleanups)
            cls.native.cut=cls.mock_now+timedelta(minutes=5)
            cls.execution=SimulationExecutionConfig('next_tradable_bar_open/v1','conservative_with_optimistic_bound/v1',10_000_000,
                SimulationCostModel('fixed_bps/v1',2,18,5,'official_period_rule','fixture',
                    '2026-01-01T00:00:00+00:00','2027-01-01T00:00:00+00:00'))
            cls.selections=[cls.native.source(a,case=f'case-{m}',minute=m,execution_override=cls.execution)[0]
                            for m in (5,6) for a in ('WAIT','REPRICE')]

    def setUp(self):
        self.calls=[]
        def opener(request,**kwargs):
            self.calls.append(request);return Response(b'{"status":"accepted","order_authorized":false}')
        self.client=CentralContentClient('https://nas.test','token',opener=opener)
        from tests.unit.test_mock_automation_specification import ACCOUNT_REF,PROFILE_ID
        self.scope=dict(account_ref=ACCOUNT_REF,credential_profile_id=PROFILE_ID,spec_id='spec',execution_run_id='run',
            expected_control_revision=1,root_intent_id='root',managed_intent_id='root')

    def packet(self):
        return self.client.prepare_native_unfilled_population(self.native.path,self.selections,
            decision_time=self.native.cut,horizon_seconds=120,population_ref='native-population',symbol='005930',
            development_role='TRAIN',**self.scope)

    def test_native_packet_freezes_original_availability_and_posts_without_source_reread(self):
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        packet=self.packet()
        self.assertEqual(max(r['available_at'] for r in packet['outcomes']),packet['available_at'])
        with patch.object(ResearchRepository,'load_position_action_evidence',side_effect=AssertionError('duplicate source read')):
            for _ in range(3): self.client.publish_native_unfilled_population(packet)
        self.assertEqual(3,len(self.calls))
        self.assertEqual('POST',self.calls[0].method)
        self.assertEqual('Bearer token',self.calls[0].get_header('Authorization'))
        self.assertEqual('https://nas.test/api/v1/mock-automation/unfilled-population',self.calls[0].full_url)
        self.assertEqual(self.calls[0].data,self.calls[2].data)
        self.assertNotIn(str(self.native.path),self.calls[0].data.decode())

    def test_unavailable_source_or_wrong_role_never_posts(self):
        with self.assertRaises(ValueError):
            self.client.prepare_native_unfilled_population(self.native.path,self.selections,
                decision_time=self.native.cut,horizon_seconds=120,population_ref='native-population',symbol='005930',
                development_role='VALIDATION',**self.scope)
        self.assertEqual([],self.calls)

    def test_packet_roundtrip_and_permuted_native_selection_share_identity(self):
        from kiwoom_monitor.application.research_evaluation import unfilled_population_publication_from_dict
        packet=self.packet()
        alternate=self.client.prepare_native_unfilled_population(self.native.path,list(reversed(self.selections)),
            decision_time=self.native.cut,horizon_seconds=120,population_ref='native-population',symbol='005930',
            development_role='TRAIN',**self.scope)
        self.assertEqual(packet,alternate)
        rows,published=unfilled_population_publication_from_dict(packet)
        self.assertEqual(4,len(rows));self.assertEqual(published.isoformat(),packet['available_at'])

    def test_tampered_unknown_fields_or_source_authority_are_rejected_before_http(self):
        from copy import deepcopy
        from kiwoom_monitor.application.research_evaluation import _content_id
        for kind in ('value','extra','authority','state','missing_pair','publication_time','revision','mixed_model'):
            packet=deepcopy(self.packet())
            if kind=='value':packet['outcomes'][0]['gross_change_won']+=1
            elif kind=='extra':packet['research_database']='private.db'
            elif kind=='authority':packet['source_receipt']['source_durability']='verified'
            elif kind=='state':packet['outcomes'][0]['order_state']='PARTIALLY_FILLED'
            elif kind=='missing_pair':packet['outcomes']=packet['outcomes'][:-1]
            elif kind=='publication_time':packet['available_at']='2099-01-01T00:00:00+00:00'
            elif kind=='mixed_model':packet['source_receipt']['sources'][0]['policy_version']='different-native-policy'
            else:packet['expected_control_revision']=True
            if kind not in {'value','extra'}:
                packet['publication_ref']=_content_id('unfilled-native-publication',{k:v for k,v in packet.items() if k!='publication_ref'})
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.client.publish_native_unfilled_population(packet)
        self.assertEqual([],self.calls)


class NativeUnfilledPublicationApiTests(unittest.IsolatedAsyncioTestCase):
    setUpClass=classmethod(NativeUnfilledPublicationClientTests.setUpClass.__func__)

    async def asyncSetUp(self):
        from datetime import timedelta
        from dataclasses import replace
        from types import SimpleNamespace
        from unittest.mock import patch
        import tests.unit.test_mock_automation_specification as fixture
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode
        from kiwoom_monitor.central_server.mock_automation_routes import create_mock_automation_router
        from fastapi import FastAPI,HTTPException,Header
        import httpx
        self.case=fixture.MockUnfilledPairedInputTests()
        self.case.unfilled_execution_policy=replace(self.case.unfilled_execution_policy,reprice_horizon_seconds=120)
        with patch.object(fixture,'NOW',self.mock_now-timedelta(hours=2)):
            self.case.setUp()
        await self.case.ready(None)
        self.case.bundle.runtime_mode=MockAccountRuntimeMode.AUTOMATIC
        self.case.bundle.automation_context.control_revision=1
        record=self.case.execution.load(self.case.intent.intent_id)
        gate=self.case.repository.load_mock_automation_approved_gate(fixture.ACCOUNT_REF,record.intent.intent_id)
        self.case.runner._register_native_unfilled(record,gate)
        self.owner=SimpleNamespace(bundle=lambda profile:self.case.bundle if profile==fixture.PROFILE_ID else None)
        self.supervisor=MockAutomationSupervisor(self.case.store,self.owner)
        self.supervisor._runners[fixture.ACCOUNT_REF]=self.case.runner
        async def authorize(authorization: str=Header(default='')):
            if authorization!='Bearer token':raise HTTPException(401,'UNAUTHORIZED')
        app=FastAPI();app.include_router(create_mock_automation_router(self.supervisor,authorize))
        self.http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline',headers={'Authorization':'Bearer token'})
        self.packet=CentralContentClient('https://nas.test','token').prepare_native_unfilled_population(
            self.native.path,self.selections,decision_time=self.native.cut,horizon_seconds=120,
            population_ref='native-population',symbol='005930',development_role='TRAIN',account_ref=fixture.ACCOUNT_REF,
            credential_profile_id=fixture.PROFILE_ID,spec_id=self.case.spec.spec_id,execution_run_id=self.case.run_id,
            expected_control_revision=1,root_intent_id=self.case.intent.intent_id,managed_intent_id=self.case.intent.intent_id)

    async def asyncTearDown(self):
        import inspect
        await self.http.aclose();await self.supervisor.close()
        while self.case._cleanups:
            fn,args,kwargs=self.case._cleanups.pop();value=fn(*args,**kwargs)
            if inspect.isawaitable(value):await value

    async def post(self, packet=None, **kwargs):
        return await self.http.post('/api/v1/mock-automation/unfilled-population',json=self.packet if packet is None else packet,**kwargs)

    def reseal(self, packet):
        from kiwoom_monitor.application.research_evaluation import _content_id
        packet['publication_ref']=_content_id('unfilled-native-publication',{k:v for k,v in packet.items() if k!='publication_ref'})
        return packet

    async def test_real_native_pc_packet_reaches_existing_runner_ram_without_orders_or_db(self):
        import asyncio
        self.case.runner._wake_loop=asyncio.get_running_loop();self.case.runner._wake_event=asyncio.Event()
        before=len(self.case.transport.calls)
        with self.case.count_native() as counts:response=await self.post()
        await asyncio.sleep(0)
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual('accepted',response.json()['status'])
        self.assertFalse(response.json()['order_authorized'])
        self.assertEqual((1,0),(counts['selects'],counts['commits']))
        self.assertTrue(self.case.runner._wake_event.is_set())
        self.assertEqual(before,len(self.case.transport.calls));self.assertEqual(0,self.case.cancel_calls)
        source=self.case.runner.status['unfilled_population_source']
        self.assertEqual(self.packet['available_at'],source['available_at'])
        self.assertEqual('PC_READONLY_REPLAY_REPORTED',source['native_validation'])

    async def test_duplicate_http_packet_preserves_one_population_and_runs_no_source_read(self):
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        first=await self.post();self.assertEqual(200,first.status_code,first.text)
        original=self.case.runner._unfilled_population;generation=self.case.runner._unfilled_source_generation
        with patch.object(ResearchRepository,'load_position_action_evidence',side_effect=AssertionError('server accessed PC source')):
            with self.case.count_native() as counts:
                for _ in range(100):
                    response=await self.post();self.assertEqual('unchanged',response.json()['status'])
        self.assertIs(original,self.case.runner._unfilled_population)
        self.assertEqual(generation,self.case.runner._unfilled_source_generation)
        self.assertEqual((0,0),(counts['selects'],counts['commits']))

    async def test_auth_size_and_malformed_body_fail_without_publication(self):
        from unittest.mock import patch
        with patch.object(self.supervisor,'publish_native_unfilled_population',side_effect=AssertionError('invalid reached supervisor')):
            response=await self.post(headers={'Authorization':'Bearer wrong'});self.assertEqual(401,response.status_code)
            response=await self.http.post('/api/v1/mock-automation/unfilled-population',content=b'x'*(1024*1024+1));self.assertEqual(413,response.status_code)
            response=await self.http.post('/api/v1/mock-automation/unfilled-population',content=b'{');self.assertEqual(400,response.status_code)
        self.assertIsNone(self.case.runner._unfilled_population)

    async def test_malformed_outcome_structure_is_bad_request_and_not_a_server_error(self):
        from copy import deepcopy
        for change in ('missing','extra','nonobject'):
            packet=deepcopy(self.packet)
            if change=='missing':packet['outcomes'][0].pop('completed_at')
            elif change=='extra':packet['outcomes'][0]['unexpected']=1
            else:packet['outcomes'][0]=None
            self.reseal(packet)
            response=await self.post(packet)
            self.assertEqual(400,response.status_code,response.text)
        self.assertIsNone(self.case.runner._unfilled_population)

    async def test_control_spec_run_and_profile_conflicts_do_not_activate_or_store(self):
        from copy import deepcopy
        from unittest.mock import patch
        for key,value in (('expected_control_revision',2),('spec_id','other'),('execution_run_id','other'),('credential_profile_id','other')):
            packet=deepcopy(self.packet);packet[key]=value;self.reseal(packet)
            with self.subTest(key=key),patch.object(self.supervisor,'activate',side_effect=AssertionError('activated')):
                response=await self.post(packet)
            self.assertIn(response.status_code,(409,503),response.text)
            self.assertIsNone(self.case.runner._unfilled_population)

    async def test_native_stop_rejects_a_new_packet_before_runner_poll_updates_status(self):
        from tests.unit.test_mock_automation_specification import ACCOUNT_REF,PROFILE_ID
        await self.supervisor.stop(account_ref=ACCOUNT_REF,credential_profile_id=PROFILE_ID,spec_id=self.case.spec.spec_id,
            expected_control_revision=1,reason='explicit stop fixture')
        response=await self.post()
        self.assertEqual(409,response.status_code,response.text)
        self.assertIsNone(self.case.runner._unfilled_population)

    async def test_expired_source_is_not_renewed_by_new_account_cut_or_http_retry(self):
        from datetime import timedelta
        self.case.clock+=timedelta(seconds=6)
        await self.case.monitor.refresh_recovery()
        response=await self.post();self.assertEqual(400,response.status_code,response.text)
        self.assertIsNone(self.case.runner._unfilled_population)

    async def test_partial_or_cancelled_current_native_state_has_explicit_ev_unavailable(self):
        from dataclasses import replace
        from kiwoom_monitor.domain.order_contract import OrderState
        response=await self.post();self.assertEqual(200,response.status_code,response.text)
        record=self.case.execution.load(self.case.intent.intent_id)
        population=self.case.runner._unfilled_population
        for state in (OrderState.PARTIALLY_FILLED,OrderState.CANCELLED,OrderState.SUBMISSION_UNKNOWN):
            evidence,report=self.case.runner._prepare_native_unfilled_evidence(population,None,replace(record,state=state),
                10,0,10,1100,None,self.case.clock)
            self.assertIsNone(evidence);self.assertEqual('native_unfilled_model_state_unsupported',report['reason'])
        self.assertEqual(0,self.case.cancel_calls)

    async def test_published_runtime_outcomes_reach_existing_pair_calculation_without_synthetic_profit(self):
        response=await self.post();self.assertEqual(200,response.status_code,response.text)
        record=self.case.execution.load(self.case.intent.intent_id)
        policy=self.case.runner._validate_unfilled_registration(self.case.runner._unfilled_registration)
        evidence,report=self.case.runner._prepare_native_unfilled_evidence(self.case.runner._unfilled_population,policy,
            record,10,0,10,1100,self.case.runner._unfilled_cost_model,self.case.clock)
        self.assertEqual((2,-12),(evidence.paired_sample_count,evidence.net_advantage_won))
        self.assertEqual('EMPIRICAL_ESTIMATE',report['comparison']['ev_status'])
        self.assertFalse(report['order_authorized'])
        self.assertEqual(0,self.case.cancel_calls)

    async def test_registration_switch_during_encoding_rejects_old_episode_packet(self):
        from copy import deepcopy
        from unittest.mock import patch
        import kiwoom_monitor.central_server.mock_automation_runner as module
        original=module.json.dumps;changed=False
        def interleave(value,*args,**kwargs):
            nonlocal changed
            if isinstance(value,dict) and 'candidate_price' in value and 'source_publication' in value and not changed:
                changed=True
                with self.case.runner._unfilled_input_lock:
                    self.case.runner._unfilled_registration=deepcopy(self.case.runner._unfilled_registration)
            return original(value,*args,**kwargs)
        with patch.object(module.json,'dumps',side_effect=interleave):response=await self.post()
        self.assertTrue(changed);self.assertEqual(400,response.status_code,response.text)
        self.assertIn('registration_changed',response.text);self.assertIsNone(self.case.runner._unfilled_population)

    async def test_protection_request_does_not_wait_for_source_decode_and_close_blocks_late_publish(self):
        import asyncio
        from unittest.mock import patch
        import kiwoom_monitor.central_server.observation_frame_recovery as frame
        entered=asyncio.Event();release=asyncio.Event();original=frame.drain_thread_call
        async def slow(function,*args,**kwargs):
            if function.__name__=='unfilled_population_publication_from_dict':
                entered.set();await release.wait()
            return await original(function,*args,**kwargs)
        with patch.object(frame,'drain_thread_call',side_effect=slow):
            posting=asyncio.create_task(self.post())
            await asyncio.wait_for(entered.wait(),2)
            self.case.runner.request_protection_review(reason='hard_risk_limit',input_ref='native-risk',requested_at=self.case.clock)
            await self.case.runner.run_once()
            self.assertTrue(self.case.runner._protection_pending())
            self.assertIsNone(self.case.runner._unfilled_population)
            await self.supervisor.close();release.set()
            response=await asyncio.wait_for(posting,2)
        self.assertEqual(503,response.status_code,response.text)


if __name__ == "__main__":
    unittest.main()

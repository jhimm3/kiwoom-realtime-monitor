from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
import httpx
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from tests.unit.historical_news_test_support import historical_news_archive_fixture, seal_historical_news_archive_fixture

def fixed_archive_contract_fixture(directory):
    """Freeze inputs before serving HTTP; never normalize returned IDs or times."""
    with patch('uuid.uuid4', side_effect=(uuid.UUID(int=i) for i in range(1, 1000))), patch('time.time', return_value=1790300000.0), patch('kiwoom_monitor.central_server.database_news_revisions.time', return_value=1790300000.0), patch('kiwoom_monitor.central_server.database_news_sources.time', return_value=1790300000.0), patch('kiwoom_monitor.central_server.database_news_jobs.time', return_value=1790300000.0):
        archive = historical_news_archive_fixture(directory)
        seal_historical_news_archive_fixture(archive)
    return archive

async def capture_archive_http_contract(client):
    """Same requests for the original loopback baseline and ASGI regression."""
    search = '/api/v1/news/historical-archive/search'
    headers = {'Authorization': 'Bearer test-token'}
    cases = []

    async def capture(name, url, params, expected_status, *, authenticated=True):
        response = await client.get(url, params=params, headers=headers if authenticated else {})
        if response.status_code != expected_status:
            raise AssertionError(f'{name}: {response.status_code} != {expected_status}')
        cases.append({'name': name, 'request': {'url': url, 'params': params, 'authenticated': authenticated}, 'response': {'status': response.status_code, 'headers': {key: response.headers[key] for key in ('content-type', 'content-length')}, 'body': response.json()}})
        return response.json()
    first = await capture('first-page', search, {'stock_code': '005930', 'limit': 1}, 200)
    await capture('next-page', search, {'stock_code': '005930', 'limit': 1, 'cursor': first['next_cursor']}, 200)
    await capture('empty-page', search, {'stock_code': '000660'}, 200)
    await capture('search-unauthorized', search, {'stock_code': '005930'}, 401, authenticated=False)
    await capture('invalid-stock', search, {'stock_code': 'bad'}, 422)
    await capture('invalid-limit', search, {'stock_code': '005930', 'limit': 0}, 422)
    await capture('invalid-cursor', search, {'stock_code': '005930', 'cursor': 'invalid'}, 400)
    article = first['items'][0]
    detail = '/api/v1/news/historical-archive/articles/' + article['article_revision_id']
    dataset = {'dataset_id': first['dataset_id']}
    await capture('article', detail, {**dataset, 'body_revision_id': article['body_revision_id']}, 200)
    await capture('article-unauthorized', detail, dataset, 401, authenticated=False)
    await capture('missing-article', '/api/v1/news/historical-archive/articles/missing', dataset, 404)
    await capture('changed-dataset', detail, {'dataset_id': 'older-generation'}, 409)
    await capture('missing-dataset', detail, {}, 422)
    await capture('wrong-body', detail, {**dataset, 'body_revision_id': 'missing'}, 400)
    return cases

class HistoricalNewsArchiveApiTests(unittest.IsolatedAsyncioTestCase):

    async def test_archive_http_responses_match_pre_extraction_baseline(self) -> None:
        baseline = Path(__file__).resolve().parents[1] / 'fixtures/api_contract_baselines/historical_archive_http_v1.json'
        expected = json.loads(baseline.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            archive = fixed_archive_contract_fixture(directory)
            before = hashlib.sha256(archive.read_bytes()).hexdigest()
            settings = CentralServerSettings(f"sqlite:///{directory / 'monitor.sqlite3'}", 'test-token', historical_news_archive_path=str(archive))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)), base_url='http://offline') as client:
                self.assertEqual(expected['cases'], await capture_archive_http_contract(client))
            self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())

    async def test_unconfigured_or_unsealed_archive_does_not_change_existing_capability(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            archive = historical_news_archive_fixture(directory)
            for configured in (False, True):
                settings = CentralServerSettings(f"sqlite:///{directory / 'monitor.sqlite3'}", 'test-token', historical_news_archive_path=str(archive) if configured else '')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)), base_url='http://offline') as client:
                    headers = {'Authorization': 'Bearer test-token'}
                    health = (await client.get('/health')).json()
                    self.assertEqual('unavailable' if configured else 'unconfigured', health['historical_news_archive']['state'])
                    capabilities = (await client.get('/api/v1/capabilities', headers=headers)).json()
                    self.assertTrue(capabilities['capabilities']['news_archive'])
                    self.assertFalse(capabilities['capabilities']['historical_news_archive_v1'])
                    response = await client.get('/api/v1/news/historical-archive/search', params={'stock_code': '005930'}, headers=headers)
                    self.assertEqual(503, response.status_code)

    async def test_sealed_archive_api_is_authenticated_read_only_and_diagnostic_gated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            archive = historical_news_archive_fixture(directory)
            seal_historical_news_archive_fixture(archive)
            before = hashlib.sha256(archive.read_bytes()).hexdigest()
            settings = CentralServerSettings(f"sqlite:///{directory / 'monitor.sqlite3'}", 'test-token', historical_news_archive_path=str(archive))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)), base_url='http://offline') as client:
                endpoint = '/api/v1/news/historical-archive/search'
                headers = {'Authorization': 'Bearer test-token'}
                self.assertEqual(401, (await client.get(endpoint, params={'stock_code': '005930'})).status_code)
                capabilities = (await client.get('/api/v1/capabilities', headers=headers)).json()
                self.assertTrue(capabilities['capabilities']['historical_news_archive_v1'])
                self.assertEqual('fixture-1', capabilities['historical_news_archive_dataset_id'])
                first = await client.get(endpoint, params={'stock_code': '005930', 'limit': 1}, headers=headers)
                self.assertEqual(200, first.status_code)
                control = directory / 'diagnostic-control.json'
                now = time.time()
                payload = {'schema': 1, 'instance_id': instance_id(), 'diagnostic_tool': {'expires_at': now + 60, 'owner': 'test', 'session_id': 'test-session'}, 'leases': {'historical_news_archive': now + 60}}
                control.write_text(json.dumps(payload), encoding='utf-8')
                with patch.dict('os.environ', {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(control)}):
                    paused = await client.get(endpoint, params={'stock_code': '005930'}, headers=headers)
                    self.assertEqual(503, paused.status_code)
                    self.assertEqual('HISTORICAL_NEWS_ARCHIVE_PAUSED', paused.json()['detail'])
                    status = (await client.get('/api/v1/diagnostics/workloads', headers=headers)).json()
                    self.assertTrue(status['workloads']['historical_news_archive']['paused_by_diagnostic'])
                    payload['diagnostic_tool']['expires_at'] = now - 1
                    control.write_text(json.dumps(payload), encoding='utf-8')
                    resumed = await client.get(endpoint, params={'stock_code': '005930'}, headers=headers)
                    self.assertEqual(200, resumed.status_code)
            self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())
if __name__ == '__main__':
    unittest.main()

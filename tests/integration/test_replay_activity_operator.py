"""Explicit fixture bootstrap only in a fresh, labelled operator test container."""
import json
import os
from pathlib import Path
import re
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch
from uuid import uuid4

import psycopg
from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from . import test_recorded_execution_postgres as recorded


@unittest.skipUnless(Path('/run/operator/request.json').is_file(),
                     'explicit owned operator replay fixture required; not a local PostgreSQL gate')
class ReplayActivityOperatorTests(recorded.RecordedExecutionPostgresTests):
    @classmethod
    def setUpClass(cls):
        request = json.loads(Path('/run/operator/request.json').read_text())
        parts = urlsplit(os.environ['KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL'])
        job = request.get('job_id', '')
        if (os.getuid() == 0 or request.get('command') != 'test'
                or not re.fullmatch('[a-f0-9]{32}', job)
                or parts.hostname != '127.0.0.1' or parts.port != 5432
                or parts.username != 'kiwoom_operator_fixture'
                or parts.path != '/kiwoom_monitor_diagnostic_test'
                or not re.fullmatch('[a-f0-9]{64}', parts.password or '')):
            raise RuntimeError('activity_fixture_requires_owned_operator_test')
        with psycopg.connect(parts.geturl(), autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('kiwoom.replay_fixture_id')")
                if cursor.fetchone() != (job,):
                    raise RuntimeError('activity_fixture_job_identity_mismatch')
        # These two fixed roles were created by the installed worker with one
        # temporary password. This is never derived from an operating DB URL.
        url = f'postgresql://{baseline.ROLE_NAME}:{parts.password}@127.0.0.1:5432/{baseline.DATABASE_NAME}'
        token = uuid4().hex
        baseline.provision_existing_empty_database(url, token)
        with baseline.ReplayDatabaseLease(url, token) as lease:
            lease.seal()
        env = patch.dict(os.environ, KIWOOM_REPLAY_DATABASE_URL=url, KIWOOM_REPLAY_OWNER_TOKEN=token)
        env.start()
        cls.addClassCleanup(env.stop)
        # The operator's /tmp is a bounded 256MiB tmpfs, below trace's
        # 256MiB-free admission threshold once any fixture exists. Keep real
        # quota checks intact and put class-owned temporary files in this
        # job's writable directory; TemporaryDirectory still removes them.
        temporary = patch('tempfile.tempdir', '/run/operator')
        temporary.start()
        cls.addClassCleanup(temporary.stop)

"""Same independent authority oracles on a dedicated real PostgreSQL schema."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import unittest
from contextlib import closing
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.domain.news_observation import stable_document_hash
from tests.unit.test_ai_request_authority import AIRequestAuthorityTests as _SQLiteAuthority


class AIRequestAuthorityPostgresTests(_SQLiteAuthority):
    native_rejection_error = psycopg.errors.RaiseException
    settlement_error_type = "RaiseException"
    previous_schema_version = 21

    @classmethod
    def setUpClass(cls):
        cls.test_url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not cls.test_url:
            raise RuntimeError("dedicated PostgreSQL test URL is required")
        if urlsplit(cls.test_url).path.lstrip("/") != "kiwoom_monitor_diagnostic_test":
            raise RuntimeError("refusing AI authority tests outside dedicated diagnostic DB")

    def _make_store(self, path):
        schema = "ar06_" + uuid4().hex
        with psycopg.connect(self.test_url) as connection:
            self.assertEqual("kiwoom_monitor_diagnostic_test", connection.execute("SELECT current_database()").fetchone()[0])
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        def drop_schema():
            with psycopg.connect(self.test_url) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                self.assertEqual((None,), connection.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone())
        self.addCleanup(drop_schema)
        return PostgresQueryStore(make_conninfo(self.test_url, options=f"-c search_path={schema}"),
                                  now_provider=lambda: self.clock)

    def _peer(self):
        return PostgresQueryStore(self.store._database_url, now_provider=lambda: self.clock)

    def _sql(self, statement, parameters=()):
        with closing(self.store._connect()) as connection, connection.cursor() as cursor:
            cursor.execute(statement.replace("?", "%s"), parameters)
            rows = cursor.fetchall() if cursor.description else []
            connection.commit()
            return rows

    def _reject_native(self, name, table, event, condition, message):
        self._sql(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                  f"BEGIN RAISE EXCEPTION '{message}'; END $$")
        self._sql(f"CREATE TRIGGER {name} BEFORE {event} ON {table} FOR EACH ROW "
                  f"WHEN ({condition}) EXECUTE FUNCTION {name}()")

    async def test_pg_overlapping_old_body_writer_waits_for_matching_commit_and_reads_fresh_current(self):
        old_job = self._seed()
        old_body = self.store.load_news_body_revision(old_job["payload"]["body_revision_id"])
        old_prepared, release_old = asyncio.Event(), asyncio.Event()
        self.gates.append(release_old)
        native_publish = self.service._publish_direct
        observations = {}
        older = newer = None
        gate = int(uuid4().hex[:15], 16)
        current_key = "news_ai_current:6:005930same-identity"
        schema = self._sql("SELECT current_schema()")[0][0]
        self.assertTrue(schema.startswith("ar06_"))
        # Map the documented product key to its actual PG lock tag. No lock-tag
        # arithmetic, mock cursor, or assumed backend PID substitutes for PG.
        with closing(psycopg.connect(self.store._database_url)) as probe:
            probe.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (current_key,))
            tags = probe.execute("SELECT classid::bigint,objid::bigint,objsubid FROM pg_locks "
                "WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted").fetchall()
            self.assertEqual(1, len(tags))
            current_tag = tags[0]
            probe.rollback()

        async def hold_old(stock, prepared, outcome):
            if prepared[0]["body_revision_id"] == old_body["body_revision_id"]:
                old_prepared.set()
                await release_old.wait()
            return await native_publish(stock, prepared, outcome)

        with closing(psycopg.connect(self.store._database_url)) as control:
            control_pid = control.info.backend_pid
            control.execute("SELECT pg_advisory_xact_lock(%s)", (gate,))
            try:
                with patch.object(self.service, "_publish_direct", side_effect=hold_old):
                    older = self._task(self.service.analyze("005930", "삼성전자", "gemini", "model",
                        [self._manual_event(old_job)], 1))
                    await asyncio.wait_for(old_prepared.wait(), 2)
                    newer_job = self._next_body_job(old_job, "new native BODY while old result awaits publication")
                    new_body_id = newer_job["payload"]["body_revision_id"]
                    self._sql("CREATE TABLE c06_overlap_probe (seq BIGSERIAL PRIMARY KEY, "
                        "backend_pid INTEGER NOT NULL, txid BIGINT NOT NULL, isolation TEXT NOT NULL, body_id TEXT)")
                    self._sql(sql.SQL("CREATE FUNCTION c06_overlap_gate() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "DECLARE projection_body TEXT; BEGIN "
                        "IF NEW.collection='news_ai' AND NEW.owner='005930' AND NEW.document_key='same-identity' THEN "
                        "projection_body := ((NEW.document_json::jsonb ->> 'provenance_json')::jsonb ->> 'body_revision_id'); "
                        "INSERT INTO c06_overlap_probe(backend_pid,txid,isolation,body_id) VALUES "
                        "(pg_backend_pid(),txid_current(),current_setting('transaction_isolation'),projection_body); "
                        "IF projection_body={body} THEN PERFORM pg_advisory_xact_lock({gate}); END IF; "
                        "END IF; RETURN NEW; END $$").format(body=sql.Literal(new_body_id), gate=sql.Literal(gate)).as_string())
                    self._sql("CREATE TRIGGER c06_overlap_gate BEFORE INSERT ON central_documents "
                        "FOR EACH ROW EXECUTE FUNCTION c06_overlap_gate()")
                    newer = self._task(self.service.analyze("005930", "삼성전자", "gemini", "model",
                        [self._manual_event(newer_job)], 1))

                    def waiting_on(blocker, target, task):
                        if task.done():
                            task.result()  # Retain an actual native exception before a wait assertion.
                            self.fail("publication returned before the controlled native wait")
                        rows = self._sql("SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid),query "
                            "FROM pg_stat_activity WHERE datname=current_database() AND ?=ANY(pg_blocking_pids(pid))",
                            (blocker,))
                        if len(rows) == 1:
                            observations[target] = rows[0]
                            return True
                        return False

                    await self._until(lambda: waiting_on(control_pid, "newer_wait", newer))
                    new_pid = observations["newer_wait"][0]
                    self.assertEqual(("Lock", "advisory"), observations["newer_wait"][1:3])
                    self.assertIn("INSERT INTO central_documents", observations["newer_wait"][4])
                    self.assertNotEqual(control_pid, new_pid)
                    self.assertEqual([(True,)], self._sql("SELECT granted FROM pg_locks WHERE pid=? "
                        "AND locktype='advisory' AND classid=?::oid AND objid=?::oid AND objsubid=?",
                        (new_pid, *current_tag)))
                    # The trigger is at projection INSERT, after the actual
                    # product advisory key has been acquired; it is not a gate
                    # before immutable insertion misreported as product locking.
                    self.assertIsNone(self.store.load_document("news_ai", "005930", "same-identity"))
                    release_old.set()
                    await self._until(lambda: waiting_on(new_pid, "older_wait", older))
                    old_pid = observations["older_wait"][0]
                    self.assertNotEqual(new_pid, old_pid)
                    self.assertEqual(("Lock", "advisory"), observations["older_wait"][1:3])
                    self.assertIn("pg_advisory_xact_lock", observations["older_wait"][4])
                    self.assertEqual([(False,)], self._sql("SELECT granted FROM pg_locks WHERE pid=? "
                        "AND locktype='advisory' AND classid=?::oid AND objid=?::oid AND objsubid=?",
                        (old_pid, *current_tag)))
                    self.assertEqual([], self.store.load_news_history("ai", target="005930"))
                    control.commit()
                    new_response, old_response = await asyncio.gather(newer, older)
            finally:
                release_old.set()
                control.rollback()
                await asyncio.gather(*(t for t in (newer, older) if t is not None), return_exceptions=True)
                print("c06_pg_overlap_wait_receipt=" + json.dumps({"control_pid": control_pid,
                    "product_key": current_key, "product_tag": current_tag, "waits": observations}, default=str))

        probe_rows = self._sql("SELECT backend_pid,txid,isolation,body_id FROM c06_overlap_probe ORDER BY seq")
        self.assertEqual([new_pid, old_pid], [row[0] for row in probe_rows])
        self.assertEqual(2, len({row[1] for row in probe_rows}))
        self.assertEqual(["read committed", "read committed"], [row[2] for row in probe_rows])
        self.assertEqual([new_body_id, new_body_id], [row[3] for row in probe_rows])
        current = self.store.load_document("news_ai", "005930", "same-identity")
        current_ref = json.loads(current["document"]["provenance_json"])
        self.assertEqual(new_response["analysis_revision_ids"][0], current_ref["analysis_revision_id"])
        self.assertEqual(new_body_id, current_ref["body_revision_id"])
        self.assertNotEqual(old_response["analysis_revision_ids"], new_response["analysis_revision_ids"])
        raw_results = self.store.load_news_history("ai", target="005930")
        by_ref = {row["analysis_revision_id"]: row for row in raw_results}
        self.assertEqual(set(old_response["analysis_revision_ids"] + new_response["analysis_revision_ids"]), set(by_ref))
        self.assertEqual(old_body["body_revision_id"], by_ref[old_response["analysis_revision_ids"][0]]["body_revision_id"])
        self.assertEqual(new_body_id, by_ref[new_response["analysis_revision_ids"][0]]["body_revision_id"])
        self.assertEqual({self.clock.timestamp()}, {row["available_at"] for row in raw_results})
        self.assertEqual(old_body, self.store.load_news_body_revision(old_body["body_revision_id"]))
        inferences = self.store.load_news_history("inference", target="005930")
        self.assertEqual(2, len(inferences))
        raw_inputs = {row["document"]["inputs"][0]["body_revision_id"]:
            row["document"]["inputs"][0] for row in inferences}
        self.assertEqual({old_body["body_revision_id"], new_body_id}, set(raw_inputs))
        self.assertEqual(old_body["body_text"], raw_inputs[old_body["body_revision_id"]]["body"])
        self.assertEqual("new native BODY while old result awaits publication", raw_inputs[new_body_id]["body"])
        for inference in inferences:
            self.assertEqual(stable_document_hash(inference["document"]), inference["content_hash"])
        self.assertEqual(2, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertEqual(2, len(self.calls))
        print("c06_pg_overlap_raw_receipt=" + json.dumps({"probe_rows": probe_rows,
            "current": current, "immutable_results": raw_results, "raw_inferences": inferences,
            "old_receipt": old_response, "new_receipt": new_response}, ensure_ascii=False, default=str))

    async def test_pg_reversed_multi_key_writers_acquire_sorted_advisory_keys_before_projection(self):
        from kiwoom_monitor.domain.news_ai_contract import (
            analysis_body_hash, decode_news_ai_input_binding, freeze_news_ai_input, news_ai_source_member,
        )

        owner = "c06-sort-" + uuid4().hex
        frozen_inputs, native_bodies = {}, {}
        for identity in ("a", "z"):
            self.store.upsert_documents("news_article", [{"owner": owner, "key": identity,
                "collector_id": "sorted-lock-oracle", "collection_scope": "watchlist", "document": {
                    "stock_code": owner, "stock_name": "sorted native inputs", "identity": identity,
                    "title": "sorted source " + identity, "description": "original source " + identity,
                    "link": "https://fixture.invalid/" + owner + "/" + identity,
                    "published_at": "2026-10-23T08:00:00+09:00",
                }}])
            article = self.store.load_news_history("article", target=owner, identity=identity)[0]
            body_ref = self.store.save_news_body_revision({
                "article_revision_id": article["article_revision_id"], "status": "fulltext",
                "body_text": "actual sorted native BODY " + identity, "extractor_version": "sorted-oracle-v1",
                "fetched_at": self.clock.timestamp(),
            })
            body = self.store.load_news_body_revision(body_ref)
            native_bodies[identity] = body
            receipt = {name: body[name] for name in ("body_revision_id", "article_revision_id", "content_hash",
                "extractor_version", "fetched_at", "available_at", "status")}
            text, title = body["body_text"], article["document"]["title"]
            member = news_ai_source_member(0, article["document"], text, body["status"],
                input_text=text, span=(0, len(text)), observed_at=self.clock.timestamp(),
                clock_source="injected_business_clock", native_body=receipt)
            frozen = freeze_news_ai_input(identity, title, text, analysis_body_hash("sorted native inputs", text),
                [member], kind="article", article_revision_id=article["article_revision_id"], body_revision_id=body_ref)
            self.assertEqual(frozen["input_binding"], decode_news_ai_input_binding(frozen["input_binding"]))
            self.assertEqual(body["content_hash"], member["source_body_hash"])
            self.assertEqual(article["article_revision_id"], body["article_revision_id"])
            frozen_inputs[identity] = frozen
        key_a = f"news_ai_current:{len(owner)}:{owner}a"
        key_z = f"news_ai_current:{len(owner)}:{owner}z"
        self.assertLess(key_a, key_z)
        first_label, second_label = "c06-first-" + uuid4().hex, "c06-second-" + uuid4().hex
        first = PostgresQueryStore(make_conninfo(self.store._database_url, application_name=first_label),
            now_provider=lambda: self.clock)
        second = PostgresQueryStore(make_conninfo(self.store._database_url, application_name=second_label),
            now_provider=lambda: self.clock)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first_entered, second_entered = threading.Event(), threading.Event()
        observations = {}
        one = two = None
        with closing(psycopg.connect(self.store._database_url)) as probe:
            probe.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key_a,))
            a_tags = probe.execute("SELECT classid::bigint,objid::bigint,objsubid FROM pg_locks "
                "WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted").fetchall()
            self.assertEqual(1, len(a_tags))
            a_tag = a_tags[0]
            probe.rollback()

        def publish(store, entered, identities, summary):
            documents = []
            for identity in identities:
                document = {"summary": summary}
                documents.append({"owner": owner, "key": identity, "document": document,
                    "input_provenance": {"status": "UNKNOWN", "output_hash": stable_document_hash(document),
                        "input_binding": frozen_inputs[identity]["input_binding"]}})
            entered.set()
            return store.save_news_ai_results(documents, [], [])

        def held_by(pid, tag):
            return self._sql("SELECT granted FROM pg_locks WHERE pid=? AND locktype='advisory' "
                "AND classid=?::oid AND objid=?::oid AND objsubid=?", (pid, *tag))

        with closing(psycopg.connect(self.store._database_url)) as control:
            control_pid = control.info.backend_pid
            control.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key_z,))
            z_tags = control.execute("SELECT classid::bigint,objid::bigint,objsubid FROM pg_locks "
                "WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted").fetchall()
            self.assertEqual(1, len(z_tags))
            z_tag = z_tags[0]
            self.assertNotEqual(a_tag, z_tag)
            try:
                one = self._task(asyncio.to_thread(publish, first, first_entered, ("z", "a"), "first-commit"))
                await self._until(first_entered.is_set)

                def wait_for_writer(label, blocker, target, task):
                    if task.done():
                        task.result()
                        self.fail("native batch returned before the controlled advisory wait")
                    rows = self._sql("SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid),query "
                        "FROM pg_stat_activity WHERE application_name=? AND ?=ANY(pg_blocking_pids(pid))",
                        (label, blocker))
                    if len(rows) == 1:
                        observations[target] = rows[0]
                        return True
                    return False

                await self._until(lambda: wait_for_writer(first_label, control_pid, "first_wait", one))
                first_pid = observations["first_wait"][0]
                self.assertEqual(("Lock", "advisory"), observations["first_wait"][1:3])
                self.assertEqual([(True,)], held_by(first_pid, a_tag))
                self.assertEqual([(False,)], held_by(first_pid, z_tag))
                two = self._task(asyncio.to_thread(publish, second, second_entered, ("a", "z"), "second-commit"))
                await self._until(second_entered.is_set)
                await self._until(lambda: wait_for_writer(second_label, first_pid, "second_wait", two))
                second_pid = observations["second_wait"][0]
                self.assertNotEqual(first_pid, second_pid)
                self.assertEqual(("Lock", "advisory"), observations["second_wait"][1:3])
                self.assertEqual([(False,)], held_by(second_pid, a_tag))
                self.assertEqual([], held_by(second_pid, z_tag))
                self.assertEqual([], self.store.load_documents("news_ai", owner))
                control.commit()
                self.assertEqual([[], []], await asyncio.gather(one, two))
            finally:
                control.rollback()
                await asyncio.gather(*(t for t in (one, two) if t is not None), return_exceptions=True)
                print("c06_pg_sorted_keys_wait_receipt=" + json.dumps({"control_pid": control_pid,
                    "keys": [key_a, key_z], "tags": [a_tag, z_tag], "waits": observations}, default=str))

        rows = self._sql("SELECT document_key,document_json,updated_at FROM central_documents "
            "WHERE collection='news_ai' AND owner=? ORDER BY document_key", (owner,))
        self.assertEqual(["a", "z"], [row[0] for row in rows])
        self.assertEqual([self.clock.timestamp(), self.clock.timestamp()], [row[2] for row in rows])
        for _key, raw, _at in rows:
            document = raw if isinstance(raw, dict) else json.loads(raw)
            self.assertEqual("second-commit", document["summary"])
            self.assertEqual({"status": "UNKNOWN", "output_hash": stable_document_hash({"summary": "second-commit"}),
                "input_binding": frozen_inputs[_key]["input_binding"]}, json.loads(document["provenance_json"]))
            self.assertEqual(native_bodies[_key], self.store.load_news_body_revision(native_bodies[_key]["body_revision_id"]))
        self.assertEqual([], self.store.load_news_history("ai", target=owner))
        self.assertEqual([], self.store.load_news_history("inference", target=owner))
        self.assertEqual([], self.calls)
        print("c06_pg_sorted_keys_raw_receipt=" + json.dumps({"rows": rows,
            "frozen_inputs": frozen_inputs, "native_bodies": native_bodies}, ensure_ascii=False, default=str))


# Do not expose the imported SQLite test class as a second discoverable suite.
del _SQLiteAuthority

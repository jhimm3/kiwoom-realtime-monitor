from __future__ import annotations

import io
import json
import unittest

from kiwoom_monitor.infrastructure.central_ai_client import CentralAIClient


class _Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


class CentralAIClientTests(unittest.TestCase):
    def test_converts_central_analysis_and_usage(self) -> None:
        def opener(_request, **_kwargs):
            return _Response(json.dumps({
                "provider": "gemini", "model": "flash", "body_hashes": ["h"], "analysis_revision_ids": ["unassigned-legacy-id"], "results": [{
                    "summary": "요약", "outlook": "긍정", "confidence": 80, "reason": "이유",
                    "positive_evidence": '["근거"]', "negative_evidence": "[]",
                    "category": "실적·전망", "company_impacts": [],
                }], "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }).encode())

        response = CentralAIClient(
            "https://nas.test", "token", opener=opener,
        ).analyze("005930", "삼성전자", "gemini", "flash", [{
            "identity": "a", "title": "제목", "body": "본문", "body_hash": "h",
        }], 1)

        results, hashes, provider, model, usage, cache_hit, provenance = (
            response.results, response.body_hashes, response.provider, response.model,
            response.usage, response.cache_hit, response.provenance)
        self.assertIsNone(response.inference, "native HTTP carries references, never copied raw inference")
        self.assertEqual("요약", results[0].summary)
        self.assertEqual(("근거",), results[0].positive_evidence)
        self.assertEqual((provider, model, usage.total_tokens), ("gemini", "flash", 15))
        self.assertEqual(("h",), hashes)
        self.assertFalse(cache_hit)
        self.assertEqual(({"status": "UNKNOWN", "unassigned_analysis_revision_ids": ["unassigned-legacy-id"]},), provenance)

    def test_native_reference_is_aligned_and_mismatched_metadata_stays_unknown(self) -> None:
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        from kiwoom_monitor.domain.news_ai_contract import AINewsAnalysis
        raw = {"stock_code": "005930", "identity": "group-representative", "provider": "gemini",
               "model": "flash", "body_hash": "same-original-hash", "summary": "요약",
               "outlook": "긍정", "confidence": 80, "reason": "이유"}
        reference = {"version": 1, "status": "ACCEPTED", "origin": "native_accepted", "analysis_revision_id": "analysis-exact",
                     "target_id": "005930", "article_revision_id": "requester-article-exact",
                     "body_revision_id": "requester-body-exact", "provider": "gemini", "model": "flash",
                     "prompt_version": "prompt-original", "schema_version": "schema-original",
                     "input_hash": "same-original-hash", "computed_at": 100.0, "available_at": 105.0,
                     "output_hash": stable_document_hash(raw),
                     "inference_ref": {"inference_id": "provider-first-input", "content_hash": "original-hash", "input_index": 0},
                     "requester": {"kind": "direct"}}
        for mode in ("exact", "mismatch", "reported", "missing_inference", "missing_clock", "missing_schema", "missing_prompt", "bad_index",
                     "status_dict", "status_list", "requester_kind_dict", "requester_kind_list"):
            with self.subTest(mode=mode):
                metadata = (dict(reference, output_hash="different-output") if mode == "mismatch" else
                            dict(reference, status="REPORTED", origin="reported_copy", reported_status="ACCEPTED") if mode == "reported" else dict(reference))
                if mode == "missing_inference": metadata.pop("inference_ref")
                if mode == "missing_clock": metadata.pop("available_at")
                if mode == "missing_schema": metadata.pop("schema_version")
                if mode == "missing_prompt": metadata.pop("prompt_version")
                if mode == "bad_index": metadata["inference_ref"] = {**metadata["inference_ref"], "input_index": "0"}
                if mode == "status_dict": metadata["status"] = {}
                if mode == "status_list": metadata["status"] = []
                if mode == "requester_kind_dict": metadata["requester"] = {"kind": {}}
                if mode == "requester_kind_list": metadata["requester"] = {"kind": []}
                def opener(_request, **_kwargs):
                    return _Response(json.dumps({"provider": "gemini", "model": "flash", "results": [raw],
                        "body_hashes": ["same-original-hash"], "analysis_revision_ids": ["analysis-exact"],
                        "analysis_provenance": [metadata]}).encode())
                response = CentralAIClient("https://nas.test", "token", opener=opener).analyze(
                    "005930", "삼성전자", "gemini", "flash", [{"identity": "group-representative",
                    "title": "title", "articles": [{"title": "member-1"}, {"title": "member-2"}]}], 2)
                self.assertEqual("요약", response.results[0].summary)
                self.assertEqual(AINewsAnalysis("요약", "긍정", 80, "이유"), response.results[0])
                self.assertEqual(("same-original-hash",), response.body_hashes)
                if mode == "reported":
                    self.assertEqual("REPORTED", response.provenance[0]["status"])
                    self.assertEqual("analysis-exact", response.provenance[0]["analysis_revision_id"])
                elif mode != "exact":
                    self.assertEqual("UNKNOWN", response.provenance[0]["status"])
                    self.assertEqual(["analysis-exact"], response.provenance[0]["unassigned_analysis_revision_ids"])
                    if mode in {"status_dict", "status_list", "requester_kind_dict", "requester_kind_list"}:
                        self.assertEqual("analysis-exact", response.provenance[0]["analysis_revision_id"])
                        self.assertEqual(105.0, response.provenance[0]["available_at"])
                        self.assertEqual("same-original-hash", response.provenance[0]["input_hash"])
                else:
                    self.assertEqual((reference,), response.provenance)
                    self.assertNotIn("articles", response.provenance[0])

    def test_reported_input_pins_survive_as_evidence_and_never_become_native_write_pins(self):
        from kiwoom_monitor.central_server.news_service_routes import AIEventInput
        value = AIEventInput(identity="identity", title="title", body="provided body", article_revision_id="claimed-article",
                             body_revision_id="claimed-body", published_at="2026-10-28T08:00:00+09:00",
                             input_source={"version": 1, "article_revision_id": "original-article", "body_revision_id": "original-body", "body_available_at": 100.25})
        event = value.as_reported_input()
        self.assertNotIn("article_revision_id", event)
        self.assertNotIn("body_revision_id", event)
        self.assertEqual("claimed-article", event["input_source"]["article_revision_id"])
        self.assertEqual("claimed-body", event["input_source"]["body_revision_id"])
        self.assertNotIn("body_available_at", event["input_source"])
        self.assertEqual("original-body", event["input_source"]["reported_source"]["body_revision_id"])
        self.assertEqual(100.25, event["input_source"]["reported_source"]["body_available_at"])
        self.assertEqual("2026-10-28T08:00:00+09:00", event["published_at"])

    def test_explicit_unknown_source_and_binding_status_are_not_upgraded_by_known_schema(self):
        from kiwoom_monitor.domain.news_ai_contract import (
            decode_news_input_source, decode_news_ai_input_binding, freeze_news_ai_input, news_ai_source_member,
        )
        source = {"version": 1, "status": "UNKNOWN", "article_revision_id": "reported-article", "body_available_at": 105.25}
        self.assertEqual(source, decode_news_input_source(source))
        member = news_ai_source_member(0, {"identity": "article", "input_source": source}, "raw", "fulltext",
                                       input_text="raw", span=(0, 3))
        frozen = freeze_news_ai_input("article", "title", "raw", "legacy-input-hash", [member], kind="article")
        original = frozen["input_binding"]
        projected = decode_news_ai_input_binding({**original, "status": "UNKNOWN"})
        self.assertEqual("UNKNOWN", projected["status"])
        self.assertEqual(original["content_hash"], projected["content_hash"])
        self.assertEqual("UNKNOWN", projected["members"][0]["source_ref"]["status"])
        self.assertNotIn("raw_body", json.dumps(projected))


if __name__ == "__main__":
    unittest.main()

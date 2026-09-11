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
                "provider": "gemini", "model": "flash", "body_hashes": ["h"], "results": [{
                    "summary": "요약", "outlook": "긍정", "confidence": 80, "reason": "이유",
                    "positive_evidence": '["근거"]', "negative_evidence": "[]",
                    "category": "실적·전망", "company_impacts": [],
                }], "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }).encode())

        results, hashes, provider, model, usage, cache_hit = CentralAIClient(
            "https://nas.test", "token", opener=opener,
        ).analyze("005930", "삼성전자", "gemini", "flash", [{
            "identity": "a", "title": "제목", "body": "본문", "body_hash": "h",
        }], 1)

        self.assertEqual("요약", results[0].summary)
        self.assertEqual(("근거",), results[0].positive_evidence)
        self.assertEqual((provider, model, usage.total_tokens), ("gemini", "flash", 15))
        self.assertEqual(("h",), hashes)
        self.assertFalse(cache_hit)


if __name__ == "__main__":
    unittest.main()

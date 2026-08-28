from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


@dataclass(frozen=True)
class AINewsAnalysis:
    summary: str
    outlook: str
    confidence: int
    reason: str
    positive_evidence: tuple[str, ...] = ()
    negative_evidence: tuple[str, ...] = ()
    category: str = "기타 증권뉴스"


DEFAULT_MODELS = {
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-3.5-flash-lite",
    "claude": "claude-haiku-4-5-20251001",
}

MODEL_OPTIONS = {
    "openai": (
        ("GPT-5.6 Luna · 저비용(추천)", "gpt-5.6-luna"),
        ("GPT-5.4 mini · 저비용", "gpt-5.4-mini"),
        ("GPT-5.6 Terra · 균형", "gpt-5.6-terra"),
        ("GPT-5.6 Sol · 고품질", "gpt-5.6-sol"),
    ),
    "gemini": (
        ("Gemini 3.5 Flash-Lite · 저비용(추천)", "gemini-3.5-flash-lite"),
        ("Gemini 3.7 Flash · 최신 Flash", "gemini-3.7-flash"),
        ("Gemini 3.6 Flash · 균형", "gemini-3.6-flash"),
        ("Gemini 3.5 Flash · 일반", "gemini-3.5-flash"),
        ("Gemini 3.1 Flash-Lite · 저비용", "gemini-3.1-flash-lite"),
        ("Gemini 3.1 Pro Preview · 고품질", "gemini-3.1-pro-preview"),
        ("Gemini 2.5 Flash · 범용", "gemini-2.5-flash"),
    ),
    "claude": (
        ("Claude Haiku 4.5 · 저비용(추천)", "claude-haiku-4-5-20251001"),
        ("Claude Sonnet 5 · 균형", "claude-sonnet-5"),
        ("Claude Opus 5 · 고품질", "claude-opus-5"),
        ("Claude Fable 5 · 최고급", "claude-fable-5"),
    ),
}


def analyze_article(settings: NewsAISettings, stock_name: str, title: str, article_text: str) -> AINewsAnalysis:
    if settings.provider not in DEFAULT_MODELS or not settings.api_key:
        raise ValueError("AI 공급자와 API 키를 뉴스 설정에서 입력하세요.")
    model = settings.model.strip() or DEFAULT_MODELS[settings.provider]
    prompt = _prompt(stock_name, title, article_text)
    if settings.provider == "openai":
        text = _openai(settings.api_key, model, prompt)
    elif settings.provider == "gemini":
        text = _gemini(settings.api_key, model, prompt)
    else:
        text = _claude(settings.api_key, model, prompt)
    return _parse(text)


def _prompt(stock_name: str, title: str, article_text: str) -> str:
    return f"""당신은 한국 주식 뉴스 분석기다. 아래 입력은 동일 사건으로 묶인 여러 기사일 수 있다. 모든 기사를 함께 읽고 중복 표현은 한 번만 반영하며, 기사에 없는 내용을 추측하지 말고 과거 사실과 현재 방향을 구분하라.
종목: {stock_name}
제목: {title}
본문: {article_text}

JSON 하나만 출력하라:
{{"summary":"3문장 이내 요약","category":"실적·전망|수주·계약|투자·인수합병|자본·주주환원|임상·허가|경영권·주주|주가·수급|공시·규제|산업·정책|기타 증권뉴스 중 하나","outlook":"긍정|부정|혼재|판단 자료 부족","confidence":0부터100 정수,"reason":"판정 이유","positive_evidence":["근거"],"negative_evidence":["근거"]}}
단순 주가 상승·하락 보도는 기업가치 호재·악재로 단정하지 말고, '뜨거운 감자' 같은 관용어와 부인·반등·회복 문맥을 정확히 구분하라."""


def _request(url: str, headers: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30, context=system_ssl_context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                if error.code == 503:
                    raise RuntimeError("AI 서버가 일시적으로 혼잡합니다(503). 잠시 후 다시 시도하세요.") from error
                raise RuntimeError(f"AI 서버가 요청을 처리하지 못했습니다({error.code}). 잠시 후 다시 시도하세요.") from error
            retry_after = error.headers.get("Retry-After", "") if error.headers else ""
            try:
                delay = max(1.0, min(5.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = float(attempt + 1)
            time.sleep(delay)
    raise RuntimeError("AI 서버 응답을 받지 못했습니다.")


def _openai(key: str, model: str, prompt: str) -> str:
    payload = _request("https://api.openai.com/v1/responses", {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    }, {"model": model, "input": prompt, "max_output_tokens": 700, "store": False})
    if payload.get("output_text"):
        return str(payload["output_text"])
    return "".join(
        str(content.get("text", "")) for output in payload.get("output", ())
        for content in output.get("content", ()) if content.get("type") == "output_text"
    )


def _gemini(key: str, model: str, prompt: str) -> str:
    payload = _request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", {
        "x-goog-api-key": key, "Content-Type": "application/json",
    }, {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}})
    return str(payload["candidates"][0]["content"]["parts"][0]["text"])


def _claude(key: str, model: str, prompt: str) -> str:
    payload = _request("https://api.anthropic.com/v1/messages", {
        "x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json",
    }, {"model": model, "max_tokens": 700, "messages": [{"role": "user", "content": prompt}]})
    return "".join(str(block.get("text", "")) for block in payload.get("content", ()) if block.get("type") == "text")


def _parse(text: str) -> AINewsAnalysis:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("AI가 판정 결과를 올바른 형식으로 보내지 않았습니다.")
    value = json.loads(match.group(0))
    outlook = str(value.get("outlook", "판단 자료 부족"))
    if outlook not in {"긍정", "부정", "혼재", "판단 자료 부족"}:
        outlook = "판단 자료 부족"
    category = str(value.get("category", "기타 증권뉴스")).strip()
    if category not in {
        "실적·전망", "수주·계약", "투자·인수합병", "자본·주주환원", "임상·허가",
        "경영권·주주", "주가·수급", "공시·규제", "산업·정책", "기타 증권뉴스",
    }:
        category = "기타 증권뉴스"
    return AINewsAnalysis(
        str(value.get("summary", "")).strip(), outlook,
        max(0, min(100, int(value.get("confidence", 0)))), str(value.get("reason", "")).strip(),
        tuple(map(str, value.get("positive_evidence", ()) or ())),
        tuple(map(str, value.get("negative_evidence", ()) or ())),
        category,
    )

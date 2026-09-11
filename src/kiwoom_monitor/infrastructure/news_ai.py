from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


# 대상 종목 관점 계약이 바뀌면 이전 분석을 완료 결과로 재사용하지 않는다.
ANALYSIS_PROMPT_VERSION = "target-company-v2"


def analysis_body_hash(stock_name: str, body: str) -> str:
    value = f"{ANALYSIS_PROMPT_VERSION}\0{stock_name.strip()}\0{body}"
    return f"{ANALYSIS_PROMPT_VERSION}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class AICompanyImpact:
    company: str
    outlook: str
    confidence: int
    reason: str


@dataclass(frozen=True)
class AINewsAnalysis:
    summary: str
    outlook: str
    confidence: int
    reason: str
    positive_evidence: tuple[str, ...] = ()
    negative_evidence: tuple[str, ...] = ()
    category: str = "기타 증권뉴스"
    company_impacts: tuple[AICompanyImpact, ...] = ()


@dataclass(frozen=True)
class AIRequestUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


DEFAULT_MODELS = {
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-3.5-flash-lite",
    "claude": "claude-haiku-4-5-20251001",
}


class NewsAIProviderError(RuntimeError):
    """AI 공급자의 재시도 가능한 HTTP 실패를 상태 코드와 함께 보존한다."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        if self.status_code == 429:
            message = "AI 공급자 호출 한도를 초과했습니다(429). 잠시 후 다시 시도하세요."
        elif self.status_code == 503:
            message = "AI 서버가 일시적으로 혼잡합니다(503). 잠시 후 다시 시도하세요."
        else:
            message = f"AI 서버가 요청을 처리하지 못했습니다({self.status_code}). 잠시 후 다시 시도하세요."
        super().__init__(message)

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
    analyses, _usage = analyze_articles(settings, stock_name, ((title, article_text),))
    return analyses[0]


def analyze_articles(
    settings: NewsAISettings, stock_name: str, articles: tuple[tuple[str, str], ...],
) -> tuple[tuple[AINewsAnalysis, ...], AIRequestUsage]:
    if settings.provider not in DEFAULT_MODELS or not settings.api_key:
        raise ValueError("AI 공급자와 API 키를 뉴스 설정에서 입력하세요.")
    if not articles:
        return (), AIRequestUsage()
    model = settings.model.strip() or DEFAULT_MODELS[settings.provider]
    prompt = _batch_prompt(stock_name, articles)
    if settings.provider == "openai":
        text, usage = _openai(settings.api_key, model, prompt)
    elif settings.provider == "gemini":
        text, usage = _gemini(settings.api_key, model, prompt)
    else:
        text, usage = _claude(settings.api_key, model, prompt)
    return _parse_many(text, len(articles)), usage


def _prompt(stock_name: str, title: str, article_text: str) -> str:
    return f"""당신은 한국 주식 뉴스 분석기다. 아래 입력은 동일 사건으로 묶인 여러 기사일 수 있다. 모든 기사를 함께 읽고 중복 표현은 한 번만 반영하며, 기사에 없는 내용을 추측하지 말고 과거 사실과 현재 방향을 구분하라.
종목: {stock_name}
제목: {title}
본문: {article_text}

outlook, confidence, reason과 긍정·부정 근거는 기사 전체나 다른 회사가 아니라 반드시 위 종목 {stock_name}의 주가·실적·사업에 미치는 영향만 판정하라. {stock_name}을 단순 나열했거나 직접 영향을 확인할 근거가 없으면 outlook을 "판단 자료 부족"으로 하고 summary에도 직접 관계가 없음을 분명히 적어라.

JSON 하나만 출력하라:
{{"summary":"3문장 이내 요약","category":"실적·전망|수주·계약|투자·인수합병|자본·주주환원|임상·허가|경영권·주주|주가·수급|공시·규제|산업·정책|기타 증권뉴스 중 하나","outlook":"긍정|부정|혼재|판단 자료 부족","confidence":0부터100 정수,"reason":"판정 이유","positive_evidence":["근거"],"negative_evidence":["근거"],"company_impacts":[{{"company":"기사에 나온 상장사명","outlook":"긍정|부정|혼재|판단 자료 부족","confidence":0,"reason":"그 회사 관점의 이유"}}]}}
단순 주가 상승·하락 보도는 기업가치 호재·악재로 단정하지 말고, '뜨거운 감자' 같은 관용어와 부인·반등·회복 문맥을 정확히 구분하라."""


def _batch_prompt(stock_name: str, articles: tuple[tuple[str, str], ...]) -> str:
    if len(articles) == 1:
        return _prompt(stock_name, articles[0][0], articles[0][1])
    sections = "\n\n".join(
        f"[사건 {index}]\n제목: {title}\n본문: {body}"
        for index, (title, body) in enumerate(articles, start=1)
    )
    return f"""당신은 한국 주식 뉴스 분석기다. 종목 {stock_name}에 대한 서로 다른 사건 {len(articles)}개를 한 요청으로 분석하라.
각 사건 본문에는 동일 사건으로 묶인 관련 기사가 여러 개 포함될 수 있다. 중복 표현은 한 번만 반영하고 과거 사실과 현재 변화를 구분하라.
각 결과의 outlook, confidence, reason과 근거는 반드시 대상 종목 {stock_name}의 관점으로 작성하라. {stock_name}이 단순 나열되었거나 직접 영향을 확인할 근거가 없으면 "판단 자료 부족"으로 판정하고 summary에도 직접 관계가 없음을 밝혀라.
{sections}

입력 순서와 같은 JSON 배열 하나만 출력하라. 각 항목에 id를 반드시 유지하라:
[{{"id":1,"summary":"3문장 이내 요약","category":"실적·전망|수주·계약|투자·인수합병|자본·주주환원|임상·허가|경영권·주주|주가·수급|공시·규제|산업·정책|기타 증권뉴스 중 하나","outlook":"긍정|부정|혼재|판단 자료 부족","confidence":0,"reason":"판정 이유","positive_evidence":["근거"],"negative_evidence":["근거"],"company_impacts":[{{"company":"상장사명","outlook":"긍정|부정|혼재|판단 자료 부족","confidence":0,"reason":"회사별 이유"}}]}}]
기사에 없는 내용을 추측하지 말고 단순 주가 반응과 관용어를 기업가치 변화로 오판하지 마라."""


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
                raise NewsAIProviderError(error.code) from error
            retry_after = error.headers.get("Retry-After", "") if error.headers else ""
            try:
                delay = max(1.0, min(5.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = float(attempt + 1)
            time.sleep(delay)
    raise RuntimeError("AI 서버 응답을 받지 못했습니다.")


def _openai(key: str, model: str, prompt: str) -> tuple[str, AIRequestUsage]:
    payload = _request("https://api.openai.com/v1/responses", {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    }, {"model": model, "input": prompt, "max_output_tokens": 7000, "store": False})
    if payload.get("output_text"):
        text = str(payload["output_text"])
    else:
        text = "".join(
        str(content.get("text", "")) for output in payload.get("output", ())
        for content in output.get("content", ()) if content.get("type") == "output_text"
        )
    usage = payload.get("usage", {}) if isinstance(payload.get("usage"), dict) else {}
    return text, AIRequestUsage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), int(usage.get("total_tokens", 0)))


def _gemini(key: str, model: str, prompt: str) -> tuple[str, AIRequestUsage]:
    payload = _request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", {
        "x-goog-api-key": key, "Content-Type": "application/json",
    }, {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}})
    usage = payload.get("usageMetadata", {}) if isinstance(payload.get("usageMetadata"), dict) else {}
    return str(payload["candidates"][0]["content"]["parts"][0]["text"]), AIRequestUsage(
        int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0)), int(usage.get("totalTokenCount", 0)),
    )


def _claude(key: str, model: str, prompt: str) -> tuple[str, AIRequestUsage]:
    payload = _request("https://api.anthropic.com/v1/messages", {
        "x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json",
    }, {"model": model, "max_tokens": 7000, "messages": [{"role": "user", "content": prompt}]})
    usage = payload.get("usage", {}) if isinstance(payload.get("usage"), dict) else {}
    input_tokens, output_tokens = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    return "".join(str(block.get("text", "")) for block in payload.get("content", ()) if block.get("type") == "text"), AIRequestUsage(
        input_tokens, output_tokens, input_tokens + output_tokens,
    )


def _parse(text: str) -> AINewsAnalysis:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("AI가 판정 결과를 올바른 형식으로 보내지 않았습니다.")
    value = json.loads(match.group(0))
    return _parse_value(value)


def _parse_many(text: str, expected: int) -> tuple[AINewsAnalysis, ...]:
    if expected == 1:
        return (_parse(text),)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("AI가 일괄 판정 결과를 올바른 형식으로 보내지 않았습니다.")
    values = json.loads(match.group(0))
    if not isinstance(values, list) or len(values) != expected:
        raise ValueError(f"AI 일괄 판정 결과 수가 요청과 다릅니다({len(values) if isinstance(values, list) else 0}/{expected}).")
    ordered = sorted(values, key=lambda value: int(value.get("id", 0)))
    return tuple(_parse_value(value) for value in ordered)


def _parse_value(value: dict[str, object]) -> AINewsAnalysis:
    outlook = str(value.get("outlook", "판단 자료 부족"))
    if outlook not in {"긍정", "부정", "혼재", "판단 자료 부족"}:
        outlook = "판단 자료 부족"
    category = str(value.get("category", "기타 증권뉴스")).strip()
    if category not in {
        "실적·전망", "수주·계약", "투자·인수합병", "자본·주주환원", "임상·허가",
        "경영권·주주", "주가·수급", "공시·규제", "산업·정책", "기타 증권뉴스",
    }:
        category = "기타 증권뉴스"
    impacts: list[AICompanyImpact] = []
    raw_impacts = value.get("company_impacts", ())
    if isinstance(raw_impacts, list):
        for impact in raw_impacts:
            if not isinstance(impact, dict) or not str(impact.get("company", "")).strip():
                continue
            impact_outlook = str(impact.get("outlook", "판단 자료 부족"))
            if impact_outlook not in {"긍정", "부정", "혼재", "판단 자료 부족"}:
                impact_outlook = "판단 자료 부족"
            impacts.append(AICompanyImpact(
                str(impact.get("company", "")).strip(), impact_outlook,
                max(0, min(100, int(impact.get("confidence", 0)))), str(impact.get("reason", "")).strip(),
            ))
    return AINewsAnalysis(
        str(value.get("summary", "")).strip(), outlook,
        max(0, min(100, int(value.get("confidence", 0)))), str(value.get("reason", "")).strip(),
        tuple(map(str, value.get("positive_evidence", ()) or ())),
        tuple(map(str, value.get("negative_evidence", ()) or ())),
        category, tuple(impacts),
    )

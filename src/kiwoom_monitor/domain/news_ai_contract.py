"""Stored AI news values and model choices shared by readers and producers."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import hashlib

from .news_observation import stable_document_hash
import math
from copy import deepcopy
from datetime import datetime
from types import MappingProxyType


# Changing this value invalidates prior analyses for the target-company contract.
ANALYSIS_PROMPT_VERSION = "target-company-theme-v3"


@dataclass(frozen=True)
class NewsAssessment:
    relevant: bool
    category: str
    outlook: str
    reason: str
    relevance_score: int
    outlook_score: int


@dataclass(frozen=True)
class StockNewsItem:
    title: str
    description: str
    link: str
    original_link: str
    published_at: datetime | None
    assessment: NewsAssessment
    input_source: dict[str, object] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class AICompanyImpact:
    company: str
    outlook: str
    confidence: int
    reason: str


@dataclass(frozen=True)
class AIThemeCandidate:
    name: str
    confidence: int
    evidence: str


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
    theme_candidates: tuple[AIThemeCandidate, ...] = ()


@dataclass(frozen=True)
class AIRequestUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def decode_news_input_source(value: object) -> dict[str, object] | None:
    """Read source references as evidence, never as an article/job write grant."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    names = ("version", "article_revision_id", "article_content_hash", "article_available_at",
             "article_published_at", "published_at_source", "body_revision_id", "body_content_hash",
             "body_status", "body_extractor_version", "body_fetched_at", "body_available_at")
    result = {name: deepcopy(value[name]) for name in names if name in value}
    sequence = value.get("native_sequence")
    if isinstance(sequence, dict):
        result["native_sequence"] = {name: deepcopy(sequence[name]) for name in
            ("authority", "anchor", "body_ref", "body_hash", "sequence") if name in sequence}
    reported = value.get("reported_source")
    if isinstance(reported, dict):
        result["reported_source"] = {name: deepcopy(reported[name]) for name in names if name in reported}
        if isinstance(reported.get("native_sequence"), dict):
            result["reported_source"]["native_sequence"] = deepcopy(reported["native_sequence"])
    # Unknown versions retain the claimed values; consumers must not reinterpret
    # them as the current contract or fill missing refs with today's latest row.
    result["status"] = ("REFERENCE" if type(value.get("version")) is int and value["version"] == 1
                        and value.get("status") != "UNKNOWN" else "UNKNOWN")
    return result


def native_news_source_sequence(value: object) -> dict[str, object] | None:
    """A native source cut is scoped to its original article and ledger authority.

    Used for no-rewind copy selection, never a write/order grant. An UNKNOWN
    current display can retain its last proven cut without becoming current.
    """
    source = decode_news_input_source(value)
    sequence = source.get("native_sequence") if source else None
    if (not isinstance(sequence, dict) or not isinstance(sequence.get("authority"), str)
            or not sequence["authority"] or type(sequence.get("sequence")) is not int or sequence["sequence"] < 1
            or sequence.get("anchor") != source.get("article_revision_id")
            or sequence.get("body_ref") != source.get("body_revision_id")
            or sequence.get("body_hash") != source.get("body_content_hash")
            or not all(sequence.get(name) for name in ("anchor", "body_ref", "body_hash"))):
        return None
    return sequence


def select_news_copy_source(current: object, reported: object, native_cut: object) -> tuple[dict[str, object] | None, bool]:
    """Apply an authenticated native read cut without guessing UUID/time order.

    The boolean allows the associated article projection to change. Conflicts
    without comparable native lineage keep its original value and expose UNKNOWN.
    """
    prior, incoming, cut = map(decode_news_input_source, (current, reported, native_cut))
    old_sequence, new_sequence = native_news_source_sequence(prior), native_news_source_sequence(cut)
    if cut is not None and cut["status"] == "REFERENCE" and new_sequence is not None:
        same_refs = prior is None or (all(prior.get(name) == cut.get(name) for name in
            ("article_revision_id", "body_revision_id", "body_content_hash"))
            and (old_sequence is None or old_sequence["authority"] == new_sequence["authority"]))
        comparable = (old_sequence is not None and all(old_sequence[name] == new_sequence[name] for name in ("authority", "anchor")))
        if comparable and new_sequence["sequence"] < old_sequence["sequence"]:
            return {**prior, "reported_source": incoming or {"status": "UNKNOWN"}}, False
        if same_refs or comparable:
            selected = {**cut}
            if incoming is not None and incoming != cut:
                selected["reported_source"] = incoming.get("reported_source", incoming)
            return selected, True
    if prior is not None and (old_sequence is not None or native_cut is not None):
        return {**prior, "status": "UNKNOWN", "reported_source": incoming or {"status": "UNKNOWN"}}, False
    if incoming is not None and "native_sequence" in incoming and native_cut is None:
        selected = {name: value for name, value in incoming.items() if name != "native_sequence"}
        selected.update(status="UNKNOWN", reported_source=incoming)
        return selected, True
    return incoming, True  # Legacy copies preserve their original unverified contract.


def news_ai_article_input(item: StockNewsItem) -> dict[str, object]:
    """One explicit serializer shared by manual, direct, and automatic groups."""
    return {
        "identity": item.original_link or item.link or f"{item.published_at!s}|{item.title}",
        "title": item.title, "description": item.description,
        "link": item.link, "original_link": item.original_link,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "input_source": decode_news_input_source(getattr(item, "input_source", None)),
    }


@dataclass(frozen=True)
class NewsAIResultEnvelope:
    results: tuple[AINewsAnalysis, ...]
    body_hashes: tuple[str, ...]
    provider: str
    model: str
    usage: AIRequestUsage
    article_count: int
    cache_hit: bool = False
    provenance: tuple[dict[str, object], ...] = ()
    inference: dict[str, object] | None = None
    stock_name: str = ""


@dataclass(frozen=True)
class NewsAIResultReceipt:
    provenance: tuple[dict[str, object], ...]
    published: bool
    inference_ref: dict[str, object] | None = None
    available_at: float | None = None
    published_indices: tuple[int, ...] | None = None
    shared_published_indices: tuple[int, ...] | None = None


def analysis_body_hash(stock_name: str, body: str) -> str:
    value = f"{ANALYSIS_PROMPT_VERSION}\0{stock_name.strip()}\0{body}"
    return f"{ANALYSIS_PROMPT_VERSION}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def normalized_news_company_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def news_ai_shared_applies(raw: object, stock_name: str) -> bool:
    name = normalized_news_company_name(stock_name)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return False
    return bool(name) and isinstance(raw, (list, tuple)) and any(
        isinstance(value, dict) and normalized_news_company_name(str(value.get("company", ""))) == name for value in raw)


def news_ai_analysis_payload(value: AINewsAnalysis) -> dict[str, object]:
    """The actual model result value, excluding storage/acceptance metadata."""
    return {
        "summary": value.summary, "outlook": value.outlook, "confidence": value.confidence,
        "reason": value.reason, "positive_evidence": list(value.positive_evidence),
        "negative_evidence": list(value.negative_evidence), "category": value.category,
        "company_impacts": [dict(company=part.company, outlook=part.outlook,
                                 confidence=part.confidence, reason=part.reason)
                            for part in value.company_impacts],
        "theme_candidates": [dict(name=part.name, confidence=part.confidence, evidence=part.evidence)
                             for part in value.theme_candidates],
    }


def news_ai_source_member(index: int, article: dict[str, object], raw_body: str, body_status: str,
                          *, input_text: str = "", span: tuple[int, int] | None = None,
                          observed_at: float | None = None, clock_source: str = "UNKNOWN",
                          native_body: dict[str, object] | None = None) -> dict[str, object]:
    """Freeze one actual source and its exact half-open code-point span."""
    reference = decode_news_input_source(article.get("input_source"))
    if reference is None and any(article.get(name) for name in ("article_revision_id", "body_revision_id")):
        reference = decode_news_input_source({"version": 1, **{name: article[name] for name in
            ("article_revision_id", "body_revision_id") if name in article}})
    return {
        "source_index": index, "identity": str(article.get("identity") or ""),
        "article": deepcopy(article), "raw_body": raw_body, "body_status": body_status,
        "source_body_hash": stable_document_hash({"body": raw_body}),
        "input_text": input_text, "span": list(span) if span is not None else None,
        "used": span is not None, "observed_at": observed_at, "clock_source": clock_source,
        "source_ref": reference, "native_body": deepcopy(native_body),
    }


def _news_ai_input_binding(title: str, body: str, input_hash: str, kind: str,
                           members: list[dict[str, object]]) -> dict[str, object]:
    sources = []
    for member in members:
        reference = decode_news_input_source(member.get("source_ref"))
        raw_hash = str(member["source_body_hash"])
        source_hash = reference.get("body_content_hash") if reference else None
        sources.append({
            "source_index": member["source_index"], "identity": member["identity"],
            "article_input_hash": stable_document_hash(member["article"]),
            "source_body_hash": raw_hash, "body_status": member["body_status"],
            "used": member["used"], "span": deepcopy(member["span"]),
            "input_text_hash": stable_document_hash({"body": str(member["input_text"])}),
            "observed_at": member.get("observed_at"), "clock_source": member.get("clock_source", "UNKNOWN"),
            "source_ref": reference, "native_body": deepcopy(member.get("native_body")),
            "body_reference_status": ("MATCH" if source_hash == raw_hash else
                                      "DIFFERENT" if source_hash else "UNKNOWN"),
        })
    value = {"version": 1, "kind": kind, "input_hash": input_hash,
             "provider_input_hash": stable_document_hash({"title": title, "body": body}),
             "span_unit": "unicode_code_points_half_open", "members": sources}
    return {**value, "content_hash": stable_document_hash(value)}


def freeze_news_ai_input(identity: str, title: str, body: str, input_hash: str,
                         members: list[dict[str, object]], *, kind: str,
                         article_revision_id: str = "", body_revision_id: str = "") -> dict[str, object]:
    value = {
        "identity": identity, "title": title, "body": body,
        "article_revision_id": article_revision_id, "body_revision_id": body_revision_id,
        "input_hash": input_hash, "body_content_hash": stable_document_hash({"body": body}),
        "source_snapshot": {"version": 1, "kind": kind, "members": deepcopy(members)},
        "input_binding": _news_ai_input_binding(title, body, input_hash, kind, members),
    }
    validate_news_ai_input(value)
    return value


def validate_news_ai_input(value: dict[str, object]) -> None:
    """Validate a new produced snapshot; legacy reads never reconstruct it."""
    body = value["body"]
    snapshot = value["source_snapshot"]
    if (not isinstance(body, str) or not isinstance(snapshot, dict)
            or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or not isinstance(snapshot.get("members"), list)):
        raise ValueError("AI input source snapshot contract differs")
    if value["body_content_hash"] != stable_document_hash({"body": body}):
        raise ValueError("AI provider body differs from its frozen hash")
    for index, member in enumerate(snapshot["members"]):
        if (not isinstance(member, dict) or type(member.get("source_index")) is not int
                or member["source_index"] != index or not isinstance(member.get("raw_body"), str)):
            raise ValueError("AI source member order differs")
        if member.get("source_body_hash") != stable_document_hash({"body": member.get("raw_body")}):
            raise ValueError("AI raw source body differs from its frozen hash")
        span = member.get("span")
        if span is None:
            if member.get("used") is not False or member.get("input_text") != "":
                raise ValueError("AI omitted member claims provider input")
        elif (not isinstance(span, list) or len(span) != 2
              or any(type(position) is not int for position in span)
              or not 0 <= span[0] <= span[1] <= len(body)
              or member.get("used") is not True or body[span[0]:span[1]] != member.get("input_text")):
            raise ValueError("AI source span differs from the actual provider input")
    expected = _news_ai_input_binding(str(value["title"]), body, str(value["input_hash"]),
                                      str(snapshot["kind"]), snapshot["members"])
    if stable_document_hash(value.get("input_binding", {})) != stable_document_hash(expected):
        raise ValueError("AI input binding differs from the produced source snapshot")


def decode_news_ai_input_binding(value: object) -> dict[str, object]:
    """A narrow reference projection. Never deserialize raw provider/source text."""
    if not isinstance(value, dict):
        return {"status": "UNKNOWN"}
    names = ("version", "kind", "input_hash", "provider_input_hash", "span_unit", "content_hash")
    result = {name: deepcopy(value[name]) for name in names if name in value}
    members = value.get("members")
    if isinstance(members, list):
        allowed = ("source_index", "identity", "article_input_hash", "source_body_hash", "body_status",
                   "used", "span", "input_text_hash", "observed_at", "clock_source", "source_ref",
                   "native_body", "body_reference_status")
        result["members"] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            projected = {name: deepcopy(member[name]) for name in allowed if name in member}
            if "source_ref" in projected:
                projected["source_ref"] = decode_news_input_source(projected["source_ref"])
            if isinstance(projected.get("native_body"), dict):
                receipt = projected["native_body"]
                projected["native_body"] = {name: deepcopy(receipt[name]) for name in
                    ("body_revision_id", "article_revision_id", "content_hash", "extractor_version",
                     "fetched_at", "available_at", "status") if name in receipt}
            result["members"].append(projected)
    core = {name: entry for name, entry in result.items() if name != "content_hash"}
    if (type(value.get("version")) is int and value["version"] == 1
            and isinstance(members, list) and len(result.get("members", ())) == len(members)
            and value.get("content_hash") == stable_document_hash(core)):
        return {**result, "status": "UNKNOWN"} if value.get("status") == "UNKNOWN" else result
    return {**result, "status": "UNKNOWN"}



def _complete_news_ai_provenance(value: dict[str, object]) -> bool:
    names = ("analysis_revision_id", "target_id", "article_revision_id", "body_revision_id",
             "provider", "model", "prompt_version", "schema_version", "input_hash", "output_hash")
    if (value.get("origin") != "native_accepted" or type(value.get("version")) is not int or value["version"] != 1
            or not all(isinstance(value.get(name), str) and value[name] for name in names)):
        return False
    for name in ("computed_at", "available_at"):
        moment = value.get(name)
        if not isinstance(moment, (int, float)) or isinstance(moment, bool):
            return False
        try:
            if not math.isfinite(moment):
                return False
        except OverflowError:
            return False
    reference, requester = value.get("inference_ref"), value.get("requester")
    if (not isinstance(reference, dict)
            or not all(isinstance(reference.get(name), str) and reference[name]
                       for name in ("inference_id", "content_hash"))
            or type(reference.get("input_index")) is not int or reference["input_index"] < 0
            or not isinstance(requester, dict) or not isinstance(requester.get("kind"), str)
            or requester["kind"] not in {"direct", "job"}):
        return False
    return (requester["kind"] == "direct" or
            (isinstance(requester.get("job_key"), str) and bool(requester["job_key"])
             and type(requester.get("attempts")) is int and requester["attempts"] > 0))


def decode_news_ai_provenance(value: object) -> dict[str, object]:
    """Decode result metadata without importing an inference processor or raw input.

    These references describe the accepted result binding, not every member of a
    grouped provider input. UNKNOWN never reconstructs article/body relationships.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = None
    if not isinstance(value, dict):
        return {"status": "UNKNOWN"}
    names = ("version", "analysis_revision_id", "target_id", "article_revision_id",
             "body_revision_id", "provider", "model", "prompt_version", "schema_version",
             "input_hash", "computed_at", "available_at", "output_hash",
             "origin", "reported_origin", "reported_status")
    result = {name: deepcopy(value[name]) for name in names if name in value}
    status = value.get("status")
    result["status"] = status if isinstance(status, str) and status in {"ACCEPTED", "REPORTED"} else "UNKNOWN"
    reference = value.get("inference_ref")
    if isinstance(reference, dict):
        result["inference_ref"] = {name: deepcopy(reference[name]) for name in
                                   ("inference_id", "content_hash", "input_index") if name in reference}
    requester = value.get("requester")
    if isinstance(requester, dict):
        result["requester"] = {name: deepcopy(requester[name]) for name in
                               ("kind", "job_key", "attempts") if name in requester}
    if "input_binding" in value:
        result["input_binding"] = decode_news_ai_input_binding(value["input_binding"])
    unassigned = value.get("unassigned_analysis_revision_ids")
    if isinstance(unassigned, (list, tuple)):
        result["unassigned_analysis_revision_ids"] = [str(item) for item in unassigned if item]
    if result["status"] == "ACCEPTED" and not _complete_news_ai_provenance(result):
        result["reported_status"], result["status"] = "ACCEPTED", "UNKNOWN"
    return result


def news_ai_version_status(items: tuple[StockNewsItem, ...], provenance: object) -> str:
    sources = {item.original_link or item.link or f"{item.published_at!s}|{item.title}": item.input_source
               for item in items}
    return news_ai_source_version_status(sources, provenance, exact_members=True)


def news_ai_source_version_status(sources: dict[str, object], provenance: object, *, exact_members: bool = False) -> str:
    """Compare the displayed sources with the actual inference's source binding.

    Requester pins, prompt freshness and receipt time cannot prove that a result
    used the displayed BODY. Missing legacy evidence stays UNKNOWN; no source or
    accepted record is rewritten to make a display current.
    """
    reference = decode_news_ai_provenance(provenance)
    binding = reference.get("input_binding")
    if (reference["status"] == "UNKNOWN" or not isinstance(binding, dict)
            or binding.get("status") == "UNKNOWN" or not sources):
        return "UNKNOWN"
    members = {str(member.get("identity") or ""): member
               for member in binding.get("members", ()) if isinstance(member, dict)}
    unknown = (not any(member.get("used") is True for member in members.values())
               or len(members) != len(binding.get("members", ()))
               or (exact_members and set(sources) != set(members)))
    for identity, member in members.items():
        if member.get("used") is False:
            continue  # Preserve the omission; it did not supply the provider's BODY.
        current = decode_news_input_source(sources.get(identity))
        if not current or current["status"] != "REFERENCE":
            unknown = True
            continue
        native = member.get("native_body")
        if isinstance(native, dict) and native.get("content_hash") == member.get("source_body_hash"):
            prior = native
            prior_hash = native.get("content_hash")
        else:
            prior = decode_news_input_source(member.get("source_ref"))
            if not prior or prior["status"] != "REFERENCE" or member.get("body_reference_status") != "MATCH":
                unknown = True
                continue
            prior_hash = prior.get("body_content_hash")
        names = ("article_revision_id", "body_revision_id")
        if not all(current.get(name) and prior.get(name) for name in names) or not prior_hash or not current.get("body_content_hash"):
            unknown = True
        elif any(current[name] != prior[name] for name in names) or current["body_content_hash"] != prior_hash:
            return "PREVIOUS"
    return "UNKNOWN" if unknown else "CURRENT"


DEFAULT_MODELS = MappingProxyType({
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-3.5-flash-lite",
    "claude": "claude-haiku-4-5-20251001",
})

MODEL_OPTIONS = MappingProxyType({
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
})

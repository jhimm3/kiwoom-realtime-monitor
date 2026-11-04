from __future__ import annotations


import json
import math
import sqlite3
from copy import deepcopy
from uuid import UUID
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from kiwoom_monitor.domain.time_basis import kst_now, as_kst

from kiwoom_monitor.application.theme_suggestions import ThemeSuggestion
from kiwoom_monitor.domain.news_ai_contract import StockNewsItem
from kiwoom_monitor.domain.news_ai_contract import (
    ANALYSIS_PROMPT_VERSION, AINewsAnalysis, AICompanyImpact, AIRequestUsage, AIThemeCandidate,
    NewsAIResultEnvelope, NewsAIResultReceipt, analysis_body_hash,
    decode_news_ai_provenance, news_ai_analysis_payload, news_ai_source_version_status, validate_news_ai_input,
    normalized_news_company_name as _normalized_company_name,
    decode_news_input_source, native_news_source_sequence,
)
from kiwoom_monitor.domain.news_observation import NEWS_ANALYSIS_SCHEMA_VERSION, stable_document_hash
from kiwoom_monitor.infrastructure.persistence.news_schema import initialize_news_schema
from kiwoom_monitor.infrastructure.persistence.sqlite_connections import (
    sqlite_read_connection,
    sqlite_transaction,
)


@dataclass(frozen=True)
class StoredAINewsAnalysis:
    analysis: AINewsAnalysis
    provider: str
    model: str
    analyzed_at: datetime
    body_hash: str = ""
    provenance: dict[str, object] = field(default_factory=lambda: {"status": "UNKNOWN"})

    @property
    def uses_current_prompt(self) -> bool:
        return self.body_hash.startswith(f"{ANALYSIS_PROMPT_VERSION}:")


def news_identity(item: StockNewsItem) -> str:
    return item.original_link or item.link or f"{item.published_at!s}|{item.title}"


class NewsAIRepository:
    def __init__(self, database_path: Path, *, now_provider=None) -> None:
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
        self._database_path = database_path
        initialize_news_schema(self._database_path)

    def load(self, stock_code: str, item: StockNewsItem) -> StoredAINewsAnalysis | None:
        with sqlite_read_connection(self._database_path) as connection:
            row = connection.execute(
                "SELECT provider, model, summary, outlook, confidence, reason, positive_evidence, negative_evidence, analyzed_at, category, body_hash, theme_candidates, provenance_json "
                "FROM stock_news_ai WHERE stock_code=? AND identity=?",
                (stock_code, news_identity(item)),
            ).fetchone()
        if row is None:
            return None
        return StoredAINewsAnalysis(
            AINewsAnalysis(str(row[2]), str(row[3]), int(row[4]), str(row[5]),
                           tuple(json.loads(row[6])), tuple(json.loads(row[7])), str(row[9]), (),
                           _decode_theme_candidates(str(row[11]))),
            str(row[0]), str(row[1]), datetime.fromisoformat(str(row[8])), str(row[10]),
            decode_news_ai_provenance(row[12]),
        )

    def load_many(
        self, stock_code: str, items: tuple[StockNewsItem, ...], stock_name: str = "",
    ) -> dict[str, StoredAINewsAnalysis]:
        """한 종목의 저장된 AI 결과를 DB 연결 한 번으로 읽는다."""
        identities = tuple(dict.fromkeys(news_identity(item) for item in items))
        if not identities:
            return {}
        placeholders = ",".join("?" for _ in identities)
        with sqlite_read_connection(self._database_path) as connection:
            rows = connection.execute(
                "SELECT identity, provider, model, summary, outlook, confidence, reason, "
                "positive_evidence, negative_evidence, analyzed_at, category, body_hash, theme_candidates, provenance_json "
                f"FROM stock_news_ai WHERE stock_code=? AND identity IN ({placeholders})",
                (stock_code, *identities),
            ).fetchall()
        loaded = {
            str(row[0]): StoredAINewsAnalysis(
                AINewsAnalysis(str(row[3]), str(row[4]), int(row[5]), str(row[6]),
                               tuple(json.loads(row[7])), tuple(json.loads(row[8])), str(row[10]), (),
                               _decode_theme_candidates(str(row[12]))),
                str(row[1]), str(row[2]), datetime.fromisoformat(str(row[9])), str(row[11]),
                decode_news_ai_provenance(row[13]),
            )
            for row in rows
        }
        missing = tuple(identity for identity in identities if identity not in loaded)
        normalized_name = _normalized_company_name(stock_name)
        if missing and normalized_name:
            placeholders = ",".join("?" for _ in missing)
            with sqlite_read_connection(self._database_path) as connection:
                shared_rows = connection.execute(
                    "SELECT identity,provider,model,summary,category,positive_evidence,negative_evidence,company_impacts,analyzed_at,body_hash,provenance_json "
                    f"FROM news_ai_shared WHERE identity IN ({placeholders})", missing,
                ).fetchall()
            for row in shared_rows:
                impacts = _decode_impacts(str(row[7]))
                impact = _shared_company_impact(impacts, stock_name)
                if impact is None:
                    continue
                loaded[str(row[0])] = StoredAINewsAnalysis(
                    AINewsAnalysis(
                        str(row[3]), impact.outlook, impact.confidence, impact.reason,
                        tuple(json.loads(row[5])), tuple(json.loads(row[6])), str(row[4]), impacts,
                    ), str(row[1]), str(row[2]), datetime.fromisoformat(str(row[8])), str(row[9]),
                    decode_news_ai_provenance(row[10]),
                )
        return loaded

    def save(self, stock_code: str, item: StockNewsItem, provider: str, model: str,
             body_hash: str, analysis: AINewsAnalysis, *, provenance: dict[str, object] | None = None) -> None:
        with sqlite_transaction(self._database_path) as connection:
            _save_analysis(connection, stock_code, news_identity(item), provider, model, body_hash,
                           analysis, self._now().isoformat(), decode_news_ai_provenance(provenance))

    def save_batch(
        self, stock_code: str, items: tuple[StockNewsItem, ...], envelope: NewsAIResultEnvelope,
    ) -> NewsAIResultReceipt:
        """Accept one actual inference and every own/shared projection atomically.

        A native remote envelope carries references only. Re-delivery of a local
        inference returns its first receipt without publishing old projections.
        """
        count = len(items)
        if len(envelope.results) != count or len(envelope.body_hashes) != count:
            raise ValueError("뉴스 AI 배치 결과 개수가 입력과 다릅니다.")
        if envelope.provenance and len(envelope.provenance) != count:
            raise ValueError("뉴스 AI 배치 출처 개수가 결과와 다릅니다.")
        if not count:
            if envelope.inference is not None:
                raise ValueError("빈 뉴스 AI 배치에는 원본 추론을 저장할 수 없습니다.")
            return NewsAIResultReceipt((), False)
        document = None
        if envelope.inference is not None:
            document = _validate_local_inference(stock_code, items, envelope)
        with sqlite_transaction(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if document is not None:
                content_hash = stable_document_hash(document)
                # INSERT is the writer claim. Only a duplicate needs one SELECT;
                # no per-article source/current reads are introduced.
                accepted_at = self._now()
                available_at = accepted_at.timestamp()
                document_json = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                inserted = connection.execute(
                    "INSERT INTO news_ai_inferences(inference_id,content_hash,computed_at,available_at,document_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(inference_id) DO NOTHING",
                    (document["inference_id"], content_hash, document["computed_at"], available_at, document_json),
                ).rowcount
                if not inserted:
                    row = connection.execute(
                        "SELECT content_hash,computed_at,available_at,document_json FROM news_ai_inferences WHERE inference_id=?",
                        (document["inference_id"],),
                    ).fetchone()
                    if row is None or row[0] != content_hash or row[1] != document["computed_at"] or row[3] != document_json:
                        raise ValueError("뉴스 AI 추론 ID의 원본이 기존 기록과 다릅니다.")
                    return _local_receipt(document, content_hash, float(row[2]), published=False)
                receipt = _local_receipt(document, content_hash, available_at, published=True)
                analyzed_at = datetime.fromtimestamp(document["computed_at"], tz=accepted_at.tzinfo).isoformat()
            else:
                provenance = tuple(decode_news_ai_provenance(value) for value in envelope.provenance) if envelope.provenance else tuple({"status": "UNKNOWN"} for _ in items)
                receipt = NewsAIResultReceipt(provenance, True)
                analyzed_at = self._now().isoformat()
            retained_own, retained_shared = _retain_matching_body_projections(
                connection, stock_code, tuple(news_identity(item) for item in items), receipt.provenance,
                str(document.get("stock_name") or "") if document is not None else envelope.stock_name)
            published_indices = tuple(index for index in range(count) if index not in retained_own)
            shared_indices = tuple(index for index in range(count)
                                   if index not in retained_shared and envelope.results[index].company_impacts)
            for index in range(count):
                item = items[index]
                _save_analysis(connection, stock_code, news_identity(item), envelope.provider, envelope.model,
                               envelope.body_hashes[index], envelope.results[index], analyzed_at, receipt.provenance[index],
                               publish_own=index in published_indices, publish_shared=index in shared_indices)
        return replace(receipt, published=bool(published_indices), published_indices=published_indices,
                       shared_published_indices=shared_indices)

    def list_theme_suggestions(self, limit: int = 1000) -> tuple[ThemeSuggestion, ...]:
        with sqlite_read_connection(self._database_path) as connection:
            rows = connection.execute(
                "SELECT a.stock_code,a.identity,a.provider,a.model,a.body_hash,a.analyzed_at,"
                "a.theme_candidates,COALESCE(n.title,''),n.published_at,"
                "COALESCE(NULLIF(n.original_link,''),NULLIF(n.link,''),"
                "CASE WHEN a.identity LIKE 'http%' THEN a.identity ELSE '' END) "
                "FROM stock_news_ai a LEFT JOIN stock_news n "
                "ON n.stock_code=a.stock_code AND n.identity=a.identity "
                "WHERE a.theme_candidates<>'[]' ORDER BY a.analyzed_at DESC LIMIT ?",
                (max(1, min(10_000, int(limit))),),
            ).fetchall()
        result: list[ThemeSuggestion] = []
        for row in rows:
            for candidate in _decode_theme_candidates(str(row[6])):
                result.append(ThemeSuggestion(
                    stock_code=str(row[0]), news_identity=str(row[1]),
                    raw_theme_name=candidate.name, evidence=candidate.evidence,
                    confidence=candidate.confidence, provider=str(row[2]), model=str(row[3]),
                    body_hash=str(row[4]), analyzed_at=datetime.fromisoformat(str(row[5])),
                    article_title=str(row[7]), article_published_at=_optional_datetime(row[8]),
                    article_url=str(row[9]),
                ))
        return tuple(result)

    def daily_count(self) -> int:
        today = as_kst(self._now()).date().isoformat()
        with sqlite_read_connection(self._database_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM news_ai_requests WHERE substr(requested_at, 1, 10)=?", (today,)
            ).fetchone()
        return int(row[0]) if row else 0

    def log_request(self, provider: str, model: str, request_mode: str, event_count: int,
                    article_count: int, usage: AIRequestUsage) -> None:
        with sqlite_transaction(self._database_path) as connection:
            connection.execute(
                "INSERT INTO news_ai_requests(requested_at,provider,model,request_mode,event_count,article_count,input_tokens,output_tokens,total_tokens) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (as_kst(self._now()).isoformat(), provider, model, request_mode, event_count, article_count,
                 usage.input_tokens, usage.output_tokens, usage.total_tokens),
            )

    def daily_usage(self) -> tuple[int, int, int, int]:
        today = as_kst(self._now()).date().isoformat()
        with sqlite_read_connection(self._database_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0) "
                "FROM news_ai_requests WHERE substr(requested_at,1,10)=?", (today,),
            ).fetchone()
        return tuple(map(int, row or (0, 0, 0, 0)))


def _retain_matching_body_projections(connection, stock_code, requested_identities, provenance, stock_name=""):
    """One batch current-source cut protects an accepted matching projection.

    Captured request items may be old. Read the stored article refs under the
    same native writer transaction; keep both actual immutable inferences.
    """
    identities = set(requested_identities)
    def member_ids(reference):
        binding = decode_news_ai_provenance(reference).get("input_binding", {})
        return {str(member["identity"]) for member in binding.get("members", ())
                if isinstance(member, dict) and member.get("identity")}
    for reference in provenance:
        identities.update(member_ids(reference))
    sources, prior, shared = {}, {}, {}
    def read_batch(requested):
        ordered = sorted(requested)
        sql = ("WITH wanted(identity) AS (VALUES " + ",".join("(?)" for _ in ordered) + ") "
               "SELECT w.identity,n.stock_code,n.input_source_json,a.provenance_json,s.provenance_json,s.company_impacts FROM wanted w "
               "LEFT JOIN stock_news n ON n.identity=w.identity "
               "LEFT JOIN stock_news_ai a ON a.stock_code=? AND a.identity=w.identity "
               "LEFT JOIN news_ai_shared s ON s.identity=w.identity")
        for identity, target, source, reference, shared_reference, impacts in connection.execute(sql, (*ordered, stock_code)).fetchall():
            source = decode_news_input_source(source)
            if native_news_source_sequence(source) is not None:
                source = {**source, "status": "REFERENCE"}  # Last proven no-rewind cut; UI UNKNOWN remains stored unchanged.
            sources.setdefault(str(target), {})[str(identity)] = source
            prior[str(identity)], shared[str(identity)] = reference, (shared_reference, impacts)
    read_batch(identities)
    additional = set()
    for reference in prior.values():
        additional.update(member_ids(reference))
    for reference, _impacts in shared.values():
        additional.update(member_ids(reference))
    if additional - identities:
        read_batch(additional - identities)
    own_sources = sources.get(stock_code, {})
    retained_own, retained_shared = set(), set()
    for index, identity in enumerate(requested_identities):
        own = prior.get(identity)
        reference, impacts = shared.get(identity, (None, None))
        applies = own is None and _shared_company_impact(impacts, stock_name) is not None
        displayed = own if own is not None else reference if applies else None
        if (news_ai_source_version_status(own_sources, displayed) == "CURRENT"
                and news_ai_source_version_status(own_sources, provenance[index]) != "CURRENT"):
            retained_own.add(index)
        frames = sources.values()
        if any(news_ai_source_version_status(frame, reference) == "CURRENT"
               and news_ai_source_version_status(frame, provenance[index]) != "CURRENT" for frame in frames):
            retained_shared.add(index)
    return retained_own, retained_shared


def _shared_company_impact(raw, stock_name):
    name = _normalized_company_name(stock_name)
    if not name:
        return None
    impacts = raw if isinstance(raw, tuple) else _decode_impacts(str(raw or "[]"))
    return next((impact for impact in impacts
                 if _normalized_company_name(impact.company) == name), None)


def _decode_impacts(raw: str) -> tuple[AICompanyImpact, ...]:
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    return tuple(
        AICompanyImpact(str(value.get("company", "")), str(value.get("outlook", "판단 자료 부족")),
                        int(value.get("confidence", 0)), str(value.get("reason", "")))
        for value in values if isinstance(value, dict) and value.get("company")
    )


def _decode_theme_candidates(raw: str) -> tuple[AIThemeCandidate, ...]:
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(
        AIThemeCandidate(
            str(value.get("name", "")), max(0, min(100, int(value.get("confidence", 0)))),
            str(value.get("evidence", "")),
        )
        for value in values
        if isinstance(value, dict) and str(value.get("name", "")).strip()
    )


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _save_analysis(
    connection: sqlite3.Connection, stock_code: str, identity: str, provider: str, model: str,
    body_hash: str, analysis: AINewsAnalysis, analyzed_at: str, provenance: dict[str, object],
    *, publish_own: bool = True, publish_shared: bool = True,
) -> None:
    """The existing own/shared SQL is shared by single and atomic batch writes."""
    provenance_json = json.dumps(provenance, ensure_ascii=False, separators=(",", ":"))
    if publish_own:
        connection.execute(
        "INSERT INTO stock_news_ai(stock_code, identity, provider, model, summary, category, outlook, confidence, reason, positive_evidence, negative_evidence, body_hash, analyzed_at, theme_candidates, provenance_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(stock_code, identity) DO UPDATE SET "
        "provider=excluded.provider, model=excluded.model, summary=excluded.summary, outlook=excluded.outlook, "
        "category=excluded.category, "
        "confidence=excluded.confidence, reason=excluded.reason, positive_evidence=excluded.positive_evidence, "
        "negative_evidence=excluded.negative_evidence, body_hash=excluded.body_hash, analyzed_at=excluded.analyzed_at, "
        "theme_candidates=excluded.theme_candidates, provenance_json=excluded.provenance_json",
        (stock_code, identity, provider, model, analysis.summary, analysis.category, analysis.outlook,
         analysis.confidence, analysis.reason, json.dumps(analysis.positive_evidence, ensure_ascii=False),
         json.dumps(analysis.negative_evidence, ensure_ascii=False), body_hash, analyzed_at,
         json.dumps([candidate.__dict__ for candidate in analysis.theme_candidates], ensure_ascii=False), provenance_json),
    )
    if publish_shared and analysis.company_impacts:
        connection.execute(
            "INSERT INTO news_ai_shared(identity,provider,model,summary,category,positive_evidence,negative_evidence,company_impacts,body_hash,analyzed_at,provenance_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET provider=excluded.provider,model=excluded.model,"
            "summary=excluded.summary,category=excluded.category,positive_evidence=excluded.positive_evidence,"
            "negative_evidence=excluded.negative_evidence,company_impacts=excluded.company_impacts,body_hash=excluded.body_hash,analyzed_at=excluded.analyzed_at,provenance_json=excluded.provenance_json",
            (identity, provider, model, analysis.summary, analysis.category,
             json.dumps(analysis.positive_evidence, ensure_ascii=False),
             json.dumps(analysis.negative_evidence, ensure_ascii=False),
             json.dumps([impact.__dict__ for impact in analysis.company_impacts], ensure_ascii=False),
             body_hash, analyzed_at, provenance_json),
        )



def _inference_document(value: object) -> dict[str, object]:
    """Validate a frozen local record without normalizing any historical value."""
    if not isinstance(value, dict):
        raise ValueError("뉴스 AI 추론 원본 형식이 올바르지 않습니다.")
    try:
        document = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        if not isinstance(document.get("inference_id"), str):
            raise ValueError("inference_id is not text")
        UUID(document["inference_id"])
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("뉴스 AI 추론 ID 또는 원본 형식이 올바르지 않습니다.") from error
    if (type(document.get("input_envelope_version")) is not int or document["input_envelope_version"] != 1
            or document.get("result_schema_version") != "news-ai-value-v1"
            or "content_hash" in document
            or any(not isinstance(document.get(key), str) or not document[key]
                   for key in ("target_id", "provider", "model", "prompt_version", "schema_version"))
            or not isinstance(document.get("stock_name"), str)):
        raise ValueError("뉴스 AI 추론 계약이 올바르지 않습니다.")
    moment = document.get("computed_at")
    if not isinstance(moment, (int, float)) or isinstance(moment, bool) or not math.isfinite(moment):
        raise ValueError("뉴스 AI 추론 계산 시각이 올바르지 않습니다.")
    inputs, outputs = document.get("inputs"), document.get("outputs")
    if not isinstance(inputs, list) or not inputs or not isinstance(outputs, list) or len(inputs) != len(outputs):
        raise ValueError("뉴스 AI 추론 입력과 결과 개수가 다릅니다.")
    for source, output in zip(inputs, outputs, strict=True):
        if (not isinstance(source, dict) or not isinstance(output, dict)
                or any(not isinstance(source.get(key), str) for key in
                       ("identity", "title", "body", "input_hash", "body_content_hash", "article_revision_id", "body_revision_id"))
                or not source["identity"] or not source["input_hash"]):
            raise ValueError("뉴스 AI 추론 입력 원본이 올바르지 않습니다.")
        try:
            validate_news_ai_input(source)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("뉴스 AI 추론 입력 출처 hash가 다릅니다.") from error
    return document


def _validate_local_inference(
    stock_code: str, items: tuple[StockNewsItem, ...], envelope: NewsAIResultEnvelope,
) -> dict[str, object]:
    document = _inference_document(envelope.inference)
    if (document["target_id"] != stock_code or document["provider"] != envelope.provider
            or document["model"] != envelope.model or document["prompt_version"] != ANALYSIS_PROMPT_VERSION
            or document["schema_version"] != NEWS_ANALYSIS_SCHEMA_VERSION
            or len(document["inputs"]) != len(items)
            or envelope.article_count != sum(len(source["source_snapshot"]["members"]) for source in document["inputs"])):
        raise ValueError("뉴스 AI 추론 대상 또는 공급자·개수가 결과와 다릅니다.")
    for index, item in enumerate(items):
        source = document["inputs"][index]
        if (source["identity"] != news_identity(item) or source["title"] != item.title
                or source["input_hash"] != envelope.body_hashes[index]
                or source["input_hash"] != analysis_body_hash(document["stock_name"], source["body"])
                or document["outputs"][index] != news_ai_analysis_payload(envelope.results[index])):
            raise ValueError("뉴스 AI 추론 입력 순서 또는 결과 원본이 다릅니다.")
    return document


def _local_receipt(document: dict[str, object], content_hash: str, available_at: float, *, published: bool) -> NewsAIResultReceipt:
    reference = {"inference_id": document["inference_id"], "content_hash": content_hash}
    provenance = tuple(decode_news_ai_provenance({
        "version": 1, "status": "REPORTED", "origin": "repository_recorded",
        "target_id": document["target_id"], "provider": document["provider"], "model": document["model"],
        "prompt_version": document["prompt_version"], "schema_version": document["schema_version"],
        "computed_at": document["computed_at"], "available_at": available_at,
        "article_revision_id": source["article_revision_id"], "body_revision_id": source["body_revision_id"],
        "input_hash": source["input_hash"], "output_hash": stable_document_hash(document["outputs"][index]),
        "input_binding": deepcopy(source["input_binding"]), "inference_ref": {**reference, "input_index": index},
    }) for index, source in enumerate(document["inputs"]))
    return NewsAIResultReceipt(provenance, published, reference, available_at)

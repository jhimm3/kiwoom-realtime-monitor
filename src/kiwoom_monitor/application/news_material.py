"""Point-in-time revisions of an explicitly identified news or disclosure event."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time
from typing import Iterable, Mapping
from urllib.parse import parse_qs, urlsplit

from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


_STAGES = frozenset({"REPORT", "CONFIRMED", "CORRECTED", "WITHDRAWN"})
_EFFECTS = frozenset({"UNKNOWN", "NONFUNDAMENTAL", "FUNDAMENTAL_ADVERSE", "INTACT", "DAMAGED"})
_RECEIPT_NUMBER = re.compile(r"[0-9]{14}")
_URL = re.compile(r"https?://[^\s<>\"']+")
_CORRECTION = re.compile(r"^\[(?:기재정정|첨부정정|정정)\]")
_WITHDRAWAL = re.compile(r"^(?:\[(?:철회|철회신고)\]|단일판매[ㆍ·・]?공급계약(?:해지|해제|철회))")
_DIRECT_ADVERSE = {
    "횡령배임혐의발생": "embezzlement_breach_allegation",
    "횡령배임사실확인": "embezzlement_breach_confirmed",
    "회생절차개시신청": "rehabilitation_filed",
    "회생절차개시결정": "rehabilitation_opened",
    "부도발생": "default",
    "영업정지": "business_suspension",
}
_ORIGINAL_FILING_LINE = re.compile(r"(?:원공시|정정대상\s*공시|철회대상\s*공시|관련\s*공시)[^\r\n]{0,180}")
_TERMINATION_TITLE = re.compile(r"^(?:\[(?:기재정정|첨부정정|정정|철회|철회신고)\])*단일판매[ㆍ·・]?공급계약(?:해지|해제)$")
_AWARD_TITLE = re.compile(r"단일판매[ㆍ·・]?공급계약체결")
_NOTICE_RECEIVED = re.compile(
    r"계약(?:해지|해제)(?:통지|통보)(?:공문)?(?:을|를)?(?:수령|접수)"
    r"(?:한날입니다|하였습니다|했습니다|하였다|했다|하였음|했음|함)[.!]?$")
_NOTICE_CANCELLED_AND_AMENDED = re.compile(
    r"계약(?:해지|해제)(?:통지|통보)(?:을|를)?(?:취소|철회)하고"
    r"(?:\d{4}년\d{1,2}월\d{1,2}일)?(?:계약금액(?:및계약기간)?|계약기간)변경에"
    r"합의(?:하였습니다|했습니다|하였다|했다|하였음|했음|함)[.!]?$")
_TERMINATED_STATUS = re.compile(r"(?:계약(?:이|은)?)?(?:해지|해제)(?:확정|완료|되었습니다|되었음|되었다|됨)?[.!]?")
_REVOKED = re.compile(r"(?:해지|해제)(?:자체|통보|통지|결정)?(?:를|을|는|은|가|이)?(?:철회|취소)"
                      r"(?:함|하였음|하였습니다|하였다|했음|했습니다|했다|되었음|되었습니다|되었다|됐습니다|됐다|됨|합니다|한다)[.!]?$")
_BARE_REVOCATION = re.compile(r"(?:계약)?(?:해지|해제)(?:자체|통보|통지|결정)?(?:철회|취소)")
_PROVISIONAL = re.compile(r"가처분|효력(?:의)?정지|임시(?:로)?회복")
_STATE_UNCERTAIN = re.compile(r"가처분|효력(?:의)?정지|임시(?:로)?회복|(?:해지|해제)(?:가|는|의)?(?:무효|여부|범위)"
                              r"|(?:해지|해제)(?:자체|통보|통지|결정|공시)?(?:를|가)?(?:철회|취소)|계약상태")
_NONFINAL = re.compile(r"예정|요청|검토|조건|경우|가능|협의중|가처분|소송|신청|계획|지않")
_TERM_LABEL = re.compile(r"^(?:해지내역|해지금액|계약금액|최근매출액|매출액대비|계약기간|해지일자"
                         r"|(?:계약)?(?:시작일|종료일|일정)|유보기한|비중)")
_TERM_VALUE = re.compile(r"^[\d.,%/()~:+\-년월일원억만천달러EURWKUSD부터까지및]+$")
_TERM_NOTE = re.compile(r"계약금액|해지금액|매출액|비중|이행금액|이행률|손해배상금")
_NUMERIC_VALUE = re.compile(r"\d+(?:[.,/-]\d+)*")
_REVENUE_NOTE = re.compile(r"^(?:상기)?(?:최근)?매출액(?:과매출액대비비중)?은\d{4}년연결재무제표기준입니다[.]"
                           r"\d{4}년연결재무제표기준매출액은[\d,]+원,매출액대비비중은[\d.]+%입니다[.]?$")
RECORDED_CONTRACT_FILINGS = "news_contract_filing"


@dataclass(frozen=True)
class ContractCorrection:
    outcome: str
    changes: tuple[tuple[str, str, str], ...]
    body_ref: str
    supersedes: str | None = None

    @classmethod
    def from_document(cls, row: object) -> ContractCorrection:
        if (not isinstance(row, Mapping) or set(row) != set(cls.__dataclass_fields__)
                or row["outcome"] not in ("TERMS_ONLY", "TERMINATION_REVOKED", "UNRESOLVED")
                or not isinstance(row["body_ref"], str) or not row["body_ref"].strip()
                or row["supersedes"] is not None and not isinstance(row["supersedes"], str)
                or not isinstance(row["changes"], (list, tuple)) or not row["changes"]
                or any(not isinstance(change, (list, tuple)) or len(change) != 3
                       or not all(isinstance(cell, str) for cell in change) for change in row["changes"])):
            raise ValueError("invalid contract correction evidence")
        return cls(row["outcome"], tuple(tuple(change) for change in row["changes"]),
                   row["body_ref"], row["supersedes"])


@dataclass(frozen=True)
class MaterialRevision:
    symbol: str
    ref: str
    revises: str | None
    stage: str
    effect: str
    effective_at: datetime
    available_at: datetime
    source_ref: str
    source_version: str
    correction: ContractCorrection | None = None

    @classmethod
    def from_document(cls, row: Mapping[str, object]) -> MaterialRevision:
        fields = set(cls.__dataclass_fields__) - {"correction"}
        if not isinstance(row, Mapping) or not fields <= set(row) <= fields | {"correction"}:
            raise ValueError("invalid material news revision fields")
        for key in ("symbol", "ref", "source_ref", "source_version", "stage", "effect"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError("material news revision requires source and identity")
        if (row["stage"] not in _STAGES or row["effect"] not in _EFFECTS
                or row["revises"] is not None and (not isinstance(row["revises"], str) or not row["revises"].strip())):
            raise ValueError("invalid material news revision stage, effect or link")
        if not all(isinstance(row[key], str) for key in ("effective_at", "available_at")):
            raise ValueError("material news timestamps require explicit offsets")
        effective, available = (aware_iso_kst(row[key]) for key in ("effective_at", "available_at"))
        if effective > available:
            raise ValueError("material news effect occurs after its availability")
        correction = ContractCorrection.from_document(row["correction"]) if row.get("correction") is not None else None
        if correction is not None and (row["stage"] != "CORRECTED"
                or correction.supersedes is not None and correction.supersedes != row["revises"]
                or row["effect"] != "UNKNOWN" and (correction.outcome, row["effect"]) != ("TERMINATION_REVOKED", "INTACT")):
            raise ValueError("contract correction must be attached to its source revision")
        return cls(**{**row, "effective_at": effective, "available_at": available, "correction": correction})


@dataclass(frozen=True)
class MaterialNews:
    chains: tuple[tuple[MaterialRevision, ...], ...]

    @classmethod
    def from_documents(cls, rows: object) -> MaterialNews:
        if not isinstance(rows, (list, tuple)):
            raise ValueError("material news revisions must be an array")
        revisions: dict[str, MaterialRevision] = {}
        for raw in rows:
            row = MaterialRevision.from_document(raw)
            if revisions.setdefault(row.ref, row) != row:
                raise ValueError("conflicting material news revision reference")
        roots: dict[str, str] = {}
        chains: dict[str, list[MaterialRevision]] = {}
        for row in sorted(revisions.values(), key=lambda item: (item.available_at, item.effective_at, item.ref)):
            root = row.ref
            if row.revises is not None:
                parent = revisions.get(row.revises)
                if parent is None or parent.symbol != row.symbol or parent.ref not in roots:
                    raise ValueError("material news revision requires an earlier same-stock source link")
                # A cancellation received first can become usable with its late official notice.
                if parent.available_at == row.available_at and not (
                    row.correction is not None and row.correction.outcome == "TERMINATION_REVOKED"
                    and row.correction.supersedes is None and parent.stage == "CONFIRMED"
                    and parent.effect == "FUNDAMENTAL_ADVERSE" and parent.effective_at < row.effective_at
                    and official_filing_ref(parent.source_ref) and official_filing_ref(row.source_ref)
                ):
                    raise ValueError("simultaneous material revisions require an official late source link")
                if row.correction is not None and row.correction.supersedes is not None and (
                    parent.stage != "CORRECTED" or parent.correction is not None
                    or parent.effect != "UNKNOWN" or parent.source_ref != row.source_ref
                    or parent.effective_at != row.effective_at
                ):
                    raise ValueError("late correction evidence must enrich its own earlier filing")
                root = roots[parent.ref]
            elif row.stage in {"CORRECTED", "WITHDRAWN"}:
                raise ValueError("material news correction or withdrawal requires a source link")
            roots[row.ref] = root
            chains.setdefault(root, []).append(row)
        return cls(tuple(tuple(chain) for chain in chains.values()))

    def as_of(self, symbol: str, cut: datetime):
        """A later report cannot overwrite a confirmed, corrected or withdrawn fact."""
        for chain in self.chains:
            if chain[0].symbol != symbol:
                continue
            visible = tuple(row for row in chain if row.available_at <= cut)
            if not visible:
                continue
            facts = _material_facts(visible)
            latest = facts[-1][1] if facts else visible[0]
            basis = _material_state(facts)
            effect = basis.effect if basis is not None else "UNKNOWN"
            yield chain[0].ref, latest, effect, visible


def decision_material_context(
    material: MaterialNews, symbol: str, cut: datetime, *, entry_cut: datetime | None = None,
) -> dict:
    """Keep the visible event version and distinguish later changes from the entry cut."""
    def visible(at):
        return {
            root: {
                "event_ref": root, "revision_ref": current.ref, "source_ref": current.source_ref,
                "stage": current.stage, "effect": effect, "effective_at": current.effective_at.isoformat(),
                "available_at": current.available_at.isoformat(),
            }
            for root, current, effect, _ in material.as_of(symbol, at)
        }

    current = visible(cut)
    result = {"factor_id": "strategy_material_news/v1", "cut": cut.isoformat(),
              "status": "OBSERVED" if current else "UNKNOWN", "events": list(current.values())}
    if entry_cut is not None:
        before = visible(entry_cut)
        result.update(entry_cut=entry_cut.isoformat(), entry_events=list(before.values()),
                      changes_after_entry=[event for root, event in current.items() if before.get(root) != event])
    return result


def _material_facts(visible: tuple[MaterialRevision, ...]):
    enriched = {row.correction.supersedes: row for row in visible
                if row.correction is not None and row.correction.supersedes is not None}
    return [(row.available_at, enriched.get(row.ref, row)) for row in visible if row.stage != "REPORT"
            and (row.correction is None or row.correction.supersedes is None)]


def material_state_revision(rows: tuple[MaterialRevision, ...]) -> MaterialRevision | None:
    """Read the status at the filing's original position after its body is visible."""
    return _material_state(_material_facts(rows))


def _material_state(facts: list[tuple[datetime, MaterialRevision]]) -> MaterialRevision | None:
    facts = [(cut, row) for cut, row in facts
             if row.correction is None or row.correction.outcome != "TERMS_ONLY"]
    if not facts:
        return None
    latest_cut, latest = facts[-1]
    effects = {row.effect for cut, row in facts if cut == latest_cut and row.ref != latest.revises}
    return latest if len(effects) == 1 else None


def _contract_changes(rows: object) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(rows, (list, tuple)):
        return ()
    table = []
    columns = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or not all(isinstance(cell, str) for cell in row):
            return ()
        compact = tuple("".join(cell.split()) for cell in row)
        if columns is None:
            if compact and compact[0] in {"정정항목", "항목"} and {"정정전", "정정후"} <= set(compact):
                columns = len(row), compact.index("정정전"), compact.index("정정후")
            continue
        width, before, after = columns
        if len(row) != width or not compact[0]:
            break
        if compact[before] != compact[after]:
            table.append((row[0].strip(), row[before].strip(), row[after].strip()))
    return tuple(table)


def read_contract_correction(rows: object, body_ref: str) -> ContractCorrection | None:
    """Compare the published before/after cells, without interpreting free-form news."""
    table = _contract_changes(rows)
    if not table:
        return None
    cells = [tuple("".join(cell.split()) for cell in row) for row in table]
    if any(_is_revoked(label, after) and not _is_revoked(label, before) for label, before, after in cells) and not any(
        _PROVISIONAL.search(after) for _, _, after in cells
    ):
        outcome = "TERMINATION_REVOKED"
    elif all(_is_terms_edit(label, before, after) for label, before, after in cells):
        outcome = "TERMS_ONLY"
    else:
        outcome = "UNRESOLVED"
    return ContractCorrection(outcome, table, body_ref)


def is_contract_award_correction(title: str) -> bool:
    compact = "".join(title.split())
    prefix = _CORRECTION.match(compact)
    return bool(prefix and _AWARD_TITLE.fullmatch(compact[prefix.end():]))


def read_contract_award_change(rows, body_ref: str) -> tuple[tuple[str, str, str], ContractCorrection | None] | None:
    """Read an actual notice or reversal from an official contract-award correction."""
    if not isinstance(rows, (list, tuple)):
        return None
    fields: dict[str, set[str]] = {}
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) == 2 and all(isinstance(cell, str) for cell in row):
            label, value = ("".join(cell.split()) for cell in row)
            label = re.sub(r"^(?:\d+\.|-)", "", label)
            fields.setdefault(label, set()).add(value)
    if not any(_AWARD_TITLE.fullmatch(value) for value in fields.get("정정관련공시서류", ())):
        return None
    identity = []
    for label in ("체결계약명", "계약상대", "계약(수주)일자"):
        values = fields.get(label, set())
        if len(values) != 1 or next(iter(values)) in {"", "-", "미정"}:
            return None
        identity.append(next(iter(values)))
    try:
        awarded = date.fromisoformat(identity[2].replace(".", "-")).isoformat()
    except ValueError:
        return None
    contract = (identity[0], identity[1], awarded)

    def states(label, cell):
        sentences = re.split(r"(?<=[.!?])\s+|\s+(?=-\s*[^\d])", cell)
        result = set()
        for sentence in sentences:
            text = "".join(sentence.split())
            if not _NONFINAL.search(text):
                if _NOTICE_RECEIVED.search(text) or label == "계약상태" and _TERMINATED_STATUS.fullmatch(text):
                    result.add("terminated")
                if _is_revoked(label, text) or _NOTICE_CANCELLED_AND_AMENDED.search(text):
                    result.add("revoked")
        return result

    changes = _contract_changes(rows)
    before, after = set(), set()
    for label, old, new in changes:
        normalized = re.sub(r"^\d+\.", "", "".join(label.split()))
        if normalized.startswith("기타투자판단") or normalized == "계약상태":
            before.update(states(normalized, old))
            after.update(states(normalized, new))
    if after == {"terminated"} and "terminated" not in before:
        return contract, None
    if before == {"terminated"} and after == {"revoked"}:
        return contract, ContractCorrection("TERMINATION_REVOKED", changes, body_ref)
    return None


def _is_revoked(label: str, text: str) -> bool:
    if _NONFINAL.search(text):
        return False
    return bool(_REVOKED.search(text) or ("계약상태" in label and _BARE_REVOCATION.fullmatch(text)))


def _is_terms_edit(label: str, before: str, after: str) -> bool:
    label = re.sub(r"^\d+\.", "", label)
    if any(_STATE_UNCERTAIN.search(value) for value in (label, before, after)):
        return False
    if _TERM_LABEL.match(label):
        return any(cell.isdecimal() for cell in after) and _TERM_VALUE.fullmatch(after) is not None
    if label.startswith("기타투자판단"):
        if _REVENUE_NOTE.fullmatch(re.sub(r"^\d+\.", "", after)) is not None:
            return True
        return bool(_TERM_NOTE.search(after) and _NUMERIC_VALUE.sub("#", before) == _NUMERIC_VALUE.sub("#", after))
    return False


def official_filing_ref(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        url = urlsplit(value)
    except ValueError:
        return ""
    if url.scheme not in {"http", "https"}:
        return ""
    if url.hostname == "dart.fss.or.kr" and url.path in {"/dsaf001/main.do", "/api/link.jsp"}:
        provider, field = "dart", "rcpNo"
    elif url.hostname == "kind.krx.co.kr" and url.path == "/common/disclsviewer.do":
        provider, field = "kind", "acptno"
    elif url.hostname == "kind.krx.co.kr" and (
        external := re.fullmatch(r"/external/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]+/([0-9]{14})/[0-9]+\.htm", url.path)
    ):
        return f"kind:{external[1]}"
    else:
        return ""
    values = parse_qs(url.query).get(field, ())
    return f"{provider}:{values[0]}" if len(values) == 1 and _RECEIPT_NUMBER.fullmatch(values[0]) else ""


def is_contract_termination_filing(title: str) -> bool:
    return _TERMINATION_TITLE.fullmatch("".join(title.split())) is not None


def official_adverse_kind(title: str) -> str | None:
    compact = re.sub("[ㆍ·・]", "", "".join(title.split()))
    wrapped = re.fullmatch(r"주요사항보고서\(([^()]+)\)", compact)
    return _DIRECT_ADVERSE.get(wrapped[1] if wrapped else compact)


def _recorded_award_chain(filings: Iterable[tuple[Mapping, datetime, datetime]]) -> list[MaterialRevision]:
    chain = []
    pending = None
    for filing, effective, available in filings:
        proof = ContractCorrection.from_document(filing["correction"]) if filing.get("correction") else None
        previous = chain[-1] if chain else None
        if previous is not None and effective <= previous.effective_at:
            continue
        if proof is not None and previous is None:
            if pending is None or pending.effective_at < effective:
                pending = MaterialRevision(filing["symbol"], filing["filing_ref"], None, "CORRECTED", "INTACT",
                                           effective, available, filing["source_ref"], filing["body_ref"], proof)
            continue
        effect = "INTACT" if proof is not None else "FUNDAMENTAL_ADVERSE"
        if previous is not None and previous.effect == effect:
            continue
        row = MaterialRevision(filing["symbol"], filing["filing_ref"], previous.ref if previous else None,
                               "CORRECTED" if proof else "CONFIRMED", effect, effective, available,
                               filing["source_ref"], filing["body_ref"], proof)
        chain.append(row)
        if pending is not None:
            if pending.effective_at > effective:
                chain.append(replace(pending, revises=row.ref, available_at=available))
            pending = None
    return chain


def recorded_contract_material(filings: Iterable[Mapping[str, object]], cut: datetime) -> MaterialNews:
    """Read committed original/correction receipts without using publication as availability."""
    chains: dict[str, list[MaterialRevision]] = {}
    aliases, originals = {}, {}
    corrections, awards = [], {}
    for filing in sorted(filings, key=lambda item: (item["available_at"], item["filing_ref"])):
        available = _recorded_time(filing.get("available_at"))
        effective = _recorded_time(filing.get("effective_at"))
        if available is None or effective is None or not effective <= available <= cut:
            continue
        ref = filing["filing_ref"]
        source = filing["source_ref"]
        title = filing["title"]
        if contract := filing.get("contract_award_key"):
            key = filing["symbol"], contract
            mirrors = awards.setdefault(key, {})
            mirrors.setdefault((effective.date(), filing["rows_hash"]), (filing, effective, available))
            continue
        stage = "CONFIRMED" if official_adverse_kind(title) else "WITHDRAWN"
        row = MaterialRevision(filing["symbol"], ref, None, stage, "FUNDAMENTAL_ADVERSE",
                               effective, available, source, filing["body_ref"])
        mirror_key = (row.symbol, effective.date(), official_adverse_kind(title), filing["rows_hash"])
        if "".join(title.split()).startswith("["):
            corrections.append((row, filing))
        else:
            first = originals.setdefault(mirror_key, ref)
            aliases[ref] = first
            chains.setdefault(first, [row])
    distinct = {}
    for row, filing in corrections:
        parent = chains.get(aliases.get(filing.get("original_filing_ref")))
        if (parent is None or parent[0].symbol != row.symbol
                or parent[0].effective_at.date() > row.effective_at.date()):
            continue
        original = parent[0]
        key = (original.ref, row.effective_at.date(), filing["rows_hash"])
        distinct.setdefault(key, (row, filing, original))
    for row, filing, original in sorted(distinct.values(), key=lambda item: (item[0].effective_at, item[0].ref)):
        proof = ContractCorrection.from_document(filing["correction"]) if filing.get("correction") else None
        effect = "INTACT" if proof is not None and proof.outcome == "TERMINATION_REVOKED" else "UNKNOWN"
        chains[original.ref].append(MaterialRevision(
            row.symbol, row.ref, original.ref, "CORRECTED", effect, row.effective_at,
            max(row.available_at, original.available_at), row.source_ref, row.source_version, proof,
        ))
    for mirrors in awards.values():
        if chain := _recorded_award_chain(mirrors.values()):
            chains[chain[0].ref] = chain
    return MaterialNews(tuple(tuple(chain) for chain in chains.values()))


def received_strategy_material(symbol: str, cut: datetime, *, articles, originals, filings, partial=False):
    """Separate reports and accepted originals from the facts used by existing entry rules."""
    official = recorded_contract_material(filings, cut)
    official_refs = {official_filing_ref(row.source_ref) for chain in official.chains for row in chain}
    receipts = {}
    for row in originals:
        available = _recorded_time(row.get("available_at"))
        source = official_filing_ref(row.get("source_ref"))
        if source and available is not None and available <= cut:
            key = source, row["article_revision_id"]
            if key not in receipts or available < receipts[key][0]:
                receipts[key] = available, row

    verified, reports, titles = [], [], {}
    for row in articles:
        document = row["document"]
        source = str(document.get("original_link") or document.get("link") or "")
        filing_ref = official_filing_ref(source)
        available = _recorded_time(row["available_at"])
        published = _recorded_time(row["published_at"])
        if available is None or available > cut or (published is not None and published > available):
            continue
        title = str(document.get("title") or "")
        titles.setdefault(source, title)
        if filing_ref in official_refs:
            continue  # Already represented by the authoritative fact chain.
        receipt = receipts.get((filing_ref, row["article_revision_id"]))
        if receipt is not None and not (is_contract_termination_filing(title) or official_adverse_kind(title)):
            accepted_at, original = receipt
            verified.append({**row, "article_revision_id": filing_ref, "stock_code": symbol,
                             "available_at": max(available, accepted_at).isoformat(),
                             "content_hash": original["body_ref"], "document": {
                                 **document, "original_link": original["source_ref"],
                                 "original_filing_url": original["original_filing_url"],
                             }})
        report = {
            "revision_ref": row["article_revision_id"], "source_ref": source, "title": title,
            "stage": "REPORT", "effect": "UNKNOWN", "available_at": available.isoformat(),
            "published_at": published.isoformat() if published is not None else None,
        }
        if receipt is not None:
            report.update(original_received_at=receipt[0].isoformat(), body_ref=receipt[1]["body_ref"])
        reports.append((row["identity"], report))

    # A mirror with identical official identity is still one filing. Retain its first accepted view.
    unique = {}
    for row in sorted(verified, key=lambda item: aware_iso_kst(item["available_at"])):
        unique.setdefault(row["article_revision_id"], row)
    received = derive_material_news(unique.values())["revisions"]
    # A matched original proves the filing was received. Only the validated
    # compact facts above establish its effect for a strategy.
    additional = MaterialNews.from_documents([{**row, "effect": "UNKNOWN"} for row in received])
    known = {official_filing_ref(row.source_ref) for chain in additional.chains for row in chain}
    recent = {}
    for identity, report in reports:
        if official_filing_ref(report["source_ref"]) not in known:
            recent.setdefault(identity, report)
    material = MaterialNews((*official.chains, *additional.chains))
    context = decision_material_context(material, symbol, cut)
    titles.update((filing["source_ref"], filing["title"]) for filing in filings)
    for event in context["events"]:
        event["title"] = titles.get(event["source_ref"], "")
    context.update(recent_reports=list(recent.values()), partial=partial)
    if recent:
        context["status"] = "OBSERVED"
    return official, context


def _filing_stage(title: str) -> tuple[str, str]:
    compact = "".join(title.split())
    if _WITHDRAWAL.match(compact):
        effect = "FUNDAMENTAL_ADVERSE" if compact.startswith("단일판매") else "UNKNOWN"
        return "WITHDRAWN", effect
    if _CORRECTION.match(compact):
        return "CORRECTED", "UNKNOWN"
    if compact in {"단일판매ㆍ공급계약체결", "단일판매·공급계약체결", "단일판매공급계약체결"}:
        return "CONFIRMED", "INTACT"
    return "CONFIRMED", "FUNDAMENTAL_ADVERSE" if official_adverse_kind(title) else "UNKNOWN"


def _recorded_time(value: object) -> datetime | None:
    try:
        if type(value) in (int, float):
            return datetime.fromtimestamp(value, KST)
        if isinstance(value, str):
            return aware_iso_kst(value)
    except (ValueError, OSError, OverflowError):
        pass
    return None


def derive_material_news(articles: Iterable[Mapping[str, object]],
                         events: Iterable[Mapping[str, object]] = (),
                         bodies: Iterable[Mapping[str, object]] = ()) -> dict:
    """Project saved article/disclosure and non-AI RULE receipts into research input.

    Explicit original filing links and existing RULE revision lineage can link
    sources. Similarity suggestions, article sentiment and AI output cannot.
    """
    original_links, correction_bodies, award_bodies = {}, {}, {}
    for row in bodies:
        cut = _recorded_time(row.get("available_at"))
        ref = row.get("article_revision_id")
        if cut is None or row.get("status") != "fulltext" or not isinstance(ref, str):
            continue
        links = {key for line in _ORIGINAL_FILING_LINE.findall(str(row.get("body_text") or ""))
                 for value in _URL.findall(line) if (key := official_filing_ref(value.rstrip(".,);]")))}
        if len(links) == 1:
            original_links.setdefault(ref, []).append((cut, next(iter(links))))
        filing = official_filing_ref(row.get("filing_url"))
        body_ref = row.get("body_revision_id") or row.get("filing_url")
        if filing and isinstance(body_ref, str) and body_ref.strip():
            filing_rows = row.get("filing_rows")
            if change := read_contract_award_change(filing_rows, body_ref):
                identity, proof = change
                award_bodies.setdefault((ref, filing), []).append((cut, body_ref, identity, proof, _contract_changes(filing_rows)))
            if correction := read_contract_correction(filing_rows, body_ref):
                correction_bodies.setdefault((ref, filing), []).append((cut, correction))

    rules, global_targets = {}, {}
    for row in events:
        if (row.get("scope") != "TARGET_COMPANY" or row.get("role") != "FACT"
                or row.get("novelty") == "REPUBLICATION"):
            continue
        cut = _recorded_time(row.get("available_at"))
        key = row.get("stock_code"), row.get("article_revision_id")
        if cut is not None and (key not in rules or (cut, str(row.get("event_revision_id"))) <
                                (rules[key][0], str(rules[key][1].get("event_revision_id")))):
            rules[key] = cut, row
            global_targets.setdefault(key[1], set()).add(key[0])

    def target_articles():
        for article in articles:
            original_ref = article.get("article_revision_id")
            if article.get("stock_code") == "GLOBAL":
                for target in sorted(str(code) for code in global_targets.get(original_ref, ()) if isinstance(code, str)):
                    yield {**article, "stock_code": target, "article_revision_id": f"{original_ref}:{target}"}, original_ref
            else:
                yield article, original_ref

    candidates, award_candidates, unresolved, duplicates = {}, {}, [], set()
    for article, original_ref in target_articles():
        symbol, ref = article.get("stock_code"), article.get("article_revision_id")
        if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii() or not symbol.isalnum():
            continue
        document = article.get("document")
        if not isinstance(ref, str) or not ref or not isinstance(document, Mapping):
            continue
        title = str(document.get("title") or "")
        source = str(document.get("original_link") or document.get("link") or ref)
        filing = official_filing_ref(source)
        reference = official_filing_ref(document.get("original_filing_url"))
        if not filing:
            links = {key for value in _URL.findall(str(document.get("description") or ""))
                     if (key := official_filing_ref(value.rstrip(".,);]")))}
            if len(links) == 1:
                reference = next(iter(links))
        stage, effect = _filing_stage(title) if filing else ("REPORT", "UNKNOWN")
        compact_title = "".join(title.split())
        contract_edit = bool(filing and compact_title.startswith("[") and _TERMINATION_TITLE.fullmatch(compact_title))
        if contract_edit:
            stage, effect = "CORRECTED", "UNKNOWN"
        available = _recorded_time(article.get("available_at"))
        published = _recorded_time(article.get("published_at"))
        if available is None or (published is not None and published > available):
            unresolved.append({"ref": ref, "stage": stage, "reason": "availability_unverified"})
            continue
        effective = published or available
        if filing:
            try:
                filing_day = date.fromisoformat(filing.split(":", 1)[1][:8])
            except ValueError:
                filing_day = None
            if filing_day is None or filing_day > available.date():
                unresolved.append({"ref": ref, "stage": stage, "reason": "filing_day_unverified"})
                continue
            publication_day = max(filing_day, published.date()) if published is not None else filing_day
            effective = min(available, datetime.combine(publication_day, time.max, KST))
        if filing and is_contract_award_correction(title):
            saved = award_bodies.get((original_ref, filing), ())
            first_cut = min((item[0] for item in saved), default=None)
            first = {item[1:] for item in saved if item[0] == first_cut}
            if len(first) == 1:
                body_ref, identity, proof, changes = next(iter(first))
                body_available = max(available, first_cut)
                if original_ref != ref:
                    body_available = max(body_available, rules[symbol, original_ref][0])
                if published is not None and published.date() == filing_day and published.time() != time.min:
                    effective = published
                record = dict(filing_ref=ref, source_ref=source, title=title, symbol=symbol, body_ref=body_ref,
                              available_at=body_available.isoformat(), effective_at=effective.isoformat(),
                              contract_award_key=identity, rows_hash=changes, correction=asdict(proof) if proof else None)
                if award_candidates.setdefault(ref, record) != record:
                    duplicates.add(ref)
                continue
        if filing and stage in {"CORRECTED", "WITHDRAWN"} and not reference:
            saved = original_links.get(original_ref, ())
            if saved:
                first_cut = min(cut for cut, _ in saved)
                first_links = {link for cut, link in saved if cut == first_cut}
                if len(first_links) == 1:
                    reference = next(iter(first_links))
                    available = max(available, first_cut)
        scoped_rule = rules.get((symbol, original_ref))
        if original_ref != ref:
            available = max(available, scoped_rule[0])
        rule = None if reference else scoped_rule
        if rule is not None:
            available = max(available, rule[0])
        version = str(article.get("content_hash") or ref)
        saved_corrections = correction_bodies.get((original_ref, filing), ()) if contract_edit else ()
        correction_receipt = None
        if saved_corrections:
            first_cut = min(cut for cut, _ in saved_corrections)
            first_bodies = {body for cut, body in saved_corrections if cut == first_cut}
            if len(first_bodies) == 1:
                correction_receipt = (first_cut, next(iter(first_bodies)))
        candidate = (available, ref, symbol, stage, effect, source, filing, reference, rule, effective, version,
                     correction_receipt)
        if candidates.setdefault(ref, candidate) != candidate:
            duplicates.add(ref)

    revisions, anchors, rule_refs, latest, roots, reports = [], {}, {}, {}, {}, {}
    confirmed_filings, terminated = set(), set()
    for available, ref, symbol, stage, effect, source, filing, reference, rule, effective, version, receipt in sorted(
        candidates.values(), key=lambda row: row[:3],
    ):
        if ref in duplicates:
            unresolved.append({"ref": ref, "stage": stage, "reason": "conflicting_saved_revision"})
            continue
        if filing and (symbol, filing) in anchors:
            continue
        explicit = anchors.get((symbol, reference)) if reference else None
        if stage == "CONFIRMED" and not reference:
            explicit = reports.get((symbol, filing))
        rule_parent = None
        if rule is not None:
            entry = rule[1]
            rule_parent = rule_refs.get((symbol, entry.get("event_id"), entry.get("revision_of")))
        if filing and stage == "CONFIRMED" and not explicit and rule_parent and roots[rule_parent] in confirmed_filings:
            unresolved.append({"ref": ref, "stage": stage, "reason": "distinct_official_filing_unlinked"})
            rule_parent = None
        if explicit and rule_parent and roots[explicit] != roots[rule_parent]:
            unresolved.append({"ref": ref, "stage": stage, "reason": "conflicting_original_links"})
            continue
        # A RULE match on amount and counterparty cannot replace the original
        # filing reference for a correction or withdrawal.
        parent = explicit if stage in {"CORRECTED", "WITHDRAWN"} else (explicit or rule_parent)
        if stage in {"CORRECTED", "WITHDRAWN"} and not parent:
            unresolved.append({"ref": ref, "stage": stage, "reason": "original_filing_unverified"})
            continue
        if parent is not None and latest[parent] >= available:
            unresolved.append({"ref": ref, "stage": stage, "reason": "source_order_unverified"})
            continue
        record = {"symbol": symbol, "ref": ref, "revises": parent, "stage": stage, "effect": effect,
                  "effective_at": effective.isoformat(), "available_at": available.isoformat(),
                  "source_ref": source, "source_version": version}
        latest[ref] = available
        root = roots[ref] = roots[parent] if parent is not None else ref
        if filing and stage == "WITHDRAWN" and effect == "FUNDAMENTAL_ADVERSE":
            terminated.add(root)
        if receipt is not None:
            body_cut, evidence = receipt
            outcome = evidence.outcome if root in terminated else "UNRESOLVED"
            supersedes = ref if body_cut > available else None
            if supersedes is not None:
                revisions.append(record)
                record = {**record, "ref": f"{ref}:filing-body", "revises": ref, "available_at": body_cut.isoformat()}
            record["correction"] = {"outcome": outcome, "changes": evidence.changes, "body_ref": evidence.body_ref,
                                    "supersedes": supersedes}
            if outcome == "TERMINATION_REVOKED":
                record["effect"] = "INTACT"
        revisions.append(record)
        if not filing and reference:
            reports.setdefault((symbol, reference), ref)
        if filing:
            anchors[symbol, filing] = ref
            if stage == "CONFIRMED":
                confirmed_filings.add(roots[ref])
        if rule is not None:
            rule_refs[symbol, rule[1].get("event_id"), rule[1].get("event_revision_id")] = ref
    award_receipts = [row for ref, row in award_candidates.items() if ref not in duplicates]
    if award_receipts:
        last_cut = max(aware_iso_kst(row["available_at"]) for row in award_receipts)
        award_timeline = recorded_contract_material(award_receipts, last_cut)
        linked = set()
        for chain in award_timeline.chains:
            for row in chain:
                linked.add(row.ref)
                revisions.append({**asdict(row), "effective_at": row.effective_at.isoformat(),
                                  "available_at": row.available_at.isoformat()})
        matched = {(row["symbol"], row["contract_award_key"], row["rows_hash"])
                   for row in award_receipts if row["filing_ref"] in linked}
        for row in award_receipts:
            if row["correction"] and (row["symbol"], row["contract_award_key"], row["rows_hash"]) not in matched:
                unresolved.append({"ref": row["filing_ref"], "stage": "CORRECTED", "reason": "award_termination_unverified"})
    revisions.sort(key=lambda row: (_recorded_time(row["available_at"]), row["ref"]))
    return {"version": "material-news-auto/v1", "revisions": revisions, "unresolved": unresolved}

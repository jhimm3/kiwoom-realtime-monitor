"""사용자 강의 파일에서 검토 가능한 전략 규칙 초안을 만든다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree


RULE_CATEGORIES = ("진입 조건", "제외 조건", "청산 조건", "위험관리", "필요 데이터", "설명")
STRATEGY_METRICS = {
    "": "수동 확인",
    "entry_from_open_pct": "장 시작가 대비 진입 등락률(%)",
    "breakout_over_30m_high_pct": "직전 30분 고점 대비 진입률(%)",
    "pullback_from_session_high_pct": "장중 고점 대비 조정폭(%)",
    "rebound_from_pullback_low_pct": "조정 저점 대비 반등률(%)",
    "entry_trade_value_eok": "진입 1분봉 거래대금(억)",
    "trade_value_vs_prior_ratio": "직전 5봉 대비 거래대금 배율",
    "recent_high_distance_pct": "최근 20일 고점 거리(%)",
    "entry_time_hhmm": "진입시각(HHMM)",
}
COMPARISONS = (">=", "<=", ">", "<", "==")


@dataclass(frozen=True)
class StrategyRuleDraft:
    category: str
    text: str
    source_file: str
    location: str
    required_data: tuple[str, ...]
    automatable: bool
    metric_key: str = ""
    comparison: str = ">="
    threshold: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "StrategyRuleDraft":
        return cls(
            str(value.get("category", "설명")), str(value.get("text", "")),
            str(value.get("source_file", "")), str(value.get("location", "")),
            tuple(str(item) for item in value.get("required_data", ()) if str(item)),
            bool(value.get("automatable", False)),
            str(value.get("metric_key", "")), str(value.get("comparison", ">=")),
            float(value["threshold"]) if value.get("threshold") is not None else None,
        )


@dataclass(frozen=True)
class ExtractedStrategyDraft:
    rules: tuple[StrategyRuleDraft, ...]
    source_texts: dict[str, str]


def rule_identity(rule: StrategyRuleDraft) -> str:
    """문장 공백·기호 차이를 무시해 이전 검토값을 이어 붙일 식별자."""
    return re.sub(r"\W+", "", rule.text).casefold()


def merge_reviewed_draft(
    previous: ExtractedStrategyDraft | None, current: ExtractedStrategyDraft,
) -> ExtractedStrategyDraft:
    """재추출된 동일 문장에는 사용자가 검토한 분류·측정식·기준값을 보존한다."""
    if previous is None:
        return current
    reviewed = {rule_identity(rule): rule for rule in previous.rules}
    merged = []
    for rule in current.rules:
        old = reviewed.get(rule_identity(rule))
        merged.append(StrategyRuleDraft(
            old.category, rule.text, rule.source_file, rule.location,
            rule.required_data or old.required_data, old.automatable,
            old.metric_key, old.comparison, old.threshold,
        ) if old is not None else rule)
    return ExtractedStrategyDraft(tuple(merged), current.source_texts)


def extend_reviewed_draft(
    previous: ExtractedStrategyDraft | None, addition: ExtractedStrategyDraft,
) -> ExtractedStrategyDraft:
    """저장된 이전 문단을 유지한 채 새 강의의 중복되지 않은 문단만 덧붙인다."""
    if previous is None:
        return addition
    identities = {rule_identity(rule) for rule in previous.rules}
    added = tuple(rule for rule in addition.rules if rule_identity(rule) not in identities)
    return ExtractedStrategyDraft(
        (*previous.rules, *added), {**previous.source_texts, **addition.source_texts},
    )


def draft_change_labels(
    previous: ExtractedStrategyDraft | None, current: ExtractedStrategyDraft,
) -> tuple[str, ...]:
    """검토 화면에서 추가·변경·유지 문단을 빠르게 구분한다."""
    if previous is None:
        return tuple("신규" for _ in current.rules)
    exact = {rule_identity(rule) for rule in previous.rules}
    locations = {(rule.source_file, rule.location): rule_identity(rule) for rule in previous.rules}
    measured = {(rule.metric_key, rule.comparison, rule.threshold) for rule in previous.rules if rule.metric_key}
    metric_keys = {rule.metric_key for rule in previous.rules if rule.metric_key}
    labels = []
    for rule in current.rules:
        identity = rule_identity(rule)
        if identity in exact:
            labels.append("유지")
        elif (rule.source_file, rule.location) in locations:
            labels.append("변경")
        elif rule.metric_key and rule.metric_key in metric_keys and (rule.metric_key, rule.comparison, rule.threshold) not in measured:
            labels.append("충돌 확인")
        else:
            labels.append("신규")
    return tuple(labels)


def extract_strategy_draft(paths: tuple[Path, ...]) -> ExtractedStrategyDraft:
    rules: list[StrategyRuleDraft] = []
    source_texts: dict[str, str] = {}
    for path in paths:
        sections = _extract_sections(path)
        source_texts[str(path)] = "\n".join(text for _, text in sections)
        for location, text in sections:
            for paragraph in _paragraphs(text):
                metric_key, comparison, threshold = _metric_mapping(paragraph)
                rules.append(StrategyRuleDraft(
                    _category(paragraph), paragraph, path.name, location,
                    _required_data(paragraph), _automatable(paragraph), metric_key, comparison, threshold,
                ))
    # 같은 스크립트와 PDF가 겹쳐도 출처가 더 명확한 첫 문단만 남긴다.
    unique: dict[str, StrategyRuleDraft] = {}
    for rule in rules:
        key = re.sub(r"\W+", "", rule.text).casefold()
        if len(key) >= 8 and key not in unique:
            unique[key] = rule
    return ExtractedStrategyDraft(tuple(unique.values()), source_texts)


def _extract_sections(path: Path) -> tuple[tuple[str, str], ...]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return (("본문", path.read_text(encoding="utf-8", errors="replace")),)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    raise ValueError(f"지원하지 않는 강의 파일입니다: {path.name}")


def _extract_docx(path: Path) -> tuple[tuple[str, str], ...]:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = ["".join(node.text or "" for node in paragraph.iter(f"{word}t")).strip() for paragraph in root.iter(f"{word}p")]
    return (("본문", "\n".join(value for value in paragraphs if value)),)


def _extract_pdf(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise RuntimeError("PDF 텍스트 추출 구성요소가 없습니다.") from error
    document = pdfium.PdfDocument(str(path))
    result = []
    try:
        for index in range(len(document)):
            page = document[index]
            text_page = page.get_textpage()
            try:
                text = text_page.get_text_range().strip()
            finally:
                text_page.close(); page.close()
            if text:
                result.append((f"{index + 1}쪽", text))
    finally:
        document.close()
    if not result:
        raise ValueError(f"PDF에 추출 가능한 텍스트가 없습니다. OCR된 PDF나 스크립트를 함께 등록해 주세요: {path.name}")
    return tuple(result)


def _paragraphs(text: str) -> tuple[str, ...]:
    lines = [re.sub(r"\s+", " ", line).strip(" -•\t") for line in text.splitlines()]
    return tuple(line for line in lines if 12 <= len(line) <= 1200)


def _category(text: str) -> str:
    if any(word in text for word in ("매도", "청산", "익절", "수익실현")): return "청산 조건"
    if any(word in text for word in ("손절", "비중", "리스크", "위험", "대응")): return "위험관리"
    if any(word in text for word in ("하지 않는다", "피한다", "제외", "금지", "매매하지")): return "제외 조건"
    if any(word in text for word in ("매수", "진입", "돌파", "눌림", "반등", "지지")): return "진입 조건"
    if _required_data(text): return "필요 데이터"
    return "설명"


def _required_data(text: str) -> tuple[str, ...]:
    mapping = {
        "분봉": ("분봉", "캔들", "봉"), "일봉": ("일봉",), "거래대금": ("거래대금",),
        "거래량": ("거래량",), "신고가": ("신고가",), "VWAP": ("vwap", "평균체결가"),
        "이동평균선": ("이동평균", "이평선"), "호가": ("호가", "잔량"),
        "체결강도": ("체결강도", "매수세"), "뉴스": ("뉴스", "재료", "공시"),
        "테마": ("테마", "주도주"), "수급": ("외국인", "기관", "수급", "프로그램"),
        "시장지수": ("코스피", "코스닥", "시장 상태", "지수"),
    }
    lowered = text.casefold()
    return tuple(name for name, words in mapping.items() if any(word.casefold() in lowered for word in words))


def _automatable(text: str) -> bool:
    if any(word in text for word in ("심리", "확신", "마음", "원칙을 지키", "느낌", "판단력")):
        return False
    return bool(_required_data(text) or re.search(r"\d+(?:\.\d+)?\s*(?:%|원|분|봉|억|주)", text))


def _metric_mapping(text: str) -> tuple[str, str, float | None]:
    numbers = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)", text.replace(",", ""))]
    threshold = numbers[0] if numbers else None
    if "거래대금" in text and "배" in text:
        return "trade_value_vs_prior_ratio", ">=", threshold
    if "거래대금" in text and ("억" in text or threshold is not None):
        return "entry_trade_value_eok", ">=", threshold
    if any(word in text for word in ("고점 대비", "고점에서")) and any(word in text for word in ("눌", "조정", "하락")):
        return "pullback_from_session_high_pct", ">=", threshold
    if any(word in text for word in ("직전 고점", "30분 고점", "돌파")):
        return "breakout_over_30m_high_pct", ">=", threshold if "%" in text else 0.0
    if any(word in text for word in ("저점 대비", "저가 대비")) and "반등" in text:
        return "rebound_from_pullback_low_pct", ">=", threshold
    if "시가" in text or "장 시작가" in text:
        return "entry_from_open_pct", ">=", threshold
    if "최근" in text and "고점" in text:
        return "recent_high_distance_pct", "<=", threshold
    if any(word in text for word in ("장 마감", "종가", "시각", "시간")) and threshold is not None:
        return "entry_time_hhmm", ">=", threshold
    return "", ">=", None


def data_burden(required_data: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    states = {
        "분봉": "기존 DB", "일봉": "기존 DB", "거래대금": "기존 DB", "거래량": "기존 DB",
        "신고가": "기존 DB", "VWAP": "기존 봉으로 계산", "이동평균선": "기존 봉으로 계산",
        "뉴스": "기존 스냅샷", "테마": "기존 스냅샷", "체결강도": "기존 실시간",
        "시장지수": "기존 실시간", "수급": "체결 스냅샷·장 마감 보완", "호가": "추가 실시간 저장 승인 필요",
    }
    return tuple((item, states.get(item, "지원 여부 검토 필요")) for item in required_data)

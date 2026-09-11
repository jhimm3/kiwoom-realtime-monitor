"""개인 매매 원칙 DOCX를 강의와 주제 계층을 보존해 추출한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree


LESSON_TOPICS = {
    0: "기초·도구·안전",
    1: "주도주 선정과 시장 판단",
    2: "주도주 돌파매매",
    3: "테마주 돌파매매",
    4: "신규주 매매",
    5: "종가베팅",
    6: "과대낙폭·낙주",
}


@dataclass(frozen=True)
class StructuredTradeRule:
    lesson: int
    lesson_topic: str
    section: str
    subsection: str
    role: str
    target_topic: str
    text: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "StructuredTradeRule":
        return cls(
            int(value.get("lesson", -1)), str(value.get("lesson_topic", "")),
            str(value.get("section", "")), str(value.get("subsection", "")),
            str(value.get("role", "context")), str(value.get("target_topic", "")),
            str(value.get("text", "")),
        )


def _role(section: str, subsection: str) -> str:
    context = f"{section} {subsection}"
    if "체크리스트" in context:
        return "checklist"
    if any(word in context for word in ("주의", "피해야", "실수", "손절", "위험관리")):
        return "risk_rule"
    if any(word in context for word in (
        "강의의 핵심", "핵심 역할", "매수 원칙", "진입", "선정", "필수", "선행조건",
        "전제", "매도 원칙", "청산", "비중",
    )):
        return "core_rule"
    if "한 문장" in context or "핵심 정리" in context:
        return "summary"
    return "context"


def _target_topic(lesson: int, section: str, subsection: str) -> str:
    context = f"{section} {subsection}"
    if lesson == 1 and any(word in context for word in ("신규주", "스팩주")):
        return "신규주 매매(참고 언급)"
    if lesson == 3 and "종가" in context:
        return "종가베팅(참고 언급)"
    if lesson == 3 and any(word in context for word in ("급락 반등", "낙주")):
        return "낙주(참고 언급)"
    if lesson == 4 and "낙주" in context:
        return "낙주(참고 언급)"
    if lesson == 4 and "종가" in context:
        return "종가베팅(참고 언급)"
    return LESSON_TOPICS.get(lesson, "미분류")


def load_personal_trade_rules(path: Path) -> tuple[str, ...]:
    """개인 문서의 전체 문단을 순서와 중복 제거를 유지해 추출한다."""
    if not path.is_file():
        return ()
    try:
        if path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            values = []
            for paragraph in root.iter(f"{namespace}p"):
                text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
                if text:
                    values.append(text)
        else:
            values = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ()
    selected: list[str] = []
    for value in values:
        rule = value.strip().lstrip("-•*0123456789. ")
        if rule and rule not in selected:
            selected.append(rule)
    return tuple(selected[:2_000])


def extract_structured_trade_rules(path: Path) -> tuple[StructuredTradeRule, ...]:
    if not path.is_file():
        return ()
    if path.suffix.lower() in (".md", ".txt"):
        return _extract_markdown_trade_rules(path)
    if path.suffix.lower() != ".docx":
        return ()
    try:
        with zipfile.ZipFile(path) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))
            styles = ElementTree.fromstring(archive.read("word/styles.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ()
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    style_names: dict[str, str] = {}
    for style in styles.iter(f"{word}style"):
        style_id = style.get(f"{word}styleId", "")
        name = style.find(f"{word}name")
        if style_id and name is not None:
            style_names[style_id] = name.get(f"{word}val", style_id)
    lesson = -1; section = subsection = ""
    result: list[StructuredTradeRule] = []
    for paragraph in document.iter(f"{word}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{word}t")).strip()
        if not text:
            continue
        style_node = paragraph.find(f"{word}pPr/{word}pStyle")
        style_id = style_node.get(f"{word}val", "") if style_node is not None else ""
        style_name = style_names.get(style_id, style_id).lower().replace(" ", "")
        heading_match = re.search(r"미모사\s*(\d+)강", text)
        if "heading1" in style_name or "제목1" in style_name:
            if heading_match:
                lesson = int(heading_match.group(1))
            section = subsection = ""
            continue
        if "heading2" in style_name or "제목2" in style_name:
            section = text; subsection = ""; continue
        if "heading3" in style_name or "제목3" in style_name:
            subsection = text; continue
        if lesson < 0 or text.startswith("※ 강의 내용을") or text.startswith("※ 본 요약"):
            continue
        result.append(StructuredTradeRule(
            lesson, LESSON_TOPICS.get(lesson, "미분류"), section, subsection,
            _role(section, subsection), _target_topic(lesson, section, subsection), text,
        ))
    return tuple(result)


def _extract_markdown_trade_rules(path: Path) -> tuple[StructuredTradeRule, ...]:
    """Parse the private structured lecture note without publishing its content."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ()
    lesson = -1
    section = subsection = ""
    result: list[StructuredTradeRule] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("|") or line.startswith("---"):
            continue
        if line.startswith("# "):
            heading = line[2:].strip()
            match = re.match(r"(?:미모사\s*)?(\d+)강(?:\s*:|$)", heading)
            if match:
                lesson = int(match.group(1))
                section = subsection = ""
            continue
        if line.startswith("## "):
            section = line[3:].strip(); subsection = ""; continue
        if line.startswith("### "):
            subsection = line[4:].strip(); continue
        if line.startswith("#") or lesson < 0:
            continue
        text = re.sub(r"^(?:[-*+]\s+|\d+\.\s+)", "", line).strip()
        if not text or text.startswith("`"):
            continue
        result.append(StructuredTradeRule(
            lesson, LESSON_TOPICS.get(lesson, "미분류"), section, subsection,
            _role(section, subsection), _target_topic(lesson, section, subsection), text,
        ))
    return tuple(result)


def applicable_lesson_text(setup_type: str) -> str:
    mapping = {
        "주도주 돌파": "2강 주도주 돌파매매 (1강 주도주 선정은 선행 조건)",
        "테마주 돌파": "3강 테마주 돌파매매",
        "신규주": "4강 신규주 매매",
        "종가베팅": "5강 종가베팅",
        "과대낙폭": "6강 과대낙폭",
        "낙주": "6강 낙주",
    }
    return mapping.get(setup_type, "적용 강의 미분류")

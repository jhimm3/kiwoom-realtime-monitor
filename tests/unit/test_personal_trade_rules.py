from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from kiwoom_monitor.application.personal_trade_rules import extract_structured_trade_rules


class PersonalTradeRulesTests(unittest.TestCase):
    def test_parses_private_markdown_rulebook_by_lesson_and_subsection(self) -> None:
        content = """# 2강: 당일 주도주 돌파매매
## 유형 A: 박스권 돌파
### 무효·손절
- 돌파 후 박스 안으로 즉시 복귀하면 손절한다.
# 3강: 테마주 돌파매매
## 후발 주도주
- 다른 종목에 거래대금이 붙으면 후발 주도주 후보다.
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.md"
            path.write_text(content, encoding="utf-8")
            rules = extract_structured_trade_rules(path)
        self.assertEqual(2, len(rules))
        self.assertEqual((2, 3), tuple(rule.lesson for rule in rules))
        self.assertEqual("risk_rule", rules[0].role)
        self.assertEqual("주도주 돌파매매", rules[0].target_topic)

    def test_preserves_lesson_section_and_marks_cross_topic_as_reference(self) -> None:
        document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>미모사 3강 통합 요약</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>6. 특수 상황</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr><w:r><w:t>종가 접근의 기본 아이디어</w:t></w:r></w:p>
        <w:p><w:r><w:t>다음 날을 노리는 종가 접근을 검토한다.</w:t></w:r></w:p>
        </w:body></w:document>'''
        styles = '''<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
        <w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
        <w:style w:styleId="Heading3"><w:name w:val="heading 3"/></w:style></w:styles>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.docx"
            with ZipFile(path, "w", ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", document); archive.writestr("word/styles.xml", styles)
            rules = extract_structured_trade_rules(path)
        self.assertEqual(1, len(rules)); self.assertEqual(3, rules[0].lesson)
        self.assertEqual("종가베팅(참고 언급)", rules[0].target_topic)

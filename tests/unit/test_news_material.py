from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.daily_decline import _material_catalyst
from kiwoom_monitor.application.news_material import MaterialNews, derive_material_news, recorded_contract_material
from kiwoom_monitor.application.panic_rebound import PanicPriorSession, material_panic_proofs
from scripts.prepare_strategy_news_material import prepare


def _at(minute):
    return f"2026-09-11T09:{minute:02d}:00+09:00"


def _dart(number):
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo=2026091100{number:04d}"


def _article(ref, minute, title, url, **fields):
    return {"stock_code": "005930", "article_revision_id": ref, "published_at": _at(minute),
            "available_at": _at(minute), "content_hash": f"version:{ref}",
            "document": {"title": title, "original_link": url, **fields}}


def _contract_corrections():
    return [
        _article("contract", 0, "단일판매ㆍ공급계약 체결", _dart(1)),
        _article("termination", 1, "단일판매ㆍ공급계약해지", _dart(2), original_filing_url=_dart(1)),
        _article("terms", 2, "[정정] 단일판매ㆍ공급계약해지", _dart(3), original_filing_url=_dart(2)),
        _article("later", 6, "[정정] 단일판매ㆍ공급계약해지", _dart(4), original_filing_url=_dart(2)),
    ]


def _filing_body(ref, minute, filing, *changes):
    return {"article_revision_id": ref, "available_at": _at(minute), "status": "fulltext",
            "filing_url": _dart(filing), "body_revision_id": f"body:{ref}",
            "filing_rows": [["정정 항목", "정정 전", "정정 후"], *changes, ["-"]]}


def recorded_filing(ref, available, *, effective=None, parent="", outcome=None, rows_hash=None):
    title = "단일판매ㆍ공급계약해지"
    document = {"filing_ref": ref, "original_filing_ref": parent, "source_ref": ref, "symbol": "005930",
                "title": "[정정] " + title if parent else title, "body_ref": "body:" + ref,
                "available_at": available, "effective_at": effective or available, "rows_hash": rows_hash or ref}
    if outcome:
        document["correction"] = {"outcome": outcome, "changes": [["계약 상태", "전", "후"]],
                                  "body_ref": document["body_ref"], "supersedes": None}
    return document


class AutomaticMaterialNewsTests(unittest.TestCase):
    def test_public_viewer_fetches_requested_receipt_and_does_not_archive_a_partial_document(self):
        from kiwoom_monitor.infrastructure.official_news_filing import fetch_official_filing

        original, corrected = _dart(1), _dart(2)
        number = corrected.split("=")[1]
        html = "<table><tr><td>공시 본문</td></tr></table>"
        family = (f'<select id="doc"><option value="rcpNo={original.split("=")[1]}" '
                  'title="단일판매ㆍ공급계약해지"></option></select>')
        viewer = f'viewDoc("{number}", "5", "0", "0", "0", "HTML", "");' + family
        with patch("kiwoom_monitor.infrastructure.official_news_filing._read", side_effect=[viewer, html]) as read:
            value = fetch_official_filing(corrected, timeout_seconds=2)
        self.assertEqual((corrected, original, html),
                         (value["filing_url"], value["original_filing_url"], value["filing_html"]))
        self.assertIn(f"/report/viewer.do?rcpNo={number}&dcmNo=5", read.call_args_list[1].args[0])
        partial = viewer.replace('"HTML"', '"dart4.xsd"') + "treeData.push(node1);" * 2
        with patch("kiwoom_monitor.infrastructure.official_news_filing._read", return_value=partial) as read:
            with self.assertRaisesRegex(ValueError, "requires a complete"):
                fetch_official_filing(corrected, timeout_seconds=2)
            read.assert_called_once()

    def test_kind_viewer_fetches_body_with_or_without_toc_and_checks_receipt(self):
        from kiwoom_monitor.infrastructure.official_news_filing import fetch_official_filing

        receipt = "20240226001194"
        doc = "20240226002985"
        root = "https://kind.krx.co.kr"
        listing = f"{root}/common/disclsviewer.do?method=search&acptno={receipt}"
        contents = f"{root}/common/disclsviewer.do?method=searchContents&docNo={doc}"
        body_url = f"{root}/external/2024/02/26/001194/{doc}/11301.htm"
        toc_url = body_url.replace(".htm", "_toc.htm")
        viewer = f'<option value="{doc}|main" selected="selected">부도발생</option>'
        body = "<h2>부도발생</h2><table><tr><td>공시 본문</td></tr></table>"

        def path_page(toc, target):
            return f"parent.setPath('{toc}','{target}','/external/path','01','20');"

        for toc in ("", toc_url):
            with self.subTest(toc=toc), patch("kiwoom_monitor.infrastructure.official_news_filing._read",
                                             side_effect=[viewer, path_page(toc, body_url), body]) as read:
                value = fetch_official_filing(listing, timeout_seconds=2)
                self.assertEqual((listing, body_url, body, ""), (value["filing_url"], value["document_url"],
                                 value["filing_html"], value["original_filing_url"]))
                self.assertEqual([listing, contents, body_url], [call.args[0] for call in read.call_args_list])
        for page, reason in (
            (path_page(toc_url, body_url.replace(doc, "20240226009999")), "number is unverified"),
            (path_page(toc_url, body_url.replace("/001194/", "/001195/")), "does not match the requested receipt"),
            (path_page(toc_url, body_url.replace(root, "https://example.invalid")), "number is unverified"),
            (path_page(toc_url, body_url) + path_page(toc_url, body_url.replace("11301", "11302")), "number is unverified"),
        ):
            with self.subTest(page=page), patch("kiwoom_monitor.infrastructure.official_news_filing._read",
                                              side_effect=[viewer, page]) as read:
                with self.assertRaisesRegex(ValueError, reason):
                    fetch_official_filing(listing, timeout_seconds=2)
                self.assertEqual([listing, contents], [call.args[0] for call in read.call_args_list])

    def test_award_correction_needs_confirmed_body_and_same_contract_before_restoration(self):
        from kiwoom_monitor.application.news_material import read_contract_award_change

        notice = "발주처의 공사도급계약 해지 통지 공문을 수령하였습니다."
        cancellation = "발주처는 계약해지 통지를 취소하고 2024년 10월 24일 계약금액 및 계약기간 변경에 합의하였습니다."

        def body(before, after, party="발주처"):
            return [
                ["1. 정정관련 공시서류", "단일판매ㆍ공급계약 체결"],
                ["정정항목", "정정전", "정정후"],
                ["9. 기타 투자판단과 관련한 중요사항", before, after],
                ["-"], ["- 체결계약명", "외동읍 신축공사"], ["3. 계약상대", party],
                ["7. 계약(수주)일자", "2021-04-23"],
            ]

        identity, initial = read_contract_award_change(body("-", notice), "notice-body")
        self.assertIsNone(initial)
        same_identity, proof = read_contract_award_change(body(notice, cancellation), "withdrawal-body")
        self.assertEqual(identity, same_identity)
        self.assertEqual("TERMINATION_REVOKED", proof.outcome)
        self.assertIsNone(read_contract_award_change(body(notice, "발주처가 해지 통지 취소를 검토 중입니다."), "other"))
        self.assertIsNone(read_contract_award_change(body(notice, "계약금액 5억원으로 변경하였습니다."), "other"))
        self.assertIsNone(read_contract_award_change(body("-", "발주처의 해지 통지가 예정되어 있습니다."), "other"))
        different, _ = read_contract_award_change(body(notice, cancellation, "다른 발주처"), "other")

        def filing(n, identity, effective, available, correction=None):
            return dict(filing_ref=_dart(n), source_ref=_dart(n), title="[기재정정]단일판매ㆍ공급계약체결",
                        symbol="005930", available_at=_at(available), effective_at=_at(effective),
                        contract_award_key=identity, body_ref=f"body:{n}", rows_hash=str(n), correction=correction)

        correction = dict(outcome=proof.outcome, changes=proof.changes, body_ref=proof.body_ref, supersedes=None)
        original = filing(1, identity, 1, 2)
        foreign = filing(2, different, 3, 3, correction)
        amended = filing(3, identity, 4, 6, correction)
        timeline = recorded_contract_material((amended, original, foreign), datetime.fromisoformat(_at(7)))
        for minute, effect in ((2, "FUNDAMENTAL_ADVERSE"), (5, "FUNDAMENTAL_ADVERSE"), (6, "INTACT")):
            root, row, observed, _ = next(timeline.as_of("005930", datetime.fromisoformat(_at(minute))))
            self.assertEqual((_dart(1), effect), (root, observed))
            if minute == 6:
                self.assertEqual((_dart(3), _dart(1), _at(6)), (row.ref, row.revises, row.available_at.isoformat()))
        late = dict(original, available_at=_at(7))
        timeline = recorded_contract_material((late, amended), datetime.fromisoformat(_at(7)))
        self.assertEqual([], list(timeline.as_of("005930", datetime.fromisoformat(_at(6)))))
        self.assertEqual("INTACT", next(timeline.as_of("005930", datetime.fromisoformat(_at(7))))[2])

        title = original["title"]
        articles = [_article("notice", 1, title, _dart(1)), _article("revoked", 4, title, _dart(3))]
        archived = [
            {**_filing_body("notice", 7, 1), "filing_rows": body("-", notice)},
            {**_filing_body("revoked", 6, 3), "filing_rows": body(notice, cancellation)},
        ]
        result = derive_material_news(articles, bodies=archived)
        self.assertEqual([], result["unresolved"])
        frozen = MaterialNews.from_documents(result["revisions"])
        self.assertEqual([], list(frozen.as_of("005930", datetime.fromisoformat(_at(6)))))
        root, latest, effect, _ = next(frozen.as_of("005930", datetime.fromisoformat(_at(7))))
        self.assertEqual(("notice", "revoked", "INTACT"), (root, latest.ref, effect))

    def test_recorded_correction_waits_for_late_original_and_mirrors_share_one_state(self):
        original = recorded_filing("kind:initial", _at(4), effective=_at(0), rows_hash="same-original")
        mirrored = recorded_filing("dart:initial", _at(5), effective=_at(0), rows_hash="same-original")
        revoke = recorded_filing("kind:revocation", _at(3), effective=_at(2), parent="kind:initial",
                                 outcome="TERMINATION_REVOKED", rows_hash="same-revocation")
        mirror_revoke = recorded_filing("dart:revocation", _at(8), effective=_at(2), parent="dart:initial",
                                        outcome="TERMINATION_REVOKED", rows_hash="same-revocation")
        filings = [mirror_revoke, revoke, mirrored, original]
        for minute in (3, 4, 8):
            cut = datetime.fromisoformat(_at(minute))
            rows = list(recorded_contract_material(filings, cut).as_of("005930", cut))
            if minute == 3:
                self.assertEqual([], rows)
            else:
                root, current, effect, revisions = rows[0]
                self.assertEqual(1, len(rows))
                self.assertEqual(("kind:initial", "kind:revocation", "INTACT", 2),
                                 (root, current.ref, effect, len(revisions)))
                self.assertEqual(datetime.fromisoformat(_at(4)), current.available_at)

    def test_official_terms_changes_retain_prior_session_termination_when_body_becomes_available(self):
        rows = _contract_corrections()[:3]
        for row, clock in zip(rows[:2], ("15:00", "17:00")):
            row["available_at"] = row["published_at"] = f"2026-09-10T{clock}:00+09:00"
        for row in rows:
            if row["article_revision_id"] in {"contract", "termination"}:
                row["document"]["original_link"] = row["document"]["original_link"].replace("20260911", "20260910")
        rows[1]["document"]["original_filing_url"] = rows[0]["document"]["original_link"]
        rows[2]["document"]["original_filing_url"] = rows[1]["document"]["original_link"]
        terms = [("2. 해지금액(원)", "10,000", "20,000"),
                 ("2. 매출액대비(%)", "1.5", "3.0"),
                 ("6. 해지일자", "2026-09-08", "2026-09-10"),
                 ("기타 투자판단에 참고할 사항", "총 계약금액의 14.0%인 30,000원을 이행하였습니다.",
                  "총 계약금액의 15.0%인 35,000원을 이행하였습니다.")]
        html = "<table>" + "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
            for row in [("정정항목", "정정전", "정정후"), *terms]
        ) + "</table>"
        body = _filing_body("terms", 4, 3)
        body.pop("filing_rows")
        body["filing_html"] = html
        with tempfile.TemporaryDirectory() as directory:
            article_path, body_path = Path(directory) / "articles.json", Path(directory) / "body.json"
            article_path.write_text(json.dumps({"kind": "article", "revisions": rows}), encoding="utf-8")
            body_path.write_text(json.dumps({"kind": "body", "revisions": [body]}), encoding="utf-8")
            result = prepare([article_path], [], body_paths=[body_path], symbol="005930", dd_event_ref="contract")
        self.assertEqual([], result["unresolved"])
        correction = result["revisions"][-1]["correction"]
        self.assertEqual(("TERMS_ONLY", tuple(terms), "terms"),
                         (correction["outcome"], correction["changes"], correction["supersedes"]))
        timeline = MaterialNews.from_documents(result["dd"]["revisions"])
        cut = datetime.fromisoformat
        prior = PanicPriorSession(cut("2026-09-10T15:30:00+09:00"), cut(_at(0)), cut(_at(15)), ("calendar",))
        for minute, state, expected in ((1, "DAMAGED", True), (2, "UNKNOWN", False), (4, "DAMAGED", True)):
            with self.subTest(minute=minute):
                self.assertEqual(state, _material_catalyst(("contract", timeline), "005930", cut(_at(minute))).state)
                proofs = material_panic_proofs(timeline, "005930", cut(_at(minute)), prior_session=prior)
                self.assertEqual(expected, bool(proofs))
                if minute == 4:
                    self.assertEqual("2026-09-10", proofs[0].effective_at.date().isoformat())
                    self.assertIn("body:terms", proofs[0].evidence_refs)
                    self.assertEqual((), material_panic_proofs(timeline, "005930", cut(_at(15)), prior_session=prior))

    def test_only_linked_final_revocation_restores_contract_and_late_older_edits_do_not_override_it(self):
        rows = _contract_corrections()
        revocation = _filing_body("later", 6, 4, ["계약 상태", "계약 해지", "계약 해지 자체를 철회하였습니다."])
        older_terms = _filing_body("terms", 8, 3, ["해지금액", "1,000", "2,000"])
        for minute, expected in ((1, "DAMAGED"), (3, "UNKNOWN"), (6, "INTACT"), (8, "INTACT")):
            with self.subTest(minute=minute):
                result = derive_material_news(rows, bodies=[older_terms, revocation])
                timeline = MaterialNews.from_documents(result["revisions"])
                cut = datetime.fromisoformat(_at(minute))
                self.assertEqual(expected, _material_catalyst(("contract", timeline), "005930", cut).state)
                self.assertEqual(minute == 1, bool(material_panic_proofs(timeline, "005930", cut)))
        for after in ("계약 해지 통보 철회를 검토 중입니다.", "계약 해지 통보 철회 요청",
                      "법원의 가처분 인용으로 해지 효력 정지", "계약 해지 통지를 철회하지 않았습니다.",
                      "계약 해지 공시를 철회했습니다."):
            with self.subTest(after=after):
                body = _filing_body("later", 6, 4, ["계약 상태", "해지", after])
                result = derive_material_news(rows, bodies=[body])
                self.assertEqual("UNRESOLVED", result["revisions"][-1]["correction"]["outcome"])
                self.assertEqual("UNKNOWN", result["revisions"][-1]["effect"])
        mismatched = derive_material_news(rows, bodies=[{**revocation, "filing_url": _dart(3)}])
        self.assertNotIn("correction", mismatched["revisions"][-1])
        rows[-1]["document"]["original_filing_url"] = "https://example.test/unverified"
        self.assertIn({"ref": "later", "stage": "CORRECTED", "reason": "original_filing_unverified"},
                      derive_material_news(rows, bodies=[revocation])["unresolved"])

    def test_saved_original_links_freeze_four_stages_and_both_existing_strategy_consumers(self):
        original = _dart(1)
        rows = [
            _article("report", 1, "삼성전자 공급계약 보도", "https://example.test/article", description=original),
            _article("confirmed", 2, "단일판매ㆍ공급계약 체결", original),
            _article("corrected", 3, "[기재정정] 단일판매ㆍ공급계약체결", _dart(2)),
            _article("withdrawn", 6, "단일판매ㆍ공급계약해지", _dart(3), original_filing_url=original),
            _article("ambiguous", 7, "단일판매ㆍ공급계약해지", _dart(4)),
        ]
        body = {"article_revision_id": "corrected", "available_at": _at(4), "status": "fulltext",
                "body_text": f"정정대상 공시 {original}"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            article_file = root / "articles.json"
            body_file = root / "bodies.json"
            article_file.write_text(json.dumps({"kind": "article", "revisions": rows}), encoding="utf-8")
            body_file.write_text(json.dumps({"kind": "body", "revisions": [body]}), encoding="utf-8")
            result = prepare([article_file], [], body_paths=[body_file], symbol="005930", dd_event_ref="report")

        self.assertEqual([("REPORT", None), ("CONFIRMED", "report"), ("CORRECTED", "confirmed"),
                          ("WITHDRAWN", "confirmed")], [(row["stage"], row["revises"]) for row in result["revisions"]])
        self.assertEqual([{"ref": "ambiguous", "stage": "WITHDRAWN", "reason": "original_filing_unverified"}], result["unresolved"])
        timeline = MaterialNews.from_documents(result["dd"]["revisions"])
        for minute, effect in ((1, "UNKNOWN"), (3, "INTACT"), (4, "UNKNOWN"), (6, "DAMAGED")):
            observed = _material_catalyst(("report", timeline), "005930", datetime.fromisoformat(_at(minute)))
            self.assertEqual(effect, observed.state)
        self.assertEqual((), material_panic_proofs(timeline, "005930", datetime.fromisoformat(_at(5))))
        proof, = material_panic_proofs(timeline, "005930", datetime.fromisoformat(_at(6)))
        self.assertEqual(("fundamental_adverse", "withdrawn"), (proof.disposition, proof.revision))

    def test_existing_rule_lineage_links_reports_but_similarity_and_future_body_do_not(self):
        report = {**_article("first", 1, "공급계약 협의", "https://example.test/first"), "stock_code": "GLOBAL"}
        confirmed = _article("official", 3, "단일판매ㆍ공급계약체결", _dart(1))
        unrelated = _article("different", 4, "공급계약", "https://example.test/other")
        rule = {"stock_code": "005930", "event_id": "event-a", "scope": "TARGET_COMPANY", "role": "FACT", "novelty": "NEW"}
        events = [
            {**rule, "article_revision_id": "first", "event_revision_id": "old", "available_at": _at(2)},
            {**rule, "article_revision_id": "official", "event_revision_id": "new", "revision_of": "old", "available_at": _at(5)},
            {**rule, "article_revision_id": "different", "event_revision_id": "other", "event_id": "event-b",
             "available_at": _at(4), "result": {"possible_related_event_ids": ["event-a"]}},
        ]
        result = derive_material_news([unrelated, confirmed, report], events)
        timeline = MaterialNews.from_documents(result["revisions"])
        self.assertEqual([], list(timeline.as_of("005930", datetime.fromisoformat(_at(1)))))
        self.assertEqual({"first:005930": "UNKNOWN", "different": "UNKNOWN"},
                         {root: state for root, _, state, _ in timeline.as_of("005930", datetime.fromisoformat(_at(4)))})
        self.assertEqual({"first:005930": "INTACT", "different": "UNKNOWN"},
                         {root: state for root, _, state, _ in timeline.as_of("005930", datetime.fromisoformat(_at(5)))})

    def test_a_delayed_old_filing_is_not_a_new_intraday_adverse_event(self):
        old = _article("old", 1, "회생절차개시신청", _dart(1))
        old["available_at"] = "2026-09-14T09:30:00+09:00"
        result = derive_material_news([old])
        row, = result["revisions"]
        self.assertEqual(("2026-09-11", "2026-09-14", "FUNDAMENTAL_ADVERSE"),
                         (row["effective_at"][:10], row["available_at"][:10], row["effect"]))
        timeline = MaterialNews.from_documents(result["revisions"])
        self.assertEqual((), material_panic_proofs(timeline, "005930", datetime.fromisoformat(old["available_at"])))

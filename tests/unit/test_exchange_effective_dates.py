from __future__ import annotations

import io
import unittest
import zipfile

from kiwoom_monitor.infrastructure.exchange_effective_dates import (
    archive_rows, extract_effective_events,
)


def _archive(source: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("filing.xml", source.encode("utf-8"))
    return buffer.getvalue()


class ExchangeEffectiveDatesTests(unittest.TestCase):
    def test_receipt_day_is_not_used_for_later_halt_resume_and_delist(self) -> None:
        rows = archive_rows(_archive("""<html><table>
            <tr><td>가.정지일시</td><td>2026-09-09</td><td>-</td></tr>
            <tr><td>3.해제일시</td><td>2026-09-10</td><td>-</td></tr>
            <tr><td>5.기타</td><td>정리매매기간: '26.09.10 ~ '26.09.18
                상장폐지일 : '26.09.21</td></tr>
        </table></html>"""))

        events = extract_effective_events(rows, exchange_filing=True)

        self.assertEqual({("halt", "2026-09-09", "date"),
                          ("resume", "2026-09-10", "date"),
                          ("delist", "2026-09-21", "date")},
                         {(event.kind, event.effective_date, event.precision)
                          for event in events})

    def test_intraday_combined_notice_and_old_change_period(self) -> None:
        rows = archive_rows(_archive("""<html><table>
            <tr><td>3. 매매거래정지 일시</td><td>2024-01-03</td><td>08:44</td></tr>
            <tr><td>4. 매매거래정지 해제일시</td><td>2024-01-03</td><td>09:30</td></tr>
            <tr><td>가.변경전</td><td>2023년 06월 28일 16:54:00</td></tr>
        </table></html>"""))

        events = extract_effective_events(rows, exchange_filing=True)

        self.assertEqual({("halt", "2024-01-03", "08:44"),
                          ("resume", "2024-01-03", "09:30")},
                         {(event.kind, event.effective_date, event.effective_time)
                          for event in events})

    def test_proposed_delisting_and_issuer_decision_are_not_effective_events(self) -> None:
        rows = archive_rows(_archive("""<html><table>
            <tr><td>상장폐지신청 예정일자</td><td>2024-01-17</td></tr>
            <tr><td>상장폐지일 : 2024-01-29 예정</td></tr>
        </table></html>"""))
        self.assertEqual((), extract_effective_events(rows, exchange_filing=False))
        self.assertEqual((), extract_effective_events(rows, exchange_filing=True))


if __name__ == "__main__":
    unittest.main()

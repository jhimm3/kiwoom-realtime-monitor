"""Compare low-volume market-news dates with Korean public/market holidays.

Holiday dates come from the Nager.Date KR public-holiday API. The KRX market
closure rules add May 1 and the year-end closing day. Neighboring dates are
reported separately; they are not assumed to explain low volume.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data/historical_collection/audits/market-news-20260929"
SOURCE = AUDIT / "market-news-low-volume-days.csv"
OUTPUT = AUDIT / "market-news-low-volume-holiday-check.csv"
SUMMARY = AUDIT / "market-news-low-volume-holiday-summary.json"


def _year_end_closure(year: int, holidays: set[date]) -> date:
    day = date(year, 12, 31)
    while day.weekday() >= 5 or day in holidays:
        day -= timedelta(days=1)
    return day


def main() -> None:
    with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    years = sorted({int(row["target_date"][:4]) for row in rows})
    holidays: dict[date, list[str]] = {}
    urls = []
    for year in years:
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/KR"
        urls.append(url)
        with urlopen(url, timeout=15) as response:
            entries = json.load(response)
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"No KR holidays returned for {year}")
        for entry in entries:
            holidays.setdefault(date.fromisoformat(entry["date"]), []).append(
                str(entry.get("localName") or entry.get("name") or "public holiday"))
    public_dates = set(holidays)
    # The community calendar omits some announced temporary/substitute holidays
    # and election days. Each extra date below has a government source recorded
    # alongside it, rather than inferring it from low article counts.
    official_extras = {
        "2021-08-16": ("광복절 대체공휴일", "https://www.mpm.go.kr/mpm/comm/newsPress/newsPressRelease/?boardId=bbs_0000000000000029&category=&cntId=3249&mode=view&pageIdx=88"),
        "2021-10-04": ("개천절 대체공휴일", "https://www.mpm.go.kr/mpm/comm/newsPress/newsPressRelease/?boardId=bbs_0000000000000029&category=&cntId=3249&mode=view&pageIdx=88"),
        "2021-10-11": ("한글날 대체공휴일", "https://www.mpm.go.kr/mpm/comm/newsPress/newsPressRelease/?boardId=bbs_0000000000000029&category=&cntId=3249&mode=view&pageIdx=88"),
        "2022-03-09": ("제20대 대통령선거", "https://museum.nec.go.kr/museum2018/bbs/2/4/1/20170912155756377100_view.do?article_category=4&article_id=20220426180757220100&bbs_id=20170912155756377100"),
        "2022-06-01": ("제8회 지방선거", "https://museum.nec.go.kr/museum2018/bbs/2/4/1/20170912155756377100_view.do?article_category=4&article_id=20220426180757220100&bbs_id=20170912155756377100"),
        "2023-10-02": ("임시공휴일", "https://www.mpm.go.kr/mpm/comm/noti/mpmNotice/?boardId=bbs_0000000000000205&category=&cntId=719&mode=view&pageIdx=17"),
        "2024-04-10": ("제22대 국회의원선거", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/29633"),
        "2024-10-01": ("국군의 날 임시공휴일", "https://www.mpm.go.kr/mpm/comm/noti/mpmNotice/?boardId=bbs_0000000000000205&category=&cntId=834&mode=view&pageIdx=13"),
        "2025-01-27": ("임시공휴일", "https://www.mpm.go.kr/mpm/comm/newsLetter/?boardId=bbs_0000000000000033&category=&cntId=222&mode=view"),
        "2025-05-06": ("부처님오신날 대체공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/30071"),
        "2025-06-03": ("제21대 대통령선거", "https://su.nec.go.kr/su/bbs/B0000268/view.do?category1=su&category2=&deleteCd=0&menuNo=200008&nttId=256375&pageIndex=4"),
    }
    for day_text, (name, _) in official_extras.items():
        holidays.setdefault(date.fromisoformat(day_text), []).append(name)
    public_dates = set(holidays)
    closures = {date(year, 5, 1): "KRX Labor Day" for year in years}
    closures.update({_year_end_closure(year, public_dates): "KRX year-end closure"
                     for year in years})
    counts: Counter[str] = Counter()
    for row in rows:
        day = date.fromisoformat(row["target_date"])
        if day in public_dates:
            category = "public_holiday_exact"
            label = "; ".join(holidays[day])
        elif day in closures:
            category = "krx_closure_exact"
            label = closures[day]
        elif day.weekday() >= 5:
            category = "weekend_other"
            label = "Saturday" if day.weekday() == 5 else "Sunday"
        else:
            adjacent = [(offset, holidays[day + timedelta(days=offset)])
                        for offset in (-1, 1) if day + timedelta(days=offset) in holidays]
            if adjacent:
                category = "adjacent_public_holiday"
                label = "; ".join(f"{offset:+d}d {' / '.join(names)}"
                                  for offset, names in adjacent)
            else:
                category = "no_calendar_match"
                label = ""
        row["calendar_category"] = category
        row["calendar_label"] = label
        counts[category] += 1
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "rows": len(rows), "counts": dict(counts), "holiday_api_urls": urls,
              "krx_rules_source": "https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp",
              "official_supplements": {day: {"label": name, "source": url}
                                       for day, (name, url) in official_extras.items()},
              "note": "Calendar coincidence is not proof of complete source coverage."}
    SUMMARY.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

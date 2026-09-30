"""Read-only audit of historical FLASH/WORLD coverage and BODY retry candidates.

The output is an evidence ledger, not an instruction to re-fetch or overwrite
the source databases. A low-volume day or missing full text is not proof that
Naver omitted articles or that the original article no longer exists.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import statistics


def connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def audit(raw_path: Path, prepared_path: Path, output_dir: Path,
          *, expected_through: date | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    raw = connect_read_only(raw_path)
    prepared = connect_read_only(prepared_path)
    try:
        days = {
            (source, date.fromisoformat(day)): (state, pages, articles, error)
            for source, day, state, pages, articles, error in raw.execute(
                "SELECT source,target_date,state,pages,articles,last_error FROM market_news_days"
            )
        }
        day_states = Counter((source, state) for (source, _), (state, *_rest) in days.items())
        page_day_count = 0
        page_sequence_issues = []
        for source, day_text, count, first, last in raw.execute(
            "SELECT source,target_date,COUNT(*),MIN(page),MAX(page) "
            "FROM market_news_pages GROUP BY source,target_date"
        ):
            page_day_count += 1
            ledger = days.get((source, date.fromisoformat(day_text)))
            if ledger is None or count != ledger[1] or first != 1 or last != count:
                page_sequence_issues.append({
                    "source": source, "target_date": day_text, "raw_page_count": count,
                    "first_page": first, "last_page": last,
                    "ledger_pages": ledger[1] if ledger else None,
                })
        missing_dates = []
        outside_run_dates = []
        low_volume = []
        for source in sorted({source for source, _ in days}):
            source_dates = sorted(day for name, day in days if name == source)
            for day in (source_dates[0] + timedelta(days=n)
                        for n in range((source_dates[-1] - source_dates[0]).days + 1)):
                if (source, day) not in days:
                    missing_dates.append({"source": source, "target_date": day.isoformat()})
            if expected_through is not None and expected_through > source_dates[-1]:
                for day in (source_dates[-1] + timedelta(days=n)
                            for n in range(1, (expected_through - source_dates[-1]).days + 1)):
                    outside_run_dates.append({"source": source,
                                              "target_date": day.isoformat()})
            for day in source_dates:
                if day < date(2021, 1, 1):
                    continue  # WORLD is sparse before the known regular coverage period.
                state, pages, articles, error = days[source, day]
                peers = [days[source, day + timedelta(days=7 * offset)][2]
                         for offset in range(-4, 5) if offset and
                         (source, day + timedelta(days=7 * offset)) in days]
                if len(peers) < 4:
                    continue
                peer_median = statistics.median(peers)
                if peer_median < 100 or articles >= peer_median * 0.30:
                    continue
                count, first_page, last_page, invalid = raw.execute(
                    "SELECT COUNT(*),MIN(page),MAX(page),COALESCE(SUM(invalid_count),0) "
                    "FROM market_news_pages WHERE source=? AND target_date=?",
                    (source, day.isoformat()),
                ).fetchone()
                low_volume.append({
                    "source": source, "target_date": day.isoformat(), "state": state,
                    "articles": articles, "pages_recorded": pages,
                    "raw_page_count": count, "first_page": first_page,
                    "last_page": last_page, "invalid_rows": invalid,
                    "same_weekday_peer_median": peer_median,
                    "ratio_to_peer_median": round(articles / peer_median, 4),
                    "last_error": error,
                    "classification": "volume_review_only",
                })
        low_volume.sort(key=lambda row: (row["source"], row["target_date"]))

        retry_path = output_dir / "market-news-body-retry.csv"
        retry_partial = retry_path.with_suffix(".csv.partial")
        retry_counts: Counter[str] = Counter()
        retry_by_source: Counter[str] = Counter()
        failed_reasons: Counter[str] = Counter()
        fields = ("scope", "source", "target_date", "identity", "state", "body_status",
                  "retry_reason", "cause_confirmed", "listing_summary_present",
                  "original_link_present", "raw_article_present", "error")
        with retry_partial.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            # This scans the immutable input once. In particular, summary_only
            # means no extracted full text, not verified absence at the publisher.
            rows = prepared.execute(
                "SELECT scope,identity,state,article_json,body_json,error "
                "FROM prepared_news WHERE state<>'ready' OR "
                "COALESCE(json_extract(body_json,'$.body_status'),'')<>'fulltext'"
            )
            for scope, identity, state, article_json, body_json, error in rows:
                article = json.loads(article_json)
                body = json.loads(body_json)
                document = article.get("document") or {}
                source = str(article.get("source") or "")
                target_date = str(article.get("target_date") or "")
                raw_present = "not_checked"
                summary_present = bool(str(document.get("description") or "").strip())
                if state != "ready":
                    candidates = raw.execute(
                        "SELECT source,published_at,summary FROM market_news_articles "
                        "WHERE article_url=?", (identity,),
                    ).fetchall()
                    raw_present = bool(candidates)
                    if candidates:
                        source = "+".join(sorted({item[0] for item in candidates}))
                        target_date = candidates[0][1][:10]
                        summary_present = any(bool(str(item[2]).strip()) for item in candidates)
                    failed_reasons[error] += 1
                    if "identity가 일치하지" in error:
                        reason = "source_item_rejected_check_title_and_identity"
                    elif "기사 본문과 목록 요약" in error:
                        reason = "body_and_listing_summary_unavailable"
                    else:
                        reason = "preparation_failed_other"
                else:
                    reason = "fulltext_not_extracted_summary_preserved"
                retry_counts[reason] += 1
                retry_by_source[source or "unknown"] += 1
                writer.writerow({
                    "scope": scope, "source": source or "unknown",
                    "target_date": target_date, "identity": identity,
                    "state": state, "body_status": body.get("body_status") or "missing",
                    "retry_reason": reason, "cause_confirmed": "false",
                    "listing_summary_present": str(summary_present).lower(),
                    "original_link_present": str(bool(document.get("original_link"))).lower(),
                    "raw_article_present": str(raw_present).lower() if isinstance(raw_present, bool)
                    else raw_present,
                    "error": error,
                })
        retry_partial.replace(retry_path)
        raw.execute("ATTACH DATABASE ? AS prepared_input",
                    (prepared_path.resolve().as_uri() + "?mode=ro",))
        unprepared_path = output_dir / "market-news-unprepared.csv"
        unprepared_partial = unprepared_path.with_suffix(".csv.partial")
        unprepared_counts: Counter[str] = Counter()
        unprepared_years: Counter[str] = Counter()
        with unprepared_partial.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("source", "published_date", "published_at", "office_id",
                             "article_id", "identity", "retry_reason"))
            for source, published, office_id, article_id, url in raw.execute(
                "SELECT a.source,a.published_at,a.office_id,a.article_id,a.article_url "
                "FROM market_news_articles a WHERE NOT EXISTS "
                "(SELECT 1 FROM prepared_input.prepared_news p "
                "WHERE p.scope='historical_market_backfill' AND p.stock_code='GLOBAL' "
                "AND p.identity=a.article_url)"
            ):
                published_date = published[:10]
                unprepared_counts[source] += 1
                unprepared_years[f"{source}:{published_date[:4]}"] += 1
                writer.writerow((source, published_date, published, office_id, article_id,
                                 url, "not_in_prepared_db"))
        unprepared_partial.replace(unprepared_path)
        raw_article_count = raw.execute(
            "SELECT COUNT(*) FROM market_news_articles"
        ).fetchone()[0]
        raw_unique_urls = raw.execute(
            "SELECT COUNT(DISTINCT article_url) FROM market_news_articles"
        ).fetchone()[0]
        prepared_article_count = prepared.execute(
            "SELECT COUNT(*) FROM prepared_news"
        ).fetchone()[0]
        day_path = output_dir / "market-news-low-volume-days.csv"
        day_partial = day_path.with_suffix(".csv.partial")
        with day_partial.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=list(low_volume[0]) if low_volume else
                                    ["source", "target_date", "classification"])
            writer.writeheader()
            writer.writerows(low_volume)
        day_partial.replace(day_path)
        report = {
            "schema": "market-news-recovery-audit/v1", "generated_at_utc": generated_at,
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "sources": {
                "raw": {"path": str(raw_path.resolve()), "bytes": raw_path.stat().st_size,
                        "modified_at_utc": datetime.fromtimestamp(raw_path.stat().st_mtime, UTC).isoformat()},
                "prepared": {"path": str(prepared_path.resolve()),
                             "bytes": prepared_path.stat().st_size,
                             "modified_at_utc": datetime.fromtimestamp(
                                 prepared_path.stat().st_mtime, UTC).isoformat()},
            },
            "day_states": {f"{source}:{state}": count for (source, state), count in
                           sorted(day_states.items())},
            "page_day_count": page_day_count,
            "page_sequence_issues": page_sequence_issues,
            "missing_dates": missing_dates,
            "expected_through": expected_through.isoformat() if expected_through else None,
            "uncollected_after_last_ledger_date": outside_run_dates,
            "low_volume_candidates": len(low_volume),
            "low_volume_by_source": dict(Counter(item["source"] for item in low_volume)),
            "retry_candidates": dict(retry_counts),
            "retry_by_source": dict(retry_by_source),
            "raw_article_rows": raw_article_count,
            "raw_unique_article_urls": raw_unique_urls,
            "prepared_article_rows": prepared_article_count,
            "unprepared_articles_by_source": dict(unprepared_counts),
            "unprepared_articles_by_source_year": dict(unprepared_years),
            "failed_reasons": dict(failed_reasons),
            "interpretation": [
                "A low-volume day is a review candidate, not confirmed missing news.",
                "An empty terminal page may set state=empty even when earlier pages have articles.",
                "summary_only means full text was not extracted; source deletion, timeout, "
                "block and parser failure cannot be distinguished per article from this DB.",
                "Unprepared means a raw article URL has no BODY/RULE preparation row. "
                "It does not imply the source page or original article is missing.",
                "No network retry or source DB mutation was performed.",
            ],
        }
        report_path = output_dir / "market-news-recovery-audit.json"
        report_partial = report_path.with_suffix(".json.partial")
        report_partial.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        report_partial.replace(report_path)
        return report
    finally:
        prepared.close()
        raw.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/naver_stock_market_news.sqlite3"))
    parser.add_argument("--prepared", type=Path,
                        default=Path("data/historical_collection/prepared-market-news.sqlite3"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("data/historical_collection/audits/market-news-20260929"))
    parser.add_argument("--expected-through", type=date.fromisoformat,
                        help="Report dates after the ledger's last date separately from internal gaps")
    args = parser.parse_args()
    print(json.dumps(audit(args.raw, args.prepared, args.output_dir,
                           expected_through=args.expected_through), ensure_ascii=False))


if __name__ == "__main__":
    main()

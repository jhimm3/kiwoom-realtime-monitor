"""Names permitted in a PC history archive and their indexed read keys."""

from __future__ import annotations

import base64
import hashlib


PC_HISTORY_ARCHIVE_KIND = "pc_history"
RESEARCH_FILE_SOURCE = "research/artifacts"
RESEARCH_FILE_CATALOG = "research/file_catalog"
RESEARCH_FILE_CHUNK_BYTES = 8 * 1024 * 1024

MARKET_SNAPSHOTS = frozenset({
    "ranking", "top20_membership", "top20_index", "top20_index_reconciled",
    "market_state", "market_index_chart", "investor_flow", "program_flow",
    "new_high", "stock_fundamentals", "nxt_eligibility", "nxt_eligibility_catalog",
    "closing_auction_price",
})
MARKET_DOCUMENTS = frozenset({
    "stock_catalog", "stock_fundamentals", "stock_nxt_eligibility", "historical_highs",
    "stock_price_references", "krx_equity_calendar", "krx_trading_day_observations", "listing_evidence",
    "market_data_coverage", "market_data_coverage_daily", "market_data_coverage_intraday",
    "market_index_chart_coverage", "daily_bar_history_coverage", "top20_daily_entrants",
    "candidate_flow_capture", "candidate_flow_finalization", "minute_trade_value_comparisons",
    "external_market_collection_status", "external_market_roll_state", "market_event_sessions",
    "condition_search_status", "morning_market_brief_runs",
    "news_article", "news_assessment", "news_original_publication", "news_contract_filing",
})
CENTRAL_TABLES = frozenset({
    "central_minute_bars", "central_daily_bars", "central_five_minute_bars",
    "central_second_trade_bars", "central_external_bars", "central_observation_revisions",
    "central_market_data_observation_meta", "central_theme_snapshots", "central_vi_event_revisions",
    "central_hot_cohort_revisions", "central_upper_limit_fact_revisions", "central_shadow_decisions",
    "central_shadow_candidate_events", "central_research_exports", "central_research_export_members",
    "central_execution_intents", "central_execution_events",
    "central_news_article_revisions", "central_news_article_target_revisions",
    "central_news_event_revisions", "central_news_event_membership_revisions", "central_news_ai_revisions",
})
JOURNAL_TABLES = frozenset({
    "journal_minute_bars", "journal_daily_bars", "journal_stocks", "trade_fills",
    "trade_group_overrides", "trade_reviews", "daily_trade_costs", "trade_setup_classifications",
    "trade_setup_cycle_overrides", "trade_entry_snapshots", "journal_analysis_revisions",
    "journal_research_links",
})
DESKTOP_NEWS_TABLES = frozenset({
    "stock_news", "stock_news_ai", "news_ai_shared", "news_ai_inferences", "journal_news_links",
})
DESKTOP_MARKET_TABLES = frozenset({
    "stocks", "stock_aliases", "stock_name_history", "kind_name_disclosures",
    "themes", "stock_themes", "theme_profiles", "profile_themes", "profile_stock_themes",
    "profile_theme_name_decisions", "profile_theme_suggestions",
    "new_high_snapshot", "new_high_snapshot_meta", "historical_high_evidence", "intraday_highs",
    "minute_bars", "daily_bars", "market_index_minute_bars", "market_index_daily_bars",
    "top20_trade_value_index", "market_data_observation_meta", "market_data_finalization_log",
    "market_data_unconfirmed_log",
})
RESEARCH_TABLES = frozenset({
    "research_runs", "research_feature_snapshots", "research_decisions", "research_candidate_events",
    "research_execution_events", "research_outcome_labels", "research_run_evaluations", "research_reports",
})


def valid_history_source(source: object) -> bool:
    if not isinstance(source, str):
        return False
    if source in {RESEARCH_FILE_SOURCE, RESEARCH_FILE_CATALOG}:
        return True
    family, separator, name = source.partition("/")
    if not separator:
        return source in CENTRAL_TABLES
    return ((family == "snapshot" and name in MARKET_SNAPSHOTS)
            or (family == "document" and name in MARKET_DOCUMENTS)
            or (family == "journal" and name in JOURNAL_TABLES)
            or (family == "desktop_news" and name in DESKTOP_NEWS_TABLES)
            or (family == "desktop_market" and name in DESKTOP_MARKET_TABLES)
            or (family == "research" and name in RESEARCH_TABLES))


def research_chunk_bytes(record: dict) -> bytes:
    data = record["data"]
    digest = data["file_sha256"]
    index, size, count = data["chunk_index"], data["total_bytes"], data["chunk_count"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(index) is not int or type(size) is not int or type(count) is not int
            or size < 0 or count != max(1, (size + RESEARCH_FILE_CHUNK_BYTES - 1) // RESEARCH_FILE_CHUNK_BYTES)
            or not 0 <= index < count or record["subject"] != digest
            or record["sort"] != f"{index:012d}" or record["key"] != f"{digest}:{index:012d}"):
        raise ValueError("invalid research archive chunk identity")
    chunk = base64.b64decode(data["bytes_base64"], validate=True)
    if (len(chunk) != min(RESEARCH_FILE_CHUNK_BYTES, size - index * RESEARCH_FILE_CHUNK_BYTES)
            or hashlib.sha256(chunk).hexdigest() != data["chunk_sha256"]):
        raise ValueError("research archive chunk differs from its recorded size or hash")
    return chunk

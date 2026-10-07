from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


API_VERSION = "v1"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ServerCapabilities:
    kiwoom_rest: bool = False
    historical_data: bool = True
    realtime_stream: bool = False
    news_archive: bool = False
    ai_analysis_archive: bool = False
    themes: bool = False
    trade_journal: bool = False
    shared_settings: bool = False
    account_query_v2: bool = False
    journal_v2_sync: bool = False
    journal_news_links_v2: bool = False
    combined_minute_bars: bool = False
    trade_value_comparisons: bool = False
    runtime_credentials_v1: bool = False
    multi_account_query_v3: bool = False
    scoped_mock_orders_v2: bool = False
    account_contexts_v3: bool = False
    planned_reconnect_v1: bool = False
    execution_event_read_v1: bool = False
    mock_automation_candidate_publish_v1: bool = False
    mock_automation_candidate_read_v1: bool = False
    mock_automation_spec_publish_v1: bool = False
    mock_automation_runtime_v1: bool = False

    def as_document(self) -> dict[str, object]:
        return {
            "api_version": API_VERSION,
            "schema_version": SCHEMA_VERSION,
            "capabilities": asdict(self),
        }


def health_document() -> dict[str, object]:
    return {
        "status": "ok",
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "server_time": datetime.now(UTC).isoformat(),
    }

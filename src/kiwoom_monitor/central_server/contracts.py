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

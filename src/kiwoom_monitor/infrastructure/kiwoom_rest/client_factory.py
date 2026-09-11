from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings

from .client import KiwoomRestClient
from .local_config import LocalApiConfig
from .remote_client import RemoteKiwoomRestClient
from .failover_client import FailoverKiwoomRestClient
from .validation_client import ParallelValidationClient


class QueryClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


def create_query_client(
    api_config_path: Path, data_source_path: Path | None = None,
    source_settings: DataSourceSettings | None = None,
) -> QueryClient:
    """데이터 모드에 맞는 키움 조회 클라이언트를 만든다."""
    source_path = data_source_path or api_config_path.with_name("data_source.json")
    source = source_settings or DataSourceConfig(source_path).load()
    if source.mode in {"local_server", "personal_server"}:
        remote = RemoteKiwoomRestClient(source.server_url, source.access_token)
        if source.mode == "personal_server" and (
            source.local_fallback_enabled or source.parallel_validation_enabled
        ):
            local_settings = LocalApiConfig(api_config_path).load()
            if local_settings.app_key and local_settings.secret_key:
                local = KiwoomRestClient(local_settings)
                if not source.parallel_validation_enabled:
                    return FailoverKiwoomRestClient(remote, local)
                return ParallelValidationClient(
                    remote, local,
                    api_config_path.with_name("central_local_validation.jsonl"),
                    fallback_on_unavailable=source.local_fallback_enabled,
                )
        return remote
    return KiwoomRestClient(LocalApiConfig(api_config_path).load())

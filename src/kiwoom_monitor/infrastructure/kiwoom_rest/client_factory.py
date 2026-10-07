from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings

from .client import KiwoomRestClient
from .local_config import LocalApiConfig
from .remote_client import RemoteKiwoomRestClient
from .failover_client import FailoverKiwoomRestClient
from .validation_client import ParallelValidationClient
from .account_query import BoundDirectAccountQueryAdapter
from .local_account_binding import LocalAccountBindingConfig


class QueryClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


def _with_verified_local_account(
    client: KiwoomRestClient, settings: Any, api_config_path: Path,
) -> QueryClient:
    bindings = tuple(
        value for value in LocalAccountBindingConfig(
            api_config_path.with_name("account-bindings.dat")
        ).load_bindings()
        if value.scope.environment.value == settings.environment
    )
    return BoundDirectAccountQueryAdapter(client, bindings[0]) if len(bindings) == 1 else client


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
                account_local = _with_verified_local_account(local, local_settings, api_config_path)
                if not source.parallel_validation_enabled:
                    return FailoverKiwoomRestClient(remote, account_local)
                return ParallelValidationClient(
                    remote, account_local,
                    api_config_path.with_name("central_local_validation.jsonl"),
                    fallback_on_unavailable=source.local_fallback_enabled,
                )
        return remote
    local_settings = LocalApiConfig(api_config_path).load()
    local = KiwoomRestClient(local_settings)
    return _with_verified_local_account(local, local_settings, api_config_path)

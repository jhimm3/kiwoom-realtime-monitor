from __future__ import annotations

import json
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


class CentralOperationalSettingsClient:
    """NAS 뉴스·AI 운영 설정의 단일 HTTP 경계."""

    def __init__(self, source: DataSourceSettings) -> None:
        self._source = source

    def load(self) -> dict[str, object]:
        return self._request("GET")

    def update(self, changes: dict[str, object]) -> dict[str, object]:
        values = self.load()
        values.update(changes)
        return self._request("PUT", values)

    def save(self, values: dict[str, object]) -> dict[str, object]:
        return self._request("PUT", values)

    def _request(self, method: str, body: dict[str, object] | None = None) -> dict[str, object]:
        if not self._source.server_url or not self._source.access_token:
            raise ValueError("NAS 주소와 접속 토큰을 입력하세요.")
        request = Request(
            f"{self._source.server_url.rstrip('/')}/api/v1/settings/operations",
            method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._source.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=10, context=system_ssl_context()) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"NAS 운영 설정을 처리하지 못했습니다: {error}") from error
        if not isinstance(result, dict):
            raise RuntimeError("NAS 운영 설정 응답 형식이 올바르지 않습니다.")
        return result


def apply_to_local_news(config: LocalNaverNewsConfig, values: dict[str, object]) -> None:
    """NAS와 겹치는 설정만 로컬 뉴스 설정에 반영하고 비밀키·필터는 보존한다."""
    ai = config.load_ai()
    official = config.load_official()
    config.save(
        config.load(),
        config.load_filter(),
        replace(
            ai,
            provider=str(values.get("ai_provider", ai.provider)),
            model=str(values.get("ai_model", ai.model)),
            daily_limit=max(0, int(values.get("ai_daily_limit", ai.daily_limit))),
        ),
        replace(official, dart_enabled=bool(values.get("dart_enabled", official.dart_enabled))),
        config.load_shortcuts(),
    )

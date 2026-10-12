from __future__ import annotations

from typing import Any, Protocol


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class NxtEligibilityService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def is_enabled(self, code: str) -> bool:
        stored_loader = getattr(self._client, "load_stored_nxt_eligibility", None)
        stored = stored_loader(code) if callable(stored_loader) else None
        response = stored if isinstance(stored, dict) else self._client.request(
            "ka10100", "/api/dostk/stkinfo", {"stk_cd": code},
        )
        flag = str(response.get("nxtEnable", "")).strip().upper()
        if flag not in {"Y", "N"}:
            # An empty NAS document is preparation pending, not a negative result.
            # Raising keeps the worker from emitting/caching False for this day.
            raise ValueError(f"{code} NXT 가능 여부가 아직 확인되지 않았습니다.")
        return flag == "Y"

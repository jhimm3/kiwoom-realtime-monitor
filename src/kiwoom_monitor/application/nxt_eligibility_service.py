from __future__ import annotations

import json
from datetime import datetime
from time import monotonic
from typing import Any, Callable, Protocol

from kiwoom_monitor.application.stock_enrichment import PremarketCatalog, load_premarket_catalog, nxt_catalog_refresh_due
from kiwoom_monitor.domain.time_basis import as_kst


class RestClient(Protocol):
    def request_with_continuation(self, api_id: str, path: str, body: dict[str, Any], **kwargs): ...


class CatalogSettings(Protocol):
    def get(self, key: str, default: str) -> str: ...
    def set(self, key: str, value: str) -> None: ...


class CatalogStore(Protocol):
    def load_premarket_flags(self, day: str) -> dict[str, bool] | None: ...
    def save_premarket_catalog(self, day: str, catalog: PremarketCatalog) -> None: ...


class NxtEligibilityService:
    _DIRECT_CATALOG = "direct_nxt_eligibility_catalog"

    def __init__(
        self, client: RestClient, *, catalog_settings: CatalogSettings | None = None,
        catalog_store: CatalogStore | None = None,
    ) -> None:
        self._client = client
        self._settings = catalog_settings
        self._catalog_store = catalog_store
        self._prepared_day = ""
        self._retry_at = 0.0
        self._direct_snapshot: tuple[str, dict[str, bool] | None] | None = None

    def preparation_due(self, now: datetime) -> bool:
        now = as_kst(now)
        if self._prepared_day == now.date().isoformat() or monotonic() < self._retry_at:
            return False
        return (self._settings is not None or self._catalog_store is not None) and nxt_catalog_refresh_due(now)

    def prepare(self, now_provider: Callable[[], datetime], interrupted: Callable[[], bool]) -> None:
        now = now_provider()
        if not self.preparation_due(now):
            return
        day = as_kst(now).date().isoformat()

        def request_page(*args, **kwargs):
            current = as_kst(now_provider())
            if interrupted() or current.date().isoformat() != day or not nxt_catalog_refresh_due(current):
                raise ValueError("NXT 종목 목록 수집 시간이 종료됐습니다.")
            return self._client.request_with_continuation(*args, **kwargs)

        try:
            store = self._catalog_store
            flags = store.load_premarket_flags(day) if store is not None else self._direct_flags(day)
            if flags is not None:
                self._prepared_day = day
                return
            if self._settings is not None:
                catalog = load_premarket_catalog(request_page)
            else:
                catalog = self._client.load_stored_premarket_catalog(day)
                if catalog is None:
                    self._retry_at = monotonic() + 30
                    return
            if interrupted() or as_kst(now_provider()).date().isoformat() != day:
                return
            flags = catalog["flags"]
            if store is not None:
                store.save_premarket_catalog(day, catalog)
            elif self._settings is not None:
                self._settings.set(self._DIRECT_CATALOG, json.dumps({"day": day, "flags": flags}))
            self._direct_snapshot = (day, flags)
            self._prepared_day = day
        except Exception:
            self._retry_at = monotonic() + 300
            raise

    def _direct_flags(self, day: str) -> dict[str, bool] | None:
        cached = self._direct_snapshot
        if cached is not None and cached[0] == day:
            return cached[1]
        if self._catalog_store is not None:
            flags = self._catalog_store.load_premarket_flags(day)
            if flags is not None:
                self._direct_snapshot = (day, flags)
                return flags
        raw = self._settings.get(self._DIRECT_CATALOG, "") if self._settings is not None else ""
        try:
            snapshot = json.loads(raw)
        except ValueError:
            snapshot = None
        flags = snapshot.get("flags") if isinstance(snapshot, dict) and snapshot.get("day") == day else None
        flags = flags if isinstance(flags, dict) else None
        self._direct_snapshot = (day, flags)
        return flags

    def stored_flag(self, code: str, now_provider: Callable[[], datetime] | None = None) -> bool | None:
        if self._settings is not None:
            if now_provider is None:
                return None
            flags = self._direct_flags(as_kst(now_provider()).date().isoformat())
            flag = flags.get(code) if flags is not None else None
            return flag if isinstance(flag, bool) else None
        loader = getattr(self._client, "load_stored_nxt_eligibility", None)
        stored = loader(code) if callable(loader) else None
        flag = str(stored.get("nxtEnable", "")).strip().upper() if isinstance(stored, dict) else ""
        return True if flag == "Y" else False if flag == "N" else None

    def is_enabled(self, code: str, now_provider: Callable[[], datetime] | None = None) -> bool:
        flag = self.stored_flag(code, now_provider)
        if flag is None:
            # An empty NAS document is preparation pending, not a negative result.
            # Raising keeps the worker from emitting/caching False for this day.
            raise ValueError(f"{code} NXT 가능 여부가 아직 확인되지 않았습니다.")
        return flag

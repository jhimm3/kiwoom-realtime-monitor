from __future__ import annotations

import os
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CentralServerSettings:
    """환경 초기값과 NAS vault를 합성하는 중앙 서버 설정.

    API 키와 DB 비밀번호는 데스크톱 설정 및 Git 저장소에 기록하지 않는다.
    """

    database_url: str
    access_token: str
    host: str = "0.0.0.0"
    port: int = 8787
    kiwoom_environment: str = "real"
    kiwoom_app_key: str = ""
    kiwoom_secret_key: str = ""
    kiwoom_mock_app_key: str = ""
    kiwoom_mock_secret_key: str = ""
    naver_news_client_id: str = ""
    naver_news_client_secret: str = ""
    dart_api_key: str = ""
    dart_enabled: bool = False
    dart_cache_path: str = "data/dart_corp_codes.json"
    ai_provider: str = "none"
    ai_model: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    ai_daily_limit: int = 0
    news_refresh_seconds: int = 300
    news_naver_api_enabled: bool = True
    news_naver_stock_enabled: bool = False
    news_naver_market_enabled: bool = True
    news_naver_stock_url: str = "https://stock.naver.com/api/domestic/detail/news"
    news_naver_flash_url: str = "https://stock.naver.com/api/domestic/news/list"
    news_naver_world_url: str = "https://stock.naver.com/api/foreign/news/worldNews"
    news_history_jobs_enabled: bool = True
    news_query_set_enabled: bool = True
    news_query_set: str = "증권,코스피,코스닥,상장사,수주 계약,유상증자,인수합병,실적 전망,최대주주"
    news_query_set_refresh_seconds: int = 300
    news_processing_excluded_providers: str = ""
    news_request_hard_limit: int = 24_000
    news_watchlist_request_limit: int = 8_000
    news_query_set_request_limit: int = 16_000
    autonomous_top20_enabled: bool = True
    market_event_collection_enabled: bool = True
    research_observation_history_enabled: bool = True
    hot_cohort_condition_name: str = ""
    hot_cohort_condition_substring: str = "15%"
    external_market_enabled: bool = False
    external_market_poll_seconds: int = 300
    external_market_symbols: str = "NASDAQ_FUTURES:MNQU26.CME,WTI_FUTURES:CLV26.NYM"
    external_market_auto_roll_enabled: bool = True
    external_market_roll_confirmations: int = 2
    top20_outbox_path: str = "data/top20-index-outbox.json"
    shadow_candidate_enabled: bool = False
    shadow_candidate_config_json: str = ""
    shadow_candidate_poll_seconds: float = 2.0
    shadow_candidate_universe_max_age_seconds: int = 0
    mock_account_monitor_enabled: bool = False
    mock_order_transport_enabled: bool = False
    mock_account_ref: str = ""
    mock_execution_run_id: str = ""
    account_identity_hmac_key: str = ""
    account_identity_registry_enabled: bool = False
    credential_directory: str = ""
    credential_trusted_proxies: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls) -> "CentralServerSettings":
        database_url = os.environ.get("KIWOOM_SERVER_DATABASE_URL", "sqlite:///data/monitor.sqlite3").strip()
        # 새 이름은 키움 공식 API 토큰과 혼동되지 않게 한다. 기존 배포의
        # 환경 변수도 당분간 보조값으로 읽어 업데이트 시 연결이 끊기지 않는다.
        access_token = (
            os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", "").strip()
            or os.environ.get("KIWOOM_SERVER_ACCESS_TOKEN", "").strip()
        )
        environment = os.environ.get("KIWOOM_ENVIRONMENT", "real").strip().lower()
        if not access_token:
            raise ValueError("MONITOR_SERVER_ACCESS_TOKEN을 설정하세요.")
        if environment not in {"real", "mock"}:
            raise ValueError("KIWOOM_ENVIRONMENT는 real 또는 mock이어야 합니다.")
        try:
            port = int(os.environ.get("KIWOOM_SERVER_PORT", "8787"))
        except ValueError as error:
            raise ValueError("KIWOOM_SERVER_PORT는 숫자여야 합니다.") from error
        if not 1 <= port <= 65535:
            raise ValueError("KIWOOM_SERVER_PORT는 1~65535 범위여야 합니다.")
        try:
            ai_daily_limit = max(0, int(os.environ.get("NEWS_AI_DAILY_LIMIT", "0")))
        except ValueError as error:
            raise ValueError("NEWS_AI_DAILY_LIMIT는 0 이상의 숫자여야 합니다.") from error
        try:
            news_refresh_seconds = max(60, int(os.environ.get("NEWS_REFRESH_SECONDS", "300")))
        except ValueError as error:
            raise ValueError("NEWS_REFRESH_SECONDS는 60 이상의 숫자여야 합니다.") from error
        try:
            query_refresh = max(60, int(os.environ.get("NEWS_QUERY_SET_REFRESH_SECONDS", "300")))
            hard_limit = max(1, min(24_000, int(os.environ.get("NEWS_REQUEST_HARD_LIMIT", "24000"))))
            watchlist_limit = max(0, min(hard_limit, int(os.environ.get("NEWS_WATCHLIST_REQUEST_LIMIT", "8000"))))
            query_limit = max(0, min(hard_limit - watchlist_limit,
                                     int(os.environ.get("NEWS_QUERY_SET_REQUEST_LIMIT", "16000"))))
        except ValueError as error:
            raise ValueError("뉴스 수집 주기와 요청 예산은 0 이상의 숫자여야 합니다.") from error
        try:
            external_market_poll_seconds = max(60, int(os.environ.get("EXTERNAL_MARKET_POLL_SECONDS", "300")))
        except ValueError as error:
            raise ValueError("EXTERNAL_MARKET_POLL_SECONDS는 60 이상의 숫자여야 합니다.") from error
        try:
            external_market_roll_confirmations = max(
                1, int(os.environ.get("EXTERNAL_MARKET_ROLL_CONFIRMATIONS", "2")),
            )
        except ValueError as error:
            raise ValueError("EXTERNAL_MARKET_ROLL_CONFIRMATIONS는 1 이상의 숫자여야 합니다.") from error
        shadow_enabled = os.environ.get(
            "SHADOW_CANDIDATE_ENABLED", "0",
        ).strip().casefold() in {"1", "true", "yes", "on"}
        shadow_config = os.environ.get("SHADOW_CANDIDATE_CONFIG_JSON", "").strip()
        try:
            shadow_poll = max(0.5, float(os.environ.get("SHADOW_CANDIDATE_POLL_SECONDS", "2")))
            shadow_universe_age = int(os.environ.get("SHADOW_CANDIDATE_UNIVERSE_MAX_AGE_SECONDS", "0"))
        except ValueError as error:
            raise ValueError("shadow 후보 주기와 후보군 최신성은 숫자여야 합니다.") from error
        if shadow_enabled:
            if not shadow_config or shadow_universe_age <= 0:
                raise ValueError(
                    "shadow 후보를 켜려면 SHADOW_CANDIDATE_CONFIG_JSON과 "
                    "SHADOW_CANDIDATE_UNIVERSE_MAX_AGE_SECONDS를 명시하세요."
                )
            try:
                if not isinstance(json.loads(shadow_config), dict):
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("SHADOW_CANDIDATE_CONFIG_JSON은 JSON 객체여야 합니다.") from error
        mock_account_monitor_enabled = os.environ.get(
            "MOCK_ACCOUNT_MONITOR_ENABLED", "0",
        ).strip().casefold() in {"1", "true", "yes", "on"}
        mock_order_transport_enabled = os.environ.get(
            "MOCK_ORDER_TRANSPORT_ENABLED", "0",
        ).strip().casefold() in {"1", "true", "yes", "on"}
        mock_account_ref = os.environ.get("MOCK_ACCOUNT_REF", "").strip()
        mock_execution_run_id = os.environ.get("MOCK_EXECUTION_RUN_ID", "").strip()
        account_identity_registry_enabled = os.environ.get(
            "ACCOUNT_IDENTITY_REGISTRY_ENABLED", "0",
        ).strip().casefold() in {"1", "true", "yes", "on"}
        if account_identity_registry_enabled and len(os.environ.get(
            "ACCOUNT_IDENTITY_HMAC_KEY", "",
        ).encode("utf-8")) < 32:
            raise ValueError(
                "계좌 신원 registry를 켜려면 ACCOUNT_IDENTITY_HMAC_KEY를 32바이트 이상으로 설정하세요."
            )
        mock_app_key = os.environ.get("KIWOOM_MOCK_APP_KEY", "").strip()
        mock_secret_key = os.environ.get("KIWOOM_MOCK_SECRET_KEY", "").strip()
        if mock_order_transport_enabled and not mock_account_monitor_enabled:
            raise ValueError(
                "모의주문 전송을 켜려면 MOCK_ACCOUNT_MONITOR_ENABLED도 켜야 합니다."
            )
        credential_directory = os.environ.get("KIWOOM_SERVER_SECRET_DIR", "").strip()
        import ipaddress
        try:
            credential_trusted_proxies = tuple(str(ipaddress.ip_address(value.strip())) for value in
                os.environ.get("CREDENTIAL_TRUSTED_PROXIES", "").split(",") if value.strip())
        except ValueError:
            raise ValueError("CREDENTIAL_TRUSTED_PROXIES는 IP 목록이어야 합니다.") from None
        if mock_account_monitor_enabled and not credential_directory:
            if not mock_app_key or not mock_secret_key:
                raise ValueError(
                    "모의계좌 모니터를 켜려면 KIWOOM_MOCK_APP_KEY와 "
                    "KIWOOM_MOCK_SECRET_KEY를 설정하세요."
                )
            if not mock_account_ref or not mock_execution_run_id:
                raise ValueError(
                    "모의계좌 모니터를 켜려면 MOCK_ACCOUNT_REF와 MOCK_EXECUTION_RUN_ID를 설정하세요."
                )
        return cls(
            database_url=database_url,
            access_token=access_token,
            host=os.environ.get("KIWOOM_SERVER_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=port,
            kiwoom_environment=environment,
            kiwoom_app_key=os.environ.get("KIWOOM_APP_KEY", "").strip(),
            kiwoom_secret_key=os.environ.get("KIWOOM_SECRET_KEY", "").strip(),
            kiwoom_mock_app_key=mock_app_key,
            kiwoom_mock_secret_key=mock_secret_key,
            naver_news_client_id=os.environ.get("NAVER_NEWS_CLIENT_ID", "").strip(),
            naver_news_client_secret=os.environ.get("NAVER_NEWS_CLIENT_SECRET", "").strip(),
            dart_api_key=os.environ.get("DART_API_KEY", "").strip(),
            dart_enabled=os.environ.get("DART_ENABLED", "0").strip().casefold() in {"1", "true", "yes", "on"},
            dart_cache_path=os.environ.get("DART_CACHE_PATH", "data/dart_corp_codes.json").strip()
            or "data/dart_corp_codes.json",
            ai_provider=os.environ.get("NEWS_AI_PROVIDER", "none").strip().casefold(),
            ai_model=os.environ.get("NEWS_AI_MODEL", "").strip(),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            ai_daily_limit=ai_daily_limit,
            news_refresh_seconds=news_refresh_seconds,
            news_naver_api_enabled=os.environ.get("NEWS_NAVER_API_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            news_naver_stock_enabled=os.environ.get("NEWS_NAVER_STOCK_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            news_naver_market_enabled=os.environ.get("NEWS_NAVER_MARKET_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            news_naver_stock_url=os.environ.get(
                "NEWS_NAVER_STOCK_URL", "https://stock.naver.com/api/domestic/detail/news",
            ).strip(),
            news_naver_flash_url=os.environ.get(
                "NEWS_NAVER_FLASH_URL", "https://stock.naver.com/api/domestic/news/list",
            ).strip(),
            news_naver_world_url=os.environ.get(
                "NEWS_NAVER_WORLD_URL", "https://stock.naver.com/api/foreign/news/worldNews",
            ).strip(),
            news_history_jobs_enabled=os.environ.get("NEWS_HISTORY_JOBS_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            news_query_set_enabled=os.environ.get("NEWS_QUERY_SET_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            news_query_set=os.environ.get(
                "NEWS_QUERY_SET", "증권,코스피,코스닥,상장사,수주 계약,유상증자,인수합병,실적 전망,최대주주",
            ).strip(),
            news_query_set_refresh_seconds=query_refresh,
            news_processing_excluded_providers=os.environ.get(
                "NEWS_PROCESSING_EXCLUDED_PROVIDERS", "",
            ).strip(),
            news_request_hard_limit=hard_limit,
            news_watchlist_request_limit=watchlist_limit,
            news_query_set_request_limit=query_limit,
            autonomous_top20_enabled=os.environ.get("AUTONOMOUS_TOP20_ENABLED", "1").strip().casefold()
            in {"1", "true", "yes", "on"},
            market_event_collection_enabled=os.environ.get(
                "MARKET_EVENT_COLLECTION_ENABLED", "true",
            ).strip().casefold() in {"1", "true", "yes", "on"},
            research_observation_history_enabled=os.environ.get(
                "RESEARCH_OBSERVATION_HISTORY_ENABLED", "true",
            ).strip().casefold() in {"1", "true", "yes", "on"},
            hot_cohort_condition_name=os.environ.get("HOT_COHORT_CONDITION_NAME", "").strip(),
            hot_cohort_condition_substring=os.environ.get(
                "HOT_COHORT_CONDITION_SUBSTRING", "15%",
            ).strip() or "15%",
            external_market_enabled=os.environ.get("EXTERNAL_MARKET_ENABLED", "0").strip().casefold()
            in {"1", "true", "yes", "on"},
            external_market_poll_seconds=external_market_poll_seconds,
            external_market_symbols=os.environ.get(
                "EXTERNAL_MARKET_SYMBOLS", "NASDAQ_FUTURES:MNQU26.CME,WTI_FUTURES:CLV26.NYM",
            ).strip(),
            external_market_auto_roll_enabled=os.environ.get(
                "EXTERNAL_MARKET_AUTO_ROLL_ENABLED", "1",
            ).strip().casefold() in {"1", "true", "yes", "on"},
            external_market_roll_confirmations=external_market_roll_confirmations,
            top20_outbox_path=os.environ.get(
                "TOP20_OUTBOX_PATH", "data/top20-index-outbox.json",
            ).strip() or "data/top20-index-outbox.json",
            shadow_candidate_enabled=shadow_enabled,
            shadow_candidate_config_json=shadow_config,
            shadow_candidate_poll_seconds=shadow_poll,
            shadow_candidate_universe_max_age_seconds=shadow_universe_age,
            mock_account_monitor_enabled=mock_account_monitor_enabled,
            mock_order_transport_enabled=mock_order_transport_enabled,
            mock_account_ref=mock_account_ref,
            mock_execution_run_id=mock_execution_run_id,
            account_identity_hmac_key=os.environ.get(
                "ACCOUNT_IDENTITY_HMAC_KEY", "",
            ),
            account_identity_registry_enabled=account_identity_registry_enabled,
            credential_directory=credential_directory,
            credential_trusted_proxies=credential_trusted_proxies,
        )

    def account_identity_key(self) -> bytes:
        value = self.account_identity_hmac_key.encode("utf-8")
        if len(value) < 32:
            raise ValueError("ACCOUNT_IDENTITY_HMAC_KEY는 32바이트 이상이어야 합니다.")
        return value

    def parsed_external_market_symbols(self) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for raw in self.external_market_symbols.split(","):
            instrument, separator, contract = raw.strip().partition(":")
            if separator and instrument.strip() and contract.strip():
                pairs[instrument.strip().upper()] = contract.strip()
        return pairs

    def parsed_news_query_set(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip() for value in self.news_query_set.split(",") if value.strip()))[:50]

    def parsed_news_processing_excluded_providers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            value.strip() for value in self.news_processing_excluded_providers.split(",") if value.strip()
        ))[:100]

    @property
    def kiwoom_configured(self) -> bool:
        return bool(self.kiwoom_app_key and self.kiwoom_secret_key)

    @property
    def kiwoom_mock_configured(self) -> bool:
        return bool(self.kiwoom_mock_app_key and self.kiwoom_mock_secret_key)

    @property
    def news_configured(self) -> bool:
        return bool(
            (self.naver_news_client_id and self.naver_news_client_secret)
            or (self.dart_enabled and self.dart_api_key)
        )

    def ai_key(self, provider: str = "") -> str:
        selected = (provider or self.ai_provider).casefold()
        return {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "claude": self.anthropic_api_key,
        }.get(selected, "")

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key or self.gemini_api_key or self.anthropic_api_key)

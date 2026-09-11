from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CentralServerSettings:
    """환경 변수로만 주입되는 중앙 서버 설정.

    API 키와 DB 비밀번호는 데스크톱 설정 및 Git 저장소에 기록하지 않는다.
    """

    database_url: str
    access_token: str
    host: str = "0.0.0.0"
    port: int = 8787
    kiwoom_environment: str = "real"
    kiwoom_app_key: str = ""
    kiwoom_secret_key: str = ""
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
    autonomous_top20_enabled: bool = True
    external_market_enabled: bool = False
    external_market_poll_seconds: int = 300
    external_market_symbols: str = "NASDAQ_FUTURES:MNQU26.CME,WTI_FUTURES:CLV26.NYM"
    external_market_auto_roll_enabled: bool = True
    external_market_roll_confirmations: int = 2
    top20_outbox_path: str = "data/top20-index-outbox.json"

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
            external_market_poll_seconds = max(60, int(os.environ.get("EXTERNAL_MARKET_POLL_SECONDS", "300")))
        except ValueError as error:
            raise ValueError("EXTERNAL_MARKET_POLL_SECONDS는 60 이상의 숫자여야 합니다.") from error
        try:
            external_market_roll_confirmations = max(
                1, int(os.environ.get("EXTERNAL_MARKET_ROLL_CONFIRMATIONS", "2")),
            )
        except ValueError as error:
            raise ValueError("EXTERNAL_MARKET_ROLL_CONFIRMATIONS는 1 이상의 숫자여야 합니다.") from error
        return cls(
            database_url=database_url,
            access_token=access_token,
            host=os.environ.get("KIWOOM_SERVER_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=port,
            kiwoom_environment=environment,
            kiwoom_app_key=os.environ.get("KIWOOM_APP_KEY", "").strip(),
            kiwoom_secret_key=os.environ.get("KIWOOM_SECRET_KEY", "").strip(),
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
            autonomous_top20_enabled=os.environ.get("AUTONOMOUS_TOP20_ENABLED", "1").strip().casefold()
            in {"1", "true", "yes", "on"},
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
        )

    def parsed_external_market_symbols(self) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for raw in self.external_market_symbols.split(","):
            instrument, separator, contract = raw.strip().partition(":")
            if separator and instrument.strip() and contract.strip():
                pairs[instrument.strip().upper()] = contract.strip()
        return pairs

    @property
    def kiwoom_configured(self) -> bool:
        return bool(self.kiwoom_app_key and self.kiwoom_secret_key)

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

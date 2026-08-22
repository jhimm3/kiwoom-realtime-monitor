"""키움 API 연결 정보 모델과 이전 설정 파일 호환 처리를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BASE_URLS = {
    "mock": "https://mockapi.kiwoom.com",
    "real": "https://api.kiwoom.com",
}


class KiwoomSettingsError(ValueError):
    """키움 API 환경 설정이 없거나 형식이 맞지 않을 때 발생한다."""


@dataclass(frozen=True)
class KiwoomSettings:
    app_key: str
    secret_key: str
    environment: str

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.environment]

    @classmethod
    def from_env_file(cls, path: Path) -> "KiwoomSettings":
        if not path.exists():
            raise KiwoomSettingsError(f"키움 설정 파일을 찾을 수 없습니다: {path.name}")

        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", maxsplit=1)
            values[key.strip()] = value.strip()

        app_key = values.get("KIWOOM_APP_KEY", "")
        secret_key = values.get("KIWOOM_SECRET_KEY", "")
        environment = values.get("KIWOOM_ENVIRONMENT", "").lower()
        if not app_key or not secret_key:
            raise KiwoomSettingsError("KIWOOM_APP_KEY와 KIWOOM_SECRET_KEY를 모두 설정하세요.")
        if environment not in BASE_URLS:
            raise KiwoomSettingsError("KIWOOM_ENVIRONMENT는 mock 또는 real이어야 합니다.")
        return cls(app_key=app_key, secret_key=secret_key, environment=environment)

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DataSourceSettings:
    mode: str = "local"
    server_url: str = ""
    access_token: str = ""
    local_fallback_enabled: bool = False
    parallel_validation_enabled: bool = False

    def validate(self) -> None:
        if self.mode not in {"local", "local_server", "personal_server"}:
            raise ValueError("데이터 모드는 local, local_server 또는 personal_server여야 합니다.")
        if self.mode == "local":
            return
        parsed = urlsplit(self.server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("중앙 서버 주소는 http:// 또는 https://로 시작해야 합니다.")
        if not self.access_token:
            raise ValueError("중앙 서버 접속 토큰을 입력하세요.")


class DataSourceConfig:
    """PC별 서버 주소 설정. API 공급자 비밀키는 여기에 저장하지 않는다."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> DataSourceSettings:
        if not self._path.exists():
            return DataSourceSettings()
        values = json.loads(self._path.read_text(encoding="utf-8"))
        result = DataSourceSettings(
            mode=str(values.get("mode", "local")),
            server_url=str(values.get("server_url", "")).rstrip("/"),
            access_token=str(values.get("access_token", "")),
            local_fallback_enabled=bool(values.get("local_fallback_enabled", False)),
            parallel_validation_enabled=bool(values.get("parallel_validation_enabled", False)),
        )
        result.validate()
        return result

    def save(self, settings: DataSourceSettings) -> None:
        settings.validate()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)

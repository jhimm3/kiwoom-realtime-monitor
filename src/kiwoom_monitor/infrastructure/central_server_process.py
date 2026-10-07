from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig


class LocalCentralServerProcess:
    """현재 앱이 소유하는 localhost 중앙 서버 프로세스."""

    def __init__(
        self, source: DataSourceSettings, api_config_path: Path, database_path: Path,
        *, executable: str | None = None,
        frozen: bool | None = None,
    ) -> None:
        if source.mode != "local_server":
            raise ValueError("로컬 중앙 서버 모드가 아닙니다.")
        self._source = source
        self._api_config_path = api_config_path
        self._database_path = database_path.resolve()
        self._executable = executable or sys.executable
        self._frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self._process: subprocess.Popen[bytes] | None = None
        parsed = urlsplit(source.server_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("로컬 중앙 서버 주소는 이 PC의 http 주소여야 합니다.")

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    def start(self, timeout_seconds: float = 10.0) -> bool:
        """이미 같은 토큰의 서버가 있으면 재사용하고, 없으면 하나만 시작한다."""
        if self._is_ready():
            return False
        profiles = LocalApiConfig(self._api_config_path).load_profiles()
        if profiles.active_environment == "real":
            app_key, secret_key = profiles.real_app_key, profiles.real_secret_key
        else:
            app_key, secret_key = profiles.mock_app_key, profiles.mock_secret_key
        if not app_key or not secret_key:
            raise ValueError("로컬 중앙 서버를 시작하려면 선택한 환경의 키움 API 키가 필요합니다.")
        environment = os.environ.copy()
        environment.update({
            "KIWOOM_SERVER_DATABASE_URL": f"sqlite:///{self._database_path.as_posix()}",
            "MONITOR_SERVER_ACCESS_TOKEN": self._source.access_token,
            "KIWOOM_SERVER_HOST": "127.0.0.1",
            "KIWOOM_SERVER_PORT": str(self._port()),
            "KIWOOM_ENVIRONMENT": profiles.active_environment,
            "KIWOOM_APP_KEY": app_key,
            "KIWOOM_SECRET_KEY": secret_key,
        })
        try:
            news = LocalNaverNewsConfig(self._api_config_path.with_name("naver_news.dat")).load()
            if news.client_id and news.client_secret:
                environment.update({
                    "NAVER_NEWS_CLIENT_ID": news.client_id,
                    "NAVER_NEWS_CLIENT_SECRET": news.client_secret,
                })
            official = LocalNaverNewsConfig(self._api_config_path.with_name("naver_news.dat")).load_official()
            if official.dart_enabled and official.dart_api_key:
                environment.update({
                    "DART_ENABLED": "1", "DART_API_KEY": official.dart_api_key,
                    "DART_CACHE_PATH": str(self._api_config_path.with_name("dart_corp_codes.json")),
                })
            ai = LocalNaverNewsConfig(self._api_config_path.with_name("naver_news.dat")).load_ai()
            if ai.provider in {"openai", "gemini", "claude"} and ai.api_key:
                key_name = {
                    "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY",
                    "claude": "ANTHROPIC_API_KEY",
                }[ai.provider]
                environment.update({
                    "NEWS_AI_PROVIDER": ai.provider, "NEWS_AI_MODEL": ai.model,
                    "NEWS_AI_DAILY_LIMIT": str(ai.daily_limit),
                    key_name: ai.api_key,
                })
        except (OSError, ValueError):
            # 뉴스 설정 오류는 키움 중앙 서버 시작 자체를 막지 않는다.
            pass
        if self._frozen:
            command = [self._executable, "--central-server", "--parent-pid", str(os.getpid())]
        else:
            command = [
                self._executable, "-m", "kiwoom_monitor.central_server",
                "--parent-pid", str(os.getpid()),
            ]
        self._process = subprocess.Popen(
            command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("로컬 중앙 서버가 시작 중 종료되었습니다.")
            if self._is_ready():
                return True
            time.sleep(0.1)
        self.stop()
        raise TimeoutError("로컬 중앙 서버가 제한 시간 안에 준비되지 않았습니다.")

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def _is_ready(self) -> bool:
        request = Request(
            f"{self._source.server_url.rstrip('/')}/api/v1/capabilities",
            headers={"Authorization": f"Bearer {self._source.access_token}"},
        )
        try:
            with urlopen(request, timeout=0.5) as response:
                document = json.loads(response.read().decode("utf-8"))
            return bool(document.get("capabilities", {}).get("kiwoom_rest"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            return False

    def _port(self) -> int:
        parsed = urlsplit(self._source.server_url)
        return parsed.port or (443 if parsed.scheme == "https" else 80)

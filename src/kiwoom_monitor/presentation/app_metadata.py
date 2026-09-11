"""화면과 업데이트 확인에서 공유하는 앱 표시 정보."""

from __future__ import annotations

import sys

APP_VERSION = "2.0.0"
APP_DISPLAY_NAME = "키움 실시간 모니터" if getattr(sys, "frozen", False) else "키움 실시간 모니터 (테스트)"
APP_COPYRIGHT = "Copyright 2026 크니. All rights reserved."
INVESTMENT_NOTICE = "본 앱은 투자 자문이 아니며 시세 지연·오류가 있을 수 있습니다."

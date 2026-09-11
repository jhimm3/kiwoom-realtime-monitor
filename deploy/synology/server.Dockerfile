FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts/check_postgres_integration.py ./scripts/check_postgres_integration.py
# 잘못된 구버전 소스를 재사용하면 컨테이너 실행 뒤 404가 나는 대신
# 빌드 단계에서 즉시 발견한다.
RUN grep -q '/api/v1/settings/operations' /app/src/kiwoom_monitor/central_server/app.py \
    && grep -q '/api/v1/diagnostics/resources' /app/src/kiwoom_monitor/central_server/app.py \
    && grep -q 'def update_operational_settings' /app/src/kiwoom_monitor/central_server/news_service.py \
    && grep -q '2026.09.12-shared-backup-contract-v1' /app/src/kiwoom_monitor/central_server/app.py \
    && test -f /app/src/kiwoom_monitor/central_server/schema_migrations.py \
    && test -f /app/src/kiwoom_monitor/central_server/persistent_outbox.py \
    && test -f /app/src/kiwoom_monitor/infrastructure/krx/stock_catalog.py \
    && test -f /app/scripts/check_postgres_integration.py \
    && grep -q '저장된 {market} 일봉이 없습니다' /app/src/kiwoom_monitor/central_server/autonomous_top20.py \
    && test -f /app/src/kiwoom_monitor/central_server/futures_roll.py \
    && grep -q 'next_futures_contract' /app/src/kiwoom_monitor/central_server/futures_roll.py
# 빌드 단계에서 Linux 호환성을 확인한다. 예전 Windows 전용 소스가
# 빌드 컨텍스트에 남아 있어도 NAS 이미지 안에서 안전하게 보정한다.
RUN sed -i \
      's/_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)/_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None/; \
       s/_kernel32.LocalFree.argtypes = \[ctypes.c_void_p\]/if _kernel32 is not None: _kernel32.LocalFree.argtypes = [ctypes.c_void_p]/; \
       s/_kernel32.LocalFree.restype = ctypes.c_void_p/if _kernel32 is not None: _kernel32.LocalFree.restype = ctypes.c_void_p/' \
      /app/src/kiwoom_monitor/infrastructure/kiwoom_rest/local_config.py
RUN PYTHONPATH=/app/src python -c "import kiwoom_monitor.infrastructure.kiwoom_rest.local_config"
# NAS 서버에는 Windows UI, OCR, Google Drive 패키지가 필요하지 않다.
# 프로젝트 자체는 의존성 없이 설치하고 중앙 서버 실행에 필요한 패키지만
# 별도로 넣어 ARM/저사양 시놀로지에서도 이미지가 불필요하게 커지지 않게 한다.
RUN pip install --no-cache-dir --no-deps . \
    && pip install --no-cache-dir \
        "fastapi>=0.116,<1" \
        "uvicorn[standard]>=0.35,<1" \
        "psycopg[binary]>=3.2,<4" \
        "websockets==16.1.1"

# 실행 시에도 이미지에 복사한 최신 소스를 우선 사용한다.
ENV PYTHONPATH=/app/src
RUN python -c "import kiwoom_monitor.infrastructure.kiwoom_rest.local_config as m; print(m.__file__)"

EXPOSE 8787
CMD ["python", "-m", "kiwoom_monitor.central_server"]

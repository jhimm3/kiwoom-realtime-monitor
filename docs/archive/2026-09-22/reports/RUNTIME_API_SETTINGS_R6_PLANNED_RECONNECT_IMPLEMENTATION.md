> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_PLANNED_RECONNECT_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6c2 — 계획된 NAS 실시간 재연결

작성일: 2026-09-16. 로컬 구현 완료, NAS 동기화/배포 전.
누적 build: `2026.09.16-runtime-credentials-r6-planned-reconnect-v1`.

## 확인한 문제와 최소 수정

담당 collector는 키 교체 시 실제 token/socket/저장을 drain하고 hub 준비 상태를 내리지만
PC에 계획된 변경이라는 상태/유한 deadline을 보내지 않았다.
새 소켓 실패는 일반 connection_failed로 배포되어 즉시 로컬 전환 대상이 될 수 있었다.
메인 표 자체를 비우는 새 경로를 추가하지 않고 기존 collector/HTTP/WS/Qt 상태 경계를 연결했다.

- collector drain 시작부터 최대 30초의 planned reconnect를 소유한다.
  반복 drain과 같은 전환의 후속 fence는 deadline을 다시 시작하지 않는다.
- health, capabilities, 기존 WS ready/central_ready/connection_opened/connection_failed에 상태를 추가한다.
  새 connection_status event는 상태 변경을 배포하며 늦게 접속한 PC도 남은 대기만 받는다.
- PC는 유효한 generation/유한 0초 초과~30초 remaining만 허용한다.
  같은 generation 통지는 deadline을 늘리지 않고 이전/이미 끝난 generation으로 재시작하지 않는다.
- 계획된 상류 오류만 실패 신호와 즉시 로컬 전환을 유예한다. 기존 순위표와 NAS DB 우선 조회를 유지한다.
  실제 NAS transport 단절은 기존 정책을 따르고 서버가 침묵해도 PC 자체 deadline을 처리한다.
- 모든 REG 승인 뒤 준비 완료로 돌아간다. 재개 시작 실패는 즉시 정상 장애를 통지한다.
  정상 관측시간의 만료는 connection_failed이며 휴장/구독 대기는 유예만 종료하고 대기 상태로 표시한다.
- 유예 종료는 장애 표시 정책이다. 실제 drain/token/socket/저장을 취소하거나 안전 fence를 해제하지 않는다.
- 저장 자료가 없는 TR에서 broker가 paused이고 planned 상태가 유효하면 명시 대기 HTTP 503을 반환한다.
  원격 client는 일반 API 대기 오류로 분리한다. 기존 REST failover는 transport 오류만 잡으므로
  이 대기를 이유로 로컬 TR을 새로 요청하지 않는다. failover 모듈의 기존 알고리즘은 수정하지 않았다.

## 관측 간격

교체 직전 마지막 허용 0B 관측과 새 REG 이후 첫 허용 0B 관측 사이의 monotonic 간격을 측정한다.
UTC 관측시각과 함께 최신 상태·NAS/PC 로그·PC 상태 문구에 남긴다.
이전 관측이 없으면 null과 `이전 체결 관측 없음`을 사용하며 0초로 추정하지 않는다.
이는 전체 수집 연결의 관측 간격이며 종목별 누락량/호가 지연/키움 직접 대비 NAS 지연은 아니다.
새 영속 DB 이력/outbox는 만들지 않았고 기존 초·분봉 gap와 source baseline/대기 저장을 유지한다.

## 변경 파일과 계약

- `central_server/realtime_collector.py`: deadline 작업 수명, 상태/만료 통지, REG 종료, 허용 0B 관측 간격.
- `central_server/app.py`, `contracts.py`: 선택적 공개 상태/capability, WS 초기/구독 상태, paused TR의 명시 대기.
- `infrastructure/kiwoom_rest/remote_client.py`: 명시 대기 오류와 재사용 가능한 유한 시간 검증 함수.
- `infrastructure/kiwoom_rest/central_realtime_worker.py`: generation별 로컬 deadline, 계획 오류 유예/만료,
  오래된 control frame 무시와 첫 체결 간격 표시.
- `presentation/main_window.py`: 기존 상태 slot에서 central_waiting/정상 재개를 구분하며 표는 보존.
- `tests/unit/test_planned_reconnect.py`: 가짜 소켓/HTTP·실제 서버 라우트·기존 메인 상태 slot 회귀.
- Dockerfile/compose/server build ID 세 곳을 일치시켰다. 새 API 버전/계층/DB/주문 경로는 없다.

상세 wire 필드/HTTP 의미/기존 클라이언트 호환은 API_CONTRACT.md에 기록했다.
구조 감사 후 Sol이 후속 구현할 때 필요한 책임과 다음 단계는 MODULE_MAP/설계 문서에 갱신했다.

## 검증

- 초기 인접 회귀 57개 통과, exit=0: `tmp/r6c2-target.log`.
- 최초 신규 회귀 15개 통과, exit=0: `tmp/r6c2-new.log`.
- 넓은 인접 회귀 251개 통과, 126.624초, exit=0: `tmp/r6c2-regression.log`.
  서버/REST 우선순위/collector/worker/failover/기존 drain/계좌/역할/PC 설정을 포함한다.
- capability 정식 계약·PC 간격 표시·잘못된 수치 보완 후 최종 해당 소스 회귀 87개 통과,
  11.687초, exit=0: `tmp/r6c2-final-source.log`.
- 재개 시작 실패의 명시 통지 보완 후 연결/계좌/역할 최종 회귀 189개 통과,
  100.314초, exit=0: `tmp/r6c2-final-failure.log`. 최종 신규 회귀는 17개다.
- Python AST 7개·변경 파일 공백 16개·build 3곳 일치와 관련 git diff --check 통과, exit=0.
- 테스트는 fake REST/WS, 임시 SQLite/설정/vault만 사용했다. 실제 키/계좌/NAS/주문은 사용하지 않았다.

## 다음 단계와 실제 미검증

R6c 입력/재연결 표시의 로컬 구현은 끝났다. 다음은 R7 누적 배포/운영 검증이다.
현재 NAS 소스는 동기화하지 않았고 재빌드도 실행/요청하지 않았다.
R7은 공유 경로와 누적 소스·백업·보존 대상을 확인한 뒤 동기화하고 사용자 재빌드와 연결한다.
실환경은 주문 없이 여러 토큰/계좌 REG 승인·수신, 담당 키 교체, 실제 관측 간격,
deadline 이후 장애/복구와 PostgreSQL을 검증한다. 무중단 수신 완료를 주장하지 않는다.

설계 충돌/새 기술 부채를 숨긴 임시 우회는 발견하지 않았다. 모델 에스컬레이션 없음.

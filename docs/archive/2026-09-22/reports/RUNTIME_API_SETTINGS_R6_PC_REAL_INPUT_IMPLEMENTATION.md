> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_PC_REAL_INPUT_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6c1 — PC NAS 실전계좌 입력 구현

작성일: 2026-09-16. 로컬 구현·가짜 API/WS 회귀 완료. NAS 동기화/배포 전.

## 구현 결과

NAS 설정의 `NAS 실전계좌·API 키 관리` 버튼에서 실전 프로필을 추가하고
키와 계좌를 확인한 뒤 확인된 계좌에 적용할 수 있다. 기존 모의계좌 관리 버튼도 유지한다.
키 비활성화와 계좌 조회 ON/OFF는 기존 서버 계약으로 실행한다.
실전 주문 허용이나 시세 담당 변경, 매매일지 기본 계좌 변경은 실행하지 않는다.

설정 revision 적용 완료와 실제 실시간 등록 승인은 별도로 표시한다.
대기/변경 중/꺼짐/승인됨은 서버의 `monitor_status.realtime.state`를 사용한다.
등록 승인 상태가 없거나 잘못된 형식이면 상태 확인 필요로 표시한다.
수신·저장 오류도 별도로 표시하고 `상태 다시 확인`으로 최신 상태를 조회한다.
이 표시는 실시간 시세 전체의 무중단 또는 모든 계좌 REST 조회 성공을 보증하지 않는다.

## 수정 대상과 계약

- `presentation/api_settings_dialog.py`: 실전 관리 버튼과 기존 공급자별 client 캐시 연결.
  real/mock 진행 요청은 별도 client에 유지하고 NAS 모드에서만 버튼을 활성화한다.
- `infrastructure/central_credentials_client.py`: 공통 계좌 생성/prepare를 추가하고
  `kiwoom_real`과 `kiwoom_mock`의 계좌 GET/PUT 환경을 고정한다.
  기존 mock 이름 메서드는 호환 진입점으로 유지하며 실제 구현을 중복하지 않는다.
- `presentation/nas_credentials_dialog.py`: 실전용 이름/입력/기존 worker 연결,
  모의주문 토글 숨김과 계좌 설정/실시간 승인 상태 표시.
- 세 기존 단위 테스트 파일에 실전 요청·UI·서버 라우트 통합 회귀를 추가했다.

프로필 추가 입력은 label/request_id이며 해당 provider의 profiles POST를 호출한다.
키 확인은 profile/revision/일시적 replacement 또는 disable이며 기존 prepare/status를 호출한다.
apply는 READY의 같은 operation/revision/검증한 UUID 계좌에만 가능하다.
계좌 조회 설정은 같은 scope의 GET/PUT이며 real의 `mock_order_enabled`는 false만 허용한다.
계좌 또는 환경이 다른 설정 응답, READY에 계좌가 없는 응답은 거절한다.

PC 키 설정/DB/미러에는 NAS 키를 기록하지 않는다. 입력은 worker 시작 전에 비우고,
prepare 응답 유실 시 같은 request_id를 유지하며 apply를 자동 재전송하지 않는다.
계좌 PUT 응답 유실 후에는 설정을 다시 읽기 전까지 새 PUT을 막는다.
네트워크 작업은 기존 단건 Qt worker를 사용한다. 새 계층/DB migration/서버 API는 없다.

## 검증

- 초기 PC 설정 회귀 40개 통과: `tmp/r6c1-target.log`.
- 실제 서버 라우트에 연결한 가짜 모의/실전 UI 통합 2개 통과: `tmp/r6c1-integration.log`.
- 최종 인접 회귀 124개 통과, 79.546초, exit=0: `tmp/r6c1-regression.log`.
  PC client/dialog/API 설정, 뉴스·AI 관리, 실전 owner, 계좌 운영 설정/REST monitor/WS를 포함한다.
- 추가 실전 회귀는 client 4개, dialog 4개, 서버 라우트 UI 통합 1개다.
  통합 검증은 프로필 생성→확인→적용→조회 ON/OFF→비활성화를 수행한다.
- Python AST 6개·변경 파일 공백 13개·기존 서버 build 3곳 일치 확인, exit=0.
- 테스트는 가짜 REST/WS, 임시 설정/SQLite/vault만 사용했다. 실제 계좌/인증키/주문/NAS는 사용하지 않았다.

## 문서와 배포

설계 문서의 R6c를 PC 입력 R6c1과 계획된 재연결 R6c2로 구분했다.
API_CONTRACT, MODULE_MAP, ARCHITECTURE_CURRENT, CHANGELOG와 NAS 배포 대기 문서를 갱신했다.
서버 동작 변경이 없으므로 누적 서버 build는
`2026.09.16-runtime-credentials-r6-real-account-realtime-v1`을 유지한다.
현재 단계에서 NAS 동기화나 재빌드를 요청하지 않는다.

## 남은 범위

- R6c2: PC/NAS 사이 planned reconnect 상태와 실제 gap/deadline을 연결한다.
  키 변경 중 기존 정상 순위표를 보존하고 deadline 후 정상 장애/failover 정책으로 복귀해야 한다.
  계좌 화면의 승인 표시만으로 이 작업이 완료된 것은 아니다.
- R7: 누적 소스 검증/백업/동기화/재빌드 뒤 실제 복수 토큰·REG 승인/수신·PostgreSQL을 확인한다.
- 현재 시세 담당 키의 비활성화 보호와 복구 필요 상태는 기존 서버 정책을 따른다.

구조 변경이나 고난도 미해결 버그는 발견하지 않았다. 모델 에스컬레이션 없음.

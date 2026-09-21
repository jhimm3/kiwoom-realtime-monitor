> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_CLAIMS_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6b1 실전계좌 활성화 저장 계약 — 로컬 구현 결과

2026-09-15. 누적 build: `2026.09.15-runtime-credentials-r6-real-account-claims-v1`.
R0~R5·R6a 누적 소스를 유지한다. NAS 소스 동기화/배포 전이며 R7까지 중간 재빌드는 요청하지 않는다.

## 목적과 확인된 원인

R6b 실제 실전 owner 연결을 검토하며 기존 `_finalize_credential_activation`의 계좌 설정
claim이 `kiwoom_mock`에만 적용되는 것을 확인했다. 실전 activation은 binding/원장을 저장해도
`server_account_settings.active_profile_id`가 비어 있고 동일 실전계좌의 두 profile을 모두 확정할 수 있었다.
이는 설계 1.1절의 scope별 단일 활성 연결 계약에 맞지 않는다.

새 회귀 8개를 추가해 수정 전 27개 실행에서 실패 7개·오류 1개로 재현했다.
오류는 첫 실전 설정이 생성되지 않아 예상 revision=1의 변경이 거절되는 결과였다.
원인 증거: `tmp/r6b1-before.log`. 실제 NAS에서 중복 계좌가 발생했다고 판단한 것은 아니다.

이번 단계는 저장 계약을 먼저 완성한다. 단일 실전계좌만 지원하는 임시 owner나
별도 조회 우회 경로를 추가하지 않는다. 실제 실전 key 적용 hook은 R6b2에서 구현한다.

## 변경 내용과 입출력

기존 `database.py`의 모의 전용 `_claim_mock_account_settings`를 `_claim_account_settings`로 확장했다.
입력은 기존 검증된 activation과 placeholder/replay/disabled이며 새 공개 입력/SQL이 없다.
provider가 `kiwoom_real` 또는 `kiwoom_mock`일 때 해당 환경의 verified scope를 사용한다.

- 최초 적용: profile/binding/activation과 같은 트랜잭션에서 active_profile_id, 조회 ON, 모의주문 OFF를 저장한다.
- 같은 profile의 키 갱신: binding revision은 증가하지만 기존 계좌 설정·설정 revision·문서 timestamp를 보존한다.
- 같은 scope의 다른 활성 profile: ACCOUNT_PROFILE_CONFLICT. 새 binding/적용 원장/profile 활성화까지 rollback한다.
  draft profile은 draft로 남고 실패로 새로 만들어진 profile은 남지 않는다.
- 실전/모의·다른 실전계좌: 설정과 claim을 각각 분리한다. 모의계좌의 주문 ON이 실전으로 전파되지 않는다.
- disable: 기존 binding revision과 이력을 유지하고 active_profile_id=None, 조회/주문 OFF를 함께 저장한다.
- 완료 replay: 이후 변경된 설정/대체 profile을 되돌리지 않는다. 기존 실전 receipt의 설정 문서가 없다면
  명시적 같은 operation replay에서만 초기 설정을 보완한다. 기동 시 일괄 저장이나 새 migration은 없다.
- 손상 설정: 복구 필요 오류로 전체 신규 적용을 rollback한다. 정상 초기값으로 조용히 덮지 않는다.

최초 monitor ON은 영속 운영 설정이며 실제 수집 시작/applied_revision을 뜻하지 않는다.
실전 모의주문 토글 거절은 기존 `_save_account_settings`의 계약을 그대로 사용한다.
공통 helper를 통해 SQLite/PostgreSQL에 같은 로직을 적용하며 기존 BEGIN IMMEDIATE와 PostgreSQL
credential-activation advisory transaction lock을 유지한다.

## 검증

- 수정 후 계좌 설정 회귀 27개 통과. 신규 8개는 최초 적용/갱신·중복 draft rollback·disable/replay·
  두 실전계좌와 모의 분리·두 DB 연결의 동시 claim 경쟁·손상 설정·구 receipt·PostgreSQL placeholder를 검증한다.
- 인증 저장/runtime·모의 owner/scoped API·선택 계좌·실전 장벽·계좌 query·REST·DB·서버 회귀:
  214개 실행, 213개 통과, 1개 skip, 실패 0, 종료 코드 0, 79.502초. 로그 `tmp/r6b1-regression.log`.
  skip은 Windows에서 실행하지 않는 POSIX 파일 권한 검사다. 실패 주입의 예상 OSError 로그가 포함된다.
- `check_postgres_integration.py`에 실전 claim/갱신 보존/중복 atomic rollback/disable 이력/replay 보존
  5개 검사와 생성한 실전 profile/registry/settings 행의 종료 정리를 추가했다.
  추가 검사 블록은 임시 SQLite 시뮬레이션에서 5개 모두 true였다(`tmp/r6b1_verify.py`).
  실제 PostgreSQL transaction/lock/Linux 권한 검증은 실행하지 않았으며 R7에 남긴다.
- Python 4개 AST 구문/공백, build 3곳 일치 및 관련 diff 공백 검사를 확인했다.
- 실제 API/주문/키/사용자 DB/NAS 파일 변경 없음.

## 구조와 다음 단계

기능을 이해하는 DB 파일 수와 호출 깊이는 전후 동일하다. 새 manager/중복 로직/테이블/열/endpoint는 없다.
스키마는 v19와 기존 server_account_settings 문서를 유지한다.

다음 R6b2는 설계 1.1절의 시세 담당과 비담당 실전계좌를 구분하는 실제 owner/API다.
후보 준비에서 계좌 불일치/중복 claim을 먼저 검증하고 기존 계좌별 요청 간격을 이어받는다.
DB claim만으로 연결을 승인하지 않고 durable activation과 binding/설정/runtime revision을 모두 확인해야 한다.
시세 담당 교체는 R6a의 동일 collector/broker/query 장벽에 연결하고, 비담당 계좌 갱신은 시장 WS를 끊지 않는다.
그 뒤 R6c 실전 입력/계획된 재연결 표시와 R7 NAS 누적 동기화/실환경 검증이 남는다.

모델 에스컬레이션: 없음. 설계 방향 변경 없이 기존 저장 트랜잭션을 최소 확장했다.

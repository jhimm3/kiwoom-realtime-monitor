# R6b3b1 — 담당 전환 독점 구간과 snapshot 재검증

2026-09-15. 서버 내부 보호 구간 로컬 구현 완료. 실제 담당 연결 전환은 아직 미완료다.
build: `2026.09.15-runtime-credentials-r6-market-role-barrier-v1`.
NAS 동기화/배포 전이며 R7까지 중간 재빌드를 요청하지 않는다.

## 목적과 변경 범위

역할 문서 CAS만으로는 vault commit과 살아 있는 연결의 교체 경쟁을 해결할 수 없다.
기존 RealCredentialOwner에 전환 수명/독점 구간을 추가했으며 새 manager/저장 계층은 만들지 않았다.
수정은 real_runtime.py, 새 test_market_role_barrier.py, build 표시 세 곳과 관련 문서다.
새 SQL/스키마/공개 API/capability/자동주문 경로는 없다. 역할 적용 revision은 계속 null이다.

## 입출력과 호출 계약

내부 호출자는 `async with owner.market_role_change(profile_id, expected_revision=..., expected_binding_revision=...) as plan:`을 사용한다.
입력은 실전 profile ID, 저장 역할 revision과 검증된 대상 계좌 binding revision이다.
plan은 현재/대상 profile ID, 입력 revision, 두 실행 credential revision과 두 context snapshot을 가진다.
credential 값/토큰은 plan 필드나 repr에 넣지 않는다. context는 repr에서 제외한다.

구간은 첫 await 전에 예약한다. 기존 실전 credential 후보가 하나라도 예약되어 있으면
PROFILE_BUSY로 거절한다. VALIDATING뿐 아니라 READY/apply까지 기존 예약을 유지하기 때문에,
사용자 키 변경 확인 대기를 역할 작업이 무기한 기다리는 구조를 만들지 않는다.
역할 구간 중 새 실전 credential prepare도 PROFILE_BUSY다. 모의 owner와 시장 순위/조회는 이 예약을 사용하지 않는다.
credential prepare 자체도 disable 역할 정책을 읽기 전에 먼저 예약하도록 순서를 바꿨다.

예약은 저장 역할과 현재 실행 역할이 일치하는지 확인한다. 미적용 역할 의도는
MARKET_ROLE_RUNTIME_NOT_READY로 거절한다. 두 context 모두 admitted 상태여야 하며,
활성 kiwoom_real/real profile, 최신 real binding, 활성 registry/계좌 설정의 active_profile_id와
owner 실행 revision에 일치하는 활성 vault record를 검증한다. 대상 binding은 입력 revision과 일치해야 한다.
검증은 저장 데이터/암호화 파일 읽기만 하며 새 TR/인증/WS 요청을 보내지 않는다.

`await owner.validate_market_role(plan)`은 같은 예약 구간에서 직전 snapshot을 다시 검증한다.
역할 revision·계좌 binding·vault/owner 적용 revision 변화 또는 context 교체는 거절한다.
구간 밖 plan은 재사용할 수 없다. 최신 DB CAS는 후속 caller에서 별도로 수행해야 한다.
예약/재검증 자체는 문서/연결을 변경하지 않고 역할 변경 완료를 주장하지 않는다.

## 취소와 종료

예약 preflight 또는 구간 본문의 취소/예외는 finally에서 예약을 해제한다.
owner 종료는 구간 종료 event를 기다린 뒤 context를 닫는다. 종료 시작 뒤에는 새 예약과
직전 검증을 거절하며 preflight 도중 종료가 시작되어도 새 구간을 yield하지 않는다.
caller는 구간 안에서 owner.close를 직접 await하지 말고 구간부터 종료한다.
역할 작업이 실제 저장/연결을 변경한 뒤 취소되는 경우의 복구 정책은 후속 전환 caller 책임이다.
이번 해제 검증은 부작용 없는 구간에 대한 것이며 postcommit rollback 검증으로 확대 해석하지 않는다.

## 검증

- 최초 관련 테스트 39개 통과: tmp/r6b3b1-target.log.
- 인접 회귀 293개 실행, 292 통과·Windows POSIX 권한 검사 1 skip, 실패 0·exit=0, 165.455초: tmp/r6b3b1-regression-final.log.
- 이후 추가한 종료 중 preflight 거절/비활성 profile 검사를 포함한 최종 관련 회귀 43개(신규 역할 테스트 12개 포함) 통과·exit=0, 43.035초: tmp/r6b3b1-target-final.log. 전체 293개 회귀와 이 최종 검증의 소스 범위를 구분한다.
- 새 역할 테스트는 예약 읽기 전용/credential·role 상호 배제/READY 취소/입력·binding·역할 drift/vault 미적용/context 교체/preflight·본문 취소/예외/종료 대기/disable 정책 읽기 경쟁/비활성 profile/종료 중 시작을 확인한다.
- 테스트는 기존 실전 owner의 임시 SQLite·암호화 vault·가짜 API fixture를 재사용한다. 실제 키/계좌/주문/사용자 DB/NAS/PostgreSQL은 사용·변경하지 않았다.
- Python 3개 구문·변경 공백·build 세 곳 일치 확인 통과·exit=0: tmp/r6b3b1_verify.py 및 git diff --check.

## 다음 단계의 완료 기준

R6b3b2 호출자는 같은 독점 구간 안에서 기존 두 계좌 조회/물리 REST 요청과 시장 WS를 drain하고,
직전 snapshot과 DB CAS를 검증한 뒤 기존 단일 시장 collector/broker를 새 담당에 연결해야 한다.
기존 기본계좌/v2 암묵적 대상·계좌 scope/cursor·독립 요청 한도·집계 상태는 유지한다.
precommit 실패는 이전 실행을 복구하고 postcommit 실패는 이전 키/역할을 임의로 다시 열지 않는다.
최신 역할 문서에 따른 재기동 반영과 연결 적용 revision/REG 준비 공개도 후속에서 연결한다.
실제 공개 PUT은 이 전환/복구가 검증된 뒤 제공한다. 실전계좌 실시간 운영·R6c·R7도 남아 있다.
기능을 이해하는 파일은 기존 owner 한 개이며 신규 전달 계층 없이 owner→snapshot 검사 한 단계다.
모델 에스컬레이션 없음.

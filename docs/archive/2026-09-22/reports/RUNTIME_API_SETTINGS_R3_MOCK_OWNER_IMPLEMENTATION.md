> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R3_MOCK_OWNER_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# 런타임 인증 R3e 실제 모의 owner

2026-09-15. 로컬 구현 완료, R3 전체 진행 중.
build `2026.09.15-runtime-credentials-r3-mock-owner-v1`, R0/R1/R2/R3a~d 누적.

## 실제 연결과 완료 범위

`MockCredentialOwner`가 기존 credential coordinator의 mock prepare/apply hooks를 실제로 제공한다.
vault/HMAC registry가 있는 서버에서 모의 profile을 생성하고 키 검증/적용으로 계좌 연결을 복구할 수 있다.
복수 계좌는 profile별 bundle·계좌 lock·revision을 사용하며 시장 담당 real client/broker와 공유하지 않는다.
기존 앱 UI에는 아직 입력 기능을 연결하지 않았다. 실제 NAS 소스도 동기화하지 않았다.

## 계약과 처리 순서

- prepare의 OAuth/ka00001은 broker 작업이다. ka00001 payload를 identity_from_payload로 변환해
  같은 TR을 추가로 요청하지 않는다. UUID registry 등록은 허용하되 아직 binding/token을 적용하지 않는다.
- 같은 계좌는 기존 client/broker·호출 이력·run을 유지한다. 다른 계좌 키는 ACCOUNT_CHANGED다.
  같은 계좌의 다른 active profile/main mock 시세 계좌는 ACCOUNT_PROFILE_CONFLICT다.
- 미확인 키는 공통 probe에서 직렬 검증한다. 신규 계좌는 fork_verified_candidate로 호출 이력을
  보존한 별도 lock/client를 얻는다. 토큰은 이 시점에 적용하지 않는다.
- owner의 계좌 lock은 READY부터 apply 종료/취소/만료까지 유지한다. coordinator의 release hook은
  늦게 끝난 취소 검증도 해제하며 후보 비밀을 다른 map에 영구 보관하지 않는다.
- drain은 기본 API에서 bundle을 내린 뒤 gateway/WS/monitor를 실제 종료하고 lease를 해제한다.
  broker/client는 닫지 않고 pause해 같은 계좌의 한도/세대를 유지한다. 설정 revision을 다시 확인한다.
- commit 시작 hook부터는 이전 키 복원을 하지 않는다. vault → binding/activation/설정 DB commit →
  token 활성화 → 새 fixed identity/binding bundle의 초기 읽기/lease 확인 순서로 ACTIVE를 공개한다.
- monitor OFF 설정은 monitor/WS를 시작하지 않고 확인한 OFF 설정 revision을 공개한다.
  신규 계좌는 gateway/order transport OFF다. 최초 env 이관은 기존 monitor/order OFF 설정을 보존한다.
- commit 전 실패는 기존 계좌/run/client로 연결을 재조립하며 기존 OFF를 ON으로 복원하지 않는다.
  commit 이후 실패는 bundle/lease를 닫고 복구 필요로 fence한다. 다음 키 검증 전 이전 암호화
  activation의 DB receipt를 완성해 파일 메타데이터 교체로 복구 기록을 잃지 않는다.
- 모의 prepare·계좌 read·WS token은 공통 동시 작업 2개를 사용한다. 계좌별 broker는 mock 1초 한도를 유지한다.
  bootstrap도 최대 2개이며 대기 중인 이전 revision보다 사용자 새 적용을 우선한다.
- owner startup/close·collector token·monitor 초기 read는 owned 작업이다. 종료 대기자 취소가
  실제 token thread/DB/lease 작업을 남긴 채 종료 완료로 바꾸지 않는다.
- profile 목록은 지원 여부를 profile별로 판단한다. legacy env record의 account_ref는 verified
  binding으로 보완하되 vault의 validation은 파일 revision의 기존 값을 유지한다.
- main mock 시세 profile은 nas-main-mock-default로 분리하고 계좌 bootstrap/변경에서 제외한다.
  vault 없는 기존 main mock profile 명칭은 유지한다. 기본 v1 주문 계좌는 nas-mock-default 고정이다.
- health는 실제 mock monitor 존재를, account settings GET은 실제 적용한 settings revision을 표시한다.
  초기 boot/교체 중 기본 v1 주문은 503이며 초기 조립 객체만으로 계좌 조회/주문을 시작하지 않는다.

## 검증과 구현 중 교정

fake client/WS, 임시 vault/DB와 생성한 UUID로 검증했다. 실제 키·공급자 네트워크·계좌 주문은 사용하지 않았다.
최종 로컬 회귀 310개 실행 중 309개 통과, POSIX 권한 1개는 Windows에서 생략했으며 종료 코드는 0이다.
신규/동일/두 번째 계좌, 중복 profile, 계좌 변경, 토큰 실패, READY 취소/늦은 취소 검증,
초기 읽기 동안 ACTIVE 미공개·대기자 취소, commit 후 실패/다음 키 복구, 이전 receipt 복구,
commit 전 설정 충돌과 ON/OFF 보존 복원, 복수 profile bootstrap·binding revision 유지,
시세 main mock profile 제외·env OFF 보존, 3계좌 read/WS token의 공통 2개 한도를 확인한다.
HTTPS 실제 route 테스트는 draft → prepare → READY → apply → ACTIVE → applied revision까지 실행하고
real 시세 capability/서버 생존과 legacy 주문 계좌 고정을 함께 확인한다.

API 테스트의 capability는 기존 capabilities 중첩 계약에 맞춰 읽도록 수정했다.
env 이관 bootstrap 테스트는 실제 compose 단계가 등록하는 legacy profile 원장도 구성하도록 교정했다.
가상 테스트의 공급자 호출로 우회하지 않았다. 최종 회귀 결과는 NAS_DEPLOYMENT_PENDING.md에 기록한다.

## 구조 비용과 남은 작업

owner는 bundle map·계좌 배타성·검증/적용/복구·동시 실행과 bootstrap 수명을 소유한다.
같은 이름의 전달 manager나 새 주문 lifecycle은 추가하지 않았다. 실행/수신의 기존 구체 모듈과
typed account scope/binding을 재사용한다. 새 파일은 test/report이며 구현은 기존 mock_runtime.py에 확장했다.
키 변경 이해에는 coordinator → owner → bundle → 기존 서비스가 필요하다. 늘어난 owner 경계는
계좌별 상태/수명 책임이며 일반 주문은 기존 gateway → execution runtime/lifecycle 경로를 유지한다.
DB schema v19와 기존 API 의미는 유지한다.

키 비활성화는 FAILED MOCK_DISABLE_NOT_READY로 거절하며 아직 tombstone 적용을 구현하지 않았다.
설정 PUT·scoped query/order·새 계좌 UI 선택/키 입력·news/real owner는 후속이다.
실제 NAS Linux/PostgreSQL/TLS/프로세스 중단/장중 검증은 R7 대기다.
현재 단계 모델 에스컬레이션 없음.

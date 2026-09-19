# 런타임 인증 R3f 모의 계좌 비활성화·설정 적용

작성일: 2026-09-15. 로컬 구현 완료, R3 전체 진행 중. NAS 소스 동기화/배포 전.
누적 build: `2026.09.15-runtime-credentials-r3-account-controls-v1` (R0~R3e 포함).

## 목적과 수정 경계

NAS 재시작 없이 모의 키를 비활성화하거나 계좌 조회/주문 허용 설정을 적용한다.
기존 MockCredentialOwner·bundle·broker·credential coordinator를 사용한다. 새 manager 계층이나
공개 주문 경로는 만들지 않았다. 실전 순위/시세와 다른 계좌의 transport를 공유하지 않는다.

## 키 비활성화 계약

- 기존 credential prepare API의 disable=true와 apply API를 사용한다. 저장된 검증 계좌/run을 미리보기 대상으로 삼고 새 OAuth/ka00001 조회는 하지 않는다.
- 준비는 현재 연결을 바꾸지 않는다. apply는 같은 계좌 lock으로 기존 주문·조회·WS·DB 작업을 끝낸 후 진행한다.
- durable commit은 빈 credentials와 disabled=true를 가진 vault revision이다. encrypted activation metadata에도 disabled bool을 명시한다. 이전 암호화 revision 보존 정책은 유지한다.
- DB activation receipt와 active_profile_id=None/monitor OFF/order OFF는 같은 트랜잭션이다. 기존 binding revision을 참조하며 검증 없이 새 ka00001 binding을 추가하지 않는다. 중앙 v19 유지.
- 연결의 활성 키/토큰은 drain 후 비우고, 계좌/run·과거 일지·미확정 주문은 유지한다. UNKNOWN 주문을 재전송/성공 처리하지 않는다.
- disabled operation의 ACTIVE는 비활성화 적용 완료다. 새 거래 연결이나 주문 허용을 뜻하지 않는다.
- 저장 후 실패는 RECOVERY_REQUIRED이고 이전 연결은 자동 재개하지 않는다. 명시적 다음 disable/키 교체로 복구한다.
- 재시작 시 tombstone은 env보다 우선하고 bootstrap/TR/WS를 시작하지 않는다. 이후 동일 계좌 키 재등록은 기존 UUID/run을 사용한다. 토글 OFF는 유지된다.
- 기존 disabled profile의 키 교체가 commit 전에 끝나지 못하면 disabled 상태를 복원한다. 이후 설정/다른 profile 활성화를 완료 receipt replay가 되돌리지 않는다.

## 계좌 설정 계약

`PUT /api/v1/settings/accounts/{account_ref}?environment=mock&broker=kiwoom`

요청: expected_revision(int), active_profile_id(현재 profile), monitor_enabled(bool), mock_order_enabled(bool).
응답: GET과 같은 settings와 applied_revision. profile 변경은 이 PUT에서 허용하지 않는다.
주문 ON은 monitor ON이 필요하다. 실제 mock owner가 없는 서버는 PUT 405, 실전 설정 변경은 503이다.
bearer 인증·HTTPS·16KiB 제한·중복 JSON 키/미정의 필드 거절을 기존 인증 API 경계에서 재사용한다.

실행 순서는 profile/계좌 제외 검사 → 기존 gateway/WS/monitor 실제 종료 → broker drain →
DB CAS 저장 → 같은 client/broker·binding·run의 새 bundle → 필요한 초기 read/lease → 적용 revision 공개다.
monitor OFF는 초기 read/WS를 시작하지 않는다. 키 revision과 binding revision은 늘리지 않는다.

- 낡은 revision/profile 준비 중: 409, 기존 접수는 유지한다.
- 저장 전 실패: DB가 기존 값임을 확인한 경우 이전 연결 복원 후 503 ACCOUNT_SETTINGS_APPLY_FAILED.
- 저장 또는 실행 결과가 미확인: 접수를 닫고 applied_revision=null, 503 ACCOUNT_SETTINGS_RECOVERY_REQUIRED.
- 동일 설정 재적용: 이미 적용됐으면 변경 없음. 미확인 상태면 같은 설정 revision으로 bundle 준비를 다시 수행한다.
- HTTP 대기자가 취소돼도 owner가 실제 적용 task와 account lock을 유지한다. owner 종료는 이 작업까지 기다린다.

## 검증과 완료 기준

관련 전체 회귀 323개 실행: 322개 통과, Windows에서 POSIX 권한 1개 생략, 종료 코드 0.
마지막 startup receipt 확인 보완 후 owner 전체 30개를 다시 실행해 모두 통과했다.
최종 Python 문법 9개 파일·diff 공백 검사 통과, 서버/Compose/Dockerfile build 세 곳 일치.

fake client/WS, 임시 SQLite/vault와 생성한 UUID를 사용했다. 실제 키·공급자 네트워크·계좌 주문은 사용하지 않았다.
검증 항목은 OFF→ON/주문 토글, credential/binding/run 보존, 잘못된 값·낡은 CAS·READY lock,
저장 전 실패 복원, 저장 후 읽기 실패와 동일 설정 복구, HTTP 대기자 취소 시 실제 read 완료,
disable 후 재등록·bootstrap 미실행·binding 보존·receipt replay, disable 저장 후 DB 실패와 다음 disable 복구,
기존 disabled 상태에서 commit 전 deadline 초과, 실제 HTTPS PUT/auth/중복 JSON 검사다.
PostgreSQL placeholder 경로는 로컬 회귀로 확인했고 실제 PostgreSQL 검사를 통합 스크립트에 추가했다.
실제 NAS PostgreSQL·Linux 권한·TLS/proxy·프로세스 중단 검증은 R7이다.

변경 전후 기존 계좌 실행을 이해하는 파일 경계는 그대로다. 설정 PUT은 API→owned apply→기존 bundle/DB,
disable은 기존 coordinator→owner→vault/DB 순서다. 단순 전달 wrapper나 새 코드 파일은 추가하지 않았다.
동시 호출 점검에서 disable 준비의 receipt 읽기 전 profile 제외를 확보하도록 보완했다.
새 키의 파일 commit만 완료된 상태에서는 설정 PUT으로 구형 client를 재개하지 못하도록,
vault credentials와 실제 context의 일치를 확인한다. 두 경계는 지연·저장 실패를 주입해 회귀로 확인한다.
disabled startup도 DB receipt와 OFF 설정을 확인한 뒤 완료 revision을 공개하며,
파일 commit 뒤 DB가 실패한 상태를 정상 비활성화 적용으로 표시하지 않는다.
API/구조/DB/모듈/변경 이력을 갱신했다. 모델 에스컬레이션 없음.

## 다음 한 단계

R3g: 선택한 account/profile에 고정된 scoped 계좌 조회·모의 주문 API를 연결한다.
기존 v1 기본 주문 계좌 nas-mock-default는 유지한다. 입력/선택 UI는 R4, news/real owner는 R5/R6,
NAS 누적 동기화·재빌드와 실제 운용 검증은 R7이다. 현재 사용자용 새 토글/키 입력 UI는 아직 없다.

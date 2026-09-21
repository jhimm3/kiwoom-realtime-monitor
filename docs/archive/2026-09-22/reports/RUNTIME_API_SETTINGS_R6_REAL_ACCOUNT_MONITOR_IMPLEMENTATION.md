> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_MONITOR_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6b3c2a — 자동 실전 REST 계좌 수집/저장/운영 설정

구현일: 2026-09-16. 로컬 누적 build: `2026.09.16-runtime-credentials-r6-real-account-monitor-v1`.
NAS 소스 동기화/빌드/배포 전이다. 실제 계좌/키/사용자 DB/NAS/PostgreSQL을 사용하거나 변경하지 않았다.

## 구현 완료

- 기존 RealCredentialOwner/context에 계좌별 30초 REST 자동 수집 task와 설정 ON/OFF를 연결했다.
- 첫 자동 계좌 조회도 30초 뒤 실행하여 부팅의 첫 순위 요청을 앞에 둔다. 수동 조회가 진행 중인 계좌는 자동 회차를 건너뛴다.
- 기존 read_account/reader/broker를 사용한다. 시세 담당은 중앙 broker, 비담당은 기존 계좌 broker/동시 실행 2개를 유지한다.
- 실전 정규화 결과를 내부 real_account_recovery 문서로 저장한다. 기존 mock 주문/계좌 snapshot/lease/reconcile은 변경하지 않는다.
- 기존 계좌 HTTPS PUT을 real/mock owner로 분배한다. real 주문 토글 true나 profile 변경/해제는 이 API로 허용하지 않는다.
- OFF/ON은 계좌 자동 조회/저장 정책만 바꾸고 시장 collector/순위·물리 client/broker/역사 조회 manager를 재시작하지 않는다.
- 키/시세 담당 변경·OFF·종료는 실제 전체 조회와 thread 저장이 끝날 때까지 기다린다. HTTP 대기 취소도 소유 작업을 취소하지 않는다.
- 역할 전환은 양쪽 계좌 monitor를 멈추고 기존 broker routing을 바꾼 뒤 계좌마다 한 task를 다시 시작한다.
- key binding 변경은 이전 성공/오류 상태를 초기화한다. 저장 전 취소 복구가 monitor 재시작에 실패하면 계좌 admission도 해제한다.

## 입출력/저장 계약

계좌 GET/PUT은 기존 settings/applied_revision에 real monitor_status를 추가한다.
mode=rest_poll이며 state는 waiting/collecting/off/paused/unavailable이다.
poll_interval_seconds, 마지막 실제 저장 성공의 last_success_at, 비밀 없는 error_code를 제공한다.
applied_revision은 REST 수집 정책의 적용이며 자료 수신 성공이나 WebSocket/REG 승인을 뜻하지 않는다.

DB는 기존 central_documents를 사용한다. collection=real_account_recovery,
owner=kiwoom:real:{account_ref}, key=정규 전체 JSON의 SHA256이다.
같은 입력의 저장 재시도는 1건이며 새 관측은 이전 관측을 덮지 않고 append한다.
scope/profile/binding_revision/settings_revision/source/received_at/recovery(account/orders)를 보존한다.
실시간 틱·원자적 동시 계좌 snapshot·최종 비용/매매일지 체결 원장으로 해석하지 않는다.
원계좌번호/키/토큰을 복사하지 않는다. 일반 content GET/POST 허용 목록에 넣지 않았다.

검증된 registry, 현재 active profile/최신 binding, monitor ON과 설정 revision이 일치해야 삽입한다.
계좌/주문 참조가 scope와 다르거나 timezone 없는 received_at/미래 관측은 거절한다.
SQLite BEGIN IMMEDIATE와 PostgreSQL credential-activation advisory lock 안에서 검증/삽입을 함께 수행한다.
새 SQL schema/migration은 없으며 v19를 유지한다.

설정 저장 전 검증/CAS 실패는 이전 정책을 복구한다. 결과 불명확/저장 후 적용 실패/복구 실패는
해당 계좌 수집을 paused/applied_revision=null로 두고 ACCOUNT_SETTINGS_RECOVERY_REQUIRED를 반환한다.
이 수집 실패 때문에 시장 collector/역사 조회를 차단하지 않는다. 원시 예외/비밀은 반환하지 않는다.
정상 키/저장 상태 확인 후 서버 재시작으로 복구한다. 별도 실행 중 설정 복구 API는 후속이다.

## 변경 파일/책임

- real_runtime.py: 자동 task/운영 설정 독점/실제 저장 drain/키·역할 재시작/상태.
- database.py: 공유 저장 helper 및 SQLite/PostgreSQL 트랜잭션.
- credential_runtime.py, app.py: 기존 HTTPS/제한 JSON/인증 경계와 환경별 PUT·GET 상태.
- check_postgres_integration.py: 임시 계좌의 저장 멱등/정책 fence 검사 후 rollback.
- test_real_account_monitor.py: 14개 신규 회귀. test_real_credential_owner.py: REST 적용/성공 관측 구분.
- API_CONTRACT.md/DB_SCHEMA.md/현재 구조·모듈 지도·설계 리뷰/배포 원장/변경 기록: 위 계약과 잔여 WS 범위.

새 manager/wrapper/interface를 만들지 않고 기존 owner/context의 수명·정책 경계에서 구현했다.
기존 reader/broker 호출 경로를 재사용하고 DB 검증 helper는 저장 트랜잭션 경계에만 둔다.

## 검증

- 인접 회귀 219개 실행: 218 통과, Windows POSIX 권한 검사 1 skip, 실패 0, exit=0.
- 로그: tmp/r6b3c2-regression-final.log. 이 실행 뒤 monitor 복구 실패 경계/검증 스크립트 회귀를 추가하여 최종 소스 회귀를 별도 실행했다.
- 최종 소스 회귀 84개 모두 통과, 실패 0, exit=0. 로그: tmp/r6b3c2-final-source.log.
- 신규 14개 회귀는 자동 두 계좌/환경 저장 분리, OFF/ON/noop, 잘못된 토글/낡은 설정,
  저장 scope/binding/시각/정책 fence, 멱등/방언, 취소 뒤 완료, 저장 전 복구/불명확 저장,
  미완성 회차 후 회복, 역할/키 변경, HTTPS 환경 분배, 실제 저장 drain, 복구 실패 admission 해제,
  PostgreSQL 검증 스크립트 rollback을 확인한다.
- AST 7개/누적 build 3곳/소스 공백과 tracked diff --check 통과.
- PostgreSQL placeholder 공유 helper와 rollback 검사 스크립트를 임시 SQLite adapter로 검증한다. 실제 PostgreSQL 실행은 R7까지 보류한다.
- 초기 실패는 테스트가 raw server_now의 timezone을 변환하지 않은 입력, 이전 단계 applied=null 기대,
  HTTPS_REQUIRED의 기존 426을 403으로 기대한 assertion, 다른 계좌 a1→b2를 키 교체로 사용한 테스트,
  잘못된 test_rest_broker 모듈명 때문이었다. 운영 계약을 우회하지 않고 테스트 입력/기대를 수정했다.

## 완료 기준/남은 작업

자동 계좌 수집/실전 저장/계좌별 REST ON-OFF와 키·역할·취소/실패 경계를 로컬 구현했다.
기존 실시간 순위 우선 큐·실전/모의 한도·mock 원장·계좌 scope 계약을 유지한다.

다음 R6b3c2b: 시세 담당의 동일 시장 WS 계좌 event 분배와 비담당 계좌 WS/REST 복구 연계.
현재 30초 REST 모드를 즉시 체결/잔고 수신 완료로 표시하지 않는다. PC 입력/상태(R6c),
누적 NAS 동기화/빌드/실환경(R7), 실제 주문 운용 검증은 남았다. 지금 재빌드를 요청하지 않는다.

모델 에스컬레이션: 없음. 실제 모델을 변경했다고 주장하지 않는다.

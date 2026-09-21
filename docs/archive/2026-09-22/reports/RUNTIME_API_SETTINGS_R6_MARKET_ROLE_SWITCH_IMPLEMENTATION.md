> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_MARKET_ROLE_SWITCH_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6b3b2 — 실행 중 시세 담당 변경

2026-09-15~16. 서버 내부 담당 전환/HTTPS API/재기동 복원 로컬 구현 완료. NAS 배포/실환경은 미실행이다.
build: `2026.09.15-runtime-credentials-r6-market-role-switch-v1`.

## 목적과 최소 변경

기존 실전 owner·중앙 broker·단일 시장 collector·계좌 query manager 안에서 역할 전환을 연결했다.
별도 market manager/새 저장소/새 WS/계층은 만들지 않았다. 핵심 파일은 real_runtime.py,
rest_broker.py, account_query.py, app.py다. 단순 전달 계층 없이 owner가 전환 수명/정책을 소유한다.
기존 DB 역할 CAS/v19를 유지하며 credential binding/run·계좌 설정 문서를 변경하지 않는다.

## 입출력과 실행 계약

인증된 PUT /api/v1/settings/market-profile은 market_profile_id, expected_revision,
expected_binding_revision 세 필드만 받는다. bool을 revision으로 받지 않는다.
성공 응답은 settings와 applied_revision이며 GET도 같은 형태다.
applied_revision은 REST/계좌 routing·collector 재연결 시작이 적용된 역할 버전이다.
모든 REG 승인/실시간 체결 연속 수신 준비는 기존 upstream ready 계약으로 따로 확인한다.

현재/대상 실전 context의 검증된 vault 적용 revision/계좌 신원을 기존 독점 구간에서 확인한다.
같은 담당이면 저장/연결을 다시 하지 않는다. 다른 담당은 다음을 순서대로 수행한다.

1. 두 계좌 query의 신규 작업을 막고 실제 진행 작업을 drain한다.
2. 기존 시장 collector의 token/socket/저장/장 경계 작업을 drain한다.
3. 두 broker의 큐와 실제 client 요청을 drain한다.
4. 역할/계좌 binding/활성 profile/적용된 vault/context snapshot을 다시 검증하고 DB 역할 CAS를 저장한다.
5. 기존 중앙 broker와 대상 계좌 broker의 물리 client를 교환한다. client의 토큰/잠금/호출 이력을 복제하거나 초기화하지 않는다.
6. 각 query manager는 drain된 상태에서 broker/조회 limiter만 바꾸며 계좌 binding/cursor를 유지한다.
7. broker/query를 다시 열고 현재 담당 계좌 신원을 적용한 뒤 기존 collector를 재연결한다.

기존 시장 broker 큐·순위 최우선·store/ingestor·hub/집계 객체는 유지한다.
이전 담당은 비담당 계좌 broker로 조회하며 공통 동시 실행 2개 limiter를 사용한다.
새 담당 계좌 query는 같은 중앙 broker를 사용해 그 계좌의 한도를 중복 requester로 나누지 않는다.
broker cache generation은 client 교환/이후 key 활성화에도 증가하고 cache를 비운다.
현재 담당과 불변 nas-real-default/v2 암묵적 계좌 대상을 구분한다.
collector token/시각/실시간 raw 계좌 resolver는 새 담당을 사용하며 기본계좌 신원을 대신 쓰지 않는다.
키 활성화/중단 후 재개도 첫 account frame 전에 올바른 context를 공개하고 재개 실패는 이를 제거한다.

## 실패·취소·종료·복원

저장 전 snapshot/CAS 실패는 이전 broker/query/collector를 복구하고 cursor를 유지한다.
알 수 없는 저장 결과는 보수적으로 저장 후 실패로 처리한다. 저장 후 실패나 이전 실행 복구 실패는
두 계좌 admission/applied credential revision을 제거하고 query/broker/collector를 다시 차단한다.
applied role revision은 null이며 MARKET_ROLE_RECOVERY_REQUIRED를 반환한다.
저장 문서를 이전 역할로 덮지 않고 old key를 임의로 다시 열지 않는다. 다른 계좌의 조회는 별도 context에 남는다.
복구 중 새 실전 credential 준비는 거절한다. 별도 live 역할 복구 API는 이번 범위 밖이며,
정상 키/저장 상태 확인 후 서버 재시작으로 저장된 담당을 복원한다.

HTTP 대기 취소는 owner가 소유한 전환 task를 취소하지 않는다. 종료가 이 task와 예약 구간이 끝나기를 기다린다.
재기동은 저장된 market_profile_id를 기존 market broker에 배치하며 legacy 계좌는 독립 조회로 복원한다.
선택된 담당 복원 실패 시 다른 계좌를 시세 담당으로 자동 대체하지 않는다.
오류 응답에는 안전한 코드만 사용하며 상류 예외 문구를 그대로 표시하지 않는다.

## 검증

- 최초 관련 회귀 59개 통과·exit=0, 36.795초: tmp/r6b3b2-target.log.
- 중간 인접 회귀 304개 실행, 303 통과·Windows POSIX 권한 1 skip, 실패 0·exit=0: tmp/r6b3b2-regression-final.log.
- 첫 frame 신원/복구 실패 fencing을 포함한 인접 회귀 306개 실행, 305 통과·같은 권한 1 skip, 실패 0·exit=0, 134.587초: tmp/r6b3b2-regression-complete.log.
- 이후 인증 precommit 중단 후 첫 frame의 이전 계좌 복원을 보완한 최종 관련 회귀 64개(신규 전환 테스트 12개 포함) 통과·exit=0, 41.058초: tmp/r6b3b2-target-final.log. 전체 인접 회귀와 이 마지막 보완의 검증 범위를 구분한다.
- 테스트는 두 계좌 client/잠금/기본계좌/cursor 보존, 동일 역할 noop/반복 교환 generation, 물리 요청 drain, CAS 복구, 저장 후/불명확/복구 실패 차단, HTTP 취소, 재기동, 최초/중단 후 계좌 frame을 검증한다.
- 기존 HTTPS 실전 owner 통합 테스트는 인증 없는 PUT 거절, 역할 변경/GET applied revision, stale revision 409, 현재 담당 raw 계좌 식별과 v2 기본계좌 유지도 확인한다.
- 변경 Python 8개 구문/공백/build 세 곳 일치 확인 통과·exit=0: tmp/r6b3b2_verify.py 및 git diff --check.
- 임시 SQLite/vault·가짜 API·mocked WS start로 검증했다. 실제 PostgreSQL/키움 API/WS·사용자 키/계좌/주문·앱/NAS에는 접근하거나 변경하지 않았다.

## 남은 범위

R6b3c 실전계좌 잔고/체결/미체결 수집·운영 PUT, R6c PC 역할 입력/계획된 재연결·실제 gap/REG 준비 표시,
R7 PostgreSQL 동시 실행·실제 TR/WS·누적 소스 동기화/배포/장중 검증이 남았다.
R7까지 NAS 중간 재빌드를 요청하지 않는다. 이번 단계는 실전 자동주문/실환경 운용 완료를 뜻하지 않는다.
모델 에스컬레이션 없음.

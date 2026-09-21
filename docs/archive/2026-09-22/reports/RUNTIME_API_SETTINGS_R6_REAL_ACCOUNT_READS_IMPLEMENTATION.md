> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_READS_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6b3c1 — 실전 read-only 계좌 복구 조회

2026-09-16. 안전한 실전 조회 기반 로컬 구현 완료. 자동 계좌 수집/저장/실시간 WS/운영 PUT 완료는 아니다.
build: `2026.09.16-runtime-credentials-r6-real-account-reads-v1`.
NAS 소스 동기화/배포 전이며 R7까지 중간 재빌드를 요청하지 않는다.

## 구현과 책임

기존 mock_account.py의 파서/연속조회는 private _KiwoomAccountReader에서 공유하고
KiwoomRealAccountReader/KiwoomMockAccountReader가 credential 환경과 조회 거래소를 분리한다.
AccountRecovery의 필드는 기존 account/orders 그대로이며 MockAccountRecovery import는 alias로 보존한다.
mock reader의 KRX 요청과 mock 환경 guard를 유지한다. 주문 실행/runtime/원장은 여전히 mock 전용이다.
이름이 mock_account.py인 기존 파일에 공유 파서가 이미 모여 있어 새 wrapper 파일이나 manager는 만들지 않았다.

RealCredentialOwner.read_account(profile_id, expected_binding_revision=...)는 admitted real context와
현재 binding revision을 확인하고 계좌당 단일 소유 task를 만든다. 결과는 해당 account_ref의
정규화된 계좌/주문 view이며 원계좌번호/토큰/원문 payload를 결과에 복사하지 않는다.
시세 담당은 기존 중앙 broker, 비담당은 기존 계좌 broker와 공통 동시 실행 2개 limiter를 사용한다.
추가 client/물리 요청 잠금/TR 한도/WS 연결을 만들지 않는다.
새 복구 TR 네 개는 내부 broker에서 허용하고 일반 v1 kiwoom/query는 ACCOUNT_QUERY_SCOPE_REQUIRED로 차단한다.
공개 v3 복구 조회/계좌 운영 PUT·실시간 적용 capability는 이번 단계에 추가하지 않았다.

계좌 read task와 paused 상태는 기존 context에 소속된다. 키 변경/담당 전환/종료는
계속되는 연속조회와 실제 요청 전체를 먼저 drain한 뒤 broker/context를 변경한다.
호출자 취소는 실제 task를 버리지 않는다. read 실패는 slot을 해제하며 검증된 계좌를 자동 비활성화하지 않는다.
전환/복구 실패는 read 신규 작업도 차단한다. 과거 kt00007/kt00015 cursor 계약은 유지한다.

## 요청·데이터 계약

실전 요청은 통합 ka10075/ka10076, KRX와 NXT의 kt00018, kt00001이다.
연속조회 없는 경우 다섯 TR이며 필요한 API만 최대 10페이지 연속조회한다.
통합 주문은 stex_tp=0, 잔고는 dmst_stex_tp=KRX/NXT를 각각 사용한다.
요청 조건은 [키움 미체결 공식 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/계좌/get_domestic_unfilled_orders.py),
[체결 공식 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/계좌/get_domestic_filled_orders.py),
[잔고 공식 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/계좌/get_domestic_account_evaluation_balance.py)를 확인했다.

같은 종목의 같은 보유 수량은 한 번만 사용하며 두 venue 수량을 더하지 않는다.
서로 다른 수량은 충돌로 실패한다. 주문별 누적 체결은 기존 max 병합 규칙을 사용하며
실제 execution ID 없는 누적 체결에 가짜 ID를 만들지 않는다.
같은 페이지 종류에서 반복된 주문번호, 알려진 서로 다른 venue의 같은 주문번호,
반복된 연속 cursor, 계좌 테이블 누락/null, 잘못된 수량은 실패로 닫고 부분 view를 반환하지 않는다.
정상 빈 list는 빈 자료다. 여러 TR은 원자적 동시 관측이 아니며 account.as_of는 마지막 예수금 조회 시각,
각 order.as_of는 해당 주문 조회 시각이다. 거래 중 수량이 변해 두 잔고가 충돌하면 성공으로 단정하지 않는다.
실제 real 표본의 빈 table 형태/거래소별 잔고 수량 의미/전체일 체결 범위는 R7에서 확인한다.

## 수정 범위와 구조 비용

mock_account.py, real_runtime.py, rest_broker.py, app.py와 새 테스트 두 개를 수정했다.
owner→reader→기존 paging/broker 경로이며 별도 위임 계층/전역 상태를 추가하지 않았다.
기존 mock paging/파서 호출 깊이는 유지하고 양쪽 reader가 하나의 구현을 공유한다.
새 DB 테이블/열/마이그레이션/저장 API는 없다. 중앙 v19를 유지한다.
실전 결과는 현재 실행 메모리뿐이며 mock 주문 snapshot 원장으로 우회 저장하지 않는다.

## 검증

- 첫 관련 회귀 48개는 fake client fixture의 후보 복사 타입 오류로 2개 실패했다. production 경로를 우회하지 않고 fixture 전체가 같은 fake client 타입을 사용하도록 보완했다.
- 보완 후 관련 48개 통과·exit=0, 29.206초: tmp/r6b3c1-target-final.log.
- 인접 회귀 342개 실행, 341 통과·Windows POSIX 권한 검사 1 skip, 실패 0·exit=0, 150.791초: tmp/r6b3c1-regression-final.log.
- 이후 계좌 table 누락을 빈 계좌로 오인하지 않는 최종 검사를 추가했다. 최종 관련 회귀 51개(신규 real reader/read 수명 15개 포함) 통과·exit=0, 39.547초: tmp/r6b3c1-final-source.log.
- 전체 인접 회귀와 마지막 table 검사 보완의 최종 검증 범위를 구분한다.
- 테스트는 real/mock 환경·요청 조건·연속조회/중복/venue 충돌/누락/원계좌번호 제외, 계좌별 서로 다른 보유량과 실제 broker routing, 취소/키 변경/담당 전환/종료 drain, 단일 read admission/실패 재조회, 무신원 v1 차단을 검증한다.
- 모의 reader/monitor/bundle와 실전 owner/role/account 설정/인증/vault/선택 계좌/REST/실시간/서버/조건검색/DB를 함께 검사했다.
- Python 6개 구문/변경 공백/build 세 곳 일치 확인 통과·exit=0: tmp/r6b3c1_verify.py 및 git diff --check.
- 임시 SQLite/vault·가짜 API로 검사했다. 실제 계좌/키/주문/API/WS·사용자 DB·앱/NAS/PostgreSQL은 사용·변경하지 않았다.

## 다음 단계

R6b3c2에서 자동 수집/실전 환경 저장·시장 WS 계좌 event 분배/비담당 계좌 WS·운영 PUT을 연결한다.
현재 실전 monitor applied_revision은 계속 null이다. read 성공을 자동 실시간 수집 적용 완료로 표시하지 않는다.
실전 저장은 mock 실행 원장 guard를 깨지 않고 별도 실전 수집 경계를 사용해야 한다.
R6c PC 입력/계획된 재연결·gap/REG 준비와 R7 PostgreSQL/실제 API·WS·누적 배포도 남았다.
모델 에스컬레이션 없음.

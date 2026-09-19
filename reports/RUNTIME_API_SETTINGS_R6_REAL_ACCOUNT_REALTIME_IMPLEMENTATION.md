# R6b3c2b — 실전 계좌 WebSocket 분배/REST 복구 연계

구현일: 2026-09-16. 누적 build: `2026.09.16-runtime-credentials-r6-real-account-realtime-v1`.
로컬 구현이며 NAS 동기화/빌드/배포 전이다. 실제 키/API/사용자 DB/WS/PostgreSQL을 사용·변경하지 않았다.

## 구현 완료

- 시세 담당 계좌는 기존 CentralRealtimeCollector의 00/04를 RealCredentialOwner로 분배한다. 두 번째 시장/계좌 연결을 열지 않는다.
- 비담당 계좌는 자신의 real token/익명 scope 전용 00/04 WS를 소유하며 시장 종목을 구독하지 않는다.
- 기존 mock 계좌 collector의 socket 수명을 private base로 공유했다. mock 구체 클래스의 생성 계약/URL/시간은 유지한다.
- real은 LOGIN/REG 명시 성공 뒤 REAL을 전달하고 PING은 그대로 응답한다. 평일 08:00~20:00 관측 정책이다.
- raw 9201 HMAC/current binding/scope를 확인한다. 신원 누락/다른 계좌/예전 monitor generation callback은 이벤트·재조회를 발생시키지 않는다.
- 00 접수/취소/체결·04 변경·연결 상태 신호는 0.5초 병합 후 기존 계좌 read_account를 깨운다. 30초 REST backup과 단일 계좌 read guard를 유지한다.
- 비담당 token 조회도 기존 물리 client/잠금과 동시 실행 2개 제한을 쓴다. 새로운 REST 한도/큐/실전 주문 경로는 없다.
- 비담당 이벤트도 기존 hub에 계좌 scope로 전달한다. 매수 종목은 기존 NAS daily 시장자료 수집 범위에 합친다. 담당 이벤트는 중복 발행하지 않는다.
- 별도 계좌 event writer가 정규화 00/04를 저장하므로 시장 수신 callback과 REST 조회가 DB 저장을 기다리지 않는다.
- OFF/키/역할/종료는 전용 socket/실제 token 작업과 진행 중 이벤트 저장까지 drain한다.
- 재연결 재개 전에 계좌 monitor generation/수집기를 준비하여 처음 수신한 계좌 이벤트도 저장한다.

## 계약/실패 처리

기존 monitor_status.mode=rest_poll/applied_revision은 REST 정책 의미를 유지한다.
realtime에 source(shared_market/account_only), state(ready/waiting/off/paused), last_event_at,
안전한 error_code와 dropped_events를 추가했다. ready는 실제 승인, last_event_at은 DB 저장 성공이다.
mock/real 계좌·실전 자동주문 허용 여부를 이 상태로 추론하지 않는다.

real_account_event는 central_documents 내부 collection이다. owner=kiwoom:real:{account_ref},
key=정규 전체 JSON SHA256이며 scope/profile/binding/settings revision/source=kiwoom_websocket/
event_type/received_at/event를 append한다. REST와 같은 verified 계좌 저장 fence를 두 DB에서 공유한다.
일반 content API에 공개하지 않고 기존 mock 주문 원장/lease/reconcile은 수정하지 않는다. schema v19 유지.
같은 입력 저장 재시도는 1건이다. 다른 수신시각의 재전달은 별도 관측일 수 있으며 유일 체결 원장은 아니다.
체결 trade_time과 서버 수신 received_at을 분리하며 없는 잔고 체결시각을 생성하지 않는다.

DB 저장 실패는 pending event를 메모리에 보존해 30초 간격으로 재시도한다.
살아 있는 키/역할/운영 설정 변경은 미저장 이벤트가 있으면 commit하지 않고 이전 정책 복구를 시도한다.
실패 후 fencing은 pending이 있어도 안전하게 실행할 수 있으며 임의 이전 DB 설정으로 덮어쓰지 않는다.
실제 socket 종료 실패는 무시하지 않는다. 해당 context에 실패 fence를 유지하여 새 연결을 만들거나
설정/키/역할 commit을 완료했다고 주장하지 않는다. 정상 키/종료 상태 확인 후 서버 재시작으로 복구한다.
1000개 event queue overflow는 수량/REAL_ACCOUNT_EVENT_QUEUE_FULL을 공개하고 REST 복구를 요청한다.
강제 종료나 DB가 계속 실패한 상태의 프로세스 종료에는 메모리 pending 영속성을 보장하지 않는다.
이벤트 수신/저장 실패를 성공으로 숨기거나 전체 NAS/시장 장애로 승격하지 않는다.

## 변경 파일

- realtime_collector.py: parsed callback/공유 account publish·기존 hub와 실제 매수 편입.
- mock_account_monitor.py: private socket base와 구체 real/mock 정책.
- real_runtime.py: 전용 WS/wake/별도 writer/pending/overflow/세대/drain/재개 순서/상태.
- database.py: 공유 계좌 write fence 및 SQLite/PostgreSQL event append.
- app.py: 이벤트 publisher/handler 연결·누적 build.
- check_postgres_integration.py: REST/event 멱등·낡은 정책 거절·rollback 검사 6개.
- test_real_account_realtime.py: 신규 17개 WS/owner 회귀. 기존 real fixture는 가짜 전용 WS를 주입해 실제 접속을 막는다.
- API/DB/현재 구조·모듈 지도·설계 리뷰·변경 기록·배포 원장: 현재 계약과 R6c/R7 잔여 범위.

새 manager/service/wrapper/interface를 추가하지 않았다. 기존 실제 socket 수명과 계좌 저장 fence만 공유한다.
market 0B/0w 등록·순위 우선 큐·실전/모의 한도·화면 선택 계좌 계약을 유지한다.

## 검증

- 첫 관련 회귀 84개 모두 통과, exit=0: tmp/r6b3c2b-target.log.
- 인접 회귀 260개 모두 통과, exit=0: tmp/r6b3c2b-regression.log.
- 최종 source 회귀를 별도 실행해 연결 상태 신호/REAL 이외 프레임 guard도 확인한다: tmp/r6b3c2b-final-source.log.
- 위 최종 source 회귀 82개 모두 통과·exit=0. 이후 socket 종료 실패 fence/회귀를 추가해 최종 drain 회귀를 실행했다: tmp/r6b3c2b-final-drain.log.
- 최종 drain/계좌/키/역할/모의/중앙 collector 회귀 95개 모두 통과·exit=0. 최종 소스 기준이다.
- tracked diff --check와 신규/미추적 소스·보고서 공백 검사도 통과했다.
- AST 9개/누적 build 3곳/소스 공백 검증 통과.
- 신규 17개는 전용 구독/시간/필수 resolver, 잘못된 계좌/null/다른 프레임,
  LOGIN/REG/PING/승인 전 REAL·실패 승인, 실제 token cancellation drain, 담당 분배,
  전용 scope/이전 callback/OFF, 20개 burst 저장과 한 REST 회차, pending 실패/복구,
  역할별 socket 전환, 저장 fence/멱등, 실제 저장 뒤 취소 OFF 완료,
  재개 첫 이벤트, 잔고 변경, 한 번의 hub/daily 매수 편입, queue overflow/수용 이벤트 drain,
  socket 종료 실패 때 설정 commit/새 연결 차단을 검증한다.
- PostgreSQL 저장 helper/rollback 검사는 임시 SQLite adapter로 확인한다. 실제 PostgreSQL은 R7까지 보류한다.
- real URL/LOGIN·성공 코드/PING 동작은 [키움 공식 WebSocket 안내](https://openapi.kiwoom.com/m/guide/index?dummyVal=0)와 대조했다.

## 남은 작업

다음 R6c: PC 실전 입력/계획된 재연결 상태·실제 공백 측정/승인 상태/기존 정상 화면 보존 연계.
R7: 누적 NAS 소스 동기화 후 이미지 빌드·실제 복수 토큰/00/04 승인·수신/PC/DB 확인.
동시 인증 세션의 실제 허용·운용/거래 시간·휴장일은 fake 테스트로 확정하지 않는다.
실제 자동주문 운용/영속 pending outbox 구현은 이번 단계 완료 범위가 아니다. 지금 재빌드를 요청하지 않는다.

모델 에스컬레이션: 없음. 실제 모델 변경을 주장하지 않는다.

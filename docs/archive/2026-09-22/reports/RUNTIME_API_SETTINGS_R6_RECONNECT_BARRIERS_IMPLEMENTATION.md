> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_RECONNECT_BARRIERS_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6a — 실전 재연결 수명과 저장/계좌 조회 경계

2026-09-15. 로컬 구현 완료, NAS 동기화/배포 전.
누적 build `2026.09.15-runtime-credentials-r6-reconnect-barriers-v1`.
R6 전체 완료나 실제 실전 키 교체 UI/API 제공을 뜻하지 않는다.

## 이번 범위

실전 키 교체에 필요한 pause/drain/resume를 기존 실시간 collector와 계좌 query manager에 추가했다.
기존 REST broker의 prepare/priority/drain/client generation 경계는 그대로 재사용할 수 있다.
실제 인증 owner/계좌 binding·활성 receipt/PC 입력·planned reconnect 표시는 R6b/R6c에 연결한다.
기존 공개 API/키움 TR/실전 지원 capability/SQL 테이블은 추가하지 않았다.

## 확인된 원인과 최소 수정

- 기존 snapshot 저장은 to_thread를 직접 기다렸다. 대기자 취소 뒤 실제 thread가 계속 쓰는데도
  close는 저장 완료를 기다리지 않고 끝나는 상태를 가짜 저장기로 재현했다 (`tmp/r6a-before.log`).
- 수신 task가 장 마감 처리 중 취소되면 접수 장 마감 coroutine도 중단되고 pause가 완료되는
  상태를 가짜 서비스로 재현했다 (`tmp/r6a-boundary-before.log`).
- queue/source 문맥을 바꾸는 대신 동일 collector에서 접수 저장/장 마감 task를 소유하고
  shield로 완료를 기다렸다. DB 저장은 단일 lock에서 직렬화한다. close의 최종 저장은 이미
  진행 중인 cycle 뒤에 실행해 그동안 도착한 최신 대기분도 저장한다. close 자체도 owned task다.

독립 저장·socket·token·장 마감 수명은 기존 collector 안의 private task/lock 상태로 유지한다.
새 Manager/Service/범용 owner를 만들지 않았다. 한 흐름을 이해하는 파일 수는 증가하지 않는다.
owned IO 경계로 저장 호출 깊이는 2단계(접수 수명/직렬 lock), 장 마감은 1단계 늘었다.
각각 실제 수명·동시성 정책을 수행하며 전달만 하는 외부 계층은 추가하지 않았다.

## 입출력/보존 계약

`CentralRealtimeCollector.begin_credential_change()`는 신규 수신 재개를 막고 generation을
증가시키며 허브의 upstream ready를 해제한다. 실제 기존 socket 종료/token thread와 접수
장 마감 작업을 기다리고 기존 저장 cycle/최종 대기 자료를 처리한다. 취소된 caller 뒤에도 계속한다.
열린 분의 수신 공백을 표시하고 기존 hub/구독/aggregator/실패 저장 대기분은 보존한다.
DB 저장 실패는 기존 retry 자료를 유지하고 인증 오류로 승격하지 않는다.

`end_credential_change()`는 drain 완료 뒤 동일 collector의 수신 loop를 재개한다.
취소된 caller도 실제 재개를 취소하지 않고 이전 resume receipt를 다음 회전에 재사용하지 않는다.
내부 credential_connection_status는 generation/phase/paused/shutdown을 제공하며 공개 HTTP는 아니다.
shutdown과 미완료 drain에서 재개는 거절한다. START 뒤 실제 준비 완료는 전체 REG 승인 때다.
계획된 pause는 connection_failed/비정상 단절을 만들지 않으며 실제 통신 실패는 기존 경로로 처리한다.

receive는 토큰/로그인/프레임 후 generation을 확인해 이전 연결의 늦은 REG/REAL을 적용하지 않는다.
새 연결의 첫 전체 REG 승인 때 같은 source도 누적 baseline/연속 관측 시작을 재설정한다.
공백 중 늘어난 누적 거래량을 재연결 첫 틱에 한꺼번에 더하지 않고 실제 관측 틱을 저장한다.
공백 자체의 거래 자료 손실이 없어지는 것은 아니며 불완전 관측을 완료 자료로 취급하지 않는다.

`AccountQuerySessionManager.begin_credential_change()`는 신규 query를 ACCOUNT_QUERY_BUSY로
거절하고 접수한 owned query를 완료까지 기다린다. `end_credential_change(invalidate_cursors=True)`는
새 binding/key 적용 뒤 이전 batch/next_key를 폐기한다. False는 commit 전 취소의 기존 cursor를
보존한다. 기존 scope 재검증은 유지하며 종료 뒤 begin/resume는 ACCOUNT_QUERY_CLOSED다.

## 검증

- 신규 11개: 저장 waiter 취소/실제 close 완료, 오래된 저장 뒤 최신 자료 저장,
  drain waiter 취소/실제 socket 종료, resume waiter 취소/다음 회전 receipt,
  실제 token thread 완료/새 연결 차단, 이전 연결 늦은 frame/단일 연결/계획 단절,
  같은 source 최초 REG의 초·분 누적 기준점, DB 실패 retry 보존,
  접수 장 마감 완료, 계좌 BUSY/접수 drain/commit cursor 폐기,
  commit 전 기존 cursor 재개/shutdown 거절.
- 초기 실시간/계좌/조건검색 관련 44개 통과. 최종 관련 회귀 235개 모두 통과, 종료 코드 0
  (`tmp/r6a-regression.log`). REST 순위 우선순위·credential barrier·모의 bundle/owner·선택 계좌
  API·초/분봉·서버 config/app/DB·조건검색을 포함한다.
- Python 4개 구문/공백 정상, tracked diff 공백 정상, 누적 build 3곳 일치.
- 가짜 socket/token/저장기·임시 SQLite·offscreen Qt만 사용했다.
  실제 NAS/PostgreSQL/키움 API/사용자 DB/인증키는 변경하지 않았다.

## 다음

R6b는 실전 owner의 prepare → 계좌 query drain → 실시간 drain → 동일 broker/client drain →
vault/binding commit → 활성 키/계좌 context 전달 → REST/query/실시간 재개를 연결한다.
commit 전 취소와 이후 복구 필요를 구분하고 실전/모의 client와 한도를 공유하지 않는다.
복수 실전 계좌와 main 공통 시세 역할, keyless 설치 조립/명시 disable도 기존 설계에 따라 구현한다.
R6c는 PC 입력과 planned reconnect/REG 확인/deadline 뒤 실제 장애 판정·공백 측정이다.
R7 누적 동기화/배포/실환경은 남았다. 중간 NAS 배포/재빌드 없음. 모델 에스컬레이션 없음.

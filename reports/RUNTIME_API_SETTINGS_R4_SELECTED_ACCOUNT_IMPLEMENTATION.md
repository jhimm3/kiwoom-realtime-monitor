# R4b 매매일지 선택 계좌와 PC client 연결 결과

2026-09-15. 로컬 구현 완료, NAS 동기화·실환경 검증 전.

## 확인된 문제와 변경

기존 계좌 selector는 로컬 일지 필터에만 쓰였고 HistoryWorker의 service는 창 생성 당시
기본 client를 사용했다. 새 NAS 모의 계좌는 체결을 가져오기 전에는 로컬 scope 목록에 없었다.
서버 v3 선택 계좌 API는 구현됐지만 현재 binding을 PC가 알아내는 목록 경로는 없었다.

인증 GET `/api/v3/kiwoom/accounts`와 capability `account_contexts_v3`를 추가했다.
기존 mock owner의 admitted bundle/main manager binding만 공개하고 raw 계좌번호/키/토큰은 없다.
새 계좌 상태나 인증을 생성하지 않으며 키움 TR도 요청하지 않는다. 비활성 계좌의 저장 일지는 유지한다.

일지는 기존 SettingsRequestWorker로 목록을 읽어 로컬 scope와 합치며 계좌 옆 `↻`로 갱신한다.
저장된 명시 선택을 유지하고 저장된 선택이 없으면 첫 조회 가능 계좌(main 우선)를 선택한다.
목록 I/O는 GUI 밖에서 수행하고 10초 timeout을 사용한다. 일지가 없는 새 계좌도 선택할 수 있다.

## 조회·저장 계약

HistoryWorker는 시작 시 선택 scope를 고정한다. worker 안에서 현재 NAS 목록을 한 번 읽어
해당 profile/binding revision의 고정 RemoteKiwoomRestClient를 만든다. 창에서 캐시한 revision을
최신으로 가정하지 않는다. 모든 날짜/페이지/비용은 같은 전체 AccountQueryContext를 요구한다.
조회 중 키 교체로 revision이 달라지면 import 전체를 저장하지 않고 새 조회를 요구한다.

기존 accountless 기본 query는 v2를 유지하고 고정 NAS context가 있는 client만 v3를 호출한다.
기존 service 생성자의 계좌를 임의 지정/재라벨링하지 않는다. 직접 adapter는 자기 검증 scope만 허용한다.
Failover/ParallelValidation client의 명시 계좌 선택은 primary만 사용한다. PC 단일 프로필로 다른
NAS 계좌를 요청하지 않으며 구 NAS 미지원도 기본 v2 계좌로 대체하지 않는다. 시세 fallback는 그대로다.

선택 worker 완료 payload는 기존 `(fills, costs, cost_error)` 뒤에 context를 추가한 4항 tuple이다.
기존 호환 worker/수신 경로의 3항 tuple도 유지한다. 빈 결과는 context scope로 저장한다.
UI에서 선택이 바뀐 늦은 4항 결과·행/완료 context가 다른 계좌는 저장 전에 거절한다.
완료 signal 대기 중 새 worker/자동 작업이 기존 작업 ID를 덮지 않게 worker pointer가 해제된 뒤 시작한다.
진행 문구도 같은 선택일 때만 갱신한다. 종료 중 결과와 마지막 interruption 이후 완료 emit을 막는다.

## 모의 명령 client 계약

고정 mock client의 submit/GET/cancel은 같은 scope/profile/revision으로 기존 v2 경로를 사용한다.
real context에는 모의 명령을 보내지 않는다. request_id와 주문 입력은 호출자가 명시하고 검증한다.
submit 응답 미확인 상태는 client 메모리에 같은 내용으로 유지하며 새 ID/내용을 거절한다.
자동 retry·자동 새 ID·legacy v1 명령 우회는 없다. caller는 미확정 요청 확인 전 같은 client/ID를 유지한다.
응답 context를 검증한 intent_id를 받아야 pending을 해제한다. 서버 원장/UNKNOWN 중복 방지는 그대로다.
GET/cancel도 같은 계좌 응답을 검증한다. 이 단계는 PC 호출 경계이며 새 주문 UI/자동주문을 켜지 않는다.

## 수정 경계와 설계 차이

`journal_process.py`의 목록/선택, `journal_workers.py`의 import context,
`kiwoom_rest/remote_client.py`의 optional 고정 context와 기존 직접/failover/병행 adapter를 수정했다.
서버는 기존 owner의 읽기 전용 binding snapshot과 app route/capability만 추가했다.
새 manager/wrapper/DB table/migration 또는 시장자료 TR은 없다.
목록 API는 R4에서 발견한 PC binding discovery 공백을 채우는 기존 계약의 확장이다.

## 검증

관련 회귀 388개 실행: 387개 통과, Windows POSIX 권한 1개 생략, 종료 코드 0.
마지막 행/완료 context 보호와 실제 Qt 목록 조회 검증 뒤 일지/선택 계좌 44개를 재실행해 모두 통과했다.
신규 R4b 테스트는 client 9개·일지 7개·라우트 통합 4개다. 실행 로그는
`tmp/r4b-regression.log`, `tmp/r4b-final-focused.log`다. fake REST/WS/주문 전송기와 임시 SQLite/vault만 사용했다.
최초 실행의 들여쓰기 오류를 수정했고 마지막 Qt 테스트의 이미 삭제된 worker 이중 wait도 정리했다.
현재 제품/테스트 오류 없음. 실제 Qt receiver로 느린 GET 중 event 처리·I/O thread 분리·중복 클릭 차단·
queued 결과 전달을 확인했다. Python 문법 12개 파일·관련 공백 검사 통과, 서버/Compose/Dockerfile build 세 곳 일치.
실제 계좌/키/사용자 DB·NAS 접속·주문은 사용하지 않았다.
확인 범위: 두 계좌 목록 인증/분리, 선택 계좌 모든 페이지, 다른 scope/연결 버전 거절,
새 조회 시 binding 재확인, 빈 context 저장, 늦은 선택 변경 시 쓰기 없음,
같은 계좌 revision 변경 시 worker 실패, 구/비활성 NAS 계좌의 기본 계좌 대체 없음,
PC fallback TR 없음, 같은 ID 주문 한 번만 전송, 동일 계좌 GET/cancel, real mock 명령 차단.
기존 원장·일지 schema/저장/동기화·설정·인증·시장/모의 owner 회귀도 확인한다.

## 다음 한 단계

R5: 뉴스/DART/AI 공급자 인증 owner와 실행 중 키 교체를 기존 처리 수명에 연결한다.
원문 추출·요약·분류 품질은 이 단계에서 변경하지 않는다. R6 실전 owner와 R7 배포 검증도 남아 있다.
최신 누적 build는 `2026.09.15-runtime-credentials-r4-selected-client-v1`이다.
NAS 소스 동기화는 하지 않았으며 R7까지 중간 재빌드를 요청하지 않는다.
실제 NAS HTTPS/PostgreSQL·다중 계좌 조회·장중 검증과 새 주문 UI/실제 운용은 완료가 아니다.
모델 에스컬레이션 없음.

# 런타임 인증 R3b 모니터 종료·DB 소유권

2026-09-15. 로컬 구현 완료, R3 전체 진행 중.
build `2026.09.15-runtime-credentials-r3-monitor-fence-v1`. R0/R1/R2/R3a 누적 소스.
NAS 소스 동기화·배포 전이며 실제 키 변경 owner는 아직 등록하지 않았다.

## 확인한 경로와 변경

기존 모니터 close는 worker task를 cancel했다. reader 조회·to_thread(DB 쓰기)를 기다리는
coroutine만 취소되고 실제 HTTP/DB 작업이 남을 수 있었다. WS도 토큰 thread 종료를 확인하지 않았다.
ExecutionRuntime의 사전 lease 확인과 repository의 결과 쓰기가 다른 트랜잭션이라,
전송 중 owner가 바뀌면 늦은 성공 응답을 이전 실행자가 저장할 수 있었다.

기존 monitor/runtime/repository/DB 경계에만 변경했고 새 Manager나 범용 계층을 추가하지 않았다.

- monitor start와 복구/직접 refresh는 owned task다. 호출자 취소는 실제 작업 취소가 아니다.
  close는 접수와 새 이벤트를 막고 queue 종료 표식까지 기존 작업을 실행한다.
  직접 refresh도 drain한 뒤 heartbeat를 닫고 현재 runtime을 stop해 lease를 조건부 해제한다.
  시작 중 close나 close 대기자 취소도 같은 소유 task의 실제 완료를 기다린다.
- heartbeat를 별도 task로 실행해 느린 reader 복구 중에도 임대 갱신이 계속된다.
  소유권을 잃으면 새 접수를 막고 이후 DB 쓰기는 owner fence로 거절한다.
- WS close는 owned task다. 토큰 provider의 to_thread가 끝난 뒤에만 종료를 완료하며,
  실제 socket context exit도 기다린다. 닫는 중 deliver는 차단하고 REAL data=null은 빈 batch다.
- Runtime은 lease 획득 뒤 repository에 account/run/owner를 불변 연결한다.
  같은 repo를 다른 owner에 재사용하지 않는다. 계좌별 후속 bundle은 각각 repo를 소유한다.
- 중앙 intent 생성, event+intent 갱신, account snapshot 저장에 선택 ownership 인자를 추가했다.
  운영 repo는 항상 전달하고 비운영 import/오프라인 caller는 기존 계약을 유지한다.
  실제 쓰기 트랜잭션 안에서 lease owner·만료·문서 scope·기존 intent scope를 확인한다.
  SQLite BEGIN IMMEDIATE, PostgreSQL lease row FOR UPDATE로 검사→쓰기 사이 교체를 막는다.
- 전송 전 SUBMISSION_UNKNOWN이 기록된 뒤 owner가 바뀌면 늦은 성공 쓰기를 거절한다.
  이전 원장을 성공으로 바꾸거나 주문을 재전송하지 않는다.

## 내부 계약과 완료 기준

monitor.close → 새 접수/이벤트 차단, 기존 queue/직접 work 완료, heartbeat 완료, 현재 lease 해제.
monitor의 종료 대기자 취소 → close는 계속 실행. 같은 인스턴스 재기동은 거절하고 새 bundle을 사용한다.
WS.close → 이벤트 차단, token thread 종료, 실제 socket exit. 진행 중 start는 거절한다.
repo owner 연결 → 불변 scope/owner. owned 쓰기 → 같은 transaction 안에서 검증 후 저장 또는 고정 오류.
owner 교체/만료는 EXECUTION_OWNERSHIP_LOST, scope 불일치는 EXECUTION_OWNER_SCOPE_MISMATCH다.
새 DB 테이블이나 migration 버전 변경은 없다. 기존 공개 HTTP 경로는 변경하지 않았다.

## 검증

새 회귀는 실제 read/DB 저장/token thread/socket exit 대기, close/start/직접 refresh 호출자 취소,
닫는 중 이벤트 차단, 복구 중 heartbeat, null frame, 세 owned 쓰기의 늦은 owner 차단,
scope/불변 owner·만료/갱신·오프라인 read, 검사와 쓰기 사이 동시 owner 교체, 늦은 주문 성공의
UNKNOWN 보존을 확인한다. 제어 가능한 fake transport와 임시 SQLite를 사용하며 실제 주문은 보내지 않는다.
테스트의 WS 시각 fixture는 KST 세션 시각으로 지정했다. 초기 UTC 00:00 fixture로는 세션이 열리지
않아 대기 테스트가 진행되지 않는 문제가 있었고, 시각과 실패 시 release cleanup을 교정한 뒤 통과했다.
기존 서버/REST/R0~R3a/뉴스/AI/설정 UI 회귀까지 264개 실행: 263 통과, POSIX 권한 1개 Windows에서 생략,
종료 코드 0. 변경 Python 문법·공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.
최종 결과는 배포 원장에도 기록했다.
PostgreSQL 통합 스크립트에 owned snapshot 성공과 세 stale owner 쓰기 거절을 추가했다.

## 남은 부분

실제 credential owner 등록, 계좌별 bundle 조립·인증 준비·같은 계좌 scope/run 유지와 새 계좌 추가,
연결별 identity/binding 캡처·query cursor/주문 route scope·startup/health 연결은 다음 R3 부분이다.
실제 NAS PostgreSQL row lock·Linux·WS/키움 인증·프로세스 중단은 R7 검증이며 미실행이다.
현재 단계 모델 에스컬레이션 없음. NAS 중간 동기화/재빌드는 요청하지 않는다.

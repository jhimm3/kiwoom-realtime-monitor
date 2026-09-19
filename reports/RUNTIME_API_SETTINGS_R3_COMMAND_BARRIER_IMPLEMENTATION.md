# 런타임 인증 R3a 모의 명령 장벽

아래는 R3a 완료 당시 기록이다. monitor/WS 종료·DB 소유권 후속 완료는
[R3b 결과](RUNTIME_API_SETTINGS_R3_MONITOR_FENCE_IMPLEMENTATION.md)를 따른다.

2026-09-15. 로컬 구현 완료. R3 전체 진행 중.
build `2026.09.15-runtime-credentials-r3-command-barrier-v1`. NAS 소스 동기화·배포 전.

## 확인한 기존 흐름과 변경

기존 gateway는 HTTP 요청 coroutine에서 account refresh와 to_thread(runtime.submit/cancel)를
직접 기다렸다. HTTP 대기자가 취소되면 실제 동기 전송이 계속되더라도 command lock이 풀릴 수 있고,
계좌 교체가 실제 종료를 확인할 명령 task가 없었다. lease는 acquire/heartbeat만 있고 현재 소유자에
한정한 해제 메서드가 없어 같은 계좌의 다음 runtime이 만료까지 기다릴 수 있었다.

기존 gateway/runtime/repository/DB에 명시 메서드를 추가했다. 별도 Manager나 계층은 만들지 않았다.

- gateway public submit/cancel은 서버 소유 task를 만들고 shield로 기다린다.
  이 task가 command lock 안에서 계좌 refresh·전송·결과 저장을 끝까지 실행한다.
  대기자 취소는 명령 취소가 아니며 동일 request_id 재요청은 기존 원장 결과를 유지한다.
- begin_credential_change는 새 명령 접수를 먼저 막고 소유 task들의 실제 완료를 기다린다.
  drain 대기자 취소도 drain을 취소하지 않는다. 완료 전 end는 거절한다.
  close는 동일 drain 후 계속 접수를 막고, 정상 서버 종료에서 monitor/broker/DB보다 먼저 실행한다.
- ExecutionRuntime은 같은 operation RLock 안에서 submit/cancel/reconcile/stop을 실행한다.
  stop은 실제 동기 전송·reconcile DB 쓰기가 종료될 때까지 기다린다.
  heartbeat는 별도 state RLock을 사용해 느린 전송 때문에 임대 갱신이 막히지 않게 했다.
- 신규 주문 OFF는 submit만 막는다. 기존 주문의 broker 대조·취소는 허용한다.
  기존 수동 전송의 활성화 정책은 유지하고 새 계좌 bundle에서는 후속 단계가 명시 OFF를 설정한다.
- release_runtime은 DB에서 account/run/owner가 같은 행만 원자 DELETE한다.
  만료 후 바뀐 owner, 다른 계좌/run, 반복된 예전 stop은 새 owner의 lease를 지우지 못한다.
  stop한 인스턴스는 retired로 남아 start/heartbeat/신규 주문으로 재활성화되지 않는다.

## 입출력과 완료 기준

gateway begin → 접수 잠금 + 실제 명령 drain 완료. 대기 중에는 gate 유지.
gateway end → drain 완료를 검증하고 접수 복원. close 이후 end는 거절.
runtime set_new_orders_enabled(false) → 새 submit만 거절. 기존 cancel/reconcile 유지.
runtime stop → 실제 동기 작업 종료 후 해당 소유 lease 해제 여부 bool. 예전 인스턴스는 retired.
repo release → 현재 owner가 일치하면 true, 없거나 다른 owner면 false. DB 오류는 성공으로 숨기지 않는다.

이 단계에서는 계좌 bundle이나 실제 credential owner를 등록하지 않았다.
stop은 운영 계좌 교체에 아직 연결하지 않으며, 호출자는 gateway/monitor를 먼저 drain해야 한다.

## 검증과 실제 미검증

새 회귀는 HTTP submit/cancel 취소 후 실제 전송·원장 완료, refresh 대기 중 drain,
drain 취소와 조기 재개 거절, 닫힌 gateway 재개 거절, 신규 주문 OFF 중 취소/대조,
실제 submit/reconcile DB 쓰기를 기다리는 stop, 느린 전송 중 heartbeat, 조건부 lease 해제를 검증한다.
임시 SQLite 원장·제어 가능한 fake transport를 사용하며 실제 주문은 전송하지 않는다.
PostgreSQL 통합 스크립트에도 조건부 release/즉시 재획득/예전 owner 해제 거절을 추가했다.
최종 로컬 회귀 250개 실행: 249 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
변경 Python 문법·공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.
최종 회귀 결과는 NAS_DEPLOYMENT_PENDING.md에도 기록했다.

## 다음 부분

monitor/WS의 실제 종료, 늦은 execution 저장의 DB 트랜잭션 소유권 검사,
계좌별 bundle 조립·검증·활성화, query cursor/주문 route scope와 startup/health 연결이 남아 있다.
원인 불명 버그나 설계 충돌은 확인하지 않았으며 현재 단계 모델 에스컬레이션 없음.
실제 NAS PostgreSQL·Linux·키움 인증과 계좌 교체는 R7까지 미검증이다.

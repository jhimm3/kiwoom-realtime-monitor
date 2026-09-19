# CR3d2c 실패·취소 최종 후보의 명시 복구

기준일: 2026-09-16. CR3d2b의 FAILED/CANCELLED 상태는 계속 terminal이며 자동 재시도하지 않는다.
이 단계는 운영자가 원인을 검토한 후보 하나에 새 요청 ID와 사유를 남겼을 때만 같은 과학적 실행을 한 번 더 허용한다.
CLI/UI, NAS 배포, 실제 계좌·주문은 범위 밖이다.

## 허용 조건

execute_final_holdout_evaluation의 선택적 recoveries는 candidate scientific hash별 `request_id`와 `reason`만 받는다.
모든 recovery 지시를 첫 claim 전에 검사한다. candidate는 현재 locked batch에 있어야 하고 기존 execution 상태는 FAILED 또는 CANCELLED여야 한다.
PreparedFinalHoldoutEvaluation의 구현 hash, 전체 후보 순서/hash, 단일 OOS 정책, source/DB/run 절대 경로도 일반 final 실행과 똑같이 재검증한다.

같은 content-addressed run ID, spec, isolated final input을 유지한다. 새로운 파라미터나 코드로 바뀌면 복구가 아니라 새 batch 판단이 필요하다.
COMPLETED, RUNNING, 아직 실행하지 않은 후보와 EXPOSED_DEVELOPMENT 창은 복구하지 않는다.
프로세스 비정상 종료로 RUNNING에 남은 실행은 terminal 근거가 없으므로 이번 단계에서 lease 만료나 강제 회수하지 않는다.

## 출력과 부분 결과

application은 해당 run의 immutable output manifest 존재 여부를 요청 기록 전에 확인한다.
manifest가 있으면 DB 완료 전 중단인지 게시 충돌인지 자동 판정할 수 없으므로 fail closed하고 수동 검토를 요구한다.
manifest가 없고 DB에 snapshot/decision/event/report 같은 부분 행이 남아 있으면 deterministic 재실행이 기존 immutable ID와 document를 검증한다.
내용이 같으면 재사용하고 다르면 기존 repository 불변성 검사로 실패한다. 행을 삭제하거나 덮어써 복구하지 않는다.

## v20 원자 복구 원장

research_final_holdout_executions에 `generation`을 추가한다. 최초 실행은 1이며 실제 recovery claim마다 1 증가한다.
research_final_holdout_recoveries는 globally unique request ID, execution, owner, reason, REQUESTED/CLAIMED,
요청·claim 시각과 허가하는 다음 generation을 append-only로 저장한다. execution+generation도 unique라서
같은 terminal 상태에서 미래 재시도 요청을 여러 개 미리 쌓을 수 없다.

request_final_holdout_recovery는 일관된 FAILED/CANCELLED run/execution인지 확인한 뒤 REQUESTED만 기록한다.
상태는 바꾸지 않으므로 요청 응답이 유실되어도 같은 내용으로 멱등 재전송할 수 있다.
claim_final_holdout_execution은 요청/owner/실행과 같은 run/spec/input을 다시 확인하고 한 BEGIN IMMEDIATE에서
research run과 final execution을 RUNNING으로 되돌리고 generation을 올린 뒤 recovery를 CLAIMED로 바꾼다.
이미 CLAIMED인 요청은 다시 실행권으로 쓰지 않는다. 복구 실행이 다시 terminal이면 새 request ID와 사유가 필요하다.

## 결과와 제한

후보 결과에는 recovery_request_id를 함께 반환한다. 기존 batch 상태와 후보 간 비합산·비재선택 계약은 유지한다.
자동 재시도, 원격 권한 시스템, RUNNING orphan 회수, 결과 manifest 수동 판정 도구는 추가하지 않았다.
read-only 비교는 v17/v18/v19/v20을 허용하고 v19 migration은 기존 execution에 generation 1을 부여한다.

## 검증 범위

- 실패와 취소의 명시 복구, 동일 run ID와 generation/owner 전환.
- output 게시 실패 뒤 기존 report/부분 행의 deterministic 재사용.
- 남아 있는 output manifest의 복구 차단과 감사 행 미생성.
- request 멱등/충돌, 동시 claim 한 소유자, CLAIMED request 재사용 차단, 새 요청의 다음 generation.
- 미실행/비terminal/노출 창/잘못된 요청 문서의 선행 거절.
- v19 read-only 무변경 조회와 v20 보존 migration.

집중 final 실행·원장·repository 회귀 47개가 36.490초에 통과했다.
전체 `test_research*.py` 연구·offscreen 화면 회귀 572개가 389.641초에 통과했다.
변경 Python AST/compile, 후행 공백, 스키마 기대값과 문서 계약도 별도로 확인했다.

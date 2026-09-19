# CR3d3a 최종평가 별도 프로세스 요청

기준일: 2026-09-16. CR3d2a 준비, CR3d2b 실행, CR3d2c 명시 복구를 기존 research_process child 경계에서 한 요청으로 연결한다.
앱 UI, NAS 배포, 실제 계좌·주문은 범위 밖이다.

## 불변 요청

independent_final_holdout_request/v1은 locked FinalHoldoutBatchSpec, 1~200개 fixed single_run candidate,
접근 request ID/시각, 실행 owner token과 선택적 recovery mapping을 포함한다. 후보는 같은 source/DB/run 경로와 단일 OOS 정책을 사용한다.
recoveries는 candidate scientific hash별 request ID/reason만 허용한다.

loader는 파일을 4 MiB로 제한하고 외곽·후보·access·recovery의 정확한 필드 집합을 검사한다.
경로를 요청 파일 기준 절대경로로 정규화하고 요청 파일이 frozen dataset 안에 있으면 거절한다.
현재 구현 hash로 후보 scientific hash 집합을 계산해 batch와 대조한다. accessed_at은 timezone-aware이며 final 종료 이후여야 한다.
이 parser는 full source bytes나 연구 DB를 읽거나 생성하지 않는다. source identity와 과거 개발 footprint는 기존 CR3d2a 준비가 확인한다.

파싱된 FinalHoldoutExecutionRequest.to_dict는 절대경로와 canonical batch를 가진 재실행 snapshot이다.
같은 snapshot은 동일 접근 event를 멱등 재사용하며 terminal FAILED/CANCELLED를 자동 재시도하지 않는다.
복구하려면 recoveries와 새 owner를 명시한 새 snapshot이 필요하고 CR3d2c의 generation/manifest 검사를 그대로 통과해야 한다.

## 실행과 파일 경계

`--evaluate-final`은 기존 source 선택 인자와 상호 배타적이다. process는 cancel marker를 확인한 뒤
prepare_final_holdout_evaluation과 execute_final_holdout_evaluation을 순서대로 호출한다.
result/cancel은 서로 달라야 하며 request, research DB, frozen dataset 또는 run artifact 아래에 둘 수 없다.
기존 `_write_result`의 임시 파일+원자 rename 경계를 사용한다.

결과는 independent_final_holdout_result/v1에 status/kind를 추가한다. 모든 candidate 상태와 batch 상태 의미는 CR3d2b 그대로다.
candidate의 FAILED는 요청 자체 파싱/준비 실패가 아니므로 exit 0 result 안의 terminal 상태로 보존한다.
취소는 exit 2, resource blocked는 exit 3, 요청/준비/저장 오류는 exit 1이다.

## 제한

실행 중 candidate별 progress snapshot은 아직 쓰지 않는다. UI는 다음 CR3d3b에서 이 불변 요청과 child를 소유한다.
자동 retry, RUNNING orphan 회수, 후보 비교·합산·재선택, EXPOSED_DEVELOPMENT 전환은 없다.
연구 DB v20, NAS/API, 모의·실계좌 주문 경로는 변경하지 않는다.

## 검증 범위

- parser 정규 roundtrip과 실제 CLI 준비→접근→실행 완료.
- terminal 실패의 무복구 재호출과 명시 recovery generation 2 완료.
- 준비 전 cancel, 실행 resource block, source identity mismatch의 상태/exit 분리.
- 외곽·candidate·hash·시간대·owner·recovery 오류와 4 MiB 제한의 DB 선행 거절.
- request/DB/dataset/run artifacts/result-cancel 충돌의 source/DB 무변경 거절.

최종 CLI·준비·실행·복구 집중 회귀 75개가 45.922초에 통과했다.
전체 `test_research*.py` 연구·offscreen 화면 회귀 580개가 387.441초에 통과했다.
변경 Python AST/compile, 후행 공백과 이전 계약 문구도 별도로 검사했다.

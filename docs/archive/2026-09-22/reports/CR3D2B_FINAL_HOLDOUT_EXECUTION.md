> **과거 기록** · 원래 경로: `reports/CR3D2B_FINAL_HOLDOUT_EXECUTION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3d2b 최종 후보 소유권 실행

기준일: 2026-09-16. CR3d2a가 검증·격리하고 접근 원장에 기록한 입력만 실제 최종 simulation engine에 연결한다.
일반 연구 실행이나 개발 검증의 OOS 차단은 유지한다. CLI/UI, NAS 배포, 실제 계좌·주문은 범위 밖이다.

## 입력과 재검증

execute_final_holdout_evaluation은 PreparedFinalHoldoutEvaluation, owner_token, 선택적 cancel callback을 받는다.
실행 직전에 현재 구현 hash와 prepared hash, 정렬된 전체 후보 hash, canonical 단일 OOS 정책을 다시 대조한다.
Prepared DTO가 보존한 source/DB/run 절대 경로 tuple도 모든 후보와 다시 대조한다.
각 후보는 CR3d2a에 고정된 dataset/DB/run 경로·세션·전략·명시 비용 모델을 그대로 사용한다.
후보마다 새 ResearchResourceGuard와 PaperExecutionEngine을 만들므로 새 현금, 빈 portfolio/pending order와 flat 전략 상태로 시작한다.

run_research의 새 independent_final_holdout/v1 scope는 isolated final runtime input만 허용한다.
final descriptor의 정책·세션·warmup/active/end, 관측/봉/테마 경계를 실행 전에 다시 검사한다.
repository 소유권 tuple(batch/candidate/owner)이 없거나 RUNNING 행과 맞지 않으면 엔진 생성 전에 거절한다.
기존 execute_research 기본 scope와 independent_development_validation/v1의 입력·ID 의미는 바꾸지 않는다.

## v19 원자 소유권

research_final_holdout_executions는 execution ID, window/batch/candidate, unique run ID, owner, state,
started/finished, logical result hash와 reason을 저장한다. batch+candidate는 유일하다.
claim_final_holdout_execution은 FINAL_RESERVED window의 locked spec, batch candidate 집합,
final runtime/spec, content-addressed run ID를 확인한다. ownership과 research_runs RUNNING 생성을 한 BEGIN IMMEDIATE에서 수행한다.
동시 claim은 하나만 CLAIMED다. 이미 RUNNING인 후보는 owner가 같아도 BUSY이며 두 번째 엔진을 시작하지 않는다.

실행 완료 순서는 scientific DB rows/report 생성, immutable output manifest 게시, research run COMPLETED,
final execution COMPLETED다. output 게시 실패는 완료 전에 발생하므로 wrapper가 run과 execution을 FAILED로 함께 종결한다.
COMPLETED cache는 research run/report/output의 run ID, logical hash, spec, input dataset/hash와 split을 모두 대조한다.
하나라도 없거나 다르면 CACHE_INVALID이며 immutable 완료를 재실행하지 않는다.

FAILED와 CANCELLED는 reason과 함께 terminal이다. 기본 start_run의 cancelled resume 계약을 final claim에는 적용하지 않는다.
후속 호출은 FAILED/CANCELLED를 그대로 반환하며 자동 retry하지 않는다. terminal 변경도 거절한다.
DB 자체가 unavailable해 terminal 기록도 실패한 경우에는 성공 상태를 만들지 않고 예외를 상위로 전달한다.
명시 recovery를 만들기 전에는 이 상태를 운영자가 검토해야 한다.

## 결과와 노출

independent_final_holdout_result/v1은 batch/window/implementation hash와 후보별 상태만 반환한다.
각 행에는 candidate hash, run ID, state/reason/result hash, performance/report status가 있다.
모든 후보가 COMPLETED 또는 검증된 CACHED일 때만 batch_status=COMPLETED다.
고정 후보는 hash 순서로 모두 독립 실행한다. 후보 간 equity/PnL을 합산하지 않고 comparison·winner를 만들지 않는다.

RUNNING 후보가 있는 final window는 EXPOSED_DEVELOPMENT로 전환할 수 없다.
모든 해당 실행이 terminal이 된 뒤에만 사용자가 final 결과를 개발 개선에 사용했다는 명시 노출 event를 기록할 수 있다.
동일 batch는 다시 준비할 수 있지만 candidate execution terminal을 재claim할 수 없다.
같은 batch의 final execution 행은 과거 개발 노출 검사에서 자기 자신으로 인식해 재준비를 막지 않는다.

## 검증

- 신규 test_research_final_execution.py 16개: 실제 final 실행/새 상태, 두 후보 독립 실행, 원자 동시 claim,
  소유권 없는 실행 차단, 완료 cache/불완전 artifact, failure/cancel terminal, output 게시 실패, 실행 중 노출 차단,
  동일 batch 재준비와 구현/정책 drift를 검사한다.
- 최종 집중 final 준비·원장·실행 회귀 57개, 32.351초, OK, native 종료 0.
- 핵심 연구/DB/runner/개발 검증 회귀 132개, 79.556초, OK, native 종료 0.
- v19와 기존 campaign/storage migration 회귀 143개, 131.907초, OK, native 종료 0.
- 전체 연구·offscreen 화면 회귀 551개, 362.418초, OK, native 종료 0.
- 최종 문서 갱신 뒤 변경 25개 파일 AST/whitespace와 문서 Git diff check 재확인도 통과했다.

## 남은 범위

FAILED/CANCELLED 후보의 권한·reason·generation과 부분 artifact 검증은 후속 CR3d2c에서 구현했다.
자동 retry는 계속 만들지 않았고 같은 코드/입력, output manifest 부재, append-only 요청 원장을 요구한다.
[CR3d2c 명시 복구 계약](CR3D2C_EXPLICIT_FINAL_RECOVERY.md)을 따른다.
CLI 요청/result file, 진행 화면, 최종 결과를 본 뒤 개발 피드백으로 넘기는 EXPOSED_DEVELOPMENT UI도 후속이다.
새 기간 선택·24시간 campaign 연결·NAS 전체 규모 성능 V1은 이후 단계다.

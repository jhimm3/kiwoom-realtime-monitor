# 전략 연구 요청 파일

기준: **2.1.0**, 2026-09-22 문서 정리. 이 문서는 현재 지원하는 요청 필드·예제·실행 경계를 보존하는 상세 계약이다.
구현·배포·운영 확인 범위는 [현재 상태](CURRENT_STATUS.md), 미완료·보류 작업은 [남은 작업](OPEN_ITEMS.md)을 따른다.
현재 PC 연구 DB는 **v23**이다. 아래 v10~v22 표기는 해당 기능을 도입한 마이그레이션 이력이며 현재 DB 버전을 뜻하지 않는다.
지속 campaign·자동 가설·다기간 bundle·실제 RSS 확인과 CPU batch 양보는 구현되어 있다. 실제 NAS 전체 규모·PC 24시간 운전 검증과는 구분한다.
최우선 자료 확보 계획은 [과거 자료 확보](HISTORICAL_BACKFILL_PLAN.md)에 있다. 새 원천의 봉·뉴스를 기존 관측 replay와 같은 의미로 자동 편입하지 않는다.

## CR3d3c 최종 결과 개발 노출 요청 (2026-09-16)

`research_process --expose-final`은 최대 1 MiB의 `final_holdout_exposure_request/v1` JSON을 받는다.

```json
{
  "version": "final_holdout_exposure_request/v1",
  "database": "C:/.../research.sqlite3",
  "batch": {"version": "final_holdout_batch/v1", "dataset_id": "...", "dataset_hash": "...", "session_profile": "...", "candidate_spec_hashes": ["..."], "evaluation": {}},
  "exposure": {"request_id": "...", "exposed_at": "2026-09-16T00:00:00+00:00", "reason": "final 결과를 보고 다음 가설을 설계함"}
}
```

database는 이미 존재해야 하고, request ID는 1~256자, reason은 1~2000자다. exposed_at은 timezone-aware이고 final 창 종료 이후여야 한다.
parser는 DB를 열지 않고 파일 존재·JSON 계약만 확인한다. child가 원장 window의 batch/spec을 대조하고 RUNNING final 후보가 없을 때 되돌릴 수 없는 EXPOSED_DEVELOPMENT event를 추가한다.

```text
python -m kiwoom_monitor.research_process --expose-final exposure.json --result exposure-result.json --cancel exposure.cancel
```

result/cancel은 request/DB를 덮어쓸 수 없다. 변경 전 취소는 exit 2, 성공/멱등 재요청은 exit 0, 계약·원장 불일치는 exit 1이다.
화면은 검증된 final result와 사용자 근거가 있을 때만 이 child를 호출한다. 성공 후 같은 기간은 final로 재사용할 수 없다.

## CR3d3b 최종 평가 화면 (2026-09-16)

전략 연구 창의 `최종 평가`는 아래 CR3d3a 요청을 선택해 정규화한 불변 snapshot을 별도 child에 전달한다.
화면은 source bytes와 연구 DB를 열지 않으며 요청 parser 외의 과학적 검증·접근 원장·실행은 child가 담당한다.

- 실행마다 `final_holdout_<uuid>.json`, `.result.json`, `.cancel`을 화면 state 폴더에 만든다. request/DB/frozen source/run artifacts 안에는 만들지 않는다.
- 결과는 native exit/status, `kind/version`, batch/window ID, 구현 hash, 정렬 candidate hash, recovery request ID, batch 완료 계산이 요청과 일치할 때만 표시한다.
- `COMPLETED/CACHED`는 재사용하고 `NOT_STARTED`만 같은 snapshot으로 이어서 실행한다. `FAILED/CANCELLED`는 자동 재시도하지 않는다.
- 명시 복구는 선택한 terminal candidate 하나에 사용자 reason, 새 request ID, 새 owner token을 넣은 새 snapshot으로만 실행한다.
- 취소·파싱·결과 범위 불일치에서는 이전 표를 유지한다. 창 닫기는 취소 파일만 쓰고 자식 종료를 기다리지 않는다.

## CR3d3a 최종 평가 프로세스 요청 (2026-09-16)

`research_process --evaluate-final`은 최대 4 MiB의 `independent_final_holdout_request/v1` JSON 하나를 받는다.
이 파일은 UI가 반복 실행할 불변 snapshot이기도 하며 다음 필드만 허용한다.

```json
{
  "version": "independent_final_holdout_request/v1",
  "batch": {"version": "final_holdout_batch/v1", "dataset_id": "...", "dataset_hash": "...", "session_profile": "...", "candidate_spec_hashes": ["..."], "evaluation": {}},
  "candidates": [{"mode": "single_run", "family": "...", "dataset": "...", "database": "...", "runs_dir": "...", "session_profile": "...", "strategy": {}, "execution": {}, "evaluation": {}, "resource_budget": {}}],
  "access": {"request_id": "...", "accessed_at": "2026-09-16T00:00:00+00:00"},
  "owner_token": "...",
  "recoveries": {"candidate_sha256": {"request_id": "...", "reason": "..."}}
}
```

후보는 1~200개 fixed single_run이며 candidate 문서에도 위 열거 필드만 허용한다.
경로는 요청 파일 기준으로 해석하지만 정규 snapshot은 절대경로를 쓴다. 요청 파일 자체는 frozen dataset 밖에 둔다.
현재 구현 hash로 후보 scientific hash를 다시 계산해 batch의 정렬된 hash와 정확히 일치해야 한다.
accessed_at은 timezone-aware이고 final window 종료 이후여야 한다. recoveries는 비어 있거나 locked candidate만 가리킨다.
parser는 이 단계에서 source bytes/DB를 읽거나 만들지 않는다.

```text
python -m kiwoom_monitor.research_process --evaluate-final final.json --result final-result.json --cancel final.cancel
```

result/cancel은 서로 달라야 하고 request, research DB, frozen dataset, run artifacts를 덮어쓸 수 없다.
exit 0은 요청 실행 완료이며 candidate의 과학적 FAILED도 result 안의 terminal 상태로 남을 수 있다.
사용자 취소는 exit 2, resource blocked는 exit 3, 파싱·준비·저장 오류는 exit 1이다.
result는 기존 `independent_final_holdout_result/v1` 필드에 `status`와 `kind=independent_final_holdout`을 추가한다.
자동 retry, 후보 재선택, 최종 결과의 개발 노출은 수행하지 않는다.
[프로세스 계약](archive/2026-09-22/reports/CR3D3A_FINAL_HOLDOUT_PROCESS.md)을 따른다.

## CR3d2b 최종 평가 실행 API (2026-09-16)

내부 API는 CR3d3a CLI가 사용한다. CR3d2a가 반환한 PreparedFinalHoldoutEvaluation과 1~256자 owner_token을
execute_final_holdout_evaluation에 전달한다. cancel_requested는 호출 가능한 함수이며 기본값은 계속 실행이다.
호출 시 구현 hash, batch의 정렬 후보 hash, 모든 후보의 canonical 단일 OOS 정책을 다시 확인한다.
Prepared DTO에 고정한 source/DB/run 절대 경로도 모든 후보와 다시 대조하여 준비 뒤 경로 교체를 막는다.

research v19는 research_final_holdout_executions를 추가한다. batch_id+candidate_spec_hash가 유일하고
run_id도 유일하다. claim은 locked FINAL_RESERVED window/batch/candidate, independent_final_holdout/v1 spec,
independent_final_holdout_input/v1 manifest와 content-addressed run ID를 확인한다.
소유권 행과 research_runs RUNNING 행은 한 BEGIN IMMEDIATE에서 생성된다.

상태 계약은 다음과 같다.

- 새 후보: CLAIMED 후 새 ResourceGuard·PaperExecutionEngine·초기 현금/빈 전략 상태로 실행한다.
- RUNNING: BUSY로 반환하며 다른 실행자가 엔진을 시작하지 않는다.
- COMPLETED: DB run/report와 immutable output의 run/hash/spec/input/split을 모두 대조한 경우만 CACHED다.
- FAILED/CANCELLED: terminal로 반환한다. 자동 재시도 또는 cancelled→running 전환은 없다.
- 출력 manifest 게시가 실패하면 research run/final execution은 FAILED이며 COMPLETED로 남지 않는다.
- RUNNING 후보가 하나라도 있는 window는 EXPOSED_DEVELOPMENT로 바꾸지 않는다.

result version은 independent_final_holdout_result/v1이다. batch/window/implementation hash와 후보별
candidate hash, run ID, state/reason/result hash, performance/report status만 반환한다.
batch_status COMPLETED는 모든 후보가 COMPLETED 또는 검증된 CACHED일 때만 사용한다.
후보 간 손익 합산·비교·승자 선택 필드는 없다. 실패·취소의 명시 복구는 CR3d3a 요청의 recoveries로 전달하며 CR3d3b 화면이 이 계약을 호출한다.

## CR3d2a 최종 평가 준비 API (2026-09-16)

CR3d3a CLI loader가 trusted 준비 코드에 기존 ResearchProcessRequest의 immutable tuple(1~200개),
FinalHoldoutBatchSpec, 기술 request_id와 timezone-aware accessed_at을 prepare_final_holdout_evaluation에 전달한다.
각 후보는 single_run, 등록 family와 일치하는 typed 전략, 명시 세션, 동일 단일 OOS 정책이어야 하며 search/development_partition은 없어야 한다.
최종 후보의 비용은 rate_basis/source/valid_from/valid_to가 명시되고 최종 active 기간을 포함해야 한다. 자본/비용률은 bool이 아닌 정수다.

final_candidate_spec_hash(request, implementation_hash)는 final_candidate/v1 canonical JSON SHA256이다.
family/전체 전략 파라미터/전체 실행·비용 모델/세션 문서/현재 구현 hash를 포함한다. 날짜·dataset/DB/run 경로·자원 설정은 제외한다.
후보 집합을 정렬한 hash tuple이 batch와 정확히 일치해야 한다. full frozen dataset ID/revision hash는 batch에 별도로 고정한다.
모든 후보의 source/DB/run 절대 경로는 같아야 하며 상태/실행 파일은 원본 동결 디렉터리 밖에 둔다.

기존 로더로 full source bytes를 검증한 뒤 independent_final_holdout_input/v1을 준비한다.
final_holdout_partition/v1 descriptor에는 평가·세션·warmup/active/end와 과거 membership seed만 들어간다.
평가 정책을 batch의 canonical UTC 문서에서 복원하므로 KST/UTC로 같은 시각을 적어도 projected 입력 ID는 같다.
전체 원본 ID/hash·후보 집합·미선택 quality/watermark/children은 engine input identity에 섞지 않는다. outer 반환 DTO의 batch가 binding을 보존한다.
일반 execute_research와 개발 projection은 final tag 또는 descriptor를 확인하면 run 생성 전에 거절한다.

원장 접근 전에 같은 BEGIN IMMEDIATE에서 연구 DB 전체 run의 실제 입력 captured_range와 evaluation_spec 구간/워밍업 합집합을 검사한다.
연속 runner는 보고서 fold 사이/밖에서도 실제 입력을 처리하므로 평가 정책만으로 사용 범위를 줄이지 않는다.
evaluation_spec 없는 legacy run도 captured_range로 검사한다. 상태와 무관하게 겹치면 차단하며 실제 범위가 없거나 malformed/unknown이면 차단한다.
과거 성과/보고서는 읽지 않는다. 이 준비 경로는 check_development_history=True를 항상 사용하지만 기존 metadata API 기본값은 False로 호환된다.
raw bytes 검증은 trusted 코드에서 event 전에 수행한다. 반환/전략 실행 이전의 기록이며 전체 시스템 열람 이전 방화벽은 아니다.
같은 nonce의 멱등 준비/새 nonce 접근 기록만으로 실행 선점·캐시·재시도 허가가 생기지 않는다. CR3d2b의 별도 후보 claim이 필요하다.

## CR3d1 최종 평가 원장 기반 (2026-09-16)

FinalHoldoutBatchSpec(final_holdout_batch/v1)은 일반 JSON 실행 요청을 대체하지 않으며 CR3d3a 전용 외곽 요청 안에서만 사용한다.
version/dataset_id/dataset_hash/session_profile/candidate_spec_hashes/evaluation을 모두 요구하며 추가 필드는 거절한다.
dataset_hash와 후보 scientific spec hash는 64자리 소문자 SHA256이고 후보 hash는 정렬된 고유 1~200개 목록이다.
evaluation은 전체 기존 평가 필드를 포함하는 단일 OOS 정책이며 노출 표시가 없어야 한다. 정수 한도에 bool을 허용하지 않는다.
시각은 UTC microsecond로 정규화한다. 같은 KRX active 기간은 자료 revision/profile/파일명이 바뀌어도 같은 window다.
record_final_holdout_access(batch, request_id=..., accessed_at=...)가 최초 batch를 고정하고 기술적 새 접근 이력을 남긴다.
expose_final_holdout(window_id, request_id=..., exposed_at=..., reason=...)는 되돌릴 수 없는 개발 노출을 기록한다.
동일 nonce/내용/시각은 멱등이고 재사용 nonce의 내용 변경, 시각 역행, 창 종료 전 접근은 거절한다.
CR3d1은 hash 형태/원장 범위 기반이며 CR3d2a가 실제 후보 full spec/hash·입력 내용 일치와 과거 연구 기간 검사를 연결했다. CR3d2b가 최종 엔진·후보별 소유권 실행을 연결했다.
원장에 없는 기간을 전체 시스템에서 미사용이라고 단정하지 않는다. [구현 계약](archive/2026-09-22/reports/CR3D1_FINAL_HOLDOUT_LEDGER.md)을 따른다.

## CR3d2c 최종 후보 명시 복구 (2026-09-16)

내부 execute_final_holdout_evaluation의 선택적 recoveries는 candidate scientific hash를 key로 하고
각 값에 `request_id`와 `reason`만 넣는다. CR3d3a 외곽 요청의 recoveries가 이 mapping을 전달한다.
현재 prepared 후보·구현 hash·단일 OOS 정책·절대 경로를 다시 검증하고, 원장의 현재 상태가 FAILED 또는 CANCELLED일 때만 허용한다.
해당 run의 immutable output `manifest.json`이 있으면 자동 판단하지 않고 복구를 거절한다.
요청은 v20 recovery 원장에 REQUESTED로 append되고, claim 시 같은 run/spec/input을 유지한 채 generation이 증가하며 CLAIMED가 된다.
동일 요청 재전송은 멱등이지만 CLAIMED 요청을 다시 실행하지 않는다. 다시 실패한 후보는 새 request ID와 사유가 필요하다.
COMPLETED/RUNNING/미실행/개발 노출 창, RUNNING orphan, 자동 retry는 대상이 아니다.
[복구 계약](archive/2026-09-22/reports/CR3D2C_EXPLICIT_FINAL_RECOVERY.md)을 따른다.

## CR3c2 종목 그룹×개발 구간 순차 검증 (2026-09-16)

--validate-partitions는 기존 v1과 함께 independent_development_validation/v2를 받는다.
v1 외곽 요청의 version을 v2로 바꾸고 다음 symbol_partition 필드를 추가한다.

```json
{
  "version": "stock_hash_partition/v1",
  "salt": "research-2026",
  "bucket_count": 4,
  "buckets": [0, 1]
}
```

위는 symbol_partition 값이며 전체 요청이 아니다. 나머지 request/dataset_id/dataset_hash/fold_names/max_seconds는 기존 필수 객체다.
v2 fold_names는 시간순 1~20개 TRAIN/VALIDATION, buckets는 오름차순/중복 없는 2~20개 정수다.
전체 step은 folds×buckets이며 200개 이하다. OOS/노출된 final/다른 mode/기존 단일 development_partition은 거절한다.
각 fold에서 buckets를 순서대로 실행한다. 같은 source를 한 번 읽으며 매 step은 새로운 portfolio다.
추가 bucket 선택은 기존 bucket의 scientific ID를 바꾸지 않으므로 cache를 재사용한다. salt/count 변경은 새 정책이다.
출력은 모든 fold/bucket/step_key·상태와 전체 batch_status를 포함하고 group_comparisons는 bucket별 확인된 ID만 비교한다.
전체 comparison=None이며 그룹 손익을 한 계좌로 합산하지 않는다. 부분 비교 완료는 전체 batch 완료가 아니다.
기존 CLI/출력 경로·취소·자원 guard/원본 검증 계약은 유지한다. CR3c3에서 전략 연구 → 여러 구간 순차 검증 → 검증 파일 선택 → 검증 실행으로 같은 v2 JSON을 실행한다.
취소 후 '같은 요청 이어서 실행'은 최초 성공적으로 실행한 정규 snapshot을 재사용한다. 선택 파일을 수정해도 이 버튼의 요청은 바뀌지 않는다.
새 요청은 파일을 다시 선택하고 '검증 실행'한다. 완료 결과는 재사용하고 소유자/복구 확인 상태는 임의 재실행하지 않는다.
단계 표의 그룹 1은 bucket 0, 그룹 2는 bucket 1이며 원래 bucket은 tooltip에 표시한다. 그룹 표는 모든 요청/완료/미시작 수와 식별된 실행의 표본 판정을 구분한다.
그룹마다 손익 중앙값·최악 구간 MDD를 표시하고 서로 다른 그룹 손익을 합산하지 않는다. 자료가 없는 그룹은 적격 0/N/A로 표시한다.
[필수 외곽 요청과 상세 출력 계약](archive/2026-09-22/reports/CR3C2_SYMBOL_WINDOW_VALIDATION.md)을 따른다.

## CR3c1 명시 종목 그룹 개발 검증 (2026-09-16)

기존 single_run/rank_comparison/limited_search 객체의 development_partition에 아래 v3 객체를 사용한다.
기존 dataset/strategy/execution/evaluation/source identity 계약은 그대로다. parent evaluation은 TRAIN/VALIDATION을 선택한다.

```json
{
  "version": "development_partition/v3",
  "fold_name": "train",
  "state_policy": "reset_state_and_cash_per_partition/v1",
  "symbol_partition": {
    "version": "stock_hash_partition/v1",
    "salt": "research-2026",
    "bucket_count": 4,
    "bucket": 0
  }
}
```

이 객체는 전체 요청이 아니라 development_partition 값이다. salt는 1~128자 비어 있지 않은 문자열이다.
bucket_count는 정수 2~20, bucket은 0 이상 bucket_count 미만이며 bool/float는 거절한다.
버전/salt/정규 종목코드 SHA256으로 bucket을 배정한다. 같은 정책의 새 종목은 자동 배정되며 기존 배정은 유지한다.
가격/종목명/순위/성과로 배정을 바꾸지 않는다. count/salt 변경은 새 정책/새 scientific 입력이다.
원래 TOP20·peer 이력/테마는 유지하고 거래 대상만 선택한다. 각 그룹 내부 종목은 한 초기 현금 portfolio를 공유한다.
그룹을 바꾼 별도 실행은 새로운 초기 현금이다. 서로 다른 bucket 결과를 기존 동일 조건 집계로 섞지 않는다.
v2 partition은 symbol_partition 필드를 받지 않는다. --validate-partitions v1/순차 검증 창은 시간 v2 partition을 유지한다.
CR3c2의 v2 batch는 그룹×시간 v3 partition을 실행한다. shared context 개발 검증이며 미사용 최종 종목 holdout 보장이 아니다.
[실행·검증 계약](archive/2026-09-22/reports/CR3C1_FIXED_SYMBOL_PARTITIONS.md)을 따른다.

## CR3b3 순차 개발 검증 파일 (2026-09-16)

새 --validate-partitions는 기존 single_run 객체에 원본 dataset_id/dataset_hash, 시간순 fold_names 2~20개,
운영 max_seconds 1~3600을 묶은 independent_development_validation/v1 JSON을 받는다.
기존 single_run/rank_comparison/limited_search 요청은 바꾸지 않는다. 전체 source는 한 번 읽으며 각 구간은 독립 엔진이다.
완료 캐시는 재사용하고 취소/예산/자원 차단 구간은 명시 재개할 수 있다. failed/cache-invalid/running은 검토 없이 덮어쓰지 않는다.
CR3b4에서 전략 연구 → 여러 구간 순차 검증 → 검증 파일 선택 → 검증 실행을 연결했다.
같은 요청 이어서 실행은 마지막으로 실행한 파싱 snapshot을 재사용한다. 원본 JSON을 바꾸려면 검증 실행을 다시 누른다.
취소/시간 예산 이후 완료 결과는 DB에서 재사용한다. 실패/소유자 확인 상태는 자동 재시도하지 않는다.
250ms마다 진행 파일을 확인하며 모든 미시작 구간도 표시한다. 자동 반복과 앱 재시작 후 batch 복원은 아직 없다.
[필수 JSON 객체와 결과 계약](archive/2026-09-22/reports/CR3B3_SEQUENTIAL_DEVELOPMENT_VALIDATION.md), [화면 실행 계약](archive/2026-09-22/reports/CR3B4_SEQUENTIAL_VALIDATION_DIALOG.md)을 따른다.

```text
python -m kiwoom_monitor.research_process --validate-partitions validation.json --result validation-result.json --cancel validation.cancel
```

옵션/파싱 오류는 종료 2/결과 없음, 정상 관리된 부분 실행은 종료 0/PARTIAL,
사용자 취소는 종료 2/cancelled, 자원 차단은 종료 3/resource_blocked다.
모든 선택 구간은 steps에 남으며 comparison은 확인된 run ID만의 범위다. 일부 comparison COMPLETE는 전체 batch 완료가 아니다.

## CR3b2 독립 결과 비교 파일 (2026-09-16)

전략 연구 → 독립 구간 결과 비교 → 비교 파일 선택 → 결과 비교.
기존 요청 mode는 바꾸지 않았으며 비교에는 다음 작은 별도 JSON을 사용한다.

```json
{
  "version": "independent_development_comparison_request/v1",
  "database": "research.sqlite3",
  "run_ids": ["저장된 TRAIN run ID", "저장된 VALIDATION run ID"]
}
```

database는 이 파일 폴더 기준 상대/절대 경로이며 이미 마이그레이션된 연구 v17~v23 DB를 읽기 전용으로 지원한다. ID는 1~200개/각 256자 이하,
파일은 64 KiB 이하이며 추가 필드는 거절한다. 아직 결과 선택 목록을 자동 생성하는 편의 UI는 없다.

```text
python -m kiwoom_monitor.research_process --compare-runs comparison.json --result comparison-result.json --cancel comparison.cancel
```

CLI 옵션/파싱 오류는 종료 2/결과 파일 없음, 조회 성공은 종료 0/ok, 취소는 2/cancelled,
조회 오류는 1/failed다. 누락/조건 불일치는 comparison 내부 상태로 보존한다.
DB·원본 요청을 result/cancel로 덮어쓰는 경로는 거절한다. UI는 요청을 UUID snapshot으로 고정한다.
시뮬레이션/주문/DB 마이그레이션은 실행하지 않는다. [세부 계약](archive/2026-09-22/reports/CR3B2_INDEPENDENT_COMPARISON_VIEWER.md).

## CR3b1 독립 결과 조회 계약 (2026-09-16)

저장소의 `load_independent_development_comparison((run_id, ...))`로 명시 1~200개 독립 개발 결과를 조회한다.
출력은 `independent_development_comparison/v1`이며 조건 불일치/누락/실패/표본 부족/기간 겹침을 보존한다.
구간 손익 분포이며 연속 계좌 수익률이 아니다. 기존 요청 mode/JSON은 바꾸지 않았다.
프로세스 명령과 화면은 위 CR3b2에 연결했다. [세부 계약](archive/2026-09-22/reports/CR3B1_INDEPENDENT_DEVELOPMENT_COMPARISONS.md)을 따른다.

## CR3a4a/CR3a4b 독립 구간 캠페인 등록 (2026-09-16)

기존 campaign을 만든 뒤 별도 연구 프로세스에서 다음 명령으로 독립 구간 요청을 등록한다.
이 명령은 원본 검증/구간 분리/DB 등록만 하고 trial이나 주문을 실행하지 않는다.

```text
python -m kiwoom_monitor.research_process --request request.json --register-campaign CAMPAIGN_ID --result registration.json
```

request.database가 campaign DB와 같아야 한다. 새 campaign 생성이나 RUNNING 활성화 명령은 아니다.
등록 결과의 job_id/experiment_id는 유효 실행 spec의 ID다. 연구 DB v17에서 추가한 원본/실행 분리 계약에 따라 request_json은 유효 spec,
source_request_json은 원본 spec을 불변으로 저장한다. 예산 revision은 두 조회 명세에 같은 운영값으로 합성한다.
기존 v1 job은 source_request_json 기본 빈값이며 원래 실행 spec으로 동일하게 재구성한다.
원본 request 파일이 없어도 저장된 source spec과 input_path로 재시작한다. 원본 데이터 파일은 필요하다.
원본 loader 검증·source ID/hash 대조·선택 입력 분리 후 등록 spec/ID와 맞아야 실행한다.
자동 source의 기간/scope는 기존 고정 범위다. 새 날짜로 자동 이동하는 기능은 아니다.
새 원본의 개발 입력이 같으면 기존 job에 acceptance만 연결한다. 원래 job의 input_path/spec은 바꾸지 않는다.
화면에서 요청을 선택하고 ‘캠페인에 요청 등록’을 누르면 별도 프로세스가 등록한다.
새 campaign은 paused 상태로 생성·선택 저장되며 등록 뒤 ‘시작 / 재개’를 눌러 실행한다.
등록 요청은 parsed 정책/절대 경로를 UUID별 snapshot에 고정한다. 원본 JSON을 바꾸거나 지워도 진행 중 등록 내용은 바뀌지 않는다.
등록 중 다른 연구 시작/캠페인 설정은 막는다. 취소/창 숨김은 해당 등록 회차의 취소 파일을 보낸다.
등록 commit 뒤 취소가 도착하면 이미 저장한 job은 보존하고 완료됐다고 표시한다.
등록 결과는 프로세스 종료 코드와 campaign/job/experiment ID·저장 원본 evidence가 맞아야 성공으로 표시한다.
결과 파일 유실이나 native 오류가 있어도 paused 선택과 DB job은 보존한다. 복원/재등록으로 확인할 수 있다.
등록 종료 뒤 해당 UUID의 snapshot/결과/취소만 정리한다. 원본 요청·export·campaign 선택·DB 결과는 지우지 않는다.

## CR3a2/CR3a3 독립 개발 실행·유한 탐색 (2026-09-16)

기존 `evaluation` v1 JSON은 그대로 사용하고 `single_run`/`rank_comparison`/`limited_search` 요청에 다음 필드를 추가한다.
명시 `session_profile`이 필수이며 원본의 profile과 일치해야 한다.

```json
"development_partition": {
  "version": "development_partition/v2",
  "fold_name": "train",
  "state_policy": "reset_state_and_cash_per_partition/v1"
}
```

`fold_name`은 evaluation 안의 TRAIN/VALIDATION 이름이어야 한다. OOS/없는 이름/미등록 version·state 정책은 거부한다.
최종 접근 시각이 이미 적힌 평가 정책을 미사용 구간으로 재분류하지 않는다. campaign은 위 별도 등록 경로를 사용한다.
UI의 기본 요청은 기존 v1 동작을 유지한다.

limited_search의 `search.dataset_id`/`dataset_hash`는 **원본 export/bundle의 식별값**이다.
parser가 고정하는 research_context에는 원본 evaluation과 development_partition 정책이 들어간다.
직접 research_context를 넣는다면 현재 코드로 생성한 context와 정확히 일치해야 한다.
worker는 원본 파일/hash/요청 ID를 검증한 뒤 선택 구간의 ID/hash·단일 평가로 **유효 search spec**을 만든다.
DB experiment/job/trial/card는 유효 spec을 기준으로 저장한다. 반환 experiment_id/job_id는 제출 spec의 ID와 다르다.
유효 spec을 원본 dataset 경로의 새 제출 요청으로 쓰지 않는다. 원본 요청을 보관하고 다시 실행한다.
완료 cache를 쓰기 전에도 매번 원본 loader 검증을 수행한다. 원본 손상/틀린 ID는 기존 cache로 우회하지 않는다.
같은 코드/개발 자료/정책에서 OOS 내용·전체 quality·원본 ID만 바뀌어도 개발 cache는 유지된다.
개발 payload/seed/theme/정책 변경은 revision ID가 같아도 선택 dataset_id를 바꾼다.
기본/no-trade/ablation/cost-stress 모두 선택 입력과 초기 현금 정책을 사용한다.
campaign은 원본 spec과 유효 spec을 따로 보존하는 검증된 등록 경로만 사용한다.

CLI는 기존 전략·비용 옵션에 `--evaluation-spec <파일> --development-partition train --session-profile krx-regular/v1`을 추가한다.
선택 구간의 시작 전 warmup을 읽되 주문·후보를 만들지 않는다. 구간마다 초기 현금/빈 상태에서 시작하고 내부 날짜는 이어간다.
직전 순위/테마는 원본 시각과 ID를 보존한 초기 상태다. 미래 테마나 시각이 없는 테마를 과거로 소급하지 않는다.
경계 미청산은 종료 시각에 censor하며 독립 fold의 손익/계좌 상태를 연속 포트폴리오처럼 합치지 않는다.

결과 manifest의 development_partition과 연구 DB run.input_manifest에 선택 정책·범위를 남긴다.
원본 전체 파일은 기존 loader가 검증하고, 실행에는 RAM에서 분리한 입력을 사용한다. 원본 hash 오류는 우회하지 않는다.
NAS 구간별 다운로드/저장 용량 감소 기능은 아니다. 품질 PASS도 전체 warmup 연속성/저장 coverage 보증은 아니다.
기존 동결 요청은 자동 교체하지 않으며 새 구현 실행은 현재 코드로 만든 요청을 사용한다. NAS 재빌드는 필요 없다.

## CR3a1 개발 후보 선택 근거 (2026-09-16)

탐색 후보의 적격 여부·사유·성과는 TRAIN/VALIDATION fold의 정보로 계산한다.
OOS의 열람/성과/실패와 전체 보고서의 집계 상태/사유를 후보 선택에 전달하지 않는다.
후보의 report_status는 개발 fold 집계이며, 원본 전체 보고서는 별도로 보존한다.
개발 무거래 NOT_APPLICABLE/자료 부족 INELIGIBLE은 구별한다. 미등록 개발 상태도 부적격으로 남긴다.
이번 변경은 요청 JSON/기존 v1 split을 변경하거나 독립 v2 실행을 켜지 않는다.
평가 코드 hash가 달라졌으므로 이전 동결 요청을 새 구현으로 자동 바꾸지 않는다. 기존 결과는 보존하며
새 실행은 현재 코드 기준의 새 요청을 사용한다. 독립 partition 입력은 CR3a2 이후, 최종 접근·실행 원장은 CR3d와 위 전용 요청에서 지원한다.

## CR2c3c2 완성 자료 등록 복구 (2026-09-16)

NAS 자동 source는 같은 동결 범위의 기존 완성 자료부터 등록하고, 대기열 여유가 있을 때 NAS를 조회한다.
저장 후 등록 전에 중단된 자료도 자동 복구한다. 용량 상한에 걸리면 새 준비만 기다리며 기존 파일은 보존한다.
NAS 접속 실패가 있어도 그 전에 성공한 등록은 유지한다. 준비 signature는 자료 등록 이후에만 확인 처리한다.
완성 자료/다른 범위 자료를 자동 삭제하거나 이동하지 않는다. 참조는 기존 jobs/acceptances에 남긴다.
이미 대기열이 가득 찬 source는 파일/연결 설정 조회 없이 대기하며, 여유가 생긴 다음 scan에서 복구한다.
CR2c3c2는 당시 v16에 새 마이그레이션을 더하지 않고 기존 설정·요청·원장을 재사용했다. 현재 연구 DB 버전은 v23이다.

## CR2c3c1 오래된 미완성 임시 자료 정리 (2026-09-16)

NAS 자동 준비가 켜진 source는 준비 전에 현재 상위 폴더의 임시 자료를 원장과 대조한다.
24시간 이상 지난 미완성 임시 폴더만 대상이며 별도 수동 경로를 지정하거나 사용자 파일을 삭제하지 않는다.
완성 manifest/연구 참조/알 수 없는 파일/링크/operation ID 없는 기존 표시가 있으면 보존한다.
일시정지/worker 종료 경계와 현재 root 설정을 지킨다. source 폴더를 바꾸면 이전 폴더를 자동 정리하지 않는다.
부분 정리/잠김은 v16에서 도입한 정리 원장에서 다시 처리한다. 완성 게시 후 미등록 자료의 복구는 위 CR2c3c2가 담당하며 완성 자료는 삭제하지 않는다.

## CR2c3b 새 자료 폴더 용량 상한 (2026-09-16)

지속 연구를 일시정지하고 worker 종료 후 **새 자료 폴더**에서 상한을 정한다.
0은 무제한이며 양수는 GB(1024^3 byte)다. 범위는 선택한 상위 폴더의 파일이며
폴더 밖 연구 DB/보고서나 NAS 원시 DB의 총 용량을 제어하는 옵션은 아니다.
상한에 도달하면 새 자료 준비를 대기하고 기존 자료/연구 결과를 삭제하지 않는다.
상한을 올리고 재개하면 다시 확인한다. 기본값 0은 기존 동작을 유지한다.
같은 capped 폴더를 여러 NAS source가 쓰면 root/상한을 일치시켜야 한다.
상한이 있는 폴더를 서로 parent/child로 겹치게 설정하지 않는다. 다른 source를 끈 뒤 설정을 바꿀 수 있다.
폴더 cap/준비 원장은 v15, 미완성 정리 원장은 v16에서 도입했다. 현재 PC 연구 DB는 v23이다. CR2c3c1은 미완성 임시 정리, CR2c3c2는 완성 게시 후 미등록 자료의 복구이며 완성 자료 삭제 기능은 아니다.

## CR2c2b NAS 자동 준비 (2026-09-16)

앱 NAS 연결 설정을 저장한 상태에서 지속 연구를 일시정지하고 작업자 종료 후 **새 자료 폴더**를 연다.
기준 실험·저장할 상위 폴더와 **자동 등록 켜기**, **NAS에서 새 자료 자동 준비**를 저장하고 시작/재개한다.
기존 source는 NAS 자동 준비 OFF다. 설정 화면은 API를 요청하지 않고 worker가 기존 접속 설정을 읽는다.
연구 DB에는 설정 파일 경로만 저장하며 접속 토큰을 복사하지 않는다. 직접 모드/설정 부재 때 키움 TR로 우회하지 않는다.

같은 기간·범위의 관측/테마 signature를 확인하고 바뀐 자료만 전체 준비한다.
새 완성 폴더 게시 뒤 자동 등록하므로 폴더를 매번 수동 생성할 필요가 없다.
일별 묶음도 기존 날짜들을 유지한다. 새 거래일 추가나 평가 기간 이동은 이 기능이 아니다.
백로그가 꽉 차면 NAS 다운로드를 기다리고 NAS 오류는 기존 source 재시도 정책을 사용한다.
취소/부분 응답/byte cap 초과는 완성 폴더를 게시하지 않는다. signature ack는 등록 경계 뒤에만 한다.
NAS 자동 준비 설정은 v14에서 도입했으며 현재 연구 DB는 v23이다. CR2c3a는 새 자동 생성 파일의 표시/읽기 전용 용량 진단을 제공한다.
기존 무표시 자료를 자동 채택하지 않고 완료된 연구 입력도 보호한다. 미완성 임시 파일만 위 CR2c3c1에서 정리한다.
폴더 cap/준비 원장은 위 CR2c3b, 완성 게시 후 등록 복구는 CR2c3c2에 연결되어 있다. 완성 자료의 이동·압축·자동 삭제, 외부 매매일지 참조 전수 조사, 새 날짜 확장과 실제 NAS 장시간 운용 검증은 남아 있다.

## CR2c2a 새 자료 폴더 (2026-09-16)

지속 연구를 **일시정지**하고 작업자 종료 후 **새 자료 폴더**를 연다.
기준 실험과 준비된 export 폴더들이 들어 있는 상위 폴더를 선택하고 **자동 등록 켜기**를 저장한다.
**시작 / 재개**하면 worker가 처음에는 즉시, 이후 정상 확인은 60초마다 수행한다.
끄려면 같은 설정에서 체크를 해제한다. 실험마다 source 하나이며 폴더 변경은 기존 처리 이력을 보존한다.

직접 하위의 완성 일별 export/bundle만 대상이다. 기준과 기간·종목 범위·kinds·세션 계약이 같아야 한다.
동일 근거, watermark만 달라진 입력, 무관 kind/비KRX 봉 변경에는 새 실험을 만들지 않는다.
전략/평가/파라미터/seed는 기준 실험을 유지하며 현재 운영 예산을 새 job에 복사한다. 기존 job은 바꾸지 않는다.
기존 자료를 덮어쓰지 않고 새 완성 폴더로 준비해야 한다. 승인 경로의 manifest 변경은 오류로 표시한다.
빈 폴더·백로그 포화는 정상 대기이고 누락/잘린 입력/자원 초과는 source별 재시도·반복 실패 상한을 적용한다.
설정 화면에 저장된 오류가 남는다. 원인 수정 후 다시 설정을 저장하면 source 재시도를 초기화한다.
오래된 implementation hash의 기준 실험은 새 요청을 명시 등록해야 하며 자동 우회하지 않는다.

CR2c2a 자체는 이미 준비된 파일의 자동 등록이며 위 CR2c2b opt-in으로 NAS 파일 준비도 연결할 수 있다.
이 discovery는 v13에서 도입한 같은 고정 범위의 입력 등록이다. 현재 연구 DB는 v23이며 새 거래일 추가·평가 기간 자동 이동은 아직 지원하지 않는다.

## CR2c1 작업자 복구 (2026-09-16)

작업자가 예상하지 못하게 종료되면 DB에 실패 이력과 다음 재시도 시각을 저장한다.
기본 정책은 첫 실패 30초, 두 번째 60초 대기이며 연속 3회 실패하면 자동 재시도를 멈춘다.
앱을 다시 켜도 이 제한은 유지한다. 원인 확인 후 **시작 / 재개**를 누르면 명시 재시도한다.
60초 동안 작업자가 생존 신호를 정상 갱신하면 연속 실패 횟수를 초기화한다.
창 숨김/일시정지/앱 종료는 실패가 아니다. 원하는 RUNNING 의도와 작업자의 실제 수명은 구별한다.
다른 실행 주체의 live lease가 있으면 중복 시작하지 않는다. lease 만료는 한 번 실패로 기록한 뒤 대기한다.

일반 worker CLI는 직접 claim한다. GUI는 먼저 claim한 뒤 내부 인수
`--worker-token TOKEN --worker-generation GENERATION`을 함께 전달한다. 종료 응답은 세대별로 멱등 처리한다.
소유권을 잃은 worker는 새 search job 실행 시작과 결과 commit을 할 수 없다. worker/job 실패 횟수는 독립이다.
작업자 복구 원장은 v12에서 도입했다. 현재 v23은 새 자료 자동 등록·NAS 준비·폴더 cap·제한 staging 정리를 포함한다. 실제 24시간 운용 검증은 별도 미완료다.

## CR2b2 예산 확대 / 재시도 (2026-09-16)

지속 연구를 **일시정지**하고 worker가 끝난 뒤 **실험 예산 / 재시도**를 연다.
등록된 실험을 골라 누적 실험 횟수 한도, 한 회차 시간(초), 메모리(MiB), CPU 사용 목표를 바꾸고
**예산 저장**을 누른다. 횟수 한도는 줄일 수 없다. 예산 저장 후 **시작 / 재개**로 이어서 실행한다.
2개 완료 뒤 한도를 4개로 늘리면 기존 2개는 보존하고 새 2개만 실행한다.
전략/기간/자료/생성 조합 변경은 예산 편집이 아니라 새 연구 요청이다.
자원 차단/실패 실험은 **차단 / 실패 실험 다시 대기**로 명시 retry할 수 있다.
단순 예산 저장/재시도만으로 worker를 자동 시작하지 않는다.

연구 DB v11은 예산 revision을 별도 보존하며 같은 과학 identity/원래 요청 파일을 유지한다.
일반 유한 완료 캐시는 그대로이고 유효한 캠페인만 추가 실험을 연다.
입력 파일·implementation hash 불일치는 예산 편집으로 우회하지 않는다.
당시 구현·검증 근거는 [CR2b2 보고서](archive/2026-09-22/reports/CR2B2_CAMPAIGN_OPERATING_BUDGETS.md)를 따른다.

## CR2b1 캠페인 실행 (2026-09-16)

기존 `limited_search` JSON을 선택하고 **캠페인에 요청 등록**을 누른다. 최초 등록은 PAUSED다.
**시작 / 재개**, **일시정지**, **중지**는 DB에 실행 의도를 저장한다. 여러 요청은 동일한 연구 DB와 결과
폴더를 사용해야 한다. 수정한 외부 파일은 기존 등록을 덮지 않는다. 새 가설은 별도 spec으로 등록한다.
수동 최종평가 접근 기록이 있는 요청은 자동 캠페인에 등록할 수 없으며 기존 유한 실행으로만 평가한다.
앱을 다시 열면 마지막 선택 캠페인의 RUNNING 의도를 복원한다. 기존 JSON을 삭제해도 재개할 수 있다.
창을 숨기거나 앱을 종료하면 의도를 유지한 채 worker를 멈춘다. 창을 다시 열면 RUNNING 캠페인을 재개한다.
PC별 `research_campaign_selection.json`은 DB/결과 폴더와 campaign_id의 위치 index로 NAS 공통 설정이 아니다.

전략 연구 창의 `자동 가설 설정 / 현황`은 선택한 campaign이 `PAUSED`일 때만 편집할 수 있다. 기존 기준
실험의 Family와 등록 searchable parameter 하나, 쉼표로 구분한 정수 허용값, seed, 완료 부모 1개당 생성 상한,
campaign 전체 가설 상한을 새 policy revision으로 저장한다. 해당 Family에 등록 가설이 없으면 기준 실험의
정규화된 전체 전략 설정으로 기준/첫 이웃을 만든다. 표의 개발 근거는 TRAIN/VALIDATION projection ID이며
FINAL/OOS 결과는 자동 생성 입력이 아니다. 실행 중 설정을 바꾸지 않고 일시정지 후 revision을 올린 뒤 재개한다.

별도 worker CLI도 지원한다. 새 JSON mode를 추가하지 않는다.

```text
python -m kiwoom_monitor.research_process --campaign CAMPAIGN_ID --database PATH_TO_DB --runs-dir OUTPUT_DIR --result RESULT_JSON --cancel CANCEL_MARKER
```

worker는 DB 작업을 순서대로 실행하고 대기 중에도 새 등록 작업을 확인한다. `--cancel`은 worker만 종료하며
RUNNING 의도를 STOPPED로 바꾸지 않는다. DB의 PAUSED/STOPPED는 현재 trial 검사에서 중단되고 완료된 결과는 유지된다.
실험 횟수 한도 도달은 조합 전체 검증 완료와 다르다. 운영 예산 확대는 위 CR2b2를 따른다.
같은 고정 범위의 새 자료 등록/준비는 CR2c2, 등록 범위의 자동 가설 생성·예약은 CR4에 연결되어 있다. 자동 최종평가와 연구 worker의 주문 실행은 지원하지 않는다.
자세한 계약과 제한은 [CR2b1 보고서](archive/2026-09-22/reports/CR2B1_CAMPAIGN_WORKER_AND_CONTROLS.md)를 따른다.

## CR2a 캠페인 원장 (2026-09-16)

PC 연구 저장소 v10은 캠페인 설정 revision, desired_state, 동결 유한 ExperimentSpec/입력 경로,
예약 cycle을 보존한다. 원장 구현 뒤 위 CR2b1에서 worker/화면을 연결했다.
새 `mode: campaign`은 지원하지 않는다. 기존 유한 요청 JSON을 등록한다.
생성 기본 의도는 PAUSED이며 원장 등록만으로 시뮬레이션을 실행하지 않는다. 자동 가설은 기본 OFF이고 위 설정 화면의 명시적 campaign 정책으로 활성화한다. 자동 최종평가는 지원하지 않는다.
원장 API와 구현 계약은 [CR2a 보고서](archive/2026-09-22/reports/CR2A_PERSISTENT_CAMPAIGN_LEDGER.md)를 따른다.

## CR1a 날짜별 자료 준비 (2026-09-16)

`scripts/export_research_dataset.py --dates YYYY-MM-DD,...`는 명시한 KST 날짜의
00:00~다음 00:00 범위를 기존 NAS 24시간 API로 각각 추출한다.
`--session-profile`은 필수이며 `--start/--end`와 함께 쓰지 않는다.
접속 토큰은 기존 `MONITOR_SERVER_ACCESS_TOKEN` 환경변수에서 읽을 수 있다.

```text
python scripts/export_research_dataset.py --server-url https://NAS주소:8443 --dates 2026-09-14,2026-09-15 --kinds ranking,top20_membership,minute_bar --session-profile krx-regular/v1 --output datasets/two-days
```

출력은 `manifest.json` bundle index와 `days/YYYY-MM-DD`의 기존 일별 export다.
동일 경로를 재시도하려면 `--reuse-days`를 명시한다. 완료·검증된 날짜 파일은
API를 다시 호출하지 않고 재사용하며 다른 조회 조건/날짜로 기존 묶음을 덮어쓰지 않는다.
불완전한 날짜 파일이나 잘린 테마 이력은 성공으로 처리하지 않는다.
날짜 목록은 거래소 휴장 달력을 자동 생성한 것이 아니며 빈 날짜의 수집 품질은 unknown으로 남긴다.

**CR1b 실행 연결:** 기존 연구 JSON의 `dataset`을 묶음 디렉터리로 지정하고,
`session_profile`을 일별 export와 동일하게 명시하면 앱의 별도 연구 프로세스와
`scripts/run_research.py --dataset ... --session-profile ...`에서 실행할 수 있다.
나머지 전략·비용·평가 필드는 기존 요청과 같다. profile 누락/불일치는 DB를 만들기 전에 거절한다.
하루 묶음은 기존 하루 export와 같은 identity/논리 결과를 유지한다.
다기간 묶음의 검색 요청은 `load_research_input(path, session_profile=...)`가 검증해 돌려준
manifest의 `dataset_id`와 `revision_ids_hash`로 고정한다. index의 bundle_id만 dataset_hash로 넣지 않는다.
여러 날짜를 한 번에 실행하므로 현금과 보유가 이어지며 매일 임의 청산하지 않는다.
미체결 주문은 기존 명시 profile의 세션 경계를 넘어 체결하지 않는다. Factor lookback도 기존 세션 정책을 따른다.
현재 지원은 기존 KRX 확정/완전 분봉과 당시 TOP20/테마 기반이다. 이 연결이 NXT/호가/초자료 전략을 추가하지 않는다.
CR1b 자원 정책과 CR2의 영속 campaign·작업자 복구가 연결되어 있다. 실제 PC 24시간 운전과 NAS 전체 규모 처리량 검증은 별도다.

단일 실행/비교 요청에는 선택적 운영 설정 `"resource_budget": {"memory_mb": 512, "cpu_duty_percent": 50}`를
최상위에 둔다. 제한 검색에서는 기존 `search.resource_budget`에 같은 값을 지정한다. 기본값은 512MiB/50%이며
메모리는 128~4096MiB, CPU 목표는 10~100%다. CLI는 `--memory-mb`, `--cpu-duty-percent`를 사용한다.
이 예산은 과학 identity에 넣지 않는다. 입력 크기×8 + 실제 RSS로 먼저 검사하고 로딩/실행 중에도 RSS를 검사한다.
50ms 단위의 협력적 CPU 양보이며 OS 강제 quota는 아니다. 이미 IO로 기다린 시간은 추가 휴식에서 뺀다.
자원 부족은 `status: resource_blocked` 또는 검색 `job_status: resource_blocked`와 이유를 반환한다.
검색 DB는 기존 cancelled job + INTERRUPTED attempt/error를 사용하며 완료 trial/실패 전략 결과를 만들지 않는다.
GUI는 자원 차단 후 자동 재개를 반복하지 않는다. 예산을 검토해 수동 재실행하면 같은 실험의 미완료 trial을 재시도한다.
입력 preflight 실패는 출력 DB를 만들지 않는다. CLI/연구 프로세스의 직접 자원 차단 종료 코드는 3이다.
전체 자료 읽기와 결과 보관은 유지하므로 실제 NAS의 큰 기간은 사전 차단될 수 있다. 일별 export 크기와
메모리 설정을 먼저 확인한다. [계측 범위와 수치](archive/2026-09-22/reports/CR1B_RESOURCE_LIMITS_AND_BENCHMARK.md)를 참고한다.

연구 데이터 export에는 `observations.jsonl`과 함께 당시 가용 테마 구성을 담은
`theme_snapshots.jsonl`이 포함될 수 있다. 두 파일은 manifest의 hash와 개수로
검증된다. 이전 export처럼 테마 sidecar가 없는 데이터도 읽을 수 있지만, 이 경우
시장 유형은 테마 근거 부족으로 `UNKNOWN`일 수 있다.

연구 결과의 `market_regime`은 run 종료 시점까지 성숙한 3분 돌파 결과만 사용한
관측용 Factor다. 기존 돌파 전략의 진입 여부를 자동으로 바꾸지 않는다. 유형을
필터로 비교할 때는 별도의 버전 있는 전략 프로파일과 D7 비교 run을 사용한다.

연구 요청은 버전 있는 `session_profile`을 명시한다. 등록값은 `krx-regular/v1`
(09:00~15:30), `krx-after/v1`(시행일 이후 16:00~20:00),
`krx-full-day/v1`(두 구간의 합집합)이다. `krx-full-day/v1`도 15:30~16:00
장후종가 주문접수·고정가 체결 구간은 포함하지 않으며 16:00에서 Factor lookback과
pending 체결 연속성을 새로 시작한다. 분봉만으로 그 구간의 호가 우선순위와 체결
순서를 재현할 수 없기 때문이다. profile 문서는 시간표 version 정책, 허용 구간,
Factor reset, 다음 봉, 단일가, horizon 정책과 함께 RunSpec·검색 evidence·forward
profile ID 및 hash에 들어간다.

이 필드가 없던 기존 요청은 호환 경계로 계속 `krx-regular/v1` reader를 사용하고
S5 이전 구현 hash와 RunSpec 형태를 보존한다. 따라서 완료된 기존 run/manifest/hash를
새 profile run으로 덮어쓰지 않는다. 새 요청과 새 D4 monitor는 profile을 명시해야
하며, 등록되지 않은 venue/profile/phase는 거부하거나 `UNSUPPORTED`/`UNKNOWN`으로
남긴다.

앱의 `전략 연구` 창은 모든 조건을 한 번에 고정한 JSON 파일을 읽는다. 화면에서 파일을 선택하면 경로, 전략 버전, 비용 출처·유효기간, 시간순 평가 구간을 먼저 검사하고 별도 프로세스에서 실행한다. 아래 숫자는 형식 설명용 예시이며 운영 추천값이 아니다. 실제 연구에서는 사용자가 검토한 값과 해당 기간에 맞는 비용 근거로 바꿔야 한다.

```json
{
  "mode": "rank_comparison",
  "family": "krx_bar_close_breakout/v1",
  "session_profile": "krx-regular/v1",
  "dataset": "datasets/frozen-export",
  "database": "output/research.sqlite3",
  "runs_dir": "output/runs",
  "strategy": {
    "strategy_version": "v1",
    "rolling_factor_version": "v1",
    "rank_factor_version": "v1",
    "lookback_bars": 5,
    "buffer_bps": 0,
    "rank_persistence_enabled": true,
    "rank_persistence_required": true,
    "rank_top_k": 20,
    "rank_window_seconds": 300,
    "rank_max_gap_seconds": 60,
    "rank_min_residency_seconds": 60,
    "stop_loss_bps": 300,
    "target_bps": 500,
    "max_hold_minutes": 10,
    "quantity": 1,
    "capital_won": 1000000,
    "signal_valid_seconds": 60,
    "cooldown_seconds": 30
  },
  "execution": {
    "version": "next_tradable_bar_open/v1",
    "same_bar_path_version": "conservative_with_optimistic_bound/v1",
    "initial_cash_won": 1000000,
    "cost_model": {
      "version": "fixed_bps/v1",
      "commission_bps": 0,
      "sell_tax_bps": 0,
      "slippage_bps": 0,
      "rate_basis": "model_estimate",
      "source": "여기에 확인한 비용 근거를 기록",
      "valid_from": "2026-09-01T00:00:00+09:00",
      "valid_to": "2026-10-01T00:00:00+09:00"
    }
  },
  "evaluation": {
    "version": "chronological_holdout/v1",
    "folds": [
      {
        "name": "train",
        "role": "TRAIN",
        "start": "2026-09-01T08:00:00+09:00",
        "end": "2026-09-08T20:00:00+09:00"
      },
      {
        "name": "validation",
        "role": "VALIDATION",
        "start": "2026-09-09T08:00:00+09:00",
        "end": "2026-09-11T20:00:00+09:00"
      },
      {
        "name": "final",
        "role": "OOS",
        "start": "2026-09-12T08:00:00+09:00",
        "end": "2026-09-13T20:00:00+09:00"
      }
    ],
    "warmup_seconds": 3600,
    "gap_seconds": 43200,
    "purge_seconds": 600,
    "minimum_closed_trades": 0,
    "minimum_active_days": 0,
    "fold_state_policy": "continuous_state_and_cash/v1",
    "period_end_position_policy": "censor_open_position/v1",
    "fit_policy": "no_fitted_parameters/v1",
    "final_holdout_accessed_at": "",
    "final_holdout_access_reason": ""
  }
}
```

상대경로는 요청 JSON 파일이 있는 폴더를 기준으로 해석한다. `database`와 `runs_dir`은 동결 dataset 폴더 밖이어야 한다. `final_holdout_accessed_at`을 비워두면 OOS 수치는 `SEALED`로 남는다. 최종 OOS를 실제로 열 때만 구간 종료 이후의 접근 시각과 접근 이유를 함께 기록해 새 run으로 실행한다.

`mode`는 다음 세 값을 지원한다.

- `single_run`: 설정한 전략 하나의 시간순 보고서를 만든다.
- `rank_comparison`: 동일 조건에서 관심순위 Factor를 끈 기준선과 설정한 전략을 모두 실행해 비교한다. 이때 `rank_persistence_enabled`가 `true`여야 한다.
- `limited_search`: 아래 `search` 객체를 추가해 등록된 값만 제한 탐색한다. 기존 `strategy`는 기준값이며 운영 설정을 바꾸지 않는다.

```json
"search": {
  "version": "limited_search/v2",
  "hypothesis_refs": ["검토한 가설 ID"],
  "dataset_id": "manifest의 dataset_id",
  "dataset_hash": "manifest의 revision_ids_hash",
  "family_allowlist": ["krx_bar_close_breakout/v1"],
  "factor_allowlist": ["rolling_high_breakout/v1", "rank_persistence/v1"],
  "parameter_space": {"lookback_bars": [3, 5], "target_bps": [300, 500]},
  "objective": {"net_pnl_won": "maximize", "max_drawdown_won": "minimize"},
  "constraints": {"minimum_closed_trades": 20, "maximum_drawdown_won": 100000},
  "split_version": "chronological_holdout/v1",
  "max_trials": 10,
  "max_seconds": 1800,
  "seed": 7,
  "generation_mode": "constrained_auto",
  "execution_environment": "historical_simulation",
  "resource_budget": {
    "max_concurrent_trials": 1,
    "cpu_duty_percent": 50,
    "max_generated_candidates": 100,
    "max_retained_jobs": 50,
    "memory_mb": 512
  },
  "include_no_trade_baseline": true,
  "ablations": ["rank_persistence"],
  "cost_stress_multipliers_ppm": [1500000, 2000000]
}
```

`limited_search/v2`는 기준전략 전체, 실행·비용 모델, 실제 평가 fold와 구현 hash를 내부 `research_context`로 고정한다. 이 값은 위 JSON에 직접 작성하지 않으며 요청 loader가 나머지 잠긴 설정에서 만들고, 임의로 전달한 값이 다르면 거부한다. CPU·시간·메모리 예산 변경은 과학적 실험 ID를 바꾸지 않는다. v1 요청과 결과는 보존되지만 v2 완료 캐시로 재사용하지 않는다.

기본전략과 무거래 기준선을 먼저 포함하고 비용 스트레스·Factor 제거는 명시한 경우에 추가한다. 후보 점수는 TRAIN/VALIDATION fold만 합산하며 OOS는 열려 있어도 선택에 사용하지 않는다. 후보 수는 전체 조합을 만들기 전에 제한한다. 완료·실패·부적격 결과만 `max_trials`를 소비한다. 사용자 중지나 프로세스 손실은 별도 attempt의 `INTERRUPTED`로 남아 같은 trial을 처음부터 다시 실행할 수 있다. 시간 예산은 진행 중 trial을 자르지 않고 다음 trial 시작만 막는다.

`family`를 생략하면 기존 `krx_bar_close_breakout/v1`을 사용한다. 두 번째 등록값 `krx_pullback_reacceleration/v1`은 전략 객체에서 `rolling_factor_version`과 `buffer_bps` 대신 `pullback_factor_version`, `minimum_pullback_bps`, `minimum_reacceleration_bps`를 받는다. 이 Family는 고점 뒤 눌림이 있었고 현재 완료봉 종가가 직전 완료봉보다 설정값 이상 재가속할 때 후보를 만든다.

`generation_mode`는 `manual`, `constrained_auto`, `free_research`, `one_parameter_at_a_time` 중 하나다. `one_parameter_at_a_time`은 기준 전략에서 한 trial마다 파라미터 하나만 바꿔 어떤 값 때문에 결과가 달라졌는지 확인한다. 모든 모드는 등록된 Family·Factor·파라미터와 유한 예산 안에서만 실행되며 임의 Python 코드를 받지 않는다. v2 `execution_environment`는 `historical_simulation`으로 고정하며 `live`는 연구 요청으로 실행할 수 없다. job 보존 한도는 queued/running 작업에 적용하고 완료 근거는 자동 삭제하지 않는다.

`historical_simulation`은 `PaperExecutionEngine`의 역사자료 모의 체결이다. 실시간 shadow/키움 모의계좌 실행 선택값은 아니다. 이전 v1의 `replay`·`simulation` 표기는 기존 자료에서만 보존하며 v2 결과로 재사용하지 않는다. 결과 기반 등록 가설 생성·Family 순환은 CR4, 다기간 bundle은 CR1, 영속 campaign은 CR2에서 구현됐다. 현재 지원 범위와 운영 확인 상태는 [현재 상태](CURRENT_STATUS.md)를 따른다.

`resource_budget.cpu_duty_percent`는 10~100이며 기본 50ms 계산 batch마다 CPU 시간과 IO 대기를 고려해 협력적으로 양보한다. `memory_mb`는 입력 크기×8과 실제 RSS의 preflight 및 로딩/실행 중 RSS 검사에 사용한다. 이는 OS 강제 quota나 순간 할당 상한 보장이 아니다. 완료 trial 경계의 재개·임대 heartbeat는 유지하며 자원 초과는 과학적 실패 결과가 아닌 재시도 가능한 운영 차단으로 남긴다.

현재 모의 체결은 같은 profile 연속 구간의 바로 다음 1분봉이 연속매매 phase일 때만
그 시가에서 전량 체결되는 모델이다. 세션 공백, 거래일 변경, 고정가·단일가 봉을
건너뛰어 체결하지 않는다. 단일가/VI 호가 근거가 없는 봉의 next-bar-open과 가상
손절·목표 체결은 `UNSUPPORTED`, horizon 자료가 wall-clock상 연속하지 않으면
실행 종료 시 `CENSORED`(진행 중이면 `PENDING`)로 남긴다. VI 주문 가능 여부와
부분체결은 아직 재현하지 않으며 결과 문서의 `limitations`에 남는다.

# CR3c2 — 종목 그룹×개발 시간 구간 순차 검증

2026-09-16. **고정 전략의 그룹×시간 순차 요청/CLI·캐시/취소 재개·그룹별 비교 완료**.
기존 실행기를 확장하며 DB v17/NAS/API/실제 계좌·주문을 변경하지 않는다. 누적 PC 소스이고 NAS 재빌드 불필요.

## 목적과 최소 변경

사용자가 그룹별 요청 파일을 반복 실행하지 않아도 한 고정 전략을 여러 종목 그룹과 시간 구간에서 실행한다.
기존 DevelopmentValidationRequest/execute_development_validation을 확장한다. 별도 실행기/Manager/Service/테이블은 없다.
source는 한 번 검증·로드하고 각 step마다 기존 독립 v3 입력/새 engine을 만든다. 복사본은 한 번에 하나만 유지한다.
실행 경로는 기존 research_process → projection/identity → claim → runner → repository/집계로 유지한다.
그룹 선택/그룹별 결과 계약만 기존 process 모듈에 추가한다. policy/runner/집계 파일 수를 늘리지 않는다.

## 입력 계약

--validate-partitions는 기존 independent_development_validation/v1과 새 v2를 받는다.
외곽 v2 필드는 아래 일곱 개만이다. request의 빈 객체는 설명용 자리이며 실제로는 기존 single_run 전체 객체를 넣어야 한다.

```json
{
  "version": "independent_development_validation/v2",
  "request": {},
  "dataset_id": "검증된 원본 manifest.dataset_id",
  "dataset_hash": "검증된 원본 manifest.revision_ids_hash",
  "fold_names": ["train", "validation"],
  "max_seconds": 60,
  "symbol_partition": {
    "version": "stock_hash_partition/v1",
    "salt": "research-2026",
    "bucket_count": 4,
    "buckets": [0, 1]
  }
}
```

- request: 고정 single_run/전체 evaluation/명시 session profile. 이미 선택된 development_partition/다른 mode 거절.
  기존 strategy/execution/비용/자원 예산/입력 경로 계약을 재사용한다.
- dataset_id/hash: full source binding. DB를 열기 전 실제 검증된 source와 일치해야 한다. cache로 우회하지 않는다.
- fold_names: 시간순/중복 없는 TRAIN 또는 VALIDATION 1~20개. OOS/누락/노출된 final 정책 거절.
- symbol_partition: 위 네 필드만. 공통 stock_hash_partition/v1/salt/bucket_count와 선택 buckets를 고정한다.
  buckets는 오름차순/중복 없는 2~20개 정수이며 각각 0~bucket_count-1. bool/float/범위 밖 값 거절.
  기존 salt/count 제약을 그대로 사용한다. 미래 성과나 목록 크기로 배정하지 않는다.
- 전체 folds×buckets는 200개 이하. 기존 비교 scope 상한과 운영 크기에 맞추며 200 경계/초과 거절을 검사한다.
- max_seconds: 정수 1~3600, 운영 예산. 파일 1 MiB 이하/UTF-8 BOM 허용/경로는 바깥 JSON 폴더 기준으로 정규화한다.

파싱 결과의 symbol_partitions는 immutable tuple이며 모든 bucket이 같은 version/salt/count여야 한다.
DevelopmentValidationRequest.to_dict는 v2 header와 절대 경로 snapshot을 재현한다.
v1의 두 개 이상 개발 fold 제약/필드/직렬화·출력 모양은 유지한다. v1에 symbol_partition을 붙이면 거절한다.

```text
python -m kiwoom_monitor.research_process --validate-partitions group-validation.json --result group-result.json --cancel group.cancel
```

기존 CLI 상호 배타적 source/옵션·원본/DB/frozen dataset 결과 경로 보호를 유지한다.
유효 종료 0=ok, 2=cancelled, 3=resource_blocked이며 관리된 실패/대기는 0/PARTIAL과 reason/steps다.
옵션/파싱 오류 2/결과 없음, source 또는 미관리 오류 1/실패 envelope도 기존 계약이다.

## 실행·캐시·취소 계약

1. 한 source를 한 번 검증/로드한다. 시간 fold 먼저/bucket 다음으로 실행한다.
   예: train/0 → train/1 → validation/0 → validation/1.
2. 각 step은 고정 development_partition/v3의 동일 시간 창/전체 as-of TOP20/peer history/테마와 선택 bucket이다.
   그룹 안의 종목은 한 portfolio를 공유하고 별도 step은 초기 현금/빈 상태로 시작한다.
   warmup 진입 금지/날짜 내부 상태 연속/경계 censor/엄격 KRX 입력은 기존 계약이다.
3. scoped scientific identity/atomic claim/sole owner terminal publication은 기존 CR3b3를 재사용한다.
   운영 요청 버전이 v2여도 execution_scope=independent_development_validation/v1이다.
   과학 입력은 per-step v3 policy에 고정되며 추가 bucket 선택만으로 기존 bucket run ID를 바꾸지 않는다.
4. completed cache는 보고서 검증 후 재사용한다. failed/cache-invalid/running은 덮거나 자동 재실행하지 않는다.
   한 실패/BUSY는 독립된 다음 step을 멈추지 않는다. 기존 running을 실제 생존 프로세스로 단정하지 않는다.
5. 취소/시간·자원 한도는 owned running만 cancelled로 정리한다. 완료는 보존하고 다음 명시 실행에서 remaining을 실행한다.
   강제 종료 orphan/자동 재시도/영속 batch 복원은 이번 범위가 아니다.
6. RUNNING·구간 종료·최종 atomic 진행 파일에 모든 step/미시작 상태와 전체 batch 상태를 남긴다.

## 출력 계약과 그룹 비교

kind/status/database/fold_names/implementation_hash/steps/run_ids/attempted_now/cached_count/not_started_count/batch_status/reason은 유지한다.
v2는 version/symbol_partition/requested_step_count/group_comparisons를 추가한다.
모든 step은 원래 fold/role/start/end/run_id/state/reason에 symbol_bucket/step_key를 추가한다.
step_key는 `${fold_name}/bucket-${bucket}`이며 선택 fold/bucket 순서대로 유일하다. 미확인 run ID는 빈 문자열이다.
state는 기존 NOT_STARTED/RUNNING/COMPLETED/CACHED/BUSY/FAILED/CACHE_INVALID/CANCELLED/BUDGET_EXHAUSTED/RESOURCE_BLOCKED다.

전체 comparison=None/comparison_scope=per_symbol_bucket_identified_runs/v1이다. 서로 다른 bucket의 PnL/MDD를 합산하지 않는다.
group_comparisons는 선택 bucket 순서의 다음 객체 목록이다.

```text
symbol_bucket
requested_step_count       = 이 그룹의 모든 선택 시간 구간 수
completed_step_count       = COMPLETED 또는 CACHED step 수
not_started_count          = NOT_STARTED step 수
run_ids                    = 이 그룹의 확인된 ID만, 시간 순서
comparison_scope           = identified_runs_only/v1
comparison                 = 기존 independent_development_comparison/v1 또는 None
```

같은 bucket 내부는 기존 조건 검증/개발 적격/표본 부족/NO_CLOSED_TRADE/실패·결측/중복·겹침 판정을 재사용한다.
comparison이 한 개 확인된 ID에 대해 COMPLETE여도 requested_step_count가 더 크면 그룹 전체 완료가 아니다.
전체 batch_status도 모든 선택 step 기준이며 적격/수익성 승인이 아니다.
기존 v3의 by_symbol 체결 합/허용 종목 검증과 shared market context 한계는 그대로다.
최대 20개 그룹의 조회는 실행 child가 수행한다. GUI/키움 TR에 그룹별 조회를 추가하지 않는다.

## 화면 경계

기존 DevelopmentValidationDialog는 v1 시간 순차 검증 표시 계약만 알고 있다.
v2 요청을 받아 child를 실행한 뒤 잘못된 표를 표시하는 상황을 막도록 파일/프로세스 생성 전에 안내/거절한다.
이 최소 방어 수정은 새 화면 연결이 아니다. 기존 v1 실행/재개/숨김 취소/stop을 유지한다.
다음 CR3c3에서 같은 창에 v2 실행·진행/그룹별 결과 scope 검증을 연결한다.

## 검증

- 신규 계약 22개: v2 parsing/roundtrip/immutable 공통 정책/최대 200·초과/순서·최종·노출·mode 거절,
  실제 4 step 엔진·source 한 번/초기 상태 reset/시간 먼저 진행/그룹별 비교·빈 bucket,
  완료 cache·추가 bucket/cancel·시간·자원 한도 재개/failed·cache-invalid·BUSY 보존,
  OOS canary/공유 context 변경/원본 binding/code drift/CLI 종료·경로/legacy v1/UI 사전 거절.
- focused 86개 / 60.170초 / OK / native exit 0: tmp/cr3c2-focus.log, tmp/cr3c2-focus-exit.txt.
- 전체 연구 회귀 478개 / 303.021초 / OK / native exit 0:
  tmp/cr3c2-regression.log, tmp/cr3c2-regression-exit.txt.
- 변경 소스/신규 테스트/문서 10개의 AST·공백 및 git diff --check 통과: tmp/check_cr3c2.py.
- 고정 fixture/임시 DB/offscreen Qt만 사용했다. 실제 NAS/사용자 DB/계좌·주문/키/실행 앱에는 접근하지 않았다.

## 다음과 실제 남은 한계

다음 CR3c3: 순차 검증 창의 그룹×시간 실행/취소/진행·그룹별 결과 표시.
순차 자동 반복/앱 재시작 batch 복원/강제 종료 orphan 복구/새 날짜 자동 확장/최종 접근 원장은 후속이다.
source 전체 검증/로드와 매 step 시간 projection을 사용하므로 실제 규모 RAM/장시간 운영은 미검증이다.
기존 전체 context 품질/warmup은 개별 허용 종목 coverage 충분성을 보장하지 않는다. 그룹별 결측·편중 보완은 후속 평가다.
종목 그룹 비교는 초기 현금이 독립인 개발 결과이며 미사용 최종 종목 입력 봉인이나 하나의 실제 계좌 수익률이 아니다.
모델 에스컬레이션: 없음.

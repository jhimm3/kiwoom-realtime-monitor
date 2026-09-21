> **과거 기록** · 원래 경로: `reports/CR3B3_SEQUENTIAL_DEVELOPMENT_VALIDATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3b3 — 고정 전략의 여러 개발 구간 순차 검증

2026-09-16. **명시 JSON/CLI 실행 계약 완료**. 화면 실행/취소/진행 표시는 다음 CR3b4다.
기존 독립 입력/엔진/연구 DB v17/집계를 재사용한다. 원본 시장자료와 실제 계좌·주문은 변경하지 않는다.

## 목적과 입출력

한 전략·실행/비용·평가/profile을 고정하고 선택한 TRAIN/VALIDATION에서 순차 실행한다.
각 구간은 새 초기 현금/빈 포지션이며 warmup 진입 금지·내부 날짜 연속·종료 경계 censor는 기존 계약이다.
자동 파라미터 탐색/새 가설 생성/종목 분할/최종 접근/새 날짜 확장은 이번 단계에 없다.

```json
{
  "version": "independent_development_validation/v1",
  "request": {
    "mode": "single_run",
    "dataset": "검증된-export-또는-bundle-폴더",
    "database": "output/research.sqlite3",
    "runs_dir": "output/runs",
    "family": "krx_bar_close_breakout/v1",
    "session_profile": "krx-regular/v1",
    "strategy": "기존 single_run 전략 객체",
    "execution": "기존 비용 모델을 포함한 실행 객체",
    "evaluation": "TRAIN/VALIDATION/OOS를 정의한 기존 v1 평가 객체",
    "resource_budget": {"memory_mb": 512, "cpu_duty_percent": 50}
  },
  "dataset_id": "검증된 원본 manifest.dataset_id",
  "dataset_hash": "검증된 원본 manifest.revision_ids_hash",
  "fold_names": ["train", "validation"],
  "max_seconds": 60
}
```

위 strategy/execution/evaluation 문자열은 설명용 자리 표시자다. 실제 파일에는 기존 요청의 객체를 그대로 넣는다.
request는 기존 single_run 파서를 사용한다. 이미 선택한 development_partition, rank_comparison/limited_search와
명시 profile 없는 요청은 거절한다. 경로는 바깥 JSON 폴더 기준으로 정규화해 한 번 포착한다.
바깥 필드는 위 여섯 개만 허용하며 1 MiB 이하, fold_names는 중복 없이 시간 순서로 2~20개다.
각 이름은 TRAIN/VALIDATION이어야 하며 OOS/누락/노출된 최종 평가 정책을 거절한다.
max_seconds는 정수 1~3600이고 운영 예산이며 scientific run ID에는 넣지 않는다.

```text
python -m kiwoom_monitor.research_process --validate-partitions validation.json --result validation-result.json --cancel validation.cancel
```

기존 --request/--campaign/--compare-runs와 상호 배타적이다. campaign/worker/등록 옵션을 받지 않는다.
result/cancel이 요청·DB 또는 frozen dataset 내부를 가리키면 결과 파일도 쓰지 않고 거절한다.
파싱/옵션 오류는 argparse 종료 2/결과 파일 없음이다. 유효 실행은 기존 CLI의 atomic result 파일을 사용한다.

## 실행과 저장 계약

1. 취소/메모리·CPU 운영 한도를 확인하고 검증된 full source를 한 번만 읽는다.
   선언된 source dataset ID/hash와 일치해야 DB를 열고 실행할 수 있다. source 전후 cancellation/예산을 확인한다.
2. 구간마다 기존 prepare_development_partition으로 해당 입력만 복사한다.
   전체 source/OOS/watermark/children/품질 identity를 엔진이나 scientific identity에 넣지 않는다.
   다음 구간 준비 전에 이전 복사본을 해제하므로 모든 구간 복사본을 한꺼번에 쌓지 않는다.
3. scripts/run_research.py의 research_run_identity를 실행/캐시 양쪽이 공유한다.
   실행 시작 시 implementation hash를 고정하고 선점 전과 runner 진입 시 다시 확인한다.
4. 새 실행의 spec.execution_scope는 independent_development_validation/v1이다.
   기존 단일 실행 ID와 구분하여 기존 무선점 경로가 새 실행을 자연스럽게 공유하지 않는다.
   기본 execute_research의 기존 ID/spec/반환 및 기존 start_run의 None 반환은 유지한다.
5. start_run(..., claim_independent=True)은 기존 research_runs에서 BEGIN IMMEDIATE로
   정확한 scoped scientific ID/spec/입력을 확인하고 삽입 또는 cancelled→running을 원자적으로 선점한다.
   상태 반환은 claimed/completed/busy/failed다. 두 batch는 같은 run을 동시에 실행하지 않는다.
6. completed는 보고서 계약을 확인한 뒤 CACHED로 재사용한다. 완료 보고서가 없거나 INVALID면
   CACHE_INVALID로 남기며 immutable completion을 재작성하지 않는다. failed는 자동 재실행하지 않는다.
   running은 BUSY/owner 또는 복구 확인 필요로 남기며 실제 프로세스가 살아 있다고 단정하지 않는다.
7. 신규 scoped runner의 예외는 batch에 전달한다. **batch만** owned run의 취소/실패 상태를 확정한다.
   runner와 batch가 두 번 취소하여 그 사이 재개한 다른 실행을 건드리는 경계를 재현한 뒤 제거했다.
   기존 기본 scope의 예외/취소 처리 의미는 유지한다.
8. 선점 후 RUNNING/ID를 먼저 진행 파일에 공개하고 구간 종료 및 최종 반환 상태를 다시 저장한다.
   취소/예산/자원 차단은 owned running을 cancelled로 남겨 이후 명시 재개가 가능하다.
   실제 실행 실패는 failed로 보존하며 독립된 다음 구간은 계속한다. source/요청 검증 실패는 전체 실행을 거절한다.

새 테이블/마이그레이션/Manager·Service·일반 wrapper는 없다. 변경 파일은 기존 프로세스/runner/repository다.
연결은 프로세스 → 독립 입력/identity → 원자 선점 → 기존 엔진 → 기존 집계이며 추가 helper는 scientific ID를 실제 계산한다.

## 결과 의미

kind=independent_development_validation, database/fold_names/implementation_hash 및 steps를 반환한다.
steps는 선택한 **모든** fold의 역할/기간/run_id/state/reason을 원래 순서로 보존한다.
state는 NOT_STARTED/RUNNING/COMPLETED/CACHED/BUSY/FAILED/CACHE_INVALID/
CANCELLED/BUDGET_EXHAUSTED/RESOURCE_BLOCKED다. 아직 ID를 계산하지 못한 구간에 가짜 ID를 만들지 않는다.
attempted_now는 이번 runner 호출 수, cached_count와 not_started_count는 운영 집계다. 전체 search attempt census는 아니다.

batch_status=COMPLETED는 모든 구간이 실행 또는 캐시로 완료됐다는 뜻이며 수익성/표본 적격 승인이 아니다.
누락/실패/선점 대기/시간 예산이면 PARTIAL이다. 관리된 부분 실행은 status=ok/종료 0이며 reason/steps에
검토 필요 이유를 남긴다. 사용자 취소는 cancelled/종료 2, 자원 차단은 resource_blocked/종료 3이다.

run_ids/comparison은 **ID가 확인된 구간만** 기존 CR3b1로 조회한다.
comparison_scope=identified_runs_only/v1을 명시하므로, 일부 comparison이 COMPLETE여도 전체 batch가 완료됐다는 뜻이 아니다.
미시작 구간은 steps/not_started_count와 batch_status로 확인한다. 독립 초기 현금 손익을 한 연속 계좌로 합치지 않는다.

## 검증

- 신규 계약 테스트 20개: 실제 두 구간 runner·한 번 source 로드·초기 상태 reset/선택 입력,
  완료 캐시·취소/예산/자원 차단과 재개·코드 drift·입력 binding·원자 선점/BUSY·실패 보존·
  완료 보고서 누락·OOS canary/선택 payload 변경·CLI 경계·RUNNING/최종 진행 저장을 확인한다.
- 취소 종료 상태 중복 확정 경계는 수정 전 실패로 재현했다: `tmp/cr3b3-owner-before.log`.
- 기존 독립 실행/평가/프로세스/저장소 focused **77개 / 40.975초 / OK / native exit 0**.
  기록: `tmp/cr3b3-focus-final.log`, `tmp/cr3b3-focus-final-exit.txt`.
- 전체 연구 회귀 **423개 / 262.752초 / OK / native exit 0**. 마지막 검토 필요 reason assertion과
  기존 검색·캠페인·저장·복구·Qt 화면을 포함했다. 기록: `tmp/cr3b3-regression.log`, `tmp/cr3b3-regression-exit.txt`.
- 변경 12개 파일 AST/공백과 `git diff --check` 통과. 신규 untracked Python도 정적 검사에 포함했다.
- 실제 NAS·사용자 DB·계좌·주문·키·실행 앱에는 접근하지 않았다. 실제 전체 규모/장시간 운용은 미검증이다.

## 다음과 실제 한계

다음 CR3b4는 순차 검증의 화면 실행/취소/진행 표시다. 현재 비교 viewer는 저장 결과 조회만 한다.
완료된 구간의 재개는 지원하지만, 강제 프로세스 종료로 running이 남으면 소유자/복구 확인 전 자동 재실행하지 않는다.
lease/PID 확인을 통한 자동 orphan 복구는 후속이다. failed/cache-invalid도 명시 검토가 필요하다.
전체 source 로드/검증 자체는 여전히 OOS를 포함한 기존 입력 경로이며 실제 전체 규모 메모리/시간은 V1 운영 검증이다.
선택한 엔진 입력과 scientific cache에는 OOS를 전달하지 않는다.
기존 단일 실행 캐시는 namespace가 달라 이 선점 계약의 결과를 대신하지 않는다.
PC 소스 누적 변경이며 NAS 동기화·재빌드·실제 앱 재시작은 수행하지 않았다.

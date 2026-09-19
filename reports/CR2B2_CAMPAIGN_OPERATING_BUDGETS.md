# CR2b2 — 캠페인 운영 예산 확대

2026-09-16. PC 연구 기능이다. 기존 연구 실행기/저장소/창을 사용하며 NAS/API/주문/새 Manager는 변경하지 않는다.
CR2 전체나 24시간 새 가설 자동 연구의 완료 판정은 아니다.

## 목적과 변경 범위

기존 유한 completed 캐시를 유지하면서 유효한 캠페인이 명시적으로 예산을 확대할 수 있도록 한다.
원래 가설/자료/평가/전략과 이미 완료한 결과는 바꾸지 않는다. 예산 2→4이면 첫 2개를 보존하고 새 2개만 확정한다.

- research_repository.py: v11 migration, 불변 예산 revision/현재 overlay, 편집 조건, 원자 search 재개/claim.
- research_process.py: 기존 유한 cache와 캠페인 lease 취득을 구별하고 현재 DB 예산으로 증가분 실행.
- research_dialog.py: 저장 실험 선택, 횟수/회차 시간/메모리/CPU 편집, 차단/실패 명시 retry.
- 신규 test_research_campaign_budget와 기존 dialog/migration 기대 버전 테스트.

주요 코드 파일 수와 흐름은 기존처럼 UI→process→runner/repository다. 새 전달 계층을 추가하지 않았다.
예산 overlay helper는 불변 과학 명세와 운영 revision을 결합하는 실제 저장 계약이며 실행기를 복제하지 않는다.

## 저장 계약

v11 campaign_operating_budget_revisions는 기존 migration v1~v10을 바꾸지 않는다.
research_campaign_job_budgets의 (campaign_id,job_id,revision)는 불변 budget_json/created_at을 저장한다.
budget_json에는 max_trials/max_seconds/resource_budget이 있다. 기존 job/cycle에 budget_revision을 추가한다.
v10의 원래 request_json 예산을 revision 1로 복사하며 원래 행/설정/의도/sequence/owner/generation은 보존한다.
새 등록도 revision 1을 생성한다. 원래 request_json은 수정하지 않는다.
로드/claim 때 현재 예산을 원래 spec에 겹쳐 실행 spec을 구성하고 cycle에 그 revision을 기록한다.

수정 입력은 campaign_id/job_id/새 ExperimentSpec/expected_revision이다.
science evidence가 원래와 동일해야 하며 max_trials는 줄이지 않는다.
resource_budget에서는 memory_mb/cpu_duty_percent/max_retained_jobs만 변경할 수 있다.
조합 생성 cap/max_concurrent_trials/기타 생성 정책은 같은 과학 identity로 바꾸지 않는다.
기존 ExperimentSpec의 횟수/CPU/메모리 검증을 재사용한다.

PAUSED/STOPPED, 일치하는 expected revision, campaign/search live owner 없음이 편집 조건이다.
남은 trial이 생기면 PENDING으로 돌리고 실패 count/backoff를 초기화한다. generation은 감소시키지 않는다.
만료된 이전 cycle은 INTERRUPTED/operating_budget_revised로 닫는다. 이전 budget revision은 보존한다.
완료 job이 다시 active가 되는 경우 campaign/global backlog cap을 확인하며 실패하면 예산 revision/상태를 모두 rollback한다.
보통 같은 예산 저장은 no-op이다. 다만 다른 실행이 더 적은 예산으로 먼저 완료해 budget_expansion_required인 경우,
같은 목표를 명시 저장해도 새 revision을 남기고 현재 목표의 재개 의도로 처리한다.

## 실행 계약

기존 일반 limited_search completed 요청은 종전처럼 cached 결과를 반환한다.
캠페인의 start_search_job은 BEGIN IMMEDIATE에서 campaign owner/generation/live lease/RUNNING,
현재 budget revision과 captured spec을 확인한다. explicit budget revision에서 실제 확정 trial 수가
목표보다 적으면 completed job을 queued로 열고 같은 트랜잭션에서 search owner/lease/generation을 취득한다.
동시 작업자 중 한 명만 취득하며 실패한 claim은 reopen/event까지 rollback한다.
이 경로는 실제 확정 trial 행을 완료 증거로 사용하므로 예산 증거가 없는 legacy cache도 명시 확대 후 처리 가능하다.
revision 1의 budget 없는 완료 cache에 대한 기존 호환 판정은 유지한다.

기존 run_limited_search의 existing_trials를 재사용한다. 이전 trial/card/report/run ID는 그대로이며 증가분만 실행한다.
trial commit은 기존 campaign/search 두 소유권과 live lease/RUNNING을 원자 검사한다.
새 예산으로 만료 소유권을 우회하거나 전략/자료/implementation hash 불일치를 무시하지 않는다.
자원 차단/실패 retry도 global active cap을 넘겨 등록하지 않는다. 자동 최종평가/가설 생성/주문은 OFF다.

## UI

일시정지 후 worker 종료 → 실험 예산 / 재시도 → 실험 선택 → 예산 저장 → 시작 / 재개.
누적 trial 한도는 0~1000의 기존 지원 범위, 메모리는 128~4096MiB, CPU 목표는 10~100%다.
회차 시간은 예산마다 실험을 나누어 이어가기 위한 한도이며 캠페인의 전체 수명 제한이 아니다.
차단/실패 실험은 차단 / 실패 실험 다시 대기로 명시 retry한다. 예산 저장/재시도만으로 worker를 시작하지 않는다.
편집 시 저장된 DB spec/revision을 사용하므로 원래 요청 파일은 필요 없다.
다른 창에서 변경되면 stale revision 오류를 표시하고 다시 편집하도록 한다.

## 검증

test_research_campaign_budget는 다음을 확인한다.
2→4 증가분만 실행/첫 결과와 보고서 내용 보존/일반 유한 cache 유지, no-op, 과학·생성 cap·횟수 축소 거부,
RUNNING/live campaign/live search owner 편집 거부, 만료 cycle/old worker 거부, campaign/global cap rollback,
예산 INSERT/claim UPDATE 실패 rollback, 두 edit/두 search worker/edit-vs-claim 원자성,
위조 captured budget 거부, 자원 예산 반영, 원래 JSON 없는 restart, v10 job/cycle 원래 열 tuple 보존,
다른 작은 완료 job의 기존 목표 명시 재개와 budget 없는 legacy 완료 캐시의 실제 trial 수 검증.
GUI는 저장/실행 중 거부/명시 retry/실제 spinbox 값 저장/worker 종료 후 제어 재활성화를 확인한다.
기존 campaign/reader/replay/process/search/자원/비교/확장/저장소/Qt 인접 회귀도 실행한다.

최종 `tmp/cr2b2-regression.log`: **173개 / 66.587초 / OK / 종료 코드 0**, skip/reader thread 예외 없음.
변경 Python 7개 AST/공백 검사와 관련 코드·문서 git diff --check도 통과했다.
테스트는 임시 자료/DB/별도 설정/offscreen Qt와 숨긴 fixture worker만 사용한다.
실제 사용자 DB/NAS/API/키/주문/실행 앱/이미지 재빌드·동기화는 사용하지 않았다.
제품에서 연구 저장소를 생성하면 v11 migration을 적용한다. v10 실행기로의 DB downgrade는 지원하지 않는다.

## 다음 단계

CR2c: 관련 새 자료 선택/자동 등록, worker 반복 종료 backoff/격리, 선택 index 실패/discovery,
보관 원장/디스크 cap/참조 보호. 원래 request/science identity를 묵시적으로 바꾸지 않는다.
자동 가설/독립 표본·최종 구간 분리/계좌별 일지 피드백/자동 모의운영은 각각 CR4/CR3/A5/O2-M 후속이다.
실제 NAS 전체 규모/장시간 동작은 V1 검증이다. 현재는 등록된 실험을 현재 예산까지 지속 실행할 수 있는 단계다.
모델 에스컬레이션: 없음.

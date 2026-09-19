# A5e3 — 새 전략 버전과 CR3 개발 재검증 queue

기준일: 2026-09-16. 로컬 구현·회귀 완료, NAS 누적 배포 전.

## 목적

명시적으로 채택된 A5e2 proposal을 기존 전략에 덮어쓰지 않고 새 버전으로 보존한 뒤 기존 CR3
개발 캠페인에서 다시 검증한다. 채택과 주문 활성화를 분리한다.

## 새 전략 버전

`feedback_strategy_version/v1`은 부모 전략, source proposal/review/evidence, mock 계좌 scope,
등록 Family·factor, 정규화된 전체 설정과 채택 사유를 내용 hash에 포함한다. 내부 계산식의
`strategy_version=v1`은 구현 호환 버전으로 유지하고 envelope의 `version_id`가 새 전략 버전 참조다.
같은 proposal은 한 번만 버전화할 수 있으며 상태는 `REVALIDATION_REQUIRED`다.

## 재검증 queue

호출자는 기존 campaign의 template job을 명시한다. 새 버전은 template의 데이터·평가·운영 예산을
유지하고 baseline strategy, Family/factor와 hypothesis ref만 새 버전에 맞춘다. parameter grid,
ablation, cost stress는 비우고 baseline/no-trade 두 trial만 실행한다. final holdout을 열었던 template은
거절한다.

새 job은 기존 `ResearchRepository.enqueue_campaign_experiment(..., source_kind="hypothesis")`를
사용한다. 연구 worker나 새 queue 계층을 만들지 않았다.

## 중단과 재시도

중앙 generic document store와 로컬 연구 SQLite는 한 트랜잭션이 아니다. 대신 전략 버전,
request, experiment, job과 receipt ID를 모두 내용 기반으로 만든다. 버전당 한 request를 연구 queue
보다 먼저 저장하므로 다른 campaign으로 중복 dispatch할 수 없다. 연구 job 저장 후 중앙 receipt
저장이 실패해도 같은 요청을 재실행하면 기존 job을 재사용하고 receipt만 완성한다.

비공개 저장 컬렉션은 다음과 같다.

- `execution_feedback_strategy_versions`: owner=`parent_strategy_ref`, key=`version_id`
- `execution_feedback_revalidation_requests`: owner=`parent_strategy_ref`, key=`request_id`
- `execution_feedback_revalidation_receipts`: owner=`campaign_id`, key=`receipt_id`

공개 API, 중앙 DB migration, 사용자 복기와 주문 활성화는 추가하지 않았다.

## 검증

- A5e3 대상 테스트 17개 통과.
- A5 전체·연구 캠페인·CR4 가설·전진평가·체결 대조 연관 회귀 75개 통과.
- 새 버전/부모 계보, exact job 1개, 반복 dispatch 멱등성, proposal 중복 채택 차단,
  다른 campaign의 중복 request 차단, final holdout 차단, receipt 저장 실패 뒤 job 무중복 복구를 확인했다.

## 다음

A5는 완료됐다. 다음은 O2-M이며, 검증된 후보를 동결된 mock 운용 정책 안에서만 자동 실행한다.
동시 전략/포지션, 자금·손실, 평가기간, 지원 시장시간, 데이터 단절, 후보 교체와 재시작 복구 값이
하나라도 비어 있으면 자동 주문은 BLOCKED 상태로 둔다.

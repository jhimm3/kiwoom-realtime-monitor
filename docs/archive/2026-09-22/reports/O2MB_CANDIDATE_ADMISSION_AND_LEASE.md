> **과거 기록** · 원래 경로: `reports/O2MB_CANDIDATE_ADMISSION_AND_LEASE.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# O2-Mb — 후보 입장과 모의계좌 단일 lease

기준일: 2026-09-16. 로컬 구현·회귀 완료, NAS 누적 배포 전.

## 목적

동결된 O2-M 명세가 실제 최종평가와 현재 계좌·전략 상태를 가리키는지 입장 시 다시 확인하고,
같은 모의계좌에서 수동 O1과 자동 후보가 동시에 주문 담당자가 되지 않게 한다.

## 입장 검사

- 저장된 운용 명세를 현재 최신 `ka00001` mock binding과 다시 대조
- 명세가 가리키는 forward profile과 최신 전략 stage가 `SHADOW`인지 확인
- `shadow_evidence_ref`가 실제 최신 SHADOW revision ID인지 확인
- CR3 final batch에서 candidate hash가 단 하나이고 execution/run이 동일 result hash로
  `COMPLETED/completed`인지 확인

통과한 spec은 내용으로 자동 `execution_run_id`를 결정한다. spec당 admission request를 먼저
불변 저장하고 기존 `central_execution_runtime_leases`의 `mock:account_ref`를 claim한다. 수동 O1이나
다른 후보가 보유 중이면 request만 재시도 근거로 남고 lease receipt는 없다.

## 주문 차단

`ExecutionRuntime.start()`에 `new_orders_enabled` 선택 인자를 추가했다. 기존 수동 호출은 기본값을
유지한다. 자동 입장은 같은 잠금 구간에서 `False`를 적용하고 lease를 얻는다. receipt도
`LEASED_ORDERS_DISABLED`와 `new_orders_enabled=false`만 저장할 수 있다. 따라서 이 단계에서는
Decision이나 주문 intent가 생성되지 않는다.

## 검증

- O2-Mb 대상 테스트 4개 통과.
- O1 계좌 복구·lease·주문 원장, 전진평가·A5 피드백, CR3 최종평가 연관 회귀 106개 통과.
- 동일 spec 반복 입장의 admission/receipt 멱등성, 주문 전송 0회, 수동 lease 충돌,
  미완료·변경 final 결과, 변경된 binding과 잘못된 SHADOW 근거 차단을 확인했다.

## 다음

O2-Mc에서 lease를 가진 자동 runtime의 broker 전체 복구와 open/unknown 주문, 기존 포지션,
자금·일일 손실, 데이터/장애 한도 판정을 완료했다. 다음은 이 검사를 매 Decision 직전까지
유지하는 O2-Md다.

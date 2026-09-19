# CR3a1 — 개발 후보 선택 근거 격리

2026-09-16. CR3의 첫 구현은 후보 선택 경계의 최소 수정이다. 독립 partition 실행 완료 보고는 아니다.

## 확인된 문제와 재현

현재 호출은 research_process의 trial 평가 → 저장 보고서 조회 → research_search.outcome_from_report → 후보 카드다.
기존 outcome 변환은 TRAIN/VALIDATION의 지표만 더했지만, 적격 상태와 reasons는 전체 보고서에서 가져왔다.
전체 보고서는 OOS를 포함하므로 최종 구간의 부적격이 개발 후보를 부적격으로 바꿀 수 있었다.
반대로 전체 보고서가 ELIGIBLE이면 개발 fold의 INELIGIBLE을 제대로 반영하지 않는 입력도 통과했다.
OOS를 열었는지만 달라져도 TrialOutcome.report_status가 변했다.

새 회귀 3개를 수정 전에 실행해 모두 실패했다 (`tmp/cr3a1-before.log`).
확인한 것은 코드 호출 경로와 합성 보고서 재현이며, 실제 NAS 연구에서 누수가 발생했다는 실측은 아니다.

## 입출력과 책임

- 기존 research_evaluation에 frozen DevelopmentEvidence와 build_development_evidence를 추가한다.
- 입력은 저장 보고서지만, 출력에는 TRAIN/VALIDATION의 상태·사유·fold 참조·요약 지표만 복사한다.
  report status/reasons, data_quality, cost/raw event, OOS 지표/상태/사유는 출력에 포함하지 않는다.
- 상태는 개발 fold만 집계한다. 하나라도 INELIGIBLE이면 부적격, ELIGIBLE이 있고 개발 실패가 없으면 적격,
  전부 NOT_APPLICABLE이면 무거래/적용 불가다. 개발 fold가 없으면 missing 이유를 남긴다.
  알 수 없는 개발 상태나 개발 fold의 SEALED를 적격으로 통과시키지 않는다.
- 이유는 개발 fold 이유만 복사한다. 원본의 mutable 목록/row를 저장하지 않으며 결과는 불변 scalar/tuple이다.
- search의 기존 outcome_from_report는 이 근거에서 TrialOutcome을 만든다. 후보 report_status는 개발 집계 상태다.
  함수 signature와 TrialOutcome/Card 필드는 유지하며 원본 전체 보고서·OOS 표시·저장 문서는 수정하지 않는다.
- legacy net sum/max fold drawdown/active-day count sum 산술은 유지한다.
  공통 현금 portfolio의 전체 MDD나 독립 거래일 수를 새로 계산했다고 해석하지 않는다.

기능을 이해하는 파일은 기존 search/evaluation 두 파일이다. 전달용 새 Service/Manager/인터페이스는 없다.
추가 extraction 호출은 최종/raw 정보가 없는 독립 데이터 경계를 만든다. 향후 generator가 받을 근거의 출발점이다.
자동 generator/최종평가를 이번에 켜지는 않는다.

## 호환성과 저장 경계

연구 DB v16/기존 migration/요청 JSON/chronological_holdout v1/NAS API/키움 TR/주문 경로는 변경하지 않는다.
기존 후보 카드와 전체 보고서를 소급 수정하지 않는다. evaluation 파일 변경은 기존 implementation hash에 반영된다.
이전 동결 요청의 hash를 새 코드로 자동 교체하지 않는다. 새 실행은 현재 구현의 새 요청을 사용한다.
실제 사용자 DB/운영 파일/실행 앱/NAS에는 접근하거나 변경하지 않았다. NAS 재빌드는 필요 없다.

## 검증

- 수정 전 새 회귀 3개: 예상된 실패 3개. 최종 실패/개발 실패 무시/열람 상태 유입을 각각 재현했다.
- 관련 search/evaluation/reports/splits 35개: 0.010초, OK, native exit 0 (`tmp/cr3a1-focus.log`).
- 새 회귀 총 9개는 최종 ±10^30 손익/최종 실패/열람 변화에도 개발 선택 입력과 모든 후보 카드가 불변인지,
  개발 실패·미등록 상태·final-only 보고서·불변 복사·무거래/표본 부족을 검증한다.
- 관련 전체 회귀 303개: 171.904초, OK, native exit 0 (`tmp/cr3a1-regression.log`).
  성과/보고/분할/후보 선택/준비 자료·용량·정리/worker·예산·캠페인/리플레이·bundle/자원/실행/offscreen UI를 포함한다.
- Python AST/변경 문서 공백 11개 파일 검사와 tracked 변경의 git diff --check 통과.
- 임시 DB/합성 자료/가짜 NAS/offscreen Qt에서만 검증했다. 실제 운영 DB/NAS 부하 검증은 수행하지 않았다.

## 남은 범위와 다음 단계

아직 v1 runner는 전체 dataset을 읽으며 연속 현금/보유 상태와 기존 보고 품질 계산을 사용한다.
전체 입력 품질 이유가 개발 fold 안에 복사된 경우를 사후 이유 문자열로 제거하지 않는다.
그 문제는 입력/품질 계산을 partition별로 분리해야 해결된다. 이번 합성 canary는 보고서 경계 검증이며
원시 final 데이터 변경을 통한 end-to-end 독립 검증은 아니다.

다음 CR3a2는 버전 고정 독립 partition 정책과 개발용 입력 제공이다.
최종 입력이 개발 runner에 들어가지 않게 한 뒤 초기 현금/빈 포지션/warmup 진입 금지·품질 계산을 연결한다.
고정 stock salt 분할, 후보 batch/최종 접근·EXPOSED_DEVELOPMENT 원장, 새 날짜 확장은 후속 CR3에서 진행한다.
CR3 전체/24시간 자동 가설 연구/자동 모의운영은 완료되지 않았다. 실제 NAS 장시간 검증은 V1이다.
모델 에스컬레이션 없음.

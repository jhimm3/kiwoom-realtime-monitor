# CR3b1 — 여러 독립 개발 구간의 비교·집계 계약

2026-09-16. 기존 연구 보고서와 DB v17을 재사용하는 읽기 전용 단계다.
자동 구간 실행·화면 표시는 다음 CR3b2이며 이번 단계는 저장 결과의 검증/집계 계약이다.

## 구현

- `research_evaluation.py`에 불변 `DevelopmentPartitionResult`, `IndependentDevelopmentComparison`과
  `build_independent_development_comparison(records)` 순수 계산을 추가했다. 기존 DevelopmentEvidence/v1 계산은 유지한다.
- `ResearchRepository.load_independent_development_comparison(run_ids)`는 명시한 1~200개 run을
  한 SELECT/LEFT JOIN의 SQLite snapshot에서 읽는다. 전체 job/trial/체결/원본 봉을 읽거나 DB를 쓰지 않는다.
- 별도 Manager/Service/파일 계층·스키마·NAS API를 추가하지 않았다. 기존 평가 모듈의 집계 정책과
  저장소의 조회 경계에 넣었으며 경로는 repository → 순수 집계 두 단계다.

## 입출력

```python
comparison = repository.load_independent_development_comparison((train_run_id, validation_run_id))
document = comparison.to_dict()
```

순수 계산의 입력은 `run_id/status/spec/input_manifest/logical_result_hash/report`를 가진 저장 run record다.
출력 버전은 `independent_development_comparison/v1`이다. 입력 순서와 요청한 모든 결과 행을 보존한다.
행에는 상태/이유, 조건 key, 선택 데이터 ID/hash, 역할/활성 기간, 거래·활성일 수, 손익/MDD와
종목/날짜/시간별 닫힌 거래 집계를 불변 tuple로 복사한다. 원본 보고서를 결과 객체에 넣지 않는다.
실패 run의 유효 partition metadata가 있으면 예정 기간/역할/입력 identity도 남긴다.

## 비교 조건

독립 입력 v1 및 development partition v2, 정확히 하나의 TRAIN/VALIDATION 평가만 허용한다.
명시 profile, code hash, 완료 logical hash, 입력 identity, 평가/partition/보고서 경계와 warmup을 확인한다.
legacy 연속 실행, OOS, 노출된 최종평가, 어긋난 보고서와 적격 기준 미달 ELIGIBLE 행은 INVALID다.

전략/파라미터/Factor·code·실행·비용·현금·평가 적격/종료 정책 및 입력 schema/profile/subject/universe/order
계약이 같아야 같은 조건 key다. 실행 spec의 per-input manifest hash와 평가 fold 이름/역할/날짜만
비교 조건에서 제외한다. 다른 날짜는 다른 선택 데이터 ID/hash를 가지는 것이 정상이며 그 identity는 행에 보존한다.
비용 모델의 출처/유효기간까지 기존 spec 그대로 비교한다. 유효기간만 다른 모델도 자동 동등 처리하지 않는다.
조건 key가 둘 이상이면 INCOMPARABLE이며 여러 조건을 섞은 통계는 없다.

같은 run reference 및 동일 조건·기간·역할·입력·logical 결과의 재실행은 중복 표본이 아니다.
동일 run ID에 모순된 record가 들어오면 양쪽을 제외한다. 같은 기간의 자료 revision이나 활성 기간이
겹친 실행은 모두 보존하지만 독립 표본 통계에서 양쪽을 제외한다. 붙어 있는 활성 기간은 허용한다.
공유 warmup은 별도 독립 자료 증거가 아니라는 한계를 표시한다.

## 상태와 통계 의미

- MISSING/FAILED/INCOMPLETE/INVALID/INELIGIBLE/INSUFFICIENT_SAMPLE/NO_CLOSED_TRADE/ELIGIBLE을 구별한다.
  완료 run의 보고서가 없으면 INVALID로 남는다. 자료·비용 문제와 표본 부족이 함께 있으면 INELIGIBLE이다.
- COMPLETE는 요청한 고유 근거가 모두 적격이고 비교 가능하다는 뜻이다. 수익성/유용성 승인이나 자동 승격이 아니다.
  누락/실패/표본 부족/겹침이 있으면 INCOMPLETE다. 일부 적격 결과의 통계는 볼 수 있지만 상태는 유지한다.
- 적격·비중복·비중첩 구간의 손익 최솟값/최댓값/중앙값, 양수 구간 수, 최악의 **구간 MDD**만 제공한다.
  손익 합·통합 수익률·연속 계좌 MDD는 계산하지 않는다. 각 구간의 초기 현금이 별개이기 때문이다.
- 활성일과 종목/날짜/시간별 표본은 구간별 값이다. 동일 날짜를 고유 날짜 여러 개로 세지 않는다.
  해당 strata는 닫힌 거래의 편중이며 전체 시장자료 coverage나 당시 시장 regime 보증이 아니다.
- 요청 run 수/고유 run 수는 명시 조회 범위다. 전체 search attempt/실패 재시도 원장을 대신하지 않는다.
  최종 자료 접근/자동 후보 승격/신규 가설 생성은 없다.

## 검증

- 새 계약 테스트 14개: 고정 합성 자료·임시 DB에서 서로 다른 두 구간의 실제 runner 결과,
  조건 불일치, 중복/겹침/자료 revision, 모순된 reference, 실패/누락/표본 부족,
  OOS canary, 불변성/읽기 전용과 조회 상한을 확인했다.
- 평가·분할·독립 입력·검색·캠페인 focused 회귀 **79개 / 46.379초 / OK / native exit 0**.
  기록: `tmp/cr3b1-focus.log`, `tmp/cr3b1-focus-exit.txt`.
- 연구 전체 회귀 **382개 / 231.241초 / OK / native exit 0**. 기존 복구/저장/worker/Qt 화면을 포함한다.
  기록: `tmp/cr3b1-regression.log`, `tmp/cr3b1-regression-exit.txt`.
- 변경 10개 파일 AST/공백 및 `git diff --check` 통과. 신규 untracked Python도 AST 검사에 포함한다.
- 실제 NAS·사용자 DB·계좌·주문·키·실행 중인 앱은 건드리지 않았다. 실제 NAS 규모 부하·장시간 운용은 미검증이다.

## 다음

CR3b2는 이 계약을 별도 연구 프로세스의 명시 결과 조회와 화면 표시로 연결한다.
자동 여러 구간 재실행·종목 분할·최종 접근 원장·새 날짜 확장은 이후 단계다. NAS 재빌드 대상은 없다.

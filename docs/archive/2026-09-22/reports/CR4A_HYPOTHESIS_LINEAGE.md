> **과거 기록** · 원래 경로: `reports/CR4A_HYPOTHESIS_LINEAGE.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR4a 등록 단일 파라미터 가설과 계보

## 범위

CR4a는 자동 연구가 실행할 후보의 정체성과 부모 관계를 먼저 고정한다. 기존 탐색 실행기,
campaign scheduler, final holdout, NAS 수집, 계좌와 주문 경계는 바꾸지 않는다.

## 입력 계약

`HypothesisGenerationRequest`는 다음 값을 받는다.

- `campaign:<id>` 같은 명시적 `research_scope_id`
- 등록된 Family ID 하나와 그 Family에 등록된 Factor allowlist
- 해당 Family config의 정규화된 전체 기준 파라미터
- 등록된 searchable parameter별 비어 있지 않은 정수 허용값 tuple
- 1~200개의 개발 근거 참조. final 원문이나 final 성과를 생성기에 전달하는 경로는 만들지 않는다.
- 정수 seed와 0~1000의 최대 변형 수

허용값은 코드가 추측하거나 자동 확장하지 않는다. 각 값을 기준 config에 한 번 적용하고 기존 전략
config 생성자가 거절하면 요청 전체를 거절한다. 비등록 파라미터, hard constraint, bool, 정규화 과정에서
사라지는 값도 거절한다.

## 출력 계약

첫 문서는 `BASELINE`, 나머지는 그 기준선을 유일한 부모로 갖는 `ONE_PARAMETER_VARIANT`다.
변형은 등록 searchable parameter 한 필드만 달라야 한다. 기준값과 같은 값 및 중복 허용값은 후보를 만들지 않는다.
기존 `generate_trials`가 baseline/no-trade 실행 variant를 계속 소유하므로 가설 생성기가 별도 무거래 전략을 만들지 않는다.

seed는 제한된 후보의 순서를 정할 뿐 가설 내용과 ID에 들어가지 않는다. `hypothesis_id`는 버전,
Family/Factor, 상태, 부모, 개발 근거, 기준 설정, 결과 설정, 변경 이유를 canonical JSON으로 만든 SHA-256이다.
따라서 같은 데이터의 기술적 재시도나 다른 seed 재정렬은 새 독립 가설·증거가 되지 않는다.

## 저장 계약

연구 DB v21은 다음 표만 추가한다.

- `research_hypotheses`: content ID, Family, 상태, 생성 시각, canonical 문서
- `research_hypothesis_parents`: 자식별 순서 있는 부모 edge와 양쪽 FK

저장은 최대 1001개 문서의 순서 있는 단일 트랜잭션이다. 부모는 이미 DB에 있거나 같은 배치에서
앞에 있어야 한다. 하나라도 위반하면 전체가 롤백된다. 같은 ID·같은 문서는 멱등이며 같은 ID의 다른
문서는 거절한다. v20 데이터는 migration 중 수정하지 않는다.

## 검증

- 동일 seed의 문서와 순서 결정성
- seed 변경 시 순서만 달라지고 ID 집합은 동일함
- 한 파라미터 차이, 기준값·중복값 제거, 생성 상한
- 비등록 Factor/파라미터와 전략 config가 거절하는 값 차단
- exact-field 역직렬화와 콘텐츠 ID 변조 차단
- v20→v21 기존 행 보존, 부모 FK·원자 rollback, 멱등 저장, Family/limit 조회

CR4a 완료 당시 연구 전체 `test_research*.py` 610개가 v21 기준으로 통과했다. 이후 두 번째 등록 Family와
부모 ID 제한 조회를 추가한 집중 회귀 20개도 통과했다. CR4b에서 scope 필드를 추가하고 동일 회귀를 계속 유지한다.

## 후속 경계

CR4b는 READY 가설을 기존 `ExperimentSpec.hypothesis_refs`와 campaign job에 연결했다. 두 Family 순환,
실패 부모에서 만드는 반증 후보, 범위 소진 대기 사유, 미등록 계산식의
`DRAFT_REQUIRES_IMPLEMENTATION`, 자동/수동 한 변수 실행 일치와 화면은 CR4c 이후다.

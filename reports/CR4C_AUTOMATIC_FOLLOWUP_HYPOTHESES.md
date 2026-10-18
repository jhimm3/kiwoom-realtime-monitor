# CR4c 개발 결과 기반 자동 후속 가설

## 목적

완료된 자동 가설의 개발 결과를 다음 단일 파라미터 가설로 연결한다. 기존 campaign worker와 제한 탐색을
그대로 사용하며 새 실행 엔진, 자동 코드 생성, 자동 final 평가 또는 주문 권한을 추가하지 않는다.

## 입력 경계

후속 생성은 자동 가설 job의 baseline trial 하나와 그 `run_id`에 저장된 연구 보고서만 찾는다.
기존 `build_development_evidence`가 TRAIN/VALIDATION fold의 상태, 이유, fold 참조와 제한된 집계만 복사한다.
FINAL/OOS fold, 전역 보고서 판정, 원시 사건·체결·관측값은 snapshot과 생성 함수에 전달하지 않는다.

`development_evidence_snapshot/v1` ID는 source run과 개발 전용 projection의 canonical hash다. 같은 run의
같은 개발 근거 재확인은 같은 ID이며 기술적 재시도를 별도 과학적 증거로 세지 않는다.

## 생성 정책

`ResearchCampaignPolicy`는 다음을 명시한다.

- Family별 등록 searchable parameter와 허용 정수값
- 생성 순서용 고정 seed
- worker 반복 한 번의 최대 생성 수
- campaign 전체 등록 가설 상한

완료 부모의 전체 전략 설정을 기준으로 한 필드만 허용값 중 다른 값으로 바꾼다. 기존 config parser를 다시
통과한 READY 자식만 저장한다. 같은 campaign에 같은 Family와 전체 설정이 이미 있으면 부모·근거가 달라도
다시 등록하지 않아 A→B→A 순환을 막는다. 허용값이 없는 Family는 명시적 대기이며 잘못된 정책은 차단 상태다.

## 영속성과 재시작

연구 DB v23 `research_campaign_hypothesis_expansions`는 campaign, 부모 가설, 개발 근거 ID와 policy revision을 기본키로 사용한다.
상태는 GENERATED, EXHAUSTED, BLOCKED이며 생성 수, 이유, 개발 evidence 문서와 시각을 저장한다.
완료 job에 바인딩된 부모만 활성 worker가 현재 revision으로 기록할 수 있다. 일시정지 상태에서 정책을 개정하면
새 worker가 새 revision으로 완료 부모를 다시 확장할 수 있고 이전 원장은 그대로 남는다.

자식 가설 저장과 campaign 등록은 기존 멱등 API를 사용한 뒤 expansion 원장을 기록한다. 그 사이 프로세스가
종료되면 다음 worker가 같은 콘텐츠 ID의 자식을 재생성하고 이미 등록된 정확한 ID를 확인한 뒤 같은 생성 수로
원장을 복구한다. 다른 설정을 같은 결과로 간주하지 않는다.

worker 반복 순서는 입력 발견, 완료 부모 최대 하나 확장, AVAILABLE 가설 최대 하나 예약, campaign cycle 실행이다.
따라서 기존 backlog·lease·재시도·Family 순환을 유지한다.

## 검증

- 순수 생성은 같은 부모·근거에서 결정론적이고 모든 자식이 정확히 한 필드만 변경한다.
- 합성 campaign에서 기준 job과 자동 baseline/no-trade 실행을 완료한 뒤 두 후속 설정을 자동 등록했다.
- 자식 등록 뒤 expansion 기록 전 종료를 모사해 같은 두 ID와 수로 복구하고 등록 수가 늘지 않음을 확인했다.
- v22 AVAILABLE campaign 가설 fixture를 v23으로 올려 기존 행 보존과 빈 expansion 원장을 확인했다.
- campaign·worker·repository 인접 회귀 106개가 통과했다.
- 정책 revision 보완 뒤 backend 집중 회귀 60개, 설정 창과 기존 연구 화면 회귀 46개가 통과했다.
- 설정 창에서 Family·파라미터·허용값을 실제 저장하고 기준/첫 이웃 3개가 등록되는 흐름을 확인했다.
- 최종 코드 상태의 연구 전체 회귀 626개가 통과했으며 마지막 UI 상한 보완의 직접 회귀 2개도 통과했다.

## 남은 범위

미등록 계산식 draft, 자동 최종평가, 모의·실계좌 주문은 이 단계에 포함하지 않는다.

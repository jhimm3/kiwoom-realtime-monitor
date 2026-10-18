# ASTRA ULTRA 설계 재검토 필요 — O2-Me 자동 모의운영 조립

> 2026-09-16 재검토 완료. 아래는 당시의 대안 검토 기록이다. 최신 결정은
> [O2-M 설계 결정·구현 계약](O2M_DESIGN_REVIEW_DECISIONS_20260916.md)을 따른다.
> O2-Md 안전 경계 완료 판정은 보완 필요로 정정했으며, 다음 구현은 O2-M0이다.
> 아래 “검증된 당일 손익” 표현은 현재 scalar/출처 문자열 검사를 실제 근거 검증으로 과대평가했다.

기준일: 2026-09-16. O2-Md 안전 경계 완료 뒤 실제 코드에서 확인한 다음 설계 결정이다.

## 현재 문제

- `mock_automation_operating_spec/v1`과 admission은 final candidate의 hash만 중앙에 보존한다. 실제
  family·parameters·execution model·implementation hash는 PC 연구 SQLite의 final run `spec`에
  있으며 NAS가 재시작 뒤 독립적으로 복원할 불변 candidate payload가 없다.
- NAS `CandidateMonitor`는 환경변수의 shadow config와 자체 `monitor_id`를 사용한다. admission의
  strategy/package/run identity와 다르므로 이 monitor의 Decision에 주문권한을 붙일 수 없다.
- O2-Md는 `account_scoped_fifo_broker_cost/v1`로 확인된 당일 순손익만 받는다. A5의 상세 체결·실제
  비용 완결 projection은 PC 매매일지 경계다. NAS 24시간 runner에 매 Decision마다 제공할 owner가
  없다.
- 손익·데이터 상태가 unknown일 때 ENTRY를 막는 것은 명확하지만, 이미 보유한 포지션의 위험 축소
  EXIT까지 같은 조건으로 막을지, 별도 broker reconcile/강제 정리 정책으로 넘길지 정해지지 않았다.

## 관련 영역

- `src/kiwoom_monitor/research_process.py`: `final_candidate_spec_hash`, final request payload
- `scripts/run_research.py`: final run `spec`의 family/parameters/execution/session/implementation hash
- `src/kiwoom_monitor/central_server/candidate_monitor.py`: 현재 order-free shadow runner/checkpoint
- `src/kiwoom_monitor/application/mock_automation_*.py`: spec/admission/recovery/Decision gate
- `src/kiwoom_monitor/central_server/execution_runtime.py`: O1 account lease와 제출 직렬화
- `src/kiwoom_monitor/application/forward_evaluation.py` 및 journal projection: 실제 비용 포함 피드백

데이터 흐름은 현재 `PC final run → hash만 O2 spec/admission → NAS O1 lease/gate`까지다. `final run의
실행 payload → NAS 전용 runner`와 `현재 체결/비용 → 매 Decision 위험 지표` 두 연결이 비어 있다.

## 기존 설계를 그대로 유지할 경우

### 장점

- 연구 worker, shadow monitor, 주문 owner의 분리가 유지된다.
- hash가 맞지 않는 설정이나 미확인 손익으로 주문이 나가지 않는다.

### 문제점

- O2-Md 함수를 명시 입력으로 시험할 수는 있지만 사용자 클릭 없는 24시간 자동 모의운영은 시작할
  수 없다.
- shadow 환경 설정을 수동으로 복제하면 final candidate와 다른 전략을 같은 hash로 오인할 수 있다.
- 실제 비용 완결 전 모든 action을 막으면 정상 EXIT도 지연될 수 있다.

### 예상되는 기술 부채

- env 기반 전략 설정과 final package 설정의 이중 원본
- PC/NAS가 서로 다른 run ID와 checkpoint를 가진 채 같은 전략처럼 보이는 상태
- ENTRY 한도와 위험 축소 EXIT 정책을 호출부마다 다르게 우회하는 중복 로직

## 가능한 대안

### 대안 A — NAS 완전 소유 runner

final candidate의 canonical payload를 새 불변 중앙 문서로 저장하고 hash와 implementation 호환성을
재계산한다. NAS가 payload로 전용 runner/checkpoint를 만들고 broker/account/data 지표도 중앙에서
생성한다. 24시간 독립 운전에 맞지만 PC A5 비용 projection을 NAS로 옮기거나 같은 계산을 중앙에
추가해야 한다.

### 대안 B — PC Decision owner, NAS 주문 owner

PC가 final run payload와 기존 연구 family를 사용해 Decision을 만들고 인증된 NAS 경계로 보낸다.
NAS는 O2-Md만 수행한다. 기존 연구·일지 계산을 재사용하지만 PC가 꺼지면 자동운영도 멈추며 NAS
24시간 운전 목표와 다르다.

### 대안 C — NAS runner + 중앙 최소 위험 projection

final candidate canonical payload를 중앙에 불변 게시하고 NAS가 전용 runner를 소유한다. O1 상세
체결과 broker 비용 대조에서 자동운영에 필요한 계좌별 당일 실현손익·완결 상태만 중앙 revision으로
만든다. A5의 복기/개선 projection은 PC에 유지한다. ENTRY는 완결 손익만 허용하고 EXIT는 별도
위험 축소 정책으로 명시한다.

## 현재 추천안

대안 C가 현재 책임 경계를 가장 적게 바꾼다. 연구 원본과 사용자 일지는 PC에 남기면서 NAS가 24시간
Decision/주문 owner가 될 수 있다. 새 문서는 범용 전략 배포 프레임워크가 아니라 등록된 두 family의
canonical parameters, execution/session policy, implementation hash와 source final lineage만 담는
`mock_automation_candidate_package/v1` 한 종류로 제한한다. 중앙 위험 projection도 자동운영 계좌의
당일 gate 값만 만들고 A5 전체를 복제하지 않는다.

## Astra가 판단해야 할 핵심 질문

1. 24시간 Decision runner의 단일 owner를 NAS로 확정할지, PC 상시 실행을 허용할지.
2. final candidate payload를 O2 spec 안에 포함할지, 별도 content-addressed package로 저장할지.
3. 현재 implementation hash와 다른 NAS build에서 package를 무조건 차단할지, 등록 family별 호환
   revision을 둘지.
4. 당일 실제 비용이 아직 완결되지 않았을 때 ENTRY를 계속 차단하면서 위험 축소 EXIT를 허용할지,
   EXIT도 broker reconcile/명시 정리 경계로만 처리할지.
5. 당일 손익/비용 gate revision의 owner를 NAS O1 ledger로 둘지, PC A5 projection으로 둘지.

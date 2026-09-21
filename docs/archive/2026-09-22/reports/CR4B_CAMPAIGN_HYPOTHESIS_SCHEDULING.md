> **과거 기록** · 원래 경로: `reports/CR4B_CAMPAIGN_HYPOTHESIS_SCHEDULING.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR4b campaign 가설 예약

## 목적

CR4a의 불변 READY 가설을 기존 지속 campaign과 제한 탐색 실행기에 연결한다. 새 실행 엔진이나
별도 scheduler를 만들지 않고 기존 worker lease, campaign cycle, search job과 trial 원장을 재사용한다.

## 소유 범위

가설의 `research_scope_id`는 생성 시 ID에 포함된다. campaign 자동 예약에 사용할 때는 정확히
`campaign:<campaign_id>`여야 한다. 다른 campaign scope의 가설은 같은 Family·파라미터여도 등록할 수 없다.
자동 가설은 기본 OFF이며 `ResearchCampaignPolicy(auto_hypotheses=True)`로 명시한 campaign만 사용한다.
자동 final 평가는 계속 거절한다.

가설 묶음 등록은 campaign이 일시정지 또는 중지 상태일 때만 가능하다. ID가 모두 v21 원장에 존재하고
scope가 일치해야 한 트랜잭션으로 AVAILABLE 등록한다. 같은 ID 재등록은 멱등이다.

## 예약과 실행

연구 DB v22 `research_campaign_hypotheses`는 다음을 보존한다.

- campaign+가설 유일키
- AVAILABLE 또는 ENQUEUED 상태
- 등록 순서, 예약 순서와 시각
- 대응 campaign job FK

활성 campaign worker만 자동 예약할 수 있다. worker 반복 한 번에 최대 한 가설을 예약하며,
마지막 ENQUEUED Family 다음 등록 Family를 먼저 고른다. 현재 두 Family가 모두 남아 있으면 번갈아 간다.
한 Family만 남으면 그 Family를 계속 처리한다.

기존 수동 campaign job의 dataset, 실행비용, 평가 구간, session profile, 목적·선택 조건과 자원 예산을
template으로 쓴다. 가설의 전체 전략 config와 Family/Factor를 적용하고 hypothesis ref는 해당 ID 하나로
고정한다. parameter grid, ablation, cost stress는 비우며 baseline과 no-trade 두 trial만 실행한다.
template의 입력 계약이 대상 Family와 다르거나 두 trial 예산이 없으면 예약을 거절한다.

job 생성과 AVAILABLE→ENQUEUED는 같은 SQLite 트랜잭션이다. job 생성이 실패하면 가설도 AVAILABLE로
남고, ENQUEUED가 된 가설은 완료·실패·재시작 뒤 AVAILABLE로 되돌리지 않는다. 기술적 재시도는 기존
job/cycle generation으로 처리하므로 독립 증거 수가 늘지 않는다.

## 대기 상태

- `no_registered_hypotheses`: 등록된 가설 없음
- `hypothesis_template_missing`: 기존 수동 기준 experiment 없음
- `hypothesis_space_exhausted`: 등록한 가설이 모두 job에 연결됨
- `campaign_active_backlog_limit`: 활성 job 상한 때문에 새 예약 대기

앞의 세 상태는 화면에 `WAITING_HYPOTHESIS`로 표시한다. 대기 전환은 기존 결과 표를 지우지 않는다.

## 후속

CR4c에서 완료된 개발 결과만 읽어 후속 가설을 생성·등록하고, 실패 부모 반증, 자동 가설 허용 범위와
현재 가설·근거·대기 사유를 사용자가 설정하고 확인하는 화면을 연결한다. 미등록 계산식은 실행하지 않는다.

## 검증 결과

- 가설·campaign·worker 집중 회귀 59개 통과
- 기존 queue/process/campaign 실행/연구 화면 인접 회귀 42개 통과
- 연구 전체 `test_research*.py` 618개 통과

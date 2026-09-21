> **과거 기록** · 원래 경로: `reports/CR2A_PERSISTENT_CAMPAIGN_LEDGER.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR2a — 영속 캠페인 원장

2026-09-16. **원장/예약 경계 완료. 실제 캠페인 worker·GUI 자동 실행은 CR2b 미구현.**
이 단계만으로 PC가 24시간 연구를 자동 실행하거나 앱을 재시작하면 자동 재개하는 상태가 되지는 않는다.

## 변경 목적과 범위

기존 유한 연구의 experiment/job/trial/attempt를 재사용한다. 별도 연구 DB/범용 스케줄러/Manager를 만들지 않았다.
PC 연구 저장소 v10에 캠페인 설정 revision, 사용자 실행 의도, 등록된 job과 예약 cycle을 추가했다.
자동 최종평가와 새 가설 생성은 OFF이며 기존 GUI의 유한 실행·자동 재개 방식은 유지한다.

## 입출력 계약

기존 `application/research_queue.py`의 `ResearchCampaignPolicy`는 backlog/실패 상한/백오프를 고정한다.
기본 unfinished backlog 100개, 실패 3회, 30초 시작/900초 최대 지수 backoff다.
설정 revision은 기존 행을 수정하지 않고 새 revision을 추가하며 PAUSED/STOPPED에서만 변경한다.
기존 cycle의 backoff 판정도 예약 시 고정된 policy revision을 사용한다.

기존 `ResearchRepository`가 아래 API를 제공한다.

| API | 입력 | 결과/주요 조건 |
| --- | --- | --- |
| create_campaign | id/name/policy | 기본 PAUSED, 동일 입력 재등록 false, 설정 불일치 거절 |
| revise_campaign_policy | id/policy/expected_revision | 명시 revision 증가, stale revision·RUNNING 중 수정 거절 |
| set_campaign_desired_state | id/RUNNING·PAUSED·STOPPED | 사용자 의도 저장, 작업 예약/갱신 여부 결정 |
| enqueue_campaign_experiment | id/유한 ExperimentSpec/input_path/source_kind | spec/경로 동결, 기존 experiment/job identity 재사용, 동일 입력 중복 등록 false |
| claim_campaign_cycle | id/owner_token/lease/UTC clock | 준비된 job과 sequence/revision/generation/동결 spec·경로, 없거나 다른 owner가 사용 중이면 None |
| renew_campaign_cycle | id/sequence/owner/generation/lease/clock | 살아 있는 owner와 RUNNING 의도만 갱신 가능 |
| finish_campaign_cycle | id/sequence/owner/generation/outcome/reason/clock | 완료·중단·실패·자원 차단 저장. 완료는 실제 기존 search job completed 확인 필수 |
| retry_campaign_job | id/blocked job | 자원 차단/상한 격리 작업의 명시 재시도, 새 generation으로 예약 |
| load_campaign/jobs/cycles | id | 재시작 후 같은 정책·의도·동결 spec·예약/종료 이력 조회 |

`source_kind`는 hypothesis/new_data다. 예약 우선순위는 준비된 재시도 → 미실행 가설 → 새 자료다.
동결 spec/경로는 descriptor이며 파일 존재·내용 hash/profile 일치 검증은 실제 실행 단계의 통합 reader 책임이다.
현재 등록 API가 NAS에서 새 자료를 찾거나 검증된 input_path를 자동 생성하지 않는다.
spec은 기존 request loader에서 얻은 제한 검색 명세를 전달한다. 일반 단일/비교 요청을 캠페인으로 변환하는 UI는 미구현이다.

## 원자성과 복구

- 기존 experiment 저장/큐 등록 SQL을 connection helper로 재사용한다. 캠페인 작업/experiment/job 등록은
  하나의 BEGIN IMMEDIATE 트랜잭션이며 backlog 실패 시 orphan experiment/job을 남기지 않는다.
- cycle INSERT, sequence 증가, job owner/generation/attempt 증가는 하나의 트랜잭션이다.
  쓰기 실패를 주입했을 때 세 변경 모두 rollback되는 것을 검증했다.
- 같은 캠페인은 살아 있는 cycle 하나만 예약한다. 기존 search job에 다른 살아 있는 owner가 있으면 예약하지 않는다.
  실제 search worker의 owner/generation/commit fence는 기존 v9를 재사용하며 CR2b에서 두 수명을 연결한다.
- lease 만료는 이전 cycle을 INTERRUPTED로 남기고 새 generation으로 예약한다. 이전 owner는 갱신/완료할 수 없다.
- 실제 search job이 이미 완료됐으면 pending/만료된 예약을 완료로 복구하고 새 cycle을 만들지 않는다.
  완료 결과와 cycle 응답 사이에서 worker가 종료된 경우에도 중복 독립 실험을 만들지 않는다.
- PAUSED/STOPPED는 다음 예약·갱신을 막는다. 이미 완료 경계에 도달한 살아 있는 cycle의 마무리는 허용하며
  사용자 의도를 RUNNING으로 덮어쓰지 않는다. 자동 앱 종료 처리 자체는 CR2b에서 연결한다.
- 실패 count는 중단/자원 차단 count와 분리한다. 자원 차단은 자동 재시도하지 않는다.
  ordinary FAILED 재시도 상한에 도달하면 NEEDS_ATTENTION으로 격리한다.
- 완료 job은 unfinished backlog에서 제외하며, 기록과 참조는 삭제하지 않는다.

원장 진행 상태는 RUNNING, PAUSED, STOPPED, WAITING_DATA, RESOURCE_BLOCKED, NEEDS_ATTENTION,
SEARCH_SPACE_EXHAUSTED다. SEARCH_SPACE_EXHAUSTED의 현재 이유는 **registered_jobs_completed**이며
등록된 유한 job 집합이 끝났다는 뜻이다. 전체 가능한 가설을 전부 연구했다는 뜻이 아니다.

## 검증

임시 SQLite DB로 설정/원장 재시작 보존, 동결 request의 외부 변경 차단, 동시 launcher 하나의 claim,
lease 갱신/만료 fencing, 중단/실패 count 분리, backoff/상한·자원 차단/명시 retry,
실제 job 완료 증거·중복 완료·응답 유실 복구, 같은 데이터의 새 가설 등록,
100개 완료 뒤 101번째 등록, 트랜잭션 쓰기 실패/전체 backlog 실패 rollback을 확인했다.
v9 search owner/generation/attempt 행을 v10 적용 전후 전체 tuple로 비교해 동일함을 확인했다.
기존 v1 run/v8 job 보존과 reader/replay/process/search/자원/Qt 화면 회귀도 실행한다.

최종 결과: `tmp/cr2a-regression.log`의 **123개 / 30.380초 / OK / 종료 코드 0**.
변경 Python 4개 AST 문법 검사와 관련 문서/기존 migration 테스트 `git diff --check`도 통과했다.

실제 NAS·사용자 DB·API·키·주문·이미지 재빌드/동기화는 사용하지 않았다.
v10은 additive 로컬 migration이며 사용자 DB에는 테스트 도구로 적용하지 않았다.
제품에서 ResearchRepository를 생성하면 v10 migration을 적용한다. v9 실행기로의 DB downgrade는 지원하지 않는다.

## 다음 구현과 발견 사항

CR2b는 기존 research_process의 유한 실행기와 cycle claim/heartbeat/commit을 연결한다.
DB에 저장된 spec을 실행하며 외부 요청 파일의 재읽기가 캠페인 설정을 바꾸지 않아야 한다.
GUI에 시작/일시정지/중지를 연결하고 창 숨김/앱 종료 시 의도를 보존한 채 worker를 종료한다.
자동 재시작·새 자료 관련성 selector·worker 반복 종료 backoff/격리와 디스크 cap/참조 보호는 아직 미구현이다.

기존 유한 search job의 completed는 당시 trial 예산을 다 쓴 결과일 수 있다.
CR2b에서는 운영 예산 확대/다음 cycle과 실제 공간 소진을 구별해야 한다.
완료된 trial을 유지하며 추가 예산으로 미실행 trial만 처리하고, 기존 유한 요청의 completed 캐시 계약은 유지한다.
현재 CR2a는 같은 job의 동결 spec/예산 변경을 묵시적으로 허용하지 않는다.
새 등록 가설이 동일 데이터에서도 선택되는 경계는 검증했고 자동 새 가설 생성은 CR4다.

수정 파일은 기존 queue/repository 두 곳과 캠페인 테스트/기존 migration 기대 버전이다.
기능 이해에 필요한 주요 코드 파일 수는 기존처럼 두 곳이며, 재사용 connection helper는
등록을 한 트랜잭션에서 처리하기 위한 실제 저장 경계다. 전달 전용 계층은 추가하지 않았다.
DB/API/서버 범위를 재정의하지 않았다. 모델 에스컬레이션: 없음.

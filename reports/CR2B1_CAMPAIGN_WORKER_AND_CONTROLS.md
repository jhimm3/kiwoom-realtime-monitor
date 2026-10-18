# CR2b1 — DB 캠페인 실행과 제어

2026-09-16. CR2a의 PC 연구 DB v10을 재사용한다. 새 서버/API/스키마/Manager는 없다.
CR2 전체 완료가 아니라 실행 연결 하위 단계다. NAS 동기화/이미지 재빌드는 필요 없다.

## 구현 계약

- 기존 limited_search 요청을 연구 화면에서 캠페인에 등록한다. 최초 생성은 PAUSED다.
- 동일 캠페인의 DB/결과 폴더는 고정한다. 다른 가설 요청을 같은 캠페인에 추가할 수 있다.
- 최초 등록 spec/context/input_path는 DB가 원본이다. 파일을 수정하거나 삭제해도 기존 실험을 바꾸지 않는다.
- 기존 검증기를 재사용해 전략/비용/평가/profile/implementation hash를 재구성하고 실제 자료 hash를 확인한다.
- 코드가 바뀌어 저장된 구현 hash와 다르면 기존 연구로 묵시적 실행하지 않고 실패/backoff로 남긴다.
- 저장된 캠페인 정책은 자동 최종평가/새 가설 생성 OFF다. 이미 최종평가를 열어둔 수동 요청도 자동 등록/실행을 거부한다.
- worker는 cycle을 claim한 뒤 existing search job의 lease를 취득한다. 실행 중 약 1초 간격으로 캠페인 lease/의도를 확인한다.
- trial 결과 저장은 BEGIN IMMEDIATE에서 campaign owner/generation/live lease/RUNNING 의도와 기존 search owner/generation/live lease를 함께 확인한다.
  확인부터 결과/attempt 저장까지 같은 트랜잭션이므로 중간 claim 교체/일시정지가 끼어들 수 없다.
- 종료 경계의 미완료 trial은 attempt 중단으로 남기며 완료 trial/candidate는 재사용한다. 리포트/계산 중간 artifact는 완료 trial 증거가 아니다.
- 자원 사전 차단/실행 중 차단은 RESOURCE_BLOCKED로 멈추며 자동 retry하지 않는다. 일반 실패는 원장 backoff/실패 상한을 따른다.
- 별도 worker는 약 1초 대기로 새 등록 작업/DB 의도를 확인하며 대기 중에도 마지막 결과 표를 유지한다.
- 결과 JSON은 개별 임시 파일을 원자 교체해 읽는 쪽에 불완전한 문서를 보이지 않는다.

## UI와 수명

기존 유한 실행 버튼/체크박스/요청 형식은 호환 유지했다. 별도 지속 연구 행에
`캠페인에 요청 등록`, `시작 / 재개`, `일시정지`, `중지`가 있다.
한 dialog의 기존 AuxiliaryProcessManager가 유한 실행 또는 캠페인 worker 한 개를 관리한다.

state_dir의 research_campaign_selection.json에는 version/database/runs_dir/campaign_id만 기록한다.
이는 PC 위치 index이며 공유 설정이나 실행 의도 원본이 아니다. 메인 창이 시작 때 연구 dialog를 생성하므로
저장된 RUNNING 캠페인을 자동 복원한다. PAUSED/STOPPED는 실행하지 않는다.
실행 명령은 --campaign/--database/--runs-dir이고 --request 파일을 다시 읽지 않는다.

창 숨김은 cancel marker로 worker 중단을 요청한다. 앱 종료는 기존 제한 시간 내 worker를 정리한다.
둘 다 DB desired_state를 보존한다. 창 다시 열기/앱 시작은 RUNNING이면 재개한다.
강제 종료에서 소유권을 즉시 풀었다고 가정하지 않으며 이전 lease 만료까지 기다릴 수 있다.
일시정지와 명시 중지는 각각 PAUSED/STOPPED를 DB에 저장한다. 현재 제품의 중지는 기록 삭제가 아니며 재개 가능하다.
worker launch 실패는 의도를 지우지 않고 UI 오류를 표시한다. DB 의도 저장 실패 시 worker를 시작하지 않는다.

## 실험 완료와 예산 소진

기존 유한 search job의 completed는 max_trials 예산을 채웠다는 의미도 가진다.
같은 과학 identity에서 예산을 바꿔도 기존 완료 캐시를 반환하는 기존 동작은 그대로 유지한다.
캠페인은 result.budget.used_trials와 저장 spec의 생성 조합을 확인한다.

- 예산 안에서 모든 등록 조합을 검증: SEARCH_SPACE_EXHAUSTED/등록된 조합 검증 완료.
- max_trials가 전체 조합보다 작음: NEEDS_ATTENTION/trial_budget_exhausted/실험 횟수 한도 도달.
- 다른 유한 실행/캠페인이 더 적은 예산으로 먼저 완료: 기존 등록은 NEEDS_ATTENTION/budget_expansion_required로 대기.
  신규 등록으로 더 큰 예산을 묵시적으로 덮는 것은 거부한다.
- 이전 원장의 budget 증거 없는 완료 job은 기존 CR2a 완료 계약을 유지한다.

SEARCH_SPACE_EXHAUSTED는 등록된 조합 집합의 완료이며 무한한 가설 공간 전체의 검증을 뜻하지 않는다.
이번 단계에서 trial 예산을 자동으로 올리지 않는다. 완료된 결과는 active backlog에 포함하지 않는다.

## 검증 범위

새 execution 테스트는 외부 요청 삭제 후 실제 실행/중복 없음, 실행 중 pause/재개,
결과 저장 직전 pause/owner 교체, cancel 의도 보존, resource 차단/파일 변조/backoff/코드 hash 불일치,
idle 마지막 표 보존, 예산 소진 구분/다른 캠페인 완료 회수, 자동 final 접근 거부를 확인한다.
실제 별도 Python worker가 임시 DB에서 4개 실험을 완료하고 DB PAUSED를 보고 exit 0으로 끝나는 테스트도 있다.
GUI 테스트는 별도 임시 QSettings/DB와 offscreen Qt에서 등록/중복/명령/복원/중지/숨김/예산 표시/
DB 경로 충돌/의도 저장 실패를 검증한다. Qt 객체는 DeferredDelete까지 처리한다.

초기 GUI 테스트 실패는 새 state_dir 미생성이 원인이었으며 재현 오류 문구 확인 뒤 생성하도록 수정했다.
전체 회귀에서 복구 cycle 사유의 기존 문자열 계약 위반 한 곳을 발견해 원래 문자열을 유지했다.
시험 child의 한글 stdout은 Windows 기본 인코딩과 로그 reader가 달라 테스트 전용 PYTHONIOENCODING을 UTF-8로 고정했다.
제품 설정/출력 정책 변경이 아니라 테스트 하네스 수정이며 최종 로그에 reader 예외가 없다.
최종 `tmp/cr2b-regression.log`: **144개 / 43.458초 / OK / 종료 코드 0**, skip/reader thread 예외 없음.
변경 Python 5개 AST 문법 검사와 관련 코드·문서 git diff --check도 통과했다.
실제 사용자 DB/NAS/API/키/주문/이미지/실행 앱은 건드리지 않았다.

## 다음 한 단계

CR2b2: science evidence/identity를 유지하는 명시적 operating-budget revision과 캠페인 전용 완료 캐시 확대.
수정 범위는 기존 queue/search/process/repository/dialog다. 기본 유한 요청은 completed cache를 유지한다.
일시정지 후 revision 확인 → max_trials/max_seconds/자원 예산 revision → 동일 job 재개 계약을 구현한다.
완료 trial은 재사용하고 증가분만 실행한다. 예산 2→4에서 첫 2개의 trial/card/보고 identity를 보존하며 새 2개만 확정해야 한다.
동시 revision/claim, 오래된 worker commit, DB 실패 rollback과 기존 유한 캐시 테스트를 완료 기준으로 한다.

CR2c: 관련 새 자료 selector/자동 등록, worker crash 반복 backoff/격리, 디스크/참조 보호와 보관 정책.
현재 한 PC에서 마지막 선택 캠페인 하나를 복원하며 여러 캠페인의 discovery/선택 UI는 아직 없다.
선택 index 쓰기 실패 뒤 생성된 DB 캠페인의 자동 discovery/복구도 이 후속 범위다.
RESOURCE_BLOCKED retry API는 원장에 있지만 UI 예산 편집/명시 retry는 CR2b2에 연결한다.
실제 규모·24시간 지속 동작은 V1 검증, 다양한 표본 분리 CR3, 새 가설 생성 CR4,
계좌별 일지 피드백 A5와 자동 모의운영 O2-M은 후속이다. 연구는 주문 API를 호출하지 않는다.

추상화 비용: 주요 경로는 기존 UI→process→runner/repository이며 전달 전용 계층/새 호출 깊이는 추가하지 않았다.
동결 요청 재구성은 기존 검증 함수, 실제 실행은 기존 finite executor를 재사용한다.
모델 에스컬레이션: 없음.

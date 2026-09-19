# CR2c2a — 같은 범위 준비 입력의 자동 등록

2026-09-16. CR2c1 누적 소스를 유지한 구현. 현재 평가 명세의 날짜가 고정되어 있으므로 같은 기간의 보완 근거부터 연결한다.
CR2c2 전체/24시간 자동 연구 완료 판정이 아니다.

## 책임과 사용 방법

기존 연구 창에서 일시정지하고 worker 종료를 기다린 뒤 `새 자료 폴더`를 연다.
기준 실험, 완성 export 폴더들이 들어 있는 상위 폴더, 자동 등록 체크를 저장하고 시작/재개한다.
체크를 해제해 끌 수 있으며 설정 저장은 worker를 시작하지 않는다. 실험당 source 하나다.
폴더 변경/오류 재시도 저장은 이전 acceptance를 보존한다. GUI에서는 작은 DB 설정만 읽고 쓴다.

실제 읽기/검증/등록은 기존 `research_process.py` worker 안에 있다.
기존 `research_data_source.py`가 scope/fingerprint를 계산하고 기존 Repository가 원자 등록을 소유한다.
새 Manager/Service/worker 프로세스/API/일반 플러그인 구조는 추가하지 않았다.
호출 경로는 dialog → process/Repository, process → 기존 reader/Repository다.

## 계약

| 입력/상황 | 결과 |
| --- | --- |
| source 설정 | campaign+기준 job identity, 명시 root, enabled를 연구 DB v13에 저장. RUNNING/활성 worker 중 편집 금지 |
| 첫 scan | 기준 파일 dataset ID/revision hash/code implementation hash를 확인하고 scope와 baseline fingerprint를 원자 저장 |
| 정상 scan | 즉시 첫 확인 후 정상 완료 기준 60초 간격. 직접 하위 manifest 있는 자료만 검사. symlink 디렉터리는 제외 |
| 연구 범위 | captured range/kinds/subject/session/universe/order contract가 같아야 함. 평가·비용·날짜·전략·파라미터·seed를 이동하지 않음 |
| 관련 근거 | 현재 두 등록 Family/시장 보고가 소비하는 ranking/top20_membership/KRX minute_bar와 테마 근거. 전송 ordinal/export watermark/id는 fingerprint에서 제외 |
| 중복/무관 변경 | 같은 fingerprint, 무관 kind/비KRX 봉만 추가된 자료는 새 job을 생성하지 않음. 다른 날짜/종목 범위는 out_of_scope |
| 신규 근거 | 기준 scientific spec과 현재 운영 예산을 새 dataset identity에 복사. source_kind=new_data로 job/budget/acceptance를 한 트랜잭션에 등록 |
| 소유권/의도 상실 | worker campaign/owner/generation/live lease, source enabled, RUNNING 의도를 초기화·등록 트랜잭션에서 확인. 더 이상 등록하지 않음 |
| backlog 포화 | acceptance/job을 만들지 않고 정상 대기. 실패 횟수를 늘리지 않으며 다음 scan에 다시 시도 |
| 파일/검증/자원/등록 오류 | source별 독립 backoff/상한. 기본 첫 30초/두 번째 60초 후 연속 3회면 NEEDS_ATTENTION. 기존 연구와 worker 실패 횟수는 별도 |
| source 오류 표시 | 재시도 대기/격리 동안 화면 상태에 계속 남으며 폴더 설정에서 저장된 상태/이유를 확인할 수 있음 |
| 설정 재저장 | 원인 보완 후 저장하면 source의 실패/다음 scan만 초기화. 과거 acceptance/기존 job은 보존 |

v13 `campaign_input_discovery`는 source/acceptance 테이블과 campaign source 조회 인덱스만 추가한다.
기존 run/job/cycle/budget/worker/attempt 행을 변경하지 않는다. DB downgrade는 지원하지 않는다.
acceptance는 (source,fingerprint) PK와 (source,path) UNIQUE로 중복·등록 경로 변경을 차단한다.
source 초기화와 등록은 BEGIN IMMEDIATE를 사용하며 acceptance 쓰기 실패 때 job/예산도 rollback된다.

## 자원과 파일 발행 경계

기존 ResourceGuard의 RSS/preflight/CPU batch 양보를 사용한다.
한 source에 최대 30초(실험 max_seconds가 더 작으면 그 값), 1000개 준비 자료, 10000개 directory entry를 허용한다.
manifest는 기존 1MiB 상한을 적용한다. 초과하면 source를 backoff하고 기존 연구는 계속한다.
설정이 없거나 scan 시각 전이면 데이터·job 전체 이력을 읽지 않는다.
이미 acceptance가 있는 경로는 작은 manifest hash만 비교해 대용량 본문 재읽기를 생략한다.

자료는 새 완성 디렉터리로 발행해야 한다. 승인된 manifest 변경은 오류이며 기존 export를 덮어쓰지 않는다.
승인 경로의 관측 파일을 manifest 변경 없이 외부에서 몰래 수정한 경우를 매 scan마다 전체 파일 hash로 재검증하지는 않는다.
최초 승인/실제 실행은 기존 reader의 파일 hash 검증을 사용한다. 이는 신뢰하는 불변 export 생산 경계의 최적화다.
폴더에 아직 manifest가 없는 미완성 입력은 건너뛰고 manifest가 있는데 파일이 잘렸으면 source 오류로 처리한다.

## 검증과 남은 범위

- 임시 연구 DB/fixture/offscreen Qt만 사용. 실제 사용자 DB/NAS/API/자격증명/주문은 사용하지 않았다.
- 관련 입력 등록 후 기존 가설을 두 번의 별도 cycle로 실행, 중복·watermark만 변경·무관 kind·새 날짜/종목/종류를 구별했다.
- 재시작 후 throttle/acceptance 유지와 승인 경로의 데이터 재읽기 생략을 검증했다.
- 오류 격리/상한, 소유권 상실/등록 직전 일시정지, backlog 완료 후 재시도, acceptance 실패 rollback을 검증했다.
- GUI의 설정 저장/끄기·RUNNING 편집 금지와 설정 시 데이터 파일을 읽지 않음을 검증했다.
- 최종 전체 연구 회귀 **217개 / 102.479초 / OK / 프로세스 exit 0**. `tmp/cr2c2a-regression.log`에 기록했다.
- 변경 Python 10개 AST/공백과 관련 `git diff --check` 통과. 테스트 로그에 skip/오류/자식 출력 읽기 예외 없음.

다음 CR2c2b는 기존 NAS export API로 같은 명시 범위의 새 완성 입력을 자동 준비하는 연결이다.
현재 기능은 NAS DB를 자동 감시/다운로드하는 기능이 아니라 준비된 PC 파일의 자동 등록이다.
새 날짜 확장은 CR3 평가 구간 정책과 함께 연결하며 디스크 cap/참조 보호/보관은 후속이다.
실환경 24시간 대량 입력 부하는 V1이다. 자동 최종평가/새 가설/주문은 계속 OFF다.
이번 변경은 PC 연구 경로로 NAS 배포/재빌드는 하지 않았다. 모델 에스컬레이션 없음.

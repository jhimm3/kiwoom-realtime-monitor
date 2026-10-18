# 연속 전략 연구와 계좌별 매매일지 — 설계 재검토 결정

검토일: 2026-09-13. 상태: **설계 결정 완료 / 아래 확장과 결함 수정은 구현 전**.

사용자가 요청한 설계 재검토 결과다. 현재 코드·기존 개발계획·처음 제시한 세 설계 문서의 목적을 함께 기준으로 삼았다. 문서의 미래 기능 설명을 현재 구현 사실이나 주문·배포 승인으로 해석하지 않는다. 이번 작업은 제품 코드를 수정하지 않는다.

Sol의 단계별 명세는 [구현 계획](CONTINUOUS_RESEARCH_ACCOUNT_IMPLEMENTATION_PLAN.md)에 있다. 이 문서는 이전의 “Astra 판단 필요” 여섯 질문을 해결하며, 기존 R2/D8/O2 계획 중 충돌하는 부분에 우선한다.

## 1. 사용자가 만들려는 기능

앱에 원칙을 등록하면 PC가 여러 실험을 이어간다. 예를 들어 “거래대금이 늘어나는 돌파가 유리하다”에서 시작해 기준값, 순위 지속 조건, 진입 시간을 바꿔 보고, 다른 종목과 날짜에서도 결과가 유지되는지 검사한다. 결과가 좋지 않아도 실패 이유와 시험 범위를 남기고 다음 가설을 만든다. 사용자는 궁금한 값 하나를 직접 바꾸는 비교도 할 수 있다.

새 자료가 없어도 아직 시험하지 않은 가설은 기존 자료로 연구한다. 새 거래일 자료가 쌓이면 고른 후보를 새로운 기간에서 검증한다. 조합을 소진했거나 자료가 부족하면 이유를 표시하고 대기한다. 같은 실험을 무의미하게 반복해 CPU를 채우는 것은 연속 연구가 아니다.

연구와 모의·실제 매매의 차이를 다음 연구에 반영한다. 후보를 모의계좌에 자동 적용하는 기능은 계좌·전략 버전·기간·자금·손실 한도를 고정한 별도 O2 정책을 거친다. **현재 수동 모의주문 기능을 자동 모의운영 완료로 간주하지 않는다.** 실계좌 적용은 별도 승격 단계다.

## 2. 최종 구조 선택

**기존 앱 확장 + PC 연구 프로세스 + 단일 매매일지 DB의 계좌 분리**를 채택한다. 새 앱, NAS 연구 연산 서버, 범용 워크플로 엔진은 만들지 않는다.

흐름: NAS 공통 자료 → PC 동결 데이터 → 사용자 원칙/자동 가설 → 시뮬레이션 → 다른 종목·기간 검증 → 다음 가설. 최종 후보 → 별도 모의운영 정책 → 모의 전진평가 → 계좌별 일지와 차이 분석 → 다음 연구.

| 소유자 | 책임 | 유지할 경계 |
| --- | --- | --- |
| NAS | 공통 시장·뉴스 원본, 가용시각/품질, 검증된 계좌 출처, 기존 모의 실행 원장 | 수집·REST 우선순위를 연구가 방해하지 않음 |
| PC research_process.py | 지속 캠페인 선택, 유한 실험 실행, 자원 양보, 재시작 복구 | Qt 메인 루프 밖, 기본 동시 trial 1개 |
| research.sqlite3 | 가설 계보, 입력·전략 버전, 시도 이력, 검증·선택 근거 | 시장 원본·주문 권한·사용자 복기와 분리 |
| 매매일지 DB | 계좌별 자료·사용자 복기·연구 연결 | 뉴스·차트 공통 자료는 복제하지 않음 |
| 기존 O1/O2 | 모의 주문 상태, 전진평가, 명시적 승격 정책 | 연구 worker가 주문 transport를 호출하지 않음 |

지속 연구는 PC와 연구 프로세스가 실행 중일 때 동작한다. 창 숨김은 계속 실행, 앱 종료는 저장 후 중단, 앱 재시작은 저장된 실행 의도에 따라 재개한다. 절전·전원 종료 중에는 계산하지 않는다. Windows 서비스·자동 로그인 시작 등록은 첫 구현 범위에 넣지 않는다.

## 3. 현재 코드 감사 결과

경로의 application/, infrastructure/, presentation/은 src/kiwoom_monitor/ 아래다. 아래는 감사 당시 결함이다. **2026-09-13 CR0에서 F01~F05와 F07의 claim/fencing·active backlog 부분을 수정했다.** F06, F07의 장기 실측, F08~F12와 전체 OOS 입력 격리는 남아 있다.

| ID | 확인한 사실과 근거 | 영향 / 선행 조치 |
| --- | --- | --- |
| F01 | **수정됨.** DB v9 `research_trial_attempts`가 INTERRUPTED를 분리하고 결과 commit과 attempt 완료를 한 트랜잭션으로 처리한다. | 중단 trial은 결과·예산을 소비하지 않고 다음 실행에서 처음부터 재시도 |
| F02 | search identity에 전체 strategy/execution/evaluation 없음. stdout 재현: 기준 목표값 500→900, 초기자금 100만→200만인데 experiment/job ID 동일 | 변경한 전략 대신 예전 완료 결과 반환. 전체 과학적 입력의 v2 식별자 필요 |
| F03 | outcome_from_report가 SEALED 이외의 fold를 합산하고 ELIGIBLE_WITH_SEALED_HOLDOUT을 INELIGIBLE로 처리. stdout 재현: 봉인 시 부적격, 개방 시 validation 10+OOS 900=선택점수 910 | 최종 검증이 후보 선택에 유입. 선택 지표와 최종 검증 분리 |
| F04 | generate_trials가 Cartesian list 전체 생성 뒤 한도 검사. 작은 재현에서도 한도 2를 넘는 4개 모두 생성 후 거부 | 조합 폭증 방어 실패. 사전 개수 검사와 지연 생성 |
| F05 | **수정됨.** dialog 소유 single-shot QTimer가 체크 해제·취소·stop·창 닫기에서 정지하고 실행 직전 의도를 재검사한다. | 예약 뒤 사용자가 중지한 worker는 다시 시작하지 않음 |
| F06 | CPU duty는 trial 뒤 단일 sleep. memory_mb는 범위 검사만 존재 | 긴 trial의 CPU·메모리 상한을 보장하지 못함. 짧은 계산 구간 양보와 실측 필요 |
| F07 | **코드 경계 수정됨.** queued 조건 원자 UPDATE와 owner token·generation·heartbeat를 claim/renew/finish/result commit에 적용하고 보존 한도는 active job만 센다. | 오래된 worker 쓰기는 거절됨. 장기 운전 중 heartbeat 간격과 충돌 실측은 V1에 남음 |
| F08 | scripts/run_research.py가 관측마다 전체 자료 replay, 결과 메모리 누적. reader도 파일 전체 읽기 | 여러 날짜 확장 전 profiling 필요. 실제 병목 비중은 아직 측정하지 않음 |
| F09 | replay/simulation 모두 PaperExecutionEngine 호출. 종목/날짜/시간 보고는 같은 run의 사후 breakdown | 독립 holdout·다기간 재검증·broker 모의투자와 구별 |
| F10 | fill/cost PK, FIFO, lookback, 수동 묶음, snapshot 조회, sync에 account scope 없음 | 화면 필터만으로 분리 불가. 실제 사용자 DB가 이미 섞였는지는 조사하지 않음 |
| F11 | QueryClient는 payload/has_next/next_key만 반환. failover가 페이지마다 경로 변경, 계좌 context 없음 | 다른 계좌/커서 혼합 가능. 계좌 전용 조회 계약 필요 |
| F12 | central_journal_sync 구 클라이언트는 모르는 컬럼 제거. journal_news_links도 기존 group_id 참조 | 필드만 추가하면 구 앱이 계좌를 합칠 수 있음. 새 컬렉션과 기존 키 보존 필요 |

D7의 SEALED는 현재 보고서 수치 표시 제한이다. runner는 전체 입력을 계산하고 전역 실행·성과·outcome을 저장한다. 이를 자동 가설 생성기에 공개하면 봉인 효과가 없다. v2에서는 최종 구간 입력과 읽기 경로 자체를 탐색에서 분리한다.

## 4. 연속 연구 계약

### 수명이 다른 세 개념

- **Campaign**: 계속 유지되는 연구 목적, 등록 Family, 탐색 범위, 검증 정책, 사용자 실행 의도. 정책 변경은 revision.
- **Cycle**: 동결 데이터와 후보 목록, 평가 구간을 가진 유한 작업 묶음. 같은 자료의 다음 가설도 새 cycle이 될 수 있음.
- **Trial / Attempt**: trial은 한 전략·입력·평가의 논리 실험, attempt는 계산 시도. 재시작은 attempt만 추가하며 독립 실험 횟수로 세지 않음.

campaign에는 평생 trial 한도를 두지 않는다. cycle별 후보 수, 진행 backlog, CPU 목표, 메모리·디스크 예산은 둔다. 실패/무거래/자료부족도 기록한다. 재현 가능한 코드 오류는 무한 재시도하지 않고 해당 입력을 격리해 다른 정상 작업을 계속한다.

새 작업 우선순위는 중단 trial 재시도 → 기존 자료의 미실행 가설 → 관련 신규 입력/다음 검증기간 성숙에 따른 재검증이다. 없으면 WAITING_DATA 또는 SEARCH_SPACE_EXHAUSTED로 backoff한다. 중앙 watermark 변경은 확인 신호일 뿐 실행 조건 자체가 아니다. 무관한 새 관측 때문에 재계산하지 않는다. 완료한 동일 실험은 재사용하되 새 검증 표본으로 세지 않는다.

### 식별자와 저장

ResearchSpec/v2에는 bundle hash, baseline 전체, Family/Factor 구현 hash, 체결·비용·수량 정책, 실제 split 구간/종목 분할, 초기 상태·warmup·purge, 선택 지표·적격/결측 규칙을 넣는다. 외부 뉴스·테마·일지 근거를 쓰면 동결 revision도 넣는다. _implementation_hash에는 replay 등 실제 행동 의존성도 포함한다.

trial_evidence_key는 변형을 적용한 최종 전략 전체와 실제 데이터·실행·평가 입력을 정규화한 hash다. 원래 baseline의 evidence ref·변형 표현·부모는 cycle/comparison 계보에 보존하며 재사용 key와 구별한다. 같은 실험이 다른 parent/campaign에서 발견되면 결과를 재사용하고 계보 링크는 각각 보존한다. 생성 정책·부모·선택 근거는 cycle에 고정한다. CPU·메모리·slice·owner·파일 경로는 근거 ID에서 제외한다.

v1 결과는 보존하고 legacy_protocol로 구별한다. 누락 baseline을 현재 파일로 추정해 v2로 인증하지 않는다. 불변 v1 CANCELLED를 성공으로 덮어쓰지도 않는다.

기존 run/search job/event를 재사용한다. 새 영속 책임은 campaign, cycle, attempt, 일반 연구 가설 계보에 한정한다. C1 뉴스 맥락 hypotheses와 전략 연구 가설을 같은 표에 무리하게 섞지 않는다. 연구 DB v8 다음 명시적 migration을 만들되 구현 시 최신 버전을 재확인한다.

### 중단·재개·자원

초기 checkpoint는 **완료 trial 경계**다. slice 만료 시 다음 trial을 시작하지 않고 진행 중 유한 trial은 완료한다. slice는 정확한 강제 종료시각이 아니다. 사용자 중지·충돌로 미완료인 trial은 다음에 처음부터 재계산한다. 전체 PaperExecutionEngine 직렬화는 첫 버전에 넣지 않는다.

DB의 결과, attempt 완료, job 진행 위치는 한 트랜잭션으로 확정한다. 결과 파일은 DB를 원본으로 원자 교체하며 게시 전 충돌하면 DB에서 복원하고 완료 trial을 재계산하지 않는다. 계산 중 진단은 attempt별로 격리해 선택에서 제외한다. claim/renew/finish는 owner token과 generation을 확인하며 임대를 잃은 worker의 쓰기를 거절한다.

낮은 우선순위·동시 실행 1개를 유지한다. CPU duty는 짧은 관측 batch마다 계산/휴식 비율을 조절하는 **목표값**이며 순간 상한이라고 표시하지 않는다. 휴식·취소·heartbeat 확인을 짧게 나눈다. 실제 RSS와 입력 규모를 검사하고 한도 초과는 RESOURCE_BLOCKED로 남긴다. 입력 일부를 조용히 생략하지 않는다.

장시간 trial은 먼저 측정한다. 전체 반복 스캔 병목이 확인되면 기존 replay에 정렬 인덱스/증분 cursor를 추가하고 기존 논리 결과와 비교한다. 세션별 손익을 더해서 연속 포지션·현금 시뮬레이션을 대체하지 않는다.

max_retained_jobs를 평생 작업 수 상한으로 쓰지 않는다. 완료 자료는 보관 상태로 전환하고 active backlog에 개수 제한을 둔다. 디스크 예산에는 동결 입력·결과·임시 파일을 포함한다. 참조 없는 재생성 가능 임시 자료부터 정리하고 근거 원본·선택 후보는 보호한다. 보호 자료만으로 한도에 도달하면 이유를 표시해 대기한다.

## 5. 데이터와 검증 설계

### 여러 날짜의 입력

중앙 export는 최대 24시간(database.py의 research export range 검사)이고 observation kind는 ranking/top20_membership/minute_bar다. 이를 없애 대량 API를 만들기보다 **기존 일별 export를 묶는 PC bundle manifest**를 추가한다. 자식 ID/hash·watermark·일자·조회 범위·revision 목록·sidecar hash를 보존한다. 서로 다른 날의 ordinal을 한 dataset의 ordinal처럼 이어 붙이지 않는다.

bundle은 available_at과 확정된 순서로 읽는다. 겹치는 export의 동일 source revision은 한 번만 소비하며 충돌은 오류다. 테마는 시작 이전 마지막 가용 snapshot과 기간 중 변경분이 필요하다. pagination/truncation·누락·세션 달력·조정주가 기준을 구분한다. 최신 테마·장후 정정을 당시 알려진 입력으로 바꾸지 않는다.

NAS 저장과 연구 reader 지원은 다르다. 첫 연속 연구는 기존 strict KRX 분봉·기록된 TOP20 범위다. H1 1초, NXT 08~20시, VI/관심 cohort, 뉴스 Factor는 해당 reader·Family capability가 연결된 뒤 활성화한다. 없는 틱·호가·진입 전 초자료를 복원했다고 표시하지 않는다.

실행 가능, 부분/결측, 검증 표본 부족을 구별한다. 하루 자료로 실행을 시험할 수 있어도 여러 날짜에서 유효하다고 판정하지 않는다. 실제 NAS에 충분한 자료가 며칠치 있는지는 이번에 확인하지 않았다.

### 개발 검증과 최종 검증

반복 선택에 사용한 자료는 다음 가설에 대해 미지의 시험지가 아니다. 많은 후보 중 최고값을 고르는 과정에는 우연히 좋은 전략을 선택할 위험이 있다. [백테스트 과적합 연구](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), [적응적 분석과 holdout 재사용 연구](https://papers.nips.cc/paper_files/paper/2015/hash/bad5f33780c42f2588878a9d07405083-Abstract.html).

아래는 이 프로젝트의 설계 선택이며 논문이 특정 분할 비율을 권고한다는 뜻은 아니다.

| 구간/관점 | 역할 | generator 접근 |
| --- | --- | --- |
| TRAIN / 개발 VALIDATION | 시간순 rolling 검증·후보 탐색 | 성과·실패·결측. 반복 사용 사실 기록 |
| 종목 holdout | 특정 종목에만 맞는 규칙인지 확인 | 피드백에 사용하면 개발 종목 검증으로 분류 |
| 시간·시장 상태 breakdown | 특정 시간/상황 편중 확인 | 판단 당시 정보로 분류한 수치와 분모 |
| FINAL_OOS | 잠근 후보 묶음의 미사용 기간 평가 | 입력·거래·점수를 generator에 제공하지 않음 |
| 모의/실제 일지 | 비용·지연·규칙 준수 차이 연구 | 계좌·기간·품질별 동결 근거. 사용 뒤 개발자료로 분류 |

종목은 고정 salt+stock_code의 hash로 분할하고 결과에 따라 재추첨하지 않는다. 신규 종목도 같은 규칙이다. 시점별 테마/시총 이력 확인 전에는 층화를 기본으로 삼지 않는다. 동시 시장 움직임이 있으므로 종목 수를 독립 거래일 수처럼 세지 않는다.

FINAL_OOS는 탐색 bundle에서 제외하고 별도 evaluator 입력으로 제공한다. 후보 목록·기준·창을 먼저 동결하고 접근/노출 이력을 남긴다. 창은 기본적으로 한 번의 동결 후보 batch에 쓴다. 결과를 보고 수정한 후보를 같은 창에서 다시 “최종 검증”하지 않는다. 기술적 재시도는 동일 candidate/input의 attempt만 허용한다.

최종 결과를 보고 새 가설을 만들 수도 있다. 이때 그 창은 EXPOSED_DEVELOPMENT로 내리고 다음 미사용 기간에서 새 최종 평가를 한다. 매 실험마다 사용자 승인을 받지 않고 campaign에 정한 자동 평가 정책을 따른다. 새 검증기간이 부족해도 개발 탐색은 계속할 수 있다.

D7 continuous_state_and_cash/v1은 기존 보고서에 보존한다. v2의 독립 평가 partition은 초기 현금/빈 포지션으로 시작하고 warmup 동안 신규 진입을 막는다. partition 내부 날짜는 원래 보유·현금 정책을 이어간다. 경계 미청산 포지션의 censor/평가를 명시한다. v1/v2 또는 독립 partition 손익을 하나의 연속 계좌 성과로 합치지 않는다.

공통 자금 포트폴리오와 종목별 신호 안정성도 구별한다. 종목별 독립 수익 합을 포트폴리오 수익이라고 쓰지 않는다. 전체 MDD는 같은 run의 시간순 자산 경로에서 계산한다. 진입 시점 Factor에 종료 시점 _final_market_regime을 소급해 붙이지 않는다.

데이터/시스템 적격성과 성과 기준을 분리한다. 최소 거래·활동일·종목 수·비용 스트레스·편중 한도는 campaign 정책으로 동결한다. 임의 공통 수익 기준을 수익성 보장값으로 하드코딩하지 않는다. 전체 고유 실험 수, 재시도, 재사용 데이터 기간, 부족한 검증 범위를 표시한다. 최종 후보 batch 크기도 사전에 제한하고 전체 후보 결과를 보존한다. 여러 후보 중 최고값만 보고 통계적 유의성을 주장하지 않으며, 유의성 지표를 추가할 때는 후보 선택 수를 반영한 별도 검증 계약을 요구한다.

## 6. 새 가설 생성 범위

처음에는 현재 두 Family의 임계값 변경, 선택 Factor 제거/추가, 등록된 조건 조합과 허용 상호작용을 자동 생성한다. 신규 범용 DSL·플러그인·임의 Python 실행기는 만들지 않는다.

ResearchHypothesis에는 parent IDs, Family/변형 규칙 버전, 바꾼 조건, 기대 이유, 생성에 사용한 개발 evidence refs, 실행 가능 여부, 중복 key를 둔다. 실행한 가설은 실패까지 보존한다. C1 뉴스 가설과 별도 의미다.

생성 순서는 기준선·무거래 → 한 변수 영향 → 후보 주변/실패 설명을 검증할 허용 조합 → 다른 Family 순환이다. 특정 Family 독점을 막고 표현만 바꾼 중복을 제거한다. 실패한 부모에서도 등록된 반증 가설은 만들 수 있다.

미등록 계산식은 입력 요구·검증 방법이 있는 DRAFT_REQUIRES_IMPLEMENTATION으로 저장한다. LLM은 선택적인 설명/초안 보조이며 실험마다 유료 AI 호출을 필수로 만들지 않는다. 한 값의 수동 비교도 같은 trial/evidence 경로를 사용하고 자동 연구 baseline을 조용히 바꾸지 않는다.

## 7. 계좌 출처와 분리

### 계좌 신원

공식 ka00001은 현재 토큰의 acctNo를 반환하고 00/04에는 계좌 필드 9201이 있다. 연결·자격 교체 시 확인하며 상품 구분을 포함한 전체 계좌값을 비교한다. kt00007의 ord_tm은 주문시간이므로 정밀 체결시각으로 가정하지 않는다. [키움 공식 API 스펙](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json). 실제 응답은 구현 단계에서 확인한다.

AccountScope는 broker=kiwoom, environment=real|mock, account_ref=지속 UUID다. 동일 계좌의 키 교체·별칭 변경은 같은 ref, 다른 계좌/환경은 다른 scope다. 시뮬레이션 run은 broker 계좌로 위장하지 않고 연구 DB에 둔다. unknown/legacy-unassigned는 migration·기존 자료 읽기 전용이며 신규 자동 수입에 사용할 수 없다.

NAS 사용자 범위 registry가 검증 계좌↔UUID 매핑을 소유한다. 비교 지문은 보호된 서버 키의 HMAC이며 키/registry는 별도 보호 백업한다. App Key hash·계좌 뒷자리·서버 URL을 ref로 쓰지 않는다. 원문 계좌값은 확인 요청 메모리에서 취급하고 로그·일지·연구 manifest·일반 설정 sync에는 남기지 않는다.

직접 조회는 최초 NAS 연결 시 같은 계좌를 확인해 ref와 로컬 검증 바인딩을 저장한다. 로컬 비교 지문/비밀은 OS 보호 저장소에 두고 일반 콘텐츠로 공유하지 않는다. 신규 PC는 재확인 후 같은 ref를 받는다. NAS 미연결 신규 직접 계좌는 로컬 scope로만 시작하고 나중에 검증된 registry alias로 연결한다. 확인 없이 기존 scope로 병합하지 않는다. origin_scope와 기존 ID는 불변이고 canonical_scope만 검증된 alias로 해석한다. alias는 연쇄·순환·환경 간 연결을 금지한다. 연결 뒤 중복은 canonical 계좌의 broker 거래일·주문·체결 ID로 한 번만 집계하며 상세 ID 없는 충돌은 미확인으로 남긴다. 바인딩/registry 복구 실패는 IDENTITY_RECOVERY_REQUIRED로 표시한다.

신원 확인도 해당 환경의 기존 client/broker/limiter를 거친다. ka00001 원문은 범용 REST 캐시/관측 저장에서 제외한다. 원문 전달이 필요한 등록은 인증된 암호화 연결로 제한한다. 현재 NAS의 지원 여부는 구현 시 확인한다.

**2026-09-13 A4b 구체화:** [직접 연결 재검토 결정](A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)에 따라 PC의 fresh ka00001 결과를 인증된 HTTPS로 NAS의 기존 검증 registry와 대조한다. 이는 신뢰하는 소유자 PC의 관측값 일치 확인이며 NAS가 PC 키 소유를 독립 증명하는 절차가 아니다. 조회 요청으로 NAS registry/profile binding을 생성·교체하지 않는다. 미등록 계좌는 local origin을 유지하고 기존 NAS broker 검증 후 결합한다. PC의 보호 secret/지문·profile revision과 NAS binding 근거를 구별하고 A→B→A에서 origin을 보존한다. 현재 앱 접속 설정은 HTTP이며 HTTPS 종단은 구현 후 운영 검증이 필요하다. 공개 v1 ka00001 원문 전달은 차단하고 내부 broker 조회는 유지하는 좁은 정책 변경도 이 보완 범위다. 현재 코드의 연결·동기화 누락은 위 문서의 0a~0c부터 고친다.

### 조회 세션

시세 QueryClient와 /api/v1/kiwoom/query는 유지한다. 계좌 전용 AccountQueryContext/v2와 account-query API를 추가한다. 응답 context는 실제 인증 프로필에서 만들며 요청 ref를 복사하지 않는다.

한 페이지 묶음의 account_ref/environment/binding_revision/transport를 고정한다. cursor도 context·API ID·본문·만료에 묶는다. NAS A→직접 B는 ACCOUNT_CONTEXT_MISMATCH로 저장 중단. NAS A→검증된 직접 A는 미완료 페이지를 폐기하고 첫 페이지부터 새 batch로 재조회한다.

체결·비용·후착 worker 결과는 UI 선택값이 아닌 요청 context를 따른다. 불일치 비용을 일반 조회 실패로 삼키지 않는다. 빈 계좌·v2 미지원 서버에서는 확인된 신규 계좌 행을 만들지 않는다. v1은 legacy 보기로만 제공한다. 병행 검증도 동일 계좌 확인 전에는 계좌 TR을 비교하지 않는다.

현재 NAS는 기본 인증 프로필과 별도 mock 프로필 구성이다. 등록된 계좌라도 현재 사용 가능한 검증 프로필이 연결돼야 신규 TR/WS를 받을 수 있다. 일지 계좌 선택은 공통 시세용 키·WS를 바꾸지 않는다. 새로운 다중 사용자 권한 체계를 추가하지 않고 현재 NAS 인증 소유자 범위를 따른다.

실전 5회/초와 모의 1회/초 분리를 유지한다. 복수 계좌를 이유로 한도를 늘리거나 mock 조회를 실전 queue에 합치지 않는다.

### 저장·이전·동기화

단일 일지 DB에 scope 컬럼/복합키를 추가한다. 체결·비용·복기·수동 회차/유형·진입 snapshot·보완 작업·파생 분석·연구 링크 모두 대상이다. FIFO·과거 원가 조회·합치기·비용 배분·snapshot 결합에도 필수 전달한다. 화면 필터만으로 완료하지 않는다.

기존 자료는 unknown/legacy-unassigned로 보존한다. fill_key/group_id/execution_key와 뉴스 DB journal_news_links·사용자 JSON 참조를 유지한다. 새 자료부터 버전 있는 scoped key를 발급한다. 과거를 모두 real로 추정하지 않는다.

기존 자료의 계좌 배정은 원본·복기·뉴스 연결·충돌을 미리 보여주는 별도 이전 기능이다. 기본 migration에서는 배정하지 않는다. 신규 수입과 legacy가 유사하면 겹침 후보를 표시하고 전체 합계에서 자동 이중 합산하지 않는다. 체결 ID 없는 과거 TR 행을 임의로 정밀 체결로 쪼개지 않는다.

sync는 journal_v2_* 새 컬렉션과 tombstone namespace를 사용한다. v1은 legacy 전용이며 v2를 이중 업로드하지 않는다. 구 앱이 새 행을 받고 계좌 정보를 제거할 수 없어야 한다. 뉴스 연결·삭제·보완도 version 경계를 따른다. 전체 SQLite 백업은 유지하고 복원 버전 검사·migration 검증을 추가한다.

### 실시간과 모의 일지

ka00001 확인 → 9201 대조 → raw 계좌 제거+scope 부착 → NAS envelope/직접 signal → scoped snapshot으로 연결한다. 현재 화면 계좌와 들어오는 체결 계좌를 혼동하지 않는다.

O1 원장을 원본으로 두고 일지에 **증분 읽기 projection**을 만든다. cursor와 projection 쓰기는 원자적이며 (scope, ledger, source_event_id)로 멱등화한다. run/intent는 계보이고 broker 체결 중복 key는 계좌·거래일·주문·체결 ID다.

상세 FILL만 확인 체결로 표시한다. BROKER_FILL_AGGREGATE는 수량/상세 미확인에 사용하며 가짜 가격·시각을 만들지 않는다. 후착 상세는 누적 복구량에 다시 더하지 않는다. kt00007과 O1이 겹치면 독립 거래로 더하지 않고 source precedence/대조 상태를 남긴다.

사용자 메모는 자동 연구가 덮어쓰지 않는다. 연구에 반영할 때 계좌·환경·수집시각·전략 버전·체결 품질을 동결한다. 사후 설명은 당시 진입 Factor가 아니다.

## 8. 이전 여섯 질문의 결정

| 질문 | 결정 |
| --- | --- |
| 계좌 출처 계약 위치 | 시세 계약 유지, 계좌 v2 context/query + ka00001/9201 확인 |
| failover 계좌 변경 | 다른/미확인은 실패, 같은 계좌만 전체 batch 재시작 |
| cycle 조건 | 미실행 가설 또는 관련 신규 입력·기간 성숙. watermark 단독 조건 폐기 |
| 종목 holdout | 버전·salt 고정 stock_code부터. 과거 이력 없는 층화 금지 |
| 자동 가설 | 등록 Family/Factor/변형 규칙. 새 계산은 구현 필요 초안 |
| mock 일지 | O1 원본+상세 체결 증분 projection. 누적량을 가짜 체결로 변환하지 않음 |

## 9. 검증 범위와 다음 작업

F01~F07 정확성·중단 문제부터 작은 단계로 고친다. 계좌 기반과 bundle은 이후 독립 작업으로 진행할 수 있다. 지속 campaign과 자동 가설은 평가 분리까지 완성한 뒤 켠다. 계좌 분리 전에는 모의/실전 일지를 자동 학습 입력으로 통합하지 않는다.

이번에는 코드 추적, 네 가지 작은 stdout 재현, 공식 API 확인을 수행했다. 제품 코드·실제 사용자 DB·NAS 설정/배포를 변경하지 않았다. GUI 취소 경합, 실제 계좌 응답, NAS 연속 운전, CPU/RSS·다기간 처리량은 후속 검증 항목이다.

현재 추가 설계 모델 결정은 필요하지 않다. Sol은 이 계약 안에서 구현할 수 있다. 실제 API·자료가 계약과 충돌하면 해당 항목만 증거와 함께 재검토한다.

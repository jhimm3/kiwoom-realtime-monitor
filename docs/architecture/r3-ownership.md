# R3 기능별 책임 계약

기준: 2026-10-18, 제품 2.1.0. 기존 owner를 보존하면서 기능 변경의 진입점과 영향 범위를 찾기 위한 문서다. 새로운 service·relay·queue·저장소를 정의하지 않는다.

현재 구조는 [ARCHITECTURE_CURRENT](../../ARCHITECTURE_CURRENT.md)와 [MODULE_MAP](../../MODULE_MAP.md), 불변 규칙은 [DEVELOPMENT_GUARDRAILS](../../DEVELOPMENT_GUARDRAILS.md)를 따른다. **R3의 진행·미해결·F01~F25 운영 상태는 [REFACTOR_BACKLOG](../../REFACTOR_BACKLOG.md)가 정본**이다. 394개 모듈의 코드 감사 완료는 기능 전체의 운영 성공이나 아래 미해결 문제의 수정 완료를 뜻하지 않는다. 함수별 근거는 [모듈 원장](../workstreams/refactor-module-coverage-20261018.json)에 있다.

## 필요한 기능군부터 읽기

| 변경하려는 기능 | 선택할 계약 |
| --- | --- |
| F01 기동, F23 설정/API 교체, F24 종료 | [조립·시계·수명](#composition) |
| F02 PC 순위, F03 NAS 순위/TR, F10 기본정보/NXT/고가 | [순위·기준 자료](#ranking) |
| F04 0B, F05 로컬 봉, F06 PC 가격, F07 NAS 저장, F08 봉/복구, F09 TOP20 | [실시간·저장·복구](#market-data) |
| F11 완료봉 후보, F12 0B 후보, F13 시장·테마 | [공유 판단과 Shadow](#shadow) |
| F14 포지션, F16 주문/취소, F17 자동 모의, F18 계좌 사건 | [계좌·Risk·Execution](#execution) |
| F15 연구→판단, F21 연구 실행/재시작 | [연구](#research) |
| F19 뉴스, F20 장전 입력 | [뉴스·장전](#news) |
| F22 콘텐츠/일지/테마 동기화, F18 일지 소비 | [일지·편집·동기화](#journal-sync) |
| F25 진단/capture/replay | [진단](#diagnostics) |

여러 기능군을 지나는 변경은 해당 경계만 함께 읽는다. 전체 모듈 재감사를 반복하지 않고, 변경으로 근거가 무효화된 caller·입력·판단·저장·API/UI·복원 범위만 추적한다.

## 모든 기능군의 공통 경계

- **상태와 transaction:** UI의 선택/표시, application의 판단, worker/runtime의 진행 상태, repository/store의 영속 상태를 구분한다. DB 연결·transaction·commit/rollback은 기존 native 저장소가 소유한다. 라우터나 UI에 같은 업무 SQL을 복제하거나 native ACK를 RAM 반영으로 대신하지 않는다. 로컬 DB의 사용자 편집/오프라인 원본과 중앙 DB의 역할도 구분한다.
- **시계와 자료:** 업무 현재 시각은 기존 키움 보정 시계의 `now_provider`/`server_now` 주입을 재사용하고 KST 경계를 유지한다. 원 체결·봉·기사 시각, 당시 가용시각, 사후 보완 시각과 provenance를 보존한다. unknown/partial을 0·완전 수집·주문 승인으로 바꾸지 않는다. 시간 문제의 미해결 범위는 R3-023이다.
- **실패와 수명:** task/thread/queue를 만든 기존 owner가 실패 전달·retry·stop·join/drain을 소유한다. 새 caller는 기존 결과/실패 신호와 ACK 의미를 소비한다. 안전시점이 다른 Risk/control read를 단순 중복으로 제거하지 않는다. 새 owner는 독립 상태·정책·수명·저장 경계가 실제 필요한 경우에만 계약 변경으로 검토한다.
- **공식 경계:** 아래의 현재 public 메서드·typed DTO·Protocol·인증 API를 사용한다. private helper를 다른 기능의 새 API로 삼거나 단순 전달 클래스를 추가하지 않는다. 기존 호환 API/저장 설정은 실제 소비자를 확인한 뒤 변경한다.

<a id="composition"></a>
## 조립·시계·수명 — F01/F23/F24

**현재 책임.** `bootstrap`은 실행 모드, client와 worker factory를 조립한다. MainWindow는 UI 선택과 신호 수신/표시를, AppController는 주입된 action·worker controller·pending buffer와 API 교체/종료 순서를 소유한다. 서버는 `app.create_app`/lifespan이 store·broker·collector·각 runtime owner를 조립하고 닫는다. 작은 worker controller의 중복 실행 방지·신호·결과 신선도·수명 책임은 유지한다.

**상태·실패·공식 경계.** PC는 `configure_runtime/configure_ranking/configure_shutdown`, `request_close` 등 기존 controller 경계를 쓴다. producer 정지 → 도착 중인 결과 처리 → pending flush → 실제 writer 종료와 queued ACK 확인 순서를 보존한다. 실패 pending이 남은 종료와 정상 종료를 구분하며 API 교체도 구 producer의 결과/loader 수명을 함께 처리한다. 서버 설정 저장 revision과 실제 적용 revision은 구분하고, 요청 시작에 선택한 runtime/gateway를 응답 중 임의 교체하지 않는다. transaction은 settings/native store에 남긴다.

**미해결.** 이 순서가 존재한다고 모든 worker 종료가 검증된 것은 아니다. R3-030/031의 entry writer, 032의 별도 thread, 042의 owner/Runner 교체, 066의 real account close 등은 원장의 개별 실패 계약으로 처리한다.

**관련 검사.** [AppController](../../tests/unit/test_app_controller.py), [모의 runtime 교체 장벽](../../tests/unit/test_mock_runtime_barrier.py), [strict restore](../../tests/unit/test_strict_restore.py). 변경한 worker의 late signal·실패·교체·재시작도 해당 검사에 연결한다.

<a id="ranking"></a>
## 순위·기준 자료 — F02/F03/F10

**현재 책임.** PC의 설정/타이머 → AppController/RankingExecutionCoordinator → RankingWorker/RankingService → 표·후속 구독/기본정보 흐름을 유지한다. NAS의 AutonomousTop20은 순위 회차·membership RAM과 후속 수집을 소유하고 모든 키움 REST는 CentralRestBroker의 우선순위·제한·재시도를 통과한다. 직접 모드도 기존 KiwoomRestClient를 사용한다. 기본정보/NXT/일봉/역사 고가 service는 조회·변환·근거를 만들고 worker가 UI로 결과를 전달한다.

**상태·실패·공식 경계.** `load_top_stocks`, 순위 응답 coordinator와 reference service의 기존 `load/is_enabled` 경계를 사용한다. 로컬 `load_ranking_cache`는 묶음 조회, native `update_price_cache`는 같은 가격 job의 단일 transaction을 소유한다. 기준 자료의 확인시각, 일자, 고가 MAX, coverage와 시장 출처를 유지한다. 순위 재시도는 worker/broker에서 수행하고 저우선 백필이 다음 순위 회차를 막지 않게 한다. 저장 실패와 이미 성공한 화면 조회를 분리한다.

**미해결.** R3-002의 transaction 통합과 R3-026의 묶음 read를 다시 만들지 않는다. 원거래일 실패 재병합은 023, Collector의 reference 복원은 034, NXT 포함 고가의 cache 증분 누락은 068로 남아 있다.

**관련 검사.** [순위 service](../../tests/unit/test_ranking_service.py), [순위 실행](../../tests/unit/test_ranking_execution.py), [StockRepository](../../tests/unit/test_stock_repository.py). 기준 자료 변경은 KRX/NXT·빈/부분 자료·수신 후 저장/재조회까지 필요한 경계를 선택한다.

<a id="market-data"></a>
## 실시간·저장·복구 — F04~F09

**현재 책임.** NAS Collector는 상류 등록/ACK·수신·RAM 최신값/봉·직렬 flush를, RealtimeHub와 기존 worker는 배포/PC 전달을 소유한다. PC MainWindow의 0B receiver가 MinuteTradeValueAggregator를 갱신하고 AppController가 pending을 MarketCacheWriter에 넘긴다. TOP20 collector는 cohort 활성화·분 내 기여를 계산한다. 서버 TOP20의 outbox/native index 저장과 ProgramSnapshotWriter의 pending/drain은 각각 기존 owner에 남긴다.

**상태·실패·공식 경계.** PC는 `queue_minute_bar/queue_price_cache`, writer의 `enqueue_*`/`stop_and_drain`을 쓴다. native bar/metadata·가격 transaction은 repository/store가 수행한다. 저장 중 batch와 새 pending을 구분하고 실패 복원이 더 최신 값을 덮지 않게 한다. query/history → writer commit ACK → aggregator의 generation별 복구 확인 순서를 유지한다. 화면은 기존 display/live-bar read를 사용하고 저장 조회를 실시간 원본 수집으로 복제하지 않는다. 직접/NAS 전환은 실제 구독 승인과 조회 성공을 소비한다.

**미해결.** R3-001/004의 metadata 중복 read·range 개선은 승계한다. lock 안 저장 대기/동일 flush(035/036), sender 정리(037), page/recent read 비용(038/039), outbox 읽기 실패(065), 장후 coverage 반복(078)은 개별 후속이다. 같은 계층을 합치는 것으로 종료·ACK 문제를 해결했다고 하지 않는다.

**관련 검사.** [MarketCacheWriter](../../tests/unit/test_market_cache_writer.py), [분봉 저장소](../../tests/unit/test_minute_bar_repository.py), [Collector](../../tests/unit/test_central_realtime_collector.py), [AutonomousTop20](../../tests/unit/test_autonomous_top20.py). 최종 값뿐 아니라 단위/metadata·gap 세대·저장 실패·late ACK·drain과 변경한 DB 호출 수를 확인한다.

<a id="shadow"></a>
## 공유 판단과 Shadow — F11/F12/F13

**현재 책임.** `research_families`는 지원 Family/config/evaluator의 등록 경계다. 순수 evaluator는 제공된 입력과 상태로 판단하며 직접 수집·DB·UI·주문을 소유하지 않는다. CandidateMonitor는 observation cursor/page와 후보 평가·checkpoint를, 0B 후보 경로는 실제 RAM trade 입력을, MarketThemeShadowRuntime은 공통 관측 선택·dispatch·결과 RAM/선별 기록·조회 수명을 소유한다.

**상태·실패·공식 경계.** Family registry/evaluator와 기존 monitor의 page/run 메서드, RAM runtime의 게시/조회 경계를 쓴다. 같은 관측의 불변 frame 변환은 재사용하되 정책 판단·cursor·실패/pending·저장 ACK를 합치지 않는다. 결과는 native Shadow/checkpoint 저장소와 API/관찰창으로 전달한다. Runtime이 가진 작업·lock·drain을 재사용하며 UI 조회 때문에 재수집/재실행하거나 결과 TTL을 연장하지 않는다.

**미해결.** R3-018의 evaluator 공유 필요성과 내부 응집 평가는 별개다. R3-024의 producer/capability 미연결, 047/048의 Runner/checkpoint 책임은 남는다. helper·fixture가 있어도 일반 연구 규칙의 자동 live 연결이나 미정 전략의 완성을 뜻하지 않는다.

**관련 검사.** [CandidateMonitor](../../tests/unit/test_candidate_monitor.py), [완료봉 판단](../../tests/unit/test_breakout_strategy.py), [0B 주도주 판단](../../tests/unit/test_leader_breakout.py). 실제 설정 선택 → 입력/cursor → 판단 → 저장/조회 경계와 미래/부분 입력을 확인한다.

<a id="execution"></a>
## 계좌·Risk·Execution — F14/F16/F17/F18

**현재 책임.** 기존 credential/runtime owner가 계좌 신원·binding/revision·선택과 account query/recovery의 수명을 소유한다. AccountMonitor는 계좌 사건/대사와 ACK된 Risk 관측을 만든다. Gateway는 인증된 명령·멱등 요청·owned command, ExecutionRuntime은 run/lease·operation lock·gate, OrderLifecycle은 QUEUED/UNKNOWN/취소/체결 전이를 소유한다. ExecutionRepository/native store는 원장·control/claim fence·transaction을 소유한다. Supervisor/Runner는 등록된 자동 모의운용과 보호의 수명/상태를 연결한다.

**상태·실패·공식 경계.** 기존 인증 order/account/control/publication API, typed OrderIntent/AccountSnapshot, runtime submit/cancel, Lifecycle reconcile와 native repository를 통과한다. 제출 전 durable claim, UNKNOWN 후 대사, 취소 ACK와 취소 확정의 구분을 유지한다. 실제 position은 상세 FILL로 갱신하고 BROKER_FILL_AGGREGATE의 증가량을 상세 체결로 위장하지 않는다. 포지션/배분/EV의 순수 계산은 같은 account/Risk cut과 기존 RAM 관측을 받아 결과를 내며 별도 계좌 조회·원장·주문 owner를 만들지 않는다.

**미해결.** R3-010의 atomic immutable/ACK, 040의 재조회, 042의 owner 연결, 049/050의 Risk/비동기 책임, 066의 close 실패를 구분한다. R3-024의 미지원 입력/정책은 그대로 남기며 연구 PASSED·Shadow 결과·목표 수량을 실행 승인으로 승격하지 않는다. 보호 Risk와 native 주문 경계를 일반 EV 대기로 대체하지 않는다.

**관련 검사.** [OrderLifecycle](../../tests/unit/test_order_lifecycle.py), [ExecutionRepository](../../tests/unit/test_execution_repository.py), [자동 Risk](../../tests/unit/test_mock_automation_risk.py), [Runner](../../tests/unit/test_mock_automation_runner.py). 계좌/세대·중복 명령·UNKNOWN·부분체결·STOP/교체·close의 실제 변경 경계를 검사한다.

<a id="research"></a>
## 연구 — F15/F21

**현재 책임.** ResearchDialog/CLI가 명시 request와 입력을 선택하고 `research_process`가 child 실행·등록/claim·진행/중단·복구·결과 게시를 조정한다. 기존 연구 실행기는 Family 판단과 simulation 원장을, 평가기는 outcome/짝 비교/report를 소유한다. ResearchRepository는 동결 입력·run/lease/generation·결과/최종평가의 native 저장을 소유한다. 후보 package/specification 게시 이후에도 별도 admission·Risk·Execution을 통과한다.

**상태·실패·공식 경계.** 기존 request/typed split·dataset·Family registry, repository의 owned claim/finish 계약과 후보 게시 API를 사용한다. 실제 지원하는 봉 해상도·정책·입력 가용시점을 명시하고 분봉을 가짜 틱/초봉으로 바꾸지 않는다. 개발/최종평가·과거 완결 결과와 현재 가용 관측을 분리한다. retry/재시작은 원래 run identity·owner·결과 불변성을 보존하고 source/result 경로를 혼용하지 않는다. child는 stop/checkpoint와 실제 완료를 부모에게 전달한다.

**미해결.** 일반 request=result 충돌은 R3-046, terminal 전이 경합은 063, 최대 낙폭 ppm은 067이다. R3-024의 feedback/context/thesis producer 및 일반 연구→live 적용 범위는 미완료다. 수동 전략팩의 일지 사후분석을 이 Family/자동매매 경로와 합치지 않는다.

**관련 검사.** [연구 process](../../tests/unit/test_research_process.py), [연구 저장소](../../tests/unit/test_research_repository.py), [실행](../../tests/unit/test_research_execution.py), [평가](../../tests/unit/test_research_evaluation.py). 고정 입력/분할·소유권·중단/복원·원본/결과 보존과 후속 후보 소비를 확인한다.

<a id="news"></a>
## 뉴스·장전 — F19/F20

**현재 책임.** CentralNewsService/공급자 수집은 기사 revision을 저장하고, 기존 job runner가 BODY/RULE/AI와 retry를 진행한다. 원문/body/AI/event revision은 서로 다른 원본·가용시각을 가진다. PC 뉴스 창/worker는 조회·그룹/대표 기사 선택·수동/자동 분석 요청·표시를 소유한다. 장전 service는 별도의 외부 공통 시황 request/예약·claim·raw/result·latest/context를 소유한다.

**상태·실패·공식 경계.** 기존 news 수집/page/history·job claim/finish/retry·인증 AI API와 PC worker 신호를 사용한다. native 저장소가 revision·job 상태·transaction을 소유하고 AI service가 accepted 요청/실행 lock을 소유한다. UI용 그룹 대표와 NAS의 저장 body revision 입력을 섞지 않는다. 미확인 자료는 원값과 확인 상태를 함께 보존한다. 장전 context의 `advisory_only/consumer_binding=not_connected`는 실시간 판단/주문 연결과 구분하며 공급자 호출이나 polling task를 소비자마다 복제하지 않는다.

**미해결.** R3-019의 read/error, 020의 프로세스 결합, 043의 stale attempt, 072의 NAS-only 수동 버튼, 073의 동일 기사 쓰기가 남는다. 장전 결과와 외부 지연시세(060/061)는 서로 다른 입력 경계다.

**관련 검사.** [CentralNewsService](../../tests/unit/test_central_news_service.py), [news jobs](../../tests/unit/test_news_jobs.py), [기사 저장소](../../tests/unit/test_stock_news_repository.py), [장전 계약](../../tests/unit/test_morning_market_brief.py). 원문 revision·중복/실패·가용시각·선택한 provider/버튼과 실제 worker 입력을 대조한다.

<a id="journal-sync"></a>
## 일지·편집·동기화 — F22 및 F18 소비

**현재 책임.** EntrySnapshotWriter는 체결 스냅샷과 수급/프로그램 보완 queue, journal 저장소는 account scope·원본/provenance·분석 revision을 소유한다. JournalWindow는 history/query·그룹 편집·복기·차트/분석의 UI와 worker 수명을 조정한다. ThemeRepository는 프로필 테마 원본·사용자 승인/편집을 소유하고 OCR/추출/preview는 적용 전 제안 경계다.

**상태·실패·공식 경계.** 기존 snapshot enqueue/backfill, scoped history/query/analysis, 테마 preview→bulk apply와 백업/복원 API를 쓴다. 네트워크 동기화는 기존 service/runner가 수행하고 native repository의 transaction을 우회하지 않는다. CentralContentSync의 identity/hash delta와 CentralJournalSync의 현재 전량 동기화는 서로 다른 경로다. 전송 ACK는 새 로컬 편집까지 저장됐다는 뜻이 아니며 원본/보완/사용자 편집을 구분한다. Drive/NAS/파일 복원도 같은 변경 필드와 revision의 소비자다.

**미해결.** R3-044/045의 ACK/내용 hash, 051·055·057~059의 일지 read/출처/사후입력, 064의 snapshot 뉴스 재전달, 071/074/075의 반복 작업, 076~077의 catalog/미사용 profile, 080~082의 OCR/표시 책임을 개별 처리한다. 기존 delta 구현을 재개발하거나 모든 일지·뉴스 sync가 이미 delta라고 설명하지 않는다.

**관련 검사.** [snapshot service](../../tests/unit/test_journal_snapshot_service.py), [일지 보완](../../tests/unit/test_journal_enrichment.py), [콘텐츠 sync](../../tests/unit/test_central_content_sync.py), [일지 sync](../../tests/unit/test_central_journal_sync.py), [테마 backup](../../tests/unit/test_theme_backup.py). 원본 재전달/보완 보존·편집 중 ACK·실패 retry·restore/close를 변경 경계에 맞춰 확인한다.

<a id="diagnostics"></a>
## 진단 — F25

**현재 책임.** 기존 registry/trace/capture가 실제 호출의 단계·결과·출처를 기록하고 manifest/reader가 보존 자료를 해석한다. dedicated replay store/clock·lease와 owned executor는 재생의 격리/복원·실제 작업 완료를 소유한다. 관측 wrapper는 측정 책임이며 원래 DB 연결·transaction·업무 판단 owner를 대신하지 않는다.

**상태·실패·공식 경계.** 기존 진단 API/CLI와 capture schema/manifest/lease를 쓴다. 원본 trace와 replay 전용 쓰기를 분리하고 일반 앱/주문 transport의 새 필수 의존성으로 만들지 않는다. capture 성공, 입력 완전성, 재생 결과 일치, 실제 운영 수락은 각각 보고한다. 손상/용량/누락·취소·종료의 실패를 성공 표본으로 숨기지 않는다.

**미해결.** R3-022의 wrapper 유지 판단과 내부 비용·책임 평가는 별개다. v4 clock 삭제 범위(079)는 조건부 가설이며 실제 dedicated PG 재현 전 수정 완료로 처리하지 않는다. 운영 OFF/ON 비용과 전체 재생/복구 수락도 별도다.

**관련 검사.** [진단 runs](../../tests/unit/test_diagnostic_runs.py), [account context](../../tests/unit/test_diagnostic_account_context.py), [replay](../../tests/unit/test_diagnostic_replay.py), [PG 격리 replay](../../tests/integration/test_diagnostic_replay_postgres.py). 운영 접근을 새로 만들지 않고 해당 격리 계약을 선택한다.

<a id="contract-change"></a>
## 정당한 책임·계약 변경

1. 기능군과 실제 진입/소비자를 고른다. 현재 owner가 부적절하면 구체적인 상태·정책·수명·저장 경계와 변경 이유를 적는다. 파일 길이 또는 Manager/Service라는 이름만으로 분리/합치지 않는다.
2. 입력/단위/시계·RAM 상태·영속 키·transaction/ACK·실패/retry·교체/종료의 전후 차이를 적는다. 한 기능 이해에 필요한 파일/호출 깊이가 늘면 실제 책임 이득을 설명하고, 단순 전달 단계만 생기면 기존 경계에서 구현한다.
3. 필요한 caller·저장/조회·API/UI·연구/Shadow/실행·복원/기존 설정 소비자를 함께 수정한다. 과거 비교용 경로와 미연결 capability는 표시를 유지하고 미정 매매 정책을 임의로 채우지 않는다.
4. 관련 테스트와 실제 반례/비용 기준을 선택해 같은 의미로 검증한다. 위 검사 목록은 출발점이며 통과 주장이나 전체 검사의 대체가 아니다. PC/NAS의 `all-local`을 자동 시작하지 않고 GitHub CI의 전체 검사는 유지한다. 실제 PG/GUI/provider/운영 수락과 미실행은 따로 기록한다.
5. 책임이 바뀌면 이 문서의 해당 기능군과 MODULE_MAP, 관련 R3/F 상태·근거를 함께 갱신한다. 기존 owner를 삭제/이관할 때는 저장 설정·호환 API·재시작 복원·종료 책임의 남은 소비자가 없는지 확인한다. 문서만 바꾼 것을 구현·배포 완료로 보고하지 않는다.

<a id="automated-checks"></a>
## 변경 영향에 따른 검증과 비용 기준

```powershell
python -m scripts.check_architecture --base-ref HEAD --report tmp/architecture.json
python -m scripts.check_test_changes --query --base-ref HEAD --path src/변경한_경로.py --limit 5 --report tmp/test-selection.json
python -m scripts.run_regression --selection tmp/test-selection.json --output tmp/selected-run
python -m scripts.run_regression --summarize tmp/selected-run/run.json
```

첫 명령은 제품 import/DB/네트워크 없이 소스 구문과 기존 영향 그래프를 검사한다. 콘솔은 실패 최대8개만, 상세는 지정 보고서에 보존한다. 나머지 명령은 기존 [선택 실행](../INCREMENTAL_TEST_REVIEW.md#선택적-조회실행-명령)을 재사용한다. 경계가 불명확한 `full_required`는 자동 전체 실행/부분 성공으로 바꾸지 않고 관련 소비자와 필요한 환경을 먼저 좁힌다. 단순 문서 수정에 기능 전체 검사를 요구하지 않는다.

| 확인 대상 | 자동 검사/관련 회귀 | 한계·사람이 확인할 내용 |
| --- | --- | --- |
| 새 계층 역방향 의존·순환 edge | `check_architecture`, 기존 `select_ci_groups` graph 재사용 | TYPE_CHECKING/조건부 import도 보수적으로 포함한다. 기존 cycle은 실행 중 순환 실패가 아니라 코드 의존 증거다. 의존을 늘리는 이유·책임을 검토한다. |
| 새 DB 연결/execute/commit/rollback 지점 | 같은 checker의 함수별 source-site delta, 기존 PG 직접 연결·QueryStore 계약 회귀 | 지점 수는 실제 실행 횟수가 아니다. 동적 receiver/별칭 재대입·반사 호출/도우미 속 I/O 전부를 증명하지 않는다. 새 helper/우회 state 변경은 실제 caller/owner 검토로 보완한다. |
| RAM 경로의 load/save·파일/HTTP/동기 대기 후보 | 계약에 지정한8개 함수와 중첩 함수, 이동/삭제도 검출 | 한 회차 공유 여부·helper 내부의 증가·반복 계산은 실제 cost/연결 검사로 확인한다. 새 고빈도 기능은 해당 함수와 테스트를 같은 계약에 추가한다. |
| API·이벤트·DB contract·수명·중복 처리 | 기존 QueryStore/source/PG와 해당 기능군의 통합·실패/중복 입력·교체/재시작/종료 검사 | 새 테스트 파일이나 동일 검사를 복제하지 않는다. 필요한 저장·Risk/control 재검증을 단순 호출 수로 제거하지 않는다. |
| Controller/Service/Manager 중복·얇은 relay·광범위한 상태 소유 | 위 기능군과 변경 diff 검토 | 이름·파일 크기·클래스 수 임계값으로 판정하지 않는다. 신규 owner의 독립 상태/정책/수명/저장 책임과 파일 수·호출 깊이의 실제 이득을 확인한다. |

[기계 계약](guardrails.json)의 `adopted_limits`는 현 미커밋 checkout의 **source-site 목록 채택**이다(boundary23/cycle261/DB1041/dynamic41 keys). 기존 R3 결함 해결·모든 비용 승인·타 작업의 의미 검토가 아니다. Git 기준을 전달하면 최초 목록과 검토 이력 재작성은 실패한다. 기존 QueryStore/PG 검사는 이미 전체 CI에 있으므로 별도 CLI로 중복 실행하지 않는다. 새 정적 검사는 기존 Windows CI에서 한 번 실행하고 fixture 검사는 기존 `runner` profile에서 실행한다. 기존 전체 CI/필수 환경·일정은 유지하며 새 주기 검사는 추가하지 않았다. 이 checkout의 활성 pre-push hook은 없고, push/PR의 자동 검증은 기존 GitHub workflow가 담당한다.

**정당한 변경:** 실패 보고의 정확한 `key`에만 `reviews`의 고유 `id`, `reason`, `connection`, `validation`, `limits`를 기록한다. 필요한 비용 증가는 독립 결과·안전성/연결 회귀와 함께 허용한다. R3로 지점/책임이 없어지면 해당 limit을0(또는 감소값)으로 좁혀 재유입을 막는다. 기준을 통째로 새 snapshot으로 만들지 않는다. `rules` 변경은 이전/새 규칙의 `canonical_digest`를 `rules_change.from/to`에 기록하고 책임 문서·관련 검사를 함께 수정한다. CLI는 계약을 자동 수정하지 않는다.

```json
{"id":"해당-R3-또는-기능-ID","reason":"필요한 비용과 이전 비용의 차이","connection":"실제 caller → owner → 소비·실패/종료","validation":"관련 현재 실행 근거","limits":{"실패 보고의 정확한 key":1}}
```

### 재현 가능한 실제 비용 기준

| 범위 | 현재 정상 기준과 oracle | 재사용 위치 |
| --- | --- | --- |
| R3-001 live metadata | 20종목 native SELECT20, BEGIN IMMEDIATE/COMMIT 경계와 값/metadata·rollback/retry | `test_minute_bar_repository.MinuteBarRepositoryTests.test_live_batch_reads_each_metadata_row_once_inside_the_writer_transaction` 및 같은 파일의 authority/실패 검사 |
| R3-002 price batch | 연결/BEGIN/COMMIT 각1, SELECT0; empty/partial·전체 rollback·retry·process exit | `test_stock_repository.StockRepositoryTests.test_price_cache_uses_one_transaction_and_keeps_field_and_date_contracts`와 같은 파일의 실패 검사. 원거래일 실패 재전달023은 미해결 |
| R3-004 RAM minute | 20종목/400행 중 실제 native 반환200행(`[10]*20`); 무제한 양성 대조400행(`[20]*20`), 8소비자/100회 조회/close에 추가 read0·native 무쓰기 | `test_candidate_monitor.CrossThemeH1SharingTests.test_minute_primary_uses_existing_sqlite_reader_without_writes_or_consumer_scaling` 기존 검사 보강. 과거9000→200 측정과 이번400→200 fixture는 구분 |
| 미해결 반복 비용/정합성 | R3-038/039/041, 063~082 등의 반례는 개선 목표이며 정상 budget으로 승인하지 않음 | 기존 R3 항목의 재현/완료 기준에서 수정 후 관련 비용 검사를 선택·승격 |

wall time·WAL/디스크 쓰기·실제 PG/장중 지연은 위 호출·행 수로 추정하지 않는다. 입력 규모·단위/metadata·ACK·최종 내용이 같다는 증거와 함께 비용을 비교하고 필요한 비용 증가는 계약/테스트 기대값과 이유를 함께 갱신한다. 미해결 반례를 통과시키는 현행 버그 기대값을 정상 CI 기준으로 옮기지 않는다.

### R3 최종 종료 조건과 점진 적용

R3-083이 이 체계의 진행 원장이다. 다른 R3 개선을 중단시키는 별도 선행 전면 정리는 아니다. 각 개선이 끝날 때 관련 owner/공식 경계·비용/실패 계약·F 원장을 좁혀 반영하며 다음 구현은 기존002+023 순서를 유지한다. 최종 종료에는 책임 계약의 현재 구현 일치, 개선된 고빈도 경로의 재현 비용/중복·실패/종료 검증, 구조 위반 시 실패/정당 변경 시 통과, 자동 CI 연결 및 실제 실행 결과 확인을 포함한다. 문서·로컬 검사 완료와 hosted CI/운영 환경 수락은 분리한다.

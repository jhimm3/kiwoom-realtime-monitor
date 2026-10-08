# 현재 모듈 지도

기준: 2.1.0 / 2026-09-23 · [현재 아키텍처](ARCHITECTURE_CURRENT.md) · [문서 안내](docs/README.md)

아래 경로는 현재 있는 파일이다. 파일을 나누는 계획이 아니라 변경 책임자를 찾는 지도다. 상세 단계별 계보는 [이전 지도](docs/archive/2026-09-22/root/MODULE_MAP.md)에 보존했다. 작업 전 [개발 불변 규칙](DEVELOPMENT_GUARDRAILS.md)을 읽는다.

## 앱·시장·저장

`src/kiwoom_monitor/` 기준 경로다.

2026-10-08 NAS operator PC preparation: `scripts/nas_operator.py` owns fixed command parsing,
source/trace admission, capture fences, isolated jobs, and deploy recovery; the installer and
worker live in `scripts/nas_operator_install.py` and `scripts/nas_operator_worker.py`. The
`deploy/synology/check-nas-operator.sh` gate tests Linux fd/ACL behavior in a disposable offline
container. PC unit tests pass; the Linux gate and NAS install have not run. See
[the operator contract](docs/NAS_OPERATOR_COMMANDS.md) before changing this boundary.

0B collector 통합 진단은 `central_server/diagnostic_collector_replay.py`가 고정 입력,
실행/종료 수명, 전용 DB 검증·정리를 소유한다. 실제 파서·RAM 집계·저장 주기는
`central_server/realtime_collector.py`와 `minute_bars.py`를 재사용한다. 추가로
`diagnostic_trace.py`가 선택된 store 입력과 collector 원인 사건을 bounded schema-2
capture로 보존하고, `diagnostic_replay_contract.py`가 허용 메서드·codec·workload 선택과
collector descendant 제외 계획을 검사한다. `diagnostic_recorded_execution.py`는 명시된
store allowlist를 caller-owned test store에서 실행하며 actor 순서·동시성·replay ID를 기록한다.
collector mode는 0B 원인 사건을 실제 collector loop에 전달하고 그 component의 과거 sink만
제외한다. 로컬 collector-input/v2는 최소 0w·0J·0U 입력과 같은 component의 market_state-only
dataset sink 대체도 지원하며 peer 및 다른 dataset은 유지한다. TOP20 subscriber 자체는
실행하지 않으며 cross-component causal replay는 미지원이다. NAS 검증·활성화는 별도 gate다.
로컬 `diagnostic_top20_input.py`는 `ka00198 qry_tp=5` freshness/slot/retry 판정만 재생한다.
`diagnostic_top20_flow_input.py`는 TOP20 최초 편입 `ka10045` 입력에서 실제 investor-flow
ingestor와 완료 marker까지 전용 replay DB 안에서 실행하며 native descendant를 중복 실행하지 않는다.
일반 수급 fan-out, broker scheduling, 전체 TOP20 subscriber/준비 작업은 지원하지 않는다.
`diagnostic_rest_input.py`는 top20_inputs opt-in의 broker 논리 요청/전송/cache·ingest 원인 및 catalog
입력 pair를 기록·검증하고 명시 lane의 offline tape client를 제공한다. `diagnostic_top20_lifecycle_input.py`는
0s·구독 intent/ACK/READY·hub control·gap 입력 pair와 descendant preflight를 검증하고, 현재 native 구독 intent에
일치하는 결과만 offline tape로 공급한다. 둘 다 native broker/collector lifecycle을 그대로 실행하지 않으며
full TOP20 executor를 열지 않는다. `diagnostic_replay_runtime.py`는 opt-in 실행의 Task와 실제 executor
작업을 제출 시점부터 추적하고 실제 종료 전 DB reset/release를 차단한다. `diagnostic_top20_outbox.py`는
run 소유 native 파일 outbox의 검증된 seed와 복구를 담당한다. 둘은 로컬 구현·native 회귀 142건을
마쳤다. NAS 임시 RAM-backed·network-isolated PostgreSQL acceptance 13건이 skipped=0으로 통과했고
취소 thread 종료, native outbox ACK 재시도, DB/file baseline 공동 복구를 확인했다. T4 v1 gate는 T3 13건 뒤
fixture setup timeout으로 실패했으나, disposable PostgreSQL gate에만 180초를 지정한 비활성 v2 candidate
`2026.10.07-top20-session-v2-57de01203aa5af84`의 후속 acceptance 15건이 skipped=0으로 통과했다.
두 baseline 보존 및 반복 복구, TOP20 단독/제외와 peer writer 유지, descendant 대체, drain 및 DB/outbox
복구를 controlled fixture에서 검증했다. 보고서는 `source_state_equivalent=false`이며 실제 장중 상태 등가,
입력 coverage, 성능을 검증하지 않는다. 로그의 stale ranking `recording_gap` 두 건은 고정 시각 fixture가
stale해져 해당 회차 저장을 건너뛴 것이며 acceptance 실패나 DB 입력 유실은 아니다. 운영 release와 두
운영 container는 변경하지 않았다.
`diagnostic_top20_seed.py`는 native TOP20 service의 의미 상태를 명시 목록으로만 검사하고, controlled
cold fixture의 baseline/input/mask/component/source-clock identity를 window frontier에 결합한다.
lock·Task·cache·pending work가 비었는지 확인하지만 RAM을 복원하거나 DB를 restore하지 않으며 execution
gate를 열지 않는다. REST/catalog 및 lifecycle 입력 tape와 native service lifecycle runner가 연결됐다.
T4 controlled PostgreSQL acceptance는 통과했으며, 장중 상태 등가 및 성능 결론은 유효한 실제 capture replay 전까지 보류한다.
`postgres_access.py`는 replay operation ID와 owner·actor·component를 DB call에
전달한다. `diagnostic_replay_baseline.py`는 고정 replay DB의 역할·소유권·run lock, baseline v1 14개
허용 테이블/sequence 및 별도 opt-in v2 15개 테이블(`central_api_query_cache` 포함) snapshot,
atomic restore와 native connection drain을 소유한다. 로컬 offline replay `run`도 v2를 선택할 수 있다.
v2 query-cache store 호출은 lease의 source clock을 사용하며 v1에서 거부한다. `rest_broker.py`의 선택적
source wall clock과 `database_query_cache.py`의
per-store clock hook은 v2 lease에서만 연결되고 운영 기본값에는 변화가 없다.
비활성 NAS candidate에서 임시 RAM-backed·network-isolated PostgreSQL baseline gate 7건과 새 native
run gate 3건, 총 10건이 skipped=0으로 통과했다. 반복 run의 source TTL과 collector/cache 공통 clock,
exclusion의 과거 cache 결과 비주입, ACK-loss 뒤 drain 및 baseline 복구를 확인했다. 성능이나 장중
상태 동등성은 입증하지 않았다. 후속 service clock 감사의 세 누수(신고가 helper 날짜, 분봉 날짜 fallback,
membership 공개시각)는 로컬 수정과 신고가 12/ingestor 13/TOP20 67 단위 회귀 및 source-date 재현으로
통과했다. 전체 TOP20 runner와 그 안에서 시간 의존 TR에 같은 clock을 바인딩하는 단계는 남아 있다.
`diagnostic_replay_database_cli.py`는 offline provisioning/status/seal/restore와 bounded
schema-2 recorded-operation replay를 제공한다. 실행은 기존에 봉인한 dedicated baseline 아래서
수행하고 종료 후 기준 상태를 복구한다. 원본 operation ID와 replay operation ID를 실제 DB-call
관측 ID에 연결하며, 이 연결은 workload가 발생시킨 연결만 관측한다. `news_article` 및
`theme_metadata`의 history projection처럼 baseline allowlist 밖의 부수 쓰기가 필요한 입력은
사전 거부한다. `scripts/check_recorded_replay_baseline.py --execution-gates`는 NAS 운영자가
기존 sealed DB에서 반복 재생·workload 제외·실패 후 정리를 검사한다. 공개 선택/비교 API와
장중 원본 동등성은 아직 없으며 운영 실행 경로에는 연결되지 않았다.
역할과 검증 경계는
[반복 부하 실험 계약](docs/RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)을 따른다.

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 조립·프로세스 | `bootstrap.py`, `news_process.py`, `journal_process.py`, `research_process.py` | 실행모드·자식 프로세스·작업 수명 |
| 사용자 데이터 | `infrastructure/app_paths.py` | 설치/개발/지정 data 경로 |
| 순위 | `application/ranking_schedule.py`, `application/ranking_execution.py`, `central_server/autonomous_top20.py` | 순위 시각·재시도·실행·NAS 독립 수집 |
| REST | `central_server/rest_broker.py`, `infrastructure/kiwoom_rest/client.py`, `infrastructure/kiwoom_rest/remote_client.py` | 중앙 우선순위·직접/원격 경계 |
| 실시간 | `central_server/realtime_collector.py`, `infrastructure/kiwoom_rest/realtime.py`, `infrastructure/kiwoom_rest/realtime_worker.py`, `infrastructure/kiwoom_rest/central_realtime_worker.py` | 중앙/직접 구독·REG·장애전환, 0s 장운영(215) 원본 이벤트 파싱·중계 |
| 세션 | `application/market_session_schedule.py`, `application/realtime_subscription.py`, `central_server/autonomous_top20.py` | 거래일별 venue/phase·구독 대상, NAS 장후 보완은 저장된 0s 거래일 증거가 있을 때만 예약. 공식 연간 휴장 달력 자동 동기화는 미구현 |
| 봉·관측 | `central_server/minute_bars.py`, `central_server/market_ingest.py`, `central_server/market_observations.py` | 1초/1분 집계·TR 적재·revision 의미. 실제 일봉 changed key 또는 저장 결과가 불확실할 때만 TOP20 daily/high 준비 상태를 code/market 범위로 재검증한다. |
| TOP20 | `application/top20_trade_value_collector.py`, `presentation/top20_trade_value.py`, `central_server/autonomous_top20.py` | 코호트·지수·차트. NAS 0w 저장은 단일 owned task를 공유하고 종료 시 producer stop→실제 save drain→pending final flush를 수행한다. 호출자 취소가 DB thread 소유권을 끊지 않는다. |
| 로컬 쓰기 | `infrastructure/persistence/market_cache_writer.py`, `infrastructure/persistence/minute_bar_repository.py` | 비동기 직렬 쓰기·봉 저장 |
| 중앙 서버 조립·API | `central_server/app.py`, `central_server/database.py`, `central_server/central_schema.py` | `app.py`가 QueryStore/backend, 서비스, lifespan과 나머지 API를 조립한다. 뉴스 조회 라우터는 기존 store와 인증 dependency를 등록한다. |
| 뉴스 조회 API | `central_server/news_read_routes.py`, `central_server/app.py` | 인증된 history/sources/market-feed 요청 검증과 응답 조립. 4개 조회 메서드만 필요한 `NewsReadStore` Protocol을 쓰고 실제 SQLite/PostgreSQL store 수명은 app이 소유한다. |
| 중앙 서버 로그 | `central_server/server_logging.py` | 파일·콘솔 로그를 KST로 표시하고 날짜 회전을 한국 자정 기준으로 유지 |
| 외부시장 봉 DB | `central_server/database_external_market.py`, `central_server/database.py` | SQLite/PostgreSQL 외부시장 봉 저장·조회 실제 구현. QueryStore 계약과 store 조립은 `database.py`에 유지 |
| 국내 시장 봉 DB | `central_server/database_market_bars.py`, `central_server/database_observation_writes.py`, `central_server/database.py`, `central_server/postgres_access.py` | SQLite/PostgreSQL 분봉·초봉·5분봉·일봉 저장·조회 구현과 revision/metadata writer helper. 분봉 metadata는 `(subject, trading_date+minute)`로 각 종목에 연결한다. 일봉 UPSERT는 commit이 확인된 changed key만 반환해 coverage freshness 입력으로 쓴다. QueryStore 계약·store 조립·기존 호출 연결은 유지하고, 공통 PostgreSQL wait probe는 뉴스 claim과 공유. 로컬 회귀 및 NAS 전용 PostgreSQL 일봉 changed-key/no-op/중복·metadata/rollback 검사 3/3 통과 |
| 시장 관측 메타데이터 DB | `central_server/database_market_metadata.py`, `central_server/database_observation_writes.py`, `central_server/database.py`, `domain/market_data_contract.py` | SQLite/PostgreSQL metadata 저장·단건·범위 조회. QueryStore/API 계약과 native transaction을 유지하며 `CoverageObservation`을 하위 value contract로 둔다. 로컬 회귀·정적 연결 및 NAS 전용 PostgreSQL gate 3/3 통과 |
| REST 응답 캐시 DB | `central_server/database_query_cache.py`, `central_server/database.py` | SQLite/PostgreSQL cache read/write와 `StoredQuery` 실제 구현. `QueryCacheStore`는 REST broker가 쓰는 두 메서드 계약이며 `QueryStore` aggregate·store 조립은 유지. NAS PostgreSQL·broker·SQLite gate 5/5 및 SQLite-backed API ASGI 연결 1/1 통과 |
| 저장소 진단 DB | `central_server/database_storage_diagnostics.py`, `central_server/database.py` | SQLite/PostgreSQL 저장 크기·분류 조회와 공통 분류 구현. 인증된 `/api/v1/diagnostics/resources` 응답 및 PC 리소스 화면의 기존 연결 유지 |
| 실시간 최신값 DB | `central_server/database_realtime_snapshot.py`, `central_server/database_codec.py`, `central_server/database.py` | SQLite/PostgreSQL 최신 스냅샷 저장·조회와 영속 0B 시총 projection. `realtime_collector.py`의 직렬 flush·실패 복구와 app.py WebSocket/API 소비 연결을 유지하며, 공통 JSON decoder는 codec에서 제공 |
| 시장 이벤트 DB | `central_server/database_market_events.py`, `central_server/database_codec.py`, `central_server/database.py` | SQLite/PostgreSQL VI·hot cohort·상한가 revision/current/history 저장·조회 구현. QueryStore 계약·store 조립과 기존 transaction/lock 소유권은 `database.py` 및 호출자에 유지 |
| 연구 관측 export DB | `central_server/database_research_export.py`, `central_server/database_codec.py`, `central_server/database.py` | 고정 revision membership manifest 생성과 cursor page 조회의 SQLite/PostgreSQL 구현. 인증 `/api/v1/research/observations`와 PC `CentralContentClient` 경로, 별도 writer/reader transaction 경계를 유지 |
| 문서·테마 이력 DB | `central_server/database_documents.py`, `central_server/database.py` | SQLite/PostgreSQL 문서 upsert·replace·조회와 테마 snapshot, 뉴스 기사 revision/BODY job의 동일 transaction 후처리. QueryStore 계약과 store 조립은 `database.py`에 유지 |
| 뉴스 작업 큐·요청 예산 DB | `central_server/database_news_jobs.py`, `central_server/database_news_job_writes.py`, `central_server/database_documents.py`, `central_server/database.py` | SQLite/PostgreSQL AI 작업 enqueue·claim·finish·retry, 요청 예산 claim/count, wake-up callback의 저장 구현. 문서 cursor transaction이 공유하는 job insert helper는 하위 공통 모듈에 두고 기존 import alias와 post-commit wake-up 순서를 유지. 로컬 회귀 및 NAS 전용 PostgreSQL gate 10/10 통과 |
| 뉴스 revision DB | `central_server/database_news_revisions.py`, `central_server/database_news_jobs.py`, `central_server/database.py`, `domain/news_observation.py` | SQLite/PostgreSQL BODY·AI·event revision 저장과 뉴스 이력 조회 구현. caller-owned cursor helper와 job wake-up 연결은 하위 뉴스 job writer를 사용하며 QueryStore 조립·기존 import alias는 유지. 로컬 회귀 135건·NAS 전용 PostgreSQL gate 9/9 통과 |
| 뉴스 소스 페이지·진행상태 DB | `central_server/database_news_sources.py`, `central_server/database_news_job_writes.py`, `central_server/database.py` | SQLite/PostgreSQL source cursor/page, source diagnostics, stored market feed. `save_news_source_page`와 historical batch가 공유 caller-owned transaction, 기사 revision/target/job/cursor atomicity, table lock 및 commit 후 wake-up을 보존한다. 로컬 회귀 76건·NAS 전용 PostgreSQL gate 5/5 통과 |
| 과거 뉴스 외부 처리·시황 batch DB | `central_server/database_historical_news.py`, `central_server/database_news_sources.py`, `central_server/database_news_revisions.py`, `central_server/database.py` | 외부 BODY/RULE lease claim/completion 및 historical market batch. QueryStore 조립·호환 alias는 root에 유지하고 ownership fence, `SKIP LOCKED`, caller-owned transaction 및 commit 후 wake-up을 보존한다. 로컬 회귀 123건·NAS 전용 PostgreSQL gate 5/5 통과 |
| 계좌 설정·복구 DB | `central_server/database_account_settings.py`, `central_server/database.py` | SQLite/PostgreSQL account settings·market profile CAS, fenced real account event/recovery 저장·조회 구현. QueryStore 계약과 store 조립은 `database.py`에 유지하며 credential activation은 기존 cursor transaction에서 domain helper를 빌림 |
| 계좌 identity·binding DB | `central_server/database_account_identity.py`, `central_server/database.py` | SQLite/PostgreSQL 계좌 identity 등록, binding revision, scope alias 검증·조회 구현. QueryStore 계약과 store 조립은 `database.py`에 유지하며 credential activation의 기존 transaction 흐름을 보존 |
| 자격증명 프로필·활성화 DB | `central_server/database_credentials.py`, `domain/credential_contract.py`, `central_server/database.py` | 프로필 생성·등록·이름·보관 및 activation receipt의 SQLite/PostgreSQL 구현. activation은 identity binding과 account settings helper를 기존 cursor transaction에서 호출하고 암호화 vault 파일 commit 경계는 credential store에 둠 |
| 실행 원장·mock 제어·lease DB | `central_server/database_execution.py`, `central_server/database.py` | SQLite/PostgreSQL intent/event/account snapshot, mock control CAS, runtime lease 저장·조회. ExecutionRepository·ForwardEvaluationRepository와 기존 mock 주문/이벤트 API 연결을 유지하고 소유권 fence, 독립 트랜잭션과 native 계측을 보존. 전용 PostgreSQL fence·ledger·control CAS 검사 3건 통과 |
| Shadow 상태·평가 DB | `central_server/database_shadow_state.py`, `central_server/shadow_checkpoint.py`, `central_server/database.py` | SQLite/PostgreSQL checkpoint 상태, shadow 평가·candidate event 저장과 cursor page 조회. `CandidateMonitor`·인증 API 연결 및 기존 caller-owned transaction을 유지하고, normalized frame format/schema/DML helper는 `shadow_checkpoint.py`에 둠. 전용 PostgreSQL gate 7건 통과 |
| dataset snapshot·관측 SQL DB | `central_server/database_datasets.py`, `central_server/database_observation_writes.py`, `central_server/database.py`, `central_server/postgres_access.py` | SQLite/PostgreSQL dataset snapshot 저장·조회 구현과 caller-owned cursor의 metadata/revision SQL을 분리했다. root QueryStore·기존 import alias 및 writer transaction은 유지한다. NAS 전용 PostgreSQL gate 9/9 통과 |
| PostgreSQL 공통 관측 pilot | `central_server/postgres_access.py`, `central_server/database.py:PostgresQueryStore`, `central_server/diagnostic_metrics.py`, `central_server/diagnostic_writer_registry.py` | 기존 호출별 연결과 transaction 경계를 유지한 명시적 writer 계측 및 query-cache·document-collection·market-bar·observation-revision·shadow·dataset snapshot·market metadata·news READ grouping. writer kind는 활성 호출 경계를 따라 점진 이관한다. 전체 store coverage나 pool 도입을 뜻하지 않는다. 상세 writer/kind와 검증 범위는 `docs/COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md` 참조 |
| PostgreSQL pilot 전용 통합검사 실행 | `scripts/run_postgres_access_integration.py`, `tests/integration/test_postgres_access_postgres.py` | 서버 컨테이너의 운영 DSN으로 읽기 전용 preflight 후 DB명만 전용 진단 DB로 바꿔 pilot 통합검사에 전달. URL·자격증명을 파일에 저장하지 않음 |
| NAS 운영 소스 release·반복 검사 | `scripts/nas_source_runtime.py`, `deploy/synology/docker-compose.source.yml`, `deploy/synology/source-runtime.sh` | 선택 가능한 읽기 전용 소스 마운트. 전체 release 게시·검사 후 수동 재시작하며 실행 경로를 release에 고정한다. 동일 마운트의 후보 release에서 기존 전용 PostgreSQL 검사 실행기를 사용한다. 기본 이미지 배포는 복귀 경로로 유지 |
| NAS 작업·writer 진단 | `central_server/diagnostic_workloads.py`, `central_server/diagnostic_sampling.py`, `central_server/diagnostic_runs.py`, `central_server/diagnostic_replay.py`, `central_server/diagnostic_metrics.py`, `central_server/diagnostic_writer_registry.py`, `scripts/nas_workload_diagnostic.py` | 공통 master/자식 제어 파일·잠금, CLI/API 공유 표본, API가 소유하는 단일 run·취소·보고서. 부분 재생은 빈 뉴스 claim/inline shadow와 `query_minute`의 명시 합성 시나리오 및 원본 call별 카운터를 사용하는 `recorded_counts` adapter를 지원한다. 수치 누락·지원 밖 shape·실측 mismatch는 실행 전 또는 보고서에서 실패로 표시한다. 호출별 subject와 중복값은 synthetic이므로 원래 key overlap·payload·WAL/lock 경쟁의 동등 재현은 보장하지 않는다. 실제 작업 drain ACK와 전수 writer 감사는 `docs/KIWOOM_STORAGE_WRITE_AUDIT.md` 진행 중 |
| 앱 작업·종료 수명 | `presentation/app_controller.py`, `presentation/main_window.py`, 기존 `*worker_controller.py` | AppController가 12개 feature controller, optional cache/snapshot writer, 동적 TOP20 NAS worker와 종료 단계·drain/wait를 소유한다. API runtime 교체 상태·worker 대기·factory 적용과 초기 순위 시작도 조정한다. 가격·당일고가·시가총액·분봉·지수·비교 pending, 저장 timer 및 실패 복구도 AppController가 맡는다. MainWindow는 UI effect callback, API 결과 표시, close accept/ignore를 연결하고 feature 결과 signal을 기존 화면 처리기로 직접 받는다. 순위 응답·세션 일정·구독·secondary followup은 AppController가 기존 policy coordinator를 호출해 조정하고, MainWindow는 표 적용과 data/UI callback을 제공한다. |
| 화면 | `presentation/main_window.py`, `presentation/main_table_formatting.py` | 위젯·표·사용자 입력·표시. AppController 책임을 제외한 업무 정책은 기존 coordinator/저장소를 재사용 |

## 뉴스·테마·일지

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 뉴스 입력·작업 | `central_server/news_sources.py`, `central_server/news_service.py`, `central_server/news_jobs.py`, `infrastructure/naver_stock_news.py` | Naver 검색·증권 종목 목록, TOP20 수집 범위와 BODY/RULE/AI 단계 |
| 과거 뉴스 PC 전처리·시황 업로드 | `scripts/preprocess_historical_news_locally.py`, `scripts/probe_historical_backfill.py`, `scripts/run_naver_stock_market_news.py`, `scripts/import_prepared_historical_news_to_nas.py`, `scripts/historical_collection_monitor.py`, `scripts/historical_collection_counts.py`, `central_server/database.py` | 두 원천 수집기가 기사별 BODY/RULE 준비를 병렬 실행해 PC 원장에 저장하고, 준비된 결과만 불변 스냅샷으로 NAS 정상 뉴스 테이블에 적재. 모니터 건수는 SQLite trigger로 원래 writer transaction 안에서 증분 갱신하며 최초 1회 집계 후 작은 summary 테이블을 읽음 |
| 종목 뉴스 조회 | `central_server/app.py`, `central_server/database.py`, `infrastructure/central_news_client.py`, `presentation/news_workers.py`, `presentation/stock_news_window.py`, `presentation/historical_news_archive_dialog.py`, `infrastructure/persistence/stock_news_repository.py` | NAS 저장분 200건 offset 페이지와 별도 과거 archive의 dataset/cursor·정확 ID 클라이언트/읽기 전용 창. 기존 화면 스크롤 추가 조회와 PC 직접 연결 보존 건수는 기존 경로 유지 |
| 뉴스창 실행·명령 | `presentation/news_window_coordinator.py`, `presentation/process_control.py`, `presentation/main_window.py` | 독립 뉴스 프로세스·명령 번호·창 복원 상태는 coordinator가 소유하고 메인 표는 사용자 입력만 전달 |
| 네이버 증권 시황 피드 | `infrastructure/naver_stock_market_news.py`, `central_server/market_news_sources.py`, `scripts/run_naver_stock_market_news.py`, `scripts/historical_collection_monitor.py` | FLASH/WORLD 날짜별 응답·발행시각, NAS 독립 cursor 수집, 역사 원응답 보존·진행 확인·재시작/정지 |
| 시장 뉴스 화면 | `presentation/market_news_window.py`, `news_process.py`, `infrastructure/central_news_client.py`, `central_server/database.py` | TOP20 앞 뉴스 진입, 공통/속보/해외 탭. NAS 저장 소스 조회와 PC 직접 연결의 화면 요청을 분리 |
| 후보 기업행동 백필 | `scripts/collect_historical_market_context.py`, `scripts/daishin_market_context_backfill.ps1`, `scripts/classify_historical_stock_adjustments.py`, `scripts/collect_candidate_event_disclosures.py`, `scripts/collect_candidate_exchange_disclosures.py`, `infrastructure/dart_disclosures.py` | 주도후보에 한정한 CREON 누적 수정계수 경계와 상장·거래량 0·거래 재개 후보 날짜 수집, DART 사건 인접 및 전체 거래소 공시 목록 연결, 원응답·재개·NAS 게시 gate |
| 거래소 공시 효력일 | `scripts/collect_candidate_exchange_effective_dates.py`, `infrastructure/exchange_effective_dates.py`, `scripts/reconcile_candidate_exchange_effective_dates.py`, `scripts/publish_historical_market_context_to_nas.py` | DART 접수일과 거래정지·재개·상폐 효력일/시각을 별도 보존하고 키움 일봉 거래량으로 대조. 원문 ZIP과 대조 원장을 시장 맥락 NAS 스냅샷에 게시 |
| 뉴스 판단 | `application/news_rules.py`, `application/news_grouping.py`, `application/news_analysis.py` | 규칙·사건 묶음·분석 |
| 단발성 과거뉴스 archive | `scripts/audit_prepared_historical_archive_readiness.py`, `scripts/build_prepared_historical_search_projection.py`, `central_server/historical_news_archive.py`, `central_server/app.py`, `infrastructure/central_news_client.py`, `presentation/historical_news_archive_dialog.py` | PC 미완성 파일의 봉인 차단 조건을 읽기 전용 감사하고, 봉인된 파일의 검색 페이지·정확한 기사 ID만 읽는 별도 인증 API와 PC 조회 창. 시황 projection·전체 봉인·실자료 검증은 미완료 |
| AI 공급자 | `infrastructure/news_ai.py` | 기존 외부 공급자 연동 |
| 테마 | `infrastructure/persistence/theme_repository.py`, `application/theme_matching.py`, `application/theme_preview.py` | 프로필·종목연결·가져오기·미리보기, 프로필별 대표명/분리 결정 재적용 |
| 테마 동기화 | `infrastructure/central_theme_sync.py` | 로컬 편집·pending·retry·NAS 스냅샷 |
| 설정·테마·뉴스 AI·일지 백업 파일 | `infrastructure/persistence/settings_backup.py`, `infrastructure/persistence/theme_backup.py`, `infrastructure/persistence/news_ai_backup.py`, `infrastructure/persistence/journal_backup.py`, `infrastructure/persistence/backup_file.py` | JSON과 일지 SQLite 백업 생성·복원, 사용자 선택 파일의 임시 저장 후 교체 |
| Google Drive 백업·엄격 복원 | `infrastructure/persistence/google_drive_sync.py`, `infrastructure/persistence/strict_restore.py`, `infrastructure/persistence/settings_backup.py`, `infrastructure/persistence/theme_backup.py`, `infrastructure/persistence/news_ai_backup.py`, `presentation/google_drive_worker_controller.py`, `bootstrap.py` | v2 세대별 불변 구성요소를 manifest로 마지막 게시하고 ID/hash/크기를 검증하며 v1 별칭을 호환 갱신한다. 설정·테마는 같은 SQLite 읽기 snapshot에서 만든다. 명시 복원은 검증 자료를 보관해 다음 시작 전 공통 프로세스 잠금 아래 적용하며 중단 시 원상복구한다. 다른 DB 간 동시시점·Drive 동시 편집 병합은 보장하지 않는다 |
| 테마 다건 가져오기 | `presentation/theme_dialogs.py`, `presentation/main_window.py`, `presentation/settings_request_worker.py`, `infrastructure/persistence/theme_repository.py` | Excel 파일 선택·검토·저장은 테마 관리창이 소유한다. 이미지 OCR 결과의 행 수정·종목명 확인·미리보기는 테마 대화상자 코드가 맡고, OCR 작업자 수명과 승인 뒤 비동기 저장은 메인창이 맡는다. 두 경로 모두 저장 성공 뒤 화면에 반영 |
| 테마/시장 연구 | `application/theme_leadership.py`, `application/market_research_features.py`, `application/context_candidates.py` | 대장·시장 특징·맥락 가설 |
| 시점 근거 | `domain/snapshot_provenance.py`, `application/trade_snapshot_context.py` | 당시 관측과 사후 보완 구분 |
| 일지 | `presentation/journal_workers.py`, `infrastructure/central_journal_sync.py` | 일지 조회/분석·계좌 scope 동기화 |

## 연구·타점

| 저장소 상대경로 | 책임 |
|---|---|
| `scripts/export_research_dataset.py` | NAS 일별 동결 export·bundle |
| `src/kiwoom_monitor/infrastructure/research_data_source.py` | 입력 로드·독립 시간/종목/final projection |
| `scripts/export_historical_reconstruction.py`, `scripts/prepare_historical_research_input.py`, `infrastructure/historical_reconstruction.py` | 사후 후보·대신 봉·과거 뉴스의 불변 복원과 기존 1분 전략 입력 어댑터. 후보 원본 DB의 현재 `stocks.market_code`를 보존하고 명시적 `--individual-stocks-only` 투영에서 개별 주식만 연구 모집단에 포함. strict TOP20 재생과 분리 |
| `scripts/plan_historical_research_split.py`, `application/historical_research_split.py` | 다기간 역사 사례의 시간순 TRAIN/VALIDATION/SEALED OOS 배정과 데이터셋 hash 결합 |
| `scripts/prepare_historical_development_inputs.py`, `infrastructure/research_data_source.py` | 봉인 계획에서 역사 provenance를 유지한 TRAIN·VALIDATION 독립 입력 투영. OOS는 별도 gated 경계 |
| `scripts/build_historical_exchange_case_context.py` | 월별 TRAIN·VALIDATION 후보에 해당하는 공식 거래소 효력일과 접수일·일봉 대조 결과를 불변 동반 자료로 투영. 역사 장중 접수 시각 미검증이므로 전략 신호·주문 가능성 판정에 자동 투입하지 않음 |
| `scripts/prepare_historical_baseline_requests.py`, `scripts/summarize_historical_baseline_results.py` | OOS 없는 역사 개발 입력의 두 기존 Family 고정 요청 생성과 실행·검열 사유 요약 |
| `scripts/audit_historical_minute_readiness.py`, `scripts/audit_historical_monthly_bar_quality.py`, `scripts/audit_historical_monthly_gap_causes.py`, `scripts/audit_historical_monthly_gap_raw.py`, `scripts/audit_historical_nas_minute_alignment.py`, `scripts/audit_historical_five_minute_clock.py`, `scripts/project_historical_minute_exclusions.py` | 후보일·종목일별 1분봉 최소 조건과 종일 봉 개수·긴 공백의 키움 일봉/분봉·CREON 일봉·VI 및 원응답과 저장 봉 일치를 읽기 전용 감사, NAS/CREON 거래일별 마감 체결 시각 대조와 CREON 5분봉 종료시각·해상도 구분, 원본 SHA-256에 묶인 종목일 제외 투영 |
| `scripts/plan_historical_monthly_case_selection.py` | 가격 결과를 보지 않고 월별 첫 후보일 16개를 고정하고 기존 봉인 OOS 날짜·제외 원장의 해시에 결합 |
| `scripts/audit_historical_monthly_selection_coverage.py` | 동결 선택일과 개발 전체 후보일의 후보 수·기초 분봉 수량을 원본 해시로 대조하는 읽기 전용 범위 감사. 가격 결과·OOS 미사용 |
| `scripts/prepare_historical_learning_cases.py`, `application/historical_learning_cases.py` | 사후 후보 선정·복원 뉴스 근거·미생성 AI 해석·미래 가격 결과를 분리한 불변 LLM 학습 준비 사례. strict 시점 재생이나 모델 학습 완료로 사용하지 않음 |
| `scripts/prepare_historical_news_review_queue.py`, `application/historical_news_review_queue.py` | 학습 준비 사례의 기사 식별자 중복 제거, 종목·사례별 비AI 규칙 힌트와 사람 검토 빈칸을 보존하는 불변 검토 대기열. 규칙 힌트는 정답이 아님 |
| `scripts/historical_news_review_workflow.py`, `application/historical_news_review_decisions.py` | 우선 검토 CSV 내보내기와 사람 판정·사건 ID·테마 프로필 검증, 불변 결정 결과 저장. 편집 CSV와 최종 결과를 분리하고 모델 학습 준비 상태는 false 유지 |
| `scripts/plan_historical_news_event_split.py`, `application/historical_news_event_split.py` | 사람 검토 관련 기사를 canonical event 단위로 유지한 시간순 TRAIN·VALIDATION·봉인 OOS 불변 계획. 무관·보류와 일부 검토 상태를 분리 |
| `scripts/prepare_historical_news_development_inputs.py`, `application/historical_news_development_inputs.py`, `presentation/historical_news_review_dialog.py` | 사건 분할에서 TRAIN·VALIDATION 사람 검토 기사만 투영한 RAG·미세조정 비교 공통 입력. 앱과 CLI 생성 경로, OOS payload 제외와 사건 중복 금지 검증 |
| `scripts/prepare_historical_news_blind_validation.py`, `application/historical_news_blind_validation.py` | 공통 VALIDATION에서 사람 target·사건 ID·테마명을 제거한 방법 중립 평가 요청. 개발 dataset ID·validation hash 결합과 정답 누수 재귀 검증 |
| `scripts/prepare_historical_news_method_results.py`, `application/historical_news_method_results.py` | prompt baseline·RAG·미세조정 예측을 동일 블라인드 요청 전체에 결합하는 불변 결과. 누락·중복·추가 응답과 OOS·정답 사용 표시 차단 |
| `scripts/evaluate_historical_news_method.py`, `application/historical_news_method_evaluation.py` | 평가기 전용 VALIDATION target 결합, 사건 pairwise 군집·테마 집합·프로필·coverage 지표. 관련성 분류와 자동 모델 승격은 지원하지 않음 |
| `scripts/assess_historical_development_readiness.py`, `infrastructure/historical_research_readiness.py` | 역사 개발 분할의 모든 사례·종목별 분봉·연속 1분쌍 완전성 gate. 이전 분할 재생용 후보군 seed는 검사 모집단에서 제외. 희소 구조 실행은 명시적 예외 |
| `src/kiwoom_monitor/research_process.py` | campaign worker·가설·순차/final 평가 프로세스 |
| `scripts/run_research.py` | 기존 runner의 재생·실행·평가 저장. 판단은 최대 128건씩 저장하고 사건 참조 전 즉시 flush하며 논리 결과 해시는 순차 계산 |
| `src/kiwoom_monitor/application/research_replay.py` | 시점별 입력 재생. 투영된 역사 입력은 별도 역사 후보군 종류를 명시해 재생하며 대상 봉·같은 종목/당일 이력을 돌파·눌림 Factor에 전달 |
| `src/kiwoom_monitor/application/research_execution.py` | PaperExecutionEngine·봉 체결·현금/비용. 기존 정수 `fixed_bps/v1`과 소수 bp 문자열 `fixed_bps/v2` 구분 |
| `src/kiwoom_monitor/application/research_evaluation.py` | 연구 성과·품질·평가 |
| `src/kiwoom_monitor/application/research_families.py` | 등록 Family·Factor·허용 파라미터 |
| `src/kiwoom_monitor/application/breakout_strategy.py` | 완료봉 돌파 판단 |
| `src/kiwoom_monitor/application/pullback_reacceleration_strategy.py` | 눌림 후 재가속 판단 |
| `src/kiwoom_monitor/application/research_search.py` | 제한 trial·개발 근거·후보 선택 |
| `src/kiwoom_monitor/application/research_hypotheses.py` | 결정적 가설·후속 생성·부모 계보 |
| `src/kiwoom_monitor/infrastructure/persistence/research_repository.py` | v24 run/campaign/lease/final/가설·순차 소유 원장. 판단 묶음은 한 SQLite 트랜잭션으로 불변성·순서를 검증 |
| `src/kiwoom_monitor/presentation/research_dialog.py`, `presentation/historical_news_review_dialog.py` | 연구·campaign·가설·순차/final UI와 과거 뉴스 사람 검토·작업표 저장·불변 결과 동결·사건 분할 계획 실행 |
| `src/kiwoom_monitor/application/research_resources.py` | RSS/preflight·CPU 양보 |
| `src/kiwoom_monitor/application/forward_evaluation.py` | feedback evidence/review/제안·forward 평가 |
| `src/kiwoom_monitor/application/feedback_strategy_revision.py` | 명시 채택 버전·재검증 spec/dispatch |

## 계좌·인증·모의 실행

`src/kiwoom_monitor/` 기준 경로다.

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 계좌 신원 | `application/account_identity.py`, `infrastructure/kiwoom_rest/account_identity.py` | scope/binding; A4B 직접 연결 미완료 범위 별도 |
| 인증 | `central_server/credential_store.py`, `central_server/credential_runtime.py` | 암호화 vault·prepare/apply·세대·drain |
| 인증 UI | `infrastructure/central_credentials_client.py`, `presentation/nas_credentials_dialog.py` | HTTPS 요청·입력·worker |
| 후보 감시 | `central_server/candidate_monitor.py` | 현재 shadow producer·후보 알림 |
| 운용 명세·입장 | `application/mock_automation_specification.py`, `application/mock_automation_admission.py` | 동결 spec·적격성·단일 계좌 lease |
| 위험·복구·전달 | `application/mock_automation_risk.py`, `application/mock_automation_recovery.py`, `application/mock_automation_execution.py` | 실제 위험 근거·대사·Decision gate |
| 주문 원장 | `application/order_lifecycle.py`, `central_server/execution_runtime.py` | intent·unknown·멱등·owner |
| 자동 모의 수명 | `central_server/mock_automation_runner.py`, `central_server/mock_automation_supervisor.py`, `presentation/mock_automation_dialog.py` | runner·영속 제어·UI |

## 다음 개발의 진입점

외부 `kiwoom_history_backfill`의 후보 DB·기존 수집 스크립트는 앱 모듈이 아니다. 현재 표본 경계는 `infrastructure/historical_backfill.py`, `scripts/probe_historical_backfill.py`, 32비트 COM 표본·연속조회 브리지 `scripts/daishin_stockchart_probe.ps1`과 `scripts/daishin_stockchart_backfill.ps1`에 있다. CREON의 종목 분봉·시총/주식수·수정주가 작업은 참조 `stocks.market_code` 0/10인 코스피·코스닥 개별 주식만 대상으로 하며 다른 상품의 기존 원응답은 삭제하지 않고 작업 원장에서 `excluded`로 분리한다. [과거 수집 기획](docs/HISTORICAL_BACKFILL_PLAN.md)으로 원천별 표본을 확인한 뒤 기존 `research_data_source`, 뉴스 revision, 프로필 저장 경계에 연결한다.

관련 테스트는 각 책임의 `tests/unit/test_*.py`와 `scripts/check_*.py`에서 찾는다. 문서 정리에서 이전 테스트 개수를 새 실행 결과로 복사하지 않는다. 실제 코드 변경 때 해당 경계의 의미 있는 회귀와 필요한 통합 검증을 수행한다.

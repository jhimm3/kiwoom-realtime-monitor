# QueryStore 호출자와 소비자 연결 검토

검토일: 2026-10-04 · 범위: 현 워크트리 소스 · DB 리팩터링 전 연결 기준

이 문서는 실제 코드의 조립과 호출을 읽은 결과다. 운영 DB/API/UI 실행 성공,
호출 빈도 또는 전체 동적 호출 coverage를 증명하지 않는다. 기준 commit과
직접 연결 원장은 [기준선 안내](README.md)와 `baseline_manifest.json`을 따른다.

## 1. 조립과 계약

`central_server/app.py:create_app`은 `create_query_store(active.database_url, ...)`로
store 하나를 만들고 `initialize()`한다. factory는 URL scheme에 따라
`SQLiteQueryStore` 또는 `PostgresQueryStore`를 반환한다. 따라서 소스에서
QueryStore 연결이 확인돼도 실제 backend가 PostgreSQL인지는 실행 설정으로
별도 확인해야 한다. DSN이나 자격증명은 이 문서에 기록하지 않는다.

현 소스의 클래스 직접 정의는 `QueryStore` 99개, `PostgresQueryStore` 105개,
`SQLiteQueryStore` 104개 메서드다. 다음 차이는 연결 누락과 구분한다.

- `__init__`, PostgreSQL `_connect`/SQLite `_connection`, `_replace_bars`는 구현 경계다.
- `set_news_job_wakeup`은 양 backend가 구현하는 선택적 callback hook이다.
  현재 Protocol에는 없으며 `NewsJobRunner`가 literal `getattr`로 찾아 등록/해제한다.
- `explain_news_job_claim_plan`, `analyze_news_job_claim_read_only`는 PostgreSQL에만
  있는 진단 capability다. 진단 route는 literal `getattr`로 지원 여부를 확인한다.

이 계약을 이번 조사에서 추가·삭제하거나 통합하지 않았다.

## 2. store가 전달되는 책임 경계

아래 receiver는 이름만 보고 정한 것이 아니라 생성자 인자·필드 대입·조립
호출을 함께 확인했다. `Any` 또는 작은 Protocol을 쓰는 경우에도 실제 중앙
조립 경로를 근거로 기록한다. 클래스의 같은 receiver는 class scope 안에서만
해석하며 다른 파일의 `self._store`까지 확대하지 않는다.

경로는 `src/kiwoom_monitor/` 기준이다.

| 소비 경계 | receiver | 확인한 전달 경로 |
|---|---|---|
| `central_server/app.py:create_app`과 내부 route/lifespan | `store` | `create_query_store`의 반환값을 closure가 공유 |
| 같은 파일 `_stored_market_response`, `_archived_chart_response`, `_explicit_coverage_complete` | `store` | route의 `asyncio.to_thread(helper, store, ...)`; helper 간 전달도 있음 |
| `CentralRestBroker` | `self._store` | `create_app → CentralRestBroker(client, store, MarketDataIngestor(store).ingest)` |
| `MarketDataIngestor` | `self._store` | 생성자 `store: QueryStore`를 필드에 대입; broker response handler |
| `CentralRealtimeCollector` | `self._store` | `create_app`에서 `store=store`; 선택적 store 인자 |
| `AutonomousTop20Service` | `self._store` | 중앙 조립에서 broker/hub/store 전달 |
| `MarketEventService` | `self._store` | 중앙 조립에서 broker/hub/store 전달 |
| `YahooDelayedMarketCollector` | `self._store` | 중앙 조립에서 store와 symbol 설정 전달 |
| `CandidateMonitor` | `self._store` | 중앙 조립/설정 교체에서 `from_json(store, ...)` |
| `CentralNewsService` | `self._store` | 중앙 조립에서 store 전달 |
| `NewsJobRunner` | `self._store` | `CentralNewsService → NewsJobRunner(store, ...)` |
| `QuerySetNewsCollector` | `self._store` | `CentralNewsService` 생성자가 같은 store를 전달 |
| `MarketFeedNewsCollector` | `self._store` | `CentralNewsService` 생성자가 같은 store를 전달 |
| `CentralAIService` | `self._store` | `create_app → CentralAIService(active, store)` |
| `CredentialStore` | `self._metadata` | `create_app → CredentialStore(directory, store)`; 비밀 파일과 DB metadata의 별도 경계 |
| `credential_store.py:compose_credential_settings` | `store` | 중앙 조립에서 vault와 store 전달 |
| `CredentialRuntime` | `self.store` | 중앙 조립에서 `CredentialRuntime(vault, store)` |
| `MockCredentialOwner` | `self.store` | 중앙 조립에서 store 전달; bundle 교체에도 같은 store 전달 |
| `MockAccountBundle` | `self._store`와 생성자 `store` | 중앙 조립/owner가 store 전달; 실행 repository와 신원 helper로 이어짐 |
| `RealCredentialOwner` | `self.store`와 생성자 `store` | 중앙 조립에서 store 전달; 계좌 event/recovery 저장 callback |
| `MockAutomationSupervisor` | `self._store` | 중앙 조립에서 store 전달; `ForwardEvaluationRepository(store)` 생성 |
| `MockAutomationRunner` | `self._store` | supervisor가 store 전달; 별도 evaluation repository 생성 |
| `ExecutionRepository` | `self._store` | `MockAccountBundle → ExecutionRepository(store)`; `ExecutionStore` 구조 계약 |
| `ForwardEvaluationRepository` | `self._store` | 중앙 route/bundle/supervisor/runner가 store 전달; `ForwardEvaluationStore` 구조 계약 |
| `application/account_identity.py` helper | `store` | 중앙 lifespan/실전·모의 owner가 `AccountRegistryStore` 인자로 전달 |
| `AICredentialOwner`, `NaverCredentialOwner`, `DartCredentialOwner` 생성자 | `service._store` | 중앙 조립에서 앞서 만든 AI/news service를 전달; credential profile 등록 |
| `diagnostic_replay.py` | `store`, `minute_store`, `no_history_store` | dedicated DB 사전검사 뒤 `_ReplayStore` 생성; production 조립과 별도 |

`MockAutomationRunner/Supervisor._repository.load_mock_automation_control`은
store 직접 호출이 아니라 `ForwardEvaluationRepository` 호출이다. 작은
repository 내부의 store 호출까지 이어서 확인해야 한다. 이를 직접 연결 두
개로 세거나 repository를 새 PostgreSQL connection으로 오인하지 않는다.

## 3. 데이터 입력에서 실제 소비자까지

여기서 '소비자'는 정적으로 연결된 코드다. 해당 분기의 실행·화면 표시·네트워크
성공은 기능별 변경 후 별도 acceptance로 확인한다. 저장 테이블의 상세 컬럼은
[DB atlas](../database/atlas.html)와 기존 store 원장을 참조한다.

| 기능 | 입력/변환과 store 호출 | API/소비자 경로 | 수정 전후 보존할 계약 |
|---|---|---|---|
| REST 영속 cache | `CentralRestBroker._resolve_cache_or_queue → load_query`; persistence 작업 → `save_query` | broker의 `BrokerResult(payload, has_next, next_key)` → REST 호출자 | TTL·연속조회·credential generation. cache hit에서도 response handler가 호출되는 분기. 응답 적재와 cache 저장은 각각의 기존 경계 |
| 조회 분봉 | broker response handler → `MarketDataIngestor._ingest_minutes → replace_minute_bars` | minute/recent-minute route → `RemoteKiwoomRestClient.load_stored_minute_bars_with_coverage` → validation/failover wrapper → `MinuteChartService` | canonical 봉·metadata·revision 한 transaction, 날짜/시장 키, SOR 우선 및 없을 때 KRX+NXT, coverage 필드 전달 |
| 조회 일봉/신고가 | `MarketDataIngestor._ingest_daily → replace_daily_bars`; TOP20 준비의 dated coverage 문서 | daily-bars route의 `load_daily_bars + load_documents` → remote client의 coverage 반환 → wrapper → `DailyHighService` | 전체 source 이력 종료/기간 증거와 NXT eligibility. 봉 존재만으로 완료로 바꾸지 않음 |
| 기본정보·NXT·수급·신고가 문서 | `MarketDataIngestor`/TOP20 준비 → dataset 또는 `upsert_documents` | snapshots/content route → remote client/전용 service/worker | 종목·날짜·시장·07:00 기본정보 갱신 구간, 준비 단계별 성공/재시도 |
| 실시간 최신값 | `CentralRealtimeCollector._flush_snapshot_cycle → save_realtime_snapshots` | `load_realtime_snapshots` → WebSocket 최초 subscribe snapshot; latest-market-caps route | event hub의 즉시 전달과 DB flush 분리, 최신 pending 합치기·실패 복구·수신시각 |
| 실시간 분봉/초봉 | collector → `save_minute_bars`, `finalize_minute_bars`, `save_second_trade_bars` | 분봉은 저장 분봉 API; 초봉의 일반 앱 reader는 이 조사에서 확인하지 못함 | 닫힌 QUERY 봉 우선권·같은 키 delta·늦은 정정·독립 COMMIT. reader 부재를 삭제 근거로 삼지 않음 |
| TOP20 membership·편입·지수 | 자율 순위/완료 분 집계 → dataset 저장; 최초 편입 → entrant 문서 | snapshots route → remote dataset reader; `load_top20_statistics` → 통계 API | RAM 최신 membership 공개와 DB fallback, entrant 최초 편입, 지수 durable outbox. 서로 다른 세 transaction 보존 |
| VI·상한가·조건 코호트 | `MarketEventService → append_vi_events/append_upper_limit_facts/record_hot_cohort_revision` | events route → history/cohort/status 문서; TOP20/연구 입력 | 동일 진행 중 key만 결과 공유, 선행 실패 재시도, 별개 key 독립성, 이력·projection 원자성 |
| 외부시장 | `YahooDelayedMarketCollector → save_external_bars` 및 상태 문서 | external-bars route → remote reader | instrument/contract/timeframe/bar_time, 동일 OHLCV no-op과 관측 상태의 별도 의미 |
| 뉴스 검색/시황 입력 | news/query-set/market-feed collector → 문서 또는 `save_news_source_page` | `CentralNewsService → load_stock_news_articles/load_confirmed_news_articles`; stored-page/market-feed API → `CentralNewsClient` → 뉴스 worker/창 | 기사·source observation·cursor·job의 관계, page 실패 rollback, 200건 page. PC historical archive는 별도 SQLite reader |
| 뉴스 BODY/RULE/AI | `NewsJobRunner`의 claim/body/event/finish/retry; `CentralAIService`의 AI revision/result | news history/content API → 중앙 news/content client → 조회·동기화 | SKIP LOCKED, lease/attempt, 역사 scope 소유권, revision/usage 원자성, enqueue wake-up hook |
| PC 과거뉴스 완료 | 인증 historical claim/complete route → `claim_external_historical_news_job/complete_external_historical_news_job` | `CentralContentClient`의 별도 claim/complete/upload 메서드 → PC 전처리 작업 | 일반 NAS claim과 구분, 처리 owner·stage·scope, completion 원자성 |
| shadow/연구 | `CandidateMonitor` → observation revisions, state/evaluation; export route → 고정 membership export | research candidates/observations API → `CentralContentClient` → 연구 입력 | cursor/revision 순서, watermark, 독립 monitor 저장. frame 후보는 기본 OFF·fallback 유지 |
| execution/모의자동화 | bundle/runtime → `ExecutionRepository`; supervisor/runner/route → `ForwardEvaluationRepository` | 계좌/모의자동화/연구 인증 route → scoped client/화면 | account_ref/run/owner_token·lease·settings revision·CAS·approval fence. 시장 cache와 transaction을 합치지 않음 |
| 실계좌·신원·비밀 | owner → real account event/recovery/settings; account helper와 vault/runtime → 신원·activation metadata | credential/account settings route; 별도 verified binding | encrypted file commit·DB activation·계좌 신원·projection의 기존 순서와 실패 복구 |
| 설정·테마·일지 콘텐츠 | content PUT/POST → `replace_documents/upsert_documents`; operational setting 별도 저장 | `CentralContentClient` → settings/theme/journal/content sync → 각각 로컬 DB와 UI | collection/owner/key·일지 scope·tombstone/delta·theme history. 서로 다른 로컬 DB와 중앙 COMMIT을 하나로 취급하지 않음 |

## 4. 소스상 비호출/간접 경로와 남은 증거

전체 `src`에서 PostgreSQL 메서드 이름과 같은 Attribute 및 literal
`getattr`/`hasattr`를 탐색하면 store 파일 밖 후보가 847개다. `close`, `initialize`,
같은 이름의 PC repository·remote client·일반 object 호출도 포함하므로 847개를
PostgreSQL 호출로 세지 않는다. 이 값은 이번 소스 snapshot의 보조 탐색값이다.

`save_five_minute_bars`, `load_five_minute_bars`, `save_market_data_metadata`,
`load_market_data_metadata`의 `src` 외부 reference는 찾지 못했다. `_replace_bars`는
외부 reference가 없어도 store의 분봉·일봉 메서드가 내부 위임하는 helper다.
source caller 검색만으로 unused를 확정하거나 삭제하지 않는다.

raw connection context가 있는 다섯 메서드 중 나머지 하나는
`register_account_scope_alias`다. `application/account_identity.py`의
`link_local_scope_to_verified_binding`가 직접 호출하지만, 그 helper의 `src`/`scripts`
호출자는 이번 검색에서 찾지 못했다. 단위시험과 별도 PostgreSQL 검사에는
호출이 있다. 따라서 '현재 운영에서 실행됨'으로 분류하지 않는다.

추가로 다음 경로를 자동 감사에서 빠뜨리면 안 된다.

- `asyncio.to_thread(store.method, ...)`: method Attribute는 Call의 함수가 아니라 인자다.
- `asyncio.to_thread(helper, store, ...)`: store를 받는 helper entry 연결도 별도 보존한다.
- `getattr(store, "method", None)`: capability 참조와 실제 실행을 구분한다.
- `hasattr(store, "method")`: 지원 여부 검사이며 transaction 호출이 아니다.
- `handler = getattr(...); handler(...)`: reference와 alias invocation을 섞어 호출 수를 부풀리지 않는다.
- field 대입은 `self._settings, self._store = settings, store` 같은 tuple 대입도 있다.
- 작은 Protocol을 받는 persistence repository는 중앙 조립과 내부 DB 호출을 함께 본다.

실행 설정별 branch, reflection의 비literal 이름, 제3자 코드, 모든 PC 화면 끝단의
실행 검증은 남아 있다. O12 장중 trace/capture와 replay는 별도 검증 범위이며,
이 지도 작성으로 완료 처리하지 않는다.

## 5. 다음 정적 감사 구현 계약

다음 작업은 새 DB abstraction이 아니라 이 소스 지도를 반복 비교하는 감사 도구다.
기존 `audit_postgres_access.py`의 driver/transaction 근거와 충돌하지 않게 별도 결과를
생성하거나 명확한 추가 section으로 연결한다. 앱 import, DB 연결, SQL 실행은 하지 않는다.

1. Protocol/양 backend의 메서드 signature·delegate·source hash를 저장한다.
2. 모든 후보 reference는 보존하고, 이 문서에서 검토한 file/scope/receiver 또는
   실제 QueryStore annotation/factory 대입 근거만 확인된 store reference로 분류한다.
   동일 field 이름이나 단순 method 이름 일치만으로 확정하지 않는다.
3. direct call, `to_thread` 등 callable argument, 기타 bound reference,
   literal getattr/hasattr를 서로 다른 reference kind로 기록한다.
4. 가장 가까운 route decorator가 있을 때 HTTP method/path를 연결한다. helper를
   다른 route까지 무조건 전파하지 않고 확인된 forwarding edge를 별도 기록한다.
5. 비교 identity는 file/lexical owner/receiver/method/reference kind/enclosing dispatch/
   route와 multiplicity다. line은 탐색용이며 줄 이동만으로 연결 단절을 판단하지 않는다.
6. 새로 생기거나 사라진 확인된 reference, contract signature 변화, parse 실패,
   검토 receiver 근거가 사라진 경우는 검토 필요로 표시한다. 후보는 자동 승인하지 않는다.
7. synthetic source fixture로 직접 호출/함수 인자/tuple 대입/getattr·hasattr/
   동일 이름 PC repository 오탐/줄 이동/실제 reference 삭제를 검증한다.
   후속 결과는 baseline을 덮어쓰지 않는다.

이후 DB 동작 변경은 기능 하나를 선택하고 같은 저장→조회→소비 경로의 결과와
commit/rollback/retry를 검사한다. pooling, COMMIT 통합, migration, NAS active release
변경을 이 정적 도구 구현에 포함하지 않는다.

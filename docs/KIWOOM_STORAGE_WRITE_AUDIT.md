# 키움 저장 경로 감사 — 첫 계측 범위

2026-10-06 C1 후속: `autonomous_top20`의 0w pending 저장은 단일 owned save task가
실제 DB thread의 완료/실패까지 소유한다. index-loop/종료 대기 취소가 save를 취소하지
않으며 종료는 producer stop→실제 save drain→pending final flush 순서다. 실패분은
같은 종목의 새 pending을 덮지 않고 복원한다. 최종 실패는 예외와 pending으로 남는다.
기존 snapshot key/UPSERT/transaction 및 0.25초 관리 주기는 유지한다. PC 관련 74건
통과, 전용 PostgreSQL 2건은 미실행(skip). 강제 종료·지속 장애의 RAM 소실과
subscriber queue의 아직 처리하지 않은 입력은 이 종료 수정의 보존 보장 범위 밖이다.

기준: 2026-09-26 · 앱 2.1.0 · NAS `2026.09.26-writer-diagnostics-v4` 실행 확인

## 소스 전수 열거와 실행 경계 (2026-09-26 추가)

[`kiwoom_storage_source_inventory.json`](kiwoom_storage_source_inventory.json)은
`scripts/audit_kiwoom_storage_paths.py`가 현재 `src/kiwoom_monitor` AST에서 다시 생성한
읽기 전용 원장이다. **소스에 이름이 있는 것과 현재 운용에서 호출되는 것은 다르다.**
키움 API ID 문자열은 32종, 중앙 broker 허용 목록의 ID는 20종이다. 그중
broker 호출의 첫 인수가 리터럴로 확인된 ID는 10종(호출 지점 29곳)이고,
동적 ID를 넘기는 지점 19곳은 호출자까지 따라가야 한다. 예를 들어
`ka10016`은 허용 목록과 저장 handler에는 남지만 현재 소스의 직접 호출
리터럴은 0건이다. 주문 ID `kt10000`~`kt10003`은
`infrastructure/kiwoom_rest/mock_execution.py`의 전송 상수이고,
`kt10006`~`kt10009`, `kt50000`~`kt50003`은 같은 파일의 지원 목록이다.
이 12개를 실제 주문 발생 건수나 NAS broker 호출로 세지 않는다.

동적 호출 19곳의 원장에는 이제 둘러싼 함수·클래스(`owner`)도 있다.
그중 `HistoricalHighService._load_rows`는 호출자에서 `ka10094`·`ka10083`·
`ka10081`을, NAS `AutonomousTop20Service._request_with_retries`는
`ka10080`·`ka10081`을 받는다. `mock_account._pages`는 실제 호출자에서
`ka10075`·`ka10076`·`kt00018`·`kt00001`·`kt00015`를 받는다.
`KiwoomAccountIdentityReader.verify`의 상수는 `ka00001`이다. 나머지
failover/validation/remote client와 chart/account adapter는 API ID를
전달하는 범용 경계다. `app.kiwoom_query`는 요청으로 전달된 API ID를 받아
허용 목록에서 검증하는 경계이므로 정적 문자열만으로 실제 사용을 확정하지 않는다.

중앙 실시간 등록 payload의 리터럴 type은 `0B`, `0w`, `0g`, `00`, `04`,
`0J`, `0U`, `0s`, `1h` 9종이다. 등록 지점은
`central_server/realtime_collector.py::_send_subscription`이며 계좌 전용
`mock_account_monitor.py`도 `00`/`04`를 별도로 등록한다. `0W` 대문자와
실제 등록값 `0w` 소문자를 혼동하지 않는다.

PostgreSQL `PostgresQueryStore`에서 **직접 SQL 쓰기 리터럴이 발견된 메서드 31개**와
PC persistence 소스에 선언된 SQLite 테이블 이름 97개를 원장에 보존했다.
31은 전체 writer 수가 아니다. SQL helper, 동적 쿼리, 같은 메서드의 여러
commit 및 다른 저장 모듈은 정적 추출만으로 세지 못한다. 현재 런타임 계측
registry의 12개는 31개와 서로 다른 기준이므로 합산하지 않는다.

| 확인된 원천 | 중앙 저장 경계 | 별도 쓰기/복제 | 코드 기준 commit |
| --- | --- | --- | --- |
| `ka10080` | broker handler → `MarketDataIngestor._ingest_minutes` → `replace_minute_bars`의 봉·metadata·revision | SOR 비교 자료가 있을 때 `minute_trade_value_comparisons` document, 장후 collector의 coverage document; PC `minute_bars`/sync log | 응답의 봉 배치 1회, SOR 비교는 해당 일자별 별도 최대 1회. coverage는 응답 handler 밖의 별도 사건. 2초 RAM cache이므로 보통 persistent query cache는 없음 |
| `ka10081` | broker handler → `_ingest_daily` → `replace_daily_bars` | 장후 coverage, PC `daily_bars`/sync log; 조건에 맞으면 persistent query cache | 봉 배치 1회와 해당 캐시 저장 최대 1회는 독립 commit. coverage는 별도 사건 |
| `ka10001` | `_ingest_fundamentals` → latest `stock_fundamentals` document와 날짜별 dataset | 별도 persistent cache, PC stock 기본정보 | handler에서 document 1회 + dataset 1회, 캐시 대상이면 추가 1회. 같은 응답의 독립 commit 경계이지만 통합 후보 확정은 아님 |
| `ka10100` | `_ingest_nxt_eligibility` → latest document와 날짜별 dataset | 별도 persistent cache, PC NXT 상태 | handler에서 2회, 캐시 대상이면 추가 1회. 각 호출은 독립 commit |
| `0B` | `CentralRealtimeCollector._flush_snapshot_cycle` → latest, delta minute, closure, second bar | account-entry/reference/history 자료가 같은 cycle에 있으면 추가 저장; PC는 중앙 읽기/동기화 cache | 현재 코드상 조건부 저장 호출 각각 독립 commit. 한 0B당 고정 1회가 아니다. 로컬 v4는 성공한 계측 writer의 flush별 COMMIT 분포를 기록하며 NAS 실측은 배포 후 필요 |
| `00`/`04` | 실시간 파싱 후 계좌 handler → 실계좌 사건/복구 원장 | mock/real 계좌·Journal projection 경로는 별도 소유자 | 계좌 identity·order 이벤트와 시장 cache는 통합하지 않음; source별 횟수는 미확정 |

`ka10045`·`ka90008`은 `investor_flow`·`program_flow` dataset으로 저장되고,
`ka00198` 순위는 `ranking` dataset 뒤 TOP20 membership/index/entrant 등 별도
사건으로 확장된다. `ka20005`/`ka20006`의 시장지수 저장은 장후 collector 경로를
따라가야 하므로 위 표의 응답당 commit 숫자에 섞지 않았다. 정적 원장의
범용 호출자의 실제 요청 목록과 account/order·Journal 최종 reader 대조가 남아 있어
전수 경로와 source별 WAL 귀속은 아직 완료되지 않았다.

계좌·주문 경계에서는 다음을 별도로 확인했다. 실계좌 WebSocket `00`/`04`
사건은 `real_runtime._write_account_events`의 전용 큐를 지나
`save_real_account_event`가 `central_documents(collection='real_account_event')`에
해시 키로 저장한다. `ka10075`·`ka10076`·`kt00018`·`kt00001`의 계좌 복구
응답은 `mock_account._pages`로 읽고 `save_real_account_recovery`가
`central_documents(collection='real_account_recovery')`에 저장한다.
두 PostgreSQL 메서드는 각각 별도 connection transaction이고 저장 전에
계좌 binding을 검증하며 `credential-activation` advisory lock을 잡는다.
`kt00015` 비용 자료와 `kt00007` 과거 체결은 journal의 별도 조회 경로에도
사용된다. 중앙 주문 원장의 `append_execution_event`는
`central_execution_events` 삽입과 `central_execution_intents` 갱신을
한 transaction에 묶는다. PC의
`journal_execution_event_projections`는 이 원장의 동일 DB 중복 writer가
아니라 journal projection이며, 실제 동기화 시점·읽기 화면 대조는 남았다.
주문 전송 `kt10000`/`kt10001`과 취소 `kt10003`의 요청 결과, 실계좌 사건,
중앙 실행 원장을 하나의 저장 호출로 간주하지 않는다.

실제 저장 변경을 위한 검증 코드는
`tests/unit/test_storage_boundary_concurrency.py`에 있다. 임시 SQLite DB와 실제
`CentralRealtimeCollector`를 사용해 동일 operation 재전송, 서로 다른 종목의
동시 저장, 두 번째 행 실패 시 전체 rollback, 잘못된 동일 ID 거부, finalization
rollback/멱등성, commit 성공 후 응답 손실로 인한 collector 재시도,
metadata·관측 revision 보존을 실행한다.
이는 **SQLite·collector 계약 검증**이며 NAS PostgreSQL의 동시 충돌과 WAL 성능,
프로세스 강제 종료를 검증한 것으로 확대 해석하지 않는다.
`tests/integration/test_storage_boundary_postgres.py`는 전용 이름
`kiwoom_monitor_diagnostic_test`의 PostgreSQL URL을 명시할 때만 동시 분봉 쓰기와
commit 후 재시도를 실행한다. 현재 전용 DB가 구성되지 않아 이 검사는 안전하게
skip됐으며 운영 `kiwoom_monitor` DB에 시험 행을 쓰지 않았다.

로컬 v4의 0B flush 식별자는 기존 직렬화 lock 안에서 정해지고
`asyncio.to_thread`의 저장 호출에 전달된다. 계측을 켠 기간의 성공 writer만
`realtime_flushes`로 묶는다. 실패했거나 쓰기가 없던 cycle은 수에 포함되지
않으므로 이 지표만으로 전체 수신 0B당 평균 COMMIT 수를 주장하지 않는다.

NAS 호스트에서 `scripts/nas_storage_mapping.py`를 읽기 전용 실행해
[`nas_storage_mapping_20260926.json`](nas_storage_mapping_20260926.json)에
실제 mountinfo·sysfs 경로를 보존했다. 확인된 bind 원본의 `PGDATA`와
`pg_wal`은 같은 `/volume1` Btrfs mount이며 source는
`/dev/mapper/cachedev_0`(장치 `dm-4`)이다. sysfs slave 관계는
`dm-4 → dm-1 → md4 → nvme0n1p1/nvme1n1p1`과
`dm-4 → dm-3 → md2/md3 → SATA partitions`의 두 갈래다.
이는 **연결 그래프**이지 각 COMMIT의 실제 I/O 배분이나 Synology 캐시
정책을 측정한 결과가 아니다. Docker bind 원본은 기존 `docker inspect`
관측값을 입력했으며 새 스크립트 자체가 컨테이너 설정을 변경하지 않았다.
NAS maintenance에 같은 JSON을 배치하면 v4 A/B/A 결과가 `storage_mapping`에
이를 첨부한다. JSON이 없거나 형식이 맞지 않으면 `available=false`로
표시한다. 장치별 I/O 표본은 구간 전체 값이며 writer별 기여량이 아니다.

## 범위와 주의

첨부 요구사항에 따라 현재 소스의 저장 경계를 다시 확인했다. 이 문서는 **전수 감사 완료 보고서가 아니다.** 이번 회차에는 PostgreSQL 저장 계측이 이미 있거나 REST 응답과 직접 연결되는 핵심 시장·실시간 writer를 정리하고, 그 writer 표본의 단계 시간을 확장했다. 계측되지 않은 저장소를 0건으로 해석하지 않는다.

현재 루트 `DB_SCHEMA.md`와 `API_CONTRACT.md`는 스키마와 호출 계약의 참조로 확인했고, 런타임 경로는 `src/kiwoom_monitor/central_server/`에서 재검증했다. 기존 `record_writer_transaction()` 호출은 성공 경로에서만 발생하므로 진단의 오류·재시도 값은 `null`(미계측)이다. 전달 행수는 실제 INSERT/UPDATE 성공 행수가 아니라 시도한 입력 행수다. COMMIT 단계가 따로 재지 않은 writer의 COMMIT percentile은 `null`이며 전체 elapsed를 COMMIT으로 간주하지 않는다.

## 확인한 PostgreSQL writer

| Writer ID | 입력/호출 경로 | 실제 저장 | PostgreSQL transaction/commit | 진단 표본 |
|---|---|---|---|---|
| `rest.market_bars.minute` | REST `ka10080` → `CentralRestBroker` response handler → `MarketDataIngestor._ingest_minutes` → `PostgresQueryStore.replace_minute_bars` | `central_minute_bars`, observation metadata, 설정 시 observation revisions | 분봉·metadata·revision을 한 DB 연결에서 처리하고 성공 시 한 transaction/commit. 입력 관측별 canonical bar와 metadata 실행, revision 최신값 SELECT가 있으며 hash 변경 시 revision INSERT가 추가된다. 진단 capture는 이제 revision SELECT/INSERT 실행 수를 별도 기록한다. SOR 비교 경로는 별도이며 이 메서드 밖의 전체 요청 저장 경계 수는 별도 계측 대상 | 연결·봉·metadata·revision 시간·revision 조회/삽입 수·COMMIT·전체 |
| `rest.market_bars.daily` | REST `ka10081` → response handler → `_ingest_daily` → `replace_daily_bars` | `central_daily_bars`, observation metadata | 한 writer transaction/commit | 연결·봉·metadata·revision·COMMIT·전체 |
| `rest.query_cache` | broker persistence queue → persistent TTL 대상 응답 → `save_query` | `central_api_query_cache`; 만료 행 정리도 같은 transaction | 캐시 UPSERT와 만료정리를 한 commit으로 처리. 앞선 response-handler 저장과는 별도 transaction | 연결·UPSERT·정리·COMMIT·전체 |
| `realtime.latest` | Kiwoom WebSocket realtime flush → `save_realtime_snapshots` | `central_realtime_latest` | 성공한 메서드 호출마다 한 connection context transaction | 성공 호출 전체 시간 |
| `realtime.minute` | 0B 집계/flush → `save_minute_bars` | `central_minute_bars`, metadata, 필요 시 revisions, `central_minute_bar_operations` | 한 호출의 transaction/commit을 유지한다. advisory lock 뒤 작업 ID hash와 query-authority를 batch prefetch하고, canonical upsert의 `RETURNING` 행을 metadata/revision에 재사용한다. 반복 minute key는 기존 순차 authority 읽기를 유지한다. | 성공 호출 전체 시간; batch lookup key 수와 봉/metadata/revision 단계 |
| `realtime.minute_finalize` | 0B 분 마감 → `finalize_minute_bars` | minute operations와 조건부 finalized observation revision | 메서드 호출 단위 transaction | 성공 호출 전체 시간 |
| `realtime.second_bar` | 0B 초봉 flush → `save_second_trade_bars` | `central_second_trade_bars` | 메서드 호출 단위 transaction | 성공 호출 전체 시간 |
| `dataset.snapshot` | broker response handler 및 central collector → `save_dataset_snapshots` | `central_dataset_snapshots`, 전달된 observation metadata/revision | 배치 전체 한 transaction. `save_dataset_snapshot` 단건은 배치 wrapper | 성공 호출 전체 시간 및 일부 종류는 commit 측정 |
| `document.collection` | response handler/service → `upsert_documents` | `central_documents`; 특정 collection은 theme/article revision 추가 | collection batch 한 transaction | 성공 호출 전체 시간 |
| `news.job_claim`, `news.job_finish`, `news.body` | News worker → claim/finish/BODY 저장 경계 | `central_news_jobs`, `central_news_body_revisions` | 계측 지점은 각각 메서드 transaction. 실패·재시도/다른 news writer는 미포함 | 성공 호출 전체 시간 |

기계 판독 가능한 목록과 동일 ID는 `central_server/diagnostic_writer_registry.py`에 있고 인증된 `/api/v1/diagnostics/writers`가 제공합니다. 현재 **12개 writer 계측 항목**을 등록했다. 이는 서버에 존재하는 전체 writer 수가 아니다.

## 응답당 저장 분리와 중복 분류

- `ka10080`/`ka10081` 응답 처리는 broker의 별도 persistence queue에서 response handler와 persistent query cache를 차례로 실행한다. persistent cache TTL이 0인 API는 캐시 writer가 생략된다. 두 경로는 같은 PostgreSQL DB여도 독립 connection/commit이다. `ka10080` 본문과 observation metadata/revision은 한 transaction에 묶여 있다.
- `ka10001`·`ka10100` handler의 최신 `central_documents`와 날짜 snapshot은 각각 별도 저장 호출과 COMMIT이다. persistent cache는 해당 API의 정책·TTL에 따라 추가로 한 번 저장된다. 따라서 실제 한 응답의 COMMIT 수는 캐시 사용 여부를 포함한 운영 표본으로 확인해야 한다.
- 실시간 `central_realtime_latest`, 분봉/초봉/current bars, finalization, TOP20 dataset은 목적과 수명, 재생성/복구 기준이 달라 값이 겹친다는 이유로 중복 저장으로 분류하지 않았다.
- NAS PostgreSQL과 PC SQLite 및 Journal SQLite 사이 복제, 계좌/주문 원장, 전체 FID/API registry, 모든 dataset/document collection은 아직 호출자→최종 reader 전수 대조가 안 됐다. `NECESSARY_DUPLICATION`, `CROSS_DEVICE_CACHE`, `REDUNDANT_STORAGE_CANDIDATE`, `TRANSACTION_FRAGMENTATION` 최종 수량은 **미확정**이다.

## 진단 추가 내용과 한계

`/api/v1/diagnostics/market-bar-saves`의 `writer_transactions`는 `calls`, `transactions`, `commits`, `rows_attempted` 및 전체 elapsed 분포(min/median/p90/p95/p99/max)를 낸다. `connect_ms`, `execute_ms`, `commit_ms`는 실제 기록한 단계만 percentile로 표시하고 그 표본 수도 `commit_latency_samples`로 별도 제공한다. query-cache, daily/minute bars, dataset snapshot은 주요 단계의 commit 분포를 보탰다. `/api/v1/diagnostics/writers`는 각 등록 항목의 table·source·durability·지원 일시정지를 반환하고 계측 누락 영역을 명시한다.

수집은 기존처럼 기본 OFF이며 짧은 TTL capture를 켠 기간만 bounded memory에 쌓인다. 성공 표본만 기록하고 실제 영향 행수, payload bytes, 예외/재시도 카운트, 모든 PostgreSQL writer의 측정·COMMIT 단계, API별 고유 WAL bytes는 아직 제공하지 않는다. WAL delta는 측정 구간 전체 합계라 개별 writer에 귀속하지 않는다. 분봉 저장의 일부 느린 COMMIT은 동일 backend PID의 `WALWrite`·`WalSync` wait와 연결했지만, 이 역시 writer별 WAL 발생량이나 장치 I/O 소유량을 뜻하지 않는다([운영 측정](NAS_RUNTIME_DIAGNOSTICS.md)). SQL별 WAL은 `pg_stat_statements` 설치·구성을 확인하지 않았고 추가 의존성으로 만들지 않았다.

## 분류 및 후속

현재 증거만으로 `REDUNDANT_STORAGE_CANDIDATE`나 `TRANSACTION_MERGE_CANDIDATE`를 확정해 삭제·통합하지 않았다. 다음 감사 범위는 실제 호출부로부터 REST/API ID 전체, WebSocket FID 전체, TOP20, 계좌·주문·일지, PC SQLite, 모든 table reader와 commit 경계를 추적하는 것이다. DB transaction·실시간 flush 경계 변경은 별도 설계·회귀 검증 단계로 남긴다.

### transaction·실시간 저장 변경 시 필수 검증

이 항목은 **저장 경계를 실제로 바꿀 때의 통과 조건**이다. 2026-09-26 로컬 분봉 원천 우선순위 수정은 이 경계에 해당한다. 닫힌 `ka10080` 봉의 OHLCV 및 앱 계산 거래대금을 최종값으로 채택하고, 뒤늦은 0B 분봉 쓰기와 종료 처리는 그 봉의 canonical·metadata·revision을 바꾸지 않는다. PostgreSQL 동일 종목·시장·날짜 쓰기는 transaction advisory lock으로 직렬화한다. 0B 진행봉은 임시값이며 `ka10080` 진행봉 뒤 첫 0B flush는 누적 합산 대신 실시간 값으로 교체한다. 독립 PostgreSQL 동시성·실패 주입과 NAS 전후 검증은 남아 있으므로 운영 배포 완료로 해석하지 않는다.

1. 변경 대상의 입력 이벤트 ID, 저장 호출·transaction·commit 경계, 재시도 주체, 최종 reader를 먼저 적는다. 기존 자료의 건수·식별자·값을 검증용 기준선으로 보존한다.
2. 같은 0B/REST 응답의 중복 도착, 서로 다른 종목의 동시 도착, 순서가 뒤바뀐 도착을 재현한다. 재시도 후에도 원천·latest·분봉/초봉·metadata·observation revision·finalization/operation 기록의 중복과 누락이 없어야 한다. 처리 순서가 결과에 영향을 주는 필드는 최신값 선택 규칙을 별도로 확인한다.
3. SQL 실행 중 오류, commit 직전 연결 끊김, commit 성공 직후 응답 손실, flush 중 프로세스 종료·재시작을 주입한다. 원자적으로 묶어야 하는 값은 함께 rollback/commit되고, 불확실한 commit 결과의 재시도는 operation ID 또는 기존 멱등성 경계로 한 번만 반영되어야 한다. 재개 가능한 큐는 미완료 작업을 되살려야 한다.
4. 실시간 0B와 REST 보완·TOP20·뉴스 등 동시 작업을 겹쳐 장중 부하를 재현한다. 입력 대비 저장·조회 건수와 식별자·값을 비교하고, 허용된 드롭이나 수신 공백은 `realtime_gap` 등 명시된 기록과 대조한다. 완료되지 않은 분봉/일봉이 확정 자료로 읽히지 않는지도 검사한다.
5. 독립 테스트 DB에서 실패 주입과 회귀 검사를 통과한 뒤 NAS에 적용한다. 적용 전후의 원천/파생 행수와 핵심 식별자·값, 재처리 가능 범위를 대조한다. 동일 조건의 latency 분포와 WAL/I/O도 비교하되 성능 개선만으로 데이터 보존 검증을 대체하지 않는다.

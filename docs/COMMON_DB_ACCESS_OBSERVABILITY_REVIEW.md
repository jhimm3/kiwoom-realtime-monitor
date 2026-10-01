# PostgreSQL 공통 접근·관측 경계 설계 검토

2026-09-28 document writer batch 완료: `upsert_documents` whitelist에서 실제 caller가 있는 `news_watchlist`, `news_automation_settings`, `server_operational_settings`를 찾았다. 같은 공통 저장 경계를 쓰는 Forward Evaluation repository의 17개 추가 collection kind도 registry/mapping에 등록했다. 이 17개가 모두 현재 runtime caller라는 의미는 아니다. 기존 per-call native transaction·업무별 단일 호출 저장·추가 theme/news helper는 유지하고 관측 wrapper만 적용했다. v1의 recovery·decision gate·dispatch metrics assertion은 같은 kind READ까지 고르는 필터 문제였고 family+kind 조건으로 수정했다. v2 전용 PostgreSQL 4 tests가 통과(38.363초): 20-kind 독립 write/read와 mock automation recovery lineage·approval 경계·dispatch projection을 검증했다. 로컬 단위검사 68건 중 28 pass/40 environment skip. v2 ZIP SHA-256 `3631B2EA634B1A3FB9F2A14D65F6F54AC771B87CE5DF2E93EFC33EFCD7A335BF` NAS host hash 일치.

2026-09-28 startup schema migration 관측 pilot 완료: PostgreSQL `initialize()` 연결 factory를 `schema.migration/central_schema` common wrapper로 감쌌다. `CentralSchemaMigrationRunner`와 SQLite 구현은 수정하지 않아 기존 savepoint 및 단일 native connection-context transaction을 유지한다. 로컬 공통 DB·migration 회귀 99건 통과(40 environment skip), NAS 전용 PostgreSQL 3 tests 통과(1.419초): initialize commit, synthetic DDL 실패 때 savepoint/native rollback·ledger 미기록, 기존 native context 종료를 확인했다. ZIP SHA-256 `B672F6322FBF6CB2D108716453F5939F906CE18CEEA4DB1C8D2F1FA56CB27BBB`의 NAS host hash 일치.

2026-09-28 추가 active READ pilot 완료: 리서치 fixed-watermark export page와 API diagnostics size/breakdown reader가 NAS 전용 PostgreSQL 2 tests에서 통과했다(0.863초). 추가 호출 경로 감사에서 `ForwardEvaluationRepository._save_immutable → _find → PostgresQueryStore.load_document`가 활성임을 확인해 `load_document`를 `read.document_collection/document:<collection>:single`로 이관했다. SQL·decode·native transaction을 유지했고 Forward Evaluation 로컬 회귀 23건과 NAS hit/missing reader 검사(0.328초)가 통과했다. v1 ZIP의 `tests/__init__.py` 누락은 v2에서 보정했으며 ZIP SHA-256 `55362A5432EDD7E236157AC024C3D099381912503DA3D6B892F4AEF322D464DA`의 NAS host hash가 일치한다. 과거 “load_document 운영 callsite 미확인” 기록은 이 호출 경로로 정정했다.

2026-09-28 execution READ 완료: `PostgresQueryStore`의 intent ID·active intent·broker order ID·intent/account event·mock automation control 6개 조회에 각각 `read.execution` 또는 `read.mock_automation` context를 적용했다. `ExecutionRepository`의 주문 복구/계좌 event paging과 `ForwardEvaluationRepository`의 control state reader를 따라 확인했다. SQL, scope filter, 결과 decoding, per-call driver context는 유지했다. NAS 전용 PostgreSQL v1에서 원장·control CAS 2건은 통과했고 stop recovery 기존 metric assertion은 동일 `writer_kind`를 가진 document reader도 writer로 선택해 실패했다. `writer_family=document.collection`으로 고친 v2에서 3건이 통과했다(6.324초). 당시 남은 active raw reader 후보는 authenticated research export page와 diagnostics resources의 size/breakdown 조회였으며, 후속 묶음에서 둘 다 이관·전용 DB 검증을 마쳤다.

2026-09-28 credential/account READ 완료: 운영 caller가 확인된 `find_credential_activation`, `list_credential_profiles`, `load_credential_activations`, `load_account_settings`, `load_market_profile_settings`, `load_account_bindings`, `resolve_account_scope` 7개를 `read.credential`·`read.account`의 독립 kind로 계측했다. 기존 SQL과 per-call connection/native transaction은 유지했다. account identity/binding, CAS settings, credential profile lifecycle, vault commit 후 activation finalize를 포함한 전용 PostgreSQL 검사 4건 묶음이 통과했다(15.970초). 주문·execution reader는 이 묶음에 포함하지 않았다.

2026-09-28 후속 pilot: `PostgresQueryStore.save_external_bars`는 Yahoo 지연 collector의 계약별 5m·1d 자료 저장 호출 각각에서 기존 native transaction을 유지하며 `external_market:bars` 공통 계측을 추가했다. 전용 PostgreSQL reader/replay·독립 commit 검사가 통과했다. API/service read 호출은 현재 코드에서 확인되지 않으며 이번 단계는 그 소비 구조를 변경하지 않았다.

2026-09-28 정정: `save_mock_automation_control`은 이후 `mock_automation.control` 공통 transaction wrapper로 이관됐다. 계좌 advisory lock·row lock·revision CAS 경계를 유지한 전용 PostgreSQL `test_mock_automation_control_cas_preserves_native_transactions`가 통과했다. 아래 stop pilot 당시의 “이 pilot 범위에 포함하지 않았다”는 역사적 범위 설명이며 현재 미이관 writer 목록으로 해석하지 않는다.

2026-09-28 후속 로컬 pilot: `MarketDataIngestor`가 호출하는 PostgreSQL `replace_minute_bars` / `replace_daily_bars`의 기존 명시 transaction과 domain-specific capture 경로를 유지하면서 연결·cursor에 공통 wrapper를 적용했다. `query_minute`·`query_daily`와 market-bar phase metrics에 동일 `call_id`를 기록한다. 로컬 회귀는 통과했고 전용 PostgreSQL의 replay·revision/advisory-lock 동시성·rollback·metrics 검증은 대기 중이다.

2026-09-27 · 제품 2.1.0 · 로컬 `main`, HEAD `047a528` 및 기존 미커밋 변경 기준.

설계 시점 상태: **현재 코드 조사와 설계 검토 완료. 당시 공통 계층 구현·writer 이관·NAS 배포는 하지 않았다.**
후속 2026-09-27 상태: `save_query`, `news.job_finish` 두 writer의 로컬 관측 pilot과 인증 API가 구현됐다. 아래 0단계 기록은 설계 시점 사실이다. 두 writer의 전용 PostgreSQL 계약 검사, 읽기 전용 SQL 및 cache SQL 등가 경계의 오버헤드 표본과 COMMIT별 backend wait 표본을 확보했다. 실제 도메인 writer 오버헤드 가드와 NAS 운영 적용은 완료되지 않았다. 현재 상태는 [CURRENT_STATUS](CURRENT_STATUS.md)와 [OPEN_ITEMS](OPEN_ITEMS.md)를 따른다.

후속 로컬 검증: 가짜 연결의 SQL/COMMIT/오류·연결 실패·무등록·세션 교체·관측 hook 실패·capture OFF·별도 connection 동시 호출 검사와 중앙 DB/서버/진단 회귀 합계 **134건 통과(종료 코드 0)**. 새 PostgreSQL 통합검사는 전용 URL 미설정으로 **skip 1건**이다. 순수 메모리 가짜 driver 1,000회씩의 사전 p95는 raw 0.0077ms, observed capture OFF 0.0213ms, capture ON 0.3075ms였다. 이는 Python wrapper 비용 표본이며 실제 PostgreSQL 연결·COMMIT·NAS 처리량의 전후 측정이 아니다. 이 수치로 성능 가드를 통과했다고 판정하지 않는다.
후속 환경 확인: 로컬 프로젝트 `.venv`의 `psycopg[binary]` 3.3.6 import 및 `pip check` 통과. pilot 단위검사 9건 통과. NAS 내부에서 읽기 전용 `psql`로 기존 전용 DB명·cache 테이블·PostgreSQL 17.11을 확인했다. PC용 전용 DB URL은 미설정이고 NAS compose에는 PostgreSQL의 PC 접속 포트가 공개되지 않으며 SSH 포트 전달도 금지한다. 현재 소스·검사를 임시 ZIP으로 서버 컨테이너 `/tmp`에 복사해 전용 DB 통합검사 3건을 실행했고 모두 통과했다(8.125초). 짝 비교를 추가한 재검사 3건도 통과(12.297초): 같은 `call_id`의 기존/신규 COMMIT 2,121/2,121.301ms 및 2,012/2,012.055ms. 이 결과는 타이머 일치를 보여주지만 지연 원인이나 wrapper 오버헤드를 밝히지 않는다. 첫 실행 후 테스트 key 잔여 행 0건; NAS 소스·이미지 변경 없음. 실제 PostgreSQL OFF/ON 오버헤드는 아직 남았다. 아래의 "psycopg 없음"과 "통합 미실행"은 설계 시점 기록이다.
후속 읽기 전용 실측: 전용 PostgreSQL에서 read-only 재사용 connection의 `SELECT 1` 1,000회×3구간을 raw/OFF/ON 교차 순서로 실행했다. 각 모드 3,000회의 p50/p95는 raw 0.094/0.120ms, OFF 0.107/0.138ms, ON 0.106/0.127ms였다. 1,000회 블록 전체 시간 중앙값은 108.748/118.879/115.037ms로 ON은 raw보다 5.78% 길었고, 블록별 변동이 커 처리량 5% 가드를 통과했다고 판단하지 않는다. ON 세 블록의 call/SQL/commit/drop 검사는 통과했다. CPU·연결 수·동시 부하와 실제 `save_query` write/COMMIT은 측정하지 않았다. 앞 문단의 "오버헤드는 아직 남았다"는 실제 writer 기준을 뜻한다.
후속 cache SQL 등가 실측: 전용 PostgreSQL에서 호출마다 새 연결을 열어 기존 `save_query`의 UPSERT·만료 DELETE·명시 COMMIT·close와 같은 순서로 raw/OFF/ON 각 18회를 교차 측정했다. commit p50/p95(ms)는 raw 103.057/1,239.752, OFF 32.194/1,850.664, ON 84.577/1,418.553이었다. 세 모드에서 모두 긴 tail이 발생해 wrapper 비용과 COMMIT 대기를 이 표본으로 분리할 수 없다. ON close p50은 0.671ms(raw 0.104ms, OFF 0.101ms)로 capture 기록을 포함하는 close 경계의 비용 후보가 보인다. ON call/SQL/COMMIT 검증과 블록별 측정 키 삭제·잔여 0은 통과했다. 도메인 `save_query` 자체, CPU, NAS 동시 부하 및 backend wait/WAL/storage correlation은 미측정이므로 성능 가드는 통과 미판정이다.
후속 COMMIT별 wait 진단: 전용 DB의 같은 SQL 경로 12개 transaction을 `DBSlowHook`과 별도 읽기 연결로 조사했다. 100ms 이후 25ms 간격 표본 총 98개 중 `IO:WalSync` 85개, `LWLock:WALWrite` 13개였다. 1,049.522ms/1,183.287ms COMMIT의 동일 backend에서 `WalSync`가 각각 36/41개로 거의 종료 시각까지 지속했고, 460.666ms COMMIT에서는 `WALWrite` 11개 뒤 `WalSync` 1개가 관측됐다. blocking PID는 없었고 probe 오류·미완료·잘림도 없었다. 운영 분봉 저장의 동일 backend WAL wait는 이미 [기존 NAS 측정](NAS_RUNTIME_DIAGNOSTICS.md)에서 확인됐다. 이번 새 사실은 작은 cache transaction에서도 같은 대기가 발생한다는 것이다. 장치·동시 writer·WAL 양의 인과 분석은 아니다. `scripts/probe_postgres_access_commit_waits.py`는 진단 전용 중복 sampler이며 검증된 공통 hook 구현으로 흡수할 때 제거한다. 운영 설정·durability는 변경하지 않았다.
운영 NAS의 현재 프로세스·드라이버 버전·설정은 이번에 조회하지 않았다. 아래 위치와 개수는 로컬 소스 증거다.

## 1. 결정

제안의 핵심인 **관측·접근 경계 공통화, writer 독립 트랜잭션·동시성 보존**을 채택한다.
새 DB 서버, 단일 쓰기 worker, pool, async driver, ORM을 도입할 이유는 현재 확인되지 않았다.
대부분의 서버 접근은 이미 `PostgresQueryStore._connect()`로 모인다. 부족한 것은 연결 지점 자체보다
명시/암묵 트랜잭션 종료를 일관되게 측정하고 직접 접속 스크립트까지 범위를 관리하는 계약이다.

1차 대상은 `PostgresQueryStore.save_query` **writer 하나**다. 순위/TOP20은 첫 단순 writer에서 제외한다.
`save_dataset_snapshots`는 종류별 기존 durability 설정, 역사 통계 캐시 무효화, advisory lock,
선택적 revision 저장, 기존 대기 probe를 함께 가진다. 코드가 짧은 호출자라고 위험이 낮지 않다.

이 검토는 COMMIT tail의 원인 확정이나 성능 해결 보고가 아니다. 기존 batch 저장 최적화,
과거뉴스 SQLite archive, accepted_sequence 소비 계약 수정도 이번 공통 계층 도입과 분리한다.

## 2. 조사 범위와 재현 자료

- [기계 판독 원장](postgres_access_inventory.json): 파일·함수·행·API 호출 지점, SQL 토큰/테이블 후보,
  로컬 helper 연결, 현재 context 구문, 예상 family/kind, 소스 SHA-256.
- 생성기: `scripts/audit_postgres_access.py`. `src`, `scripts`, `tests`의 Python을 AST로 읽기만 한다.
  앱 import, DB 연결, 운영 프로세스 제어, 환경변수/SQL parameter 출력은 하지 않는다.
- 재생성·가드: 프로젝트 Python으로 `scripts/audit_postgres_access.py --check --output docs/postgres_access_inventory.json`.
  검토한 직접 연결 예외는 `docs/postgres_access_direct_connection_approvals.json`의 파일·함수·호출별 원장과 대조한다.
- 현재 정적 원장 수치(2026-09-28 재생성): `PostgresQueryStore` 메서드 **102개**, 리터럴
  driver 연결 지점 **29개**, 전체 backend 후보 API 호출 지점 **6,679개**, AST parse 오류 **0개**.
  이 후보 총계는 실제 운영 writer·transaction·commit 수를 나타내지 않는다. 원장의 `source_sha256`가
  생성 대상 코드와 일치하는지 함께 확인한다.
- 102에는 읽기·초기화·위임·연결 factory가 포함된다. 29개 연결 후보에는 진단·관리·시험용 직접 접속이
  포함된다. 6,679개 API 후보에는 SQLite 및 같은 이름의 비DB 함수도 포함되므로, 어느 수도 운영
  writer/transaction/COMMIT 수가 아니다.
- helper의 공통 SQLite 분기와 동적 SQL은 후보로 남긴다. imported alias·reflection·외부 라이브러리는
  완전 해석하지 않는다. 정적 결과만으로 전수 런타임 coverage 완료를 선언하지 않는다.

## 3. 현재 연결·종료 경계

아래 함수는 별도 표기가 없으면 `src/kiwoom_monitor/central_server/database.py`에 있다.
예상 family/kind는 설계 라벨이며 이미 등록·측정된다는 뜻이 아니다.

| 파일/함수 | 실제 작업·연결 소유자 | 현재 transaction boundary | 예상 family / kind |
| --- | --- | --- | --- |
| `PostgresQueryStore._connect` :4256 | 동기 psycopg 3 새 연결 반환; store는 URL/설정만 보유 | pool 없음, 연결 singleton 없음; 호출자가 종료 | 연결 factory, writer 아님 |
| `save_query` :2578 | JSON 직렬화 후 호출별 연결; cache UPSERT+만료 DELETE | 명시 commit 1회; `finally: close`; 오류 때 명시 rollback 없음 | `rest.query_cache` / `query_cache` |
| `load_query` :2566 | cache SELECT; 호출별 연결 | driver connection context가 종료 처리; SELECT도 implicit transaction 가능 | `read.query_cache` / `query_cache` |
| `_replace_bars` :2864 | 분/일봉 canonical·metadata·revision; 호출별 연결 | 명시 commit, 예외 rollback, finally close; capture 설정 실패 복구 시 추가 rollback 가능 | `rest.market_bars.minute/daily` / `query_minute/query_daily` |
| `save_dataset_snapshots` :3115 | snapshot·통계 무효화·metadata·revision | 호출별 명시 commit/rollback/close; 종류 혼합도 한 transaction | `dataset.snapshot` / `dataset:<kind>` 또는 `dataset:mixed` |
| `load_top20_statistics` :3255 | SELECT 후 누락 과거일 계산·캐시 UPSERT | 같은 연결/context에서 읽기·쓰기; advisory lock도 있음 | `dataset.statistics_cache` / `dataset:top20_statistics_day` |
| `create_observation_export` :3502 | 고정 연구 export를 만들기 위해 immutable revision 목록을 선택하고 manifest와 ordered membership 저장 | 기존 connection context 하나에서 SELECT→manifest INSERT→member executemany; driver native transaction 유지 | 로컬 pilot `research.observation_export / research_observation_export_create`; 전용 PostgreSQL 검증 대기 |
| `load_observation_export_page` :3542 | 고정 watermark manifest와 member/revision page를 읽음 | 별도 reader connection context; export manifest transaction과 분리 | research export reader |
| `save_realtime_snapshots`, `save_minute_bars`, `finalize_minute_bars`, `save_second_trade_bars` | 0B flush latest/봉/멱등성/이력 | 각 메서드의 별도 connection context; 하나의 flush를 transaction 하나로 합치지 않음 | 기존 `realtime.*` / 기존 metric_kind |
| `upsert_documents` :3464 | collection UPSERT와 theme/news 선택 helper | 같은 connection/cursor, context 종료 commit; helper가 새 transaction을 만들지 않음 | `document.collection` / `document:<collection>` |
| `replace_documents` :3486 | collection 삭제+삽입+선택 theme history | 하나의 context; 빈 목록도 삭제 의미 유지 | `document.collection` / `document:<collection>` |
| `claim_news_jobs` :3545 | lease 복구, SKIP LOCKED claim, RUNNING 전이 | 한 context; 선택 결과 0건이어도 앞선 SQL/갱신이 있을 수 있음 | `news.job_claim` / `news_job_claim` |
| `claim_external_historical_news_job` :3586 | PC 소유권 claim+별도 대기 probe | 연결 생성 후 `with connection`; 기존 driver 종료 | `news.external_claim` / `news_external_claim` |
| `finish_news_job`, `retry_news_job`, `save_news_body_revision`, `save_news_ai_results`, `save_news_event_revision` | job/body/assessment/event 의미는 기존 helper 소유 | 개별 connection context 유지; RULE 여러 저장 호출을 합치지 않음 | 기존 `news.*` 및 단계별 신규 등록 |
| `save_external_bars`, `save_five_minute_bars`, `append_vi_events`, `record_hot_cohort_revision` 등 | 입력별 기존 canonical/이력 | 호출별 context; 기계 원장에 각 메서드·helper 기재 | 각각의 도메인 family, 임의 `misc` 영구 등록 금지 |
| `finalize_credential_activation` | vault 파일 commit 뒤 account binding revision·activation receipt·profile·settings를 기록; `CredentialRuntime` apply/recovery 및 legacy vault import가 호출 | per-call connection context, global credential-activation advisory lock, 단일 native transaction | `credential.activation` / `credential_activation_finalize`; 전용 PostgreSQL replay·rollback pilot 완료 |
| credential profile `create/register/rename/archive` | 요청 digest receipt·draft profile 생성, startup idempotent registration, display label 및 archive lifecycle | 각 per-call context, global credential-activation advisory lock; create receipt와 profile은 원자 저장 | `credential.profile`의 4개 kind; 전용 PostgreSQL replay·rollback pilot 완료 |
| 나머지 계좌·execution·automation writer | identity/ownership/advisory lock/원장 보호 | 기존 context와 helper 경계 | 각 transaction 의미를 조사한 뒤 별도 family/kind로 점진 이관 |
| `initialize` :2560 → `schema_migrations.CentralSchemaMigrationRunner.apply` | caller 소유 connection/cursor를 runner가 빌림 | outer context+runner savepoint; schema 변경은 이 작업 범위 밖 | `schema.migration` / `central_schema` |
| `load_latest_news_body`, `save_dataset_snapshot`, `replace_minute_bars/daily_bars` | 다른 store 메서드 위임 | 위임 호출에 새 transaction/통계 중복 생성 금지 | 실제 소유자 transaction만 기록 |

### 직접 driver 연결 29곳 (2026-09-28 재생성)

원장은 `scripts/audit_postgres_access.py`를 현재 저장소에 실행해 재생성했다.
102 store methods, 29 literal `psycopg.connect` callsites, 6,679 backend 혼합 후보 API
calls, parse errors 0이다. 호출 지점은 transaction 수나 활성 운영 호출 수가 아니다.
파일별 함수·행·해시는 `docs/postgres_access_inventory.json`에 둔다.

| 파일/함수 | 지점 수 | 목적·소유권·boundary | 예상 관측 분류 |
| --- | ---: | --- | --- |
| `PostgresQueryStore._connect` | 1 | 기존 per-call connection factory; 호출 caller가 관측 wrapper에 넣어 소유 | `infrastructure.connection`; 직접 writer 호출 아님 |
| `database._sample_postgres_backend_waits`, `_sample_postgres_commit_waits` | 2 | 업무 transaction을 관찰하는 별도 diagnostic thread; autocommit wait SELECT | `diagnostic.probe`; 업무 writer 합계 제외 |
| `scripts/benchmark_minute_revision_batch_postgres.py` (`_check_database`, `_seed`, `_current_batch`, `_legacy_rowwise`, `main`) | 5 | 전용 진단 DB benchmark, 행별/batch write 및 cleanup | `test.benchmark` |
| `scripts/benchmark_postgres_access_pilot.py` (`_check_database`, `_run_block.connect`) | 2 | 전용 진단 DB read-only SELECT overhead 비교; preflight도 read-only | `test.benchmark` |
| `scripts/benchmark_postgres_access_cache_writer.py` (`_connect`, `_preflight`) | 2 | 전용 진단 DB의 SQL equivalent cache benchmark와 임시행 정리 | `test.benchmark` |
| `scripts/probe_postgres_access_commit_waits.py:_sample_waits` | 1 | 전용 DB cache transaction PID를 별도 autocommit 연결로 bounded sample | `test.diagnostic_probe` |
| `scripts/cancel_stale_news_claim.py:main` | 1 | PID·query prefix·최소 실행시간 확인 후 `pg_cancel_backend` 요청; autocommit | `maintenance.control`; 수동 실행 전용, workload writer 아님 |
| `scripts/create_news_claim_order_index.py:main` | 1 | session advisory lock과 `CREATE INDEX CONCURRENTLY`; autocommit DDL | `maintenance.schema`; schema DDL 경계로 일반 transaction wrapper 대상 아님 |
| `scripts/diagnose_active_news_queries.py`, `diagnose_long_news_query.py`, `diagnose_news_claim_query.py`, `diagnose_prepared_news_db_waits.py` | 4 | 활동/실행계획/대기 정보 조회, autocommit | `diagnostic.query`; read-only 진단 |
| `scripts/export_historical_news_seed.py:export_seed` | 1 | 운영 PostgreSQL을 `read_only` + `REPEATABLE_READ`로 읽어 SQLite seed 생성 | `maintenance.read_export`; 장시간 read-only transaction |
| `scripts/import_prepared_historical_news_to_nas.py:main` | 1 | 장수명 연결; revision 조회/commit과 completion batch/savepoint. 완료 batch 경계는 공통 wrapper로 검증됨 | `maintenance.prepared_news`; archive 대체 계획의 기존 importer |
| `scripts/measure_news_claim_order_index.py:main` | 1 | 실행계획·읽기 ANALYZE·index 상태 조회, autocommit | `diagnostic.query` |
| `scripts/nas_workload_diagnostic.py:_measure` | 1 | DB activity/statistics 조회, CLI 소유 autocommit connection | `diagnostic.measure` |
| `scripts/purge_unprocessed_legacy_news.py:main` | 1 | dry-run이 기본인 legacy cleanup; `--execute`에서 manifest 및 bounded delete transaction | `maintenance.cleanup`; destructive opt-in, 현재 실행 증거 없음 |
| `scripts/run_postgres_access_integration.py:main` | 1 | 운영 URL은 read-only preflight로 DB 존재 확인, 이후 test DB URL로 통합검사 | `test.preflight` |
| `scripts/verify_prepared_news_import_postgres.py:main` | 1 | 운영 URL은 read-only DB명 확인, target은 전용 진단 DB | `test.preflight` |
| `tests/integration/test_historical_news_seed_postgres.py` (`setUp`, test) | 2 | 전용 진단 DB에서 임시 schema fixture와 repeatable-read reader | `test.integration` |
| `tests/integration/test_storage_boundary_postgres.py:test_pg17_io_views_are_readable_and_optional_failure_is_isolated` | 1 | 전용 진단 DB autocommit read/failure isolation | `test.integration` |

테스트의 `store._connect()` 사용은 추가 드라이버 factory가 아니라 기존 factory 호출이다.
위 도구의 존재를 현재 실행 증거나 재실행 권한으로 해석하지 않는다.

## 4. 호출 경로·동시성

```text
REST 결과 → CentralRestBroker._persist_queue / _run_persistence (기존 queue)
           ├─ to_thread(MarketDataIngestor.ingest) → 각 canonical/dataset/document transaction
           └─ TTL 대상만 to_thread(save_query) → 별도 cache transaction

WebSocket → CentralRealtimeCollector._flush_lock (기존 flush 보호)
           → to_thread(latest / minute / closure / second ...) → 각각 독립 transaction

NewsJobRunner → to_thread(claim) → fetch/분석 → BODY/RULE/event/finish 각 기존 저장 경계

API / 계좌 / 외부시장 → 기존 store → 호출별 독립 connection
```

공통 계층은 이 queue, flush lock, advisory lock, thread 실행 순서를 바꾸지 않는다.
기존 REST 전용 queue는 전체 DB 단일 worker가 아니므로 제거하거나 다른 writer를 합류시키지 않는다.
공통 metrics 잠금은 메모리 append/counter만 보호하고 DB I/O 동안 보유하지 않는다.

현재 `CentralRestBroker._run_persistence`는 응답 저장기를 기다린 뒤 TTL 대상의
`save_query`를 기다리고 나서 `job.future`를 완료한다. 따라서 해당 요청의 결과 전달 시간에
cache COMMIT 대기가 포함된다. `AutonomousTop20Service`의 일봉 보완 등은
`recording_succeeded`와 저장 행을 확인하므로 완료 시점 변경은 공통 관측 계층 이관이 아닌
별도 데이터 계약 검토가 필요하다. 기존 흐름의 두 저장은 서로 다른 transaction이다.

다음 이관 후보의 종료 경계도 분리한다. `save_query` 외의 단순 store writer 다수는
`with self._connect() as connection`으로 driver가 암묵 COMMIT/ROLLBACK과 close를 소유한다.
나머지 명시 종료 경로인 봉·dataset 저장은 기존 wait probe, revision, savepoint 또는
durability 정책을 포함한다. 현재 명시 종료 전용 proxy를 이름만 바꿔 적용하지 않고,
native context 종료 계측의 실제 Psycopg 동작을 검증한 뒤 다음 writer를 선택한다.

로컬 설치본 Psycopg 3.3.6 `Connection.__exit__` 확인: 정상 종료에는 `commit()` 뒤
pool 소유가 아닌 연결의 `close()`를 호출하고, 본문 예외에는 `rollback()` 실패를
경고로 남긴 뒤 기존 예외를 유지하며 `close()`한다. 정상 경로의 `commit()`이 실패하면
`close()`까지 도달하지 않는다. 따라서 proxy가 raw `__exit__`만 호출하면 종료 단계
계측이 빠지고, `__exit__` 전체 시간은 COMMIT과 close를 분리하지 못한다.
다음 writer 이관은 실제 Psycopg 통합검사에서 성공·본문 예외·COMMIT 실패의
결과/connection 수명/계측이 기존과 같음을 입증한 뒤 진행한다. 이 검사는 WAL 병목
재측정과 목적이 다르며, 검증 전에는 context writer를 이관하지 않는다.
현재 로컬 adapter에는 native `__exit__`를 proxy에서 실행하는 경계를 추가했고, 설치된
Psycopg의 `__exit__`를 가짜 연결에 적용한 성공·본문 예외·COMMIT 실패 단위검사가 통과했다.
사용자 제공 NAS 전용 PostgreSQL 실행 출력에서 성공·본문 예외와 지연 제약 COMMIT 실패의
native context 계약 검사 2건이 통과했다(0.221초). 이 검사 시점의 운영 writer 이관은 없다.
후속으로 `news.job_finish`의 단일 UPDATE를 같은 connection context에 보존한 채 공통
계측에 연결했다. 기존 성공 지표와 `db_call_id`를 짝지으며 registry의 이관 표시도 갱신했다.
로컬 성공·실행 오류 계약과 인접 회귀가 통과했다. 사용자 제공 NAS 컨테이너 출력에서
전용 PostgreSQL의 실제 `finish_news_job` 행 상태·공통 COMMIT/SQL 횟수·기존 지표와
call ID 연결·측정 행 정리를 확인하는 통합검사 1건도 통과했다(1.144초).
설치된 Psycopg의 `Connection.__exit__`를 사용한 추가 단위검사에서 ROLLBACK 자체가
실패해도 본문 예외가 유지되고 close·`unknown` 관측이 남는 계약을 확인했다.
후속으로 `news.job_claim`의 일반 뉴스 worker와 `news.job_retry`를 계측 이관했고, retry는 전용 PostgreSQL 검증도 통과했다. 일반 claim의 만료 lease 복구·SKIP LOCKED·상태 갱신과 retry의 attempts 조회·상태 계산·갱신은 각각 기존 단일 transaction 안에 유지한다. 이어 PC 과거뉴스의 `news.external_claim` 경로를 로컬 이관했다. 이 경로의 `SKIP LOCKED`와 기존 독립 wait sampler는 유지하며 공통 wrapper에는 별도 hook을 지정하지 않아 중복 sampler를 만들지 않는다. 전용 PostgreSQL 외부 claim 병렬·reader·계측/cleanup 검사 1건이 통과했다. 실제 writer 오버헤드 가드와 운영 NAS 적용은 미완료다.
Phase 3은 `upsert_documents` 전체를 감싸지 않고, history helper가 없는 좁은 kind부터 진행한다. `document:news_sync`에 이어 `document:krx_trading_day_observations`, `document:external_market_roll_state`, 뉴스 collector의 KRX catalog fallback인 `document:stock_catalog`, SOR/조회 분봉 거래대금 비교 문서 `document:minute_trade_value_comparisons`, `ka10100` 최신 NXT 문서 `document:stock_nxt_eligibility`, `ka10001` 최신 기본정보 문서 `document:stock_fundamentals`를 좁은 kind로 추가했다. NXT·기본정보 문서 다음의 날짜별 dataset snapshot은 각각 별도 transaction으로 유지한다. 해당 경로의 기존 `asyncio.to_thread`와 context-manager transaction은 유지하며 공통 계측 경계만 적용한다. `stock_catalog`의 별도 `replace_documents` writer와 `theme_metadata`/`news_article` 이력 helper는 이번 범위가 아니다. 거래대금 비교는 분봉 본 저장 뒤 발생하는 독립 best-effort transaction이며, 실패 격리와 API reader 요약 규칙을 유지한다. 테스트·운영 적용 여부는 CURRENT_STATUS와 OPEN_ITEMS를 따른다.
`news_article` 이후 별도 marker transaction이므로 기사 revision/작업 생성 helper를
건드리지 않으며, 나머지 collection은 기존 연결 경로를 유지한다. 로컬 단위검사는
통과했다. 사용자 제공 NAS 출력에서 전용 PostgreSQL의 동일 문서 replay·행값·COMMIT/SQL
횟수·기존 지표/call ID 연결·측정 행 정리 통합검사 1건도 통과했다(0.595초).

`news.body`의 본문 revision INSERT, 기사 revision 잠금, RULE job 생성은 기존 하나의 native connection transaction 안에서 공통 관측만 추가했다. BODY job 완료는 호출자의 별도 transaction으로 유지한다. 성공 시 기존 `news_body` 표본에 DB call ID를 연결한다. 로컬 공통 access·뉴스 회귀 46건이 통과했고, 사용자 제공 컨테이너 실행 결과로 전용 PostgreSQL reader·연결 RULE job·오류 rollback 검사 1건도 통과했다(0.329초, 정리 assertion 통과).
`news.ai_results`는 최신 `news_ai` 문서, 불변 AI revision, `news_request_usage`를 저장하는 기존 native connection transaction만 공통 관측에 연결했다. 기존 legacy writer 표본이 없는 경로라 공통 측정만 기록한다. 로컬 공통 access·중앙 AI 회귀 39건과 사용자 제공 전용 PostgreSQL reader·중복 revision rollback·계측 검사 1건이 통과했다(0.397초, cleanup assertion 포함).
`news.event`는 일반 RULE worker의 `save_news_event_revision` 메서드와 PC 과거뉴스 RULE 완료를 관측한다. 후자는 `complete_external_historical_news_job`의 `news.external_finish:RULE` 한 transaction 안에서 event helper를 호출하므로 assessment·event/membership·job 완료가 함께 commit/rollback된다. 일반 RULE worker는 assessment 저장 및 job 완료와 각각 독립 transaction을 유지한다. 일반 event writer의 동시 replay·membership rollback 검사는 1.219초에 통과했고, PC 완료 경로의 replay·강제 실패 rollback·metrics 검사는 4.890초에 통과했다.
`news.job_enqueue`는 `NewsService`의 AI 후보 등록에서만 호출되는 `enqueue_news_ai_jobs`의 PostgreSQL connection context를 감싼다. 최신 기사·본문 revision SELECT와 본문 revision 기반 `ON CONFLICT DO NOTHING` INSERT는 기존 한 transaction이고, 반환값은 실제 삽입 수다. 공통 `rows_attempted`는 후보 수이므로 반환값과 다를 수 있다. AI worker의 claim 및 분석 결과 저장은 별도 transaction으로 유지한다. 관련 로컬 회귀 71건과 전용 PostgreSQL 병렬 replay·본문 참조·후보 중간 실패 rollback 검사 1건(0.752초, cleanup assertion 포함)이 통과했다.
`news.source_page`는 두 수집기의 `save_news_source_page` 진입점을 관측한다. `query_set`과 `naver_stock_market`은 kind를 분리한다. 기사 revision 재사용·BODY job·대상 revision·source observation·run·cursor는 하나의 native transaction이며 빈 페이지도 run과 cursor를 기록한다. `rows_attempted`는 페이지 기사 후보 수라 run/cursor 행을 포함하지 않는다. 과거 시황 batch의 `_save_postgres_news_source_page` 호출은 상위 `save_historical_market_news_batch`의 `news.historical_market_batch` wrapper로 관측된다. PC 과거뉴스 importer의 caller-owned batch/savepoint helper 직접 호출은 별도 경로지만, 인증 API가 호출하는 `complete_external_historical_news_job`의 BODY/RULE 완료는 `news.external_finish`로 별도 관측한다. 전용 PostgreSQL replay·FLASH reader·중간 오류 rollback 검사 1건(0.642초)이 통과했다.
`news.request_budget`는 `claim_news_request`의 PostgreSQL native transaction만 감싼다. `query_set`/`watchlist` kind를 분리하며, EXCLUSIVE table lock 뒤 일일 총량·scope 횟수를 읽고 한도 미달일 때만 UPSERT한다. 거부된 요청도 native context에 따라 commit한다. `rows_attempted`는 미설정한다. 로컬 회귀 117건과 전용 PostgreSQL 동시 claim·scope/hard limit·계측·정리 검사 1건이 통과했다(1.436초).
`news.historical_market_batch`는 준비된 FLASH/WORLD 과거뉴스 importer의 `save_historical_market_news_batch`를 관측한다. advisory transaction lock 아래 중복 `run_id` 확인과 article/source progress 저장은 한 native transaction이다. replay도 기존대로 COMMIT되고 저장을 건너뛴다. kind는 원천별이며 `rows_attempted`는 입력 article 수다. 관련 로컬 회귀 108건과 전용 PostgreSQL 동시 replay·reader·실패 rollback 검사 1건이 통과했다(1.252초, cleanup assertion 포함). 최초 통합검사는 올바른 `ValueError` 대신 `KeyError`를 기대해 실패했고, 이를 수정한 v2에서 통과했다.
`document:top20_daily_entrants`는 `AutonomousTop20Service`의 실제 entrant persistence에서 호출되는 `upsert_documents`의 공통 관측 kind다. 이전 일일 projection 조회와 upsert는 원래 구조대로 분리되고, 저장 한 건은 기존 native transaction 하나다. kind registry에 추가했으며 TOP20·DB·계측 로컬 회귀 137건과 구문검사가 통과했다. 전용 PostgreSQL 동일값 replay·reader·계측 검사 1건도 통과했다(0.199초, cleanup assertion 포함).
`document:historical_highs`는 `AutonomousTop20Service._ensure_historical_high`가 계산한 하루별 historical high projection을 저장하는 `upsert_documents` kind다. service freshness check와 `/api/v1/content/historical_highs` reader는 기존 경로를 유지한다. registry에 추가하고 TOP20·DB·계측 회귀 137건, 구문검사, 전용 PostgreSQL replay·reader·계측 검사 1건이 통과했다(0.359초, cleanup assertion 포함).
`document:market_index_chart_coverage`는 `_backfill_market_indexes`에서 KOSPI/KOSDAQ dataset 저장을 마친 뒤 완료 marker를 기록하는 `upsert_documents` kind다. 다음 실행의 skip 판단이 읽으며, 두 market의 dataset save와 marker save는 기존 별도 transaction으로 유지한다. registry에 추가했고 TOP20·DB·계측 회귀 137건과 구문검사를 통과했다. 전용 PostgreSQL replay·실제 skip reader·계측 검사 1건도 통과했다(0.307초, cleanup assertion 포함).

`document:market_data_coverage_daily`는 `_backfill_daily`가 저장된 일봉을 확인한 다음 날짜별 coverage marker를 기록할 때 쓰는 `upsert_documents` kind다. 후속 backfill과 API가 읽는다. 일봉 저장과 marker는 기존 별도 transaction이며 marker writer만 관측 대상으로 추가했다. 관련 로컬 TOP20·DB·계측 테스트 99건 통과(1건 skip). 첫 ZIP과 v2는 차례로 `scripts`, `tests.integration` package marker 누락으로 테스트 시작 전 실패했다. v3는 package marker를 포함하고 새 소스를 ZIP 최상위에 배치했으며 로컬 ZIP import와 NAS SHA-256 일치, 전용 PostgreSQL replay·reader·계측 검사 1건(0.263초, cleanup assertion 포함)을 확인했다.

`document:market_data_coverage`는 `_backfill_minutes`의 완료 marker다. reader는 완전한 marker와 저장 분봉을 함께 확인해 재조회 skip을 결정한다. marker writer kind만 공통 계측에 추가했다. 관련 로컬 회귀 99건 통과(1건 skip), 전용 PostgreSQL reader/replay·실제 skip·계측 검사 1건이 통과했다(0.661초, cleanup assertion 포함).

`document:market_data_coverage_intraday`는 신규 편입 종목의 `_backfill_entry_minutes` 완료 marker다. 저장 이후 쓰며 다음 실행에서 이미 수집된 종목의 재조회를 건너뛴다. writer kind만 공통 계측에 추가했다. 관련 로컬 회귀 99건 통과(1건 skip); 전용 PostgreSQL replay·reader skip·계측 검사 1건이 통과했다(0.974초, cleanup assertion 포함).

`document:candidate_flow_capture`는 신규 편입 시 수급 API 응답을 검증한 뒤 저장하는 최초 capture marker다. 후속 호출은 이 문서를 읽어 API 재호출을 생략한다. marker kind만 공통 계측에 추가했다. 관련 로컬 회귀 99건 통과(1건 skip); 전용 PostgreSQL replay·reader·계측 검사 1건이 통과했다(0.331초, cleanup assertion 포함).

`document:candidate_flow_finalization`는 장후 투자자·프로그램 수급 API 응답을 모두 검증한 뒤 쓰는 날짜별 완료 marker다. 후속 backfill은 marker를 읽어 두 요청을 생략한다. writer kind만 관측 목록에 추가했다. 관련 TOP20 idempotency·DB 테스트 55건 및 전용 PostgreSQL replay·reader skip·계측 검사 1건이 통과했다(0.767초, cleanup assertion 포함).

`document:condition_search_status`는 `MarketEventService._condition_list`의 선택 결과와 runtime 상태 projection이다. `/api/v1/market/events?kind=cohort`가 읽는다. 기존 state-machine 처리 후 저장하는 kind만 공통 계측에 추가했다. 관련 market-event·DB 회귀 65건과 전용 PostgreSQL 실제 handler/replay·reader·계측 검사 1건이 통과했다(0.242초, cleanup assertion 포함).

`document:market_event_sessions`는 KRX session observed, regular close, full-day close projection이다. 세 단계가 동일 session key를 별도 native transactions에서 갱신한다. 현재 repo에 해당 collection을 읽는 domain call site는 보이지 않는다. collection kind만 common DB metrics에 추가했으며 세 writer boundaries와 replay를 확인하는 전용 PostgreSQL 테스트 1건이 통과했다(0.611초, cleanup assertion 포함).

`document:news_original_publication`은 `NewsJobRunner._run_body`가 원문 fetch에서 검증한 publication timestamp를 본문 revision 저장보다 먼저 기록하고 `_run_rule`이 `load_documents`로 읽는 projection이다. 기존 async-to-thread 호출, publication-marker transaction, 별도 body-revision transaction 순서를 보존하고 collection kind만 common DB metrics에 추가했다. 로컬 뉴스·DB 회귀 65건과 전용 PostgreSQL `_run_body` write/readback/replay 및 metric call ID 검사가 통과했다(0.317초, cleanup assertion 포함).

`document:external_market_collection_status`는 Yahoo 지연 시세 collector가 성공·실패 직후 provider 상태 projection을 기록한다. 현재 저장소에서 이를 읽는 domain call site는 찾지 못했다. `_save_status`의 기존 transaction만 공통 계측에 추가했으며 로컬 외부시장·계측·DB 회귀 100건과 전용 PostgreSQL 실패 status write/readback/replay·metric call ID 검사가 통과했다(2.201초, cleanup assertion 포함).

`document:news_assessment`는 `NewsJobRunner._run_rule`이 원문 발행시각을 결합해 저장하고 `/api/v1/news/history/body` 및 news feed/article query 경로가 읽는 RULE projection이다. 공통 계측 kind만 추가했으며 기존 async writer와 event revision save 경계를 유지했다. 로컬 뉴스 job·계측·DB 회귀 103건과 전용 PostgreSQL 저장/readback/replay·metric call ID 검사가 통과했다(2.749초, cleanup assertion 포함).

`document:execution_mock_automation_runner_current`는 NAS app의 PostgreSQL store를 전달받은 mock automation runner가 account별 `current` checkpoint를 저장하는 collection이다. 재시작 시 version/spec/run/candidate hash가 맞는 checkpoint만 복원하고, 없을 때만 history bootstrap 후 새 checkpoint를 쓴다. collection kind만 공통 계측에 추가했다. 관련 로컬 97건과 전용 PostgreSQL 최초 저장·checkpoint 갱신·재시작 복원·restore 뒤 추가 write 없음·metric call ID 검사가 통과했다(0.658초, cleanup assertion 포함).

`document:credential_vault_state`는 `CredentialStore`가 암호화 vault 파일의 초기화 및 revision fence로 사용한다. 최초 0 fence 저장, 암호화 파일의 atomic replace/fsync, revision fence 갱신 순서를 바꾸지 않고 해당 collection kind만 공통 계측에 추가했다. 저장소 회귀 114건과 ZIP import/test load를 확인했다. 전용 PostgreSQL 통합 검사에서 파일 저장 후 fence 실패의 vault 재시작 복구, 최초 파일 저장 실패의 `RECOVERY_REQUIRED`, metric call ID 및 임시행 정리가 통과했다(1.416초).

`execution_mock_automation_risk_snapshots`와 `execution_mock_automation_current_risk`는 `ForwardEvaluationRepository.save_mock_automation_risk_snapshot`의 서로 다른 저장 경계다. 먼저 immutable snapshot을 저장·재조회하고, 새 revision이면 별도 transaction으로 계좌별 현재 projection을 갱신한다. 두 kind를 독립 계측에 등록했으며 transaction 병합은 하지 않았다. 로컬 위험·admission·DB 회귀 114건과 전용 PostgreSQL 저장/replay·current writer 실패 뒤 retry 복구·단조 revision 거부·metric call ID 검사가 통과했다(0.590초).

`execution_mock_automation_recovery_decisions`와 `execution_mock_automation_current_recovery`는 `ForwardEvaluationRepository.save_mock_automation_recovery_decision`에서 각각 immutable decision과 admission별 current projection을 저장한다. 각 write 전 operating spec·admission·lease 및 risk lineage 검사를 유지하고 두 transaction을 별도로 계측한다. 첫 전용 PG 시도와 v2는 fixture projection 누락으로 domain 호출 전 실패했으며, v3 fixture를 repository save 경계와 맞춘 뒤 관련 로컬 unittest 60건과 전용 PG lineage/replay·risk 거부·부분 실패 복구·metric call ID·cleanup 검사가 통과했다(4.946초). 운영 적용은 하지 않았다.

`execution_mock_automation_decision_gates`와 `execution_mock_automation_approved_gates`는 `save_mock_automation_decision_gate`가 gate history와 승인 intent projection으로 각각 기록한다. 승인 저장은 submit 호출보다 먼저 실행되며 두 `upsert_documents` 호출의 분리 경계를 계측한다. 로컬 unittest 114건, SQLite fixture/storage preflight, ZIP test load 및 전용 PostgreSQL `test_mock_automation_decision_gate_preserves_approval_boundary`가 통과했다(11.141초). PG는 approval/block/replay, approved projection 부분 실패와 retry, risk lineage, call ID 및 cleanup을 검증했으며 실제 주문은 제출하지 않았다. 운영 DB·이미지는 변경하지 않았다.

`execution_mock_automation_dispatch_receipts`와 `execution_mock_automation_dispatch_by_intent`는 `save_mock_automation_dispatch_receipt`가 승인 gate 확인 뒤 receipt history와 intent lookup projection으로 기록한다. 기존 두 immutable write와 native transaction을 유지하고 collection kind만 공통 계측에 추가했다. 로컬 mock automation·DB·공통 계측 회귀 114건 및 SQLite 저장 preflight가 통과했다. 전용 PostgreSQL `test_mock_automation_dispatch_receipt_recovers_separate_intent_projection`에서 receipt replay, 두 reader, projection 실패 뒤 history 보존·재시도, metric call ID 및 cleanup 검사가 통과했다(0.591초). 검사는 실제 dispatch/주문 제출을 실행하지 않았다. 운영 DB·이미지는 변경하지 않았다.

`execution_mock_automation_stop_revisions`와 `execution_mock_automation_current_stop`는 `save_mock_automation_stop_revision`의 immutable stop history와 admission별 latest projection이다. 긴급 중지는 별도 `save_mock_automation_control` transaction으로 STOPPED를 확정한 뒤 런타임 신규 주문을 닫고 두 문서를 쓴다. 이 두 collection kind만 관측에 추가했다. 로컬 회귀 113건과 SQLite preflight, 전용 PostgreSQL `test_mock_automation_stop_preserves_closed_control_and_recovers_projection`가 통과했다(1.575초). projection 실패에서도 STOPPED control과 history가 남고 repository 재시도로 latest reader를 복원하는지 확인했다. `save_mock_automation_control`은 계좌 advisory lock과 row lock 아래 revision compare-and-set을 하므로 이 pilot 범위에 포함하지 않았다.

과거뉴스의 `PostgresQueryStore.complete_external_historical_news_job`은 prepared importer가 직접 호출하지 않는다. `scripts/import_prepared_historical_news_to_nas.py`는 `_complete_prepared_job`에서 `_complete_external_news_job` helper를 직접 사용하며, `_complete_batch`가 caller-owned connection의 batch transaction 안에 record별 savepoint를 둔다. 반면 인증 API `/api/v1/news/historical-jobs/complete`는 해당 store 메서드를 호출하며 이 경로는 `news.external_finish`로 계측했다. 기존 `open_observed_connection`은 factory에서 connection을 얻고 native connection context 종료를 측정하므로 caller-owned batch/savepoint 경계에 그대로 적용할 수 없다. 현재 importer는 새 `observe_existing_transaction` 계약으로 outer batch만 관측하며 article savepoint는 원래 driver 경계로 남긴다. NAS 전용 PostgreSQL importer 통합검사 4건은 모두 통과했다(사용자 제공 결과, 16.147초).

`CURRENT_API_ID`, `CURRENT_FLUSH_ID`는 이미 ContextVar다. `to_thread` 전파는 기존 테스트로 확인했다.
반면 queue의 생산자 context는 소비자 task에 자동 복원되지 않는다. 필요한 `request_id/parent_call_id`는
기존 job/envelope에 불변 metadata로 전달하고 소비자가 token을 set/reset한다. 임의 새 Thread에도
공유 connection을 넘기지 않고 ID/별도 context만 전달한다. await 취소는 실행 중 DB thread 종료나
rollback 성공을 뜻하지 않으므로 실제 driver 완료를 기준으로 결과를 기록한다.

## 5. 새 코드의 최소 책임과 API

권고 신규 런타임 파일은 **`central_server/postgres_access.py` 하나**다.
`DBWriterContext`와 `ObservedConnection`/`ObservedCursor`를 같은 파일에 두고
context·기계적 계측·driver 호출을 소유한다. 별도 Manager/Service/Repository 층은 추가하지 않는다.

metrics 저장/요약은 **기존 `diagnostic_metrics.py`를 확장**하고,
분류는 **기존 `diagnostic_writer_registry.py`를 확장**한다. 새 `DBMetricRegistry` 저장소를
병렬로 만들어 동일 counter를 두 벌 유지하지 않는다. 기존 domain phase와 진단 출력은 유지한다.

```python
# 제안 API. 이번 검토에서 구현하지 않음.
context = DBWriterContext(
    writer_family="rest.query_cache", writer_kind="query_cache",
    operation="save_query", access_mode="write",
    rows_attempted=1, api_id=api_id,
)
connection = self._connect(writer=context)  # 선택 이관한 호출만 observed factory
try:
    with connection.cursor() as cursor:
        cursor.execute(existing_upsert_sql, existing_parameters)
        cursor.execute(existing_cleanup_sql, existing_cleanup_parameters)
    connection.commit()
finally:
    connection.close()
```

1차에는 이 writer의 **명시 종료 형태 그대로** 유지한다. 오류 때 새 `rollback()`을 추가하지 않는다.
close로 미완료 transaction이 폐기된 경우 `closed_uncommitted`로 관측하고
명시 rollback 호출·latency와 구분한다. 일반 `with db.transaction()`으로 일괄 교체하지 않는다.

driver의 `Connection`/`Cursor` 공개 subclass·cursor_factory 방식이 첫 후보다.
native context 종료가 overridden commit/rollback을 호출할 수 있어 driver 의미를 재작성할 필요가 적다.
설치 버전과 실제 PG에서 확인한 뒤 확정한다. 단순 composition proxy가 raw `__exit__`를 호출하면
raw 내부 commit이 계측을 우회할 수 있으며, raw cursor를 반환하면 execute도 빠질 수 있다.
`execute()`의 cursor 반환/체이닝, `rowcount`, fetch/iteration, SQL composable, cursor context도 보존한다.

현재 로컬 venv에는 psycopg가 없어 subclass prototype의 호환성은 아직 검증하지 않았다.
공식 3.2.13 소스는 참고일 뿐 NAS 설치 버전의 증거가 아니다. native context, 명시 commit,
`connection.transaction()`, SQL `COMMIT`, nested savepoint는 다른 종료 경로다.
첫 pilot은 명시 commit 경로만 완료 범위로 삼고, 다른 경로는 이관 전에 호환 시험을 추가한다.
private driver method monkeypatch나 전역 `psycopg.connect` 교체는 하지 않는다.

multi-row SQL 생성은 기존 `_execute_multirow_upsert`에 남긴다. 공통 계층은 만들어진 execute를 관측한다.
새 `execute_many_values`를 만들면서 batch 크기/순서/bind 수를 바꾸지 않는다.
미이관 `_connect()`는 기존 raw 연결을 반환하며 coverage를 미이관으로 표시한다.

이관한 기능 이해에 필요한 주 파일은 `database.py + diagnostic_metrics.py`에서
`database.py + postgres_access.py + diagnostic_metrics.py`로 한 개 늘어난다.
증가한 계층은 중복된 driver 호출 계측과 connection/transaction 관측 수명을 맡고 도메인 helper 깊이는 그대로다.

## 6. 식별자·트랜잭션 상태 계약

필수 context는 `writer_family`, `writer_kind`, `operation`, `call_id`이며
`call_id`는 실제 transaction attempt마다 새 값이다. context descriptor를 재사용하더라도 ID는 재사용하지 않는다.
연결에는 별도 `connection_id`, 상위 업무에는 `parent_call_id/request_id`, flush에는 기존 `flush_id`를 둔다.
한 connection의 두 commit 사이에는 서로 다른 transaction ID가 필요하다.
outer transaction 안의 savepoint는 `span_id`로 기록하고 독립 commit/transaction 수에 넣지 않는다.

- 연결 시도 실패: `calls=1`, 실제 `transactions=0`, `backend_pid=null`, 연결 오류 기록.
- 빈 입력 조기 return: DB 호출 없음. 도메인 호출 건수와 DB 호출 건수를 섞지 않는다.
- READ: implicit transaction도 측정하되 업무 write 합계와 구분. `SELECT` 문자열만으로 read-only를 확정하지 않는다.
- `load_top20_statistics`처럼 읽기·쓰기 혼합인 operation은 처음부터 mixed/write context를 선언한다.
- commit 요청 수, 활성 transaction commit 확인 수, noop commit, commit 실패를 구분한다.
- COMMIT 응답 유실/연결 단절: `commit_outcome=unknown`; rollback 호출이 성공했어도 원래 commit 미반영을 보장하지 않는다.
- statement 실패가 savepoint에서 회복되면 statement error는 남기고 outer outcome은 commit일 수 있다.
- rollback/close 실패는 별도 cleanup outcome. 관측 실패가 원래 예외를 가리거나 재시도를 유발하지 않게 한다.
  기존 driver/호출자의 예외 우선순위 자체를 정리하는 작업은 별도 변경이다.
- 명시 retry를 추가하지 않는다. 기존 retry owner가 알려 준 attempt만 기록하고 미전달 retry는 `null`이다.

최소 상태는 `not_started`, `in_transaction`, `committed`, `rolled_back`, `closed_uncommitted`,
`unknown`으로 분리하고 `error_stage/exception_type`은 별도 필드로 둔다.
driver에서 확인되지 않은 transaction/commit 수를 호출 횟수로 대체하지 않는다.

## 7. 공통 측정 의미

| 항목 | 정확한 의미 |
| --- | --- |
| 집계 key | `(writer_family, writer_kind)`; ID·종목·기사·URL은 label로 사용하지 않음 |
| calls / transactions | DB 시도와 실제 transaction 시작을 분리; savepoint/연결 실패/빈 입력 구분 |
| commits / rollbacks | 확인된 종료와 요청·실패·noop을 별도 counter로 보존 |
| rows_attempted | 업무 입력 수. cache 입력 1건이어도 만료 DELETE의 affected rows는 따로 기록 |
| logical_rows / affected_rows | 실행별 입력 수와 driver가 실제 제공한 rowcount 구분; 미지원·부분 실패는 null |
| connection_acquire_ms | 현재 새 연결을 얻는 client elapsed; pool_wait는 해당 없음(null), setup 별도 분해는 미계측 |
| execute_ms | driver 호출 wall duration; 서버 CPU·실제 디스크 I/O 시간이 아님. fetch_ms와 분리 가능 |
| commit_ms / rollback_ms | 해당 driver 호출만; close/probe join/로그 작성 시간을 포함하지 않음 |
| total_ms | 연결 시도부터 release/close까지; 별도 transaction elapsed와 domain total 유지 |
| sql_operations | 요청한 SQL 실행 수. executemany N개는 N회 round trip 뜻이 아니며 실패 시 완료 N개도 아님 |
| errors / retries | 연결·statement·commit·rollback·close·진단 오류와 retry 보고 출처 분리 |
| 시간 분포 | min/p50/p90/p95/p99/max + n + drop/coverage; 서로 다른 outcome을 성공 분포에 합치지 않음 |

시간 측정에는 monotonic/perf_counter를 사용한다. 외부 상관용 UTC 시작·종료 시각도 남긴다.
서버 `clock_timestamp()`와 PC/NAS 시계가 다를 수 있으므로 clock domain과 관측 offset/불확실성을 표시한다.
`backend_pid`는 `connection.info.backend_pid`로 얻어 추가 SQL round trip을 만들지 않는다.
PID 단독은 재사용되므로 connection_id·call_id·접속 대상 별칭·시간창과 함께 묶는다.

executemany를 세기 위해 generator를 선소비/리스트화하지 않는다. 크기를 모르면 null이나
소비 시 확인된 수를 사용한다. SQL 전송 횟수와 execute wrapper 호출 수를 별도로 기록한다.
Psycopg의 pipeline 내부 실행·fetch·flush 비용을 순수 서버 실행 시간으로 표현하지 않는다.

기존 cache total에는 JSON encode가 들어가고 새 DB total에는 제외될 수 있다.
기존 dataset phase에도 probe 준비·정리 등의 차이가 있다. 따라서 동일 call_id의 동일 시작/종료 경계끼리
비교하고, 기존/new total의 차이를 오류나 성능 개선으로 바로 판단하지 않는다.

## 8. 기존 capture·registry 통합

기존 `diagnostic_metrics.py`는 capture 기본 OFF, bounded deque, 성공 writer 표본 중심이다.
`diagnostic_writer_registry.py`의 12개 항목은 전체 DB writer 목록이 아니다.
기존 rows는 대부분 시도 수, 여러 writer의 commit_ms/errors/retries는 미계측이다.

제안의 ‘모든 transaction에 동일 관측’은 **모든 이관 경로에 관측 가능한 경계를 둔다**는 뜻으로 적용한다.
실제 표본 수집은 기존 master→capture 자식 ON/TTL 계약을 유지한다. OFF 동안 0건 관측을
0건 DB 활동으로 표시하지 않는다. 상시 상세 수집 정책을 이번 리팩터링에 끼워 넣지 않는다.

- 최소 bounded call record를 기존 registry에 추가하되 schema/version을 구분한다.
- 기존 domain 표본과 새 DB 표본은 call_id로 연결한다. 한 commit을 legacy+new 두 번 합산하지 않는다.
- family별 capture 선택도 master/session/TTL 아래 둔다. 저장 동작 pause를 자동 추가하지 않는다.
- writer registry에 `registered/unregistered/unmigrated/diagnostic_exempt`와 측정 지원 범위를 명시한다.
- session ID/revision을 시작에 고정하고 종료에 재확인한다. 만료된 call이 새 capture에 섞이지 않게 한다.
- 오래 걸리는 call의 만료·잘림은 dropped/incomplete로 표시한다. bounded deque를 넘어선 자료는
  ‘전체 호출’이라고 출력하지 않는다. cumulative counter와 retained-sample percentile 범위를 구분한다.
- dynamic kind cardinality와 raw statement/diagnostic sample 수에도 상한을 둔다. 임의 collection은
  무한 label을 만들지 않고 미등록/overflow로 표시한다. 기존 입력은 거부하지 않는다.
- 관측 sink 실패는 운영 DB 실패로 승격하지 않으며 failure counter/제한 로그만 남긴다.

기존 TOP20·external-news claim wait probe는 master capture와 별개로 지연 시 작동하는 경로가 있다.
따라서 ‘현재 capture OFF이면 모든 기존 probe가 꺼진다’고 보고하지 않는다.
이 경로를 새 hook으로 통합하는 것은 해당 writer 이관 시 별도 호환 검토 항목이다.

## 9. 느린 호출 hook과 안전한 진단

완료 후 `commit_ms >= threshold`에서 실행하는 hook은 slow 기록/경보만 가능하다.
이미 끝난 COMMIT 당시 wait event는 복원할 수 없다.

실시간 wait 수집은 capture ON일 때 호출 시작을 등록하고, 별도 진단 observer가
**아직 실행 중인 단계**가 임계시간을 넘었을 때만 시작한다. 앞부분 대기는 누락될 수 있음을 표시한다.
첫 pilot은 hook interface와 시간창만 제공하고 기존 무거운 probe를 전역 확대하지 않는다.

- callback: `stage_started(call, stage, timestamp)`, `stage_finished(...)`, `call_finished(record)`.
- driver 호출 thread에서는 시간/상태 전달만 한다. blocking sampler·진단 SQL·파일 I/O를 기다리지 않는다.
- 진단 concurrency·표본 수·timeout은 제한하고 probe 실패/누락을 기록한다. 이 제한은 업무 writer를 직렬화하지 않는다.
- probe는 별도 연결; 업무 연결로 `pg_stat_activity`를 읽지 않는다. 진단 연결은 recursive hook에서 제외한다.
- snapshot을 잡아 오래된 상태를 재사용하지 않도록 기존 autocommit 진단 전략을 유지한다.
- master OFF/TTL/세대 교체 때 새 probe 금지 및 기존 probe 종료. 업무 transaction은 중지하지 않는다.
- storage는 기존 sampler의 call window와 연결한다. host-wide/cluster-wide 값을 단일 writer의 WAL/IO로 귀속하지 않는다.
- summary: writer aggregate. verbose: slow call와 wait별 count/first/last offset.
  raw: bounded 개별 표본을 파일로 저장. wait 표본 수×25ms를 정확한 대기시간으로 간주하지 않는다.

## 10. UNREGISTERED와 우회 탐지

런타임 UNREGISTERED는 **observed connection을 통과했으나 writer context가 없는 접근**만 검출한다.
raw `psycopg.connect()` 우회는 이 hook에서 보이지 않는다. 다음 두 축을 모두 갖춰야 한다.

1. 이관 경로 runtime context 누락: `UNREGISTERED`, operation/callsite ID/PID 기록. 운영 동작은 보존.
2. 정적 factory/direct access 원장: 승인된 예외(진단/관리/테스트)의 범위·소유자·이유와 새 우회 지점 검사.

현재 공통 계층을 지나는 호출의 context 누락만 runtime `UNREGISTERED`로 계측한다.
미이관 raw 연결은 이 수치에서 빠지므로 전체 writer의 `UNREGISTERED=0`이라고 보고하지 않는다.
전수 완료 조건은 미이관 0 + 미승인 직접 연결 0 + 검증한 capture 범위 UNREGISTERED 0이다.
진단 exempt는 숨기지 않고 별도 count로 유지한다. DDL·COPY·함수 호출·WITH DML 등은 SQL 첫 단어
정규식만으로 판정하지 않는다. 모르는 statement는 unknown으로 남겨 false zero를 막는다.

callsite는 정적으로 등록한 module/function/line을 우선한다. 매 execute stack inspection은 하지 않는다.
SQL/DSN/parameter/원문/개인정보/예외 message는 기본 수집하지 않는다. statement ID·등록 table·
안전한 template fingerprint·exception type/SQLSTATE만 사용한다. fingerprint를 만든다는 이유로
값이 보간된 SQL 원문을 보관하거나 전체 query를 로그하지 않는다.

## 11. 단계별 이관과 완료 조건

| 단계 | 범위 | 다음 단계 진입 조건 |
| --- | --- | --- |
| 0 (이번) | 현재 경로·예외·계측 차이 정리 | 이 문서와 원장 확인; runtime 변경 없음 |
| 1 | 단일 postgres_access 파일, 기존 registry 확장, save_query 1개 | 명시 commit/close·implicit discard·예외 보존, 아래 전용 PG/오버헤드 기준 통과 |
| 2 | native connection context를 쓰는 단순 writer 하나 | native __exit__의 성공/오류/rollback 실패 검증; 기존 성공 계측과 같은 경계 비교 |
| 3 | document 및 읽기 중 쓰는 통계 cache | helper·collection 종류·혼합 입력·기존 ID/revision/reader 보존 |
| 4 | news 및 외부 importer | lease/claim/기사별 savepoint/긴 연결의 복수 transaction/원장 순서 검증 |
| 5 | dataset 및 REST market bars | 기존 durability·advisory lock·revision·probe·혼합 kind 보존 |
| 6 | realtime, 계좌/주문/credential 각각 별도 단계 | flush/멱등성/재시작·중복/소유권 회귀; 보호 경로는 더 강한 검증 |

기존 목록의 news→market→realtime 순서는 대체로 유지한다. 계좌/주문/초기화/관리 도구도 최종 coverage에서
누락하지 않는다. SQLite runtime은 이번 이관 대상이 아니며 PG PID/WAL 기능을 억지로 붙이지 않는다.

Phase 5의 dataset pilot은 `save_dataset_snapshots`의 동종 `market_state`, `new_high`,
`program_flow`, `ranking`, `top20_membership`, `top20_index`, `market_index_chart`,
`investor_flow`, `stock_fundamentals`, `nxt_eligibility` batch를 관측한다. 열 kind 모두 전용
PostgreSQL replay·rollback·metrics 검증을 완료했다.
기존 호출별 연결, kind별 비동기 COMMIT 설정, 과거 날짜 advisory lock, 저장 순서와 명시
commit/rollback/close를 보존한다. 실제 운영 batch 호출은 `market_state`와 `program_flow`의
동종 kind만 전달하므로, 혼합 kind batch는 기존 연결 경로로 두고 실호출이 생길 때 별도 범위를
결정한다. 각 kind의 검증 상태는 CURRENT_STATUS와 OPEN_ITEMS를 따른다.

`load_top20_statistics`의 read/cache-fill transaction도 common wrapper를 통과한다. cache miss
경로는 advisory lock 아래 원천 snapshot을 읽고 `top20_statistics_day`를 executemany로 저장하며,
native connection context의 종료가 commit한다. cache hit, cache miss, cache 삭제 후 동시 조회의
reader·commit·lock 동작을 전용 PostgreSQL 검사에서 확인했다.

후속 `NewsService` / `CentralContentSync` pilot에서 `news_article` revision과 `theme_metadata`
snapshot/replace 경계를 계측했으며 전용 PostgreSQL replay·rollback 검사를 통과했다. 당시의 미계측
후보 문구는 현재 상태가 아니다. 단계별 결과는 CURRENT_STATUS와 OPEN_ITEMS를 따른다.

2026-09-28 REST 분·일봉과 `CandidateMonitor` decision/event·checkpoint pilot이 전용 PostgreSQL
검증을 통과했다. CandidateMonitor 두 저장은 서로 다른 native context transaction으로 각각 관측한다.
전용 검사에서 checkpoint 실패 뒤 event reader 결과와 sequence 유지, 같은 event 재생의 중복 방지,
immutable decision 오류 rollback, 두 writer의 독립 call ID/backend PID를 확인했다. 로컬 검사 46건,
전용 PostgreSQL 검사 1건(1.352초)이 통과했고 ZIP의 NAS host hash가 일치한다.

2026-09-28 inventory 재대조에서 `PostgresQueryStore.create_observation_export`의 활성 caller를 확인했다.
`app.py`의 인증된 `/api/v1/research/observations` handler는 watermark가 없을 때 이를 `asyncio.to_thread`로
호출하고, 반환된 fixed watermark로 `load_observation_export_page`를 읽는다. writer는 immutable revision
selection, manifest INSERT, ordered membership executemany를 기존 connection context 안에서 수행한다.
로컬 관측 이식과 registry 등록을 마쳤다. 전용 PostgreSQL `test_research_export_keeps_fixed_membership_and_rolls_back_partial_failure`가 통과했다(0.533초): fixed membership reader, 신규 revision 비편입, 부분 membership 실패 rollback, metrics 및 cleanup을 확인했다.
`save_five_minute_bars`는 store/schema와 테스트 외에 production
caller가 확인되지 않아 활성 경로로 분류하지 않았다. execution intent/event/account snapshot/runtime lease
5개 PostgreSQL 쓰기는 2026-09-28 공통 관측 pilot과 전용 PostgreSQL 검증을 마쳤다. 각 native transaction,
event/projection 원자성, lease CAS·ownership 검사를 유지했다. account identity/binding/scope alias와
legacy/bootstrap binding append 2개 PostgreSQL 쓰기도 전용 PostgreSQL 검증을 통과했다. identity replay,
binding revision advisory lock, 실패 rollback을 확인했다. `register_account_scope_alias` helper는 현재
`src` caller가 없어 활성 경로로 분류하지 않았다. `finalize_credential_activation`은 vault commit 후
global advisory lock 아래 binding revision·activation receipt·profile lifecycle·account settings를
한 native transaction에 기록하며 recovery에서도 재호출된다. 이 writer와 credential profile의
create/register/rename/archive는 각각 관측 kind를 갖고 전용 PostgreSQL replay·rollback 검증을 마쳤다.
CandidateMonitor의 `_run` 예외 경로에서 `_consume` 중 바뀐 in-memory state와 이전 cursor를 함께 checkpoint할 수 있는 조합도 별도 검증이 필요하다.
`_consume` 중 바뀐 in-memory state와 이전 cursor를 함께 checkpoint할 수 있는 조합도 별도 검증이 필요하다.

2026-09-28 후속 caller 추적에서 `complete_external_historical_news_job`은 인증된
`/api/v1/news/historical-jobs/complete`가 `asyncio.to_thread`로 호출하는 활성 PostgreSQL 경로임을 확인했다.
기존 관측 wrapper에 필요한 import와 로컬 writer 생성이 빠져 있던 결함을 수정하고
`news.external_finish / news_external_finish:BODY|RULE`로 등록했다. BODY revision·RULE 작업 또는 RULE
assessment·event/membership·job 상태 변경은 기존 한 native transaction에 유지했다. 로컬 검사 54건 중
53건 통과, PC 전용 DB 1건 skip; 컨테이너 전용 PostgreSQL replay·강제 실패 rollback 검사는 4.890초에 통과했다.

실계좌 모니터의 `_write_account_events` 큐 drain과 `_monitor_account_cycles` 주기 read 뒤 호출되는
`save_real_account_event`·`save_real_account_recovery`는 각각 `account.real_monitor`의 별도 kind로
관측한다. 기존 `asyncio.to_thread`, per-call native transaction, 전역 `credential-activation`
advisory lock, 문서 hash 기반 replay와 계좌 binding/settings fence는 유지한다. 로컬 공통 DB 및
실계좌 회귀가 통과했고 컨테이너 전용 PostgreSQL 검사도 1.076초에 통과했다. 동시 저장·replay·fence·rollback·정리를 확인했다.

`save_account_settings`·`save_market_profile_settings`도 `account.settings` family의 서로 다른 kind로
공통 관측한다. real/mock account settings 저장과 market-role 전환의 실제 호출 경로 및 CAS reader를
확인했고, 기존 per-call native transaction·`credential-activation` advisory lock·runtime drain/credential
fence를 보존했다. 관련 로컬 검사 45건과 전용 PostgreSQL 검사가 통과했다(4.861초).
동시 저장·replay·rollback·market-profile fence 및 global row 복구를 확인했다.

READ pilot 구현 범위는 운영 호출자가 확인된 `CentralRestBroker._resolve_cache_or_queue →
asyncio.to_thread(PostgresQueryStore.load_query)`다. `load_query`에는 기존 native context와 SQL·반환값을
유지한 명시적 READ context가 연결됐다. 공통 call record의 `access_mode`는 기본 WRITE이며 READ는
`summarize_db_calls` 응답의 별도 `readers` 집계에 둔다. 기존 `writers` 집계는 그대로 유지한다.
SELECT 뒤 driver context COMMIT은 reader transaction의 종료로 관측되고 write commit 집계에 섞이지
않는다. 로컬 회귀 44건과 NAS 전용 PostgreSQL hit/expired/missing 검사가 통과했다(0.497초).
`fetchone`은 현재 `execute_ms`에 포함되지 않으므로 `total_ms`만 조회 전체를 포괄한다.

다음 활성 READ boundary로 `PostgresQueryStore.load_documents`를 구현했다. 서버 operational settings,
시장 coverage, 종목 catalog, 뉴스 source와 collector 등에서 호출한다. PostgreSQL SELECT·result decoding·
native context를 유지하고 collection별 `read.document_collection/document:<collection>`으로 기록한다.
로컬 회귀 99건과 전용 PostgreSQL hit/empty 검사가 통과했다(0.352초). `fetchall`은 `execute_ms`와 분리
계측하지 않으므로 전체 조회를 `total_ms`로 본다. `load_document`는 활성 운영 caller가 없어 제외했다.

다음 active reader로 실제 API·market ingest caller가 확인된 `load_minute_bars`와 `load_daily_bars`를
선정하고 구현했다. 두 reader는 query, result mapping, native transaction 및 SQLite 경계를 유지하면서
`read.market_bars/minute_bar|daily_bar`로 기록한다. 로컬 공통 DB/store/API 회귀 100건과 전용
PostgreSQL hit/empty·native context·reader metrics 검사(0.570초)가 통과했고 ZIP의 NAS 호스트 해시도 일치했다.
prepared-news importer의 caller-owned batch transaction은 outer
`connection.transaction()` 한 번을 관측하는 경계로 연결했다. 기존 기사별 nested
savepoint는 그대로 driver가 관리하며 별도 transaction/call로 집계하지 않는다.
같은 연결을 다음 batch에서 재사용하면 새 call ID를 발행하고 연결 획득 시간은
`None`으로 기록한다. 실제 PostgreSQL 전용 DB에서 배치 commit·savepoint 동작을
검증하는 4건의 통합검사는 통과했다. 이 결과는 운영 부하의 COMMIT 지연을 측정한 값은 아니다.

candidate monitor의 초기 seed·증분 replay와 mock automation 초기 seed reader 두 개는
`read.observation_revisions/observation_revision`과
`read.observation_revisions/observation_revisions_after`로 구분했다. SQL·filter·order·limit·row decoder,
native connection context와 SQLite 구현을 유지했다. 로컬 회귀 117건과 NAS 전용 PG 검사(0.208초)가 통과했다. sequence 발급/commit 순서가 뒤집힐 때
증분 cursor가 낮은 sequence를 놓칠 수 있는 기존 계약 문제는 별도 OPEN ITEM이며 이번에 변경하지 않았다.

shadow monitor state/event page, dataset snapshot, market metadata range 네 reader batch는 단일 dedicated PG
검사와 관련 로컬 회귀 183건이 통과했다(0.565초). 뒤이은 active news READ batch는 article/body history와
revision, source cursor/diagnostics, news publications/feed의 8개 직접 조회 boundary를 3 family로 묶었다.
기존 source-page replay/failure 테스트에 reader hit 결과와 metrics assertions를 함께 넣었고, 관련 caller 로컬
회귀 214건이 통과했다. 첫 전용 DB run은 GLOBAL source-page row를 watchlist-only reader에 기대한 테스트 fixture 오류로 실패했으며, 별도 watchlist fixture를 넣은 v2에서 통과했다(3.656초). production query와 저장 계보는 유지했다.

다음 market-state READ batch는 실제 API/runtime caller가 확인된 realtime snapshot/latest market cap, theme snapshot, market-event history/hot cohort의 다섯 read boundaries를 family/kind별로 관측했다. 기존 writer replay/rollback, SELECT/result decoding, native transaction은 보존했다. 반복 `--test` 옵션으로 묶은 세 전용 DB 테스트가 통과했다(3.239초).

이어 active `CentralRestBroker`, `NewsSourceCollector`, `CentralNewsAIService` 경로의 external bars, request budget count, AI revision lookup reader 세 개를 family/kind별로 관측한다. native query/transaction은 그대로 유지하고 기존 external bars replay, budget concurrency, AI result/revision atomicity 테스트를 반복 `--test` 한 번으로 검증한다. 결과는 OPEN_ITEMS를 따른다.

최초 정적 조사에서 직접 `self._connect()` context는 49곳(읽기·정보 45, 저장 3, schema 초기화 1)이었다.
query-cache, documents, minute bars, daily bars, observation-revision, shadow, dataset snapshot, metadata-range,
news readers 열여덟 곳을 공통 wrapper로 옮긴 뒤 직접 context는 31곳이었다. market-state batch 다섯 context와 external/news batch 세 context를 적용한 당시 수는 23곳(읽기·정보 19, 저장 3, schema 초기화 1)이었다.
2026-09-28 재감사에서는 AST 기준 `PostgresQueryStore`의 직접 `self._connect()` context가
5곳, `open_observed_connection` context가 84곳이다. 다섯 곳은 `save/load_five_minute_bars`,
`save/load_market_data_metadata`, `register_account_scope_alias`이다. 첫 네 곳은 현재 `src`의
실행 호출자가 검색되지 않았고 alias 등록은 `application/account_identity.py` helper에서
호출되지만 그 helper의 운영 호출자는 검색되지 않았다. 테스트·점검 호출 및 동적 호출
가능성은 남는다. 전체 소스 AST 재실행 결과는 store method 102개, literal driver connect
29곳, backend 혼합 후보 DB API call 6,679곳, parse error 0이다. 생성 원장 JSON도
이 결과로 갱신했다. `src`의 직접 driver
connect 세 곳은 store factory와 기존 wait probe 두 곳이다. 나머지는 scripts/tests에 있고
진단·일회성 관리·실제 실행의 분류는 위 29곳 원장에 기록했다. 이는 정적 조사이지 전체 runtime coverage의
증거가 아니다.

현재 `UNREGISTERED`는 observed wrapper에 context 없이 들어온 호출만 세며 raw driver
연결은 보지 못한다. `_DB_CALLS`는 capture 중인 프로세스의 bounded deque에만 있으므로
독립 importer의 call은 서버 API의 집계에 나타나지 않는다. 모든 writer의 관측 가능한
출입구라는 목표를 선언하려면 직접 연결 예외/이관과 프로세스 간 진단 전달 계약이 필요하다.
PC 완성 archive가 대량 importer를 대체하는 계획이므로 준비 단계 READ commit을
추가 이관하기 전 실제 사용 여부를 다시 확인한다.

공통 layer 배포만으로 기존 직접 접근을 일괄 막거나 모든 `_connect`를 observed로 바꾸지 않는다.
미등록 탐지는 먼저 보고 모드이고 enforcement는 coverage 확인 후 별도 결정한다.

### 2026-09-28 수집 경계 결정과 다음 구현 계약

후속 로컬 구현: 아래 1~4항의 서버 producer metadata와 NAS 진단 보고서 `db_calls`
summary 연결을 완료했다. 동일 프로세스·capture session 여부, 제어 revision 변경,
bounded sample 잘림, 구버전/통신 오류를 구분한다. 관련 로컬 묶음 회귀 119건과
최종 API 단일 검사 1건이 통과했다. 운영 NAS 연결·배포는 확인하지 않았다.
아래의 "아직 구현하지 않았다" 및 "다음 한 묶음 구현"은 설계 당시 기록이다.

**결정:** 공통 access API와 record 형식은 유지하되 수집 버퍼는 프로세스별로 둔다.
이번 범위에 공유 파일 append, IPC daemon, PostgreSQL metrics table은 도입하지 않는다.
서버는 기존 인증 진단 API로 회수하고, 실제 사용이 확인된 standalone 도구는 자신의
실행 결과 파일로 회수하는 방식으로 구분한다. standalone 출력은 아직 구현하지 않았다.
이는 capture 밖 transaction이나 raw 연결의 전체 관측을 완료했다는 뜻이 아니다.

소스 근거와 확인 범위:

- Dockerfile의 진입점은 `python -m kiwoom_monitor.central_server`이며 `__main__.py`는
  `uvicorn.Server(...).run()`을 한 프로세스에서 실행한다. 현재 운영 컨테이너의 실제
  프로세스 수나 배포 버전은 이번 단계에서 조회하지 않았다.
- `diagnostic_workloads.instance_id()`는 Linux에서 boot ID와 `/proc/1/stat`을 사용한다.
  같은 컨테이너의 서버와 `docker exec` 도구가 같은 제어 세션을 볼 수 있지만 각자의
  `_DB_CALLS` 메모리는 공유되지 않는다. 이 ID를 metric 생산 프로세스의 식별자로 쓰면 안 된다.
- `/api/v1/diagnostics/db-calls`는 현재 서버 프로세스의 bounded capture만 반환한다.
  기존 `nas_workload_diagnostic._measure`는 `market-bar-saves`만 받아 보고서에 넣으며
  새 `db-calls`는 요청하지 않는다. 따라서 다음 우선 작업은 기존 보고서의 수집 누락 보완이다.
- `_run_measurement`가 반환한 뒤 자동 capture OFF를 실행하고 `_save(report)`를 호출한다.
  OFF/TTL 만료/세션 변경 때 메모리 표본이 지워지므로 회수는 OFF 전에 끝나야 한다.

대안 비교: 공유 spool/IPC는 별도 수집 수명·파일 경합·복구·보존 정책을 추가한다.
현재 소스의 단일 서버와 교체 예정 legacy importer만으로 그 책임을 추가할 근거는 없다.
PostgreSQL에 metrics를 쓰면 관측 대상에 추가 transaction/WAL 부하를 만들므로 제외한다.
서버 다중 worker/replica 도입 또는 여러 상시 writer 프로세스의 통합 실시간 조회 요구가
확인되면 공유 수집을 다시 검토한다. 현재 기록의 출처를 분리하면 후속 전환도 가능하다.

**다음 한 묶음 구현 — 기존 NAS 진단 보고서 연결:**

이 작업은 로컬 구현 및 회귀 검증을 완료했다. 실제 standalone 결과 전달 검토 결과,
현재 NAS 진단기는 이미 JSON 파일을 저장하고, legacy importer runner는 상태 JSON/log를,
seed exporter는 SQLite manifest와 stdout 요약을 남긴다. 저장소 내 정기 실행 경로도 찾지
못했으므로 별도 공통 exporter는 추가하지 않는다. NAS 밖 scheduler의 존재나 현재 실행 상태는
이 정적 조사 범위에서 확인하지 않았다.

2026-09-28 실행 NAS 상태 확인: 공유 경로의 Compose는 8787을 publish하며, 실행 중
`/health`는 `2026.09.26-bar-upsert-wait-correlation-v5`를 반환했다. 인증 header 없는
`/api/v1/diagnostics/db-calls` 요청은 404였다. 현재 운영 앱에는 이 endpoint가 없다.
이 검사는 route 부재만 판별했고 DB handler를 호출하지 않았다. 로컬 코드와 기존 NAS build의
차이를 조사하지 않은 채 서버 전체를 교체하면 안 되며, 운영 적용은 별도 배포 검토가 필요하다.
NAS 공유 source의 `app.py`도 같은 구형 build marker이며 `postgres_access.py`가 없다.
현재 workspace `app.py` 변경에는 DB 진단 route 외에 historical archive·Top20 기능 변경이
섞여 있고 `database.py`는 repository HEAD 대비 1,650 insertion/151 deletion이다. 따라서
현재 workspace 전체를 이 endpoint 하나를 위한 안전한 배포 patch로 간주할 수 없다.

1. `diagnostic_metrics.summarize_db_calls`의 기존 필드는 유지하고 producer metadata를
   추가한다. producer는 실제 PID와 프로세스 시작 수명마다 새 UUID로 식별하며,
   container 제어 instance ID 및 capture session ID와 구분한다. DSN·호스트 비밀·SQL
   parameter는 넣지 않는다. 새 전역 공유 연결이나 metric 저장 서비스를 만들지 않는다.
2. `_measure`의 동일 `[started, ended)` 구간으로 인증 `db-calls` API를 호출하고 응답을
   보고서의 별도 `db_calls` 항목에 보존한다. 기본은 summary이며 기존 writers/readers,
   UNREGISTERED, retained/drop/truncation 및 coverage 설명을 누락하지 않는다.
   기존 domain metrics는 그대로 둔다. 동일 call의 구/신 metrics를 더해 commit 수를 계산하지 않는다.
3. 측정 전후 응답에서 producer와 capture session이 같은지 확인한다. 시작 metadata를
   얻기 위한 조회도 summary API의 유효한 짧은 구간을 쓰고, 그 표본은 측정 구간에 합치지 않는다.
   OFF·TTL 만료·세션 교체·프로세스 재시작이 있으면 해당 DB 관측 구간을 불완전으로 표시한다.
   구버전 404, 인증/통신 오류, metadata 누락은 unavailable/미확인으로 남기며 0건으로 바꾸지 않는다.
   DB 계측 조회 실패로 기존 storage/domain 보고서를 버리지 않는다.
4. 회수는 기존 자동 capture OFF 전에 수행하고 파일 저장은 이미 회수한 객체를 사용한다.
   기존 session/owner fence, TTL, pause/resume 및 DB transaction은 변경하지 않는다.
   raw export 확장은 이번 묶음에 넣지 않는다. 현재 raw API는 limit 및 raw_truncated가 있는
   제한 표본이며 전체 raw 보존 기능이 아니다.
5. `test_diagnostic_cli_controls`, `test_postgres_access`, 중앙 API 관련 검사를 한 번의
   로컬 회귀 묶음으로 실행한다. 정상 구간, OFF 전 회수, 세션/producer 교체, endpoint
   unavailable, capture 만료 및 기존 metrics 보존을 fake API/시계로 검증한다.
   도메인 SQL을 바꾸지 않으므로 이 변경만을 위해 NAS writer pilot을 반복하지 않는다.
   운영 API 연결 확인은 실제 배포 검증 때 한 번 수행하며 지금 통과했다고 기록하지 않는다.

**별도 남은 범위:** static bypass guard는 29곳 원장의 파일+함수+연결 표현식을 기준으로
`scripts/audit_postgres_access.py --check`에 연결했고
`docs/postgres_access_direct_connection_approvals.json`에 함수 단위 수·분류·이유를 기록했다.
2026-09-28 현재 29/29 일치, 미승인/오래된 항목 0이다. 새 미승인 연결 또는 코드에서
사라진 예외가 생기면 검사 종료 코드 1과 원장 차이를 반환한다. 진단/관리/test 분류가
그 코드를 실행해도 된다는 승인은 아니다.
런타임 `UNREGISTERED=0`과 정적 미분류 연결 0은 각각 보고하며 동적 우회 부재를 증명하지 않는다.
운영 caller 미확인 5개 store context와 importer 준비 READ는 여전히 제외 목록이다.
standalone 실행이 다시 필요해지면 그때 bounded 실행 세션 및 종료 전 자체 report export를
붙인다. 서버 API에 임의로 다른 프로세스 표본을 섞지 않는다. 일반 운영 상시 집계,
전체 raw 보존, 실제 writer overhead acceptance 및 운영 적용은 별도 OPEN ITEM으로 유지한다.

## 12. 검증 계획

### 동작 보존

전용 `kiwoom_monitor_diagnostic_test`에서 DSN의 DB명과 `SELECT current_database()`를 모두 확인한다.
테스트 fixture는 격리하고 운영 DB에 시험 write 후 지우는 방식은 사용하지 않는다.

- cache insert/update, 만료 DELETE, 변경 없는 replay: 반환/행값/행수와 기존 SQL 순서 동일.
- raw와 observed 경로의 commit/rollback/close 호출 시퀀스 및 서버 transaction 결과 비교.
- execute 오류·연결 실패·duplicate/ON CONFLICT·commit 실패/응답 유실·rollback/close 실패.
- save_query의 오류는 기존처럼 close; 새 명시 ROLLBACK 추가 없음. 원래 예외 보존.
- 예외 metadata·backend PID·무등록 context·capture OFF/ON/만료/세대 교체·drop 검증.
- 별도 thread/connection의 두 transaction을 barrier로 겹치게 해 직렬화가 추가되지 않았는지 확인.
  한 writer rollback이 다른 writer commit을 취소하지 않는지 확인.
- `to_thread`, 기존 queue 전달, ContextVar reset; 취소된 await 뒤 늦게 완료되는 DB 작업 구분.
- 뒤 단계에서 nested savepoint/empty transaction/mixed read-write/DDL autocommit/COPY 지원별 검사 추가.

### 기존/new 계측 비교 및 오버헤드

동일 call_id에서 commit wrapper의 monotonic 원시 구간과 기존 commit 구간을 짝지어 비교한다.
기존 ms 반올림과 새 timer 해상도 차이를 반영하며 다른 호출의 percentile끼리 일치 판정하지 않는다.
중첩 측정 때문에 신규 commit 시간이 기존 외곽 timer보다 조금 작을 수 있다.

raw 기준선 / observed+capture OFF / observed+capture ON(hook 비활성)을 같은 입력·DB·
동시성·반복수로 warm-up 후 교차 반복한다. 작은 transaction과 실제 cache cleanup 두 부하를 나눈다.
표본 수·p50/p95/p99·CPU·연결 수·처리량·누락/drop과 동시 부하를 함께 보고한다.
1차 가드 제안은 1,000회 이상×3구간, 추가 client p95 1ms 이하 및 처리량 저하 5% 이내이나,
노이즈가 이보다 크면 통과로 단정하지 않고 안정된 전용 환경/더 많은 표본으로 재측정한다.
진단 probe ON은 별도 비용 실험이며 일반 wrapper overhead에 섞지 않는다.

### 초기 설계 조사 당시 실행한 확인

- 초기 AST 조사 당시 수치: 102/29/6,661, parse 오류 0. 이후 원장을 갱신했다. 최신 수치와
  direct-connection guard 결과는 위 2절과 2026-09-28 수집 경계 결정 항목을 따른다.
- 원장의 모든 소스 해시·개수 대조, 격리 소스 fixture의 helper 추적/SQLite 제외,
  읽기·쓰기 혼합 분류, 조사 도구 구문 검사 통과. `git diff --check`와 신규 파일 공백 검사 통과.
- 기존 `test_diagnostic_flush_metrics`, `test_diagnostic_workloads`,
  `test_storage_boundary_concurrency`: **21건 통과, 프로세스 종료 코드 0**.
- 이 테스트는 기존 구현의 기준선이다. 아직 없는 공통 layer의 검증 결과가 아니다.
- 로컬 프로젝트 Python 3.13.15 실행 확인. `import psycopg`는 ModuleNotFoundError였고
  `KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL`은 미설정이다. 설치/접속/운영 변경을 진행하지 않았다.
- 실제 PostgreSQL 통합테스트 및 wrapper OFF/ON 성능 측정: **미실행**.

## 13. 남은 판단과 배포 경계

1. 실제 NAS psycopg/libpq 버전 확인 후 공개 subclass/cursor_factory 호환 prototype 검증.
2. 전용 PG 환경에서 pilot의 종료/오류·동시성·observer 고장 격리와 overhead 확인.
3. 최초 공통 call record/API 출력은 additive로 설계하고 기존 진단 소비자의 schema 호환 검증.
4. 미이관 raw factory와 진단/maintenance 예외를 점진적으로 줄여 coverage 증명.
5. 기존 `ASYNC_COMMIT_DATASET_KINDS`는 이번에 추가한 정책이 아니다. 1차에서는 보존하며
   해당 데이터의 손실 허용성 재검토는 별도 항목이다. 다른 writer에 이를 확대하지 않는다.
6. existing observation sequence 발급/commit 순서 역전 문제는 공통 layer가 해결하지 않는다.
   별도 재현·계약 검토 항목으로 유지한다.
7. protected pause/drain/durable handoff와 과거뉴스 archive는 별도 설계다. 이번 계층에 끼워 넣지 않는다.

이번 변경은 문서와 읽기 전용 조사 도구뿐이므로 server build marker를 올리거나 NAS에 배포하지 않는다.
추후 서버 런타임 구현 시 app/Compose/Dockerfile marker를 함께 갱신하고 관련 PG 검증 뒤 배포한다.

모델 라우팅: 본 검토는 저장소 전체 재설계가 아니라 DB 공통 경계 검토다. 후속 구현은
드라이버 종료·실패·동시성 계약 때문에 **Sol High**가 적절하며, 반복 등록만 남으면 Luna Medium부터 재평가한다.
현재 실행 모델의 정확한 변형·추론 수준은 이 문서에서 확인하거나 변경했다고 주장하지 않는다.

## 14. 외부 계약 근거

소스 조사에 더해 아래 공식 자료로 driver 계약을 확인했다. 운영 설치 버전은 별도 확인이 필요하다.

- [Psycopg transaction 관리](https://www.psycopg.org/psycopg3/docs/basic/transactions.html):
  implicit transaction, connection context와 nested transaction/savepoint 차이.
- [Psycopg 3.2.13 connection 소스](https://github.com/psycopg/psycopg/blob/3.2.13/psycopg/psycopg/connection.py):
  native context의 commit/rollback/close 호출 및 별도 transaction context 경로.
- [Psycopg pipeline 설명](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html):
  executemany의 pipeline 사용과 실행 횟수/round trip 구분.
- [Python asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread):
  contextvars 전파 계약. 별도 queue envelope 전파는 애플리케이션 책임이다.
`account_entry_symbols_daily`와 `stock_price_references`는 `CentralRealtimeCollector`의 동일 flush에서 각각 실제 매수 체결 편입 종목과 0G 기준가격 문서를 `upsert_documents`로 기록한다. 첫 writer는 `AutonomousTop20Service._load_account_entry_codes` 및 장후 보완 대상 선정에, 둘째는 `MarketEventService._load_metadata`의 상한가 기준 조회에 쓰인다. 둘 다 단순 `central_documents` UPSERT라 reader 결과와 replay를 유지한 채 allowlist 관측을 추가했다. `theme_metadata`/`news_article`의 revision helper 및 `market_state` dataset 저장 정책은 다른 transaction 흐름이므로 제외했다. 전용 PostgreSQL `test_realtime_document_batch_preserves_readers_and_correlates_metrics`가 통과해 두 kind의 reader/replay 및 call ID correlation을 확인했다. v1 ZIP은 package initializer 누락으로 app의 구버전 모듈을 불렀으나, initializer를 포함한 v2 ZIP을 NAS에 보내 재검사해 통과했다.

## 15. 2026-10-01 shadow checkpoint 쓰기 최적화 설계

상태: **전용 PostgreSQL 계약 검사 5/5 및 현실 dataclass fixture 비교 3/3 통과. 단일 frame 변경 DML의 WAL은 약 99% 줄었지만, 전체 writer 성능 우위는 입증되지 않았고 총 column payload는 약 9.9배다. 성능 후보는 채택하지 않으며 writer 기본값 OFF와 NAS 활성 release를 유지한다. 세부 판정은 아래 2026-10-01 측정 결과를 따른다.**
공통 관측 layer의 최초 migration과 별개인 O12 성능 후속 작업이다.

### 근거와 범위

- 장중 보고서 `20261001T014152Z-b2f12614`의 60초 구간에서 checkpoint 14회,
  회당 약 1.16MB의 직렬화 입력, 매번 봉 frame 1,803개가 관측됐다.
  같은 구간 shadow evaluation 저장은 29회였다. 관측 창 경계·실패를 고려하면
  이것을 정확한 변경 frame 수로 등치할 수 없지만 작은 변경에 전체 상태를
  전송하는 구조의 개선 후보를 뒷받침한다. 입력 JSON 바이트는 물리 저장량이나 WAL 바이트가 아니다.
- `CandidateMonitor.run_once -> _consume -> _save_checkpoint ->
  PostgresQueryStore.save_shadow_monitor_state`가 writer다.
  `_restore_or_bootstrap -> load_shadow_monitor_state`가 복구 reader다.
- 새 관측을 소비하면 cursor가 바뀌므로 전체 문서 동일성 조건만으로 저장을 줄일 수 없다.
  `_trim_bars`는 해당 종목의 `lookback_bars + 2`개만 제한하고 다른 종목을 보존한다.
  늦은 수정분도 평가하므로 날짜나 현재 TOP20 밖이라는 이유로 추가 삭제하지 않는다.
- 평가/후보 ledger 저장과 checkpoint는 현재 서로 다른 transaction이다. 합치지 않는다.
  frame 보관 규칙, 전략 상태, emitted candidate keys, 원본 observation/research 이력은 변경하지 않는다.

### 선택한 저장 방식

PostgreSQL에 한정해 **복구 header와 봉 frame 행을 분리하고 DB에서 변경 행을 선별**한다.
현재 `save_shadow_monitor_state(monitor_id, document)` 및
`load_shadow_monitor_state(monitor_id)`의 논리 문서 계약은 그대로 유지한다.
SQLite 저장은 현재 형식을 유지한다. 앱에 별도 delta cache나 전역 connection을 만들지 않는다.

PostgreSQL migration 21에서 새 header/frame 테이블을 추가한다. SQLite의 지원 버전은 20으로 유지하고 inline 저장을 사용한다.

- header: monitor_id PK, storage_version, updated_at, bars를 제외한 document_json,
  원래 bars 배열의 순서를 나타내는 bar_order(JSONB).
- frames: (monitor_id, code, observation_key) PK, 원래 frame 전체의 frame_json(JSONB).
  header 삭제 시 해당 복구 frame만 정리하는 FK를 둔다. 원본 봉/observation ledger와 관계없다.
- 기존 `central_shadow_monitor_state`는 v1 복구 및 명시적 downgrade를 위해 남긴다.
  v2 존재 시 v2가 권위본이며, 남아 있는 v1을 최신 상태라고 읽지 않는다.
- v2 정규화 대상은 현재 CandidateMonitor의 schema_version=1 문서이며 bars의
  (code, observation_key)가 유효하고 유일해야 한다. 지원하지 않는 일반 문서는
  기존 inline 저장을 유지한다. v2에서 inline으로 전환할 때도 한 transaction 안에서 처리한다.
  논리 schema_version과 물리 storage_version은 별개다.

저장 순서:

1. 입력을 검증/직렬화하고 기존 관측 connection context를 연다.
2. monitor_id별 안정된 부모 행을 확보하고 잠근다. 최초 생성 경합도 같은 키로 직렬화한다.
   기존 v1 행을 잠금 기준으로 사용할 수 있으며 기존/새 writer가 서로 다른 잠금을 쓰면 안 된다.
3. 새 header와 bar_order를 저장한다. 동일한 bar_order는 기존 값을 유지한다.
4. 입력 frame을 집합으로 전달해 DB의 실제 frame_json과 `IS DISTINCT FROM`으로 비교한다.
   없거나 변경된 frame만 bulk UPSERT한다. 동일 frame은 UPDATE 대상에서 제외한다.
5. 이번 완전한 입력 문서에서 사라진 복구 frame만 집합 DELETE한다.
6. 한 번 COMMIT한다. 어느 단계든 실패하면 header/frame 변경 전체를 rollback한다.

항상 완전한 논리 문서를 입력받아 DB 상태와 대조하므로 오래된 앱 측 delta 기준이나
COMMIT 응답 유실 뒤 재시도에 의존하지 않는다. 같은 monitor_id의 저장은 섞이지 않고,
다른 monitor_id/writer의 transaction은 독립적이다. 행별 execute/commit은 금지한다.
초기 버전은 전송 JSON의 크기를 줄였다고 주장하지 않는다. 먼저 실제 변경 행과 WAL을 줄이는 범위다.

읽기는 **하나의 SQL snapshot**으로 header와 bar_order 순서의 frame을 조립한다.
여러 READ COMMITTED SELECT로 서로 다른 시점의 header/frame을 섞지 않는다.
빈 bars와 bars 필드 없음, 원래 배열 순서, 모든 frame 필드를 보존한다.
없는 v2는 v1으로 읽되, 존재하지만 frame이 누락됐거나 알 수 없는 버전인 v2는
명시적 저장소 오류로 중단한다. CandidateMonitor의 ValueError bootstrap fallback으로
이 오류를 숨기지 않는다.

### 대안과 선택 이유

- 저장 간격 확대: 장애 뒤 재생 구간/복구 보장이 달라지므로 이번 범위에서 제외.
- 오래된 종목/frame 삭제: 지연 도착 수정분과 평가 결과의 동등성을 입증하지 못했으므로 제외.
- revision ID만 저장 후 원본에서 재구성: 복구가 별도 원본 보존/조회 계약에 의존하므로 제외.
- JSONB 내부 일부만 수정: SQL 표현만 바꿔 큰 값의 물리 쓰기가 줄었다고 볼 수 없으므로 제외.
- 봉별 저장: 실제 유지 중인 frame 자체를 보존하면서 작은 변경만 쓰고,
  기존 복구 문서를 정확히 다시 만들 수 있어 선택. 추가 SQL/인덱스 비용은 전용 DB에서 검증한다.

PostgreSQL은 변경되지 않은 큰 컬럼 값을 보존할 수 있지만, 내부 JSON 필드 변경을
컬럼 불변과 동일시하면 안 된다. 근거: [PostgreSQL 17 TOAST](https://www.postgresql.org/docs/17/storage-toast.html).

### 호환·배포 조건

- PostgreSQL 지원 migration은 21, SQLite는 20으로 유지한다. `central_schema_migrations(dialect)`가
  해당 backend의 연속된 지원 계획을 선택하고 기존 runner는 그대로 사용한다.
  SQL이 없는 migration도 기록하는 runner 특성 때문에 PostgreSQL 전용 21번을 SQLite에
  전달하지 않는다. SQLite inline checkpoint와 기존 20번 소스의 복귀 호환성을 보존한다.
- 먼저 v1 fixture -> v2 저장 -> 동일 문서 복원 검증을 전용 DB에서 수행한다.
  운영 startup에서 기존 모든 checkpoint를 한꺼번에 변환하지 않는다.
- v2 reader, writer, migration, 테스트, downgrade 변환과 source-runtime 보호가
  완성되기 전에는 운영에서 v2 쓰기를 켜지 않는다. 현재 `shadow_checkpoint_frames_enabled`
  기본값은 꺼져 있다.
- 현행 source-runtime은 health 실패 시 이전 release를 다시 선택한다. 구버전은
  v2 reader가 없으므로 수동 rollback과 자동 복귀 양쪽 모두 호환 검사를 추가해야 한다.
  v2가 있는 상태에서 이를 읽지 못하는 release로 그대로 재시작하면 안 된다.
- downgrade는 checkpoint writer가 완전히 종료된 상태에서 최신 v2를 정확한 v1 문서로
  원자적으로 복원하고 v2 권위본을 해제한 뒤 이전 release를 실행한다.
  live writer와 동시 변환하지 않는다. source 변경 전 호환 검사/변환 실패는 안전하게 중단한다.
  남겨 둔 오래된 v1에서 재생하면 되리라는 가정으로 rollback을 허용하지 않는다.
- 별도 제품 정책/공개 API/시장 자료 포맷 변경, durability 완화, global queue 도입은 없다.

### 구현·검증 완료 조건

1. 단위/전용 PG: v1 읽기, v2 첫 저장, cursor-only 변경, 한 봉 변경, eviction,
   지연 수정분, 빈 배열, 배열 순서, 일반 inline 문서, 재시작 결과 동등성.
2. frame 저장 중 고장으로 header까지 rollback되는지, COMMIT 응답 유실 재시도,
   동일 monitor 동시 저장에서 혼합 snapshot이 없는지, 다른 monitor 독립 commit.
3. 기존 checkpoint 실패 뒤 candidate/decision 중복 방지 회귀와 source-runtime
   수동/자동 복귀의 v2 보호 및 v1 복원 검사.
4. 기존/new 경로 모두 같은 1,803개 fixture에 cursor-only/1개 변경/여러 개 변경을 적용.
   초기 적재와 정상 갱신을 분리하고 실행 순서를 교대한다. SQL 수, 실제 변경 frame 수,
   입력 bytes, execute/commit, 물리 크기와 WAL을 기록한다.
5. WAL은 전용 DB의 해당 DML에 `EXPLAIN (ANALYZE, WAL, BUFFERS, FORMAT JSON)`을
   적용해 statement 발생량을 비교할 수 있다. 실제 DML 실행이므로 운영에서는 하지 않는다.
   COMMIT record/지연과는 구분하고 공유 NAS 전체 WAL delta를 writer 전용 수치로 쓰지 않는다.
   근거: [PostgreSQL 17 EXPLAIN](https://www.postgresql.org/docs/17/sql-explain.html).
6. 이 저장 방식의 성능 후보는 전용 DB에서 전체 writer 이점이 확인되지 않아 운영 적용 단계로
   진행하지 않는다. 추후 별도 저장 용량/복구 목적 때문에 다시 채택을 검토한다면 source-runtime
   후보 호환성 검사와 별도 운영 승인을 거친 뒤에만 장중 동일 지표를 비교한다. 전용 DB에서
   WAL이 줄어도 운영 COMMIT spike의 원인이 제거됐다고 미리 결론내리지 않는다.

현재 성능 평가 단계는 운영 성능 gate 미충족으로 종료한다. 전용 PostgreSQL 회귀 및 1,803개
frame fixture 비교는 완료했으며, 운영 배포·장중 재측정은 이 후보를 채택하지 않으므로 진행하지 않는다.

### 2026-10-01 비교 결과와 현재 판정

전용 `kiwoom_monitor_diagnostic_test`에서 실제 dataclass 1,803-frame fixture로 계약/회귀 및
비교 검사 3/3이 통과했다. 단일 변경 DML의 WAL은 inline 151,643 bytes/213 records에서
frame 1,499 bytes/8 records로 줄었고 DML 실행 합은 28.321ms에서 23.059ms였다. 이는 해당
전용 DB의 DML 측정에 한정되며 COMMIT 지연을 설명하지 않는다.

public writer 비교는 각 모드·변경량당 3회였다. inline 2 SQL에 비해 frame writer는 5 SQL을
실행했고 execute median은 inline 약 68ms, frame 약 73~77ms였다. cursor-only/1-frame/50-frame의
median total은 각각 inline 184/264/269ms, frame 2,644/269/275ms였다. 양쪽 모두 수 초 COMMIT
outlier가 있어 작은 표본으로 차이를 원인에 귀속할 수 없다. legacy downgrade fallback까지
포함한 column payload는 inline 약 138.5KB, frame v2 전체 약 1.368MB(약 9.9배)이며 index와
relation overhead는 포함하지 않았다.

현재 판정은 성능 개선 미입증이다. `shadow_checkpoint_frames_enabled`는 기본 OFF로 유지하며
운영 활성화/배포를 진행하지 않는다. WAL 감소는 측정된 장점이지만 추가 SQL·execute 비용 및
column payload 증가를 고려하면 현재 결과만으로 채택할 근거가 부족하다. 다른 저장 형식 목적을
검토하지 않는 한 이 성능 후보의 구현·검증 단계는 닫고, 운영 장중 효과는 미확인으로 남긴다.

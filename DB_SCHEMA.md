# 데이터베이스 스키마 지도

2026-09-24 과거 검색뉴스 로컬 캐시: `data/historical_intelligence.sqlite3.news_article_body_snapshots`는 `(provider, office_id, article_id, extractor_version, body_sha256)`를 기본키로 하여 발행시각 확인에 사용한 동일 HTML에서 추출한 `body_text`, `source_url`, `published_at`, `fetched_at`을 불변 보존한다. 기존 `news_articles`나 NAS 본문 revision을 덮어쓰지 않는다. PC 역사 BODY 작업은 해당 extractor 버전의 최신 스냅샷을 우선 읽고 없을 때만 원문을 조회한다. NAS의 `central_news_jobs` 완료 상태가 재처리를 방지하며 NAS 실시간 뉴스 작업과는 별개다.

기준: 2.1.0 / 2026-09-22. 이 문서는 테이블·마이그레이션 상세 참조다. 연구 현재 버전은 v27이며 아래 도입 버전은 이력이다. [현재 상태](docs/CURRENT_STATUS.md)와 [남은 작업](docs/OPEN_ITEMS.md)으로 구현·운영 범위를 구분한다. 신규 대신/뉴스 백필의 입력 저장 계약은 [수집 기획](docs/HISTORICAL_BACKFILL_PLAN.md) 단계에서 확정한다.

CR3d1 연구 v18은 final_holdout_access_ledger migration으로 아래 두 표를 추가한다. CR3d2b 연구 v19는 소유권 표를, CR3d2c 연구 v20은 세대와 복구 감사 표를 추가한다. CR4a 연구 v21은 불변 자동 가설과 부모 계보 표를, CR4b 연구 v22는 campaign별 가설 큐를, CR4c 백엔드는 v23 후속 생성 원장을 추가한다. 기존 v17의 run/report/campaign과 migration 이력은 그대로 유지한다.

- research_final_holdout_windows: window_id PK, UTC start/end, FINAL_RESERVED 또는 EXPOSED_DEVELOPMENT state, unique batch_id, immutable spec_json, created_at.
- research_final_holdout_events: event_id PK, window_id FK, globally unique request_id, FINAL_ACCESS 또는 EXPOSED_DEVELOPMENT event_type, accessed_at, reason. window/time/event index로 제한 조회한다.
- research_final_holdout_executions(v19, v20 generation 추가): execution_id PK, window_id FK, batch_id+candidate_spec_hash unique, run_id unique FK, owner_token, RUNNING/COMPLETED/FAILED/CANCELLED state, started/finished, logical result hash, reason, 1부터 증가하는 generation.
- research_final_holdout_recoveries(v20): globally unique request_id PK, execution_id FK, owner_token, reason, REQUESTED/CLAIMED state, requested/claimed 시각, 허가하는 다음 generation. execution+generation도 unique인 append-only 감사 기록이다.
- research_hypotheses(v21): 콘텐츠 hash `hypothesis_id`, 등록 Family, READY 상태, 생성 시각과 canonical 가설 문서. 같은 ID의 다른 문서는 거절한다. 미등록 계산식 draft는 아직 저장하지 않는다.
- research_hypothesis_parents(v21): hypothesis별 순서 있는 parent edge. 양쪽 모두 가설 FK이며 같은 parent 중복과 부모 없는 저장을 거절한다.
- research_campaign_hypotheses(v22): campaign+가설 PK, AVAILABLE/ENQUEUED 상태, 등록/예약 순서와 시각, 대응 campaign job FK. scope가 다른 가설과 같은 가설의 두 번째 job을 거절한다.
- research_campaign_hypothesis_expansions(v23): campaign+부모 가설+개발 근거 ID+campaign policy revision PK, GENERATED/EXHAUSTED/BLOCKED 상태, 생성 수·이유·개발 전용 evidence snapshot과 생성 시각. 완료 job에 연결된 부모만 활성 worker가 현재 revision으로 기록한다.

원장 mutation은 BEGIN IMMEDIATE로 batch/overlap/nonce와 event 시각을 확인하며, 노출 상태를 되돌리거나 기록을 삭제하는 API는 없다.
동일 request의 완전 일치만 멱등이고 내용 변경은 오류/롤백한다. 창 identity에 dataset revision/profile을 포함하지 않아 같은 KRX 기간의 우회 재사용을 차단한다.
기존 read-only 비교는 v17부터 v27까지 허용하며 마이그레이션/DB 생성 없이 조회한다. 접근 원장은 v18, final 실행 소유권은 v19, 명시 복구는 v20, 순차 검증 소유권은 v24 표를 사용한다.
기존 본문의 v17-only 설명은 CR3b 당시 계약 기록이며 현재 비교 reader는 위 두 버전을 지원한다.

CR3d2a는 migration/column 없이 v18 원장 동작만 확장한다. 최종 준비 경로의 FINAL_ACCESS 전에 같은 BEGIN IMMEDIATE에서
research_runs의 input_manifest_json.captured_range와 spec_json.evaluation_spec의 fold/warmup 합집합을 순차 검사한다.
연속 실행은 보고서 fold 사이/밖의 실제 입력도 소비하므로 captured_range를 생략하지 않는다. 실제 범위가 없거나 malformed/unknown이면 미사용으로 간주하지 않는다.
성과/report/outcome 표는 이 검사에서 읽지 않는다. 기존 record_final_holdout_access 호출은 check_development_history=False 기본값으로 호환되고,
CR3d2a 준비 경로만 True를 사용한다. CR3d2b가 실제 final 실행 소유권/상태를 v19 migration으로 추가했다.

CR3d2b v19 claim은 locked window/batch/candidate와 final runtime/spec/run ID를 검사하고 research_runs RUNNING 행과 execution 소유권을
같은 BEGIN IMMEDIATE에서 생성한다. 같은 후보의 동시 claim은 하나만 CLAIMED이고 나머지는 BUSY다. terminal 행은 자동으로 RUNNING으로 되돌리지 않는다.
immutable output manifest가 게시되고 research run이 completed/hash 일치한 뒤에만 final execution을 COMPLETED로 확정한다.
FAILED/CANCELLED는 reason과 research run terminal을 같은 트랜잭션에 기록한다. terminal 내용은 immutable이다.
RUNNING execution이 있는 window는 EXPOSED_DEVELOPMENT로 바꿀 수 없다. 완료 cache는 run/report/output identity를 모두 대조한다.

CR3d2c v20 recovery 요청은 일관된 FAILED/CANCELLED execution에만 append한다. 요청 자체는 execution/run 상태를 바꾸지 않는다.
같은 request ID/실행/owner/reason은 멱등이고 하나라도 다르면 충돌이다. 한 execution의 같은 다음 generation에 요청을 미리 둘 이상 만들 수 없다. claim은 FINAL_RESERVED와 같은 run/spec/input을 다시 확인하고
research_runs와 final execution을 같은 트랜잭션에서 RUNNING으로 되돌리며 generation을 1 올리고 recovery를 CLAIMED로 바꾼다.
이미 CLAIMED인 요청은 새 실행권이 아니며 다음 복구에는 새 요청이 필요하다. output manifest 부재 검사는 run storage를 아는 application 경계에서 선행한다.
COMPLETED/RUNNING/미실행/노출 창은 복구하지 않으며 RUNNING orphan의 lease 회수는 이 migration 범위 밖이다.

CR3b3 순차 검증은 기존 research_runs/spec_json을 유지한다. 후속 v24 `research_independent_run_owners`와 `research_independent_run_owner_history`는 새 UI 순차 실행의 run별 현재 owner token·generation과 재사용 금지 이력을 추가한다. 기존 무소유 run은 소급 소유자로 간주하지 않는다.
spec.execution_scope=independent_development_validation/v1은 기존 단일 실행과 구분하는 scientific ID 범위다.
start_run(claim_independent=True)은 BEGIN IMMEDIATE로 신규/취소 행을 선점하며 running/failed/완료를 보존한다. 새 UI claim은 owner token을 기록하고 같은 run에서 이전 토큰 재사용을 거부한다.
새 scope의 예외 종료는 batch가 한 번 확정한다. 독립 순차 run의 신규 생성과 취소 run 재선점은 일반 start_run 호출로 우회할 수 없다.

CR3b2 연구 결과 비교는 기존 연구 v17을 `ResearchRepository(path, read_only=True)`로 연다.
SQLite mode=ro/timeout 1초이며 새 DB/폴더/마이그레이션/테이블을 만들지 않는다.
이미 마이그레이션된 v17~v27을 허용하며 기본 생성자의 쓰기/마이그레이션 동작은 유지한다.

R3g 선택 계좌 모의 주문도 기존 intent/event 원장을 사용한다(중앙 v19 유지).
새 `manual_mock_scoped_` intent ID는 environment/account_ref/run/request_id로 분리하고,
기존 v1 `manual_mock_` 행과 ID 계산은 변경하지 않는다. 조회·취소는 저장 intent의 account_ref/run을 검증한다.
페이지 cursor와 owned query는 bundle별 메모리 상태이며 새 DB 테이블은 추가하지 않았다.

스키마의 실행 원본은 각 `CREATE TABLE` 코드다. 이 문서는 수정 범위 파악용이다. 로컬 SQLite 마이그레이션 실행기는 `infrastructure/persistence/schema_migrations.py`에 있다. 메인 DB는 v8, 매매일지 DB는 v9다. 메인 v2와 매매일지 v1은 기존에 멱등적 `CREATE TABLE`·호환 `ALTER TABLE`로 형성된 스키마의 기준선이고, v3에서 공통 시장 관측 메타데이터 표를, v4에서 종목별 당일 실제 상한가 가격 캐시를, v5에서 프로필별 테마 이름 결정 원장을, v6에서 AI 테마 제안 검토 원장을, v7에서 제안 판단에 필요한 기사 제목·발행시각·원문 URL을, v8에서 메인→뉴스 DB 데이터 이관 완료 원장을 추가했다. 기존 시장 데이터 행의 시각·출처를 추정해 변환하지 않는다. 이후 필드 변경은 시작 코드에 임의 SQL을 더하지 말고 다음 연속 버전의 명시적 마이그레이션과 이전 DB fixture를 함께 추가한다.

마이그레이션 원장은 `version`, 변경 불가한 `name`, `applied_at`을 저장한다. 실행기는 버전 연속성과 이름 일치를 검사하고 각 변경을 savepoint 안에서 수행한다. 실패한 변경은 DDL과 원장을 함께 되돌리고, 앱이 지원하는 버전보다 새로운 DB는 조용히 열지 않고 오류로 거부한다. 중앙 SQLite/PostgreSQL도 동일한 중앙 마이그레이션 계획을 각 DB 방언의 한 트랜잭션에서 실행한다.

## NAS 누적 보존 원칙

`central_second_trade_bars`, `central_minute_bars`, `central_daily_bars`, `central_dataset_snapshots` 및 TOP20 편입 문서는 자동 삭제하지 않고 같은 키의 관측만 갱신한다. `central_api_query_cache`는 만료 자료를 정리하고 `central_realtime_latest`는 최신 상태만 보유하므로 NAS의 모든 테이블이 append-only인 것은 아니다.

`GET /api/v1/diagnostics/resources`의 분류별 저장량은 테이블·인덱스 물리 크기와 `central_documents` 문서 크기 비율을 이용한 운영 진단 추정값이다. 삭제 가능 여부를 뜻하지 않으며, 보호 참조 기반 보존 정책이 확정되기 전에는 자동 정리에 사용하지 않는다.

- `top20_membership`: 30초 관측별 실제 구성과 키움 원본 행
- `top20_index`: 1분 거래대금 합계, 시장별 값, 구성 종목과 코호트
- `top20_daily_entrants`: 거래일 중 한 번이라도 편입된 모든 종목
- `market_data_coverage`: 거래일·종목·시장별 분봉 장후 보완 완료 근거
- `market_data_coverage_daily`: 종목·시장별 최근 250일 일봉 보완 기준일

## 로컬 메인 DB: `monitor.sqlite3`

| 분류 | 테이블 | 역할/주요 키 |
| --- | --- | --- |
| 메타 | `schema_migrations` | 메인 스키마 버전·이름·적용 시각. 현재 v8 |
| 이관 완료 원장 | `legacy_news_transfer_migrations` | 메인 DB의 옛 뉴스 표를 전용 `news.sqlite3`로 옮긴 데이터 이관 v1 완료 시각. 대상 저장·건수 확인 뒤 원본 표 제거와 같은 메인 DB 트랜잭션에서 기록 |
| 관측 메타 | `market_data_observation_meta` | `(dataset_kind, subject, observation_key)`별 시장 기준/가용 시각·시장·단위·실제/추정·완결성·출처·후보군. 신규 실시간/조회 분봉·일봉과 같은 트랜잭션으로 저장 |
| 설정 | `settings`, `central_setting_versions`, `column_settings` | 앱 값, 중앙 병합 시각, 표 열 구성 |
| 종목 | `stocks` | `code` PK, 이름·시장·기본정보·신고가/NXT·당일 상한가 가격 캐시 |
| 종목명 | `stock_aliases`, `stock_name_history`, `kind_name_disclosures` | 별칭, 변경 이력/승인, KIND 확인 대기 |
| 테마(구형/호환) | `themes`, `stock_themes` | 테마와 종목 N:M |
| 테마 프로필 | `theme_profiles`, `profile_themes`, `profile_stock_themes` | 프로필 → 테마 → 종목 N:M |
| 테마 이름 결정 | `profile_theme_name_decisions` | 프로필별 별칭(`alias`), 분리 확장(`split_to`), 재병합 금지(`keep_separate`)와 결정 출처·갱신 시각 |
| AI 테마 검토 | `profile_theme_suggestions` | 프로필별 기사 identity·제목·발행시각·원문 URL, 종목·원시 테마·근거·확신도와 pending/approved/rejected, 사용자가 승인한 적용 이름 |
| 신고가 | `new_high_snapshot`, `new_high_snapshot_meta`, `intraday_highs`, `historical_high_evidence` | 기간 목록, 당일 고가, 수정주가 근거 |
| 원시/보정 봉 | `minute_bars`, `daily_bars` | 종목·날짜·분/일 OHLCV와 거래대금 |
| 지수 봉 | `market_index_minute_bars`, `market_index_daily_bars` | KOSPI/KOSDAQ OHLCV·거래대금 |
| 파생 지표 | `top20_trade_value_index` | 분별 TOP20 합계, 시장별 분해, 구성 종목/구간, 수집 상태 |
| 파생 통계 캐시 | `top20_statistics_daily_cache` | PC 직접 연결의 완료일 시간대 합계·표본 수·정규장 합계·시장 분모. 원본 보완 시 해당 날짜를 무효화하고 다음 조회에서 재생성 |
| 수집 상태 | `minute_history_sync_log`, `daily_bar_sync_log`, `market_data_finalization_log`, `market_data_unconfirmed_log` | 백필·확정·최종 실패 상태 |

`stocks.code`가 테마·신고가의 논리적 부모다. 일부 봉 테이블은 성능과 과거 호환 때문에 외래키를 강제하지 않는다. `top20_trade_value_index`는 원시 체결이 아니라 순위 코호트와 분봉에서 계산된 파생 데이터다.

NAS `central_dataset_snapshots(kind='top20_statistics_day')`는 완료된 날짜의 TOP20 시간대·정규장 파생 집계다. `top20_index` 또는 `market_index_chart`가 같은 날짜에 다시 저장되면 같은 트랜잭션에서 해당 집계를 삭제하고 다음 통계 조회에서 재생성한다. 당일은 캐시하지 않는다.

`themes/stock_themes`는 프로필 기능 도입 전 자료와 구형 설정·테마 백업 복원을 위한 호환 원본이다. 최초 `theme_profiles_initialized` 이전 때 기본 프로필로 한 번 이전하며, 현재 테마 편집과 새 설정/Google/NAS 백업의 쓰기 원본은 `theme_profiles/profile_themes/profile_stock_themes`다. `profile_theme_name_decisions`는 이후 가져오기와 LLM 제안이 같은 사용자 결정을 재사용하도록 이름 관계를 프로필 범위로 보존한다. 새 백업은 활성 프로필, 이름 결정과 테마 가져오기 규칙을 함께 보존한다. 두 구조를 활성 이중 쓰기로 맞추거나 구형 표를 바로 삭제하지 않는다.

## 로컬 뉴스 DB: `news.sqlite3`

| 테이블 | 역할/관계 |
| --- | --- |
| `news_schema_migrations` | 뉴스 스키마 버전·이름·적용 시각. 현재 v4 |

뉴스 DB 시작 경로는 `news_schema_migrations`의 v1~v4 이름·버전이 모두 일치하면 스키마 변경 실행기를 건너뛴다. 새 버전이나 불완전한 원장은 기존 변경 실행기가 처리한다. 옛 메인 DB 뉴스 표의 별도 이관은 `legacy_news_transfer_migrations` v1이 있으면 재실행하지 않는다. 완료 표식이 있는데 전용 뉴스 DB 파일이 없거나 메인 DB에 옛 뉴스 표가 다시 나타나면 빈 DB 생성·재이관 대신 오류로 알린다.
| `stock_news` | `(stock_code, identity)` PK. 제목·요약·링크·게시시각·기본 판정 |
| `stock_news_sync` | 종목별 전체/네이버 마지막 확인 시각 |
| `journal_news_links` | origin/canonical 계좌 scope와 `(group_id, stock_code, identity)`로 계좌별 복기 묶음과 기사 연결. v3는 `is_deleted/updated_at` tombstone과 source collection/owner/key/content hash를 보존 |
| `stock_news_ai` | 기사/사건 identity별 종목 영향 분석과 근거 있는 원시 `theme_candidates`; `stock_news`와 논리 연결 |
| `news_ai_shared` | 종목 간 재사용 가능한 사건 분석 |
| `news_ai_requests` | 공급자·모델·요청 모드·기사 수·토큰 사용량 원장 |

뉴스 v3는 기존 origin과 `linked_at`을 보존한 채 활성 상태로 이전하고 이후 삭제를 tombstone으로 유지한다. 과거 행에서 복원할 수 없는 source owner/hash는 `unknown`으로 보존한다. v4는 기존 AI 결과를 유지하면서 테마 후보 JSON 열을 빈 배열 기본값으로 추가한다.

`stock_news`는 수집 원본에 가까운 데이터, AI 세 테이블은 계산/파생 데이터다. 뉴스 DB는 메인 DB에서 물리적으로 분리되어 메인 실시간 SQLite 잠금을 줄인다.
실행 스키마는 `infrastructure/persistence/news_schema.py`가 단일 소유하며 `StockNewsRepository`와 `NewsAIRepository` 어느 쪽을 먼저 열어도 같은 v4까지 적용한다.

## 로컬 매매일지 DB: `journal.sqlite3`

실행 스키마 위치: `infrastructure/persistence/journal_schema.py`. 저장·조회 구현은 `journal_database.py`에 남아 있다.

| 분류 | 테이블 | 역할/주요 키 |
| --- | --- | --- |
| 메타 | `journal_schema_migrations` | 매매일지 스키마 버전·이름·적용 시각. 현재 v9 |
| 동기화 상태 | `journal_sync_states` | `(collection, origin owner, document_key)`별 계좌 scope·활성/삭제 상태·갱신 시각. v8은 같은 group/key의 real/mock tombstone을 분리한다. v7의 v1 collection은 `legacy` scope로 보존하고 복원 불가능한 v2 scope만 `unknown`으로 격리한다 |
| 구형 수입 원장 | `journal_legacy_imports` | v1 원본 collection/owner/key/정규 content hash, 관측·수정 시각, 로컬 대상과 IMPORTED/CONFLICT 상태. v7에서 알 수 없던 owner/hash는 `unknown` 유지 |
| 관측 메타 | `market_data_observation_meta` | 메인 DB와 동일한 관측 의미 계약. 신규 부분/확정 분봉·일봉 및 체결 스냅샷 필드별 `entry_context`와 같은 트랜잭션으로 저장하며 확정·장후 보완값은 임시값으로 강등하지 않음 |
| 원시 체결 | `trade_fills` | origin broker/environment/account UUID를 포함한 `fill_key` PK와 canonical account ref. 기존 행은 기존 fill key를 유지한 `legacy-unassigned`, 신규 계좌 행은 `fill:v2` hash. 저장 구현은 `journal_trade_repository.py` |
| 실제 비용 | `daily_trade_costs` | origin 계좌 scope·체결일·종목·방향별 정산금액/수수료/세금과 canonical account ref. 저장 구현은 `journal_trade_repository.py` |
| 사용자 데이터 | `trade_reviews`, `trade_group_overrides` | 계좌 origin/canonical scope를 가진 묶음 복기와 수동 묶음. 기존 group/fill 참조는 유지. 저장 구현은 `journal_trade_repository.py` |
| 종목 목록 | `journal_stocks` | 날짜·종목별 열람/수집 대상 |
| 봉 캐시 | `journal_minute_bars`, `journal_daily_bars`, `journal_bar_backfill` | 차트용 로컬 캐시와 확정 상태. 저장·메인 DB 가져오기 구현은 `journal_bar_repository.py` |
| 설정/전략 | `journal_settings` | UI 설정, 개인원칙, 전략팩·강의 추출 초안·버전 JSON. 전략 관련 저장 구현은 `journal_strategy_settings_repository.py` |
| 파생 분류 | `trade_setup_classifications`, `trade_setup_cycle_overrides` | 계좌별 자동 유형과 묶음/회차 수동 수정. 저장 구현은 `journal_trade_repository.py` |
| 시점 원본 | `trade_entry_snapshots` | 계좌별 체결 순간 순위·대금·테마·신고가·뉴스·수급·호가·시장 JSON. 신규 키는 `snapshot:v2`이고 기존 execution key는 유지. 저장·보완 구현은 `journal_snapshot_repository.py` |
| 자동보완 상태 | `journal_enrichment_tasks` | 대상·종류·입력 지문·정책 버전별 상태. 실행 owner와 다음 재시각은 PC 로컬이며 중앙 콘텐츠로 동기화하지 않음 |
| 파생 분석 이력 | `journal_analysis_revisions` | 사용자 복기와 분리한 입력 지문·분석 버전별 기계 판정 revision. 같은 입력과 버전은 중복 저장하지 않음 |
| 연구 근거 연결 | `journal_research_links` | 실제 체결 참조와 연구 run/decision/snapshot/진입가설 ID의 불변 연결. 과거·수동 거래는 decision ID가 없을 수 있음 |
| 실행 projection 상태 | `journal_execution_projection_states` | 중앙 mock 실행 원장의 계좌별 마지막 `accepted_sequence` cursor. projection 행과 같은 트랜잭션으로만 전진 |
| 실행 체결 근거 | `journal_execution_event_projections` | 상세 `FILL`과 수량만 가진 `BROKER_FILL_AGGREGATE`의 불변 파생 원장. source event ID와 계좌·KST 거래일·broker 주문/체결 ID를 각각 멱등 키로 사용 |

매매 묶음/회차 자체는 체결과 `trade_group_overrides`에서 계산되며 별도 고정 테이블이 아니다. 자동 분류는 재계산 가능한 파생값이지만 수동 수정은 사용자 원본이므로 보존해야 한다. 매매일지 v4는 기존 사용자 행을 변경하지 않고 자동보완 원장·파생 revision·연구 근거 링크를 추가했다. v5 `account_scoped_trade_ledger`는 기존 체결·비용을 `legacy-unassigned`로 옮기고 기존 `fill_key`와 수동 묶음 참조를 보존한다. v6 `account_scoped_journal_artifacts`는 계좌 scope를 추가한다. v7은 legacy 수입 원장을 만들고 v8은 owner/content hash와 관측·수정 시각 및 충돌 상태를 추가한다. v9는 중앙 실행 원장의 상세 체결과 누적 수량 표시를 분리해 저장하고 계좌별 cursor를 원자 확정한다. A5c read projection은 새 표 없이 v9 상세 FILL과 `trade_fills`를 canonical 계좌·거래일·주문·종목·매수/매도로 대조하며, 상세가 있으면 요약을 더하지 않는다. `SUMMARY_ONLY`·`PARTIAL`·`CONFLICT`는 미확인이라 후속 확정 손익에서 제외해야 한다. 이 두 v9 표는 중앙 원장에서 다시 만들 수 있는 파생 자료라 `journal_v2_*` 콘텐츠 동기화에는 추가하지 않으며 전체 SQLite 백업에는 포함된다. filled/captured 시각은 수정 revision으로 사용하지 않는다.

## 로컬 연구 DB: `research.sqlite3`

실행 스키마와 저장 구현은 `infrastructure/persistence/research_repository.py`가 소유한다. v1 `research_run_ledger`부터 v20 `explicit_final_holdout_recovery`까지의 원장을 보존하고, v21 `immutable_research_hypothesis_lineage`가 가설 문서와 parent edge를, v22 `campaign_hypothesis_queue`가 campaign별 단방향 실행 바인딩을, v23 `campaign_hypothesis_expansion_ledger`가 완료 부모의 후속 생성 결과를, v24 `independent_development_run_ownership`이 새 순차 UI 실행의 소유 토큰·세대를 추가한다. v25 `opt_in_daily_development_expansion`은 기존 source에 `rolling_daily` 기본 0을 추가하고 v26 `nas_rolling_daily_cursor`는 다음 NAS 날짜 cursor를, v27 `nas_rolling_empty_day_rechecks`는 NAS 관측 0건 평일의 재확인 원장을 추가한다. v21~v27은 기존 run/report/campaign/job/trial/final 행을 변경하지 않는다. v20은 final execution generation과 명시 복구 이력을, v19는 후보별 final execution 소유권을, v18은 final 접근 원장을 소유한다. v17은 campaign job에 source_request_json 기본 빈값만 추가하며 독립 구간의 원본 spec은 이 필드에, 유효 실행 spec은 기존 request_json에 보존한다. 실제·모의 체결을 기존 `trade_fills`에 넣지 않는다. 이전 실행기로의 DB downgrade는 지원하지 않는다.

| 테이블 | 역할/주요 키 |
| --- | --- |
| `research_schema_migrations` | 연구 스키마 버전·이름·적용 시각. 현재 v27 |
| `research_campaign_staging_cleanups` | v16 operation_id PK/FK, source_id FK, 임시 경로/marker hash, READY/DELETED/MISSING/PROTECTED/FAILED, 실패 횟수/다음 재시도/예상 파일 byte/이유/갱신시각. 작업별 한 행. 삭제 전 intent를 보존하고 marker 마지막 삭제로 중단을 재개. 실패는 60초부터 최대 1시간 backoff이며 수집 실패와 별도. 완성 자료는 삭제하지 않음 |
| `research_campaign_input_sources` | source_id PK(캠페인+기준 job), campaign/template FK, 명시 상위 폴더·enabled·동결 scope JSON, READY/BACKOFF/NEEDS_ATTENTION/WAITING_STORAGE, 독립 실패 횟수·다음 scan/이유. v14 nas_auto_prepare 기본 OFF, PC NAS config 경로, remote signature. v15 storage_cap_bytes 기본 0 무제한. v25 rolling_daily 기본 OFF이며 준비된 일별 입력에서 단일 TRAIN/VALIDATION 평가 날짜만 확장한다. v26 `rolling_next_start`는 NAS 자동 준비를 함께 켰을 때 다음 조회 날짜를 작업자 소유권으로 저장한다. 용량/다른 준비 대기는 실패 횟수 0, 60초 후 확인. 토큰 없음. 설정은 일시정지/worker 종료 후 변경 |
| `research_campaign_storage_operations` | v15 operation_id PK, source/campaign FK, root/cap/owner/generation, PREPARING/PUBLISHED/UNCHANGED/BLOCKED/FAILED/CANCELLED/ABANDONED, 시작·종료, staging_path/input_path/이유. 기존 worker lease로 DB당 한 자료 준비만 허용. 오래된 작업은 ABANDONED 기록만 하며 삭제하지 않음. sidecar operation_id로 파일과 대조. 변경 없는 signature 확인에는 새 행을 만들지 않음 |
| `research_campaign_input_acceptances` | (source_id,evidence fingerprint) PK, (source_id,input_path) UNIQUE, 등록 job_id와 manifest hash. 기준 입력도 seed하여 중복 근거를 차단. job/budget/acceptance는 한 트랜잭션 |
| `research_campaign_rolling_empty_days` | v27 (source_id,range_start) PK, NAS 관측 0건 평일의 시도 횟수·마지막 확인·다음 재확인 시각. 새 날짜 cursor와 같은 거래일 조회 후 원자 기록하며 최소 1일 뒤 재확인한다. 완료된 입력이 acceptance에 등록된 뒤에만 삭제하고, 재확인은 전체 source에서 10분에 최대 한 번이다 |
| `research_campaign_workers` | campaign_id PK/FK, 단조 증가 generation, 현재 owner/lease, IDLE/STARTING/RUNNING/FAILED/NEEDS_ATTENTION, 연속 실패 횟수, 다음 재시도 시각/이유. 시작/완료/만료 기록은 원자 처리 |
| `research_campaign_worker_attempts` | (campaign_id,generation) PK, owner, 시작 시 campaign policy revision, STARTING/RUNNING/EXPECTED_EXIT/FAILED, 시작/종료 시각·exit_code·이유. 중복 종료는 재기록하지 않음 |
| `research_campaigns` | campaign_id, 현재 설정 revision, desired_state(RUNNING/PAUSED/STOPPED), operational_state/이유, cycle_sequence, 생성/갱신 시각 |
| `research_campaign_revisions` | (campaign_id,revision)별 불변 canonical policy JSON. active backlog·실패 재시도/백오프와 아직 OFF인 자동 최종평가/가설 생성 정책 |
| `research_campaign_jobs` | (campaign_id,job_id)별 기존 search job 참조, 동결 실행 ExperimentSpec/request_json, 불변 원본 source_request_json(v17; 기존 v1은 빈값), 로컬 input_path, 현재 budget_revision, source_kind, 상태, attempt/실패 count, owner/generation/lease, 다음 재시도 시각, 등록 순서. 완료 작업은 backlog에서 제외. reason의 trial_budget_exhausted/budget_expansion_required로 예산 소진을 구별 |
| `research_campaign_job_budgets` | (campaign_id,job_id,revision)별 불변 canonical 운영 예산 JSON(max_trials/max_seconds/resource_budget)/created_at. 과학 명세/원래 request_json은 변경하지 않음 |
| `research_campaign_cycles` | (campaign_id,sequence)별 예약 attempt와 해당 정책 revision/budget_revision/job/generation/owner, 종료 상태/이유. cycle 생성과 sequence/claim 증가를 단일 BEGIN IMMEDIATE 트랜잭션으로 저장 |
| `research_runs` | 고정 dataset manifest와 Factor/전략/정책/파라미터, 코드 hash, 실행 상태와 논리 결과 hash. S5 신규 RunSpec은 버전 있는 session profile 문서를 포함하고 필드가 없는 기존 run은 기존 ID/hash를 유지. 같은 run ID의 입력은 변경 불가 |
| `research_independent_run_owners` | v24 이후 순차 개발 검증 UI가 claim한 run의 현재 owner token·세대·claim 시각. 기존 run은 채우지 않음 |
| `research_independent_run_owner_history` | 같은 run에서 이전 owner token을 다시 쓰지 못하도록 generation별 claim을 보존 |
| `research_feature_snapshots` | 판단 시각·입력 cutoff·당시 universe·Factor 값/결측 사유·입력 revision·판단 전 상태 |
| `research_decisions` | Snapshot별 proposal/final action, 이유·제약·판단 전후 상태 |
| `research_candidate_events` | 종목+setup+기준 관측+전이+전략 버전의 중복 키를 가진 후보 사건. run 안에서 같은 키는 한 번만 저장 |
| `research_execution_events` | 모의 주문 제출·거부·취소/검열, 체결, 위험조건 체결, mark와 당시 현금·예약금·포지션. 실계좌 주문 ID는 사용하지 않음 |
| `research_outcome_labels` | 후보별 T+1초/1·3·5·10분 결과와 COMPLETE/PENDING/CENSORED/UNSUPPORTED 상태, 사용 봉 revision |
| `research_run_evaluations` | 비용 모델 적격성, 완료 모의 거래 수, 총/순 실현손익과 비용·수익률, 동일봉 낙관 추가범위 |
| `research_reports` | 한 run의 불변 시간순 fold 명세, 데이터·비용 적격성, sealed OOS 접근 상태, fold별 성과·사건 통계·NO_TRADE·purge 결과 |
| `research_comparisons` | 같은 dataset·비용·체결·전략 Family에서 관심순위 Factor만 바꾼 기준선/variant run 쌍. 공통·기준선 전용·variant 전용 후보와 회피 손실·놓친 이익을 fold별 불변 저장 |
| `research_context_hypothesis_revisions` | 종목·사건별 C1 가설의 최초 사실/영향 추론과 장중 응답별 상태 개정. `revision_available_at`까지 실제로 알 수 있던 최신 revision을 as-of 조회하며 특정 run이나 주문에 종속되지 않음 |
| `research_theme_leadership_revisions` | 테마·시장·판단시각별 H2 대장 순위와 가격/거래대금 상태·사건·품질의 불변 revision. 화면 표시 안정화 상태와 원시 사건 시각을 함께 보존 |
| `research_entry_theses` | 종목 진입 당시 대장 ID, 테마 revision, Factor와 가설 참조를 고정한 근거. 이후 대장 교체로 과거 행을 수정하지 않음 |
| `research_thesis_decisions` | 진입 근거 상태별 가격 전용/즉시 이탈/위험 시 축소/확인 후 이탈 정책의 비교 제안. 실제 주문 권한은 항상 없음 |
| `research_search_experiments` | dataset ID/hash, Family·Factor allowlist, parameter space, 목적·선택 조건·분할/OOS 접근 이력과 유한 예산을 고정한 R1 ExperimentSpec |
| `research_search_trials` | Experiment 안의 결정론적 순서와 파라미터, 과학적 종료 상태·run ID·결과. 완료·실패·부적격만 저장하며 실행 중단은 저장하지 않음 |
| `research_candidate_cards` | trial별 순손익·최대 낙폭·거래 수·활동일·원래 사유·선택 조건을 보존하는 전체 후보 카드 |
| `research_search_jobs` | dataset/spec별 작업의 queued/running/completed/failed/cancelled 상태, 시도 수, owner token·generation·heartbeat 실행 임대, 결과·오류 projection. 완료 job은 재등록하지 않음 |
| `research_search_job_events` | 작업 상태 전이를 수락 순서대로 보존하는 불변 event 원장 |
| `research_trial_attempts` | trial의 실제 실행 시도. owner/generation, 시작·heartbeat·종료와 COMPLETED/FAILED/INELIGIBLE/INTERRUPTED를 보존하며 INTERRUPTED는 결과로 승격하지 않음 |

run의 시작/완료 시각은 운영 메타데이터이며 재현성 hash에는 들어가지 않는다. 논리 결과는 고정 입력의 가상 `available_at` 순서와 전략·실행·outcome 문서만으로 계산한다. 완료 run의 입력·결과 행은 새 값으로 덮어쓰지 않는다.

## 중앙 DB: PostgreSQL 또는 중앙 SQLite

중앙 스키마 v17은 계좌 원문과 자격증명을 저장하지 않는 계좌 신원 registry를 추가하고, v18은 검증된 local→central 계좌 scope alias를 추가한다.

| 테이블 | 역할/주요 키 |
| --- | --- |
| `central_account_registry` | broker·real/mock 환경·보호 키 HMAC 지문을 지속 UUID `account_ref`에 연결. 원문 계좌번호와 App Key는 저장하지 않음 |
| `central_account_binding_revisions` | credential profile이 검증 시점에 어떤 account scope였는지 `ka00001` 방법과 revision으로 불변 보존 |
| `central_account_scope_aliases` | 오프라인 origin UUID를 같은 broker·real/mock 환경의 검증된 중앙 UUID에 연결. 존재하는 binding revision을 증거로 요구하며 변경·연쇄·순환을 허용하지 않음 |

두 구현은 같은 `QueryStore` 계약을 따른다.

분봉·일봉의 공통 열 순서와 행 변환, limit/offset 범위 계약은 `central_server/database_codec.py`에 있으며 SQLite와 PostgreSQL 구현이 함께 사용한다.
테이블·인덱스 생성문의 실행 원본은 `central_server/central_schema.py`이며, 동일 객체 목록을 유지하면서 SQLite의 TEXT/INTEGER와 PostgreSQL의 DATE/TIME/JSONB 자료형 차이는 명시적으로 보존한다.
마이그레이션 실행 원본은 `central_server/schema_migrations.py`다. `central_schema_migrations`의 v1 `current_central_storage_baseline`은 변경하지 않는다. v2~v13은 관측 메타데이터, 시장시각 보정, 초·테마·뉴스·시장사건 이력과 고정 연구 export를 추가했다. v14 `minute_bar_revision_history`는 분봉 operation 원장과 연구 관측 가용시각 인덱스를, v15 `shadow_candidate_ledger`는 주문 없는 후보 원장을 추가했다. v16 `mock_execution_ledger`는 mock broker intent·event·계좌 snapshot·단일 실행자 lease를, v17 `verified_account_identity_registry`는 보호된 계좌 지문 registry와 binding revision을 추가했다. v18 `verified_account_scope_aliases`는 오프라인 origin scope와 검증된 중앙 scope의 불변 연결을 추가한다. 현재 v19 `encrypted_credential_activation_ledger`는 비밀 없는 프로필·멱등 활성화 원장을 추가한다. 초기 이관과 파일 revision fence는 `central_documents`의 `credential_vault_state`에 저장한다. 키는 DB가 아닌 `/app/secrets`에 보존한다. 양쪽 DB의 변경은 같은 버전에 SQLite/PostgreSQL 명세를 함께 추가해야 한다.

| 테이블 | 역할 |
| --- | --- |
| `central_schema_migrations` | 중앙 스키마 버전·변경 불가 이름·적용 시각. 현재 v19 |
| `central_credential_profiles` | v19: profile_id PK, provider/environment, label/lifecycle_state, created_at/archived_at. 비밀과 계좌번호 없음 |
| `central_credential_activations` | v19: operation_id PK, provider/profile/revision, request_id/keyed digest, nullable account_ref/run_id/binding_revision, committed_at. profile/revision·request 중복 금지. binding과 한 트랜잭션 |

R2 프로필 생성의 멱등 원장은 기존 `central_documents`의 `credential_profile_requests`를 사용한다.
연결 해제 후 계좌를 삭제하면 row를 물리 삭제하지 않고 `lifecycle_state=archived`와
`archived_at`을 기록한다. 일반 인증 목록과 재시작 bootstrap에서는 제외하지만 기존 계좌 신원,
binding revision, activation receipt와 매매 이력 참조는 유지한다. vault에는 빈 credentials의
disabled tombstone만 남으므로 과거 App Key/Secret Key는 보존하지 않는다.

R6b3a 시세 역할은 기존 central_documents의 collection=`server_market_profile_settings`,
owner=`global`, key=`settings`에 저장한다. 필드는 market_profile_id,
불변 legacy_real_profile_id=`nas-real-default`, 양의 정수 revision 세 개뿐이다.
미저장 읽기는 두 profile의 기본값과 revision=0을 반환하고 저장하지 않는다.
입력 expected_binding_revision은 현재 real binding 검증용이며 문서에는 저장하지 않는다.
역할 CAS·활성 profile/registry/계좌 설정 검증은 SQLite BEGIN IMMEDIATE와 PostgreSQL의
기존 credential-activation advisory transaction lock에서 처리한다.
담당 profile disable/계좌 연결 해제는 같은 저장 경계에서 거절하고 완료 replay는 보존한다.
손상 문서는 복구 필요로 닫는다. 일반 content·PC 설정 백업으로 역할을 복제하지 않는다.
새 테이블/열/마이그레이션 없이 중앙 v19를 유지한다. 저장 revision은 실제 연결 적용 완료를 뜻하지 않는다.

R6b3b2 역할 변경은 같은 문서 CAS를 유지한다. routing 적용 revision은 owner의 실행 상태이며
DB 문서/credential binding/run/cursor에 새 필드를 추가하지 않는다. 적용 실패/결과 불명확은
저장 문서를 이전 값으로 덮지 않고 실행을 차단한다. 재기동은 저장된 담당을 사용한다.

R6b3c1의 실전 read-only 복구 결과는 실행 메모리의 AccountRecovery이며 새 DB 저장을 추가하지 않는다.
기존 execution account snapshot/주문 원장은 계속 mock 전용이다. 실제 계좌 결과를 이 원장에
편입하거나 같은 account_ref라도 mock/real 환경을 생략하지 않는다.

R6b3c2a 실전 자동 수집은 기존 central_documents의 내부 collection=`real_account_recovery`,
owner=`kiwoom:real:{account_ref}`, key=`SHA256(정규 JSON 전체)`에 append한다. 동일 결과의 저장 재시도는 1건이다.
문서는 scope, credential_profile_id, binding_revision, settings_revision, source=kiwoom_rest,
received_at 및 recovery(account/orders)를 포함한다. 금액은 원, 시각은 timezone-aware ISO다.
현금/보유/주문 as_of는 서로 다른 TR 관측시각이며 동시 원자 snapshot이나 틱/최종 비용 자료로 해석하지 않는다.
현재 verified registry·active profile·최신 binding revision·monitor ON·설정 revision이 같을 때만 저장한다.
SQLite BEGIN IMMEDIATE와 PostgreSQL credential-activation advisory lock에서 검증/삽입을 함께 수행한다.
원계좌번호/키/토큰을 저장하지 않으며 일반 content GET/POST 허용 목록에 넣지 않는다.
새 SQL 테이블/마이그레이션 없이 v19를 유지한다. 모의 실행 원장/lease/reconcile 계약은 변경하지 않는다.

R6b3c2b는 같은 저장 fence를 공유하는 내부 collection=`real_account_event`를 추가한다.
owner=`kiwoom:real:{account_ref}`, key=정규 전체 JSON SHA256이다. scope/profile/binding_revision/
settings_revision/source=kiwoom_websocket/event_type/received_at/event를 append한다.
event_type은 order_execution 또는 account_balance이며 기존 계좌번호 없는 parser dataclass만 허용한다.
같은 입력 저장 재시도는 1건이다. received_at이 다른 동일 체결의 재전달은 별도 관측일 수 있으므로
execution_no 유일 체결 원장/자동 일지 dedupe를 이 key로 대체하지 않는다.
원체결 trade_time 문자열과 서버 수신 aware received_at을 분리하며 잔고에는 없는 체결시각을 만들지 않는다.
일반 content GET/POST에 공개하지 않고 mock 실행 원장에는 넣지 않는다. schema v19는 유지한다.
저장 실패 이벤트는 실행 메모리에 대기하여 재시도하고 정상 살아 있는 키/역할/설정 commit 전 drain한다.
DB가 계속 실패한 상태의 강제 종료/프로세스 종료에는 메모리 대기의 영속성을 보장하지 않는다.

R3d 계좌 운영 문서는 `central_documents`의 collection=`server_account_settings`,
owner=`kiwoom:{environment}:{account_ref}`, key=`settings`다. 문서에는 검증된 `scope`,
nullable `active_profile_id`, bool `monitor_enabled`/`mock_order_enabled`, 정수 `revision`만 저장한다.
문서가 없는 검증 계좌의 읽기는 revision=0·두 토글 OFF·profile=null을 반환하며 저장하지 않는다.
실제 저장은 expected_revision CAS로 보호하고 unchanged는 revision/updated_at을 유지한다.
새 모의 활성화는 binding/activation과 같은 트랜잭션에서 초기 monitor ON·주문 OFF 설정을 저장한다.
R6b1부터 실전 활성화도 같은 저장 계약에 포함한다. 실전/모의별 verified scope의 단일 active_profile_id를
확정하며 중복 연결 실패는 profile/binding/원장/설정을 함께 rollback한다. 실전의 mock_order_enabled는
항상 OFF다. 같은 profile의 키 갱신은 설정값/revision을 보존하고, disable은 과거 binding을 삭제하지 않는다.
완료 replay는 이후 설정/새 활성 profile을 덮지 않으며 기존 실전 receipt에 설정이 없을 때만 초기 설정을 보완한다.
새 테이블/열/마이그레이션은 없다. 기존 v19 및 server_account_settings 문서를 사용한다.
같은 계좌의 다른 활성 profile 충돌은 전체 롤백이며 같은 profile 갱신은 설정을 보존한다.
완료 replay는 기존 설정을 변경하지 않는다. 문서 손상은 복구 필요로 닫고 기본값으로 덮지 않는다.
설정과 실행 적용은 별개다. R3e에서 적용 revision GET, R3f에서 실제 모의 설정 PUT을 연결했다. 키 disable의 vault activation metadata에는 disabled bool을 명시한다. DB는 기존 activation 원장을 사용하고 disable 시 기존 binding_revision을 참조하며 같은 트랜잭션에서 계좌 설정의 active_profile=None·토글 OFF를 저장한다. 완료 replay는 이후 설정을 되돌리지 않는다. 새 테이블/마이그레이션은 없고 중앙 v19 유지.
owner=provider, key=request_id이며 document는 provider/profile_id/label/keyed request_digest만 가진다.
새 프로필 draft row와 이 문서를 같은 트랜잭션으로 만들고 최대 64프로필을 허용한다.
적용 완료 때 draft→active 변경과 binding/활성화 삽입도 한 트랜잭션이다. SQLite BEGIN IMMEDIATE와
PostgreSQL credential activation advisory lock을 사용하며 새 테이블·스키마 버전 변경은 없다.

R3a 실행 임대 해제는 기존 central_execution_runtime_leases에서 owner_key와 owner_token을
동시에 조건으로 DELETE한다. owner_key=mock:account_ref, owner_token=run_id:owner_token이다.
다른 run/owner 또는 이미 교체된 소유자는 해제할 수 없다. SQLite BEGIN IMMEDIATE와
PostgreSQL 단일 조건부 DELETE를 사용하며 새 스키마는 없다.
R3b에서 운영 ExecutionRepository에 account/run/owner context를 불변 연결한다. 새 intent,
event+intent 갱신, account snapshot 쓰기는 같은 트랜잭션에서 해당 임대 owner/만료와 문서 scope를 확인한다.
SQLite BEGIN IMMEDIATE와 PostgreSQL lease row FOR UPDATE가 검사 이후 owner 교체를 직렬화한다.
만료/교체된 owner는 EXECUTION_OWNERSHIP_LOST, 다른 scope는 EXECUTION_OWNER_SCOPE_MISMATCH다.
DB 스키마 변경 없이 내부 쓰기 메서드의 선택 ownership 인자로 연결한다. 비운영 import/오프라인 원장의
기존 비소유 계약은 유지하며 새로운 운영 runtime은 시작 시 반드시 소유 context를 연결한다.
| `central_api_query_cache` | 요청 fingerprint별 짧은 REST 응답 캐시/연속조회 정보 |
| `central_realtime_latest` | 이벤트 종류·종목별 최신 실시간 값; 재접속 초기 복원 |
| `central_second_trade_bars` | 날짜·초·종목·시장별 OHLC, 거래량(주), 체결가×체결량 거래대금(원), 체결 건수와 최종 가용 시각. 최근 5초 안의 늦은 체결은 같은 행을 절대값으로 갱신하고 동일 저장 재시도는 합산하지 않음 |
| `central_minute_bars` | 날짜·분·종목·시장별 OHLCV/거래대금 백만원. 조회 봉과 실시간 형성 중 봉의 메타데이터를 같은 트랜잭션으로 기록 |
| `central_five_minute_bars` | 날짜·5분 구간 종료시각·종목·시장·공급자·원/수정주가 기준별 OHLCV와 공급자 원시 거래대금. 1분봉과 다른 키로 보존하며 원시 거래대금 단위는 검증 전 환산하지 않음 |
| `central_daily_bars` | 날짜·종목·시장별 일봉. 장 마감 여부에 따른 진행 중/확정 메타데이터를 같은 트랜잭션으로 기록 |
| `central_dataset_snapshots` | `ranking/top20_membership/top20_index/market_state/investor_flow/program_flow/new_high/stock_fundamentals/nxt_eligibility` 시계열 JSON |
| `central_market_data_observation_meta` | 관측 종류·대상·키별 실제 기준/가용 시각·시장·단위·실제/추정·완결성·출처·후보군. 순위·TOP20·시장 상태는 원본 스냅샷과 같은 트랜잭션으로 기록하며, 종류·대상·기준시각 범위 조회가 coverage 진단의 입력이 됨 |
| `central_observation_revisions` | `ranking/top20_membership/minute_bar`의 수락 순서, UTC 기준·수신·가용시각, source, payload/metadata hash, 정정 연결과 원본 순위 참조를 append-only로 보존. 분봉은 각 delta 반영 뒤 누적 전체 봉과 시간상 마감 revision을 남김 |
| `central_research_exports` | D2 추출 조건, 생성시각, revision 수·ID hash와 재생 계약을 가진 immutable dataset manifest |
| `central_research_export_members` | dataset별 확정 revision ID와 안정적인 1부터 시작하는 page ordinal. 추출 이후 들어온 관측은 기존 dataset에 추가하지 않음 |
| `central_minute_bar_operations` | 실시간 분봉 delta·마감 operation ID와 입력 hash. 봉·메타·revision과 같은 트랜잭션에서 기록해 성공 응답 유실 뒤 재시도의 이중 합산을 막음 |
| `central_theme_snapshots` | `theme_metadata(default,full)` 완전 문서가 수락된 순서대로 저장하는 테마 이력. snapshot ID, 활성 프로필 이름(`profile_id`), 내용 hash, 편집 기준시각, 서버 수신/가용시각, 원본 PC, 직전 snapshot ID와 전체 관계 문서를 보존 |
| `central_news_article_revisions` | `(stock_code, identity)` 호환 식별자별 기사 관측 revision. 내용 hash, 수집기/범위, 게시시각, 서버 수신·가용시각, 이전 revision과 원문 projection을 불변 보존 |
| `central_news_body_revisions` | 기사 revision별 본문 추출 결과. 본문 hash, extractor 버전, 수집·가용시각, `fulltext/summary_only/failed`, 본문과 오류를 불변 보존 |
| `central_news_ai_revisions` | 대상 종목과 정확한 기사/본문 revision을 참조하는 AI 결과. provider/model/prompt/schema/input hash, 계산·가용시각, 실제 출력·사용량을 불변 보존 |
| `central_news_event_revisions` | 정확한 기사/본문 revision에서 판정한 공급계약 사건 revision. URL과 다른 영속 event ID, 후보 연결 key, 역할·범위·확실성·신규성·점수/산식·근거·금액·상대방·AI 필요 사유와 가용시각을 불변 보존 |
| `central_news_event_membership_revisions` | event revision과 article/body revision 사이의 소속 근거. 정정·후속 단계는 새 membership revision 및 `revision_of`로 누적하며 과거 소속을 수정하지 않음 |
| `central_news_jobs` | 기사 수락과 함께 생성되는 BODY, 본문 저장과 함께 생성되는 기록 전용 RULE 및 선택적 AI 영속 작업. stage+target+입력 revision/hash+처리 버전의 안정 키, 상태·시도·다음 재시도·출력 참조를 보존한다. claim은 현재 사용자 선택 종목, watchlist 최신 BODY, query-set 최신 BODY, 나머지 후속 작업 순으로 조회한다. |
| `central_news_source_cursors` | query별 확정/진행 cursor(게시시각+identity), 다음 page·schedule, 성공/확인시각, coverage/truncated/error 최신 상태 |
| `central_news_source_runs` | source page 실행별 raw/unique/duplicate/request/budget/truncated/error와 gap 문서의 append-only 기록 |
| `central_news_source_observations` | 같은 기사가 여러 query/page에 나타난 사실을 버리지 않는 append-only 관측. global article revision과 query membership을 연결 |
| `central_news_article_target_revisions` | KRX 정확한 회사명 경계 일치의 confirmed 또는 동명 ambiguous/약칭·미일치 unresolved를 근거·버전과 함께 누적 |
| `central_news_request_budget` | KST 날짜·scope별 실제 NAVER 요청 직전 원자 claim 횟수. 기본 hard 24,000, watchlist 8,000, query_set 16,000 |
| `central_vi_event_revisions` | 시장 전체 1h 및 ka10054 보완에서 확인한 VI 발동/해제 사실. 종목·일자·시간·유형·가격·방향·횟수·거래소 기반 키로 동일 재수신만 합치며 과거 행은 수정하지 않음 |
| `central_hot_cohort_current` | 선택된 저장 조건식에 편입된 종목의 현재 추적 projection. 최초 관측·편입 KRX 세션·마지막 I/D·NXT 가능 여부·실제 만료 시각 보존 |
| `central_hot_cohort_revisions` | 조건 선택 이후 INITIAL/I/D/eligibility/expiry 사실의 append-only 이력. D와 15% 아래 하락은 당일 추적을 종료하지 않음 |
| `central_upper_limit_fact_revisions` | `upl_pric`과 0B 현재가/당일고가로 확인한 UNKNOWN/TOUCHED/CURRENT/CLOSED_AT_LIMIT 사실. 조건 편입만으로 확정하지 않음 |
| `central_documents` | 뉴스·AI·매매일지 뉴스 연결, 전체 테마·활성 프로필·별칭·종목 목록, 매매일지·공통설정·표 표시/순서, 종목별 최신 기본정보·NXT 가능 여부·0g 가격제한/기준가(`stock_price_references`)와 O2a content-addressed 내부 문서의 `(collection, owner, key)` JSON 저장소 |
| `central_external_bars` | 외부 공급원·논리 상품·실제 계약·주기·UTC 시각별 OHLCV 누적 자료 |

중앙 `central_documents`는 여러 로컬 스키마를 그대로 복제하지 않고 버전 가능한 문서로 동기화한다. 테마는 삭제 전파 때문에 컬렉션 전체 교체를 지원하고, 나머지는 upsert/증분 병합한다. 테마 이력은 세 projection의 중간 상태를 조합하지 않고 `ThemeBackupService.export_document()`가 만든 `theme_metadata(default,full)` 하나만 사용한다. 이 문서 교체와 이력 append는 한 DB 트랜잭션이며, 연속해서 같은 내용 hash가 오면 재전송으로 보고 새 행을 만들지 않는다. 과거 이력 행을 갱신하는 저장소 API는 없다.

`news_article/news_ai`는 기존 UI와 동기화 호환을 위한 최신 projection으로 유지한다. 기사 projection과 기사 revision 및 BODY job은 한 트랜잭션으로 저장한다. 같은 수집기의 같은 내용 재전송만 합치므로 다른 수집기의 최초 NAS 수신은 별도 revision으로 남는다. NAS `received_at/available_at`은 서버가 수락할 때 정하며 로컬 `first_seen_at`이나 projection `updated_at`을 복사하지 않는다. BODY/AI 원장은 범용 콘텐츠 동기화 upsert 대상이 아니며, AI latest projection·AI revision·일일 사용량은 한 트랜잭션으로 저장한다.

본문 revision과 RULE job도 한 트랜잭션으로 저장한다. RULE은 `SUPPLY_CONTRACT`만 기록하며 MOU/논의/기대, 해지/부인, 가격 반응, 외화·조건부 금액과 제목-본문 충돌을 삭제하지 않는다. 같은 기사 identity의 정정 또는 상대방+금액 근거가 같은 진행은 기존 event ID의 새 revision이 되고, 관계가 부족하거나 상대방이 다르면 새 event ID와 `possible_related` 후보를 남긴다. 규칙 원장은 generic `central_documents` upsert를 거치지 않으며 AI·수동 분석·주문 경로를 변경하지 않는다.

N3 query_set 기사는 `stock_code=GLOBAL`인 하나의 immutable article 원장으로 합치고 query별 출현은 source observation으로 각각 남긴다. 같은 source+identity의 직전 내용 hash와 같으면 그 article revision을 재사용하므로 여러 query의 서로 다른 요약이 번갈아 와도 BODY 작업을 반복 생성하지 않는다. 같은 source가 A→B→A로 실제 판본을 바꾸면 세 관측과 세 판본은 보존한다. BODY 결과가 저장될 때 confirmed target만 target code/name을 가진 RULE job으로 예약하고, 본문 완료 뒤 target이 확인된 경우에는 기존 본문 revision으로 RULE을 멱등 예약한다. 동명·약칭·부분 문자열은 규칙을 자동 실행하지 않는다. source page의 article revision·target relation·observation·run·cursor는 한 트랜잭션이며 과거 cursor/관측을 새 도착 정보로 다시 쓰지 않는다.

종목 뉴스 목록용 조회는 `central_news_article_target_revisions`에서 `relation_status=confirmed`와 정확한 `stock_code`만 선택해 GLOBAL article에 연결한다. 같은 identity의 confirmed 판본이 여러 개면 `central_news_article_revisions.accepted_sequence`가 가장 큰 한 판본만 반환한다. unresolved·ambiguous·다른 종목 관계는 포함하지 않으며 이 읽기 자체는 BODY/RULE/AI 작업을 만들지 않는다.

TOP20 지수 저장 대기열은 DB 표가 아니라 `server-data` 볼륨의 `top20-index-outbox.json`에 기록된다. DB 저장 성공 뒤 제거되며 컨테이너 재시작 후 다시 읽는다. 동일 `snapshot_key` 저장은 중앙 DB upsert이므로 저장 성공과 outbox 제거 사이에 종료돼도 중복 행이 생기지 않는다.

신고가 목록(`new_high`)은 조회 시각별 원본 응답을 스냅샷으로 남긴다. 기본정보(`stock_fundamentals`)와 NXT 가능 여부(`nxt_eligibility`)는 시점별 스냅샷을 누적하고, 화면의 빠른 최신값 조회를 위해 `central_documents`의 `stock_fundamentals`·`stock_nxt_eligibility`에도 최신 문서를 upsert한다. 이 추가는 기존 범용 표를 사용하므로 중앙 스키마 버전을 올리지 않는다.

NAS의 키움 WebSocket 원본이 끊긴 동안에는 TOP20 지수 분 행을 0이나 로컬 장애전환 값으로 채우지 않는다. 재연결 후 새 행 저장을 재개하며, 빠진 타임스탬프 자체가 차트의 `수집 중단` 경계가 된다.

과거 시장 시뮬레이션 관점의 보존 단위·수집 공백·선행 보완 항목은 `HISTORICAL_DATA_CONTRACT.md`를 따른다. 특히 중앙 순위 스냅샷은 시점별 원본이지만 테마 문서는 현재 상태 교체본이므로 둘을 같은 시점 자료로 간주하지 않는다.

coverage 진단은 새 테이블이나 추정 데이터를 만들지 않고 위 메타데이터와 `central_documents.market_data_coverage`의 장후 완료 근거를 읽는다. 따라서 기존 행을 소급 보정하거나 DB 스키마 버전을 올리지 않는다.

## Raw와 파생 구분

- Raw/관측: `trade_fills`, Kiwoom 봉, `central_second_trade_bars`, 뉴스 기사/링크, `central_realtime_latest`, 진입 스냅샷.
- `trade_entry_snapshots`의 장후 보완 뉴스와 외국인·기관/프로그램 자료는 각 JSON 내부의 `backfilled=true`로 실시간 관측값과 구분한다. 기존 행에 표시가 없으면 실시간 관측 또는 레거시 자료이며, 분석에서는 `available=false` 수급을 누락으로 취급한다.
- 계산/파생: TOP20 지수, 거래강도, 신고가 근거 캐시, 사건 묶음/AI 판정, 매매 유형·복기 통계.
- 사용자 원본: 설정, 테마 편집, 복기, 수동 묶음/유형, 전략팩.
- 운영 메타: sync/finalization/unconfirmed 로그, 요청 캐시, AI 사용량.

외부 시장 자료는 `(provider, instrument, contract, timeframe, bar_time)`을 기본키로 사용한다. 같은 봉의 재수신은 정정하고 서로 다른 시각·계약은 계속 누적한다. 현재 `yahoo_delayed`는 임시 공급원이며 계약 롤오버 시 과거 계약을 덮어쓰지 않는다. 대표 월물·차월물·확인 횟수·전일 종가 기준 등락률은 `central_documents`의 `external_market_roll_state` 컬렉션에 저장한다.

파생값은 원본을 덮어쓰지 않는다. 확정 API 봉/비용을 실시간 임시값보다 우선하는 현재 병합 규칙을 유지한다.

## 중앙 D4 shadow 후보 원장 (schema v15)

| 테이블 | 역할/주요 키 |
| --- | --- |
| `central_shadow_monitor_state` | 전략 설정 hash를 포함한 `monitor_id`별 증분 cursor, bounded 봉·순위 입력, 후보 상태와 품질 checkpoint |
| `central_shadow_decisions` | 완료 KRX 분봉별 Snapshot 기반 판단의 불변 JSON. `decision_id` PK |
| `central_shadow_candidate_events` | 주문 없는 후보 전이 원장. 서버 `accepted_sequence`, 유일 `event_id`, 감지·만료 시각과 불변 JSON |

checkpoint는 재시작 위치이며 연구 증거 원본이 아니다. Decision과 CandidateEvent는 같은 ID의 다른 내용으로 덮어쓸 수 없다. 이 원장은 로컬 연구 paper 체결·손익 및 실제 `trade_fills`와 분리한다.

## 중앙 O1 모의 주문 원장 (schema v16)

| 테이블 | 역할/주요 키 |
| --- | --- |
| `central_execution_intents` | `intent_id`별 mock/KRX 주문 명세와 현재 상태, broker 주문번호, 마지막 대조 시각. JSON에는 상세 체결 합계와 REST broker 누적 체결 합계를 따로 보존하며 계좌 모니터는 `environment+account_ref+run_id+broker_order_id`로 기존 intent를 찾음 |
| `central_execution_events` | 접수 시도·응답 유실·접수·00 상세 체결·REST 누적체결 복구·취소·대조의 append-only 이력. 실제 broker execution ID가 없는 REST 누적량에는 ID를 합성하지 않음 |
| `central_execution_account_snapshots` | 전송 전·복구 시점의 가용현금, 미체결 예약, 보유수량과 broker as-of |
| `central_execution_runtime_leases` | `environment+account_ref`별 단일 NAS 실행자 임대. 소유값에 run과 process token 포함 |

연구용 paper 원장과 broker mock 원장은 `environment/account_ref/run_id`로 분리한다. 응답 유실 상태는 자동 재전송하지 않고 미체결·체결·잔고 snapshot으로 대조한다. 명시적으로 켠 mock 계좌 모니터는 실전과 분리된 모의 키·REST limiter·broker·00/04 WebSocket을 사용한다. 별도 주문 플래그를 켜면 수동 게이트웨이가 `run_id+request_id`의 결정적 intent ID로 이 기존 v16 원장을 재사용하므로 스키마 증가는 없다. 같은 요청은 기존 행을 반환하며 다른 주문 내용에 같은 ID를 재사용하면 거부한다. `04`는 전체 REST 복구의 변경 신호이며 원문·실제 계좌번호·인증키를 저장하거나 API로 반환하지 않는다.

## 중앙 O2a 전진평가 문서 (기존 schema v16)

| `central_documents.collection` | 역할/주요 키 |
| --- | --- |
| `execution_forward_profiles` | `owner=strategy_ref`, `key=profile_id`. 전략·연산 버전·주 데이터 경로·mock 계좌·평가 기간·기준과 신규 profile의 연구 session profile을 시작 전에 동결. 기존 필드 누락 문서는 기존 ID로 읽음 |
| `execution_forward_reports` | `owner=profile_id`, `key=report_id`. 데이터/시스템/성과 gate와 비용·한계·승격 가능 근거 |
| `execution_strategy_stage_revisions` | `owner=strategy_ref`, `key=revision_id`. 이전 stage, 새 stage, 불변 evidence ref와 판단 사유 |
| `execution_feedback_evidence` | `owner=strategy_ref`, `key=evidence_id`. canonical mock 계좌·기간·사전 run 선택·PIT/최종노출 상태·대조 체결 품질·실제 비용과 확인된 순손익을 내용 주소형으로 동결 |
| `execution_feedback_reviews` | `owner=strategy_ref`, `key=review_id`. 저장된 feedback evidence와 명시 최소 거래일·거래 수 정책으로 다시 계산 가능한 기계 복기 revision. 사용자 메모와 전략 원본은 포함하지 않음 |
| `execution_feedback_improvement_proposals` | `owner=strategy_ref`, `key=proposal_id`. 적격 review와 등록 Family·factor·허용값 안에서 기준 전략의 한 파라미터만 바꾼 검토 대기 제안. 채택·새 전략 버전·실행 상태는 포함하지 않음 |
| `execution_feedback_strategy_versions` | `owner=parent_strategy_ref`, `key=version_id`. 채택 proposal의 설정과 부모·계좌·review/evidence 계보를 보존한 새 불변 전략 버전. 상태는 `REVALIDATION_REQUIRED` |
| `execution_feedback_revalidation_requests` | `owner=parent_strategy_ref`, `key=request_id`. 전략 버전마다 한 번만 허용하는 개발 재검증 요청. campaign/template/experiment/job ID를 queue 전에 고정 |
| `execution_feedback_revalidation_receipts` | `owner=campaign_id`, `key=receipt_id`. 기존 개발 template에서 만든 experiment/job ID와 새 전략 버전의 queue 확인 근거 |
| `execution_mock_automation_specs` | `owner=mock account_ref`, `key=spec_id`. 최종 후보/result hash, 현재 검증 binding, forward profile과 모든 운용 한도를 고정한 O2-M 명세. 저장만으로 주문은 활성화되지 않음 |
| `execution_mock_automation_admissions` | `owner=mock account_ref`, `key=admission_id`. READY 명세와 CR3 final batch/run/result 대조 후 계좌 lease 전에 저장하는 단일 후보 입장 요청 |
| `execution_mock_automation_lease_receipts` | `owner=mock account_ref`, `key=receipt_id`. 기존 O1 account lease를 결정적 자동 run이 획득했고 신규 주문은 비활성임을 기록 |
| `execution_mock_automation_recovery_decisions` | `owner=mock account_ref`, `key=decision_id`. broker 전체 복구 fingerprint와 주문·포지션·예약자금·손익·데이터/장애 한도의 BLOCKED 또는 CLEARED_ORDERS_DISABLED revision |
| `execution_mock_automation_decision_gates` | `owner=mock account_ref`, `key=gate_id`. 매 action Decision 직전의 binding/lease/session/data/account/손익/장애 및 진행 중 O1 주문 판정. 통과는 한 intent 제출만 허용 |
| `execution_mock_automation_dispatch_receipts` | `owner=mock account_ref`, `key=receipt_id`. 승인 gate가 결정적 O1 intent와 현재 order state에 연결된 결과. 같은 Decision 재호출은 기존 intent를 사용 |
| `execution_mock_automation_stop_revisions` | `owner=mock account_ref`, `key=revision_id`. 신규 주문을 즉시 닫은 긴급 중지 사유와 시각. 기존 주문·포지션 자동 취소/청산 근거로 사용하지 않음 |
| `execution_mock_automation_control` | `owner=key=mock account_ref`. 현재 RUNNING/STOPPED, 단조 증가 `control_revision`, 활성 spec/run을 저장하는 조건부 갱신 문서 |
| `execution_mock_automation_admission_by_spec` / `execution_mock_automation_lease_by_admission` | 감사 이력을 반복 탐색하지 않기 위한 현재 identity 문서. 원본 불변 admission/lease를 그대로 가리킴 |
| `execution_mock_automation_current_recovery` / `execution_mock_automation_current_stop` | admission별 최신 broker 대사와 최신 stop 문서. 원본 append-only revision은 그대로 보존 |
| `execution_mock_automation_approved_gates` / `execution_mock_automation_dispatch_by_intent` | intent별 승인 gate v2와 최초 dispatch receipt 단건 조회 문서. 같은 Decision 재호출로 receipt를 늘리지 않음 |
| `execution_mock_automation_risk_snapshots` | `owner=mock account_ref`, `key=snapshot_id`. 계좌/run·KST 거래일·broker 관측 구간·binding/account/reconciliation/event/cost revision, FIFO 당일 실현 순손익 또는 unknown 사유, 보유/미체결·장애 근거를 보존하는 append-only 문서 |
| `execution_mock_automation_current_risk` | `owner=key=mock account_ref`. 단조 증가한 최신 risk reconciliation revision의 단건 조회 문서. 운영 recovery/gate는 이 문서와 같은 snapshot만 사용 |

이 컬렉션들은 공개 콘텐츠 API allowlist에 넣지 않은 NAS 내부 문서다. 불변 이력 key는 내용 hash를 사용하고 현재 상태 문서는 명시 identity key와 조건부 control revision을 사용한다. O1의 단일 NAS 실행자 전제를 그대로 사용하므로 별도 테이블이나 중앙 schema v17을 추가하지 않는다. O2a stage 저장은 보고서 통과와 분리된 명시 작업이며 `approved_for_live`를 만들 수 없다. O2-M0 복구 통과도 신규 주문을 열지 않으며 승인된 한 Decision의 intent claim에서 현재 control revision을 다시 확인한다.
## O2-Me1 자동 모의운용 후보 게시 문서

기존 `central_documents` 물리 테이블 안의 다음 세 컬렉션은 서버 내부 전용이며 일반 콘텐츠 API
allowlist에 포함하지 않는다.

- `execution_mock_automation_candidate_packages`: owner=`strategy_ref`, key=`package_hash`.
- `execution_mock_automation_eligibility_policies`: owner=`strategy_ref`, key=`package_hash`; 문서 안의
  `policy_id`는 정책 내용 hash다. 첫 게시 시 이 행을 먼저 고정해 중단 후 다른 합격선으로 바꾸지 못한다.
- `execution_mock_automation_eligibility_receipts`: owner=`account_ref`, key=`package_hash`.

모두 내용 주소형 불변 문서다. 같은 key에 다른 JSON은 거절한다. receipt는 현재 검증된 mock binding과
package/policy 계보가 일치할 때만 저장되며 `BLOCKED`도 감사 근거로 저장할 수 있다. 이 저장은 operating
spec, admission, runtime lease, execution intent를 만들지 않는다.

## 과거 시장 맥락 SQLite의 거래소 효력일

`data/historical_market_context.sqlite3`는 운영 PostgreSQL과 분리된 연구 원장이다. `historical_exchange_document_fetch`는 DART 원문 접수번호별 수집 상태·원문 ZIP 경로·SHA-256을 보존한다. `historical_exchange_effective_events`는 후보 종목 코드와 접수번호별 `receipt_date`, 거래정지/재개/상폐의 `effective_date`·`effective_time`·`precision`, 원문 필드 근거를 보존한다. `historical_exchange_effective_daily_checks`는 같은 사건에 대한 키움 일봉 거래량·전후 거래일·접수일과 효력일의 날짜 차이, `review_status`, 대조한 원본 DB 식별자를 기록한다. 일봉 대조는 공식 효력일을 덮어쓰지 않는다. 정정 공시나 효력 날짜가 없는 공시는 근거와 상태를 남기고 자동 단일 사건으로 확정하지 않는다.

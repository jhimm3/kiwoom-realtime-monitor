# 데이터베이스 스키마 지도

스키마의 실행 원본은 각 `CREATE TABLE` 코드다. 이 문서는 수정 범위 파악용이다. 로컬 SQLite 마이그레이션 실행기는 `infrastructure/persistence/schema_migrations.py`에 있다. 메인 DB는 v3, 매매일지 DB는 v2다. 메인 v2와 매매일지 v1은 기존에 멱등적 `CREATE TABLE`·호환 `ALTER TABLE`로 형성된 스키마의 기준선이고, 다음 버전에서 공통 시장 관측 메타데이터 표를 추가했다. 기존 시장 데이터 행의 시각·출처를 추정해 변환하지 않는다. 이후 필드 변경은 시작 코드에 임의 SQL을 더하지 말고 다음 연속 버전의 명시적 마이그레이션과 이전 DB fixture를 함께 추가한다.

마이그레이션 원장은 `version`, 변경 불가한 `name`, `applied_at`을 저장한다. 실행기는 버전 연속성과 이름 일치를 검사하고 각 변경을 savepoint 안에서 수행한다. 실패한 변경은 DDL과 원장을 함께 되돌리고, 앱이 지원하는 버전보다 새로운 DB는 조용히 열지 않고 오류로 거부한다. 중앙 SQLite/PostgreSQL도 동일한 중앙 마이그레이션 계획을 각 DB 방언의 한 트랜잭션에서 실행한다.

## NAS 누적 보존 원칙

`central_minute_bars`, `central_daily_bars`, `central_dataset_snapshots` 및 TOP20 편입 문서는 자동 삭제하지 않고 같은 키의 관측만 갱신한다. `central_api_query_cache`는 만료 자료를 정리하고 `central_realtime_latest`는 최신 상태만 보유하므로 NAS의 모든 테이블이 append-only인 것은 아니다.

- `top20_membership`: 30초 관측별 실제 구성과 키움 원본 행
- `top20_index`: 1분 거래대금 합계, 시장별 값, 구성 종목과 코호트
- `top20_daily_entrants`: 거래일 중 한 번이라도 편입된 모든 종목
- `market_data_coverage`: 거래일·종목·시장별 분봉 장후 보완 완료 근거
- `market_data_coverage_daily`: 종목·시장별 최근 250일 일봉 보완 기준일

## 로컬 메인 DB: `monitor.sqlite3`

| 분류 | 테이블 | 역할/주요 키 |
| --- | --- | --- |
| 메타 | `schema_migrations` | 메인 스키마 버전·이름·적용 시각. 현재 v3 |
| 관측 메타 | `market_data_observation_meta` | `(dataset_kind, subject, observation_key)`별 시장 기준/가용 시각·시장·단위·실제/추정·완결성·출처·후보군. 신규 실시간/조회 분봉·일봉과 같은 트랜잭션으로 저장 |
| 설정 | `settings`, `central_setting_versions`, `column_settings` | 앱 값, 중앙 병합 시각, 표 열 구성 |
| 종목 | `stocks` | `code` PK, 이름·시장·기본정보·신고가/NXT 캐시 |
| 종목명 | `stock_aliases`, `stock_name_history`, `kind_name_disclosures` | 별칭, 변경 이력/승인, KIND 확인 대기 |
| 테마(구형/호환) | `themes`, `stock_themes` | 테마와 종목 N:M |
| 테마 프로필 | `theme_profiles`, `profile_themes`, `profile_stock_themes` | 프로필 → 테마 → 종목 N:M |
| 신고가 | `new_high_snapshot`, `new_high_snapshot_meta`, `intraday_highs`, `historical_high_evidence` | 기간 목록, 당일 고가, 수정주가 근거 |
| 원시/보정 봉 | `minute_bars`, `daily_bars` | 종목·날짜·분/일 OHLCV와 거래대금 |
| 지수 봉 | `market_index_minute_bars`, `market_index_daily_bars` | KOSPI/KOSDAQ OHLCV·거래대금 |
| 파생 지표 | `top20_trade_value_index` | 분별 TOP20 합계, 시장별 분해, 구성 종목/구간, 수집 상태 |
| 수집 상태 | `minute_history_sync_log`, `daily_bar_sync_log`, `market_data_finalization_log`, `market_data_unconfirmed_log` | 백필·확정·최종 실패 상태 |

`stocks.code`가 테마·신고가의 논리적 부모다. 일부 봉 테이블은 성능과 과거 호환 때문에 외래키를 강제하지 않는다. `top20_trade_value_index`는 원시 체결이 아니라 순위 코호트와 분봉에서 계산된 파생 데이터다.

`themes/stock_themes`는 프로필 기능 도입 전 자료와 구형 설정·테마 백업 복원을 위한 호환 원본이다. 최초 `theme_profiles_initialized` 이전 때 기본 프로필로 한 번 이전하며, 현재 테마 편집과 새 설정/Google/NAS 백업의 쓰기 원본은 `theme_profiles/profile_themes/profile_stock_themes`다. 새 백업은 활성 프로필과 테마 가져오기 규칙도 함께 보존한다. 두 구조를 활성 이중 쓰기로 맞추거나 구형 표를 바로 삭제하지 않는다.

## 로컬 뉴스 DB: `news.sqlite3`

| 테이블 | 역할/관계 |
| --- | --- |
| `news_schema_migrations` | 뉴스 스키마 버전·이름·적용 시각. 현재 v1 기준선 |
| `stock_news` | `(stock_code, identity)` PK. 제목·요약·링크·게시시각·기본 판정 |
| `stock_news_sync` | 종목별 전체/네이버 마지막 확인 시각 |
| `journal_news_links` | `(group_id, stock_code, identity)`로 복기 묶음과 기사 연결 |
| `stock_news_ai` | 기사/사건 identity별 종목 영향 분석; `stock_news`와 논리 연결 |
| `news_ai_shared` | 종목 간 재사용 가능한 사건 분석 |
| `news_ai_requests` | 공급자·모델·요청 모드·기사 수·토큰 사용량 원장 |

`stock_news`는 수집 원본에 가까운 데이터, AI 세 테이블은 계산/파생 데이터다. 뉴스 DB는 메인 DB에서 물리적으로 분리되어 메인 실시간 SQLite 잠금을 줄인다.
실행 스키마는 `infrastructure/persistence/news_schema.py`가 단일 소유하며 `StockNewsRepository`와 `NewsAIRepository` 어느 쪽을 먼저 열어도 같은 v1 기준선을 확인한다. 기존에 각 저장소 생성자에 나뉘어 있던 `CREATE TABLE` 문은 이 모듈로 통합했으며 데이터 형식은 바꾸지 않았다.

## 로컬 매매일지 DB: `journal.sqlite3`

실행 스키마 위치: `infrastructure/persistence/journal_schema.py`. 저장·조회 구현은 `journal_database.py`에 남아 있다.

| 분류 | 테이블 | 역할/주요 키 |
| --- | --- | --- |
| 메타 | `journal_schema_migrations` | 매매일지 스키마 버전·이름·적용 시각. 현재 v3 |
| 동기화 상태 | `journal_sync_states` | 중앙 동기화 문서별 활성/삭제 상태와 갱신 시각. 수동 묶음·회차 유형 삭제가 다음 동기화에서 되살아나지 않도록 tombstone을 보존 |
| 관측 메타 | `market_data_observation_meta` | 메인 DB와 동일한 관측 의미 계약. 신규 부분/확정 분봉·일봉 및 체결 스냅샷 필드별 `entry_context`와 같은 트랜잭션으로 저장하며 확정·장후 보완값은 임시값으로 강등하지 않음 |
| 원시 체결 | `trade_fills` | 주문번호·종목·시각·매수/매도 복합 PK. 저장 구현은 `journal_trade_repository.py` |
| 실제 비용 | `daily_trade_costs` | 체결일·종목·방향별 정산금액/수수료/세금. 저장 구현은 `journal_trade_repository.py` |
| 사용자 데이터 | `trade_reviews`, `trade_group_overrides` | 묶음 복기, 수동 묶음. 저장 구현은 `journal_trade_repository.py` |
| 종목 목록 | `journal_stocks` | 날짜·종목별 열람/수집 대상 |
| 봉 캐시 | `journal_minute_bars`, `journal_daily_bars`, `journal_bar_backfill` | 차트용 로컬 캐시와 확정 상태. 저장·메인 DB 가져오기 구현은 `journal_bar_repository.py` |
| 설정/전략 | `journal_settings` | UI 설정, 개인원칙, 전략팩·강의 추출 초안·버전 JSON. 전략 관련 저장 구현은 `journal_strategy_settings_repository.py` |
| 파생 분류 | `trade_setup_classifications`, `trade_setup_cycle_overrides` | 자동 유형과 묶음/회차 수동 수정. 저장 구현은 `journal_trade_repository.py` |
| 시점 원본 | `trade_entry_snapshots` | 체결 순간 순위·대금·테마·신고가·뉴스·수급·호가·시장 JSON. 저장·보완 구현은 `journal_snapshot_repository.py` |

매매 묶음/회차 자체는 체결과 `trade_group_overrides`에서 계산되며 별도 고정 테이블이 아니다. 자동 분류는 재계산 가능한 파생값이지만 수동 수정은 사용자 원본이므로 보존해야 한다. 매매일지 v3 마이그레이션은 기존 사용자 행을 변경하지 않고 중앙 동기화 삭제 상태표만 추가한다.

## 중앙 DB: PostgreSQL 또는 중앙 SQLite

두 구현은 같은 `QueryStore` 계약을 따른다.

분봉·일봉의 공통 열 순서와 행 변환, limit/offset 범위 계약은 `central_server/database_codec.py`에 있으며 SQLite와 PostgreSQL 구현이 함께 사용한다.
테이블·인덱스 생성문의 실행 원본은 `central_server/central_schema.py`이며, 동일 객체 목록을 유지하면서 SQLite의 TEXT/INTEGER와 PostgreSQL의 DATE/TIME/JSONB 자료형 차이는 명시적으로 보존한다.
마이그레이션 실행 원본은 `central_server/schema_migrations.py`다. `central_schema_migrations`의 v1 `current_central_storage_baseline`은 변경하지 않고, v2 `market_data_observation_metadata`에서 SQLite/PostgreSQL 양쪽에 공통 관측 메타데이터 표를 추가했다. 현재 v3 `repair_market_state_special_trade_time`은 키움 장 종료 특수값 `888888` 때문에 생긴 `market_state`의 잘못된 `T88:88` 키만 `saved_at`의 서울 시각 분으로 옮기고 원본 payload는 그대로 보존한다. 이후 양쪽 DB의 변경도 같은 버전에 SQLite/PostgreSQL 명세를 함께 추가해야 한다.

| 테이블 | 역할 |
| --- | --- |
| `central_schema_migrations` | 중앙 스키마 버전·변경 불가 이름·적용 시각. 현재 v3 |
| `central_api_query_cache` | 요청 fingerprint별 짧은 REST 응답 캐시/연속조회 정보 |
| `central_realtime_latest` | 이벤트 종류·종목별 최신 실시간 값; 재접속 초기 복원 |
| `central_minute_bars` | 날짜·분·종목·시장별 OHLCV/거래대금 백만원. 조회 봉과 실시간 형성 중 봉의 메타데이터를 같은 트랜잭션으로 기록 |
| `central_daily_bars` | 날짜·종목·시장별 일봉. 장 마감 여부에 따른 진행 중/확정 메타데이터를 같은 트랜잭션으로 기록 |
| `central_dataset_snapshots` | `ranking/top20_membership/top20_index/market_state/investor_flow/program_flow/new_high/stock_fundamentals/nxt_eligibility` 시계열 JSON |
| `central_market_data_observation_meta` | 관측 종류·대상·키별 실제 기준/가용 시각·시장·단위·실제/추정·완결성·출처·후보군. 순위·TOP20·시장 상태는 원본 스냅샷과 같은 트랜잭션으로 기록하며, 종류·대상·기준시각 범위 조회가 coverage 진단의 입력이 됨 |
| `central_documents` | 뉴스·AI·매매일지 뉴스 연결, 전체 테마·활성 프로필·별칭·종목 목록, 매매일지·공통설정·표 표시/순서 및 종목별 최신 기본정보·NXT 가능 여부의 `(collection, owner, key)` JSON 저장소 |
| `central_external_bars` | 외부 공급원·논리 상품·실제 계약·주기·UTC 시각별 OHLCV 누적 자료 |

중앙 `central_documents`는 여러 로컬 스키마를 그대로 복제하지 않고 버전 가능한 문서로 동기화한다. 테마는 삭제 전파 때문에 컬렉션 전체 교체를 지원하고, 나머지는 upsert/증분 병합한다.

TOP20 지수 저장 대기열은 DB 표가 아니라 `server-data` 볼륨의 `top20-index-outbox.json`에 기록된다. DB 저장 성공 뒤 제거되며 컨테이너 재시작 후 다시 읽는다. 동일 `snapshot_key` 저장은 중앙 DB upsert이므로 저장 성공과 outbox 제거 사이에 종료돼도 중복 행이 생기지 않는다.

신고가 목록(`new_high`)은 조회 시각별 원본 응답을 스냅샷으로 남긴다. 기본정보(`stock_fundamentals`)와 NXT 가능 여부(`nxt_eligibility`)는 시점별 스냅샷을 누적하고, 화면의 빠른 최신값 조회를 위해 `central_documents`의 `stock_fundamentals`·`stock_nxt_eligibility`에도 최신 문서를 upsert한다. 이 추가는 기존 범용 표를 사용하므로 중앙 스키마 버전을 올리지 않는다.

NAS의 키움 WebSocket 원본이 끊긴 동안에는 TOP20 지수 분 행을 0이나 로컬 장애전환 값으로 채우지 않는다. 재연결 후 새 행 저장을 재개하며, 빠진 타임스탬프 자체가 차트의 `수집 중단` 경계가 된다.

과거 시장 시뮬레이션 관점의 보존 단위·수집 공백·선행 보완 항목은 `HISTORICAL_DATA_CONTRACT.md`를 따른다. 특히 중앙 순위 스냅샷은 시점별 원본이지만 테마 문서는 현재 상태 교체본이므로 둘을 같은 시점 자료로 간주하지 않는다.

coverage 진단은 새 테이블이나 추정 데이터를 만들지 않고 위 메타데이터와 `central_documents.market_data_coverage`의 장후 완료 근거를 읽는다. 따라서 기존 행을 소급 보정하거나 DB 스키마 버전을 올리지 않는다.

## Raw와 파생 구분

- Raw/관측: `trade_fills`, Kiwoom 봉, 뉴스 기사/링크, `central_realtime_latest`, 진입 스냅샷.
- `trade_entry_snapshots`의 장후 보완 뉴스와 외국인·기관/프로그램 자료는 각 JSON 내부의 `backfilled=true`로 실시간 관측값과 구분한다. 기존 행에 표시가 없으면 실시간 관측 또는 레거시 자료이며, 분석에서는 `available=false` 수급을 누락으로 취급한다.
- 계산/파생: TOP20 지수, 거래강도, 신고가 근거 캐시, 사건 묶음/AI 판정, 매매 유형·복기 통계.
- 사용자 원본: 설정, 테마 편집, 복기, 수동 묶음/유형, 전략팩.
- 운영 메타: sync/finalization/unconfirmed 로그, 요청 캐시, AI 사용량.

외부 시장 자료는 `(provider, instrument, contract, timeframe, bar_time)`을 기본키로 사용한다. 같은 봉의 재수신은 정정하고 서로 다른 시각·계약은 계속 누적한다. 현재 `yahoo_delayed`는 임시 공급원이며 계약 롤오버 시 과거 계약을 덮어쓰지 않는다. 대표 월물·차월물·확인 횟수·전일 종가 기준 등락률은 `central_documents`의 `external_market_roll_state` 컬렉션에 저장한다.

파생값은 원본을 덮어쓰지 않는다. 확정 API 봉/비용을 실시간 임시값보다 우선하는 현재 병합 규칙을 유지한다.

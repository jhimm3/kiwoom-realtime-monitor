# NAS 전체 테이블·컬럼 사전

기준: **2026-10-01 23:41:36 KST**, `kiwoom_monitor` PostgreSQL 17.11 / migration 20. **43개 테이블, 418개 컬럼**. 카탈로그의 타입·NULL·기본값·PK/UNIQUE·인덱스를 그대로 보존했다. 데이터 행은 조회하지 않았다.

[전체 지도](README.md) · [선택형 관계도](atlas.html) · [분야별 관계도](RELATIONSHIPS.md) · [문서 종류](COLLECTIONS.md) · [원본 카탈로그+설명](catalog.json)

**읽는 법:** 모든 관계선은 코드로 유지되는 논리 관계다. 이번 NAS 스키마에는 실제 FOREIGN KEY 제약이 0개다. 관계가 그려졌다고 삭제 시 자동 cascade 또는 참조 무결성이 보장되는 것은 아니다. PK/UNIQUE는 아래 실제 제약을 따른다. 함수 근거는 PC 커밋 `d92ded6`이며 동적 SQL은 명시적 호출 경계도 함께 표시했다. 호출 목록은 정적 참조이고 실행 횟수나 전 경로 커버리지가 아니다.

JSON 컬럼의 내부 키는 SQL 컬럼이 아니다. 문서 종류별 계약과 화면별 사용은 별도 문서에 설명한다. `accepted_sequence`, `profile_id`, `run_id` 등 이름이 같아도 다른 테이블의 동일 ID라고 가정하지 않는다.

<a id="central_account_binding_revisions"></a>
## 검증된 계좌 연결 이력 — `central_account_binding_revisions`

자격증명 프로필과 실제 계좌의 검증된 연결 1개.

- **쓰는 곳/계기:** 계좌 연결 검증 → append_account_binding / 자격증명 활성화
- **읽는 곳/용도:** 계좌 설정·실계좌 저장 시 연결 revision 검증
- **저장 경계:** 계좌 연결 revision을 보존; 계좌번호나 비밀키 자체를 뜻하지 않음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `binding_id` | `text` | 아니오 | — | 검증된 계좌 연결 기록 식별자 |
| `credential_profile_id` | `text` | 아니오 | — | 계좌 연결에 사용한 자격증명 프로필 ID |
| `broker` | `text` | 아니오 | — | 증권사/중개 공급자 구분 |
| `environment` | `text` | 아니오 | — | 실계좌/모의 등 실행 환경 |
| `account_ref` | `text` | 아니오 | — | 검증된 계좌의 불투명 식별자. 계좌번호 원문이 아님 |
| `binding_revision` | `bigint` | 아니오 | — | 연결 변경/검증 버전. 소유권 확인에 사용 |
| `verified_at` | `timestamp with time zone` | 아니오 | — | 계좌/별칭 연결 검증 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `verification_method` | `text` | 아니오 | — | 계좌/별칭 연결 검증 방법 |

**실제 제약**

- `central_account_binding_revis_credential_profile_id_broker__key`: `UNIQUE (credential_profile_id, broker, environment, binding_revision)`
- `central_account_binding_revisions_pkey`: `PRIMARY KEY (binding_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_account_binding_revis_credential_profile_id_broker__key ON public.central_account_binding_revisions USING btree (credential_profile_id, broker, environment, binding_revision);
CREATE UNIQUE INDEX central_account_binding_revisions_pkey ON public.central_account_binding_revisions USING btree (binding_id);
CREATE INDEX idx_central_account_binding_latest ON public.central_account_binding_revisions USING btree (credential_profile_id, broker, environment, binding_revision DESC);
```

**논리 관계**

- [검증된 계좌 연결 이력](TABLES.md#central_account_binding_revisions) → [계좌 신원 등록부](TABLES.md#central_account_registry): `account_ref → account_ref` (계좌 참조)
- [검증된 계좌 연결 이력](TABLES.md#central_account_binding_revisions) → [자격증명 프로필 목록](TABLES.md#central_credential_profiles): `credential_profile_id → profile_id` (프로필 참조)
- [자격증명 활성화 원장](TABLES.md#central_credential_activations) → [검증된 계좌 연결 이력](TABLES.md#central_account_binding_revisions): `profile/account/binding_revision` (검증된 연결)

**코드 근거**

- [_credential_schema_statements](../../src/kiwoom_monitor/central_server/central_schema.py) (L876) — read
- [_verify_real_account_write](../../src/kiwoom_monitor/central_server/database.py) (L632) — read
- [_save_market_profile_settings](../../src/kiwoom_monitor/central_server/database.py) (L735) — read
- [_save_account_settings](../../src/kiwoom_monitor/central_server/database.py) (L812) — read
- [_finalize_credential_activation](../../src/kiwoom_monitor/central_server/database.py) (L906) — read
- [PostgresQueryStore.append_account_binding](../../src/kiwoom_monitor/central_server/database.py) (L5280) — read
- [PostgresQueryStore.load_account_bindings](../../src/kiwoom_monitor/central_server/database.py) (L5308) — read
- [_verify_account_scope_alias_postgres](../../src/kiwoom_monitor/central_server/database.py) (L5595) — read

<a id="central_account_registry"></a>
## 계좌 신원 등록부 — `central_account_registry`

broker·environment·identity_fingerprint로 식별한 계좌.

- **쓰는 곳/계기:** register_account_identity
- **읽는 곳/용도:** 계좌 범위 해석, 연결 검증, 설정 저장
- **저장 경계:** 신원 fingerprint와 불투명 account_ref를 연결

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `account_ref` | `text` | 아니오 | — | 검증된 계좌의 불투명 식별자. 계좌번호 원문이 아님 |
| `broker` | `text` | 아니오 | — | 증권사/중개 공급자 구분 |
| `environment` | `text` | 아니오 | — | 실계좌/모의 등 실행 환경 |
| `identity_fingerprint` | `text` | 아니오 | — | 계좌 신원 fingerprint. 계좌번호 원문 대신 일치 확인 |
| `created_at` | `timestamp with time zone` | 아니오 | — | 해당 기록 생성 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `status` | `text` | 아니오 | — | 본문 추출/계좌/상한가 등의 상태. 테이블별 계약 |

**실제 제약**

- `central_account_registry_broker_environment_identity_finger_key`: `UNIQUE (broker, environment, identity_fingerprint)`
- `central_account_registry_pkey`: `PRIMARY KEY (account_ref)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_account_registry_broker_environment_identity_finger_key ON public.central_account_registry USING btree (broker, environment, identity_fingerprint);
CREATE UNIQUE INDEX central_account_registry_pkey ON public.central_account_registry USING btree (account_ref);
```

**논리 관계**

- [검증된 계좌 연결 이력](TABLES.md#central_account_binding_revisions) → [계좌 신원 등록부](TABLES.md#central_account_registry): `account_ref → account_ref` (계좌 참조)
- [검증된 계좌 범위 별칭](TABLES.md#central_account_scope_aliases) → [계좌 신원 등록부](TABLES.md#central_account_registry): `canonical_account_ref → account_ref` (검증된 별칭)
- [주문 의도 현재 상태](TABLES.md#central_execution_intents) → [계좌 신원 등록부](TABLES.md#central_account_registry): `account_ref; environment 함께 검증` (계좌 범위)
- [실행 계좌 상태 관측](TABLES.md#central_execution_account_snapshots) → [계좌 신원 등록부](TABLES.md#central_account_registry): `account_ref; environment 함께 검증` (계좌 관측)

**코드 근거**

- [_verified_settings_scope](../../src/kiwoom_monitor/central_server/database.py) (L610) — read
- [_finalize_credential_activation](../../src/kiwoom_monitor/central_server/database.py) (L902) — read
- [PostgresQueryStore.register_account_identity](../../src/kiwoom_monitor/central_server/database.py) (L5240) — write
- [PostgresQueryStore.append_account_binding](../../src/kiwoom_monitor/central_server/database.py) (L5273) — read
- [PostgresQueryStore.resolve_account_scope](../../src/kiwoom_monitor/central_server/database.py) (L5351) — read
- [_verify_account_scope_alias_postgres](../../src/kiwoom_monitor/central_server/database.py) (L5583) — read

<a id="central_account_scope_aliases"></a>
## 검증된 계좌 범위 별칭 — `central_account_scope_aliases`

옛 계좌 범위를 검증된 정식 범위로 연결한 기록.

- **쓰는 곳/계기:** register_account_scope_alias
- **읽는 곳/용도:** resolve_account_scope 및 계좌 자료 읽기
- **저장 경계:** 검증된 연결만 사용; 문자열이 비슷하다고 계좌를 합치지 않음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `origin_account_ref` | `text` | 아니오 | — | 별칭 등록 전 사용한 원래 계좌 범위 |
| `canonical_account_ref` | `text` | 아니오 | — | 별칭이 가리키는 검증된 정식 계좌 식별자 |
| `broker` | `text` | 아니오 | — | 증권사/중개 공급자 구분 |
| `environment` | `text` | 아니오 | — | 실계좌/모의 등 실행 환경 |
| `credential_profile_id` | `text` | 아니오 | — | 계좌 연결에 사용한 자격증명 프로필 ID |
| `binding_revision` | `bigint` | 아니오 | — | 연결 변경/검증 버전. 소유권 확인에 사용 |
| `verified_at` | `timestamp with time zone` | 아니오 | — | 계좌/별칭 연결 검증 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `verification_method` | `text` | 아니오 | — | 계좌/별칭 연결 검증 방법 |

**실제 제약**

- `central_account_scope_aliases_pkey`: `PRIMARY KEY (origin_account_ref)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_account_scope_aliases_pkey ON public.central_account_scope_aliases USING btree (origin_account_ref);
CREATE INDEX idx_central_account_scope_alias_target ON public.central_account_scope_aliases USING btree (canonical_account_ref, broker, environment);
```

**논리 관계**

- [검증된 계좌 범위 별칭](TABLES.md#central_account_scope_aliases) → [계좌 신원 등록부](TABLES.md#central_account_registry): `canonical_account_ref → account_ref` (검증된 별칭)

**코드 근거**

- [PostgresQueryStore.register_account_scope_alias](../../src/kiwoom_monitor/central_server/database.py) (L5322) — read
- [PostgresQueryStore.resolve_account_scope](../../src/kiwoom_monitor/central_server/database.py) (L5351) — read
- [_verify_account_scope_alias_postgres](../../src/kiwoom_monitor/central_server/database.py) (L5589) — read

<a id="central_api_query_cache"></a>
## 키움 응답 캐시 — `central_api_query_cache`

요청 키별 유효기간·페이지 응답.

- **쓰는 곳/계기:** REST broker → save_query
- **읽는 곳/용도:** REST broker → load_query
- **저장 경계:** 만료 캐시 정리 포함; 봉 원본의 영구 보존과 별개

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `cache_key` | `text` | 아니오 | — | API·요청 조건·페이지 등으로 만든 응답 캐시 키 |
| `api_id` | `text` | 아니오 | — | 키움 요청 API 식별자(예: ka10080) |
| `expires_at` | `double precision` | 아니오 | — | 캐시 또는 후보 이벤트가 유효한 기한. 단위는 아래 자료형·설명 참조 이 컬럼은 Unix epoch 초로 저장. |
| `payload_json` | `jsonb` | 아니오 | — | 응답·스냅샷·작업 입력 등 기능별 구조화 payload |
| `has_next` | `boolean` | 아니오 | — | 캐시된 응답에 다음 페이지가 있는지 |
| `next_key` | `text` | 아니오 | — | 키움 응답의 다음 페이지 연속조회 키 |

**실제 제약**

- `central_api_query_cache_pkey`: `PRIMARY KEY (cache_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_api_query_cache_pkey ON public.central_api_query_cache USING btree (cache_key);
CREATE INDEX idx_central_api_query_expiry ON public.central_api_query_cache USING btree (expires_at);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.load_query](../../src/kiwoom_monitor/central_server/database.py) (L2768) — read
- [PostgresQueryStore.save_query](../../src/kiwoom_monitor/central_server/database.py) (L2797) — write

<a id="central_credential_activations"></a>
## 자격증명 활성화 원장 — `central_credential_activations`

활성화 요청 1개의 확정 결과.

- **쓰는 곳/계기:** _finalize_credential_activation
- **읽는 곳/용도:** 활성화 재시도와 복구
- **저장 경계:** operation/request와 digest로 재시도 일치 확인; 파일 vault와 DB 확정 경계 별도

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `operation_id` | `text` | 아니오 | — | 멱등 작업 식별자. 테이블별 작업 범위가 다름 |
| `provider` | `text` | 아니오 | — | 외부 데이터/AI/자격증명 공급자. 소속 테이블별 의미가 다름 |
| `credential_revision` | `bigint` | 아니오 | — | 활성화 대상 암호화 자격증명 revision |
| `request_id` | `text` | 아니오 | — | 활성화 요청의 멱등 식별자 |
| `request_digest` | `text` | 아니오 | — | 활성화 요청 내용의 digest. 동일 request의 내용 변경 검출 |
| `profile_id` | `text` | 아니오 | — | 프로필 식별자. 테마 프로필과 자격증명 프로필은 서로 다른 범위 |
| `account_ref` | `text` | 예 | — | 검증된 계좌의 불투명 식별자. 계좌번호 원문이 아님 |
| `run_id` | `text` | 예 | — | 실행 묶음 식별자. 뉴스 수집 run과 주문 실행 run은 같은 범위가 아님 |
| `binding_revision` | `bigint` | 예 | — | 연결 변경/검증 버전. 소유권 확인에 사용 |
| `committed_at` | `timestamp with time zone` | 아니오 | — | 활성화 원장이 확정된 시각 PostgreSQL timestamptz(시간대 있는 시각). |

**실제 제약**

- `central_credential_activation_provider_profile_id_credentia_key`: `UNIQUE (provider, profile_id, credential_revision)`
- `central_credential_activation_provider_profile_id_request_i_key`: `UNIQUE (provider, profile_id, request_id)`
- `central_credential_activations_pkey`: `PRIMARY KEY (operation_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_credential_activation_provider_profile_id_credentia_key ON public.central_credential_activations USING btree (provider, profile_id, credential_revision);
CREATE UNIQUE INDEX central_credential_activation_provider_profile_id_request_i_key ON public.central_credential_activations USING btree (provider, profile_id, request_id);
CREATE UNIQUE INDEX central_credential_activations_pkey ON public.central_credential_activations USING btree (operation_id);
```

**논리 관계**

- [자격증명 활성화 원장](TABLES.md#central_credential_activations) → [자격증명 프로필 목록](TABLES.md#central_credential_profiles): `profile_id → profile_id` (활성화 프로필)
- [자격증명 활성화 원장](TABLES.md#central_credential_activations) → [검증된 계좌 연결 이력](TABLES.md#central_account_binding_revisions): `profile/account/binding_revision` (검증된 연결)

**코드 근거**

- [_load_credential_activations](../../src/kiwoom_monitor/central_server/database.py) (L514) — read
- [_find_credential_activation](../../src/kiwoom_monitor/central_server/database.py) (L568) — read
- [_finalize_credential_activation](../../src/kiwoom_monitor/central_server/database.py) (L932) — write

<a id="central_credential_profiles"></a>
## 자격증명 프로필 목록 — `central_credential_profiles`

프로필 이름·환경·수명 상태.

- **쓰는 곳/계기:** 프로필 등록·이름 변경·보관 처리
- **읽는 곳/용도:** 프로필 선택 화면 및 활성화 검증
- **저장 경계:** 키/비밀번호 저장 테이블이 아님; 암호화 vault와 구분

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `profile_id` | `text` | 아니오 | — | 프로필 식별자. 테마 프로필과 자격증명 프로필은 서로 다른 범위 |
| `provider` | `text` | 아니오 | — | 외부 데이터/AI/자격증명 공급자. 소속 테이블별 의미가 다름 |
| `environment` | `text` | 예 | — | 실계좌/모의 등 실행 환경 |
| `label` | `text` | 아니오 | — | 사용자가 보는 프로필 이름 |
| `lifecycle_state` | `text` | 아니오 | — | 프로필의 활성/보관 등 수명 상태 |
| `created_at` | `timestamp with time zone` | 아니오 | — | 해당 기록 생성 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `archived_at` | `timestamp with time zone` | 예 | — | 프로필 보관 상태로 전환한 시각 PostgreSQL timestamptz(시간대 있는 시각). |

**실제 제약**

- `central_credential_profiles_pkey`: `PRIMARY KEY (profile_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_credential_profiles_pkey ON public.central_credential_profiles USING btree (profile_id);
```

**논리 관계**

- [검증된 계좌 연결 이력](TABLES.md#central_account_binding_revisions) → [자격증명 프로필 목록](TABLES.md#central_credential_profiles): `credential_profile_id → profile_id` (프로필 참조)
- [자격증명 활성화 원장](TABLES.md#central_credential_activations) → [자격증명 프로필 목록](TABLES.md#central_credential_profiles): `profile_id → profile_id` (활성화 프로필)

**코드 근거**

- [_credential_schema_statements](../../src/kiwoom_monitor/central_server/central_schema.py) (L876) — write
- [_register_credential_profile](../../src/kiwoom_monitor/central_server/database.py) (L502) — read
- [_list_credential_profiles](../../src/kiwoom_monitor/central_server/database.py) (L522) — read
- [_archive_credential_profile](../../src/kiwoom_monitor/central_server/database.py) (L528) — read
- [_rename_credential_profile](../../src/kiwoom_monitor/central_server/database.py) (L550) — read
- [_create_credential_profile](../../src/kiwoom_monitor/central_server/database.py) (L591) — write
- [_save_market_profile_settings](../../src/kiwoom_monitor/central_server/database.py) (L730) — read
- [_save_account_settings](../../src/kiwoom_monitor/central_server/database.py) (L807) — read
- [_finalize_credential_activation](../../src/kiwoom_monitor/central_server/database.py) (L896) — read

<a id="central_daily_bars"></a>
## 시장별 일봉 — `central_daily_bars`

종목·거래일·시장 1개 봉.

- **쓰는 곳/계기:** ka10081 → 시장 응답 저장 → replace_daily_bars
- **읽는 곳/용도:** 일봉 API·TOP20 준비·신고가 계산
- **저장 경계:** 조회 봉과 해당 메타데이터를 한 저장 호출에서 기록; unchanged guard 존재

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `trading_date` | `date` | 아니오 | — | 거래일. 단순 수신일과 다를 수 있음 |
| `code` | `text` | 아니오 | — | 종목 코드 |
| `market` | `text` | 아니오 | — | 봉 원본 시장 KRX/NXT/SOR. COMBINED는 읽을 때 만든 결과이며 저장 시장 아님 |
| `open` | `bigint` | 아니오 | — | 봉의 시가 |
| `high` | `bigint` | 아니오 | — | 봉의 고가 |
| `low` | `bigint` | 아니오 | — | 봉의 저가 |
| `close` | `bigint` | 아니오 | — | 봉의 종가. 가격 단위는 해당 시장/공급자 계약 |
| `volume` | `bigint` | 아니오 | — | 봉의 거래량. 시장/공급자 단위에 따름 |
| `trade_value_million_won` | `bigint` | 예 | — | 거래대금, 백만원 단위 |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_daily_bars_pkey`: `PRIMARY KEY (trading_date, code, market)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_daily_bars_pkey ON public.central_daily_bars USING btree (trading_date, code, market);
CREATE INDEX idx_central_daily_bars_code_market_date ON public.central_daily_bars USING btree (code, market, trading_date DESC);
```

**논리 관계**

- [시장별 일봉](TABLES.md#central_daily_bars) → [현재 봉의 시간·출처 근거](TABLES.md#central_market_data_observation_meta): `daily_bar / 종목·시장 / 거래일 관측키` (품질·가용성)

**코드 근거**

- [PostgresQueryStore.load_daily_bars](../../src/kiwoom_monitor/central_server/database.py) (L3476) — read
- [replace_daily_bars](../../src/kiwoom_monitor/central_server/database.py) (L1016) — dynamic boundary
- [_replace_bars](../../src/kiwoom_monitor/central_server/database.py) (L1501) — dynamic boundary

<a id="central_dataset_snapshots"></a>
## 종류별 관측 스냅샷 — `central_dataset_snapshots`

kind·subject·snapshot_key별 관측 문서.

- **쓰는 곳/계기:** ranking·TOP20 구성/지수·수급 등 → save_dataset_snapshots
- **읽는 곳/용도:** 종목 화면·TOP20 통계·분석·재현
- **저장 경계:** 같은 키 재저장과 새 시점 추가를 구분; 종류마다 key/단위가 다름

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `kind` | `text` | 아니오 | — | 데이터셋/관측 종류. 허용 종류마다 payload 의미가 다름 |
| `subject` | `text` | 아니오 | — | 데이터 대상 키. 종목·시장 포함키·집계 대상 등 kind별로 다름 |
| `snapshot_key` | `text` | 아니오 | — | 데이터 종류 안의 관측 시점/논리 키 |
| `saved_at` | `double precision` | 아니오 | — | 스냅샷을 저장한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `payload_json` | `jsonb` | 아니오 | — | 응답·스냅샷·작업 입력 등 기능별 구조화 payload |

**실제 제약**

- `central_dataset_snapshots_pkey`: `PRIMARY KEY (kind, subject, snapshot_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_dataset_snapshots_pkey ON public.central_dataset_snapshots USING btree (kind, subject, snapshot_key);
CREATE INDEX idx_central_dataset_lookup ON public.central_dataset_snapshots USING btree (kind, subject, snapshot_key DESC);
```

**논리 관계**

- [종류별 관측 스냅샷](TABLES.md#central_dataset_snapshots) → [시장 관측 revision 이력](TABLES.md#central_observation_revisions): `일부 kind만 revision 추가; 모든 snapshot의 1:1 대응 아님` (관측 이력)

**코드 근거**

- [central_schema_migrations](../../src/kiwoom_monitor/central_server/central_schema.py) (L233) — write
- [PostgresQueryStore.save_dataset_snapshots](../../src/kiwoom_monitor/central_server/database.py) (L3564) — write
- [PostgresQueryStore.load_dataset_snapshots](../../src/kiwoom_monitor/central_server/database.py) (L3631) — read
- [PostgresQueryStore.load_top20_statistics](../../src/kiwoom_monitor/central_server/database.py) (L3678) — read

<a id="central_documents"></a>
## 기능별 문서 저장소 — `central_documents`

collection·owner·document_key별 JSON 문서.

- **쓰는 곳/계기:** upsert_documents / replace_documents 및 전용 저장 함수
- **읽는 곳/용도:** load_document(s)·뉴스/설정/매매일지/후보 기능
- **저장 경계:** 문서 종류마다 최신값·이력·완료 표식 의미가 다름; 한 테이블이 한 기능은 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `collection` | `text` | 아니오 | — | 문서 종류. 같은 테이블 안 기능별 논리 구획 |
| `owner` | `text` | 아니오 | — | 문서 소유/분리 범위. 종목·날짜·프로필·계좌 등 collection별로 다름 |
| `document_key` | `text` | 아니오 | — | collection·owner 내부 문서 키 |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_documents_pkey`: `PRIMARY KEY (collection, owner, document_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_documents_pkey ON public.central_documents USING btree (collection, owner, document_key);
CREATE INDEX idx_central_documents_lookup ON public.central_documents USING btree (collection, owner, updated_at DESC);
```

**논리 관계**

- [테마 구성 이력](TABLES.md#central_theme_snapshots) → [기능별 문서 저장소](TABLES.md#central_documents): `theme_metadata/profile 문서에서 만들어진 snapshot` (테마 현재와 이력)
- [기사 원본 revision](TABLES.md#central_news_article_revisions) → [기능별 문서 저장소](TABLES.md#central_documents): `news_article 현재 문서와 기사 revision` (현재와 이력)
- [뉴스 AI 분석 이력](TABLES.md#central_news_ai_revisions) → [기능별 문서 저장소](TABLES.md#central_documents): `news_ai/news_ai_shared 최신 문서와 AI 이력` (현재와 이력)

**코드 근거**

- [_create_credential_profile](../../src/kiwoom_monitor/central_server/database.py) (L579) — read
- [_save_real_account_recovery](../../src/kiwoom_monitor/central_server/database.py) (L665) — write
- [_save_real_account_event](../../src/kiwoom_monitor/central_server/database.py) (L688) — write
- [_load_market_profile_settings](../../src/kiwoom_monitor/central_server/database.py) (L697) — read
- [_save_market_profile_settings](../../src/kiwoom_monitor/central_server/database.py) (L753) — write
- [_load_account_settings](../../src/kiwoom_monitor/central_server/database.py) (L762) — read
- [_save_account_settings](../../src/kiwoom_monitor/central_server/database.py) (L826) — write
- [_require_execution_ownership](../../src/kiwoom_monitor/central_server/database.py) (L956) — read
- [PostgresQueryStore.storage_breakdown](../../src/kiwoom_monitor/central_server/database.py) (L2864) — read
- [PostgresQueryStore.upsert_documents](../../src/kiwoom_monitor/central_server/database.py) (L4129) — write
- [PostgresQueryStore.replace_documents](../../src/kiwoom_monitor/central_server/database.py) (L4169) — write
- [PostgresQueryStore.load_document](../../src/kiwoom_monitor/central_server/database.py) (L4209) — read
- [PostgresQueryStore.save_news_ai_results](../../src/kiwoom_monitor/central_server/database.py) (L4610) — write
- [PostgresQueryStore.load_mock_automation_control](../../src/kiwoom_monitor/central_server/database.py) (L5141) — read
- [PostgresQueryStore.save_mock_automation_control](../../src/kiwoom_monitor/central_server/database.py) (L5186) — read
- [_complete_external_news_job](../../src/kiwoom_monitor/central_server/database.py) (L6158) — write
- [_confirmed_news_articles_query](../../src/kiwoom_monitor/central_server/database.py) (L7182) — read
- [_market_news_feed_sql](../../src/kiwoom_monitor/central_server/database.py) (L7208) — read
- 나머지 정적 참조는 `catalog.json`에 포함.

<a id="central_execution_account_snapshots"></a>
## 실행 계좌 상태 관측 — `central_execution_account_snapshots`

계좌의 잔고·예수금 등 한 관측 문서.

- **쓰는 곳/계기:** save_execution_account_snapshot
- **읽는 곳/용도:** 실행 증거/복구 입력; 전용 table reader는 직접 참조 검색에서 미확인
- **저장 경계:** account_ref·environment로 격리; 문서에 상세 상태 보존

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `snapshot_id` | `text` | 아니오 | — | 테마/계좌 관측 snapshot 식별자. 테이블별 범위 |
| `environment` | `text` | 아니오 | — | 실계좌/모의 등 실행 환경 |
| `account_ref` | `text` | 아니오 | — | 검증된 계좌의 불투명 식별자. 계좌번호 원문이 아님 |
| `as_of` | `timestamp with time zone` | 아니오 | — | 계좌 상태가 가리키는 기준 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `received_at` | `timestamp with time zone` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_execution_account_snapshots_pkey`: `PRIMARY KEY (snapshot_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_execution_account_snapshots_pkey ON public.central_execution_account_snapshots USING btree (snapshot_id);
CREATE INDEX idx_central_execution_account_snapshots ON public.central_execution_account_snapshots USING btree (environment, account_ref, as_of DESC);
```

**논리 관계**

- [실행 계좌 상태 관측](TABLES.md#central_execution_account_snapshots) → [계좌 신원 등록부](TABLES.md#central_account_registry): `account_ref; environment 함께 검증` (계좌 관측)

**코드 근거**

- [PostgresQueryStore.save_execution_account_snapshot](../../src/kiwoom_monitor/central_server/database.py) (L5122) — write

<a id="central_execution_events"></a>
## 주문 상태·체결 이벤트 원장 — `central_execution_events`

주문 의도의 상태 변화/체결 1개.

- **쓰는 곳/계기:** append_execution_event
- **읽는 곳/용도:** 계좌 이벤트·복구·매매일지 입력
- **저장 경계:** intent 상태 갱신과 이벤트 append를 함께 확정

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_execution_events_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `event_id` | `text` | 아니오 | — | 논리 사건/이벤트 식별자. 소속 테이블별 네임스페이스 |
| `intent_id` | `text` | 아니오 | — | 내부 주문 의도 식별자 |
| `state` | `text` | 아니오 | — | 작업/주문 처리 상태. 테이블마다 상태 머신이 다름 |
| `occurred_at` | `timestamp with time zone` | 아니오 | — | 주문 이벤트가 발생한 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `received_at` | `timestamp with time zone` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `broker_execution_id` | `text` | 아니오 | — | 증권사 체결/실행 식별자. 내부 event_id와 구분 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_execution_events_event_id_key`: `UNIQUE (event_id)`
- `central_execution_events_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_execution_events_event_id_key ON public.central_execution_events USING btree (event_id);
CREATE UNIQUE INDEX central_execution_events_pkey ON public.central_execution_events USING btree (accepted_sequence);
CREATE INDEX idx_central_execution_events_intent ON public.central_execution_events USING btree (intent_id, accepted_sequence);
```

**논리 관계**

- [주문 상태·체결 이벤트 원장](TABLES.md#central_execution_events) → [주문 의도 현재 상태](TABLES.md#central_execution_intents): `intent_id → intent_id` (주문 상태 이력)

**코드 근거**

- [PostgresQueryStore.append_execution_event](../../src/kiwoom_monitor/central_server/database.py) (L5019) — write
- [PostgresQueryStore.load_execution_events](../../src/kiwoom_monitor/central_server/database.py) (L5082) — read
- [PostgresQueryStore.load_account_execution_events](../../src/kiwoom_monitor/central_server/database.py) (L5099) — read

<a id="central_execution_intents"></a>
## 주문 의도 현재 상태 — `central_execution_intents`

계좌·환경·run에 속한 주문 의도.

- **쓰는 곳/계기:** create_execution_intent / append_execution_event
- **읽는 곳/용도:** 활성 주문 조회·체결 연계·복구
- **저장 경계:** 주문 이벤트와 현재 상태 일치 검증; DB 기록이 실제 주문 승인 자체는 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `intent_id` | `text` | 아니오 | — | 내부 주문 의도 식별자 |
| `run_id` | `text` | 아니오 | — | 실행 묶음 식별자. 뉴스 수집 run과 주문 실행 run은 같은 범위가 아님 |
| `environment` | `text` | 아니오 | — | 실계좌/모의 등 실행 환경 |
| `account_ref` | `text` | 아니오 | — | 검증된 계좌의 불투명 식별자. 계좌번호 원문이 아님 |
| `state` | `text` | 아니오 | — | 작업/주문 처리 상태. 테이블마다 상태 머신이 다름 |
| `broker_order_id` | `text` | 아니오 | — | 증권사 주문 번호. 내부 intent_id와 구분 |
| `last_broker_as_of` | `timestamp with time zone` | 예 | — | 마지막 반영한 증권사 기준 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `created_at` | `timestamp with time zone` | 아니오 | — | 해당 기록 생성 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `updated_at` | `timestamp with time zone` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_execution_intents_pkey`: `PRIMARY KEY (intent_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_execution_intents_pkey ON public.central_execution_intents USING btree (intent_id);
CREATE INDEX idx_central_execution_intents_scope ON public.central_execution_intents USING btree (environment, account_ref, run_id, state);
```

**논리 관계**

- [주문 의도 현재 상태](TABLES.md#central_execution_intents) → [계좌 신원 등록부](TABLES.md#central_account_registry): `account_ref; environment 함께 검증` (계좌 범위)
- [주문 상태·체결 이벤트 원장](TABLES.md#central_execution_events) → [주문 의도 현재 상태](TABLES.md#central_execution_intents): `intent_id → intent_id` (주문 상태 이력)
- [주문 의도 현재 상태](TABLES.md#central_execution_intents) → [실행 소유권 임대](TABLES.md#central_execution_runtime_leases): `쓰기 전 owner_key/owner_token 검증; 직접 FK 아님` (소유권 검사)

**코드 근거**

- [_require_execution_ownership](../../src/kiwoom_monitor/central_server/database.py) (L975) — read
- [PostgresQueryStore.create_execution_intent](../../src/kiwoom_monitor/central_server/database.py) (L5001) — write
- [PostgresQueryStore.append_execution_event](../../src/kiwoom_monitor/central_server/database.py) (L5027) — write
- [PostgresQueryStore.load_execution_intent](../../src/kiwoom_monitor/central_server/database.py) (L5048) — read
- [PostgresQueryStore.find_execution_intent_by_broker_order_id](../../src/kiwoom_monitor/central_server/database.py) (L5064) — read
- [PostgresQueryStore.load_account_execution_events](../../src/kiwoom_monitor/central_server/database.py) (L5099) — read
- [PostgresQueryStore.load_active_execution_intents](../../src/kiwoom_monitor/central_server/database.py) (L5160) — read

<a id="central_execution_runtime_leases"></a>
## 실행 소유권 임대 — `central_execution_runtime_leases`

owner_key별 소유 토큰·만료.

- **쓰는 곳/계기:** acquire_execution_runtime / release_execution_runtime
- **읽는 곳/용도:** 쓰기 전 _require_execution_ownership
- **저장 경계:** 주문 안전 fence; 일반 writer들을 직렬화하는 공통 락 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `owner_key` | `text` | 아니오 | — | 실행 임대를 나누는 논리 소유 범위 |
| `owner_token` | `text` | 아니오 | — | 임대 소유권 검증 토큰. 문서에는 실제 값을 포함하지 않음 |
| `lease_expires_at` | `timestamp with time zone` | 아니오 | — | 실행 소유권 임대의 만료 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `updated_at` | `timestamp with time zone` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 PostgreSQL timestamptz(시간대 있는 시각). |

**실제 제약**

- `central_execution_runtime_leases_pkey`: `PRIMARY KEY (owner_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_execution_runtime_leases_pkey ON public.central_execution_runtime_leases USING btree (owner_key);
```

**논리 관계**

- [주문 의도 현재 상태](TABLES.md#central_execution_intents) → [실행 소유권 임대](TABLES.md#central_execution_runtime_leases): `쓰기 전 owner_key/owner_token 검증; 직접 FK 아님` (소유권 검사)

**코드 근거**

- [_require_execution_ownership](../../src/kiwoom_monitor/central_server/database.py) (L947) — read
- [PostgresQueryStore.release_execution_runtime](../../src/kiwoom_monitor/central_server/database.py) (L2584) — write
- [PostgresQueryStore.acquire_execution_runtime](../../src/kiwoom_monitor/central_server/database.py) (L5217) — write

<a id="central_external_bars"></a>
## 외부시장 봉 — `central_external_bars`

공급자·지표/상품·계약·주기·시각별 봉.

- **쓰는 곳/계기:** 외부시장 수집 → save_external_bars
- **읽는 곳/용도:** load_external_bars, 외부시장 차트·상태
- **저장 경계:** 국내 KRX/NXT 봉과 별도; 계약과 주기를 보존

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `provider` | `text` | 아니오 | — | 외부 데이터/AI/자격증명 공급자. 소속 테이블별 의미가 다름 |
| `instrument` | `text` | 아니오 | — | 외부시장 지표/상품 식별자 |
| `contract` | `text` | 아니오 | — | 외부시장 선물 등의 계약/월물 구분 |
| `timeframe` | `text` | 아니오 | — | 외부시장 봉의 집계 주기 |
| `bar_time` | `timestamp with time zone` | 아니오 | — | 공급자 봉의 기준 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `open` | `double precision` | 예 | — | 봉의 시가 |
| `high` | `double precision` | 예 | — | 봉의 고가 |
| `low` | `double precision` | 예 | — | 봉의 저가 |
| `close` | `double precision` | 예 | — | 봉의 종가. 가격 단위는 해당 시장/공급자 계약 |
| `volume` | `double precision` | 예 | — | 봉의 거래량. 시장/공급자 단위에 따름 |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_external_bars_pkey`: `PRIMARY KEY (provider, instrument, contract, timeframe, bar_time)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_external_bars_pkey ON public.central_external_bars USING btree (provider, instrument, contract, timeframe, bar_time);
CREATE INDEX idx_central_external_bars_lookup ON public.central_external_bars USING btree (instrument, timeframe, bar_time DESC);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.save_external_bars](../../src/kiwoom_monitor/central_server/database.py) (L4964) — write
- [PostgresQueryStore.load_external_bars](../../src/kiwoom_monitor/central_server/database.py) (L4984) — read

<a id="central_five_minute_bars"></a>
## 과거 5분봉 — `central_five_minute_bars`

공급자·수정주가·시각 의미까지 구분한 과거 봉.

- **쓰는 곳/계기:** 과거자료 이관 → save_five_minute_bars
- **읽는 곳/용도:** load_five_minute_bars, 과거자료·연구 조회
- **저장 경계:** 현재 0B 분봉과 다른 입력 계약; raw 거래대금을 임의 환산하지 않음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `trading_date` | `date` | 아니오 | — | 거래일. 단순 수신일과 다를 수 있음 |
| `minute` | `time without time zone` | 아니오 | — | 봉이 속한 분 시각. trading_date와 함께 사용 |
| `code` | `text` | 아니오 | — | 종목 코드 |
| `market` | `text` | 아니오 | — | 봉 원본 시장 KRX/NXT/SOR. COMBINED는 읽을 때 만든 결과이며 저장 시장 아님 |
| `provider` | `text` | 아니오 | — | 외부 데이터/AI/자격증명 공급자. 소속 테이블별 의미가 다름 |
| `adjustment_mode` | `text` | 아니오 | — | 원본/수정주가 등 공급자의 가격 조정 구분 |
| `bar_time_semantics` | `text` | 아니오 | — | 봉 시각이 시작/종료 등 무엇을 뜻하는지 나타내는 규약 |
| `open` | `bigint` | 아니오 | — | 봉의 시가 |
| `high` | `bigint` | 아니오 | — | 봉의 고가 |
| `low` | `bigint` | 아니오 | — | 봉의 저가 |
| `close` | `bigint` | 아니오 | — | 봉의 종가. 가격 단위는 해당 시장/공급자 계약 |
| `volume` | `bigint` | 아니오 | — | 봉의 거래량. 시장/공급자 단위에 따름 |
| `trading_value_raw` | `bigint` | 예 | — | 공급자가 제공한 원시 거래대금. provider 계약 없이는 원/백만원으로 단정하지 않음 |
| `observed_at` | `timestamp with time zone` | 아니오 | — | 공급자 봉을 관측/확보한 시각 PostgreSQL timestamptz(시간대 있는 시각). |

**실제 제약**

- `central_five_minute_bars_adjustment_mode_check`: `CHECK ((adjustment_mode = ANY (ARRAY['raw'::text, 'adjusted'::text])))`
- `central_five_minute_bars_bar_time_semantics_check`: `CHECK ((bar_time_semantics = 'interval_end'::text))`
- `central_five_minute_bars_pkey`: `PRIMARY KEY (trading_date, minute, code, market, provider, adjustment_mode)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_five_minute_bars_pkey ON public.central_five_minute_bars USING btree (trading_date, minute, code, market, provider, adjustment_mode);
CREATE INDEX idx_central_five_minute_bars_lookup ON public.central_five_minute_bars USING btree (code, trading_date, minute);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.save_five_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L3169) — write
- [PostgresQueryStore.load_five_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L3181) — read

<a id="central_hot_cohort_current"></a>
## 현재 조건편입 대상 — `central_hot_cohort_current`

종목별 현재 hot 상태.

- **쓰는 곳/계기:** record_hot_cohort_revision
- **읽는 곳/용도:** load_hot_cohort·관심 대상/구독 선택
- **저장 경계:** 현재값; 과거 이력은 revisions에 별도 보존

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `stock_name` | `text` | 아니오 | — | 기록 당시 종목명 |
| `condition_name` | `text` | 아니오 | — | 조건검색 조건 이름 |
| `first_seen_at` | `double precision` | 아니오 | — | 해당 조건편입 대상을 처음 관측한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `entry_session` | `text` | 아니오 | — | 조건편입이 시작된 시장 세션 구분 |
| `last_signal` | `text` | 아니오 | — | 현재 대상에 마지막으로 반영한 조건 신호 |
| `last_signal_at` | `double precision` | 아니오 | — | 마지막 조건 신호 시각 이 컬럼은 Unix epoch 초로 저장. |
| `active` | `boolean` | 아니오 | — | 현재 대상 활성 여부 |
| `nxt_eligible` | `boolean` | 예 | — | NXT 대상 여부의 저장 상태 |
| `expired_at` | `double precision` | 예 | — | 현재 조건편입 대상의 만료 시각 이 컬럼은 Unix epoch 초로 저장. |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_hot_cohort_current_pkey`: `PRIMARY KEY (stock_code)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_hot_cohort_current_pkey ON public.central_hot_cohort_current USING btree (stock_code);
```

**논리 관계**

- [현재 조건편입 대상](TABLES.md#central_hot_cohort_current) → [조건편입 변화 이력](TABLES.md#central_hot_cohort_revisions): `stock_code; 최신 투영과 변화 이력` (현재와 이력)

**코드 근거**

- [PostgresQueryStore.record_hot_cohort_revision](../../src/kiwoom_monitor/central_server/database.py) (L4884) — write
- [PostgresQueryStore.load_hot_cohort](../../src/kiwoom_monitor/central_server/database.py) (L4899) — read

<a id="central_hot_cohort_revisions"></a>
## 조건편입 변화 이력 — `central_hot_cohort_revisions`

조건검색 hot 대상의 상태 변화 1개.

- **쓰는 곳/계기:** record_hot_cohort_revision
- **읽는 곳/용도:** 조건편입 계보·현재 투영 근거
- **저장 경계:** current 투영과 같은 저장 transaction

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_hot_cohort_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `revision_id` | `text` | 아니오 | — | 관측 revision 고유 ID |
| `revision_key` | `text` | 아니오 | — | 조건편입 변화의 중복 판단 키 |
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `event_type` | `text` | 아니오 | — | 이벤트 종류. 테이블마다 허용 값과 의미가 다름 |
| `condition_name` | `text` | 아니오 | — | 조건검색 조건 이름 |
| `condition_seq` | `text` | 아니오 | — | 조건검색 조건 식별 순번 |
| `session_id` | `text` | 아니오 | — | 시장 이벤트의 거래 세션 식별자 |
| `effective_at` | `double precision` | 아니오 | — | 시장 사실/판단이 적용되는 기준 시각. 수신·가용 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_hot_cohort_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`
- `central_hot_cohort_revisions_revision_id_key`: `UNIQUE (revision_id)`
- `central_hot_cohort_revisions_revision_key_key`: `UNIQUE (revision_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_hot_cohort_revisions_pkey ON public.central_hot_cohort_revisions USING btree (accepted_sequence);
CREATE UNIQUE INDEX central_hot_cohort_revisions_revision_id_key ON public.central_hot_cohort_revisions USING btree (revision_id);
CREATE UNIQUE INDEX central_hot_cohort_revisions_revision_key_key ON public.central_hot_cohort_revisions USING btree (revision_key);
CREATE INDEX idx_central_hot_cohort_lookup ON public.central_hot_cohort_revisions USING btree (stock_code, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [현재 조건편입 대상](TABLES.md#central_hot_cohort_current) → [조건편입 변화 이력](TABLES.md#central_hot_cohort_revisions): `stock_code; 최신 투영과 변화 이력` (현재와 이력)

**코드 근거**

- [PostgresQueryStore.record_hot_cohort_revision](../../src/kiwoom_monitor/central_server/database.py) (L4876) — write

<a id="central_market_data_observation_meta"></a>
## 현재 봉의 시간·출처 근거 — `central_market_data_observation_meta`

데이터 종류·대상·관측 키별 최신 메타데이터.

- **쓰는 곳/계기:** 봉 저장 → _save_postgres_metadata
- **읽는 곳/용도:** 봉 품질·완료 판정·query/realtime 권위 검사
- **저장 경계:** available_at은 단순 최종 조회 시각이 아님; 값·상태·출처 변화와 함께 해석

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `dataset_kind` | `text` | 아니오 | — | 메타데이터 대상 종류(예: 분봉/일봉) |
| `subject` | `text` | 아니오 | — | 데이터 대상 키. 종목·시장 포함키·집계 대상 등 kind별로 다름 |
| `observation_key` | `text` | 아니오 | — | 해당 관측의 논리 키. dataset/subject 또는 kind와 함께 의미가 결정됨 |
| `effective_at` | `timestamp with time zone` | 예 | — | 시장 사실/판단이 적용되는 기준 시각. 수신·가용 시각과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `available_at` | `timestamp with time zone` | 예 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `venue` | `text` | 아니오 | — | 관측 시장/거래 장소 |
| `unit` | `text` | 아니오 | — | 관측 payload의 단위 명세 |
| `value_kind` | `text` | 아니오 | — | 실측/추정 등 값 성격 |
| `completeness` | `text` | 아니오 | — | 진행 중/구간 종료/최종화 등 관측 완결성 |
| `origin` | `text` | 아니오 | — | 조회/실시간/이관 등 관측 생성 방식 |
| `source` | `text` | 아니오 | ''::text | 메타데이터/시장 사실의 출처 |
| `candidate_universe` | `text` | 아니오 | — | 이 관측이 속한 후보군 식별/범위 |

**실제 제약**

- `central_market_data_observation_meta_pkey`: `PRIMARY KEY (dataset_kind, subject, observation_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_market_data_observation_meta_pkey ON public.central_market_data_observation_meta USING btree (dataset_kind, subject, observation_key);
```

**논리 관계**

- [시장별 분봉](TABLES.md#central_minute_bars) → [현재 봉의 시간·출처 근거](TABLES.md#central_market_data_observation_meta): `minute_bar / 종목·시장 / 거래일·분 관측키` (품질·가용성)
- [시장별 일봉](TABLES.md#central_daily_bars) → [현재 봉의 시간·출처 근거](TABLES.md#central_market_data_observation_meta): `daily_bar / 종목·시장 / 거래일 관측키` (품질·가용성)
- [시장 관측 revision 이력](TABLES.md#central_observation_revisions) → [현재 봉의 시간·출처 근거](TABLES.md#central_market_data_observation_meta): `종류·대상·관측키; 최신 메타와 불변 이력의 대응` (관측 계보)

**코드 근거**

- [central_schema_migrations](../../src/kiwoom_monitor/central_server/central_schema.py) (L239) — write
- [PostgresQueryStore.load_market_data_metadata](../../src/kiwoom_monitor/central_server/database.py) (L3942) — read
- [PostgresQueryStore.load_market_data_metadata_range](../../src/kiwoom_monitor/central_server/database.py) (L3963) — read
- [_market_metadata_upsert_sql](../../src/kiwoom_monitor/central_server/database.py) (L7311) — write
- [_minute_query_authority](../../src/kiwoom_monitor/central_server/database.py) (L7814) — read
- [_load_postgres_minute_query_authorities](../../src/kiwoom_monitor/central_server/database.py) (L7848) — read
- [_save_postgres_metadata](../../src/kiwoom_monitor/central_server/database.py) (L8185) — write

<a id="central_minute_bar_operations"></a>
## 분봉 재처리 방지 원장 — `central_minute_bar_operations`

실시간 분봉 저장 operation 1개.

- **쓰는 곳/계기:** save_minute_bars / finalize_minute_bars
- **읽는 곳/용도:** 분봉 저장의 동일 operation 재시도 확인
- **저장 경계:** 봉 갱신과 함께 확정; 한 operation이 여러 봉을 포함할 수 있음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `operation_id` | `text` | 아니오 | — | 멱등 작업 식별자. 테이블별 작업 범위가 다름 |
| `operation_hash` | `text` | 아니오 | — | operation의 입력 내용 hash. 같은 ID의 다른 내용 충돌 검출 |
| `processed_at` | `timestamp with time zone` | 아니오 | — | 분봉 operation 처리 확정 시각 PostgreSQL timestamptz(시간대 있는 시각). |

**실제 제약**

- `central_minute_bar_operations_pkey`: `PRIMARY KEY (operation_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_minute_bar_operations_pkey ON public.central_minute_bar_operations USING btree (operation_id);
```

**논리 관계**

- [시장별 분봉](TABLES.md#central_minute_bars) → [분봉 재처리 방지 원장](TABLES.md#central_minute_bar_operations): `저장 batch의 operation_id; 봉 행에 FK 컬럼 없음` (재처리 방지)

**코드 근거**

- [PostgresQueryStore.save_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L3063) — write
- [PostgresQueryStore.finalize_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L3092) — read
- [_load_postgres_minute_operation_hashes](../../src/kiwoom_monitor/central_server/database.py) (L7832) — read

<a id="central_minute_bars"></a>
## 시장별 분봉 — `central_minute_bars`

종목·거래일·분·시장 1개 봉.

- **쓰는 곳/계기:** 0B 집계 → save_minute_bars; ka10080 → replace_minute_bars; finalize_minute_bars
- **읽는 곳/용도:** 분봉 API·메인 차트·매매일지·연구
- **저장 경계:** 실시간 재전송과 조회 확정값의 우선순위를 검사; KRX/NXT/SOR를 별도 행으로 보존

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `trading_date` | `date` | 아니오 | — | 거래일. 단순 수신일과 다를 수 있음 |
| `minute` | `time without time zone` | 아니오 | — | 봉이 속한 분 시각. trading_date와 함께 사용 |
| `code` | `text` | 아니오 | — | 종목 코드 |
| `market` | `text` | 아니오 | — | 봉 원본 시장 KRX/NXT/SOR. COMBINED는 읽을 때 만든 결과이며 저장 시장 아님 |
| `open` | `bigint` | 아니오 | — | 봉의 시가 |
| `high` | `bigint` | 아니오 | — | 봉의 고가 |
| `low` | `bigint` | 아니오 | — | 봉의 저가 |
| `close` | `bigint` | 아니오 | — | 봉의 종가. 가격 단위는 해당 시장/공급자 계약 |
| `volume` | `bigint` | 아니오 | — | 봉의 거래량. 시장/공급자 단위에 따름 |
| `trade_value_million_won` | `bigint` | 아니오 | — | 거래대금, 백만원 단위 |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_minute_bars_pkey`: `PRIMARY KEY (trading_date, minute, code, market)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_minute_bars_pkey ON public.central_minute_bars USING btree (trading_date, minute, code, market);
CREATE INDEX idx_central_minute_bars_lookup ON public.central_minute_bars USING btree (code, trading_date, minute);
```

**논리 관계**

- [시장별 분봉](TABLES.md#central_minute_bars) → [분봉 재처리 방지 원장](TABLES.md#central_minute_bar_operations): `저장 batch의 operation_id; 봉 행에 FK 컬럼 없음` (재처리 방지)
- [시장별 분봉](TABLES.md#central_minute_bars) → [현재 봉의 시간·출처 근거](TABLES.md#central_market_data_observation_meta): `minute_bar / 종목·시장 / 거래일·분 관측키` (품질·가용성)

**코드 근거**

- [PostgresQueryStore.save_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L3025) — write
- [PostgresQueryStore.load_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L3147) — read
- [_load_postgres_minute_bar](../../src/kiwoom_monitor/central_server/database.py) (L7594) — read
- [replace_minute_bars](../../src/kiwoom_monitor/central_server/database.py) (L1009) — dynamic boundary
- [_replace_bars](../../src/kiwoom_monitor/central_server/database.py) (L1501) — dynamic boundary

<a id="central_news_ai_revisions"></a>
## 뉴스 AI 분석 이력 — `central_news_ai_revisions`

기사·본문·모델·프롬프트·입력별 분석.

- **쓰는 곳/계기:** save_news_ai_results → _save_postgres_news_ai
- **읽는 곳/용도:** 뉴스 AI 결과·동일 입력 재사용
- **저장 경계:** 출력/사용량과 관련 최신 문서 투영을 원자적으로 기록하는 경로

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_ai_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `analysis_revision_id` | `text` | 아니오 | — | AI 분석 revision 식별자 |
| `target_id` | `text` | 아니오 | — | 뉴스 분석/작업 대상 식별자. article revision ID와 별개 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `body_revision_id` | `text` | 예 | — | 근거 본문 추출 revision 식별자 |
| `provider` | `text` | 아니오 | — | 외부 데이터/AI/자격증명 공급자. 소속 테이블별 의미가 다름 |
| `model` | `text` | 아니오 | — | AI 모델 식별/버전 |
| `prompt_version` | `text` | 아니오 | — | AI 분석 프롬프트 버전 |
| `schema_version` | `text` | 아니오 | — | 관측/AI 출력 문서의 schema 버전. DB migration 버전과 별개 |
| `input_hash` | `text` | 아니오 | — | 분석/작업 입력의 hash. 같은 입력 재처리 판별 |
| `computed_at` | `double precision` | 아니오 | — | 분석 결과 계산 시각 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `output_json` | `jsonb` | 아니오 | — | AI 분석 구조화 출력 |
| `usage_json` | `jsonb` | 아니오 | — | AI 사용량/요금 산정 근거 등의 구조화 정보 |

**실제 제약**

- `central_news_ai_revisions_analysis_revision_id_key`: `UNIQUE (analysis_revision_id)`
- `central_news_ai_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_ai_revisions_analysis_revision_id_key ON public.central_news_ai_revisions USING btree (analysis_revision_id);
CREATE UNIQUE INDEX central_news_ai_revisions_pkey ON public.central_news_ai_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_news_ai_lookup ON public.central_news_ai_revisions USING btree (target_id, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [뉴스 AI 분석 이력](TABLES.md#central_news_ai_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (AI 기사 근거)
- [뉴스 AI 분석 이력](TABLES.md#central_news_ai_revisions) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `body_revision_id → body_revision_id` (AI 본문 근거)
- [뉴스 AI 분석 이력](TABLES.md#central_news_ai_revisions) → [기능별 문서 저장소](TABLES.md#central_documents): `news_ai/news_ai_shared 최신 문서와 AI 이력` (현재와 이력)

**코드 근거**

- [PostgresQueryStore.find_news_ai_revision](../../src/kiwoom_monitor/central_server/database.py) (L4836) — read
- [_save_postgres_news_ai](../../src/kiwoom_monitor/central_server/database.py) (L6338) — write
- [_news_history_query](../../src/kiwoom_monitor/central_server/database.py) (L7099) — read

<a id="central_news_article_revisions"></a>
## 기사 원본 revision — `central_news_article_revisions`

기사 내용·수집 범위별 관측 revision.

- **쓰는 곳/계기:** 기사 저장 또는 source page 저장
- **읽는 곳/용도:** 뉴스 목록·BODY/RULE/AI 작업·과거뉴스 archive reader
- **저장 경계:** content_hash와 revision_of로 내용 변경 구분; 단순 제목 키 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_article_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `identity` | `text` | 아니오 | — | 기사 동일성을 나타내는 키(URL 등에서 정규화). DB revision ID와 다름 |
| `content_hash` | `text` | 아니오 | — | 정규화한 내용의 hash. 동일 내용 재사용/변경 판별 |
| `collector_id` | `text` | 아니오 | — | 기사를 수집한 주체/수집기 식별자 |
| `published_at` | `text` | 예 | — | 원문 기사 발행시각 |
| `received_at` | `double precision` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `collection_scope` | `text` | 아니오 | — | 기사 수집 범위(실시간/과거 등). 처리 책임과 구분해 해석 |
| `revision_of` | `text` | 예 | — | 이전 revision 식별자. 해당 테이블 안 계보 연결 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_news_article_revisions_article_revision_id_key`: `UNIQUE (article_revision_id)`
- `central_news_article_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_article_revisions_article_revision_id_key ON public.central_news_article_revisions USING btree (article_revision_id);
CREATE UNIQUE INDEX central_news_article_revisions_pkey ON public.central_news_article_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_news_article_lookup ON public.central_news_article_revisions USING btree (stock_code, identity, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (본문의 원기사)
- [기사와 종목의 관련성 이력](TABLES.md#central_news_article_target_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (종목 관련성)
- [뉴스 AI 분석 이력](TABLES.md#central_news_ai_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (AI 기사 근거)
- [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (사건 기사 근거)
- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (근거 기사)
- [뉴스 처리 작업 큐](TABLES.md#central_news_jobs) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (처리 입력)
- [뉴스 출처별 발견 이력](TABLES.md#central_news_source_observations) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (발견한 기사)
- [기사 원본 revision](TABLES.md#central_news_article_revisions) → [기능별 문서 저장소](TABLES.md#central_documents): `news_article 현재 문서와 기사 revision` (현재와 이력)
- [기사 원본 revision](TABLES.md#central_news_article_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `revision_of → 해당 revision ID` (이전 revision)

**코드 근거**

- [PostgresQueryStore.claim_external_historical_news_job](../../src/kiwoom_monitor/central_server/database.py) (L4446) — read
- [PostgresQueryStore.load_news_article_revision](../../src/kiwoom_monitor/central_server/database.py) (L4674) — read
- [_append_postgres_news_articles](../../src/kiwoom_monitor/central_server/database.py) (L5945) — read
- [_enqueue_postgres_news_ai_jobs](../../src/kiwoom_monitor/central_server/database.py) (L6032) — read
- [_complete_external_news_job](../../src/kiwoom_monitor/central_server/database.py) (L6103) — read
- [_save_postgres_news_body](../../src/kiwoom_monitor/central_server/database.py) (L6290) — read
- [_save_postgres_news_event](../../src/kiwoom_monitor/central_server/database.py) (L6444) — read
- [_save_postgres_news_source_page](../../src/kiwoom_monitor/central_server/database.py) (L6711) — read
- [_postgres_possible_related](../../src/kiwoom_monitor/central_server/database.py) (L7069) — read
- [_news_history_query](../../src/kiwoom_monitor/central_server/database.py) (L7090) — read
- [_confirmed_news_articles_query](../../src/kiwoom_monitor/central_server/database.py) (L7177) — read
- [_stock_news_articles_query](../../src/kiwoom_monitor/central_server/database.py) (L7236) — read
- [HistoricalNewsArchiveReader.article_by_id](../../src/kiwoom_monitor/central_server/historical_news_archive.py) (L151) — read

<a id="central_news_article_target_revisions"></a>
## 기사와 종목의 관련성 이력 — `central_news_article_target_revisions`

기사 revision과 종목의 관련성 근거.

- **쓰는 곳/계기:** source page 대상 판정 → _insert_postgres_article_target
- **읽는 곳/용도:** 확정 종목 뉴스 목록·GLOBAL 기사 RULE 대상
- **저장 경계:** confirmed/관련성 판단은 코드 규칙; 종목명이 보인다고 자동 확정 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_article_target_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `target_revision_id` | `text` | 아니오 | — | 기사-종목 관련성 revision 식별자 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `identity` | `text` | 아니오 | — | 기사 동일성을 나타내는 키(URL 등에서 정규화). DB revision ID와 다름 |
| `stock_code` | `text` | 예 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `stock_name` | `text` | 예 | — | 기록 당시 종목명 |
| `relation_status` | `text` | 아니오 | — | 기사-종목 관련성의 확정 상태 |
| `evidence_text` | `text` | 아니오 | — | 기사와 종목의 관련성 판단 근거 텍스트 |
| `rule_version` | `text` | 아니오 | — | 뉴스 판단/대상 매핑 규칙 버전 |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `revision_of` | `text` | 예 | — | 이전 revision 식별자. 해당 테이블 안 계보 연결 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_news_article_target_r_article_revision_id_stock_cod_key`: `UNIQUE (article_revision_id, stock_code, stock_name, relation_status, rule_version)`
- `central_news_article_target_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`
- `central_news_article_target_revisions_target_revision_id_key`: `UNIQUE (target_revision_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_article_target_r_article_revision_id_stock_cod_key ON public.central_news_article_target_revisions USING btree (article_revision_id, stock_code, stock_name, relation_status, rule_version);
CREATE UNIQUE INDEX central_news_article_target_revisions_pkey ON public.central_news_article_target_revisions USING btree (accepted_sequence);
CREATE UNIQUE INDEX central_news_article_target_revisions_target_revision_id_key ON public.central_news_article_target_revisions USING btree (target_revision_id);
CREATE INDEX idx_central_news_article_targets_lookup ON public.central_news_article_target_revisions USING btree (article_revision_id, available_at DESC, accepted_sequence DESC);
CREATE INDEX idx_central_news_targets_stock_lookup ON public.central_news_article_target_revisions USING btree (stock_code, relation_status, article_revision_id, accepted_sequence DESC);
```

**논리 관계**

- [기사와 종목의 관련성 이력](TABLES.md#central_news_article_target_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (종목 관련성)
- [기사와 종목의 관련성 이력](TABLES.md#central_news_article_target_revisions) → [기사와 종목의 관련성 이력](TABLES.md#central_news_article_target_revisions): `revision_of → 해당 revision ID` (이전 revision)

**코드 근거**

- [_save_postgres_news_body](../../src/kiwoom_monitor/central_server/database.py) (L6302) — read
- [_insert_postgres_article_target](../../src/kiwoom_monitor/central_server/database.py) (L6777) — read
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L7018) — read
- [_confirmed_news_articles_query](../../src/kiwoom_monitor/central_server/database.py) (L7177) — read

<a id="central_news_body_revisions"></a>
## 뉴스 본문 추출 이력 — `central_news_body_revisions`

기사 revision의 본문 추출 결과.

- **쓰는 곳/계기:** 뉴스 BODY 처리 또는 외부 완료 → _save_postgres_news_body
- **읽는 곳/용도:** RULE/AI 처리·뉴스 상세
- **저장 경계:** 본문 저장과 후속 RULE enqueue가 같은 경로; 없는 article은 실패

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_body_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `body_revision_id` | `text` | 아니오 | — | 근거 본문 추출 revision 식별자 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `content_hash` | `text` | 아니오 | — | 정규화한 내용의 hash. 동일 내용 재사용/변경 판별 |
| `extractor_version` | `text` | 아니오 | — | 본문 추출 규칙/추출기 버전 |
| `fetched_at` | `double precision` | 아니오 | — | 뉴스 본문을 가져온 시각 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `status` | `text` | 아니오 | — | 본문 추출/계좌/상한가 등의 상태. 테이블별 계약 |
| `body_text` | `text` | 아니오 | — | 추출한 뉴스 본문 텍스트 |
| `error` | `text` | 아니오 | — | 실패 사유/오류 기록. 비밀정보를 기록하는 필드가 아님 |

**실제 제약**

- `central_news_body_revisions_body_revision_id_key`: `UNIQUE (body_revision_id)`
- `central_news_body_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_body_revisions_body_revision_id_key ON public.central_news_body_revisions USING btree (body_revision_id);
CREATE UNIQUE INDEX central_news_body_revisions_pkey ON public.central_news_body_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_news_body_lookup ON public.central_news_body_revisions USING btree (article_revision_id, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (본문의 원기사)
- [뉴스 AI 분석 이력](TABLES.md#central_news_ai_revisions) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `body_revision_id → body_revision_id` (AI 본문 근거)
- [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `body_revision_id → body_revision_id` (사건 본문 근거)
- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `body_revision_id → body_revision_id` (근거 본문)
- [뉴스 처리 작업 큐](TABLES.md#central_news_jobs) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `payload_json의 body_revision_id 또는 BODY output_ref; 단계별` (다형 결과/입력)

**코드 근거**

- [PostgresQueryStore.load_news_body_revision](../../src/kiwoom_monitor/central_server/database.py) (L4656) — read
- [_enqueue_postgres_news_ai_jobs](../../src/kiwoom_monitor/central_server/database.py) (L6039) — read
- [_complete_external_news_job](../../src/kiwoom_monitor/central_server/database.py) (L6141) — read
- [_save_postgres_news_body](../../src/kiwoom_monitor/central_server/database.py) (L6281) — write
- [_enqueue_postgres_existing_body_rule](../../src/kiwoom_monitor/central_server/database.py) (L6803) — read
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L7010) — read
- [_news_history_query](../../src/kiwoom_monitor/central_server/database.py) (L7095) — read
- [_confirmed_news_articles_query](../../src/kiwoom_monitor/central_server/database.py) (L7182) — read
- [_stock_news_articles_query](../../src/kiwoom_monitor/central_server/database.py) (L7240) — read
- [HistoricalNewsArchiveReader.article_by_id](../../src/kiwoom_monitor/central_server/historical_news_archive.py) (L159) — read

<a id="central_news_event_membership_revisions"></a>
## 사건과 근거 기사의 연결 이력 — `central_news_event_membership_revisions`

사건 revision에 연결된 기사·본문.

- **쓰는 곳/계기:** _save_postgres_news_event
- **읽는 곳/용도:** 사건 근거·기사 상세·archive reader
- **저장 경계:** 사건 revision과 같은 transaction에 저장

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_event_membership_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `membership_revision_id` | `text` | 아니오 | — | 뉴스 사건-기사 연결 revision 식별자 |
| `event_id` | `text` | 아니오 | — | 논리 사건/이벤트 식별자. 소속 테이블별 네임스페이스 |
| `event_revision_id` | `text` | 아니오 | — | 뉴스 사건 판단 revision 식별자 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `body_revision_id` | `text` | 아니오 | — | 근거 본문 추출 revision 식별자 |
| `relation` | `text` | 아니오 | — | 사건과 기사 사이의 관계 분류 |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `revision_of` | `text` | 예 | — | 이전 revision 식별자. 해당 테이블 안 계보 연결 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_news_event_membership_event_revision_id_article_rev_key`: `UNIQUE (event_revision_id, article_revision_id, body_revision_id)`
- `central_news_event_membership_revisi_membership_revision_id_key`: `UNIQUE (membership_revision_id)`
- `central_news_event_membership_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_event_membership_event_revision_id_article_rev_key ON public.central_news_event_membership_revisions USING btree (event_revision_id, article_revision_id, body_revision_id);
CREATE UNIQUE INDEX central_news_event_membership_revisi_membership_revision_id_key ON public.central_news_event_membership_revisions USING btree (membership_revision_id);
CREATE UNIQUE INDEX central_news_event_membership_revisions_pkey ON public.central_news_event_membership_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_news_membership_lookup ON public.central_news_event_membership_revisions USING btree (event_id, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions): `event_revision_id → event_revision_id; event_id도 보존` (사건 연결)
- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (근거 기사)
- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `body_revision_id → body_revision_id` (근거 본문)
- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions): `revision_of → 해당 revision ID` (이전 revision)

**코드 근거**

- [_save_postgres_news_event](../../src/kiwoom_monitor/central_server/database.py) (L6454) — read
- [_postgres_possible_related](../../src/kiwoom_monitor/central_server/database.py) (L7069) — read
- [_news_history_query](../../src/kiwoom_monitor/central_server/database.py) (L7110) — read
- [HistoricalNewsArchiveReader.article_by_id](../../src/kiwoom_monitor/central_server/historical_news_archive.py) (L173) — read

<a id="central_news_event_revisions"></a>
## 뉴스 사건 판단 이력 — `central_news_event_revisions`

수주 등 논리 사건의 판단 revision.

- **쓰는 곳/계기:** RULE 결과 → _save_postgres_news_event
- **읽는 곳/용도:** 뉴스 사건 조회·후보 분석
- **저장 경계:** event_id는 사건, event_revision_id는 판단 버전; 혼동 금지

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_event_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `event_revision_id` | `text` | 아니오 | — | 뉴스 사건 판단 revision 식별자 |
| `event_id` | `text` | 아니오 | — | 논리 사건/이벤트 식별자. 소속 테이블별 네임스페이스 |
| `event_key` | `text` | 예 | — | 사건 동일성·중복 판단용 논리 키 |
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `event_type` | `text` | 아니오 | — | 이벤트 종류. 테이블마다 허용 값과 의미가 다름 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `body_revision_id` | `text` | 아니오 | — | 근거 본문 추출 revision 식별자 |
| `rule_version` | `text` | 아니오 | — | 뉴스 판단/대상 매핑 규칙 버전 |
| `input_hash` | `text` | 아니오 | — | 분석/작업 입력의 hash. 같은 입력 재처리 판별 |
| `role` | `text` | 아니오 | — | 사건에서 대상 종목이 수행하는 역할 |
| `scope` | `text` | 아니오 | — | 뉴스 수집/요청/사건의 적용 범위. 테이블별 해석 |
| `certainty` | `text` | 아니오 | — | 사건 확실성 분류 |
| `novelty` | `text` | 아니오 | — | 사건 신규성 분류 |
| `amount_won` | `bigint` | 예 | — | 판단된 금액, 원 단위 |
| `counterparty` | `text` | 예 | — | 사건의 계약 상대방 등 상대 주체 |
| `importance_score` | `integer` | 아니오 | — | 사건 중요도 점수 |
| `confidence_score` | `integer` | 아니오 | — | 판단 신뢰 점수. 모델/규칙 버전과 함께 해석 |
| `novelty_score` | `integer` | 아니오 | — | 사건 신규성 점수 |
| `ai_required` | `boolean` | 아니오 | — | 추가 AI 분석이 필요한지 나타내는 판단 값 |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `revision_of` | `text` | 예 | — | 이전 revision 식별자. 해당 테이블 안 계보 연결 |
| `result_json` | `jsonb` | 아니오 | — | 뉴스 규칙/사건 판단의 구조화 결과 |

**실제 제약**

- `central_news_event_revisions_article_revision_id_body_revis_key`: `UNIQUE (article_revision_id, body_revision_id, rule_version, input_hash)`
- `central_news_event_revisions_event_revision_id_key`: `UNIQUE (event_revision_id)`
- `central_news_event_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_event_revisions_article_revision_id_body_revis_key ON public.central_news_event_revisions USING btree (article_revision_id, body_revision_id, rule_version, input_hash);
CREATE UNIQUE INDEX central_news_event_revisions_event_revision_id_key ON public.central_news_event_revisions USING btree (event_revision_id);
CREATE UNIQUE INDEX central_news_event_revisions_pkey ON public.central_news_event_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_news_event_lookup ON public.central_news_event_revisions USING btree (event_id, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (사건 기사 근거)
- [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `body_revision_id → body_revision_id` (사건 본문 근거)
- [사건과 근거 기사의 연결 이력](TABLES.md#central_news_event_membership_revisions) → [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions): `event_revision_id → event_revision_id; event_id도 보존` (사건 연결)
- [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions) → [뉴스 사건 판단 이력](TABLES.md#central_news_event_revisions): `revision_of → 해당 revision ID` (이전 revision)

**코드 근거**

- [_save_postgres_news_event](../../src/kiwoom_monitor/central_server/database.py) (L6436) — read
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L7014) — read
- [_postgres_possible_related](../../src/kiwoom_monitor/central_server/database.py) (L7069) — read
- [_news_history_query](../../src/kiwoom_monitor/central_server/database.py) (L7104) — read
- [HistoricalNewsArchiveReader.article_by_id](../../src/kiwoom_monitor/central_server/historical_news_archive.py) (L173) — read

<a id="central_news_jobs"></a>
## 뉴스 처리 작업 큐 — `central_news_jobs`

기사/대상/단계/버전별 작업 1개.

- **쓰는 곳/계기:** enqueue → claim_news_jobs → finish/retry
- **읽는 곳/용도:** NewsJobRunner·PC 외부 처리 claim
- **저장 경계:** BODY/RULE/AI, 재시도·선점 소유권; 기사 원본 이력과 다름

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `job_key` | `text` | 아니오 | — | 뉴스 작업의 멱등 식별 키 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `target_id` | `text` | 아니오 | — | 뉴스 분석/작업 대상 식별자. article revision ID와 별개 |
| `stage` | `text` | 아니오 | — | 뉴스 처리 단계(BODY/RULE/AI) |
| `input_hash` | `text` | 아니오 | — | 분석/작업 입력의 hash. 같은 입력 재처리 판별 |
| `processing_version` | `text` | 아니오 | — | 뉴스 단계 처리 규칙/구현 버전 |
| `attempts` | `integer` | 아니오 | — | 작업 시도 횟수. 재시도 상한 판단에 사용 |
| `next_retry_at` | `double precision` | 아니오 | — | 뉴스 작업이 재시도 가능해지는 시각 이 컬럼은 Unix epoch 초로 저장. |
| `state` | `text` | 아니오 | — | 작업/주문 처리 상태. 테이블마다 상태 머신이 다름 |
| `output_ref` | `text` | 아니오 | — | 완료한 작업의 결과 ID. stage에 따라 본문/사건/분석 등 대상이 달라짐 |
| `error` | `text` | 아니오 | — | 실패 사유/오류 기록. 비밀정보를 기록하는 필드가 아님 |
| `payload_json` | `jsonb` | 아니오 | — | 응답·스냅샷·작업 입력 등 기능별 구조화 payload |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_news_jobs_pkey`: `PRIMARY KEY (job_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_jobs_pkey ON public.central_news_jobs USING btree (job_key);
CREATE INDEX idx_central_news_jobs_claim_order ON public.central_news_jobs USING btree (stage, processing_version, updated_at, next_retry_at) WHERE (state = 'PENDING'::text);
CREATE INDEX idx_central_news_jobs_ready ON public.central_news_jobs USING btree (state, next_retry_at, updated_at);
```

**논리 관계**

- [뉴스 처리 작업 큐](TABLES.md#central_news_jobs) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (처리 입력)
- [뉴스 처리 작업 큐](TABLES.md#central_news_jobs) → [뉴스 본문 추출 이력](TABLES.md#central_news_body_revisions): `payload_json의 body_revision_id 또는 BODY output_ref; 단계별` (다형 결과/입력)

**코드 근거**

- [PostgresQueryStore.claim_news_jobs](../../src/kiwoom_monitor/central_server/database.py) (L4265) — write
- [PostgresQueryStore.explain_news_job_claim_plan](../../src/kiwoom_monitor/central_server/database.py) (L4330) — write
- [PostgresQueryStore.claim_external_historical_news_job](../../src/kiwoom_monitor/central_server/database.py) (L4446) — read
- [PostgresQueryStore.finish_news_job](../../src/kiwoom_monitor/central_server/database.py) (L4552) — write
- [PostgresQueryStore.retry_news_job](../../src/kiwoom_monitor/central_server/database.py) (L4569) — read
- [_insert_postgres_news_job](../../src/kiwoom_monitor/central_server/database.py) (L5992) — write
- [_enqueue_postgres_news_ai_jobs](../../src/kiwoom_monitor/central_server/database.py) (L6051) — write
- [_complete_external_news_job](../../src/kiwoom_monitor/central_server/database.py) (L6103) — read
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L7006) — read

<a id="central_news_request_budget"></a>
## 뉴스 요청 한도 — `central_news_request_budget`

날짜·scope별 소비한 요청 수.

- **쓰는 곳/계기:** claim_news_request
- **읽는 곳/용도:** 요청 허용 판정·news_request_count
- **저장 경계:** 원자적 소비 카운터; 기사 수와 같은 값이 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `budget_date` | `text` | 아니오 | — | 뉴스 요청 한도를 계산하는 날짜 |
| `scope` | `text` | 아니오 | — | 뉴스 수집/요청/사건의 적용 범위. 테이블별 해석 |
| `request_count` | `integer` | 아니오 | — | 요청 소비/실행 횟수. 기사 건수와 구분 |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_news_request_budget_pkey`: `PRIMARY KEY (budget_date, scope)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_request_budget_pkey ON public.central_news_request_budget USING btree (budget_date, scope);
```

**논리 관계**

- [뉴스 수집 실행 이력](TABLES.md#central_news_source_runs) → [뉴스 요청 한도](TABLES.md#central_news_request_budget): `날짜·scope 예산; 직접 행 FK 없음` (요청 한도)

**코드 근거**

- [PostgresQueryStore.claim_news_request](../../src/kiwoom_monitor/central_server/database.py) (L4726) — read
- [PostgresQueryStore.news_request_count](../../src/kiwoom_monitor/central_server/database.py) (L4754) — read
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L7003) — read

<a id="central_news_source_cursors"></a>
## 뉴스 수집 진행 위치 — `central_news_source_cursors`

source_id별 이어받기/일정 상태.

- **쓰는 곳/계기:** source page 저장 → cursor 갱신
- **읽는 곳/용도:** 다음 페이지·재개 지점·수집 진단
- **저장 경계:** 페이지 자료와 진행 근거를 함께 저장; 완료 추정으로 원본을 생략하지 않음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `source_id` | `text` | 아니오 | — | 관측 공급원 또는 뉴스 수집 source 식별자. 종류별 범위 |
| `scope` | `text` | 아니오 | — | 뉴스 수집/요청/사건의 적용 범위. 테이블별 해석 |
| `query_text` | `text` | 아니오 | — | 뉴스 수집에 사용한 검색어 |
| `cursor_published_at` | `text` | 예 | — | 확정된 뉴스 수집 경계의 발행시각 |
| `cursor_identity` | `text` | 아니오 | — | 확정된 뉴스 수집 경계의 기사 identity |
| `pending_published_at` | `text` | 예 | — | 아직 확정되지 않은 다음 수집 경계 발행시각 |
| `pending_identity` | `text` | 아니오 | — | 아직 확정되지 않은 다음 수집 경계의 기사 identity |
| `next_start` | `integer` | 아니오 | — | 다음 수집 페이지 시작 위치 |
| `next_schedule_at` | `double precision` | 아니오 | — | 다음 뉴스 수집 예정 시각 이 컬럼은 Unix epoch 초로 저장. |
| `checked_at` | `double precision` | 예 | — | 수집/진행 상태를 검사한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `last_success` | `double precision` | 예 | — | 마지막 수집 성공 시각 |
| `coverage` | `text` | 아니오 | — | 수집 범위/연속성 충족 상태. 단순 행 존재와 다름 |
| `truncated` | `boolean` | 아니오 | — | 수집 범위/결과가 제한 때문에 잘렸는지 |
| `error` | `text` | 아니오 | — | 실패 사유/오류 기록. 비밀정보를 기록하는 필드가 아님 |
| `updated_at` | `double precision` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_news_source_cursors_pkey`: `PRIMARY KEY (source_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_source_cursors_pkey ON public.central_news_source_cursors USING btree (source_id);
```

**논리 관계**

- [뉴스 수집 실행 이력](TABLES.md#central_news_source_runs) → [뉴스 수집 진행 위치](TABLES.md#central_news_source_cursors): `source_id; 같은 수집원 상태` (진행 위치)

**코드 근거**

- [PostgresQueryStore.load_news_source_cursor](../../src/kiwoom_monitor/central_server/database.py) (L4771) — read
- [_upsert_postgres_source_cursor](../../src/kiwoom_monitor/central_server/database.py) (L6822) — write
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L6965) — read

<a id="central_news_source_observations"></a>
## 뉴스 출처별 발견 이력 — `central_news_source_observations`

실행·페이지에서 기사를 발견한 관측.

- **쓰는 곳/계기:** source page 저장
- **읽는 곳/용도:** 시장 뉴스 feed·출처 추적·중복 진단
- **저장 경계:** 동일 기사를 여러 출처에서 봐도 관측 이력 보존

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_source_observations_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `observation_id` | `text` | 아니오 | — | 뉴스 출처 관측 1개의 고유 ID |
| `run_id` | `text` | 아니오 | — | 실행 묶음 식별자. 뉴스 수집 run과 주문 실행 run은 같은 범위가 아님 |
| `source_id` | `text` | 아니오 | — | 관측 공급원 또는 뉴스 수집 source 식별자. 종류별 범위 |
| `query_text` | `text` | 아니오 | — | 뉴스 수집에 사용한 검색어 |
| `page_start` | `integer` | 아니오 | — | 뉴스 검색/수집 페이지의 시작 위치 |
| `article_revision_id` | `text` | 아니오 | — | 근거 기사 revision 식별자 |
| `identity` | `text` | 아니오 | — | 기사 동일성을 나타내는 키(URL 등에서 정규화). DB revision ID와 다름 |
| `published_at` | `text` | 예 | — | 원문 기사 발행시각 |
| `received_at` | `double precision` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `content_hash` | `text` | 아니오 | — | 정규화한 내용의 hash. 동일 내용 재사용/변경 판별 |
| `duplicate` | `boolean` | 아니오 | — | 이번 발견이 기존 기사와 중복인지 표시 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_news_source_observations_observation_id_key`: `UNIQUE (observation_id)`
- `central_news_source_observations_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_source_observations_observation_id_key ON public.central_news_source_observations USING btree (observation_id);
CREATE UNIQUE INDEX central_news_source_observations_pkey ON public.central_news_source_observations USING btree (accepted_sequence);
CREATE INDEX idx_central_news_source_identity_lookup ON public.central_news_source_observations USING btree (source_id, identity, accepted_sequence DESC);
CREATE INDEX idx_central_news_source_observations_lookup ON public.central_news_source_observations USING btree (source_id, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [뉴스 출처별 발견 이력](TABLES.md#central_news_source_observations) → [기사 원본 revision](TABLES.md#central_news_article_revisions): `article_revision_id → article_revision_id` (발견한 기사)
- [뉴스 출처별 발견 이력](TABLES.md#central_news_source_observations) → [뉴스 수집 실행 이력](TABLES.md#central_news_source_runs): `run_id; 여러 page/revision이 있을 수 있음` (발견한 실행)

**코드 근거**

- [_save_postgres_news_source_page](../../src/kiwoom_monitor/central_server/database.py) (L6705) — read
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L6975) — read
- [_market_news_feed_sql](../../src/kiwoom_monitor/central_server/database.py) (L7206) — read

<a id="central_news_source_runs"></a>
## 뉴스 수집 실행 이력 — `central_news_source_runs`

run의 페이지/상태 revision.

- **쓰는 곳/계기:** save_news_source_page / 과거시장 batch
- **읽는 곳/용도:** 수집 진행·요청·중복 진단
- **저장 경계:** run_id가 같아도 revision/page가 여러 개일 수 있음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_news_source_runs_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `run_revision_id` | `text` | 아니오 | — | 뉴스 수집 실행 상태 revision ID |
| `run_id` | `text` | 아니오 | — | 실행 묶음 식별자. 뉴스 수집 run과 주문 실행 run은 같은 범위가 아님 |
| `source_id` | `text` | 아니오 | — | 관측 공급원 또는 뉴스 수집 source 식별자. 종류별 범위 |
| `scope` | `text` | 아니오 | — | 뉴스 수집/요청/사건의 적용 범위. 테이블별 해석 |
| `page_start` | `integer` | 아니오 | — | 뉴스 검색/수집 페이지의 시작 위치 |
| `checked_at` | `double precision` | 아니오 | — | 수집/진행 상태를 검사한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `completed_at` | `double precision` | 아니오 | — | 수집 실행의 완료 시각 이 컬럼은 Unix epoch 초로 저장. |
| `raw_count` | `integer` | 아니오 | — | 수집한 원시 결과 건수 |
| `unique_count` | `integer` | 아니오 | — | 중복을 제거한 수집 결과 건수 |
| `duplicate_count` | `integer` | 아니오 | — | 수집 실행/페이지에서 중복으로 판정한 건수 |
| `request_count` | `integer` | 아니오 | — | 요청 소비/실행 횟수. 기사 건수와 구분 |
| `budget_remaining` | `integer` | 아니오 | — | 해당 수집 시점에 관측한 남은 요청 예산 |
| `truncated` | `boolean` | 아니오 | — | 수집 범위/결과가 제한 때문에 잘렸는지 |
| `coverage` | `text` | 아니오 | — | 수집 범위/연속성 충족 상태. 단순 행 존재와 다름 |
| `error` | `text` | 아니오 | — | 실패 사유/오류 기록. 비밀정보를 기록하는 필드가 아님 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_news_source_runs_pkey`: `PRIMARY KEY (accepted_sequence)`
- `central_news_source_runs_run_revision_id_key`: `UNIQUE (run_revision_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_news_source_runs_pkey ON public.central_news_source_runs USING btree (accepted_sequence);
CREATE UNIQUE INDEX central_news_source_runs_run_revision_id_key ON public.central_news_source_runs USING btree (run_revision_id);
CREATE INDEX idx_central_news_source_runs_lookup ON public.central_news_source_runs USING btree (source_id, checked_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [뉴스 출처별 발견 이력](TABLES.md#central_news_source_observations) → [뉴스 수집 실행 이력](TABLES.md#central_news_source_runs): `run_id; 여러 page/revision이 있을 수 있음` (발견한 실행)
- [뉴스 수집 실행 이력](TABLES.md#central_news_source_runs) → [뉴스 수집 진행 위치](TABLES.md#central_news_source_cursors): `source_id; 같은 수집원 상태` (진행 위치)
- [뉴스 수집 실행 이력](TABLES.md#central_news_source_runs) → [뉴스 요청 한도](TABLES.md#central_news_request_budget): `날짜·scope 예산; 직접 행 FK 없음` (요청 한도)

**코드 근거**

- [PostgresQueryStore.save_historical_market_news_batch](../../src/kiwoom_monitor/central_server/database.py) (L4533) — read
- [_save_postgres_news_source_page](../../src/kiwoom_monitor/central_server/database.py) (L6759) — write
- [_load_postgres_news_source_diagnostics](../../src/kiwoom_monitor/central_server/database.py) (L6970) — read

<a id="central_observation_revisions"></a>
## 시장 관측 revision 이력 — `central_observation_revisions`

한 관측의 불변 revision 1개.

- **쓰는 곳/계기:** 봉/관측 snapshot 저장 → revision append
- **읽는 곳/용도:** after_sequence reader·shadow 분석·고정 연구 export
- **저장 경계:** 현재 봉과 과거 관측 이력을 분리; revision_of로 이전 revision 연결

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_observation_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `revision_id` | `text` | 아니오 | — | 관측 revision 고유 ID |
| `observation_key` | `text` | 아니오 | — | 해당 관측의 논리 키. dataset/subject 또는 kind와 함께 의미가 결정됨 |
| `schema_version` | `integer` | 아니오 | — | 관측/AI 출력 문서의 schema 버전. DB migration 버전과 별개 |
| `source_id` | `text` | 아니오 | — | 관측 공급원 또는 뉴스 수집 source 식별자. 종류별 범위 |
| `source_session_id` | `text` | 아니오 | — | 원본 공급 스트림의 세션 |
| `source_sequence` | `text` | 아니오 | — | 원본 공급 스트림의 순서. 중앙 accepted_sequence와 별개 |
| `kind` | `text` | 아니오 | — | 데이터셋/관측 종류. 허용 종류마다 payload 의미가 다름 |
| `subject` | `text` | 아니오 | — | 데이터 대상 키. 종목·시장 포함키·집계 대상 등 kind별로 다름 |
| `venue` | `text` | 아니오 | — | 관측 시장/거래 장소 |
| `effective_at` | `timestamp with time zone` | 예 | — | 시장 사실/판단이 적용되는 기준 시각. 수신·가용 시각과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `received_at` | `timestamp with time zone` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `available_at` | `timestamp with time zone` | 예 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `revision_of` | `text` | 예 | — | 이전 revision 식별자. 해당 테이블 안 계보 연결 |
| `payload_hash` | `text` | 아니오 | — | 관측 payload의 동일성 hash |
| `unit` | `text` | 아니오 | — | 관측 payload의 단위 명세 |
| `value_kind` | `text` | 아니오 | — | 실측/추정 등 값 성격 |
| `completeness` | `text` | 아니오 | — | 진행 중/구간 종료/최종화 등 관측 완결성 |
| `origin` | `text` | 아니오 | — | 조회/실시간/이관 등 관측 생성 방식 |
| `candidate_universe` | `text` | 아니오 | — | 이 관측이 속한 후보군 식별/범위 |
| `quality_flags_json` | `jsonb` | 아니오 | — | 관측 품질 플래그 목록/구조 |
| `clock_quality` | `text` | 아니오 | — | 원본 시각의 신뢰/품질 구분 |
| `source_ref_json` | `jsonb` | 아니오 | — | 원본 기록을 다시 찾기 위한 참조 문서 |
| `payload_json` | `jsonb` | 아니오 | — | 응답·스냅샷·작업 입력 등 기능별 구조화 payload |

**실제 제약**

- `central_observation_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`
- `central_observation_revisions_revision_id_key`: `UNIQUE (revision_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_observation_revisions_pkey ON public.central_observation_revisions USING btree (accepted_sequence);
CREATE UNIQUE INDEX central_observation_revisions_revision_id_key ON public.central_observation_revisions USING btree (revision_id);
CREATE INDEX idx_central_observation_available ON public.central_observation_revisions USING btree (kind, available_at, accepted_sequence);
CREATE INDEX idx_central_observation_lookup ON public.central_observation_revisions USING btree (kind, subject, observation_key, source_id, accepted_sequence DESC);
```

**논리 관계**

- [시장 관측 revision 이력](TABLES.md#central_observation_revisions) → [현재 봉의 시간·출처 근거](TABLES.md#central_market_data_observation_meta): `종류·대상·관측키; 최신 메타와 불변 이력의 대응` (관측 계보)
- [시장 관측 revision 이력](TABLES.md#central_observation_revisions) → [시장 관측 revision 이력](TABLES.md#central_observation_revisions): `revision_of → revision_id` (이전 revision)
- [고정 연구 데이터셋 구성원](TABLES.md#central_research_export_members) → [시장 관측 revision 이력](TABLES.md#central_observation_revisions): `revision_id → revision_id` (고정 원본)
- [종류별 관측 스냅샷](TABLES.md#central_dataset_snapshots) → [시장 관측 revision 이력](TABLES.md#central_observation_revisions): `일부 kind만 revision 추가; 모든 snapshot의 1:1 대응 아님` (관측 이력)

**코드 근거**

- [PostgresQueryStore.load_observation_revisions_after](../../src/kiwoom_monitor/central_server/database.py) (L3751) — read
- [PostgresQueryStore.create_observation_export](../../src/kiwoom_monitor/central_server/database.py) (L3878) — read
- [_observation_revision_select](../../src/kiwoom_monitor/central_server/database.py) (L7340) — read
- [_observation_export_page_sql](../../src/kiwoom_monitor/central_server/database.py) (L7530) — read
- [_append_postgres_observation_revision](../../src/kiwoom_monitor/central_server/database.py) (L7801) — read
- [_load_postgres_latest_revisions](../../src/kiwoom_monitor/central_server/database.py) (L7897) — read
- [_insert_postgres_observation_revision](../../src/kiwoom_monitor/central_server/database.py) (L7928) — write
- [_insert_postgres_observation_revisions_batch.flush](../../src/kiwoom_monitor/central_server/database.py) (L7985) — write

<a id="central_realtime_latest"></a>
## 실시간 종류별 최신값 — `central_realtime_latest`

이벤트 종류·대상 키별 마지막 수신값.

- **쓰는 곳/계기:** 실시간 수신 → save_realtime_snapshots
- **읽는 곳/용도:** 실시간 snapshot API·시가총액 등 현재값 복구
- **저장 경계:** 같은 키를 갱신하는 최신 투영; 모든 체결 이력 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `event_type` | `text` | 아니오 | — | 이벤트 종류. 테이블마다 허용 값과 의미가 다름 |
| `item_key` | `text` | 아니오 | — | 실시간 이벤트 대상 키(종목 등). event_type과 함께 유일 |
| `received_at` | `double precision` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `event_json` | `jsonb` | 아니오 | — | 최근 실시간 이벤트의 구조화 원문/정규화 값 |

**실제 제약**

- `central_realtime_latest_pkey`: `PRIMARY KEY (event_type, item_key)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_realtime_latest_pkey ON public.central_realtime_latest USING btree (event_type, item_key);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.save_realtime_snapshots](../../src/kiwoom_monitor/central_server/database.py) (L2883) — write
- [PostgresQueryStore.load_realtime_snapshots](../../src/kiwoom_monitor/central_server/database.py) (L2903) — read
- [PostgresQueryStore.load_latest_market_caps](../../src/kiwoom_monitor/central_server/database.py) (L2922) — read

<a id="central_research_export_members"></a>
## 고정 연구 데이터셋 구성원 — `central_research_export_members`

데이터셋의 순서 있는 revision 1개.

- **쓰는 곳/계기:** create_observation_export
- **읽는 곳/용도:** 고정 export 페이지 읽기
- **저장 경계:** manifest와 member 저장은 함께 확정; 최신 봉을 다시 골라 넣지 않음

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `dataset_id` | `text` | 아니오 | — | 고정 연구 export 데이터셋 식별자 |
| `ordinal` | `integer` | 아니오 | — | 고정 export 안에서 revision이 나타나는 순서 |
| `revision_id` | `text` | 아니오 | — | 관측 revision 고유 ID |

**실제 제약**

- `central_research_export_members_dataset_id_revision_id_key`: `UNIQUE (dataset_id, revision_id)`
- `central_research_export_members_pkey`: `PRIMARY KEY (dataset_id, ordinal)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_research_export_members_dataset_id_revision_id_key ON public.central_research_export_members USING btree (dataset_id, revision_id);
CREATE UNIQUE INDEX central_research_export_members_pkey ON public.central_research_export_members USING btree (dataset_id, ordinal);
```

**논리 관계**

- [고정 연구 데이터셋 구성원](TABLES.md#central_research_export_members) → [고정 연구 데이터셋 명세](TABLES.md#central_research_exports): `dataset_id → dataset_id` (고정 묶음)
- [고정 연구 데이터셋 구성원](TABLES.md#central_research_export_members) → [시장 관측 revision 이력](TABLES.md#central_observation_revisions): `revision_id → revision_id` (고정 원본)

**코드 근거**

- [PostgresQueryStore.create_observation_export](../../src/kiwoom_monitor/central_server/database.py) (L3897) — write
- [_observation_export_page_sql](../../src/kiwoom_monitor/central_server/database.py) (L7530) — read

<a id="central_research_exports"></a>
## 고정 연구 데이터셋 명세 — `central_research_exports`

고정된 revision 묶음 1개.

- **쓰는 곳/계기:** create_observation_export
- **읽는 곳/용도:** load_observation_export_page·PC 연구 입력
- **저장 경계:** 범위뿐 아니라 실제 member 목록을 고정

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `dataset_id` | `text` | 아니오 | — | 고정 연구 export 데이터셋 식별자 |
| `created_at` | `timestamp with time zone` | 아니오 | — | 해당 기록 생성 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `start_at` | `timestamp with time zone` | 아니오 | — | 고정 export 요청 범위 시작 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `end_at` | `timestamp with time zone` | 아니오 | — | 고정 export 요청 범위의 종료 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `kinds_json` | `jsonb` | 아니오 | — | 고정 export에 포함할 관측 종류 목록 |
| `subject` | `text` | 아니오 | — | 데이터 대상 키. 종목·시장 포함키·집계 대상 등 kind별로 다름 |
| `revision_count` | `integer` | 아니오 | — | 고정 export에 포함된 revision 수 |
| `revision_ids_hash` | `text` | 아니오 | — | 고정된 revision ID 목록의 검증 hash |
| `manifest_json` | `jsonb` | 아니오 | — | 고정 연구 입력의 범위·계보·검증 정보를 담은 명세 |

**실제 제약**

- `central_research_exports_pkey`: `PRIMARY KEY (dataset_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_research_exports_pkey ON public.central_research_exports USING btree (dataset_id);
```

**논리 관계**

- [고정 연구 데이터셋 구성원](TABLES.md#central_research_export_members) → [고정 연구 데이터셋 명세](TABLES.md#central_research_exports): `dataset_id → dataset_id` (고정 묶음)

**코드 근거**

- [PostgresQueryStore.create_observation_export](../../src/kiwoom_monitor/central_server/database.py) (L3893) — write
- [PostgresQueryStore.load_observation_export_page](../../src/kiwoom_monitor/central_server/database.py) (L3915) — read

<a id="central_schema_migrations"></a>
## 중앙 스키마 적용 원장 — `central_schema_migrations`

적용한 migration 버전 1개.

- **쓰는 곳/계기:** 중앙 schema migration runner
- **읽는 곳/용도:** 초기화 호환성·스키마 검증
- **저장 경계:** 현재 실 DB 최대 버전20; health.schema_version과 다른 번호

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `version` | `integer` | 아니오 | — | 중앙 DB migration 순서 번호 |
| `name` | `text` | 아니오 | — | 스키마 migration 이름 |
| `applied_at` | `timestamp with time zone` | 아니오 | — | 스키마 변경 적용 시각 PostgreSQL timestamptz(시간대 있는 시각). |

**실제 제약**

- `central_schema_migrations_pkey`: `PRIMARY KEY (version)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_schema_migrations_pkey ON public.central_schema_migrations USING btree (version);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [downgrade_schema](../../src/kiwoom_monitor/central_server/shadow_checkpoint.py) (L176) — read
- [_apply_plan](../../src/kiwoom_monitor/central_server/schema_migrations.py) (L58) — dynamic boundary

<a id="central_second_trade_bars"></a>
## 시장별 초봉 — `central_second_trade_bars`

종목·거래일·초·시장별 체결 집계.

- **쓰는 곳/계기:** 0B 메모리 집계 → save_second_trade_bars
- **읽는 곳/용도:** 저장·연구용 원본; 전용 read API 사용은 이 조사에서 확인하지 못함
- **저장 경계:** 최신값·분봉과 독립 저장 호출; 초봉 거래대금은 원 단위

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `trading_date` | `date` | 아니오 | — | 거래일. 단순 수신일과 다를 수 있음 |
| `trade_second` | `time without time zone` | 아니오 | — | 초봉의 초 시각. trading_date와 함께 사용 |
| `code` | `text` | 아니오 | — | 종목 코드 |
| `market` | `text` | 아니오 | — | 봉 원본 시장 KRX/NXT/SOR. COMBINED는 읽을 때 만든 결과이며 저장 시장 아님 |
| `open` | `bigint` | 아니오 | — | 봉의 시가 |
| `high` | `bigint` | 아니오 | — | 봉의 고가 |
| `low` | `bigint` | 아니오 | — | 봉의 저가 |
| `close` | `bigint` | 아니오 | — | 봉의 종가. 가격 단위는 해당 시장/공급자 계약 |
| `volume` | `bigint` | 아니오 | — | 봉의 거래량. 시장/공급자 단위에 따름 |
| `trade_value_won` | `bigint` | 아니오 | — | 거래대금, 원 단위 |
| `trade_count` | `bigint` | 아니오 | — | 초 구간에 집계한 체결 수 |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |

**실제 제약**

- `central_second_trade_bars_pkey`: `PRIMARY KEY (trading_date, trade_second, code, market)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_second_trade_bars_pkey ON public.central_second_trade_bars USING btree (trading_date, trade_second, code, market);
CREATE INDEX idx_central_second_trade_bars_lookup ON public.central_second_trade_bars USING btree (code, trading_date, trade_second);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.save_second_trade_bars](../../src/kiwoom_monitor/central_server/database.py) (L3127) — write

<a id="central_shadow_candidate_events"></a>
## 화면에 전달할 후보 이벤트 — `central_shadow_candidate_events`

monitor의 candidate 사건 1개.

- **쓰는 곳/계기:** save_shadow_evaluation
- **읽는 곳/용도:** research/candidates API·PC 후보 polling
- **저장 경계:** accepted_sequence로 증분 조회; expires_at은 표시 유효기간이며 자동 삭제와 별개

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_shadow_candidate_events_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `event_id` | `text` | 아니오 | — | 논리 사건/이벤트 식별자. 소속 테이블별 네임스페이스 |
| `monitor_id` | `text` | 아니오 | — | 후보 분석 monitor 식별자 |
| `available_at` | `timestamp with time zone` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `expires_at` | `timestamp with time zone` | 아니오 | — | 캐시 또는 후보 이벤트가 유효한 기한. 단위는 아래 자료형·설명 참조 PostgreSQL timestamptz(시간대 있는 시각). |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_shadow_candidate_events_event_id_key`: `UNIQUE (event_id)`
- `central_shadow_candidate_events_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_shadow_candidate_events_event_id_key ON public.central_shadow_candidate_events USING btree (event_id);
CREATE UNIQUE INDEX central_shadow_candidate_events_pkey ON public.central_shadow_candidate_events USING btree (accepted_sequence);
CREATE INDEX idx_central_shadow_candidate_available ON public.central_shadow_candidate_events USING btree (available_at, accepted_sequence);
```

**논리 관계**

- [화면에 전달할 후보 이벤트](TABLES.md#central_shadow_candidate_events) → [후보 판단 원장](TABLES.md#central_shadow_decisions): `document_json 안의 판단 근거; 같은 평가 transaction` (판단과 전달)

**코드 근거**

- [PostgresQueryStore.load_shadow_candidates](../../src/kiwoom_monitor/central_server/database.py) (L3852) — read
- [_save_postgres_shadow_evaluation](../../src/kiwoom_monitor/central_server/database.py) (L7405) — read

<a id="central_shadow_decisions"></a>
## 후보 판단 원장 — `central_shadow_decisions`

monitor가 내린 판단 1개.

- **쓰는 곳/계기:** save_shadow_evaluation
- **읽는 곳/용도:** 판단 재현·candidate 사건의 근거
- **저장 경계:** candidate event와 같은 transaction으로 기록

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `decision_id` | `text` | 아니오 | — | 후보 판단의 고유 식별자 |
| `monitor_id` | `text` | 아니오 | — | 후보 분석 monitor 식별자 |
| `decided_at` | `timestamp with time zone` | 아니오 | — | 후보 판단을 내린 시각 PostgreSQL timestamptz(시간대 있는 시각). |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_shadow_decisions_pkey`: `PRIMARY KEY (decision_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_shadow_decisions_pkey ON public.central_shadow_decisions USING btree (decision_id);
```

**논리 관계**

- [화면에 전달할 후보 이벤트](TABLES.md#central_shadow_candidate_events) → [후보 판단 원장](TABLES.md#central_shadow_decisions): `document_json 안의 판단 근거; 같은 평가 transaction` (판단과 전달)
- [후보 판단 원장](TABLES.md#central_shadow_decisions) → [후보 분석 재개 상태](TABLES.md#central_shadow_monitor_state): `monitor_id; checkpoint는 별도 transaction` (같은 monitor)

**코드 근거**

- [_save_postgres_shadow_evaluation](../../src/kiwoom_monitor/central_server/database.py) (L7391) — read

<a id="central_shadow_monitor_state"></a>
## 후보 분석 재개 상태 — `central_shadow_monitor_state`

monitor별 cursor·누적 상태 체크포인트.

- **쓰는 곳/계기:** candidate monitor → save_shadow_monitor_state
- **읽는 곳/용도:** load_shadow_monitor_state·프로세스 재시작
- **저장 경계:** 평가 결과 저장과 별도 transaction; 운영은 JSON 한 문서 방식

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `monitor_id` | `text` | 아니오 | — | 후보 분석 monitor 식별자 |
| `updated_at` | `timestamp with time zone` | 아니오 | — | 저장 행/현재 상태의 갱신 시각. 시장 effective_at과 구분 PostgreSQL timestamptz(시간대 있는 시각). |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_shadow_monitor_state_pkey`: `PRIMARY KEY (monitor_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_shadow_monitor_state_pkey ON public.central_shadow_monitor_state USING btree (monitor_id);
```

**논리 관계**

- [후보 판단 원장](TABLES.md#central_shadow_decisions) → [후보 분석 재개 상태](TABLES.md#central_shadow_monitor_state): `monitor_id; checkpoint는 별도 transaction` (같은 monitor)

**코드 근거**

- [PostgresQueryStore.save_shadow_monitor_state](../../src/kiwoom_monitor/central_server/database.py) (L3807) — write
- [save_frames](../../src/kiwoom_monitor/central_server/shadow_checkpoint.py) (L124) — write
- [downgrade_schema](../../src/kiwoom_monitor/central_server/shadow_checkpoint.py) (L192) — write
- [load_shadow_monitor_state](../../src/kiwoom_monitor/central_server/database.py) (L1034) — dynamic boundary

<a id="central_theme_snapshots"></a>
## 테마 구성 이력 — `central_theme_snapshots`

테마 프로필의 특정 시점 구성.

- **쓰는 곳/계기:** 테마 metadata 저장 → _append_postgres_theme_snapshot
- **읽는 곳/용도:** load_theme_snapshots·테마 재현
- **저장 경계:** 문서 현재값과 같은 저장 transaction에서 이력을 기록하는 경로

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_theme_snapshots_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `snapshot_id` | `text` | 아니오 | — | 테마/계좌 관측 snapshot 식별자. 테이블별 범위 |
| `profile_id` | `text` | 아니오 | — | 프로필 식별자. 테마 프로필과 자격증명 프로필은 서로 다른 범위 |
| `content_hash` | `text` | 아니오 | — | 정규화한 내용의 hash. 동일 내용 재사용/변경 판별 |
| `effective_at` | `text` | 예 | — | 시장 사실/판단이 적용되는 기준 시각. 수신·가용 시각과 구분 |
| `received_at` | `double precision` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `origin_device` | `text` | 아니오 | — | 테마 구성을 만든 장치 식별자 |
| `revision_of` | `text` | 예 | — | 이전 revision 식별자. 해당 테이블 안 계보 연결 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_theme_snapshots_pkey`: `PRIMARY KEY (accepted_sequence)`
- `central_theme_snapshots_snapshot_id_key`: `UNIQUE (snapshot_id)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_theme_snapshots_pkey ON public.central_theme_snapshots USING btree (accepted_sequence);
CREATE UNIQUE INDEX central_theme_snapshots_snapshot_id_key ON public.central_theme_snapshots USING btree (snapshot_id);
CREATE INDEX idx_central_theme_snapshot_available ON public.central_theme_snapshots USING btree (available_at DESC, accepted_sequence DESC);
```

**논리 관계**

- [테마 구성 이력](TABLES.md#central_theme_snapshots) → [기능별 문서 저장소](TABLES.md#central_documents): `theme_metadata/profile 문서에서 만들어진 snapshot` (테마 현재와 이력)
- [테마 구성 이력](TABLES.md#central_theme_snapshots) → [테마 구성 이력](TABLES.md#central_theme_snapshots): `revision_of → 해당 revision ID` (이전 revision)

**코드 근거**

- [PostgresQueryStore.load_theme_snapshots](../../src/kiwoom_monitor/central_server/database.py) (L4222) — read
- [_append_postgres_theme_snapshot](../../src/kiwoom_monitor/central_server/database.py) (L5857) — read

<a id="central_upper_limit_fact_revisions"></a>
## 상한가 사실 이력 — `central_upper_limit_fact_revisions`

종목·세션의 상한가 판정 사실.

- **쓰는 곳/계기:** 시장 이벤트 수집 → append_upper_limit_facts
- **읽는 곳/용도:** 시장 이벤트·후보 판단 데이터
- **저장 경계:** 가격과 판정 근거 보존; 기본정보 응답 전체와 별개

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_upper_limit_fact_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `fact_id` | `text` | 아니오 | — | 상한가 사실 기록 고유 ID |
| `fact_key` | `text` | 아니오 | — | 상한가 사실의 동일성 키 |
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `session_id` | `text` | 아니오 | — | 시장 이벤트의 거래 세션 식별자 |
| `status` | `text` | 아니오 | — | 본문 추출/계좌/상한가 등의 상태. 테이블별 계약 |
| `upper_limit_price` | `bigint` | 예 | — | 당일 상한가 가격 |
| `current_price` | `bigint` | 예 | — | 판정 시점 현재가 |
| `high_price` | `bigint` | 예 | — | 판정에 사용한 당일 고가 |
| `effective_at` | `double precision` | 아니오 | — | 시장 사실/판단이 적용되는 기준 시각. 수신·가용 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `source` | `text` | 아니오 | — | 메타데이터/시장 사실의 출처 |
| `evidence` | `text` | 아니오 | — | 판정 근거 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_upper_limit_fact_revisions_fact_id_key`: `UNIQUE (fact_id)`
- `central_upper_limit_fact_revisions_fact_key_key`: `UNIQUE (fact_key)`
- `central_upper_limit_fact_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_upper_limit_fact_revisions_fact_id_key ON public.central_upper_limit_fact_revisions USING btree (fact_id);
CREATE UNIQUE INDEX central_upper_limit_fact_revisions_fact_key_key ON public.central_upper_limit_fact_revisions USING btree (fact_key);
CREATE UNIQUE INDEX central_upper_limit_fact_revisions_pkey ON public.central_upper_limit_fact_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_upper_limit_lookup ON public.central_upper_limit_fact_revisions USING btree (stock_code, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.append_upper_limit_facts](../../src/kiwoom_monitor/central_server/database.py) (L4924) — write

<a id="central_vi_event_revisions"></a>
## VI 이벤트 이력 — `central_vi_event_revisions`

VI 이벤트 식별 키별 사실.

- **쓰는 곳/계기:** 시장 이벤트 수집 → append_vi_events
- **읽는 곳/용도:** 시장 이벤트/후보 분석 경로
- **저장 경계:** 동일 이벤트 중복 insert 방지; 시간별 현재값 테이블 아님

| 컬럼 | 실제 자료형 | NULL 허용 | DB 기본값 | 의미 |
|---|---|---|---|---|
| `accepted_sequence` | `bigint` | 아니오 | nextval('central_vi_event_revisions_accepted_sequence_seq'::regclass) | 그 테이블 안에서 증가하는 수신/확정 순서. 다른 테이블의 sequence와 같은 사건 번호가 아님 |
| `event_id` | `text` | 아니오 | — | 논리 사건/이벤트 식별자. 소속 테이블별 네임스페이스 |
| `event_key` | `text` | 아니오 | — | 사건 동일성·중복 판단용 논리 키 |
| `stock_code` | `text` | 아니오 | — | 대상 종목 코드. 이 이름만으로 물리 FK가 생기지는 않음 |
| `event_kind` | `text` | 아니오 | — | VI 등 이벤트 세부 종류 |
| `vi_type` | `text` | 아니오 | — | VI 세부 발동 유형 |
| `effective_at` | `text` | 예 | — | 시장 사실/판단이 적용되는 기준 시각. 수신·가용 시각과 구분 |
| `received_at` | `double precision` | 아니오 | — | 시스템이 데이터/이벤트를 수신한 시각 이 컬럼은 Unix epoch 초로 저장. |
| `available_at` | `double precision` | 아니오 | — | 해당 값/판단을 시스템에서 사용할 수 있게 된 시각. 시장 발생 시각·단순 최종 조회 시각과 구분 이 컬럼은 Unix epoch 초로 저장. |
| `price` | `bigint` | 예 | — | VI 이벤트 발생 가격 |
| `direction` | `text` | 아니오 | — | 가격 변화/VI 방향 구분 |
| `trigger_count` | `integer` | 예 | — | VI 발동 횟수 |
| `exchange` | `text` | 아니오 | — | 거래소/시장 구분 |
| `source` | `text` | 아니오 | — | 메타데이터/시장 사실의 출처 |
| `document_json` | `jsonb` | 아니오 | — | 해당 기능의 구조화 문서. 내부 필드는 SQL 컬럼이 아니며 기능별 계약에 따름 |

**실제 제약**

- `central_vi_event_revisions_event_id_key`: `UNIQUE (event_id)`
- `central_vi_event_revisions_event_key_key`: `UNIQUE (event_key)`
- `central_vi_event_revisions_pkey`: `PRIMARY KEY (accepted_sequence)`

**실제 인덱스**

```sql
CREATE UNIQUE INDEX central_vi_event_revisions_event_id_key ON public.central_vi_event_revisions USING btree (event_id);
CREATE UNIQUE INDEX central_vi_event_revisions_event_key_key ON public.central_vi_event_revisions USING btree (event_key);
CREATE UNIQUE INDEX central_vi_event_revisions_pkey ON public.central_vi_event_revisions USING btree (accepted_sequence);
CREATE INDEX idx_central_vi_event_lookup ON public.central_vi_event_revisions USING btree (stock_code, available_at DESC, accepted_sequence DESC);
```

**논리 관계**

다른 SQL 테이블을 향한 직접 논리 참조를 이 지도에서 확정하지 않았다. 입력·사용처 연결은 위 설명을 따른다.

**코드 근거**

- [PostgresQueryStore.append_vi_events](../../src/kiwoom_monitor/central_server/database.py) (L4857) — write

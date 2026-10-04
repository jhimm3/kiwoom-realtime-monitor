# QueryStore 도메인 분류와 첫 물리 분리 계약

2026-10-05: 21개 도메인 분리 후 [QueryStore 계약 재평가](query_store_protocol_review.md)를
마쳤고 REST 캐시 두 메서드의 작은 소비자 계약과 상속 계약 감사를 현재 워크트리에 적용했다.
후보 NAS 검사가 5/5 통과했다. 추가 소비자별 계약은 실제 책임 경계를 좁히지 못해 만들지
않으며 기존 aggregate·backend·transaction 경계를 유지한다. 운영 active release에는 반영하지 않았다.

2026-10-05 잔여 root 점검: `database.py`의 top-level 정의는 `QueryStore`,
`SQLiteQueryStore`, `PostgresQueryStore`, `create_query_store`뿐이다. backend별 class는
생성자·초기화·종료·연결 수명을 소유하고, 도메인 저장 구현은 기존 mixin에서 상속한다.
이 수명·조립 경계를 다른 module로 옮기면 별도 책임이 생기지 않고 위임 단계만 추가되므로
현재 유지한다. 기존 import 경로도 호환 계약으로 보존한다.

같은 날 전체 정적 연결 감사를 재실행했다. QueryStore 99개, SQLite/PostgreSQL 구현 103/104개,
consumer 338곳, forwarding 46개, direct PostgreSQL 연결 승인 42/42에 추가·삭제·signature drift·
parse 오류가 없다. 저장 경로 원장은 domain module 위치 이동을 정규화해 API ID 그룹과 PostgreSQL
writer/table identity가 기준선과 같은 것을 확인했다. 정적 원장은 runtime·UI 성공의 증거가 아니다.

2026-10-04 · 소스 기준 `1ccaca1` 위의 현재 작업 트리 · 첫 도메인 물리 분리 및 전용 PostgreSQL 검증 완료

첫 분리의 완료는 소스와 전용 진단 DB의 계약 검증을 뜻한다. 운영 NAS 활성 release는
바꾸지 않았으며, 운영 반영은 종료 계획의 배포 단계에서 별도로 검증한다.

## 목적과 현재 단계

`database.py`의 책임 분리는 성능 최적화와 독립적으로 진행한다. 정적 연결 기준선과
검토 원장 정정이 끝났으므로 latency 증거를 기다리지 않고 첫 도메인을 분리한다.
이번 변경은 호출·반환값·SQL·트랜잭션 의미를 유지하는 코드 이동이다.
pooling, SQL 최적화, COMMIT 통합, schema 변경은 이 변경에 포함하지 않는다.

[분류 원장](domain_inventory.json)은 현재 `database.py`의 SHA-256, AST 위치와 hash,
메서드별 책임군·self 의존성·지역 helper 연결을 보존한다. 전체 8,258줄에서
QueryStore 계약 99개, SQLite 구현 104개, PostgreSQL 구현 105개를 분류했다.
구현 수에는 생성자가 포함된다. 기존 소비자 기준선과 비교할 때는 생성자를 제외한
SQLite 103개 / PostgreSQL 104개를 사용한다. 최상위 함수는 152개다.

이 원장은 소스 분류이지 전체 기능의 실행 검증 결과가 아니다. 분류한 책임군 하나가
반드시 파일 하나가 되는 것도 아니다. 각 단계에서 함께 커밋되는 작업과 공유 helper를
검토한 뒤 물리 경계를 확정한다. 첫 분리만 아래와 같이 확정했다.

## 전체 책임군

모든 메서드 이름과 두 backend의 구현 위치는 분류 원장의 `domains`, `methods`에 있다.
아래 수는 Protocol 메서드 수이며 합계는 99개다.

| 책임군 | 계약 수 | 현재 책임과 분리 시 보존할 연결 |
|---|---:|---|
| 생성·연결·초기화·종료 | 2 | `initialize`, `close`; 생성자, `_connect`, SQLite `_connection`은 구현 내부. backend 선택·schema runner·연결 수명은 조립부에 유지 |
| 저장소 진단 | 2 | 저장 크기·분류 조회. 진단 결과를 실제 쓰기 소유권으로 오해하지 않는다 |
| REST 응답 캐시 | 2 | `StoredQuery`, TTL, 연속조회 필드, upsert와 만료 삭제의 기존 COMMIT |
| 실시간 최신값 | 3 | snapshot 저장·읽기·최신 시총. 수집기의 flush/pending 복구 경계 유지 |
| 국내 봉 | 9 | 실시간 분봉·초봉, 조회 분봉·일봉·5분봉. `_replace_bars`는 내부 구현. 날짜 lock·query authority·replay 순서 유지 |
| 관측 metadata·revision 조회 | 5 | available/effective 시각과 완료 근거, revision 커서. 봉/dataset writer가 같은 cursor로 호출하는 helper와 분리해서 판단 |
| dataset·TOP20 통계 | 4 | snapshot, membership/ranking, 관측 revision, 통계 캐시 무효화 원자성 |
| shadow 상태·평가 | 4 | 복구 checkpoint, inline/frame 경로, 평가·후보. 기존 `shadow_checkpoint.py`와 책임 중복 금지 |
| 연구 export | 2 | 고정 membership/watermark, 페이지 커서, 생성 실패 rollback |
| 문서·테마 이력 | 5 | 문서 upsert/replace와 뉴스·테마 후처리가 공유하는 transaction. 이름만 보고 범용 CRUD로 분리하지 않음 |
| 뉴스 | 23 | 기사/BODY/AI/event, job claim/finish/retry, 외부 historical 소유권, source cursor와 요청 예산 |
| 시장 이벤트 | 5 | VI·hot cohort·상한가 사실·이력. revision과 현재 projection의 동시 저장 |
| 외부시장 봉 | 2 | 별도 외부시장 OHLCV 테이블의 저장·조회. **첫 분리 대상** |
| 실행 원장·제어·lease | 12 | intent/event/account snapshot, mock control CAS, acquire/release. 실행 소유권과 독립 COMMIT 보존 |
| 계좌 신원·binding·alias | 5 | 검증된 account scope와 lineage. identity/binding/scope alias의 backend 구현은 `database_account_identity.py`에 분리 |
| 계좌 설정·실계좌 복구 | 6 | 설정 CAS, market profile, real event/recovery. backend 구현은 `database_account_settings.py`에 분리하고 자격증명 활성화가 기존 cursor transaction에서 helper를 빌림 |
| 자격증명 | 8 | profile 생성·등록·보관·이름·activation. 기존 `credential_store.py`, 파일 commit과 DB fence 경계 보존 |

### 여덟 번째 분리 완료: TOP20 통계 집계·캐시

TOP20 통계의 SQLite/PostgreSQL `load_top20_statistics` 구현 2개와 날짜 범위,
오늘 기준, 일별 집계·병합 및 캐시 날짜 helper 6개를
`database_top20_statistics.py`로 옮겼다. `QueryStore` signature와 인증 API 경로,
PC remote client 계약은 그대로다. 새 모듈은 저장소 backend에 제공된 lock/connection과
하위 PostgreSQL 접근 helper만 사용한다.

`save_dataset_snapshots`와 단건 snapshot writer는 이 모듈로 이동하지 않았다. TOP20 및
시장지수 원본 snapshot 저장, `top20_statistics_day` 캐시 무효화, 선택적 관측 metadata와
revision 추가가 기존 한 transaction/cursor를 공유하기 때문이다. root store는 캐시 날짜와
현재 날짜 helper를 직접 import해 그 처리 순서를 유지한다. 데이터베이스 스키마나 API/UI는
변경하지 않았다.

AST 대조에서 이동 구현 2개와 helper 6개가 원문과 같고, 기존 root method 165개·helper
115개 및 Protocol 계약이 보존됐다. QueryStore 소비자 338곳과 PostgreSQL 직접 연결 승인
42/42, PostgreSQL 105개 구현의 논리 SQL/table/helper inventory도 이동 전후 차이가 없다.
SQLite 캐시·계산과 PC remote client 검사가 통과했다. Windows 격리 환경에서 기존 API
`TestClient`와 ASGI 요청 검증은 응답 전에 멈춰 API runtime 결과는 미확인으로 남긴다.
NAS 전용 진단 DB에서 통계 캐시 원자적 무효화 및 warm/cold/concurrent read 검사 2건이
통과했다(3.184초). 후보 실행기가 ZIP 안의 실제 `database_top20_statistics.py` 경로를
출력해 새 모듈을 선택한 것도 확인했다. 전용 PostgreSQL gate를 완료했으며, API route와
remote client 코드는 변경하지 않았고 client 계약 검사는 통과했다. NAS 운영 release·운영
DB·이미지는 변경하지 않았다.

선택 capability도 계약 목록 밖이라는 이유로 제거하지 않는다.
`set_news_job_wakeup`은 두 backend, `explain_news_job_claim_plan`과
`analyze_news_job_claim_read_only`는 PostgreSQL의 뉴스 책임군에 포함했다.

## 한꺼번에 나누지 않는 경계

- `upsert_documents("news_article", ...)`는 같은 cursor로 기사 revision/job까지
  저장하고 성공한 connection context 밖에서 worker를 깨운다. theme metadata도
  문서와 이력을 같은 transaction에서 저장한다. 뉴스 helper를 별도 객체의 독립 연결로
  바꾸면 이 원자성이 깨진다.
- 봉과 dataset은 metadata/revision helper를 공유한다. `_market_metadata_upsert_*`,
  `_append_*_observation_revision`, revision values 등은 현재 호출자의 cursor를 빌린다.
  helper 이동을 새 transaction 시작으로 바꾸지 않는다.
- 자격증명 활성화가 계좌 binding과 설정 helper를 함께 사용한다. API 이름 기준으로
  나누기 전에 activation fence와 파일/DB commit 순서를 보존할 경계를 정한다.
- 대기 계측 helper와 JSON 변환 일부는 여러 책임군에서 사용한다. 이번에 무조건
  `common` 파일로 옮기지 않는다. 첫 도메인에 필요 없는 이동은 후속으로 남긴다.

원장의 helper `reachable_from_method_domains`는 이러한 공유 후보를 보여 준다.
지역 이름 참조를 따라간 정적 결과이므로 동적 호출과 import된 구현은 별도 검토 대상이다.
`create_query_store`는 backend method가 아닌 앱 조립부가 호출하는 factory라서
`no_static_method_reference_review_required`로 표시된다. 미사용 함수라는 뜻이 아니다.

## 첫 분리: 외부시장 봉

### 변경 전 연결과 데이터

`YahooDelayedMarketCollector._collect_once` → `store.save_external_bars` →
`central_external_bars` → `store.load_external_bars` → 인증된
`GET /api/v1/market/external-bars` 응답이다.

수집기는 월물별 5분봉과 일봉을 각각 저장하고 roll state·collection status는
문서 경로로 별도 저장한다. 이 COMMIT들을 합치거나 수집 일정을 바꾸지 않는다.
현재 source에서 `external-bars` 경로의 직접 PC client 호출은 발견하지 못했다.
따라서 API 응답 검증을 곧바로 PC 화면 검증으로 보고하지 않는다.

테이블 열은 `provider, instrument, contract, timeframe, bar_time, open, high,
low, close, volume, updated_at` 11개다. conflict key는 앞의 5개이며 OHLCV가
같으면 `updated_at`도 유지한다. OHLCV가 NULL을 포함해 바뀌면 갱신한다.
조회는 instrument/timeframe으로 필터하고 최신 N개를 고른 뒤 역순으로 반환한다.
기존 반환값, limit 상한 10,000, PostgreSQL `bar_time::text`와 SQL 정렬 표현을 보존한다.

### 물리 구조 결정

새 파일은 `src/kiwoom_monitor/central_server/database_external_market.py` 하나다.
`SQLiteExternalMarketStoreMixin`, `PostgresExternalMarketStoreMixin`에 각 backend의
`save_external_bars`와 `load_external_bars` 실제 구현을 옮긴다. 기존 backend class는
해당 mixin을 정적으로 상속한다. root에 같은 이름의 전달 메서드를 남기지 않는다.

함께 이동할 지역 helper는 `_external_bar_values`, `_external_bar_result` 두 개다.
두 helper의 현재 repository 참조는 이 네 메서드와 정의뿐이다. helper alias를
`database.py`에 남겨 새로운 호환 계층을 만들 필요는 없다.

`QueryStore`, `SQLiteQueryStore`, `PostgresQueryStore`, `StoredQuery`,
`create_query_store`의 기존 import 경로와 public signature는 그대로 유지한다.
새 mixin에는 생성자, 독립 상태, 새 connection factory, lock, observer, cache를 두지 않는다.
같은 store 객체의 `_lock`, `_connection`, `_connect`를 기존대로 사용한다.
새 모듈은 root `database.py`를 import하지 않는다. 필요한 `Any`, `bounded_limit`,
기존 method-local `postgres_access` import만 사용한다.

이 선택은 정적 method 배치다. runtime repository 객체·라우터·팩토리·위임 체인을
추가하지 않는다. 새 저장 서비스로 위임하는 대안은 상태 소유자와 전달 호출만 늘리므로
선택하지 않았다. runtime method 대입과 monkeypatch 방식도 정적 추적을 어렵게 하므로
사용하지 않는다. 두 backend를 별도 거대 파일로 통째로 옮기는 방식도 이번 목적에 맞지 않는다.

한 기능의 SQL과 helper를 이해하는 파일은 변경 전 `database.py` 1개에서 변경 후
`database_external_market.py` 1개로 바뀐다. 조립까지 추적하면 root + domain 2개가 되지만
호출 깊이는 늘지 않는다. method lookup은 기존 store 객체의 정적 상속에서 해결하며
수집기/라우트 → public store method → helper/driver 호출 단계가 유지된다.

### 그대로 보존할 경계

| 항목 | 보존 조건 |
|---|---|
| 빈 저장 입력 | 연결과 SQL 없이 반환 |
| SQLite | 기존 `self._lock`와 `_connection()` context, executemany, rollback/close 의미 |
| PostgreSQL | 매 호출 `open_observed_connection(self._connect, context)`와 native context 종료; 다른 호출의 COMMIT과 독립 |
| 쓰기 관측 | family `external_market.bars`, kind `external_market:bars`, operation `save_external_bars`, rows_attempted 유지 |
| 읽기 관측 | family `read.external_market_bars`, kind `bars:{timeframe}`, read mode 유지 |
| 에러 | SQL/commit 오류 전파와 rollback/close 의미 유지; observer 실패가 업무 결과를 바꾸지 않음 |
| 재수신/정정 | conflict key·NULL 비교·기존 timestamp 유지·정정 반영 동일 |
| schema | migration/DDL/인덱스 변경 없음 |

## 코드 이동으로 감사가 누락되지 않게 하는 변경

현재 세 감사 도구는 root `database.py`에 직접 선언된 메서드만 수집한다.
상속된 구현을 검사하지 않으면 물리 이동 뒤 메서드/SQL이 사라진 것처럼 보이거나
감사 대상에서 누락된다. **첫 분리와 함께 이 정적 해석을 보완한다.**

공통 AST 해석은 `scripts/query_store_source.py`에 둔다. 실제 사용자가 세 도구라서
각 도구에 같은 예외 규칙을 복사하지 않는다. 앱 코드를 import하거나 실행하지 않는다.
첫 단계의 지원 범위는 root backend class, root의 명시적 local import로 찾은 직접
domain base class와 해당 모듈의 지역 함수다. 임의의 Python MRO 엔진은 만들지 않는다.

- root class와 base의 구현을 합쳐 logical owner `PostgresQueryStore.method` 또는
  `SQLiteQueryStore.method`를 반환하고, physical file/class/line을 별도로 붙인다.
- Protocol의 stub는 backend 구현으로 세지 않는다. 기존 `QueryStore` 상속 fixture도
  이 규칙을 따른다. method signature·decorator·본문은 실제 구현 AST에서 읽는다.
- 지원하지 않는 동적 base, 해석되지 않는 base/import, 중복/덮어쓴 구현, 추가 상속 단계는
  무시하지 않고 검토 오류로 보고한다. 첫 두 mixin은 생성자와 method override를 갖지 않는다.
- helper closure는 method의 physical module에 있는 지역 함수를 따라간다.
  외부시장 두 helper와 method 안의 연결 context/SQL이 기존 원장에 계속 나타나야 한다.
- `audit_query_store_consumers.py`: 유효 backend 메서드 수·signature와 logical
  내부 delegate identity 보존. 물리 위치 이동과 외부 호출 삭제를 구분한다.
- `audit_postgres_access.py`: moved method의 postgres/sqlite scope와 local helper,
  literal table, connection context 보존. 직접 psycopg 연결 지점은 바뀌지 않는다.
- `audit_kiwoom_storage_paths.py`: `save_external_bars`와 `central_external_bars`의
  쓰기 연결을 inherited implementation에서도 수집한다.

기존 baseline JSON과 승인 원장을 새 결과로 덮어써 통과시키지 않는다.
이동한 네 메서드·두 helper는 분류 원장의 AST hash와 이동 후 hash를 대조하고,
logical method counts/signatures/callers 및 driver 연결 identity가 유지되는지 확인한다.
파일/물리 owner/줄 번호 변경은 별도 relocation으로 기록한다. 새 감사 도구의 과거 결과
정규화가 필요하면 명시적인 위치 매핑을 사용하고 unrelated 차이는 계속 실패시킨다.

## 첫 분리 구현과 검증 순서

1. 공통 정적 source 해석과 세 감사 도구를 보완한다. 기존 단일 파일, 분리된 두 backend,
   Protocol-only stub, 누락된 구현/base, 중복 method fixture로 성공/실패를 검사한다.
   변경 전 소스에서 기존 기준선과 같은 logical 결과가 나오는지 먼저 확인한다.
2. 외부시장 네 method와 두 helper를 본문 수정 없이 옮기고 정적 상속을 연결한다.
   helper import 의존성과 원래 두 backend 타입/생성 경로를 확인한다.
3. AST 이동 대조와 QueryStore/driver/storage 감사들을 묶어서 실행한다.
   Protocol 99, SQLite 103, PostgreSQL 104(생성자 제외)와 기준선의 외부 소비 연결을 유지한다.
4. 기존 SQLite `test_external_market_bars_skip_unchanged_values_and_keep_null_safe_corrections`,
   외부시장 collector 회귀, 해당 API 인증 검사를 실행한다. 실제 temporary SQLite를 통해
   저장 → 인증 route → 11개 field/순서/timeframe/limit 응답도 검증한다.
   fake PostgreSQL driver로 빈 입력, native 성공·실패·close, 서로 다른 호출의 연결 및
   계측 경계를 확인한다. 이미 같은 의미를 검사하는 기존 test는 중복 작성하지 않는다.
5. 전용 PostgreSQL에서
   `test_external_market_bar_batch_preserves_reader_and_independent_commits`와 관련 새 실패
   fixture를 한 묶음으로 실행한다. 기존 test는 5m/1d 재수신·정정·5개 독립 COMMIT·reader
   metric을 검사한다. NULL 정정 및 중간 실패의 전체 rollback은 실제 test 내용을 확인해
   부족한 부분만 보완한다. 전용 DB 사용/잔여 정리는 기존 guard를 따른다.
6. `MODULE_MAP.md`와 이번 문서에 실제 이동 위치·검증 결과를 기록한다.
   소스 분리 완료, PostgreSQL 실행 검증, 운영 적용은 별도 상태로 보고한다.
   NAS active release나 운영 DB는 이 설계 단계에서 변경하지 않는다.

첫 물리 분리의 완료 조건은 실제 구현과 helper가 새 도메인에 있고 root에는 위임 wrapper가
없으며, 감사가 inherited implementation을 놓치지 않고 같은 계약/저장 결과/transaction을
검증하는 것이다. latency 개선은 이 단계의 완료 조건이 아니다.

### 2026-10-04 진행·검증 기록

- 외부시장 네 메서드와 두 helper를 `database_external_market.py`로 이동했다. 이동 전
  분류 원장의 AST 본문 hash와 6/6이 일치하고, root backend는 정적 mixin 상속만 한다.
- 세 감사 도구의 inherited method 해석을 보완했다. 소비자 감사는 계약 99,
  SQLite 구현 103, PostgreSQL 구현 104와 추가·누락 연결 0을 보고했다. PostgreSQL
  접근 감사는 직접 driver 연결 지점 42, 미승인·오래된 승인 0을 보고했고,
  저장 경로 감사에도 이동된 `save_external_bars`가 남아 있다.
- 로컬 SQLite 재수신·NULL 정정, PostgreSQL native context 모형의 독립 연결·
  COMMIT·rollback·close, 인증 API의 실제 SQLite 왕복, 외부시장 수집기 후속 저장을
  통과했다. 이 결과는 실제 PostgreSQL 서버의 SQL 실행을 대신하지 않는다.
- 전용 `kiwoom_monitor_diagnostic_test`에서 성공·재수신·정정·독립 COMMIT·reader
  관측, NULL 정정·배치 중간 SQL 실패 rollback·동료 데이터 보존 검사를 함께 실행해
  2/2 통과했다(2026-10-04, 0.499초). 두 테스트의 `tearDown`은 자신이 만든
  instrument 행을 삭제하고 `COUNT(*)=0`을 확인한다. 첫 실행에서 rollback 데이터
  확인 뒤 실패했던 것은 준비 COMMIT 세 건까지 세던 테스트 관측 필터였으며,
  rollback 호출만 고른 v2에서 통과했다. NAS source-runtime은 현재 작업 트리의
  Dockerfile·`central_schema.py` 계약 hash와 달라 후보 게시를 거부했고, SSH 서버는
  DB 포트 포워딩을 금지한다. 계약을 바꾸거나 운영 release를 교체해 우회하지 않았다.
- 수정한 검증 ZIP v2를 NAS 호스트
  `/tmp/kiwoom-db-external-market-split-pilot-v2-20261004.zip`에 전송하고 SHA-256
  `7ade89a2ad14e3f0594483dcbb6719a21ff11238cb29a716c847c098da7ea35e`을
  대조했다. 사용자가 sudo로 컨테이너에 복사해 위 두 검사를 실행했다.
- 운영 DB, NAS active release, 서버 이미지는 변경하지 않았다. 첫 물리 분리의
  소스·계약·동작 검증은 완료했고, 운영 배포 및 장중 동작 확인은 별도 단계다.

## 후속으로 남긴 것

첫 분리 이후 나머지 책임군은 공유 cursor/helper와 호출자 검증을 기준으로 다음 하나를
선택한다. 이 문서가 17개 파일 일괄 생성이나 기존 backend 전체 재설계를 승인하지 않는다.
성능 최적화·운영 병목 조사와 trace/replay 보완은 기존 별도 작업 범위를 따른다.

## 두 번째 분리: REST 응답 캐시

REST 응답 캐시는 `load_query`/`save_query` 두 계약과 `StoredQuery` 데이터 형식으로
구성되며, 호출자는 `CentralRestBroker`다. SQLite 구현은 기존 store lock과
`_connection()`을 사용한다. PostgreSQL read는 기존 observed native connection context를
유지하고, write는 한 연결에서 upsert와 만료 행 삭제를 실행한 뒤 같은 명시 COMMIT을 한다.
SQL, TTL 판정, cache payload 및 연속조회 필드, DB-call 계측과 느린 저장 경고는 바꾸지 않는다.

`StoredQuery`의 구현 위치를 `database_query_cache.py`로 옮기되 `database.py`에서 다시
가져오므로 현재의 `database.StoredQuery` import 경로와 broker 계약은 유지한다. 새 모듈은
DB 계층 밖을 import하지 않는다. backend 객체는 외부시장 도메인 다음에 query-cache mixin을
상속하며 전달 wrapper를 추가하지 않는다.

검증 기준은 기존 QueryStore source audit(Protocol 99, SQLite 103, PostgreSQL 104,
consumer 338곳, 연결 추가·삭제 0), 변경 전 메서드 AST 본문 hash 4/4 일치, SQLite cache
hit/expiry, PostgreSQL cache writer timing 및 reader native transaction, REST broker cache
hit와 in-flight dedupe다. 성공·실패 결과와 각 backend의 연결·transaction ownership은
기존 코드에 맞춰 보존한다. PostgreSQL 서버 통합 검증은 다음 전용 DB 실행 묶음에 포함한다.

### 2026-10-04 구현·로컬 검증

- 네 backend 메서드와 `StoredQuery` 정의를 `database_query_cache.py`로 이동했다. 메서드
  AST 본문 hash가 변경 전 원장과 4/4 일치한다. `database.py`는 두 cache mixin을 정적으로
  상속하고 `StoredQuery` 이름을 재노출해 기존 import를 유지한다.
- QueryStore source audit는 Protocol 99, SQLite 103, PostgreSQL 104, consumer 338,
  signature 변경 0, 추가/삭제 연결 0을 보고했다. PostgreSQL access audit는 승인된 직접
  연결 지점 42/42, 미승인·오래된 승인 0이다.
- SQLite hit/expiry, PostgreSQL writer SQL·명시 COMMIT·close 및 기존 지표 상관관계,
  PostgreSQL reader hit/miss/failure와 native transaction을 확인한 로컬 검사 4건 통과.
  SQLite store 전체 단위검사 57건도 통과했다. Windows 기본 temp 경로 권한 때문에 첫 실행은
  막혔으나 프로젝트 가상환경과 워크트리 임시 경로로 다시 실행해 통과했다.
- 전용 PostgreSQL `kiwoom_monitor_diagnostic_test`에서 두 검사를 후보 소스 우선 실행기와
  query-cache 모듈이 포함된 v2 ZIP으로 다시 실행해 2/2 통과했다(2026-10-04, 0.355초).
  실행기 `scripts/run_postgres_access_integration.py`는 ZIP 내부 `src/`를 먼저 import한다.
  replay/failure 호출 두 건은 기존·공통 commit 지표가 각각 21/20.827ms,
  21/20.702ms였고 최종 행 보존을 확인했다. ZIP SHA-256은
  `B21FEBF9E588AD709DA9ED8B350D6793AE1968ACF24155E78D93B505E8F35C7C`다.
  이 결과로 query-cache의 후보 PostgreSQL 동작 gate를 완료했다. 운영 DB와 NAS active
  release에는 적용하지 않았다.

### 세 번째 분리: 저장소 진단

`store.storage_size_bytes()`와 `store.storage_breakdown()`은 인증된
`GET /api/v1/diagnostics/resources`가 별도의 `asyncio.to_thread` 작업으로 호출한다.
응답의 `storage_categories`는 PC `api_settings_dialog.py`가 표시한다. SQLite 파일 크기와
`dbstat` 기반 분류, PostgreSQL catalog/공유 문서 분류를 함께 반환한다.

실제 구현은 `database_storage_diagnostics.py`의 SQLite/PostgreSQL mixin으로 이동했다.
공유 helper `_storage_category`, `_storage_breakdown_rows` 및 category labels도 이 파일로
이동했다. QueryStore 계약과 app route는 그대로 두고 backend store가 mixin을 정적으로
상속한다. 새 연결·서비스 계층을 추가하지 않았으며, PostgreSQL 두 메서드는 기존처럼 각각
독립된 observed reader context를 소유한다. SQLite `_lock`/`_connection`, `:memory:` 크기
처리, `dbstat` 오류 시 0 fallback, PostgreSQL SQL/수치 변환과 결과 category 순서를 보존했다.
schema, COMMIT, writer 동작은 변경하지 않았다.

2026-10-04 로컬 검증에서 네 구현 메서드의 AST hash가 분류 원장과 4/4 일치했고,
QueryStore consumer audit은 Protocol/backend 계약과 338개 연결이 그대로임을 확인했다.
PostgreSQL direct-access guard는 42/42 승인 지점을 유지했다. SQLite DB 전체 단위검사 57건은
통과했다. PC 리소스 화면의 storage categories 표시 회귀 1건도 통과했다.
`GET /api/v1/diagnostics/resources`의 실제 HTTP 인증·응답 검사는 이 Windows 실행 환경에서
요청 처리 중 멈춰 통과로 계산하지 않는다. API route와 응답 계약은 변경하지 않았고,
route가 기존 QueryStore 메서드를 호출하는 연결을 소스에서 확인했다. PC 설정 화면의
storage category 표시 회귀검사는 통과했다. 전용 PostgreSQL 후보 ZIP v1은 NAS에서
`test_storage_diagnostics_keep_separate_native_reader_transactions`가 통과했지만, 사후 점검에서
실행기가 ZIP의 `src/`를 import 경로에 추가하지 않아 설치된 컨테이너 패키지를 실행했을 가능성을
확인했다. 따라서 이 결과는 후보 코드 검증으로 인정하지 않는다. v2는 실행기가 ZIP의 `src/`를
테스트 루트보다 먼저 import하도록 고쳤고, 회귀검사 8건·zipapp `--help`·모듈 경로 확인을 통과했다.
모듈 경로가 실제 v2 ZIP 내부의 `src/kiwoom_monitor/central_server/database_storage_diagnostics.py`임을
검증했다. 후보 ZIP `kiwoom-db-storage-diagnostics-split-pilot-v2-20261004.zip`의 SHA-256은
`B21FEBF9E588AD709DA9ED8B350D6793AE1968ACF24155E78D93B505E8F35C7C`다. 사용자가 NAS에서
고정된 v2 ZIP으로 `test_storage_diagnostics_keep_separate_native_reader_transactions`를 다시
실행해 1/1 통과(0.096초)를 확인했다. 이에 따라 저장소 진단의 후보 DB 동작 및 native reader
transaction gate는 완료다. 실제 NAS HTTP runtime은 미검증이며, 운영 배포나 성능 개선은 수행하지
않았다. 다음 단일 도메인 후보는 실시간 최신값이다.
  첫 ZIP은 `scripts/__init__.py`가 없어 컨테이너에서 실행되지 않았다. v2에는 이를 넣었지만
  `tests/integration/__init__.py`가 빠져 fully-qualified test import가 실패했다. v3는 두
  패키지 초기화 경로를 모두 포함하며, 프로젝트 Python에서 테스트 모듈 import와 zipapp
  `--help`를 확인했다. SHA-256은
  `6D8E18A1BD4A6108218F1B3981ECC9759469E1AFAA26E8970D4C9CBDBC5052A5`다. 전용 PostgreSQL
  실행은 아직 남았으며 운영 DB와 NAS active release에는 적용하지 않았다.

### 네 번째 분리: 실시간 최신값

`CentralRealtimeCollector._flush_serialized()`는 `_flush_snapshot_cycle()`을 소유해
실시간 스냅샷을 `asyncio.to_thread(store.save_realtime_snapshots, ...)`로 저장한다.
저장 실패 시 가져간 행을 `_pending_snapshots`에 다시 합치며, 취소·종료 때는 실제 저장
작업과 마지막 도착 값을 기다린다. 저장된 최신값은 `GET /api/v1/market/latest-market-caps`와
인증된 `/api/v1/realtime` 구독 초기 복원에서 읽는다.

SQLite/PostgreSQL의 `save_realtime_snapshots`, `load_realtime_snapshots`,
`load_latest_market_caps` 세 구현씩과 `_latest_market_cap_rows`를
`database_realtime_snapshot.py`로 옮겼다. 두 backend store는 기존 조립 순서를 유지하면서
도메인 mixin을 정적으로 상속한다. `_json_document`는 도메인 밖 실행 문서 저장에서도 쓰여
공통 `database_codec.py`로 옮겼고, `database.py`에서 기존 이름으로 가져와 호출부를 보존했다.
새 모듈은 DB 계층 안의 codec, postgres access, diagnostic metrics만 import한다.

보존한 경계는 다음과 같다.

- SQLite는 기존 `_lock`, `_connection()`과 한 호출 단위 저장을 사용한다.
- PostgreSQL 저장은 `realtime.latest` / `realtime_latest` observed writer transaction,
  snapshot 읽기는 `read.realtime_market_state`의 기존 독립 reader transaction을 쓴다.
- 실시간 복원은 종목 요청 목록과 최근 300초 조건을 유지하며 `market_state`는 요청 종목과
  관계없이 함께 복원한다. 영속 0B 시총은 freshness cutoff 없이 마지막 저장 자료에서 양수
  시총만 반환하고 과거 가격은 살려내지 않는다.
- SQL, schema, API, payload, transaction 및 collector retry/drain 정책은 바꾸지 않았다.

세 store 메서드의 AST 본문 hash와 QueryStore의 기존 Protocol/backend 메서드 hash가 모두
그대로다. consumer audit은 Protocol 99, SQLite 103, PostgreSQL 104, 확인된 소비 연결 338을
유지했다. PostgreSQL direct connection gate도 기존 42개 승인을 유지했고, 이동한 세 PostgreSQL
메서드의 SQL/table/context 비교 차이는 0이다. `_json_document`를 공유 codec으로 옮기며
실행 문서 도메인의 local helper 연결 11곳만 codec 소유로 바뀌었다.

워크트리에서 DB·codec 회귀 63건, collector 실패 재시도·취소·종료와 0B API 검사 4건,
실제 인증 WebSocket snapshot 복원 및 resource API 검사 2건이 통과했다. 격리 환경에서 비동기
검사가 멈춘 건 Windows event loop의 local socketpair 생성 단계였고, 사용자 실행 환경에서는
같은 검사들이 통과했다. 실시간 snapshot 중간 행 SQL 실패의 전체 rollback과 독립 peer 보존을
보호하는 전용 PostgreSQL 검사를 추가했다. 전용 PostgreSQL 두 검사가 v3 후보 소스로 통과해
이 도메인의 DB gate도 완료됐다. 운영 DB와 NAS active release는 변경하지 않았다.

전용 PostgreSQL 검사 ZIP v1은 새 rollback 검사에 `psycopg` import가 없어 실패했다.
v2에서는 import 문제를 해결했으며, NAS 실행에서 실제 rollback, 기존 snapshot과 독립 peer의
보존, 잘못된 행의 부재, 기존 realtime writer 회귀검사는 확인됐다. 새 검사는 첫 SQL statement가
실패한 경우 observer가 `transactions=None`으로 보고하도록 정의된 계약을 고려하지 않아 assertion만
실패했다. 첫 statement가 실패하면 observer는 transaction 시작 여부를 확정하지 않고 rollback 수를
별도로 기록한다. 테스트는 이 관측 의미에 맞춰 `(rows_attempted=2, transactions=None, commits=0,
rollbacks=1)`을 기대하도록 수정했다. 이 수정과 Python 구문 컴파일을 확인했고, v3 ZIP
`kiwoom-db-realtime-snapshot-split-pilot-v3-20261004.zip`의 SHA-256은
`B9A81131D6CE08697CBF4CE092E82119DFABF437837A9FF92C4687D3CA3DE8A5`다.
2026-10-04 v3을 NAS 전용 `kiwoom_monitor_diagnostic_test`에서 실행해 아래 두 검사가
모두 통과했다(2 tests, 3.294초). 첫 검사는 batch rollback, 기존 snapshot·독립 peer 보존,
잘못된 행 부재와 rollback 관측값을 확인했고, 두 번째는 기존 realtime writer replay/lineage를
재확인했다. 이로써 realtime snapshot 도메인의 PostgreSQL gate를 완료했다. NAS active release,
운영 DB, 서버 이미지는 변경하지 않았다.
`test_realtime_snapshot_batch_failure_rolls_back_and_keeps_peer`와
`test_realtime_writer_batch_preserves_replay_lineage_and_independent_commits`.

### 다섯 번째 분리: 시장 이벤트

`MarketEventService`는 중앙 realtime collector 입력으로 VI revision을 받고,
조건검색에서 수신한 hot-cohort revision/current projection 및 0g 근거에 따른
상한가 사실 revision을 저장한다. 인증된 market-event diagnostics API와 조건검색·상한가
후속 작업은 `load_market_event_history`를 통해 기존 이벤트 기록을 조회한다.

SQLite/PostgreSQL의 `append_vi_events`, `record_hot_cohort_revision`, `load_hot_cohort`,
`append_upper_limit_facts`, `load_market_event_history` 구현과 이 도메인 순수 변환 helper를
`database_market_events.py`로 옮겼다. `_event_document`는 공통 `database_codec.py`로 이동해
기존 `database._event_document` 이름도 가져오기로 유지했다. 두 store는 기존 mixin 조립 순서에
새 도메인 mixin을 추가하며 QueryStore Protocol과 외부 메서드 signature는 그대로다.

보존한 경계는 다음과 같다.

- SQLite 각 호출은 기존 `_lock`과 `_connection()`에서 실행한다.
- PostgreSQL은 각 호출자가 이미 소유한 observed/native connection transaction을 계속 사용한다.
- SQL, table/schema, idempotent unique key, revision/current projection 관계, 정렬·limit, 오류 및
  rollback 의미는 변경하지 않았다.
- 새 모듈은 DB helper 방향만 의존하며 service/API/UI 계층을 import하지 않는다.

로컬 정적 검증에서 QueryStore Protocol 99개와 backend 구현 계약 및 338개 consumer reference가
그대로였고, PostgreSQL direct-connection gate 42/42, write-table inventory, 이동 메서드의
SQL/table/context 대조도 차이 0이었다. Python 구문 컴파일과 비동기 없는 이벤트 parser 및
중복 identity 회귀 2건이 통과했다. 비동기 market-event 전체 단위 묶음은 이 Windows 실행
환경에서 event-loop socketpair 단계에 멈춰 통과로 세지 않았다. 전용 PostgreSQL 후보 ZIP은
X 공유에 스테이징했다(SHA-256
`D717D4EB19EF26C7F67A26799577B93F482D786E94C989B6467FA586B20265F2`). 사용자가 NAS 전용
진단 DB에서 `MarketEventPostgresTests` 4건과 revision/projection/rollback 검사 1건을 실행해
5/5 통과를 확인했다(2.133초). 이로써 시장 이벤트의 PostgreSQL gate를 완료했다. 운영 DB·NAS
active release·이미지는 변경하지 않았다.

### 여섯 번째 분리: 연구 관측 export

인증된 `GET /api/v1/research/observations`는 최초 요청에서
`create_observation_export`를 호출해 요청 범위의 revision ID membership과 manifest를 고정한다.
후속 요청은 watermark와 ordinal cursor로 `load_observation_export_page`를 호출하며,
PC `CentralContentClient.load_research_observations_page`가 페이지 순회에 사용한다.
페이지 이후 추가된 revision은 기존 export membership에 들어오지 않는다.

SQLite/PostgreSQL 두 store의 export manifest writer와 page reader 구현 4개 및 전용 helper
7개를 `database_research_export.py`로 이동했다. revision row를 읽는 observation reader도
공유하는 `_observation_revision_columns`는 공통 `database_codec.py`로 옮겼으며,
`database.py`가 같은 함수 객체를 가져와 기존 private binding을 보존한다.
QueryStore Protocol·API route·PC client·응답 필드/정렬/watermark/cursor는 변경하지 않았다.

보존한 경계는 다음과 같다.

- SQLite writer는 기존 `_lock`과 한 `_connection()` context에서 source SELECT, manifest insert,
  fixed membership insert를 함께 커밋한다. reader는 기존 별도 lock/connection context다.
- PostgreSQL writer는 `research.observation_export`의 기존 observed native writer context를
  유지하고, page reader는 `read.research_export`의 별도 native reader transaction을 사용한다.
- timezone-aware 범위·24시간 제한·허용 kind·revision membership hash·ordinal ordering·page limit,
  알 수 없는 watermark 오류와 실패 rollback 의미는 그대로다.
- 새 모듈은 공통 codec·연구 관측 도메인 상수·PostgreSQL 하위 접근 helper만 의존한다.

원본과 대조해 이동 method 4개와 export helper 7개, shared SQL-column helper 1개의 AST가
각각 일치했다. QueryStore consumer audit은 Protocol 99, SQLite 103, PostgreSQL 104,
확인된 소비 338곳을 유지했고, direct connection gate는 42/42로 통과했다. PC `.venv`에서
고정 watermark·5,000건 넘는 동시각 revision의 pagination·revision reader 7건, 인증 API의
고정 페이지 응답 1건, PC client의 watermark/cursor 요청 1건이 통과했다. sandboxed Windows
실행은 TestClient loopback socketpair에서 멈췄지만, 같은 API 검사를 임시 SQLite로 제한된
실행 외부에서 다시 돌려 통과했다. 전용 PostgreSQL 후보 ZIP은 X 공유에 스테이징했다(SHA-256 `3279E0740A461EAF4DEE51645CB7DBE324127EBB6B6C841147528860466F668E`). NAS 검사는 아직 실행 전이며 이 도메인은
그 gate 이후에 완료로 판정한다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

v1 전용 PostgreSQL 묶음에서는 고정 membership/rollback 검사가 이동된 helper 대신
이전 `database.py` private binding을 monkeypatch해 실패 주입 단계에서 오류가 났다.
함수의 실제 전역 이름이 새 `database_research_export.py`에 있으므로 테스트 patch 대상을
그 소유 모듈로 옮겼다. 같은 v1 실행의 observation revision reader와 market bar reader
검사는 통과했다. 수정 후 PC 회귀 9건과 ZIP 실행 시 후보 모듈 경로를 확인했고,
v2 ZIP SHA-256 `3B0804A2C62619E8457C104F6F0ED4057AB9F2B6A9C062DD741C52F47E309`를
X 공유에 스테이징했다. NAS에서 같은 3개 검사를 다시 실행하기 전까지 PostgreSQL gate는
미완료다.


2026-10-04 NAS PostgreSQL 후보 v2 검증 완료: `test_research_export_keeps_fixed_membership_and_rolls_back_partial_failure`, `test_observation_revision_readers_keep_native_context_and_separate_kinds`, `test_query_market_bars_keep_native_history_and_correlate_both_metrics` 3건이 모두 통과했다(2.072초). 실행기가 ZIP 내부의 `database_research_export.py`를 candidate module로 출력했다. v1 rollback 검사의 오류는 테스트가 이전 `database.py` private binding을 patch한 데서 왔고, v2는 실제 helper 소유 모듈을 patch해 rollback과 고정 membership 보존을 검증했다. 여섯 번째 도메인의 source/consumer/API/client/SQLite/PostgreSQL gate가 완료됐다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

### 일곱 번째 분리: 문서·테마 이력

SQLite/PostgreSQL 각각의 `upsert_documents`, `replace_documents`, `load_documents`,
`load_document`, `load_theme_snapshots` 구현 10개와 관련 helper 10개를
`database_documents.py`로 옮겼다. `QueryStore` Protocol, 호출자, 반환 계약, SQL,
SQLite lock/connection과 PostgreSQL observed/native connection 경계는 그대로다.
`news_article`의 문서·기사 revision·BODY job 및 `theme_metadata`의 문서·테마 snapshot은
각각 기존 cursor와 transaction에서 함께 저장된다. 뉴스 worker wake-up도 성공 COMMIT 뒤에만
실행한다. 공유 job-insert helper를 여전히 사용하는 뉴스 도메인 메서드는 새 모듈 함수를
직접 가져오며, 새 모듈은 service/API/UI나 root `database.py`를 import하지 않는다.

원본과 대조해 이동 메서드 10개, helper 10개 및 이동하지 않은 root 메서드 167개의 AST가
일치했다. QueryStore consumer audit 338곳, PostgreSQL 직접 연결 승인 42/42,
이동 전후 PostgreSQL 메서드의 reachable SQL/table/helper 목록이 동일했다.
이동한 공유 helper가 다른 뉴스 메서드에서도 정적 감사에 나타나도록
`audit_postgres_access.py`가 store 구현 파일 사이의 직접 named import를 추적하도록
보강했다. PC DB/codec/news/theme/content 회귀 109건, 인증 API와 PC client 검사 4건,
정적 감사 검사 3건이 통과했다. NAS X 공유에 스테이징한 전용 PostgreSQL 후보 ZIP v1 SHA-256은
`A938EF44C57A9E5A33811CD77ED519438BBD0FD5E172B7EC20430AF642EEE2E3`다.
NAS 전용 진단 DB에서 후보 ZIP v1의 article projection/job 원자성, theme
snapshot/rollback, collection reader 및 기존 settings document writer 경계 검사 6건이
통과했다(2.991초). 실행 runner가 ZIP 안의 `src`를 우선 import 경로에 둔다. 출력된
`candidate_module`은 `__main__.py` 파일 경로에 ZIP 내부 경로를 덧붙인 표시값이라 실제
모듈 파일 경로 증거로 사용하지 않는다. 로컬 ZIP import 확인에서는 두 backend의
`upsert_documents` 구현이 `database_documents`에서 로드됐다. 일곱 번째 도메인의
SQLite/PostgreSQL 및 API/client 검증 gate를 완료했다. 운영 DB·NAS active release·이미지는
변경하지 않았다.


### 아홉 번째 분리 완료: 관측 metadata·revision 조회

SQLite/PostgreSQL의 `load_observation_revisions`, `load_observation_revisions_after`,
`load_market_data_metadata` 구현 6개와 `_observation_revision_select`를
`database_observation_readers.py`로 옮겼다. QueryStore Protocol, callers, SQL, 반환값,
각 reader의 기존 별도 connection/transaction 의미는 유지했다. 새 모듈은 DB 하위 codec와
접근 helper만 참조하며 service/API/UI나 root `database.py`를 import하지 않는다.

이 단계에서는 `save_market_data_metadata`를 root에 남기고,
`load_market_data_metadata_range`도 application의 `CoverageObservation` 반환 타입 때문에
이동하지 않았다. 후속 경계 검토 결과, 직접 metadata writer는 자체 native connection context를
열며 bar/dataset writer가 공유하는 것은 `database_observation_writes.py`의 cursor-level SQL
helper임을 확인했다. 이어서 value-only `CoverageObservation`을 domain contract로 옮기고 기존
application import 경로에 같은 타입을 재노출해, 세 metadata 메서드를 한 DB 모듈에 둘 수 있었다.

이동 method/helper AST 대조, QueryStore 소비자 연결, PostgreSQL 직접 연결 승인 및 105개
PostgreSQL 메서드의 논리 SQL/table/helper 대조에서 차이가 없었다. SQLite DB·시장 수집 및
관측 consumer 회귀 24건이 통과했다. mock runner 재시작 비동기 검사는 Windows 실행 환경에서
멈춰 미확인으로 남겼다. NAS 전용 진단 DB에서 `test_observation_revision_readers_keep_native_context_and_separate_kinds`와
`test_query_bar_metadata_available_at_changes_only_with_content_or_state`가 모두 통과했다
(2건, 0.775초). ZIP 실행기가 새 `database_observation_readers.py`를 실제 후보 모듈로
출력했다. 이로써 관측 metadata·revision reader 분리의 PostgreSQL gate를 완료했다.
운영 DB·NAS active release·이미지는 변경하지 않았다.

### 열일곱 번째 도메인: 시장 관측 메타데이터 저장·조회 — 완료

SQLite/PostgreSQL의 `save_market_data_metadata`, `load_market_data_metadata`,
`load_market_data_metadata_range` 6개 구현과 `_metadata_from_range_row`를
`database_market_metadata.py`로 옮겼다. cursor를 빌리는 bar/dataset metadata helper는
`database_observation_writes.py`에 두어 기존 transaction 소유권을 보존했다. 반환 value type인
`CoverageObservation`은 domain contract로 옮기고 `application.market_data_coverage`와
`central_server.database` 양쪽이 같은 class를 import하도록 했다. QueryStore Protocol과 외부
호출 signature, API 응답 모양은 바꾸지 않았다.

기준선 22개 SQLite·ingestor·coverage API 테스트가 통과했다. AST 대조에서 QueryStore 99,
SQLite 103, PostgreSQL 104 method 구현 전체의 기존 본문·signature가 그대로다. 소비자 338곳,
forwarding edge 46개, PostgreSQL direct connection 승인 42/42와 차이가 없고, 새 모듈은
service/API/UI/root store를 import하지 않는다. candidate ZIP
`kiwoom-db-market-metadata-split-pilot-v1-20261005.zip`의 SHA-256은
`098FD0BF3DC2B03F310B9DD6E399CC1B37774B916F2718CE01958370EEAADFB6`다. 첫 NAS gate에서는
rollback 테스트 주입 SQL이 12개 bind parameter를 보존하지 않아 `ProgrammingError`가 발생했고,
나머지 두 테스트는 통과했다. v2는 원래 parameterized upsert 뒤에 문법 오류만 추가해 같은
PostgreSQL `SyntaxError` rollback 경로를 검사한다. 후속 검증 ZIP은
`kiwoom-db-market-metadata-split-pilot-v2-20261005.zip`이며 SHA-256은
`B48041EEEF90793F3DE647D23DA09174A1CB2078BA1CC443F64B62BD7515CD16`다.
ZIP launcher와
후보 모듈 경로 출력은 확인했고, NAS dedicated PostgreSQL gate 세 건이 통과했다(0.861초).
물리 분리는 완료 처리했다. 운영 DB·NAS active release·image는 변경하지 않았다.

NAS 진단 DB gate는 아래 세 테스트를 한 번에 실행한다.

- `PostgresAccessIntegrationTests.test_direct_market_metadata_writer_rolls_back_and_range_reader_keeps_native_context`
- `PostgresAccessIntegrationTests.test_query_bar_metadata_available_at_changes_only_with_content_or_state`
- `PostgresAccessIntegrationTests.test_market_bar_readers_keep_native_context_and_separate_kinds`

모두 `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests` 소속이다.

### 열아홉 번째 도메인 완료: 뉴스 revision 저장·조회

SQLite/PostgreSQL의 BODY·AI·event revision 저장, 본문·revision·기사 이력 조회 구현 20개와
관련 SQL·row helper 19개를 `database_news_revisions.py`로 이동했다. store 조립과 QueryStore 계약,
`database`의 기존 helper import alias를 유지한다. `SUPPLY_CONTRACT_RULE_VERSION`은 하위
`domain.news_observation` 계약에 두고 기존 `application.news_rules` 경로에서도 같은 상수를 참조한다.
새 DB 모듈은 service/API/UI 계층을 import하지 않는다.

BODY revision과 RULE job 삽입은 기존 connection/cursor transaction을 공유하고 post-commit wake-up
순서를 유지한다. AI projection·analysis revision·usage document는 기존 원자적 저장 단위 그대로이며,
event와 membership revision은 같은 transaction과 PostgreSQL table lock을 유지한다. 외부 뉴스 완료와
source-page 저장은 기존 호출자 transaction을 유지하고 root의 호환 helper alias를 통해 같은 cursor를
빌린다. 실패 주입 테스트는 helper 정의가 이동한 `database_news_revisions.uuid`를 patch한다.

AST method body/signature 대조 20개와 helper 19개, 상수 값·rule body, 모듈 import 소유권 검사가
통과했다. QueryStore consumer 338곳, 승인 PostgreSQL 연결 42곳, storage path 및 105개 PostgreSQL
method inventory가 변경 전후 동일하고 내부 delegate 2곳은 새 mixin 위치로 기준선을 갱신했다.
뉴스·기사·소스·규칙·PostgreSQL 접근 로컬 회귀 135건이 통과했다. NAS 전용 PostgreSQL 후보 gate는
다음 9개를 한 번에 실행한다.

- `test_news_event_parallel_replay_and_membership_failure_preserve_history`
- `test_news_ai_results_keep_projection_revision_usage_atomic_and_observed`
- `test_news_body_revision_links_rule_job_and_rolls_back_missing_article`
- `test_external_news_claim_preserves_parallel_ownership_and_reader`
- `test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback`
- `test_news_source_page_replay_and_failure_preserve_article_and_progress`
- `test_news_article_projection_revision_and_job_share_observed_commit`
- `test_news_original_publication_replay_preserves_reader_and_correlates_metrics`
- `test_news_rule_assessment_replay_preserves_reader_and_correlates_metrics`

모두 `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests` 소속이다.
후보 ZIP `kiwoom-db-news-revisions-split-pilot-v1-20261005.zip`의 SHA-256은
`A9346788F742E503FD87CA3657B7E86CFFEE9F3FA7FD9E3FEB23D126914E0A10`이다. ZIP 내부 candidate
module 경로 출력과 압축 무결성을 확인했고, NAS 전용 PostgreSQL gate 9/9가 통과했다(2.793초).
이에 정적 연결, QueryStore, SQLite/PostgreSQL 동작과 기존 뉴스 저장 consumer의 물리 분리가
완료됐다. 현재 운영 DB·NAS active release·image는 변경하지 않았다.

### 열여덟 번째 도메인: 뉴스 작업 큐·요청 예산 — 완료

SQLite/PostgreSQL의 뉴스 AI 작업 `set_news_job_wakeup`, `enqueue_news_ai_jobs`,
`claim_news_jobs`, `finish_news_job`, `retry_news_job`, 요청 예산
`claim_news_request`·`news_request_count`와 PostgreSQL claim plan/read-only 분석 구현을
`database_news_jobs.py`로 이동했다. 문서·기사 저장 메서드가 자신의 기존 cursor와 transaction 안에서
사용하는 `_insert_sqlite_news_job`, `_insert_postgres_news_job`, wake-up helper는
`database_news_job_writes.py`에 두었다. `database_documents.py`에서는 동일 함수 객체를 기존 이름으로
재노출해 import consumer를 보존했다. job enqueue를 위해 별도 연결이나 transaction을 열지 않으며,
wakeup callback은 기존대로 document commit 뒤 실행한다.

QueryStore Protocol 99개 계약, SQLite/PostgreSQL store 조립, root `database.py`의 기존 상수·helper
호환 이름, DB 호출 서명과 구현 본문을 보존했다. claim의 stale recovery·`SKIP LOCKED`, retry 제한,
request-budget 직렬화, writer 관측 종류와 commit/rollback 경계를 변경하지 않았다. 새 구현 모듈은
service/API/UI나 root store를 import하지 않고 하위 DB helper만 참조한다. 기사 BODY/RULE 결과,
article projection/revision과 source-page 저장 transaction 구현은 `database_documents.py` 및 기존
호출자 경계에 유지한다.

분리 전후 QueryStore consumer 338곳과 PostgreSQL direct-connection 승인 42/42가 같고,
정적 storage-path 감사와 AST method/helper 대조가 통과했다. 기존 뉴스·기사·소스·관측 로컬 회귀
125건도 통과했다. NAS 전용 PostgreSQL 후보 ZIP은
`kiwoom-db-news-jobs-split-pilot-v1-20261005.zip` (SHA-256
`344871AC40F47B3DE31E3D5AECD4CC97158779B0E04278772309FDB03063C2A6`)의 job claim/finish/retry,
요청 예산 동시성, idempotent enqueue와 기존 기사·BODY·source transaction 연결 gate가 NAS 전용 DB에서
10/10 통과했다(3.165초). ZIP 실행기는 후보 `database_news_jobs.py` 경로를 출력했다. 이로써 로컬 회귀,
정적 연결, SQLite/PostgreSQL 동작과 기존 저장 consumer 경로 검증을 완료했다. 운영 DB·NAS active
release·image는 변경하지 않았다.

NAS 진단 DB gate는 다음 테스트를 한 번의 실행으로 묶는다.

- `test_news_job_finish_updates_only_its_job_and_correlates_metrics`
- `test_news_job_retry_preserves_attempt_threshold_and_correlates_metrics`
- `test_news_job_claim_recovers_stale_and_prevents_parallel_duplicate_claim`
- `test_news_job_claim_candidate_preserves_postgres_selection_and_skip_locked`
- `test_news_request_budget_serializes_parallel_claims_and_observes_limits`
- `test_news_ai_job_enqueue_replay_and_batch_failure_preserve_queue`
- `test_news_article_projection_revision_and_job_share_observed_commit`
- `test_news_body_revision_links_rule_job_and_rolls_back_missing_article`
- `test_news_source_page_replay_and_failure_preserve_article_and_progress`
- `test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback`

모두 `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests` 소속이다.

### 열 번째 분리 후보: 계좌 identity·binding·scope alias

SQLite/PostgreSQL 구현 10개(`register_account_identity`, `append_account_binding`,
`load_account_bindings`, `register_account_scope_alias`, `resolve_account_scope`)와
도메인 helper 13개를 `database_account_identity.py`로 옮겼다. QueryStore 계약과
인증 journal API 및 PC client 호출 경로는 유지했다. credential activation이 사용하는
공통 document/canonicalization helper는 root `database.py`의 기존 binding으로 계속
제공해, activation의 파일 교체와 binding/profile DB 변경 순서 및 rollback 경계를 바꾸지 않았다.

보존한 저장 의미는 다음과 같다.

- SQLite identity 등록과 binding revision은 기존 `BEGIN IMMEDIATE` 경계에서 직렬화하고,
  동일 identity 재등록의 idempotency와 binding sequence를 유지한다.
- PostgreSQL binding revision은 기존 advisory transaction lock과 row lock을 유지한다.
  scope alias는 origin identity lock, verified canonical binding 검사, alias chain 금지,
  immutable replay와 단일 native transaction을 보존한다.
- `resolve_account_scope`는 DB 도메인 책임으로 남고, 인증된 journal v2 저장·조회는 기존
  canonical account scope 검증을 그대로 사용한다. API/client payload와 응답 계약은 바꾸지 않았다.
- 새 모듈은 표준 라이브러리와 하위 DB 공통 helper만 의존하며 service/API/UI/root
  `database.py`를 import하지 않는다.

이동 구현과 helper AST, QueryStore/소비자, PostgreSQL 직접 연결 승인, SQL/table 경계의
전후 대조는 차이 없이 통과했다. account identity·credential activation·journal API/client
관련 로컬 검사 42건과 전용 PostgreSQL 검사 3건이 통과했다(2.473초): binding/revision의
분리 transaction, scope alias 검증·rollback·peer isolation, credential activation replay 및
파일 교체 후 DB rollback을 확인했다. 후보 실행기는 ZIP 내부
`database_account_identity.py`를 실제 모듈로 불러왔다. 이로써 열 번째 도메인의 source,
consumer/API/client, SQLite, PostgreSQL gate를 완료했다. 운영 DB·NAS active release·이미지는
변경하지 않았다.

### 열한 번째 분리 완료: 계좌 설정·실계좌 복구·이벤트

SQLite/PostgreSQL 구현 12개(각각 `load_account_settings`, `save_account_settings`,
`load_market_profile_settings`, `save_market_profile_settings`,
`save_real_account_recovery`, `save_real_account_event`)와 helper 9개를
`database_account_settings.py`로 이동했다. QueryStore Protocol과 API/client 입력·응답
계약은 유지했다. 새 모듈은 `database_account_identity.py`의 canonical account helper와
하위 DB 공통 접근 계층만 참조하고 service/API/UI/root `database.py`를 import하지 않는다.

설정의 expected revision CAS와 변경 없는 저장의 timestamp 보존, 실계좌 identity/binding 및
settings revision fence, recovery/event replay 멱등성은 구현 원문 그대로 옮겼다. SQLite의
`_lock`·`BEGIN IMMEDIATE`, PostgreSQL native observed connection과
`credential-activation` advisory transaction lock·별도 호출별 COMMIT/rollback도 유지했다.
credential activation의 `_claim_account_settings`는 공개 store 메서드를 다시 호출하지 않고,
기존 transaction cursor로 같은 domain의 `_load_account_settings`/`_save_account_settings`
helper를 계속 실행한다. root는 기존 private import 호환성과 activation 흐름을 위해 helper
binding을 유지한다.

변경 전후 AST 대조에서 이동 구현 12개와 helper 9개, 남은 root 구현 137개와 helper 92개,
QueryStore Protocol이 동일했다. 소비자 338곳, PostgreSQL 구현 105개의 논리 SQL/table/helper
목록, literal write 경계 32개의 전후 차이는 없고 PostgreSQL 연결 승인 42/42도 통과했다.
계좌 설정 API, market profile, 실계좌 runtime/realtime, credential activation 및 저장 경계
로컬 검사 81건이 통과했다. 회귀 중 `check_postgres_integration.py`가 앞서 문서 domain에서
이동된 news helper의 옛 root import를 요구하는 문제가 발견되어 실제 소유 모듈에서 가져오도록
연결을 고쳤고 해당 checker rollback/runtime 검사가 다시 통과했다.

후보 ZIP은 `kiwoom-db-account-settings-split-pilot-v1-20261004.zip`이고 실행기가 ZIP 내부
`database_account_settings.py` 경로를 출력했다. NAS 전용 PostgreSQL 검사 세 건이 모두
통과했다(6.665초):

- `test_real_account_event_and_recovery_keep_replay_fence_and_separate_commits`
- `test_account_and_market_profile_settings_keep_cas_fence_and_independent_commits`
- `test_credential_activation_finalize_replays_and_rolls_back_after_file_commit`

이로써 열한 번째 도메인의 정적 연결, QueryStore, API/client, SQLite 및 PostgreSQL 검증
gate를 완료했다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

### 열두 번째 분리 완료: 자격증명 프로필·활성화

SQLite/PostgreSQL 구현 16개(각각 `find_credential_activation`, `list_credential_profiles`,
`create_credential_profile`, `archive_credential_profile`, `rename_credential_profile`,
`register_credential_profile`, `finalize_credential_activation`,
`load_credential_activations`)와 helper 10개를 `database_credentials.py`로 이동했다.
QueryStore Protocol, API/client method와 payload는 그대로 유지했다. 두 backend store는
credential mixin을 정적으로 상속하며 store facade나 추가 connection 계층은 만들지 않았다.

provider field 이름은 DB 코드가 상위 `credential_store` 서비스 모듈을 import하던 의존을
끊기 위해 하위 공통 `domain/credential_contract.py`로 옮겼다. `credential_store.PROVIDER_FIELDS`와
기존 DB private helper import 이름은 같은 객체/함수 binding을 재노출해 소비자 경로를 보존했다.
vault의 암호화 파일 저장·교체, 검증, 수명주기 구현은 이동하거나 변경하지 않았다.

activation은 profile/identity/binding/account-settings 변경을 기존 cursor에서 함께 수행한다.
SQLite `BEGIN IMMEDIATE`와 `_lock`, PostgreSQL 호출별 native observed connection,
`credential-activation` advisory transaction lock, 성공·실패 시 COMMIT/rollback 소유권을
본문 그대로 유지했다. identity와 settings helper는 기존 DB 도메인 모듈에서 직접 import하며
상위 API/service/UI를 참조하지 않는다.

정적 대조에서 이동 backend 구현 16개, helper 10개, 남은 root 함수, QueryStore Protocol이
원문과 일치했다(provider import만 상위 저장소에서 하위 계약으로 이동). consumer 338곳,
PostgreSQL 논리 SQL/table/helper, 승인된 driver 연결 42개에 변화가 없었다. API/client,
실계좌·모의계좌 owner와 vault를 포함한 로컬 회귀 196건 중 194건 통과, POSIX 전용 1건 skip,
기존 DART read-only 기대값 불일치 1건 실패다. 이 실패는 보존한 변경 전 코드에서도 같게
재현됐으며 DB 분리 회귀로 보지 않고 `OPEN_ITEMS.md`에 남겼다.

후보 ZIP `kiwoom-db-credential-profiles-split-pilot-v1-20261004.zip`의 SHA-256은
`832D9958E971AC9CA5453C1E66B94DFB61E37D34CD187B6ACD1A333CD6D9C9A0`이며, 실행기에서
ZIP 안의 자격증명 DB 모듈과 통합 테스트 모듈 import 경로를 확인했다. 전용
`kiwoom_monitor_diagnostic_test`에서 후보 ZIP의 `src/`를 우선 import해 다음 검사를 함께 실행했고
4/4 통과했다(4.146초).

- `test_credential_profile_writers_preserve_replay_lifecycle_and_independent_metrics`
- `test_account_and_market_profile_settings_keep_cas_fence_and_independent_commits`
- `test_credential_activation_finalize_replays_and_rolls_back_after_file_commit`
- `test_real_account_event_and_recovery_keep_replay_fence_and_separate_commits`

이로써 열두 번째 도메인의 정적 연결, QueryStore, API/client, SQLite 및 PostgreSQL 검증
gate를 완료했다. DART read-only 기대값 실패는 원본 코드에서도 재현돼
`OPEN_ITEMS.md`에 별도로 기록했다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

### 열세 번째 분리 완료: 실행 원장·mock 제어·runtime lease

SQLite/PostgreSQL 각각에서 QueryStore 구현 12개와 공통 helper 3개를
`database_execution.py`의 두 mixin으로 옮겼다. QueryStore Protocol·store 생성·상속
조립·기존 helper import 이름은 `database.py`에 유지했다. helper와 저장 method는 서로
다른 연결을 열지 않는다.

`ExecutionRepository`가 intent 생성·event 반영·계좌 event page·account snapshot·lease를
사용한다. `ForwardEvaluationRepository`는 mock automation control CAS를 읽고 쓴다.
mock 주문 submit/get/cancel 및 execution-events 인증 API는 기존 repository를 그대로
거친다. 실행 intent/event 재생, account별 페이지 cursor와 identity scope, journal projection,
runner admission/recovery, bundle drain과 lease release에 이 연결이 이어진다.

같은 계좌 intent와 event projection은 한 호출의 transaction에 남겼다. 중복 event는 기존
멱등 규칙대로 projection을 건너뛴다. ownership guard는 현재 호출 cursor에서 lease token·만료,
RUNNING control revision/spec/run, 기존 intent의 계좌/run scope를 확인한다. PostgreSQL은
같은 native transaction 안에서 보호 행을 잠그고 mock control 저장의 advisory transaction lock과
CAS를 유지한다. SQLite의 `_lock`·`BEGIN IMMEDIATE`, writer/reader별 기존 COMMIT·rollback,
runtime lease token/expiry conditional upsert 및 token 일치 release도 원문에서 바꾸지 않았다.
새 service/repository relay, 테이블, schema migration, API/client 필드는 추가하지 않았다.

변경 전후 AST 대조는 backend method 24개와 helper 3개의 본문, 남은 `database.py` 함수와
QueryStore 계약이 같음을 확인했다. consumer audit은 계약 변경 0, 호출 추가/삭제 0,
검토된 소비자 338곳, 내부 delegate 4, forwarding edge 46을 보고했다. PostgreSQL audit은
구현 105개, literal connect 승인 42/42와 parse error 0을 보고했고 저장 경로 집계도
전후 항목 수가 같았다(POSTGRES literal writer 32, PC SQLite tables 97).

로컬 기준선 45개와 변경 뒤 관련 실행 원장·admission·runner/supervisor·mock bundle·selected
client/API 검사는 총 115개 통과했다. API TestClient 묶음은 fake news background 저장이
끼어들 때 20초 diagnostic stack snapshot을 출력했지만 최종 8/8 및 4/4 모두 통과했다.
이는 source move 뒤 테스트 실패로 계산하지 않았고 runtime 지연의 원인도 이 검사만으로
단정하지 않는다. NAS 전용 PostgreSQL에서 아래 3건도 3/3 통과했다(2.049초).

- `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests.test_execution_ledger_and_lease_keep_native_boundaries_and_metrics`
- `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests.test_execution_ownership_control_fences_rollback_and_keep_peer`
- `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests.test_mock_automation_control_cas_preserves_native_transactions`

후보 `kiwoom-db-execution-ledger-split-pilot-v1-20261004.zip`은 X 공유에 스테이징했고
SHA-256 `41CA3646F32F90D0EB6818518ECAD34137C750E5D8E8AA51B53DD5121F937984`를 확인했다.
NAS 테스트 실행에서 실제 ZIP 내부 `database_execution.py`가 import된 것도 확인했다.
운영 DB·NAS active release·image는 변경하지 않았다.

### 열네 번째 분리 완료: shadow 상태·평가

`CandidateMonitor`는 `load_shadow_monitor_state`, `save_shadow_monitor_state`,
`save_shadow_evaluation`을 사용한다. 인증 API는 `load_shadow_candidates`를 호출해 cursor
기반 candidate event page를 제공한다. 각 backend의 QueryStore 구현 4개와 helper 4개를
`database_shadow_state.py`의 SQLite/PostgreSQL mixin으로 이동했다. QueryStore Protocol,
store 조립, 기존 root helper 이름은 유지한다.

기존 `shadow_checkpoint.py`는 checkpoint frame 분해·복구, schema statements, frame DML을
caller-owned cursor로 실행하는 별도 경계다. 새 DB module은 이 helper를 호출하되 기존
PostgreSQL connection/context를 넘겨 추가 connection·COMMIT을 만들지 않는다. inline/v2
fallback, stable parent row lock, same-monitor serialization, 독립 peer commit과 snapshot history
동작을 유지한다. 새 API, 테이블, migration, DB transaction 통합은 없다.

변경 전후 backend method 8개와 helper 4개의 AST 본문, QueryStore Protocol이 동일하다.
consumer audit은 signature/호출 변경 0, 소비자 338곳이며 PostgreSQL connection gate는
42/42 그대로다. 로컬 SQLite·observed transaction·CandidateMonitor·인증 API 관련 검사가
11건 통과했다. 이를 포함해 API route의 현재 저장소 소비까지 로컬로 확인했다.

후보 PostgreSQL 검사 묶음은 다음 7개다.

- `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests.test_shadow_evaluation_and_checkpoint_keep_replay_and_independent_commits`
- `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests.test_mock_automation_checkpoint_restores_state_without_extra_write`
- `tests.integration.test_shadow_checkpoint_postgres.ShadowCheckpointPostgresTests.test_migration_cursor_only_one_frame_eviction_order_and_inline_recovery`
- `tests.integration.test_shadow_checkpoint_postgres.ShadowCheckpointPostgresTests.test_frame_failure_rolls_back_header_and_commit_ack_retry_changes_no_frame`
- `tests.integration.test_shadow_checkpoint_postgres.ShadowCheckpointPostgresTests.test_same_monitor_serializes_snapshots_while_peer_commits_independently`
- `tests.integration.test_shadow_checkpoint_postgres.ShadowCheckpointPostgresTests.test_missing_or_unknown_v2_is_not_silently_replaced_by_legacy`
- `tests.integration.test_shadow_checkpoint_postgres.ShadowCheckpointPostgresTests.test_offline_downgrade_commits_latest_document_and_restores_old_schema_contract`

후보 `kiwoom-db-shadow-state-split-pilot-v1-20261004.zip`을 X 공유
`X:\kiwoom-monitor\artifacts`에 스테이징했고 SHA-256은
`623E95852965C2518A81D1EC4D8409CA1415937861F33B584D1A8CCB5E17F12F`다. ZIP CRC와 후보
모듈·7개 테스트 import 경로를 확인했다. NAS 전용 PostgreSQL gate는 7/7 통과했다
(4.979초). 운영 DB·NAS active release·image는 변경하지 않았다.

### 열다섯 번째 분리 계획: dataset snapshot

#### 구현 상태 · 2026-10-04

로컬 후보 구현은 완료했다. 두 backend의 dataset store 구현 6개와 shared observation helper 6개를
각자의 새 하위 모듈로 이동하고 두 backend wait probe를 `postgres_access.py`로 옮겼다.
이동 AST digest 18개는 계획 manifest와 모두 일치하며, root compatibility import·QueryStore
계약·logger 이름을 유지했다. 보강된 정적 PostgreSQL 감사는 직접 연결 42/42, 미승인·stale
0을 보고했다. helper-only 2단계 named-import/alias와 상위 계층 미확장 fixture도 통과했다.
SQLite store 57건, market ingestor 12건과 후보 모듈 import/compile 검사가 통과했다.
소비자 감사에서 계약 서명·reviewed store reference 338건·forwarding edge 46건은 변함없다.
추가/삭제 candidate 두 건씩은 단건 writer의 동일 batch 위임 소유자가 `database.py`에서 새
dataset mixin 파일로 이동한 경로 변경이며, 새 consumer나 위임 edge가 아니다. stale binding은
없다. 도구의 `review_required` 상태는 이 경로 이동 후보를 자동 승인하지 않는 결과다.

Windows sandbox에서는 asyncio event loop 초기화가 loopback socket pair 생성에서 멈춰
TOP20 async suite를 실행하지 못했다. 실제 authenticated snapshot API 및 PC client decoding도
아직 미확인이다. 이를 통과로 간주하지 않는다. 2026-10-04 NAS 전용 PostgreSQL gate 9/9가
후보 ZIP에서 통과했다(4.973초). 후보 실행기는 ZIP 내부의 새 dataset 모듈을 확인했다.
SQLite·consumer 정적 검증과 PostgreSQL gate가 끝났으므로 이 도메인의 물리 분리는 완료다.
운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-04에 현재 소스의 공유 helper와 transaction 소유권을 조사했다. 이 단계에서 두
backend의 `save_dataset_snapshot`, `save_dataset_snapshots`, `load_dataset_snapshots` 구현
6개를 분리했다. 이미 분리한 `load_top20_statistics`는 기존 모듈에 유지한다.

`MarketDataIngestor`·`AutonomousTop20Service`·`RealtimeCollector`가 dataset을 저장한다.
`central_dataset_snapshots`의 키는 `(kind,subject,snapshot_key)`이며 reader는
subject 필터와 snapshot_key 역순/limit을 적용한다. 인증 snapshot API와 PC
`remote_client.py`가 같은 반환 형식을 소비한다. TOP20 membership의 RAM latest 분기와
DB fallback도 보존해야 한다. 실시간 collector와 program snapshot writer의 실패 후
pending 복원은 호출자 책임으로 남긴다.

SQLite batch는 기존 `_lock`과 `_connection()`을 사용한다. PostgreSQL batch의 snapshot
upsert, 과거 날짜 advisory transaction lock, 통계 캐시 삭제, metadata upsert와 revision
추가는 기존 한 cursor/connection에 남는다. 단건 writer는 기존 batch 메서드에 위임한다.
현재 async COMMIT 대상 kind와 혼합 batch의 동기 COMMIT 판정, observed kind 선별,
기존 명시 COMMIT·rollback·close 순서와 wait-thread 종료 순서를 바꾸지 않는다.

#### 공유 helper 소유권과 이동 범위

새 dataset 모듈이 root store를 역으로 import하지 않도록 다음 위치를 사용한다.
함수 본문과 기존 root import 이름은 그대로 보존한다.

| 위치 | 실제 이동 대상 | 소유권 |
|---|---|---|
| 새 `database_datasets.py` | backend 구현 6개, `DatasetSnapshotWrite`, `ASYNC_COMMIT_DATASET_KINDS`, `COMMON_OBSERVED_DATASET_KINDS`, `_uses_async_dataset_commit` | dataset 저장·조회와 해당 commit 정책. 생성자/연결 factory/초기화는 root에 유지 |
| 새 `database_observation_writes.py` | `_market_metadata_upsert_sql`, `_market_metadata_upsert_suffix`, `_append_sqlite_observation_revision`, `_append_postgres_observation_revision`, `_insert_postgres_observation_revision`, `_observation_revision_values` | 봉·dataset이 빌려 준 cursor/connection으로 실행하는 공통 관측 SQL. 독립 연결·COMMIT을 만들지 않음 |
| 기존 `postgres_access.py` | `_sample_postgres_backend_waits`, `_postgres_wait_summary` | dataset과 뉴스 claim이 공유하는 지연 진단. 기존 1초 지연·0.1초 주기·SELECT·연결 인자를 유지 |

`database_datasets.py`는 domain 계약, 기존 metadata codec, 하위 DB codec/관측 SQL/
PostgreSQL 관측 helper, 기존 TOP20 캐시 날짜 helper만 참조한다. 상위 application,
service/API/UI 또는 root `database.py`를 import하지 않는다. logger는 기존
`kiwoom_monitor.central_server.database` 이름을 사용해 경고 채널을 유지한다.

root는 위 타입·상수·함수를 다시 import하여 기존 import 이름을 유지하고 두 mixin을
store 조립에 추가한다. 남은 봉·metadata writer와 batch revision helper는 같은 함수
binding을 사용한다. batch revision 구현이나 `save_market_data_metadata` 구현은 이번
단계에 이동하지 않는다. 전달 wrapper나 새 runtime branch는 추가하지 않는다.

dataset 저장을 이해하는 실제 구현 파일은 root 중심에서 dataset/관측 SQL/common
PostgreSQL 접근 파일로 나뉜다. 이는 실제 저장 책임과 공유 cursor helper의 이동이며,
`단건 → batch → revision helper → INSERT helper` 호출 깊이는 늘어나지 않는다.

#### 이동 전 필요한 정적 감사 보강

현재 `audit_postgres_access.py`는 store 메서드가 있는 파일끼리의 named helper import만
따른다. helper만 있는 새 관측 SQL 모듈을 그대로 추가하면 helper SQL이 원장에서
빠지는 문제가 있다. 따라서 DB 소스 이동 전 다음 보강을 먼저 검증한다.

- AST만 읽어 로컬 `central_server/database_*.py`와 `postgres_access.py`의 top-level
  function을 수집하고, 명시적인 named import/alias를 따라 helper 호출을 추적한다.
  상위 service/API/UI, reflection, 동적 import와 외부 library까지 확장하지 않는다.
- 실제 PostgreSQL method에서 도달한 helper 파일을 selected PG/helper 원장에도 넣는다.
  helper-only 파일, 두 단계 helper import와 alias의 SQL/table 보존 및 상위 계층 미추적을
  fixture로 검증한다. 모듈 실행이나 DB 연결은 하지 않는다.
- 같은 보강된 도구로 소스 이동 직전/직후 원장을 따로 생성한다. 기존 기준선은 덮어쓰지
  않는다. 추가로 보이는 helper는 감사 coverage 보강으로 설명하고 실제 SQL 변경으로
  보고하지 않는다.
- 대기 probe의 승인 site는 `database.py:_sample_postgres_backend_waits`에서
  `postgres_access.py:_sample_postgres_backend_waits`로 정확히 한 곳만 이동한다.
  실제 함수 AST·driver call·연결 인자를 비교한 뒤 기존 승인 이유를 보강한다.
  승인 수 42개 유지와 unapproved/stale 0을 확인하며 새 site를 자동 승인하지 않는다.

#### 검증 범위

변경 전후 구현 6개·helper 9개·타입/상수 3개의 AST와 QueryStore 계약을 비교한다.
기존 root alias와 logger identity, 상위 import 없음, consumer 추가/삭제 0도 확인한다.
SQLite dataset upsert/filter·batch 실패 rollback·ranking 관측 시각·TOP20 캐시 무효화,
기존 관측 revision deduplication/batch chain 및 봉+metadata rollback 검사를 묶는다.
실제 SQLite store를 사용하는 collector/ingestor/TOP20 경로, snapshot 인증 API와 PC
remote client decoding도 확인한다. TestClient 환경 문제가 생기면 저장소 `.venv`의
승인된 사용자 환경에서 같은 검사만 재실행하고 실제 결과를 기록한다.

NAS 전용 PostgreSQL 후보 검사 묶음은 다음 9개를 사용한다.

- `test_market_state_dataset_replay_and_failure_preserve_reader_and_correlate_metrics`
- `test_new_high_and_program_flow_dataset_replay_preserve_readers_and_correlate_metrics`
- `test_ranking_and_top20_membership_revisions_and_metrics_are_preserved`
- `test_top20_and_market_index_writes_invalidate_statistics_atomically`
- `test_investor_fundamental_and_nxt_dataset_writes_preserve_reader_boundaries`
- `test_top20_statistics_cache_preserves_warm_cold_and_concurrent_reads`
- `test_active_readers_batch_preserve_native_context_and_separate_metrics`
- `test_observation_revision_readers_keep_native_context_and_separate_kinds`
- `test_query_bar_metadata_available_at_changes_only_with_content_or_state`

모두 `tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests`의
메서드다. 후보 ZIP은 package marker와 runner를 포함하고, 내부에서 새 모듈과 9개 테스트
이름이 해석되는지 확인한 뒤 X artifacts에 stage한다. NAS active release·운영 DB·image는
변경하지 않는다. PostgreSQL gate 완료 전까지 물리 분리 완료로 표시하지 않는다.

### 열여섯 번째 도메인 완료: 국내 시장 봉 저장·조회

양 backend의 분봉·초봉·5분봉·일봉 저장/조회 구현과 bar 전용 helper를
`database_market_bars.py`로 이동했다. QueryStore Protocol과 store 조립은 `database.py`에
남겼고, 기존 호환 import 이름도 유지했다. 공통 대기 probe는 뉴스 claim과 분봉 SQL 계측이
함께 쓰므로 `postgres_access.py`로 옮겨 양 경로가 같은 구현을 사용하게 했다. 새 도메인
모듈은 서비스·API·화면 계층을 import하지 않는다.

보존 대상은 0B 분봉 flush와 ka10080 조회 authority에 따른 canonical bar 선택, operation
hash/idempotency, 날짜·종목·시장 advisory transaction lock, 중복 키의 입력 순서, bar·metadata·
revision 원자 저장 및 rollback, changed-only replacement와 `available_at`, PostgreSQL native
connection/context와 writer metrics다. helper 본문과 SQL 순서는 추출 과정에서 바꾸지 않았고,
테스트의 private patch 위치만 새 소유 모듈로 이동했다. 이전 dataset 분리 때 누락된 두
`save_dataset_snapshot` 내부 delegate의 정적 identity도 기존 책임자에 맞게 baseline에 반영했다.

기준선 inventory에 기록된 구현 메서드·bar helper 20개의 AST hash가 이동 전 값과 모두
일치한다. 로컬 검증은 DB 및 commit correlation 77건, PostgreSQL access의 뉴스 probe와 market-bar
reader/sort 검사 4건, QueryStore 소스 상속·소비자 baseline 검사 3건이 통과했다. 정적
QueryStore audit은 계약 signature 변경 0, 소비자 연결 추가/삭제 0, internal delegate 4개,
forwarding edge 46개이며 baseline과 일치한다. PostgreSQL direct connection audit은 승인된
42/42 연결이 일치하고 신규·stale 0이다. NAS 전용 PostgreSQL gate 9/9가 통과했다(12.607초).

후보 ZIP의 전용 PostgreSQL gate는 아래를 함께 실행한다.

- `PostgresAccessIntegrationTests.test_query_market_bars_keep_native_history_and_correlate_both_metrics`
- `PostgresAccessIntegrationTests.test_market_bar_readers_keep_native_context_and_separate_kinds`
- `PostgresAccessIntegrationTests.test_query_bar_metadata_available_at_changes_only_with_content_or_state`
- `PostgresStorageBoundaryTests.test_realtime_minute_batch_prefetches_guards_and_preserves_replay`
- `PostgresStorageBoundaryTests.test_realtime_minute_duplicate_keys_keep_sequential_delta_updates`
- `PostgresStorageBoundaryTests.test_closed_query_remains_canonical_after_late_realtime_save_and_finalize`
- `PostgresStorageBoundaryTests.test_parallel_closed_query_and_realtime_write_keep_query_values`
- `DiagnosticReplayPostgresTests.test_query_minute_scenarios_preserve_noop_revision_values_and_clean_all_tables`
- `DiagnosticReplayPostgresTests.test_query_minute_invalid_observation_rolls_back_and_cleans_only_its_scope`

NAS candidate test 성공으로 물리 분리가 완료됐다. 후보 ZIP은
`kiwoom-db-market-bars-split-pilot-v1-20261004.zip`이며, 운영 DB·NAS active release·image는
변경하지 않았다. 실제 API/client/UI consumer runtime 연결은 source/runtime 환경이 없는 로컬
검사에서 새로 확인하지 않았으며, 기존 consumer site 338개와 계약은 정적으로 동일함을 확인했다.

### 스무 번째 도메인 완료: 뉴스 소스 페이지·진행상태·저장 피드

SQLite/PostgreSQL 저장소 구현 8개와 source cursor/page, article target, 기존 BODY/RULE job
연결, source diagnostics 및 저장 market feed helper 17개, cursor field 상수 1개를
`database_news_sources.py`로 옮겼다. QueryStore 계약, 두 backend의 mixin 조립, 기존
`database` 모듈의 함수·상수 이름을 유지했다. source page와 historical batch는 같은
caller-owned cursor를 사용하고 기사 revision·target·job·run/source observation·cursor 변경을
한 transaction에 보존한다. PostgreSQL article revision table lock과 성공 commit 뒤 worker
wake-up도 이동 전 순서를 유지했다. 새 모듈은 DB 하위 계약·codec·job-write helper만 참조하고
service/API/UI 계층은 import하지 않는다.

AST 대조상 QueryStore 99개와 SQLite 104/PostgreSQL 105개 구현 계약·본문 변경은 없고,
이동 helper 미해결 전역 참조는 0이다. consumer 338곳, PostgreSQL 직접 연결 승인 42/42,
storage path 및 logical PG inventory 105개도 변경되지 않았다. 뉴스 source collector,
market-feed collector/client/window, authenticated diagnostics/feed API, historical PC job,
news job·article/event history 로컬 회귀 76건이 통과했다. 전체 API route 계약 검사는
이 선택 검사 묶음에 포함하지 않았다.

전용 PostgreSQL 후보는 `kiwoom-db-news-sources-split-pilot-v1-20261005.zip`이며 SHA-256은
`6B6B13646FFE1BB85434C7483EEEF588EB2031950EFA3B5E87A787D2002F8ACF`다. ZIP 안에서 새
모듈이 로드됐고 아래 NAS 전용 PostgreSQL 검사 5/5가 통과했다(1.906초).

- `PostgresAccessIntegrationTests.test_news_source_page_replay_and_failure_preserve_article_and_progress`
- `PostgresAccessIntegrationTests.test_historical_market_batch_replay_and_failure_preserve_history_and_progress`
- `PostgresAccessIntegrationTests.test_external_news_claim_preserves_parallel_ownership_and_reader`
- `PostgresAccessIntegrationTests.test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback`
- `PostgresAccessIntegrationTests.test_news_body_revision_links_rule_job_and_rolls_back_missing_article`

운영 DB·NAS active release·image는 변경하지 않았다.

### 스물한 번째 도메인 완료: 과거 뉴스 외부 처리·시황 batch

SQLite/PostgreSQL 저장소 구현 `claim_external_historical_news_job`,
`complete_external_historical_news_job`, `save_historical_market_news_batch` 3개씩과
caller-owned DB context를 사용하는 `_historical_market_source_page`,
`_complete_external_news_job` helper를 `database_historical_news.py`로 이동했다. 기존
QueryStore 계약·backend mixin 조립·`database` import 호환성을 보존했다. Completion 정책은
기존 `grouped_candidate_identities` 함수를 store 조립 시 callback으로 주입해 DB 모듈이
application/service 계층을 import하지 않게 했으며, 같은 cursor와 transaction을 유지한다.
PostgreSQL `FOR UPDATE ... SKIP LOCKED` claim, completion ownership/attempt fence, article
revision·job atomicity, batch advisory lock/idempotency, 성공 commit 뒤 wake-up 순서를 바꾸지
않았다.

정적 이동 감사에서 QueryStore consumers 338개, PostgreSQL 승인 연결 42/42, storage paths 및
logical PostgreSQL method inventory가 이동 전과 동일했다. 관련 로컬 회귀 123건과 NAS 전용
PostgreSQL gate 5/5가 통과했다(1.728초). 첫 후보에서 마지막 gate가 실패한 것은 테스트의 실패
주입이 root 호환 alias를 패치하고 실제 소유 모듈 helper를 패치하지 않은 테스트 seam 불일치였다.
패치 대상을 새 모듈로 옮긴 v2에서 rollback 검증이 통과했다. 후보
`kiwoom-db-historical-news-split-pilot-v2-20261005.zip` SHA-256:
`D5F3A56A4165F2F89744E020EADD7E2210B6AFAB9F3AD6D073028142CD2FFC85`.
운영 DB·NAS active release·image는 변경하지 않았다.

- `PostgresAccessIntegrationTests.test_news_body_revision_links_rule_job_and_rolls_back_missing_article`
- `PostgresAccessIntegrationTests.test_news_source_page_replay_and_failure_preserve_article_and_progress`
- `PostgresAccessIntegrationTests.test_historical_market_batch_replay_and_failure_preserve_history_and_progress`
- `PostgresAccessIntegrationTests.test_external_news_claim_preserves_parallel_ownership_and_reader`
- `PostgresAccessIntegrationTests.test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback`

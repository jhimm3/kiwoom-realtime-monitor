# DB 리팩터링 — 변경 전 연결 기준선

2026-10-04 · 기준 commit `1ccaca1` · 첫 조사 단계

## 범위와 완료 조건

첨부된 구조·운영 로드맵 중 DB 접근/관측, 데이터 중복·누락·재실행,
DB 작업의 동시성·종료, migration, DB 재시도·오류, 저장·조회 통합검증을 적용한다.
이번 시작 범위는 중앙 `QueryStore`의 PostgreSQL 경로와 SQLite 호환 경로다.
PC monitor/news/journal의 별도 SQLite 저장소는 후속 기능별 조사에 포함하며,
이번 원장만으로 그 저장소 전체의 연결을 검증했다고 판단하지 않는다.

기준선 완료 후에는 `central_server/database.py`의 QueryStore 메서드와
PostgreSQL/SQLite 구현을 도메인별로 분류하고, 외부 계약과 transaction·lock·revision
의미를 보존하면서 도메인 하나씩 물리 모듈로 분리한다. 관측된 latency 문제가 없는
도메인도 책임 집중 해소 필요성을 별도로 평가한다. 전달만 하는 계층을 추가하거나
두 backend를 한 번에 재설계하지 않는다.

새 DB 구현 모듈은 상위 service/API/UI 계층을 import하거나 이에 의존하지 않는다.
DB 저장·조회 책임과 하위 공통 DB helper 방향을 유지한다.

한 도메인의 물리 분리는 변경 전후 정적 연결 대조, QueryStore 계약, SQLite/PostgreSQL
동작, transaction·rollback·lock·revision 의미, 실제 API/client/화면 또는 후속 작업
연결 검증을 모두 통과한 뒤 완료로 판단하고 다음 도메인으로 넘어간다.

`database.py`의 물리적 책임 분리는 동작 최적화와 별개다. 기준선이 완료됐으면
latency 문제 유무와 관계없이 도메인 분류와 첫 모듈 분리를 진행한다.

각 수정은 호출자 → 저장 책임자 → 테이블/키 → 조회자 → API/화면/후속 작업을
변경 전에 기록하고 변경 후 같은 입력으로 대조한다. 정적 이름이 남는 것만으로
연결 정상이나 기능 보존을 판정하지 않는다. 기존 transaction 소유자,
독립 COMMIT, rollback, 종료, retry, lock, revision, cache 무효화를 보존한다.

## 생성한 기준선

첫 물리 분리의 진행 상태와 미완료 PostgreSQL 검증은
[도메인 분리 기록](domain_decomposition.md#2026-10-04-진행검증-기록)에 기록한다.

- `baseline_postgres_access.json`: 기존 `scripts/audit_postgres_access.py` 결과.
  현재 store 메서드 105개, 직접 driver 연결 호출 지점 42개, AST parse 오류 0개다.
  `candidate_db_api_calls_all_backends=7004`는 SQLite/비DB 이름까지 포함하는
  후보 호출 지점이며 실제 DB 호출 수나 운용 중 writer 수가 아니다.
- `baseline_storage_paths.json`: 기존 `scripts/audit_kiwoom_storage_paths.py` 결과.
  REST 원천·실시간 등록·직접 SQL 저장 후보를 함께 보존한다.
- `baseline_manifest.json`: 두 원장의 hash, 기준 commit, 생성 도구와 범위.

원장은 운영 DB에 연결하거나 앱을 import하지 않는 소스 검사로 생성했다.
기준선을 수정 후 결과로 덮어쓰지 않는다. 이후 비교는 같은 도구를 사용하되
별도의 `after_*` 파일로 생성한다. 줄 번호 이동은 연결 삭제로 판단하지 않고
파일/함수/연결 표현식과 개수를 대조한다. 간접 호출·동적 SQL은 수동 추적한다.

2026-10-04에 QueryStore 소비자 정적 감사 기준선도 추가했다.
`baseline_query_store_consumers.json`은 QueryStore와 두 backend 전체 method signature
(인자, 기본값, 반환형, sync/async, decorator, type parameter 포함)와 확인된
호출 identity, 같은 이름 후보, helper 전달 경로를 보존한다. 현재 원장은
Protocol 99개 method, PostgreSQL 104개, SQLite 103개, 확인된 store reference
338곳, backend 내부 delegate 4곳, store 전달 edge 46개, 미분류 동명이인 후보
23건이다. 후보는 DB 연결로 세지 않으며 사라지거나 새로 생기면 비교 gate가 검토를
요구한다. 승인된 receiver 근거는 `query_store_consumers_approvals.json`에 있고,
새 후보를 승인 목록에 자동 추가하지 않는다.

기준선 확인 명령은 `python scripts/audit_query_store_consumers.py --check`이다.
상세 JSON이 필요할 때는 `--output <별도 결과 경로>`를 더한다. 기준선 생성 옵션은
초기 연결 검토 때만 사용한다. 이 검사는 Python AST만 읽으므로 앱 import, DB 접속,
SQL 실행이나 실제 route/backend 실행 여부는 증명하지 않는다.

## 이번에 코드로 확인한 연결

| 기능 | 입력/호출 책임자 | 저장과 조회 연결 | 보존할 의미 |
|---|---|---|---|
| 중앙 store 조립 | `app.create_app` → `create_query_store` | URL scheme으로 `SQLiteQueryStore` 또는 `PostgresQueryStore` 생성 후 `initialize` | 조립에서만 backend 선택. 서비스와 라우트는 같은 store를 사용한다 |
| REST 영속 캐시 | `rest_broker.py`의 `load_query`/`save_query` 호출 | `central_api_query_cache` → `StoredQuery(payload,has_next,next_key)` → broker 응답 | 만료 필터·payload/연속조회 필드. PG 저장의 upsert와 만료 삭제는 기존 명시 COMMIT 안에 있다 |
| REST 분봉 | `MarketDataIngestor` → `replace_minute_bars` | canonical 봉·관측 metadata·revision → `load_minute_bars` → `app.py`의 분봉 조회 | SOR/KRX/NXT 구분, 같은 키 replay, metadata와 revision 원자성. 비교 document는 별도 저장 경계다 |
| 실시간 최신값 | `RealtimeCollector._flush_snapshot_cycle` → `save_realtime_snapshots` | `central_realtime_latest` → `load_realtime_snapshots` → 중앙 WebSocket subscriber | flush lock·`asyncio.to_thread`·실패 시 pending 복원. native connection context의 COMMIT/close |
| 일봉과 완료 근거 | store의 `load_daily_bars`와 coverage document | `app.py` 일봉 라우트 → `choose_daily_coverage` → bars/coverage 응답 | 날짜/시장/완료 근거를 함께 보존. 존재 여부만으로 완료 판정하지 않는다 |
| TOP20 dataset | `autonomous_top20.py` → dataset snapshot 저장 | `load_dataset_snapshots` → `/api/v1/market/snapshots/{kind}` | membership의 RAM latest 분기와 저장 snapshot fallback을 각각 검사한다 |
| shadow 복구 | `CandidateMonitor`의 `load_shadow_monitor_state`/`save_shadow_monitor_state` | `central_shadow_monitor_state`와 opt-in frame 경로 → 같은 monitor 상태 복구 | inline 기본 경로·frame 후보·downgrade fallback을 별개로 검증한다 |
| 뉴스 문서와 작업 | `NewsService`/`NewsJobRunner` → document·job 메서드 | `load_stock_news_articles`, BODY/RULE 작업 claim/finish 및 projection | claim의 SKIP LOCKED·상태/attempt 경계, 기사와 revision 연결. 전체 뉴스 화면까지의 검증은 후속 |

이 표는 해당 코드의 연결을 읽은 증거이며 실행 성공 표가 아니다.
PC 원격 client·화면 끝단, 계좌/주문, 일지·설정·백업의 전체 경로 대조는 남아 있다.
계좌 신원·원장·자격증명 경계는 시장 cache와 COMMIT을 합치지 않는다.

## 확인한 직접 연결 검토 원장 불일치와 정정

현재 승인 원장은 30곳인데 소스에는 42곳이 있어 기존 `--check`가
`review_required`를 반환했다. 누락 12곳은 다음 10개 함수에 있다.
이 차이는 연결 단절이나 성능 병목의 증거가 아니라 검토 원장과 소스의 차이다.

| 파일/함수 | 새 호출 지점 | 코드에서 확인한 용도 |
|---|---:|---|
| `scripts/check_source_database.py:main` | 1 | source 호환성 점검. 기본은 READ ONLY, 명시 `--offline`은 배포 소유자가 서버를 멈춘 뒤 알려진 checkpoint downgrade를 하는 별도 maintenance 경로. observed wrapper 내부 factory |
| `diagnostic_replay.py:_ReplayStore._connect` | 1 | replay용 독립 연결 factory. `run_replay` 사전검사가 전용 DB를 확인한 뒤 store를 만든다 |
| `diagnostic_replay.py:run_replay` | 1 | autocommit/READ ONLY 연결로 실제 DB명·필수 테이블·scope 충돌 확인 후 측정 writer 연결로 진행 |
| `test_diagnostic_replay_postgres.py:_assert_minute_scope_empty` | 1 | 재생/정리 이후 scope 잔여 확인 |
| 같은 파일 `test_news_and_shadow_replay_commit_and_remove_only_their_rows` | 2 | 전용 DB 사전검사와 peer/정리 대조 |
| 같은 파일 `test_query_minute_invalid_observation_rolls_back_and_cleans_only_its_scope` | 1 | 전용 DB 사전검사와 실패 scope 검증 |
| 같은 파일 `test_realtime_and_program_shapes_commit_verify_and_clean_scoped_rows` | 1 | 전용 DB 사전검사와 원천 shape 검증 |
| 같은 파일 `test_recorded_minute_failed_call_rolls_back_then_cleans_seeded_scope_and_keeps_peer` | 1 | 전용 DB 사전검사와 실패/peer 보존 대조 |
| `test_shadow_checkpoint_postgres.py:test_1803_frame_single_change_compares_real_dml_wal_and_buffers` | 2 | class 사전검사로 전용 DB 확인 후 실제 DML 측정·rollback 및 상태 대조 |
| 같은 파일 `test_offline_downgrade_commits_latest_document_and_restores_old_schema_contract` | 1 | class 사전검사 후 테스트 전용 schema의 downgrade 검증 |

시험 코드의 호출 지점은 9개, 진단 runtime/maintenance는 3개다.
production DSN으로 replay DML을 허용하는 변경이나 기존 연결을 공통 singleton으로
통합하는 변경은 이 조사에서 제안하지 않았다. 승인 원장을 수정할 때 위 용도와
보호 조건을 이유에 명시하고, 새 연결이나 삭제된 승인 항목은 계속 검사 실패로 남긴다.

2026-10-04 후속에서 위 12곳을 용도별 이유와 함께 검토 원장에 등록했다.
새 `after_postgres_access.json`에서 42/42 site identity 일치, 신규 0, stale 0,
parse 오류 0으로 gate가 통과했다. 기준선 JSON은 최초 30/42 상태를 보존한다.
이 정정은 동작을 바꾸지 않고 production 연결의 빠짐을 승인한 것이 아니다.
정적 감사 회귀검사는 미검토 site와 남은 승인을 각각 실패시킨다.
대상 PC 단위검사 2건도 통과했다.

같은 기준선과 후속 원장의 direct connection identity를 전수 비교했다.
파일/함수/driver 호출 개수는 같고 줄 번호만 달라진 경우는 연결 삭제로 판단하지 않았다.
storage inventory도 다시 만들어 API ID·실시간 등록·쓰기 경계 후보가 같은지 대조했다.

## 기능 보존 검사 순서

1. 정적 연결 원장 전후 대조: direct connection 검토, QueryStore 계약·호출자,
   backend 구현과 delegate/helper. 다른 이름의 동일 메서드는 실제 소유자를 확인한다.
2. 변경 대상 단위 경로: 같은 input/output, key/순서, commit/rollback/close 및
   observer 실패가 업무 결과를 바꾸지 않는지 검사한다.
3. SQLite fixture와 독립 PostgreSQL 진단 DB: 동일 키·중복·부분 실패·병렬 처리,
   COMMIT 응답 유실·재시작 뒤 값/건수/revision/완료 상태를 비교한다.
4. 실제 소비자 통합: 저장 → 인증 API 조회 → 기존 client decoding → 화면 또는
   다음 작업의 입력까지 연결한다. raw cache hit와 Kiwoom TR 요청은 구분한다.
5. 운영 적용 뒤 동일 조건의 자료 보존과 지연 지표를 다시 확인한다.
   소스 복사·`OK`·health만으로 장중 보존이나 성능 개선을 확정하지 않는다.

검사 도구가 현재 통과한다는 이유로 과거에 통과한 DB 검사를 모두 다시 실행하지 않는다.
실제 변경된 경계에 맞는 검사만 묶어서 실행하고 검증되지 않은 끝단은 남겨 둔다.

## 다음 변경과 남은 범위

2026-10-04에 [QueryStore 호출자·소비자 연결 검토](query_store_connection_review.md)를
추가했다. store 조립/전달 경계, 서비스→store→API→PC 소비 경로와 선택적
capability를 확인했고, 후속 정적 감사의 reference/비교 계약을 정했다.
이 문서는 코드 검토 결과이며 운영 실행 coverage 또는 기능 acceptance가 아니다.

감사 원장 정정과 회귀검사는 끝났다. 아직 수행하지 않은 DB 동작 변경은
실제 latency 또는 중복·누락 후보를 확인한 뒤 연결·동작 보존 검사를 갖춘 기능 하나를
선택해 진행한다. runtime 재현 없이 pooling, COMMIT 통합, schema 변경,
writer 수명 변경에 착수하지 않는다.

이 증거 조건은 동작 최적화에 적용한다. 물리적 책임 분리는 정적 기준선 완료에 따라
진행하며, [도메인 분류와 분리 계약](domain_decomposition.md)에 QueryStore 전체
메서드 분류와 도메인별 물리 분리·검증 기록을 남긴다. 외부시장 봉, REST 응답 캐시,
저장소 진단을 포함한 21개 책임군의 실제 이동은 각 후보 기준 전용 PostgreSQL 검사로
검증했다. 현재 `database.py`에는 aggregate 계약, SQLite/PostgreSQL store 조립·초기화·종료·연결,
factory와 기존 import 호환 이름이 남아 있다. 이 잔여 경계는 저장 backend 선택과 수명을
소유하므로 전달용 모듈로 다시 옮기지 않는다. QueryStore 작은 계약 재평가 결과는 아래와
[도메인 분리 기록](domain_decomposition.md)에 남긴다.

정적 기준선은 확인된 QueryStore 호출과 한 단계 helper/store 전달을 함께 보존하지만,
임의의 reflection, 동적 method 이름, 다단계 호출 전파, route/backend 분기 실행,
PC remote client와 실제 화면 끝단의 runtime 성공을 증명하지 않는다. 미분류 receiver는
후보로 남기며, 해당 기능을 수정할 때 저장 결과부터 PC 소비까지 실행 검증을 보탠다.

기준선·소비자 연결 검토 단계는 애플리케이션 코드와 DB를 변경하지 않았다. 이후
도메인별 물리 분리에서는 해당 코드 경계만 변경하며, schema·실제 DB·NAS active
release는 변경하지 않는다. 기존 보류 사항과 장중 trace/capture 작업은 `../OPEN_ITEMS.md`를 따른다.

도메인별 구현 모듈 분리가 충분히 진행된 뒤 현재의 큰 `QueryStore Protocol`도
도메인별 작은 계약으로 분리할 필요가 있는지 별도로 재평가한다. 단순히 크다는 이유만으로
분리하지 않으며, 실제 책임·소비자 경계가 명확한 경우에만 진행한다.

2026-10-05 재평가에서 정한 REST 캐시 두 메서드의 소비자 계약을 현재 워크트리에 적용했다.
기존 aggregate는 보존하고 상속 계약 감사도 보강했다. [QueryStore 계약 검토](query_store_protocol_review.md)에
8개 직접 소비자 대조와 근거를 기록한다. 수정 후보의 PostgreSQL writer/reader, REST broker
period·in-flight merge, SQLite cache round-trip/expiry gate가 5/5 통과했다. 후속 후보 재평가에서
저장소 진단은 독립 주입 경계가 아니고 나머지 서비스는 여러 책임군 또는 worker 전달을 포함해
추가 Protocol을 보류했다. 후속 v2 test-only 후보에서는 SQLite-backed market coverage API
요청이 실제 ASGI route와 store를 통과하는 연결 검사가 NAS에서 1/1 통과했다. 운영 반영을 뜻하지 않는다.

2026-10-05 잔여 `database.py` 경계 확인: top-level에는 `QueryStore`, 두 backend composition
class, `create_query_store`만 남아 있다. backend class는 생성자·초기화·종료·연결 수명을 직접
소유하고 저장 구현은 분리된 mixin에서 제공한다. 별도의 lifecycle module은 store 조립을 다시
위임하는 층만 만들게 되므로 추가 이동하지 않는다. 이 판단으로 DB 업무 로직 전체의 API/client/UI
runtime 검증까지 끝난 것은 아니며, 미확인 끝단과 운영 반영은 별도 상태로 유지한다.

같은 날 전체 정적 연결 원장을 다시 검사했다. QueryStore 계약 99개, backend 구현 103/104개,
consumer reference 338곳과 전달 edge 46개가 기준선과 일치했고 추가·삭제·signature 변경·parse
오류는 0이다. PostgreSQL 접근 감사는 direct connection 42/42 승인 일치, 미승인·stale 0이다.
storage path inventory의 29개 literal REST call, 32개 API ID 그룹, 19개 동적 호출 후보,
20개 broker ID, 9개 실시간 타입, 32개 PostgreSQL writer/table 쌍, 97개 PC SQLite table을
확인했다. API ID의 파일 위치는 도메인 모듈 이동을 root `database.py`로 정규화해 비교했고,
writer/table 쌍은 method/table identity로 비교해 기준선과 같았다. 이는 정적 연결 증거이며
runtime·화면 사용 성공을 증명하지 않는다.

추가로 24개 `database_*.py` 모듈의 import AST를 검사했다. `application`, `presentation`,
API route `app`, broker/service/collector 모듈로 향하는 import는 0건이었다. 이 검사는 import
정적 방향만 다루며 동적 import나 runtime plugin 경로는 증명하지 않는다.

## 2026-10-04 이미 병합된 분봉·일봉 no-op 저장 검토

다음 동작 후보를 고르기 위해 현재 `main`의 기존 저장 최적화를 대조했다.
동일 봉 재수신 때 중복 metadata UPDATE를 줄이는 구현은 새 변경이 아니라
commit `d050cb5` (`Reduce redundant market data writes`)에 이미 포함돼 있다.
이를 다시 구현하거나 동일 동작을 덧붙이지 않는다.

경로는 `MarketDataIngestor` → `QueryStore.replace_minute_bars` /
`replace_daily_bars` → canonical bar와 metadata/revision 저장 →
`load_minute_bars` / `load_daily_bars` 및 metadata 조회다. PostgreSQL은 canonical
행의 의미 있는 값이 바뀐 경우에만 반환 키를 만들고, 변경되지 않은 봉은 metadata의
의미 필드(`effective_at`, venue/unit, value kind, completeness, origin, source,
candidate universe)가 바뀐 경우에만 metadata 행을 갱신한다. 이 경우 새 `available_at`
도 저장한다. 단순 재조회에서 이 의미 필드가 같으면 기존 `available_at`을 보존한다.
minute observation revision 비교와 저장은 기존 transaction 안에 남는다.

직접 대응하는 PostgreSQL 회귀검사
`test_query_bar_metadata_available_at_changes_only_with_content_or_state`는 동일 분봉·일봉
재조회 시각을 보존하고, 봉 값·완료 상태·출처가 바뀌면 새 시각을 저장하는 계약을
검사한다. 로컬 SQLite 경로의
`test_replay_preserves_metadata_and_single_observation_revision`도 같은 재생에서
metadata와 revision 중복 방지를 확인했다. 이번 실행에서는 후자 단일 테스트가 통과했고,
QueryStore 호출자 감사도 338개 reference, signature 변경 0, 추가/삭제 연결 0으로 통과했다.

현재 PC에는 `KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL`이 없어 PostgreSQL 통합검사는
실행되지 않았다(테스트 설정 단계에서 skip). 따라서 PostgreSQL SQL 동작·transaction
보존은 소스와 기존 회귀검사 계약을 확인했을 뿐, 이번 실행으로 새로 입증하지 않았다.
NAS 실행본 반영이나 운영 성능 개선도 확인하지 않았다. 다음 DB 동작 변경은 전용
PostgreSQL에서 같은 입력의 저장값·metadata 시각·revision·rollback과 소비자 조회를
대조할 수 있는 후보를 고른 뒤에만 진행한다.

## 2026-10-04 일봉 조회 정렬 계획 회귀 방지

기존 운영 계측에서 `load_daily_bars`의 `ORDER BY trading_date`가 SELECT의
`trading_date::text` 출력 별칭에 결합해 Seq Scan+Sort를 유발한 사례가 기록돼 있다.
현재 소스는 `ORDER BY central_daily_bars.trading_date DESC`로 이미 수정돼 있고,
운영 테이블의 code/market/date 보조 인덱스도 과거 별도 NAS 작업에서 생성됐다고
`docs/OPEN_ITEMS.md`가 기록한다. 이 단계에서는 쿼리를 다시 바꾸지 않고 정렬 표현을
보호하는 단위 회귀검사 `test_daily_bar_reader_orders_by_physical_date_column`을 추가했다.
검사는 PostgreSQL reader가 물리 날짜 컬럼으로 정렬하고 기존 날짜 문자열 결과,
필터·limit 인자와 한 번의 native transaction을 유지하는지 확인한다.

이번 PC 실행에서 새 SQL 회귀검사와 기존 native reader/context 검사를 실행해 2건이
통과했다. QueryStore consumer audit도 338개 연결에서 추가·삭제·signature drift 0으로
통과했다. 다만 전용 PostgreSQL URL과 NAS Docker 접근은 이 환경에서 확인되지 않아
실제 EXPLAIN 계획·현재 운영 release·실측 성능은 미검증 상태다. 이전 OPEN_ITEMS의
실행계획·시간 수치는 과거 관측으로만 취급하며 현재 활성 버전이나 통제된 전후
비교로 인용하지 않는다.

# QueryStore 계약 분리 재평가 — 2026-10-05

## 결정과 현재 상태

21개 도메인의 구현 분리 뒤 실제 소비자를 대조했다. **전체 99개 계약을 일괄 분할하지 않고,
REST 응답 캐시의 두 메서드만 독립 소비자 계약으로 좁힌다.** 기존 `database.QueryStore`는
전체 저장소를 조립·주입하는 지점의 호환 계약으로 유지한다. 첫 계약 이동과 정적 감사 보강은
현재 워크트리에 적용했고, 로컬·NAS 계약 검증을 완료했다. 이 판단은 DB 성능 개선 완료를
뜻하지 않는다.

크기가 아니라 확인된 소비자 경계가 선택 근거다. `CentralRestBroker`는 `load_query`와
`save_query`만 필요하고 기존 테스트의 작은 Store도 두 메서드만 구현한다. 반면 뉴스 서비스는
store를 하위 worker/collector에 전달하고, AI 서비스는 기능 유무에 따라 fallback하므로
직접 호출 목록만으로 계약을 줄이면 안 된다.

## 조사 근거

현재 워크트리에서 `scripts.audit_query_store_consumers.inventory()`를 실행했다.
앱 import·DB 접속 없이 확인한 결과는 `pass`다.

- QueryStore 99개, SQLite 103개, PostgreSQL 104개 메서드(backend 생성자 제외).
- 검토된 소비자 참조 338곳, 저장소 내부 위임 4곳, 전달 참조 46곳.
- 동명 미분류 후보 23곳을 보존했고 parse error는 0이다.
- 기준선 대비 검토된 참조·전달·계약 signature의 추가/삭제/변경은 없다.

표는 `QueryStore`를 직접 타입으로 받는 8개 소비자의 **서로 다른 메서드 수**다.
실제 DB 호출 횟수나 전체 간접 의존성 수가 아니다. 선택적 기능 참조도 포함한다.

| 소비자 | 메서드 수 / 참조 지점 | 직접 사용하는 범위 | 판단 |
| --- | --- | --- | --- |
| CentralRestBroker | 2 / 2 | `load_query`, `save_query` | 첫 적용 대상 |
| YahooDelayedMarketCollector | 3 / 5 | 외부시장 봉, 문서 조회·갱신 | 봉과 수집 상태 책임을 함께 후속 평가 |
| MarketDataIngestor | 7 / 14 | 일봉·분봉, metadata 범위, 문서, dataset | 응답 반영 경계 유지 |
| MarketEventService | 6 / 14 | VI·상한가 사실, cohort 이력, 문서 | event/revision 연결 별도 확인 |
| AutonomousTop20Service | 10 / 41 | 봉, dataset, 관측 revision, cohort, 문서 | 여러 준비 단계의 합성 소비자 |
| CentralAIService | 6 / 12 | 뉴스 AI/body/history, 문서 | 선택 기능과 fallback 구분 필요 |
| CentralNewsService | 7 / 18 | 요청 예산, 기사, AI job, dataset, 문서 | 하위 수집기·worker 전달까지 포함 필요 |
| CentralRealtimeCollector | 6 / 7 | latest·분봉·초봉·확정, dataset, 문서 | 독립 저장·종료 경계 보존 필요 |

세부 경로는 [소비자 연결 검토](query_store_connection_review.md)를 따른다.
`CentralNewsService.__init__`은 같은 store를 `NewsJobRunner`, `QuerySetNewsCollector`,
`MarketFeedNewsCollector`에 넘긴다. 타입이 `Any`인 소비자와 credentials owner의 service
store 접근도 이후 평가에서 제외하지 않는다. `app.py`의 factory/route/lifespan 및 진단
replay의 `PostgresQueryStore` 하위 클래스는 작은 캐시 계약 소비자로 취급하지 않는다.

이미 `ExecutionStore`, `ForwardEvaluationStore`, `AccountRegistryStore` 계약이 있으므로
중복 생성하거나 이번 단계에서 통합하지 않는다. `src`, `tests`, `scripts` 검색에서 현재
QueryStore를 대상으로 한 `isinstance`/`issubclass`와 직접 `__dict__` 참조는 발견되지 않았다.
정적 검색이 임의 reflection이나 외부 소비자의 부재까지 증명하지는 않는다.

첫 계약 이후의 후보도 직접 호출과 실제 소비 경계로 재검토했다. 저장소 진단의 두 메서드는
`app.py`의 store 조립 closure 안에서 한 route가 함께 호출한다. 별도 주입·수명 경계가 없어
작은 Protocol을 추가해도 소비자 의존성이 좁아지지 않으므로 보류한다. 나머지 직접 QueryStore
소비자는 여러 DB 도메인에 걸친 호출을 갖거나 같은 store를 하위 worker에 전달한다. 현재
증거로는 이들을 서비스별 Protocol로 나누는 것보다 aggregate를 유지하는 편이 안전하다.
따라서 이번 재평가에서 추가 계약은 만들지 않는다. 새로운 독립 소비자 경계가 생기거나 기존
서비스가 한 DB 책임군으로 좁혀질 때 다시 평가한다.

## 대안과 확정한 첫 적용 범위

| 대안 | 효과와 비용 | 선택 |
| --- | --- | --- |
| 큰 계약을 모든 소비자에서 유지 | 수정은 없지만 캐시 전용 경계도 타입에 드러나지 않음 | 조립부에서는 유지 |
| 기존 캐시 모듈에 작은 계약을 두고 aggregate가 상속 | 새 파일·래퍼·연결 없이 브로커 계약을 2개로 축소 | 첫 적용안 |
| 99개를 모든 도메인 Protocol로 일괄 이동 | 복합 소비자·선택 기능·간접 전달 검증이 한꺼번에 필요 | 현재 범위에서 제외 |

1. `database_query_cache.py`의 기존 `StoredQuery` 옆에 `QueryCacheStore(Protocol)`을 둔다.
   현재 두 signature를 그대로 이동한다:
   `load_query(self, cache_key: str) -> StoredQuery | None`,
   `save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None`.
   `StoredQuery`의 위치·class identity·필드는 유지한다.
2. `database.py`가 이를 import하고 `QueryStore(QueryCacheStore, Protocol)`로 선언한다.
   중복된 두 직접 stub만 없앤다. 상속 포함 전체 계약은 99개다. 기존 `database.StoredQuery`,
   `database.QueryStore`, factory 반환 타입을 유지한다.
3. `rest_broker.py`는 캐시 모듈에서 `QueryCacheStore, StoredQuery`를 import하고 생성자 타입을
   `QueryCacheStore | None`으로 좁힌다. 같은 store 객체를 받으며 adapter/wrapper, 새 store나
   runtime 타입 검사를 만들지 않는다.
4. backend mixin·SQL·TTL·직렬화·계측·connection/transaction 소유권과 브로커의 순위 우선순위,
   저장 queue, 취소·종료, 캐시 유효기간은 변경하지 않는다.

캐시 기능을 읽는 경로는 `rest_broker → database의 계약/재노출 → database_query_cache`
세 파일에서 `rest_broker → database_query_cache` 두 파일로 줄어든다. store 생성까지 이해할
때는 여전히 `database.py`가 필요하다. 실행 중 메서드 호출 깊이는 그대로다. 캐시 모듈은
service/API/UI를 import하지 않고 기존 하위 DB helper 방향을 유지한다. Protocol을 담기 위한
별도 범용 contracts 계층은 만들지 않는다.

## 선행 조건: 상속 계약 감사

`scripts/query_store_source.py`의 `method_sources()`는 QueryStore의 직접 메서드만 수집한다.
그대로 stub을 옮기면 97개로 잘못 집계한다. **기준선을 97개로 다시 작성하면 안 된다.**
첫 적용과 함께 다음 제한적인 처리를 추가한다.

- QueryStore의 정적으로 import된 직접 leaf Protocol 기반 계약을 수집한다. 기존 local
  `database_*` import 해석을 사용하며 앱을 import하지 않는다.
- `Protocol` 기반은 표식으로 처리하고 leaf 메서드를 logical owner `QueryStore`에 병합한다.
  physical file/class는 실제 선언 위치를 보존한다.
- root/leaf 또는 leaf 사이 중복 이름은 signature가 같아도 오류로 거부한다. 동적·해결 불가·
  누락 base와 leaf의 추가 상속도 거부한다. 중첩·순환 상속용 범용 탐색기로 확장하지 않는다.
- 기존 backend mixin의 constructor·override·nested inheritance 거부와 logical owner 해석을
  유지한다. Protocol stub을 backend 구현으로 계산하지 않는다.

## 완료 판정과 다음 작업

### 현재 워크트리 검증 결과

- resolver와 consumer audit 테스트 8건이 통과했다. QueryStore 99개 및 SQLite 103개/
  PostgreSQL 104개 구현(생성자 제외), consumer 338곳, forwarding 46곳의 기존 기준선이 유지됐다.
- SQLite query cache 저장·조회·만료 검사가 통과했다. `StoredQuery`의 root/cache module
  class identity와 aggregate 첫 base `QueryCacheStore`도 확인했다.
- REST broker async 기간전환 테스트는 현재 Windows 실행 환경에서 이벤트 루프가 시작되지 않아
  완료하지 못했다. assertion 실패로 계산하지 않으며, NAS Linux runner에서 해당 회귀를 함께
  실행하도록 후보에 포함했다.
- NAS PostgreSQL cache writer replay/rollback, reader metric, broker 07시·자정 cache
  period, in-flight 요청 병합, SQLite cache round-trip/expiry 검사가 수정 ZIP에서 5/5 통과했다
  (0.669초). 최초 ZIP의 `tests.unit` package marker 누락은 빈
  `tests/unit/__init__.py`를 포함한 수정본에서 해결됐다. 최종 후보
  `kiwoom-db-query-cache-contract-pilot-v1-20261005.zip` SHA-256은
  `C5C9E0BF56FD1D721AA66A7644F9AC050F3F15EB66B00657B1D9F12F4E02107D`다.
  운영 DB·NAS active release·이미지는 변경하지 않았다.
- 후속 API 연결 검증 명령은 v1 ZIP에 포함되지 않은
  `tests.unit.test_central_server_app`을 지정해 NAS에서 import error가 났다. 이를 위해
  Starlette `TestClient`/`httpx2`에 의존하지 않고 ASGI HTTP 요청을 직접 보내는 SQLite API
  연결 smoke를 추가한 v2 후보를 만들었다. 이 경로는 NAS에서 1/1 통과했다(1.082초). 후보
  `kiwoom-db-query-cache-contract-pilot-v2-20261005.zip` SHA-256은
  `31B993866675D2AEBD71300DF88FCF600E7ED43E4FC9BBACFEF7B4659C227D77`이다.

구현은 감사기와 계약 선언을 포함한 한정된 세 소스 파일에서 진행했다. 완료 판정에는 다음을 쓴다.

1. source resolver 테스트에 leaf Protocol 수집·physical owner 및 중복/해결 불가/중첩 base
   거부를 추가한다. 기존 backend resolver와 소비자 감사 테스트도 통과시킨다.
2. QueryStore 99개 전체 signature, backend 103/104개, 소비자 참조 338곳과 전달 관계를 기존
   기준선과 대조한다. 결과를 숨기는 baseline 재작성은 하지 않는다.
3. root/캐시 모듈의 `StoredQuery`가 같은 객체인지, 브로커가 작은 Store 및 `None`을 계속
   받는지 확인한다. 필요한 계약 회귀만 보강하며 runtime capability check는 넣지 않는다.
4. `test_central_rest_broker`의 캐시 hit/miss, 07시 전환, 느린 캐시 조회·저장 중 순위 요청,
   중복 요청, 취소·종료 회귀와 `test_central_server_database`의 캐시 관련 SQLite 검사를 실행한다.
   API의 기존 broker 주입 경로도 대조한다. 실행하지 못한 API/client/화면 검증은 별도로 남기며
   정적 타입 변경만으로 실제 화면 검증 완료를 주장하지 않는다.
5. 후보 소스로 PostgreSQL `test_cache_replay_and_failure_preserve_final_rows`와
   `test_query_cache_reader_keeps_native_context_and_separates_metrics`를 묶어 확인한다.
   운영 DB·active release·이미지 변경은 이 계약 분리의 완료 조건이 아니다.

첫 적용과 후속 소비자 재평가를 마쳤다. 현재 독립 Protocol은 REST cache 한 개뿐이다.
다음 계약 후보는 새 소비 경계가 생길 때 직접 호출·전달·선택 기능과 도메인 경계를 다시
대조해 고른다. 메서드가 적다는 이유만으로 계약을 자동 생성하지 않는다. 동작 최적화, 새 연결 pool,
transaction 통합, QueryStore factory/lifecycle 재설계는 이번 결정에 포함되지 않는다.

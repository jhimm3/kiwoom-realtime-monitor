# 앱 전체 부하 감사와 단계별 변경 기준

작성: 2026-10-06 · 범위: OPEN_ITEMS O12 · 상태: **감사·분류 완료, 성능 구조 변경 보류**

## 1. 이번 결정

목표는 DB 호출 수 자체가 아니라 **기존 기능·데이터 의미·복구를 보존하면서 장초 peak와 불필요한 작업을 줄이는 것**이다.
현재의 5분, 매분 45초, 분 종료 +2초, batch 크기, timer, writer 분리와 동시 실행 수는 정답이나 유지 조건이 아니다.
다만 불완전한 capture를 근거로 새로운 수치나 구조를 결정하지 않는다.

감사 항목을 다음 세 원장으로 분리한다.

1. **A — 코드와 소비자로 확인되는 반복 작업:** 제거·축소 범위를 구체화한다. 기능·복구 회귀를 통과해야 적용한다. 절감량은 측정 전 미확정이다.
2. **B — 성능 후보:** 정상 입력·기준선이 없으므로 batch, cache 범위, writer 통합·분리, concurrency, 시간 배치 결정은 보류한다.
3. **C — 정합성·복구:** 성능 변경과 다른 변경 단위에서 재현·수정한다. 아직 실패 주입이 없는 항목은 결함 해결 완료로 표시하지 않는다.

이번 감사에서 제품 실행 코드를 바꾸거나 NAS를 재시작하지 않았다. 조회·로그·보존 trace 분석만 수행했다.
기존 PC의 trace payload 묶음 fsync/메모리 변경은 별도 미배포 변경으로 보존했다.

## 2. 확인한 실행 상태와 증거 한계

- 2026-10-06 18:39 KST 읽기 전용 확인: NAS `/health`와 capabilities build는 `2026.10.06-recorded-capture-api-v1`.
- active release: `2026.10.06-recorded-capture-api-v1-af7d3d49b61aad09`. 진단 master/capture OFF. 당시 실시간 READY.
- 확인한 주요 파일 11개 중 app, broker, TOP20, collector, candidate, DB facade, query cache, market bars, daily-high/coverage의 10개는 PC/NAS hash가 같았다. `diagnostic_trace.py`만 PC 후속 변경으로 달랐다. 전체 배포 파일을 대조한 것은 아니다.
- PC HEAD는 `d8357c3e17f8fb5ccdf065c3eb5200f658d9e208`; 미커밋 변경이 있다. PC 실행 프로세스의 메모리 코드와 각 창의 활성 상태는 이번에 확인하지 않았다.
- 정적 DB 감사: parse error 0, 직접 PostgreSQL 연결 guard 50/50 승인 목록과 일치. 이 수치는 운영 활성 연결 수나 계측의 완전성을 뜻하지 않는다.
- QueryStore 소비자 감사는 `review_required`: 현재 339 reviewed callsite, 미판정 같은 이름 후보 24, forwarding 46. live minute RAM overlay로 이동한 호출 위치 등 기준선 차이가 있다. 이를 성능 결함이나 연결 손실로 해석하지 않는다.

### 불완전한 장중 자료의 사용 제한

`20261005T235952Z-c138934f2486`은 `incomplete`다. accepted=written 197,333이지만 known dropped 35,251,
입력 거부 97,849가 있어 전체 사건·호출·동시성 기준선이 아니다. 입력 거부와 이벤트 유실은 다른 계수이며 합쳐서 앱 데이터 유실로 부르지 않는다.

이번 분석에서 보존 call start/end가 연결되는 21,436쌍을 확인했다. 시작만 27개, 종료만 16개였다.
**쌍이 맞는다는 것만으로 해당 workload가 재생 가능하거나 선택 구간이 완전하다는 뜻은 아니다.**
숫자는 해당 경로가 실제 실행됐고 어떤 모양의 호출이 남았는지 확인하는 데만 사용한다.
평균·백분위·시간 합계·호출 비중으로 전체 병목 기여도나 최적화 우선순위를 확정하지 않는다.
서로 겹친 호출의 지연 합계는 CPU 사용 시간도 아니다.

보존 표본에는 일봉 715회/427,547행 시도, 프로그램수급 8,336회/17,595행 시도,
초봉 57회/122,752행 시도, shadow 결정 저장 1,499회와 checkpoint 205회가 있다.
시도 행은 변경 행이 아니다. 프로그램수급은 기존에 비동기 commit 설정을 사용하므로 짧은 commit 시간이 WAL 부담이 없다는 증거도 아니다.
이 표본의 순위나 수치로 주기·batch·내구성 설정을 변경하지 않는다.

별도 서버 access/TR 로그의 09:00~10:00 KST에는 후보 API 1,680회, 일봉 TR 804회, 분봉 TR 90회가 기록됐다.
이 역시 중복 payload 수나 전체 DB 호출 수가 아니며 연속조회·다른 요청 목적을 포함한다.
16:00~17:00 로그와는 유입·진단 ON/OFF·외부 부하가 달라 통제된 A/B 비교가 아니다.

이번 감사에서 **검증 완료된 부분 replay 구간을 새로 지정하지 않았다.** 부분 사용은 선택 writer/시간창의 원인 입력,
payload, 시작·종료, checksum, sequence, 초기 상태와 미지원 작업까지 별도 검증한 뒤 그 범위로만 허용한다.

로컬 원시 증거(운영 데이터이므로 일반 Git 문서에 payload를 복제하지 않음):

- `artifacts/whole-app-live-evidence-20261006.json` — API/read-only NAS hash·기존 로그 요약.
- `artifacts/whole-app-retained-trace-summary-20261006.json` — 불완전한 보존 표본 요약, 전체 기준선 아님.
- `artifacts/whole-app-postgres-access-audit-20261006.json`, `artifacts/whole-app-query-store-audit-20261006.json` — 정적 감사.

## 3. 전체 실행 경로와 보호 대상

| 경로 | 실제 소비자·용도 | 현재 처리 및 이번 판단 |
|---|---|---|
| TOP20 순위/TR | 메인 표시, 구독, membership 이력, 후보 universe | broker 우선순위·30초 회차 유지가 현재 동작. 수신·표시와 비긴급 후속 일을 분리해 보호. 동일 구성이라도 다른 시각의 membership을 무조건 삭제하지 않음 |
| 0B 실시간 | 화면 현재/직전 분, 분·초봉, TOP20 거래대금·지수, 후속 분석 | 수신→RAM 집계/전달 유지. DB 저장 지연과 화면 지연을 별도 판정. 저장 policy는 B1 |
| 분봉·일봉 REST | 화면/일지, 데이터 coverage, 신고가, 장후 보완, observation 이력 | 페이지/시장/날짜/출처·완료 증거가 다르면 동일 OHLCV만으로 중복 취급하지 않음. B2/B3 |
| 0w 프로그램수급 | PC 프로그램수급 조회, 저장 snapshot·장후 사용 | 같은 초에도 값이 변함. 마지막 성공 payload 비교/묶음은 B4, 종료는 C1 |
| shadow | 완료봉 결정 감사, 후보 이벤트, 모의운용 입력, checkpoint 복구 | 결정·후보·checkpoint는 역할이 다름. 무소비 화면 polling은 A1; 결정·checkpoint 변경은 B5 |
| 뉴스 | 원문/본문/규칙, 화면, 장후 연구, cursor·job recovery | page commit+cursor+wake 및 job claim 원자성 유지. 반복 카탈로그 A4, polling·claim B7 |
| 외부시장 | 월물/roll·종가·차트 등 시장 컨텍스트 | 현재 표본에서 실행 확인. 전체 창 재조회 B8, 일봉 실패 재시도 C2 |
| 실계좌·모의운용 | 주문/체결/잔고, 승인·lease·fence, 장애 복구 | 중요도·deadline 우선 보호. 서로 다른 cursor·독립 transaction을 편의상 합치지 않음 |
| PC 메인·매매일지·뉴스 | 표시, 로컬 증거, 편집, NAS 동기화, 별도 프로세스 | PostgreSQL뿐 아니라 SQLite, GUI thread, 전체 문서 전송 포함. A2/A3, B9/B10 |
| 연구 작업 | PC campaign/검증, 서버 요청형 평가·export | PC child process의 SQLite/파일 polling과 NAS polling 구분. 실행 시 별도 CPU·RAM·IO 부하로 계측 |
| 계측·로그 | 원인 분석, replay source | payload 복사/직렬화/hash/fsync·probe 연결·access 로그까지 B0/B12에 포함 |

### 공통 보존 계약

- event time, 관측·완료·출처 의미, revision 순서, 동일 operation 재시도, 다른 호출의 독립 commit/rollback을 보존한다.
- 새 값 수신·실제 저장 성공·최신 표시·복구 가능한 상태를 같은 시각으로 취급하지 않는다.
- 완료 표식은 필요한 자료가 commit되기 전에 올리지 않는다. COMMIT 응답 유실도 동일 입력 재시도로 처리한다.
- NAS와 PC의 다른 프로세스가 쓰는 자료는 RAM에만 넣고 무기한 신뢰하지 않는다. 변경 invalidation과 cold-start 경로가 필요하다.
- 원본 이벤트/결정 감사/계좌 안전 자료는 최신값 projection처럼 덮어 합칠 수 없다. 같은 데이터도 보존 목적과 독자가 다르면 중복이라고 단정하지 않는다.

## 4. A — 명백한 반복 작업과 제한된 제거·축소 후보

아래 순서는 **확실성·영향 범위·검증 준비 순서**다. 아직 전체 부하 절감 효과 순위가 아니다.

| ID | 코드로 확인한 반복 | 실제 소비자와 보존할 동작 | 판정·구현 경계 |
|---|---|---|---|
| A1 | 메인 시작 시 숨겨진 후보 창도 2초 polling 시작; close는 hide | 후보 화면·알람. NAS 후보 생성/자동운용은 이 PC 창과 독립 | **PC 소스 수정:** 숨김 AND 알람 OFF면 poll worker를 시작하지 않거나 대기시킨다. 보임 OR 알람 ON이면 유지. 재개 시 누락 구간을 조용히 catch-up하고 이후 신규 후보만 알린다. 기능/lifecycle 검증 완료; 사용량 절감량은 미측정 |
| A2 | 일지 선택 종목 timer가 매초 메인 DB 당일 봉 전체→일지 upsert→전체 재조회→전체 렌더 | 일지 차트/표, provenance, 장후 확정·수동 기록 | **축소:** 새 source revision/행 변경이 없을 때 반복 copy/write/render. 내용·출처·완료가 바뀌면 유지. 변동분·RAM view 도입 방식과 실제 절감량은 B9에서 검증 |
| A3 | 순위 처리마다 고정 별칭 seed와 기존 이름 확인; 동일 종목 필드도 저장·전체 UI rebuild | 종목명·시장·별칭 검색, 현재가·순위, 후속 worker | **축소/통합:** 고정 seed는 초기화 책임에 배치할 후보. 변경 없는 필드 write/render 제거 후보. `updated_at` 소비자·이름 변경·사용자 alias·새 DB 초기화를 확인한 뒤 적용. 순위 수신·후속 신선도는 유지 |
| A4 | 뉴스 검색어별/시장뉴스 source별 같은 카탈로그를 반복 로드 | 기사→종목 연결. 카탈로그는 별도 갱신 경로 존재 | **RAM 재사용:** 동일 카탈로그 revision에 한정한 bounded immutable snapshot 후보. 하루 고정 캐시는 아님. 개별 수집 도중/다른 process 갱신 invalidation을 증명하기 전 구현 보류 |

근거:

- A1: [candidate_monitor_dialog.py](../src/kiwoom_monitor/presentation/candidate_monitor_dialog.py) `CandidatePollWorker.run`, constructor, show/close/shutdown;
  [main_window.py](../src/kiwoom_monitor/presentation/main_window.py) 창 생성·종료;
  [database_shadow_state.py](../src/kiwoom_monitor/central_server/database_shadow_state.py) 후보 MAX(sequence)+range SELECT.
- A2: [journal_process.py](../src/kiwoom_monitor/journal_process.py) `_live_timer` 및 2162행 부근 갱신;
  [journal_bar_repository.py](../src/kiwoom_monitor/infrastructure/persistence/journal_bar_repository.py) upsert와 metadata·일별 조회.
- A3: [ranking_service.py](../src/kiwoom_monitor/application/ranking_service.py),
  [stock_repository.py](../src/kiwoom_monitor/infrastructure/persistence/stock_repository.py),
  [stock_aliases.py](../src/kiwoom_monitor/infrastructure/persistence/stock_aliases.py), main_window 순위 적용.
- A4: [news_sources.py](../src/kiwoom_monitor/central_server/news_sources.py),
  [market_news_sources.py](../src/kiwoom_monitor/central_server/market_news_sources.py) source별 catalog 10,000건 조회.

숨김 polling의 시작 조건은 코드로 확정했다. 관측 API 횟수만으로 실제 앱의 알람 설정이나 인스턴스 수까지 확정한 것은 아니다.
A2~A4도 **반복되는 연산**은 확인했지만 모든 실행이 같은 내용을 처리했다고 확인한 것은 아니다. 제거는 증명된 동일 버전/동일 상태 분기에만 한정한다.

## 5. B — 유효한 기준선까지 결정 보류할 성능 후보

| ID | 대상·현재 형태 | 검토할 선택지 | 결정 전에 필요한 증거·보존 경계 |
|---|---|---|---|
| B0 | capture의 payload 파일별 동기화; PC에는 묶음 fsync/예산 확대 구현 | **batch 조정·RAM 상한 검증**, 승인된 capture ON/OFF 실험 | 기존/후보 동일 입력, producer 지연·RSS·queue age·fsync IO·거부/누락. RAM 상향만으로 해결 선언 금지. 장초 capture 자체 부하부터 확인 |
| B1 | collector 직렬 flush: latest→minute→계좌 종목→기준값→finalize→seconds→시장 이력 | **batch 조정·실행 시점 분산·제한된 concurrency** 비교 | 행/bytes/최대 대기 혼합 정책 vs 현재. 한 작업 실패가 뒤를 막는 시간, late tick·순서·실패 복구·RAM overlay 확인. 독립 lane 생성/통합 모두 측정 전 미결정 |
| B2 | ka10080 페이지마다 하루 범위 metadata, SOR 봉, 비교 문서 반복 조회 | **조회 범위 축소·RAM 재사용** 비교 | 실제 page key/time 범위, 이후 page와 중첩, 다른 writer 변경. ACTUAL 우선권·KRX+NXT comparison·페이지 commit 유지. 하루 캐시 영구화 금지 |
| B3 | 일봉 preparation, 역사 신고가 refinement, REST 영속 cache가 같은 봉 경로를 사용 | **요청/계산 통합·RAM cache·불필요 재수집 제거** 검토 | 요청 목적/시장/기준일/연속조회 key·adjusted data·coverage version 연결. TR 804회만으로 중복 판정 금지. cache hit handler 재실행은 과거 recording gap 복구 기능인지 확인 |
| B4 | 0w 종목별 RAM pending을 0.25초 loop에서 저장, 같은 초 key UPSERT | **동일 성공 payload skip·batch 조정** 비교 | 동일 key여도 값 변경 가능. 저장 성공 cache만 유효. 변경/동일 비율, 늦은 tick, `saved_at` 소비, 최대 지연·종료 C1. 8,336 표본 호출을 근거로 새 주기 확정 금지 |
| B5 | shadow는 usable 완료봉마다 decision commit, 처리 batch마다 전체 checkpoint | **유지 우선**, 입력 불변 재계산 제외/상태 checkpoint 대안 비교 | 동일 결정 ID conflict 수, 신규 결정 수, 상태 변화 bytes. HOLD도 감사 근거일 수 있음. 결정 commit 후 checkpoint 실패 재생 계약 보존. 기존 frame 정규화의 공간·SQL 증가 재검토 |
| B6 | 각 writer/reader가 독립 DB connection; 공유 `to_thread` executor | **concurrency 제한·executor 분리·connection 재사용** 후보 | 접속/배정 대기 vs SQL/lock/commit, 동시 connection, 작업 완료율과 queue drain. 새 pool/session 상태·transaction 소유권은 별도 설계/동시성 회귀 없이 변경 금지 |
| B7 | 뉴스 idle backoff/wake는 이미 있음; claim마다 stale RUNNING 복구 UPDATE | **유지/복구 scan 주기 분리** 비교 | UPDATE 명령 수와 실제 변경행 구분. 재선점 기한·SKIP LOCKED·page commit 후 wake·프로세스 간 enqueue 유지 |
| B8 | 외부시장 기본 300초마다 최근 5일 전체 재수신·시도, 일봉 1일/2년 범위 | **증분+정정 overlap·batch 조정·시점 분산** 비교 | 재요청 중복률, 지연 정정, active/next 월물·roll·이전 종가. 같은 값 SQL guard가 있어도 전송/직렬화/충돌 검사 비용 존재. C2 먼저 분리 |
| B9 | PC GUI 동기 SQLite 다중 조회, 전체 재그리기, cache writer 1초 job queue | **RAM cache·변경분 렌더·queue/batch 조정** 비교 | GUI 지연, SQLite WAL·lock, queue depth/age, 현재/직전 거래대금·late worker·shutdown drain. NAS DB 비용과 PC 비용을 구분 |
| B10 | 일지 매분 전체 pull·전체 local read·전체 upload; 뉴스는 cursor/hash 있으나 push 때 전체 export | **변경분 통합·RAM/영속 sync cache** 검토 | 계좌 identity, tombstone, remote merge, 업로드 성공 ACK·재시작 cursor. 단순 timer 늘리기로 대신하지 않음. 전체 sync를 생략하는 새 계약은 별도 검증 필요 |
| B11 | TOP20 매 순위 성공 시 당일 계좌 편입 목록 재조회; aux task별 실행; 시작 시 여러 서비스 즉시 실행 | **RAM cache·동시 실행 제한·실행 시점 분산** 후보 | 다른 계좌/process 매수 감지, cold-start, pending slot 누락 없이 최신화, catalog/entry 준비 deadline. 현재 inflight dedup이 막는 범위와 그 밖 backlog 구분 |
| B12 | payload 직렬화, DB/HTTP 로그, slow-wait probe가 부하와 같이 증가 | **bounded 계측·중복 계산 제거·batch 로그** 비교 | TOP20 wait probe는 1초 넘긴 호출만 추가 DB 연결, 다른 capture probe는 별도 cap/guard. 계측 ON/OFF CPU·IO·event-loop lag와 장애 증거 보존을 함께 측정 |

관련 소스: [market_ingest](../src/kiwoom_monitor/central_server/market_ingest.py),
[rest_broker](../src/kiwoom_monitor/central_server/rest_broker.py),
[realtime_collector](../src/kiwoom_monitor/central_server/realtime_collector.py),
[database_market_bars](../src/kiwoom_monitor/central_server/database_market_bars.py),
[database_query_cache](../src/kiwoom_monitor/central_server/database_query_cache.py),
[database_datasets](../src/kiwoom_monitor/central_server/database_datasets.py),
[candidate_monitor](../src/kiwoom_monitor/central_server/candidate_monitor.py),
[central_journal_sync](../src/kiwoom_monitor/infrastructure/central_journal_sync.py),
[postgres_access](../src/kiwoom_monitor/central_server/postgres_access.py).

### queue·backpressure에 대한 현재 결정

**기존 queue와 writer를 지금 한 곳으로 합치지 않는다. 값이 정해진 새 제한도 아직 적용하지 않는다.**
보호할 입력/출력 경계는 위 표로 결정했고, 다음 기준선은 각 경계의 queue 길이뿐 아니라 bytes·최고 age·생성/완료율을 포함해야 한다.

- 비긴급 REST/backfill은 포화 시 새 작업 생성 속도를 제한하는 방향을 검토한다. 조회/저장 큐 둘 다 본다. TR 우선순위만으로 이미 실행 중인 긴 저장이 선점되는 것은 아니다.
- 네트워크 실시간 수신 coroutine를 느린 DB 큐 대기로 막아서는 안 된다. 그렇다고 무제한 task/RAM 적재나 원본 사건 조용한 drop을 허용하지도 않는다.
- latest projection은 소비자 계약이 허용하는 같은 key만 합칠 수 있다. 원본/원장/operation delta는 같은 방식으로 합칠 수 없다.
- 영속 backlog가 필요하다면 그 spool 역시 NAS IO를 만들므로 별도 후보로 측정한다. RAM 한도를 늘리는 것만으로 지속 과부하를 해결할 수 없다.
- 여러 lane을 만들면 총 connection/CPU/IO 동시성도 제한해야 한다. 한 개의 전역 semaphore가 안전계좌·TOP20까지 막지 않도록 우선순위와 owner 경계를 함께 검증한다.
- overload 시 데이터 공백/복구 상태를 명시한다. 예산 초과를 누락 없는 성공으로 표시하지 않는다.

보호 순서는 계좌 안전 deadline·실시간 수신/구독·TOP20 수신/표시 → 현재 판단에 필요한 준비/조회 → 장후/연구/복제/진단 등 비긴급 작업이다.
이것은 스케줄러 구현 승인이나 특정 concurrency 수치 결정이 아니라 실험의 회귀 기준이다.

## 6. C — 성능과 분리할 정합성·복구 원장

| ID | 확인한 사실과 아직 미확인인 범위 | 다음 재현·처리 |
|---|---|---|
| C1 | 기존 TOP20 close의 pending 미저장·실제 DB thread 전 종료·ACK 유실 뒤 재시도 누락을 PC fixture로 재현(3건 실패). 단일 owned save와 취소 보호 close, producer stop→save drain→pending final flush를 최소 수정. 관련 74건 통과. 실제 운영 손실량 미확인 | `2026.10.06-top20-program-drain-v1` PC 후보. 전용 PostgreSQL 2건은 DSN 없어 skip; NAS 검사/배포는 미완료. 주기/batch/transaction 변경 없음. 지속 DB 장애·강제 종료 및 미처리 subscriber queue 영속 복구는 보장하지 않음 |
| C2 | 외부시장 일봉 fetch/save 오류를 내부에서 잡은 뒤 정상 반환할 수 있는데 run loop는 daily-date 완료 표식 설정 | intraday 성공+daily 실패 주입 후 다음 poll의 daily retry 확인. 월물별 성공/실패 표식을 분리하는 최소 수정 후보. 성능 batch와 섞지 않음 |
| C3 | LG이노텍의 KRX 일봉 증거는 유효, NXT는 저장 window와 증거 불일치로 신고가 미검증. 후속 봉 쓰기와 완료 표식 invalidation 관계 조사 중 | 실제 변경 전후 key/coverage fingerprint와 writer 연결을 확보. 유효성 검사를 제거하거나 오래된 값을 무조건 정상 표시하지 않음. 원인 writer 확정 전 성능 수정 금지 |
| C4 | shadow `_consume`가 RAM state를 바꾼 뒤 evaluation 저장; 실패하면 cursor 전진 전 같은 observation 재시도 가능 | evaluation 저장 실패 후 동일 입력의 state/decision ID, 재시작 checkpoint 경로를 재현. 결함 여부 미확정. checkpoint 실패 후 이미 commit된 후보 보존 테스트는 계속 유지 |

근거: autonomous_top20 `close`, `_flush_program_snapshots`; external_market_collector `_collect_once`, `_run`;
candidate_monitor `_consume`, `_restore_or_bootstrap`; `artifacts/lg-innotek-high-live-20261006.json`.

## 7. 다음 정상 capture와 동일 workload 비교 절차

1. **P0 — 계측 유효성:** 먼저 기존 B0 후속의 기능/overhead/메모리 검증을 마친다. 배포한 release/hash·설정·DB schema를 기록한다. 이 변경은 제품 최적화와 분리한다.
2. **정상 장중 capture:** 09:00~09:10을 필수로 포함한다. 시작/종료·재시작·거부·drop·censor·checksum·순서·payload 수용 범위를 확인한다. 실패한 자료를 정상으로 승격하지 않는다.
3. **현재 코드 baseline을 먼저 실행:** 고정된 입력 세트 hash, 초기 DB baseline ID, 코드/의존성/설정, DB durable 설정, 선택 workload와 실행 조건을 봉인한다. 구현 후보를 먼저 적용한 결과를 원래 기준선으로 부르지 않는다.
4. **범위별 재현 타당성:** source state가 장중과 동등하지 않다면 `source_state_equivalent=false`를 유지한다. 통제 fixture로 같은 코드 A/B는 비교할 수 있어도 운영 장초 동일 부하 재현이라고 일반화하지 않는다.
5. **전체/단독/하나 제외/조합:** 같은 workload와 초기 상태를 복구하며 실행한다. 제외 실험은 원인 후보 좁히기이며, 제외한 기능이 없어도 된다는 승인은 아니다. 각 run 후 baseline restore와 잔여 queue/connection 없음을 확인한다.
6. **한 변경씩 A/B 또는 A/B/A:** 순서를 교차하고 준비/워밍 상태를 맞춘다. 동일 입력에서도 다른 NAS 작업이 변할 수 있으므로 host IO·CPU·PG waits와 background 상태를 같이 기록한다. 성능 결론은 반복 결과와 변동폭을 포함한다.
7. **회귀 + 전체 peak:** canonical 값·metadata·revision chain·결정/후보·cursor·coverage·계좌 safety·UI 현재/직전 분·cold start·shutdown·ACK 유실을 비교한다. 저장 주기 변경은 허용된 내구 지연만 다를 수 있으며 그것도 명시한다.
8. **승인된 후보만 단계 배포:** immutable 이전 release와 설정 rollback을 유지한다. schema 변경은 별도 migration/복구 검사. 새 source stage·Git push·NAS active·실제 운영 검증을 구분한다.

### 재생 입력의 현재 공백

기존 upstream capture는 0B collector 사건과 native store 입력을 지원하지만, 이 사실만으로 **TOP20의 0w 처리·REST 응답 orchestration·PC timer/창 가시성/일지 sync**까지 원인부터 재생된다고 볼 수 없다.
그런 경로의 변경을 비교하려면 해당 source owner의 입력/설정/시간을 통제하는 adapter 또는 유효한 별도 fixture가 필요하다.
고정된 과거 DB 호출만 재생하면 조회 생성 횟수·수집 batch·새 RAM 재사용 정책의 효과를 시험할 수 없다.
upstream에서 재생한 component가 만들어내는 DB 호출은 같은 component의 과거 sink trace와 중복 실행하지 않는다. 다른 producer의 같은 DB method는 별개다.

### 비교할 지표

- 1초/5초/10초 rolling peak: 시작/완료 호출 수, 실제 변경 행·WAL·동시 transaction, queue bytes/age와 drain 시간.
- event→RAM 전달→화면/판단 지연; TR queue wait·반응시간·재시도; `to_thread` 제출→실행 대기·event-loop lag.
- connection/SQL/commit 시간, blocking owner·wait event. 전체 WAL delta와 해당 replay connection 관측치를 구분하며 개별 writer 귀속을 만들지 않는다.
- NAS/PC CPU·RSS·디스크 bytes/latency·SQLite WAL·직렬화/hash/로그·capture 비용. DB 감소만으로 CPU/RAM/queue 증가를 가리지 않는다.
- 입력 수·논리 결과 수·중복/실패/재시도·중단 복구 후 상태. 단순 rows_attempted/COMMIT 횟수를 변경행/WAL flush 횟수와 같다고 취급하지 않는다.

## 8. 실행 우선순위와 종료 조건

| 순서 | 지금 할 일 | 하지 않는 일 |
|---|---|---|
| 완료 | **C1 종료/저장 경계** 수정·회귀, NAS 후보 2건 및 배포 확인 | 실제 장중 입력 보존/성능 효과는 별도 검증 |
| 완료 | **P8 뉴스 child close timer**: close 시 중단, 재열기 시 즉시 재준비; PC lifecycle 회귀 | P9 중앙 sync는 계속 유지, 외부 호출·부하 절감량은 미측정 |
| 다음 | **B0 capture 품질/overhead 검증 → 정상 장초 capture → 현재 코드 baseline 고정** | 이번 불완전 기록으로 구조 튜닝 수치 확정 안 함 |
| 후속 | baseline에서 **앱 전체 peak 기여가 큰 B 후보 순으로 한 번에 하나** 실험. B1/B3/B4/B5/B9/B10을 빠짐없이 평가 | 현재 call 수·개별 max latency만으로 고정 순위를 정하지 않음 |
| 별도 | C2 재시도, C3 coverage, C4 평가 실패를 독립 기능 결함 작업으로 추적 | 최적화 성공과 정합성 해결을 섞어 완료 보고하지 않음 |

현재 최종 판정은 **실시간·안전·감사/복구 의미 유지**, **A의 무소비·동일 버전 작업만 제거/축소 후보 확정**,
**B의 통합/cache/batch/분산/concurrency 선택은 정상 기준선까지 보류**, **C는 별도 재현**이다.
아직 새 저장 간격·batch 상한·동시성 수치, 전체 앱 성능 개선율, 운영 병목 해결을 확정한 항목은 없다.

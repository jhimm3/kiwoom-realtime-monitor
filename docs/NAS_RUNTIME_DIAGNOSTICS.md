# NAS 작업별 병목 진단

2026-10-05 후속 목표는 **같은 장중 사건 기록과 DB 초기 자료를 고정하여 전체·단독·
하나씩 제외·코드 수정 전후를 비교**하는 것이다. [다중 workload 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)이
후속 구현 순서의 정본이다. 아래 0B fixture는 기반 검사이며 목표 전체의 구현 완료가 아니다.
새 계약은 여러 작업의 실제 store 입력과 collector 입력을 함께 기록하고, collector가
생성하는 DB 호출과 기록된 하위 호출을 중복 재생하지 않는 선택 경계를 정의한다.

2026-10-05 로컬 진행: bounded schema-2 store/collector input capture 및 plan compiler 구현,
관련 로컬 검사 191건 통과. compiler는 실제 작업을 실행하지 않으며 `execution_ready=false`다.
새 capture 전용 PostgreSQL 검사, 장기 prefix/window reader, 동일 replay DB baseline,
time-preserved collector/background runner, API·NAS 연결과 운영 overhead 검증이 남았다.

첫 전용 PostgreSQL 실행은 capture gate 4건 통과, schema-1 trace 읽기 오류 1건,
collector 실패주입 검사 오류 1건이었다. 두 검사를 수정한 후보
`2026.10.05-recorded-capture-gate-v2-81fc0b5e7f04dbcf`를 stage했다.
stage 자체 검증은 파일 checksum/import 통과이며 운영 active pointer는 기존 v2 그대로다.
새 후보의 6건 묶음 검증에는 NAS 전용 테스트 DB가 필요하다.

```sh
sudo sh /volume1/docker/kiwoom-monitor/deploy/synology/source-runtime.sh test 2026.10.05-recorded-capture-gate-v2-81fc0b5e7f04dbcf \
  --test tests.integration.test_recorded_workload_capture_postgres.RecordedWorkloadCapturePostgresTests \
  --test tests.integration.test_diagnostic_collector_replay_postgres.DiagnosticCollectorReplayPostgresTests
```

PC capture 회귀 9건은 통과했고 새 PG 클래스는 URL 미설정으로 skip됐다. 따라서 위 묶음의
실제 PostgreSQL 결과를 받기 전까지 capture 원자성·동시성 gate는 미확인으로 둔다.

2026-10-05 v2 NAS gate에서 collector의 rollback 주입이 여전히 실행되지 않는 것을 확인했다.
같은 분 시각의 서로 다른 종목 observation이 metadata lookup에서 충돌했다. SQLite/PostgreSQL
분봉 writer가 subject와 분 시각을 함께 사용하도록 수정하고 양 backend 회귀를 추가했다.
로컬 SQLite 검사·문법 검증은 통과했으며 v3 후보에서는 다음을 함께 확인한다.

```sh
sudo sh /volume1/docker/kiwoom-monitor/deploy/synology/source-runtime.sh test 2026.10.05-recorded-capture-gate-v3-ed9daa478d48ac35 \
  --test tests.integration.test_recorded_workload_capture_postgres.RecordedWorkloadCapturePostgresTests \
  --test tests.integration.test_diagnostic_collector_replay_postgres.DiagnosticCollectorReplayPostgresTests \
  --test tests.integration.test_storage_boundary_postgres.PostgresStorageBoundaryTests.test_realtime_minute_metadata_is_scoped_by_subject_for_same_minute
```

후보 `2026.10.05-recorded-capture-gate-v3-ed9daa478d48ac35`에서 위 묶음 7/7이 통과했다
(9.109초). collector의 statement rollback·lost commit ACK 재시도와 같은 분의 다종목 metadata
격리를 전용 PostgreSQL에서 확인했다. 이 후보는 active로 선택하거나 운영 서버에 배포하지 않았다.

<a id="collector-replay-design"></a>
## 2026-10-05 collector fixture 기반 및 다중 입력 capture — 로컬 진행

`diagnostic_collector_replay.py`의 고정 `collector-0b/v1` fixture와 collector의
네트워크 없는 진입점·인스턴스별 저장 시계·기존 저장 loop를 연결했다.
6분간 세 시장 0B 메시지 362개를 넣는 정확성 검사에서 분봉 21개·초봉 1,080개,
정상 주기의 분봉/확정 각 6회·초봉 6회·latest 2회와 별도 종료 flush 3회가 확인됐다.
단위검사는 인스턴스별 시계를 제어하므로 NAS 성능 측정 결과가 아니다.
입력 파서의 계좌 자료 거부, 열린 분/직전 분 RAM 조회, QUERY 완료 봉 우선권,
구독 기준/gap, 저장 중 취소 후 실제 DB 작업 대기도 검증했다.
관련 확대 회귀 130건 중 127건 통과, 2건은 PC의 전용 PostgreSQL URL 미설정으로 skip,
1건은 기존 trace route 5개 누락 API 계약 fixture 실패다. PostgreSQL 원자 rollback·
COMMIT ACK loss·peer 보존·전체 scope cleanup 검사 2건은 준비했으나 아직 실행하지 않았다.
다중 workload 입력 capture/compiler 핵심은 아래 최신 상태에 추가 구현됐다. 남은 후속은
capture 전용 PostgreSQL 검사, API workload 연결, 실제 replay runner, NAS 배포·실제 1배속 측정이다.

### 목적과 확인된 경계

운영 v2 `2026.10.05-realtime-minute-live-view-v2-ea54fd68a086689b`의 NAS 검사
83건은 통과했다. 후속 DB writer replay `20261005T100452Z-80ed9651`도 245건,
오류 0건으로 완료됐다. 다만 기존 `diagnostic_replay.run_replay()`는 저장 함수를
직접 호출하므로 collector의 저장 주기 변경 효과를 검증하지 않는다.
해당 run의 일정 지연 최대값도 약 394ms로 `timing_preserved=false`였다.

첫 구현은 **0B 입력 → 실제 파서 → 실제 RAM 집계 → 실제 저장 루프 → 전용 PostgreSQL**을
검증한다. 뉴스, TOP20 REST, ka10080 보완, VI, 계좌 이벤트, 외부시장 수집과 GUI는
이 범위에 포함되지 않는다. 시장 전체 또는 당시 NAS 전체 부하의 동일 재현이라고
표시하지 않는다. 기존 DB-call replay는 writer 단독 비교 도구로 그대로 유지한다.

### 실제 코드 재사용과 실행 수명

- 새 `diagnostic_collector_replay.py`는 입력 재생, 실행 수명, 결과 검증을 소유한다.
  집계와 저장 알고리즘은 이 모듈로 복사하지 않는다. 별도 범용 Manager는 만들지 않는다.
- `CentralRealtimeCollector`에 외부 네트워크 없이 입력을 받아 저장 task만 소유하는
  좁은 진단 진입점을 추가한다. 운영 `start()`의 네트워크 task 시작과 분리하되,
  `_publish_parsed`, `_save_snapshots`, `_flush_snapshots`, `close()`를 그대로 사용한다.
  전달만 하는 대체 파서·가짜 flush 루프는 만들지 않는다.
- 진단 인스턴스는 독립 `RealtimeHub`와 전용 store를 가진다. 토큰 공급자는 호출되면
  실패하는 함수이며 account callback, account resolver, market-events service는 없다.
  운영 collector의 RAM·구독·pause·자격증명에는 접근하지 않는다.
- 구독 승인 집합·연속 수신 시작·gap 적용은 현재 collector의 상태 전이를 작은 내부
  메서드로 모아 운영과 재생이 공유한다. 재생 코드가 private dict를 임의 조작하지 않는다.
  전체 WebSocket 연결/LOGIN/REG 통신 재현은 첫 버전 범위 밖이다.
- 입력 종료 → feeder 종료 확인 → collector `close()` → shield된 저장 task 완료 확인
  → 데이터 검증 → 진단 소유 행 정리 순서를 지킨다. 취소·master 만료도 같은 경로를 탄다.
  DB thread가 살아 있는데 task만 취소하고 행을 지우거나 run lock을 풀지 않는다.
  drain이 오래 걸리면 `finalizing`과 미완료 사유를 노출하고 실제 작업의 소유권을 유지한다.
- `DiagnosticRunService`의 기존 run lock, master session/CAS, 휴장일/시간 제한,
  보고서 보존·cleanup을 재사용한다. 전용 DB 이름은 연결 전과 후 모두 확인한다.
  writer마다 기존 연결·transaction·operation ID·query authority 경계를 유지한다.

### 시계와 저장 시점

collector의 기존 `now_provider`를 시장 시각의 단일 기준으로 사용한다. 현재
`received_at`, 최신값 만료, latest/second deadline에 직접 쓰는 `time.time()`도
이 공급자의 timestamp로 맞춘다. 운영 기본 공급자는 기존 실제 시각이며,
API 프로세스의 전역 `time` 또는 `asyncio.sleep`을 monkeypatch하지 않는다.

성능 재현은 원본 시작 시각 + 실제 monotonic 경과를 쓰는 **1배속**으로 제한한다.
1초 관리 sleep과 DB 실행은 실제 시간으로 진행한다. 입력 예정 시각과 실제 투입 시각을
모두 남기며 밀린 입력을 삭제하거나 미래 입력을 앞당겨 timing 성공으로 만들지 않는다.
원본의 분·5분 위상(:02/:45/5분 경계+:10)을 유지한다. sleep 이후 deadline 검사도
실제 collector 코드가 담당하므로 DB 지연에 따른 저장 지연을 숨기지 않는다.

단위검사에서만 인스턴스별 sleep/clock을 제어해 빠르게 경계를 검증한다.
이 결과는 `correctness_only`이며 NAS 성능 결과와 합치지 않는다.
네트워크 대기용 monotonic과 성능 측정용 perf counter는 실제 시간을 유지한다.

### 입력 원본과 초기 상태

1차는 고정된 버전의 대표 fixture를 사용한다(`collector_fixture`). 최소 6분 길이로
분봉·초봉·latest 경계를 모두 지나며 KRX/NXT/SOR, 누적값 초기 기준, 지연·중복 체결,
구독 변경·gap과 종료 시 미저장 자료를 포함한다. 입력은 실제 `REAL/0B` 형태이고
예상 OHLC·거래량·거래대금·완료 여부를 별도로 검산한다. 실패 주입은 검증용 fixture로
분리하며 정상 성능 fixture에 섞지 않는다. 과거 trace를 임의 체결로 변환해
`recorded`라고 부르지 않는다.

2차 `collector_recorded`는 새로 기록한 0B 입력만 사용한다. 기존 DB trace의 행 수,
call ID, 변경량으로 원래 틱 순서·가격·누적량을 복원할 수 없으므로 해당 입력으로는 거부한다.
중간 구간을 고를 때는 기록 시작부터 선택 구간까지의 prefix를 같은 collector에 먼저
재생한다. prefix를 분석 구간에서 제외하고 별도 비용으로 보고한다. 첫 5분 미만의
warm-up이나 구독 기준이 없는 종목은 초기 상태 미확인으로 표시한다.

첫 버전은 운영 RAM 전체나 운영 DB snapshot을 복제하지 않는다. 따라서 충분한 prefix가
있어도 capture 이전의 RAM·기존 DB·query authority·스케줄러 위상까지 동일하다는 보장은 없다.
`initial_state=cold_with_prefix`, `database_state=isolated_fixture`로 명시하고 원본과의
상태 동일성은 미확인으로 둔다. QUERY/COMPLETE 선점 행은 별도 fixture에서 검증한다.
재시작으로 나뉜 capture를 연속 무손실 입력으로 이어 붙이지 않는다.

### 경량 0B capture 계약

기존 trace에 선택적인 `collector_input` 스트림을 추가한다. 기본값은 OFF이며 기존
DB-call trace 계약은 유지한다. `trace_id/producer_id/input_seq`, 수신 wall/monotonic,
원래 메시지의 row 순서, 실제 source release와 parser schema를 남긴다.
0B 필드는 `item/stk_cd/code`와 FID `10,12,13,14,15,17,20,228,290,311`만 복사한다.
문자열 부호·빈 값·필드 부재·시장 suffix는 그대로 보존한다. 구독 승인과 gap,
capture 시작 시 승인 source/연속 수신 기준도 같은 입력 순서에 넣는다.
같은 메시지 안의 제외된 종류는 개수로 공개한다. LOGIN·토큰·00/04 계좌 자료를
수집하거나 fixture에 넣지 않는다. 지원하지 않는 schema는 추정 복원하지 않고 거부한다.

수신 경로는 허용 필드의 bounded copy와 enqueue만 한다. JSON 인코딩·압축·파일 I/O·fsync는
백그라운드에서 묶어서 한다. enqueue 실패와 한도를 넘는 입력은 순번/사유를 남기고
자료 불완전으로 처리하며 실제 수신·집계를 막지 않는다. mutable 원본 dict를 queue에
그대로 참조시키지 않는다. DB-call과 입력 queue 예산을 분리해 입력 폭주가 DB 계측을
조용히 밀어내지 않도록 한다. 입력 queue는 32,768개 및 보수적 메모리 비용 64MiB 중
먼저 도달하는 한도로 시작하고, 문자열·행 수 제한을 적용해 단일 이벤트 크기도 제한한다.
큰 메시지는 순서와 원본 message ID를 보존한 bounded row group으로 나눈다.

현재 writer는 5초마다 최대 4,096개/1MiB chunk 하나만 내보내므로 이를 그대로 틱에
적용하지 않는다. 입력 스트림은 5초 또는 buffer high-water에 worker를 깨우고,
worker만 bounded chunk들을 연속 처리하도록 한다. chunk별 batched fsync와 checksum,
manifest의 committed high-water를 유지한다. 기존 저장 quota·부분 파일·재시작의
unknown-tail-loss 의미도 보존한다. 디스크가 못 따라가면 명시적으로 incomplete이지
무제한 RAM 증가나 수신 thread의 동기식 쓰기로 해결하지 않는다.

65분 capture 허용은 구현만으로 보장하지 않는다. 실제 관측 peak와 그 2배 입력의
65분 검증에서 seq/drop, queue bytes/RSS, worker CPU·flush/fsync, 수신 지연을 확인한다.
동일 입력의 capture OFF/ON 반복 비교 결과가 나오기 전에는 개장 capture overhead가
작다고 확정하지 않는다. 누락·quota 초과 trace로 원본 부하 동일성 판정은 하지 않는다.

### 격리·보고서·비교

재생 종목은 run별 진단 키로 매핑하고 시장·동일 키 관계를 보존한다. 원본 날짜/시각을
유지하더라도 전용 DB와 진단 키로 격리한다. 선점 실패나 취소도 해당 run의 봉·metadata·
revision·operation marker·latest 행만 지우며 peer sentinel로 보존을 검사한다.
매핑이 실제 파서의 코드 해석을 바꾸지 않는지 fixture 검사에 포함한다.

보고서에는 입력 수/누락/수신 지연, warm-up·측정·drain 단계, flush ID별 writer 호출,
SQL/COMMIT 시간·실제 변경 행·revision, RAM pending 최대값, 최종 자료 검증과
cleanup 결과를 분리한다. 입력→flush는 여러 틱이 한 저장으로 합쳐지는 관계이며
1:1 call ID라고 표시하지 않는다. 가능한 key별 input seq 범위와 flush ID를 연결하고
범위는 정확한 seq 집합이 아니라는 점을 명시한다. writer의 DB call ID 연결은 유지한다.

`input_timing_preserved`는 누락 없이 1배속 투입되고 최대 일정 지연 100ms 이내일 때만
true다. 이것도 당시 전체 시스템/DB 상태의 동일성 증거는 아니다. 저장 경계의 지연은
별도 `flush_lateness_ms`로 기록한다. 종료 flush는 정상 주기 COMMIT 수에 섞지 않는다.
DB transaction/WAL delta와 NAS 장치 통계는 **측정 구간 전체**로 명명하며 개별 replay
writer에 귀속하지 않는다. fixture 준비/정리 WAL도 성능 구간 밖으로 분리한다.

정확한 변경 전후 비교에는 동일 입력·초기 fixture·배속을 처리하는 실제 두 코드 버전이
필요하다. 옛 1초 flush를 흉내 낸 다른 루프를 기준으로 만들지 않는다. 기준 버전에
같은 진단 진입점이 없다면 우선 현재 버전의 정확성·실제 호출 수만 보고하고, 감소율은
비교 가능 버전 준비 전까지 미확인으로 둔다. 단순 코드상 예상 감소율을 실측으로 쓰지 않는다.

### 구현 순서와 완료 조건

다음 번호는 0B 단독 경로의 기존 단계 구분이다. 후속 개발 우선순위는 위 다중 workload
계약 8절을 따른다. 아래 고정 fixture·네트워크 없는 collector 검사는 이미 구현된 기반이며,
다중 workload 원인 입력 capture 및 replay 완성을 뜻하지 않는다.

1. **완료된 기반:** collector의 인스턴스 시계·네트워크 없는 수명 진입점,
   `collector_fixture` runner와 고정 fixture 회귀. 실제 parser와 저장 loop, current/previous-minute
   RAM 표시, :02/:45/5분+:10 저장과 종료 flush, 중복/지연/시장 분리/query authority를 검사한다.
2. **완료된 로컬 capture 핵심:** 다중 workload store 입력과 0B·승인·gap 원인 사건의 bounded capture,
   actor/component 연결 및 선택 plan compiler. 아직 runner/API에 연결되지 않아 실제 replay를 실행하지 않는다.
3. **다음 단계:** 새 capture의 전용 PostgreSQL 원자성·정리 검사를 추가하고, prefix/warm-state를
   포함한 장기 trace window reader와 동일 replay DB baseline 복구를 구현한다. 그 뒤 원인 입력과
   동시 background operation을 같은 시계로 실행하는 runner, 코드 A/B, plan/run API·보고서를 연결한다.
   선택 workload의 descendant DB call을 두 번 적용하지 않고 peer producer의 동일 메서드 호출은
   보존하는지 검증한다. capture 입력/순번/queue 포화/파일 한도/중단 복구와 65분 overhead gate를
   통과한 후보만 장중 capture에 사용한다.

위 기반 fixture와 다중 workload capture/compiler 핵심은 로컬 구현했지만 실행 경로는 미완성이다.
이 변경을 NAS 운영 릴리즈에 적용하지 않았다.

2026-09-29 로컬 v4 후보에서는 인증 API로 master/capture 제어, 고정 PostgreSQL·host snapshot, 단일 진단 run 시작·상태·취소, 기존/new JSON 보고서 및 history 조회를 연결했다. API는 같은 `diagnostic-workloads.json`과 기존 CLI 수집·report 로직을 공유한다. 로컬 구현·검사 단계이며 실제 NAS 서버는 아래 v2 설명의 build가 실행 중이다. [API 범위와 제한](NAS_DIAGNOSTIC_API_DESIGN.md) 및 [OPEN_ITEMS](OPEN_ITEMS.md)의 배포 잔여를 확인한다.

NAS build `2026.09.28-db-observability-v2`에서 인증된 `GET /api/v1/diagnostics/news-job-claim-plan`으로 stale news-job 복구 UPDATE와 BODY/RULE 선점 SELECT의 PostgreSQL 실행계획을 조회할 수 있다. 이 경로는 임의 SQL을 받지 않고 `EXPLAIN (FORMAT JSON)`만 실행하며 `ANALYZE`는 사용하지 않는다. 응답은 database/version과 제한된 plan node 정보를 반환한다. 2026-09-29 확인한 PostgreSQL 17.11 계획 및 추정치의 한계는 [OPEN_ITEMS](OPEN_ITEMS.md)에 기록했다.

2026-09-27 로컬 공통 DB 관측 pilot: 진단 master와 capture가 ON인 동안 인증된 `GET /api/v1/diagnostics/db-calls?start=<epoch>&end=<epoch>&mode=summary|verbose|raw`가 `save_query` 한 writer의 call ID·backend PID·execute/COMMIT/close 시간과 오류 상태를 반환한다. `limit` 기본 200(최대 500), `slow_ms` 기본 500, 구간 최대 30분이다. raw에는 SQL/parameter/DSN을 넣지 않는다. 종료된 commit의 과거 wait event를 이 API로 복원하지 않는다. `UNREGISTERED`는 관측 연결을 사용한 무등록 호출만 탐지하며 미이관 경로 수는 포함하지 않는다. 이 로컬 build는 아직 NAS에 적용되지 않았다.

현재 확인된 NAS marker는 `2026.09.26-minute-revision-batch-insert-v1`이다. 이전 paired 측정 `20260926T072545Z-588bd4f0`은 revision INSERT 행 584건이 들어간 한 호출에서 execute 4,660ms / revision 전체 4,987ms / COMMIT 32ms를 보였다. batch build 후 측정 `20260926T082101Z-70672d4f`에서는 60초 동안 분봉 저장 25회·22,500행을 관측했으나 revision INSERT는 0건이었다. 무변경 분봉 경로의 revision p50/p95/max 217/569/1,179ms, total 834/2,534/2,684ms, COMMIT 378/2,144/2,294ms였다. 이는 batch INSERT가 수행된 사례가 아니므로 이전 584건 수정 사례와 성능 비교가 불가능하다. 같은 구간 WAL 19.57MB, dm-4 busy 65.32%·write await 197.17ms·queue 79.18, news claim 115회·active query 63표본, 외부시장 수집 5,068행이 관측됐다. WAL timing은 꺼져 있었고 전체 WAL·장치 수치는 writer 단독 귀속값이 아니다. 측정 뒤 master와 capture OFF, pause 없음이 확인됐다.

2026-09-26 전용 DB 합성 비교 `benchmark_minute_revision_batch_postgres.py`는
`kiwoom_monitor_diagnostic_test`에서 900개 키 중 584개 revision 변경을 행별/묶음
경로로 각 3회 측정했다. 행별은 lookup 900회와 INSERT 584회, 묶음은 lookup 1회와
multi-row INSERT 1회였다(묶음은 별도 sequence allocation SELECT도 수행). COMMIT 전
revision 처리 중앙값은 399.048ms에서 191.358ms로 52% 감소했다. COMMIT 포함 중앙값은
2,042.357ms에서 812.893ms였으나 commit 편차가 행별 428~2,420ms, 묶음 161~1,219ms로
커서 전체 감소분을 묶음 처리의 효과로 단정하지 않는다. 각 3회인 합성 테스트이며
실제 운영의 지연 원인이나 개선 폭을 입증하지 않는다. 스크립트는 격리된 revision
행을 `finally`에서 지웠고 운영 테이블은 건드리지 않았다.

2026-09-26 로컬 후속 구현은 최상단 진단 ON의 고유 세션 ID와 revision을 보존한다.
같은 세션에서 `tool on`을 반복하면 자식 상태와 TTL이 유지되고, OFF→ON은 새
세션이다. 자식 cleanup은 자신의 세션에만 작동한다. 측정 전에 전체 구간의
TTL을 확인하며, 측정 중 master OFF·만료·세션 교체가 생기면 다음 표본 전에
중단해 부분 결과를 `aborted`로 저장한다. 보고서 읽기는 OFF에서도 가능하다.
현재 `effective`는 요청 상태이며 실제 작업 중지 ACK는 아직 없다.
로컬 build marker는 `2026.09.26-external-market-diagnostics-v1`이다. 앞선
운영 확인에서 NAS `/health.server_build`도 v1로 응답했다. 그 배포 뒤 로컬에
추가된 DB probe 오류 처리와 회귀 검사는 아직 NAS 소스에 동기화·재빌드되지
않았으므로, 현재 NAS v1은 그 후속 변경을 포함하지 않는다.

2026-09-26 후속 설계는 [진단 스위치 설계 검토](DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md)를
따른다. 보호 수신·주문·writer 제어와 durable 인계는 설계 상태이며 아직 미구현이다.
로컬 v2는 하위 제어 파일 갱신 시 master가 삭제되는 결함을 수정했다. master 만료 중
측정 취소, 실제 중지 ACK, 세대별 cleanup은 후속 구현 전이므로 아래 현재 도구의
측정 결과만으로 완전한 중지·복구를 보장하지 않는다.

이 도구는 서버 컨테이너를 한 번 갱신한 뒤, 재빌드 없이 **서버가 소유한 선택적 배경 작업**을 짧게 중지·복구하며 저장 지연을 비교한다. 운영 설정이나 PostgreSQL의 내구성 설정은 바꾸지 않는다. 실시간 순위·0B·계좌·주문·체결 기록은 제어 대상이 아니다. 별도 프로세스로 실행한 과거자료 적재기도 제어하지 않으며, 측정 결과에 감지된 실행기 이름만 남긴다.

측정용 메모리 수집은 평소 **꺼져 있다**. 켰을 때만 분봉·일봉 저장 단계와 writer 호출 표본을 모으고, 끄거나 만료되면 메모리 표본을 비운다. PostgreSQL의 저장 동작이나 운영 기능에는 영향을 주지 않는다.

NAS SSH에서 컨테이너 안의 도구를 실행한다. 예:

```sh
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py status
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py tool on --ttl 10m
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py capture on --ttl 10m
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py capture status
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py pause minute_backfill --ttl 10m
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py resume minute_backfill
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py test news_jobs --duration 1m
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py capture off
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py tool off
sudo docker exec kiwoom-monitor-server-1 python /app/scripts/nas_workload_diagnostic.py reset
```

계층은 **진단도구 master 스위치 → 개별 자식 스위치**다. master는 기본 OFF이며, 먼저 `tool on --ttl 10m`으로 켜야 `capture`, `pause/resume`, `measure`, `test`가 동작한다. master TTL은 기본 10분, 최대 60분이다. 하위 capture·작업 lease는 master 만료를 넘지 않는다. `tool off` 또는 master TTL 만료는 하위 pause와 capture를 모두 비활성화하고, `tool on`을 새로 켜면 이전 자식 상태를 초기화한다. 컨테이너가 재시작하면 master와 모든 자식 상태가 꺼진다. 

master가 켜진 상태에서 `capture on`은 기본 10분, 최대 60분 동안 메트릭 수집을 켠다. `capture off`는 즉시 끄고 메모리 버퍼를 비운다. `measure`와 `test`는 master가 켜져 있을 때만 실행하고, 수집이 꺼져 있으면 필요한 시간만 임시로 켠 뒤 끈다. `status`와 진단 API에서 master 및 각 자식의 상태·만료 시각을 확인한다.

writer 경로 확인은 인증된 `GET /api/v1/diagnostics/writers`에서 가능하다. 이 목록은 현재 계측 중인 writer만 보여 주며 전수 목록이 아니다. A/B/A 측정 JSON의 `writer_registry`와 `market_bar_saves.writer_transactions`에는 호출·transaction·commit 수·시도 행수, 전체 elapsed 및 실제 계측된 connect/execute/commit 단계의 min/median/p90/p95/p99/max가 포함된다. `market_bar_saves.kinds.minute`의 `revision_lookup_statements`와 `revision_insert_statements`는 revision helper 내부 쿼리 수만 뜻한다. 현재 `ka10080` canonical 봉과 metadata는 PostgreSQL에서 최대 1,000행, SQLite에서 최대 80행의 multi-row UPSERT SQL로 실행되며 각 batch SQL은 개별 execute 구간으로 대기 진단에 잡힌다. 그러므로 이전 build의 행별 `executemany` 동작/비용과 현재 batch 결과를 직접 같은 문장 표본으로 비교하면 안 된다. `revision_rows_ms`는 hash 비교·insert 준비·DB 호출을 포함하고 `revision_insert_execute_ms`는 실제 revision INSERT execute 시간의 부분집합이므로 둘을 합산하지 않는다. 측정되지 않은 단계는 비어 있고 `commit_latency_samples`가 실제 COMMIT 시간 표본 수를 나타낸다. 오류·재시도·payload bytes·실제 반영 행수는 아직 계측하지 않는다. WAL delta와 wait sample은 측정 구간 전체 값이라 writer별 귀속값이 아니다. 저장 경로와 미완료 감사 범위는 [키움 저장 경로 감사](KIWOOM_STORAGE_WRITE_AUDIT.md)에 기록한다.

2026-09-26 NAS 단일 60초 측정 `20260926T064325Z-6bf6dd08`에서는 7개 `ka10080` 저장·6,300행에 revision latest lookup 7회, revision INSERT 1,090회가 기록됐다. revision 전체 phase는 p50/max 289/7,783ms였으며, 같은 시각의 `revisions_ms=34,895` 경고는 capture window 전에 끝나 상세 표본에 포함되지 않았다. 따라서 이 결과는 batch lookup 호출 수는 확인하지만 그 긴 사건의 내부 원인을 가르지는 못한다. fine-grained phase 수치는 새 local build가 NAS에서 실행된 뒤 다시 측정한다.

두 번째 표본 `20260926T065335Z-cf197ea9`는 60초 동안 `ka10080` 저장 23회·20,422행, revision lookup 23회·20,422키, revision INSERT 12회였다. revision 전체 p50/max 218/381ms, 전체 저장 p50/p95/max 662/2,093/2,414ms, commit p50/p95/max 166/1,715/2,030ms다. 0B 성공 저장은 0회였으며 news claim 117회와 외부시장 collector 활동이 동시 관측됐다. dm-4 busy 48.53%·average queue 48.06은 첫 표본보다 낮지만 통제된 A/B는 아니므로 최초 34.9초 사례의 원인이 제거됐다고 결론내리지 않는다. 이 JSON에 분리 phase 필드가 없는 점도 NAS 새 build 미반영과 일치한다.

세 번째 표본 `20260926T070655Z-ea8d03ea`는 새 build에서 60초 `measure`를 실행했다. 분봉 저장 22회·19,800행, revision lookup 22회·19,800키, revision INSERT 305회였다. revision source 구성 p50/p95/max 189.5/220/231ms, lock 1/1/10ms, batch lookup 35/42/46ms, row 처리 1/1/2,120ms, INSERT execute 0/0/2,099ms; 전체 revision 233/268/2,358ms, 저장 645/2,891/4,366ms, COMMIT 213.5/2,393/3,738ms다. row/INSERT tail이 근접하지만 호출별 paired phase가 아니어서 상관관계와 인과를 확정할 수 없다. 구간 전체 dm-4 busy 64.01%·queue 190.29·write await 575.9ms, news active query 64회 및 외부시장 6,539행이 함께 관측됐다. 0B 저장은 없었다. master OFF 결과를 확인했다. 최초 34.9초 outlier는 재현되지 않았고 이번 결과만으로 해결 여부를 판단하지 않는다.

`2026.09.26-minute-revision-call-samples-v1`의 `call_samples`는 timestamp, API ID, 행수, revision lookup/insert 개수와 각 phase 시간을 같은 저장 호출에 연결한다. `2026.09.26-minute-revision-batch-insert-v1`에서는 `revision_insert_statements`가 batch INSERT SQL 수를, `revision_insert_rows`가 DB에 보낸 revision 행 수를 나타낸다. accepted sequence 배정을 위해 batch당 별도 sequence 조회가 추가되며 이 조회는 INSERT statement 수에 포함되지 않고 `revision_rows_ms`에 포함된다. 전용 PostgreSQL DB의 동시성·rollback 통합검사 7건은 통과했고 NAS `/health`에서 해당 build와 `status=ok`를 확인했다. 같은 운영 조건의 전후 성능 비교는 아직 없어 속도 향상은 주장하지 않는다.

세 번째 표본 `20260926T070655Z-ea8d03ea`는 새 build에서 60초 `measure`를 실행했다. 분봉 저장 22회·19,800행, revision lookup 22회·19,800키, revision INSERT 305회였다. revision source 구성 p50/p95/max 189.5/220/231ms, lock 1/1/10ms, batch lookup 35/42/46ms, row 처리 1/1/2,120ms, INSERT execute 0/0/2,099ms; 전체 revision 233/268/2,358ms, 저장 645/2,891/4,366ms, COMMIT 213.5/2,393/3,738ms다. row/INSERT tail이 근접하지만 호출별 paired phase가 아니어서 상관관계와 인과를 확정할 수 없다. 구간 전체 dm-4 busy 64.01%·queue 190.29·write await 575.9ms, news active query 64회 및 외부시장 6,539행이 함께 관측됐다. 0B 저장은 없었다. master OFF 결과를 확인했다. 최초 34.9초 outlier는 재현되지 않았고 이번 결과만으로 해결 여부를 판단하지 않는다.

`pause`는 작업별 TTL을 가지며 기본 10분, 최대 60분이다. 만료·컨테이너 재시작 시 자동으로 운영 설정으로 돌아간다. `test`는 ON → OFF → ON을 각각 같은 길이로 측정하고 자기 작업의 일시중지만 해제한다. 이미 중지된 작업이나 운영 설정상 꺼진 작업은 비교하지 않는다. 진행 중인 작업은 OFF 직후에도 마무리될 수 있으므로 작업량이 각 구간에서 실제로 달라졌는지 결과를 확인한다. 보고서와 조작 이력은 `/app/data/maintenance/diagnostic-results/` 및 `diagnostic-history.jsonl`에 남는다. 표본은 측정 결과 파일에 복사되며 수집 종료 뒤 메모리 버퍼는 유지하지 않는다.

제어 대상은 `minute_backfill`, `top20_after_close`, `news_jobs`, `news_stock_refresh`, `news_query_set`, `news_market_feed`, `external_market`, `candidate_monitor`다. `minute_backfill`은 TOP20 신규 편입·장후 보완의 키움 분봉 조회만 멈추고 실시간 0B 분봉 저장은 계속된다. 따라서 이 스위치로 NAS의 **모든 분봉 쓰기** 효과를 재는 것은 아니다.

master 아래에 둘 자식 항목은 작업별 pause와 메트릭 capture를 각각 둔다. **현재 실제 pause가 연결된 작업은 8개**(`minute_backfill`, `top20_after_close`, `news_jobs`, `news_stock_refresh`, `news_query_set`, `news_market_feed`, `external_market`, `candidate_monitor`)이고, 각 제어 범위는 아래 설명과 구현부를 따른다. writer 계측은 12개 항목이다. 실시간 WebSocket 수신·0B 저장, 계좌 이벤트/복구, 주문·체결 원장의 개별 pause 스위치는 아직 연결되지 않았다. 이 미완료 writer는 진단도구에서 `unsupported`로 드러나야 하며 `status`에 표시되지 않는 항목을 비활성·무부하로 해석하면 안 된다. 모든 핵심 경로도 최종적으로 master 하단의 개별 자식으로 추가하되, drain·재개·공백 기록 및 보호 경로 강제 옵션을 먼저 구현하고 보존 검증을 통과시킨다.

DB, 테이블, 저장 경로 또는 writer를 새로 추가·이전하는 개발은 master 아래에 개별 자식 스위치를 추가하고 진단도구 갱신을 완료 조건에 포함한다. 소스 경로 원장과 writer registry/메트릭, 적절한 WAL·wait·장치 표본, 중단 중 자료 처리와 재개 동작, 데이터 보존·동시성 검증을 함께 갱신한다. 관련 규칙은 [개발 불변 규칙 29-6](../DEVELOPMENT_GUARDRAILS.md)을 따른다.

측정은 PostgreSQL 분봉·일봉 저장 호출의 `bars/metadata/revisions/commit/total` 분포와 계측된 writer의 호출 수·시도 행수·elapsed를 포함한다. COMMIT percentile은 시간을 따로 잰 writer에서만 제공한다. API ID가 broker 저장 경계에 있는 경우 `api_writers`로 관측된 문서·dataset·persistent cache 저장 호출을 구분한다. PC SQLite의 별도 저장은 NAS PostgreSQL WAL에 포함하지 않는다.

`pg_stat_wal`의 WAL bytes·write·sync 증가량, PostgreSQL 대기 표본, NAS 장치 통계와 CPU/메모리도 구간 전체 값으로 남긴다. **전체 WAL 증가량을 특정 API의 WAL 사용량으로 해석하면 안 된다.** 여러 writer가 같은 시간에 쓰므로 API별 호출 수와 WAL의 인과관계는 A/B/A의 작업량·대기·저장시간 변화를 함께 보고 판단한다. 운영 중 백업, Docker, 외부 수집기, 체크포인트 같은 미제어 작업도 결과를 흔들 수 있다. 진단 버퍼가 넘쳐 이전 표본이 버려지면 결과의 `truncated` 또는 `writer_truncated`가 참이다.

이 도구는 지연의 기여도를 확인하는 수단이다. 특정 작업을 끄고 지연이 줄었다는 결과만으로 PostgreSQL 내부의 근본 원인이나 안전한 영구 중지 여부가 확정되지는 않는다. 실제 운영 NAS에 새 빌드가 반영됐는지는 `/health`의 `server_build`와 `status` 응답을 확인한다.

2026-09-26에는 `/health.server_build=2026.09.26-writer-diagnostics-v4`와
인증된 writer registry 12개 응답을 확인했다. 실제 장중/장후 A/B/A 결과와
flush별 COMMIT 표본은 아직 측정하지 않았다. 기본 OFF capture를 운영에서
임의로 상시 켜지 않는다.

## 기능 변경과 검증 모델

이 도구의 명확한 동작을 다루는 일반 코드 수정·단위 테스트·결과 문서화는 **GPT-6 Luna Low**로 진행해도 충분하다. 검증은 다음 순서로 기록한다.

1. 로컬 단위 테스트에서 TTL 만료, 작업별 일시중지, 복구 뒤 재개를 확인한다.
2. NAS 빌드에 반영한 뒤 `status`에서 `configured`, `effective`, `paused_by_diagnostic`, 작업별 만료 시각을 확인한다.
3. 짧은 A/B/A 비교를 실행하고 각 구간에 대상 작업이 실제로 활동했는지, 다른 NAS 작업이나 저장장치 부하가 함께 바뀌지 않았는지 확인한다.
4. 운영에서 OFF→ON 복구가 확인되기 전에는 해당 기능을 검증 완료로 표시하지 않는다.

저장 경계·동시성·작업 수명 설계를 바꾸거나, 실제 운영 지연의 원인이 갈라져 판단이 필요한 경우에는 현재 모델로 계속 확인 가능한 증거 수집과 단순 검증을 진행한 다음에만 상위 모델 검토를 권한다. 이 에스컬레이션 기준은 구현 난이도만으로 자동 적용하지 않는다.

v4의 `market_bar_saves.realtime_flushes`는 계측 중 **성공한 저장 호출이 있는**
0B flush별 COMMIT 수의 분포다. 실패하거나 쓰기 없는 flush는 표본에 없고
수신된 모든 0B의 수가 아니다. A/B/A 보고서는 `wal_fpi`·`wal_buffers_full`을
추가하며 `wal_timing.time_values_available`로 WAL 시간 통계의 사용 가능 여부를
표시한다. NAS 호스트에서 캡처한
`/app/data/maintenance/diagnostic-storage-mapping.json`이 있으면 저장장치
연결 그래프를 `storage_mapping`에 첨부한다. 이 그래프와 장치별 통계는
개별 PostgreSQL COMMIT의 실제 I/O 귀속을 뜻하지 않는다.

2026-09-26 NAS 측정 `20260926T094808Z-3b1cdb6c` (`commit-delay-rerun`, 60초):
health build는 `2026.09.26-minute-revision-batch-insert-v1`이었다. 진단 master는
측정 전에 OFF였고 측정 종료 뒤 명시적으로 OFF 복귀했다. `ka10080` 저장 28회·
24,922행에서 revision INSERT는 0회였다. 같은 호출의 COMMIT p50/p90/p95/p99/max는
127/1,236/1,259/1,375/1,375ms, 전체 저장은 578/1,669/1,676/1,679/1,679ms였다.
revision p95/max는 262/265ms였고 commit p95/max가 더 컸다. 따라서 무변경 재조회
표본에서도 COMMIT 지연이 반복됐다. 0B 실시간 저장은 0회(중앙 실시간 상태는
`WAITING_MARKET`)라 이번 구간의 0B COMMIT을 평가하지 않았다. 전체 PostgreSQL
표본에는 WAL sync 6회·WALWrite 8회가 있었지만 `track_wal_io_timing=off`라 WAL
시간 델타는 unavailable이었다. WAL 15.04MB, `dm-4` write await 144.97ms·queue
56.76·busy 58.98%, active query 표본 news 64·other 22·minute_bars 4가 함께 관측됐다.
이는 commit tail과 동시 저장장치 부하가 재현됐다는 근거지만 개별 COMMIT의 WAL/장치
원인을 증명하거나 news·분봉 중 하나에 귀속하지 않는다. 이후 `/health`에서
`2026.09.26-daily-minute-batch-storage-v1` 재빌드를 확인하고 같은 진단을 반복했다.

재빌드 후 측정 `20260926T100618Z-71c63050` (`commit-delay-post-rebuild`, 60초)은
health에서 `2026.09.26-daily-minute-batch-storage-v1` 실행을 확인했다. `ka10080`
저장 22회·19,522행, revision INSERT 0회였고 COMMIT p50/p90/p95/p99/max는
199.5/1,683/2,093/3,389/3,389ms, 전체 저장은 1,152/2,089/2,476/3,809/3,809ms였다.
직전 측정의 COMMIT p50/p95/max 127/1,259/1,375ms보다 이번 tail이 길지만 호출 수·동시
부하가 달라 빌드가 지연을 악화시켰다고 해석하지 않는다. 두 구간 모두 revision INSERT
없는 재조회에서도 commit tail이 재현됐다. 이번 구간 WAL은 7.10MB, sync 69회였고
`track_wal_io_timing=off`; wait samples는 WALWrite 16, WalSync 3이었다. `dm-4`
write await 237.9ms·queue 89.23·busy 60.04%, news active query 65표본과 함께 관측됐지만
모두 구간 전체 지표여서 개별 COMMIT에 귀속하지 않는다. 0B 저장은 여전히 0회,
실시간 연결은 `WAITING_MARKET`이었다. 진단 master는 측정 후 OFF로 확인됐다. 따라서
재빌드 후에도 COMMIT tail은 남아 있으며, 현재 자료로는 PostgreSQL COMMIT의 개별 대기
원인이나 특정 writer의 기여를 확정할 수 없다.

2026-09-26 O12 로컬 구현 `2026.09.26-commit-wait-correlation-v1`: capture가 켜져
있을 때만 분봉/일봉 저장 트랜잭션에서 `SET LOCAL track_wal_io_timing=on`을
시도하고, 권한이 없으면 savepoint rollback 후 저장은 그대로 진행한다. commit이
100ms를 넘으면 해당 backend PID의 `pg_stat_activity` wait event·blocking PID를
25ms 간격으로 읽어 동일 저장 호출에 연결한다. 측정 CLI는 host device counters를
250ms 간격으로 수집하고 commit 시작·종료에 가장 가까운 경계의 delta를 call sample에
붙인다. 장치 수치는 같은 구간에 겹친 다른 프로세스 쓰기를 포함하므로 개별 commit의
I/O 귀속이 아니다. commit wait 표본은 100ms 미만 대기나 probe 연결 실패를 놓칠 수
있으며 이를 결과에 기록한다. 전역 PostgreSQL 설정은 바꾸지 않는다. 단위검사 9건과
`git diff --check`는 통과했다. 누적 소스와 문서는 NAS 공유에 SHA-256 일치로 복사했고
백업도 만들었다. 사용자의 누적 재빌드, 새 marker 확인, 운영 재측정이 남아 있다.
공식 문서는
`track_wal_io_timing`이 WAL I/O 호출 시간을 재며 일부
플랫폼에서 계측 오버헤드가 있을 수 있다고 설명한다.

2026-09-26 O12 `bars_ms` 분해 최초 구현 `2026.09.26-bar-upsert-wait-correlation-v1`:
capture 중 분봉 `executemany` 전체 호출과 일봉 multi-row UPSERT batch 호출을
시간 측정하고, 100ms 이상인 구간은 해당 writer backend PID의 wait event와
blocking PID를 표본화한다. PostgreSQL UPSERT 전에 transaction-local
`track_wal_io_timing` 적용을 시도해 UPSERT와 이후 COMMIT 안에서 유지하며,
권한 오류는 savepoint rollback으로 격리한다. `measure`는 250ms host device delta를
각 UPSERT 문장 구간에도 붙인다. capture OFF에는 별도 probe를 만들지 않는다.
빠른 문장은 probe의 100ms 시작 지연보다 먼저 끝날 수 있어 표본되지 않는다. 장치
delta는 host-wide이고 다른 프로세스의 I/O가 포함되며 개별 SQL 귀속은 아니다.
관련 unittest 57건·구문검사 및 `git diff --check` 통과. 다음 독립 검토에서 아래
v2 보완사항을 확인했다.

독립 검토 후 로컬 v2 (`2026.09.26-bar-upsert-wait-correlation-v2`)에서는 분봉
`executemany`의 여러 행을 하나의 SQL 호출 구간으로 명시하고, 일봉의 여러 multi-row
batch는 호출별 구간으로 남긴다. `bars_ms`에는 probe 종료 대기를 넣지 않고 실제
UPSERT 호출 시간은 각 window의 `duration_ms`에 기록한다. probe 시작 실패는 저장을
중단하지 않으며, probe가 결과 수집 시점에 아직 실행 중이면 그 상태를 표시한다.
100ms 미만 구간에서 표본이 비면 `sampling_status=below_initial_delay`로 표시하고,
probe 오류·진행 중·표본 수집 성공도 구분한다. COMMIT probe 종료를 기다리는 시간도
저장 `total_ms`에 포함하지 않는다.
backend wait 표본은 SQL 또는 COMMIT 시작·종료 구간 안의 값만 채택한다. NAS 장치값은
실제 읽은 시각과 관측된 표본 간격을 기록한다. 장치 delta는 SQL 실행의 개별 I/O
기여량이 아니라 같은 구간 호스트 전체 활동이다. v2 소스 12개는
`X:\kiwoom-monitor-backups\20260926-200349-diagnostic-session-control-v1`에 원본
백업 후 NAS 공유에 해시 검증 복사했고, 사용자가 서버를 재빌드했다. NAS
`/health`에서 `status=ok`, `server_build=2026.09.26-bar-upsert-wait-correlation-v2`
를 확인했다.

v2 운영 측정 `20260926T111142Z-fb233263`은 60초 동안 `ka10080` 저장 25회·
22,222행을 기록했다. 분봉 UPSERT 계측 구간은 24개·21,322 행 처리였고 첫 저장은
측정 경계에서 backend PID/SQL probe가 없어 빠졌다. UPSERT `bars_ms`는 p50/p90/p95/
max 79/192/317/331ms였다. 100ms 이상 6개 구간 가운데 4개에서 같은 backend의
`DataFileWrite`, 그중 1개에서는 `DataFileRead`도 관측됐다. 나머지 긴 구간은
CPU 상태 표본 2개와 시작 지연 안에 끝나 표본되지 않은 1개였다.

같은 저장의 COMMIT은 p50/p90/p95/max 154/1,970/2,380/2,810ms였다. 2,810ms
COMMIT에서 같은 backend가 `WALWrite` 16표본 뒤 `WalSync` 88표본에 머물렀고,
2,380ms COMMIT에서는 `WalSync` 86표본이었다. 관측된 blocking PID는 없었다.
60초 DB 전체 delta는 WAL 15.60MB, wal_write/wal_sync 각 81회,
`wal_sync_time=8,924.9ms`였다. 이는 DB 전체 통계이므로 분봉 단독 시간으로
귀속하지 않지만, 동일 분봉 backend 표본은 COMMIT의 WAL 대기를 직접 확인한다.
dm-4의 같은 구간 host-wide 쓰기 대기와 queue도 높았으나, 다른 writer I/O가 포함되고
측정 경계 padding이 142~457ms여서 장치 시간을 SQL 단독 원인으로 귀속하지 않는다.
뉴스 claim 117회 등 다른 작업이 계속 실행 중이었고 일봉 저장 표본은 없었다.
측정 종료 후 진단 master와 capture는 OFF, workload pause는 없었다.

이 결과에서 측정 연결의 `track_wal_io_timing=off`와, 각 24개 저장 transaction의
`SET LOCAL` 성공 및 nonzero WAL timing delta가 함께 나왔다. 측정 연결 값만으로
WAL 시간 표본이 없다고 표시하면 잘못 해석하므로 로컬 v3는 연결 설정과 transaction
설정, cluster-wide `pg_stat_wal` delta를 나눠 보고한다. 관련 회귀 86건 통과.
v3 marker는 `2026.09.26-bar-upsert-wait-correlation-v3`이며 아직 NAS 공유/서버에
복사되지 않았고, 다음 측정은 v3 재빌드 후 해야 한다.

O12 후속 로컬 v4 `2026.09.26-bar-upsert-wait-correlation-v4`는 진단 `measure`의
시작과 끝에서 PostgreSQL 17 `pg_stat_io` 및 `pg_stat_checkpointer`를 읽어 차분한다.
`pg_stat_io`는 backend_type/object/context별 read/write/writeback/extend/fsync 횟수와
시간, hit/eviction/reuse를 반환하며, checkpointer는 timed/requested checkpoint 수,
쓰기·sync 시간, 기록 buffer 수를 반환한다. 기존 `pg_stat_wal` delta와 함께 봐서
관계 파일 I/O, checkpointer writeback, WAL sync가 같은 측정 구간에 어떻게 변했는지
구분한다. 이 값들은 클러스터 전체 누적 통계의 전후 차분이라 개별 SQL에 I/O를
귀속하지 않는다. 통계 조회 실패/권한 부족은 해당 세부 항목을 `available=false`로
표시하고 본 측정은 계속한다. `stats_reset`이 전후 달라지면 음수 또는 오해를 부르는
delta 대신 counter delta를 null로 표시한다. 계측은 master가 켜진 명시적 측정에서만
실행된다. PostgreSQL 누적 통계는 backend가 아직 실행 중이면 공유 반영이 늦을 수 있어
짧은 측정의 전후 차분에 반영되지 않은 쓰기가 있을 수 있다. `pg_stat_io`의 실행 불가능한
operation은 0이 아니라 NULL이며, 누락된 그룹·reset·감소한 counter의 차분도 알 수 없음으로
표시한다. `pg_stat_io`의 I/O 시간 필드는 해당 I/O가 실행될 때 `track_io_timing`이 켜져
있었을 때만 신뢰할 수 있다. 반면 `pg_stat_checkpointer.write_time/sync_time`은 checkpoint
단계 시간으로 `track_io_timing` 설정에 의존하지 않는다. 진단 연결의 현재 설정과 이 한계를
함께 기록한다. 이 기능은 운영 DB 설정을 변경하지 않는다. v4
관련 단위검사는 통과했으며 NAS 재빌드·실제 PostgreSQL 실행 측정은 아직 수행하지 않았다.

2026-09-26 master 계층 구현에서는 하위 `capture`, `pause/resume`, `measure`,
`test`, `report`가 최상단 `tool on --ttl ...` lease를 요구한다. 부모 스위치를
끄거나 만료시키면 모든 자식 override가 효력을 잃고, 새로 켜면 기존 자식 상태를
비운다. 로컬 build marker는 `2026.09.26-diagnostic-master-switch-v1`이며,
NAS에 반영한 뒤 `/health.server_build`와 `tool status`를 확인해야 한다.
이 marker는 master gate를 추가한 버전이고, 자식 workload 범위는 앞에서 적은
8개다. 계좌·주문·체결 및 모든 저장 writer의 개별 ON/OFF 스위치가 완료됐다는
뜻은 아니다.

2026-09-26 NAS `diagnostic-session-control-v1`에서 두 개의 30초 A/B/A를 수행했다.
`minute_backfill` test `20260926T030851Z-76ebef1c`의 ON/OFF/ON별
`ka10080` 완료 수는 26/0/26, 저장 transaction은 20/0/20, 저장 행은
8,484/0/8,782였다. 전체 WAL 증가는 3.15/0.12/1.85 MB였고, 다시 켠 구간의
분봉 저장 p95/COMMIT p95는 2,124/1,383 ms, 느린 저장 경고는 3건이었다.
이는 해당 보완 조회가 분봉 저장과 WAL 쓰기를 만든다는 점을 확인하지만, OFF
구간에도 호스트 장치 쓰기 대기와 다른 DB 작업이 남아 있어 전체 지연의 유일한
원인이라고 결론내리지는 않는다.

`news_jobs` test `20260926T031325Z-6d4feb05`에서는 ON/OFF/ON 동안 뉴스 claim
transaction이 60/0/59였고, PostgreSQL blocks_read 증가량은 6,066,244 /
58,520 / 5,927,592였다. 뉴스 작업 OFF 구간에도 `ka10080` 분봉 저장은
20건·8,467행 계속됐으며, 분봉 저장 p95/COMMIT p95는 1,341/679 ms,
1,164/1,120 ms, 1,218/793 ms였다. 느린 저장 경고는 세 구간 모두 3건이었다.
따라서 이 표본에서는 뉴스 작업이 DB 읽기 부하를 크게 만들지만 뉴스 작업을
멈춰도 분봉 저장 지연이 사라지지 않았다. WAL 증가량은 OFF에서 오히려
2.94 MB였고 장치 쓰기 대기도 계속되어 뉴스 작업을 분봉 지연의 단독 원인으로
볼 근거는 없다.

두 비교 모두 각 구간이 30초이고 WAL I/O 시간 계측은 꺼져 있었다. 전체 WAL·장치
통계는 여러 writer와 호스트 작업의 합계라 개별 commit에 귀속할 수 없다. 이번
결과는 `minute_backfill`의 저장 기여와 `news_jobs`의 읽기 부하를 분리했지만,
분봉 commit 지연을 유발한 장치 I/O의 구체적인 소유 writer는 확정하지 못했다.
진단 CLI는 테스트 종료 시 workload override를 복구하고 임시 metrics capture를
끄도록 실행됐다. 저장된 원본 보고서는 NAS `/app/data/maintenance/diagnostic-results/`
아래 각 test ID의 JSON이다.

`external_market` test `20260926T031919Z-14cdead1`는 각 30초로 완료됐으나,
세 구간 모두 외부시장 전용 writer transaction이 하나도 관측되지 않았다. 대신
분봉 저장은 20건씩 유지됐고 p95/COMMIT p95는 925/424, 980/613,
1,076/686 ms였으며 느린 저장 경고는 1/1/2건이었다. 장치 `dm-4` 쓰기 대기는
88/86/87 ms로 비슷했다. 이 결과에서는 `effective=true`도 실제 활성 증거로 쓸 수
없었다. 당시 API는 collector 객체가 존재하면 `configured=true`로, pause되지 않으면
`effective=true`로 만들었지만, collector task 시작에는 별도 운영 설정
`external_market_enabled`가 필요했다. 따라서 해당 시험의 실제 실행 여부와 부하 효과는
판정 불가다.

로컬 `2026.09.26-external-market-diagnostics-v1`에서는 상태 API가 운영 활성화,
task 생존, 실제 polling 주기, 누적 실행·완료 횟수, 누적 성공 행 수, 최근
완료시각·저장 행 수·오류를
반환한다. 비교 CLI는 외부시장 collector가 실제 실행 중이 아니면 시작을 거부하고,
한 구간이 보고된 polling 주기보다 짧으면 거부한다. A/B/A 결과에 각 구간의 실행 횟수
완료 횟수·누적 저장 행 수 차이를 표시하고 ON/OFF/ON 각각 활동이 관측되지 않으면 귀속 부적합으로
표시한다. 기본 polling 주기 300초에 맞는 시험은 `--duration 5m`보다 조금 길게,
예를 들어 `310s`로 지정해야 한다. 세 구간이 길어지므로 master 및 metrics capture TTL도
전체 실행 시간보다 길게 설정한다. 이 코드는 로컬 검증 후이며 NAS 배포 확인 전이다.

transaction 경계 또는 실시간 저장 흐름을 실제 수정하는 단계에서는 [키움 저장 경로 감사의 필수 검증](KIWOOM_STORAGE_WRITE_AUDIT.md#transaction실시간-저장-변경-시-필수-검증)을 추가한다. 특히 동시 도착·중복·순서 역전, commit 전후 장애·재시작, 재시도 멱등성, 원천과 파생 자료의 변경 전후 보존을 확인해야 한다. A/B/A 지연 측정은 이 검증의 대체물이 아니다.

## 2026-10-02 뉴스 claim 단계 대기 진단

로컬 `2026.10.02-news-claim-phase-waits-v1`은 기존 뉴스 claim SQL과 transaction을 바꾸지 않고 `recover_stale`, `select_candidates`, `mark_running` 시간을 `call_id`에 연결한다. `select_candidates`는 execute와 `fetchall()`을 합친 구간이므로 공통 `execute_ms`와 경계가 다르다. 단계가 100ms 이상 지속되면 별도 연결로 같은 writer backend의 state/wait event/blocking PID를 최대 25ms 간격으로 표본화한다. statement timeout은 500ms이며 뉴스 claim 진단 observer는 동시 3개로 제한된다. capture OFF 또는 generation이 바뀌면 추가 표본 연결을 열지 않고, probe 실패는 원래 SQL 결과를 바꾸지 않는다. 호출당 raw 단계 16개와 단계당 표본 256개까지 보존하며 초과는 truncated 표식으로 드러낸다.

`/api/v1/diagnostics/db-calls`의 summary `writers["news.job_claim/news_job_claim"].phase_diagnostics`에서 단계별 `duration_ms` 분위수, sampling status, wait event 표본 수, blocking PID 표본 수, probe 오류를 본다. raw 모드의 각 `calls[]` 항목 `phase_diagnostics[]`에 개별 시작/종료 시각과 wait 표본이 있다. wait 표본 count는 실제 wait duration이 아니고, `no_sample`은 wait가 없었다는 뜻이 아니다. 새 서버 build는 `2026.10.02-news-claim-phase-waits-v1`이며 사용자가 NAS 전용 PostgreSQL 검사 2건 통과와 운영 적용 결과를 제공했다.

## 2026-10-02 기록된 DB 호출의 장후 부분 재생 pilot (로컬 후보)

인증된 `POST /api/v1/diagnostics/runs`에서 `kind=replay`, `workload=recorded_news_shadow`, 완료된 `measure`의 `profile_report_id`, `seconds`를 지정한다. `window_start_seconds`와 `window_end_seconds`는 원본 측정 시작 기준 초 단위이며, `include_writer_kinds` 또는 `exclude_writer_kinds`로 정확한 writer kind를 고른다. 선택 구간 길이보다 측정 시간을 최소 5초 길게 둔다. 동일 보고서와 구간으로 전체 지원 writer, 뉴스 제외, shadow 제외를 각각 별도 실행해 비교할 수 있다. 선택한 종류가 지원되지 않으면 실행을 거부한다.

현재 재생 가능한 호출은 원본 raw의 `sql_calls=2`인 **빈** `news_job_claim`과 `sql_calls=2`인 **inline** `shadow_monitor_state`뿐이다. 다른 뉴스 상태, 0B, 분봉, TOP20, reader, frame 저장 및 원본 SQL parameter는 재생하지 않는다. 따라서 이 pilot으로 장중 전체 부하나 어떤 writer의 인과적 기여를 판정하지 않는다. 원본 호출 개수, 선택 구간 호출 개수, 실제 재생/누락/밀림 개수를 결과에 분리한다. 원본 시각 간격은 유지하고 뉴스 최대 3개·shadow 최대 1개로 병렬 실행한다. 속도 배율과 임의 동시성 옵션은 아직 없다.

재생은 평일 KST 07:30~20:30에 거부한다. 다만 `KIWOOM_ENVIRONMENT=real`이고 `diagnostic_replay.py`에 실계좌 한국 증권시장 휴장일로 확인해 등록한 날짜, 그리고 주말은 이 시각 제한에서 제외한다. 2026년 휴장일은 [한국거래소의 휴장 규칙](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp)과 [공식 2026년 월력요항 발표](https://www.kasi.re.kr/kor/post/newsMaterial/32031)에 따라 등록했다. 새해의 날짜나 임시 휴장은 근거를 확인해 등록하기 전까지 일반 평일로 취급한다. WebSocket `READY`나 당일 `0s` 미수신만으로 휴장을 추정하지 않는다. 기존 진단 master/capture/run lock을 요구한다. DB 이름은 전용 `kiwoom_monitor_diagnostic_test`로 연결 전후 확인하고, 해당 DB의 뉴스 작업 행이 비어 있어야 한다. shadow는 별도 진단 키만 쓰고 측정 종료 후 삭제·검증한다. 운영 DB에는 재생 쓰기를 하지 않는다. PostgreSQL 전체 WAL과 NAS 장치 통계에는 다른 호스트 작업도 포함되므로 결과의 차이가 재생 writer에 귀속된다고 단정하지 않는다.

# NAS 진단도구 API 연결 설계

2026-09-29 · O12 · **v4 배포 및 observe-only API smoke 완료; 비교 run/전체 동등성은 미확인**

NAS 현행 v2 소스와 다른 `app.py`/`database.py`는 진단 블록만 선별 반영했다.
기존 NAS 대상 7개 파일은 `X:\kiwoom-monitor-backups\20260929-diagnostic-api-v4`에
백업했고, 후속 API/history 수정 전 2개 파일은 `...-refresh-1`에 추가 백업했다.
새 모듈 2개를 포함한 9개 파일의 X: 소스 구문·import 및 진단 단위검사 29건은
통과했다. 배포 스크립트는 NAS artifacts에 배치하고 SHA-256/셸 구문을 확인했다.
실행 `/health.server_build`는 `2026.09.29-diagnostic-api-v4`다. 인증 capabilities와
모든 snapshot section이 반환됐고 PostgreSQL 17.11에서 `pg_stat_io`, checkpointer,
activity, news table/index 정보가 읽혔다. legacy report 20개와 history page를 조회했고
5초 observe-only run의 schema-2 보고서를 raw/summary 양쪽으로 회수했다. 그 창에 raw
DB call 15건, `unregistered_calls=0`이 기록됐고 coverage는 opt-in 관측 한정이다.
종료 후 master/capture OFF 및 workload pause 0을 다시 확인했다. 이 결과는 한 짧은
시각의 API 경로 검증이며 CLI와 동일 window 대조나 전체 writer coverage를 입증하지 않는다.
compare는 workload를 pause하므로 운영 run 검증은 하지 않았다. NAS host Python 3.8은
앱 의존 코드의 `datetime.UTC`를 지원하지 않아 host에서 lock 시험은 수행할 수 없었다.
컨테이너 Python 3.13의 cross-process flock은 별도 확인 항목이다. storage mapping은
2026-09-26 생성본이며 현재 토폴로지 검증으로 간주하지 않는다.

사용자 요청은 NAS 명령어를 붙여 넣지 않고 기존 운영 진단도구의 정보를 API로
얻는 것이다. 기존 `nas_workload_diagnostic.py`의 상태·제어·측정·A/B/A 비교·보고서와
뉴스 쿼리 진단 스크립트의 읽기 전용 정보를 포함한다. 운영 writer의 transaction,
concurrency, schema, durability 또는 poll 주기를 바꾸는 작업은 아니다.

## 1. 조사 결과와 현재 경계

- HEAD `047a528` 위에 누적 미커밋 구현이 있다. 전체 작업본이나 NAS 소스를 덮어쓰지 않는다.
- 현재 NAS `/health.server_build`는 `2026.09.28-db-observability-v2`다. 2026-09-29
  인증 API 재확인에서 master/capture OFF, workload 8종을 확인했다. 로컬은 archive
  workload까지 9종이므로 로컬 전체 app/database를 NAS에 복사하면 안 된다.
- 로컬 v3 초안에는 claim plan의 parent/depth/filter/cost 필드와 빌드 표식 변경이
  남아 있다. 아직 검증·NAS 반영하지 않았다. 이번 구현에 통합해 검증한다.
- NAS `server-data/maintenance/diagnostic-results`에 기존 JSON 보고서 20개가 있다.
  storage mapping도 존재하며 생성 시각은 `2026-09-25T21:28:32.516603+00:00`다.
  이는 현재 토폴로지를 새로 측정했다는 증거가 아니다.
- `central_server/__main__.py`는 한 Uvicorn 프로세스에서 앱을 생성한다. 공통 DB
  metric deque는 서버 프로세스 로컬이다. 독립 importer/PC 프로세스 메모리는 보이지 않는다.

| 정보/동작 | 현재 생산 코드·원천 | 현재 API | 이번 연결 계약 |
| --- | --- | --- | --- |
| master/session/revision, capture, workload/TTL | `diagnostic_workloads.py`; CLI `_set_tool/_set_capture/_set` | workloads GET만 | 동일 파일·잠금·TTL을 사용하는 인증 제어 API |
| writer/read 계측·call ID/PID·단계시간·오류·UNREGISTERED | `postgres_access.py`, `diagnostic_metrics.py` | db-calls | 기존 응답 보존, run 결과에 같은 구간 summary/보존 raw 포함 |
| bars/revision/flush·COMMIT/SQL wait·blocking PID | `diagnostic_metrics.py`의 domain 기록 | market-bar-saves | 기존 정보를 그대로 포함, 장치 window 상관 추가 |
| writer registry/지원하지 않는 경로 | `diagnostic_writer_registry.py` | writers | 그대로 포함; 관측 0건과 미계측 구분 |
| 자원·DB/범주별 추정 크기 | app resources, store storage methods | resources | 그대로 연결; 추정 표시 유지 |
| WAL/database counter와 delta | CLI `_snapshot/_wal_timing_report` | 없음 | before/after, reset·시각·scope·timing 가용성 |
| pg_stat_io/checkpointer | CLI `_pg_stat_io_snapshot/_checkpointer_snapshot/_counter_delta` | 없음 | backend/object/context 차원, NULL·reset 보존 |
| active/idle-in-transaction, wait, blocker, query/xact age | `diagnose_news_claim_query.py`, `diagnose_active_news_queries.py`, `diagnose_long_news_query.py`, `diagnose_prepared_news_db_waits.py` | 없음 | 현재 snapshot와 시간 표본; PID/backend_start·query fingerprint·분류 |
| news jobs table/index 통계 | `diagnose_news_claim_query.py` | 없음 | 같은 catalog 조회, 누적값/추정치임을 표시 |
| claim EXPLAIN | `PostgresQueryStore.explain_news_job_claim_plan` | claim-plan v2 | 고정 SQL 유지, parent/subplan/join/filter/cost 관계 보완 |
| host load/memory/device | CLI `_host_usage/_device_stats/_device_delta` | resources에 일부 | 원시 counter·실제 표본 시각·구간 delta·누락 이유 |
| PGDATA/WAL mount/block graph | `nas_storage_mapping.py`; 저장된 mapping JSON | 없음 | 전체 저장본+생성시각+현재 sysfs 검증 결과 |
| COMMIT/SQL ↔ 장치 window | CLI `_correlate_commit_device_samples` | 없음 | 기존 domain phase와 가능한 common call window 모두 연결 |
| 로그 분류 건수 | CLI `_log_position/_log_counts` | 없음 | 고정 분류 counts, rotation/missing/truncation 상태; 원문 제외 |
| 외부 importer 존재 | CLI `_uncontrolled_importers` | 없음 | 알려진 프로세스 이름만; PID namespace 내 가시성 한계 |
| A/B/A 및 외부시장 실제 활동 | CLI `_run_measurement/_external_market_activity` | 없음 | 동일 비교 순서·제한·판정 한계 유지 |
| 보고서·제어 이력 | CLI `_save/_history`, diagnostic-results/*.json | 없음 | 기존 보고서 포함 목록/조회/download, 제한된 이력 페이지 |

대상은 **NAS 운영 진단의 정보 동등성**이다. dedicated PostgreSQL 통합검사,
합성 write benchmark, PC SQLite 감사, 임의 shell/SQL 실행은 운영 진단 API로
전환하지 않는다. 이 도구들이 생성한 별도 결과를 운영 관측 표본인 것처럼 합치지 않는다.
서버 capabilities에 이 범위와 미지원 항목을 명시한다. NAS 밖의 독립 프로세스·호스트
관리 권한이 있어야 새로 만들 수 있는 정보는 `unavailable` 또는 `snapshot`으로 표시한다.

## 2. 선택한 구조

**기존 CLI와 API가 제어 파일·수집 함수·보고서 계산을 공유한다.**
API에서 CLI subprocess를 실행하는 안은 기존 코드를 덜 옮기지만, 자식 프로세스 회수,
stdout 파싱, 서버 자신에 대한 HTTP 요청, 서버 종료와 자식 TTL 차이를 추가한다.
별도 host agent는 이번 정보 범위에 필요하지 않다. 따라서 둘 다 도입하지 않는다.

책임은 다음 세 곳에 둔다. 범용 provider/plugin/manager 계층을 만들지 않는다.

1. 기존 `diagnostic_workloads.py`: 파일 control read/evaluate뿐 아니라 기존 mutation,
   atomic write, lock, owner/session 검증을 소유한다. CLI 내부 함수는 여기로 이동한다.
   Linux flock은 유지하고 Windows import에서 fcntl을 요구하지 않게 한다. Windows
   실제 mutation을 제공한다면 OS 잠금을 구현하며 thread lock으로 cross-process 잠금을
   대체하지 않는다. 지원하지 않는 환경은 명시적으로 거절한다.
2. 신규 `central_server/diagnostic_sampling.py`: 기존 CLI의 PostgreSQL/host/log snapshot,
   delta와 DB-call 보고서 계산을 이동한다. query 분류와 뉴스 table/index 조회도 이곳으로
   합친다. 고정 query만 사용한다. 호환성이 필요한 `_measure/_run_measurement`는 CLI
   모듈에 유지하고 API가 이를 in-process import하며 callback/stop을 주입한다.
3. 신규 `central_server/diagnostic_runs.py`: 한 진단 run의 독립 수명, 소유권, 중단,
   상태/보고서 파일을 소유한다. 앱 lifespan에 instance 하나를 명시적으로 붙인다.
   `app.py`는 인증·입력 검증·서비스 wiring만 담당한다.

`_api()`로 자기 서버를 HTTP 호출하던 부분은 주입된 snapshot 함수로 바꾼다. API와
기존 GET route가 동일 runtime builder를 호출한다. CLI의 현재 프로세스 밖 server
metrics 접근은 HTTP adapter를 유지하므로 CLI 실행 프로세스의 빈 deque를 읽지 않는다.
CLI와 API의 run admission에는 같은 nonblocking OS run lock을 적용한다. control lock은
짧은 파일 변경에만 사용한다. 긴 측정 중 control lock을 잡지 않는다.

수집은 서버 소유 worker thread에서 수행하고 event loop를 막지 않는다. 하나의
진단 run만 허용하지만 운영 writer는 직렬화하지 않는다. 시작 전에 원자적으로 slot을
예약하고 파일 잠금까지 확인한다. 동시/CLI 충돌은 409로 반환하고 큐에 적재하지 않는다.
`asyncio.to_thread` task 취소만으로 실행 thread가 멈췄다고 취급하지 않는다.

## 3. 인증 API 계약

기존 여섯 GET은 유지한다. 신규 경로는 모두 기존 Bearer 인증을 사용한다.
GET은 master나 capture를 켜거나 workload를 멈추지 않는다.

| 경로 | 입력·결과 |
| --- | --- |
| `GET /api/v1/diagnostics/capabilities` | build/producer, 지원 항목·원천·scope·상한·미지원 이유; 실행 없이 확인 |
| `PUT /api/v1/diagnostics/control` | `target=master|capture|workload`, `enabled` 또는 `paused`, workload, TTL, expected instance/session/revision. target별 허용 필드만 수락 |
| `GET /api/v1/diagnostics/snapshot` | 고정 section enum(`postgres,activity,news_jobs,storage,host`) 선택, 선택 PID. 즉시 읽기 전용 bounded snapshot |
| `POST /api/v1/diagnostics/runs` | `kind=measure|compare`, seconds, label, compare workload, request_id, expected session/revision. 202 + run_id/status URL |
| `GET /api/v1/diagnostics/runs/{run_id}` | 상태·phase·실제 기간·수집/누락 수·부분 결과·정리 결과 |
| `POST /api/v1/diagnostics/runs/{run_id}/cancel` | 해당 run에 cooperative stop 요청; 202. 반복은 멱등, 완료됐으면 현재 결과 |
| `GET /api/v1/diagnostics/reports` | 기존 20개 포함 bounded 목록·cursor·schema/version·날짜·완료 상태 |
| `GET /api/v1/diagnostics/reports/{report_id}` | summary 또는 전체 JSON download; 생성된 보고서만 반환 |
| `GET /api/v1/diagnostics/history` | 제어/수집 이벤트의 제한된 페이지, raw log/임의 파일 경로 금지 |

control의 master ON은 기존 ON이면 자식/TTL을 보존한다. 새 ON/OFF 및 자식 변경은
잠금 안에서 expected instance/session/revision을 재검증하고 mismatch면 409다.
master OFF는 모든 override와 새 수집을 해제한다. 기존 workload admission 의미를
바꾸지 않는다. 보호 경로용 새 pause 스위치를 만들지 않는다. runtime ACK가 없는 경로는
`pause_requested`로 표시하며 단순 `effective=false`를 drain 완료로 설명하지 않는다.

run은 활성 master에서 시작한다. master/capture 시작도 API로 가능하므로 셸이 필요 없다.
capture가 이미 활성화돼 있으면 남은 TTL을 검증해 빌려 쓰고 종료 시 끄지 않는다.
없으면 run owner로 capture를 만들고 자신이 만든 것만 정리한다. request_id는 같은
session에서 같은 요청 재전송 시 기존 run을 돌려주며, 내용이 다르면 409다.
compare만 명시적으로 pause한다. measure는 운영 workload를 바꾸지 않는다.

기존 시간 상한을 유지한다: master/capture/pause 60..3600초, measure 5..300초,
compare phase 5..1100초. 기존 전체 TTL 사전검사를 보존한다. label은 최대 80자,
보고서 목록/이력은 페이지당 최대 100건, activity 표본은 최대 200 backend,
EXPLAIN node는 최대 256개·전체 응답 1 MiB로 제한하고 잘림을 표시한다.
잘못된 enum/비유한 숫자/경로형 ID는 거절한다. 부재 404, 미지원 501, 충돌 409,
수집 실패는 section별 error code/type로 반환하며 DSN·예외 원문을 넣지 않는다.

## 4. 수집·중단·재시작 계약

run 상태는 `starting → running → finalizing → completed|incomplete|aborted|failed`다.
각 결과는 run/session/producer/control revision, build, wall-clock 시작/종료,
monotonic 실제 elapsed, 요청 기간, 표본 수, section별 availability를 포함한다.

- stop Event와 session/TTL을 매 tick·DB query 사이·phase 사이에서 확인한다.
  기존 고정 sleep은 `stop.wait(timeout)`으로 바꾼다. connect timeout 5초,
  statement timeout 2초, 진단 connection에만 read-only session 설정을 사용한다.
- diagnostics connection은 기존 autocommit probe ownership을 유지한다. 이동한
  단일 probe factory를 direct-access 감사의 diagnostic 예외로 갱신한다. 여러 SELECT를
  하나의 업무 transaction으로 기록하거나 observer 자체를 도메인 writer로 집계하지 않는다.
  observer PID·query 수·시간은 보고서에 따로 남기고 activity 표본에서 자신을 제외한다.
- default activity 간격 1초, device 250ms를 유지한다. 기존 5초 wait 도구의 정보를
  위한 activity 250ms 선택도 허용하되 최소 250ms·총 duration 상한을 적용한다.
  각 실제 tick 시각을 남기고 늦은 tick을 정시에 측정한 것처럼 보간하지 않는다.
- 정상 종료는 **capture OFF 전에** 서버 metrics와 raw를 동결하고 보고서를 만든다.
  session 변경/OFF/TTL은 즉시 새 수집을 중단한다. 최근 보존한 in-memory snapshot과
  수집 완료 표본으로 부분 결과를 저장하며, 마지막 metrics 회수 이후 누락 구간을
  표시한다. 기존 `clear_metrics` 동작을 무력화하거나 끝난 capture를 다시 켜지 않는다.
- partial 결과를 위해 metrics의 bounded copy를 최대 초당 한 번 메모리에 갱신한다.
  `_LOCK` 아래에서는 copy만 하고 집계/직렬화/파일 I/O를 하지 않는다. 정상 종료에
  한 번 더 동결한다. 복사 비용과 collector 오버헤드를 검증한다.
- API client disconnect는 run을 자동 취소하지 않는다. run ID로 다시 조회할 수 있다.
  lifespan shutdown은 stop→worker join→owner 정리 순서다. join budget 10초가 지나도
  살아 있는 worker를 완료로 표시하거나 새 run slot을 열지 않는다.
- 시작 manifest를 원자 기록한다. 프로세스 재시작 시 이전 running manifest는
  `aborted:producer_restarted`로 정리하고 자동 재개하지 않는다. instance가 같은
  Uvicorn process restart도 producer ID로 구분한다. 다른 owner의 자식 상태는 지우지 않는다.
- cleanup은 `(instance, session, owner)`가 여전히 같은 항목에만 적용한다. control
  revision 변경은 비교를 incomplete로 표시하고 새 수동 override를 덮어쓰지 않는다.
- compare의 2초 대기는 기존처럼 settle 시간일 뿐 drain ACK가 아니다. 이미 paused인
  대상은 거절하고 종료·예외·취소 모두 자신이 만든 pause만 해제한다. 모든 phase의
  실제 활동 증거가 부족하면 인과 판정을 하지 않는다.

## 5. 정보 정확성·보관·노출

WAL/DB/장치 counter는 before/after reset·identity 변경·감소를 확인한다. NULL이나
권한/버전 미지원은 0으로 바꾸지 않는다. pg_stat_wal/pg_stat_io/checkpointer는
cluster scope, pg_stat_database는 DB scope, 장치는 host scope다. observer 자신의
부하도 포함됨을 적고 call ID별 I/O라고 주장하지 않는다. sampled wait 없음은 CPU
병목 확정이 아니다. PID 재사용을 구분하기 위해 backend_start도 함께 기록한다.

장치는 현재 고정 4개 대신 검증된 mapping graph의 기기와 partition의 parent device를
수집한다. 중첩 block layer의 값을 합산하지 않는다. sysfs identity가 맞지 않으면
mapping을 stale/unverified로 표시한다. 저장된 host mapping 전체와 captured_at을
제공하되 Docker bind source의 현재 일치는 별도 배포 검증이다. Docker socket·host
전체 /proc·PGDATA를 앱에 새로 마운트하지 않는다. 호스트 topology 변경 시 mapping
재생성은 배포 절차에 포함하고, API가 과거 snapshot을 live 결과로 표시하지 않는다.

query text·뉴스 원문·계좌번호·parameter·DSN·token·프로세스 전체 command line은
저장/반환하지 않는다. activity에는 query_kind/fingerprint와 알려진 고정 SQL의
parameter 없는 template를 제공한다. 기존 진단의 단순 regex는 dollar/E string 등에
충분하지 않으므로 재사용하지 않는다. 식별하지 못한 SQL은 template unavailable로
표시한다. claim EXPLAIN의 Filter/Index Cond는 고정 query+서버 생성 상수만 포함하도록
유지하고, 임의 SQL/사용자 값으로 EXPLAIN할 수 있게 확장하지 않는다. ANALYZE 없음.

report는 기존 `diagnostic-results` 안에 versioned JSON으로 저장한다. 완료·부분 결과
모두 조회 가능하다. 기존 보고서는 legacy schema로 읽고 새 필드가 없으면 unavailable로
표시한다. 새 report/raw는 파일당 최대 32 MiB, raw call 50,000·activity row 50,000·
device tick 16,000을 상한으로 두며 drop 수·raw_truncated를 기록한다. 큰 결과는
download로 회수하고 console 기본은 summary다. 압축 전 크기도 제한한다.

Terminal run status는 report 파일이 실제로 저장되고 symlink가 아니며 크기 상한 안에
있는 경우에만 `report_url`을 제공한다. run 상태 완료와 파일 저장 사이의 짧은 구간에는
status 재조회로 링크가 나타날 때까지 기다린다.

기존 보고서를 자동 삭제하지 않는다. 새 진단 산출물은 디렉터리 전체 1 GiB/1,000개
admission quota를 두고 여유가 부족하면 새 run을 거절한다. 기존 control history의
과거 파일은 보존하고 새 이벤트는 제한된 크기의 segment로 쓴다. 임의 경로·symlink
밖 파일은 읽지 않는다. legacy report도 크기 검사·민감 필드 projection을 거친다.
보고서 실패/디스크 부족이 운영 DB 저장이나 control OFF를 막아서는 안 된다.

run 저장은 측정 종료 뒤 수행해 파일 쓰기를 측정 구간에서 가능한 한 분리한다.
저장 실패면 메모리에서 회수 가능한 결과와 `report_persist_failed`를 반환한다.
summary/verbose/raw는 같은 run의 동결 표본을 사용하고 별도 시각의 재측정을 섞지 않는다.

## 6. 구현 묶음과 검증 종료 조건

1. control mutation 공유화, read-only sampling 이동, snapshot/capabilities, plan 관계를
   한 묶음으로 구현한다. old/new 동일 fixture의 보고서 필드를 대조한다.
2. owned run/cancel/report/history와 CLI adapter를 구현한다. lifecycle·control race를
   해결한 뒤 단순 API wiring/문서 작업은 모델을 다시 낮춰 평가한다.
3. API contract·MODULE_MAP·runtime docs·OPEN_ITEMS·build 세 표식을 같은 배포로 갱신한다.
   이번 문서는 설계일 뿐 현재 API 목록의 구현 완료 표시는 아니다.
4. 묶음 unit 검증: 인증/입력/경로·비밀 보호, 동시 start와 request replay, CLI/API lock,
   TTL/OFF/new session/수동 capture 소유권, cancel/disconnect/shutdown/restart,
   report 실패·quota·legacy 보고서, counter reset·NULL·version/권한 부족·로그 회전,
   plan 부모관계, raw 상한과 같은 측정 window를 검사한다.
5. Linux에서 실제 flock contention과 thread 종료를 검사한다. Windows 가짜 flock
   테스트를 cross-process 검증으로 보고하지 않는다. dedicated PG에서는 snapshot
   read-only·실패 timeout·다른 transaction에 영향 없음만 묶어 검증한다. 이미 통과한
   writer 79개를 변경 근거 없이 다시 한 개씩 사용자에게 요청하지 않는다.
6. NAS 현행 소스 기준 선택 patch·백업·import/route/build 검사 뒤 배포한다. live build,
   인증 capabilities, 기존 20개 보고서 조회, 5초 observe-only run→결과 다운로드→
   capture/master OFF를 API만으로 수행한다. compare는 단위검증 후 명시 사용 시 실행한다.
   결과는 기존 CLI와 같은 fixture/window로 대조하며 다른 시간의 NAS 부하로 동등성을
   주장하지 않는다. 관측 overhead·미계측 범위·저장장치 snapshot 시각을 함께 보고한다.

완료 기준은 사용자가 NAS 명령을 반복하지 않고 **제어→측정→상태 확인→모든 보존된
결과 회수→종료**를 API로 수행할 수 있는 것이다. 데이터가 원천적으로 관측되지 않는
경우는 가용성/이유로 드러내며, 표본·범위 제한을 숨긴 `all information available`은 금지한다.

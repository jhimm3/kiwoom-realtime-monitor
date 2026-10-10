# 현재 모듈 지도

기준: 2.1.0 / 2026-09-23 · [현재 아키텍처](ARCHITECTURE_CURRENT.md) · [문서 안내](docs/README.md)

아래 경로는 현재 있는 파일이다. 파일을 나누는 계획이 아니라 변경 책임자를 찾는 지도다. 상세 단계별 계보는 [이전 지도](docs/archive/2026-09-22/root/MODULE_MAP.md)에 보존했다. 작업 전 [개발 불변 규칙](DEVELOPMENT_GUARDRAILS.md)을 읽는다.

## 앱·시장·저장

2026-10-11 main integration: `diagnostic_control_routes.py` owns the recorder's
account/large request flags and capabilities after the route extraction. `app.py`
passes its existing store to the router for the bounded account-context capture;
the recorder retains capture, persistence and native transaction ownership.

`scripts/nas_scheduled_trace.py` owns the one-shot NAS-local capture start. A plan may
explicitly select account/large opt-ins in addition to the original three flags. It checks
schema4 capability/context contracts before controls change and verifies all selected flags
in the single POST response. Old plans retain schema3. No retry after uncertain acknowledgement;
the process survives SSH disconnect but not NAS reboot. After a root-owned deploy protects active.json,
the scheduler uses the installed read-only kiwoom-nas status interface on PermissionError for the fixed
NAS root; malformed/readable or missing selectors are not silently bypassed. Sixteen scheduler gates
passed and the actual deployed NAS preflight passed without control changes. No permission/sudoers edit.

`scripts/check_causal_capture_capacity.py` owns two fixed offline operator envelopes:
60second smoke and actual3600second capture with a private native SQLite account/lease/VI
fixture, distinct large blocks and streaming durable verification. It never accesses the
operating DB/network or establishes whole causal Replay eligibility. The existing operator
owns their fixed12GiB worker/12GiB disk preflight/14400second deadline and approved server
pause → isolated drain/cleanup → exact server resume; installed configuration is unchanged.
Real Linux host/cgroup9GiB start headroom is mandatory. Installed helpers and the actual
NAS60second gate passed; the3600second gate failed with worker RuntimeError, without OOM.
The exact operating server is restored/healthy. The private checker logs bounded seal/persist/
verify progress, rejects failed/interrupted capture immediately and polls scalars without
copying the full blob/chunk index. Its local durable verifier also pins a terminal manifest for the
existing checksum/bounds readers, then rejects changed manifest bytes; it avoids public status copies
per payload. Eleven related gates passed, including reuse of the same saved files with start/stop forbidden.
The immutable full job timed out after persistence completed; cleanup and exact server restoration passed.
Its2,329,089events were privately preserved. The extracted read-only verify_persisted_capture path verifies
that same saved capture with native checksum/sequence/pair/block readers; NAS verification passed184.287s,
including72,360collector messages/721,080closed deliveries/120large calls. No re-recording or writer ran.
The fixed artifact launcher has no network or operating app/DB mounts, and uses read-only saved files.
The actual recorder retains1MiB/s pacing and.25-5s backoff; paid pacing is now excluded from extra
cooldown. Same short NAS A/B preserved38827 events/45 transactions and reduced persistence24.476%.
An exact local operating overlay exposes account/large API opt-ins and records actual
real/mock account and VI actors. It retains the active app's other routes and market queues;
101 pinned-source API/account/lease/VI/recording regressions passed. A new actual scheduler->ASGI->
native SQLite account/VI/large persistence path passed on that frozen source (24/24 events,
reject/drop0/RAM0). Exact NAS storage105/105 and the same-source native account/v3 replay case passed;
operating6390e0b7de1a5189 is deployed/healthy, PG preserved, Monday60minute five-flag plan armed.
This is not full app-lifespan/causal Replay or2-5x performance acceptance. The standalone NAS starter
uses the updated scheduler artifact while the admitted app release stays immutable.

`central_server/diagnostic_account_context.py` owns the bounded account-context/v2
projection, initial execution ledger/documents, authority aliases and observed next
event sequence. It validates JSON/column scope and event lineage before native replay.
The existing v3 baseline applies rows and transactional sequence RESTART in one seal;
v1 capsules retain their four-table/two-document scope. No writer transaction changes.
PC 수명 경계 재검토(2026-10-11): 기존 AppController/feature controller/실제 writer 책임을
유지하며 추가 관리 계층은 만들지 않는다. 마지막 분봉/가격 실패 signal이 pending을 복원하면
AppController가 `cache_failed`로 close를 보류한다. 닫기 재요청은 같은 정지 writer와 RAM pending을
사용한 명시 재시도이며 정상 타이머·생산자·시작 백업·완료한 TOP20 partial 저장을 다시 실행하지 않는다.
종료 시 마지막 batch만 보관해 늦게 온 옛 실패가 더 최신 COMMIT 값을 덮지 않게 하고,
대체되지 않은 peer와 마지막 batch 자체의 실패만 복원한다. 실제 저장 성공과
queued 결과를 확인한 뒤 보조 자원/화면을 종료한다. MarketCacheWriter/DB의 queue·SQL·transaction은
유지한다. 이는 owner 이동이 아니라 재현으로 확인한 종료 완료 판단의 최소 버그 수정이다.

서버 수명 보완 A+B(2026-10-10): app은 단일 소유 종료 task와 오류 우선순위를 유지하고
DiagnosticRuns parent/trace thread를 timeout=None로 off-loop 대기한다. DiagnosticRuns는
replay child가 실제 끝난 뒤 capture/report/run lease를 정리한다. MockAutomationSupervisor는
소유 종료 task를 재호출에도 기다리고 모든 runner close 결과 뒤 오류를 전파하며 실패 참조를
유지한다. 수동 진단의 bounded 대기와 API/DB/주문 계약은 그대로다. 아래 A 당시 B 미완료
표현은 이 보완으로 갱신됐으며 전체/독립 환경 검증 상태는 CURRENT_STATUS/OPEN_ITEMS를 따른다.

`central_server/market_events.py` owns upper-limit fact decisions and immutable RAM
admission separately from native ACK waits. One feature-owned worker retries frozen
one-fact calls; the existing store owns each connection/transaction. Public observations
and regular-close markers wait for ACK. Close disconnects/drains input and persistence;
fact_collection reports saturation/gaps separately from condition REG. RAM admission
does not provide crash recovery. VI ownership and the shared hub drop policy are unchanged.
Condition signals share metadata/expiry's existing state lock through snapshot, native
ACK and RAM publication; invocation time/session/condition are frozen before lock wait.
Signal queue admission also freezes time/session/condition. Existing workers retain
failed inputs; the same frozen native revision/current is retried under the state lock.
Metadata publishes eligibility after ACK. An owned input task covers retry, actual
native completion and descendant admission; close drains signals before metadata and
keeps waiting after its warning threshold. cohort_collection reports pending/failure/drop
state separately from condition registration. No new service/store or persistence schema.
`MarketEventService` types its store dependency to the six methods it calls through
`MarketEventStore`; the QueryStore aggregate, independent writer transactions and service lifecycle stay intact.

`central_server/observation_frame_recovery.py` owns ephemeral legacy checkpoint
prefix progress and frames-only repair. CandidateMonitor and MockAutomationRunner
retain their execution cursor/state, seed scratch through the old cursor on bounded
safe pages, then atomically save frames/protocol through their existing checkpoint
writer. Shared frame trim functions preserve each consumer's existing horizon policy;
no historical decision/order path is invoked. Cancellation drains owned native repair
work before consumer close returns. NAS deployment and migration cost acceptance are
separate from this correctness gate.

`central_server/database_datasets.py` batches distinct snapshot/metadata identities
inside the existing per-call transaction; shared multirow SQL helpers stay in
`database_market_bars.py`. Duplicate identities, research revision chains, cache
invalidation and large text use the original sequential path; saved_at freshness stays.
`database_market_bars.py` also owns bounded advisory scope requests used by stock/day
and research source/subject locking. Caller sort, serialized keys, hash seeds and
transaction ownership remain; no separate lock service/connection/pool is added.
Native blocking-order/rollback checks and same-recording A/B are required for retention.

`src/kiwoom_monitor/` 기준 경로다.

2026-10-08 NAS operator: `scripts/nas_operator.py` owns fixed command parsing, source/trace admission,
capture fences, isolated jobs, deploy recovery, and opt-in replay maintenance of the approved app
container. `replay --pause-operational` journals and stops only the server, leaves PostgreSQL
running, cleans the isolated job before resuming the same release, and supports explicit recovery;
it reports the expected collection gap without claiming realtime-loss verification. This is a
candidate change until the exact-source NAS gate is run. The installer and
worker live in `scripts/nas_operator_install.py` and `scripts/nas_operator_worker.py`. The
administrator-only `--update` replaces just the installed helper pair while retaining the original
revoke backups and permission surface. `scripts/prepare_nas_operator_update.py` prepares an inactive
operator-only overlay on the verified active NAS app plus an immutable checksum-pinned admin bundle.
PG start/readiness failure diagnostics belong to this existing operator: verify the temporary
job label, preserve bounded startup markers/state before cleanup, and report no raw logs/env.
The 2026-10-10 local49/NAS portable47 gates passed. The administrator reported Linux68 and
helper update success; the installed supervisor hash matches the prepared bundle.
Follow-up diagnostic-only candidate adds fixed-role UID/GID/mode, container/daemon userns
and permission operation/role enums to distinguish the confirmed PGDATA mkdir denial;
local50/NAS portable48 passed, with no permission/ownership repair and no new command surface.
`deploy/synology/check-nas-operator.sh` gate tests Linux fd/ACL behavior in a disposable offline
container. The follow-up identity diagnostic passed administrator Linux69 and is installed.
The disk-parent repair stays in this operator: root-owned generated data parent only,
anchored no-follow fd/fchmod0711, verify applied mode and close fd. Local51/NAS portable49 passed;
candidate95f22a1d0c5913af passed administrator Linux70 and its helper hash is installed;
actual disk replay42e7cb83fe64b0f0a33a7264c0946ddc passed with cleanup/resume.
The partial workload timing gate failed (one113.875ms actor wait); startup repair and
whole-app performance attribution remain separate. See
[the operator contract](docs/NAS_OPERATOR_COMMANDS.md) before changing this boundary.

0B collector 통합 진단은 `central_server/diagnostic_collector_replay.py`가 고정 입력,
실행/종료 수명, 전용 DB 검증·정리를 소유한다. 실제 파서·RAM 집계·저장 주기는
`central_server/realtime_collector.py`와 `minute_bars.py`를 재사용한다. 추가로
`diagnostic_trace.py`가 선택된 store 입력과 collector 원인 사건을 schema-3 capture로 보존한다.
지연 저장 모드는 `diagnostic_trace_ram.py`의 framed RAM block으로 bounded raw queue를 압축하고,
기존 writer가 순서대로 persistence한다. 메모리/event quota 및 packing backlog도 capture 상태로 공개한다.
기본 schema-3 limits는 8 GiB / 5,000,000 events이며, 실제 RSS·장중 전체 부하 수용을 뜻하지 않는다.
Opt-in `large_inputs` selects schema4: the same capture owner freezes one bounded typed input,
`diagnostic_trace_ram.py` encodes it into1MiB blocks, and the existing trace writer publishes
all parts before one logical reference. `diagnostic_trace_payload.py` owns immutable offline
descriptors and checksummed reconstruction. The recorded scheduler validates each selected
input before native execution, shares three decode credits via `diagnostic_replay_runtime.py`,
prepares a bounded frontier and drains actual threads before releasing credit. These local
contracts passed62 regressions. `diagnostic_account_input.py` owns explicit typed account
projection and session HMAC authority aliases; the source repository supplies opaque run IDs.
Account event/recovery, heartbeat/start/stop and VI live/backfill retain actual task actors.
Baselinev3 extends only12 named tables and5 actual sequences; v1/v2 are preserved. Private
v3 clocks serve native lease expiry and real-account updated_at without changing operational
time or transactions. The pure alias-frontier resolver preserves nested valid prefixes and
unbound release identity; native scheduler wiring is implemented. `diagnostic_account_context.py`
owns explicit bounded source prerequisites, read-only MVCC projection, checksummed
recorded context reading and the private v3 capsule application. Capture admission places
one frozen capsule before native producers. The existing v3 lease verifies its v1 parent
then applies and seals within one transaction. Source time/identity projection gaps remain
explicit; the capsule is not a whole-DB baseline. Native expected-failure Replay passed the
78-test PostgreSQL/NAS gate. Missing context/clock still rejects before DB connection.
`diagnostic_replay_contract.py`가 허용 메서드·codec·workload 선택과
collector descendant 제외 계획을 검사한다. `diagnostic_recorded_execution.py`는 명시된
store allowlist를 caller-owned test store에서 실행하며 actor 순서·동시성·replay ID를 기록한다.
`diagnostic_replay_sampling.py`는 opt-in offline replay 중 기존 lease 관리 연결을
독점 사용해 읽기 전용 PostgreSQL activity 표본과 worker CPU/RSS를 수집한다.
`diagnostic_recorded_execution.py`가 native 실행과 observer drain을 소유하고 관리
연결 재사용/복구 전에 종료를 기다린다. `diagnostic_replay_database_cli.py`가 같은
backend/COMMIT 구간에 표본을 연결한다. writer 트랜잭션/공개 API/운영 계측은 바꾸지 않는다.
표본은 SQL 원문 없이 bounded 상태/wait/blocker만 담으며 물리 장치 원인이나 미관측 대기를
확정하지 않는다. 정확한 후보 소스의 PostgreSQL gate와 동일 입력 A/B를 별도로 기록한다.
`diagnostic_replay_comparison.py`는 소유한 replay DB의 native drain 이후 네 결과 표의
내용·revision 연결 비교값을 만든다. 기존 전체 hash/reset 판정은 그대로이며, 저장 시각과
UUID 차이는 명시적으로 분리하고 source 시각·payload·revision 순서는 유지한다.
이 비교는 화면/증분 동기화 등 wall-clock reader 동작이나 전체 기능 동등성을 승인하지 않는다.
collector mode는 0B 원인 사건을 실제 collector loop에 전달하고 그 component의 과거 sink만
제외한다. 로컬 collector-input/v2는 최소 0w·0J·0U 입력과 같은 component의 market_state-only
dataset sink 대체도 지원하며 peer 및 다른 dataset은 유지한다. TOP20 subscriber 자체는
실행하지 않으며 cross-component causal replay는 미지원이다. NAS 검증·활성화는 별도 gate다.
로컬 `diagnostic_top20_input.py`는 `ka00198 qry_tp=5` freshness/slot/retry 판정만 재생한다.
`diagnostic_top20_flow_input.py`는 TOP20 최초 편입 `ka10045` 입력에서 실제 investor-flow
ingestor와 완료 marker까지 전용 replay DB 안에서 실행하며 native descendant를 중복 실행하지 않는다.
일반 수급 fan-out, broker scheduling, 전체 TOP20 subscriber/준비 작업은 지원하지 않는다.
`diagnostic_rest_input.py`는 top20_inputs opt-in의 broker 논리 요청/전송/cache·ingest 원인 및 catalog
입력 pair를 기록·검증하고 명시 lane의 offline tape client를 제공한다. `diagnostic_top20_lifecycle_input.py`는
0s·구독 intent/ACK/READY·hub control·gap 입력 pair와 descendant preflight를 검증하고, 현재 native 구독 intent에
일치하는 결과만 offline tape로 공급한다. 둘 다 native broker/collector lifecycle을 그대로 실행하지 않으며
full TOP20 executor를 열지 않는다. `diagnostic_replay_runtime.py`는 opt-in 실행의 Task와 실제 executor
작업을 제출 시점부터 추적하고 실제 종료 전 DB reset/release를 차단한다. `diagnostic_top20_outbox.py`는
run 소유 native 파일 outbox의 검증된 seed와 복구를 담당한다. 둘은 로컬 구현·native 회귀 142건을
마쳤다. NAS 임시 RAM-backed·network-isolated PostgreSQL acceptance 13건이 skipped=0으로 통과했고
취소 thread 종료, native outbox ACK 재시도, DB/file baseline 공동 복구를 확인했다. T4 v1 gate는 T3 13건 뒤
fixture setup timeout으로 실패했으나, disposable PostgreSQL gate에만 180초를 지정한 비활성 v2 candidate
`2026.10.07-top20-session-v2-57de01203aa5af84`의 후속 acceptance 15건이 skipped=0으로 통과했다.
두 baseline 보존 및 반복 복구, TOP20 단독/제외와 peer writer 유지, descendant 대체, drain 및 DB/outbox
복구를 controlled fixture에서 검증했다. 보고서는 `source_state_equivalent=false`이며 실제 장중 상태 등가,
입력 coverage, 성능을 검증하지 않는다. 로그의 stale ranking `recording_gap` 두 건은 고정 시각 fixture가
stale해져 해당 회차 저장을 건너뛴 것이며 acceptance 실패나 DB 입력 유실은 아니다. 운영 release와 두
운영 container는 변경하지 않았다.
`diagnostic_top20_seed.py`는 native TOP20 service의 의미 상태를 명시 목록으로만 검사하고, controlled
cold fixture의 baseline/input/mask/component/source-clock identity를 window frontier에 결합한다.
lock·Task·cache·pending work가 비었는지 확인하지만 RAM을 복원하거나 DB를 restore하지 않으며 execution
gate를 열지 않는다. REST/catalog 및 lifecycle 입력 tape와 native service lifecycle runner가 연결됐다.
T4 controlled PostgreSQL acceptance는 통과했으며, 장중 상태 등가 및 성능 결론은 유효한 실제 capture replay 전까지 보류한다.
`postgres_access.py`는 replay operation ID와 owner·actor·component를 DB call에
전달한다. `diagnostic_replay_baseline.py`는 고정 replay DB의 역할·소유권·run lock, baseline v1 14개
허용 테이블/sequence 및 별도 opt-in v2 15개 테이블(`central_api_query_cache` 포함) snapshot,
atomic restore와 native connection drain을 소유한다. 로컬 offline replay `run`도 v2를 선택할 수 있다.
v3 native recorded runner는 계좌7종/VI를 명시적으로 실행하며 검증한 context capsule,
동일 owner alias frontier, 봉인된 context/token hash와 실제 경과를 반영하는 lease-owned
source clock을 요구한다. 예상 실패는 정확한 class/code로만 수락하고 결과 불일치는 drain한다.
offline CLI의 v3 seal/run 선택은 녹화 context와 명시 window/owner frontier를 preflight한다.
`scripts/nas_operator.py`는 schema4 block bundle을 bounded stdlib로 검사·등록하고 등록 후
파일 hash를 재검사한다. v3 baseline과 trace context 연결 및 worker source-origin 전달을
검사한다. root helper 반영, public 실행 선택과 NAS 녹화 운영 배포는 별도 단계다.
v2 query-cache store 호출은 lease의 source clock을 사용하며 v1에서 거부한다. `rest_broker.py`의 선택적
source wall clock과 `database_query_cache.py`의
per-store clock hook은 v2 lease에서만 연결되고 운영 기본값에는 변화가 없다.
비활성 NAS candidate에서 임시 RAM-backed·network-isolated PostgreSQL baseline gate 7건과 새 native
run gate 3건, 총 10건이 skipped=0으로 통과했다. 반복 run의 source TTL과 collector/cache 공통 clock,
exclusion의 과거 cache 결과 비주입, ACK-loss 뒤 drain 및 baseline 복구를 확인했다. 성능이나 장중
상태 동등성은 입증하지 않았다. 후속 service clock 감사의 세 누수(신고가 helper 날짜, 분봉 날짜 fallback,
membership 공개시각)는 로컬 수정과 신고가 12/ingestor 13/TOP20 67 단위 회귀 및 source-date 재현으로
통과했다. 전체 TOP20 runner와 그 안에서 시간 의존 TR에 같은 clock을 바인딩하는 단계는 남아 있다.
`diagnostic_replay_database_cli.py`는 offline provisioning/status/seal/restore와 bounded
schema-2 recorded-operation replay를 제공한다. 실행은 기존에 봉인한 dedicated baseline 아래서
수행하고 종료 후 기준 상태를 복구한다. 원본 operation ID와 replay operation ID를 실제 DB-call
관측 ID에 연결하며, 이 연결은 workload가 발생시킨 연결만 관측한다. `news_article` 및
`theme_metadata`의 history projection처럼 baseline allowlist 밖의 부수 쓰기가 필요한 입력은
사전 거부한다. `scripts/check_recorded_replay_baseline.py --execution-gates`는 NAS 운영자가
기존 sealed DB에서 반복 재생·workload 제외·실패 후 정리를 검사한다. 공개 선택/비교 API와
장중 원본 동등성은 아직 없으며 운영 실행 경로에는 연결되지 않았다.
역할과 검증 경계는
[반복 부하 실험 계약](docs/RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)을 따른다.

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 조립·프로세스 | `bootstrap.py`, `news_process.py`, `journal_process.py`, `research_process.py` | 실행모드·자식 프로세스·작업 수명 |
| 사용자 데이터 | `infrastructure/app_paths.py` | 설치/개발/지정 data 경로 |
| 순위 | `application/ranking_schedule.py`, `application/ranking_execution.py`, `central_server/autonomous_top20.py` | 순위 시각·재시도·실행·NAS 독립 수집 |
| REST | `central_server/rest_broker.py`, `infrastructure/kiwoom_rest/client.py`, `infrastructure/kiwoom_rest/remote_client.py` | 중앙 우선순위·직접/원격 경계 |
| 실시간 | `central_server/realtime_collector.py`, `infrastructure/kiwoom_rest/realtime.py`, `infrastructure/kiwoom_rest/realtime_worker.py`, `infrastructure/kiwoom_rest/central_realtime_worker.py` | 중앙/직접 구독·REG·장애전환, 0s 장운영(215) 원본 이벤트 파싱·중계 |
| 세션 | `application/market_session_schedule.py`, `application/realtime_subscription.py`, `central_server/autonomous_top20.py` | 거래일별 venue/phase·구독 대상, NAS 장후 보완은 저장된 0s 거래일 증거가 있을 때만 예약. 공식 연간 휴장 달력 자동 동기화는 미구현 |
| 봉·관측 | `central_server/minute_bars.py`, `central_server/market_ingest.py`, `central_server/market_observations.py` | 1초/1분 집계·TR 적재·revision 의미. `MarketDataIngestor`는 봉 교체·관측 metadata 조회·dataset/문서 저장의 7개 메서드 `MarketIngestStore` 계약을 사용한다. 실제 일봉 changed key 또는 저장 결과가 불확실할 때만 TOP20 daily/high 준비 상태를 code/market 범위로 재검증한다. native transaction과 broker 적재 호출 깊이는 유지한다. |
| TOP20 | `application/top20_trade_value_collector.py`, `presentation/top20_trade_value.py`, `central_server/autonomous_top20.py`, `central_server/program_snapshot_writer.py` | 코호트·지수·차트. NAS 서비스의 `AutonomousTop20Store`는 직접 호출과 writer에 전달하는 저장을 합친 10개 메서드 계약이다. `ProgramSnapshotWriter`는 단일 메서드 `ProgramSnapshotStore`로 0W pending·실제 저장 task·실패 병합·drain을 소유한다. 순위·구독·준비·저장 주기와 전체 종료는 TOP20 서비스가 조정하며 producer stop→실제 save drain→pending final flush를 유지한다. 호출자 취소가 DB thread 소유권을 끊지 않는다. |
| 로컬 쓰기 | `infrastructure/persistence/market_cache_writer.py`, `infrastructure/persistence/minute_bar_repository.py` | 비동기 직렬 쓰기·봉 저장 |
| 중앙 서버 조립·API | `central_server/app.py`, `central_server/database.py`, `central_server/central_schema.py` | `app.py`가 QueryStore/backend, 서비스, lifespan과 나머지 API를 조립한다. 같은 파일의 명시적 시작/종료 경로가 현재 동적 owner를 사용하며 부분 시작·본문 실패·반복 취소에도 소유 종료 task를 기다린다. 첫 close 실패는 원래 오류와 안전한 단계 기록을 보존하고 후속 broker/vault/store 해제를 중단한다. 각 실제 worker/drain은 기존 서비스 책임이다. 진단 parent/child·trace 실제 종료 대기와 supervisor의 종료 task/실패 전달도 보완했으며 최종 전체 검증의 과거뉴스 폴더 이동 오류는 OPEN_ITEMS에 남아 있다. 뉴스 검색·AI 분석·이력 조회·과거 archive·외부 작업·콘텐츠 동기화·연구 조회·모의 자동매매 제어·시장 스냅샷/통계 라우터에는 app이 소유하는 service/store/reader와 기존 인증 dependency를 등록한다. |
| 클라이언트 실시간 WebSocket | `central_server/realtime_routes.py`, `central_server/realtime_hub.py`, `central_server/realtime_collector.py`, `central_server/app.py` | 인증 header/query 우선순위·ready/pong·구독 응답·초기 DB/RAM 값·central_ready/상류 승인·누락 알림·sender 취소/구독자 정리 정책을 소유한다. app의 같은 hub/token 검사/collector와 단일 메서드 snapshot reader를 주입한다. 실제 상류 연결·집계·DB 저장·서버 수명은 기존 owner에 유지하고 새 state/task 계층이나 전달 wrapper를 추가하지 않는다. |
| 진단 실행·보고서 API | `central_server/diagnostic_run_routes.py`, `central_server/app.py`, `central_server/diagnostic_runs.py`, `central_server/diagnostic_sampling.py` | snapshot/run 시작·상태·취소/보고서 목록·상세/이력 7개 HTTP 정책·요청 모델·인증·오류 변환·summary/raw 필터를 소유한다. 같은 require-runs/DiagnosticRuns·logger/config/build 값을 전달한다. native sampler/worker/실행·취소·보고서 파일 읽기·이력·페이지·app 조립/수명은 기존 owner에 유지한다. 오류 우선순위·revision/session/전체 replay 옵션 전달과 명세/등록 순서를 보존하며 새 state/task/수명 wrapper나 호출 깊이를 추가하지 않는다. |
| 진단 스위치·trace API | `central_server/diagnostic_control_routes.py`, `central_server/app.py`, `central_server/diagnostic_workloads.py`, `central_server/diagnostic_trace.py` | capabilities/control/trace 시작·상태·종료·manifest·chunk 7개의 요청 모델/인증/검증/오류 변환을 소유한다. app의 같은 run 객체·require-runs·workload reader와 config/build 값을 전달한다. native CAS/session/instance/TTL·이력·busy 4상태·실패 시 조건부 rollback·capture 옵션과 chunk media type을 유지한다. control 파일/trace worker/저장/종료와 app run 조립/수명은 기존 경계에 남으며 새 상태·수명 wrapper나 호출 깊이를 추가하지 않는다. |
| 진단 조회 API | `central_server/diagnostic_read_routes.py`, `central_server/app.py`, `central_server/diagnostic_metrics.py`, `central_server/diagnostic_workloads.py` | resources/workloads/market-bar-saves/writers/db-calls/news plan/read-only analyze 7개의 HTTP 조회 정책·한도·오류 변환을 소유한다. storage 두 메서드의 read 계약과 명시적 optional PostgreSQL 기능을 같은 store에서 읽는다. 동일 workload reader를 control update/run sampling도 사용하며 후보는 현재값 getter로 읽는다. metrics/registry/control 파일 평가·native DB/transaction·run 조립/수명은 기존 경계에 유지한다. 기존 명세/등록 순서·501 우선순위·1MiB/BODY/RULE 경계를 유지하고 후보 getter만 한 호출 추가한다. |
| 운영 설정 API·저장/적용 상태 | `central_server/operational_settings_routes.py`, `central_server/app.py`, `central_server/candidate_monitor.py` | 운영 설정 GET/PUT의 요청 모델·URL/조건/정합성 검증·native 저장→서비스 적용→후보 close/공개/start→applied revision 정책을 소유한다. per-app OperationalSettingsState는 기존 settings dict·applied revision·후보 pointer·lock 슬롯을 담으며 작업/수명 메서드는 없다. app lifespan·진단·연구가 같은 현재 후보를 사용하고 공개 app.state는 앱 callback으로 갱신한다. 저장 실패/적용 실패/같은 revision 재시도 의미, 실제 native DB/서비스/작업 수명과 등록 순서는 보존한다. `OperationalSettingsStore`는 설정 저장과 후보 bootstrap/checkpoint/평가·safe-prefix 복구에 필요한 7개 메서드를 요구하며 같은 native backend를 후보에 전달한다. GET/PUT 호출 깊이는 유지하고 candidate publication만 callback 한 호출 추가된다. |
| 시장 역할·계좌 설정 API | `central_server/account_settings_routes.py`, `central_server/app.py`, `central_server/database_account_settings.py`, `central_server/real_runtime.py`, `central_server/mock_runtime.py` | 세 HTTP endpoint의 인증·키 집합·scope·오류 응답과 저장/적용 revision·monitor 상태 조립을 라우터가 소유한다. 두 native 조회 메서드만 필요한 `AccountSettingsReadStore`를 쓰며 역할 변경은 app이 선택한 기존 RealCredentialOwner에 그대로 위임한다. 계좌 검증·revision CAS·binding fencing·트랜잭션/rollback·실제 작업 drain/복구·owner 수명은 기존 DB/runtime/app에 유지한다. 등록 위치와 요청 호출 깊이는 동일하다. |
| 모의 주문·취소·조회 API | `central_server/mock_order_routes.py`, `central_server/app.py`, `central_server/execution_runtime.py`, `infrastructure/persistence/execution_repository.py` | v1/v2 주문·조회·취소와 계좌별 실행 사건 7개 API의 모델/인증/오류 변환/응답 formatter를 소유한다. app의 공유 AccountTarget·selected_mock와 현재 gateway getter를 전달한다. 요청 시작 시 선택한 gateway를 응답까지 유지하고 계좌/ref/revision/run fence·멱등 namespace·unknown·취소 오류를 보존한다. 계좌 선택/publication/lifespan은 app/owner, 원장/transaction/lease/owned command 종료는 native gateway/runtime/repository에 유지한다. 두 router의 원래 등록 위치/명세/순서는 유지하며 legacy getter 한 호출만 추가된다. |
| 계좌 목록·계좌 query API | `central_server/account_query_routes.py`, `central_server/app.py`, `central_server/account_query.py`, `central_server/real_runtime.py`, `central_server/mock_runtime.py` | 목록 GET v3와 query POST v2/v3의 HTTP 모델·인증·오류 정제·context 일치 정책을 소유한다. 목록은 단일 `list_credential_profiles` 계약만 사용한다. 모의 주문과 같은 AccountTarget 타입과 같은 selected_account를 전달하며 선택/publication/수명은 app/기존 owner에 유지한다. legacy binding/session은 시작·키 교체 후 현재값 getter로 요청마다 읽는다. native cursor·본문/revision fencing·만료·완료·busy/closed·후착 응답·owned task drain·DB/트랜잭션을 변경하지 않는다. 두 router 등록 위치와 전체 명세/순서를 유지한다. 새 상태·수명 계층은 없으며 legacy 경로에는 현재값 getter 한 호출만 추가된다. |
| 일반 키움 조회·저장 응답 재사용 API | `central_server/market_query_routes.py`, `central_server/app.py`, `central_server/rest_broker.py`, `central_server/market_read_routes.py` | 일반 query의 요청 모델·인증·계좌 복구 API 우회 차단, 최신 기초/NXT 문서·완료 봉의 archive-first 응답, continuation 및 재연결 gate·오류 응답을 함께 소유한다. 세 메서드의 `MarketQueryStore` 계약과 같은 app-selected broker/collector를 사용한다. 기존 coverage 완료 predicate, DB source tag·native 저장/트랜잭션·broker scheduling·수명은 유지하고 두 app helper alias를 보존한다. 선택적 HTTP 의존성은 factory 안에서 import한다. |
| 시장 스냅샷·TOP20 통계 조회 API | `central_server/market_dataset_read_routes.py`, `central_server/database_datasets.py`, `central_server/database_top20_statistics.py`, `central_server/app.py` | 허용 kind/subject/limit 검증, TOP20 membership의 단건 prefer_live 선택·저장소 fallback, 통계 날짜 파싱/최대 367일 범위를 라우터가 소유한다. 두 메서드의 `MarketDatasetReadStore` 계약과 app이 구성 시 선택한 같은 TOP20 서비스만 사용한다. QueryStore 연결·통계 GET의 과거 날짜 캐시 저장 트랜잭션·무효화 및 TOP20 생성/시작/종료는 기존 app/DB/service에 유지한다. |
| 시장 봉·시총·coverage·이벤트 조회 API | `central_server/market_read_routes.py`, `central_server/app.py` | 봉/시총/거래대금 비교/coverage/외부 봉 일곱 API는 여섯 메서드의 `MarketReadStore`를 쓰고 현재 collector를 매 요청 app.state getter로 읽는다. 시장 이벤트 API는 별도 세 메서드의 `MarketEventReadStore`로 VI·코호트·상한가 이력, 비활성 코호트 포함 현재값, 조건검색 문서와 app이 구성한 기존 서비스의 현재 상태를 읽는다. 세 router 등록 위치로 기존 API 순서를 유지한다. 수집기/store/service 생성·시작/종료·RAM/DB 저장 의미·SOR·cutoff·DB source tag는 기존 경계에 유지한다. app helper alias와 FastAPI의 선택적 import도 보존한다. |
| 모의 자동매매 후보·명세 게시/조회 API | `central_server/mock_publication_routes.py`, `infrastructure/persistence/forward_evaluation_repository.py`, `application/mock_automation_specification.py`, `central_server/app.py` | 두 strict 요청 모델, scope/과학적 구현 hash/크기 검증, 현재 mock binding 선택과 bounded shadow 근거 검색, HTTP 오류 변환을 라우터가 소유한다. binding/shadow 조회 두 메서드의 `MockPublicationEvidenceStore`와 기존 실제 repository를 주입한다. repository는 DB 참조만 보유하며 app에서 같은 store로 조립한다. immutable publication·READY 검증·문서 저장/transaction과 admission/주문/제어는 기존 경계에 유지한다. 게시가 주문을 시작하지 않는다. |
| 모의 자동매매 제어 API | `central_server/mock_automation_routes.py`, `central_server/mock_automation_supervisor.py`, `central_server/app.py` | 상태·시작·중지·재개의 strict 요청 모델, 미설정 503, credential/supervisor 오류 변환과 인자 전달을 라우터가 소유한다. app이 구성 시 선택한 같은 supervisor를 직접 사용한다. 생성·시작/종료, admission·revision·복구·주문 차단·runner 및 DB 소유권은 기존 app/supervisor/실행 경계에 유지한다. 후보/spec 게시 API는 mock_publication_routes.py의 별도 책임이다. |
| 연구 관측자료·후보 조회 API | `central_server/research_read_routes.py`, `central_server/database_research_export.py`, `central_server/database_shadow_state.py`, `central_server/app.py` | 고정 export 생성/페이지의 시간대·cursor·watermark 일치 검증과 후보 만료/quality HTTP 응답을 소유한다. 3개 메서드의 `ResearchReadStore` 계약을 쓰며 export membership 저장 트랜잭션은 기존 DB에 유지한다. 후보 생성기는 app이 시작/종료·운영 설정 교체를 소유하고, 라우터는 DB await 후 callback으로 현재 생성기를 한 번 읽어 초기 객체에 고정되지 않는다. |
| 콘텐츠 동기화·테마 이력 API | `central_server/content_routes.py`, `central_server/database_documents.py`, `central_server/app.py` | 문서 조회·부분 upsert·테마 전체 교체·테마 이력 조회의 HTTP 모델/허용 collection/인증/일지 계좌 scope·삭제 상태·뉴스 link key 검증을 라우터가 함께 소유한다. 5개 메서드의 `ContentStore` 계약으로 app이 선택한 같은 store를 사용하며 연결·트랜잭션/rollback·동일 문서 updated_at·뉴스/테마 revision·post-commit wake-up은 기존 DB에 유지한다. 요청 처리에 전달 전용 계층은 추가하지 않는다. |
| 뉴스 검색·AI 분석 API | `central_server/news_service_routes.py`, `central_server/news_service.py`, `central_server/ai_service.py`, `central_server/app.py` | 검색·저장 페이지·AI 분석의 요청 모델/인증/오류 응답은 라우터가 소유한다. `CentralAIService`는 실제 호출하는 6개 저장 메서드의 `CentralAIStore` 계약만 참조하고, 검색과 분석 router를 각각 원래 위치에 등록해 archive 조회를 포함한 API 순서를 유지한다. 서비스 생성·자격증명·설정 갱신·시작/종료와 기존 저장/usage/revision 경계는 app과 실제 서비스에 유지한다. |
| 뉴스 조회 API | `central_server/news_read_routes.py`, `central_server/app.py` | 인증된 history/sources/market-feed 요청 검증과 응답 조립. 4개 조회 메서드만 필요한 `NewsReadStore` Protocol을 쓰고 실제 SQLite/PostgreSQL store 수명은 app이 소유한다. |
| 과거 뉴스 archive 조회 API | `central_server/historical_news_archive_routes.py`, `central_server/historical_news_archive.py`, `central_server/app.py` | 라우터가 검색·정확 기사 ID 조회의 인증/요청 검증/오류 응답과 요청 시점 diagnostic pause를 소유한다. reader 설정·봉인 확인·생성과 health/capabilities 공개는 app에 유지한다. 기존 reader의 읽기 전용 연결과 cursor/dataset 의미를 그대로 사용한다. |
| 과거 뉴스 외부 처리 API | `central_server/historical_news_processing_routes.py`, `central_server/database_historical_news.py`, `central_server/database_news_revisions.py`, `central_server/app.py` | 작업 접수·완료·시황 batch의 요청 모델/인증/검증/오류 응답을 라우터가 소유한다. 5개 메서드의 `HistoricalNewsProcessingStore` 계약만 사용하고 store 생성·수명은 app, 임대/attempt 소유권·revision·트랜잭션/rollback·완료 후 wake-up은 기존 DB 구현에 유지한다. |
| 중앙 서버 로그 | `central_server/server_logging.py` | 파일·콘솔 로그를 KST로 표시하고 날짜 회전을 한국 자정 기준으로 유지 |
| 외부시장 봉 DB | `central_server/database_external_market.py`, `central_server/external_market_collector.py`, `central_server/database.py` | SQLite/PostgreSQL 외부시장 봉 저장·조회 실제 구현. Yahoo collector는 봉 저장과 roll/collection state 문서 조회·upsert만 요구하는 `ExternalMarketCollectorStore`를 사용하고, 조회 API는 `MarketReadStore`를 사용한다. QueryStore aggregate와 backend 선택은 `database.py`에 유지한다. |
| 국내 시장 봉 DB | `central_server/database_market_bars.py`, `central_server/database_observation_writes.py`, `central_server/database.py`, `central_server/postgres_access.py` | SQLite/PostgreSQL 분봉·초봉·5분봉·일봉 저장·조회 구현과 revision/metadata writer helper. 분봉 metadata는 `(subject, trading_date+minute)`로 각 종목에 연결한다. 일봉 UPSERT는 commit이 확인된 changed key만 반환해 coverage freshness 입력으로 쓴다. QueryStore 계약·store 조립·기존 호출 연결은 유지하고, 공통 PostgreSQL wait probe는 뉴스 claim과 공유. 로컬 회귀 및 NAS 전용 PostgreSQL 일봉 changed-key/no-op/중복·metadata/rollback 검사 3/3 통과 |
| 시장 관측 메타데이터 DB | `central_server/database_market_metadata.py`, `central_server/database_observation_writes.py`, `central_server/database.py`, `domain/market_data_contract.py` | SQLite/PostgreSQL metadata 저장·단건·범위 조회. QueryStore/API 계약과 native transaction을 유지하며 `CoverageObservation`을 하위 value contract로 둔다. 로컬 회귀·정적 연결 및 NAS 전용 PostgreSQL gate 3/3 통과 |
| REST 응답 캐시 DB | `central_server/database_query_cache.py`, `central_server/database.py` | SQLite/PostgreSQL cache read/write와 `StoredQuery` 실제 구현. `QueryCacheStore`는 REST broker가 쓰는 두 메서드 계약이며 `QueryStore` aggregate·store 조립은 유지. NAS PostgreSQL·broker·SQLite gate 5/5 및 SQLite-backed API ASGI 연결 1/1 통과 |
| 저장소 진단 DB | `central_server/database_storage_diagnostics.py`, `central_server/database.py` | SQLite/PostgreSQL 저장 크기·분류 조회와 공통 분류 구현. 인증된 `/api/v1/diagnostics/resources` 응답 및 PC 리소스 화면의 기존 연결 유지 |
| 실시간 최신값 DB | `central_server/database_realtime_snapshot.py`, `central_server/database_codec.py`, `central_server/database.py` | SQLite/PostgreSQL 최신 스냅샷 저장·조회와 영속 0B 시총 projection. `realtime_collector.py`는 snapshot·minute/second bar 저장, finalization, live bar 조회, document upsert에 필요한 7개 메서드의 `RealtimeCollectorStore` 계약을 사용한다. 직렬 flush·실패 복구와 app.py WebSocket/API 연결을 유지하며, 공통 JSON decoder는 codec에서 제공 |
| 시장 이벤트 DB | `central_server/database_market_events.py`, `central_server/database_codec.py`, `central_server/database.py` | SQLite/PostgreSQL VI·hot cohort·상한가 revision/current/history 저장·조회 구현. QueryStore 계약·store 조립과 기존 transaction/lock 소유권은 `database.py` 및 호출자에 유지 |
| 연구 관측 export DB | `central_server/database_research_export.py`, `central_server/database_codec.py`, `central_server/database.py` | 고정 revision membership manifest 생성과 cursor page 조회의 SQLite/PostgreSQL 구현. 인증 `/api/v1/research/observations`와 PC `CentralContentClient` 경로, 별도 writer/reader transaction 경계를 유지 |
| 문서·테마 이력 DB | `central_server/database_documents.py`, `central_server/database.py` | SQLite/PostgreSQL 문서 upsert·replace·조회와 테마 snapshot, 뉴스 기사 revision/BODY job의 동일 transaction 후처리. QueryStore 계약과 store 조립은 `database.py`에 유지 |
| 뉴스 작업 큐·요청 예산 DB | `central_server/database_news_jobs.py`, `central_server/database_news_job_writes.py`, `central_server/database_documents.py`, `central_server/database.py` | SQLite/PostgreSQL AI 작업 enqueue·claim·finish·retry, 요청 예산 claim/count, wake-up callback의 저장 구현. 문서 cursor transaction이 공유하는 job insert helper는 하위 공통 모듈에 두고 기존 import alias와 post-commit wake-up 순서를 유지. 로컬 회귀 및 NAS 전용 PostgreSQL gate 10/10 통과 |
| 뉴스 revision DB | `central_server/database_news_revisions.py`, `central_server/database_news_jobs.py`, `central_server/database.py`, `domain/news_observation.py` | SQLite/PostgreSQL BODY·AI·event revision 저장과 뉴스 이력 조회 구현. caller-owned cursor helper와 job wake-up 연결은 하위 뉴스 job writer를 사용하며 QueryStore 조립·기존 import alias는 유지. 로컬 회귀 135건·NAS 전용 PostgreSQL gate 9/9 통과 |
| 뉴스 소스 페이지·진행상태 DB | `central_server/database_news_sources.py`, `central_server/database_news_job_writes.py`, `central_server/database.py` | SQLite/PostgreSQL source cursor/page, source diagnostics, stored market feed. `save_news_source_page`와 historical batch가 공유 caller-owned transaction, 기사 revision/target/job/cursor atomicity, table lock 및 commit 후 wake-up을 보존한다. 로컬 회귀 76건·NAS 전용 PostgreSQL gate 5/5 통과 |
| 과거 뉴스 외부 처리·시황 batch DB | `central_server/database_historical_news.py`, `central_server/database_news_sources.py`, `central_server/database_news_revisions.py`, `central_server/database.py` | 외부 BODY/RULE lease claim/completion 및 historical market batch. QueryStore 조립·호환 alias는 root에 유지하고 ownership fence, `SKIP LOCKED`, caller-owned transaction 및 commit 후 wake-up을 보존한다. 로컬 회귀 123건·NAS 전용 PostgreSQL gate 5/5 통과 |
| 계좌 설정·복구 DB | `central_server/database_account_settings.py`, `central_server/database.py` | SQLite/PostgreSQL account settings·market profile CAS, fenced real account event/recovery 저장·조회 구현. QueryStore 계약과 store 조립은 `database.py`에 유지하며 credential activation은 기존 cursor transaction에서 domain helper를 빌림 |
| 계좌 identity·binding DB | `central_server/database_account_identity.py`, `central_server/database.py` | SQLite/PostgreSQL 계좌 identity 등록, binding revision, scope alias 검증·조회 구현. QueryStore 계약과 store 조립은 `database.py`에 유지하며 credential activation의 기존 transaction 흐름을 보존 |
| 자격증명 프로필·활성화 DB | `central_server/database_credentials.py`, `domain/credential_contract.py`, `central_server/database.py` | 프로필 생성·등록·이름·보관 및 activation receipt의 SQLite/PostgreSQL 구현. activation은 identity binding과 account settings helper를 기존 cursor transaction에서 호출하고 암호화 vault 파일 commit 경계는 credential store에 둠 |
| 실행 원장·mock 제어·lease DB | `central_server/database_execution.py`, `central_server/database.py` | SQLite/PostgreSQL intent/event/account snapshot, mock control CAS, runtime lease 저장·조회. ExecutionRepository·ForwardEvaluationRepository와 기존 mock 주문/이벤트 API 연결을 유지하고 소유권 fence, 독립 트랜잭션과 native 계측을 보존. 전용 PostgreSQL fence·ledger·control CAS 검사 3건 통과 |
| Shadow 상태·평가 DB | `central_server/database_shadow_state.py`, `central_server/shadow_checkpoint.py`, `central_server/database.py` | SQLite/PostgreSQL checkpoint 상태, shadow 평가·candidate event 저장과 cursor page 조회. `CandidateMonitor`·인증 API 연결 및 기존 caller-owned transaction을 유지하고, normalized frame format/schema/DML helper는 `shadow_checkpoint.py`에 둠. 전용 PostgreSQL gate 7건 통과 |
| dataset snapshot·관측 SQL DB | `central_server/database_datasets.py`, `central_server/database_observation_writes.py`, `central_server/database.py`, `central_server/postgres_access.py` | SQLite/PostgreSQL dataset snapshot 저장·조회 구현과 caller-owned cursor의 metadata/revision SQL을 분리했다. root QueryStore·기존 import alias 및 writer transaction은 유지한다. NAS 전용 PostgreSQL gate 9/9 통과 |
| 관측 증분 전달·bootstrap DB | `central_server/database_observation_readers.py`, `central_server/database.py` | 기존 native 연결에서 sequence 상한과 유한 잠금 cohort로 safe page를 제공한다. store별 RAM epoch/fencing과 단일 snapshot bootstrap API를 소유하며 writer SQL/COMMIT·raw history·연구 export는 유지한다. 두 consumer의 bootstrap/legacy frames-only 복구까지 NAS 55건·로컬 36건 gate 통과. concurrent reader/lock-transfer와 비용 검증 후 배포하며 현재 운영 미배포다. |
| PostgreSQL 공통 관측 pilot | `central_server/postgres_access.py`, `central_server/database.py:PostgresQueryStore`, `central_server/diagnostic_metrics.py`, `central_server/diagnostic_writer_registry.py` | 기존 호출별 연결과 transaction 경계를 유지한 명시적 writer 계측 및 query-cache·document-collection·market-bar·observation-revision·shadow·dataset snapshot·market metadata·news READ grouping. writer kind는 활성 호출 경계를 따라 점진 이관한다. 전체 store coverage나 pool 도입을 뜻하지 않는다. 상세 writer/kind와 검증 범위는 `docs/COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md` 참조 |
| PostgreSQL pilot 전용 통합검사 실행 | `scripts/run_postgres_access_integration.py`, `tests/integration/test_postgres_access_postgres.py` | 서버 컨테이너의 운영 DSN으로 읽기 전용 preflight 후 DB명만 전용 진단 DB로 바꿔 pilot 통합검사에 전달. URL·자격증명을 파일에 저장하지 않음 |
| NAS 운영 소스 release·반복 검사 | `scripts/nas_source_runtime.py`, `deploy/synology/docker-compose.source.yml`, `deploy/synology/source-runtime.sh` | 선택 가능한 읽기 전용 소스 마운트. 전체 release 게시·검사 후 수동 재시작하며 실행 경로를 release에 고정한다. 동일 마운트의 후보 release에서 기존 전용 PostgreSQL 검사 실행기를 사용한다. 기본 이미지 배포는 복귀 경로로 유지 |
| NAS 작업·writer 진단 | `central_server/diagnostic_workloads.py`, `central_server/diagnostic_sampling.py`, `central_server/diagnostic_runs.py`, `central_server/diagnostic_replay.py`, `central_server/diagnostic_metrics.py`, `central_server/diagnostic_writer_registry.py`, `scripts/nas_workload_diagnostic.py` | 공통 master/자식 제어 파일·잠금, CLI/API 공유 표본, API가 소유하는 단일 run·취소·보고서. 부분 재생은 빈 뉴스 claim/inline shadow와 `query_minute`의 명시 합성 시나리오 및 원본 call별 카운터를 사용하는 `recorded_counts` adapter를 지원한다. 수치 누락·지원 밖 shape·실측 mismatch는 실행 전 또는 보고서에서 실패로 표시한다. 호출별 subject와 중복값은 synthetic이므로 원래 key overlap·payload·WAL/lock 경쟁의 동등 재현은 보장하지 않는다. 실제 작업 drain ACK와 전수 writer 감사는 `docs/KIWOOM_STORAGE_WRITE_AUDIT.md` 진행 중 |
| 앱 작업·종료 수명 | `presentation/app_controller.py`, `presentation/main_window.py`, 기존 `*worker_controller.py` | AppController가 12개 feature controller, optional cache/snapshot writer, 동적 TOP20 NAS worker와 종료 단계·drain/wait를 소유한다. API runtime 교체 상태·worker 대기·factory 적용과 초기 순위 시작도 조정한다. 가격·당일고가·시가총액·분봉·지수·비교 pending, 저장 timer 및 실패 복구도 AppController가 맡는다. MainWindow는 UI effect callback, API 결과 표시, close accept/ignore를 연결하고 feature 결과 signal을 기존 화면 처리기로 직접 받는다. 순위 응답·세션 일정·구독·secondary followup은 AppController가 기존 policy coordinator를 호출해 조정하고, MainWindow는 표 적용과 data/UI callback을 제공한다. |
| 화면 | `presentation/main_window.py`, `presentation/main_table_formatting.py` | 위젯·표·사용자 입력·표시. AppController 책임을 제외한 업무 정책은 기존 coordinator/저장소를 재사용 |

## 뉴스·테마·일지

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 뉴스 입력·작업 | `central_server/news_sources.py`, `central_server/news_service.py`, `central_server/news_jobs.py`, `central_server/market_news_sources.py`, `infrastructure/naver_stock_news.py` | Naver 검색·증권 종목 목록, TOP20 수집 범위와 BODY/RULE/AI 단계. `CentralNewsService`의 `CentralNewsStore`는 직접 호출 7개와 동일 저장소를 받는 job/query-set/market-feed 작업자의 요구를 합친 19개 메서드 계약이다. 선택적 job wakeup 감지와 기존 worker 수명·native transaction/revision·요청 quota는 유지한다. |
| 과거 뉴스 PC 전처리·시황 업로드 | `scripts/preprocess_historical_news_locally.py`, `scripts/probe_historical_backfill.py`, `scripts/run_naver_stock_market_news.py`, `scripts/import_prepared_historical_news_to_nas.py`, `scripts/historical_collection_monitor.py`, `scripts/historical_collection_counts.py`, `central_server/database.py` | 두 원천 수집기가 기사별 BODY/RULE 준비를 병렬 실행해 PC 원장에 저장하고, 준비된 결과만 불변 스냅샷으로 NAS 정상 뉴스 테이블에 적재. 모니터 건수는 SQLite trigger로 원래 writer transaction 안에서 증분 갱신하며 최초 1회 집계 후 작은 summary 테이블을 읽음 |
| 종목 뉴스 조회 | `central_server/app.py`, `central_server/database.py`, `infrastructure/central_news_client.py`, `presentation/news_workers.py`, `presentation/stock_news_window.py`, `presentation/historical_news_archive_dialog.py`, `infrastructure/persistence/stock_news_repository.py` | NAS 저장분 200건 offset 페이지와 별도 과거 archive의 dataset/cursor·정확 ID 클라이언트/읽기 전용 창. 기존 화면 스크롤 추가 조회와 PC 직접 연결 보존 건수는 기존 경로 유지 |
| 뉴스창 실행·명령 | `presentation/news_window_coordinator.py`, `presentation/process_control.py`, `presentation/main_window.py` | 독립 뉴스 프로세스·명령 번호·창 복원 상태는 coordinator가 소유하고 메인 표는 사용자 입력만 전달 |
| 네이버 증권 시황 피드 | `infrastructure/naver_stock_market_news.py`, `central_server/market_news_sources.py`, `scripts/run_naver_stock_market_news.py`, `scripts/historical_collection_monitor.py` | FLASH/WORLD 날짜별 응답·발행시각, NAS 독립 cursor 수집, 역사 원응답 보존·진행 확인·재시작/정지 |
| 시장 뉴스 화면 | `presentation/market_news_window.py`, `news_process.py`, `infrastructure/central_news_client.py`, `central_server/database.py` | TOP20 앞 뉴스 진입, 공통/속보/해외 탭. NAS 저장 소스 조회와 PC 직접 연결의 화면 요청을 분리 |
| 후보 기업행동 백필 | `scripts/collect_historical_market_context.py`, `scripts/daishin_market_context_backfill.ps1`, `scripts/classify_historical_stock_adjustments.py`, `scripts/collect_candidate_event_disclosures.py`, `scripts/collect_candidate_exchange_disclosures.py`, `infrastructure/dart_disclosures.py` | 주도후보에 한정한 CREON 누적 수정계수 경계와 상장·거래량 0·거래 재개 후보 날짜 수집, DART 사건 인접 및 전체 거래소 공시 목록 연결, 원응답·재개·NAS 게시 gate |
| 거래소 공시 효력일 | `scripts/collect_candidate_exchange_effective_dates.py`, `infrastructure/exchange_effective_dates.py`, `scripts/reconcile_candidate_exchange_effective_dates.py`, `scripts/publish_historical_market_context_to_nas.py` | DART 접수일과 거래정지·재개·상폐 효력일/시각을 별도 보존하고 키움 일봉 거래량으로 대조. 원문 ZIP과 대조 원장을 시장 맥락 NAS 스냅샷에 게시 |
| 뉴스 판단 | `application/news_rules.py`, `application/news_grouping.py`, `application/news_analysis.py` | 규칙·사건 묶음·분석 |
| 단발성 과거뉴스 archive | `scripts/audit_prepared_historical_archive_readiness.py`, `scripts/build_prepared_historical_search_projection.py`, `central_server/historical_news_archive.py`, `central_server/app.py`, `infrastructure/central_news_client.py`, `presentation/historical_news_archive_dialog.py` | PC 미완성 파일의 봉인 차단 조건을 읽기 전용 감사하고, 봉인된 파일의 검색 페이지·정확한 기사 ID만 읽는 별도 인증 API와 PC 조회 창. 시황 projection·전체 봉인·실자료 검증은 미완료 |
| AI 공급자 | `infrastructure/news_ai.py` | 기존 외부 공급자 연동 |
| 테마 | `infrastructure/persistence/theme_repository.py`, `application/theme_matching.py`, `application/theme_preview.py` | 프로필·종목연결·가져오기·미리보기, 프로필별 대표명/분리 결정 재적용 |
| 테마 동기화 | `infrastructure/central_theme_sync.py` | 로컬 편집·pending·retry·NAS 스냅샷 |
| 설정·테마·뉴스 AI·일지 백업 파일 | `infrastructure/persistence/settings_backup.py`, `infrastructure/persistence/theme_backup.py`, `infrastructure/persistence/news_ai_backup.py`, `infrastructure/persistence/journal_backup.py`, `infrastructure/persistence/backup_file.py` | JSON과 일지 SQLite 백업 생성·복원, 사용자 선택 파일의 임시 저장 후 교체 |
| Google Drive 백업·엄격 복원 | `infrastructure/persistence/google_drive_sync.py`, `infrastructure/persistence/strict_restore.py`, `infrastructure/persistence/settings_backup.py`, `infrastructure/persistence/theme_backup.py`, `infrastructure/persistence/news_ai_backup.py`, `presentation/google_drive_worker_controller.py`, `bootstrap.py` | v2 세대별 불변 구성요소를 manifest로 마지막 게시하고 ID/hash/크기를 검증하며 v1 별칭을 호환 갱신한다. 설정·테마는 같은 SQLite 읽기 snapshot에서 만든다. 명시 복원은 검증 자료를 보관해 다음 시작 전 공통 프로세스 잠금 아래 적용하며 중단 시 원상복구한다. 다른 DB 간 동시시점·Drive 동시 편집 병합은 보장하지 않는다 |
| 테마 다건 가져오기 | `presentation/theme_dialogs.py`, `presentation/main_window.py`, `presentation/settings_request_worker.py`, `infrastructure/persistence/theme_repository.py` | Excel 파일 선택·검토·저장은 테마 관리창이 소유한다. 이미지 OCR 결과의 행 수정·종목명 확인·미리보기는 테마 대화상자 코드가 맡고, OCR 작업자 수명과 승인 뒤 비동기 저장은 메인창이 맡는다. 두 경로 모두 저장 성공 뒤 화면에 반영 |
| 테마/시장 연구 | `application/theme_leadership.py`, `application/market_research_features.py`, `application/context_candidates.py` | 대장·시장 특징·맥락 가설 |
| 시점 근거 | `domain/snapshot_provenance.py`, `application/trade_snapshot_context.py` | 당시 관측과 사후 보완 구분 |
| 일지 | `presentation/journal_workers.py`, `infrastructure/central_journal_sync.py` | 일지 조회/분석·계좌 scope 동기화 |

## 연구·타점

| 저장소 상대경로 | 책임 |
|---|---|
| `scripts/export_research_dataset.py` | NAS 일별 동결 export·bundle |
| `src/kiwoom_monitor/infrastructure/research_data_source.py` | 입력 로드·독립 시간/종목/final projection |
| `scripts/export_historical_reconstruction.py`, `scripts/prepare_historical_research_input.py`, `infrastructure/historical_reconstruction.py` | 사후 후보·대신 봉·과거 뉴스의 불변 복원과 기존 1분 전략 입력 어댑터. 후보 원본 DB의 현재 `stocks.market_code`를 보존하고 명시적 `--individual-stocks-only` 투영에서 개별 주식만 연구 모집단에 포함. strict TOP20 재생과 분리 |
| `scripts/plan_historical_research_split.py`, `application/historical_research_split.py` | 다기간 역사 사례의 시간순 TRAIN/VALIDATION/SEALED OOS 배정과 데이터셋 hash 결합 |
| `scripts/prepare_historical_development_inputs.py`, `infrastructure/research_data_source.py` | 봉인 계획에서 역사 provenance를 유지한 TRAIN·VALIDATION 독립 입력 투영. OOS는 별도 gated 경계 |
| `scripts/build_historical_exchange_case_context.py` | 월별 TRAIN·VALIDATION 후보에 해당하는 공식 거래소 효력일과 접수일·일봉 대조 결과를 불변 동반 자료로 투영. 역사 장중 접수 시각 미검증이므로 전략 신호·주문 가능성 판정에 자동 투입하지 않음 |
| `scripts/prepare_historical_baseline_requests.py`, `scripts/summarize_historical_baseline_results.py` | OOS 없는 역사 개발 입력의 두 기존 Family 고정 요청 생성과 실행·검열 사유 요약 |
| `scripts/audit_historical_minute_readiness.py`, `scripts/audit_historical_monthly_bar_quality.py`, `scripts/audit_historical_monthly_gap_causes.py`, `scripts/audit_historical_monthly_gap_raw.py`, `scripts/audit_historical_nas_minute_alignment.py`, `scripts/audit_historical_five_minute_clock.py`, `scripts/project_historical_minute_exclusions.py` | 후보일·종목일별 1분봉 최소 조건과 종일 봉 개수·긴 공백의 키움 일봉/분봉·CREON 일봉·VI 및 원응답과 저장 봉 일치를 읽기 전용 감사, NAS/CREON 거래일별 마감 체결 시각 대조와 CREON 5분봉 종료시각·해상도 구분, 원본 SHA-256에 묶인 종목일 제외 투영 |
| `scripts/plan_historical_monthly_case_selection.py` | 가격 결과를 보지 않고 월별 첫 후보일 16개를 고정하고 기존 봉인 OOS 날짜·제외 원장의 해시에 결합 |
| `scripts/audit_historical_monthly_selection_coverage.py` | 동결 선택일과 개발 전체 후보일의 후보 수·기초 분봉 수량을 원본 해시로 대조하는 읽기 전용 범위 감사. 가격 결과·OOS 미사용 |
| `scripts/prepare_historical_learning_cases.py`, `application/historical_learning_cases.py` | 사후 후보 선정·복원 뉴스 근거·미생성 AI 해석·미래 가격 결과를 분리한 불변 LLM 학습 준비 사례. strict 시점 재생이나 모델 학습 완료로 사용하지 않음 |
| `scripts/prepare_historical_news_review_queue.py`, `application/historical_news_review_queue.py` | 학습 준비 사례의 기사 식별자 중복 제거, 종목·사례별 비AI 규칙 힌트와 사람 검토 빈칸을 보존하는 불변 검토 대기열. 규칙 힌트는 정답이 아님 |
| `scripts/historical_news_review_workflow.py`, `application/historical_news_review_decisions.py` | 우선 검토 CSV 내보내기와 사람 판정·사건 ID·테마 프로필 검증, 불변 결정 결과 저장. 편집 CSV와 최종 결과를 분리하고 모델 학습 준비 상태는 false 유지 |
| `scripts/plan_historical_news_event_split.py`, `application/historical_news_event_split.py` | 사람 검토 관련 기사를 canonical event 단위로 유지한 시간순 TRAIN·VALIDATION·봉인 OOS 불변 계획. 무관·보류와 일부 검토 상태를 분리 |
| `scripts/prepare_historical_news_development_inputs.py`, `application/historical_news_development_inputs.py`, `presentation/historical_news_review_dialog.py` | 사건 분할에서 TRAIN·VALIDATION 사람 검토 기사만 투영한 RAG·미세조정 비교 공통 입력. 앱과 CLI 생성 경로, OOS payload 제외와 사건 중복 금지 검증 |
| `scripts/prepare_historical_news_blind_validation.py`, `application/historical_news_blind_validation.py` | 공통 VALIDATION에서 사람 target·사건 ID·테마명을 제거한 방법 중립 평가 요청. 개발 dataset ID·validation hash 결합과 정답 누수 재귀 검증 |
| `scripts/prepare_historical_news_method_results.py`, `application/historical_news_method_results.py` | prompt baseline·RAG·미세조정 예측을 동일 블라인드 요청 전체에 결합하는 불변 결과. 누락·중복·추가 응답과 OOS·정답 사용 표시 차단 |
| `scripts/evaluate_historical_news_method.py`, `application/historical_news_method_evaluation.py` | 평가기 전용 VALIDATION target 결합, 사건 pairwise 군집·테마 집합·프로필·coverage 지표. 관련성 분류와 자동 모델 승격은 지원하지 않음 |
| `scripts/assess_historical_development_readiness.py`, `infrastructure/historical_research_readiness.py` | 역사 개발 분할의 모든 사례·종목별 분봉·연속 1분쌍 완전성 gate. 이전 분할 재생용 후보군 seed는 검사 모집단에서 제외. 희소 구조 실행은 명시적 예외 |
| `src/kiwoom_monitor/research_process.py` | campaign worker·가설·순차/final 평가 프로세스 |
| `scripts/run_research.py` | 기존 runner의 재생·실행·평가 저장. 판단은 최대 128건씩 저장하고 사건 참조 전 즉시 flush하며 논리 결과 해시는 순차 계산 |
| `src/kiwoom_monitor/application/research_replay.py` | 시점별 입력 재생. 투영된 역사 입력은 별도 역사 후보군 종류를 명시해 재생하며 대상 봉·같은 종목/당일 이력을 돌파·눌림 Factor에 전달 |
| `src/kiwoom_monitor/application/research_execution.py` | PaperExecutionEngine·봉 체결·현금/비용. 기존 정수 `fixed_bps/v1`과 소수 bp 문자열 `fixed_bps/v2` 구분 |
| `src/kiwoom_monitor/application/research_evaluation.py` | 연구 성과·품질·평가 |
| `src/kiwoom_monitor/application/research_families.py` | 등록 Family·Factor·허용 파라미터 |
| `src/kiwoom_monitor/application/breakout_strategy.py` | 완료봉 돌파 판단 |
| `src/kiwoom_monitor/application/pullback_reacceleration_strategy.py` | 눌림 후 재가속 판단 |
| `src/kiwoom_monitor/application/research_search.py` | 제한 trial·개발 근거·후보 선택 |
| `src/kiwoom_monitor/application/research_hypotheses.py` | 결정적 가설·후속 생성·부모 계보 |
| `src/kiwoom_monitor/infrastructure/persistence/research_repository.py` | v24 run/campaign/lease/final/가설·순차 소유 원장. 판단 묶음은 한 SQLite 트랜잭션으로 불변성·순서를 검증 |
| `src/kiwoom_monitor/presentation/research_dialog.py`, `presentation/historical_news_review_dialog.py` | 연구·campaign·가설·순차/final UI와 과거 뉴스 사람 검토·작업표 저장·불변 결과 동결·사건 분할 계획 실행 |
| `src/kiwoom_monitor/application/research_resources.py` | RSS/preflight·CPU 양보 |
| `src/kiwoom_monitor/application/forward_evaluation.py` | feedback evidence/review/제안·forward 평가 |
| `src/kiwoom_monitor/application/feedback_strategy_revision.py` | 명시 채택 버전·재검증 spec/dispatch |

## 계좌·인증·모의 실행

`src/kiwoom_monitor/` 기준 경로다.

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 계좌 신원 | `application/account_identity.py`, `infrastructure/kiwoom_rest/account_identity.py` | scope/binding; A4B 직접 연결 미완료 범위 별도 |
| 인증 | `central_server/credential_store.py`, `central_server/credential_runtime.py` | 암호화 vault·prepare/apply·세대·drain |
| 인증 UI | `infrastructure/central_credentials_client.py`, `presentation/nas_credentials_dialog.py` | HTTPS 요청·입력·worker |
| 후보 감시 | `central_server/candidate_monitor.py`, `central_server/observation_frame_recovery.py` | 현재 shadow producer·후보 알림. 안전한 공통 bootstrap과 구형 C 이하 frames-only 복구를 수행한다. protocol과 frames는 기존 checkpoint에 원자 저장하고 과거 판단을 재실행하지 않는다. 독립 scratch 진행·trim 및 close 시 실제 native 작업 drain을 보존한다. |
| 운용 명세·입장 | `application/mock_automation_specification.py`, `application/mock_automation_admission.py` | 동결 spec·적격성·단일 계좌 lease |
| 위험·복구·전달 | `application/mock_automation_risk.py`, `application/mock_automation_recovery.py`, `application/mock_automation_execution.py` | 실제 위험 근거·대사·Decision gate |
| 주문 원장 | `application/order_lifecycle.py`, `central_server/execution_runtime.py` | intent·unknown·멱등·owner |
| 자동 모의 수명 | `central_server/mock_automation_runner.py`, `central_server/mock_automation_supervisor.py`, `central_server/observation_frame_recovery.py`, `presentation/mock_automation_dialog.py` | runner·영속 제어·UI. 안전한 bootstrap/legacy 복구 중 fill 반영/control 검사와 기존 intent/binding을 유지한다. 구형 C 이하 frames만 복구하고 과거 주문을 실행하지 않는다. frames/protocol 원자 checkpoint 및 실제 native drain 후 종료를 소유한다. |

## 다음 개발의 진입점

외부 `kiwoom_history_backfill`의 후보 DB·기존 수집 스크립트는 앱 모듈이 아니다. 현재 표본 경계는 `infrastructure/historical_backfill.py`, `scripts/probe_historical_backfill.py`, 32비트 COM 표본·연속조회 브리지 `scripts/daishin_stockchart_probe.ps1`과 `scripts/daishin_stockchart_backfill.ps1`에 있다. CREON의 종목 분봉·시총/주식수·수정주가 작업은 참조 `stocks.market_code` 0/10인 코스피·코스닥 개별 주식만 대상으로 하며 다른 상품의 기존 원응답은 삭제하지 않고 작업 원장에서 `excluded`로 분리한다. [과거 수집 기획](docs/HISTORICAL_BACKFILL_PLAN.md)으로 원천별 표본을 확인한 뒤 기존 `research_data_source`, 뉴스 revision, 프로필 저장 경계에 연결한다.

관련 테스트는 각 책임의 `tests/unit/test_*.py`와 `scripts/check_*.py`에서 찾는다. 문서 정리에서 이전 테스트 개수를 새 실행 결과로 복사하지 않는다. 실제 코드 변경 때 해당 경계의 의미 있는 회귀와 필요한 통합 검증을 수행한다.

# 남은 작업과 보류 사항

**2026-10-08 의존성 축소와 기능 보존 검증:**
[실행 계획](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md). 기존 143회 회귀 호출(142개 고유 모듈)
및 batch 목록을 같은 순서로 새 manifest에 옮겼다. Windows Job Object로 worker를 프로세스
시작 시점부터 묶고, timeout/중단/부모 종료 때 자손을 종료한 뒤 각 PID의 종료 신호를 확인한다.
실행기 검사 17건, PC 수명 검사 69건, 뉴스 기준선 비교 2건, TOP20 수명 검사 10건과
기존 core 1,365건을 포함한 `all-local` 1,465건이 사용자 Windows 환경에서 모두 통과했다.
9개 worker 모두 종료됐고 process tree 종료도 확인됐다. 잔류 자손은 없었다. 첫 실행에서 드러난 다섯 테스트
fixture/기대값 문제는 원인을 확인해 테스트만 보정했다. 앱 동작 코드는 바꾸지 않았다.
기존 ASGI 기준선 12건은 유지했고,
`tests/fixtures/api_contract_baselines/news_reads_http_v1.json`에 Uvicorn loopback 실제 HTTP
기준선 13건을 추가했다. 200 네 건, 인증 실패 401 네 건, 404 한 건, 422 네 건의 전체 JSON
본문과 content-type/content-length를 보존한다. 이전 기준선은 덮어쓰지 않았다.

제한 실행 환경에서 TOP20과 broker 테스트가 멈춘 원인은 Windows Proactor 이벤트 루프의
socketpair 내부 accept 단계였다. 같은 두 테스트는 사용자 Windows 실행 환경에서 각각
0.032초와 0.281초에 통과했다.

첫 core 실행에서 운영 설정 실패 주입이 store 인스턴스에 닿지 않았고, trace 경로 5개가
명시 기대 목록에서 빠졌으며, 일봉·통계·뉴스 fixture 조건이 각 테스트 기대와 맞지 않는
문제가 확인됐다. 각각의 조건을 테스트에서 수정한 뒤 1,365건 core와 전체 1,463건
`all-local`이 실패·오류·skip 없이 통과했다. 상세 원인과 실행 결과는 계획 문서에 기록했다.

새 사용자 요구: Windows worker의 timeout/중단 때 worker 자손까지 종료됐는지 확인하고,
뉴스 API 이동 전후 같은 HTTP 요청의 상태·헤더·본문을 비교한다. 자손 종료 검사는 구현·검증했고
뉴스 조회 세 경로를 독립 라우트 모듈로 옮긴 뒤 ASGI 12건과 loopback HTTP 13건의 기준선,
SQLite 무변경 검사, 전체 `all-local` 1,465건을 통과했다. 정적 QueryStore 감사는 이동 전후
모두 `review_required`로 남았으며 parse 오류나 stale binding은 없다. 네 조회 binding은
새 모듈로 승인 기록했고 app router 등록 forwarding edge 하나가 추가됐다. 기존 감사의
계약 변경 및 미해결 항목이 있으므로 전체 감사 통과로 보지 않는다.
`.github/workflows/dependency-regression.yml`에 PR·push·수동 실행 workflow를 추가했다.
Windows `all-local`과 격리 PostgreSQL 17 integration job이 각각 실행 결과·로그를 보관한다.
로컬 YAML parsing과 두 job/runner 구성, regression profile 목록 확인은 통과했다.
GitHub hosted runner의 실제 실행과 branch protection 필수 check 적용 여부는 아직 확인되지 않았다.
CI가 실제 통과하고 required check가 적용되기 전까지 3단계는 미완료다. NAS 배포는 진행하지 않았다.

**2026-10-08 NAS restricted operator local implementation:**
The fixed client/supervisor/installer and isolated worker are implemented in the PC workspace;
`tests.unit.test_nas_operator` passed 19 tests. The offline Linux filesystem/ACL acceptance in
`deploy/synology/check-nas-operator.sh` is prepared but has not been run. NAS install, sudoers
validation, isolated NAS acceptance, and first command execution remain open. Do not treat this
as available on NAS until those gates pass. The current 8GiB capture must finish and its deferred
trace must persist before any NAS installation or server change. No production credentials are
included in the prepared operator reports.

**2026-10-08 opening capture partial input coverage:**
At the 09:18 KST read-only check, trace `20261007T235957Z-e8cb574bf964` was running with 807,307
accepted, 0 written (persistence is deferred), 0 known queue drops, and 355 rejected inputs.
Rejections mean this trace cannot qualify as complete lossless input coverage even if its final
chunks and checksums validate. Inspect the final rejection breakdown and chunk continuity after
20:10; use only individually supported, non-rejected event groups for explicitly scoped replay.

**2026-10-08 NAS log display timezone:** `server_logging.py` now has a local candidate change to
format timestamps in Asia/Seoul and rotate daily logs at 15:00 UTC (KST midnight), without changing
the process timezone, database timestamps or scheduler. It is not deployed. The already armed
October 8 capture checks the active release `2026.10.08-trace-ram-8g-5m-v1-e1cc01dde5bacbb9`;
deploying another release or restarting the server before its deferred trace persistence completes
would invalidate that capture plan. Validate and activate after the trace reaches `complete` at or
after 20:10 KST, then verify the new timestamp and rotation behavior on NAS. No test suite was run.


**2026-10-08 O12 local start armed:** detached NAS PID 3492 runs the stdlib one-shot scheduler
for 08:59:50–09:59:50 / 20:10 deferred persistence. Seven scheduler tests and NAS read-only
preflight passed; the immutable server release and both containers are unchanged.
Start heartbeat is now read-only at 09:01; final verifier reads the NAS `.status.json` trace_id.
No duplicate chat start or automatic retry after an uncertain POST. Confirm actual start and
accepted growth; written may remain zero during deferred RAM retention. This wait does not
survive a NAS reboot. Actual start/capture coverage/persistence acceptance remains open.
The earlier chat-driven 08:58 preflight reservation is superseded.

**2026-10-08 O12 current acceptance:** NAS 103 tests, private API gate and a 294-event durable
smoke passed. Release `2026.10.08-trace-ram-8g-5m-v1-e1cc01dde5bacbb9` is active;
live API confirms schema 3 / 8GiB / 5,000,000 events / 1MiB/s. Start and verification automations
now require this exact release and all three input flags, targeting 08:59:50–09:59:50 on October 8,
deferred persistence at 20:10, verification October 9 02:30. Actual timing must be checked.
The post-deploy helper's image-source import error did not undo deployment; the helper check
now reads the live API and PID1 source identity. No new activation or regression rerun is needed.
Still open: actual opening capture integrity/replay coverage, whole-app capture overhead,
full-capacity persistence and source-state equivalence. A small smoke is not proof of these.
Earlier gate-pending and older release/window notes below are historical.

**2026-10-08 O12 NAS gate loading failure:** optional Starlette TestClient dependency prevented
the first 8GiB/5M NAS gate from loading; deployment did not proceed. Do not install httpx2.
The candidate API tests now execute real HTTP ASGI authentication/middleware/validation and
retain all four acceptance tests without TestClient/httpx/httpx2. PC API 4/4 and related full
suite 103/103 passed; no optional HTTP client modules were loaded in the API-only run.
Republish the test-only correction, then run exact NAS regression, bounded durable smoke and
source activation before updating the two schedules. Full persistence/market overhead gaps remain.

**2026-10-08 O12 latest request supersedes earlier window/defaults:** 08:59:50–09:59:50 KST,
3,600 seconds, actual deferred defaults 8GiB / 5,000,000 events. Staged immutable candidate
`2026.10.08-trace-ram-8g-5m-v1-abece6f02adbe726` has matching API capabilities, 8GiB total
storage quota, unchanged 1MiB/s paced persistence, and 9GiB host/cgroup start headroom.
PC related regression passed 68 tests and direct schema-3 API forwarding/default limits gate.
Active v6 and both schedules remain unchanged pending exact-source NAS gate and activation.
Then update capture and verification schedules together, including all three input flags and
the existing 20:10 KST deferred persistence deadline. Still unverified: real opening maximum,
whole-app capture overhead, and full-capacity persistence. Prior synthetic capacity results
must not be generalized as a whole-app performance baseline. Release preparation is authorized
by the user's explicit 8GiB/5M release request; this does not make missing measurements verified.

**2026-10-08 O12 requested capture window: 09:00–10:00 KST (60 minutes):**
The user shortened the target to 3,600 seconds, excluding the pre-open five minutes. Prepare an
inactive exact-v11-derived 36,000-message probe using the same 20-row/100ms synthetic input envelope,
REST/catalog/subscription/lifecycle mix, private 15GiB/5M budget and 4GiB physical reserve guard.
Logical charge is not RSS: v11 charged 16,075,246,320 bytes while RSS peaked at 4,675,239,936 bytes.
Do not recalibrate accounting or infer a physical 15GiB RAM requirement from this change. Keep
operational 4GiB/1M defaults and active v6 unchanged until exact-source regression, NAS memory/capacity,
overhead and persistence gates pass. Both capture-start and verification reservations must then agree
on 09:00–10:00, the accepted limits, schema 3, all three input flags, exact build/release and deferred
persistence deadline. A private synthetic capacity pass alone does not authorize deployment.

Staged private candidate: `2026.10.08-causal-capture-60m-v1-a8376b5b6bdf96e7` (929 files,
isolated commit `ffc47de13c7348081845f2d876988c67c28eb785`). Exact-source PC unit/API regression
passed 101 tests with no skips; API forwarding, 100-message smoke and POSIX shell syntax passed.
NAS active/runtime/runner metadata stayed identical. User report `causal-capture-60m.wMVk6k.log`
completed all 36,000 synthetic messages: accepted 2,307,608, input_rejected=known_dropped=0,
written=0 (deferred RAM). Charge=15,550,948,874, high-water=15,559,223,163,
RSS peak=4,526,362,624, minimum host available=13,554,601,984 bytes. Only about3.4% charge
margin remains. This is not an actual market maximum or full consumer execution benchmark.
ON short-message p95=7.45–7.71ms vs OFF=2.94–2.99ms; full ON max=1,837ms,
payload copy max=2,477ms. Next isolate slow copy inputs/GC without changing runtime policy;
do not attribute the maximum to GC without correlation evidence. The 294-event durable smoke
passed, but full-capacity persistence and overhead acceptance remain pending. Operational
defaults and both reservations are unchanged.
Inactive diagnosis candidate `2026.10.08-causal-capture-copy-profile-v1-f5b07b1d5fd17b0e`
adds only private per-input copy timing and GC correlation (worst8 records bounded). Recorder,
contract, GC policy and operational limits are unchanged. PC20-message smoke preserved accepted294,
zero rejected/drop; POSIX syntax passed. Run `scripts/check_causal_copy_profile.sh` on NAS.
Instrumented results narrow causes only; they do not replace uninstrumented overhead acceptance.

User report `causal-copy-profile.1gzqXn.log` completed the same36,000-message envelope losslessly
in RAM (accepted2,307,608, written0). Largest REST copy=2448.337ms, GC inside=2430.272ms;
small realtime-input copy=1989.087ms, GC=1988.862ms; save_query copy=1636.053ms,
GC=1617.831ms. Gen2 GC totaled24801.542ms over38 collections. Observed long-copy stalls
are predominantly GC pauses in this controlled probe, not simply large payload traversal.
Remaining decision: reduce retained Python object-graph cost without changing process-wide GC
policy or moving unacceptable CPU/queue delay into collector paths. Current DeliveryStage/Context
ownership and frozen tuples must retain charge/refcounts, event ordering, causal IDs and durable schema.
No production gc.disable/gc.freeze/threshold change is authorized by this diagnosis. Large paced
persistence, uninstrumented overhead and actual market maximum remain unverified.

**2026-10-08 O12 v10 sizing did not start; v11 15GiB private probe prepared:**
v8 private 12GiB/5M sizing had two market_request_lane_limit rejections at mixed round 24,361. v9
stores the exact canonical UTF-8 request signature internally and charges its byte length, retaining
per-signature ordinals, replay pairing, wire payload, 4MiB byte cap, and 32,768 lane cap. Exact v9
source passed 100 unit regressions/API gate; focused REST/tape/lifecycle tests passed 49/49.
The v9 NAS 12GiB/5M run retained 3,968 request keys charged at 2,911,020 bytes. Copy reservation
then saturated at 29,731 messages (about 49.6 minutes at the probe's 100ms cadence): 1,905,826
accepted, 28 capture_memory_full input rejections, zero known drops, 12,851,783,703 charged bytes,
RSS peak 3,735,203,840 bytes, minimum host MemAvailable 14,344,683,520 bytes, and cgroup headroom
13,431,042,048 bytes. This was an expected classified saturation, not a complete 65-minute input
run. It does not establish sufficient capacity.

Inactive v10 candidate 2026.10.07-causal-deferred-capture-v10-fdbeeef6e982aa2e is staged. It probes
39,000 messages at 100ms intervals (65 minutes of synthetic timestamps) with a private 16GiB charge
budget inside a 20GiB cgroup, preserving a 4GiB host/cgroup reserve. The gate must complete all
39,000 with zero input rejection/drop and report actual RSS/headroom. Exact-source PC regression
100/API and a small 16GiB smoke passed; POSIX shell syntax passed. The NAS 65-minute gate then failed
before recording input: its preflight requires 17GiB host/cgroup headroom for a 16GiB charge budget,
while the preceding host MemAvailable sample was 18,073,481,216 bytes, 180,129,792 bytes below that
threshold. It is a startup-headroom failure, not evidence that the 16GiB charge budget saturated.
Inactive v11 `2026.10.08-causal-deferred-capture-v11-dffa8b971619a1ea` was staged from exact v10 source
(928 files, commit `ae1e7cbe9c71ec44ca83cdeddbcae4bf529137d8`, active/runtime unchanged). Its 15GiB NAS
probe reached copy-reservation saturation after 37,201 of 39,000 messages (about 62:00 of synthetic
65:00), with 2,384,653 accepted events, 28 `capture_memory_full` rejections and zero known drops.
The memory charge limit was first; event count remained below its 5M cap. Charged bytes were
16,075,246,320; RSS peak 4,675,239,936 bytes; minimum host MemAvailable 13,404,901,376 bytes and
cgroup headroom 16,789,262,336 bytes. The 4GiB runtime guard remained satisfied. Therefore 15GiB is
insufficient for this envelope.

The probe also reported `copy_ms_max=2,481` and message latency max 1,833ms, without identifying which
input caused the maximum. Capture overhead is not approved. Next: read current NAS MemAvailable and
rerun the staged v10 16GiB private gate only if it is at least 18,253,611,008 bytes (17GiB); otherwise
stop before the probe. Do not weaken production preflight. Active release/runtime, containers,
4GiB/1M defaults and the capture reservation remain unchanged. Even a complete synthetic envelope
does not establish actual market maximum, 65-minute live capture, paced near-capacity persistence,
global file quota, or the source of the copy-time outlier.

**2026-10-07 O12 v7 NAS correctness 통과, capacity/overhead 승인 보류:** API, 94개 회귀,
15개 PostgreSQL gate 및 294event catalog durable smoke가 통과했다. 4GiB/1M은 630,132event,
8GiB/5M은 1,267,390event에서 copy admission RAM 한도로 입력 거부가 발생했다. known_dropped=0도
input_rejected>0을 무손실로 바꾸지 않는다. OFF/ON p95 2.967/7.896ms 및 mixed 경로 지연을 포함해
전체 장중 consumer의 영향은 아직 미확정이다. private v8 12GiB/5M sizing 후보는 PC 97개 회귀/API
검증을 통과했지만 NAS 포화·RSS/headroom·ON/OFF 및 full-capacity durable drain은 남아 있다.
운영 한도·active v6·Oct 8 예약은 유지한다. 실제 최대 입력 envelope, 파일 4GiB quota, 지연 gate까지
통과하기 전 65분 충분성을 승인하거나 예약 expected build를 바꾸지 않는다.

**2026-10-07 O12 catalog copy profile PC 구현 완료, NAS gate 대기:** 혼합 native 경로의 5,000종목 catalog 저장
인자가 8MiB copy charge에 걸려 input rejection을 재현했다. 원형 인자 14,038,408 bytes를 담는
catalog-only 16MiB profile을 capture reserve와 replay decoder/preflight에 대칭 적용했다.
일반 입력 8MiB, node/type/secret 한도와 native 단일 호출은 보존한다. 프로파일 구현·회귀·NAS gate 전에는
활성 v6/Oct 8 예약을 바꾸지 않는다. 2,500종목/150event durable 성공을 5,000종목 또는 65분 성공으로
대체하지 않는다. [계약·파일·검증 범위](DB_REPLAY_TRACE_DESIGN.md#catalog-copy-profile-decision-2026-10-07-implementation-pending).
현재 비활성 NAS 후보는 `2026.10.07-causal-deferred-capture-v7-5127bafdc15c374a`이다. v4는 DSM Docker의
미지원 `--cpus 1` 옵션으로 테스트 시작 전에 실패했다. v5 API는 통과했으나 v6까지 deferred trace의
4GiB+64MiB 파일 저장 여유 preflight에 작은 tmpfs가 걸렸고, 게이트가 요구하는 T1–T4 테스트 파일 3개도
release에 누락되어 51개 중 21개 오류가 났다. v7은 5GiB tmpfs와 누락 테스트를 포함한다. NAS 회귀,
RSS/MemAvailable/cgroup, ON/OFF 지연 및 paced persistence는 아직 미검증이며 활성 release와 예약은 그대로다.

**2026-10-07 O12 shared delivery retention PC 검증 완료, NAS 용량 gate 대기:** immutable
source/subscriber/delivery identity와 slotted stage record를 deferred capture에 적용했다. 세
delivery stage·JSONL 필드와 replay coverage는 보존되고 관련 84개 회귀가 통과했다. input rejection은
terminal incomplete로 처리한다. 중간 PC 샘플은 4만 tick에서 charge 998MiB→219MiB, RSS 증가
155MiB→113MiB였고 지연 개선은 확인하지 않았다. 이 작은 분포를 65분·장초 혼합 입력의 capacity
보장으로 일반화하지 않는다. 새 비활성 후보를 exact-source로 staging한 뒤 NAS에서 OFF/ON·mixed
shape·실 RSS/MemAvailable/cgroup·output bytes 및 persist deadline을 검증한다. 기존 4GiB/1M은
아직 판정하지 않았고 8GiB/5M도 미승인이다. active v6와 Oct 8 예약은 gate 통과 전까지 유지한다.

**2026-10-07 causal capture/deferred 후보 용량 gate 보류:** 기능 통합 및 로컬 회귀 78건은
통과했지만 native delivery 경로의 통제 burst에서 8GiB/5M도 memory charge 한도에 먼저 도달했다.
326,960 tick에서 약 101만 accepted event, RSS 약 1.26GiB였으며 `capture_memory_full`이 발생했다.
delivery receipt가 작은 fixture charge의 약 88%를 차지한다. 실제 장초 입력 최대량은 아직 미확정이다.
receipt/provenance 정합성을 보존하면서 retained metadata와 charge 계산을 재검토하고, 전체 causal
혼합 입력량 및 NAS RSS/MemAvailable/ON-OFF 지연을 확인해야 한다. 비활성 후보의 NAS gate 후에도
65분 용량 acceptance가 별도로 필요하다. 그 전에는 active v6와 10월 8일 08:55 예약의 build/options를
변경하지 않는다. 불완전한 10월 6일 trace로 전체 성능·용량 기준선을 대신하지 않는다.

**2026-10-07 O12 T4 native TOP20 + peer acceptance 완료:** 비활성 v2 candidate에서 15개 테스트가
skipped=0으로 통과했다. 반복 실행과 service OFF/peer 조합, descendant 중복 방지, ACK loss 후 실제 drain,
DB/outbox 복구와 v1 baseline 보존을 확인했다. report `replay-cache-v2-acceptance-ec337a0eda5c1167ff7f36c17a59a872.log`.
두 stale/partial ranking 경고는 fixed controlled response가 후속 source slot에 맞지 않아 service가 해당 회차를
저장하지 않은 것이다. 따라서 이 gate는 T4 lifecycle correctness만 승인하며 실제 장중 source-state 등가나
성능을 승인하지 않는다(`source_state_equivalent=false`). **다음:** 무손실·유효 coverage의 실제 capture를 확보하면
현재 코드 상태부터 같은 입력으로 baseline replay하고 이후 한 후보씩 전후 비교한다. 불완전한 capture는 전체
성능 기준선으로 사용하지 않는다.

**2026-10-07 O12 T4 shared execution 경계 로컬 검증:** 당시 v2 `_ReplayStore` clock을 lease generation/active/run-ready
검증을 보존하는 explicit bound method로 연결했고, TOP20 native core가 store/clock/lease identity를 검사한다.
기존 recorded peer scheduler는 이미 arm된 동일 source clock과 run 소유 native task/executor를 재사용할 수
있도록 확장했다. operation error 및 waiter cancellation은 runtime 결과를 실패로 남기며 standalone runner가
공유 runtime을 획득하는 경로는 DB restore 전 차단한다. 관련 회귀 34건 통과, 결과는
`artifacts/t4-shared-clock-native-worker-tests.log`. 검증은 unit/fake store이며 전용 PostgreSQL의 TOP20+peer
통합 실행·descendant 중복 방지·최종 DB/revision 비교·DB/outbox 복구는 미실행이다. **다음:** source manifest
preflight와 workload mask가 확정된 뒤 owned lease 안에서 TOP20+peer를 함께 실행하고 반복/제외 및 최종 복구를
검증했다. 이후 T4 controlled PostgreSQL acceptance 15건이 skipped=0으로 통과해 lifecycle gate는 완료됐다.
전체 장중 상태 등가와 성능 비교는 유효한 실제 capture replay 전까지 미완료다. NAS active release와 capture 설정은 그대로다.

**2026-10-07 O12 T3 NAS acceptance 완료:** 비활성 후보
`2026.10.07-top20-replay-drain-v1-795caf04dde5e9ce`의 임시 RAM-backed·network-isolated PostgreSQL
검사 13건이 모두 통과했고 skipped=0이었다. cancelled executor의 실제 drain 전 baseline reset 차단,
native outbox commit ACK loss 후 재시도, DB/file 공동 복구 실패 처리, v1 snapshot 보존을 확인했다.
report `replay-cache-v2-acceptance-ac1d6c7568abcfccedf0547c765c517c.log`;
v1 `56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`, v2
`36e6ece20620ade0804ca28782fe23340fd9bceb24ce48d991883636e7e97446`. 운영 active release와 두
컨테이너는 변경되지 않았다. **다음:** T4 native TOP20 lifecycle runner. 이 acceptance는 controlled
fixture 정확성만 검증했으며 장중 상태 등가·성능을 뜻하지 않는다.

**2026-10-07 O12 T1–T3 TOP20 입력·종료 경계 로컬 구현 및 후속 검증:** T1의 `diagnostic_rest_input.py`는 native broker/catalog
입력 pair와 effect closure를 검증하고 offline tape로 공급한다. T2의 `diagnostic_top20_lifecycle_input.py`는
0s·구독 intent·ACK/READY·hub control·gap 입력을 검증하고 실제 구독 요청에 대응하는 tape 및 descendant
실행 계획을 제공한다. 관련 로컬 회귀 174건이 통과했다. 검증은 fake WebSocket/REST와 SQLite 경계이며,
PostgreSQL·실제 capture overhead·전체 lifecycle replay acceptance는 아니다. T3의 run 소유 task/thread drain은
실제 executor 종료를 확인한 뒤에만 reset/release를 허용하고, native `JsonRecordOutbox`는 실행 전용 파일과
hash로 seed·복구를 확인한다. 기존 TOP20·broker·collector 회귀 142건, NAS PostgreSQL acceptance 13건이
모두 통과했다. NAS gate는 controlled fixture에서 drain·DB/outbox 공동 복구를 검증했다. native lifecycle
runner는 T4에 남아 있다.
활성 NAS release와 녹화 설정은 유지한다.
실제 capture overhead·무손실 새 입력 수용은 미검증이다.

**2026-10-07 O12 TOP20 lifecycle 후속 구현 순서:**
[실험 설계의 단계별 gate](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#top20-lifecycle-실행-계약과-구현-순서--2026-10-07-설계-확정)를 따른다.
T1 request/catalog 입력 경계는 관련 회귀 135건, T2 0s·subscription/ACK/READY/gap 입력과 descendant plan은
관련 회귀 174건으로 로컬 구현을 마쳤다. T2의 검증은 fake WebSocket/REST와 SQLite 경계이며 PostgreSQL·실제
capture overhead·전체 lifecycle acceptance를 뜻하지 않는다. 활성 NAS release와 녹화 설정은 그대로다. T3의
owner/drain/outbox core와 관련 142건 회귀는 로컬 통과했다. PostgreSQL acceptance 13건도 NAS에서 통과했다.
다음은 T4 native lifecycle 반복 gate,
T5의 유효한 실제 입력 baseline 및
동일 replay 전후 비교다. DB 연결 수 0만으로 drain을 판정하지 않으며 뒤늦게 연결을 여는 thread도 막아야 한다.
불완전한 capture에서 입력을 추정해 채우지 않고 `input_unavailable`/미지원을 유지한다. 단계별 세부 검증 경계는
실험 설계 문서에 기록한다.

**2026-10-07 TOP20 replay v2 clock/cache/run (PostgreSQL correctness acceptance 통과):**
`CentralRestBroker` 선택적 source wall clock과 query-cache v2 lease clock을 연결했다. 기존
production/default broker와 baseline v1은 변하지 않는다. 별도 v2는 기존 14개 테이블 위에
`central_api_query_cache`를 포함한다. CLI status/seal/restore 및 로컬 offline `run`에 opt-in v2가
연결됐으며 기본값은 v1이다. 새 v2 실행은 복구 후 source clock을 arm하고 cache store 호출을 허용한다.
v2 봉인은 복구된 v1 parent와 frozen source origin을 확인한다. source origin 불일치 복구와 오래된 lease
연결은 거부한다. 로컬 cache expiry/07:00 key boundary/baseline ownership/seed gate focused 58건과 로컬
clock 및 baseline unit 20건이 통과했다. 이어 NAS 임시 RAM-backed, network-isolated PostgreSQL에서 v1/v2
acceptance 7건이 통과했고 skipped=0이었다. 이로써 v1 snapshot 보존과 v2 봉인·복구·rollback·generation
fencing 및 snapshot/clock mismatch 거부를 실제 PostgreSQL에서 확인했다. v1/v2 baseline ID는 삭제된 임시
cluster의 controlled fixture에만 해당하므로 재사용 가능한 NAS baseline이 아니다. `source_state_equivalent=false`이며
성능 검증도 아니다. 임시 컨테이너와 비밀 파일은 제거됐고 운영 active release 및 서버/DB container는
변경되지 않았다. 새 `run` 연결 후보 `2026.10.07-replay-cache-run-v1-329cb888022f0de9`의 NAS 임시
RAM-backed·network-isolated PostgreSQL harness에서 기존 7건과 run gate 3건, 총 10건이 skipped=0으로 통과했다.
반복 source-TTL run, collector/cache 공통 source clock, 제외 mask에서 cache descendant 미주입, COMMIT ACK loss
뒤 drain 및 v1/v2 baseline 복구를 확인했다. report는
`replay-cache-v2-acceptance-ed1cd3ae0653aff1d8db76f329493bc9.log`다. v1 fixture baseline은
`56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`, v2는
`50f49c0274a116d846d0134b4b4379cc96940f539b630e0c7facefbbdbdf2a35`; 두 ID와 임시 cluster는 검사 뒤 제거됐다.
active release 및 서버/DB container는 유지됐다. **완료:** service source-clock 감사의 3개 경로(신고가 TR
기준일/250일 evidence, 분봉 날짜 fallback, membership 공개시각)를 수정했다. 신고가 12건, ingestor 13건,
TOP20 67건이 통과했고 source 날짜 2001-04-03의 native fake broker/store 재현에서 세 누수 모두 사라졌다.
이는 clock 정합성만 검증하며 성능 증거나 전체 service replay가 아니다. 시간 의존 TR의 ingestor shared-clock
바인딩은 미래 native runner의 필수 조건이다. 그 뒤 request-identity
REST/catalog 입력, durable outbox, TOP20 lifecycle runner를 진행한다. 전체 TOP20 실행/기능 ON-OFF replay는
여전히 미지원이다. 앞선 baseline-only report `replay-cache-v2-acceptance-50caa849ae0c62fbcbc8717d8ae13f05.log`는
실행 gate 추가 전의 별도 7건 결과다. 그때의 v2 fixture ID `3e59d961fd9d4c69f215003d1075ce741607a79693c46516964c933614bab291`와
이번 run report의 v2 ID는 서로 다른 삭제된 임시 DB에서 왔으며 재사용하지 않는다.

**2026-10-07 TOP20 원인 receipt·window manifest·cold seed preflight (로컬 구현, service replay 미지원):**
[반복 부하 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)의 구현 결과와 coverage 경계를 따른다.
collector→hub→TOP20 dequeue에 trade/program 입력 receipt를 전달하고 subscriber queue coverage와
`top20_index`/`program_flow`의 제한 sink 분할을 사전 검사한다. TOP20/capture/replay 검사 80개가
통과했다. 새 window 판독은 전체 chunk sequence/checksum 및 payload hash를 확인하고 window store
pair와 capture 시작~window 끝의 TOP20 전달 prefix에 필요한 행만 보유한다. 65분 통제 fixture에서
12,013개 중 13개 행을 유지했다. 한도는 window 10분, capture-relative 7,200초, 보유 metadata 50,000행/32 MiB,
payload 32 MiB이며 초과는 오류로 드러난다. 일반 부분 replay 판정은 유지한다. 이 결과는 로컬 fixture며
실제 NAS trace 검증이 아니다. 추가 cold fixture gate는 versioned semantic state를 확인하고 readiness/
pending write/collector 누적/broker queue·cache/구독/사용 중 lock이 있으면 거부한다. baseline ID,
입력 manifest hash, mask, 원본 component 역할, frozen-before-start source clock을 묶으며, gate는
service 실행이나 DB restore를 하지 않는다. 결합 focused 검사 총 30건 통과. cold fixture이므로
`source_state_equivalent=false`; `database_restore_verified=false`, `execution_authorized=false`,
`top20_session_execution_ready=false`를 유지한다. broker의 cache expiry/reservation이 직접 system clock을
읽는 경로, baseline의 `central_api_query_cache` 누락, durable outbox, REST/catalog adapter 및 실제
service lifecycle runner가 남았다. market_operation/0s·구독 상태도 재생 미지원이다. 활성 NAS와 capture
설정은 바뀌지 않았다. 기존 `recorded_top20_queue_frontier`의 10,000 event 제한도 유지된다. 다음은 이
차단 항목과 dedicated PostgreSQL restore/drain 검증이며, 장중 성능 판단이나 구조 튜닝은 포함하지 않는다.

**2026-10-07 TOP20 원인 입력 후보(로컬 상태):** `top20_inputs` capture 옵션과 schema 3
`ka00198 qry_tp=5` 회차 기록, 최신성·20-slot·재시도만 재생하는 offline 경로가 로컬 작업트리에 있다.
기본값은 OFF다. 이 경로는 downstream subscriber/fundamentals/index/database 작업을 실행하지 않아
전체 TOP20 기능 replay·부하 실험을 지원하지 않는다. 활성 NAS와 10월 8일 capture 예약은 변경되지 않았다.

**2026-10-07 TOP20 최초 편입 수급 입력(비활성 NAS 후보):** `top20-candidate-flow-input/v1` 회차가
`ka10045` 응답의 실제 investor-flow ingest와 완료 marker를 연결한다. 별도 `top20_inputs` 플래그 대신
v6와 호환되는 `store_inputs` opt-in 경계로 기록한다. 실제 consumer/ingest 로직은 전용 replay DB에서
재생할 수 있지만 broker scheduling과 일반 수급 경로는 제외된다. fake-client/SQLite 및 TOP20/broker
결합 회귀 108건과 후보 소스의 flow/replay-operator 16건이 통과했다. NAS 비활성 후보
`2026.10.07-top20-flow-replay-v1-997871fd90ff5074`는 v6 기반 894개 파일로 stage됐으며 manifest와
핵심 변경 파일 해시가 일치한다. active는 계속 `2026.10.07-trace-market-inputs-v6-2597a00f99c13b50`다.
봉인된 전용 PostgreSQL에서 candidate flow 반복 ingest·COMMIT 응답 유실 복구와 mixed collector replay를 포함한
두 acceptance 묶음을 실행했다. 총 9 tests, skipped=0, unittest `OK`였다. 최종 JSON의 `state=failed`는
acceptance runner가 캡처 4개+execution 5개인 9개 대신 7개를 기대한 판정기 오류다. execution-only 기대 수도
3에서 5로 고쳐야 함을 확인했다. 로컬 후보 복사본의 checker는 이 수치로 수정했지만, 895개 전체 release 재복사는
중단해 NAS에는 반영되지 않았다. 기능 테스트 결과와 checker 상태를 구분하며, NAS 비활성 후보 v1·active v6 및
10월 8일 녹화 예약은 변경하지 않았다. 운영 활성화는 보류한다.

**현재 상태 보정(2026-10-07):** 아래의 collector-input/v2 항목 중 “v6 비활성/미활성” 표현은
stage 시점의 기록이다. 사용자가 이후 `2026.10.07-trace-market-inputs-v6-2597a00f99c13b50`를
운영 활성화했으며 build/release 일치와 DB container unchanged가 확인됐다. 후속 health는
`WAITING_MARKET`, `observation_expected=false`라 장외 실시간 구독·수신은 확인되지 않았다.
현재 예약된 capture는 0B/0w/0J/0U의 부분 입력 범위다. TOP20 순위의 `ka00198` 응답과 retry,
news/REST 원인 입력은 포함되지 않으며 이 사실을 전체 replay coverage로 일반화하지 않는다.

2026-10-07 O12 collector-input/v2 후속: 혼합 0B/0w/0J/0U 입력 재생과 같은 component descendant 대체를 구현했다. NAS active `2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f`에서 시작한 비활성 후보 `2026.10.07-trace-market-inputs-v6-2597a00f99c13b50`는 892개 파일, working tree clean으로 stage됐고 `active_changed=false`다. 후보 build/import 및 collector/replay 단위 테스트 35건 통과. v6의 grouped PostgreSQL gate는 capture invariant 4건(`kiwoom_monitor_diagnostic_test`)과 recorded execution 3건(sealed `kiwoom_monitor_replay_test`)을 19.215초에 `tests=7, skipped=0`으로 통과했다. baseline은 `4c4daa238a7e7d4221234087dce35a5b0956caf6629b05896fca7e8df175bbd4`; fixture는 controlled이며 `source_state_equivalent=false`다. 별도 PC controlled-overhead 실행은 active code snapshot과 v6를 각각 OFF/ON으로 3회씩 비교했다. 0B profile은 라운드당 100 WS-shaped messages × 10 ticks(1,000건), mixed profile은 같은 0B에 각 100건의 0w/0J/0U를 추가했고 12개의 isolated in-memory SQLite dataset commits를 확인했다. 모든 조합에서 collector RAM 상태·store payload·hub count가 OFF/ON 및 두 소스 사이에 일치했고, drop/reject=0, written=0이었다. collector copy time은 0B에서 24–40ms/라운드, mixed에서 28–40ms/라운드였다. 입력 처리 paired p50 차이 중앙값은 active snapshot에서 0.34ms/메시지(0B), 0.40ms/메시지(mixed), v6에서 0.37ms와 0.36ms였다. loop p50 차이는 반복 간 일관된 증가가 없었고 OS 스케줄 변동 outlier가 있었다. trace charged bytes는 각 1,000행 표본에서 3.6–4.4MiB, memory high-water는 11.6–12.3MiB였다. Windows 실행이라 process RSS는 측정 불가이며, 이 도구는 PostgreSQL·NAS·collector socket task·디스크 저장 및 09:00~09:10 부하를 측정하지 않는다. 상세 24회 결과는 `artifacts/capture-overhead-20261007-partial.json`에 있다. 이는 capture copy overhead의 부분 비교이지 운영 성능 수용 기준이 아니다. **NAS 후속 측정 완료(2026-10-07):** active 2026.10.07-trace-deferred-ram-v1와 inactive v6 후보에서 0B/mixed·OFF/ON 각 3회, 총 24조건을 실제 NAS Linux 프로세스로 실행했다. fixture parity가 맞고 drop/reject/written은 0이었다. capture ON의 입력 처리 paired p50 증가는 active 기준 0.462ms(0B)/0.467ms(mixed), v6 기준 0.472ms/0.557ms per WebSocket-shaped message였다. 프로세스 RSS 증가는 중앙값 0B 약 1.67MiB, mixed 약 1.95MiB(active)/2.25MiB(v6)였고, trace 내부 charged bytes는 3.58~4.38MiB였다. 이벤트 루프 지연은 반복 간 일관된 악화가 없었다. 단, 전체 cgroup 여유·PostgreSQL·실제 WebSocket task·파일 저장·65분 지속 부하는 측정하지 않았다. 보고서는 rtifacts/capture-overhead-20261007-nas.json이다. **남음:** 이 결과는 작은 합성 fixture의 recorder copy 비용만 확인한 것이므로 성능 승인이나 장초 전체 부하 기준선으로 일반화하지 않는다. 후보 v6는 아직 활성화하지 않는다. 다음 정상 장중 capture를 우선 확보하고, 그 동일 trace의 현재 코드 baseline replay와 workload coverage를 검증한다. TOP20·뉴스·REST 원인 replay, cross-component causal replay, 초기 RAM 상태 등가는 계속 미지원이다.

2026-10-07 O12 replay coverage 후속: [기능별 범위와 구현 순서](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#2026-10-07-재생-범위-감사와-다음-구현-결정)를 확정했다. v6의 0B·0w·0J·0U collector 입력 재생, 같은 component market_state sink 대체, grouped NAS PostgreSQL gate 및 controlled capture overhead 검증은 완료됐지만 이는 실제 장중 상태 등가나 전체 앱 성능 acceptance가 아니다. TOP20 subscriber·뉴스·REST outcome 원인 replay, generated-ID/clock adapter, 초기 RAM 상태 동등성, 기능별 인과 제외, 긴 capture의 bounded reader는 별도 미완료다. store 입력이 기록된다는 사실만으로 현재 executor 지원이나 전체 앱 원인 재현을 완료 처리하지 않는다. 사용자가 2026-10-07 v6를 활성화했고 배포 출력은 health ok, build/release 일치, DB container unchanged를 확인했다. realtime phase는 CONNECTING으로 구독·수신 정상 여부가 미확인이다. 10월 8일 capture/verification 예약은 v6 build와 0B·0w·0J·0U를 검사한다. 부분 trace 결과는 지원된 입력과 발생 건수만 보고하고 미지원 workload를 전체 coverage로 일반화하지 않는다.

2026-10-07 O12 시장 TR 원인 replay 우선순위(최초 정적 감사 당시 상태): TOP20 root input은 `ka00198 qry_tp=5`이고 유효 응답이 membership publish, 0B 구독 갱신, 신규 종목 준비와 index 작업을 시작한다. 이후 로컬 후보에서 이를 위한 opt-in `top20_inputs` 기록과 순위 최신성·20-slot·재시도 판정만 실행하는 validation replay를 추가했다. 이는 순위 입력 판정의 반복 검증이지 membership publish, 0B 구독, 신규 종목 준비/index fan-out을 재실행하는 전체 TOP20 workload replay는 아니다. 이 입력 경로는 기본 OFF이며 활성 NAS/예정 capture에는 적용되지 않았다. 다음 후보는 fan-out에서 발생한 `ka10001/10100`, `ka10080/81`, `ka10045`의 최소 응답 payload와 descendant ID 연결이다. `ka90008`, index, VI, 역사적 고가는 다른 owner/trigger라 TOP20과 한 workload로 묶지 않는다. 세부 mapping과 최소 payload 범위는 [반복 부하 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#2026-10-07-kiwoom-resttr-원인-입력-경계-확인)에 기록했다. live 호출 빈도·운영 성능 영향 및 전체 fan-out replay는 미검증이며, capture는 기본 OFF로 둔다.

2026-10-07 O12 deferred-RAM trace follow-up (candidate staged, not active): implemented opt-in
`persist_at`, 4GiB charged-memory budget, 1,000,000 event cap, host+cgroup 5GiB startup
headroom gate, and 1MiB/s paced post-deadline persistence. Local deferred lifecycle/chunk/trace/input
regressions passed in grouped runs (46 tests). Candidate
`2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f` was posted as an immutable inactive release
from exactly active release `2026.10.06-top20-daily-freshness-v2-8fe3781e9c3ba685`; it contains
892 files and staging left the active pointer unchanged. NAS candidate tests/deployment are still
pending because SSH can read the NAS but `sudo` requires an interactive password. The API and controls
remain on the old build. The existing 08:55 capture automation now fails closed unless the new build
and deferred capability are active, and requests `persist_at=20:10 KST`; verification is moved to
October 9 at 02:30 KST to allow paced persistence to finish. Before the capture, run source-runtime
tests on NAS, verify container headroom and `/health`/capabilities, then activate only the server.
require complete state, accepted/written equality, zero drops/rejections, all chunks/checksums/sequence,
and captured/persisted timestamps. Do not use the old 10:01 verification schedule. Process restart/OOM
before 20:10 loses volatile payloads by design; disk capacity and actual RSS/65-minute market load
remain unverified.

2026-10-06 O12 trace-recorder-v3 확인: 사용자가 배포한 active release/build
`2026.10.06-trace-recorder-v3-e8bfc0fa8ce80117` / `2026.10.06-trace-recorder-v3`를
인증 API로 확인했다. 장외 60초 smoke trace `20261006T122707Z-ffbb6ed84bbb`는
588/588 accepted/written, known_dropped=0, censored=false로 완결됐으나 input_rejected 10건
(의도적 제외 2, 미지원 `load_documents` 6, shadow payload budget 2)이 있다. 0B가 없고
미지원 writer 입력도 있어 이 trace를 장중 replay baseline으로 쓰지 않는다. recorder v3의
API/저장 동작 확인이지 장초 수집 완전성, capture overhead, 운영 DB 부하 검증이 아니다.
후속 PC 후보는 거부 이유에 collection 및 byte/node 한도 관측치를 payload 없이 덧붙여
다음 짧은/정상 장중 capture에서 거부 경로를 구분한다. 관련 recorder/trace/capture unit 묶음은
28/28 통과했다. 현재 코드 전체의 다른 미커밋 작업과 혼합하지 말고 이 후보만 분리 검토한다.
남음: 지원할 `load_documents` collection/consumer를 실제 재생 계약으로 확정할지 판단하고,
PostgreSQL 전용 capture 테스트를 dependency가 갖춰진 환경에서 실행하며, 이후 정상 장중 trace를
현재 활성 코드 baseline으로 완전성 gate부터 통과시킨다. 이 과정 전까지 해당 입력을 조용히
추가/제거하거나 운영 release에 반영하지 않는다.

2026-10-06 O12 `load_documents` 거부 경로 정적 대조: 현재 replay allowlist와 literal callsite를
비교해 `server_operational_settings`(앱 설정 초기 로드), `credential_vault_state`(credential
store 상태 marker), `execution_mock_automation_runner_current`(mock runner 복구 상태)가
명시적 누락 후보임을 찾았다. `app.py`의 동적 일봉 coverage 상수는
`daily_bar_history_coverage`로 허용 목록에 있다.
`ForwardEvaluationRepository`의 collection 상수는 forward report/spec, feedback/revalidation,
mock automation 실행·복구 상태를 가리키며 허용 목록 바깥이다. 호출은 연구용 mock 후보/spec
API의 명시 요청과 mock automation runner/supervisor의 활성화·복구 경로에서 발생하므로,
일반 장중 hot path나 ownerless 수집으로 분류하지 않는다. 기존 v3 trace는 거부 당시
collection 이름을 보존하지 않아, 이 중 무엇이 실제 6건을 만들었는지 소급할 수 없다.
신규 metadata-only 후보가 다음 capture에 기록할 이름과 대조한다. Replay는 기록된 인자로
진단 DB에서 실제 store method를 실행하고 원본 상태 등가를
보장하지 않는다(`source_state_equivalent=false`). 따라서 collection을 전부 allowlist에 추가하는
것은 보류하고, 정확한 collection별 consumer·시험 DB seed·복구 영향이 확인된 읽기부터 따로 승인한다.

2026-10-06 O12 C1 0w 종료 보존: PC에서 재현 3건 실패→수정 후 관련 74건 통과.
`2026.10.06-top20-program-drain-v1`은 단일 owned save/drain·최종 flush와 실패 시 최신
pending 보존을 구현했다. NAS 후보 `2026.10.06-top20-program-drain-v1-b619e0fc91db27cf`
(890 files)의 전용 PostgreSQL 검사 2건은 skipped=0, 0.562초로 통과했다. 2026-10-06 사용자
배포 결과에서 active source release와 server build가 각각 후보 ID와
`2026.10.06-top20-program-drain-v1`로 일치했고 health는 `ok`, realtime phase는 `READY`,
database container는 unchanged였다. 이는 정상 재시작 후 health 확인이며 실제 장중 0w 입력
보존·강제 종료·지속 DB 장애·미처리 subscriber queue의 영속 복구나 성능 개선을 뜻하지 않는다.
저장 주기·batch·DB transaction과 UPSERT는 그대로다. 로컬 전용 DSN이 없어 NAS PostgreSQL
검사 전 로컬에서는 해당 2건이 skip된 상태였음을 함께 기록한다.

2026-10-06 O12 전체 부하·외부 입력 감사 후속:
[부하 분류/검증 기준](WHOLE_APP_LOAD_OPTIMIZATION_REVIEW.md),
[collector lifecycle/최신성 원장](EXTERNAL_INPUT_LIFECYCLE_AUDIT.md).
숨김+알람 OFF 후보 polling(A1)과 뉴스 child close timer(P8)는 PC 소스에서 수정·기능 검증했다.
뉴스창 close 시 60초 준비 timer를 멈추고 재열기 시 저장 자료를 즉시 다시 준비한다. 실제
외부 요청 수·DB/CPU 절감량은 미측정이다.
P9 중앙 sync는 뉴스/AI/매매일지 링크의 실패 push 재시도와 체결 진입 뉴스 snapshot용 PC cache
갱신 소비자가 있어 유지한다. 창 닫힘 중 delta pull 비용 및 on-open catch-up 대안은 실측·정합성
비교 전까지 보류한다.
0w 종료 drain과 기본정보 갱신 부족은 별도 정합성 항목으로 남긴다. 일봉 coverage/신고가(U1)는
SQLite 재현으로 결함을 확인했다: 같은 code/day의 일봉 값 변경 뒤 coverage fingerprint가
불일치가 되어도 TOP20 stage RAM 표식이 준비 완료로 남아 재검증 task가 예약되지 않는다.
재진입해 `_ensure_historical_high`를 직접 호출해도 `checked_on >= day` 조건만으로 기존 신고가를
그대로 반환한다. 이 때문에 변경된 원본으로 coverage가 무효화된 뒤에도 표시/소비되는 신고가가
오래될 수 있다. 매 30초 순위 주기마다 일봉을 재조회하는 방식은 추가 부하 때문에 채택하지 않는다.
2026-10-06 로컬 수정: canonical daily UPSERT가 실제 변경된 `(trading_date, code, market)` 키만
반환하고, 변경 또는 COMMIT 응답 불확실 시 해당 종목·시장 세대만 무효화한다. 순위 schedule은
DB 재조회 없이 RAM 세대와 stage marker를 비교하며 기본정보·분봉·수급은 유지하고 일봉·신고가만
재검증한다. 동일 값 재수신은 기존 준비를 보존한다. 일봉 coverage 기록과 신고가 계산 중 입력이
다시 바뀌면 완료 표식을 발급하지 않는다. 신고가 문서에는 KRX 및 대상이면 NXT의 canonical
일봉 fingerprint/coverage identity를 넣어 재시작 뒤에도 같은 날의 오래된 계산을 구분한다.
저장 실패는 커밋됐을 가능성을 고려해 RAM 준비 상태를 보수적으로 무효화하고, 다음 확인에서
원본 coverage가 그대로면 새 TR 없이 다시 신뢰할 수 있다. SQLite·TOP20·ingest focused 회귀
158건과 핵심 focused 9건 통과. NAS 전용 PostgreSQL 후보에서 canonical 변경키 반환, 동일값 재수신,
중복 key와 metadata, invalid metadata rollback을 확인하는 3건이 0.303초에 통과했다. 이후 사용자가
v2 후보를 활성화했고 `/health`의 build/release는 후보와 일치했으며 database container는 unchanged였다.
직후 `CONNECTING`에서 이후 `WAITING_MARKET`, `observation_expected=false`로 바뀌었다. 조회는 23:26
KST 장외 표본이며 ranking snapshot은 08:05, top20_index는 19:59, market_state는 15:32였다.
장중 WebSocket 재구독과 체결 수신은 아직 확인할 수 없다.
이 검증은 저장 정합성만 다루고 성능 이득은 측정하지 않았다. 현재 화면/API가 보관된
과거 신고가를 계산 완료 여부와 별개로 계속 제공하는 의미는 별도 consumer 검토 대상으로 남긴다.
외부시장 일봉 실패 뒤 날짜가
완료 처리되어 재시도가 막히던 결함은 코드 주입으로 재현하고 로컬 수정했다. 실패한 월물만
다음 polling에서 재요청하고 이미 성공한 월물은 그 날짜에 중복 fetch하지 않도록 했다. 관련 PC
collector/runtime 테스트 20건과 focused 회귀 2건에 DB 저장 실패·재시작 복구 검사 2건을 더해
관련 두 모듈 22건이 통과했다. 이는 stub store·synthetic provider 검사다. 수정은 PC 소스에만 있고
NAS에는 미반영이다. 실 Yahoo 응답 및 NAS 후보 실행은 별도 확인 대상이며, 성능 개선을 측정한
결과는 아니다.
Naver/DART pagination·cursor 공백 후보는 현재 활성 source와 기존 coverage 증거부터 대조한다.
현재 incomplete capture는 전체 성능 baseline이나 주기/batch/writer/concurrency 변경 근거가 아니다.
정상 capture와 현재 코드 baseline을 먼저 고정하고 peak 기여가 큰 변경부터 하나씩 비교한다.
새 상시 계측은 만들지 않으며, 기존 로그로 부족한 경로에만 한정된 임시 확인을 허용한다.

2026-10-06 O12 trace recorder 후속(NAS 후보 미활성): NAS에 비활성 v2 후보를 복사해
private-control 검사 4종을 돌렸다. 모두 pass, accepted=written, known_dropped=0이었다.
grouped 검사 32회 fsync 합계 19.905초, 최대 단일 fsync 10.682초를 보여 NAS 종료 지연의
직접 원인이 recorder 파일 동기화 대기임을 확인했다. 이 결과는 진단기 파일 I/O만 포함하고
운영 업무의 DB/WAL 지연이나 장중 recorder overhead 전체를 대표하지 않는다. 또 종료 manifest
동기화가 끝나기 전에 `complete`가 노출되는 정합성 결함을 재현해 PC 코드와 회귀 2건을 수정했고
관련 drain/manifest 테스트 15건이 통과했다. 수정본은 후보
`2026.10.06-trace-recorder-v3-e8bfc0fa8ce80117`(891 files)로 NAS에 stage했고 active는 그대로다.
v3 묶음 시험, NAS 재검증, ON/OFF 비용/RSS, peak/2배 burst 65분 검증이 남아 있으므로
후보를 활성화하지 않는다.

2026-10-06 O12 trace 묶음 동기화/메모리 후속(PC 소스): payload를 최대 16MiB
청크 묶음으로 보존해 fsync를 묶음당 한 번 수행하고 RAM charge 예산을 256MiB로
상향했다. 기존 schema-2 payload JSON/API bytes와 hash dedup은 호환한다. 실제
20261005T235952Z-c138934f2486 manifest에는 capture_memory_full 96,876건,
known_dropped 35,251건과 payload fsync 합계 약 3,548초가 기록돼 있어 이 두 경로를
개선 대상으로 삼았다. input_rejected와 known_dropped는 서로 다른 지표이며 합산한
숫자를 앱 원본 데이터 유실 건수로 취급하지 않는다. 로컬 burst 검사는 기존 48MiB
queue 예산을 넘는 입력을 수용하고 64개 unique payload를 fsync 2회로 저장했다.
NAS 배포·capture ON/OFF 비용/RSS 대조 및 실제 peak/2배 burst 65분 검증은 남아 있다.
한도 확대는 무손실 보장이 아니며 기존 단일 사본 8MiB/미지원 입력 거부도 유지된다.
PC에서 batching·256MiB 메모리 예산·65분 bounded burst 관련 단위검사 13건은 통과했지만,
활성 NAS release의 64MiB recorder와는 다른 코드이므로 NAS 수용이나 운영 오버헤드 증거가 아니다.
별도 PC 프로세스의 5,000건×3회 synthetic OFF/ON은 recorder 추가 p95 38.2~44.6µs,
RSS 표본 증가 약 10.4MB, 매회 payload fsync 1회와 drain 209~241ms였다. DB/네트워크/NAS IO는
측정하지 않았으므로 기존 65분/2배 burst gate와 NAS OFF/ON 비용·RSS 검증은 계속 미완료다.
20261005T235952Z-c138934f2486은 chunk 160개 무결성이 모두 일치하지만 35,251 sequence 누락,
97,849 payload 입력 거부(그중 capture_memory_full 96,876), 거부/누락 없는 1분 구간 0개라
전체 또는 부분 replay source로 사용할 수 없다. 캡처 시작 당시 활성 NAS recorder는 64MiB였고
PC의 256MiB 변경은 아직 NAS에 반영되지 않았다. 다음 capture 전 후보를 활성화한 뒤 OFF/ON 비용·RSS,
실제 peak 및 2배 burst를 먼저 검증하고, 정상 장중 capture 완전성 gate를 통과시킨다.

2026-10-06 O12 trace 입력 API 후속: `/api/v1/diagnostics/trace`가 `store_inputs`와
`collector_inputs`를 기본 OFF의 strict boolean으로 받아 schema-2 recorder까지 전달한다.
capabilities는 schema 2, 0B 범위, 관측 전용 coverage 및 미검증 overhead를 광고한다.
API/capture 관련 로컬 회귀 33건 통과. 후보
`2026.10.06-recorded-capture-api-v1-af7d3d49b61aad09`(888 files)를 NAS source-runtime에
stage하고 사용자가 활성화했다. `/health`와 인증 capabilities API에서 server/capabilities build,
source release, schema 2, `0B`, 기본 OFF 두 옵션을 확인했다. master/trace는 OFF이며 실시간은
장외 `WAITING_MARKET` 상태다. 08:54 KST 시작 예약은 expected build와 schema-2
capability를 preflight하고 `store_inputs=true`, `collector_inputs=true`를 보내도록 갱신했다.
10:01 검증 예약도 schema/옵션, 무손실·입력거부·체크섬·sequence 및 collector/store event 수를
확인하도록 갱신했다. 운영 active 전환은 아직이며 preflight 실패 시 capture는 시작하지 않는다.
실제 65분 event 보존, drop 0, 파일 checksum, 입력 수 및 capture overhead는 실행 후 검증한다.

**현행 O12 상태 (2026-10-06):** 전용 replay DB baseline lease/restore와 선택 실행 offline CLI를 구현했고,
기존 관련 unit 회귀 50건이 통과했다. NAS operator helper는 고정 role/database 준비와 비밀
저장, controlled fixture 봉인, PostgreSQL acceptance 4건을 한 번에 수행한다. helper·lease·CLI·
direct-access audit 25건이 통과했다. NAS에서 acceptance 4/4 통과, skipped=0 (2.787초),
baseline ID `4c4daa238a7e7d4221234087dce35a5b0956caf6629b05896fca7e8df175bbd4`를 확인했다.
기준은 controlled fixture라 `source_state_equivalent=false`이며 실제 capture 시작 상태의
재현은 검증되지 않았다. 로컬 CLI/실행기 회귀는 총 47건 통과했다. NAS 후보
`--execution-gates` 2건도 2/2 통과했다(9.595초, skipped=0). 반복 재생·workload 제외·원본/재생
DB-call 연결·COMMIT 응답 유실 뒤 baseline 복구를 검사했고 baseline ID가 유지됐다. 실행 전후
active release와 두 컨테이너 ID도 동일했다. generated-ID/projection adapters, 공개 run/비교 API,
capture overhead 및 실제 장중 capture replay acceptance는 남아 있다.

선택 replay 후보 `2026.10.06-recorded-replay-execution-v1-624aade862f1b298`(887 files)는
X: source-runtime에 stage했고 `active_changed=false`를 확인했다. 운영자 실행 파일은
`X:\kiwoom-monitor\artifacts\check-recorded-replay-execution-v1.sh`이며 SHA-256은
`4fa890b00abe4ba7561f888f9437ea5bc4e8542e93beb6e154078f5024f03088`다. 이 파일은 고정 후보
경로·현재 bind mount·기존 두 컨테이너를 확인한 뒤 새 프로세스로 후보 helper의 PostgreSQL gate
2건을 실행하고, 종료 뒤 active pointer와 컨테이너 ID가 바뀌지 않았는지 검사한다. 결과는 2/2
통과, skipped=0, 9.595초다. baseline ID와 cleanup이 보존되고 `Acceptance verified` 출력까지
확인했다. controlled fixture 결과이므로 장중 상태 재현이나 성능 acceptance로 해석하지 않는다.

2026-10-05 O12 다중 workload 부하 실험 다음 단계: [반복 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md).
schema-2 capture·원인 사건·bounded window reader·선택 compiler에 더해 내부 실행기를 로컬
구현했다. 지원 allowlist store operation은 actor별 순서·actor 간 동시성으로 재생한다. 실제
0B 사건은 collector loop로 넣고 동일 component의 과거 sink만 제외하며 다른 생산자는 유지한다.
재생 ID와 DB 관측 context를 연결한다. 취소/drain 및 혼합 경로를 포함한 관련 단위 회귀 29건
통과. caller-provided test store 내부 검사이며 공개 API나 전용 replay PostgreSQL 검증은 아니다.
이후 baseline lease/restore·run lock과 operator helper를 구현했다. 실제 PostgreSQL
rollback/sequence/foreign-session acceptance는 NAS 전용 replay DB에서 4/4 통과했다.
미지원/generated-ID operation adapter,
캡처 코드 A/B 실행·결과/API, NAS capture overhead와 실제 장중 acceptance는 남았다.

2026-10-05 O12 bounded window reader 후속: operation 입력은 선택 구간에 한해 payload를
hydrate하고, `collector_with_background`는 지정 component의 prefix를 최대 15분까지
포함한다. capture 종료 monotonic 시각을 넘는 범위와 32MiB 초과 payload window는 거부한다.
chunk checksum·sequence·manifest를 capture 전체에서 확인한다. trace/capture 동기 회귀 17건이
통과했다. Windows sandbox에서 asyncio Proactor가 socketpair 초기화에 멈추는 기존 collector
비동기 단위검사 2건은 실행 완료로 세지 않는다. NAS active release 및 운영 자료는 변경하지 않았다.

2026-10-05 O12 capture 전용 PostgreSQL gate 4건과 기존 collector→DB 2건의 첫 NAS 실행에서
4건 통과, schema-1 reader 사용 오류 1건, stale 실패주입 1건을 확인했다. 두 테스트만 수정해
새 불변 후보 `2026.10.05-recorded-capture-gate-v2-81fc0b5e7f04dbcf`(877 files)를 stage했다.
stage checksum/import는 통과했고 운영 active pointer는 기존 v2를 유지한다. 남음: 새 후보에서
6건 묶음 재실행. 동기 단위검사 2건과 py_compile은 통과했으나 현재 샌드박스의 Windows asyncio
socketpair 제한 때문에 비동기 로컬 회귀를 완료하지 못했다. 운영 활성 릴리즈·DB는 변경하지 않는다.

후속 v2 NAS 결과에서도 collector 실패주입 검사가 실패했다. 분봉 metadata가 분 시각만으로
연결되어 같은 분에 저장되는 종목 observation끼리 충돌하는 실제 writer 결함을 확인했다.
SQLite/PostgreSQL lookup을 `(subject, minute)`로 좁히고 전용 PG 회귀를 추가했다. 로컬 대상
SQLite 검사 및 정적 검증은 통과했다. 불변 후보
`2026.10.05-recorded-capture-gate-v3-ed9daa478d48ac35`(877 files)의 manifest checksum/import도
확인했다. NAS 전용 PostgreSQL gate 7/7이 통과했다. candidate는 검사만 했으며 기존 active
release와 운영 DB는 변경하지 않았다. 운영에 이 writer 수정을 반영하려면 별도 release 전환이 남았다.

2026-10-05 O12 collector fixture 단계 기록 (이후 상태는 위 최신 항목으로 갱신): 실제 0B parser·RAM 집계·저장 loop를
네트워크 없이 실행하는 `diagnostic_collector_replay.py`와 고정 6분 fixture를 추가했다.
세 시장·중복/지연 체결·RAM 표시/query authority·구독 reset/gap·취소 후 owned write 대기를
검증했다. 확대 130건 중 127 통과·전용 PostgreSQL URL 미설정 2 skip·기존 trace route 계약
fixture 누락 1 실패다. **기반 검증 잔여:** 새 PostgreSQL 묶음 2건을 NAS 전용 DB에서 실행한다.
이 기록 이후 schema-2 multi-workload input capture와 plan compiler의 핵심을 로컬 구현했다.
API workload·보고서 observer join·master/measurement/drain 연결, 기존 helper의 report_url 대기,
capture 전용 PostgreSQL 검사와 65분 overhead 검증은 여전히 후속이다. 현재 NAS active release는 기존 v2이며
이 로컬 구현의 저장 감소율·장중 부하 효과를 실측 완료로 보지 않는다.

2026-10-04 자격증명 DB 회귀에서 확인한 기존 DART API 검사 실패: `tests.unit.test_dart_credential_owner.DartCredentialAPITests.test_keyless_on_server_collects_disclosures_after_default_profile_activation`은 활성화 뒤 뉴스 검색 결과가 생길 것을 기대하지만, `create_app`은 `CentralNewsService(read_only_search=True)`로 구성되어 검색 중 외부 수집을 하지 않는다. 같은 테스트가 변경 전 `database.py` 및 `credential_store.py` 복사본으로도 동일하게 실패해 이번 DB 이동으로 생긴 회귀가 아님을 확인했다. DB 범위와 분리된 뉴스 테스트/제품 계약 문제로 남겨 둔다.

2026-10-04 MainWindow → AppController 단계 A-D 로컬 구현·회귀 완료: 가격/고가/시가총액, 종목·시장 분봉 및 비교 pending, timer, 실패 복구를 AppController로 옮겼다. 변경 전 정적 연결 116건은 MainWindow 99건 + AppController 이동 17건으로 모두 대응했고 UI/control 연결 17건이 추가됐다. 현재 worktree `src` 기준 offscreen 관련 검사 142건이 통과했다. **남음:** 실제 설치 테스트 앱에서 순위·NXT/일반 세션·차트·뉴스·일지·설정/API 재연결·종료 흐름을 확인하고, 거래 중 실제 체결·시장 전환 검증은 장중에 수행한다. 이번 작업은 커밋·NAS 반영을 하지 않았다. 근거와 연결별 대응은 [계획](MAIN_WINDOW_APP_CONTROLLER_PLAN.md), [정적 연결 대조](MAIN_WINDOW_CONNECTION_COMPARISON.json), `artifacts/app-controller-validation/stage-d-suite.log`에 있다.

2026-10-04 MainWindow AppController 단계 A/B 로컬 구현 완료: A의 앱 작업 객체 소유·종료 조정과 B의 API runtime 교체·초기 순위 시작 흐름을 이전했다. AppController가 기존 feature controller 12개, 조건부 MarketCacheWriter/EntrySnapshotWriter, 동적 TOP20 NAS worker set, 테마 저장 QThread, API runtime factory/current client 및 교체 상태를 소유한다. MainWindow는 기존 신호 receiver와 위젯 동작, API 결과 표시·cache/subscription/aggregator 적용 callback을 유지한다. 변경 전 정적 connection 116건은 모두 의미 기준으로 대응되고 제거/미대응은 0건, 종료·ranking·API 결과 연결 7건이 추가됐다. 기존 MainWindow/controller regression 묶음과 AppController 검사 130/130 통과, process exit 0. producer 후착 결과/flush-drain 순서, runtime 성공 교체·factory 실패 시 기존 runtime 보존, 교체 중 close 취소를 검사했다. 설치된 앱 전체 흐름의 수동 검증 및 NAS 배포는 하지 않았다. 다음 단계 C(rank/subscription/followups), 이후 D(pending state ownership)를 진행한다. 상세 [설계·구현 기록](MAIN_WINDOW_APP_CONTROLLER_PLAN.md), [정적 연결 비교](MAIN_WINDOW_CONNECTION_COMPARISON.json).

2026-10-03 O12 `query_minute` trace replay: NAS active release `2026.10.03-db-minute-replay-v1-388841088675094c`의 저장 trace `20261003T054415Z-1984933624e2`에서 세 합성 시나리오 replay를 마쳤다. 이전 trace에는 shape counters가 없어 변경/중복/revision 분포는 복원되지 않았고, 기존 세 run의 schedule lag p95는 1.16초·1.40초·9.44초로 `timing_preserved=false`였다. 후속 후보는 필수 호출별 계수로 partial-change recipe를 만들고 실제 DB 계수와 비교하며, 설명 불가·누락 shape 및 mismatch를 거부한다. 동일값 duplicate와 호출별 독립 subject만 재현하고 원본 키 관계·payload·신규/변경 비율은 복원하지 않는다. PC 단위/API 회귀 34건 및 NAS candidate 전용 PostgreSQL 검사 6건 통과(18.268초). immutable 후보 `2026.10.03-db-minute-recorded-shape-v1-94fdce4bf35406a7`는 준비됐으며 active pointer·운영 DB·서버 이미지는 변경하지 않았다. NAS unit bundle은 설치된 Starlette가 요구하는 시험 전용 `httpx2`가 server image에 없어 import 단계에서 시작하지 못했으며, 동일 34건은 PC `.venv`에서 통과했다. **남음:** 새 shape trace에서 recorded-counts replay와 실제 관측 계수 대조, trace 오버헤드·65분 내구성 검증 및 10월 6일 capture 확인. Codex heartbeat `nas-65-minute-db-trace-capture` (08:54 KST)와 `verify-nas-db-trace-capture` (10:01 KST)는 2026-10-06 1회 실행으로 활성화되어 있다. PC와 데스크톱 앱이 켜져 있어야 하며 예약은 capture 성공의 증거가 아니다.

2026-10-03 O12 장후 선택 재생: active release `2026.10.03-db-trace-replay-v1-48d061e4289a6e37`에서 60초 trace `20261003T054415Z-1984933624e2`를 완료했다. 633 이벤트 전부 chunk로 기록됐고 known drop 0, checksum 12/12, 시작/완료 280쌍이 일치했다. 뒤이어 선택한 `news_job_claim`·`shadow_monitor_state` 부분 재생 `20261003T054931Z-2eba0750`가 dedicated `kiwoom_monitor_diagnostic_test`에서 38/38 완료, 미실행·backpressure·오류 0, shadow fixture 행 검증 및 정리, 관측 DB call 38건 상관 확인을 통과했다. 종료 후 master/capture/trace OFF, pause 없음. **남음:** trace ON/OFF 비용 비교, 장중 08:55~10:00 예약·실행 수용, `query_minute`/TOP20/readers 등 미지원 경로 adapter 검토. 이번 토요일 trace는 장중 부하가 아니며 `synthetic_shape`가 원래 payload/key 분포·실제 WAL/lock 경쟁을 복원하지 않으므로 성능 동등성이나 병목 원인으로 해석하지 않는다.

2026-10-02 O12 경량 trace 구현 진행: [장중 trace 설계](DB_REPLAY_TRACE_DESIGN.md)에 bounded queue/chunk 보존, raw 독립 제어, master TTL 7200초, restart OFF, 부분 재생의 입력·lane·coverage 계약을 확정했다. 로컬 trace 및 DB 관측 검사 88건과 NAS v2 전용 DB 검사 2건이 통과했다. v1에서 찾은 연결 종료 후 DB 이름 조회 오류는 v2에서 수정했다. v2 immutable release를 stage했으며 활성 전환·실제 trace smoke·오버헤드·13시 자료 확보·10월 6일 예약은 미완료다. 기존 replay pilot의 미지원 writer를 해결했다고 보지 않는다.

2026-10-02 O12 개장 부하 replay 원본 보존 설계: 기존 `measure`는 최대 300초, 공통 DB raw는 프로세스별 50,000건 bounded deque, DB 호출 API 조회 구간은 최대 1,800초이므로 08:55~10:00의 65분을 하나의 완전한 원본으로 보장하지 않는다. 현재 `record_db_call`은 capture 중 호출마다 제어 파일 상태를 확인하고 기존 raw는 단계별 세부 표본까지 담아 개장 capture의 비용을 별도로 측정해야 한다. 원본 payload 전체를 저장하지 않는 경량 이벤트 형식, 순번 기반 무손실 chunk 경계, 제한량·유실 표식, 재시작/TTL/취소 복구, background flush의 NAS I/O 영향, 실제 writer 입력 복원 가능성을 먼저 결정한다. 2026-10-05 월요일은 KRX 휴장 안내가 있으므로 실제 개장 첫날 08:55~10:00(특히 09:00~09:10)을 대상으로 준비하며, 실행 전 거래일 여부를 다시 확인한다. 현재 장후 부분 재생 pilot의 2026-09-30 23:15 KST 표본 166건 중 지원 호출은 빈 뉴스 claim 36건뿐이어서 개장 부하 원본으로 쓰지 않는다.

2026-10-02 O12 장후 DB 호출 부분 재생 pilot: 로컬에서 완료된 raw 측정 보고서의 시간 구간·writer 선택을 전용 PostgreSQL 재생에 연결했으나, 지원 종류는 빈 뉴스 claim과 기존 inline shadow checkpoint에 한정된다. NAS 전용 DB 실제 실행·정리 확인과 원본/재생 호출·WAL/COMMIT 비교가 남는다. 특히 현재 과거 보고서의 shadow 호출은 `sql_calls=1`이어서 현행 2-SQL 저장과 동일 재생할 수 없다. 0B·분봉·TOP20 및 read 호출의 payload/transaction 계약을 별도 검증하기 전에는 장중 병목 원인 탐색의 전체 replay 또는 writer 제외 실험으로 확대하지 않는다.

2026-10-01 O12 shadow checkpoint frame 저장 후보: PostgreSQL 전용 migration 21과 opt-in header/frame 저장을 구현했고 SQLite는 migration 20 inline 호환을 유지한다. 전용 DB 계약 검사 5/5와 현실 dataclass fixture 비교 3/3 통과. 1,803 frame·1,130,335-byte 입력의 단일 frame DML EXPLAIN에서 inline WAL 151,643 bytes/213 records, frame 합계 1,499 bytes/8 records였다(약 99.0% 감소). DML 실행 합은 28.321ms→23.059ms, shared hits는 313→501이었다. 추가 public writer 비교는 각 경우 3회이며 inline 2 SQL 대 frame 5 SQL, execute median 약 68ms 대 73~77ms였다. cursor-only/one-frame/50-frame 총 column payload는 inline 138.5KB 대 frame v2+downgrade fallback 1.368MB(약 9.9배)다. median total은 cursor-only 184ms inline 대 2,644ms frame, one-frame 264 대 269ms, 50-frame 269 대 275ms였다. COMMIT은 양쪽 경로에서 수 초 outlier가 관측됐고 각 사례 3표본이므로 outlier 원인이나 안정적인 latency 우위를 귀속할 수 없다. **판정:** WAL 감소만으로는 추가 SQL·execute 비용과 약 10배 column payload를 정당화하는 전체 writer 성능 개선이 확인되지 않았다. writer 기본값 OFF를 유지하고 운영 활성화·배포는 하지 않는다. 별도 index/relation 실제 크기와 장중 효과는 측정하지 않았으며 현재 후보를 성능 개선으로 승인하지 않는다. 더 진행하려면 저장 형식의 용량/안전성 목적과 운영 trade-off를 먼저 다시 판단해야 한다.

2026-10-01 O12 VI 동일 이벤트 동시 저장 완화: 같은 불변 `event_key`의 저장이 진행 중이면 완료 결과를 공유해 중복 transaction을 생략하고, 선행 저장 실패 시 대기 이벤트가 다시 저장한다. 서로 다른 이벤트의 독립 transaction은 유지하고 완료 키 캐시는 두지 않는다. 같은 키의 동시 저장 10쌍과 transactionid 대기를 관측했으나 중복 수신의 상위 원인은 아직 특정하지 못했다. 시장 이벤트·실시간 수집기·허브 로컬 회귀 41건과 전용 PostgreSQL 통합검사 4/4가 NAS에서 통과했다(중복 공유, 실패 후 재시도, 취소/종료 중 commit 대기, commit 확인 응답 유실 후 재시도). 실패 로그 2건은 테스트가 주입한 오류이며 각 복구 assertion은 통과했다. **남음:** 운영 배포 및 동일 장중 조건의 경합·WAL·저장장치 전후 비교. 현재 성공은 기능/정합성 검사이지 부하 개선 입증은 아니다.

2026-10-01 O12 TOP20 진입 준비·기본정보 reader: NAS source v2 `2026.10.01-top20-entry-preparation-v2-e55eb967f5e572c2`에서 단계별 재시도와 KRX/NXT 일봉 분리를 적용했고 전용 DB 검사 4건이 통과했다. 동등한 90초 표본에서 출처가 명확한 `top20.entry_daily_history` 일봉·coverage reader가 각각 95→0회, candidate_flow_capture·historical_highs가 각각 60→0회 관측됐다(이 두 kind의 baseline source는 unattributed). 남은 기본정보 60회는 NAS `server.log`에서 매 30초마다 같은 TOP20 코드들을 조회한 `/api/v1/content/stock_fundamentals` 요청으로 확인했다. PC `monitor.sqlite3`의 해당 20코드는 필수 값이 있지만 `fundamentals_updated_at`이 UTC 날짜(2026-09-30 22:xx)로 남아 KST trading day와 비교할 때 매 회차 stale 처리됐다. `StockRepository.fundamentals_to_refresh`를 SQLite timestamp의 KST 날짜로 비교하도록 로컬 수정했고 새 날짜 경계 검사 포함 저장소 단위검사 13건이 통과했다. **남음:** 실행 PC 앱에 이 로컬 코드를 반영한 뒤 한 차례 기본정보 캐시 보완 이후 반복 reader가 사라지는지 재측정한다. NAS 내부 TOP20 기본정보 collector 또는 키움 ka10001 TR 반복으로 오인하지 않는다. 별도 실시간 재수신 상태도 확인한다.

2026-10-01 O12 TOP20 writer boundary 확인: 완성 순위의 membership snapshot은 30초 순위 회차마다 저장되며 snapshot/metadata/revision은 한 transaction이다. 일별 entrant는 미저장 종목이 있을 때만 별도 batch upsert한다. 완료 minute index는 durable outbox에 먼저 기록한 뒤 별도 transaction으로 flush하고 실패·재시작 시 재시도한다. 세 저장은 실행 빈도와 복구·실패 의미가 달라 transaction 병합은 하지 않는다. membership 저장 전에 live projection/구독 대상을 적용하는 순서도 유지한다. **남음:** entrant no-op 수정 후 운영 writer 호출/COMMIT 변화는 아직 재측정되지 않았다. 별도 증거 없이 transaction을 합치지 않는다.

2026-10-01 O12 shadow checkpoint 후속: 현재 보고서의 60초 표본은 2회 저장이며 COMMIT은 약 23~27ms였다. 각 저장은 관측 커서·전략 상태·순위/봉 프레임을 담으므로 값이 실제로 바뀐 호출이었다. 저장 실패 뒤 큐가 비면 커서가 저장되지 않은 채 재시도하지 않는 결함을 재현하고, 빈 큐에서도 pending checkpoint를 재시도하도록 고쳤다. 상태 frame 복사는 동일 1,005,849바이트 JSON을 만드는 synthetic 1,800-bar/11-universe 비교에서 median 6.966ms에서 0.749ms로 줄었다(인코딩과 DB 시간은 비교하지 않음). 불변 프레임 shallow copy와 bar/universe/중복 방지 키 수 계측을 추가했다. CandidateMonitor 검사 9건 통과; PostgreSQL 전용 DB와 운영 측 재검증, 실제 WAL/COMMIT 개선은 미검증이다. 이전 90초 표본의 38회를 60초라고 잘못 옮긴 값을 바로잡았다.

2026-10-01 O12 TOP20 일별 편입 문서 반복 쓰기 억제 로컬 구현: `AutonomousTop20Service`는 하루 첫 편입 때 `first_seen_at`만 저장하고, 이미 persisted된 코드만 남아 있는 반복 순위 회차에서는 `_persist_missing_daily_entrants`가 DB writer를 호출하지 않는다. 실패한 저장은 persisted set에 반영하지 않아 다음 회차에 재시도하고, 프로세스 재시작으로 빠진 기록은 날짜별 `top20_membership` snapshot과 revision에서 복구한다. 당일 순위와 전날 장후 보완의 캐시를 분리하고 전날 보완 때는 매 재시도 새 entrant 문서를 읽어 누락 종목을 포함한다. 저장소 upsert도 SQLite/PostgreSQL 모두 같은 JSON 문서면 충돌 UPDATE를 건너뛰며 PostgreSQL은 `affected_rows`를 관측 metric에 담는다. 기존 90초의 3 calls/60 rows는 9/30 측정으로, 이 로컬 변경 뒤의 성능 증거가 아니다. 최초 저장 실패·이탈 종목 재시도·같은 날 반복/재시작 skip·다음 날짜 reset·전날 backfill과 당일 ranking 상태 분리·5000행 recovery limit 표적 회귀 4건을 이번 확인에서 실행해 4/4 통과했다(기존 TOP20 회귀 56건 결과는 별도 기록). **남음:** 운영 서버 반영 후 같은 30초 ranking 조건에서 entrant writer가 최초 누락 때만 호출되는지, 저장 실패 시 재시도되는지 계측한다. 운영 반영과 실제 부하 변화는 아직 미검증이다.

2026-09-30 O12 외부시장 봉 반복 UPDATE 감소 로컬 변경: NAS 운영 60초 표본의 `external_market:bars`는 4회·3,691행이었다. 정기 수집 두 회차의 읽기 전용 API 결과를 대조하니 공통 봉 중 OHLCV는 같고 `updated_at`만 달라진 행이 3,695개, OHLCV 정정 4개, 새 봉 8개였다. SQLite/PostgreSQL `save_external_bars`에 OHLCV의 NULL-safe 차이 조건을 추가해 같은 내용의 충돌 UPDATE를 건너뛰고, 실제 값 변경 시에만 `updated_at`을 갱신한다. 키·응답 필드와 기존 호출별 transaction/COMMIT 경계는 유지했다. SQLite DB 모듈 회귀 56건 통과. 같은 재수신 시각 보존·OHLCV 정정·NULL 변경을 확인하는 전용 PostgreSQL 통합검사는 보강했으나 PC에 `KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL`이 없어 실행하지 않았다. 로컬 소스 변경이며 NAS 빌드·배포와 WAL/장치 사용량의 전후 효과는 미검증이다.

2026-09-30 O12 DB writer 후보 상세 계측 운영 배포(빌드 `2026.09.30-db-writer-detail-v1`, NAS `/health`에서 `status=ok` 확인): 기존 90초 run `20260930T101938Z-a54c0a8a`를 다시 읽어 후보의 규모와 느린 COMMIT을 구분했다. shadow checkpoint는 38회/38 commit, 직렬화 약 157KB, COMMIT p95/max 1,276/1,540ms였다. `realtime.minute`는 61회·798행·7,173 SQL 실행, execute p95 69ms·COMMIT p95/max 361/1,881ms였다. TOP20 편입 문서는 3회·60행·COMMIT max 1,727ms, `realtime.latest`는 61회·789행·COMMIT max 2,370ms, `realtime.second_bar`는 62회·1,063행·COMMIT max 615ms였다. runtime lease는 3회였고 COMMIT max 857ms라 호출 빈도는 낮지만 공유 저장 지연의 영향을 배제하지 않는다. 로컬 계측은 shadow 저장 이유/encode 시간·payload 크기, 분봉 단계별 시간/상태 건수, TOP20 문서 실제 반영 행 수를 추가했다. 진단 측정은 500ms 이상 모든 관측 writer COMMIT에 같은 PID·backend 시작 시각·COMMIT 시간창 안의 `pg_stat_activity` 표본을 붙인다. 1초 점 표본이며 `no_sample`은 대기가 없다는 뜻이 아니다. 관련 검사 81건과 진단/상관 검사 92건이 통과했다(서로 중복 포함). 기존 SQL·domain 저장 규칙·transaction 수는 바꾸지 않았다. 지연의 공통 원인·writer별 WAL 인과는 아직 확정하지 못했고, 배포된 계측으로 장중 표본을 추가 확보해야 한다.
2026-10-01 O12 `realtime.latest` 경로 검토: WebSocket parser는 trade/market_state/program_trade를 client hub에 즉시 publish하고, 별도의 `_pending_snapshots[(event_type,item_key)]` 맵에 최신 payload만 보관한다. 1초 flush는 lock으로 직렬화하고 비어 있지 않은 batch만 `asyncio.to_thread`로 PostgreSQL writer에 보내며, writer는 `executemany` 한 transaction으로 `central_realtime_latest`의 key별 최신 projection을 upsert한다. 따라서 장중 61 calls/789 rows는 event별 transaction이 아니라 이미 초당 최대 한 번으로 묶인 flush를 나타낸다. 실패 때 flush 입력을 복원하고 더 최신 pending 값은 보존한다. DB snapshot reader는 앱 WebSocket client가 subscribe할 때만 호출되어 최근 5분 값과 market_state를 초기 전송하며, 매 realtime event마다 DB를 읽지 않는다. `received_at`은 freshness 의미가 있어 payload가 같아 보여도 이를 무조건 생략하는 것은 안전하지 않다. 확인된 코드만으로는 더 줄 반복 SQL이 보이지 않아 이 후보에 writer 변경을 넣지 않는다. 앞선 운영표본의 COMMIT max 2,370ms는 다른 독립 writer와 공통 WAL/스토리지 대기 가능성이 남아 있어 `realtime.latest` 자체의 원인으로 귀속할 수 없다.

2026-10-01 O12 `realtime.second_bar` 경로 검토: 0B trade event는 client hub로 즉시 전달되고 `SecondTradeAccumulator`가 거래일·초·종목·시장별 OHLCV/대금/건수를 메모리에서 누적한다. 누적 체결값이 있는 중복 tick은 최근 5초 signature로 억제하며, 늦은 체결은 해당 초 절대 상태를 다시 dirty 처리한다. 매 1초 flush에서 dirty key를 한 번씩 뽑고 재시도 자료와 key별 최신 `(available_at,trade_count)`만 합쳐 `save_second_trade_bars` 한 번을 호출한다. PostgreSQL writer는 SELECT 없이 `executemany`/한 transaction으로 PK `(trading_date,trade_second,code,market)`에 upsert하고 더 오래된 절대 상태의 역행을 막는다. 기존 90초 장중 표본은 62 calls/1,063 rows(평균 약 17.1행/호출), COMMIT max 615ms였다. 저장 경로의 앱 reader는 검색되지 않았지만 데이터 계약은 TOP20/hot cohort 수신 체결의 부분적 1초 이력이며 구독 전·비가동 구간은 복원할 수 없다고 명시한다. 따라서 reader 부재만으로 보존 writer를 끄지 않는다. 현재 관측은 호출/입력 행 수만 제공해 행별로 신규 초·진행 중 초·늦은 정정·재시도 중 무엇이었는지, conflict가 실제 UPDATE였는지를 분리하지 못한다. 1초 저장보다 늦춰도 되는 허용 지연 계약과 함께 이 분류를 추가 측정하기 전에는 transaction/flush 간격 변경을 하지 않는다. 기존 계약 테스트는 절대값 replay·오래된 상태 역행 차단·commit 뒤 응답 유실 재시도를 확인한다. 다음 O12 후보는 TOP20 편입 문서 writer의 반복 UPDATE가 실제로 남았는지 현재 코드와 측정으로 재검토한다.

2026-10-01 O12 `realtime.minute` SQL 반복 축소 로컬 구현: 2026-09-30 측정의 61회·798행·7,173 SQL 실행을 실제 0B collector → `save_minute_bars` 경로와 대조했다. 기존에는 봉마다 operation hash SELECT, query-authority SELECT, canonical UPSERT 뒤 저장 봉 SELECT가 발생했다. 기존 transaction·advisory-lock·revision·operation replay 규칙은 유지하면서 operation hash와 고유 minute key authority를 batch 조회하고, UPSERT `RETURNING`을 metadata/revision 계산에 재사용하도록 바꿨다. batch lookup key/statement 수를 writer domain metrics에 추가했다. 로컬 관련 회귀 63건 중 62건 통과·1건은 PC 전용 PostgreSQL URL 미설정으로 skip됐다. NAS 전용 DB의 첫 시도에서 계측 capture 누락으로 실패한 테스트 두 건은 임시 capture fixture를 넣은 v2에서 수정됐고, 관련 전용 DB 검사 5건이 모두 통과했다. PostgreSQL 동시성·replay·닫힌 query authority 보존은 확인됐으나, 운영 서비스 빌드에서 SQL 실행량이 줄었는지와 latency/WAL 효과는 아직 측정하지 않았다. 이 변경으로 WAL/COMMIT 병목이 해결됐다고 보지 않는다.

2026-10-01 O12 `realtime.minute` 운영 확인: NAS `/health`의 16:12 KST 응답은 `2026.10.01-top20-entry-preparation-v2`였다. 로컬 후보 build marker는 `2026.10.01-shadow-checkpoint-frames-v1`이므로 현재 NAS 동작으로 candidate 효과를 재측정했다고 볼 수 없다. **남음:** closeout 배포 조건을 충족한 뒤 후보를 적용하고 동일 장중 진단 지표로 확인해야 한다. 지금은 deployment와 성능 개선 결론을 보류한다.

2026-10-01 O12 execution runtime lease 검토: 획득은 owner token이 같거나 기존 lease가 만료됐을 때만 원자적으로 UPSERT하고, release는 현재 owner token 일치가 필수다. runtime 시작·명시 heartbeat·보호된 실행 작업·종료 경계에서만 호출된다. 기존 90초 표본은 3 calls, COMMIT max 857ms로 표본 빈도는 낮다. 안전 갱신을 생략하거나 다른 writer와 transaction을 합치지 않는다. 로컬 lease/owner 교체/release barrier 검사 14건 통과했다. **판정:** 현재 코드에서 안전한 DB 작업 감소 근거 없음; commit tail은 공통 storage 대기로 남겨둔다.

2026-10-01 O12 `read.market_data_metadata/metadata_range` 지연 분리: production 호출은 `market_ingest.ka10080` 실제값 보호 조회와 `/api/v1/market/coverage` API 두 곳이다. ingest는 기존 `source` 태그가 있었고 coverage API에 `api.market.coverage` 태그를 추가해 `asyncio.to_thread` 경계 뒤에도 호출별 출처를 기록하며 summary에 출처별 호출 수와 단계별 지연 분위수를 제공한다. 관련 market ingest/DB 관측 테스트 63건 통과(43 skip), coverage API 출처 테스트도 프로젝트 가상환경에서 4건 통과했다. metadata 기본키는 `(dataset_kind,subject,observation_key)`이며 `effective_at` 범위 전용 인덱스는 없다. **미확인:** 현재 PostgreSQL `EXPLAIN`과 source별 운영 지연은 미측정이므로 인덱스 추가나 원인 확정은 보류한다.
2026-10-01 O12 metadata reader 운영 상태 재확인: NAS 공유의 `active.json`과 인증 읽기 전용 API를 확인했다. 활성 release는 `2026.10.01-top20-entry-preparation-v2-e55eb967f5e572c2`이며 `/health`는 정상이다. 활성 소스에는 `api.market.coverage` 태그와 `source_metrics`가 아직 없고, 최근 30분 `/api/v1/diagnostics/db-calls`는 capture OFF·metadata reader 0건·dropped/truncated 0·UNREGISTERED 0을 반환했다. 이는 신규 계측 표본이 아니라 계측 코드가 아직 활성 release에 반영되지 않았다는 뜻이다. 로컬 working tree에는 여러 독립 미커밋 기능이 있으므로 전체 소스 staging/deploy는 하지 않았다. 다음 운영 판별에는 observability 변경만 포함한 좁은 release 활성화와 capture 표본이 필요하다. PostgreSQL EXPLAIN은 아직 미실행이다.

2026-09-30 O12 일봉 기간 검증 로컬 구현: `daily_bar_history_coverage` 문서로 KRX/NXT 응답 구간·기준일·저장값 fingerprint를 확인하고, GET daily-bars에 읽기 전용 기간 상태를 추가했다. 5·20·250일 각각 검증하며, 원천 연속조회가 정상 종료된 짧은 전체 이력은 실제 확보 개수와 함께 기간 고가로 계산한다(상장·정지 사유는 추정하지 않음). 기본정보/로컬 캐시의 미검증 250일 값은 검증된 수정주가 고가로 취급하지 않고, 장후 완료에는 final scope·대상일 봉·기존 거래일 증거를 요구한다. 전용 PostgreSQL URL 미설정으로 전용 통합검사 1건은 skip. 관련 로컬 검사 묶음은 67건, 63건(1 skip), 65건, 추가 상태 전이 2건 실행 기록에서 통과했다(묶음 간 일부 겹침, 고유 합계 아님). DB schema/legacy API parameters·bars/기존 transaction·commit 경계는 바꾸지 않았다. NAS 빌드/실행 API·PC/NAS 누적 적용은 아직 하지 않았으며 실제 실행 결과를 확인해야 한다. [구현 계약과 남은 검증](../HISTORICAL_DATA_CONTRACT.md#일봉-확보기간-고가-검증--로컬-구현-운영-검증-대기).

2026-09-30 O12 일봉 reader v12 운영 재계측: 실행 빌드 `2026.09.30-daily-bar-lookup-v1`; run `20260929T210701Z-a0f9fc8f`, 60.004초. `read.market_bars/daily_bar` 50회 모두 `top20.entry_daily_history`; execute p50/p95 3.098/3.546ms, max 20.138ms; total p50/p95 15.353/16.697ms, max 60.259ms; connection acquire p95 13.135ms, commit p95 0.220ms. DB·observer 오류 0, dropped 0, truncated false. Database-wide WAL 238,626 bytes, sync/write 각각 23회; `dm-4` busy 6.17%, average queue 2.58, write await 65.51ms. `pg_stat_wal` time values는 측정 연결에서 track_io_timing이 꺼져 있어 사용할 수 없다. v11의 직전 60초 표본은 69회·unattributed, execute p95 6.19ms였으므로 caller/요청 크기 mix 차이 때문에 v12 대 v11 수치를 직접 성능 비교로 해석하지 않는다. v12에서 250행 API 경로의 caller별 실측은 아직 없다. master/capture OFF, paused 0 복귀 확인.

2026-09-30 O12 일봉 조회 인덱스 운영 적용·검증: 운영 `kiwoom_monitor.public.central_daily_bars`에 `(code, market, trading_date DESC)` 인덱스 `idx_central_daily_bars_code_market_date`를 `CREATE INDEX CONCURRENTLY`로 생성했다. 유효·ready 상태와 약 155MB 크기를 확인했고, 기존 테이블·데이터·transaction 경계·스키마 버전은 변경하지 않았다. 현재 배포된 v11 쿼리의 동일 종목 250행 읽기 전용 단일 실행은 적용 전 519/584ms에서 적용 후 4.494ms, 로컬의 정렬 수정 쿼리는 적용 전 189ms에서 적용 후 0.379ms였다. 5,000행 요청도 기존 배포 SQL 7.038ms·로컬 SQL 3.962ms이며 새 인덱스를 사용하고 테이블 전체 Seq Scan을 하지 않았다. 앱 그대로 60초 계측한 일봉 reader는 적용 전후 모두 69회였고 execute p95가 415.406ms에서 6.19ms, total p95가 429.655ms에서 20.704ms로 낮아졌다. 이 비교는 서로 다른 시간대의 관측이며 다른 부하를 통제하지 않았지만, 동일 호출 수와 계획 변화가 인덱스 효과를 뒷받침한다. 진단 master/capture OFF·pause 0 복귀 확인. 로컬 `ORDER BY` 수정과 caller 출처 태그는 아직 NAS v11 미배포이므로 별도 배포·출처 검증이 남는다.

2026-09-30 O12 일봉 reader 지연 원인·로컬 수정: NAS v11 운영 측정 60초에서 `read.market_bars/daily_bar` 69회, 실행 p95 415ms·최대 459ms를 관측했다. 읽기 전용 PostgreSQL 확인에서 `central_daily_bars`는 추정 4,921,680행·830MB이며 `(trading_date,code,market)` 기본키만 있었다. 운영 query의 `ORDER BY trading_date`가 SELECT의 `trading_date::text` 별칭에 결합되어 병렬 Seq Scan+Sort를 택했다. 동일 종목·250행 `EXPLAIN ANALYZE`에서 기존 쿼리는 519ms/72,488 shared blocks, 반복 584ms/72,488 blocks였다. `ORDER BY central_daily_bars.trading_date`는 기존 기본키 Index Scan·189ms/5,484 shared blocks였다. `PostgresQueryStore.load_daily_bars`의 정렬만 실제 날짜 컬럼으로 한정했고 caller 구분용 `db_call_source`를 TOP20 진입/장후/신고가 및 두 일봉 API 경로에 추가했다. DB/TOP20 58건과 일봉 API·아카이브 2건이 통과했다. 응답의 날짜 문자열·결과 행·transaction 경계는 유지했다. 로컬 수정은 아직 NAS v11에 미배포이며 실제 caller 출처와 실운영 전후 효과는 미확인이다. `limit=5000` 계획은 여전히 Seq Scan+Sort이므로 일봉 API 전체 병목이 해결됐다고 보지 않는다. 스키마 인덱스 변경은 하지 않았다.

2026-09-30 별도 회귀 발견: 뉴스 backoff 변경과 무관한 `tests.unit.test_central_server_app.test_public_api_route_contract_is_stable`이 로컬 앱에 이미 있는 `GET /api/v1/diagnostics/news-job-claim-readonly-analyze`를 expected route 목록에 포함하지 않아 실패한다. 이번 뉴스 worker 변경 범위에서는 route 또는 테스트 계약을 수정하지 않았다.

2026-09-30 O12 뉴스 작업 idle backoff·wake-up 로컬 구현: 현재 서버의 BODY 2개/RULE 1개 worker는 빈 claim 뒤 1·2·4·최대 5초 간격으로 조회하고, 같은 프로세스의 기사·소스 페이지·본문 후속·AI job·retry 저장이 COMMIT된 뒤 즉시 깨어난다. 기존 job claim/transaction·worker 수는 바꾸지 않았다. 외부 프로세스가 같은 DB에 직접 저장하거나 알림이 누락된 경우에도 최대 5초 간격 조회가 남는다. 이는 PC 소스 변경이며 NAS 배포·운영 polling 감소·새 job 선점 지연 측정은 아직 수행하지 않았다. 기존 진단 기록의 “backoff 보류”는 이 변경 전 상태를 설명한다.

2026-09-30 O12 일봉·분봉 metadata `available_at` 후속 변경(PC 소스, NAS 미배포): 동일 봉 값·완료 상태·출처를 다시 조회한 경우 metadata 갱신을 건너뛰고, 봉 값이나 metadata 의미 필드가 바뀐 경우에만 새 관측시각을 저장하도록 SQLite/PostgreSQL 저장 경로를 수정했다. 기존 분봉 revision과 transaction 경계는 유지한다. 관련 단위검사 66건 통과; 전용 PostgreSQL 통합검사 1건은 PC에 `KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL`이 없어 실행되지 않았다. 아래의 재조회 시각 갱신 및 동작 변경 보류 설명은 당시 운영 v8 진단 시점의 기록이다. NAS 적용과 실제 PostgreSQL 확인, 기존 시각의 소급 보정 여부는 남아 있다.

2026-09-30 O12 sudo 호스트 I/O 이벤트 진단 결과: 해시 검증된 일회성 `/tmp/kiwoom-host-io-owner-probe-20260930-exact.py`를 사용자가 NAS에서 실행했다. 최대 90초 동안 호스트 I/O 표본 215개가 수집됐고 `/proc/<pid>/io` 권한 거부는 0, DB raw capture의 dropped/truncated는 0이었다. 그러나 기준인 1초 이상 `query_minute` COMMIT이 없어서 `triggered=false`였으며, 이 실행으로 느린 COMMIT의 host I/O owner를 판정할 수 없다. 도구 v1은 이벤트가 없을 때 분봉 호출 수·최대 COMMIT·전체 구간 I/O를 출력하지 않아 해당 구간의 실제 분봉 저장 횟수도 알 수 없다. 종료 출력은 master/capture OFF·workload pause 0이었다. 직후 NAS `/health`는 v9, 분봉 보완·metadata 저장·뉴스 작업 등은 설정상 active였지만 실제 수행 횟수의 증거는 아니다. 같은 90초 검사를 즉시 반복하지 않고 로컬 `artifacts/nas_host_io_owner_probe.py`에 no-trigger 호출 수·최대 COMMIT·전체 구간 I/O 요약을 추가해 비권한 사전검사를 통과시켰다. 사용한 NAS `/tmp`의 이전 버전은 SHA-256과 경로를 확인한 뒤 제거했다. 개선본은 NAS에 아직 다시 배치하거나 sudo 실행하지 않았다. 느린 이벤트의 호스트 소유자 확인은 실제 spike가 재현되는 시간대의 새 관측이 필요하다.

2026-09-30 O12 호스트 I/O 소유자 진단 준비: NAS 호스트의 cgroup v1 `blkio` 계층에는 `blkio.io_service_bytes`와 `blkio.throttle.io_service_bytes`가 없고, 일반 `k379` 계정은 보이는 약 458개 프로세스 중 2~3개의 `/proc/<pid>/io`만 읽는다. `sudo -n docker stats`는 암호를 요구하므로 현 세션에서 비대화형으로 host owner를 확정할 수 없다. 기존 API 캡처를 사용하면서 1초 이상 분봉 COMMIT 발생 시점의 호스트 프로세스·cgroup별 읽기/쓰기 바이트와 `md4`/`dm-4` 장치 카운터를 최대 90초간 함께 모으는 일회성 읽기 전용 스크립트 `artifacts/nas_host_io_owner_probe.py`를 만들었다. NAS `/tmp/kiwoom-host-io-owner-probe-20260930-exact.py`에 바이트 동일 SHA-256 `0C704939B0B48B3DA987E6DB1835D87E9F9408386697577B8F1CC463ADDA41A0`으로 배치했고 mode 600·비권한 `--preflight`의 문법/장치 경로를 확인했다. 실제 root 측정은 아직 실행하지 않았다. 첫 PowerShell 파이프 전송본은 줄바꿈으로 해시가 달라 검증본과 별도로 제거했다. sudo 측정 후 결과 회수와 `/tmp` 검증본 제거가 남았다. 프로세스 `/proc/io`와 블록 장치 카운터는 집계 계층이 달라 차액을 특정 프로세스에 직접 배분하지 않는다.

2026-09-30 O12 분봉 COMMIT의 PostgreSQL 프로세스 I/O 귀속: NAS 호스트의 `ps`에 보이는 9개 `postgres`는 모두 DSM 내부 `pgsql.service` 소속이고 앱 DB 컨테이너 프로세스가 아니었다. 앱 PostgreSQL 계정은 superuser이며 읽기 전용 `pg_read_file('/proc/<pid>/io')`로 `pg_stat_activity`가 열거한 자기 컨테이너 프로세스의 누적 I/O만 조회할 수 있음을 확인했다. 첫 이벤트 표본에서 1,083.554ms 분봉 COMMIT을 감싼 1,773.3ms 창의 `dm-4` 쓰기 약 17.74MB와 앱 DB background writer/checkpointer/WAL writer 합계 약 1.78MB가 관측됐으나, 호출별 연결이 창 끝 전에 닫혀 client backend의 쓰기량을 그 표본으로 산정할 수 없었다. 이어 60초 한도의 짧은 간격 읽기 전용 표본에서 1,389.048ms 분봉 COMMIT 한 건이 재현됐고, 초기 100ms 뒤 25ms 간격의 동일 backend wait는 `LWLock:WALWrite` 49표본(오류 0)이었다. 이를 포괄하는 1,501.4ms 장치 구간에서 `md4` queue 116.53·쓰기 약 9.87MB, `dm-4` queue 114.77·쓰기 약 9.90MB가 관측됐다. 같은 구간에 앱 DB 분봉 client backend PID 1745는 1,315.7ms 동안 추적됐고 `write_bytes` 증가는 0이었다. 앱 DB background writer는 약 2.92MB(`IO:DataFileWrite` 2표본), WAL writer 약 0.39MB(`IO:WalSync` 5표본), checkpointer 약 0.23MB를 썼다. 이 수치는 각 프로세스 `/proc/io`와 호스트 block-device 카운터로 출처·계층이 달라 차액을 NAS 외부 프로세스에 그대로 귀속할 수 없다. 분봉 backend가 직접 파일을 쓰지 않아도 WAL·background write·장치 대기에 막힐 수 있다는 강한 증거이며, 실제 대기열의 주된 생성자는 여전히 미확정이다. PostgreSQL I/O 표본은 0오류, opt-in capture dropped/truncated 0, 종료 뒤 master/capture OFF·진단 pause 0이었다. 다음 원인 분리는 host 측 프로세스/컨테이너 I/O 또는 파일시스템 writeback 소유자와 같은 이벤트 창을 맞춰야 한다. 현재 일반 NAS 계정은 다른 프로세스의 `/proc/<pid>/io`와 Docker daemon에 접근할 수 없어 독립적인 host owner 귀속에는 권한 경계가 있다.

2026-09-30 O12 분봉 COMMIT 이벤트 동시 표본: 운영 설정·writer 동작을 바꾸지 않고 NAS 호스트에서 진단 capture와 읽기 전용 `pg_stat_activity`·`/proc/diskstats`를 같은 프로세스에서 수집했다. 첫 30개 호스트 표본에서는 분봉 COMMIT 1,166.468ms가 발생한 창에 WAL writer `IO:WalSync`/`LWLock:WALWrite`가 각각 1회 관측됐고, 해당 창 주변 장치 표본에서는 `md4` queue 49.15, `dm-4` queue 48.02였다. 첫 장치 구간(836ms)은 실제 COMMIT 1,166ms 전체를 감싸지 않아 정량 비교에 사용하지 않는다. 이어진 별도 87개 호스트 표본에서는 분봉 COMMIT 1,580.33ms 1건을 정확히 포괄하는 1,727.8ms 장치 구간을 얻었다. 그 창에 해당 분봉 backend의 초기 100ms 지연 후 25ms 간격 wait probe는 `LWLock:WALWrite` 55표본, `IO:WalSync` 2표본(오류 0), 호스트 1초 표본에는 WAL writer `IO:WalSync` 1회가 있었다. 장치 구간의 `md4` 평균 queue 64.31·busy 57.18%, `dm-4` queue 75.68·busy 97.23%, `md2` queue 5.96·busy 58.69%였다. 같은 분봉 COMMIT 창에 opt-in 계측의 news claim COMMIT 5건 및 reader COMMIT 3건이 겹쳤지만 이들이 물리 I/O를 유발했다는 뜻은 아니다. 두 capture 모두 dropped/truncated 0, 호스트 PostgreSQL 표본 오류 0, 종료 후 master/capture OFF·진단 pause 0을 확인했다. 따라서 수초 지연이 PostgreSQL WAL 대기 및 NAS NVMe 경로 정체와 같은 시간에 발생한다는 증거는 강화됐으나, WAL writer·다른 PostgreSQL backend·NAS의 다른 프로세스 중 누가 장치 대기열을 만들었는지는 미확정이다. 다음 조사는 같은 이벤트 창의 프로세스별 I/O 또는 동등한 I/O owner 증거가 필요하다. 일반 NAS 계정에서는 다른 프로세스의 `/proc/<pid>/io`가 거부되어 현재 읽기 전용 API만으로는 그 귀속을 확정하지 못한다.

2026-09-30 O12 WAL background 동시 표본: NAS 호스트의 기존 `psql`을 환경 파일의 자격증명으로만 사용해 비밀번호·query 본문 출력 없이 읽기 전용 `pg_stat_activity`를 조회했고, 기존 API의 45초 `measure`와 같은 프로세스에서 1초 간격 44개 호스트 표본을 수집했다(run `20260929T180436Z-c5875eee`). 구간은 complete, 분봉 저장 23회, COMMIT median/p95/max 33/341/341ms로 앞선 수초 spike가 재현되지 않았다. WAL writer `IO:WalSync`는 호스트 표본 3개에 나타났으나 500ms 이상 분봉 COMMIT은 0회였다. 45초 전체 WAL sync 76회·sync time 1,647.965ms, `md4` 평균 queue 19.06·busy 23.04%였고 앞선 느린 COMMIT 창의 높은 queue와 조건이 달랐다. 따라서 이 구간의 결과만으로 WAL writer의 `WalSync` 관측을 분봉 spike의 충분조건으로 볼 수 없고, 과거 수초 지연의 원인을 배제하거나 특정 프로세스에 귀속하지도 않는다. 첫 동시 측정은 보고서 게시 전 조회 404로 호스트 표본과 연결하지 못해 결론에 사용하지 않았다. 재측정은 동일 실행의 시작/종료 시각을 확인했고 종료 뒤 master/capture OFF·pause 0·metadata 정상 상태를 확인했다. 다음 증거는 수초 분봉 COMMIT이 실제 발생한 동일 창에서 WAL writer/checkpointer 대기와 `md4` 장치·다른 프로세스 I/O를 함께 잡는 이벤트 중심 표본이다.

2026-09-30 O12 기존 분봉 A/B/A의 호출별 장치 상관 재분석: 새 운영 측정 없이 저장된 원시 보고서의 `commit_diagnostics.storage_device_window`를 대조했다. 두 번째 run `20260929T172745Z-fc7c33e0`은 분봉 호출 26/23/24건 모두 COMMIT 창 장치 표본이 있었고, B 구간의 100~499ms COMMIT 11건과 500ms 이상 10건에서 NVMe 경로 `md4` 평균 queue 중앙값이 각각 1.02/65.81, write await 중앙값이 4.81/139.29ms였다. 마지막 A는 같은 구분 10/4건에서 queue 7.45/120.03, write await 66.64/385.9ms였다. 첫 run의 5,329ms 분봉 COMMIT 창에서는 `md4` busy 98.13%·queue 149.93, `md2` busy 22.39%·queue 2.04였고 B의 5,654ms 창도 `md4` busy 95.03%·queue 84.78, `md2` busy 20.99%·queue 0.65였다. 이들은 250ms 목표 간격의 전후 장치 표본을 COMMIT 창에 맞춘 호스트 전체 지표이므로 각 트랜잭션의 물리 I/O 기여나 `md4` 정체를 만든 프로세스를 식별하지 않는다. 짧은 COMMIT과 긴 COMMIT의 관측 창 길이도 다르므로 단순 queue 차이를 인과 효과로 해석하지 않는다. 다만 같은 backend의 `WalSync`/`WALWrite` 대기와 NVMe 경로 정체가 느린 창에 반복 동시 관측돼, 원인 추적은 WAL 경로의 동시 호스트 부하·PostgreSQL background process와 미계측 writer를 구분하는 쪽으로 좁혀졌다. 첫 run의 최대 10,951ms 표본은 측정 경계를 걸쳐 호출별 장치 창이 없어 이 비교에서 제외한다.

2026-09-30 O12 기존 A/B/A 원시 보고서의 COMMIT 시각 대조: 두 번째 run의 계측된 쓰기 COMMIT은 A/B/A 225/213/210회, 초당 최대 8/6/8회였고 첫 run은 199/204/201회였다. 두 번째 run의 분봉 reader 188/46/47회 중 첫 A에는 `market_data_coverage` reader 136회가 추가로 관측됐다. 서버 로그의 TOP20 장후 보완 재시도 및 코드의 보완 대상별 분봉 확인 경로와 일치하지만 reader에 호출자 ID가 없어 188회의 개별 소유자는 확정하지 않는다. 느린 분봉 COMMIT의 동일 backend에서 `LWLock:WALWrite`/`IO:WalSync`를 확인했고, 그 창과 겹친 writer 대부분은 `news_job_claim`이었다. 그러나 두 번째 run의 claim COMMIT 중앙값은 0.236/0.223/0.238ms이고 느린 분봉 COMMIT과 겹친 claim의 COMMIT은 모두 50ms 미만이었다. 단순 시간 겹침만으로 news claim이 WAL 대기 원인이라고 단정하지 않는다. 원시 DB capture는 절단되지 않았지만 opt-in 계측 경로만 포함하며, 읽기 transaction COMMIT 횟수를 물리적 WAL sync 횟수로 취급하지 않는다. 다음에는 느린 COMMIT 창의 WAL 실행 주체와 동시 저장 부하를 구분할 증거가 필요하다.

2026-09-30 O12 분봉 metadata A/B/A 재측정: 같은 NAS v9에서 run `20260929T172745Z-fc7c33e0`을 A→B→A 각 60초로 완료했다. 분봉 저장 호출/행은 26/23,122 → 23/20,422 → 24/21,322로 첫 run보다 유사했고 B metadata 20,422행을 실제 생략했다. A 구간 metadata 반영은 23,122/21,322행, B는 0행; 세 구간 봉 반영·revision 추가는 0행이었다. 분봉 COMMIT median/p95/max(ms)는 265/1,666/1,968 → 287/1,776/1,803 → 126/1,356/1,439로 B의 COMMIT 개선은 재현되지 않았다. 동일 backend COMMIT wait의 `IO:WalSync` 표본은 227/151/49, `LWLock:WALWrite`는 150/246/156이었다. 전역 WAL bytes는 24,359,622/3,635,955/6,407,053, WAL write/sync는 101/100 → 64/63 → 90/89, dm-4 busy(%)는 58.76/50.66/38.57, 평균 queue는 25.98/31.14/17.87이었다. 첫 A에는 외부시장 봉 writer가 4회 있었고 B/A2에는 0회였으며 minute-bar reader도 188/46/47회로 크게 달랐다. 따라서 첫 A의 큰 WAL을 metadata에 귀속할 수 없고 장치 busy의 완만한 하락도 시간·다른 부하와 분리되지 않는다. checkpointer timed/requested 완료는 각 구간 0, 통계 reset·market bar capture 절단 없음. 종료 뒤 master/capture OFF, paused 0, metadata 정상 저장 복귀를 확인했다. 앞선 run과 합치면 metadata 생략은 확실히 작동하지만 공유 WAL/COMMIT spike의 단독 원인 또는 해결책이라는 증거는 없다. B에서 생략한 행은 자동 재생되지 않는다.

2026-09-30 O12 분봉 metadata 전용 운영 A/B/A 완료: NAS v9 `/health.server_build=2026.09.30-minute-metadata-aba-v9`, run `20260929T171853Z-44764cf9`에서 각 60초 A→B→A가 complete였다. B에서 `minute_query_metadata`만 paused였고 분봉 metadata 6,022행 저장이 실제로 생략됐다. 분봉 호출/행은 12/10,522 → 7/6,022 → 4/3,600, metadata 반영 행은 9,622 → 0 → 3,600, 봉 반영·revision 추가는 세 구간 모두 0이었다. 분봉 COMMIT median/p95/max(ms)는 3,386/10,951/10,951 → 3,762/5,654/5,654 → 3,652.5/5,032/5,032. WAL bytes는 7,136,785 → 3,782,376 → 3,648,712, WAL write/sync는 32/31 → 29/28 → 28/27, WAL sync time(ms)은 19,287/15,419/9,672였다. dm-4 busy(%)는 100.28/99.75/99.92, 평균 queue는 77.23/74.24/85.55. B에서도 해당 분봉 COMMIT backend의 `IO:WalSync` 592표본과 `LWLock:WALWrite` 508표본, 수초 COMMIT이 남았다. 각 구간 호출량과 다른 DB read/write 부하가 달랐고 B에도 news claim 175회, 마지막 A에는 minute bar reader 198회가 겹쳤다. checkpoint 완료 증가는 0, 통계 reset 없음, market bar capture truncated=false. 따라서 B가 metadata 쓰기를 실제로 제거한 것은 확인됐지만 전역 WAL 차이의 단독 귀속이나 metadata 제거로 spike가 해결된다는 결론은 내리지 않는다. 종료 뒤 master/capture OFF, paused 0, metadata 정상 저장 복귀를 확인했다. B에 생략된 metadata는 자동 재생되지 않으며, 진단 코드 제거·이미지 재빌드는 별도 후속 항목이다.

2026-09-30 O12 분봉 metadata A/B/A 진단 준비(v9): 사용자 승인에 따라 `query_minute` PostgreSQL 저장의 metadata UPSERT만 master TTL에 묶인 `minute_query_metadata` 진단 스위치가 켜진 B 호출에서 생략하도록 했다. 봉 UPSERT, revision, 기존 연결/transaction/COMMIT과 다른 writer는 유지한다. 호출별 `metadata_suppressed_rows`를 남겨 구간 경계의 진행 중 호출을 구분한다. NAS v8 원본 해시 6개를 확인해 `X:\kiwoom-monitor\.codex-backups\20260930-minute-metadata-aba-v9`에 보존하고 v9 소스/빌드 표식을 배치했다. NAS 배치 소스 기준 관련 단위검사 80건 통과. B의 metadata 누락은 사용자 허용 범위이며 A 복귀가 누락분을 자동 재생하지는 않는다.

2026-09-30 O12 분봉 metadata 계측 운영 결과: NAS 실행 `/health.server_build=2026.09.30-minute-metadata-diagnostics-v8` 확인 뒤 기존 작업을 멈추지 않고 60.004초 `measure` run `20260929T163650Z-ba987a7a`를 완료했다. `query_minute` legacy 표본은 15회·13,500행이며 시작 경계를 걸친 1회는 SQL probe가 없어 반영 행 수 분석에서 제외했다. 나머지 14회·12,600행의 동일 호출별 봉 UPSERT `affected_rows` 합계는 0, metadata UPSERT는 12,600, revision insert는 전체 0이었다. metadata 실행 시간 median/p95는 140/232ms, COMMIT은 495/15,156ms였다. 최대 15,156ms COMMIT의 같은 backend에서 `LWLock:WALWrite` 581표본이 있었고 해당 호출 metadata SQL은 900행 반영·135ms였다. metadata SQL wait 표본은 전부 `NONE:NONE`(14개)이므로 이 창의 긴 지연은 metadata 문장 실행보다 COMMIT 단계에서 관측됐다. 전역 WAL 32,495,790바이트, dm-4 busy 99.81%·평균 queue 77.16이었지만 다른 writer(news claim 172회 등)가 겹쳤으므로 WAL/장치 사용량을 metadata에 단독 귀속하지 않는다. metadata `available_at`은 재조회 때 갱신되는 현행 데이터 계약이므로 저장을 생략하는 동작 변경은 계속 보류한다. capture dropped 0·DB/WAL stats reset 없음, 종료 뒤 master/capture OFF·진단 pause 0 확인. 향후 원인 분리는 전용 DB에서 metadata 현재/대안의 동일 부하 WAL·행 값 비교와 운영의 독립 부하 창이 필요하다.

2026-09-30 O12 분봉 metadata 계측 전용 NAS 소스 staging: NAS v7의 기존 source hash를 확인한 뒤 `database.py`의 capture-only metadata SQL wait/affected_rows, `diagnostic_metrics.py`의 분리 집계, app/Compose/Dockerfile 빌드 표식만 `2026.09.30-minute-metadata-diagnostics-v8`로 올렸다. 기존 5파일은 `X:\kiwoom-monitor\.codex-backups\20260930-minute-metadata-diagnostics-v8`에 바이트 그대로 보존했다. NAS 소스 기준 Python 구문 확인과 분봉·진단 단위검사 68건이 통과했다. 로컬의 별도 뉴스 claim 변경을 NAS에 복사하지 않았으므로 이를 import하는 로컬 `test_postgres_access` suite는 NAS 소스와 결합 시 import 오류가 났다(계측 회귀 실패가 아님). 실행 서버 `/health.server_build`는 staging 직전 v7이며 아직 재빌드·재생성하지 않았다. Docker CLI는 NAS 계정에서 daemon 권한이 없고 비대화형 sudo는 암호를 요구하므로 런타임 적용은 사용자 sudo 빌드 뒤에 검증한다. 분봉 저장·다른 로컬 동작 변경은 보류한다.

2026-09-30 O12 분봉 저장 원인 축소(로컬 진단 보완, NAS 미적용): 운영 v7의 90초 raw report `20260929T154528Z-309966bb`에서 `query_minute` 22회 모두 revision insert 0행이었다. 기존 SQL은 동일 canonical 봉 UPDATE를 `IS DISTINCT FROM`으로 건너뛰지만, `ka10080` 관측시각으로 매번 달라지는 metadata `available_at`은 900행 UPSERT에서 갱신한다. 가장 느린 한 호출은 봉 UPSERT 4,255ms, metadata 17,211ms, COMMIT 2,315ms였고 봉 SQL의 동일 backend 표본은 `LWLock:WALWrite`였다. metadata 17,211ms의 wait와 실제 갱신 행 수는 기존 보고서에 없어 WAL 기여를 확정할 수 없다. capture 중에만 metadata SQL의 동일 backend wait와 `rowcount`를 기록하도록 진단 계측을 추가했다. 일반 저장 경로·transaction·데이터 계약은 변경하지 않았고 관련 단위검사 68건 통과했다. 다음에는 새 빌드에서 동일 부하 창의 metadata wait/affected rows와 봉 affected rows를 확인한 뒤, `available_at` 갱신 의미를 보존하는 범위에서만 쓰기 축소를 결정한다. 사용자의 요청에 따라 이번 범위는 계측만이며 기존 분봉 외 로컬 수정·성능 동작 변경과 NAS 적용은 보류한다. SSH 터널을 통한 운영 DB 직접 읽기는 서버의 포트 전달 정책(`administratively prohibited`)으로 차단됐고 터널은 종료했다.

2026-09-30 O12 분봉 보완 심층 비교: 운영 v7 진단 API `compare` run `20260929T155411Z-6800792e`에서 `minute_backfill`만 ON–OFF–ON 각 60.004초로 측정했다. OFF 적용 및 마지막 ON 재개가 확인됐고, 구간별 분봉 writer는 11회·9,900행 / 2회·1,800행 / 8회·7,200행, `ka10080` 완료 로그는 30/4/20회였다. OFF에도 이미 진행 중인 2회가 완료됐다. 전역 WAL bytes는 3,780,390 / 1,386,424 / 20,067,643으로 달랐으나, `dm-4` busy는 99.72/99.85/99.75%, 평균 queue는 76.29/66.79/87.56이었다. OFF의 PostgreSQL `blocks_read`가 1,856,270으로 ON 351,320/169,957보다 훨씬 컸고, 같은 구간에 timed checkpoint 1회의 통계 반영(`sync_time_ms` 증가 9,518)과 다른 계측 writer(query cache 19회, investor-flow snapshot 13회)가 겹쳤다. 외부시장 봉 저장은 세 구간 모두 0행이며 마지막 ON에 collection 시도 1회만 시작했다. WAL timing은 측정 연결에서 OFF이고 분봉 transaction-local 계측 호출 수가 11/2/8이라 전역 WAL sync time 차이를 전체 writer의 기여도로 해석하지 않는다. 분봉 보완을 멈추면 해당 호출·행 수가 줄어드는 것은 확인됐지만, 높은 장치 사용률은 그대로여서 분봉 단독 원인 가설은 지지되지 않는다. 큰 읽기량과 checkpoint/다른 프로세스의 개별 소유자는 미확정이다. 세 구간 DB-call capture complete, dropped/truncated 0, 통계 reset 없음. 종료 뒤 master/capture OFF·pause 0을 확인했다.

2026-09-30 O12 운영 다중 writer 재측정: v7 진단 API `measure` run `20260929T154528Z-309966bb`를 00:45:29 KST부터 90.004초 실행했다. 운영 workload는 중지하지 않았고, 완료 보고서 raw 611건은 `dropped=0`·`raw_truncated=false`, 통계 reset 없음, DB/observer 오류 0이었다. 분봉 `query_minute` 22회·19,522행의 COMMIT median/p95/max는 417/7,783/11,063ms였으며 같은 backend COMMIT probe에서 `LWLock:WALWrite` 947·`IO:WalSync` 540표본을 관측했다. `ka10080` 완료 로그 44회, 일봉 writer 0회, 외부시장 봉 4회·3,782행(최대 COMMIT 17,065ms)이 겹쳤다. 전체 WAL 27,273,043바이트·sync 90회·sync time 14,704.671ms, `dm-4` busy 99.78%·평균 queue 72.98·write await 169.82ms, timed checkpoint 1회·checkpointer write/sync 누적 증가 250,705/10,178ms였다. TOP20 entrants·shadow·계좌 복구도 소량 호출인데 각각 수초 COMMIT이 있었으므로 특정 writer 단독 원인으로 귀속하지 않는다. 뉴스 빈 claim 261회는 execute p50/p95 8.337/19.87ms, commit p95 2.037ms였다. 이 창은 일봉 호출이 없어 기존 일봉 고부하 run과 일대일 전후 비교가 아니다. checkpoint와 다른 프로세스 I/O의 기여도, 외부시장·분봉 간 선후 원인은 미확정이다. 종료 뒤 master/capture OFF, paused workload 0을 확인했다.

2026-09-30 O12 장후 프로그램 수급 빈 응답 판정(로컬, NAS 미적용): 정상 0행 종목에는 상장 첫날 종목·우선주·ETF가 섞여 있어 0행 자체를 요청 실패로 볼 근거가 없다. 같은 달력 날짜의 장후 조회에서 원본 저장이 확인된 `ka90008` 빈 목록만 `candidate_flow_finalization`에 `program_flow_rows=0`으로 표시하고 재요청하지 않도록 수정했다. 응답 형식 오류·저장 실패는 계속 실패로 남고, `date`가 무시되는 API의 익일 전일 조회 0행은 전일 자료로 확정하지 않는다. NAS 적용과 동일 부하 전후 효과는 미검증이다.

2026-09-30 O12 저장 없는 키움 직접 KRX 대조: NAS 앱 키로 공식 `ka90008`에 직접 읽기 요청을 보내고 DB 저장·원문 출력 없이 응답 코드/행 수/시간 양끝만 확인했다. 비교 종목 005930은 `date=20260929`와 `20260930` 모두 `return_code=0`, 200행, 첫/마지막 `tm=200005/181418`이었다. 486510·0035S0·950250·005935·950260·069500의 일반 KRX 코드를 `date=20260929`로 조회한 6건은 모두 `return_code=0`, 0행이었다. 즉 현재 시각에는 요청 날짜 변경이 6종목의 KRX 0행을 해소하지 않았고, `_AL`만 비어 일반 KRX는 채워진다는 후보도 지지되지 않는다. 005930의 두 날짜 응답은 건수·시간 양끝만 비교했으므로 전체 payload 동일성은 확인하지 않았다. 거래일 필드가 없는 `ka90008`에서 실제 기준일 전환 시각과 6종목의 데이터 미제공 원인은 아직 미확정이다.

2026-09-30 O12 사용자 요청 KRX 즉시 조회: 한국시간 00:23~00:26에 운영 NAS의 기존 인증 `/api/v1/kiwoom/query`로 486510·0035S0·950250·005935·950260·069500의 일반 KRX 코드와 `date=20260930`을 각각 1회씩 조회했다. 6건 모두 HTTP/API 정상 응답(`return_code=0`, `cache_hit=false`)이었으나 `stk_tm_prm_trde_trnsn`은 0행이었다. 이 조회 경로는 요청한 날짜의 `program_flow` 스냅샷으로 응답을 저장한다. 키움의 자료 기준일이 자정에 즉시 바뀌는지는 확인되지 않았으므로, 9월 29일 `_AL` 0행과 다른 기준일이라고 단정하지 않는다. 반대로 응답 행에 거래일 필드가 없어 같은 기준일이라고 입증할 수도 없다. 두 코드 형식 모두 0행이었다는 사실만으로 코드 형식 또는 종목별 프로그램 데이터 부재를 원인으로 확정하지 않는다.

2026-09-30 O12 `ka90008` 전일 KRX 대조 제약: NAS 공유의 키움 REST API 공식 문서(ka90008, PDF 372/855쪽)는 `date`에 다른 날짜를 넣어도 **당일 데이터만 제공**하며 `date` 파라미터는 삭제 예정이라고 명시한다. 문서의 `당일`이 자정 이후 즉시 새 달력 날짜로 전환된다는 근거는 없다. 사용자는 과거 기본정보 조회 등의 새벽 기준일 전환 경험을 알려주었으며, `ka90008`의 실제 전환 시각은 미측정이다. 따라서 00시대 KRX·전일 장후 SOR의 0행을 같은 거래일 표본으로 사용할 가능성은 열어 두되, 이번 빈 응답만으로 기준일을 검증할 수는 없다. `date=20260929`로 운영 `/api/v1/kiwoom/query`를 호출하면 서버가 실제 어느 거래일 자료를 반환하든 요청 날짜 키에 저장하므로 실행하지 않았다. 기존 `_backfill_candidate_flows`는 익일 07:40 전 전일 재시도를 허용하면서 `ka90008` 결과의 `tm`만 있는 행에 날짜 검증을 적용하지 못한다. 익일의 비어 있지 않은 응답이 어떤 거래일에 속하는지 별도 확인이 필요하다. 현재 운영 코드는 변경하지 않았다.

2026-09-30 O12 `ka90008` 빈 응답의 시장 코드 가설: NAS 인증 읽기 API로 운영 `program_flow` 스냅샷의 원문 값 없이 행 수만 확인했다. 2026-09-29 `SOR`에서 486510·0035S0·950250·005935·950260·069500은 각 0행이고, 비교 종목 005930·000660·035420은 각 200행이었다. 일부 빈 종목은 앞선 여러 날짜도 0행이었다. 현재 `_backfill_candidate_flows`는 모든 종목에 `{code}_AL`을 먼저 요청하고, 요청이 예외를 낼 때만 일반 `{code}`로 재요청한다. 따라서 성공 응답의 0행에 대해서는 KRX 형식 비교가 수행되지 않았으며, 6종목의 원인이 시장 코드인지 실제 프로그램매매 데이터 부재인지 미확정이다. 공식 키움 REST 문서는 `ka90008`의 `stk_cd`에 KRX/NXT/SOR 코드를 허용한다. 빈 응답을 정상 완료로 바꾸거나 일반 코드 fallback을 추가하기 전에 같은 종목·거래일에 대한 두 시장 코드의 실제 응답과 장중 0w 존재 여부를 대조한다. 현재 장후 완료 marker는 0행일 때 기록되지 않는다.

2026-09-30 O12 장후 TOP20 백필 반복 경로: 운영 로그에서 6개 종목의 성공했지만 비어 있는 `ka90008` 응답이 각 32~33회 반복됐고, 같은 기간 전체 거래일 백필 미완료 재시도 경고가 32회였다. `backfill_day`가 수급 단계 하나 실패해도 다음 재시도에서 전체 cohort의 일봉·분봉·coverage를 다시 검증하는 호출 경로를 확인했다. 로컬 수정은 성공한 종목의 봉·수급 단계만 동일 프로세스/거래일의 재시도 동안 기억한다. 실패 수급은 빈 응답을 성공으로 간주하지 않고 재시도하며, 새 cohort 종목을 처리하고, 전체 거래일이 성공하면 상태를 지운다. 서버 재시작·거래일 변경 때는 DB 상태를 다시 확인한다. 외부에서 이미 확인한 봉 또는 coverage를 같은 실행 중 삭제·변경할 경우 다음 재시도에서 즉시 재검증하지 않는 제한이 있으므로 NAS 배포 전 운영 데이터 변경 경로와 acceptance를 재확인한다. `ka90008` 빈 응답의 정상적인 비거래 의미는 아직 확정되지 않았다. 관련 단위검사 46건 통과, 운영 spike 개선은 재현 조건이 달라 미판정이다.

2026-09-29 O12 빈 뉴스 claim의 잔여 약 60ms 원인 확인과 통계 갱신: 삭제 후 실제 호출 28건의 SQL 실행창에서 stale RUNNING 복구 UPDATE p50 2.412ms, 후보 SELECT p50 63.143ms였다. 운영 DB의 동일 필터·정렬을 잠금/UPDATE 없이 읽기 전용 `EXPLAIN ANALYZE`로 실행하면 BODY/RULE 54.743/55.517ms, 결과 0행, ready 인덱스 251 shared hit·0 read, 기사 revision 스캔 0회였다. 동일 인덱스의 단순 빈 큐 조회는 1.425ms여서 인덱스 버퍼 확인 자체만으로 55ms를 설명할 수 없다. 복잡한 SELECT의 비용 추정치는 278577.77로 JIT 기준 100000을 넘고 JIT 함수 18개가 생성됐다. 같은 SQL을 세션 한정 `jit=off`로 실행하자 61.049ms에서 1.484ms로 줄었다. 삭제 직후 `pg_stat_user_tables`에는 dead estimate/deletes 26,982, `last_analyze`/`last_autovacuum` null이었고 자동 VACUUM 기준은 당시 catalog `reltuples` 약 308,770에 대해 기본 50+10%였다. 원인 판별 뒤 운영 `central_news_jobs`에 `ANALYZE`만 수행했다(11.313초, 성공; 데이터·schema·전역 설정·SQL 변경 없음). 후속 계획 비용 16.87, JIT 없음, 동일 읽기 전용 SELECT 1.954ms였다. 실제 worker 후속 capture 61.707초에서 빈 claim 175회·SQL 350회·commit 175회·DB/observer 오류 0회·dropped 0회였고 execute p50/p95 9.389/12.811ms, connection 획득 13.305/15.346ms, 전체 transaction 23.305/29.286ms였다. 두 측정 창의 부하가 같다고 보장할 수 없으므로 정확한 개선 배율은 단정하지 않는다. 호출량은 이전 약 106회/분에서 후속 약 170회/분으로 늘었고 execute p99 1324.68ms의 간헐적 지연은 미귀속이다. `ANALYZE` 뒤 `reltuples=286199`, `n_live_tup=286199`, `n_dead_tup=83404`는 추정치이며 실제 행 수가 아니다. 통계 재노후화와 간헐적 지연은 감시 대상으로 남기되, 현재 빈 claim의 지속적 60ms 문제만으로 pool·SQL·backoff/wake-up 코드를 추가하지 않는다. capture/master 종료 후 모두 OFF, 뉴스 worker는 계속 ON을 확인했다.

2026-09-29 O12 삭제 후 실제 claim 재측정: 운영 뉴스 worker를 멈추지 않고 공통 DB-call capture를 약 118초 실행해 빈 `news_job_claim` 209회(약 106회/분), SQL 418회(호출당 2회), commit 209회, DB/observer 오류 0회를 관측했다. execute p50/p95는 61.543/64.982ms, connection 획득 12.319/12.757ms, commit 0.381/0.438ms, 전체 transaction 74.545/77.83ms였다. 기존 삭제 전 60초 A/B/A의 claim SELECT p50 약 475/504ms와는 다른 시각·부하·계측 범위이므로 정확한 개선 배율은 주장하지 않는다. 별도 짧은 capture의 원시 기록 28건에서는 두 SQL 실행의 순서별 p50/p95가 stale RUNNING 복구 UPDATE 2.412/5.358ms, 후보 SELECT 63.143/65.308ms였다. 원시 응답은 다른 DB 호출과 함께 500건 제한에 걸려 전체 구간 표본이 아니며, 28건 분포를 전체 worker 분포로 일반화하지 않는다. 이 경로에서 잔여 실행 시간은 후보 SELECT가 지배한다. 현재 관측만으로 분당 약 106회의 빈 polling이 NAS CPU·WAL·저장장치 병목에 유의미하게 기여한다고 확정할 수 없어 idle backoff+enqueue wake-up 및 connection/SQL 동작 변경은 보류한다. 필요 시 같은 부하 구간의 DB/장치 비용을 먼저 귀속하고, SELECT 후보는 선택 순서·동시 선점·transaction 경계를 보존하는 전용 DB 검증 뒤에만 적용한다. 두 capture 종료 후 diagnostic master/capture OFF를 확인했다.

2026-09-29 O12 사용자 요청으로 기존 과거 뉴스 `PENDING` job 26,982행을 운영 PostgreSQL에서 삭제했다. 사용자가 아카이브 완료·백업 선행을 원하지 않는다고 명시해 별도 백업은 만들지 않았다. 삭제 전 `central_news_jobs`의 해당 두 PC 과거 scope·BODY/RULE·`PENDING` 키 집합은 아래 PC seed 지문과 동일했고, 참조 외래키·사용자 트리거는 없었다. 건수와 정렬 job_key 지문이 정확히 일치할 때만 실행하고 `DELETE ... RETURNING` 결과의 건수·지문도 다시 확인하는 단일 트랜잭션이 성공했다. 삭제 SQL은 기사 revision·본문·완료된 job·아카이브 파일을 건드리지 않았다. 삭제 후 과거 `PENDING` 0건, 전체 `PENDING` 0건을 읽기 전용으로 확인했다. 동일 필터·정렬의 잠금 없는 BODY/RULE `EXPLAIN ANALYZE`는 삭제 전 495.576/509.978ms, 삭제 후 55.985/56.58ms였고, 삭제 후 `idx_central_news_jobs_ready`의 필터 제외 0행·기사 revision Seq Scan `Actual Loops=0`·루트 shared read 0 blocks였다. 이 수치는 각 시점의 적은 표본이며 운영 전체 transaction 지연의 전후 분포를 뜻하지 않는다. 아래 26,982건/과거 후보 진단은 모두 **삭제 전 기록**이다. 향후 과거 importer가 같은 job을 다시 생성하지 않는지 확인하고, 별도 실시간 뉴스 enqueue·처리 회귀를 감시한다.

2026-09-29 O12 과거 채팅 seed와 운영 큐 대조: “모니터 리팩토링 및 과거자료수집” 작업에서 2026-09-27 운영 PostgreSQL의 뉴스 seed를 읽기 전용으로 추출할 때 `central_news_jobs` 110,982행 중 `PENDING` 26,982행이 기록됐다. 그때 PC에 보존한 `nas-news-seed-20260927.sqlite3`의 `PENDING` job_key를 정렬해 계산한 MD5와 삭제 직전 운영 PostgreSQL의 `PENDING` job_key 정렬 MD5는 모두 `de122024e8b8239cd51733a9bf1234c0`이고 건수도 각각 26,982로 같다. 삭제 직전 ready 조건에서도 26,982건이며 scope/stage는 아래 실측과 같다. 따라서 삭제 직전 빈 generic claim에서 걸러진 작업은 해당 seed의 기존 과거 작업과 동일한 키 집합이다. 지문은 키 집합 대조용이며 전체 행 내용·참조가 현재 seed와 불변이라는 증거는 아니다. 당시의 운영 job 변경 보류 판단은 위 사용자 요청에 따른 정확한 키 집합 삭제로 대체됐다.

2026-09-29 O12 운영 읽기 전용 실측: NAS 호스트의 기존 PostgreSQL 클라이언트로 `default_transaction_read_only=on`, 쿼리별 2.5초 timeout을 적용하고, 현재 generic claim의 BODY/RULE 필터·정렬·`LIMIT 1`만 `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`으로 실행했다. 실제 `FOR UPDATE SKIP LOCKED`와 stale-job UPDATE는 실행하지 않았다. BODY/RULE 각 1회는 결과 0행, 실행 495.576/509.978ms, `idx_central_news_jobs_ready` scan에서 필터 제외 26,982행, 역사 scope 기사 revision 순차 스캔에서 실제 55,053행을 보였다. 루트의 shared read는 각각 93,920/93,084 blocks이며 자식 노드의 포괄 값을 합산하지 않는다. 별도 읽기 전용 `PENDING`/retry-ready COUNT는 26,982였고, scope·stage 집계에서는 전부 과거 작업(`historical_market_pc_backfill` BODY 23,629/RULE 1, `historical_news_pc_backfill` BODY 3,352)으로 확인됐다. 전체 job `COUNT(*)`는 2.5초 제한 안에 끝나지 않아 현재 전체 실제 행 수는 미확인이다. `central_news_jobs`의 catalog `reltuples=308770`, 통계 `n_live_tup=n_dead_tup=0`, heap 약 894MB, index 약 149MB; 기사 revision heap 약 829MB다. 이 실측은 오래된 약 30만 건 삭제만으로 현재 상태를 설명한다는 가설을 확인하지 않으며, 넓은 역사 작업 후보와 revision 스캔이 빈 claim SELECT 비용에 직접 포함됨을 보여준다. 의미가 동등한 진단 후보 SQL을 교차 실행한 표본은 BODY 2.5초 timeout, RULE 798.136ms(원본 BODY/RULE 약 497.688/509.264ms)로 개선 근거가 없다. 캐시·동시 부하는 통제하지 못했으므로 표본 간 비율을 성능 회귀값으로 확정하지 않는다. 운영 SQL·polling·인덱스·VACUUM·자료 삭제는 변경하지 않았다. 다음에는 기존 과거 작업의 참조/보존 경계를 확인하고, 실제 선택/지연을 보존하는 별도 후보를 동등성·동일 부하 조건으로 검증한다. 로컬의 임시 read-only analyze API는 원인 판별 후 유지 필요성을 결정하고, 불필요하면 제거한다.

2026-09-29 O12 범위 정정: 앞으로 게시할 과거뉴스는 PC에서 ARTICLE/BODY/assessment/RULE까지 완성하고 불변 archive를 NAS에 읽기 전용으로 게시한다. archive의 seed `PENDING` job은 이력 증거일 뿐 NAS 실행 큐에 넣지 않으며, 해당 reader는 enqueue·BODY/RULE 계산을 호출하지 않는다. 따라서 일반 뉴스 worker의 enqueue 깨우기는 현재 NAS 실시간/검색/시장 뉴스 경로에만 적용한다. 이전 `scripts/import_prepared_historical_news_to_nas.py`는 정상 PostgreSQL 뉴스 테이블에 저장하면서 BODY/RULE job도 생성하는 별도 legacy 경로이므로 새 archive 게시 경로로 실행하지 않는다. 운영 PostgreSQL에 이미 남은 legacy historical `PENDING` job과 앞으로의 archive는 구분하며, 전자는 현재 generic claim 계획의 제외 필터 비용 후보지만 실제 scope별 잔량·버퍼 기여를 확인하기 전에는 원인으로 확정하지 않는다. 기존 행 정리는 archive 검증 뒤 별도 F단계의 PK/checksum manifest·참조·복구 검증을 거쳐야 하며 성능 개선 목적으로 선행 삭제하지 않는다.

2026-09-29 O12 일반 뉴스 claim의 실제 처리 행·버퍼 수를 구분하기 위해 로컬에 인증 전용 `GET /api/v1/diagnostics/news-job-claim-readonly-analyze?stage=BODY|RULE`을 추가했다. 한 번의 호출에서 지정 stage의 필터·정렬·`LIMIT 1`을 `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`으로 실행하되, 읽기 전용 transaction·2초 statement timeout을 적용하고 `FOR UPDATE SKIP LOCKED` 및 stale-job UPDATE는 제외한다. 실제 row·loop·shared/temp buffer만 제한된 노드 요약에 반환하며 기사/작업 행은 반환하지 않는다. 잠금·UPDATE가 빠진 계획이므로 운영 claim의 전체 실행시간과 같다고 해석하지 않는다. 기존 `idx_central_news_jobs_claim_order`는 stage·processing_version을 고정하는 별도 과거 뉴스 claim용이며, 일반 claim의 동적 stage/우선순위 정렬을 해결한 증거가 아니다. 로컬 관련 단위검사 4건 통과; NAS API 미배포. 동등한 SELECT의 운영 PostgreSQL 읽기 전용 실측 결과는 위 문단에 기록했고, 운영 SQL 변경 판단은 남았다.

2026-09-29 O12 저장 run `20260929T124558Z-a1ff1ba2` 뉴스 A/B/A는 각 60초 완료됐다. phase별 실제 claim 행은 0/0/0건, finish/retry 호출은 없었다. raw SQL 실행창에서 stale 복구 UPDATE p50은 baseline/resumed 2.455/2.599ms, claim SELECT p50은 474.697/503.712ms였고 COMMIT p50은 1.182/1.222ms였다. 즉 뉴스 worker의 빈 큐 polling이 느린 읽기 쿼리를 반복한다는 가설은 이 표본으로 지지된다. 이것은 DB 실행 부하 근거이며, WAL bytes나 장치 busy에 대한 개별 인과 기여를 입증하지 않는다. baseline에서만 외부시장 봉 저장 4회·3,637행 및 collector completion 1회가 있었고 다른 뉴스 경로도 계속 실행됐다. 같은 A/B/A는 반복하지 않는다. 후속 순서는 원본 claim SELECT의 revision 필터·정렬 비용을 줄이는 동등 SQL/인덱스 후보를 검증한 뒤, 빈 큐에 한정한 adaptive backoff와 새 job enqueue 후 worker 깨우기를 함께 검토하는 것이다. 현재 ready 인덱스는 이미 사용되고, 진단 후보 SQL도 revision 순차 스캔·정렬을 제거하지 못했으므로 몇 ms 수준의 개선을 약속하지 않는다. 깨우기는 성공한 transaction의 COMMIT 이후에 전달되어야 하고 검색·시장 뉴스·본문 후속·AI 등 모든 enqueue 경로를 포함해야 한다. 다른 프로세스의 enqueue나 신호 누락에도 작업을 영구히 놓치지 않도록 bounded polling을 유지한다. 최대 대기 5초는 깨우기가 닿지 않는 경로의 첫 선점 지연을 최대 그만큼 늘릴 수 있다. 운영 DB 설정·durability는 변경하지 않았다.

2026-09-29 O12 진단 API와 경계 검사: 컨테이너의 실제 진단 모듈에서 1,000개 보고서 count quota, 1GB sparse-file byte quota, raw 필드 제거, hard report size 제한, oversized 보고서 읽기/목록 제외, malformed JSON 목록 제외, history bounded window 초과 7개 검사가 모두 통과했고 임시 데이터는 자동 제거됐다. 저장장치 mapping의 새 timestamp도 컨테이너에서 읽혔다. CLI/API는 같은 `_run_measurement`를 공유한다. API는 raw DB-call endpoint를 추가 조회하고 CLI 기본 경로는 `db_calls_raw=null`이며, report `summary`는 raw를 제거하고 `raw` mode는 보존한다. API가 raw 정보를 더 주므로 전체 보고서 byte 동등성은 계약이 아니다. 0 elapsed에서 WAL/sec와 장치 `average_queue`·`busy_percent`가 모두 0으로 나눌 수 있음을 확인해 rate 값은 `null`로 두고 누적 counter는 보존하게 했다. relation 통계의 `relname` 모호성도 `to_regclass`/`relid`로 고쳤다. 진단 소스와 app/Compose/Dockerfile의 v7 표식을 NAS 공유에 단계 적용하고 원본 백업을 검증했다. NAS 실제 소스 AST·두 stats query fake-cursor·전체 `_measure()` 0초/장치표본 검사와 로컬 관련 테스트 27건이 통과했다. v7 후보 이미지 격리 smoke test 후 서버 서비스만 재생성했고 `/health`와 인증 snapshot의 전체 5개 section을 확인했다. 운영 DB 쓰기/workload 진단은 실행하지 않았다. snapshot 순간 `public.central_news_jobs`는 index scan 254,372회와 index tuples read/fetched 3,431,732,652, live/dead estimate=0, analyze 시각 null을 보였다. catalog에서는 `reltuples=308770`, `relpages=109110`, autovacuum enabled, table options 없음, analyze threshold 50/scale factor 0.1, database stats_reset null이었다. 즉 실제 행 0건이 아니라 `pg_stat_user_tables` estimate와 `pg_class` planner estimate의 불일치다. 저장소 코드·스크립트에서 `pg_stat_reset*`나 해당 relation `TRUNCATE` 호출은 찾지 못했지만, table-level counters가 0인 이유와 초기화 주체는 미확정이다. snapshot 순간 `WalSync`·`DataFileWrite` wait도 각 1행이었으나 특정 writer/호출로 귀속하지 않는다. 운영 compare와 추가 반복 표본은 아직 실행하지 않는다.

저장된 run `20260929T120733Z-ba837d0b`의 per-call market-bar COMMIT probe에서 `ka10081` PID 4463의 10,620ms COMMIT 동안 같은 backend가 `IO:WalSync` 402회와 `LWLock:WALWrite` 2회를 보였다. 다른 일봉 저장 COMMIT도 같은 두 wait를 보였고, 여러 writer에서 짧은 execute와 긴 commit이 동반됐다. 이로써 적어도 해당 느린 COMMIT의 직접 대기 종류는 WAL sync 경로로 좁혀졌으며, 동일 구간의 host `dm-4` queue/busy/write-await도 높았다. 다만 sampler가 capture 시점에 pending이었고 장치 지표는 host-wide이므로 장치 계층과 WAL 부하의 생성 주체는 아직 미확정이다. 새 측정이나 DB 동작 변경은 하지 않는다. 다음은 이미 확보한 정확한 PID·시간 표본만 사용해 WAL sync와 장치 매핑의 인과 한계를 정리하는 것이며, 이 결과만으로 durability/transaction/schema 조정은 하지 않는다.

2026-09-29 O12 일반 뉴스 선점 후보: 전용 PostgreSQL 동등성·`SKIP LOCKED` 2건 통과 후 운영 read-only 계획을 같은 파라미터로 비교했다. BODY/RULE 공통으로 원본 `hashed SubPlan`에서 후보 `Hash Anti Join`으로 바뀌고 플래너 비용은 278,577.78→155,633.36으로 감소했다. 기사 revision 순차 스캔(추정 53,558행), 작업 정렬 및 `LockRows`는 남는다. 이 비용은 실행 시간이 아니며 실제 지연·I/O·WAL wait 영향과 소유자는 미확정이다. O12 공통 DB 관측 1차 범위에서 writer SQL·인덱스·스키마를 변경하지 않는다. 별도 성능 작업을 진행한다면 운영 부하 조건과 충분한 표본을 맞춘 실제 시간·wait 비교 및 rollback 경계를 마련해야 한다. 로컬 `candidate_plans` 진단 API 코드는 미배포이며, 이 결과만을 위해 v7 서버를 재빌드하지 않는다. 일회성 probe는 결과 JSON 보존 후 NAS와 로컬에서 제거했다.

이 시황뉴스 감사의 날짜 무공백 판정은 **기존 수집 종료일 2026-09-22까지**다. 2026-09-23~09-28의 FLASH/WORLD 각 6일은 그 실행 범위 밖이라 원장에 없으며, 실패 날짜와 분리해 감사 JSON의 `uncollected_after_last_ledger_date`에 남겼다. 이 기간을 archive에 포함하려면 후속 수집 범위를 명시적으로 늘려야 한다.

2026-09-29 시황뉴스 완성 전 누락·재시도 원장: PC 원본 2019-01-01~2026-09-22의 FLASH/WORLD 날짜 각 2,822일과 페이지 순서를 읽기 전용으로 검사했다. 날짜 누락·페이지 번호 공백은 0건이며, 2021년 이후 기사 0건인 날짜도 0건이다. 동일 요일 전후 4주 중앙값의 30% 미만인 FLASH 83일/WORLD 8일은 [검토표](../data/historical_collection/audits/market-news-20260929/market-news-low-volume-days.csv)에 남겼다. 이 91일의 원본 페이지는 모두 연속·invalid 0이므로 저수집을 곧바로 누락으로 판정하지 않는다. 준비 DB에는 fulltext 4,228,654건, summary_only 52,754건, failed 711건이 있다. 실패 709건은 본문·목록 요약 모두 확보 실패, 2건은 WORLD 원본 제목 공란에 따른 변환 거부다. 원본 기사 URL 4,837,954개와 준비 결과를 직접 대조해 [미준비 원장](../data/historical_collection/audits/market-news-20260929/market-news-unprepared.csv) 555,835건(FLASH 2019~2020-08-24의 555,831건, 초기 WORLD 4건)을 확인했다. 이는 선처리기 연결 전 이미 완료된 날짜가 현 수집 경로에서 건너뛰어진 범위이며 원본 뉴스가 없다는 뜻이 아니다. [원문 재시도 원장](../data/historical_collection/audits/market-news-20260929/market-news-body-retry.csv)에 summary_only·failed 53,465건을 별도로 남겼다. 원문 삭제/접속 실패/본문 추출 실패의 개별 원인은 당시 준비 DB로 확정 불가다. PC 원본에서 미준비 기사 BODY/RULE을 2026-09-29 재개했으며 [진행 상태](../data/historical_collection/audits/market-news-20260929/preparation-status.json)와 URL별 요청 오류를 남긴다. 50건 재조회 표본은 FLASH 29건 시간 초과, WORLD 17건 현재 본문 확보·4건 HTTP 200이지만 본문 추출 실패였다. [휴일 대조](../data/historical_collection/audits/market-news-20260929/market-news-low-volume-holiday-check.csv)에서는 91일 중 공휴일 87일, KRX 휴장일 2일, 크리스마스 다음 날 2일이었다. 과거뉴스 archive 봉인·NAS 게시 전 두 원장의 처리 범위를 재검증한다. 원본 DB·NAS는 변경하지 않았다.

2026-09-29 O12 NAS 진단 API v6 운영 후속: v6 빌드 및 인증 API를 live 확인했다. v5 report publication race의 기존 report GET은 현재 200이다. 진단 API의 교차 프로세스 flock contention은 NAS 컨테이너의 실제 모듈로 3회 검사해 잠금 보유 중 `diagnostic_run_busy`, 해제 뒤 `ACQUIRED`가 모두 확인됐다. CLI와 API는 동일한 `_run_measurement`를 호출한다. 로컬 CLI 제어·workload 회귀 24건도 통과했다. 추가로 0초 경과에서 `wal_bytes_per_second`가 0으로 나누어지던 보고서 경계 결함을 고쳤으나 아직 NAS 미배포다. PostgreSQL Docker inspect의 bind source와 mountinfo/sysfs 재조회 결과는 일치했고 새 매핑을 API 파일 경로에 저장했다. 남은 것은 컨테이너가 새 timestamp를 읽는지 확인, CLI/API 보고서 전체 동등성, bounded history/report quota·failure다. 운영 compare는 workload pause를 동반하므로 실행하지 않는다. 일반 `NewsJobRunner.claim_news_jobs`는 read-only 계획에서 ready-index scan 뒤 약 13,536행 추정 Sort/LockRows 및 revision 테이블 약 53,558행 추정 Seq Scan(`hashed SubPlan`)을 보였다. CLI run `20260929T075024Z-0b3ad2eb`의 30초 집계만으로는 query별 wait 귀속이 안 됐지만, 뒤이은 API run `20260929T082106Z-9446184d`의 raw call과 동시 activity 표본을 대조해 24회 claim 중 22회, 총 35개 표본을 같은 PID/SELECT 실행창에 연결했다. 후보 SELECT 중 `DataFileRead` 10, `WALWrite` 11, `WalSync` 2, `BufferIo` 3회가 관측됐다. p50 execute/commit은 546.9/1.179ms, p95는 2,237.1/2.609ms였고, 2.75초 SELECT 중 WALWrite와 WalSync가 관측된 예도 있었다. 따라서 짧은 COMMIT은 WAL 대기를 배제하지 않으며, WAL 대기는 SELECT 실행 도중에도 발생한다. 원인 기여도와 WAL 부하 owner는 미확정이다. 추가 동일 측정은 보류하고, 전용 DB에서 의미가 보존되는 후보 쿼리의 계획과 replay·동시 claim 동작을 검증한다. 그 검사는 실행 경로 변경의 안전성을 판단할 뿐 운영 지연의 기여도를 확정하지 않으며, 운영 최적화는 별도 근거가 갖춰진 뒤 결정한다. 별도 historical claim 인덱스의 no-Sort 계획은 generic claim 증거가 아니다. relation stats의 0 estimate/NULL analyze 원인도 미확정이다. relname-only 통계 조회는 로컬 두 경로에서 `to_regclass`/`relid`로 수정·단위검증했으나 NAS v6에는 미반영이다. CLI/API 진단은 종료 후 master/capture OFF, paused workload 0, DB/observer 오류 0, dropped/truncated 0을 확인했다. 운영 DB write/schema 변경은 없다.

2026-09-29 O12 로컬 진단 API 초기 구현 시점 기록: control/snapshot/단일 owned run/cancel/report/history 경로와 CLI 공유 sampling, 고정 read-only PostgreSQL snapshot, 시간·원시 DB-call 결과를 로컬 구현했다. 기존 계측·app·CLI 묶음 회귀와 신규 인증/파일잠금 검사가 통과했다. 이 문단의 v4 배포 전 잔여 항목은 이후 v6 배포 확인으로 일부 해소됐으며, 현행 잔여는 바로 위 최신 기록을 따른다.

2026-09-29 O12 전체 NAS 운영 진단 API 설계 확정(구현 전): 사용자 요청에 따라 CLI의 제어·측정·비교·보고서와 PostgreSQL/WAL/대기·장치 정보를 API로 회수하는 [구현 계약](NAS_DIAGNOSTIC_API_DESIGN.md)을 정했다. 기존 CLI 수집을 import 가능한 코드로 공유하고 앱이 진단 run의 수명·중단·보고서를 소유한다. 현행 API와 writer transaction은 보존한다. 실제 NAS 재확인 결과 build v2, master/capture OFF, 기존 보고서 20개와 2026-09-25 생성 host storage mapping이 존재했다. 로컬 v3 plan 상세 초안은 아직 미검증·미배포다. 다음 구현은 control 공유화·수집 API·owned run/보고서이며, 이 기록은 API 완성이나 운영 배포 완료를 뜻하지 않는다.

2026-09-29 O12 API 진단 보완 및 NAS 배포 검증: 고정된 authenticated `GET /api/v1/diagnostics/news-job-claim-plan`을 추가해 NAS 공유에 선택 반영하고, live `/health`가 `2026.09.28-db-observability-v2`임과 API HTTP 200을 확인했다. PostgreSQL 17.11의 실제 계획 응답에서 stale-running UPDATE는 `idx_central_news_jobs_ready` 경로·estimated 1 row였다. BODY/RULE claim SELECT는 같은 ready index 경로와 Sort/LockRows, estimated 13,536 rows를 보고했고 `central_news_article_revisions` Seq Scan estimated 53,558 rows도 포함했다. API가 parent/join/filter 관계를 반환하지 않으므로 순차 스캔의 연결 방식과 실제 처리량·시간은 아직 모른다. 이는 `EXPLAIN` 예상치이지 실행 계측이 아니며 `ANALYZE`는 실행하지 않았다. 운영 스키마·데이터 변경 없음. 다음은 plan node 관계를 확인할 수 있는 안전한 관측 범위를 정하고, 근거 없이 poll 주기·인덱스·도메인 동작을 변경하지 않는 것이다.

2026-09-28 O12 첫 운영 표본에서 `news_job_claim`의 execute 대기가 새 조사 항목으로 확인됐다. 300초 동안 602회, execute p50/p95 473.6/489.2ms, commit p50/p95 1.181/1.323ms, SQL 1,204회였다. 현재 코드의 claim은 항상 stale-job UPDATE + 후보 SELECT를 실행하며 row를 claim하면 추가 UPDATE가 생기므로, 이 표본에서는 전 호출이 빈 claim이었던 것으로 추론된다. 10회 active-query 표본은 같은 claim fingerprint를 매회 포착했고 그 중 2회 `IO:DataFileRead`, blockers 없음이었다. `idx_central_news_jobs_ready`의 누적 `idx_tup_read`는 36 scan 동안 485,676 증가(약 13,491 tuples/scan), `claim_order` 사용 횟수 변화 없음이었다. live/dead tuple은 311,499/54,106 추정, 마지막 autoanalyze는 2026-09-25. 이는 실제 query 실행계획의 원인을 확정하진 않지만 넓은 후보 인덱스 scan/I/O 가능성을 강하게 좁힌다. 이제 원본 경계와 동일한 `EXPLAIN (FORMAT JSON)`만(ANALYZE 없이) stale 복구 UPDATE 및 BODY/RULE SELECT에 실행하는 `artifacts/explain_news_job_claim_readonly.py`를 준비·NAS artifacts에 해시 일치 배치했다(SHA-256 `D7054AEE75A57E00BB2E2E5D3745E4B85F7D87538BDE6226B172B5147373B492`). 다음은 해당 출력으로 index path·예상 row·정렬 계획 확인이다. poll 주기나 인덱스·데이터 변경은 계획/증거 검토 전 보류한다.

2026-09-28 O12 capture helper 기록: `artifacts/capture_live_db_calls_5m.py`는 capture 자동 종료까지 수행했고 5분 표본 결과는 위 항목에 기록했다. 출력의 `enabled_at_end=true`는 capture 종료 직전 측정 창의 상태다. 후속 진단 상태 조회로 완전 OFF를 독립 확인하는 일은 남아 있다. 관측 `UNREGISTERED=0`은 opt-in 표본에 한정되며 전체 DB 경로 coverage의 증거가 아니다.

2026-09-28 O12 운영 배포 완료, 계측 acceptance 미완료: NAS 서버만 `2026.09.28-db-observability-v1`로 재생성했고 health 및 인증 `/api/v1/diagnostics/db-calls` 응답을 확인했다. database 컨테이너 ID는 유지됐다. 이 검증은 API 가용성을 확인한 것이며 실제 운영 요청의 writer/read call, 미등록 write 0건, DB transaction/commit 수 보존, wrapper 처리량 overhead와 장시간 안정성은 아직 판정하지 않았다. 스키마·저장 포맷을 바꾸지 않았고 운영 DB 백업은 사용자 요청에 따라 생략했다. 아래 배포 전 상태 문단은 시점 기록이다.

2026-09-28 O12 운영 전환 대기: 사용자의 판단에 따라 무스키마 변경에 대한 별도 운영 DB 백업 단계는 생략했다. 기존 NAS 소스·Compose·Dockerfile 7개는 별도 백업됐고, 후보와 운영 프로젝트의 Docker 빌드 입력 309개가 해시 일치한다. 실행 중 컨테이너는 이전 이미지 그대로다. 서버만 재생성하는 `artifacts/deploy-db-observability-v1.sh`를 NAS에 배치했으며 NAS 셸 구문 검사·실행, 새 `/health.server_build`와 인증 `/api/v1/diagnostics/db-calls`, DB 컨테이너 불변을 확인해야 한다. 실패 시 스크립트는 이전 이미지로 서버만 복귀하며 소스·Compose 복원은 별도 확인이 필요하다.

2026-09-28 O12 배포 게이트 갱신: 별도 NAS 후보 이미지 `2026.09.28-db-observability-v1` 빌드와 후보/실행 이미지 ID 구분은 완료했다. 운영 서버는 기존 `2026.09.26-bar-upsert-wait-correlation-v5`로 계속 실행 중이다. 기존 NAS 소스·Compose·Dockerfile 7개는 해시 검증해 별도 백업했다. 운영 DB 백업, 후보와 운영 소스 동기화, 서버 재생성, `/health` 및 인증 진단 API 검증이 남았다. 아래의 '이미지 build 남음'은 빌드 전 시점의 기록이다.

2026-09-28 O12 정적 direct connection guard 완료: 기존 함수 단위 29곳의 분류·이유를 [승인 원장](postgres_access_direct_connection_approvals.json)에 두고 `scripts/audit_postgres_access.py --check --output docs/postgres_access_inventory.json`으로 검사한다. 신규 또는 사라진 직접 연결은 review_required와 종료 코드 1로 표시된다. 현재 기준 통과. 별칭/동적 연결, 실행 시점의 transaction coverage 증명은 이 AST guard 범위 밖이다. standalone report 필요성 조사도 완료했으며 현재 별도 공통 exporter는 만들지 않는다. NAS 실행 빌드 `2026.09.26-bar-upsert-wait-correlation-v5`에서 인증 진단 route가 404임을 확인했다. 운영 적용은 미완료다. 운영 API 검증, writer overhead acceptance, 상시 집계 및 전체 raw 보존이 남아 있다.

운영 적용 전 source-scope 검토: NAS 공유 체크아웃 HEAD `5779931`은 로컬 HEAD `047a528`의 조상이지만 양쪽 모두 미커밋 변경이 많다. NAS `app.py`/`database.py`도 NAS HEAD 대비 각각 +250/-16, +1670/-171행이므로 깨끗한 구버전 소스가 아니다. 두 작업본 직접 비교에서 로컬 `app.py`는 NAS보다 +91/-3행(진단 route와 별개인 historical archive 경로 포함), `database.py`는 +871/-106행(다수 transaction의 공통 wrapper 이관)이다. NAS에는 `postgres_access.py`가 없고 `diagnostic_metrics.py`의 DB call 집계·capture generation도 없다. 따라서 route만 추가하면 공통 metric producer가 없어 빈/부분 결과가 된다. 현재 실행 빌드는 `2026.09.26-bar-upsert-wait-correlation-v5`이며 공유 소스의 같은 문자열은 실행 이미지와의 완전 일치를 증명하지 않는다. 임시 NAS 기반 합성 후보는 아래와 같이 만들었으며, 전체 차이·기존 기능·전용 DB 회귀를 확인한 뒤 배포 범위를 결정한다. 현재 NAS 파일·image·운영 DB는 변경하지 않았다.

source-scope 검토의 PC 임시 후보: NAS 공유 `src`/`scripts`를 PC 임시 디렉터리에 복제하고 로컬 `database.py`, `diagnostic_metrics.py`, `diagnostic_writer_registry.py`, 신규 `postgres_access.py`, `nas_workload_diagnostic.py`와 인증 `db-calls` route만 통합했다. NAS `app.py`의 historical archive 경로·설정은 가져오지 않았다. 이 복제 후보에서 관련 단위검사 107건과 workload/session 상관 단위검사 23건, 중앙 서버 구문 검사가 통과했다. 더 넓은 로컬 테스트 163건 중 1건은 로컬 API 계약 fixture가 후보에서 의도적으로 제외한 historical archive 2개 경로를 요구해 실패했다(나머지 162건 통과). 이는 운영 이미지와 전체 기능의 검증이 아니다. NAS 기반 후보와 PostgreSQL 통합검사 79개를 묶은 v1 `artifacts/kiwoom-db-access-nas-stage-20260928.zip`은 전용 DB 사전검사 후 79 tests를 실행했고 58 pass/21 fail(46.197초)이었다. 21건 모두 call count assertion에서 실패했다. 해당 assertion들은 `writer_kind`만 골라 같은 kind의 관측 READ도 writer 횟수에 포함할 수 있었다. 실패 전체에서 동일 패턴을 확인해 해당 21개 선택 조건에 `access_mode=write`를 추가한 v2 `artifacts/kiwoom-db-access-nas-stage-v2-20260928.zip`(1,324,437바이트, SHA-256 `7A932E4A0FB28797FF2560A27CFA6869390409B6A14B245B197536152D115B4C`)를 재생성했다. 사용자 실행 결과 전용 PostgreSQL 통합검사 79개가 38.854초에 모두 통과했다. 이는 NAS 공유 소스 기반 합성 후보의 전용 DB 검증이며, 운영 이미지와 일치하거나 운영 API에서 계측된다는 증거는 아니다. Docker 이미지 build, 실행 이미지 식별·rollback, 운영 적용은 남았다.

2026-09-28 O12 standalone report 필요성 확인: 저장소 내 NAS workload 진단기는 자체 JSON 보고서를 저장하며 DB summary 연결도 로컬 완료했다. prepared-news import runner는 고정 snapshot용 상태 JSON/log를 쓰고, archive 설계는 이 legacy 경로를 대량 적재 용도로 재사용하지 않는다고 명시한다. seed exporter는 산출 SQLite manifest와 stdout summary를 제공하는 읽기 전용 일회성 도구다. 저장소 내 scheduled caller는 확인되지 않았다. 별도 범용 metric export 구현은 현재 필요성이 확인되지 않아 보류한다. NAS 외부의 수동/예약 실행 여부는 확인하지 않았다.

상태 보정: 수집 방식은 process-local capture와 기존 API를 유지하는 것으로 결정했고 보고서 summary 연결도 로컬 구현했다. 아래 coverage 재감사 문단의 “다음 설계 판단은 standalone ... 또는 공용 수집”은 결정 전 기록이다. 공용 collector는 현재 후속 작업이 아니다.

2026-09-28 O12 보고서 연결 당시 남은 항목 기록: NAS 진단 CLI의 `db_calls` 수집과 producer/session 검사는 로컬 구현 및 관련 회귀를 통과했다. 운영 NAS 서버의 버전·인증 API 연결, 직접 연결 정적 guard(후속 완료), 실제 활성 standalone 도구 자체 report, writer overhead acceptance, 상시 집계·전체 raw 보존은 이 기록 시점에 남아 있었다. 아래 수집 경계 선택·보고서 구현 문단은 당시의 미완료 기록이다.

2026-09-28 O12 수집 경계 결정 후 다음 작업: 공유 collector는 보류하고 기존 `nas_workload_diagnostic` 보고서에 `/diagnostics/db-calls` summary를 연결한다. 현재 CLI는 domain metrics만 회수한다. 공통 계측의 producer 수명과 capture session을 확인하고 자동 OFF 전에 같은 시간 구간을 회수하며, 만료/교체/404/통신 실패를 빈 정상 결과와 구분한다. 구현·로컬 묶음 회귀는 아직 남았다. [공통 DB 검토](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)의 구현 계약을 따른다. 다음 별도 범위는 정적 미분류 direct-connection guard이며 standalone export는 실제 사용 확인 시 적용한다. 상시 집계·전체 raw 보존·실제 writer overhead acceptance·운영 배포 검증도 미완료다. 아래 coverage 재감사 문단의 수집 방식 선택은 이번 결정으로 해소됐으나 coverage 한계 자체는 남는다.

2026-09-28 O12 공통 DB coverage 재감사: 현재 AST 재실행은 PostgreSQL store method 102개, literal driver connect 29곳, 전체 backend 후보 API call 6,679곳, parse error 0이다. `PostgresQueryStore`에서 직접 `_connect()` context를 여는 곳은 5개(`save/load_five_minute_bars`, `save/load_market_data_metadata`, `register_account_scope_alias`)이고 공통 관측 context는 84개다. 5개 중 `src`에서 운영 호출자가 확인된 것은 없으며 alias 등록은 상위 application helper까지만 확인됐다. 직접 driver 29곳을 공통 writer factory, 별도 diagnostic probe, test/benchmark, 운영 maintenance로 분류해 상세 원장을 갱신했다. 테스트·점검 및 동적 호출 가능성은 남으므로 전수 runtime coverage로 해석하지 않는다. raw `psycopg.connect`는 공통 runtime `UNREGISTERED`에서 보이지 않고, capture-only DB call deque는 프로세스 로컬이라 독립 importer 표본을 서버 API가 조회할 수 없다. 다음 설계 판단은 standalone maintenance 실행 결과를 자체 출력으로 남길지, 공용 수집 경계를 만들지다. PC 완성 archive가 기존 대량 PG importer를 대체할 예정이므로 importer 준비 단계 READ 계측은 그 실행 필요성을 확인한 뒤 진행한다.

2026-09-28 O12 prepared-news importer 배치 관측 pilot 완료: `_complete_batch` outer native transaction만 공통 계측하고 기사별 savepoint와 연결 소유권을 보존했다. 같은 connection의 두 batch에서 distinct call ID와 commit count, 보류 기사 savepoint rollback 뒤 peer commit을 포함한 NAS 전용 PostgreSQL 통합검사 4건이 통과했다(사용자 제공 결과, 16.147초). 운영 환경의 실제 COMMIT latency 전후 비교는 하지 않았다. importer 준비 단계의 `_article_revision` SELECT와 그 연결의 read-only commit은 아직 공통 관측 범위 밖이다. 단독 importer 프로세스의 in-memory metric은 서버 API 프로세스에서 직접 조회되지 않으므로 장기 보관·공유 방식은 별도 검토한다.

2026-09-28 O12 document writer batch 완료: 실제 호출이 확인된 `news_watchlist`, `news_automation_settings`, `server_operational_settings`와 Forward Evaluation repository가 선언하는 17 collection kind에 collection별 common DB context를 연결했다. 후자의 모든 kind에 production caller가 있다는 뜻은 아니며, 공통 generic writer가 지원하는 이름의 계측 격차를 닫는다. native per-call transaction과 commit boundary는 바꾸지 않았다. v1의 기존 recovery·decision-gate·dispatch metrics assertion은 동종 READ 포함 때문에 실패했으나 family+kind 필터로 수정했다. v2 전용 PostgreSQL 4 tests가 통과(38.363초)했고 20 kind 독립 write/read·mock automation lineage·approval·dispatch 검증을 마쳤다. 로컬 관련 unit 68건 중 28 pass/40 environment skip. v2 ZIP SHA-256 `3631B2EA634B1A3FB9F2A14D65F6F54AC771B87CE5DF2E93EFC33EFCD7A335BF`의 NAS 전송·host hash 대조 완료.

2026-09-28 O12 startup schema migration pilot 완료: PostgreSQL `initialize()`를 `schema.migration/central_schema`로 관측했다. SQLite 및 migration runner는 유지했다. 로컬 공통 DB·migration 99건 통과(40 environment skip), 전용 PostgreSQL 3 tests 통과(1.419초): 정상 재실행 commit, 실패 migration의 DDL rollback·ledger 미기록, native context 종료 동작을 확인했다. ZIP SHA-256 `B672F6322FBF6CB2D108716453F5939F906CE18CEEA4DB1C8D2F1FA56CB27BBB` NAS host hash 일치.

2026-09-28 O12 active single-document READ pilot 완료: `load_document`가 `ForwardEvaluationRepository._save_immutable → _find` replay 경로에서 호출됨을 확인하고 `read.document_collection/document:<collection>:single` 관측을 적용했다. SQL·decode·per-call native transaction은 유지했다. Forward Evaluation 로컬 회귀 23건과 NAS 전용 PostgreSQL hit/missing reader·metrics 검사가 통과했다(0.328초). 첫 ZIP package error는 `tests/__init__.py`를 추가한 v2에서 해결했다. 그 밖의 `load_five_minute_bars`, `load_market_data_metadata`는 현재 활성 PostgreSQL production caller가 확인되지 않아 이 pilot 범위에 포함하지 않았다.

2026-09-28 O12 실계좌 event/recovery writer 공통 관측 pilot 완료: 두 PostgreSQL 메서드에 `account.real_monitor / real_account_event|real_account_recovery`를 등록했다. 기존 큐·주기 호출, per-call native transaction, global advisory lock과 데이터 저장 helper를 유지했다. 로컬 공통 DB 40건과 실계좌 46건 통과. NAS 전용 PostgreSQL `test_real_account_event_and_recovery_keep_replay_fence_and_separate_commits` 통과(1.076초): 동시 저장·replay·stale settings fence·rollback·metrics·cleanup을 확인했다. ZIP 해시 대조 완료. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 계좌 설정 writer 공통 관측 pilot 완료: `save_account_settings`와 `save_market_profile_settings`를 `account.settings / account_settings_save|market_profile_settings_save`로 등록했다. 기존 per-call native transaction·`credential-activation` advisory lock·CAS helper 및 real/mock runtime drain·credential fence를 보존했다. 로컬 관련 검사 45건과 전용 PostgreSQL `test_account_and_market_profile_settings_keep_cas_fence_and_independent_commits` 통과(4.861초). 동시 real/mock 저장, replay, CAS rollback, market-profile fence와 global 설정 row 원복을 확인했다. ZIP의 NAS 호스트 SHA-256도 일치했다. `register_account_scope_alias`는 현재 runtime caller가 없어 활성 writer에 포함하지 않았다. 운영 DB와 NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 query-cache READ 공통 관측 pilot 완료: 실제 REST 호출 `CentralRestBroker._resolve_cache_or_queue → asyncio.to_thread(store.load_query)`에 명시적 `read.query_cache/query_cache` context를 연결했다. 공통 raw call에 `access_mode`를 기록하고 집계 응답을 `writers`와 `readers`로 나눴으며, 기존 context의 기본값은 `write`다. 기존 SELECT·hit/miss/expiry 반환·driver-native transaction 종료를 유지했다. 로컬 회귀 44건 및 전용 PostgreSQL `test_query_cache_reader_keeps_native_context_and_separates_metrics` 통과(0.497초): hit·expired·missing 결과, 세 native SELECT transaction의 context/commit/PID 및 readers 분리를 확인했다. ZIP 1,317,559바이트(SHA-256 `4D3D304A74D8BCADA53E93EE7874F6E6D693394413F4A5C8250FC6211A153765`) 호스트 전송·해시 일치를 확인했다. 운영 DB와 NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 document collection READ pilot 완료: API operational settings, market coverage, stock catalog, external-market roll state, mock automation과 news source가 호출하는 `PostgresQueryStore.load_documents`를 collection별 `read.document_collection/document:<collection>`으로 계측했다. PostgreSQL SELECT·결과 decoding·native context 및 SQLite 구현을 유지했다. 로컬 회귀 99건과 전용 PostgreSQL `test_document_collection_reader_keeps_native_context_and_kind_metrics` 통과(0.352초): hit·빈 결과·native commit·reader grouping 및 writer 격리를 확인했다. ZIP 1,317,824바이트(SHA-256 `C854AC3B299F7F858596114D2CB95D813A69E124B232CAC4A586F23C64B3ED4B`) import와 NAS 호스트 해시도 일치했다. 운영 DB·앱 이미지는 변경하지 않았다. `fetchall`은 현재 `execute_ms`에 따로 포함되지 않아 전체 조회 시간은 `total_ms`에서 확인한다. `load_document`는 현재 운영 callsite가 검색되지 않아 범위에서 제외했다.

2026-09-28 O12 market bar READ pilot 완료: market-data API와 `CentralMarketDataIngestor`가 호출하는 `PostgresQueryStore.load_minute_bars`·`load_daily_bars`에 `read.market_bars`의 독립 kind를 연결했다. 기존 SQL, result mapping, connection/native transaction 및 SQLite 구현을 유지했다. 로컬 회귀 100건과 전용 PostgreSQL hit/empty·native context·reader metric 검사 `test_market_bar_readers_keep_native_context_and_separate_kinds`가 통과했다(0.570초). ZIP 1,318,215바이트의 NAS 호스트 해시가 일치했다. `fetchall`은 `execute_ms`와 분리되지 않아 `total_ms`가 read 전체를 포괄한다. caller-owned connection의 prepared-news importer는 별도 경계다.

2026-09-28 O12 observation revision READ pilot 완료: candidate monitor의 초기 seed·증분 replay 및 mock automation 초기 seed reader 두 개를 `read.observation_revisions`의 별도 kind로 관측했다. 기존 query contract와 native connection context·SQLite 구현을 보존했다. 로컬 회귀 117건 및 NAS 전용 PostgreSQL 검사(0.208초)가 통과했다. 증분 sequence/commit 순서 문제는 기존 cursor contract 항목으로 별도 유지한다.

2026-09-28 O12 active READ batch 완료: 운영 caller가 확인된 shadow state/event page, dataset snapshot, market metadata range 네 reader를 세 family로 이관했다. 기존 SELECT 수와 native per-call context를 보존했다. 관련 로컬 회귀 183건과 NAS 전용 PG 통합검사(0.565초)가 통과했다.

2026-09-28 O12 news READ batch 완료: active news API/collector/job의 history·article/body revision·stock/confirmed publications·source cursor/diagnostics·market feed 8개 reader를 세 family로 계측했다. NAS 전용 PostgreSQL `test_news_source_page_replay_and_failure_preserve_article_and_progress` v2가 통과했다(3.656초). v1 테스트 실패는 GLOBAL source-page fixture를 watchlist-only reader에 넣은 데이터 범위 불일치였고, 생산 경로를 변경하지 않고 watchlist 전용 임시 row로 수정했다. 전용 검증은 ZIP `kiwoom-db-access-news-readers-batch-pilot-v2-20260928.zip`의 NAS 호스트 SHA-256 일치본으로 실행했다. 이전 v1 실패와 보정 사유를 기록으로 보존한다.

2026-09-28 O12 market-state READ batch 완료: `load_realtime_snapshots`, `load_latest_market_caps`, `load_theme_snapshots`, `load_market_event_history`, `load_hot_cohort` 5개 reader를 계측했다. NAS 전용 PostgreSQL에서 realtime writer/replay, market-event history·cohort revision rollback, theme replacement/snapshot-history 3건이 묶음 실행으로 통과했다(3.239초). ZIP `kiwoom-db-access-market-state-readers-batch-pilot-20260928.zip` 1,312,198바이트(SHA-256 `9C3AA646A1E43A8BCA4670216E32068DC5F54A739D814AC35A70970630EC1BC2`)의 NAS host hash가 일치했다.

2026-09-28 O12 external/news READ batch 완료: active `CentralRestBroker` external bar API, `NewsSourceCollector` request budget 및 `CentralNewsAIService` revision reuse reader 세 개에 공통 context를 연결했다. NAS 전용 PostgreSQL external bar replay, concurrent budget claim, AI result/revision atomicity 테스트 세 건이 묶음 실행으로 통과했다(6.748초). ZIP `kiwoom-db-access-external-news-readers-batch-pilot-20260928.zip` 1,312,481바이트(SHA-256 `E35BBE3A5712ECFCE47D58E93F1CD02C0FEABADCB4520848E7AA7C06AD7A69C8`)의 NAS 호스트 hash가 일치했다.

2026-09-28 O12 credential/account READ batch 완료: 실제 caller가 있는 7개 조회에 common READ 관측을 연결했고 SQL·native transaction을 보존했다. account identity/binding, account settings CAS, credential profile lifecycle, vault commit 후 activation replay/rollback을 포함한 NAS 전용 PostgreSQL 묶음 4건이 통과했다(15.970초). 다음은 주문·execution 복구에 직접 연결된 intent/broker-order/event/mock-control reader의 호출·저장·복구 경계를 조사한다.

2026-09-28 O12 execution READ batch 완료: intent ID, active intent, scoped broker order, intent/account event, mock control 6개 reader를 공통 READ context에 연결했다. NAS 전용 DB v1에서 원장·control CAS가 통과했고 stop recovery metrics assertion은 reader/writer kind 충돌로 실패했다. `writer_family` 필터를 보강한 v2에서 3건이 모두 통과했다(6.324초). 기존 SQL·native transaction·복구 결과는 보존했다.

2026-09-28 O12 `news.external_finish` BODY/RULE 전용 PostgreSQL 검사 완료(4.890초): BODY 저장과 RULE assessment/event/membership/job 완료의 replay·metrics, event/membership 후 강제 실패 rollback을 확인했다. NAS 앱 이미지와 운영 DB는 변경하지 않았다.


2026-09-28 O12 PC 과거 뉴스 완료 경로 pilot 완료: `complete_external_historical_news_job`에 `news.external_finish:BODY/RULE` 관측을 추가하고 기존 native transaction 경계를 유지했다. 로컬 관련 검사 54건 중 53건 통과·전용 DB 1건 skip. 전용 PostgreSQL `test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback` 통과(4.890초): replay, assessment/event/membership/job 완료 원자성, 부분 실패 rollback, metrics 및 cleanup을 확인했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 연구 observation export writer pilot 완료: `research.observation_export / research_observation_export_create`를 등록하고 기존 native transaction과 fixed-watermark reader를 보존했다. 전용 PostgreSQL `test_research_export_keeps_fixed_membership_and_rolls_back_partial_failure` 통과(0.533초): replay pagination, 새 revision 비편입, member 부분 저장 실패 rollback, metrics 및 cleanup을 확인했다. NAS 서버 앱 이미지·운영 DB는 변경하지 않았다.

2026-09-28 O12 credential profile 공통 관측 pilot 완료: create/register/rename/archive 4개 writer를 개별 kind로 등록하고 native transaction 및 global advisory lock을 보존했다. 로컬 회귀 142건 중 141건 통과(전용 DB 1건 skip). NAS 전용 PostgreSQL `test_credential_profile_writers_preserve_replay_lifecycle_and_independent_metrics` 통과(6.812초): 동시 create replay와 요청 digest conflict, rename/archive replay 및 보관 뒤 상태 보존, register 멱등성, commit/rollback metrics와 cleanup을 확인했다. ZIP 1,312,748바이트, SHA-256 `1880FED52C2E95512305EF685B54250C7CB934EE9FE6196D1273F6205A1239CC`; NAS host hash 일치. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 credential activation finalize 공통 관측 pilot 완료: `finalize_credential_activation`을 `credential.activation / credential_activation_finalize`로 등록했다. 기존 per-call connection, global advisory lock, binding revision·activation receipt·profile·settings 단일 transaction을 보존했다. 관련 로컬 회귀 115건 통과. 전용 PostgreSQL `test_credential_activation_finalize_replays_and_rolls_back_after_file_commit` 통과(8.831초): file commit 뒤 동시 finalize replay, 단일 binding/receipt, account settings conflict 시 multi-table rollback과 vault file 유지, metrics·비밀값 미기록·cleanup을 확인했다. ZIP 1,311,643바이트, SHA-256 `D60983CAEE9E9533ADA0F3FB789F1424687E6278599766F3342073F7857CDA94`; NAS 호스트 해시 일치. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 execution ledger 공통 관측 pilot 완료: intent 생성·event 적용·계좌 snapshot·runtime lease acquire/release 5개 저장 경로를 계측하고 registry에 writer kind를 명시했다. 각 메서드의 native transaction 경계를 유지했다. 로컬 회귀 101건 및 전용 PostgreSQL `test_execution_ledger_and_lease_keep_native_boundaries_and_metrics`가 통과했다(3.967초). replay/readers, 실패 시 event rollback과 intent 보존, lease 획득 거부·token CAS·소유권 상실, metrics/call ID/backend PID 및 임시 행 cleanup을 확인했다. ZIP 1,301,195바이트, SHA-256 `8CD43418B57598B888F8BBE488C574B1F3AA37E5DC3B9C4B14F1A18B0F7B6D24`; NAS 호스트 해시 일치. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset.statistics_cache` / `dataset:top20_statistics_day` pilot 완료: warm/cold cache와 cache 행 단일성, 제거 뒤 동시 조회, native context commit·metrics를 전용 PostgreSQL 1건(0.820초)에서 확인했다.

2026-09-28 O12 `document:news_article`·테마 문서 저장 pilot 완료: `NewsService`의 기사 UPSERT가 `central_documents` projection, immutable article revision, BODY job을 기존 한 transaction에 저장하는 경로를 계측했다. `CentralContentSync`의 테마 증분 UPSERT와 전체 교체에서 `theme_profile`·`theme_stock`·`theme_metadata`를 각각 계측하고 metadata snapshot 이력 처리를 유지했다. 로컬 관련 회귀 92건 통과(27 skip 포함), 전용 PostgreSQL 기사 revision/job/replay/failure rollback 검사(2.551초)와 테마 UPSERT·교체/snapshot/rollback 검사(v2, 3.652초)가 통과했다. v2 ZIP의 NAS 호스트 SHA-256이 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `document:news_ai`·`document:news_ai_shared`·`document:news_request_usage` pilot 완료: `CentralContentSync._read_news`의 증분 전송 대상 세 projection writer를 명시 계측했다. 최초 전용 PostgreSQL 검사는 `news_ai`의 성공 2건과 의도한 rollback 1건을 assertion에서 합산하지 않아 실패했고, kind별 기대 건수를 바로잡은 v2 검사에서 replay·rollback·metrics와 cleanup이 통과했다(1.716초). 로컬 회귀 92건(27 skip 포함) 및 구문·diff 검사가 통과했고 NAS ZIP SHA-256 대조 완료. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `document:journal_news_link`·`document:journal_v2_news_links` pilot 완료: legacy와 계정별 v2 push UPSERT를 공통 계측 목록에 추가했다. 전용 PostgreSQL 검사에서 같은 기사 identity의 legacy/v2 저장 분리, 서로 다른 v2 계정, 양쪽 replay, v2 tombstone을 통한 pull·로컬 merge·계정별 reader 결과, 실패 rollback, metrics와 cleanup이 통과했다(8.958초). 관련 로컬 회귀 176건도 통과했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `CentralJournalSyncService` document kind 22종 pilot 완료: `journal_*` v1/v2 collection 20종과 `journal_sync_states`/`journal_v2_sync_states`를 `document.collection` 공통 관측에 명시 등록했다. 로컬 관련 회귀 107건과 SQLite 실제 sync fixture 확인이 통과했다. 전용 PostgreSQL에서 v1/v2 계정 scope, tombstone, replay, collection별 독립 transaction 및 후속 실패 rollback 검사가 통과했다(21.111초). 검사 ZIP의 NAS SHA-256 일치를 확인했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `CentralSettingsSyncService`의 `app_settings`·`app_column_settings` pilot 완료: 두 순차 POST와 독립 transaction을 유지하면서 각각 `document:app_settings`·`document:app_column_settings`로 계측했다. 전용 PostgreSQL `test_central_settings_collections_replay_and_keep_independent_transactions`에서 settings 필터·버전 병합, 열 너비의 로컬 보존, replay, 첫 저장 commit 뒤 두 번째 묶음 실패 rollback, call metrics 및 cleanup이 통과했다(4.449초). v1 fixture의 미commit SQLite insert 때문에 sync가 0건이었던 문제를 fixture transaction으로 수정한 v2에서 통과했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 REST 분·일봉 공통 관측 pilot 완료: 실제 `MarketDataIngestor` 저장과 `load_minute_bars`/`load_daily_bars` reader를 확인하고 `_replace_bars`의 연결·cursor만 wrapper로 이관했다. 기존 capture 전용 WAL 설정/savepoint, market-bar SQL phase 계측, COMMIT wait sampler, minute advisory lock, metadata/revision lineage, 명시 COMMIT·rollback·close 경계를 보존했다. phase 표본과 legacy transaction 표본은 공통 `call_id`로 연결한다. 로컬 회귀 102건(전용 PostgreSQL 검사 2건 skip), 구문·ZIP 검사가 통과했고 전용 PostgreSQL `test_query_market_bars_keep_native_history_and_correlate_both_metrics`에서 minute/daily replay, same-key 병렬 revision chain, 실패 rollback, PID·metrics correlation이 통과했다(1.501초). ZIP(2,179,903바이트, SHA-256 `0FD65D7CB04C76C2F595F51061403E2E5AFC234810CB2A5F0F28FD6DD4F75D84`)의 NAS host hash 일치를 확인했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `CandidateMonitor` decision/event 및 checkpoint writer pilot 완료: 두 기존 transaction을 각각 관측하고 전용 PostgreSQL replay·reader·checkpoint 실패·rollback 검사(`test_shadow_evaluation_and_checkpoint_keep_replay_and_independent_commits`, 1.352초)가 통과했다. 로컬 관련 검사 46건도 통과했다. 검사 ZIP 1,299,765바이트, SHA-256 `CF5FCD256FBBD38815A670F49C3FC7407D6E6E5175AE370CBD25203DC1A2FD5E`의 NAS host hash 일치 확인. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

완료(2026-09-28): PostgreSQL 정적 원장을 현재 코드로 재생성하고 production caller 및 common writer registry와 대조했다. `PostgresQueryStore.create_observation_export`는 `/api/v1/research/observations`의 활성 PostgreSQL writer다. immutable revision selection→fixed manifest 및 ordered export membership 저장은 단일 native transaction이며 이후 `load_observation_export_page`가 동일 watermark로 다시 읽는다. writer 계측과 registry 등록 후 전용 PostgreSQL `test_research_export_keeps_fixed_membership_and_rolls_back_partial_failure`가 통과했다(0.533초). 원장 후보 총계는 운영 writer/transaction 수가 아니다. scope alias helper와 `save_five_minute_bars`는 production caller가 확인되지 않아 이번 활성 후보에서 제외했다.

`complete_external_historical_news_job`의 기존 관측 wrapper 결함과 전용 PostgreSQL 검증은 상단 pilot 결과로 해결했다. 이전 문서의 “PC 과거뉴스 direct event helper는 미계측” 표기는 `news.external_finish:RULE` 관측으로 대체한다.

확인 필요: `CandidateMonitor._run`은 iteration 예외 처리에서 `_save_checkpoint`를 다시 호출한다. 실패가 `_consume` 중 전략 메모리 상태를 바꾼 뒤 발생하면 cursor는 이전 값인데 바뀐 state가 checkpoint에 남을 수 있다. 이번 pilot은 transaction 경계를 바꾸지 않았고, dedicated PostgreSQL 검사도 실패한 checkpoint write 뒤 event replay를 검증했지만 이 별도 `_run` error-path 조합은 재현하지 않았다. 후보 중복·누락 영향은 전용 회귀로 확인한 뒤 필요하면 별도 수정한다.

정정: `save_mock_automation_control`은 이후 공통 layer로 이관됐고 `test_mock_automation_control_cas_preserves_native_transactions` 전용 PostgreSQL 검증도 통과했다. 계좌 advisory lock과 revision compare-and-set은 transaction 안에 유지한다. 아래 과거 stop pilot 문장의 “다음 단계에서 검토”는 현재 상태로 대체한다.

혼합 kind `save_dataset_snapshots` 호출은 현재 production source에서 확인되지 않았다. 공개 저장 메서드는 기존 연결 경로를 유지하고, 실호출이 생기기 전에는 wrapper 대상으로 추정 확장하지 않는다.

2026-09-28 O12 `dataset:ranking`·`dataset:top20_membership` 공통 관측 pilot 완료: 동종 batch만 wrapper로 감싸고 native transaction·비동기 COMMIT·revision source lineage를 유지했다. 로컬 회귀 143건 통과(27 skip 포함), NAS 전용 PostgreSQL replay·revision lineage·rollback·metrics 통합검사 1건 통과(2.952초).

2026-09-28 O12 `dataset:new_high`·`dataset:program_flow` pilot 완료: 동종 batch writer 두 kind를 공통 관측에 등록했다. 로컬 회귀 92건(65 pass·27 skip)과 전용 PostgreSQL kind별 replay/readback·제약 실패 rollback·metrics/call ID·cleanup 검사가 통과했다(0.362초). 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset:market_state` pilot 완료: 공통 계측을 동종 배치에만 적용하고 기존 비동기 COMMIT·저장·rollback 경계를 보존했다. 로컬 관련 회귀 92건(65 pass·27 skip)과 전용 PostgreSQL replay/readback·제약 실패 rollback·metrics/call ID·cleanup 검사가 통과했다(0.193초). v1의 잘못된 `saved_at` 동등성 assertion은 v2에서 기존 갱신 동작을 검증하도록 수정했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 external market bar 관측 pilot 완료: `YahooDelayedMarketCollector._collect_once`의 5m·1d fetch 호출이 각각 `PostgresQueryStore.save_external_bars`를 실행하며, 기존 호출별 native transaction을 유지하고 `external_market:bars` 공통 계측을 추가했다. 관련 SQLite writer·공통 access 로컬 회귀 39건 중 12건 통과·27건 skip, 구문검사와 `git diff --check` 통과. 전용 PostgreSQL `test_external_market_bar_batch_preserves_reader_and_independent_commits`에서 timeframe별 load/replay, 독립 transaction 4건, call ID/backend PID metrics가 통과했다(0.994초). 무작위 instrument 자료 cleanup 검증도 통과했다. ZIP `kiwoom-db-access-external-market-bars-pilot-20260928.zip`(2,501,069바이트, SHA-256 `A8F152E6FE783C686819B9A332FA33A873E2CFF045B997743617639342C4F4AB`) NAS 호스트 hash 대조 완료. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 다음 realtime document 묶음 완료: 실시간 flush에서 매수 체결 종목 범위를 보존하는 `account_entry_symbols_daily`와 0G 상·하한가/기준가 문서 `stock_price_references`를 `upsert_documents` 명시 관측 kind에 추가하고 기존 reader/replay/metric correlation을 검증했다. 둘 다 일반 `central_documents` UPSERT라 기존 transaction과 reader 계약은 그대로다. `theme_metadata`/`news_article` history helper와 `market_state` dataset commit 정책은 제외했다. 구문검사와 `git diff --check` 통과, `test_postgres_access` 단위 38건 중 11건 통과·27건 skip. v1 ZIP은 package initializer 누락으로 app의 구버전 module을 불러 검사 시작 전에 실패했고, DB에는 접근하지 않았다. initializer를 포함한 v2 `kiwoom-db-access-realtime-documents-pilot-v2-20260928.zip`(2,500,635바이트, SHA-256 `EAD8801700C777CDA8980A0F96E71A9F1EA64A5FF8AB3125539B4CC6FCF99138`)을 NAS 호스트로 보내 hash/size 일치를 확인했다. 사용자 실행 결과 전용 PostgreSQL `test_realtime_document_batch_preserves_readers_and_correlates_metrics` 1건이 0.714초에 통과했다. 운영 DB와 NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `realtime.latest` / `realtime.minute` / `realtime.minute_finalize` / `realtime.second_bar` 묶음 pilot 완료: 실시간 flush의 latest snapshot, 0B 분봉 delta, 분봉 확정, 절대 초봉 writer 네 개를 공통 계측에 연결하고 각 기존 transaction과 legacy success sample에 call ID를 보존했다. 분봉 advisory lock, operation ID/hash 재생 방지, query authority 판정, metadata/revision 이력과 collector의 실패 복구 순서는 유지했다. 관련 로컬 회귀 130건이 통과했고 전용 PostgreSQL `test_realtime_writer_batch_preserves_replay_lineage_and_independent_commits`에서 네 writer replay/reader, 실패 delta 전체 rollback, final revision, 최신 초봉 유지, metric correlation 및 cleanup이 통과했다(2.143초). 검사 ZIP `kiwoom-db-access-realtime-writers-pilot-20260928.zip`(1,292,240바이트, SHA-256 `F6DD2985EFFADEACB79F2ACCDE11C533D3B8559683B47D72F5F416FFF2EA3650`)의 NAS 호스트 hash를 확인했다. PC 검사 실행기는 이 세션에 DB URL이 없어 preflight를 실행하지 못했으며, NAS 컨테이너 전용 DB 검사는 통과했다. 운영 DB·서버 이미지는 변경하지 않았다. 운영 writer overhead guard는 미완료다.

2026-09-28 O12 `market_event.revision` 묶음 pilot 완료: `MarketEventService`의 VI 이력, hot cohort 이력/current projection, 상한가 사실 저장 세 writer에 공통 관측을 연결했다. 호출당 독립 native transaction, cohort 이력과 current의 동일 transaction, 기존 replay/reader 동작을 유지했다. 관련 로컬 회귀 49건이 통과했고 전용 PostgreSQL `test_market_event_revision_batch_preserves_replay_projection_and_rollback`에서 세 writer replay, 현재 projection, 중간 실패 rollback, backend PID·call metrics 및 cleanup이 통과했다(1.704초). 검사 ZIP `kiwoom-db-access-market-events-pilot-20260928.zip`(1,290,600바이트, SHA-256 `C99182724DCC305E3F267ECF396EE0010F1887D79DC2C96A89EF56F4D26EE087`)을 NAS에 전송하고 호스트 hash를 확인했다. 운영 DB·서버 이미지는 변경하지 않았다. 실제 운영 계측과 overhead guard는 미완료다.

2026-09-28 O12 `document:execution_mock_automation_recovery_decisions` / `document:execution_mock_automation_current_recovery` 계측 pilot 완료: `ForwardEvaluationRepository.save_mock_automation_recovery_decision`의 operating spec/admission/lease/risk lineage 검증과 기존 두 transaction 경계를 유지했다. fixture를 repository save 경계와 일치시킨 뒤 관련 로컬 unittest 60건 및 구문/whitespace 검사가 통과했다. 전용 PostgreSQL `test_mock_automation_recovery_preserves_lineage_and_separate_projection`에서 저장/replay, 최신 risk 불일치 거부, current write 실패 뒤 재시도, 이력/current 독립성, metric call ID 및 cleanup이 통과했다(4.946초). v3 ZIP NAS 전송과 해시 대조 완료. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 O12 `document:execution_mock_automation_decision_gates` / `document:execution_mock_automation_approved_gates` 계측 pilot 완료: `save_mock_automation_decision_gate`의 gate history와 승인 gate일 때 intent별 approved projection을 별도 transaction으로 유지했다. submit 전 저장 순서와 application unit tests의 동일 intent 단일 제출/replay 검증은 변경하지 않았다. 관련 unittest 114건, SQLite fixture/storage preflight, ZIP import/test load 및 전용 PostgreSQL `test_mock_automation_decision_gate_preserves_approval_boundary`가 통과했다(11.141초). 전용 PG는 승인/차단/replay·부분 실패/retry·risk lineage·metric·cleanup을 확인했으며 실제 주문은 제출하지 않았다. 운영 DB·이미지는 변경하지 않았다.

2026-09-28 O12 `document:execution_mock_automation_dispatch_receipts` / `document:execution_mock_automation_dispatch_by_intent` 계측 pilot 완료: `ForwardEvaluationRepository.save_mock_automation_dispatch_receipt`의 승인 gate 검증을 유지하고 receipt history와 intent reader projection의 독립 저장 transaction을 계측했다. 관련 mock automation·DB·계측 로컬 회귀 114건 및 SQLite fixture/storage preflight가 통과했다. 전용 PostgreSQL `test_mock_automation_dispatch_receipt_recovers_separate_intent_projection`에서 이력/replay reader, projection write 실패 뒤 이력 보존과 재시도, call ID 및 cleanup 검사가 통과했다(0.591초). 시험은 저장소만 호출해 주문 transport를 실행하지 않았다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 O12 `document:execution_mock_automation_stop_revisions` / `document:execution_mock_automation_current_stop` 계측 pilot 완료: `emergency_stop_mock_automation`의 STOPPED control 저장과 메모리 신규 주문 차단 뒤 stop history/current projection을 기록하는 흐름을 확인하고 두 document kind만 계측했다. 관련 로컬 회귀 113건 및 SQLite failure/retry preflight가 통과했다. 전용 PostgreSQL `test_mock_automation_stop_preserves_closed_control_and_recovers_projection`에서 current projection 실패 뒤 STOPPED control과 history 보존, projection 재시도, reader·metric correlation 및 cleanup이 통과했다(1.575초). 운영 DB·서버 이미지는 변경하지 않았다. 별도 미이관 경로인 `save_mock_automation_control`은 account별 advisory lock과 revision compare-and-set transaction을 사용하며 다음 단계에서 독립적으로 검토한다.

2026-09-28 O12 `document:execution_mock_automation_risk_snapshots` / `document:execution_mock_automation_current_risk` 계측 pilot 완료: `ForwardEvaluationRepository.save_mock_automation_risk_snapshot`은 immutable snapshot을 저장·재조회한 뒤 별도 transaction으로 계좌별 current risk projection을 갱신한다. reader는 이력 전체와 최신 projection을 각각 읽는다. 두 collection kind만 공통 계측에 등록했으며 revision gate와 transaction 순서는 그대로다. 관련 mock risk·admission·DB·계측 로컬 회귀 114건이 통과했다. 전용 PostgreSQL 검사에서 저장/replay, current writer 실패 뒤 기존 projection 유지와 retry 복구, 단조 revision 거부, 두 writer kind의 metric call ID, 임시행 cleanup이 통과했다(0.590초). ZIP `kiwoom-db-access-mock-risk-pilot-20260928.zip`(1,287,342바이트, SHA-256 `5788882DB386034C387F7EA4F25BC8CBAEB3741C0DB5BE419019236EB5687B15`)의 import·정확한 테스트 load와 NAS 전송·원격 해시 대조도 통과했다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 O12 `document:credential_vault_state` 계측 pilot 완료: `CredentialStore._mark`는 PostgreSQL `credential_vault_state` 문서에 초기화·revision fence만 저장한다. 최초 marker(0)는 암호화 파일 전에 쓰고, 이후 암호화 파일이 commit point이며 fence write 실패 시 다음 load에서 더 높은 file revision을 복구한다. collection kind만 공통 계측 대상으로 등록해 이 순서와 DB transaction 경계를 유지했다. credential vault·DB·공통계측 로컬 회귀 114건이 통과했다. 전용 PostgreSQL 통합검사에서 first marker/write, fence failure 뒤 vault 재시작 복구, file write failure의 fail-closed, metric call ID 및 임시행 cleanup이 통과했다(1.416초). ZIP `kiwoom-db-access-credential-vault-pilot-20260928.zip`(1,286,606바이트, SHA-256 `4020530D8C7D0606C08244E43DDC928255C07946BD79B83E2757A91BC02D122E`)의 NAS 전송·해시 대조 및 ZIP import/test load 검사도 통과했다. 운영 DB와 서버 이미지는 변경하지 않았다.

2026-09-28 O12 `document:execution_mock_automation_runner_current` 로컬 계측 이관: NAS 앱은 `create_query_store(active.database_url)`로 PostgreSQL store를 만들고 이를 `MockAutomationSupervisor`/`MockAutomationRunner`에 전달한다. runner는 account owner/current key로 checkpoint를 읽고 version/spec/run/hash가 일치할 때 cursor, fill cursor, StrategyState, seen fills, status를 복원한다. 일치 checkpoint가 없을 때에만 입력 history를 bootstrap하고 최초 checkpoint를 저장한다. `upsert_documents` kind만 계측에 추가해 이 조건 및 native per-call transaction을 유지했다. mock runner·공통계측·DB 로컬 회귀 97건과 구문·ZIP import/정확한 테스트 load가 통과했다. 전용 PostgreSQL boot checkpoint·상태 갱신·restart 복원·복원 추가 write 없음·metric call ID 검사 ZIP `kiwoom-db-access-mock-runner-checkpoint-pilot-20260928.zip`(1,285,914바이트, SHA-256 `71275ADD67556610271C2C7EC743AFDBF7A413AFD5066229E93F4E496C630FAD`)은 NAS `/tmp` 전송 및 원격 해시 일치를 확인했고, 전용 DB 검사가 통과했다(0.658초, cleanup assertion 포함). 운영 DB와 서버 이미지는 변경하지 않았다.

2026-09-28 O12 `document:news_assessment` 로컬 계측 이관: `NewsJobRunner._run_rule`은 원문 발행시각을 조회해 RULE assessment projection을 기록한다. `/api/v1/news/history/body`가 `load_document`로 읽고, market feed/stock article query들도 collection을 join한다. `_run_rule`의 기존 async-to-thread writer와 후속 event revision 저장 경계를 유지하면서 collection kind만 공통 계측에 추가했다. 뉴스 job·공통계측·DB 단위 회귀 103건과 구문·ZIP import/정확한 test load 검사가 통과했다. 전용 PostgreSQL `_run_rule` GLOBAL assessment 저장·reader·동일값 replay·metric call ID 검사 ZIP `kiwoom-db-access-news-assessment-pilot-20260928.zip`(1,284,897바이트, SHA-256 `B2755768C05A73A95F02532B1B561BF56F5C9272C9F25D012F6A10E0C2685FCB`)은 NAS `/tmp` 전송 및 원격 해시 일치를 확인했고 전용 DB 검사가 통과했다(2.749초, cleanup assertion 포함). 운영 적용은 하지 않았다.

2026-09-28 O12 `document:external_market_collection_status` 로컬 계측 이관: `YahooDelayedMarketCollector.collect_once` 성공/실패 경로가 `_save_status`를 호출해 provider·contract별 최근 상태를 기록한다. 저장소에서 이 문서를 읽는 domain call site는 확인되지 않았다. 기존 상태 저장 transaction만 공통 계측 kind에 추가했다. 외부시장·공통계측·DB 단위 테스트 100건, 구문 및 ZIP import/정확한 테스트 로드 검사가 통과했다. 전용 PostgreSQL `_save_status` 실패 상태 write·readback·동일 projection replay·metric call ID 검사 ZIP `kiwoom-db-access-external-market-status-pilot-20260928.zip`(1,284,563바이트, SHA-256 `197346C54E1B6AA2B89818A7BDE8F0D9D9FFC6999B4EC2EC88A0C9D6713B3D98`)은 NAS `/tmp` 전송 및 원격 해시 일치를 확인했고, 전용 DB 검사가 통과했다(2.201초, cleanup assertion 포함). 운영 적용은 하지 않았다.

2026-09-28 O12 `document:news_original_publication` 로컬 계측 이관: `NewsJobRunner._run_body`가 본문 fetch 후 원문 발행시각을 문서에 먼저 저장하고, 이후 별도 BODY transaction을 실행한다. `_run_rule`은 같은 collection을 읽는다. 기존 async-to-thread 호출과 publication/body transaction 분리를 유지하고 이 kind만 common DB observability에 추가했다. 관련 뉴스·DB 단위 회귀 65건, 구문·ZIP import·정확한 테스트 로드 검사가 통과했다. 전용 PostgreSQL `_run_body` write·`load_documents` readback·동일값 replay·metric call ID 검사 ZIP `kiwoom-db-access-news-original-publication-pilot-20260928.zip`(1,284,394바이트, SHA-256 `3E39A57D0CFABCB7B9395B26A26AF6958D286DADC30A8B2680E6669930CCEE13`)은 NAS `/tmp` 전송 및 원격 해시 일치를 확인했고, 전용 DB 검사가 통과했다(0.317초, cleanup assertion 포함). 운영 적용은 하지 않았다.

2026-09-28 O12 `document:market_event_sessions` 로컬 계측 이관: `MarketEventService.mark_krx_session_observed`, `close_krx_regular_session`, `close_observation_day`가 같은 owner/session key projection을 각기 독립 `upsert_documents` transaction으로 갱신한다. 현재 소스에서 별도 domain reader는 찾지 못했으며 전용 검사는 세 원래 transaction과 replay 1회를 기록하고 최종 projection을 DB에서 확인했다. 해당 collection kind만 공통 계측에 추가했다. market-event·DB 테스트 65건 및 전용 PostgreSQL marker/readback·계측 검사 1건이 통과했다(0.611초, cleanup assertion 포함). 운영 적용은 미완료다.

2026-09-28 O12 `document:condition_search_status` 로컬 계측 이관: `MarketEventService._condition_list`는 조건식 목록에서 정책에 맞는 선택 결과·runtime 상태를 저장하고, `/api/v1/market/events?kind=cohort`가 같은 문서를 읽는다. 실제 handler test를 추가하고 해당 `upsert_documents` kind만 common observability에 추가했다. 관련 market-event·DB 회귀 65건과 구문검사, ZIP `--help`·내부 소스 import·정확한 test load가 통과했다. 전용 PostgreSQL 실제 condition-list handler·문서 replay·reader·metric call ID 검사 1건이 통과했다(0.242초, cleanup assertion 포함).

2026-09-28 O12 `document:candidate_flow_finalization` 로컬 계측 이관: `AutonomousTop20Service._backfill_candidate_flows`는 투자자·프로그램 수급 응답을 검증한 뒤 날짜별 `complete` marker를 기록하고, 다음 호출은 marker를 읽어 두 요청을 생략한다. marker writer kind만 공통 계측 목록에 추가했다. 관련 TOP20 idempotency·DB 테스트 55건 통과, 구문검사 통과. 전용 PostgreSQL replay·reader skip·metric call ID 검사 1건이 통과했다(0.767초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-28 O12 `document:candidate_flow_capture` 로컬 계측 이관: `AutonomousTop20Service._capture_candidate_investor_flow`는 API에서 수급 원본을 검증한 뒤 최초 편입 marker를 기록하고, 재호출은 marker를 읽어 Kiwoom 요청을 건너뛴다. marker kind만 공통 관측 목록에 추가해 API 및 marker transaction 구성을 보존했다. 관련 TOP20·DB·계측 회귀 99건 통과(1건 skip), ZIP 실행기·내부 소스 import·정확한 test load·구문 검사가 통과했다. 전용 PostgreSQL replay·reader skip·metric call ID 검사 1건이 통과했다(0.331초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-28 O12 `document:market_data_coverage_intraday` 로컬 계측 이관: `AutonomousTop20Service._backfill_entry_minutes`는 신규 편입 종목의 날짜별 장중 분봉을 수집·저장한 뒤 `entry_backfill` marker를 쓰고 다음 실행은 그 문서를 읽어 반복 수집을 건너뛴다. marker kind만 공통 관측에 추가해 수집·저장 순서와 transaction 경계를 유지했다. 관련 TOP20·DB·계측 테스트 99건 통과(1건 skip), ZIP 실행기·소스 import·정확한 테스트 이름 로드와 구문검사를 통과했다. 전용 PostgreSQL replay·reader skip·metric call ID 검사 1건이 통과했다(0.974초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-28 O12 `document:market_data_coverage` 로컬 계측 이관: `AutonomousTop20Service._backfill_minutes`는 완료 문서의 `kind/window_closed/session_finalized`와 저장된 분봉이 함께 확인될 때만 재조회 없이 반환한다. 완료 후 marker를 기록하며 app의 archive reader도 이를 확인한다. 해당 `upsert_documents` kind만 공통 관측 목록에 추가해 기존 저장 transaction 경계를 유지했다. 관련 로컬 TOP20·DB·계측 테스트 99건 통과(1건 skip), ZIP `--help`·소스 import·정확한 통합검사 이름 로드와 구문 검사를 통과했다. 전용 PostgreSQL reader/replay·실제 skip 경로·metric call ID 검사 1건이 통과했다(0.661초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-28 O12 `document:market_data_coverage_daily` 로컬 계측 이관: `AutonomousTop20Service._backfill_daily`는 실제 일봉 저장을 확인한 뒤 날짜별 완료 marker를 기록하고 다음 호출은 `as_of` 기준으로 재사용한다. API reader도 같은 문서를 읽는다. 해당 `upsert_documents` kind만 기존 native transaction 계측에 추가했으며 일봉 저장과 marker transaction 경계는 유지했다. TOP20·DB·공통 계측 로컬 테스트 99건 통과(1건 skip). 첫 ZIP은 `scripts`, v2는 `tests.integration` package marker 누락으로 테스트 시작 전에 실패했다. v3 `kiwoom-db-access-daily-coverage-pilot-v3-20260928.zip`(1,279,973바이트, SHA-256 `4FE5C547AEB633334CD044A70EE2A796F42860EDC005224FF4A9E9B77FF89183`)에는 두 package marker와 ZIP 최상위 `kiwoom_monitor` 소스를 포함했다. 로컬 ZIP 자체 import와 NAS 해시 일치를 확인했으며, 전용 PostgreSQL replay·reader·metric call ID 연결 검사 1건이 통과했다(0.263초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `document:market_index_chart_coverage` 로컬 계측 이관: `AutonomousTop20Service._backfill_market_indexes`는 KOSPI/KOSDAQ의 지수 분봉·일봉 dataset을 각각 저장한 뒤 완료 문서를 기록하며, 다음 실행은 이 문서를 읽어 재수집을 건너뛴다. 완료 문서의 `upsert_documents` kind만 공통 관측에 추가하고 기존 별도 transaction 순서를 유지했다. 관련 TOP20·DB·계측 회귀 137건 통과. 전용 PostgreSQL 저장/replay·실제 skip reader·metric call ID 연결 검사 1건이 통과했다(0.307초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `document:historical_highs` 로컬 계측 이관: `AutonomousTop20Service._ensure_historical_high`는 KRX 일봉·NXT 설정을 사용해 목표를 계산하고 하루 기준 projection을 `upsert_documents`로 갱신한다. 같은 서비스와 API reader가 `historical_highs`를 읽는다. 이 collection kind만 기존 document native transaction 계측 목록에 추가했다. 관련 TOP20·DB·계측 로컬 회귀 137건 통과. 전용 PostgreSQL 동일값 replay·reader·기존 metric call ID 연계 검사용 ZIP `kiwoom-db-access-historical-highs-pilot-20260927.zip`(1,274,066바이트, SHA-256 `FD5789464BA1C66F5EB52E38DB886B46D4B3ABA1B1055D4F466A85CAF1568F9F`)은 NAS `/tmp` 전송 및 해시 일치를 확인했고 전용 DB 검사 1건이 통과했다(0.359초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `document:top20_daily_entrants` 로컬 계측 이관: 실제 TOP20 갱신 흐름에서 `AutonomousTop20Service`가 전날 snapshot을 읽고 `PostgresQueryStore.upsert_documents`로 일별 신규 편입 종목을 기록한다. 이 collection만 기존 native transaction 기반 common DB 계측 대상으로 추가했다. 관련 TOP20·DB·계측 로컬 회귀 137건 통과. 전용 PostgreSQL 동일값 replay·reader·기존 metric call ID 연계 검사용 ZIP `kiwoom-db-access-top20-entrants-pilot-20260927.zip`(1,273,996바이트, SHA-256 `D7011AD19590D7403A123E4FF8F55631CC713C4133E36023CF0BE09493CF78B9`)은 NAS `/tmp` 전송 및 해시 일치를 확인했고 전용 DB 검사 1건이 통과했다(0.199초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.historical_market_batch` 로컬 계측 이관: 준비된 FLASH/WORLD 과거뉴스 import 호출인 `save_historical_market_news_batch`에 공통 관측을 추가했다. batch별 advisory transaction lock, 이미 import된 결과 반환, 기사 이력·작업·source observation·run·cursor 저장을 기존 native transaction 안에 뒀다. kind는 원천별, `rows_attempted`는 입력 기사 수로 기록한다. 관련 local 회귀 108건 통과. 최초 전용 검사에서 저장 경로가 올바르게 `ValueError`를 발생시켰으나 통합검사가 `KeyError`를 기대해 실패했다. 검사 기대값을 실제 입력 검증 계약에 맞춰 수정한 뒤 v2 ZIP `kiwoom-db-access-historical-market-batch-pilot-v2-20260927.zip`(1,273,684바이트, SHA-256 `A89C566C34C41941DC15E64C282D54BF1D5942ABF36AA506F6AB0FBD71173B23`)로 재실행해 전용 PostgreSQL 동시 replay·reader·실패 rollback 검사 1건이 통과했다(1.252초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.request_budget` 로컬 계측 이관: `PostgresQueryStore.claim_news_request`는 `query_set`/`watchlist` 호출의 날짜별 총량과 scope 한도를 `central_news_request_budget`의 EXCLUSIVE table lock 아래 읽고, 허용 시에만 UPSERT한다. 기존 native connection transaction과 거부 시 commit을 유지한 채 공통 관측을 연결했다. kind는 scope별로 나누고 `rows_attempted`는 실제 쓰기가 없는 거부 호출도 있으므로 미설정한다. 관련 로컬 회귀 117건 통과. 전용 PostgreSQL 동시 claim·scope/hard limit·계측 검사용 ZIP `kiwoom-db-access-news-request-budget-pilot-20260927.zip`(1,272,740바이트, SHA-256 `8B857A3281CD5D73D915D7C2271C949B687B04B87223B9D377744D534C79A0CA`)은 NAS `/tmp` 전송 및 해시 일치를 확인했고 통합검사 1건이 통과했다(1.436초, 임시 행 정리 assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.source_page` 로컬 계측 이관: 검색뉴스와 FLASH/WORLD 수집기가 `asyncio.to_thread(PostgresQueryStore.save_news_source_page)`로 호출하는 PostgreSQL 저장 transaction에 공통 관측을 추가했다. 기사 이력 잠금·재사용, BODY 작업, 대상 revision, source observation, run, cursor는 기존 한 native transaction에 남는다. 계측 kind는 `query_set`과 `naver_stock_market`으로 분리하고 `rows_attempted`는 페이지 기사 후보 수만 뜻한다. 공통 DB·뉴스 source·중앙 DB 로컬 회귀 117건 통과. 전용 검사 ZIP `kiwoom-db-access-news-source-page-pilot-20260927.zip`(1,280,019바이트, SHA-256 `26FF6A0EFE1671D32530222760037B5353EED3B2920CEC2DFE19B37FB7ED5527`)의 전용 PostgreSQL replay·FLASH reader·중간 오류 rollback 통합검사 1건이 통과했다(0.642초, 정리 assertion 포함). PC 과거뉴스 완료가 helper를 직접 호출하는 경로는 이 메서드 계측 범위가 아니다. 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.job_enqueue` 로컬 계측 이관: `NewsService`의 `asyncio.to_thread(PostgresQueryStore.enqueue_news_ai_jobs)`가 최신 기사·본문 revision을 읽고 본문 revision 기반 AI 작업을 `ON CONFLICT DO NOTHING`으로 등록하는 기존 한 native transaction에 공통 관측을 추가했다. 입력 후보 수는 `rows_attempted`로 기록하며 실제 삽입 수와 구분한다. 공통 DB·뉴스 관련 로컬 회귀 71건과 변경 파일 구문 검사가 통과했다. 전용 검사 ZIP `kiwoom-db-access-ai-enqueue-pilot-20260927.zip`(1,278,863바이트, SHA-256 `F4416BC24634DA074B1A4B63E71F5CFF1B17D737E78F307178C46D03016BF248`)의 전용 PostgreSQL 병렬 동일 작업 replay·본문 참조 reader·후보 중간 실패 rollback 통합검사 1건이 통과했다(0.752초, 정리 assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.event` 로컬 계측 이관: 일반 `NewsJobRunner._run_rule`이 `asyncio.to_thread(PostgresQueryStore.save_news_event_revision)`로 호출하는 사건 저장 한 transaction에 공통 관측만 연결했다. table lock·기존 사건 재사용·event/membership 이력 INSERT의 순서와 native COMMIT을 유지한다. 앞선 `news_assessment` 문서 저장과 후속 job 완료는 별도 transaction이며, PC 과거뉴스 완료의 직접 helper 호출은 이번 계측 범위가 아니다. 공통 access·사건 이력·PC 과거뉴스 관련 로컬 회귀 58건 통과. 전용 검사 ZIP `kiwoom-db-access-news-event-pilot-20260927.zip`(1,275,800바이트, SHA-256 `CE5014D0E1C7BBE22CE1EF75C02BC37FDA91F9DBAB550D4F3C6280C940D43471`)의 전용 PostgreSQL 통합검사 1건이 통과했다(1.219초): 동시 replay·reader 이력·membership 실패 rollback·정리 검증을 확인했다. 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 PC FLASH/WORLD 시황뉴스 기사 제한·로그 후속: 개별 기사 BODY 2초 제한과 준비 단계별 JSONL을 적용한 수집기를 날짜 경계에서 재시작했고, 준비 worker 기본값을 4에서 6으로 조정했다. 100건 페이지 callback의 초기 표본 중앙값은 재개 전 2,867ms(100건), 재개 후 2,063ms(27건)다. 날짜/기사 구성이 다른 초기 표본이므로 장시간 날짜·시간당 처리량과 실패·차단률을 비교해 유지 여부를 판단한다. 네이버 목록 요청 제한은 기존 값으로 유지한다.

2026-09-27 PC FLASH/WORLD 페이지 간 대기시간 실측 후속: 직전 0.7초 실행의 15분 표본은 목록 555페이지(약 37/분), HTTP 오류 0건이었다. 준비 결과 저장은 3,606회·20,578행·저장 시간 합계 약 182초였고 빈 drain 224회의 저장 시간은 약 1초였다. 빈 drain의 SQLite 저장 호출만 건너뛰도록 로컬 수정해 12개 관련 테스트가 통과했다. 수집기를 날짜 경계에서 종료하고 기존 CLI 옵션을 0.5초로 재시작한 첫 10.21분은 목록 448페이지(약 43.9/분), HTTP 403/429·재시도 0건, 완료 날짜 6일이었다. 새 실행의 빈 drain 184회는 SQLite 저장 시간 0ms로 기록됐다. 날짜별 기사 수가 달라 이 수치만으로 장기 완료시간 개선은 확정할 수 없다. 0.5초 설정의 장시간 처리량과 차단률을 더 관찰하고, 오류가 늘면 0.7초로 복귀한다.

2026-09-27 PC FLASH/WORLD 준비 결과 SQLite 연결 재사용: 같은 로컬 임시 DB에서 1행 저장 40회씩 순서를 바꿔 측정한 중앙값은 매번 연결 방식 33~35ms, 연결 재사용 6~13ms였다. `ConcurrentArticlePreparation`의 한 실행 수명 동안 출력 연결만 재사용하고, 저장 호출마다 기존 `executemany`와 COMMIT을 유지했다. 실패 때 ROLLBACK·연결 폐기, 정상 종료 때 연결 close를 검증했고 재시작 후 ready 중복 건너뛰기까지 관련 62개 회귀가 통과했다. 수집기를 날짜 경계에서 같은 0.5초 설정으로 재시작한 6.11분 표본은 실제 저장 3,052회·10,402행, 저장 중앙값 16ms, 저장 시간 합계 약 45.7초, 저장 오류 0건이었다. 직전 0.5초 실행은 저장 5,154회·26,482행, 중앙값 32ms, 합계 약 227.4초였다. 2~5행 저장 구간 중앙값도 31→16ms였다. 새 표본의 준비 완료 기사 10,402건과 drain된 행 10,402건이 일치했고 WAL 파일 크기는 관찰 중 일정했다. 날짜별 기사량과 저장 묶음 크기가 달라 전체 완료시간 개선 폭은 미확정이며 장시간 오류·WAL·처리량 감시가 남는다.

2026-09-28 PC FLASH/WORLD BODY 준비 worker 6→8 운영 비교: 동일한 0.5초 목록 간격과 각각 10분 창에서 6-worker는 준비 18,168건·저장 5,775회·저장 시간 약 93초·용량 제한 대기 약 188초였고, 날짜 경계에서 재시작한 8-worker는 준비 18,224건·저장 3,498회·저장 시간 약 61초·용량 제한 대기 약 113초였다. 대기 중앙값은 328→312ms, p95는 468→453ms였다. 완료 기사 수는 사실상 같아 처리량 증가를 확정하지 않는다. BODY 요청 오류는 84/18,167→118/18,225건으로 늘었지만 모두 원문 본문 판독 `ValueError`였고 목록 403/429·준비 저장 오류는 없었다. 페이지·날짜별 기사 구성이 달라 장시간 오류율과 완료 날짜/시간은 계속 비교한다. 현재 8-worker 실행을 유지하고 Python·PowerShell의 기본값을 8로 맞췄다.

2026-09-28 PC FLASH/WORLD 준비 queue drain 묶음 및 결과 조회 연결 재사용: `ConcurrentArticlePreparation`은 실행 중 열린 prepared SQLite 연결로 `already_ready`를 확인하고, pending이 worker×4에 이르면 완료 결과를 worker 수만큼 모아 저장한다. 쓰기 실패면 SQLite rollback 뒤 pending 결과와 queued key를 보존해 재시도한다. 임시 SQLite의 2,000건 무작업 BODY 시험은 매 요청 별도 결과 DB 연결 방식 3.90~3.97초에서 연결 재사용 방식 0.93~1.21초로 줄었다. 400건 BODY 150ms 모의 시험은 저장 drain 97→48회, 전체 7.64→7.83초로 처리량 차이는 없었다. 관련 뉴스 단위 회귀 52건(1건 skip) 및 구문 검사를 통과했다. 2026-09-28 날짜 경계에서 검색·시황 수집기를 작업 경계로 중지·재개했다. 직전 10분과 재개 뒤 10분의 `market-news-timing.jsonl` 표본에서 준비 건수는 18,885(1,889/분)→15,286(1,529/분), 페이지 요청은 500(50/분)→463(46/분)이었다. drain은 3,679→1,456회로 줄고 평균 저장 행은 5.14→10.50행, SQLite 저장 시간은 70.6초→27.5초(분당 7.06초→2.75초, 행당 약 3.73→1.80ms)였다. 반면 drain의 future wait는 분당 5.57초→11.87초로 늘었다. BODY 시간 중앙값/p95는 141/328ms로 동일했고 준비 queue 중앙값/p95는 328/453ms→297/453ms였다. 동시간 수집량은 줄었지만 요청 페이지 수도 7% 적고 기사 구성이 달라 수집 처리량의 인과적 저하로 단정할 수 없다. 확인된 것은 SQLite 저장 횟수·행당 저장 시간이 줄고 worker 완료 결과를 모으는 대기가 늘었다는 점이다. 검색·시황 프로세스는 재개 후 계속 실행 중이므로 완료 기사/날짜, queue 대기, 실패율을 장기 관찰한다.

2026-09-27 O12 `news.ai_results` 로컬 계측 이관: `CentralAIService.analyze`의 `asyncio.to_thread`가 PostgreSQL 최신 `news_ai` 문서, 불변 AI revision, `news_request_usage`를 기존 한 native transaction에 저장한다. 공통 관측만 연결하고 논리 시도 행수는 세 종류의 합으로 기록한다. 공통 access·중앙 AI 회귀 39건 통과. 전용 검사 ZIP `kiwoom-db-access-news-ai-results-pilot-20260927.zip`(1,274,924바이트, SHA-256 `CA26F4BD610A9E4A1DB5C3359B85FCF9E032798CD55177BB68028653EC14017F`)을 NAS `/tmp`로 전송했고 호스트 해시가 일치한다. 사용자 제공 컨테이너 출력으로 reader·중복 revision 오류 rollback·계측 통합검사 1건이 통과했다(0.397초, cleanup assertion 포함). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.body` 로컬 계측 이관: 본문 revision INSERT·기사 잠금·RULE job 생성의 한 native transaction과 호출자의 별도 BODY job 완료 경계를 유지했다. 기존 성공 지표에 공통 DB call ID를 연결했다. 공통 access·뉴스 회귀 46건 통과. 사용자 제공 컨테이너 출력에서 전용 PostgreSQL reader·RULE job 연결·오류 rollback 통합검사 1건이 통과했다(0.329초, 정리 assertion 통과). 운영 적용과 실제 writer 오버헤드 가드는 미완료다.

2026-09-27 O12 `news.external_claim` 로컬 계측 이관: 과거뉴스 claim의 `SKIP LOCKED`·상태 갱신·native context 종료와 기존 독립 wait sampler를 유지한 채 공통 관측을 추가했다. 로컬 공통 access·PC 과거뉴스 회귀 43건 통과. 사용자 제공 컨테이너 결과로 병렬 claim 중복 방지·기사 reader·COMMIT/계측 검사 1건이 통과했다(1.005초, 테스트 행 정리 후 잔여 0건). 실제 writer overhead 가드와 운영 적용도 미완료다.

2026-09-27 공통 DB `news.job_retry` 로컬 이관: `CentralNewsJobRunner.run_once`의 예외 경로가 `asyncio.to_thread(PostgresQueryStore.retry_news_job)`를 호출한다. `attempts` 조회→기존 `PENDING/FAILED` 판정→행 갱신은 한 native connection context에 두고 공통 관측만 적용했다. 로컬 공통 access·뉴스 worker 회귀 37건 통과. 사용자 제공 컨테이너 출력에서 전용 PostgreSQL attempts=2/3 경계·최종 행·계측 통합검사 1건이 통과했다(1.598초). 테스트 tearDown은 임시 job 2건 삭제 후 잔여 0건을 확인한다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 과거뉴스 원문 병목 후속: 같은 호스트의 연속 timeout에는 두 번째 요청을 허용하고, 3건 연속 timeout 이후 같은 호스트의 나머지 원문은 요청 없이 실패 사유를 남긴다. 원문 URL당 접속·읽기는 2초로 제한하고 검색 요청·원문 요청·cooldown·로컬 저장/전처리·오류의 누적 JSONL을 새 자식에서 확인했다. 원문 실패 뒤의 보관 URL은 본 수집 중 요청하지 않으며 `archive_deferred` 건수와 원장 URL만 남긴다. 2초 적용 전 초기 원문 219건은 2초 초과가 없었지만 문제 호스트가 충분히 포함된 장기 창은 아직 없다. 이후 `news-timing-*.jsonl`과 `article_fetch.expanded_hosts`·`unreachable_hosts`·`skipped_articles`를 이전 4시간 창과 같은 기준으로 비교하고, 실패/차단·보관 URL 보류량 및 전체 처리량을 확인한다. 빠른 호스트나 일시 오류가 과도하게 제외되면 연속 timeout 기준을 재검토한다. 전체 수집 속도 개선은 미확정이다.

2026-09-27 O12 `news.job_claim` 로컬 계측 이관: 기존 lease 복구·`SKIP LOCKED`·상태 갱신의 한 transaction을 그대로 관측 wrapper로 통과시키고 성공 시 기존 `news_job_claim` 표본에 `db_call_id`를 연결했다. 공통 `rows_attempted`는 복구 UPDATE의 영향 행수가 미상이므로 `None`으로 둔다. 공통 DB·뉴스 회귀 35건 통과. 전용 DB 검사 ZIP `kiwoom-db-access-news-claim-pilot-20260927.zip`(SHA-256 `889DB1635BB9CA90C29C2493F810BA97B5E34550959B08E7C0A851F9D8E61A87`)을 NAS `/tmp`에 전송했고 해시가 일치한다. 사용자 제공 전용 PostgreSQL 실행 결과로 stale lease 복구·병렬 중복 claim 방지·metric correlation 검사 1건이 통과했다(0.278초, 테스트 행 정리 후 잔여 0건). 운영 오버헤드 가드와 운영 적용은 미완료다.

2026-09-27 O12 document 일곱 번째 좁은 kind 로컬 이관: `stock_fundamentals`의 최신 문서 UPSERT만 공통 계측에 연결했다. `ka10001` 뒤의 날짜별 snapshot은 독립 transaction으로 유지하고 API/TOP20/PC reader freshness 계약을 보존한다. 로컬 회귀 88건 통과. 검사 ZIP `kiwoom-db-access-stock-fundamentals-pilot-20260927.zip`(SHA-256 `25E4C2DEBF81F0460AC7F36772B005596FF1FF91B2C6A0F6776BA015DD3F8558`)을 NAS에 전송해 해시를 확인했다. 사용자 제공 컨테이너 실행 결과로 전용 PostgreSQL reader/replay/계측/cleanup 검사 1건이 통과했다(3.150초). 운영 오버헤드 가드와 운영 적용은 미완료다.

2026-09-27 O12 document 여섯 번째 좁은 kind 로컬 이관: `stock_nxt_eligibility`의 문서 UPSERT만 공통 관측에 연결했다. `ka10100` 응답의 날짜별 `nxt_eligibility` snapshot은 후속 독립 transaction으로 남기고, 최신 문서의 API/TOP20/PC reader 계약은 유지한다. 로컬 회귀 140건 통과. 검사 ZIP `kiwoom-db-access-nxt-eligibility-pilot-20260927.zip`(SHA-256 `2D16C3788E644A87AC2E28D37DA707768408B614C6A297D27B47BA118DE0D5E7`)의 NAS 호스트 해시가 일치한다. 전용 PostgreSQL replay/reader/call correlation/cleanup 검사는 사용자 제공 실행 결과로 1건 통과했다(0.384초). 실제 writer overhead 가드와 운영 적용은 미완료다.

2026-09-27 과거뉴스 archive 봉인 선행조건: 읽기 전용 준비도 감사에서 현재 building 파일의 SQLite 무결성은 `ok`이나 검색 pending 327,815건, assessment_done 2,446건, source_failed 37건, RULE 검증·사건·검색/시황 projection·coverage·dataset ID 누락을 확인했다. 37건은 본문과 목록 요약 확보 실패이므로 각각의 원본 확보 가능성과 실패 보존 범위를 명시적으로 검토해야 한다. 수집 종료 뒤 검색/시황을 각각 새 일관 스냅샷으로 고정하고 같은 generation의 ARTICLE/BODY/assessment/RULE/사건/projection을 완성·대조한 다음에만 봉인 조건을 재평가한다. 현재 building 파일을 NAS에 게시하거나 기존 PostgreSQL 뉴스를 삭제하지 않는다.

2026-09-27 O12 document 첫 kind 검증 완료: `document:news_sync`만 공통 계층으로 옮겼고 다른 collection은 기존 raw connection 경로를 유지한다. 기존 뉴스 기사 저장 transaction과 marker 저장 transaction은 합치지 않았다. 로컬 pilot·뉴스·DB·진단 회귀 99건, 그리고 전용 PostgreSQL의 동일 문서 replay·행값·COMMIT/SQL 횟수·기존 지표/call ID 연결·측정 행 정리 통합검사 1건(0.595초)이 통과했다. theme/news_article 이력 helper와 다른 document kind는 미이관이다. 실제 writer overhead 가드와 운영 배포도 여전히 미완료다.

2026-09-27 O12 document 두 번째 좁은 kind 로컬 이관: `krx_trading_day_observations`는 0s market-operation event 한 곳에서 `asyncio.to_thread`로 저장되고 `AutonomousTop20Service._is_observed_krx_trading_day`가 같은 `load_documents` API로 읽는다. theme/news 이력 helper는 호출하지 않는다. 공통 observer는 이 kind에만 추가했고 기존 SQL·context-manager transaction·호출 스레드 경계는 유지했다. DB/TOP20/URL 관련 회귀 68건이 프로젝트 `.venv`에서 통과했다. 전용 PostgreSQL replay/reader/call correlation/cleanup 통합검사는 ZIP(`kiwoom-db-access-krx-trading-day-pilot-20260927-v2.zip`, SHA-256 `E4C7186692BA3C1CD2FE0BA1C0C4FE0B4974C9AF24246C31CA5B6E0CFBDC2519`)으로 NAS에서 실행해 1건 통과했다(0.308초).

2026-09-27 O12 document 세 번째 좁은 kind 검증 완료: `external_market_roll_state`는 `YahooDelayedMarketCollector._save_roll_state`가 `asyncio.to_thread`를 통해 쓰고 다음 수집의 `_load_roll_state`가 읽는다. 기존 SQL·context-manager transaction·월물 선택 계산은 유지했다. 관련 단위검사 86건, runtime discovery 10건, 전용 PostgreSQL replay/reader/call correlation/cleanup 통합검사 1건(0.469초)이 통과했다. 검사 ZIP `kiwoom-db-access-external-roll-pilot-20260927.zip`의 NAS 호스트 SHA-256도 일치했다. `theme_metadata`/`news_article` history helper를 비롯한 다른 collection, 실제 writer overhead 가드, 운영 적용은 미완료다.

2026-09-27 O12 document 네 번째 좁은 kind 검증 완료: `stock_catalog`는 `QuerySetNewsCollector._load_catalog`의 빈 cache fallback이 UPSERT하고 `QuerySetNewsCollector` 및 `MarketFeedNewsCollector`가 `load_documents`로 읽는다. `theme_metadata`/`news_article` history helper는 실행하지 않는다. TOP20 쪽의 별도 `replace_documents`는 이번 이관에서 다루지 않았다. DB·뉴스 회귀 51건, URL 실행기/공통 관측 회귀 28건, 전용 PostgreSQL replay·`MarketFeedNewsCollector._catalog` reader·call correlation·cleanup 검사 1건(1.251초)이 통과했다. 검사 ZIP `kiwoom-db-access-stock-catalog-pilot-20260927.zip`의 NAS 호스트 SHA-256은 일치했다. 운영 writer overhead 가드와 운영 적용은 미완료다.

2026-09-27 O12 document 다섯 번째 좁은 kind 검증 완료: `minute_trade_value_comparisons`는 `MarketDataIngestor._record_sor_trade_value_comparisons`가 canonical 분봉 저장 다음, 별도 best-effort transaction으로 기록한다. `/api/v1/market/trade-value-comparisons`가 읽어 complete/partial summary를 계산한다. 비교 계산 실패는 원본 분봉 저장을 되돌리지 않는 현재 격리 규칙을 유지했고, 이번엔 `upsert_documents` 공통 관측만 연결했다. DB·ingest·API·URL 관련 회귀 95건 통과. 전용 PostgreSQL 검사 ZIP `kiwoom-db-access-trade-value-comparison-pilot-20260927.zip`(1,272,266바이트, SHA-256 `202250885819D326455BBE9A30DAD25D0A9B64CBA55D6B1ED0FA31B19FBC2BD6`)의 로컬 검사와 NAS 호스트 해시 대조가 끝났다. 사용자 제공 컨테이너 출력에서 전용 DB replay/요약 reader/call correlation 통합검사 1건이 통과했다(2.343초). tearDown은 측정 키 삭제 후 잔여 행 0건을 검사한다. 운영 writer overhead 가드와 운영 적용은 미완료다.

2026-09-27 전용 PostgreSQL URL 준비 후속: 실행기가 명시 `KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL` 단독 실행을 지원하도록 보완했다. 대상 DB명(`kiwoom_monitor_diagnostic_test`)을 검사하고 해당 연결에 read-only preflight하며, 테스트가 자동으로 schema를 만들지 않도록 기존 schema 필수 검사를 활성화한다. 변수가 없으면 기존 NAS `KIWOOM_SERVER_DATABASE_URL` 파생 흐름을 유지한다. URL guard·공통 DB 계측 단위검사 28건 통과. 현재 Codex 세션에 전용 URL이 전달되지 않아 실 PostgreSQL 검사는 남아 있다.

2026-09-27 O12 다음 document 단계 진입 전 확인: 두 pilot의 명시 COMMIT·native context 계약 뒤, 설치된 Psycopg `Connection.__exit__`를 사용한 ROLLBACK 실패 주입 단위검사도 통과했다. `PostgresQueryStore.upsert_documents`는 여러 collector·broker 경로가 호출하는 공통 메서드이고 `theme_metadata`/`news_article` 입력은 같은 transaction에서 별도 이력 helper를 실행한다. 메서드 전체를 감싸면 단순 collection뿐 아니라 이 두 이력 writer에도 새 관측 경계가 적용되며, credential·뉴스·시장 데이터의 기존 reader와도 결과 호환성을 확인해야 한다. 따라서 다음은 collection별 호출자→helper→읽기·revision 계약 및 기존 성공 지표를 먼저 좁히고, 그 범위에 한정해 전용 DB 결과·COMMIT 수·오류 경계를 비교한다. 현재 1차 pilot의 운영 배포·실제 도메인 writer overhead 판정은 여전히 미완료다.

2026-09-27 O12 공통 DB 두 writer pilot: `rest.query_cache`의 명시 COMMIT과 `news.job_finish`의 Psycopg native context를 각각 기존 transaction 경계로 계측한다. 사용자 제공 NAS 전용 DB 출력에서 native context 계약 2건(0.221초) 및 실제 `finish_news_job` UPDATE·행 결과·기존 지표/call ID 연결·측정 행 정리 1건(1.144초)이 통과했다. 로컬 pilot 단위검사 16건, 뉴스·DB·진단 인접 회귀 71건도 통과했다. 운영 NAS에는 새 계층을 아직 배포하지 않았다. 다른 writer의 raw 직접 접근은 공통 layer를 거치지 않아 전체 PostgreSQL writer의 `UNREGISTERED=0`을 증명할 수 없다. 실제 도메인 writer의 동일 부하 OFF/ON overhead 가드, 나머지 writer의 점진 이관, 운영 적용 후 회귀 확인은 미완료다. WAL wait 원인을 재측정만으로 단정하거나 transaction/commit 정책을 변경하지 않는다.

2026-09-27 O12 cache COMMIT wait 표본과 기존 운영 증거 대조: 전용 DB cache SQL 등가 경로 총 12회에서 같은 call ID·backend PID·COMMIT 시간창의 `pg_stat_activity` 표본을 얻었다. 6회에서 `IO:WalSync` 85표본과 `LWLock:WALWrite` 13표본이 있었고, 1.05초·1.18초 COMMIT 두 건은 각각 36·41표본이 `WalSync`였다. 측정 키 정리·capture 검증 통과, probe 오류·미완료 없음. 그러나 운영 DB의 2026-09-26 분봉 저장 측정 `20260926T111142Z-fb233263`에서 이미 동일 호출 backend의 2,810ms COMMIT에 `WALWrite` 16→`WalSync` 88표본, 2,380ms COMMIT에 `WalSync` 86표본과 DB 전체 WAL 15.60MB·동시 장치 부하가 기록됐다([NAS 진단 기록](NAS_RUNTIME_DIAGNOSTICS.md)). 새 표본은 작은 cache SQL transaction도 같은 WAL 대기를 겪는다는 범위 확장이지 새 대기 메커니즘 발견이 아니다. WAL wait의 전용 DB 반복 측정은 중단하고, 후속은 기존 운영 자료로 **어떤 writer·작업이 WAL/장치 부하를 만드는지**를 분리한다. `fsync`·durability·transaction 경계는 바꾸지 않는다. 실제 `save_query` 메서드 및 운영 부하에 대한 공통 layer 성능 가드는 여전히 미판정이다.

2026-09-27 O12 cache writer 후속 표본: 전용 PostgreSQL의 SQL 등가 경로에서 호출별 새 연결·UPSERT·만료 DELETE·COMMIT·close를 raw/OFF/ON 각 18회 교차 측정했다. COMMIT p50/p95(ms)는 103/1,240, 32/1,851, 85/1,419로 모든 모드에 긴 tail이 있다. ON close p50 0.671ms(raw 0.104ms, OFF 0.101ms)는 capture 기록 비용 후보지만 전체 지연의 원인은 아니다. 각 블록의 측정 키 정리와 ON call/SQL/commit 기록은 통과했다. 낮은 표본 수와 NAS 공유 부하, 실제 도메인 `save_query` 미호출 때문에 5% 처리량/추가 p95 가드는 **미판정**이다. 후속 COMMIT backend wait 표본은 위 항목에서 확보했지만 WAL·장치·동시 writer 인과는 남았다. 그 전에 래퍼 때문에 COMMIT이 느리다고 수정하지 않는다. 운영 배포는 하지 않는다.

2026-09-27 O12 읽기 전용 공통 DB 계측 표본: 전용 PostgreSQL에서 raw/OFF/ON 각 3,000회 `SELECT 1` 교차 측정을 완료했다. 합산 p95는 0.120/0.138/0.127ms, 1,000회 블록 시간 중앙값은 108.748/118.879/115.037ms였다. ON capture의 세 블록은 SQL·commit·drop 검사를 통과했다. ON 블록 중앙값은 raw보다 5.78% 길고 블록 변동 및 미측정 CPU·동시 부하 때문에 5% 처리량 기준은 **통과 미판정**이다. 이 결과는 재사용 읽기 연결의 SQL 경계에 한정된다. 실제 `save_query` writer의 연결·cleanup·COMMIT을 포함한 같은 조건 비교, 느린 COMMIT의 wait/WAL/lock 근거, 관련 회귀 및 임시 컨테이너 파일 정리가 남았다. 운영 배포는 하지 않는다.

2026-09-27 O12 공통 DB 관측 pilot 후속 검증: `save_query` 한 writer의 로컬 계층/API와 격리 단위검사는 구현했다. 로컬 프로젝트 `.venv`에는 `psycopg[binary]` 3.3.6을 설치했고 import(`binary`)·`pip check` 및 pilot 단위검사 9건이 통과했다. NAS의 기존 전용 `kiwoom_monitor_diagnostic_test`(PostgreSQL 17.11)에 현재 소스·검사를 임시 ZIP으로 전달해 실제 Psycopg cache replay, 실패 시 close 폐기, 서로 다른 connection의 commit/실패 격리 통합검사 3건이 통과했다. 보완 재검사 3건도 통과했으며 같은 `call_id`의 기존/공통 COMMIT 계측 2쌍은 2,121/2,121.301ms와 2,012/2,012.055ms였다. 이는 계측 일치 확인이지 2초 지연 원인 확인이 아니다. 두 실행 후 테스트 전용 key 잔여 행은 0건이었다. 이 경로는 사용자의 `sudo docker cp/exec`로 실행했으며 NAS 소스·이미지는 배포하지 않았다. capture OFF/ON 실제 PostgreSQL 오버헤드와 관련 회귀가 남았으므로 NAS에 pilot을 배포하지 않는다. native connection context·savepoint·직접 maintenance 연결·실시간/계좌·주문 writer는 미이관. 두 ZIP의 로컬·NAS 호스트 복사본은 제거했고 컨테이너 `/tmp`의 ZIP·추출 디렉터리는 제거가 남았다. 앞의 설계 검토 단계 기록은 구현 이전 상태의 이력이다.

2026-09-27 O12 공통 DB 접근·관측 후속: [설계 검토](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)의 1차 범위는 기존 연결·commit/rollback/close·동시성·durability를 보존하는 공통 관측 경계와 `save_query` writer 하나의 이관이다. 기존 metrics/registry를 확장하고 새 pool/queue/독립 metrics 저장소는 만들지 않는다. 실제 driver 버전 확인, 전용 PG의 명시 종료·오류/응답 유실·동시성 검증, 같은 call의 기존/new 계측 비교와 OFF/ON overhead 측정이 남았다. UNREGISTERED는 runtime 미계측이며 raw 우회까지 0이라고 선언하지 않는다. 기존 dataset 비동기 COMMIT 정책, sequence/commit 순서 역전, 보호 경로 pause/drain, 과거뉴스 archive는 별도 항목으로 유지한다. 이번은 설계·정적 조사만 완료했으며 구현/배포 완료가 아니다.

2026-09-27 PC 완성 과거뉴스 archive 다음 단계: [설계의 구현 계약 A~F 및 B 사건 순서 결정](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md)을 따른다. A의 seed 추출·기존 ID 물질화와 9/25 검색 준비본 330,261 ready/37 failed 입력 원장화를 진행했다. fulltext 281,502건 중 133,357건은 원본 hash·획득시각이 일치하고 148,145건은 대응 BODY snapshot 부재다. B의 2,446 기사/BODY·평가 문서/RULE 입력은 staging이며, PC RULE 오프라인 검증기와 사건 최종화 transaction·결정적 작업순서·모호한 그룹 충돌 후보 원장·재시작/단일 실행 보호를 로컬 구현했다. 검색기사 목록 projection 빌더도 로컬 구현해 `historical` seed와 검증된 prepared만 목록에 넣고 PC에서 본문·판정·사건 관계를 고정한다. 기존 발행시각 해석, seed 소유권, 실패 rollback을 포함한 인접 단위 회귀 17건이 통과했다. 복사본에서 2,446 RULE 계산은 모두 일치했으나 원본 미완성 archive에 검증 원장이나 사건·projection을 기록하지 않았다. 전체 사건 후보 37,816건은 seed 정확 재사용 2,448건과 seed 그룹에 겹치지 않는 새 후보 35,368건으로 대조했다. 다음은 나머지 327,815건 ARTICLE/BODY/assessment 처리와 같은 generation의 RULE 검증·실자료 사건 및 검색 projection 실행, 시황 projection·전체 봉인, 계속 수집 중인 검색·시황의 최종 입력 고정, C~D reader/소비자 검증이다. 기본은 수집 종료 후 전체 게시이며 중간 게시 세대의 ID는 최종 세대에 보존한다. 그 전에는 NAS 게시나 기존 뉴스 삭제를 하지 않는다.

2026-09-27 과거뉴스 이관 후속 미완료: 실제 원본/seed 범위 감사, 기존 ID/이력/사건 정합성, 연구·일지 소비 경로와 NAS 자동 재계산 금지 검증이 남아 있다. NAS 이관 원장 기록은 검색 23,546건·시황 103건이며 운영 DB 실제 상태와 별도로 대조해야 한다. archive가 기존 이관분을 대체한다고 검증한 뒤 PK/checksum 및 현재 참조로 이관 전용 행만 별도 정리한다. 현재/과거를 하나의 목록으로 합치는 기능은 첫 구현에서 제외하며 기존 PG 페이지 계약을 유지한다. 앞선 25건 batching 및 PostgreSQL 통합검사 통과를 archive 기능의 완료나 검증으로 간주하지 않는다.

2026-09-27 단발성 준비 과거뉴스 수입: 검색기사 25건 묶음의 기존 문서/revision writer 사용과 단계별 시간 로그를 구현했고, 로컬 회귀 6건과 NAS 전용 `kiwoom_monitor_diagnostic_test` 통합 3건이 통과했다. 동일문서 replay, 변경 revision chain, 잘못된 문서의 batch rollback/건별 격리, 준비된 BODY/RULE 완료 및 재실행 중복 방지를 확인했다. 기존 NAS 소스·실행본을 백업한 뒤 일회성 스크립트 두 개를 실행 경로에 SHA-256 검증 후 반영했지만, 전체 가져오기는 재개하지 않았다. 운영 25건 소량 표본은 실패·보류 0건, 기사 저장 4,055ms에 비해 기존 NAS BODY/RULE job 완료 경로가 32,235ms였다. PC가 BODY/RULE 결과를 계산했어도 NAS는 job 소유권·해시 검증, 본문 revision, 평가 문서, 사건 연결과 COMMIT을 처리한다. 따라서 이 변경은 전체 대량 적재 목표의 해결이 아니며, 사용자 의도에 맞는 일회성 벌크 수입에서 어떤 결과를 PC가 고정하고 어떤 live revision/event 정합성을 NAS가 최소 검증해야 하는지 설계를 확정해야 한다. 그 전에는 330,261건 전량 적재를 재개하거나 완료 시각을 확정하지 않는다. 일반 실시간 뉴스는 이 단발성 적재 변경 범위 밖이다.

2026-09-26 O12 분봉 canonical/metadata batch UPSERT: `ka10080 replace_minute_bars`의 PostgreSQL·SQLite 경로를 bounded multi-row UPSERT로 바꾼 로컬 구현이 있다. 900개 고유 봉은 PostgreSQL canonical 1 SQL + metadata 1 SQL, SQLite는 각각 12 SQL(80행 batch)이며 transaction은 저장 호출당 1회다. 중복 canonical 키의 앞선 관측은 기존 순서대로 적용해 값이 바뀌었다가 복귀할 때의 `updated_at` 의미를 보존한다. 전용 PostgreSQL 테스트 DB 통합 14건과 로컬 관련 회귀 86건 통과, 1002개 관측·1001개 고유 봉에서 canonical batch 2개·중복 선행 SQL 1개, metadata batch 2개와 revision chain을 확인했다. NAS 운영 소스 복사·재빌드 및 동일 부하 전후 성능 측정은 남아 있다. 0B live writer는 별도 경로다.

2026-09-26 O12 UPSERT/I/O 진단 로컬 v5: `measure`가 `pg_stat_io`와 `pg_stat_checkpointer`의 cluster-wide 누적 counter delta를 수집한다. `pg_stat_io`의 불가능한 I/O NULL, stats reset, missing group, 감소 counter는 0으로 바꾸지 않는다. `pg_stat_io` 시간은 작업 당시 `track_io_timing`에 의존하지만 checkpointer 단계 시간은 그렇지 않다. 해당 view의 실제 PG17 SELECT와 실패한 선택적 조회 뒤 연결 재사용을 전용 DB에서 확인했다. 개별 SQL I/O attribution은 아니며 master가 허용한 진단에서만 실행된다. NAS 운영 재빌드·전후 측정은 남아 있다.

2026-09-26 O12 UPSERT 대기 계측 독립 검토: v1의 분봉 `executemany` 문장별이라는 설명, probe 종료 대기의 `bars_ms` 포함 가능성, 오래된 장치 표본의 잘못된 시작시각을 수정한 로컬 v2가 있다. PostgreSQL 실제 권한 거부·실패 롤백 경로와 NAS 실측은 아직 검증 전이다. v2는 NAS 공유에 복사하거나 서버를 재빌드하지 않았다. NAS 공유의 v1 파일로 재빌드하면 이번 검토 수정은 포함되지 않는다.

2026-09-26 O12 `bars_ms` UPSERT 대기 계측 v1: 분봉 `executemany` 전체 호출과 일봉 multi-row UPSERT batch 호출에 PostgreSQL backend wait event·blocking PID 및 정렬된 host device delta 진단을 추가했다. capture OFF에서는 추가 probe 없음. 관련 unittest 57건·구문검사 통과, NAS 누적 동기화/백업 및 SHA-256 확인 완료. v1 marker는 `2026.09.26-bar-upsert-wait-correlation-v1`; NAS 재빌드·health marker와 동일 운영 측정은 하지 않았다. 현재 로컬 v2 수정은 위 항목을 따른다. host device counters는 같은 구간의 타 프로세스 I/O를 포함하며 단독 귀속값이 아니다.

2026-09-26 O12 COMMIT 재측정: 첫 60초 측정 `20260926T094808Z-3b1cdb6c`는 `ka10080` 28회·24,922행, revision INSERT 0회, COMMIT p50/p95/max 127/1,259/1,375ms였다. 누적 재빌드 후 health marker `2026.09.26-daily-minute-batch-storage-v1`을 확인하고 반복한 `20260926T100618Z-71c63050`는 22회·19,522행, revision INSERT 0회, COMMIT 199.5/2,093/3,389ms, 전체 저장 p50/p95/max 1,152/2,476/3,809ms였다. 두 구간 모두 revision INSERT 없는 재조회에서 COMMIT tail이 재현됐고, 재빌드 후에도 지연은 해소되지 않았다. 요청량과 동시 부하가 달라 악화 인과는 주장하지 않는다. 재측정 구간의 WAL sync wait 3·WALWrite 16, `track_wal_io_timing=off`, dm-4 write await 237.9ms·queue 89.23·busy 60.04%, news active query 65표본은 전체 구간의 동시 지표이며 개별 commit에 귀속할 수 없다. 0B flush 0회·실시간 `WAITING_MARKET`이라 0B 저장 경로는 미평가다. 진단 master는 측정 후 OFF였다. COMMIT tail은 확인됐지만 정확한 원인 writer와 per-COMMIT I/O는 아직 미확정이다. 후속 로컬 계측은 capture 중에만 트랜잭션 범위 `track_wal_io_timing`, backend PID별 25ms wait-event 표본, call window에 맞춘 250ms host device delta를 추가했다. 관련 소스·문서를 NAS 공유에 해시 일치로 복사했고 백업했다. marker `2026.09.26-commit-wait-correlation-v1` 재빌드·운영 확인이 남았다. 장치 delta는 host-wide이며 동시 프로세스 I/O를 포함한다.

2026-09-26 O12 저장 후속: `ka10081` canonical과 metadata 행별 UPSERT는 PostgreSQL 1,000행·SQLite 80행 제한 multi-row UPSERT로 바꿨다. PC SQLite 분봉은 revision latest 조회를 80키씩, revision INSERT를 40행씩 batch하고 `revision_of` 체인·replay dedup·transaction rollback을 보존한다. 저장 관련 SQLite 회귀 63건 통과. PostgreSQL 전용 일봉 통합검사와 NAS 운영 성능 측정은 남았다. SQLite 분봉 변경은 로컬 소스이며 NAS에 배포하지 않았다.

2026-09-26 O12 분봉 revision paired 측정 `20260926T072545Z-588bd4f0`: 사용자가 `2026.09.26-minute-revision-call-samples-v1`로 60초 측정했고 진단 master는 종료 후 OFF였다. `ka10080` 저장 16회·14,122행, revision INSERT 584행. 같은 호출에서 584 revision INSERT execute 4,660ms / revision 전체 4,987ms / commit 32ms가 확인돼 revision 삽입은 실재하는 비용 후보다. 별도 호출은 revision INSERT 0인데 commit 14,677ms, 또 다른 호출은 bar write 4,793ms라 revision batch만으로 전체 병목을 해결할 수 없다. 30.0MB WAL, `dm-4` busy 80.34%·write await 990.95ms·queue 351.92, 뉴스 claim 113회, 외부시장 6,539행도 겹쳤다. 이는 shared-storage 부하 맥락이지 특정 writer 인과 귀속은 아니다. 로컬 `2026.09.26-minute-revision-batch-insert-v1` 검토에서 VALUES 처리 순서 의존을 제거하려고 linked sequence 값을 batch마다 발급해 `accepted_sequence`에 입력 순서대로 명시했다. unit 47건과 전용 `kiwoom_monitor_diagnostic_test` PostgreSQL 통합검사 7건이 통과했다. 최초 검사 2건의 실패는 테스트 subject 조회 조건 오류였고 이를 `code:market`으로 수정한 뒤 통과했다. chain/sequence/replay/rollback/concurrency 저장 경계는 확인했다. `20260926T082101Z-70672d4f`는 60초 동안 저장 25회·22,500행이었지만 revision INSERT 0건이라 batch 비용 전후 비교가 되지 않았다. 무변경 경로의 total p50/p95/max 834/2,534/2,684ms, COMMIT 378/2,144/2,294ms였다. 뒤이어 `benchmark_minute_revision_batch_postgres.py`로 전용 PostgreSQL DB에서 900행 중 584 revision 변경을 각 경로 3회 비교했다. 행별 경로는 lookup 900회·INSERT 584회, batch는 lookup 1회·INSERT SQL 1회(추가 sequence allocation 조회 1회)였고 revision 처리 중앙값은 399.048ms에서 191.358ms로 52% 줄었다. COMMIT 포함 전체 중앙값은 2,042.357ms에서 812.893ms였으나 COMMIT 분산이 커 그 차이를 batch만의 효과로 귀속하지 않는다. 이는 합성·전용 DB 결과이며 실제 운영 부하에서의 개선은 아직 미검증이다. 별개로 기존 `load_observation_revisions_after(accepted_sequence)` 소비자는 서로 다른 트랜잭션의 sequence 발급 순서와 commit 순서가 뒤집히면 늦게 commit한 낮은 번호의 행을 건너뛸 수 있다. 이는 이번 변경 전에도 존재한 증분 cursor 계약 문제이며, batch 사전 발급은 노출 시간을 늘릴 수 있으므로 전용 DB에서 두 트랜잭션 interleaving을 재현해 별도 설계 판단이 필요하다.

2026-09-26 O12 진단 측정 유효성 보완 로컬 구현: 외부시장 collector의 실제 활성 task·poll 주기·실행 횟수·최근 결과를 API에 추가했고, A/B/A가 비활성 또는 poll 주기보다 짧은 구간을 거부하며 phase별 실행량을 기록한다. NAS 배포/실행 확인과 300초 주기를 초과하는 A/B/A는 남아 있다. 이전 `20260926T031919Z-14cdead1` 시험은 당시 상태 API가 운영 활성 설정을 반영하지 않아 실행 여부와 부하 효과를 판정할 수 없다.

2026-09-26 O12 다음 구현 범위: NAS `/health.server_build`에서 `2026.09.26-diagnostic-session-control-v1` 배포를 확인했다. 그 뒤 로컬에 추가한 DB probe 오류 처리·회귀 검사는 아직 NAS 동기화/재빌드 전이다. 개별 작업의 실제 drain/PAUSED ACK, 뉴스 stage별 gate, 보호 수신·주문 admission, 원장 없는 writer의 durable 인계, 독립 PostgreSQL 강제 종료 검증도 남았다. `effective`는 실제 중지 확인값이 아니며 A/B/A는 작업량 변화와 in-flight 종료를 별도로 확인해야 한다. Linux 제어 lock의 동시성 검증도 미완료다.

2026-09-26 O12 최신 설계 검토: [진단 스위치 설계](DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md)를 후속 구현 기준으로 정리했다. master 삭제 결함은 로컬 v2에서 수정·회귀 68건 통과했다. 이어 로컬에 session ID·revision·master TTL 연동·측정 중 세션 만료 취소를 적용했으며 이 추가 코드는 현재 NAS v1 배포본에 포함되지 않았다. 다음 순서는 해당 로컬 변경의 NAS 반영·Linux lock 검증 → 실제 PAUSED ACK → 뉴스 stage 및 원천별 gate → 보호 수신·주문 admission → durable 인계와 atomic writer 제어 → 독립 PostgreSQL/운영 OFF→ON 검증이다. 기존 RAM 큐만으로 DB 저장을 끄면 안 되며, 일반 `document.collection` 전체를 무조건 끄는 방식도 사용하지 않는다. 보호 경로 구현과 운영 OFF→ON 검증은 미완료다. 아래 과거 시점의 배포 문장은 당시 기록으로 읽는다.

2026-09-26 O12 후속 감사: 읽기 전용 AST 원장으로 Kiwoom API ID 문자열 32종, broker 선언 20종, 리터럴 호출 10종/29곳, 동적 요청 19곳, 중앙 실시간 등록 type 9종, PostgreSQL 직접 SQL 쓰기 메서드 31개를 구분했다. 이는 **활성 API 32개/전체 writer 31개**라는 뜻이 아니다. `ka10016`처럼 선언·handler만 남은 경로와 계좌/주문 동적 호출을 최종 대조해야 한다. 격리 SQLite와 실제 collector를 묶은 동시성·재시도·rollback·관측 이력 검사 7건은 통과했다. 운영 PostgreSQL의 독립 테스트 DB 동시 충돌/강제 종료는 미검증이다. 계측 writer 12개는 v3가 NAS에서 실행 중이며 전수 writer 수, 응답당 commit 전수, per-writer WAL, wait PID 연결, storage mapping의 진단 결과 자동 첨부, PC/Journal reader 완전 대조, 안전한 개별 writer 제어는 남아 있다. transaction fragmentation·redundant·merge 후보의 확정 수량은 **미확정**이고, 정적 코드로 발견한 독립 commit만 후보로 기록한다.

2026-09-26 O12 진단 도구 확장: PostgreSQL writer 12개만 런타임 계측 registry에 등록했고, 실제 commit 구간이 있는 일부 writer에만 commit percentile을 추가했다. 이는 writer 전수 수가 아니며 최종 API/FID·0B·TOP20·계좌/주문/Journal·PC SQLite 저장경로 감사는 미완료다. 응답당 transaction/commit 수는 registry의 검증 범위와 진단 표본에서 확인할 수 있지만, 모든 source별 횟수는 아직 미확정이다. `REDUNDANT_STORAGE_CANDIDATE` 및 `TRANSACTION_MERGE_CANDIDATE` 확정 수량은 0으로 결론난 것이 아니라 미확정이다. NAS storage bind mount→파일시스템→dm/md→물리 장치 관계는 읽기 전용 호스트 스크립트로 JSON 증거를 생성했다. 캐시 정책과 COMMIT별 실제 I/O 배분은 미측정이고 A/B/A 진단 결과에 mapping을 자동 첨부하는 v4 코드는 NAS에 배포했으며 실제 A/B/A 결과 검증이 남았다. WAL bytes/wait event는 구간 합계라 per-writer attribution도 미구현이다. 상세 추적 범위와 registry는 `KIWOOM_STORAGE_WRITE_AUDIT.md`에 있다. NAS `/health`에서 `2026.09.26-writer-diagnostics-v4` 실행을 확인했다. 진단 registry는 계측 중인 12개 writer만 반환한다.

2026-09-25 Google Drive 백업·엄격 복원 ([설계·구현 경계](GOOGLE_DRIVE_RESTORE_DESIGN.md)): v2의 실제 Drive 게시를 완료했다. 세 설정·테마·뉴스 AI 구성요소의 원격 바이트·크기·SHA-256·형식을 검증했고 구형 v1 별칭과도 동일함을 확인했다. manifest 게시 후 다시 읽어 활성 세대 참조를 검증했다. 11.2 MB 뉴스 AI 파일의 첫 일반 업로드는 시간 초과했고, 1 MiB 재개 업로드와 3회 변경요청 재시도를 추가했다. 첫 시도의 HTTP 500 뒤 검증된 기존 구성요소 세대를 재사용해 manifest만 게시했다. Drive·엄격 복원·설정·테마 회귀 40건 통과. 읽기 전용 목록에서 manifest 1개와 v2 세대 2개를 확인했다. 활성 세대의 세 파일은 모두 참조되고 첫 실패 세대의 설정·테마 두 파일(599,218 B)은 미참조다. 자동 삭제는 하지 않으며 보존 기간·다른 PC 게시 중 충돌 방지 절차는 미결정이다. 실제 Drive v2를 임시 DB에 내려받아 현행 설정 87개·테마 2개·뉴스 AI 8,694건·공통 AI 2,421건의 복원과 DB 무결성을 검증했다. 백업에 남은 구형 설정 키 7개는 현재 복원 대상이 아니다. 실제 Drive 세대를 임시 데이터 폴더에 예약하고 새 프로세스의 시작 전 복원으로 COMMITTED 원장·복원 전 DB 사본·복원 결과를 검증했다. 같은 실제 v2를 사용한 임시 폴더 실패 주입에서는 ROLLED_BACK 원장, 이전 설정·테마·뉴스 상태 복구, 다음 시작의 재적용 방지를 확인했다. 자식 프로세스를 설정·테마 적용 뒤 강제 종료한 실계정 v2 격리 시험에서도 APPLYING 원장과 부분 변경을 확인했고, 다음 시작이 이전 DB를 복구해 ROLLED_BACK으로 끝났다. 운영 사용자 데이터 폴더와 앱 GUI의 복원, 두 PC 동시 편집, 전원 장애 내구성은 미검증이고 설정/테마와 별도 뉴스 AI DB 간 동일 시점 및 편집 병합은 제공하지 않는다. NAS·앱 빌드에는 미반영. 사용자 Windows `pc-1` 프로필에서 DPAPI 암·복호화 왕복을 확인했으며 격리 샌드박스 계정은 DPAPI를 사용할 수 없다.

2026-09-25 B 감사 후 운영 검증: `2026.09.25-audit-b01-b07-v1` 재빌드·배포 뒤 실제 NAS 원본 수급 저장 실패·빈 지수 응답·장중 NXT 변경·subscriber queue 유실을 같은 데이터 경계에서 확인한다. 휴장 오조회는 로컬에서 키움 `0s` 장운영 이벤트를 원장에 보존하고 관측된 거래일만 장후 보완하는 gate로 막았다. 공식 연간 KRX 달력 자동 동기화는 아직 없으며 NAS 새 빌드에서 0s 실제 수신·휴장일 무요청을 확인해야 한다. B05 빈 응답은 이 검증 전까지 완료가 아닌 재시도 대상으로 남는다. B01 두 PC의 동시 편집은 로컬 새 편집의 덮어쓰기 방지는 구현됐지만 의미적 병합 정책은 미정이다. B03 주기·가격 기준 분리는 현재 사용 조건상 제외, B08 일반 REST 저장대기 개선은 현 우선순위에서 제외한다.

2026-09-25 A04~A07·A09 로컬 구현 뒤 남은 검증: NAS 재빌드 후 TOP20 `partial`/`realtime_gap` 운영 계측, 장후 실패 후 같은 날짜 재시도, 순위 요청의 guard 대기와 DB 저장 지연 전후를 같은 조건에서 비교한다. 장후 자료별 성공은 기존 `market_data_coverage*`·수급·지수 coverage에 남고 실패일은 재시도한다. A08은 제외하고 A10 collector lock 변경도 사용자 결정에 따라 보류한다. A11·A12는 개발 단계로 별도 검증한다.

2026-09-25 `ka10016` 후속 결정: 이전 부하 감사에서 줄였던 NAS 자동 목록 조회는 현재 화면의 신고가 가격·거리·강조에 소비되지 않아 전부 제거했다. PC 직접 연결의 초기 목록 조회도 제거했다. 기존 저장 스냅샷은 읽기 호환성을 위해 보존한다. NAS 소스는 `2026.09.25-no-ka10016-v1`로 동기화했고, 사용자 컨테이너 재빌드 뒤 실제 `ka10016` 완료 로그가 0건인지 확인해야 한다.

2026-09-25 첨부 감사 재검증: 신규주 장후 `ka10081` 저장행 재사용은 확정 coverage 확인 없이 가능하지만, 이것만으로 네오사피엔스 9/21 저녁 10,000원 표시가 입증되지는 않는다. 현재 PC `intraday_highs`에는 9/21 07:00 UTC(16:00 KST)에 고가 40,000원이 저장돼 있고 화면 정책은 이 당일고가와 일봉 최고가 중 큰 값을 고른다. 당시 사용 빌드·표시 입력값이 필요하다. TOP20 정상 분 마감은 시장별 값과 함께 저장되고 다음 앱 실행은 저장본을 읽는다. 별도 legacy repair는 현재 PC 8,446행 중 대상 0행이며 `unknown_trade_value_eok>0`인 1,222행의 전부가 시장 미분류라고 볼 수 없다. 따라서 날짜 watermark만으로 과거 보정을 영구 종료하기보다, 시장 카탈로그와 분봉 공백이 새로 채워진 행을 구분해 필요한 행만 다시 계산하는 방식이 적합하다.

같은 첨부의 성능 제안 중 현재 사실: 로컬 1초 가격 캐시 flush는 현재가·시총·당일고가를 각각 commit하고, 당일고가 저장은 매번 35일 이전 행을 삭제한다. 분봉 본문/시장지수와 history/sync도 별도 commit이다. GUI의 날짜 변경 정리와 시작 후 background 정리가 중복된다. 단, 개발 확인 CSV는 MarketCacheWriter가 있으면 이미 worker에서 처리하고 GUI 직접 경로는 fallback이다. NAS `program_flow`는 종목별 `save_dataset_snapshot` 반복이 아니라 `save_dataset_snapshots` 배치다. 새 TOP20 메모리 구독은 로컬에서 DB await 앞으로 이동했으나 현재 NAS 실행 빌드는 이전 `2026.09.24-news-claim-diagnostics-v6`이며, `_index_loop`는 여전히 collector lock 안에서 index DB 저장을 await한다. 2초 query cache의 PostgreSQL 저장, failover 15초 재확인/기본 30초 요청 timeout, 숨긴 ResearchDialog의 campaign 복원 시작은 실제 코드에 있다. 각 항목의 지연 기여는 동일 시각 계측으로 판단한다.

2026-09-26 `ka10081` 느린 캐시 저장 진단: 사용자 로그 `handler_ms=646 cache_ms=2652 total_ms=3298`에서 가장 긴 구간은 응답 저장기 뒤의 PostgreSQL 조회 캐시 저장이다. 기존 `cache_ms`는 JSON 직렬화·매 요청 새 DB 연결·캐시 UPSERT·매 요청 만료행 삭제·commit을 한 숫자로 합쳐 실제 DB 대기 지점을 구분하지 못한다. 로컬 소스에 1초 이상 저장의 각 하위 시간 계측을 추가했고 회귀 2건을 통과했다. 이 원인별 로그는 NAS 재빌드 뒤부터만 생긴다. 당시 PostgreSQL wait event/잠금 표본은 없어 과거 2.652초의 내부 원인은 아직 미확정이며, 계측 배포 후 같은 지연 재현 시 하위 시간과 DB 대기 표본을 맞춰 판단한다.

2026-09-26 재빌드 뒤 `ka10080` handler 지연 조사: 사용자 로그의 Kiwoom 응답은 약 55ms였지만 후속 응답 반영 handler는 1.3~2.2초였다. 이 TR은 PostgreSQL 조회 캐시 대상이 아니므로 `cache_ms=0`; 느린 구간은 `MarketDataIngestor._ingest_minutes`의 데이터 준비, 기존 ACTUAL metadata 조회, 분봉·metadata·관측 이력 기록, SOR 비교 조회/저장 경계다. 기존 `handler_ms`만으로 각 구간의 기여도는 구별할 수 없어 이들 단계와 PostgreSQL 분봉 UPSERT·metadata·revision·commit 시간을 로컬에 계측했다. 새 빌드 `2026.09.26-market-ingest-db-timing-v1` 반영 전이며, 이 계측을 포함한 NAS 실행 로그는 아직 없다. 같은 시각의 9/26 분봉 공백 경고는 KST 00:32(토요일) 요청 날짜 자료 부재로 별도 이슈이며 저장 지연 원인으로 취급하지 않는다.

2026-09-25 사용자 제안 감사: TOP20 repair의 시작 동기 실행과 생성자+타이머 이중 실행은 현재 소스와 다르다. 시작 경로는 `QTimer.singleShot(0)` 1회이며 worker 실행 중 검사와 30초 제한이 있다. 다만 `minute_saved` 및 직접 분봉 flush가 repair를 다시 요청하고, `KOSPI=0 AND KOSDAQ=0`인 완전 미분류 행은 재시도 후보에 남는다. 현재 PC 후보 0행의 전체 표 스캔은 읽기 전용 측정 16ms였으며 시작 지연의 주원인으로 확정하지 않는다. 긴 repair가 종료를 막지 않도록 행별 중단 검사를 추가했고 관련 저장소·worker 회귀 20건이 통과했다. 완료 표식·재시도 입력 변경 감지와 분봉 저장 신호 제거는 함께 설계해야 한다.

Candidate는 창 생성 시 숨김과 무관하게 poll worker를 시작하고 cursor를 0에서 시작하며 저장된 `last_consumed_sequence`를 복원하지 않는다. 다만 단순 cursor 복원은 창의 재시작 후 이전 사건 목록을 비우므로 표시용 역사 조회와 알림용 증분 cursor를 분리해야 한다. 종료는 메인 창의 비동기 polling 단계가 있으나 cache writer drain과 Candidate/Research/Mock의 `stop()`에 동기 wait가 남아 있다. EntrySnapshotWriter는 중단 요청 후에도 백필 항목을 포함한 전체 queue를 비운다. 이 경계는 필수 스냅샷 보존과 백필 재개 원장 설계가 필요하다.

NAS 일봉 기반 장후 확정은 PC의 `DailyHighService`가 중앙 일봉 행을 coverage 없이 재사용하며, 강제 worker 실행도 서비스의 이 저장 우선순위를 바꾸지 않는다. 따라서 날짜 행만으로 최종 확정으로 오인할 가능성이 있다. 다만 2026-09-21 네오사피엔스의 10,000원 표시 원인은 당시 입력 기록이 없어 확정하지 않았다. 중앙 realtime snapshot 조회는 최근 300초 제한이고 PC에는 중앙 당일고가 전용 조회가 없지만, 당일 저장 분봉 조회를 통해 고가를 복원하는 경로는 이미 있다. 이 경로의 장후 완료 시점·coverage와 화면 반영을 재현해 보고 중앙 고가 API 필요 여부를 결정한다.

2026-09-25 TOP20 후속·NAS 부하 감사: PC followup의 세 진입점은 적용된 순위 회차당 한 번만 시작하도록 수정했다. 이미 시작한 worker의 이후 실패는 다음 순위 회차에서 재시도할 수 있다. NAS `ka00198`의 q1~q4는 `ranking` 스냅샷으로 저장되고 앱에서 선택 가능하다. NAS 2026-09-24 로그에서 `ka10016` 완료 호출은 4,238회, 그중 20:00~다음 08:59 KST 2,325회였다. `ka10016` 시장 구분이 KRX(`stex_tp=1`)여서 평일 09:00~15:29 매분·16:00~19:59 실행 중 1회로 변경했으며 실제 운영 감소량은 재빌드 후 확인해야 한다. 2초 캐시 대상(`ka10080/ka10045/ka90008/ka20005`)의 1초 이상 broker persistence 경고 190건 중 38건은 캐시 단계가 1초 이상이었으므로 0.2~2초 query cache만 RAM-only로 전환하고 원본 response handler 저장은 유지했다. 느린 dataset snapshot 345건의 연결 시간 중앙값은 13ms, 1초 이상 연결 10건, snapshot 단계 133건, commit/close 단계 146건이라 connection pool 우선 변경 근거는 부족하다. TOP20의 별도 사건 간 commit 통합도 이번 근거로는 하지 않는다. 로컬 TOP20 시장분류 보정 대상은 현재 0행이고 조회 계획은 전체 표 스캔이나 UI 밖 QThread에서 실행된다.

같은 TOP20 회차의 `top20_membership` snapshot·`top20_daily_entrants` upsert·완료된 `top20_index`는 현재 각각 저장 호출이다. `save_dataset_snapshot()` 내부의 snapshot/metadata/observation은 이미 한 트랜잭션이다. 앞의 세 저장을 하나로 묶을 필요가 있는지는 함께 성공해야 하는 불변 조건과 쓰기 시간 계측을 확인한 뒤 결정하며, 지금은 일괄 commit 변경을 하지 않는다.

2026-09-25 휴장 중 확인: 모의 runner의 STOPPED→재시작→재개 관측 커서와 동일 Decision 재전달 시 단일 broker transport 호출을 오프라인 회귀로 확인했다. 휴장으로 NAS 장중 입력·실제 mock broker 응답, 24시간 지속 운용과 지연 분포는 검증하지 못했다. O11·O12의 운영 확인 범위는 그대로 남는다.

2026-09-25 자동운용 지연: 모의 runner의 poll/DB 조회/관측 처리/분봉 시각 지연 계측을 로컬 소스에 추가했다. NAS 배포 뒤 모의 계좌의 장중 관측으로 지연 분포와 저장·계좌 조회 병목을 측정해야 한다. 계측은 실전 주문 권한이나 성능 합격 판정이 아니다.

2026-09-25 시각 경계 보완: KRX 지연 개장일 다섯 날짜(2021~2025년)는 16:30 마감으로 공통 시간표·D03 어댑터·읽기 전용 감사를 수정했다. 2025-11-13 `347850` CREON 원분봉 379개의 중복 시각은 해소됐지만 NAS 대응 봉이 0건이라 가격 일치는 미검증이다. 월별 D03 파생 입력은 새 ID로 재생성했고 동일 구현 hash의 옛/새 TRAIN 눌림 대조에서 체결 사건·날짜별 손익이 일치했다. 장마감 미체결 주문이 다른 종목의 다음 세션 봉을 건너뛰며 여러 달의 주문을 막던 오류도 수정했다. 네 구조 실행은 최종 구현으로 모두 재검증했고 OOS는 봉인했다. 과거 5분봉의 전략 입력 연결, 종일 봉 품질·후보 대표성·뉴스 이용 가능 시각 검증은 별도 작업이다.

기준: 2026-09-23 · [현재 상태](CURRENT_STATUS.md) · [개발 순서](../FUTURE_DEVELOPMENT_ROADMAP.md)

아래는 문서 이동 뒤에도 유지할 잔여 목록이다. 과거 보고서의 ‘다음 단계’를 전부 새 작업으로 복사하지 않았다. 상태는 **계획 / 미구현 / 운영 미확인 / 재현 필요 / 보류**로 구분한다. 상세 미확인 조건은 [파일별 감사 목록](archive/2026-09-22/document-inventory.json)의 `remaining`에 보존한다.

## 우선 진행할 개발

2026-09-25 22:31 KST 과거뉴스 적재 운영 상태: NAS SSH에서 `run_prepared_historical_news_imports.py` 실행을 시작했다. 검색 기존분 19,191건은 이미 적재돼 모두 skip, FLASH/WORLD 103건은 실패 없이 완료, PC 검색 snapshot은 330,261 ready 중 첫 100건까지 실패·deferred 0으로 진행 중이다. 5초 진단에서는 차단 쿼리 없이 PostgreSQL `COMMIT`의 WAL 동기화 대기가 보였다. 다음 처리 묶음이 찍히는 시각으로 실제 속도를 확인하고 완료까지 모니터링한다. 스냅샷 이전에 누락된 37건은 준비 DB에서 `failed` 상태라 적재 대상에 들어가지 않았다. 이 과거자료 수집기는 PC 원문·BODY/RULE 준비와 달리 NAS에 자동 업로드하지 않는다. 60초 claim timeout은 실제 claim 요청이 다시 발생할 때 운영 경고·시간을 확인한다.

후속 코드 확인에서 NAS 적재기는 기사 문서 저장, BODY 완료, RULE 완료를 각각 PostgreSQL commit하는 경로였다. 읽기 전용 표본은 COMMIT WAL 대기를 보였지만 PID별 호출자를 특정하지는 못했다. 로컬 스크립트는 기존 문서·revision 저장과 기사별 잠금 경계를 유지하면서 BODY/RULE 완료만 기본 25건 단위 transaction으로 묶도록 수정했다. 기사별 savepoint와 기존 ownership 검사가 남아 단일 실패·다른 실행기 소유 건은 같은 묶음의 성공 행을 되돌리지 않는다. 스크립트 문법 검사만 통과했으며 NAS 반영·실행량 전후 비교는 아직 안 됐다. 이미 실행 중인 NAS 프로세스는 로드한 기존 코드를 계속 사용한다.

과거 검색·FLASH/WORLD 뉴스의 PC BODY/RULE 인계 v5와 진단 빌드 v6는 NAS 운영 `/health.server_build`로 확인했다. 재빌드 뒤 기존 검색기사의 PC BODY/RULE과 FLASH/WORLD 수입은 진행 중이다. v6 계측에서 기존 `pc` 작업 선점 지연은 주로 PostgreSQL SELECT 5~8초이며 `IO:BuffileWrite`·`LWLock:WALWrite` 대기 표본이 있었다. 새 검색기사 `pc_search` 단독 선점은 당시 재시도에서도 클라이언트 60초 읽기 시간 초과로 종료됐고 서버 완료 로그가 없었다. 이후 확인한 실행계획과 읽기 경로 개선은 아래에 기록했으며, 60초 장애 전체의 원인·해소는 아직 확정하지 않았다. 기존 NAS 적재분에서 완료 BODY/RULE이 재선점되지 않는지, 미완료분만 PC 인증 API로 진행되는지 PostgreSQL 표본에서 확인하고 본문·평가·핵심문장·수주 결과를 대조한다. 처리량·언론사 차단률·NAS 순위 지연을 계측하기 전에는 대량 전환을 완료로 기록하지 않는다.

2026-09-25 추가 NAS 실행계획·실행 표본: 읽기 전용 `EXPLAIN` 네 조합(pc 검색/시황 × BODY/RULE)은 모두 기존 `(state,next_retry_at,updated_at)` 인덱스로 jobs 후보를 찾고, `stage`·`processing_version` 필터와 article revision 범위 조인을 거친 뒤 `updated_at` 정렬을 수행했다. 실제 실행 없는 계획은 3,438~5,691 jobs 행, 최종 181~887행을 추정했다. PC 검색 BODY의 잠금 없는 읽기 전용 `EXPLAIN ANALYZE`는 65.914ms, shared hit/read 8,786/3 blocks였고 외부 병합 정렬로 임시 파일 3,533 blocks를 쓰고 1,085 blocks를 다시 읽었다. 인덱스 하나를 schema migration 21로 배포하면 기존 20 버전 서버로 되돌릴 때 시작을 거부하므로 그 접근을 철회했다. 대신 `scripts/create_news_claim_order_index.py`로 NAS PostgreSQL에 `(stage,processing_version,updated_at,next_retry_at) WHERE state='PENDING'` 인덱스를 `CONCURRENTLY`로 생성했다. 57.928초 후 유효 상태와 크기 1,613,824 B를 확인했으며 앱 스키마 버전은 20 그대로다. 같은 PC 검색 BODY 쿼리의 후속 계획은 실제 `FOR UPDATE OF j SKIP LOCKED` 형태에서도 새 인덱스를 선택하고 정렬 노드가 없었다. 잠금 없는 읽기 전용 `EXPLAIN ANALYZE` 표본은 0.197ms, shared hit/read 2/6 blocks, 임시 읽기/쓰기 0/0 blocks였다. 데이터량과 캐시 상태가 다른 두 시점의 한 번씩 표본이므로 수치 비율을 운영 처리량 개선으로 일반화하지 않는다. 기존 60초 timeout은 어느 표본에서도 재현되지 않았고, 실제 claim의 잠금·갱신·commit 시간도 읽기 실행 표본에 포함되지 않는다. 운영 중 실제 claim 단계 시간과 PC BODY/RULE 처리량, TOP20 지연을 함께 관찰한 뒤 전체 병목 해소 여부를 판단한다. 누적 인덱스 통계는 reset 시각을 알 수 없어 이번 지연의 시간 구간으로 해석하지 않는다.

과거자료 게시와 운영 DB 통합은 별개다. 2026-09-24 기준 NAS에 게시한 CREON 봉·지수·VI·시총/주식수 SQLite는 운영 PostgreSQL 봉 조회 및 연구의 동일 DB 입력에 아직 연결되지 않았다. 실측상 키움 PC 일봉은 NAS 표본과 일치하지만 1분봉은 일부 NAS에 없고, 기업행동 전 `347850`의 키움 PC 1분봉은 CREON 원주가와 맞으며 CREON 수정주가와 다르다. 2026-09-25 읽기 전용 NAS/CREON 대조에서 `035720` 정규장 381분의 OHLCV가 전부 일치했고 NAS 정규장 외 봉은 234개, `347850`의 NAS 봉은 0개였다. 최초 15:29 누락 판정은 CREON 15:30 마감 체결을 일반 1분처럼 이동한 비교 오류로 정정했다. CREON 5분봉 7종목·일은 76개 연속 구간과 15:30 별도 종가 체결, 하루 내 1분/5분 비혼합을 확인했지만 공급자 간 5분 OHLCV 일치는 미검증이다. 타점 연구의 1분봉·5분봉과 체결가격에는 원주가를 사용한다. 공급자 간 원주가·봉 시각·거래량 차이를 확대 검증하고 연구 조회 경로에 연결하는 작업이 남았다. 기존 D03 파생 입력의 15:30 가상 1분 시각은 수정 코드로 새 ID를 만들어야 성과 비교에 사용할 수 있다. 장기 특징과 거래일 간 수익률은 키움 수정 일봉과 기업행동 사건 기록을 별도로 사용하며 원주가 분봉의 절대 가격과 직접 비교하지 않는다. 기업행동을 가로지르는 보유·수익 계산은 수량·현금 변화를 반영하거나 해당 사례를 격리한다. 2026-09-24 후보일 80,809쌍의 검사에서 4,080구간 중 84구간이 단일 CREON 사건별 수정계수로 설명되지 않았고 최근 날짜를 제외해도 10종목 구간은 불일치한다. 키움 기준 수정분봉 전체 재구성과 이 불일치 해소는 타점 연구의 선행 조건이 아니다. 향후 수정분봉이 별도로 필요해질 때만 기준일·계수 출처·봉 단위 검증을 갖춘 파생값으로 게시하고 기존 실시간/원주가 봉을 덮어쓰지 않는다. 불변 원본은 계속 보존한다.

수정주가 제외 원장은 `scripts/audit_kiwoom_adjustment_candidates.py`와 `data/historical_collection/kiwoom-adjustment-candidate-audit-20260924.json`에 있다. 불일치 84구간은 향후 파생 수정분봉 입력에서 격리했고, 동률 올림 가정에서 일봉 기준 계수가 하나로 좁혀진 2,948구간도 분봉 봉별 검증 전에는 수정분봉 가격으로 승격하지 않는다. 계수 모호·관측 부족·4자리 가정 불일치 1,048구간은 같은 파생 작업에서 보류한다. 이 원장의 보류 상태는 원주가 분봉을 사용하는 타점 연구를 막지 않는다. 원본 데이터와 운영 DB는 변경하지 않았다.

저장 키움 분봉 711종목·일 중 708일은 원주가 기준임을 확인했다. 키움 수정분봉을 사건 이후 기준일로 실조회한 6종목·일의 공통 2,025분 OHLC는 날짜별 계수와 일치했지만 전 구간 검증은 아니다. `059120`·`183300`은 4자리 계수의 반올림 동률 처리 결과가 서로 달랐고, 두 사건을 겪은 `183300`은 기준일에 따라 중간 `0.5093`과 최종 `0.2041`이 달랐다. 이 기록은 가격 기준 차이 감사에 보존한다. 5분봉과 전체 날짜의 수정분봉 자동 파생·게시 gate는 미구현이며 현재 타점 연구에 요구하지 않는다.

| ID | 상태 | 작업·완료 조건 |
|---|---|---|
| D01 | 진행 중 | 삼성전자 1분 최과거→이전 5분 최과거 연속조회·최신 보강·일 단위 경계·구간 종료시각·NAS 스냅샷 완료. 후보 전체 시세 재개 원장 운영 중. 숫자 전용 필터에서 빠졌던 영문 포함 6자리 후보 91개를 원장에 추가했고, 전체 수집도 1분 최과거일 전날을 5분 종료일로 보내 중첩 다운로드를 제거함. 현재 개별 주식 2,637개 중 분봉 완료 2,617개·실패 20개이며, 이전 수집분 중 비주식 308개는 원본을 보존하고 수집 범위에서 제외했다. 남은 주식 공백과 NAS 최신 스냅샷 반영은 O21에 기록 |
| D02 | 진행 중 | 날짜 지정 네이버 웹 검색, 원응답·기사-종목 연결·원문 URL별 시도·발행시각 상태 저장, 당시 상호+변경일 ±14일 구·신 이름 작업 95,090개와 NAS 스냅샷 완료. 검색 403/429는 성공 페이지를 유지하고 막힌 페이지만 공통 제한 대기 후 재시도함. 작업 실제 소진·2년/5년 도달·이용 범위 확인 |

2026-09-28 D02 후속: 검색 페이지 저장 트랜잭션에 기사·종목별 `news_article_pipeline` 대기열을 추가했다. 검색 수집기는 `--search-only`로 원문 대기 없이 다음 검색 작업을 이어가며 `search_complete`는 원문/BODY/RULE 완료가 아니라 검색 목록 완료를 뜻한다. 별도 PC 원문 수집기가 대기열을 claim하고 기존 2초 원문 정책과 PC BODY/RULE 준비를 마친 후에만 작업을 `complete`로 확정한다. 중단된 claim은 10분 후 재선점하고, 원문 저장 뒤 준비가 실패하면 원문 재요청 없이 준비를 재시도한다. 대기열·검색 목록·원문·준비 데이터는 모두 PC에 남고 NAS 대량 적재는 별도 단발성 단계다. 기존 실행 중인 수집 프로세스는 시작 당시 코드를 사용하므로 안전한 작업 경계에서 재시작해 실제 처리량·대기열 배수·중복/누락을 확인해야 한다.

2026-09-28 D02 원문 HTTP 403 후속: 같은 원문 호스트가 연속 3회 403을 반환하면 해당 실행 묶음의 나머지 요청을 생략하고 호스트별 30분 재시도 시각을 PC SQLite에 보존한다. 실제 403 시도와 요청 생략은 구분해 기록하며, 보류 기사는 `pending`으로 남겨 BODY/RULE 준비나 작업 `complete`로 승격하지 않는다. 다른 호스트는 계속 처리하고 재시작 후에도 재시도 시각 전에는 보류 호스트를 선점하지 않는다. 영구 차단 호스트의 최종 종료 정책은 아직 없으므로 해당 기사는 미완료로 남을 수 있다. 운영 재시작 후 실제 403 빈도·원문 처리량·SQLite claim 시간을 관찰한다.
| D03 | 부분 구현 / 실행 검증 중 | `historical_reconstruction/v1`과 기존 1분 전략 runner용 어댑터 구현. 후보일 장후 선택→다음 거래일 결과, 실제 수집시각/복원 clock, 사후 후보/당시 TOP20을 분리하고 순위 Factor를 차단. 2024~2026 세 날짜의 개별 주식 파생 입력에서 연속 1분봉 쌍이 없는 종목(`000545`, `019175`, OOS의 `012860`)은 사용자 결정에 따라 제외하고 원본 후보·봉과 파생 manifest의 제외 원장은 보존했다. 첫 개발 입력은 TRAIN 41/41, VALIDATION 44/44 최소 연속쌍 조건으로 `READY`이며 OOS는 봉인했다. 2024-09~2025-12 후보일 321일 중 모든 후보가 최소 조건을 충족한 날은 150일이고, 종목·결과일 단위로 불충족 307/13,632개만 제외하면 321일 모두 적어도 한 후보가 남는다. 제외 투영 원장은 원본 감사의 SHA-256과 제외 코드를 고정했다. 기존 150일 6,327종목·일의 종일 봉 개수 감사에서 263종목·일이 300봉 미만이었고 원인은 봉 개수만으로 확정하지 않았다. 이는 종일 완전성·대표성·수익성 검증이 아니다. 512MB RSS 한도 중단 뒤 1GB에서 평가를 완료했으나 정각 봉 종료→다음 봉 시작 검증의 1분 오차를 발견해 기존 결과는 비교에서 제외했다. 경계 계산 수정 후 첫 TRAIN·VALIDATION 돌파·눌림 네 실행이 모두 `ELIGIBLE`·자료 품질 `PASS`로 완료됐고 OOS는 봉인했다. 후속 월별 16개 날짜의 개발 파생 후보 657종목·일과 제외 15건을 사전 원장과 대조했다. 다기간 독립 TRAIN 488/488·VALIDATION 169/169 최소 연속쌍 gate는 `READY`이며 OOS는 입력에서 제외했다. 동결 월별 657종목·결과일의 추가 감사에서 300봉 미만 22건·10분 초과 간격이 있는 종목·일 15건(실제 간격 26구간)을 진단 원장에 기록했다. 8구간은 분봉 합계와 키움 일봉 거래량 일치, 2구간은 키움 분봉에도 사이 봉 없음, 1구간은 공식 VI와 겹쳤다. CREON 원응답과 저장 DB는 15종목·일 모두 시각·거래량이 일치해 26구간은 저장 누락이 아닌 원응답 공백이다. 남은 15구간의 무거래·공급자 누락 여부는 직접 확정하지 못했고 어느 구간도 자동 제외로 처리하지 않았다. 뉴스 백필이 진행 중이고 종일 봉 완전성·사후 후보 대표성은 확인 전이므로 이 구조 스냅샷을 최종 성과 비교에 승인하지 않음 |
| D04 | 부분 구현 | 프로필별 별칭·분리 확장·재병합 금지 원장과 뉴스 AI의 근거 있는 원시 테마 후보, 기사 제목·발행시각·원문을 포함한 활성 프로필 검토, 사용자 승인·거절·이름 수정 원장, 반복 제안 승인 중 최신 별칭 재해석, 파일 백업·NAS 전체 스냅샷 보존을 구현. 실제 기사 제안 품질·의미별 병합 정확도, 과거 사건 일괄 후보 생성과 검토량 운영은 남음. 동일 지역의 다른 사업은 지역명만으로 합치지 않음 |
| D05 | 이후 개선 | 사용자 학습·사례를 반영한 타점 정책 비교. 현재 기준을 최종 전략으로 확정하지 않음 |
| D06 | 부분 구현 | 첫 50사례와 고유 기사 1,422개를 동결하고, 앱의 105행 사람 검토·불변 판정·사건 단위 시간순 분할·OOS 제외 공통 개발 입력, 정답 없는 블라인드 요청, 방법별 완전 응답 결과와 사건 군집·테마 채점 계약까지 구현함. 현재 사람 판정은 0건이라 실제 입력·요청·결과·평가는 없음. 실제 검토와 첫 입력 생성, RAG·미세조정 실행기, 별도 관련성 음성 표본, 비교 결과 검토, 로컬 LLM/Mac 작업자와 행동 EV 범위 결정이 남음 |
| D07 | 지수·VI NAS 게시 완료 / 개별 주식 3코드 공백 | 지수 1분·5분·일봉과 사용자 KRX VI 476,626건을 확보하고 시장 맥락 DB·원응답을 `historical-market-context/v1` 불변 run에 게시해 NAS DB 해시를 검증했다. 원래 후보 2,945코드 중 코스피·코스닥 개별 주식 2,637개를 시총·주식수·수정주가 대상으로 확정했다. 각 작업은 2,634개 완료·3개(`008290`, `046070`, `082660`) CREON 코드 거부이며 ETF 184·ETN 119·리츠 5개는 제외 상태다. 과거 값 단위·후보일 누락과 연구 입력 연결은 남는다. 기존 NAS 실시간 `dstr_stk` 스키마는 변경하지 않는다다으 |

D03 월별 16사례의 네 고정 TRAIN·VALIDATION 구조 실행은 완료했다. 하루 표본의 저장 병목은 묶음 트랜잭션으로 줄였고, 재생 커서는 해당 종목·당일 봉만 전략에 전달한다. 월별 71,911개 판단에서 중단했던 RSS 증가 원인은 Windows 메모리 측정 함수의 매 호출 네이티브 객체 재생성으로 재현했고, 1회 캐시 수정 후 동일 입력의 첫 판단 전 RSS를 2,149→769MB로 줄였다. 새 독립 DB의 네 요청은 2GB 한도 안에서 모두 `ELIGIBLE`·자료 품질 `PASS`이고, VALIDATION 돌파의 다음 봉 공백 주문 1건은 검열 처리했다. 진단 DB와 OOS는 성과 비교에 사용하지 않는다. 사전 자료 범위 감사에서 선택일 16/전체 321일, 선택 종목·일 657/전체 13,325개이고 후보 수 중앙값은 선택일 41·나머지 42개다. 이는 날짜 고정의 국면 편향이나 종일 봉·뉴스 시점 품질을 해소하지 않는다. 다음에는 뉴스 백필 완료 후 당시 이용 가능 시각·후보 대표성·종일 봉 품질을 재평가하고, 월별 첫 후보일에 한정된 이 구조 표본을 최종 전략 선택 자료로 승격할지 판단한다.

[D01~D03 상세 기획](HISTORICAL_BACKFILL_PLAN.md). D04~D06을 완성해야 D01을 시작할 수 있는 구조로 만들지 않는다.

## 운영·정확성 후속

| ID | 상태 | 남은 범위 |
|---|---|---|
| O01 | 운영 미확인 | 최신 `market-cap-reference-v1` 이미지와 `/health` 일치, 시가총액 복원·계좌 이름 변경·구형 계좌 해제. 소스 동기화만으로 완료 처리하지 않음 |
| O02 | 운영 미확인 | PC 재시작 공유설정 불필요 POST, NAS 복귀 분봉 조회·API 문구·직접 시총 캐시, proxy 재생성 후 HTTPS/WSS 확인 |
| O03 | 저장 경계 실측 / 공급 신선도 관찰 중 | 2026-09-23 09:28:30 회차의 `ka00198`는 전송 73ms·큐 대기 3,461ms였다. 09:28:22.111 `ka10001` 응답 이후 다음 Kiwoom 완료 로그까지 11.5초 공백이 있고, 기존 브로커는 응답 기록·쿼리 캐시 저장을 마칠 때까지 단일 전송 worker를 점유했다. 같은 구간의 정확한 후처리 단계는 당시 로그만으로 특정할 수 없다. 별도 직렬 저장 worker·단계별 계측 배포 후 `/health.server_build=2026.09.23-ranking-broker-persistence-v1`을 확인했다. 10:05~10:07 회차의 `ka00198` 큐 대기는 0~5ms 수준이었고, 동시에 `ka10001` 캐시 저장 9,205ms 경고가 남아 기존 저장 병목의 실재를 확인했다. 10:06:00 순위는 10:06:02.765까지 직전 회차를 반환하고 10:06:03.559에 최신 20행이 관측됐다. 공급 응답이 갱신되는 정확한 시각과 재조회 간격의 효과는 후속 관찰 범위이며, 최신 회차 전에는 직전·부분 순위를 새 순위로 공개하지 않는다 |
| O04 | 운영 미확인 | PostgreSQL 다중 연결에서 lease·STOP·시작·인증 교체·늦은 writer·CAS·프로세스 강제종료 복구. 기본 schema checker와 별도 |
| O05 | 운영 미확인 | NAS vault Linux 권한·보호키/암호문 별도 백업·복원, 재발급 token 영향·만료 계좌 UNKNOWN 처리 |
| O06 | 운영 미확인 | 실전/모의 복수 계좌 REG·00/04·9201 격리, 조회 표본/빈 표/거래소 잔고 의미, 후착 응답·동일 주문번호 분리 |
| O07 | 미구현/운영 미확인 | 서버 역할 API는 구현됐으나 PC 시세 담당 선택 UI는 연결 필요. 장중 인증/역할 교체·첫 frame·실제 gap·순위 기한·표 보존·장애전환 실검증 |
| O08 | 운영 미확인 | 실제 NAVER/DART/AI 키·IP·예산·캐시·기사/job 보존과 실패격리. AI ACTIVE가 유료 분석 성공을 보증하지 않음 |
| O09 | 운영 미확인 | 조건검색 ACK/재등록/장후 지원, 해외시세 실제 cycle 중 ON/OFF·주기·월물 전환 |
| O10 | 운영 미확인 | 뉴스 revision·usage·대상 병합·읽기만 할 때 예산/job 불증가, query-set 7일 품질·저장량, 사람 정답셋에 대한 의미 정확도. 네이버 증권 FLASH/WORLD의 NAS 빌드 반영·실제 cursor 증가·중복/누락·BODY 부하, 과거 날짜 전체 도달범위와 연구 입력 연결은 별도 확인 |
| O11 | 운영 미확인 | 구체적인 승인 범위가 있을 때 제한 모의 주문·체결·취소·UNKNOWN·비용·A5 대조. 과거 1주 시험 계획은 새 주문 권한이 아님 |
| O12 | 운영 미확인 | 실제 NAS/PC 24시간 이상, 연구 ON/OFF 비교, CPU/RSS/디스크/DB/queue·순위/초/분봉 연속성·복구·다른 PC API/WS/일지 동기화. 2026-09-25 17:04~17:05의 분봉 저장 경고를 2026-09-26 02:05 KST NAS `iostat`와 시각 대조했다(로그 시각은 UTC 기준). `commit_ms` 898~1,010ms 구간과 겹쳐 NVMe RAID1 `w_await` 128~283ms·`aqu-sz` 56~218, Docker 경로 `dm-4` 사용률 99~99.8%가 관측됐고, PostgreSQL `WALWrite` 표본과도 부합한다. 저장 경로 쓰기 지연이 커밋 지연에 기여한다는 증거는 강하지만, 어떤 컨테이너/호스트 작업이 부하를 만들었는지는 미확인이다. 2026-09-26 02:13 표본에서 `docker stats`의 server/database `Block I/O`가 30회 모두 `0B / 0B`였고, `iostat` 30초 표본에는 dm-4 `w_await` 최고 1,366.8ms·대기열 189.42·사용률 99.4%가 있었다. 느린 PostgreSQL 경고와 같은 시각 로그가 없어 이 표본은 장치 부하만 재확인했고, 특정 커밋이나 컨테이너에 귀속하지 못했다. `docker stats` 반복 수집은 02:13:31~02:15:12로 약 101초 걸렸지만 동시 `iostat`는 30초만 실행돼 두 자료가 전체 구간에서 정렬되지 않았다. 2026-09-26 02:22:53~02:23:26 표본에서는 DB 컨테이너의 PostgreSQL 프로세스 3개(`/proc/<pid>/io`)가 합계 23,986,176 B를 기록했고, `dockerd`는 5,976,064 B, 여러 `kworker`도 각각 수 MB를 기록했다. 이 시간 구간 안에 `17:23:11.728 UTC` 분봉 저장 경고가 있어 `commit_ms=2,007`, 전체 저장 2,524ms였으며, 접속 13ms·SQL 단계 합계보다 COMMIT이 지배적이었다. 같은 수집 구간의 `iostat`에서 dm-4 사용률 최고 99.7%, md4 `w_await` 최고 1,743.6ms·사용률 83.7%, NVMe `w_await` 최고 503.23ms·사용률 77.9%도 관측됐다. 따라서 DB writeback과 저장장치 포화가 같은 짧은 구간에 겹친 직접 증거가 추가됐고 저장 경로 지연이 커밋 지연에 기여한다는 판단은 더 강해졌다. 다만 iostat의 dm-4 `w_await=49,582.5ms`는 쓰기 2건뿐인 한 초의 극단 표본이므로 대표 지연값으로 쓰지 않는다. PostgreSQL `COMMIT` 자체의 같은 시각 wait event는 채집되지 않았고, 로그 출력은 요청한 `02:23:35 KST`를 넘어 `02:26:53 KST`까지 포함하므로 정확한 대조에는 02:22:53~02:23:26의 공통 구간만 사용했다. 2026-09-26 02:33:36~02:34:36 KST의 100ms 폴링에서는 `COMMIT` 상태 표본 99회 중 `IO:WalSync` 54회, `LWLock:WALWrite` 45회였고 모든 행의 차단 PID 목록은 비어 있었다. 이는 COMMIT 지연이 잠금 차단이 아니라 WAL 쓰기·동기화 대기임을 직접 확인한다. 이는 99개의 개별 트랜잭션 수가 아니라 반복 표본 수다. 한 PID에서 `WALWrite`가 최대 15회 연속, 다른 PID의 `WalSync`가 최대 10회 연속 관측됐다. 같은 표본 전체의 `DataFileRead` 225회는 다른 활성 쿼리 대기라 COMMIT 원인으로 합산하지 않는다. 기존 `fsync=on`, `synchronous_commit=on` 설정에서는 COMMIT이 WAL의 내구성 확인을 기다리는 동작과 일치한다. WAL 대기 메커니즘은 확정됐지만, 이 새 1분 구간의 동시 `iostat` 및 느린 저장 경고가 없어 개별 WAL 동기화 시간을 특정 디스크 지연과 수치로 연결하지는 못했다. 2026-09-26 02:39:14~02:40:13 KST에는 `iostat` 60표본과 PostgreSQL COMMIT 폴링이 겹쳤다. COMMIT 표본 86회 중 `IO:WalSync` 57회, `LWLock:WALWrite` 28회였고 나머지 1회는 대기 이벤트가 없었으며, 차단 PID는 모두 비어 있었다. 같은 초에 dm-4의 쓰기 대기시간은 평균 140.6ms·최대 2,106.77ms, 대기열 최대 191.5·사용률 최대 100%였고, 예를 들어 02:39:41에는 dm-4 `w_await=2,106.77ms`·사용률 99.7%와 PostgreSQL `WalSync` 표본이 동시에 기록됐다. 따라서 해당 시간대 COMMIT이 WAL sync/write를 기다리며, NAS 볼륨 저장 경로가 포화된 것이 지연에 직접 기여한 점은 확인됐다. 같은 02:39:14~02:40:14 KST 서버 경고에는 분봉 저장 6건이 기록됐고 `commit_ms`는 332, 447, 452, 1,023, 1,265, 579ms였다. `17:39:21.935 UTC`의 분봉 저장은 COMMIT 1,023ms로, 종료 시각에서 측정시간을 뺀 약 17:39:20.912~21.935 구간이 PostgreSQL `WalSync` 표본 및 02:39:21 dm-4 쓰기 대기 187.90ms·대기열 165.54·사용률 99.2%와 겹쳤다. `17:39:41.859 UTC` 분봉 저장은 COMMIT 1,265ms로, 약 17:39:40.594~41.859 구간이 PostgreSQL `WalSync` 표본 및 02:39:41 dm-4 쓰기 대기 2,106.77ms·대기열 191.50·사용률 99.7%와 겹쳤다. 이로써 실제 분봉 저장 COMMIT 지연이 WAL 대기와 NAS 볼륨 저장장치 지연이 겹친 구간에 발생한 것을 확인했다. 같은 구간의 서버 로그에는 분봉 저장 6건이 있었고 `commit_ms`는 332, 447, 452, 1,023, 1,265, 579ms였다. `17:39:21.935 UTC`의 저장(762행, COMMIT 1,023ms)은 대략 17:39:20.912~21.935에 진행됐으며, PID 19151의 `WalSync` 표본(17:39:21.076~21.884) 및 02:39:21의 dm-4 지연과 겹친다. `17:39:41.859 UTC`의 저장(256행, COMMIT 1,265ms)은 대략 17:39:40.594~41.859에 진행됐으며 PID 19283의 `WalSync` 표본(17:39:41.079~41.787) 및 02:39:41 dm-4 최대 지연과 겹친다. 따라서 분봉 저장의 실제 COMMIT 지연이 WAL 대기·NAS 볼륨 쓰기 지연과 함께 발생한 사실은 확인됐다. 저장 경고에는 backend PID/application_name이 없어 특정 wait 표본과 같은 트랜잭션이라고 단정할 수는 없다. 2026-09-26 02:50 무렵의 동시 `/proc/<pid>/io` 표본에서는 DB 컨테이너 PostgreSQL의 background writer 20,230,144 B, walwriter 19,931,136 B, PID 7789 postgres 13,344,768 B, checkpointer 5,783,552 B가 관측됐고, `dockerd`도 19,230,720 B였다. 다른 Python 컨테이너는 180,224 B, smbd는 172,032 B였으며 여러 `kworker`도 각각 2.6~11.7 MB를 기록했다. 따라서 관측된 사용자 공간 쓰기 주체로는 PostgreSQL과 Docker daemon이 컸지만, `kworker` 카운터를 별도 원천으로 합산할 수 없고 Docker daemon의 쓰기 파일도 특정하지 못했다. 같은 60초 iostat에서 dm-4는 02:50:19에 `w_await=703.52ms`, 대기열 161.18, 사용률 99.3%까지 올라갔다. 이 새 표본에는 같은 시각의 PostgreSQL COMMIT wait와 분봉 저장 경고가 없어 프로세스 쓰기량만으로 해당 장치 지연의 유발자를 확정하지 않는다. | 2026-09-26 02:54:52~02:55:52 KST PostgreSQL 17 `pg_stat_io` 표본에서는 client backend relation write 약 23.2MB, background writer relation write 약 28.3MB, WAL 증가 약 22.5MB(97,698 records, `wal_write` 124회, `wal_sync` 123회)가 관측됐다. `wal_buffers_full=0`; 출력된 쓰기 표본에는 checkpointer/autovacuum 항목이 없었다. 이 집계는 해당 분의 DB 쓰기량을 보여주지만 앱 저장 경고·iostat와 동시 채집되지 않아 어떤 기능/요청이 relation write를 만들었는지, 이 표본이 느린 COMMIT과 직접 겹쳤는지는 확인할 수 없다. `pg_stat_io` 수치만으로 background writer와 client backend 쓰기를 합산해 서로 다른 물리 쓰기량으로 해석하지 않는다. 2026-09-26 02:54:52~02:55:52 KST와 같은 UTC 구간의 NAS 로그에서 `ka10080` 분봉 수집 경고 13건, broker 저장 경고 13건, PostgreSQL 분봉 저장 경고 8건(총 4,854행)을 확인했다. 8개 분봉 저장의 `commit_ms`는 268~1,079ms(평균 약 728ms), 전체 저장시간은 약 1.0~1.6초였다. 따라서 이 분에 분봉 적재가 실제 동시 DB 쓰기 작업이었고 PostgreSQL 쓰기량에 기여했을 가능성은 확인됐다. 다만 `pg_stat_io`는 테이블·호출자별 쓰기를 연결하지 않으며 로그에는 backend PID가 없어, 전체 relation write나 물리 장치 포화의 기여분을 분봉에 단독 귀속하지 않는다. 붙여넣은 Docker 로그에는 요청한 구간 이후 17:58대 기록도 함께 있었으므로 집계는 UTC timestamp로 지정 구간을 필터링해 계산했다. 추가로 2026-09-26 03:01:12~03:02:10 KST `iostat` 59표본에서 `dm-4` 쓰기 대기시간 평균 130.63ms·최대 1,963.67ms, 100ms 초과 16회, 대기열 최대 227.16·사용률 최대 100%였다. NVMe0 쓰기 대기 평균 56.59ms·최대 390.03ms, NVMe1 평균 30.27ms·최대 194.91ms였다. 이 표본은 앞선 02:54 분봉 로그/PG 통계와 다른 시각이라 해당 분봉 커밋에 귀속하지 않으며, 같은 03:01 구간 앱 로그·PostgreSQL wait 표본은 아직 미수집이다. 2026-09-26 03:01:12~03:02:10 KST `iostat`와 18:01:12~18:02:10 UTC 서버 로그를 정렬했다. 겹치는 구간에는 분봉 저장 9건(4,529행)이 있었고 `commit_ms` 225~1,030ms(평균 737ms), 전체 저장 1,150~1,975ms였다. 저장 종료 시각 부근의 1초 `dm-4` 표본에서 `w_await` 169~700ms, 사용률 71.5~99.5%가 관측됐다(일부 commit은 직전 초 표본에도 대기 증가가 있음). 따라서 분봉 DB 저장 중 NAS 볼륨 쓰기 지연이 함께 발생했다는 상관 증거가 추가됐다. 1초 iostat 집계와 앱 로그만으로 개별 COMMIT이 실제 장치 대기한 시간, 다른 DB 쓰기 작업의 비중, 저장장치 지연의 근본 유발 프로세스는 확정할 수 없다. 2026-09-26 원인 분리용으로 `AUTONOMOUS_TOP20_MINUTE_BACKFILL_ENABLED` 환경설정을 추가했다. `0`은 TOP20 순위·실시간 구독을 유지하면서 NAS 자동 `ka10080` 분봉 백필만 건너뛴다. 로컬 단위검사는 통과했으며, NAS 적용 후 OFF/ON 비교는 아직 수행하지 않았다.
| O13 | 미구현 범위 포함 | Shadow producer는 현재 breakout 경로. 다른 Family의 READY/자동운용은 producer 지원·평가가 필요 |
| O14 | 미구현 | A4B 직접/fallback 계좌 신원 1~5단계: DPAPI 지문·고정자격·프로세스간 한도, HTTPS resolve/aliases, fresh REST batch, 9201 직접 WS, offline-origin 결합과 두 PC 검증. NAS R6 완료와 별개 |
| O15 | 미구현/운영 미확인 | S2b VI 대상 호가 snapshot/parser/storage, 실제 phase/FID·애프터 적격·누적 reset·조건/지수/비용·TR 범위 |
| O16 | 운영 미확인 | 15:20/15:30/15:40/16:00/20:00/20:05 경계, 정규종가/전체일 coverage·VI/cohort·다음 거래일 만료·0g 전환 표시 |
| O17 | 운영 미확인/미결정 | SOR 원문·source 전환·partial·통합 차트·과거 오표기 범위. 근거 없이 과거 KRX를 일괄 수정하지 않음. 200종목 초과 수집범위와 통합 연구전략은 별도 |
| O18 | 재현 필요 | 과거 장중 점검의 상한가 `effective_at`/`available_at` 약 9시간 차이. naive KST/Linux 시간대 경로 확인 후 원인·수정 범위 결정. 참조된 JSON 원자료가 현재 보고서 폴더에 없음 |
| O19 | 제한 병렬화 운영 시작 / 별도 프로세스 설계·장시간 검증 필요 | NAS 뉴스 수집·BODY·RULE·AI를 서버 API와 독립 프로세스로 분리하는 작업은 미완료다. 기존 단일 작업기는 BODY 분당 8~10건, 대기 최대 약 43시간, RULE 기아를 보였다. 새 서버 빌드에서는 같은 Uvicorn 프로세스 안에 BODY 2·RULE 선호 1 lane의 제한 병렬화와 중복 claim 방지를 적용했고 첫 1분에 BODY 11건·RULE 5건을 확인했다. 장시간 처리량·DB/CPU wait·순위 지연은 미검증이다. 독립 worker는 vault 단일 소유·키 교체 전달·PostgreSQL job claim·설정 즉시 반영·중복 수집 방지를 정한 뒤 구성한다. |
| O20 | 중복 진입 방지 구현 / 장시간 재검증 대기 | PC 과거 종목 뉴스 수집기 두 개가 동시에 실행돼 공용 `.news-job-heartbeat.json.tmp`에서 `WinError 32`가 발생한 기록이 있다. PowerShell 수집기 시작 전에 배타적 파일 잠금을 잡고, 중복 실행은 상태·STOP 파일을 건드리지 않고 종료한다. Python heartbeat 임시 파일은 PID·UUID별 고유 경로를 사용한다. 잠금을 선점한 재실행이 스킵되는 동작과 동시 heartbeat 쓰기의 JSON/임시 파일 정리를 검증했으며 현재 실행 중인 구 프로세스의 재시작 뒤 장시간 운영 검증은 남았다. |
| O21 | 개별 주식 20코드 공백 / NAS 반영 대기 | 원래 실패 139개를 관리자 CREON에 재조회해 122개 코드 거부와 17개 1분봉 0건을 재현했다. 122개 중 ETN 119개는 `A` 접두사 요청 오류였으나 개별 주식 수집 범위 밖이다. 코스피·코스닥 개별 주식 2,637개 기준 분봉 완료 2,617·실패 20이다. 거부 3개(`008290`, `046070`, `082660`)는 현재 CREON 코드 목록에도 없으며 정확한 목록 제외 사유·대체 원천 확인이 남는다. 1분봉 0건 17개 중 16개는 과거 5분봉을 확보했다. 이전 표적 재조회로 17개 종목의 5분봉 661,048행을 로컬에 추가했고, 주식 기준 5분봉 보유는 2,448개다. 비주식 원응답·기존 봉은 삭제하지 않고 `excluded`로 보존한다. 로컬 갱신분의 NAS 불변 스냅샷 게시와 연구 입력의 비주식 필터 확인이 남는다 |
| O22 | 거래소 원문 효력일 대조 완료 / 정정·상충 사례 검토 | 개별 주식 2,637개의 CREON 수정계수 사건, 상장일, 키움 일봉의 장기 거래량 0 전환·재개일, 상폐 검토 대상의 마지막 거래일에서 4,587개 사건 단서를 만들었다. 기존 DART 수정계수 조회 1,993건을 재사용하고 새 조회는 완료 2,531·공식 빈 응답 63건으로 종결했다. 거래량 0은 거래정지 확정이 아닌 조회 단서다. 짧은 거래정지 누락을 줄이기 위한 2,637종목 전체 DART 거래소 유형 I 공시 조회도 완료 2,620·빈 응답 17건, 공시 332,723건으로 종결했고 우선주 2건의 발행회사 매핑을 재검증했다. DB 무결성 `ok`, 새 DART ZIP 4,215개 해시 일치, NAS 최신 run `20260924T130303Z-2b6cc96dd69a`의 DB SHA-256·원응답 18,449개·latest 전환을 확인했다. 기존 시장 맥락 공급 불가 6건은 게시 manifest에 남고, 거래소 제출 관련 공시 원문 4,220건을 추가 확인해 명시 효력일 3,083건(정지·재개·상폐)을 접수일과 분리했고, 일봉 대조에서 접수일과 다른 효력일 1,562건을 확인했다. 원문 5건은 DART 014로 받을 수 없고, 상폐일 뒤 거래가 나타난 1건과 효력일보다 늦게 접수된 1건은 검토 상태로 보존한다. 월별 16개 TRAIN/VALIDATION 동반 자료에 겹치는 사건 6건을 반영했으나 장중 공시 수신시각·정정 계보가 미확정이라 전략 신호/거래 가능 판정에는 자동 사용하지 않는다. 새 NAS 불변 게시본의 검증 결과는 CURRENT_STATUS를 따른다 |

O12 분봉 저장 후속(2026-09-26): 앞선 WAL 동기화 대기·NAS 볼륨 쓰기 지연 증거를 바탕으로 PostgreSQL `ka10080` 동일 봉 재저장 시 정규 봉 행의 불필요한 UPDATE를 생략하는 로컬 수정을 검증했다. 장중 신규 편입 분봉 보완과 장후 확정 수집의 기본 동작은 변경하지 않았다. NAS 공유에는 누적 빌드 소스를 배치하고 임시 진단용 분봉 OFF 설정을 `1`로 되돌렸지만, 실행 컨테이너 재빌드와 운영 중 지연 감소는 아직 확인되지 않았다. 장중 OFF/ON 재시험은 완료 조건이 아니다. 메타데이터·확정 이력 쓰기와 저장장치 자체 지연은 남을 수 있다.

O12 작업별 진단 후속: 서버 소유 배경 작업을 재빌드 없이 제한 시간 동안 중지·복구하고 ON/OFF/ON으로 비교하는 진단 도구를 추가해 NAS 공유에 누적 소스와 백업·해시를 검증해 배치했다. PostgreSQL 저장 함수별 성공 호출 수와 빠른 건을 포함한 분봉·일봉 시간 분포를 수집한다. 키움 응답의 문서·dataset·cache 저장은 별도 writer로 표시하며, PC SQLite 저장과 NAS WAL을 구분한다. 전체 WAL 증가량은 특정 API에 직접 귀속하지 않는다. 실행 컨테이너 재빌드는 NAS `sudo` 인증이 필요해 아직 하지 않았으며, 실제 TTL·중지 후 재개, 장시간 측정, 분봉/뉴스/기타 writer의 부하 기여도는 운영 검증 전이다. [사용법과 범위](NAS_RUNTIME_DIAGNOSTICS.md)를 따른다.

O12 진단 수집 제어: DB 저장 함수의 메트릭 수집은 기본 OFF이고, `capture on/off/status`로 1~60분 임시 제어한다. 단일 측정과 A/B/A는 수집이 꺼져 있을 때에만 자기 작업 시간만 임시로 켜고 완료·중단 시 소유 lease를 확인해 복구한다. 수집 OFF/lease 만료 때 메모리 버퍼를 비우며 저장 동작은 계속된다. 로컬 반영만 했으며 운영 컨테이너 재빌드와 ON/OFF 복구 확인은 남아 있다.

2026-09-26 O12 진단 master 계층 후속: `tool on/off/status` 최상위 스위치와 master ON을 요구하는 하위 capture·pause·measure·test를 로컬에 구현했다. build marker는 `2026.09.26-diagnostic-master-switch-v1`이다. 이 변경은 기존 8개 선택 작업 제어에 상위 gate를 추가한 것이다. 모든 저장 writer의 개별 자식 스위치가 완성된 상태는 아니다. 0B/실시간, 계좌 복구·이벤트, 주문·체결 원장, REST writer 단위 스위치는 아직 미구현이며, 손실 없이 멈추고 재개하는 각 경계 설계와 전용 테스트를 마친 뒤 같은 master 하위에 추가해야 한다. NAS 소스 동기화·컨테이너 재빌드와 `/health.server_build` 확인이 남아 있다.

2026-09-26 O12 분봉/뉴스 A/B/A 진단 및 내부 실행계수: 60초 `news_jobs` ON/OFF/ON 비교에서 분봉은 각 단계에 실제 유입됐고 0B flush는 없었다. OFF 구간에 뉴스 claim/활성 SQL은 0이었지만 분봉 저장 p95는 1,576ms로 baseline 1,368ms보다 낮아지지 않았고, 구간별 분봉 건수·행수와 타 writer 부하가 달라 뉴스가 분봉 지연의 주원인이라는 결론은 내리지 않았다. `ka10080` 18,808행 표본은 41회 저장, 전체 시간 p95 988ms, revision 단계 p95 477ms, commit p95 649ms였으며 호출당 transaction/commit은 각각 1회다. 다음 재현에서 revision 최신값 SELECT와 revision INSERT 실행 수를 직접 확인하도록 메트릭을 추가했다. 여기에는 canonical bar·metadata `executemany`의 행별 실행 수가 포함되지 않는다. 로컬 변경이며 전용 회귀·격리 PostgreSQL 동시성 및 보존 테스트, NAS 반영 후 운영 계측은 남아 있다. observation history를 끄거나 revision 데이터를 생략하는 조치는 하지 않는다.

2026-09-26 O12 NAS revision 계수 측정 `20260926T053843Z-62810d24`: `news_jobs` A/B/A의 각 60초 구간에서 `ka10080` 완료 56/54/60회, 분봉 저장 42/40/38회·18,022/18,633/16,562행이었다. 각 저장의 transaction·commit은 1회다. revision SELECT는 각 행마다 1회(총 18,022/18,633/16,562), revision INSERT는 세 구간 모두 0회였다. revision p95 524/382/592ms, commit p95 798/776/1,342ms, 전체 저장 p95 1,489/1,069/1,870ms였다. OFF 구간은 `news_jobs.effective=false`, `paused_by_diagnostic=true`, 활성 news query와 claim 0으로 실제 정지했으며 분봉은 계속 유입됐다. OFF 구간 p95 개선 뒤 ON 복귀에서 악화됐지만 device await·queue와 트랜잭션/WAL 활동도 함께 변했고 요청량도 완전히 같지 않아 뉴스의 인과 기여나 저장장치 단독 원인을 확정하지 않는다. 관측된 비용 후보는 다수의 중복 방지 revision SELECT와 독립적인 COMMIT 대기다. revision 최신값 배치 조회와 닫힌 `ka10080` 분봉 우선권은 SQLite 단위검사를 통과했다. 사용자 요청으로 NAS `2026.09.26-minute-source-authority-v1`에 배포했고 `/health` 상태와 build marker를 확인했다. 다만 배포 전 독립 PostgreSQL 통합검사는 실행하지 못했으므로 same-key 동시성, 0B/REST 순서 역전, revision chain, replay/dedup, rollback은 아직 운영환경 전용 DB에서 검증해야 하며, 저장 성능 개선도 재측정 전이다.

2026-09-26 O12 NAS revision 성능 측정 `20260926T064325Z-6bf6dd08`: 60초 `measure`에서 `ka10080` 7회·6,300행, revision latest lookup 7회·6,300키, revision INSERT execute 1,090회였다. 분봉 저장 p50/max는 3,423/9,799ms, revision 전체 p50/max 289/7,783ms, commit p50/max 245/8,555ms다. 별도 warning의 34,895ms revision phase는 정식 표본 시작 2초 전 완료되어 phase별 진단 통계에 포함되지 않았다. 그 이벤트의 lookup/INSERT 세부시간과 INSERT 수가 없으므로 원인 하위단계는 아직 확정하지 않는다. 표본에는 `news_job_claim` 117회, external-market 6,539행 수집이 함께 있었고 WAL 약 13.48MB 및 host-wide dm-4 busy 91.55%, average queue 662.96이 관측됐다. 전체 WAL·저장장치 지표는 개별 분봉 호출에 귀속할 수 없다. 계측을 세분화해 `revision_sources_ms`, `revision_locks_ms`, `revision_lookup_ms`, `revision_rows_ms`, `revision_insert_execute_ms`를 추가하는 로컬 구현을 진행했다. 저장 의미·트랜잭션은 변경하지 않았다. 새 marker `2026.09.26-minute-revision-phase-metrics-v1`의 NAS 재빌드 뒤 동일 로그와 60초 측정을 다시 수집해야 하며, NAS에는 아직 반영되지 않았다.

두 번째 NAS 측정 `20260926T065335Z-cf197ea9`도 이전 build에서 실행됐다. 60초 분봉 저장 23회·20,422행, revision lookup 23회·20,422키, revision INSERT 12회; revision p50/max 218/381ms, 저장 전체 p50/p95/max 662/2,093/2,414ms, commit p50/p95/max 166/1,715/2,030ms였다. 34.9초 outlier는 이 창에서 재발하지 않았으나 조건이 통제되지 않아 최초 지연이 해결됐다고 보지 않는다. 성공한 0B flush는 0회였고, 뉴스 claim 117회 및 외부시장 collector 3회가 병행됐다. dm-4 busy 48.53%, average queue 48.06으로 첫 표본과 다르지만 인과 비교는 아니다. 새 phase 계측이 운영 build에 반영된 뒤 동일 경계에서 재측정해야 한다.

새 build 배포 후 세 번째 측정 `20260926T070655Z-ea8d03ea`에서 세부 계측을 확인했다. 60초에 `ka10080` 44회 완료, 저장 22회·19,800행, revision lookup 22회·19,800키, revision INSERT 305회였다. source 구성 p50/p95/max 189.5/220/231ms, advisory lock 1/1/10ms, batch lookup 35/42/46ms, row 처리 1/1/2,120ms, 실제 INSERT execute 0/0/2,099ms; revision 전체 233/268/2,358ms, 전체 저장 645/2,891/4,366ms, COMMIT 213.5/2,393/3,738ms였다. row 처리와 INSERT execute tail이 근접하므로 느린 revision outlier에서 INSERT 실행 비용이 유력한 기여 후보지만, 호출별 phase 상관 자료가 아니므로 동일 요청의 기여율은 미확정이다. 34.9초 사례는 재현되지 않았다. 동시 구간에 dm-4 busy 64.01%·queue 190.29·write await 575.9ms, news active query 64회/claim 111회, 외부시장 6,539행이 있었고 `0B` 저장은 없었다. 분봉 단독 원인으로 귀속할 수 없다. 다음은 이벤트별 phase를 한 레코드로 연결해 상위 revision/COMMIT 지연의 동시성을 확인하고, 필요하면 별도 PostgreSQL에서 변경 의미·동시성·rollback을 검증하는 것이다. 현 측정은 원인 제거·성능 개선 완료가 아니다. 실행 후 diagnostic master OFF를 확인했다.

O12 후속 계측 로컬 구현 `2026.09.26-minute-revision-call-samples-v1`: percentile 집계에 더해 각 `ka10080` 저장 표본의 timestamp·API ID·행/lookup/insert 수·모든 저장 phase를 같은 객체로 반환한다. 이를 통해 revision row/INSERT 시간이 높았던 동일 호출에서 COMMIT과 total도 높았는지 직접 대조한다. payload 내용은 기록하지 않고 저장·transaction 동작도 바꾸지 않는다. 결과 크기는 단일 measure 최대 300초 범위의 선택 표본에 한정된다. 관련 테스트 통과와 NAS 누적 동기화·재빌드 뒤 새 표본 검증이 남아 있다.

O18은 미해결 가능성을 보존한 항목이며 이번에 재현·확정한 버그가 아니다. 기존 R7 HTTPS/WSS와 PostgreSQL schema19 검증 완료 기록은 인정하되 위 추가 검증을 대신하지 않는다.

## 연구 기능의 확장·제약

| ID | 상태 | 범위 |
|---|---|---|
| R01 | 일별 평가기간 확장·NAS 새 날짜 자동 export와 빈 날짜 재확인 로컬 구현 / 실제 NAS 단기 검증 완료, 운영 검증 대기 | opt-in source는 새 거래일의 완성된 동결 입력을 발견하면 동일 전략·비용·예산으로 단일 TRAIN/VALIDATION fold를 이동해 별도 실험으로 등록한다. NAS 자동 준비와 함께 켜면 기준 단일 일별 입력 다음 날짜부터 완료 후 24시간이 지난 날짜를 하루씩 조회·동결·검증·등록하고 작업자 소유 cursor를 전진시킨다. 주말은 건너뛰고 NAS 관측 0건 평일은 v27 원장에 남겨 하루 뒤부터 재확인한다. 늦게 들어온 자료도 같은 불변 게시 경로로 등록한다. FINAL/OOS·범위/종목/세션 불일치·비용 유효기간 밖은 제외한다. 2026-09-25 실제 NAS 두 거래일의 90초 표본으로 격리된 동결·자동 등록을 확인했다. 운영 캠페인 source 미설정으로 24시간 전체 구간, 장시간 처리량·지연 자료 운영 검증은 남았다 |
| R02 | 신규 순차·final RUNNING 수동 회수 구현 / 기존 순차 무소유 run 보류 | 순차 개발 검증과 final batch의 마지막 고정 요청을 앱 상태 폴더에 원자 저장한다. 재시작 후 수동으로 이어서 실행하면 기존 연구 원장의 완료 결과를 확인·재사용한다. final의 명시 복구 요청은 저장 snapshot에서 제거해 재시작만으로 복구를 반복하지 않는다. 손상된 요청은 자동 실행하지 않는다. final 후보별 `NOT_STARTED/RUNNING/COMPLETED` 등 진행 snapshot은 원자 게시하고 실행 중 화면에 표시하되 종료 결과 전에는 복구·개발 사용을 허용하지 않는다. 두 화면의 자식 PID·시작 토큰·고정 요청 hash를 작업별 소유 기록으로 남기고 정상 종료 때 정리하며 `scripts/inspect_research_operation_receipts.py`로 생존·종료·확인 불가를 읽기 전용 판별한다. final UI는 고유 owner token을 갖고, `scripts/recover_final_holdout_orphan.py`가 종료된 자식·요청 hash·미완료 결과·산출물 부재를 확인한 뒤 `--execute`에서만 같은 owner/generation의 RUNNING 후보를 CANCELLED 처리한다. 순차 UI도 새 실행부터 v24 run owner 토큰·세대를 원자 claim하고, `scripts/recover_development_validation_orphan.py`가 같은 종료·요청·산출물 검증 후 해당 세대의 RUNNING만 수동 취소한다. 다음 순차 실행은 사용자가 같은 요청을 다시 시작할 때 새 owner·세대로 claim한다. v24 이전 무소유 RUNNING에는 소유 증거가 없어 자동 회수하지 않는다. |
| R03 | 후속 검토 | 자동 final 선택/실행·미등록 계산식 draft·뉴스/초자료/호가 신규 Family. 기존 자동 가설은 등록 Family/허용값 범위 |
| R04 | 운영 미확인 | 대규모 bundle·입력 전체 로드/RAM projection·결과 보관량. 현재 RSS/preflight·CPU 양보는 OS 강제 quota가 아님 |
| R05 | 범위 미확인 | 기업행동·상장/휴장/특별개장 기준정보의 역사 revision, 직접 TOP20 저장 선택과 coverage UI의 실제 제공 범위 |
| R06 | 모델 한계 | 현 연구 export의 T+1초 미지원, VI 호가·부분체결·시장충격 재현 불가, 미사용 검사가 현재 연구 DB footprint 범위에 한정 |
| R07 | 구조 실행 완료 / 실전비용 미검증 | 연구 `fixed_bps/v2`는 소수 bp 문자열을 정확히 받아 수수료 1.5bp를 양쪽에, 매도세 20bp를 매도에 적용한다. 기존 `fixed_bps/v1` 정수 계약과 과거 1bp+18bp 구조 요청은 보존하되 성과 근거로 재사용하지 않는다. D03 TRAIN·VALIDATION 네 구조 실행에서 v2 계약과 추정 슬리피지 5bp를 동일하게 적용했다. 각 분할은 하루이므로 정책 성과는 미확정이고 실제 브로커 비용 검증도 남는다 |
| R08 | 개별 주식 3코드 공백 / 사건 유형 검토 | 주도후보 원본 2,945코드에서 개별 주식 2,637개만 CREON 수정계수 작업 대상으로 유지한다. 2,634개 완료·3개 코드 거부이고 비주식 308개 작업은 `excluded`다. 기존에 발견한 사건 3,097건의 DART 분류 조회는 끝났지만 확정 989·수동 검토 984·미매칭 1,124이며 이 중 개별 주식은 2,106건(확정 987·수동 검토 980·미매칭 139), 비주식은 991건이다. 주도후보 2,637개 키움 수정 일봉의 NAS 재조회·종목별 누락 0 검증은 완료했다. 후보 연구 창과 겹친 CREON/DART 알려진 사건 보고서는 894종목·1,447건이며, 키움 고유 사건 누락 가능성과 수동 검토 667·미매칭 98건이 남는다 |

## 사용자가 보류한 것

**NAS 저장 용량 상한·자동삭제는 2026-09-21 결정에 따라 보류한다.** 현재 읽기 전용 용량 진단과 24시간 이상 미완성 연구 staging의 제한 정리를 완성 자료 자동삭제로 설명하지 않는다. 완성 연구자료의 삭제/압축/이동과 외부 일지 참조 전수 보호도 미구현이다.

재개 조건은 사용자의 재요청 또는 디스크 임계치 근접이며, 기존 결정대로 [보존 설계 기록](archive/2026-09-22/reports/NAS_STORAGE_RETENTION_DESIGN_REVIEW_20260921.md)의 보호 관계·삭제 순서·기간/용량 충돌을 **Astra Ultra 설계 재검토**로 확정한 뒤 구현한다. `0=무제한`과 원본/일지/연구 참조 보호를 유지한다. 이번 문서 이동은 앱 데이터 정리·삭제 정책의 실행이 아니다.

SOR 프리/애프터 시장가 주문 UX, 시장 수급/Open Space/Base/M/W/압축 화면은 별도 후속 제품 범위다. 일부 기반 모듈의 존재를 전체 UI 완료로 보지 않으며 현재 데이터 확보보다 앞세우지 않는다.

2026-10-01 O12 250일 신고가 PC 경로 복구: NAS의 005930·000660 KRX/NXT 일봉을 실제 PC 클라이언트 경로로 읽어 `DailyHighService`가 각각 250행, `ready`, 검증 완료로 계산하는 것을 확인했다(250일 고가 380,000원·3,002,000원). 원인은 NAS 응답이나 일봉 계산이 아니라 failover/validation wrapper가 coverage 조회 계약을 전달하지 않아 기간 증거가 사라진 것이었다. wrapper 전달과 장후 NAS coverage 재조회 경로를 로컬에서 수정했고 관련 회귀 41건이 통과했다. 키움 TR fallback은 사용하지 않았다. NAS 소스는 변경하지 않았다. 실행 앱은 아직 이전 메모리를 사용할 수 있으므로 앱 재시작 뒤 TOP20 250일 표시 확인이 남아 있다.

2026-10-01 O12 250일 신고가 일부 `-` 후속 수정: 직전 live 비교에서 현재 TOP20 20종목 중 11종목은 KRX coverage가 `ready`였으나 NXT eligibility가 `nxtEnable=N`, NXT 봉 0건·coverage 미확인이라 무조건 KRX+NXT를 요구하는 계산이 `unverified`를 반환했다. `DailyHighService`가 오늘 PC 캐시에 확인된 NXT 여부를 우선 사용하고, 값이 없을 때만 NAS의 저장 eligibility 문서를 읽도록 바꿨다. 명시적 NXT 비대상만 KRX 검증 결과로 계산하고, NXT 가능/불명은 기존처럼 두 시장 검증을 요구한다. 수정 코드로 NAS의 현재 TOP20 및 실제 PC SQLite의 오늘 eligibility cache를 사용해 다시 계산해 20/20이 계산 가능함을 확인했다(오늘 확인 20종목, NXT 비대상 11종목). 185행·1행 종목도 원천 이력 종료 증거가 있어 확보된 기간의 최고가를 반환했다. 키움 TR이나 NAS write는 발생하지 않았다. 이번 eligibility 수정의 자동 회귀검사는 아직 실행하지 않았고, 실행 PC 앱의 실제 표시는 재시작 뒤 확인해야 한다.

## 종료된 리팩터링

2.0.0 리팩터링의 1~5단계는 완료다. controller 통합·대형 UI 분할을 크기만으로 재개하지 않는다. 장시간/다른 PC 확인은 O12로 승계했다. [종료 상태](../REFACTORING_CLOSEOUT_PLAN.md)와 [과거 보고서](archive/2026-09-22/reports/REFACTORING_CLOSEOUT_REPORT.md)를 참조한다.
2026-10-05 O12 DB trace chunk bound: reproduced a 3,899,223-byte UTF-8 chunk that exceeded the 2,000,000-byte download limit while the trace could otherwise finish as complete. Local fix caps new chunks at 1 MiB, preserves ordered pending suffixes across flushes, counts pending events in status, and fails explicitly for a single oversized event. The trace/replay unit suite passed 18/18 locally and in the NAS test container. Focused immutable source release `2026.10.05-db-trace-chunk-bounds-v1-9209b8fd29423301`, based on active release `2026.10.03-db-minute-replay-v1-388841088675094c`, changed only `app.py`, `diagnostic_trace.py`, and its trace test; NAS source-runtime trace tests passed 7/7. Deployment is confirmed by the reported `/health` build/release and `database_container_unchanged=true`. Post-deploy status is `WAITING_MARKET`, `observation_expected=false`, trace `off`. Still open: measure capture overhead and verify a full 65-minute market trace and replay.

2026-10-05 분봉 RAM 표시 보완 회귀: 중앙 앱 계약 fixture가 기존 trace route 5개(GET/POST trace, POST trace/stop, GET trace/{trace_id}, GET trace/{trace_id}/chunks/{chunk_name})를 누락하여 test_public_api_route_contract_is_stable 1건 실패. 이번 변경은 route 추가가 아니므로 해당 계약 fixture 보완은 별도 작업으로 남긴다. 관련 로컬 159건 중 나머지 158건 통과. QueryStore 소비자 감사는 load_minute_bars의 선택 realtime_deltas 인자 및 앱 표시 조회 경로 이동으로 review_required이며 기준 원장은 덮어쓰지 않았다. 후속 후보 v1은 collector 함수 누락으로 import 오류가 나 배포되지 않았다. v2 불변 게시본에서 관련 로컬 57개, 이후 NAS 전용 PostgreSQL 포함 83개가 통과했고 배포와 health v2를 확인했다. writer replay 20261005T100452Z-80ed9651은 245건 오류 없이 완료했지만 timing_preserved=false이며 collector 저장 주기 효과는 검증하지 않는다. **남음:** [collector 통합 재현 설계](NAS_RUNTIME_DIAGNOSTICS.md#collector-replay-design)에 따른 실제 parser/집계/저장 loop의 전용 DB fixture 검증, 이후 경량 0B 입력 capture와 recorded replay. 현재 trace의 5초당 4,096건·1MiB 단일 chunk 처리량으로 개장 틱 수집을 보장하지 않는다. replay 조회 helper는 finalizing 및 report_url 게시를 기다리지 않는 결함이 있으므로 API 연결 단계에서 보완한다. 직전 404가 어느 중간 상태에서 발생했는지는 당시 상태 출력이 없어 확정하지 않는다.

2026-10-06 과거자료 수집기 모니터의 반복 DB 전수 집계를 SQLite transaction 내부 증분 카운터로 교체했다. 기준 집계는 PC 원장 4개에서 1회 완료됐고, 이후 3초마다 summary만 읽는다. 모니터 재실행 뒤 자동 진행률 갱신과 SSD 사용량을 확인하고, 카운터와 원장 상태가 일치하는지 다음 운영 점검에서 재확인한다.

2026-10-08 accumulated source publication boundaries:
The PC working source, regression tests and audit/design documents are being committed on `main`.
The independently validated NAS capture snapshot is preserved on `codex/trace-ram-8g-5m-v1`
at `0dbde2307b0b63898b9905b172e74f7fa2dcd6b9`; it is not silently merged into the PC source.
In particular, its packed-RAM trace implementation and API-test dependency correction remain
in that exact candidate branch. Publishing either branch does not activate a new NAS release.
The KST logging change and NAS-local scheduling script are included in the PC working source;
logging activation remains deferred until the retained capture has been persisted.
Generated logs, payloads, archives, temporary copies and local credentials are not publication inputs.

2026-10-08 O12 NAS-local one-shot start replaces chat-driven start:
`scripts/nas_scheduled_trace.py` uses NAS Python 3.8 stdlib and local authenticated API;
it does not change the immutable source release or restart containers. Seven scheduler tests
passed on PC; NAS read-only preflight passed. Posted script/plan hashes were checked.
Detached NAS process PID 3492 is armed for October 8 08:59:50 KST / 3,600 seconds,
deferred persistence 20:10 / exact e1cc01dde5bacbb9 release / 8GiB / 5M / all three input flags.
Status is `/volume1/docker/kiwoom-monitor/artifacts/nas-trace-start-20261008.status.json`;
the adjacent `.log` records the start response. A lock and existing-status fence prevent duplicate
local launches; API session/revision/instance guards remain. Ambiguous POST acknowledgements
are not retried or stopped; excessive lateness fails visibly. Actual timing is still measured.
The existing start heartbeat is now a read-only 09:01 start verifier, not a second starter.
October 9 02:30 integrity verification obtains trace_id from this local status file.
NAS reboot terminates the one-shot wait; failure must be reported rather than silently rescheduled.
During deferred capture, accepted grows while written can remain zero until 20:10 persistence.
The earlier 08:58 chat-preflight reservation below is superseded.

2026-10-08 O12 8GiB/5M NAS activation and reservation confirmed:
NAS exact-source regression passed 103 tests (54.788s), private API gate passed, and the bounded
durable persistence smoke completed 294 events with no drops/rejections and verified checksums,
order and payload references. Active release is
`2026.10.08-trace-ram-8g-5m-v1-e1cc01dde5bacbb9`, build `2026.10.08-trace-ram-8g-5m-v1`.
Live API confirms schema 3, 8GiB / 5,000,000 events and 1MiB/s deferred persistence.
Master/trace are off, no diagnostic runs or paused workloads. Database container was unchanged.
The last helper check imported the image source and failed after successful deployment;
its check now uses the live API and PID1 concrete source path. No release restart is needed.
Both existing automations were updated and their saved settings verified: start preflight at
08:58 KST on October 8, target capture 08:59:50–09:59:50 (3,600 seconds), all three input flags
enabled, persist_at 20:10 KST; verification October 9 02:30 KST. Scheduler/API lateness must be
reported from actual timestamps. Full-capacity persistence, opening-peak losslessness, overhead
and source-state equivalence remain unverified. The following pending notes are historical.

2026-10-08 O12 NAS API-test dependency correction:
8GiB/5M의 첫 NAS 배포 gate는 `test_diagnostic_trace_api.py`의 Starlette TestClient import에서
중단됐다. 운영 코드 실패나 배포 완료가 아니며 httpx2는 설치하지 않았다. 같은 후보의 테스트만
실제 HTTP ASGI middleware/router/validation 경로를 직접 실행하도록 바꿨다. 인증 오류·잘못된
session·엄격한 boolean·실패 시 control rollback·chunk byte 응답 등의 기존 assertion은 유지했다.
소스 lifetime/background 시작은 이전 fixture와 같이 실행하지 않는다. 별도 새 클라이언트 의존성은 없다.
해당 API 4건 통과 후 httpx/httpx2/starlette.testclient가 로드되지 않은 것을 확인했다.
동일 후보의 전체 관련 PC 회귀 103건이 통과했다. 정확한 NAS 테스트·작은 durable smoke·활성화는 남았다.
AGENTS.md에 httpx2 설치/의존성 추가 금지와 테스트 실패를 조용히 생략하지 않는 규칙을 추가했다.
테스트 수정 게시본은 `2026.10.08-trace-ram-8g-5m-v1-e1cc01dde5bacbb9` (933 files,
isolated commit `0dbde2307b0b63898b9905b172e74f7fa2dcd6b9`, code working tree clean)이다.
기존 8GiB 후보와 운영 src_hash가 같고 `test_diagnostic_trace_api.py`만 바뀌었다.
API 재확인에서 운영은 v6, master/trace off, active runs/paused workloads 없음이었다.

2026-10-08 O12 최신 사용자 선택 — 8GiB / 5,000,000 events, 08:59:50~09:59:50 KST:
RAM packed-block 후보의 실제 기본값과 API capabilities를 8GiB/5M으로 맞췄다.
전체 trace 저장 quota도 8GiB로 조정했으며 deferred 저장 속도 1MiB/s와 64KiB block은 유지한다.
호스트·cgroup 모두 시작 시 9GiB 여유가 필요하며 8GiB를 즉시 할당하지 않는다.
비활성 게시본은 `2026.10.08-trace-ram-8g-5m-v1-abece6f02adbe726` (933 files,
isolated commit `b49576921869d26eebbc8ef0414775434b4818dc`, code working tree clean)이다.
관련 PC 회귀 68건과 schema 3 / three-input flags / limits API gate가 통과했다.
사용자가 제공한 v2 private 4GiB/5M synthetic 60분 결과는 accepted 2,307,608,
input_rejected=known_dropped=0, RSS peak 2,812,207,104 bytes였다. 실제 장초 최대 입력 보장이나
전체 앱 성능 기준선이 아니다. 새 정확한 NAS 소스의 회귀·작은 durable smoke·활성화 확인은 남았다.
NAS 활성 포인터는 v6로 보존했고 예약은 아직 기존 설정이다. 새 릴리즈 검증·활성화 확인 뒤
두 예약을 3,600초 / schema 3 / store_inputs+collector_inputs+top20_inputs / 정확한 build·release로
맞추며 persist_at은 기존 20:10 KST를 유지한다. 전체 용량의 장후 persistence와 실제 overhead는 미검증이다.

아래 09:00~10:00 및 4GiB/1M 기록은 이전 단계의 이력이며 최신 요청 조건이 아니다.

2026-10-08 O12 사용자 capture 구간 변경 — 09:00~10:00 KST:
사용자가 65분 대신 60분을 선택했고 개장 전 5분을 제외한다. v11의 동일 혼합 입력 구성에서
36,000개 메시지(100ms 간격의 합성 시각 60분)를 검사하는 비활성 후보
`2026.10.08-causal-capture-60m-v1`을 준비했다. private probe의 15GiB/5M 한도는
논리적인 retained charge 예산이며 RSS 할당량이나 운영 기본값이 아니다. 이전 v11의 RSS 최고치
4,675,239,936 bytes와 charge 16,075,246,320 bytes를 같은 값으로 취급하지 않는다.
현재 메모리 계정 방식은 유지한다. 실제 RSS·host/cgroup 여유·ON/OFF 지연·입력 거부를
새 60분 envelope에서 다시 검사한다. API 전달 값은 3,600초이며 recorder 운영 기본값은
4GiB/1M 그대로다. NAS exact-source gate가 끝나기 전에는 후보 활성화나 예약 build/options 변경을
하지 않는다. 09:00~10:00은 승인된 목표 구간이고 아직 실제 예약 변경 완료가 아니다.

비활성 후보 `2026.10.08-causal-capture-60m-v1-a8376b5b6bdf96e7`을 NAS에 stage했다
(929 files, isolated candidate commit `ffc47de13c7348081845f2d876988c67c28eb785`).
정확한 후보 소스로 PC unit/API 회귀 101개(skipped=0), API 전달 gate, 100-message smoke,
POSIX shell 문법 검사를 통과했다. active.json·runtime.json·runner.py는 stage 전후 동일하다.
NAS의 전체 36,000-message 용량/RSS gate는 사용자 보고서
`causal-capture-60m.wMVk6k.log`에서 통과했다. accepted 2,307,608, input_rejected 0,
known_dropped 0, written 0(deferred RAM)이다. charge 15,550,948,874 bytes,
memory high-water 15,559,223,163 bytes, RSS peak 4,526,362,624 bytes,
host MemAvailable 최소 13,554,601,984 bytes, cgroup headroom 최소 16,934,567,936 bytes였다.
논리 15GiB 예산의 여유는 약3.4%다. 이는 지정된 synthetic 60분 입력의 수용 결과이며
실제 장초 최대 입력량에 대한 보장이 아니다. 운영 active/container identity는 그대로다.
짧은 ON/OFF 메시지 p95는 ON 7.45~7.71ms / OFF 2.94~2.99ms였고, 전체 ON p95 8.05ms,
max 1,837ms, payload-copy max 2,477ms였다. 지연 원인은 미확정이므로 overhead 승인은 보류한다.
294-event 작은 durable smoke만 checksum/sequence/payload 검증까지 완료했고, 전체 230만 건의
persistence는 검사하지 않았다. full-capacity persistence·overhead 승인 전 운영 활성화는 보류한다.
복사 지연의 입력 종류와 동시 GC 시간을 확인하는 비활성 진단 후보
`2026.10.08-causal-capture-copy-profile-v1-f5b07b1d5fd17b0e`를 stage했다(931 files).
recorder·payload 계약·GC 정책·운영 한도는 그대로이며 private probe에만 timing wrapper를 둔다.
PC 20-message smoke는 기존과 같은 accepted 294, rejected/drop 0을 유지했고 shell syntax가 통과했다.
NAS에서는 `scripts/check_causal_copy_profile.sh`로 검사한다. 이 계측 결과는 원인 축소용이고
capture ON/OFF 성능 승인 기준선으로 사용하지 않는다.

2026-10-08 O12 copy/GC NAS 진단 결과:
사용자 보고서 `causal-copy-profile.1gzqXn.log`는 동일 36,000-message 입력을 끝까지 수용했다
(accepted 2,307,608, input_rejected/drop 0, written 0). RSS peak 4,525,232,128 bytes,
최소 host MemAvailable 13,554,970,624 bytes였고 운영 active/container는 변경되지 않았다.
최대 rest_input copy 2,448.337ms 중 GC 2,430.272ms, 작은 19,268-byte charge의
top20_realtime_input copy 1,989.087ms 중 GC 1,988.862ms였다. save_query copy도
1,636.053ms 중 GC 1,617.831ms였다. 따라서 이 probe의 관측된 긴 copy outlier는
payload 크기만으로 설명되지 않으며 대부분 자동 GC 대기로 설명된다. 전체 GC gen2는
38회/24,801.542ms였다. 이 계측은 실제 장중의 전체 병목 원인을 확정하는 자료는 아니다.
다음 단계는 deferred queue의 대규모 Python 객체 graph를 줄이는 내부 보존 형식과 그
CPU/지연/RAM trade-off 결정이다. 앱 전체 gc.disable/gc.freeze/threshold 변경은 선택하지 않았다.
장후 near-capacity persistence와 계측 없는 ON/OFF 재검증은 계속 미완료다.

2026-10-08 O12 causal/deferred v10 사전검사 결과 및 v11 private sizing 준비:
NAS v8의 12GiB/5M 시험은 24,361번째 혼합 요청 묶음에서 REST request lane limit으로 거부됐다.
v9는 canonical 요청 signature UTF-8 bytes를 내부 key로 보관하고 길이를 charge해 이 과다 계산을 고쳤다.
signature별 ordinal과 저장/replay 형식, 4MiB/32,768-key 한도는 유지했다. REST tape·구독 provenance 회귀
49개와 v9 정확한 후보 회귀 100개, API gate가 통과했다.

v9 NAS 시험에서 REST lane은 3,968개, 2,911,020 bytes로 한도 이내였다. 그 뒤 12GiB copy reservation이
먼저 찼다: 29,731개 입력 메시지(각 100ms 간격으로 약 49.6분분), accepted 1,905,826, 거부 28건
(모두 capture_memory_full), dropped 0, charged 12,851,783,703 bytes, memory high-water
12,856,897,977 bytes. RSS peak 3,735,203,840 bytes, 최소 host MemAvailable 14,344,683,520 bytes,
container headroom 13,431,042,048 bytes였다. 제한 포화 자체는 예상 분류로 gate가 끝났지만, 이 입력량은
65분분에 못 미친다. 따라서 12GiB는 이 synthetic 입력률에서도 65분 capture에 충분하다고 볼 수 없다.

비활성 NAS 후보 `2026.10.07-causal-deferred-capture-v10-fdbeeef6e982aa2e`의 회귀 100개·API gate와
16GiB 소규모 smoke는 통과했다. 그러나 NAS 65분 입력 probe는 trace 입력 전에
`trace_memory_headroom_insufficient`로 중단됐다. deferred start preflight는 설정된 charge budget에 1GiB를
더한 headroom을 요구한다. 16GiB 설정의 요구량은 18,253,611,008 bytes(17GiB)이며, 직전 production baseline
snapshot의 host MemAvailable은 18,073,481,216 bytes로 180,129,792 bytes 부족했다. 따라서 이번에는 39,000개
입력·실제 RSS high-water·65분 용량·지속 중 4GiB 여유를 측정하지 못했다. 이는 포화 또는 16GiB의 실제 RSS
부족을 뜻하지 않고 시작 preflight 미충족을 뜻한다. active release, 운영 컨테이너, 4GiB/1M 기본값 및 Oct 8
예약은 변경하지 않았다. 다음 private sizing 단계는 운영 preflight를 약화하지 않은 채 시작 조건을 만족하는
비활성 NAS 후보 `2026.10.08-causal-deferred-capture-v11-dffa8b971619a1ea`를 v10 정확한 소스에서
stage했다(928 files, commit `ae1e7cbe9c71ec44ca83cdeddbcae4bf529137d8`, active/runtime 변경 없음).
v11 15GiB NAS probe는 39,000개 중 37,201개 입력(100ms 간격 기준 약 62분)을 처리한 뒤
`copy_reservation_budget`에 닿았다. `accepted=2,384,653`, `known_dropped=0`, 그러나 `capture_memory_full`
거부가 28건이어서 손실 없는 완료가 아니다. copy reservation이 먼저였고 5M event cap은 먼저 차지 않았다.
charge는 16,075,246,320 bytes, memory high-water 16,080,360,594 bytes, 메시지당 평균 charge 약 432,119
bytes였다. 실제 process RSS peak는 4,675,239,936 bytes(시작 약44MiB 대비 증가 약4.63GB), 최소 host
MemAvailable 13,404,901,376 bytes, 최소 cgroup headroom 16,789,262,336 bytes로 측정됐다. 시험 중
4GiB 물리 여유 guard는 지켰다. 15GiB 예산은 이 synthetic 65분 envelope에 부족하다.

추가로 최대 payload-copy 시간이 2,481ms, message latency max가 1,833ms였다. 어떤 입력 event가 이 시간을
만들었는지는 현재 집계로 분리되지 않아, capture overhead 승인은 보류한다. 같은 profile의 짧은 ON/OFF
비교도 200 messages 조건에 한정되고 실제 장중 지연으로 일반화하지 않는다.

16GiB v10 재시험은 예상 입력 charge와 5M event 여유로 보면 39,000개 완료 가능성이 있지만, preflight는
17GiB available을 요구한다. v11 대형 시험 직전 host MemAvailable은 18,036,588,544 bytes로 요구치보다
217,022,464 bytes 부족했다(별도 production snapshot 차이는 약180MB). 다음은 NAS의 현재 MemAvailable을
읽고 18,253,611,008 bytes 이상일 때만 v10 private gate를 재실행한다. 그보다 낮으면 시작하지 않는다.
active release, 운영 컨테이너, 4GiB/1M 기본값 및 capture 예약은 계속 변경하지 않는다. 16GiB pass도 실제
시장 최대 입력률, 65분 실시간 지속, paced persistence/저장공간 및 copy 지연 원인 확인을 대신하지 않는다.

2026-10-07 O12 causal/deferred v7 NAS 결과 및 v8 private sizing 후보:
사용자 제공 NAS `causal-capture-sizing.1sKoif.log`에서 API, 회귀 94개, PostgreSQL gate 15개가
통과했고 5,000종목 catalog를 포함한 294event durable 검증도 통과했다. 그러나 4GiB/1M은
630,132event, 8GiB/5M은 1,267,390event에서 `capture_memory_full`로 입력이 거부됐다.
두 경우 event capacity보다 retained-memory copy admission이 먼저 제한됐다. 8GiB 시험의 RSS는
2,508,378,112 bytes, host MemAvailable은 15,572,205,568 bytes였다. charge는 실제 RSS가 아니다.
단순 입력 OFF/ON p95는 2.967/7.896ms로 overhead 승인도 미완료다. 이 controlled shape의
추정 처리율을 실제 장초 최대 입력률이나 65분 성공으로 사용하지 않는다.
v8는 운영 기본값 4GiB/1M을 유지한 채 12GiB/5M만 private process에서 시험한다. PC 회귀 97개,
API gate, 소규모 mixed 158event(거부/누락 0), POSIX 문법과 성공 출력 sink를 통한 종료 코드
0/7/125 전파를 검증했다. NAS 시험은 16GiB 컨테이너, 시작 여유 13GiB, 실행 중 host/cgroup
여유 4GiB guard와 즉시 report/진행률 출력을 사용한다. full-capacity persistence와 전역 파일
보존 한도 4GiB는 별도 미완료다. 운영 active v6와 Oct 8 예약은 변경하지 않는다.

2026-10-07 O12 causal/deferred catalog 입력 한도 — 이전 준비 기록:
혼합 native capture probe의 5,000종목 catalog 응답은 기록되지만, DB용으로 확장한
`replace_documents(stock_catalog)` 인자가 호출당 8MiB copy charge에 걸린다. 전체 RAM은 당시
약 12.3MiB뿐이므로 session RAM 확대와 별개다. 원형 인자는 14,038,408 charged bytes,
직렬화 814,054 bytes로 16MiB 안에 들어왔다. 이 경로에만 `stock-catalog-documents/v1` 16MiB
profile을 두고 원형 인자·단일 native call·원인 관계를 보존하도록 결정했다. 범용 8MiB 및 node/type/secret
검사는 유지한다. reserve/finally와 replay decoder/preflight가 같은 profile을 검증해야 한다.
columnar 변환 및 원인 payload로 저장 인자를 재구성하는 방식은 이번 후보에 도입하지 않는다.
상세 계약과 gate는 [catalog copy profile 설계](DB_REPLAY_TRACE_DESIGN.md#catalog-copy-profile-decision-2026-10-07-implementation-pending).
active v6와 Oct 8 예약은 변경하지 않았으며 4GiB/1M, 8GiB/5M의 65분 충분성은 아직 미확정이다.
v4 NAS gate는 DSM Docker가 `--cpus 1`을 거부해 시작되지 않았다. v5에서 이 제한만 제거한 뒤 API 검사는
통과했으나, 128MiB 임시공간에서 회귀 51개 중 41개가 quota 오류를 냈다. v6는 tmpfs를 512MiB로 늘렸지만,
deferred trace의 테스트 경로가 세션 시작 시 실제 파일 저장 여유 4GiB+64MiB를 확인해 여전히 부족했고,
게이트가 지정한 T1–T4 테스트 파일 3개도 release에 빠져 총 21개 오류가 났다. 모두 용량 측정 전의 gate 구성
오류다. tmpfs를 5GiB로 설정하고 누락된 테스트 세 파일을 포함한 비활성 v7 후보
`2026.10.07-causal-deferred-capture-v7-5127bafdc15c374a`를 NAS에 staging했다. v7도 `--network none`,
`--memory 12g`, 읽기 전용 후보 mount와 운영 포인터 불변 검사로 격리된다. 용량/RSS/MemAvailable/ON-OFF/
persistence gate는 아직 미실행이며 active v6와 Oct 8 예약은 바꾸지 않았다.

2026-10-07 O12 causal/deferred compact retention — PC 구현 완료, NAS gate 대기:
동일 native 4만 tick·124,001 event의 Windows 설계 실험에서 기존 RSS 증가 155.13MiB,
JSON bytes 176.28MiB, shared fixed fields + slotted stage 106.04MiB였다. JSON bytes는 채택하지 않는다.
공통 source/subscriber/delivery 정보를 불변 객체로 공유하고 세 논리 event의 순서·시각·내용을
그대로 보존하는 RAM 표현을 구현했고 관련 PC 회귀 84건이 통과했다. 실제 보유 참조에 따른 charge와
durable drain 뒤 해제는 기존 recorder lock 안에서 관리한다. 8GiB/5M은 아직 후보이며 최종 설정이 아니다.
input rejection의 terminal incomplete 처리도 반영했다. 전역 파일 보존 한도 4GiB는 별도 미완료 gate다.
비활성 candidate는 `2026.10.07-causal-deferred-capture-v2-872a3b5a5ee3f389`이며 NAS 성능 검증과
p95 개선, 장초 최대 혼합 입력 coverage는 아직 확인되지 않았다.
상세 계약·비교 결과·구현/검증 순서는 [capture capacity 설계](DB_REPLAY_TRACE_DESIGN.md#t1t4-deferred-capture-capacity-decision-2026-10-07-implementation-pending)에 있다.
현재 active v6 및 10월 8일 08:55 예약은 그대로이며, 유효한 최대 혼합 입력량과 NAS gate는 미완료다.

2026-10-07 O12 T4 native TOP20 + peer PostgreSQL acceptance — controlled fixture gate 완료:
비활성 candidate `2026.10.07-top20-session-v2-57de01203aa5af84`에서 T3 13건과 T4 2건, 총 15건이
skipped=0으로 통과했다. 반복 native TOP20 실행, service OFF/peer 선택, descendant sink 대체,
COMMIT ACK loss 뒤 실패 표식·drain 및 DB/outbox baseline 복원을 확인했다. v1 baseline
`56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3` 보존, v2 baseline
`61113931f06005e5afb24abad62622c1d52a34d9549012b3977f593040afe240` 복구, owned connection 0,
임시 cluster 제거, 운영 active와 두 container 보존이 보고서
`replay-cache-v2-acceptance-ec337a0eda5c1167ff7f36c17a59a872.log`에 기록됐다. 실행 중 2회 stale/partial
순위 응답 경고가 있었고 service가 그 회차를 저장하지 않았다. 이는 fixed controlled input에 대한 stale
응답 거부 동작이며 정상 장중의 연속 순위 입력 coverage를 입증하지 않는다. report의
`source_state_equivalent=false` 그대로이며 성능 기준선도 아니다. 다음은 유효한 실제 입력 capture가 확보된 뒤
현재 코드 baseline replay를 먼저 고정하는 것이다.

2026-10-07 O12 T4 native TOP20 replay 후속 — shared source clock와 native peer worker의 실제 실행·종료
경계를 연결했다. v2 lease cache clock은 active/run-ready/generation 검증을 그대로 보존하며, 독립 recorded
runner가 공유 runtime으로 몰래 진입하는 것을 차단한다. peer 작업은 이미 arm된 TOP20 source clock을 다시
arm하지 않고 같은 절대 timeline에서 native actor로 실행한다. DB worker는 runtime 소유 executor에서 실제
끝날 때까지 추적하며 operation error와 waiter cancellation은 실험을 실패 상태로 남긴다. TOP20 shared
execution 및 관련 boundary/recorded execution/cache clock 회귀 34건이 로컬에서 통과했다. 기록은
`artifacts/t4-shared-clock-native-worker-tests.log`다. 검증은 단위/fake store 범위이며 전용 PostgreSQL lease,
동일 DB baseline/최종 DB·revision 검사나 peer descendant closure를 통합한 T4 acceptance는 아니다. 다음은
source descendant를 중복 재생하지 않도록 preflight한 뒤 core를 owned lease/restore/drain lifecycle에 잇는 것이다.
활성 NAS release와 capture 설정은 변경하지 않았다. 상세는
[T4 shared execution 경계](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#t4-shared-clock-peer-execution-경계--2026-10-07-로컬-검증)를 본다.

2026-10-07 O12 T4 native TOP20 replay core — 로컬 부분 검증 완료: 동일 controlled fixture로 native
순위·편입 준비·offline 구독/ACK·0B 입력 경로를 세 번 실행했고, TOP20 제외 실행도 확인했다.
반복 실행의 membership 및 준비 단계 결과가 일치했고, 제외 실행은 membership·구독·REST 요청·과거
결과 주입을 만들지 않았다. 일봉·수급 fixture 빈 응답은 준비 실패로 유지됐다. 재생 2개 테스트가
96.020초에 통과했으며 구독·RAM cold gate·반복 취소/drain과 기존 collector/broker/runtime 회귀
126건도 통과했다. 이 core는 controlled SQLite fixture이며 `source_state_equivalent=false`,
`full_experiment_acceptance=false`다. 전용 PostgreSQL baseline lease/복구, peer workload 동시 실행,
최종 DB/revision 검증과 공개 runner/API는 아직 연결되지 않아 T4 전체 완료나 성능 결론이 아니다.
NAS 활성 release와 capture 설정은 변경하지 않았다. 상세는
[T4 native lifecycle core](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#t4-native-top20-lifecycle-core--2026-10-07-로컬-부분-검증)를 본다.

2026-10-07 O12 T3 NAS PostgreSQL acceptance 완료: 비활성 후보
`2026.10.07-top20-replay-drain-v1-795caf04dde5e9ce`에서 RAM-backed·network-isolated PostgreSQL
검사 13건 모두 통과(skipped=0). run 소유 worker drain, 취소된 thread의 실제 종료 전 reset 차단,
commit ACK 유실 후 native pending outbox 재시도, DB/file baseline 공동 복구와 v1 snapshot 보존을
확인했다. v1 baseline `56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`,
v2 baseline `36e6ece20620ade0804ca28782fe23340fd9bceb24ce48d991883636e7e97446`.
보고서 `replay-cache-v2-acceptance-ac1d6c7568abcfccedf0547c765c517c.log`.
active release·서버/DB 컨테이너는 유지됐다. controlled fixture 정확성 결과이며 장중 상태 등가나
성능 기준선은 아니다. 다음은 T4 native TOP20 lifecycle runner다.

2026-10-07 O12 T1 request/catalog 입력 로컬 구현: `diagnostic_rest_input.py`가 opt-in 시장 REST의
요청 identity/응답/오류, cache·ingest 및 shared 부모와 catalog 입력을 기록·검증한다. native broker의
캐시·우선순위·저장 handler와 일봉 변경 callback을 보존하는 explicit-lane tape client를 추가했다.
미기록 공유·중간 effect의 원인 변경·missing pair/입력은 거부하며 cache 결과를 전송으로 대체하지 않는다.
관련 REST broker/TOP20/ingestor/input 회귀 135건이 통과했다. fake REST client와 SQLite를 이용한 로컬
검증이며 PostgreSQL, 실제 capture overhead, 전체 lifecycle 또는 NAS acceptance는 아니다. delayed response
재현에서 membership 공개 `saved_at`이 요청 시작 시각으로 남는 결함을 확인해 실제 공개 시각을 쓰도록
보완했고 target slot의 `observed_at`은 유지했다. NAS 활성 release와 녹화 설정은 변경하지 않았다.
전체 TOP20 runner는 계속 미지원이다. T2 입력/descendant plan은 로컬 구현과 관련 회귀 174건을 마쳤고,
T3의 run 소유 task/thread drain과 native 파일 outbox 경계도 로컬 구현했다. native TOP20·broker·collector
관련 회귀 145건 중 142건이 통과했으며 전용 PostgreSQL gate 세 건은 sealed NAS fixture가 없어 건너뛰었다.
상세는
[실험 설계와 gate](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#top20-lifecycle-실행-계약과-구현-순서--2026-10-07-설계-확정)를 본다.

2026-10-07 O12 TOP20 lifecycle replay 후속 설계: native broker/cache/ingestor와 일봉 변경 callback을
유지하고 외부 transport·catalog만 tape로 공급한다. T1과 T2는 로컬 구현 및 관련 회귀 검증을 마쳤다.
T2는 0s·subscription intent·ACK/READY·hub control·gap 입력을 검증하고 현재 native 구독 요청과 일치하는
offline tape/descendant preflight를 제공한다. 가짜 WebSocket/REST와 SQLite 기반 174건이며 PostgreSQL·실제
capture overhead·전체 lifecycle acceptance는 아니다. T3는 opt-in task/thread owner와 실제 `JsonRecordOutbox`
seed/복구 경계를 구현했고 관련 native unit 회귀 142건을 통과했다. 전용 PostgreSQL gate 세 건은 sealed NAS
fixture가 없어 이 PC에서 실행되지 않았다. 기존 RAM-backed NAS acceptance harness에 세 gate를 연결했다.
T3 PostgreSQL acceptance는 위 13건으로 완료했다. native runner는 T4, 유효 실제 입력의 고정 baseline 비교는
T5다. T1의 delayed-response 회귀는 membership
`saved_at`을 실제 공개 시각으로 보정하면서 target slot `observed_at`을 보존한다. 단계별 구현은 계속
로컬에 한정하고 운영/capture·NAS 활성 release는 변경하지 않는다. 상세 계약은
[lifecycle 설계](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#top20-lifecycle-실행-계약과-구현-순서--2026-10-07-설계-확정)를 본다.

2026-10-07 O12 TOP20 source-clock 누수 수정 완료(로컬): `HistoricalHighService.load(as_of=...)`가
fresh/incremental/refinement, KRX/NXT chart 및 250일 evidence에 하나의 날짜를 전달하고 TOP20 caller는
이미 coverage에 쓰는 KST basis 날짜를 넘긴다. `MarketDataIngestor`의 time-only 분봉 날짜 fallback은
같은 ingest 시각을 사용하며 live membership의 `saved_at`도 순위 source 시각에서 계산한다. 신고가 12건,
market ingestor 13건, TOP20 service 67건의 단위 회귀가 통과했다. source 날짜를 2001-04-03으로 고정한
native fake broker/store 재현에서 세 경로 모두 누수 없음으로 확인했다. 재현은 DB·외부 네트워크에 접근하지
않았다. TTL·queue wait·재시도/sleep·duration·reconnect health는 실제 경과시간을 유지한다. 이는 clock
정합성 수정이지 병목 개선이나 전체 TOP20 runner/replay 지원은 아니다. NAS 운영소스와 활성 release는 미변경이다.
상세 판단/소비자는 [실험 설계의 source-clock 감사](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#top20-service-source-clock-감사--2026-10-07)에 있다.

2026-10-07 O12 TOP20 replay v2 offline run PostgreSQL acceptance 완료: 비활성 NAS candidate
`2026.10.07-replay-cache-run-v1-329cb888022f0de9`에서 임시 RAM-backed, network-isolated PostgreSQL
harness가 기존 v1/v2 baseline gate 7건과 새 run gate 3건을 모두 실행해 통과했다(skipped=0).
반복 source-TTL 실행과 v1 snapshot/cache 복구, collector/cache 공통 source clock, 제외 mask의 과거 cache
결과 비주입, COMMIT acknowledgement-loss 뒤 drain 및 baseline 복구를 확인했다. fixture는 controlled이며
`source_state_equivalent=false`다. 임시 baseline v1 `56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`,
v2 `50f49c0274a116d846d0134b4b4379cc96940f539b630e0c7facefbbdbdf2a35`와 임시 cluster는 검사 뒤 제거됐다.
report는 `replay-cache-v2-acceptance-ed1cd3ae0653aff1d8db76f329493bc9.log`이며 active release 및 서버·DB
container는 변경되지 않았다. 이는 PostgreSQL correctness acceptance이지 성능 또는 전체 TOP20 replay
수용이 아니다. 다음은 service 내부의 직접 날짜/벽시계 참조를 조사하고 request-identity REST/catalog 입력,
durable outbox, TOP20 lifecycle runner 경계를 진행하는 것이다. 상세 잔여 범위는 [OPEN_ITEMS](OPEN_ITEMS.md)와
[실험 설계](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)를 본다.
2026-10-07 O12 TOP20 최초 편입 수급 원인 입력 로컬 후보: `ka10045`의 TOP20 최초 편입 경로에서
종목/대상일, marker 상태, `_AL`→일반 code 재시도, 실제 ingestor payload와 저장 확인, 완료 marker
시각을 기록하도록 했다. broker persistence task에서 먼저 실행되는 실제 `MarketDataIngestor.ingest`에
cause ID를 전파하고, offline 경로는 같은 ingestor와 TOP20 consumer를 봉인된 전용 replay DB에서
실행한 뒤 baseline을 복구한다. 과거 descendant는 다시 실행하지 않으며 RAM cache 응답은 baseline에
원본 dataset이 있어야 하고, shared request/불확실한 write/native 오류는 실행을 거부하거나 실패로
남긴다. replay 취소는 baseline read와 native write drain을 기다린다. 신규 fake-client/SQLite 검사
신규 12건을 포함한 TOP20 순위 입력·CentralRestBroker·AutonomousTop20 결합 회귀 108건이 통과했다. 전용 PostgreSQL
반복 재생 및 COMMIT acknowledgement-loss 복구 검사 2건은 추가했으나 PC에 dedicated replay DB URL이
없어 실행하지 못했다. 수정은 로컬 작업트리에만 있으며 NAS source/runtime, trace 예약, 실장 운영은
변경하지 않았다. 상세 경계는
[반복 부하 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)에 기록했다.

2026-10-06 외부 입력 감사 U3 기준정보 freshness 경계 확인: 코드상 TOP20 catalog는 날짜가 바뀌어도 저장된 시장 map을 fallback으로 쓰며, 갱신 실패 때 기존 map이 있으면 해당 날짜를 준비 완료로 표시해 그날 재요청을 막는다. map은 TOP20 rank의 시장 분류에 사용된다. 활성 market-event cohort의 NXT metadata reader는 저장 문서가 있으면 observed_at 거래일을 확인하지 않고 구독에 사용한다. TOP20의 별도 _nxt_enabled()는 일자 freshness를 검사한다. NAS 읽기 전용 확인(2026-10-06, build `2026.10.06-top20-daily-freshness-v2`)에서는 cohort 218종목 중 활성 18종목 모두 오늘 관측 문서가 있었고 stale/missing은 0이었다. 비활성 200종목 중 오래된 문서는 137개였다(전체 cohort 문서는 모두 존재). 따라서 현재 활성 구독이 stale인 증거는 없지만, 비활성 종목이 재진입할 때 이전 `nxt_eligible` 값을 먼저 구독에 반영하고 `_load_metadata()`가 오래된 문서를 유효값처럼 재사용할 수 있는 경로는 남는다. 현재 stale inactive 항목의 재진입 영향은 발생 여부 미확인이다. freshness 재조회만 추가하면 재시작 때 활성 cohort의 TR이 몰릴 수 있으므로 즉시 수정하지 않고 재조회·분산 정책을 별도 검증한다. 종목 catalog의 현재 관측일은 읽기 API가 노출하지 않아 NAS 최신성은 확인하지 못했다.

2026-10-06 외부 입력 감사 U11 DART 페이지 범위 확인(정적 코드): NAS 뉴스 service의 주기 refresh는 fresh TOP20 membership을 기준으로 종목별 `_collect()`를 실행하며 DART 운영 설정과 API key가 모두 활성인 경우 공시 검색도 함께 한다. `DartDisclosureClient.search()`는 최근 30일·페이지당 30건의 첫 페이지에 한정되고 `total_page`를 확인하지 않는다. pagination 함수 `list_disclosures()`는 별도 경로에 있지만 자동 종목뉴스 refresh는 이를 사용하지 않는다. DART 공식 개발가이드의 페이지당 최대 건수는 100이고 응답에 `total_page`가 있으므로 30건을 넘는 회사는 자동 refresh가 일부 공시를 놓칠 가능성이 코드상 존재한다. 다만 실제 NAS 응답의 페이지 수와 초과 종목 수는 기존 계측에 없어 영향 규모는 확인되지 않았다. 과소수집 가능성과 함께 잠재적인 API 호출량 증가를 고려해 pagination을 즉시 추가하지 않고, 기존 또는 최소한의 안전한 page-count 관측으로 실제 범위를 확인한다. 이 내용은 정적 코드 확인이며 현재 NAS 설정/호출 횟수나 실제 누락 건수를 확인한 결과가 아니다.

2026-10-06 외부 입력 감사 U12 뉴스 page-cap 경계 확인(정적 코드): Naver 종목 site source가 켜져 있으면 종목뉴스 refresh에서 최대 3페이지를 조회하고 다음 회차에 overlap 후 이어간다. 그러나 hard limit인 page 100에 도달하면 client는 `complete=false`, `page_limit_reached=true`, `next_page=1`을 반환하고, caller는 pending cursor를 비운 채 최신 게시시각을 확정 cursor로 저장한다. 그 조건에서는 100페이지 뒤의 과거 backlog를 재개하지 못할 가능성이 있다. 당시 확인 가능한 live 설정 snapshot(2026-10-06 18:57 KST)에서는 Naver 종목 site 및 시황 source가 OFF였으므로 이는 실행 증거가 아니라 조건부 코드 위험이다. 현재 켜진 실행의 page-limit 이벤트나 누락 수는 확인되지 않아 수정·수집량 추정은 하지 않았다. 재활성화 시 cursor/`site_last_page_limit_at`와 응답 경계를 검증한다.

2026-10-06 O12 일봉 coverage/신고가 freshness 결함 로컬 수정:
키움 일봉 적재가 실제 canonical 값을 변경했지만 TOP20 일봉·신고가 stage가 당일 완료로 남던
결함을 종목·시장별 generation으로 고쳤다. 변경 key는 SQLite/PostgreSQL UPSERT의 `RETURNING`에서
가져오며 commit 뒤에만 알린다. 저장 예외는 COMMIT 결과 불확실성을 위해 해당 시장만 보수적으로
dirty 처리한다. 순위 polling에서는 DB를 다시 읽지 않고 RAM marker를 비교한다. daily coverage 또는
신고가 계산/게시 중 입력이 바뀌면 성공 marker를 남기지 않는다. 신고가 문서는 KRX 및 대상이면
NXT 일봉 fingerprint와 coverage identity를 저장해 재시작 후에도 당일 오래된 결과를 감지한다.
기본정보·분봉·수급 단계와 독립된 일봉·신고가만 재검증하고 동일 OHLCV 재수신은 timestamp와
완료 상태를 유지한다. 신규 race/rollback/COMMIT acknowledgement-loss/NXT isolation/restart 검사와
기존 daily-history, TOP20, ingest, SQLite store 회귀 총 158건과 핵심 focused 9건이 통과했다.
격리 후보
`2026.10.06-top20-daily-freshness-v2-8fe3781e9c3ba685`를 NAS source-runtime에 비활성 stage했다.
후보 manifest 무결성 검사가 통과했고 파일 891개는 기준 active release와 같았다. stage 직후에는
기존 release가 active였고, 이후 사용자가 v2를 활성화했다. v2 후보에서 일봉
coverage/TOP20/SQLite store/ingest 회귀 158건이 40.986초에
통과했다. NAS 전용 PostgreSQL에서는 일봉 UPSERT 변경 감지·중복/metadata·rollback 검사 3건이
0.303초에 통과했다. 이는 저장 정합성 검증이며 성능 개선량은 측정하지 않았다. 먼저 만든 비활성
v1은 Python cache 80개가 추가된 상태로 남아 있어 v2로 대체했으며 선택·실행하지 않는다.
사용자가 2026-10-06 후보를 활성화했다. `/health`는 `ok`, server_build와 source_release는 각각
`2026.10.06-top20-daily-freshness-v2` 및
`2026.10.06-top20-daily-freshness-v2-8fe3781e9c3ba685`로 일치했고 database container는
unchanged였다. 직후에는 `CONNECTING`이었고 이후 인증 조회는 `WAITING_MARKET`,
`observation_expected=false`를 반환했다. 이는 조회 시각 23:26 KST에 구독 대상 시장 시간이 아니어서
관측을 기대하지 않는 상태와 일치한다. 최신 snapshot key는 ranking `2026-10-06T08:05:00`,
top20_index `2026-10-06T19:59+09:00`, market_state `2026-10-06T15:32`였다. 장중 재연결·체결 수신은
이 장외 표본으로 검증되지 않았다. 정합성 기능 배포 확인이며 운영 성능 개선을 뜻하지 않는다.

2026-10-06 O12 외부시장 일봉 실패 재시도 정합성 수정(로컬 전용): injected Yahoo 1d 요청 실패를
활성 월물 성공·다음 월물 실패로 재현했다. 기존 코드는 5분봉 성공만으로 cycle을 `ok` 처리하고
poll loop의 `_last_daily_date`를 전진시켜 실패한 일봉을 그날 다시 요청하지 않았다. 날짜/월물별
성공 표식을 추가해 이미 저장된 일봉은 반복 fetch하지 않고 실패 월물만 다음 poll에서 재시도한다.
어느 대상에서든 일봉 요청/저장이 실패하면 일봉 날짜를 완료 처리하지 않으며 collection status도
`failed`로 기록한다. 성공한 월물의 성공 표식은 프로세스 메모리에만 있어 재시작하면 재조회한다.
외부시장 collector/runtime 관련 PC 테스트 20건과 retry/date regression 2건에 더해, injected DB
저장 실패 뒤 실패 월물만 재시도하는 검사와 부분 실패 후 새 collector로 재시작해 미완료 일봉을
다시 요청하는 검사를 추가했다. 관련 두 테스트 모듈 22건이 통과했다. 로컬 작업트리만 수정했고
NAS source/runtime은 바꾸지 않았다. Yahoo 실응답과 NAS 후보 실행은 미검증이며 성능 개선량도
측정하지 않았다.

2026-10-07 O12 deferred trace RAM 후보(로컬): 사용자가 정한 4GiB RAM charge와 1,000,000 event
cap을 opt-in `persist_at` 모드에 추가했다. 65분 capture 종료 때 producer token을 닫고 20:10 KST
까지 payload I/O를 보류하며, 이후 64KiB writes를 최대 1MiB/s로 제한하고 chunk fsync 뒤 최대
5초 쉬도록 했다. Linux host/cgroup에 최소 5GiB headroom이 없으면 시작을 거부한다. 이는 실제 RSS
hard cap이 아니고, 이 한 시각의 host 여유 18,077,790,208 bytes도 용량 수용 증거가 아니다.
로컬 후보 검사 46건을 통과한 뒤 활성 NAS 소스 891개를 기준으로 후보
`2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f`(892 files)를 NAS에 비활성 게시했다.
게시 도구가 manifest를 검증했고 active 포인터는 변경되지 않았다. 활성 NAS build는 여전히
`2026.10.06-top20-daily-freshness-v2`이며, NAS 컨테이너 검사와 활성화는 sudo 인증이 없어
수행하지 못했다. 이후 v6 후보의 collector/replay 및 NAS controlled overhead 검증은 통과했지만
장중 성능 acceptance는 미결이다. NAS `active.json`은 배포 전에 deferred-RAM v1을 가리켰으나,
사용자가 2026-10-07 `2026.10.07-trace-market-inputs-v6-2597a00f99c13b50`를 활성화했다.
배포 출력에서 health `ok`, build/release 일치, database container unchanged를 확인했다.
2026-10-07 05:21 KST 공개 health 재조회는 동일 build `ok`, realtime `WAITING_MARKET`,
`observation_expected=false`를 반환했다. 장외 대기 상태로 보이며 이번 조회에서는 실시간 구독·수신을
검증할 수 없다. 인증 capabilities/status는 현재 세션에 진단 API token이 없어 재조회하지 않았다.
10월 8일 08:55 capture 예약은 v6 build와 0B·0w·0J·0U 지원을 필수로
검사하고, 조건 불일치 시 진단을 켜지 않도록 갱신했다. 사후 검증도 v6 build와 각 입력 종류
개수를 구분해 보고하도록 갱신했다. 예약은 해당 종류가 실제 발생하지 않은 경우를 coverage로
간주하지 않으며, 아직 기록하지 않는 TOP20·뉴스·REST 원인 경로는 별도 공백으로 남는다.
상세한 중단·디스크 제한·확인 범위는
[DB trace/replay design](DB_REPLAY_TRACE_DESIGN.md)에 기록했다.

2026-10-06 O12 trace-recorder-v3 운영 확인 및 짧은 API smoke: NAS active release/build는
`2026.10.06-trace-recorder-v3-e8bfc0fa8ce80117` / `2026.10.06-trace-recorder-v3`로
일치한다. 인증 API에서 recorder capabilities schema 2, payload capture 기본값 OFF,
진단 master/trace OFF, paused workload 없음, trace `off`를 확인한 뒤 60초 trace
`20261006T122707Z-ffbb6ed84bbb`를 실행했다. 결과는 complete, schema 2,
store_inputs/collector_inputs=true, accepted=written=588, known_dropped=0,
payload 127개, 15 chunks, 377,003 bytes, input_capture_censored=false였다.
거부 입력은 10건: 명시 제외 `acquire_execution_runtime` 2건, 미지원 `load_documents` 6건,
`save_shadow_monitor_state` 예산 거부 2건. capture는 WAITING_MARKET의 장외 구간이라
0B 원인 입력이 없으며 장초 workload의 완전성·부하·무손실을 검증하지 않는다.
코드상 예산 거부가 byte/node 중 어느 한도였는지와 미지원 collection 이름을 기록하는
안전한 metadata-only 개선을 로컬에 추가했고 recorder/trace/capture 회귀 28건이 통과했다.
이 rejection-detail 변경은 NAS에 배포되지 않았다. 전용 PostgreSQL integration test는
기본 Python에 psycopg가 없어 실행되지 않았으며, 이전 실행의 elevated local-loopback
unit bundle 결과와 구분한다.

2026-10-06 O12 trace recorder NAS 후보 v2 확인(진단기만, 운영 변경 없음): NAS에 별도 복사한
`2026.10.06-trace-recorder-v2-3c8831fa89cf18da`에서 private control과 임시 폴더만 사용하는
검사 4종(scalar, payload batch, busy-copy, grouped)이 모두 통과했고 accepted=written,
known_dropped=0이었다. grouped 19.977초 중 instrumented fsync 합계 19.905초, 단일 최대
10.682초였으며 stop 대기는 최대 10.000초였다. 따라서 이 NAS 시험에서 종료 지연은 recorder 파일
sync I/O와 직접 겹쳤다. 장중 업무 DB/WAL 대기 또는 capture 처리율 전체를 입증한 측정은 아니다.
코드 검사에서는 final manifest를 디스크에 쓰기 전에 메모리 상태를 `complete`로 노출하는 순서도
확인했다. PC에서 manifest 내구성 확인 전까지 `stopping`을 유지하도록 바꾸고, final manifest가
실패하면 `failed`로만 공개하는 회귀 2건을 추가했다. trace drain/manifest 관련 PC 테스트 15건 통과.
후보 `2026.10.06-trace-recorder-v3-e8bfc0fa8ce80117`(891 files)을 NAS에 비활성 stage하고
manifest 파일 checksum 검증을 통과했다. active release는 계속
`2026.10.06-top20-program-drain-v1-b619e0fc91db27cf`이며 NAS v3 회귀는 아직 실행하지 않았다.

2026-10-06 O12 B0 capture 무결성 확인(읽기 전용): trace
`20261005T235952Z-c138934f2486`는 KST 08:59:52~10:05:46 기록으로, 요청한
08:55 시작보다 약 4분 53초 늦게 시작했다. 160개 chunk의 파일 크기·SHA-256·개수·범위는
manifest와 일치하지만 197,333 기록에 sequence 누락 35,251건, payload 입력 거부 97,849건이
있다(주요 거부 사유 capture_memory_full 96,876건). 08:59~10:05 분 단위 중 거부와 누락이
모두 0인 1분도 없어 이 trace는 전체·부분 성능 replay source로 승격하지 않았다. 집계된
event 범위는 operation_start 7,379, operation_end 21,453, collector_input 37,516이다.
trace 시작 당시 source release/build와 instance는 요청값에 일치했다. 현재 NAS 활성 release는
`2026.10.06-top20-program-drain-v1-b619e0fc91db27cf`; 그 코드의 memory limit은 64MiB다.
PC 소스 recorder는 256MiB와 payload bundle fsync를 구현했지만 NAS 비용/RSS와 65분 burst 검증은
미완료다. 해당 PC 코드의 batching·메모리 예산·65분 bounded burst 단위검사 13건은 통과했다.
추가로 PC 별도 프로세스에서 synthetic 0B형 입력 5,000건×3회 OFF/ON을 비교했다. OFF token 경로는
p50 0.1~0.2µs/p95 0.2~0.3µs, ON payload admission은 p50 19.8~31µs/p95 38.2~44.6µs였다.
ON은 매회 5,000건 모두 수용, payload 약 1.64MB, payload fsync 1회, RSS 표본 peak 증가 약 10.4MB,
drain 209~241ms였다. 이는 PC 임시 디스크·합성 입력의 recorder 비용만 측정했으며 NAS IO, DB 처리량,
실시간 수신 지연, 65분 연속 capture 비용을 대표하지 않는다. 전체 캡처 승인으로 일반화하지 않는다.
진단 master·trace·run은 OFF/idle이며 제어 상태나 운영 source는 변경하지 않았다.

2026-10-06 O12 C1 0w 종료 보존 수정(PC 소스): 기존 TOP20 close의 pending 미저장,
실제 DB thread 완료 전 반환, COMMIT 응답 유실 뒤 재시도 누락을 같은 fixture로 재현했다.
프로그램수급 save는 서비스가 단일 owned task로 소유하며 호출자 취소와 분리한다.
종료는 producer를 멈춘 뒤 실제 save를 기다리고 남은 pending을 마지막에 저장한다.
실패 병합은 같은 종목의 새 pending을 우선하며 최종 실패는 pending을 유지하고 예외로 알린다.
서버 소스 build는 `2026.10.06-top20-program-drain-v1`로 올렸다. 신규 7건과 기존 TOP20
회귀를 묶은 74건 통과(23.782초, exit 0). 격리 source-runtime 후보
`2026.10.06-top20-program-drain-v1-b619e0fc91db27cf`(890 files)를 stage했다.
전용 PostgreSQL 수용 검사 2건이 NAS 후보에서 skipped=0으로 통과했다(0.562초).
2026-10-06 사용자 배포 결과에서 active source release와 server build가 각각 후보 ID와
`2026.10.06-top20-program-drain-v1`로 일치했고 health는 `ok`, realtime phase는 `READY`,
database container는 unchanged였다. 이는 정상 재시작 후 health 확인이며 실제 장중 0w 입력
보존·강제 종료·지속 DB 장애·미처리 subscriber queue의 영속 복구나 성능 개선을 뜻하지 않는다.
저장 주기·batch·DB transaction과 UPSERT는 그대로다.
강제 종료·지속 DB 장애·subscriber 큐에서 아직 처리되지 않은 입력의 영속 복구를
새로 보장하는 변경은 아니다.

2026-10-06 O12 A1 숨김 후보 polling(PC 소스): 후보 창이 보이거나 알람이 켜졌을 때만
2초 poll worker가 실행되게 했다. 숨김+알람 OFF에서는 미시작/대기하며 재개 시 missed events는
조용히 catch-up해 이후 신규 이벤트만 알린다. 앱 종료 시 대기 worker를 깨워 종료를 기다린다.
candidate alert·auxiliary-window lifecycle·main-window 회귀 49건 통과. 이는 불필요한 요청 경로를 막는
기능 검증이며 운영 호출량·전체 성능 절감량은 측정하지 않았다. NAS 후보 생성·자동운용은
PC polling과 별개로 유지된다.

2026-10-06 O12 P8 뉴스창 숨김 lifecycle(PC 소스): 뉴스 전용 child process는 창을 닫아도
메인 앱의 재사용 명령을 기다리며 살아 있으나, 종목 뉴스 60초 준비 timer가 standalone child의
close 경로에서 계속 실행되는 것을 확인했다. close 시 parent 유무와 관계없이 timer를 멈추고,
같은 창 재열기 때 timer를 재개하며 선택 종목의 저장 자료를 즉시 다시 준비한다. 이미 시작된
기사 수집·저장은 건드리지 않고, 별도 P9 중앙 콘텐츠 동기화도 계속 유지한다. stock-news,
news-process, coordinator 회귀 33건 통과. 실제 외부 요청 수나 DB/CPU 절감량은 측정하지 않았다.
P9는 창이 닫혀도 주기 유지가 필요하다: 실패한 기사/AI/매매일지 링크 push를 재시도하고 중앙 변경분을
PC cache로 가져오며, `EntrySnapshotWriter`는 그 로컬 뉴스 DB를 체결 시점 뉴스 snapshot에 사용한다.
닫힌 동안의 delta pull 호출량은 미측정이라 주기 축소를 결정하지 않았다.

2026-10-06 O12 앱 전체 감사: [부하 원장](WHOLE_APP_LOAD_OPTIMIZATION_REVIEW.md)과
[외부 입력 lifecycle](EXTERNAL_INPUT_LIFECYCLE_AUDIT.md)에 PC/NAS 소비자·시작/종료·
과수집·갱신 부족을 정리했다. 468개 Python 파일의 lifecycle 후보 구문 검색은 parse error 0;
이를 실제 활성 collector 수로 해석하지 않는다. 18:57 KST 기존 API 확인은
`2026.10.06-recorded-capture-api-v1`이며 DART·외부시장·Shadow·조건검색 ON, Naver 수집 옵션 OFF다.
기존 access/TR 로그·불완전 trace는 경로 실행 증거로만 사용하고 전체 성능 baseline으로 쓰지 않았다.
코드상 명백한 반복 작업, 실측까지 보류할 구조 후보, 별도 정합성/복구 문제를 분리했다.
제품 코드·NAS 활성 릴리즈·설정·진단 제어는 이번 감사에서 변경하지 않았다. 주기/batch/concurrency
선정과 성능 개선 확인은 정상 capture→현재 코드 baseline→한 변경씩 동일 replay 순서로 남긴다.

2026-10-06 O12 trace 보존 비용 개선(PC 소스): payload마다 JSON 파일/fsync를 만들던
경로를 청크별 최대 16MiB `.payloads` 묶음과 묶음당 한 번 fsync로 변경했다. hash별
name/offset/bytes 참조와 checksum으로 원래 입력을 읽으며 기존 payload JSON도 호환한다.
payload 묶음·이벤트 청크 저장 뒤에만 참조를 공개하고 실패/종료 중 입력·미확정 suffix의
순서와 누락 판정을 유지한다. 보수적 메모리 charge 예산은 64MiB→256MiB로 상향했다
(worker 예약 제외 queue/pending/copy 최대 48MiB→240MiB). worker 깨우기는 기존
16MiB/4,096 queued events와 최대 5초를 유지한다. 새 status/manifest는 fsync 횟수,
파일 형식, 메모리·묶음 예산을 기록한다. 로컬 64개 unique payload burst 검사에서
payload fsync 2회와 전 입력/sequence/charge 정리를 검증했다. NAS 활성 소스는 아직
변경하지 않았으며 실제 NAS ON/OFF 부하·RSS 및 65분 무손실 수용은 남아 있다.

2026-10-06 O12 trace 입력 API 연결: 인증된 trace 시작 요청에 엄격한 boolean
`store_inputs`와 `collector_inputs`를 추가했다(기본 OFF). 둘 중 하나를 켜면 schema-2로
기록하며, store 공개 호출 입력과 실제 관측 0B collector 사건은 기존 allowlist·민감정보
제외 경계를 그대로 사용한다. capabilities는 이 기능의 schema, 기본값, 0B 범위와
`overhead_verified=false`를 알린다. API·trace·capture 관련 로컬 회귀 33건이 통과했다.
후보 `2026.10.06-recorded-capture-api-v1-af7d3d49b61aad09`(888 files)를 NAS source-runtime에
stage한 뒤 사용자가 활성화했다. `/health`와 인증 capabilities API에서 server/capabilities build와
source release가 일치하며 schema 2, 0B 범위, 두 기본 OFF 옵션 및 미측정 overhead를 확인했다.
진단 master/trace는 OFF다. 실시간 상태는 장외 `WAITING_MARKET`, observation 미예정이다.
배포 출력에서 DB 컨테이너가 바뀌지 않았음을 확인했다. 08:54 KST capture와 10:01 검증 예약은
schema-2 지원/build preflight 및 두 opt-in 전달, manifest/chunk·입력 event 수 검증을 하도록
갱신했다. 실제 65분 보존과 진단 오버헤드는 미검증이다.

2026-10-06 O12 선택 재생 실행기를 전용 PostgreSQL에 연결했다. offline CLI가 checksummed
schema-2 capture의 bounded window를 읽고 선택한 native store operation을 workload별로 실행하며,
각 원본 operation ID → replay operation ID → 실제 관측 DB call ID를 연결한다. 보고서의 DB-call
통계 범위는 replay 프로세스의 관측 connection이고 WAL은 transaction별 귀속 불가로 표시한다.
실행 전에 미지원 projection을 거부하고, 성공·실패 뒤 owned connection drain 후 sealed baseline을
복구한다. 특히 COMMIT 응답 유실 시 replay 전용 connection을 닫아 복구가 무한 대기하지 않게 했다.
관련 로컬 회귀 47건 통과. NAS 후보 `2026.10.06-recorded-replay-execution-v1-624aade862f1b298`
(887 files)를 X: source-runtime에 stage했다. 후보 전용 검사 스크립트
`X:\kiwoom-monitor\artifacts\check-recorded-replay-execution-v1.sh`도 SHA-256
`4fa890b00abe4ba7561f888f9437ea5bc4e8542e93beb6e154078f5024f03088`로 복사 검증했다.
이 스크립트는 후보 helper의 PostgreSQL gate 2건만 실행하고 active pointer와 server/database
container ID가 그대로인지 확인했다. NAS 결과는 2/2 통과, skipped=0, 9.595초이며 baseline ID는
기존 봉인값과 같다. 마지막 `Acceptance verified` 출력까지 확인했다. 운영 source/active release는
바뀌지 않았다. controlled capture fixture는 실장중 시작 상태와 같지 않아 동등 재현을 뜻하지 않는다.

2026-10-06 O12 replay baseline PostgreSQL acceptance 완료: NAS operator helper가 고정 전용
role/database에 controlled fixture baseline을 봉인하고 4개 PostgreSQL gate를 실행했다.
4/4 통과, skipped=0, 2.787초. baseline ID는
`4c4daa238a7e7d4221234087dce35a5b0956caf6629b05896fca7e8df175bbd4`다.
복구·sequence·rollback·외부 연결 차단 경계가 검증됐다. fixture는 실제 장중 capture 시작
상태와 동등하지 않으므로 `source_state_equivalent=false`다. helper 실행은 active pointer를
바꾸지 않았으며 이 검증 결과만으로 NAS 운영 릴리즈가 갱신된 것은 아니다.

검증 후보 `2026.10.06-recorded-replay-baseline-v1-c2bdaa4345836df6`(886 files)을
X: source-runtime에 stage했다. 현재 active는
`2026.10.05-realtime-minute-live-view-v2-ea54fd68a086689b`로 유지된다.

2026-10-05 O12 후속: 내부 recorded-operation executor와 `collector_with_background` 실행을
로컬에 연결했다. store allowlist를 actor별 순서·actor 간 동시성으로 호출하고 원본 operation
ID와 새 replay ID, 원본 DB call 연결, scheduler/actor/concurrency 대기와 결과 digest를 남긴다.
collector mode는 선택된 0B/source approval/gap을 기존 parser·accumulator·flush loop에 넣고
그 component의 과거 sink 호출만 제외한다. 진행 중인 DB 호출과 종료 flush는 취소 뒤에도
끝날 때까지 기다린다. 관련 executor/capture/collector 회귀 29건 통과. caller-owned test store
내부 실행이며 전용 replay DB baseline/소유권/run lock, 미지원 operation reference adapter,
공개 run/비교 API는 미구현이다. `baseline_managed=false`, `public_execution_ready=false`,
`source_state_equivalent=false`; NAS는 변경하지 않았다.

2026-10-05 O12 replay baseline 후속: 고정 replay DB 소유권/run lock, immutable logical
baseline seal/restore, 14개 table과 accepted-sequence next value 검증, native per-call
connection drain fence와 offline CLI를 로컬 구현했다. CLI·lease·executor·capture·collector·
DB access audit unit regression 50건 통과. 실제 PostgreSQL rollback·
sequence·외부 연결 acceptance 4건은 `kiwoom_monitor_replay_test`와 role이 준비되지 않아
skip됐으며, 실제 DB 안전성은 아직 확인되지 않았다. 공개 API·운영 재생에는 연결하지 않았다.

2026-10-05 장중 사건 반복 실험 capture 핵심을 로컬 구현했다. opt-in schema-2 trace가
허용된 native store public call의 인수, async owner/actor/component, DB call 연결과 실제
collector의 0B·구독 승인·gap 사건을 bounded immutable payload로 기록한다. plan compiler는
전체/단독/제외/조합 workload를 검증하고, `collector_with_background`에서 같은 collector가
만든 과거 sink 호출만 제외하며 다른 producer의 같은 method 호출은 보존한다. 혼합 최신값,
누락·중복 sequence, censored operation, 미지원/잘린 입력은 선택 plan에서 거부한다.
관련 로컬 capture/trace/PostgreSQL access/collector 80건과 뉴스·TOP20·shadow·REST loop
회귀 111건이 통과했다. bounded window reader와 새 capture의 전용 PostgreSQL gate는
후속 단계에서 추가 검증했다. **내부 실행기까지 로컬 구현:** caller-owned test store에서만
실행한다. 동일 baseline 복구, 안전한 run/비교 API 및 운영 overhead 검증은 남았다.
공개 plan은 계속 `execution_ready=false`; NAS 운영 capture는 하지 않았다. 자세한 경계는
[반복 부하 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)에 기록했다.

2026-10-05 O12 다음 단계: bounded window reader를 로컬 구현했다. reader는 capture 전체
chunk checksum·sequence·manifest 경계를 검증하면서 선택 구간의 operation payload만 읽고,
collector 재생은 지정 collector의 capture-relative prefix를 최대 15분까지만 읽는다.
선택 구간이 실제 capture 완료 시각을 넘으면 거부한다. payload 로드 한도는 32MiB다.
관련 단위검사 17건 통과; Windows sandbox의 asyncio Proactor socketpair 대기 때문에
collector async 통합 단위검사 2건은 이 실행에서 제외했다. 실제 collector→DB PostgreSQL
회귀 7건은 직전 v3 candidate에서 통과했지만 이 reader 변경은 해당 candidate에 포함되지 않았다.
**아직 plan-only:** replay runner/API, baseline 복구, window 선택과 재생의 end-to-end 연결,
65분 NAS 보존·overhead 및 장중 capture acceptance는 남아 있다. NAS active release는 변경하지 않았다.

같은 capture 소스의 전용 PostgreSQL gate 4건과 기존 collector→DB 2건을 후보
`2026.10.05-recorded-capture-gate-v1-fdec04c4475dc1ef`에서 실행했다. 4건은 통과했으나
capture ON/OFF 검사가 의도된 schema-1 trace를 schema-2 전용 replay reader에 넘겨 실패했고,
기존 collector rollback 검사는 현재 key lookup 경계에 맞지 않는 실패 입력을 사용해
rollback을 실제로 일으키지 못했다. 두 테스트 원인을 고쳐 새 후보
`2026.10.05-recorded-capture-gate-v2-81fc0b5e7f04dbcf`(877 files)를 stage했다.
stage는 각 파일 checksum/import를 확인했고 active 포인터는 기존
`2026.10.05-realtime-minute-live-view-v2-ea54fd68a086689b`에 그대로다. 활성화·배포는 하지 않았다.
수정한 두 묶음 게이트는 새 후보에서 재실행 대기 중이다. 이 샌드박스의 Windows asyncio
socketpair 생성 제한으로 비동기 로컬 단위검사를 끝까지 재검증하지 못했으며, 동기 단위검사
2건·문법 확인은 통과했다.

2026-10-05 후속 NAS gate에서 collector rollback/commit-ack 검사가 계속 실패한 원인을 확인했다.
분봉 metadata 연결이 분 시각만을 key로 사용해 같은 분에 여러 종목이 있으면 observation이
서로 덮이는 것을 원인으로 확인했다. SQLite/PostgreSQL writer 모두 `(subject, minute)`로
metadata를 찾도록 수정하고 같은 분 다종목 분리 검사를 추가했다. 로컬 SQLite 회귀 1건,
py_compile 및 `git diff --check` 통과. 불변 후보
`2026.10.05-recorded-capture-gate-v3-ed9daa478d48ac35`(877 files)를 stage하고 manifest
checksum/import/build marker를 확인했다. NAS 전용 PostgreSQL gate에서 capture 4건, collector
2건 및 다종목 metadata 회귀 1건이 7/7 통과했다(9.109초). 후보는 검사만 했고 active release는
`2026.10.05-realtime-minute-live-view-v2-ea54fd68a086689b` 그대로다.

2026-10-05 중앙 실시간 봉의 DB 저장 시점을 분리했다. 열린 분봉 delta는 RAM에 보관하고
종료+2초에 저장한 다음 기존 확정 처리를 유지한다. 초봉은 매분 45초에 이전 분까지
저장한다(10:04분 초봉은 10:05:45). 정상 종료·인증 전환은 전량 flush하고 실패분은
다음 관리 주기에 재시도한다. 수신·집계·허브 전달과 다른 writer 주기는 유지한다.
앱 표시용 두 분봉 API는 저장 이력과 미저장 RAM delta를 함께 반환해 신규 접속·편입에도
현재 분 prefix를 복원한다. 저장 중·실패 재시도 operation ID는 같은 읽기 snapshot에서
확인하여 중복 합산을 방지하고 완료된 ka10080 봉·장후 완료 coverage는 보존한다.
관련 로컬 회귀 57건이 통과했다. 최초 NAS 후보 v1은 collector 저장 주기 함수가 빠져 테스트
import 단계에서 중단됐고 배포되지 않았다. 수정한 v2 불변 스냅샷
`2026.10.05-realtime-minute-live-view-v2-ea54fd68a086689b`(870 files)은 체크섬과 실제
import 경로를 확인했으며, 그 스냅샷 자체에서 같은 로컬 회귀 57건이 통과했다.
PostgreSQL을 포함한 NAS 묶음 검사 83건이 59.905초에 통과했고, 사용자 배포 출력과
후속 API에서 v2 실행을 확인했다(HTTP 준비 15초, DB 컨테이너 유지).
DB writer replay `20261005T100452Z-80ed9651`은 245건을 오류 없이 완료했다.
입력 일정의 최대 지연은 약 394ms로 timing_preserved=false이며, 이 방식은 collector를
거치지 않아 변경한 저장 주기의 부하 감소를 검증한 결과가 아니다.
collector 입력부터 scheduler와 DB까지의 통합 replay는 고정 0B fixture 기반으로
로컬 핵심 경로를 구현했다. 실제 parser·RAM 집계·저장 loop 및 취소/종료 검사는 통과했으나
새 PostgreSQL gate 2건, API 연결과 NAS 1배속 성능 측정은 남아 있다. 입력 capture의 이후
로컬 구현 상태는 이 문서 첫 항목을 따른다.
구현 경계와 검증 순서는 [통합 재현 설계](NAS_RUNTIME_DIAGNOSTICS.md#collector-replay-design)에 기록했다.

2026-10-05 실계좌 자동 복구 조회를 계좌 이벤트 중심으로 조정했다. 운영 시간은 평일
08:00~20:00이며, 장전 08:00:15와 장후 20:05:15에 각각 확인하고 운영 중에는
최근 조회로부터 약 5분 뒤의 `:15` 안전 조회를 유지한다. 주문·잔고 이벤트는 즉시 전체 계좌
복구를 깨우고, 운영 시간 밖의 WebSocket 연결·해제 이벤트만으로는 재조회하지 않는다.
초기 기동 복구와 실패 후 제한 재시도는 유지한다. 주문·잔고·현금이 하나의 복구
스냅샷이므로 다섯 REST 응답 중 일부만 합치는 방식은 적용하지 않았다. 로컬 관련
회귀 44건이 통과했으며 NAS active release·실제 키움 호출량은 아직 검증하지 않았다.

2026-10-05 QueryStore 계약 첫 축소: REST 캐시 소비자에 `QueryCacheStore`의 두 메서드를
적용하고, 기존 99개 aggregate 계약을 보존하도록 source 감사기를 확장했다. NAS 후보의
PostgreSQL replay/reader, broker cache period·in-flight merge, SQLite round-trip/expiry 검사는
5/5 통과했다. 소비자 재평가에서 추가로 좁힐 독립 경계는 확인하지 않아 aggregate는 유지한다.
세부 근거는 [검토 기록](db_refactoring/query_store_protocol_review.md)에 있다. 이 변경은 현재
워크트리 소스이며 NAS active release에는 아직 반영하지 않았다.

후속 API 확인에서 첫 실행 후보 ZIP에 테스트 모듈이 빠져 import 오류가 났다. ASGI 요청으로
SQLite-backed `/api/v1/market/coverage`를 확인하는 test-only v2 후보를 만들었고 NAS Linux에서
1/1 통과했다(1.082초). 이는 API-to-SQLite 호출 연결을 확인하며 운영 배포를 뜻하지 않는다.

2026-10-05 `database.py` 잔여 책임도 확인했다. top-level에는 `QueryStore` aggregate,
SQLite/PostgreSQL 조립 class, factory가 남으며 backend 연결·초기화·종료를 직접 소유한다.
이 경계를 더 옮기면 별도 DB 책임 대신 위임 계층만 생겨 현재 유지하기로 했다. 세부 근거는
[DB 분리 기록](db_refactoring/domain_decomposition.md)에 있다.

전체 정적 연결 감사도 통과했다: QueryStore consumer 338곳·forwarding 46곳·signature 변경 0,
PostgreSQL 연결 승인 42/42, 저장 경로 baseline과 domain relocation을 정규화한 API ID 및 writer/table
identity 일치. runtime 또는 PC 화면의 종단 간 성공을 의미하지 않는다.

분리된 DB/helper 모듈 24개의 import 방향도 AST로 검사했다. application·presentation·API route·
broker/service/collector로 향하는 import는 발견되지 않았다. 동적 import와 실행 중 plugin 경로는
이 정적 검사 범위 밖이다.

2026-10-05 DB 책임 분리 스물한 번째 도메인 `과거 뉴스 외부 처리·시황 batch` 완료. SQLite/PostgreSQL QueryStore 구현 3개씩을 `database_historical_news.py`로 이동하고 caller-owned DB cursor/connection을 받는 batch-page와 completion helper 2개도 함께 옮겼다. 기존 BODY/RULE lease 및 retry/ownership fence, claim의 `FOR UPDATE ... SKIP LOCKED`, article revision·job transaction, batch run idempotency, post-commit wake-up을 보존했다. 관련 로컬 회귀 123건과 NAS 전용 PostgreSQL gate 5/5가 통과했다(1.728초). 첫 NAS gate의 실패 주입이 이전 `database.py` import alias를 패치해 분리된 module helper 호출을 놓친 것을 확인하고 테스트 patch target을 수정한 v2에서 rollback 검증도 통과했다. 후보 `kiwoom-db-historical-news-split-pilot-v2-20261005.zip` SHA-256은 `D5F3A56A4165F2F89744E020EADD7E2210B6AFAB9F3AD6D073028142CD2FFC85`이다. 운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-05 DB 책임 분리 스무 번째 도메인 `뉴스 소스 페이지·진행상태·저장 피드` 완료. SQLite/PostgreSQL 구현 8개와 source-page/feed/diagnostics helper 17개, cursor field 상수 1개를 `database_news_sources.py`로 이동했다. QueryStore 99개 계약과 backend 조립, 기존 `database` private import alias, 페이지 안의 기사·target·revision/job/cursor 단일 transaction, PostgreSQL revision table lock 및 commit 뒤 worker wake-up을 보존했다. method/helper AST 대조에서 계약 본문 변경과 미해결 module global은 0이고 consumer 338곳·PostgreSQL 승인 연결 42/42·storage path가 그대로다. 수집기·저장 피드 client·market news window·API와 뉴스 job/이력 관련 로컬 회귀 76건 및 NAS 전용 PostgreSQL gate 5/5가 통과했다(1.906초). 후보 모듈이 ZIP 내부에서 로드된 것도 확인했다. 후보 ZIP `kiwoom-db-news-sources-split-pilot-v1-20261005.zip` SHA-256은 `6B6B13646FFE1BB85434C7483EEEF588EB2031950EFA3B5E87A787D2002F8ACF`이다. 운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-05 DB 책임 분리 열아홉 번째 도메인 `뉴스 revision 저장·조회` 완료. SQLite/PostgreSQL 구현 20개와 뉴스 history/article helper 19개를 `database_news_revisions.py`로 옮겼다. `SUPPLY_CONTRACT_RULE_VERSION`은 하위 `domain.news_observation` 계약으로 이동했고 기존 `application.news_rules`와 `database` 경로는 같은 값을 참조한다. BODY revision과 RULE 작업 enqueue, AI projection/revision/usage, event/membership revision의 기존 transaction·lock 경계를 유지했다. AST method body/signature 대조, import 소유권, consumer 338곳, PostgreSQL 연결 승인 42곳, storage path 정적 비교가 통과했고 관련 로컬 회귀 135건과 NAS 전용 PostgreSQL gate 9/9가 통과했다(2.793초). 후보 `kiwoom-db-news-revisions-split-pilot-v1-20261005.zip` SHA-256은 `A9346788F742E503FD87CA3657B7E86CFFEE9F3FA7FD9E3FEB23D126914E0A10`이다. 운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-05 DB 책임 분리 열여덟 번째 도메인 `뉴스 작업 큐·요청 예산` 완료. SQLite/PostgreSQL 구현 16개와 claim SQL/helper 6개를 `database_news_jobs.py`로 옮겼고, 문서 저장 경로와 같은 caller-owned cursor에서 사용하는 job insert·wakeup helper는 하위 모듈 `database_news_job_writes.py`로 이동했다. QueryStore 99개 계약, store 조립, 이전 import alias, transaction·lock·retry·request-budget·post-commit wake-up 의미를 유지했다. 변경 후 뉴스·기사·소스·관측 회귀 125건과 NAS 전용 PostgreSQL gate 10/10이 통과했다(3.165초). 후보 모듈이 ZIP 내부 경로에서 로드됐고 consumer 338곳·PostgreSQL 연결 승인 42/42에도 차이가 없다. 후보 ZIP SHA-256은 `344871AC40F47B3DE31E3D5AECD4CC97158779B0E04278772309FDB03063C2A6`이다. 운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-05 DB 책임 분리 열일곱 번째 도메인 `시장 관측 메타데이터` 완료: SQLite/PostgreSQL metadata 저장·단건·범위 조회 6개와 row helper를 `database_market_metadata.py`로 이동했다. `CoverageObservation`은 domain value contract로 이동하고 기존 application/database import가 동일 class를 가리킨다. QueryStore 99개 계약과 SQLite 103/PostgreSQL 104개 구현의 AST signature/body가 보존됐다. 로컬 SQLite·ingestor·coverage API 22건이 통과했고 consumer 338곳·PostgreSQL connection approval 42/42에 변화가 없다. 후보 `kiwoom-db-market-metadata-split-pilot-v2-20261005.zip`의 ZIP 실행기와 candidate module 경로를 확인했다(SHA-256 `B48041EEEF90793F3DE647D23DA09174A1CB2078BA1CC443F64B62BD7515CD16`). NAS 전용 PostgreSQL gate 3/3 통과 (0.861초)이며 운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-04 DB 책임 분리 열여섯 번째 도메인 `국내 시장 봉` 완료: SQLite/PostgreSQL 분봉·초봉·5분봉·일봉 저장/조회와 bar helper를 `database_market_bars.py`로 이동했다. 공통 wait probe는 뉴스 claim도 사용하므로 `postgres_access.py`에서 공유한다. QueryStore/API 계약과 호출 경로, transaction·lock·revision·metadata 의미는 유지했다. 구현 method/helper 20개 AST hash가 기존 inventory와 일치한다. 로컬 DB/계측 77건, PostgreSQL 접근 관련 검사 4건, 소스·정적 소비자 검사 3건이 통과했고, 정적 소비자 338개와 승인 PostgreSQL 연결 42개에 추가·삭제가 없다. NAS 전용 PostgreSQL gate 9/9가 통과했다(12.607초). 후보 `kiwoom-db-market-bars-split-pilot-v1-20261004.zip`을 검증했으며 운영 DB·NAS active release·image는 변경하지 않았다.

2026-10-04 DB 책임 분리 열다섯 번째 도메인 `dataset snapshot` 완료: SQLite/PostgreSQL snapshot 저장·조회 6개와 `database_observation_writes.py`의 metadata/revision cursor helper 6개를 분리했고 대기 probe helper 2개는 `postgres_access.py`로 옮겼다. QueryStore·root 호환 이름, logger 이름, transaction/lock/revision 코드 본문은 보존했다. 이동 대상 AST hash 18개가 기준선과 일치하고 PostgreSQL 연결 승인 42/42, QueryStore 계약/호출 지점 변경 없음, helper-only 2단계 import 정적 감사가 통과했다. SQLite store 57건과 market ingestor 12건, NAS 전용 PostgreSQL gate 9/9(4.973초)가 통과했다. Windows sandbox의 asyncio event-loop socketpair 초기화 대기로 TOP20 async·실제 API 연결 검증은 미확인이다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

2026-10-04 DB 책임 분리 열세 번째 도메인 `실행 원장·mock 제어·lease` 완료: SQLite/PostgreSQL 메서드 12개씩과 helper 3개를 `database_execution.py`로 이동했다. QueryStore 계약, 독립 연결, SQLite lock/BEGIN IMMEDIATE, PostgreSQL advisory/row lock·observed transaction과 ownership fence 구현 본문을 보존했다. 소비자 338곳·계약 변경 없음·PostgreSQL 승인 연결 42/42의 정적 대조와 로컬 회귀 115건이 통과했다. NAS 전용 PostgreSQL fence·ledger·control CAS 검사가 3/3 통과했다(2.049초). 운영 DB·NAS active release·이미지는 변경하지 않았다.

2026-10-04 DB 책임 분리 열네 번째 도메인 `shadow 상태·평가` 완료: SQLite/PostgreSQL QueryStore 구현 4개씩과 helper 4개를 `database_shadow_state.py`로 옮겼다. `shadow_checkpoint.py`의 frame format·schema·caller-owned cursor helper를 유지하고 연결·transaction·migration 경계를 보존했다. AST/QueryStore·consumer 정적 대조와 CandidateMonitor·SQLite·관측 경계·인증 API 관련 로컬 검사 11건, NAS 전용 checkpoint replay/rollback/concurrency 검사 7건이 모두 통과했다(4.979초). 운영 DB·NAS active release·이미지는 변경하지 않았다. 상세는 [DB 도메인 분해](db_refactoring/domain_decomposition.md)에 기록한다.

2026-10-04 DB 책임 분리 열두 번째 도메인 `자격증명 프로필·활성화` 완료: SQLite/PostgreSQL 구현 16개와 helper 10개를 `database_credentials.py`로 이동했다. provider field 목록은 하위 공통 계약으로 옮기고 기존 `credential_store.PROVIDER_FIELDS` 이름에서 같은 객체를 재노출했다. activation의 계좌 binding·설정 cursor/transaction, API/client 계약과 vault 파일 commit 경계를 보존했다. 정적 AST·consumer/SQL 대조와 로컬 자격증명/API/client/owner 회귀 196건 중 194 통과, 1 skip, 1 기존 실패였다. 실패한 DART API 기대값은 분리 전 코드에서도 동일했고 현재 read-only search 경로에서 재현돼 별도 보류했다. NAS 전용 진단 DB 검사 4/4가 통과했다(4.146초). 운영 DB·NAS active release·이미지는 변경하지 않았다. 상세는 [DB 도메인 분해](db_refactoring/domain_decomposition.md)를 따른다.

2026-10-04 DB 책임 분리 열한 번째 도메인 `계좌 설정·실계좌 복구·이벤트` 완료: SQLite/PostgreSQL 구현 12개와 helper 9개를 `database_account_settings.py`로 이동했다. QueryStore·API/client 계약, CAS/fence/revision과 connection·transaction·lock 소유권을 유지했고, 정적 연결/SQL 대조 및 로컬 회귀 81건이 통과했다. 전용 PostgreSQL 설정 CAS, real event/recovery fence·분리 COMMIT, credential activation replay·rollback 검사가 3/3 통과했다(6.665초). 후보 실행기가 ZIP 내부 모듈 경로를 확인했다. 운영 DB·NAS active release·이미지는 변경하지 않았다. 다음 책임 경계는 [DB 도메인 분해](db_refactoring/domain_decomposition.md)를 따른다.

2026-10-04 DB 책임 분리 열 번째 도메인 `account identity/binding/scope alias` 완료: SQLite/PostgreSQL 구현 10개와 공통 helper 13개를 `database_account_identity.py`로 이동했다. QueryStore·API/client 계약과 credential activation transaction 연결을 유지했고, 정적 연결·저장 경계 대조 및 로컬 회귀 42건이 통과했다. 전용 PostgreSQL binding/revision, alias rollback·peer isolation, credential activation replay/rollback 검사가 3/3 통과했다(2.473초). 후보 실행기가 새 모듈 경로를 확인했다. 운영 DB·NAS active release·이미지는 변경하지 않았다. 상세는 [DB 도메인 분해](db_refactoring/domain_decomposition.md)를 따른다.

2026-10-04 DB 책임 분리 아홉 번째 도메인 `observation metadata/revision readers` 완료: SQLite/PostgreSQL reader 6개와 helper 1개를 `database_observation_readers.py`로 이동했다. 정적 AST·consumer·SQL/table 대조 및 SQLite 회귀 24건, 전용 PostgreSQL reader/available_at 검사 2건(0.775초)이 통과했다. mock runner 비동기 재시작은 Windows 환경에서 미확인이다. 운영 DB·NAS active release·이미지는 변경하지 않았다.
2026-10-04 DB 책임 분리 여덟 번째 도메인 `TOP20 statistics` 완료: 통계 조회 구현 2개와 집계·날짜·캐시 helper 6개를 `database_top20_statistics.py`로 옮겼다. 원본 dataset snapshot writer는 metadata/revision 및 캐시 무효화가 같은 transaction을 쓰므로 기존 위치에 남겼다. 이동 AST와 남은 root 구현, QueryStore Protocol, 소비자 338곳, PostgreSQL 연결 승인 42/42 및 논리 SQL/table/helper 목록의 전후 차이는 0이다. SQLite 통계·캐시와 PC remote client 검사 2건, 전용 PostgreSQL 캐시 원자성·warm/cold/concurrent read 검사 2건(3.184초)이 통과했다. 후보 ZIP 실행에서 실제 새 모듈 경로를 확인했다. Windows sandbox의 TestClient/ASGI endpoint 검증은 응답 전에 멈춰 runtime 결과는 미확인이다. 운영 DB·NAS active release·운영 이미지는 변경하지 않았다.
2026-10-04 MainWindow → AppController 단계 A-D 로컬 구현 완료. 앱 자원·종료, API runtime 교체, 순위/구독/보완 순서와 가격·분봉·지수·비교 pending 저장 수명을 이전했다. 실제 UI 상태와 TOP20 collector/snapshot 계산 책임은 원래 담당자에 유지했다. 정적 연결 116건 중 99건은 MainWindow에 남고 17건은 AppController로 이동했으며, UI/control 연결 17건을 추가했다. offscreen Qt 관련 19개 모듈 142건이 현재 worktree 소스를 사용해 통과했다(exit code 0). 지연 체결이 종료 대기 중 처리된 뒤 임시 SQLite에 현재가·분봉이 저장되는 연결도 확인했다. 전체 테스트 앱 수동 흐름과 장중 동작, NAS 반영은 확인하지 않았다. 커밋하지 않았다. 상세는 [AppController 계획](MAIN_WINDOW_APP_CONTROLLER_PLAN.md), [변경 전 연결 목록](MAIN_WINDOW_CONNECTION_BASELINE.json), [전후 연결 대조](MAIN_WINDOW_CONNECTION_COMPARISON.json).

2026-10-04 DB 책임 분리 네 번째 도메인 `realtime snapshot` 완료: SQLite/PostgreSQL 세 계약을 `database_realtime_snapshot.py`로 이동하고 collector/API/WebSocket 연결과 기존 호출 경계를 보존했다. 전용 NAS PostgreSQL 검사에서 rollback/peer 보존과 realtime writer replay/lineage 2건이 통과했다(3.294초). 테스트 관측 계약에 맞춰 첫 SQL statement 실패 시 transaction 수는 unknown으로 두고 rollback 수로 확인한다. 운영 DB·NAS active release·이미지는 변경하지 않았다. 도메인 기록은 [DB 도메인 분해](db_refactoring/domain_decomposition.md)를 따른다.

2026-10-04 DB 책임 분리 다섯 번째 도메인 `market events` 완료: VI revision, hot-cohort revision/current, 상한가 사실 revision, market-event history의 SQLite/PostgreSQL 구현과 해당 순수 변환 helper를 `database_market_events.py`로 이동했다. QueryStore 계약, store 상속 조립, 호출자의 transaction 및 SQLite lock 경계는 유지한다. AST method/helper 본문 대조, consumer 338개·Protocol/backend 계약, PostgreSQL 직접 연결 승인 42개, write table 경계 SQL 감사가 모두 차이 없이 통과했다. Python 구문 컴파일과 비동기 없는 로컬 parser/중복 identity 검사 2건 및 SQLite VI 저장→이력 조회 smoke가 통과했다. 전체 비동기 market-event 단위 묶음은 이 Windows 실행에서 event-loop socketpair 단계에 멈춰 통과로 계산하지 않는다. 후보 PostgreSQL ZIP `kiwoom-db-market-events-split-pilot-v1-20261004.zip`을 만들고 X 공유에 SHA-256 `D717D4EB19EF26C7F67A26799577B93F482D786E94C989B6467FA586B20265F2`로 스테이징했다. 후보 ZIP에서 NAS 전용 PostgreSQL 검사 5건이 통과했다(2.133초). 운영 DB·NAS active release·이미지는 변경하지 않았다. 상세는 [DB 도메인 분해](db_refactoring/domain_decomposition.md)를 따른다.

# 현재 앱과 검증 상태

2026-10-07 O12 collector 시장 입력 확장 로컬 구현: `collector-input/v2`가 0w·0J·0U 최소 FID를 0B와 함께 보존하고 실제 collector에서 재생한다. 같은 component의 market_state-only history 저장을 대체하며 peer와 다른 dataset은 유지한다. 구형 누락·혼합 transaction·금지 입력은 사전 거부하고 보고서에 제외 건수/혼합 경계 한계를 표시한다. 관련 묶음 45건 + 시장 이력 취소/drain 1건이 통과했다. 전용 PostgreSQL 혼합 capture/재생 검사는 추가했으나 미실행이며 stage·배포·예약 변경은 미수행이다. 아래 감사의 '설계만' 표기는 그 시점 결과이며 이 항목이 후속 상태다.

2026-10-07 O12 replay 입력 경계 감사: [재생 범위·다음 구현 결정](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md#2026-10-07-재생-범위-감사와-다음-구현-결정)에 기능별 capture/executor 차이와 첫 변경 범위를 기록했다. 현재 0B 원인 replay와 store-only replay를 구분하며, 다음 구현은 0w·0J·0U 최소 FID capture 및 동일 collector의 market_state descendant 중복 방지다. 이 설계는 아직 구현·테스트·배포하지 않았다. 활성 NAS·예약은 변경하지 않았다. 전체 앱 원인 재생 및 source-state 동등성은 미완료다.

2026-10-04 DB 책임 분리 일곱 번째 도메인 `documents/theme` 완료: SQLite/PostgreSQL 문서 upsert·replace·조회 구현 10개와 관련 helper 10개를 `database_documents.py`로 이동했다. 기존 QueryStore 계약·SQL·동일 cursor의 테마 snapshot 및 뉴스 기사 revision/BODY job 저장, 성공 COMMIT 이후 worker wake-up을 유지했다. 이동 메서드/helper AST 20개와 기존 root 메서드 AST 167개가 이동 전과 일치한다. consumer 338곳·직접 연결 gate 42/42 및 PostgreSQL 논리 SQL/table inventory가 그대로다. PC DB/codec/news/theme/content 회귀 109건과 인증 API/PC client 4건이 통과했다. 파일 간 이동한 helper를 PostgreSQL 정적 감사가 놓치지 않도록 직접 import 추적을 보강하고 감사 테스트 3건을 통과했다. NAS 전용 PostgreSQL 후보 6건이 통과했다(2.991초). 출력된 candidate_module 경로는 ZIP 경로를 일반 디렉터리처럼 조합한 표시값이며, 실행 runner는 ZIP 안의 `src`를 우선 import 경로에 올린다. 후보 ZIP `kiwoom-db-documents-split-pilot-v1-20261004.zip`은 NAS X 공유와 원본의 SHA-256 `A938EF44C57A9E5A33811CD77ED519438BBD0FD5E172B7EC20430AF642EEE2E3` 일치가 확인됐다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

2026-10-04 DB 책임 분리 여섯 번째 도메인 `research export` PostgreSQL gate 완료: NAS 전용 후보 v2에서 fixed membership/rollback, observation revision reader, market bar reader 검사가 3/3 통과했다(2.072초). v1의 실패는 이동된 helper 대신 구 `database.py` binding을 patch한 테스트 주입 문제였으며 수정 뒤 통과했다. 후보 모듈 경로는 ZIP 내부 `database_research_export.py`로 확인했다. 운영 DB·NAS active release·이미지는 변경하지 않았다.

2026-10-04 DB 책임 분리 여섯 번째 도메인 `research export` 로컬 후보: SQLite/PostgreSQL 고정 membership manifest 생성과 cursor page reader 4개 구현, 도메인 helper 7개를 `database_research_export.py`로 옮겼다. 공유 revision SQL projection helper는 `database_codec.py`로 이동하고 기존 root binding을 유지했다. 이동 구현/helper AST 12개가 원본과 같고 consumer audit 338곳·QueryStore 계약·PostgreSQL 직접 연결 승인 42개가 그대로다. PC SQLite 고정 watermark/5,000건 이상 pagination/revision reader 7건, 인증 API export 1건, PC client query 1건이 통과했다. sandbox에서 TestClient socketpair 대기 후 동일 API 검사를 제한 밖 임시 SQLite에서 통과했다. 후보 ZIP `kiwoom-db-research-export-split-pilot-v1-20261004.zip`을 X 공유에 SHA-256 `3279E0740A461EAF4DEE51645CB7DBE324127EBB6B6C841147528860466F668E`로 스테이징했다. NAS 전용 DB 3개 검사는 아직 실행 전이다. 운영 DB·NAS active release·이미지는 변경하지 않았다. 상세는 [DB 도메인 분해](db_refactoring/domain_decomposition.md)를 따른다.

2026-10-03 O12 분봉 replay recorded-counts: trace의 행 시도·변경·중복키·revision·metadata 억제·history 상태를 synthetic 입력 생성에 사용한다. 고유 키보다 변경/revision이 많거나 필수 계수가 없고 지원하지 않는 metadata shape면 실행을 거부한다. 재생 호출 실제 계수가 원본과 다르거나 관측되지 않으면 run을 실패 처리한다. 호출마다 별도 synthetic subject를 사용하므로 원본 종목/key overlap·값·중복 payload·신규/변경 비율은 복원하지 않는다. 관련 PC 단위/API 회귀 34건 및 NAS 후보 전용 PostgreSQL 검사 6건이 통과했다(NAS 검사 18.268초). 후보 `2026.10.03-db-minute-recorded-shape-v1-94fdce4bf35406a7`는 immutable source-runtime으로만 준비됐고 active release는 `2026.10.03-db-minute-replay-v1-388841088675094c` 그대로다. NAS server image에는 Starlette `TestClient`용 시험 의존성 `httpx2`가 없어 해당 단위 묶음은 NAS에서 import 단계에 멈췄지만, PC `.venv`에서는 전체 34건이 통과했다. **남음:** 새 shape trace로 recorded-counts end-to-end 실행, trace 오버헤드·65분 보존 검증. 상세 경계는 [DB trace/replay 설계](DB_REPLAY_TRACE_DESIGN.md)를 따른다.

2026-10-03 O12 replay 보고서 계측 보완 로컬 후보: replay 입력마다 `source_call_id → replay_call_id → observed db_call_id`를 연결하고 호출별 input rows, domain counts, elapsed/execute/commit, transaction/outcome을 보고하도록 했다. `query_minute`의 changed/duplicate/revision 계수 누락은 별도 상태로 나타낸다. 기존 `database_delta`는 유지하면서 측정 전체 구간 값으로 scope를 명시했다. WAL은 PostgreSQL cluster 전역 통계이고 DB transaction delta는 현재 진단 DB 전체 값이므로 replay 전용 WAL로 해석하지 않는다. replay call 자체의 행·transaction·commit 합계는 ID로 연결된 실제 DB call에서 별도 산출한다. 로컬 관련 검사 78건 통과, 전용 PostgreSQL 4건은 URL 미설정으로 skip. 이 변경은 NAS에 배포하지 않았고 10월 6일 trace capture 예약은 active `2026.10.03-db-minute-replay-v1-388841088675094c`에서 수행하도록 유지한다. per-transaction WAL bytes는 PostgreSQL 통계로 분리할 수 없어 미지원이다.

2026-10-03 O12 `query_minute` 선택 replay: active NAS release `2026.10.03-db-minute-replay-v1-388841088675094c`에서 capabilities/health를 확인하고 저장 trace `20261003T054415Z-1984933624e2`의 분봉 31 calls·27,900 input rows를 `unchanged_page`, `one_changed_bar`, `fresh_page`로 재생했다. 세 시나리오 모두 전용 DB 결과/revision/metadata와 cleanup 검증을 마쳤다. 원 trace는 이전 release라 call별 shape counters가 없어 종목/key overlap·실제 변경/no-op 비율은 복원되지 않았다. schedule lag p95는 약 1.16초·1.40초·9.44초로 세 run 모두 `timing_preserved=false`; 이는 synthetic adapter가 입력 분포 또는 운영 병목을 재현한다는 증거가 아니다. fresh-page의 WAL 85.1MB/978 transaction은 전체 DB cluster 통계이며 단독 귀속값이 아니다. trace queue 65분 예산 회귀검사에서는 3,900초 설정 아래 20,000 event를 손실 없이 chunk로 비웠다. 이 검사는 실제 65분 대기·NAS 내구성·trace 오버헤드를 측정하지 않았다. Codex 일회성 예약 `nas-65-minute-db-trace-capture`(08:54 KST 시작)와 `verify-nas-db-trace-capture`(10:01 KST 검증)를 2026-10-06로 등록했다. 첫 작업은 거래일·health·idle 상태를 재검사하고 3,900초 capture를 시작해 trace ID를 기록한다. 두 번째는 manifest/chunk checksums를 확인한다. 두 예약은 PC와 Codex 데스크톱 앱이 켜져 있어야 하며, 실제 capture와 산출물 검증은 미완료다.

2026-10-03 O12 장후 선택 재생: 이전 immutable release `2026.10.03-db-trace-replay-v1-48d061e4289a6e37`에서 60초 NAS trace `20261003T054415Z-1984933624e2`를 완료했다. 633/633 이벤트 기록, known drop 0, 12/12 chunk checksum 검증, 시작/완료 280쌍 일치였다. 뉴스/shadow replay `20261003T054931Z-2eba0750`는 dedicated DB에서 38/38 완료, 미실행·backpressure·오류 0, fixture 생성/검증/정리 및 관측 DB call 38건 상관 확인을 통과했다. master/capture/trace는 OFF이고 pause는 없었다. 이후 현재 `db-minute-replay-v1` release로 분봉 synthetic replay도 완료했다. 두 trace replay 경로 모두 실제 장중 payload/key 분포·WAL/lock 경합의 동등 재현을 보장하지 않는다. **남음:** actual trace 오버헤드/65분 NAS 내구성, 거래시간 capture, capture 예약·파일 확인.

2026-10-02 O12 장중 경량 trace 후보: 오늘 12:55~13:10 KST 자료 보존을 우선하고 10월 6일 08:55~10:00까지 같은 경량 경로를 사용하도록 [구현 계약](DB_REPLAY_TRACE_DESIGN.md)을 정했다. 별도 bounded RAM queue와 background chunk, raw capture와 독립된 master 자식, DB 호출 시작/완료 및 성공 writer domain 수치를 구현했다. 로컬 관련 검사 88건이 통과했다. NAS 후보 v1의 전용 DB 검사 2건 중 1건에서 연결 종료 후 DB 이름 조회 결함을 발견해 v2에서 연결이 열릴 때 이름을 보관하도록 수정했다. NAS v2의 전용 DB 검사 2건은 모두 통과했으며 immutable release `2026.10.02-db-light-trace-v2-bccb28f493330b50`은 stage만 완료했다. 활성 서버 전환, trace API smoke, 오버헤드 측정, 실제 자료 확보와 10월 6일 예약은 아직 미완료다. 주요 writer 입력 재생은 별도 후속이다.

2026-10-02 O12 장후 DB 호출 부분 재생 로컬 후보: 완료된 측정 보고서의 raw에서 시간 구간·writer kind를 선택해 전용 `kiwoom_monitor_diagnostic_test`에 빈 뉴스 claim/inline shadow 호출만 재생하도록 연결했다. 평일 KST 07:30~20:30에는 거부하고, 누락/미지원 호출을 명시한다. 기존 master/capture/run lock·보고서 경계를 재사용한다. 로컬 단위검사 12건은 통과했고 NAS 전용 PostgreSQL 실제 재생·성능 비교·운영 적용은 아직 하지 않았다. 0B/분봉/TOP20 재생이 없어 장중 전체 병목 재현 도구로 판정하지 않는다. 로컬 후보 build marker는 `2026.10.02-db-call-replay-pilot-v1`이다. [제약과 사용 범위](NAS_RUNTIME_DIAGNOSTICS.md#2026-10-02-기록된-db-호출의-장후-부분-재생-pilot-로컬-후보).

2026-10-02 O12 뉴스 claim 단계별 대기 계측: `news_job_claim`의 기존 native transaction과 SQL 순서를 유지하면서 stale 복구 UPDATE, 후보 SELECT+fetch, `mark_running`을 같은 `call_id`와 backend PID로 기록한다. metrics capture 중에만 별도 연결이 100ms 이후 최대 25ms 간격으로 `pg_stat_activity`를 읽으며, 뉴스 claim probe는 동시 3개·단계당 보존 표본 256개·호출당 16단계로 제한한다. raw에는 단계별 시간·wait/blocking PID 표본과 probe/SQL 실패 유형을 남기고, summary에는 단계별 지연 분포와 wait count만 제공한다. wait count는 대기 시간으로 해석하지 않고, 후보 SELECT 시간은 fetch를 포함한다. capture OFF·세션 교체·probe 오류는 업무 claim 결과를 바꾸지 않는다. 관련 로컬 검사 93건이 통과했다. 사용자가 NAS source-runtime 후보의 전용 PostgreSQL 검사 2건 통과와 `2026.10.02-news-claim-phase-waits-v1-dd16a3b0e4a543d7` 배포 및 `/health` 정상 출력을 제공했다. 새로운 병목 귀속 측정은 아직 없다.

2026-10-01 O12 shadow checkpoint frame 후보: 현실 dataclass fixture 전용 DB 검사 3/3 통과. 같은 1,803-frame 입력에서 단일 변경 DML의 EXPLAIN WAL은 inline 151,643→frame 1,499 bytes(약 99.0% 감소)였지만 public writer는 SQL 2→5회, execute 중앙값 약 68→73~77ms였다. cursor-only/1-frame/50-frame의 총 column payload는 inline 약 138.5KB, frame v2와 downgrade fallback 합계 약 1.368MB로 약 9.9배다. 각 writer 사례는 3표본이며 양쪽 모두 수 초 COMMIT outlier가 있어 안정적인 시간 우위는 입증되지 않았다: median total은 cursor-only 184ms inline 대 2,644ms frame, 1-frame 264 대 269ms, 50-frame 269 대 275ms였다. 따라서 성능 후보로 운영 활성화하지 않고 writer 기본값 OFF를 유지한다. NAS 활성 release는 바뀌지 않았다. 상세 수치와 해석은 [OPEN_ITEMS O12](OPEN_ITEMS.md) 및 [공통 DB 관측 검토](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)를 따른다.

2026-10-01 O12 VI 동일 이벤트 동시 저장 완화: 전용 PostgreSQL 검사 4/4가 NAS source-runtime 후보 `2026.10.01-shadow-checkpoint-frames-v1-111044c01410cc8c`에서 통과했다. 동일 키 저장 병합, 선행 실패 후 재시도, 취소/서비스 종료 중 실제 commit 대기, commit 응답 유실 뒤 중복 이력 없는 재시도를 검증했다. 실패 로그 2건은 의도적으로 주입한 예외이며 복구 검사는 통과했다. 후보는 테스트만 수행했고 운영 release는 변경하지 않았다. 부하 개선 여부와 중복 수신의 상위 원인은 미확인이다.

2026-10-01 O12 `realtime.latest` 호출 경로 검토: 수신 event는 client hub에 바로 전달되고 DB에는 `(event_type,item_key)`별 최신값만 1초 flush batch로 저장한다. PostgreSQL은 batch당 한 transaction/`executemany`를 사용하며 WebSocket subscribe 시에만 초기 snapshot을 읽는다. 매 event별 저장이나 매 event별 DB read는 아니다. `received_at` freshness 보존이 필요해 동일 payload처럼 보이는 값을 그대로 건너뛰는 최적화는 적용하지 않았다. 기존 운영표본 COMMIT max 2,370ms는 이 writer 고유 병목으로 귀속되지 않았다. 안전한 로컬 코드 최적화는 확인되지 않았다.

2026-10-05 `realtime.latest` 후속 변경: 사용자 지정에 따라 DB 최신값 checkpoint를 매 5분 경계의 10초로 늦췄다. 기존 구독자에게는 수신 event를 즉시 전송하고 새 구독자에게는 최근 RAM 값을 DB checkpoint보다 우선한다. 정상 종료·인증 전환은 대기값을 즉시 저장하고 분봉·초봉 등 다른 1초 writer 주기는 유지한다. 비정상 종료 시 최대 약 5분의 latest projection 손실 가능성이 있으며 NAS 운영 반영과 장중 전후 계측은 아직 확인하지 않았다.

2026-10-01 O12 `realtime.second_bar` 경로 검토: 수신 0B tick은 메모리 누적 후 dirty 초봉을 1초 단위 batch로 저장하며 PostgreSQL은 한 transaction의 `executemany` upsert를 사용한다. 지연 tick은 최근 5초 절대 상태 정정이고 재시도는 최신 상태만 유지한다. 앱 reader는 찾지 못했으나 데이터 계약은 부분 수집 원자료 보존이므로 저장을 중단하지 않는다. 운영 표본은 90초 62 calls/1,063 rows, COMMIT max 615ms였다. 기존 계측만으로 초봉의 신규/진행/늦은 정정/재시도 비중과 실제 update 여부를 분리할 수 없고 저장 지연 허용 계약도 확인되지 않아 flush를 늦추는 변경은 하지 않았다. 다음은 TOP20 편입 문서 writer를 확인한다.

2026-10-01 O12 TOP20 일별 편입 문서 writer 검토: 이미 저장된 종목은 in-memory persisted set으로 반복 순위 저장에서 제외하고, 최초 누락만 batch upsert한다. DB 계층에도 동일 JSON no-op 충돌 방지가 있어 중복 시도만으로 실제 UPDATE/WAL이 발생하지 않게 되어 있다. 실패는 성공 set에 넣지 않아 재시도하며, 재시작 복구는 membership snapshot/revision, 전날 보완은 최신 entrant 문서 재조회에 의존한다. 성공/실패·재시작·날짜 경계·복구 한도 네 표적 테스트를 실행해 4/4 통과했다. 9/30의 3 calls/60 rows 표본은 이 구현 이전 자료여서 새 동작의 전후 비교로 사용할 수 없다. NAS 운영 반영과 post-change 측정은 아직 없다.

2026-10-01 O12 TOP20 저장 경계 검토: `top20_membership`은 유효한 완성 순위마다 저장하며, snapshot·metadata·observation revision은 `save_dataset_snapshot()` 내부 한 transaction이다. `top20_daily_entrants`는 날짜 상태 복구 뒤 새 편입 종목이 있을 때만 별도 upsert하고, `top20_index`는 완료된 분 집계만 별도 transaction으로 저장한다. 지수 항목은 DB 쓰기보다 먼저 durable outbox에 넣고 실패·재시작 뒤 다시 flush한다. 멤버십은 DB 저장 전에 live projection과 구독 대상을 먼저 갱신하므로 DB 지연이 화면/구독 갱신을 막지 않는다. 세 writer를 한 transaction으로 합치면 빈도와 실패·재시도 경계가 다른 데이터를 결합하고 outbox 복구 계약도 약화하므로 현재 별도 transaction을 유지한다. 기존 운영 호출 수치는 entrant writer의 반복 저장 제거 전 자료라 새 transaction 절감 효과를 정량화하지 못한다. 세 경로를 합칠 근거가 확인되지 않아 코드 변경은 하지 않았다.

2026-10-01 O12 TOP20 진입 준비 단계와 기본정보 reader 확인: NAS release `2026.10.01-top20-entry-preparation-v2-e55eb967f5e572c2`에서 health 정상, DB 컨테이너 미재생성, 단계별 전용 DB 검사 네 건 통과를 확인했다. 05:12 KST report `20260930T201216Z-818f8cc0`와 07:13 KST report `20260930T221328Z-1e746543`는 각각 complete 90초 표본이며, `top20.entry_daily_history` 일봉·coverage reader는 각 95회에서 0회로 감소했다. candidate_flow_capture·historical_highs도 각 60회에서 0회였지만 이전 표본의 source가 unattributed여서 TOP20 전용 감소량은 확정하지 않는다. 남은 stock_fundamentals 60회는 server.log의 30초 간격 `/api/v1/content/stock_fundamentals` GET과 대조해 PC의 기본정보 worker 요청으로 확인했다. 20개 코드의 PC 캐시 timestamp는 UTC `2026-09-30 22:xx`였고, 영업일 `2026-10-01`과 문자열 비교해 반복 조회하는 날짜 경계 오류를 로컬 `StockRepository.fundamentals_to_refresh`에서 수정했다. 새 KST 변환 단위검사 포함 `test_stock_repository` 13건 통과. 이 로컬 수정이 실행 앱에 반영된 뒤 reader가 한 차례 보완 이후 멈추는지 확인해야 하며, NAS 기본정보 TR 재요청을 의미하는 측정은 아니다. 실시간 재수신도 별도 확인 대상이다.

2026-10-01 O12 writer 후보 누적 배포: 사용자 NAS 실행 결과 `/health`에서 `status=ok`, `server_build=2026.10.01-db-writer-candidate-fixes-v1`을 확인했고 PostgreSQL 컨테이너 미재생성을 검증했다. 운영 소스 마운트·반복 검사 도구는 현재 작업본에 추가하는 단계이며, 실제 NAS 모드 전환과 재시작/수신 공백 비교는 아직 검증하지 않았다. 운영 소스 모드의 절차는 `deploy/synology/README.md`를 따른다.

2026-10-01 O12 `realtime.minute` SQL 반복 축소 후보: 전용 `kiwoom_monitor_diagnostic_test`에서 replay/idempotency, 중복 분 키 순차 누적, 두 connection 독립성, late realtime 저장 후 닫힌 query 보호, realtime writer lineage 검사를 묶어 5/5 통과했다. 기존 transaction 경계와 revision/authority 동작이 보존됐다. 해당 서버 코드의 운영 빌드·동일 조건 부하 재측정은 아직 남아 있어 SQL 감소량과 WAL/COMMIT 영향은 확정하지 않았다. 상세 범위는 [OPEN_ITEMS O12](OPEN_ITEMS.md)를 따른다.

2026-10-01 O12 `realtime.minute` 운영 상태 확인: NAS의 읽기 전용 `/health`가 16:12 KST에 `server_build=2026.10.01-top20-entry-preparation-v2`를 반환했다. 현재 로컬 후보는 `2026.10.01-shadow-checkpoint-frames-v1`이므로 실행 중 NAS가 분봉 batch-lookup 후보를 포함한다고 확인할 수 없다. 전용 PostgreSQL 계약검사 5/5는 후보 소스의 기능 경계를 확인한 것이며 운영 성능 측정은 아니다. 후보 적용 뒤 장중 SQL/call/COMMIT/WAL 전후 표본은 없다.

2026-10-01 O12 execution runtime lease 경로 검토: 획득은 단일 조건부 UPSERT로 같은 owner token의 갱신 또는 만료된 lease만 인수하고, 해제는 owner token까지 일치할 때만 DELETE한다. `ExecutionRuntime.start`, `heartbeat`, 보호된 실행 작업의 `_require_active`, `stop`에서 호출되며 단순 주기 polling writer는 아니다. 기존 90초 진단은 lease writer 3회·COMMIT max 857ms였다. 호출 빈도가 낮고 오래 걸린 commit은 공통 저장 지연 후보이므로 lease SQL을 제거·병합하지 않았다. 갱신 생략은 60초 만료 뒤 다른 실행자가 인수할 수 있어 중복 실행 위험이 있다. lease expiry/owner replacement/release barrier 로컬 검사 14/14 통과; NAS 운영 성능의 원인 귀속은 하지 않았다.

2026-09-30 O12 DB writer 후보 상세 계측 NAS 배포(빌드 `2026.09.30-db-writer-detail-v1`, `/health`에서 `status=ok` 확인): 기존 90초 표본에서 shadow checkpoint 38회, 분봉 61회/798행/7,173 SQL, TOP20 편입 문서 3회/60행, 최신 실시간 snapshot 61회/789행, 초봉 62회/1,063행, runtime lease 3회였다. 서로 다른 writer에서 COMMIT tail이 관측됐고 SQL execute가 짧은 경로에서도 발생했다. shadow payload/encode, 분봉 내부 단계, TOP20 실제 반영 행 수와 같은 backend의 느린 COMMIT wait-event 상관을 추가했다. 관련 검사 81건·진단/상관 검사 92건 통과(중복 포함). 운영 부하 원인 제거는 아직 하지 않았고, 배포된 계측으로 장중 재측정 후 원인별 개선을 검증해야 한다.

2026-09-30 O12 일봉 기간 검증 로컬 구현(운영 미반영): NAS 수집은 일봉 한 행이 있어도 최신 원천 구간을 확인하고, 연속조회 종료 뒤 저장된 값까지 대조해 `daily_bar_history_coverage`에 확인 상태를 남긴다. 5·20·250일은 각각 계산한다. 신규주처럼 원천 전체가 정상 종료해 확보 이력이 N일보다 짧으면 확보한 봉 수를 표시해 그 구간의 최고가를 사용한다. coverage는 기존 일봉 API의 `bars`와 파라미터를 유지하며 읽기만 한다. PC 계산·캐시도 검증 상태 없이 구형 fundamental/cache 값을 기간 고가로 인증하지 않는다. 로컬 관련 회귀 묶음은 통과했으나 전용 PostgreSQL URL이 없어 해당 통합검사 1건은 건너뛰었다. NAS 빌드 및 실제 응답/화면 확인 전이므로 운영 배포·실제 동작 완료 상태는 아니다. 세부사항과 남은 검증은 OPEN_ITEMS O12 및 HISTORICAL_DATA_CONTRACT의 일봉 기간 검증 계약을 따른다.

2026-09-30 일봉 reader v12 배포·재계측: `/health`가 `status=ok`, `server_build=2026.09.30-daily-bar-lookup-v1`을 반환했다. 60초 run `20260929T210701Z-a0f9fc8f`에서 일봉 reader 50회, 모두 `top20.entry_daily_history`; execute p50/p95 3.098/3.546ms, total p50/p95 15.353/16.697ms, DB·observer 오류 0, dropped/truncated 0이었다. master/capture OFF·workload pause 0 복귀를 확인했다. 직전 v11 60초 구간은 69회·unattributed라 호출 수/출처 mix가 달라 v12 변경의 직접 전후 비교로 해석하지 않는다. 자세한 조건은 OPEN_ITEMS O12.

2026-09-30 NAS 일봉 조회 인덱스 적용 당시 기록: 운영 `central_daily_bars`에 `(code, market, trading_date DESC)` 인덱스를 concurrent 방식으로 생성했고 유효·ready 상태를 확인했다. v11 적용 후 60초 계측은 일봉 reader 69회·unattributed, execute p95 6.19ms였다. 이후 v12에는 qualified `ORDER BY`와 caller 출처 태그가 배포됐다. 자세한 비교 조건과 미확인 사항은 OPEN_ITEMS O12를 따른다.

2026-09-30 누적 NAS 빌드 적용: PC 작업본과 NAS v10 소스를 대조하고 `2026.09.30-cumulative-v11`로 서버·Compose·Dockerfile 빌드 표시를 맞췄다. 서버 관련 로컬 회귀 207건 통과, 공개 경로 계약의 신규 진단 API 누락을 수정한 단일 검사 통과. 추적 파일 1,179개를 비교해 차이 117개를 NAS 공유에 해시 검증하며 복사했고, 기존 55개는 `X:\kiwoom-monitor-backups\20260930-cumulative-v11`에 보존했다. `.env`·운영 DB·서버 데이터·비밀 저장소는 변경하지 않았다. 사용자가 NAS 이미지를 빌드·서버를 재시작했고, NAS `/health`에서 `status=ok`, `server_build=2026.09.30-cumulative-v11`을 확인했다. 인증 API·실시간 수신·운영 부하 변화는 아직 확인하지 않았다.

2026-09-30 뉴스 작업 idle backoff·wake-up(v11 포함): 빈 큐 worker는 1·2·4·최대 5초로 claim 간격을 늘린다. 같은 서버 프로세스의 뉴스 job 저장은 성공한 COMMIT 뒤 worker를 깨우며, 별도 프로세스 저장·신호 누락에는 최대 5초 조회가 남는다. BODY/RULE worker 수와 DB transaction은 유지했다. 관련 로컬 단위검사는 통과했고 v11 서버 health를 확인했지만 운영 claim 호출량·선점 지연은 미확인이다.

2026-09-30 순위 별도 저장 시간 축소(v11 포함): `ka00198`의 `ranking` 스냅샷은 한국시간 07:55:00~08:05:59에만 저장한다. NAS가 자동으로 수집하는 보조 순위(qry_tp 1~4)도 이 시간에만 요청한다. 주 순위(qry_tp 5)의 30초 조회·실시간 표시와 `top20_membership`/당일 편입 이력은 장중 계속 유지한다. `ranking`과 `top20_membership`은 내용이 겹치지만 별도 데이터 계약이며, 후자는 후보·뉴스·장후 보완 입력이다. 보조 순위를 화면에서 선택하면 08:06 이후 중앙 저장본은 아침 값이므로 최신 순위로 해석하지 않는다. 로컬 관련 회귀 75건 통과; NAS v11 health 확인, 실제 저장량 감소는 미측정이다.

2026-09-30 O12 분봉 metadata A/B/A 두 번째 운영 측정: run `20260929T172745Z-fc7c33e0`의 60초씩 비교에서 B metadata 20,422행 생략을 확인했다. 분봉 호출 26/23/24회에도 COMMIT p95는 1,666/1,776/1,356ms로 B의 지연 감소가 재현되지 않았다. 첫 A에만 외부시장 봉 writer 4회와 많은 분봉 reader 호출이 겹쳐 전역 WAL/장치 수치는 metadata 효과로 귀속할 수 없다. 진단 master/capture OFF, pause 0 복귀 확인. 상세 결과는 OPEN_ITEMS O12 참조.

2026-09-30 O12 분봉 metadata A/B/A 운영 측정 완료: NAS v9 run `20260929T171853Z-44764cf9`에서 각 60초 비교를 완료했고 B에서만 metadata 6,022행을 생략했다. B에서도 분봉 COMMIT median 3,762ms, `WalSync`/`WALWrite` 대기와 dm-4 busy 99.75%가 남았다. A/B/A 분봉 호출 12/7/4회와 다른 reader/writer 부하 차이로 전역 WAL 감소를 metadata 단독 효과로 볼 수 없다. master/capture OFF 및 pause 0 복귀 확인. 상세 수치와 미확정 사항은 OPEN_ITEMS O12 참조.

2026-09-30 O12 분봉 metadata A/B/A 진단 v9 준비: 분봉 metadata만 TTL 진단 스위치로 일시 생략하고 호출별 생략 행 수를 기록하도록 NAS 소스를 배치했다. NAS 배치 소스 기준 관련 회귀 80건 통과, v8 원본 6파일은 `.codex-backups/20260930-minute-metadata-aba-v9`에 보존했다. 운영 결과와 복귀 상태는 위 측정 기록을 따른다.

2026-09-30 O12 분봉 metadata 진단 v8 운영 적용·측정: `/health.server_build=2026.09.30-minute-metadata-diagnostics-v8`을 확인했다. 이전 NAS v7 소스를 백업하고 SQL wait·실제 반영 행 수 계측과 빌드 표식만 반영했으며 분봉 저장 동작은 유지했다. 60초 run `20260929T163650Z-ba987a7a`의 완전 계측 분봉 14회에서 봉 본문 반영 0행, metadata 반영 12,600행, revision 추가 0행이었다. 최대 지연 호출은 metadata 실행 135ms 뒤 COMMIT 15,156ms에서 `LWLock:WALWrite` 581표본이 있었다. 다른 writer도 겹쳐 전역 WAL 32.5MB의 단독 원인은 미확정이다. 진단 종료 뒤 master/capture OFF·pause 0을 확인했다. 동작 변경은 사용자 요청에 따라 보류한다. 상세 수치·표본 한계는 OPEN_ITEMS O12 참조.

2026-09-30 O12 분봉 보완 심층 비교: v7 `minute_backfill` ON–OFF–ON 각 60초에서 분봉 저장 11/2/8회·9,900/1,800/7,200행으로 OFF 효과가 확인됐지만, `dm-4` busy는 세 구간 모두 약 99.7~99.9%였다. OFF 구간의 PostgreSQL 읽기량이 크게 증가하고 timed checkpoint가 완료되어 WAL·장치 수치만으로 분봉의 단독 기여도를 계산할 수 없다. 외부시장 봉 저장은 세 구간 0행이었고 마지막 구간에 수집 시도 1회가 시작됐다. 완료 보고서 `20260929T155411Z-6800792e`, 진단 master/capture OFF·pause 0. 상세 수치·제약은 OPEN_ITEMS O12 참조.

2026-09-30 O12 운영 재측정: v7 진단 API run `20260929T154528Z-309966bb`의 90초 표본은 분봉 22회·19,522행과 외부시장 봉 4회·3,782행이 있는 실제 고부하 창이었다. 분봉 COMMIT p95 7,783ms·최대 11,063ms의 동일 backend에서 `WALWrite`/`WalSync` 대기가 확인됐고, 외부시장 봉 최대 COMMIT은 17,065ms였다. 전체 WAL sync time 증가는 14,705ms, `dm-4` busy 99.78%·평균 queue 72.98이었다. 복수 writer의 수초 COMMIT이 겹쳐 공유 저장 경계 지연이 재현됐으나 부하 생성 writer와 checkpoint·다른 프로세스의 개별 기여는 아직 미확정이다. 일봉 writer는 0회여서 이전 일봉 창과 동등 비교할 수 없다. 종료 후 진단 master/capture OFF·pause 0을 확인했다. 세부 표본과 한계는 OPEN_ITEMS O12에 기록했다.

2026-09-30 O12 장후 프로그램 수급 빈 응답 보정(로컬, NAS 미적용): `ka90008`의 정상 0행을 무조건 실패 처리하던 경로를 좁혔다. 조회 대상 달력 날짜 안에서 응답 형식과 원본 저장을 확인한 0행은 명시적인 빈 관측으로 완료 표시해 같은 종목 재요청을 멈춘다. 익일 전일 0행은 날짜 귀속을 확인할 수 없어 여전히 완료 처리하지 않는다. 운영 부하 감소와 익일 비어 있지 않은 응답의 거래일 귀속은 미검증이다.

2026-09-30 O12 다중 DB 지연 조사: 기존 고부하 run `20260929T120733Z-ba837d0b`에서는 일봉·수급·TOP20 문서·shadow·cache 등 서로 다른 writer의 COMMIT이 수초씩 지연됐고, 한 일봉 COMMIT의 backend wait 표본은 주로 `IO:WalSync`였다. 동일 60초 창의 `dm-4` 평균 queue 502.29·busy 81.4%와 WAL sync time 12,179ms는 공유 저장 경계 지연을 지지하지만 writer별 장치 사용량의 인과 귀속은 아니다. 뉴스 큐 정리·ANALYZE 뒤 별도 60초 run `20260929T145032Z-b0bd46d1`에서는 1,007개 DB call에서 500ms 초과 execute/COMMIT이 없고 장치 queue 2.11·busy 6.91%였으나, 이전 창의 `ka10080` 10회가 새 창에는 0회라 같은 부하 비교가 아니다. 운영 로그와 호출 경로에서 장후 TOP20 백필은 `ka90008`이 성공했어도 대상일 행이 비면 완료 처리하지 않고 재시도하며, 그동안 완료된 종목의 봉 검증을 매번 반복하는 것을 확인했다. 로컬에서는 같은 프로세스·같은 거래일의 성공 종목/단계만 재시도에서 건너뛰고 실패 단계와 신규 종목은 계속 확인하도록 최소 수정했다. 장후 TOP20 단위 테스트 46건 통과; NAS 미배포, 운영 spike 감소는 미검증이다. 뉴스 idle backoff·enqueue wake-up은 계속 보류한다.

2026-09-29 O12 운영 뉴스 작업 큐 정리: 사용자 요청으로 PC 과거 scope(`historical_market_pc_backfill`, `historical_news_pc_backfill`)의 BODY/RULE `PENDING` job 26,982건만 단일 검증 transaction에서 삭제했다. 삭제 전 키 집합은 9월 27일 PC seed의 `PENDING` 26,982건과 건수·정렬 MD5가 일치했고, 삭제 결과도 같은 건수·지문으로 확인했다. 삭제 후 해당 과거 `PENDING`과 전체 `PENDING`은 0건이다. 기사/본문/완료 job은 삭제 SQL 대상이 아니다. 별도 백업은 사용자의 명시적 지시에 따라 만들지 않았다. 잠금 없는 읽기 전용 BODY/RULE claim SELECT 표본은 삭제 전 약 496/510ms, 삭제 후 약 56/57ms였고, 과거 기사 revision Seq Scan은 후속 계획에서 실행되지 않았다. 삭제 후 실제 운영 worker의 약 118초 capture에서 빈 claim 209회, execute p50/p95 61.543/64.982ms, 전체 transaction p50/p95 74.545/77.83ms를 확인했다. 후속 분해에서 오래된 `central_news_jobs` 통계가 높은 예상 비용과 JIT를 유발함을 확인해 운영 테이블에 `ANALYZE`만 수행했다. 같은 읽기 전용 SELECT는 JIT ON 61.049ms에서 통계 갱신 후 1.954ms(JIT 없음), 실제 worker execute p50/p95는 9.389/12.811ms(61.7초·175회)였다. 호출 빈도는 약 170회/분으로 늘었고 간헐적 p99 지연은 남았다. SQL·connection·polling 코드는 변경하지 않았으며 CPU·WAL 기여가 분리되지 않아 idle backoff·enqueue wake-up 변경도 보류한다. 상세 증거와 남은 감시는 OPEN_ITEMS O12에 기록한다.

2026-09-29 O12 저장 run `20260929T124558Z-a1ff1ba2`의 `news_jobs` A/B/A 비교는 60초씩 완료됐으나 workload의 WAL 기여도는 미확정이다. 원본 report의 legacy writer 집계에서 baseline/paused/resumed `news_job_claim` 호출은 120/0/119, 실제 선점 행은 0/0/0이었다. baseline/resumed claim transaction은 모두 SQL 2회였고 `news_job_finish`·`news_job_retry`는 없었다. raw execute window 분해에서 stale RUNNING 복구 UPDATE의 p50/p95는 2.455/2.746ms와 2.599/3.199ms, 후보 claim SELECT는 baseline 474.697/481.556ms·resumed 503.712/526.888ms였다. COMMIT p50은 각각 1.182/1.222ms다. 따라서 120/119회는 실제 작업 처리 없이 느린 빈 큐 SELECT를 반복한 것이며, 실행시간 대부분은 COMMIT이 아닌 SELECT에서 발생했다. capture는 동일 세션이며 세 구간 모두 DB call·legacy writer 표본 절단과 DB 오류가 없었다. 이 pause 비교는 뉴스 job polling 부하가 사라지는 동작을 확인하지만, 첫 구간 WAL 4,694,631바이트의 원인이 news worker라고 증명하지 않는다. WAL bytes는 4,694,631/411,071/425,864, `dm-4` busy는 8.55/3.00/4.39%, average queue는 3.41/0.72/1.62였다. baseline에서만 외부시장 봉 writer 4회·3,637행 및 collector completion 1회가 겹쳤으나 개별 WAL량은 없어 교란 후보로만 남긴다. `news_stock_refresh`, `news_query_set`, `news_market_feed`도 계속 실행됐다. 세 phase의 전역 WAL write/sync 시간은 `track_wal_io_timing=off`로 0이었다. 같은 A/B/A 반복은 보류한다.

2026-09-29 O12 저장 run `20260929T123657Z-106e4b9a`의 `candidate_monitor` A/B/A 비교 완료(각 60초, 상태 complete, 오류 없음). 중간 B에서만 target pause가 확인됐고 마지막 A에서 재개됐다. 세 phase의 계측 DB-call summary에 candidate/shadow writer call은 없었다. 전체 wait 표본은 baseline `CPU 38/DataFileRead 20`, paused `CPU 36/DataFileRead 24`, resumed `CPU 31/DataFileRead 26/WalSync 1`이었다. WAL bytes는 375,871/116,195/263,214이고 write/sync 수는 19/18, 19/19, 22/20이다. `dm-4` busy는 3.62/3.51/3.61%, average queue는 1.55/1.15/0.89로 모두 직전 고부하 표본보다 낮았으며 느린 DB-call commit도 없었다. 따라서 이 창에서는 target의 계측 writer 활동과 고부하가 재현되지 않아 paused 효과를 판정할 수 없다. 직전 run과 부하 조건이 달라 WAL bytes 차이를 target 효과로 해석하지 않는다. `track_wal_io_timing=off`인 상태라 이 report의 전역 WAL timing 값은 0이었고, wait event 및 writer 관측과 구분한다. 같은 측정을 곧바로 반복하지 않으며, 다음 비교는 해당 writer가 실제 호출되는 부하 창에서만 의미가 있다.

2026-09-29 O12 최신 acceptance: NAS 컨테이너의 `_storage_mapping()`은 `captured_at_utc=2026-09-29T10:39:14.603060+00:00`와 확인된 PGDATA/pg_wal block graph를 반환했다. 보고서 quota·failure 경계 7개도 통과했다. 0 elapsed에서 WAL/sec, 장치 `average_queue`·`busy_percent`가 모두 0으로 나눌 수 있음을 확인해 rate 결과만 `null`로 두고 누적 counter는 유지했다. `to_regclass`/`relid` 통계 조회 수정과 함께 진단 소스 세 파일을 NAS X:에 최소 적용했고 app/Compose/Dockerfile 표식을 v7로 동기화했다. 기존 NAS 소스와 세 marker 파일을 별도 백업했다. X: 실제 `_measure()` 0초+장치표본, 두 stats query fake-cursor, 구문 검사가 통과했고 로컬 진단 제어/workload/sampling 27건도 통과했다. v7 후보 이미지의 격리 smoke test 통과 후 사용자가 `kiwoom-monitor` Compose 프로젝트 이름을 지정해 서버만 재생성했고, `/health`에서 v7·ok를 확인했다. 인증 snapshot의 5개 section(postgres/activity/news_jobs/host/storage)도 운영 읽기 전용으로 응답했다. snapshot 순간 activity는 2행(WalSync 1, DataFileWrite 1)이었으나 PID/호출 소유자는 출력하지 않아 귀속하지 않는다. `public.central_news_jobs` 통계는 index scan 254,372회, index tuples read/fetched 3,431,732,652였고 live/dead estimate는 0, analyze 시각은 null이었다. 추가 read-only catalog 조회에서 `pg_class.reltuples=308770`, `relpages=109110`, relation options 없음, `autovacuum=on`, analyze threshold 50/scale factor 0.1, database stats_reset null을 확인했다. 따라서 행 0건이 아니라 table-level activity estimate와 catalog planner estimate가 불일치한다. 저장소의 앱·스크립트 경로에서는 `pg_stat_reset*` 및 해당 relation `TRUNCATE` 호출을 찾지 못했으나 통계 초기화 주체는 미확인이다. DB 서비스 재생성·쓰기·workload 진단은 하지 않았다.

2026-09-29 O12 run `20260929T120733Z-ba837d0b`의 저장된 raw report를 재검토했다(새 측정 없음). `ka10081` 일봉 저장 4건 중 COMMIT 10,620ms인 PID 4463/call `b9c07629f7dc4138b7b3a5c908f000a9`에서 동일 PostgreSQL backend의 COMMIT wait probe가 `IO:WalSync` 402회, `LWLock:WALWrite` 2회를 관측했다. 같은 run의 다른 일봉 COMMIT도 PID 4327에서 WalSync 13/WALWrite 69, PID 4566에서 WalSync 8/WALWrite 15였으며, 전체 일봉 표본 합계는 WalSync 423/WALWrite 86이다. 각 일봉 call의 probe error는 0이고 `probe_pending_at_capture=true`라 sampler 종료 확인 전 snapshot이라는 한계가 있다. 표본은 25ms 간격이며 100ms 미만 wait는 놓칠 수 있다. 느린 call 동안 host-wide `dm-4`는 busy 99.35%, 평균 queue 1,454.76, write await 2,708.74ms였으나 다른 프로세스 I/O를 포함하므로 개별 장치 동작에 대한 인과 증명은 아니다. 같은 run에서 여러 writer의 작은 execute 뒤 긴 commit, 전체 WAL sync 시간 12,179ms/60s도 함께 관측했다. 현재 증거는 이 구간의 주된 대기가 SQL 계산이 아니라 PostgreSQL WAL sync 경로에 있었고 저장장치 압박과 동반됐다는 강한 근거다. 어느 장치 계층 또는 어떤 concurrent writer가 압박을 만들었는지는 미확정이며, durability·transaction·schema 변경 근거로 사용하지 않는다.

2026-09-29 O12 NAS 저장장치 매핑 재확인·갱신: PostgreSQL `docker inspect`의 bind source `/volume1/docker/kiwoom-monitor/deploy/synology/postgres-data`가 사용한 PGDATA와 일치했다. NAS host의 `mountinfo/sysfs`를 2026-09-29T10:39:14.603060Z에 다시 읽어 PGDATA와 `pg_wal`이 `/volume1` btrfs, `/dev/mapper/cachedev_0`(249:4), `dm-4` graph를 가리키는 것을 확인했다. 결과를 `deploy/synology/server-data/maintenance/diagnostic-storage-mapping.json`에 저장했고 SHA-256은 `6398D462218283307128F3B1AFAD785CA6886AD8FFDE8BE16908F1F1349C671F`다. DB와 workload는 건드리지 않았다. Compose의 `server-data:/app/data` bind는 확인했으며, 컨테이너에서 최신 timestamp를 읽는 최종 확인은 남아 있다.

2026-09-29 O12 진단 측정 0초 경과 방어: `scripts/nas_workload_diagnostic.py`에서 측정 시작·종료 시각이 같은 값으로 기록되면 WAL 초당량 계산이 `ZeroDivisionError`를 내던 경로를 확인해, elapsed가 0 이하일 때 초당량을 `null`로 두도록 했다. CLI 제어·진단 workload 회귀 24건 통과. API sampling 테스트는 현재 bundled Python에 `psycopg`가 없어 import 단계에서 실행되지 않았고, 프로젝트 `.venv` 실행 파일은 프로세스를 시작하지 못했다. NAS v6에는 미배포다.

2026-09-29 O12 NAS 진단 API 교차 프로세스 잠금 검사 통과: NAS 컨테이너의 실제 `/app/src/kiwoom_monitor/central_server/diagnostic_workloads.py`를 불러 별도 Python 프로세스와 3회 대조했다. 잠금 보유 중 자식 프로세스 3회 모두 `diagnostic_run_busy`, 잠금 해제 뒤 3회 모두 `ACQUIRED`를 반환했다. 임시 파일만 사용했고 DB, workload, API 제어 상태는 변경하지 않았다.

2026-09-29 O12 일반 뉴스 선점 후보 계획 결과: 운영 PostgreSQL 17.11 `kiwoom_monitor`에서 원본과 동등 조건 후보를 같은 파라미터로 `EXPLAIN (FORMAT JSON)`만 실행했다(`ANALYZE` 없음). BODY/RULE 모두 원본은 `idx_central_news_jobs_ready` 뒤 `hashed SubPlan`으로 기사 revision을 걸러 `Sort → LockRows → Limit`이며 계획 비용 278,577.78, 정렬 추정 13,536행이다. 후보는 `Hash Anti Join`을 택해 계획 비용 155,633.36(약 44.1% 낮음)이지만 기사 revision의 순차 스캔 추정 53,558행과 정렬·`LockRows`는 그대로이고 정렬 추정은 18,610행이다. 이는 플래너 추정치이며 실제 실행 시간·I/O·WAL wait 감소는 측정하지 않았다. 전용 DB의 선택·동시 선점 동등성 2건은 통과했지만 운영 writer 전환은 보류한다. 원시 결과는 [plan JSON](../artifacts/news-claim-candidate-plan-readonly-20260929.json)(SHA-256 `E4DF25332A99E125341B0C2F19D7C90399C0CECE95271E65322FC10D30229F7F`)에 보존하고 일회성 probe와 그 임시 백업은 NAS 및 로컬에서 제거했다. 운영 source/image/schema/DB 데이터 변경은 없고 health는 v6다. 로컬 `candidate_plans` API 변경은 미배포다.

2026-09-29 O12 운영 후보 계획 비교 준비: 현재 NAS는 `/health.server_build=2026.09.29-diagnostic-api-v6`이다. 서버 재빌드 전 의미 있는 계획 변화가 있는지 확인하려고 기존 통합검사 ZIP을 재사용하는 일회성 `artifacts/diagnose_news_claim_candidate_plan_readonly.py`와 `artifacts/run-news-claim-candidate-plan-readonly.sh`를 NAS artifacts에 배치하고 해시·셸 구문을 검증했다. wrapper는 컨테이너의 운영 PostgreSQL URL로 `default_transaction_read_only=on`, 5초 statement timeout을 적용해 원본/후보 `EXPLAIN (FORMAT JSON)`만 실행하고 결과를 NAS artifacts JSON으로 저장한다. NAS sudo 권한이 필요한 실행은 아직 하지 않았다. 이 probe는 후보 판정과 결과 기록 뒤 제거한다. 운영 source/image/DB는 변경하지 않았다.

2026-09-29 O12 generic news claim 후보 계획 비교 로컬 준비: 실제 선점 SQL은 바이트 단위로 유지하고, 인증 진단 API의 기존 `plans`와 별도로 `candidate_plans`에 BODY/RULE의 동등 조건 `NOT EXISTS` 후보에 대한 `EXPLAIN (FORMAT JSON)`만 추가했다. `ANALYZE`나 운영 선점 실행은 하지 않는다. SQLite 행 선택·우선순위 비교 및 진단 응답 단위검사 2건과 전용 PostgreSQL 통합검사 2건(원본 stale/parallel claim, 후보 동등 선택·`SKIP LOCKED`)이 통과했다. 통합검사 ZIP `artifacts/kiwoom-db-access-news-claim-candidate-20260929.zip`(SHA-256 `13A4A13BFD760B2FBD9696AE940355C6E2D3889954541DED1EC2CD6EDF003E77`)은 NAS 공유 artifacts에 해시 일치로 배치했다. 운영 health는 v6다. NAS 앱 파일은 로컬보다 과거 뉴스 아카이브 경로 77줄이 적고 v6 배포 스크립트는 v5→v6 전용이므로, 로컬 파일 전체를 덮지 않고 진단 경로만 안전하게 새 빌드에 반영하는 검토가 남았다. 후보 계획의 운영 데이터 계획과 지연 개선 효과는 미확인이다.

2026-09-29 O12 진단 API v6 운영 오류 탐색: 사용자가 NAS 서버를 재빌드·재생성한 뒤 인증 API에서 `/health.server_build=2026.09.29-diagnostic-api-v6`, PostgreSQL 17.11 snapshot, writers/workloads/reports/db-calls를 확인했다. 과거 v5에서 완료 상태가 report 파일 저장보다 먼저 공개되어 생기던 404는 v6에서 `report_url` 공개 순서를 고쳤고, 기존 run `20260928T174235Z-69129b49`의 상태와 report summary GET이 현재 각각 완료/200으로 확인됐다. 이는 현재 간헐 404가 재현되지 않았다는 뜻이며 반복 404 부재를 보증하지 않는다. claim plan endpoint와 운영 표본은 `NewsJobRunner.run_once → PostgresQueryStore.claim_news_jobs → _NEWS_JOB_CLAIM_SELECT_SQL` 경로다. read-only 계획에서 BODY/RULE은 ready index scan 뒤 약 13,536행 추정 Sort/LockRows와 article revision 약 53,558행 추정 Seq Scan(`hashed SubPlan`)을 보인다. 2026-09-28 300초 표본 602회에서 execute p95 489.2ms, commit p95 1.323ms였고, ready index는 264 scan 사이 3,561,624 index tuple 증가(약 13,491 tuples/scan)였다. 2026-09-29 CLI run `20260929T075024Z-0b3ad2eb`(30초)는 57회, SQL 114회, execute p50/p95 505/1,882ms, commit p50/p95 1.2/7.1ms였다. CLI 집계 wait와 dm-4 busy 93.55%·평균 queue 495.24·write await 914.09ms는 query별 상관이 없었다. 후속 API run `20260929T082106Z-9446184d`(15초)는 24회 claim/48 SQL을 raw 기록했다. execute p50/p95/max 546.9/2,237.1/2,751.8ms, commit p50/p95 1.179/2.609ms였고 첫 SQL은 p50 2.67ms, 두 번째 후보 SELECT가 지연 구간이었다. 두 번째 SQL 실행창 내부에서 동일 PID의 활동 표본 35건을 22개 호출과 시간·PID로 연결했다: `IO:DataFileRead` 10, `LWLock:WALWrite` 11, `IO:WalSync` 2, `IPC:BufferIo` 3, wait event 없음 9; 관측된 blocker는 없었다. 예를 들어 PID 20844는 SELECT 2,747ms 동안 WALWrite 3회와 WalSync 2회 표본을 보였고 COMMIT은 1.47ms였다. 따라서 “COMMIT이 짧으므로 WAL 지연이 아니다”라는 앞선 해석은 정정한다. COMMIT 대기는 짧지만 후보 SELECT 안에서 WAL 대기가 확인됐다. 계획의 넓은 revision scan과 저장장치/WAL 압박이 유력한 후보이나 각각의 지연 기여도 및 WAL 부하의 발생 주체는 확정되지 않았다. `rows_attempted`는 계속 unavailable이어서 호출당 SQL 2회만으로 claim 0건을 확정하지 않는다. 이번 run의 DB/observer 오류, raw dropped/truncated, 상관 오류는 0이었고 종료 뒤 master/capture OFF·paused workload 0을 확인했다. 기존 `candidate.shadow_checkpoint` 작은 표본과 `central_news_jobs` relation stats 0/0·NULL analyze 원인은 별도 미확정이다. relname-only 통계 조회는 로컬 두 경로에서 `to_regclass('central_news_jobs')`의 relid 사용으로 수정·단위검증했으나 NAS v6에는 미반영이다. 원시 호출 및 PID 상관 표본은 [진단 근거 JSON](../artifacts/news-claim-pid-correlation-20260929T082106Z-9446184d.json)에 보관했다. 운영 DB write·schema 변경은 없었다.

2026-09-29 O12 진단 API v4 NAS 배포와 smoke 확인: 실행 `/health.server_build=2026.09.29-diagnostic-api-v4`; 인증 capabilities, 기존 report 20개/history, PostgreSQL 17.11 read-only snapshot의 postgres/activity/news_jobs/host/storage section을 조회했다. `pg_stat_io`와 checkpointer 사용 가능, activity 2행, news index 3개였다. 5초 observe-only API run은 schema-2 report로 완료했고 raw/summary 다운로드가 됐다. 창 내 common DB raw call 15건, 관측 `unregistered_calls=0`, `coverage=opt_in_observed_calls_only`; 종료 뒤 master/capture OFF, paused 0을 재확인했다. 운영 DB 쓰기/스키마 변경은 없었다. 표본은 API 연결 검증이며 전체 DB 경로 coverage나 CLI 동시간 대조가 아니다. NAS 저장 mapping은 2026-09-26 생성본이다.

2026-09-28 O12 첫 운영 표본 완료: 300초 capture가 `sample_status=complete`, producer/control revision 안정, `dropped=0`, `truncated=false`로 끝났다. 요약은 writer 13종 685회/commit 685회, reader 19종 1,349회, DB/observer error 0, 관측된 `unregistered_calls=0`이다(`coverage=opt_in_observed_calls_only`). 새 신호는 `news_job_claim` 602회에서 execute p50/p95 473.6/489.2ms, commit p50/p95 1.181/1.323ms다. 구현상 매 호출은 stale RUNNING 복구 UPDATE와 `FOR UPDATE SKIP LOCKED` 후보 SELECT를 실행하고, 실제 claim row가 있으면 추가 UPDATE를 실행한다. 관측 `sql_calls=1204`는 호출당 정확히 2건이므로 이 구간에는 claim된 row가 없었던 것으로 코드 경로상 추론된다. 후속 10회 `pg_stat_activity` 표본에서 동일 query fingerprint가 10회 모두 활성, 2회 `IO:DataFileRead`, 차단 PID 없음이었다. 같은 표본 사이 `idx_central_news_jobs_ready`는 36 scan / 485,676 index tuple을 추가로 읽었고 `idx_central_news_jobs_claim_order` scan은 1로 변하지 않았다. 테이블 통계 추정은 live 311,499/dead 54,106, 마지막 autoanalyze 2026-09-25다. 이것은 넓은 index scan/heap I/O 후보를 강하게 지지하지만 이 누적 통계를 개별 쿼리의 정확한 읽기량으로 귀속하거나 원인을 확정하지 않는다. read-only `EXPLAIN` helper로 stale recovery UPDATE와 BODY/RULE claim SELECT의 계획을 별도 확인할 차례다. query_cache COMMIT p95 626.3ms(n=1), investor_flow COMMIT p95 360.9ms(n=4), shadow_monitor_state COMMIT p95 289.9ms(n=10)는 표본이 작아 경향 확정에 쓰지 않는다.

2026-09-28 O12 capture 실행 경로: API 확인 시점에는 capture가 비활성이었으나 위 5분 표본 실행 전에 기존 활성 진단 세션·pause lease가 없음을 확인했다. 운영 DB에 시험 쓰기나 workload pause를 하지 않는 `artifacts/capture_live_db_calls_5m.py`를 사용했다. Python 구문 검사 및 NAS 공유 복사본 SHA-256 `5D5ADC1578DDC73D5ED5C1F374838EE0616251D83F4C3E1864BF5DA763BF808D` 확인. 출력의 `enabled_at_end=true`는 정리 직전 sample window 종료시점 상태다. 후속 `nas_workload_diagnostic.py status`에서 master/capture OFF, paused 없음, uncontrolled importers 없음이 독립 확인됐다.

2026-09-28 O12 NAS DB 관측 pilot 운영 배포 확인: 후보 이미지 `sha256:164aa67ac954269d88e2761dc5a2c2df39cbccfd72c2341e0ffe9cfb40690183`에서 네트워크 없는 SQLite 앱·진단 route 사전검사를 통과한 뒤, Compose `--no-build --no-deps --force-recreate server`로 서버만 교체했다. 사용자 제공 전환 스크립트 최종 출력은 새 이미지 health와 인증 `/api/v1/diagnostics/db-calls` 200 응답·필수 summary keys, database 컨테이너 ID 불변을 확인했다. PC에서 별도로 조회한 운영 `/health`는 `status=ok`, `server_build=2026.09.28-db-observability-v1`이다. 운영 DB 백업은 사용자 요청에 따라 생략했고 기존 이미지 및 NAS 소스·Compose·Dockerfile 7개 백업은 유지했다. 새 API의 응답 확인은 실제 writer call의 생산량·UNREGISTERED=0·성능 overhead 합격이나 모든 DB 접근의 이관을 증명하지 않는다.

2026-09-28 O12 NAS 운영 소스 동기화·전환 준비: 사용자가 이번 무스키마 변경의 운영 DB 백업을 생략하기로 했다. 앞서 백업한 기존 NAS 파일 7개의 해시와 원본 불변을 재확인한 뒤 관측 관련 8개 파일(app/database/diagnostic metrics·registry/신규 postgres access/NAS 진단 스크립트/Dockerfile/Compose)을 검증된 후보와 일치시켰다. 후보의 Docker build 입력 309개가 운영 프로젝트 파일과 모두 SHA-256 일치한다. DB·볼륨·실행 컨테이너는 변경하지 않았으며 운영 `/health.server_build`는 아직 기존 `2026.09.26-bar-upsert-wait-correlation-v5`다. 이미지 ID·Compose project·빌드 표식을 검사하고 SQLite 격리 사전검사, 서버만 재생성, health·인증 DB-call API 검증, 실패 시 기존 이미지 복귀를 수행하는 스크립트를 `artifacts/deploy-db-observability-v1.sh`에 준비해 NAS artifacts로 해시 일치 복사했다. 스크립트의 NAS 셸 구문 검사와 실행·운영 검증은 아직 남았다. 이미지 복귀 시 운영 소스·주 Compose 파일은 후보로 남으므로 후속 원복 판단이 필요하다.

2026-09-28 O12 NAS 별도 후보 이미지 빌드 성공: 사용자가 후보 build context에서 `kiwoom-monitor-server:2026.09.28-db-observability-v1`을 빌드했고 이미지 ID는 `sha256:164aa67ac954269d88e2761dc5a2c2df39cbccfd72c2341e0ffe9cfb40690183`이다. Dockerfile의 빌드 표식·진단 route·관측 모듈 검사와 서버 import가 통과했다. 실행 중 `kiwoom-monitor-server-1`은 여전히 이전 이미지 ID `sha256:0a996650e1e8428f204912b73b3c6b1e91e676d272e0dce017754642447f56e2` 및 `2026.09.26-bar-upsert-wait-correlation-v5` 태그다. NAS 공유 후보와 운영 소스 재대조에서 296개 src 파일이 같고, app/database/diagnostic_metrics/diagnostic_writer_registry 4개가 다르며 postgres_access 1개가 미존재였다. 별도로 `nas_workload_diagnostic.py`, Dockerfile, Compose가 다르다. 운영 기존 파일 7개를 `X:\kiwoom-monitor-backups\20260928-db-observability-v1-predeploy`에 해시 일치로 백업했다. 운영 DB 백업·소스 동기화·서버 재생성·인증 진단 API 확인은 아직 수행하지 않았다.

2026-09-28 O12 NAS 별도 이미지 빌드 후보 준비: 전용 PostgreSQL 79건 통과 후보를 NAS 공유의 현행 미커밋 소스와 파일 해시로 대조했다(관측 overlay 외 불일치 0). `2026.09.28-db-observability-v1` 표식을 맞춘 Docker build context 310개 파일을 `artifacts/kiwoom-db-observability-build-candidate-20260928.zip`으로 묶고, NAS 공유 `artifacts/db-observability-build-20260928-v1`에 별도 추출했다. ZIP SHA-256은 `178EC08D768DBD16CF3FC8E4398B35DE3A132DF05F65C5E59C542BB358472CA0`이며 NAS 복사본과 후보 핵심 파일의 manifest 해시가 일치한다. 이 단계에서는 PC에 Docker CLI가 없고 NAS SSH 자동 접속이 거부되어 이미지를 빌드하지 못했다. 운영 `/health.server_build`는 `2026.09.26-bar-upsert-wait-correlation-v5`였다. 운영 프로젝트 소스·Compose·컨테이너·DB는 변경하지 않았다.

2026-09-28 O12 NAS 기반 계측 후보 묶음 검증 완료: NAS 공유 source를 복제해 DB 관측 파일·route만 얹은 후보에서 v2 전용 PostgreSQL 통합검사 79개가 모두 통과했다(38.854초). v1의 21개 실패는 READ와 WRITE를 구분하지 않은 검사 필터를 수정한 뒤 재검사했다. NAS 운영 source·image·운영 DB에는 변경이 없고, 이 결과는 실제 운영 endpoint 배포·계측 검증이 아니다. 배포 전 candidate Docker build와 실행 source/image 식별, rollback 경계 확인이 남았다.

2026-09-28 O12 NAS 진단 API 배포 확인: 로컬에서 게시된 `http://192.168.0.5:8787/health`가 `status=ok`, `server_build=2026.09.26-bar-upsert-wait-correlation-v5`를 반환했다. 인증 없이 `/api/v1/diagnostics/db-calls`를 요청한 결과 404로 해당 라우트가 실행 서버에 아직 없다. `/health` 구현은 DB 쿼리를 호출하지 않고 diagnostic 요청에는 인증 header를 보내지 않았으므로 DB handler도 실행되지 않았다. 이는 로컬 `db_calls` 코드의 운영 배포나 수집 성공을 뜻하지 않는다. NAS 앱 source/image 변경은 하지 않았다.


2026-09-28 O12 standalone 결과 전달 검토: NAS workload 진단 JSON, prepared-news importer 상태/log, seed exporter SQLite manifest/stdout summary가 각각 존재한다. 저장소 안에서 정기 실행하는 caller는 찾지 못했다. Importer는 단발 snapshot 이관이며 archive 설계가 전량 경로의 대체를 명시한다. 별도 공통 exporter는 추가하지 않는다. 이는 NAS 외부의 수동/예약 실행을 확인한 것은 아니다.

2026-09-28 O12 직접 PostgreSQL 연결 정적 guard 완료: `audit_postgres_access.py --check`가 함수 단위 승인 원장과 현행 직접 driver 연결을 대조한다. 현재 29곳이 승인 원장과 일치하고 미승인·오래된 항목은 0이다. 검사 범위는 직접 식별한 `psycopg.connect`/`psycopg2.connect`; 동적 alias/reflection과 전체 runtime 경로를 증명하지 않는다. 원장과 범위는 [검토 문서](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)에 기록했다. DB 접속이나 실행 경로 변경은 없다.

2026-09-28 O12 NAS 진단 보고서의 공통 DB call 연결 로컬 완료: `diagnostic_metrics.summarize_db_calls`가 PID와 프로세스 수명 ID를 반환하고 `nas_workload_diagnostic._measure`가 측정 구간의 `/diagnostics/db-calls` summary를 자동 capture OFF 전에 회수한다. 서버 교체·capture 세션 종료/제어 변경·bounded sample 손실은 `incomplete`, 구버전/통신 실패·필수 metadata 부재는 `unavailable`로 보존한다. 기존 domain metrics와 별도 `db_calls` 항목이며 합산하지 않는다. 프로젝트 가상환경에서 진단 CLI·공통 DB·API 119건 및 최종 API 단일 검사 1건 통과. NAS 배포·운영 API 연결·DB 재검사는 하지 않았다. 당시 예상한 다음 단계인 정적 guard와 standalone 보고서 필요성 검토는 모두 완료했다.

2026-09-28 O12 수집 경계 설계 당시 기록: process-local capture와 기존 인증 API 유지, 공유 collector 미도입을 결정했다. 뒤이어 진단 report 연결과 정적 guard를 구현했다. 상세 계약과 후속 결과는 [공통 DB 검토의 수집 경계 결정](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)을 따른다. 이 문단은 설계 시점 기록이며 현재 상태가 아니다.

2026-09-28 O12 공통 PostgreSQL 연결 재감사: `scripts/audit_postgres_access.py`를 재실행해 writer method 102개, direct driver connection site 29개, backend 혼합 후보 DB API call 6,679개, parse error 0을 확인했다. 29곳을 store factory·wait probe·전용 DB test/benchmark·진단·maintenance 경계로 분류해 [정적 원장](postgres_access_inventory.json)과 [공통 DB 검토](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)를 갱신했다. `PostgresQueryStore`의 직접 `_connect()` 사용 5곳은 현재 `src` 운영 caller가 확인되지 않았다. raw connect 우회는 runtime `UNREGISTERED`에 보이지 않고 metric deque는 process-local이라는 한계가 남는다. 전용/운영 DB나 서버는 접근하지 않았다.

2026-09-28 O12 prepared-news importer 배치 경계 pilot 완료: `import_prepared_historical_news_to_nas._complete_batch`의 caller-owned outer `connection.transaction()`에 `maintenance.prepared_news/prepared_news_completion_batch` 관측을 연결했다. 같은 연결에서 배치마다 새 call ID와 outer COMMIT/ROLLBACK 시간을 기록하며 기사별 nested savepoint, 별도 기사 저장 연결, SQLite ledger commit 순서는 유지한다. 프로젝트 가상환경에서 공통 DB·importer 단위검사 51건 모두 통과했다. 사용자가 NAS 전용 PostgreSQL importer 통합검사 4건 모두 통과했다고 보고했다(16.147초). 새 검사에는 동일 연결의 두 배치, 보류 기사 savepoint rollback과 다른 기사 commit, 개별 call ID·commit count 검증이 포함된다. 검증 ZIP 1,280,254바이트(SHA-256 `73CA8F3504F6569149FBE67B643F3A6A8EED619DF5E4996C4DE8761EED95BB08`)의 NAS 호스트 해시 일치를 확인했다. 기존 연결 획득 시간은 이 호출에서 측정하지 않으며 savepoint별 시간도 독립 call로 집계하지 않는다. COMMIT 지연의 운영 전후 비교는 아직 하지 않았다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 document writer batch 완료: `news_watchlist`(allowlisted content API), `news_automation_settings`(NewsService), `server_operational_settings`(operational settings API)와 Forward Evaluation/mock automation 저장소의 추가 17 collection kind를 `document.collection` common context에 등록했다. per-call connection·native transaction·각 호출의 separate commit은 그대로 두었다. v1에서 확인한 3개 기존 테스트 metric assertion은 같은 kind의 READ도 포함하는 필터 문제였고, `writer_family`와 `writer_kind` 모두로 제한해 수정했다. v2 NAS 전용 PostgreSQL 묶음 4건이 모두 통과(38.363초): 20 kind 독립 write/read metric, replay, reader, recovery, decision gate, dispatch projection을 검증했다. v2 ZIP SHA-256 `3631B2EA634B1A3FB9F2A14D65F6F54AC771B87CE5DF2E93EFC33EFCD7A335BF` NAS host hash 일치. 로컬 py_compile 및 관련 unit 68건 중 28건 통과·40건 환경 skip. 최신 정적 원장은 102 PostgreSQL store method·29 literal driver connect site·6,657 static candidate DB calls·0 parse error다.

2026-09-28 O12 startup schema migration pilot 완료: PostgreSQL `initialize()` 연결을 `schema.migration/central_schema`로 계측했다. migration runner와 SQLite 경로는 그대로다. 로컬 공통 DB·migration 회귀 99건 통과(40 environment skip 포함), NAS 전용 PostgreSQL 3 tests 통과(1.419초): 정상 initialize commit, 진단 migration DDL 실패 후 savepoint/native rollback 및 ledger 미기록, 기존 native context 종료 경계를 확인했다. ZIP SHA-256 `B672F6322FBF6CB2D108716453F5939F906CE18CEEA4DB1C8D2F1FA56CB27BBB` NAS host hash 일치. 같은 static scan에서 `save_five_minute_bars`, `save_market_data_metadata`는 production caller가 확인되지 않았고 `register_account_scope_alias`는 application helper와 테스트에서만 참조되어 활성 runtime writer로 단정하지 않았다.

2026-09-28 O12 추가 active READ pilot 완료: `PostgresQueryStore.load_document`를 `read.document_collection/document:<collection>:single`로 계측했다. 활성 경로는 `ForwardEvaluationRepository._save_immutable → _find → store.load_document`이며, 기존 SELECT·반환 decoding·native transaction을 유지했다. 리서치 export page·diagnostics 두 검사(2 tests, 0.863초)와 NAS 전용 PostgreSQL single-reader 검사(0.328초), Forward Evaluation 로컬 회귀 23건이 통과했다. NAS v1은 `tests/__init__.py` 누락으로 discovery 전에 실패했고, 패키지 표식을 넣은 v2에서 검증을 완료했다. v2 ZIP SHA-256 `55362A5432EDD7E236157AC024C3D099381912503DA3D6B892F4AEF322D464DA`의 NAS host hash가 일치한다.

2026-09-28 O12 query-cache READ 공통 관측 pilot 완료: `PostgresQueryStore.load_query`의 native SELECT transaction을 유지한 READ 계측을 추가했다. `/api/v1/diagnostics/db-calls`는 writers와 readers를 분리한다. 로컬 회귀 44건과 전용 PostgreSQL hit/expired/missing reader 테스트 1건(0.497초)이 통과했고 ZIP·NAS 호스트 SHA-256도 일치했다. 운영 DB와 NAS 앱 이미지는 변경하지 않았다. 다음 READ 경계는 활성 callsite가 확인된 `PostgresQueryStore.load_documents`다.

2026-09-28 O12 document collection READ pilot 로컬 구현: 실제 호출이 확인된 `PostgresQueryStore.load_documents`를 collection별 reader로 기록한다. SELECT, result decoder, native transaction, SQLite 경로는 유지했다. 로컬 공통 DB/store/API 회귀 99건 통과. 전용 PostgreSQL hit/empty·metrics ZIP(1,317,824바이트, SHA-256 `C854AC3B299F7F858596114D2CB95D813A69E124B232CAC4A586F23C64B3ED4B`)을 NAS 호스트에 전송하고 해시를 대조했다. 전용 DB 결과는 아직 대기 중이다.

2026-09-28 O12 document collection READ pilot 완료: `load_documents`의 collection별 READ 관측을 추가하고 로컬 회귀 99건과 전용 PostgreSQL hit/empty reader 검사(0.352초)를 통과했다. 현재 다음 검토 경계는 실제 API/market ingest 호출이 있는 minute·daily bar reader다.

2026-09-28 O12 market bar READ pilot 완료: `load_minute_bars`와 `load_daily_bars`를 `read.market_bars` family 아래 별도 kind로 관측한다. query/result mapping·native transaction·SQLite 경로를 유지했고 관련 로컬 회귀 100건 및 전용 PostgreSQL `test_market_bar_readers_keep_native_context_and_separate_kinds` 1건(0.570초)이 통과했다. ZIP 1,318,215바이트의 NAS 호스트 SHA-256도 일치했다. 운영 DB·앱 이미지는 변경하지 않았다.

2026-09-28 O12 observation revision READ pilot 완료: candidate monitor·mock automation의 `load_observation_revisions`·`load_observation_revisions_after`를 `read.observation_revisions` 아래 분리 계측했다. SQL·필터·정렬·limit·native context와 SQLite 경로를 유지했다. 로컬 관련 회귀 117건과 NAS 전용 PostgreSQL `test_observation_revision_readers_keep_native_context_and_separate_kinds` 1건(0.208초)이 통과했다. 증분 sequence/commit 역전 문제는 기존 cursor contract OPEN ITEM으로 유지하며 변경하지 않았다.

2026-09-28 O12 active READ batch 완료: shadow state/event page, dataset snapshots, market-data metadata range 네 reader를 세 family로 계측했다. 묶음 회귀 183건과 NAS 전용 PostgreSQL `test_active_readers_batch_preserve_native_context_and_separate_metrics` 1건(0.565초)이 통과했다.

2026-09-28 O12 news READ batch 완료: 활성 news API/collector/job 경계의 8개 reader(history, article/body revision, stock/confirmed publications, source cursor/diagnostics, market feed)를 세 family로 계측했다. 관련 로컬 회귀는 이전 214건 통과 기록이 있으며, NAS 전용 PostgreSQL `test_news_source_page_replay_and_failure_preserve_article_and_progress`가 v2에서 통과했다(3.656초). v1 실패는 source-page GLOBAL 기사와 watchlist reader fixture의 범위 불일치였고, 테스트 전용 watchlist 행으로 바로잡았다. production SQL/저장 규칙은 유지했다. v2 ZIP 1,311,911바이트(SHA-256 `BE10F842820A30CC782AF4F4DCB29F216F5608D62F8B05AD0A686A0C96FCE5DE`)의 NAS 호스트 hash가 일치한다. 다음 조사는 남은 active direct readers다.

2026-09-28 O12 market-state READ batch 완료: realtime snapshot·latest market cap, theme snapshots, market-event history·hot cohort reader 5개를 family/kind별 계측으로 연결했다. 반복 `--test` 옵션으로 NAS 전용 DB에서 realtime writer/replay, market-event revision/rollback, theme replacement 3개 검사를 함께 실행했고 모두 통과했다(3.239초). 운영 query와 native connection 경계는 유지했다. ZIP 1,312,198바이트(SHA-256 `9C3AA646A1E43A8BCA4670216E32068DC5F54A739D814AC35A70970630EC1BC2`)의 NAS 호스트 hash가 일치한다.

2026-09-28 O12 external/news READ batch 완료: 활성 caller의 `load_external_bars`, `news_request_count`, `find_news_ai_revision` 3개 reader를 family/kind별 계측했다. NAS 전용 PostgreSQL에서 external bar replay, concurrent request-budget claim, AI result/revision atomicity 테스트를 반복 `--test`로 함께 실행해 통과했다(3 tests, 6.748초). 기존 SQL·반환값·native transaction은 유지했다. ZIP 1,312,481바이트(SHA-256 `E35BBE3A5712ECFCE47D58E93F1CD02C0FEABADCB4520848E7AA7C06AD7A69C8`)의 NAS 호스트 hash가 일치한다.

2026-09-28 O12 credential/account READ batch 완료: activation lookup, profile list/activation list, account/market-profile settings, binding list, account scope 7개 reader에 `read.credential`·`read.account` context를 연결했다. SQL·반환값·per-call native transaction 및 writer 경계는 유지했다. 구문·diff 검사와 account identity/central DB 회귀 62건 통과; 공통 계층 단위검사는 52건 중 12건 통과·40건 환경 skip. 추가 묶음 113건은 6건이 bundled Python의 `fastapi`·`starlette` 미설치로 로드 오류였으며 코드 회귀 통과로 표기하지 않는다. 전용 PostgreSQL 묶음 4건이 NAS에서 통과했다(15.970초): identity/binding revision, account settings CAS, credential profile lifecycle, vault commit 뒤 activation replay/rollback을 확인했다. ZIP(1,321,611바이트, SHA-256 `8864DE22377379BF1DDDC8BD38FD5B9880C246A260D40481F471BE9CBA639684`)의 NAS host hash 일치. 다음 실제 reader는 execution intent·broker order·event·mock control 조회이며 주문 상태 복구와 연결된 경계다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 O12 execution READ batch 완료: `ExecutionRepository`의 intent ID·active intent·broker order lookup·intent/account event 조회와 mock automation control 조회 6개를 `read.execution`·`read.mock_automation` family의 독립 kind로 계측했다. 기존 SQL·scope·결과 decoding·per-call native transaction을 유지했다. 관련 로컬 unittest 108건 중 68건 통과·40건 환경 skip. NAS 전용 PostgreSQL v1에서 원장·control CAS 2건 통과, stop recovery는 `writer_kind`만 고른 이전 metrics assertion으로 실패했다. 조회와 저장의 family를 구분하도록 고친 v2에서 같은 3건이 통과했다(6.324초). ZIP(1,321,976바이트, SHA-256 `6107490800BF13A6D7B04417A907924B6DF8A2111039838E9659BE87B80A73E9`)의 NAS host hash 일치. 운영 DB·서버 이미지는 변경하지 않았다.


2026-09-28 O12 계좌 설정 writer 공통 관측 pilot 완료: `save_account_settings`와 `save_market_profile_settings`에 `account.settings`의 독립 kind를 추가했다. real/mock 저장과 real market-role 전환의 실제 호출, advisory lock, CAS·reader 계약을 보존했고 관련 로컬 회귀 45건이 통과했다. 전용 PostgreSQL `test_account_and_market_profile_settings_keep_cas_fence_and_independent_commits`도 통과했다(4.861초): 동시 real/mock 저장·replay·CAS rollback·market-profile 선택·해제 차단과 global 설정 row 원복을 확인했다. ZIP `kiwoom-db-access-account-settings-pilot-20260928.zip`(1,317,135바이트, SHA-256 `E0B36DF81E0F9D2A5206BEF3E5429624E9FF2442B4FA1087A4C02C2B7D24033F`)의 NAS 호스트 해시가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 실계좌 event/recovery PostgreSQL 관측 pilot 완료: `PostgresQueryStore.save_real_account_event`와 `save_real_account_recovery`에 `account.real_monitor`의 독립 kind를 추가했다. `RealAccountRuntime`의 `asyncio.to_thread` 호출, 이벤트 큐의 pending/retry·종료 경계, 두 메서드 각각의 per-call native transaction과 `credential-activation` advisory lock, 저장 문서 형식은 유지했다. 공통 DB 검사 40건과 실계좌 테스트 discovery 46건이 통과했다. 전용 DB 통합검사는 PC URL 부재로 로컬에서 skip됐고, NAS 컨테이너의 `test_real_account_event_and_recovery_keep_replay_fence_and_separate_commits`는 통과했다(1.076초): 동시 저장, replay, stale settings fence, rollback, metrics와 cleanup을 확인했다. 검증 ZIP SHA-256 `8E6A4205E86F8B453076788F5CB31705464BF38EA3E7E8CA871DCB6E4D7E8833`의 NAS 호스트 대조도 완료했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 PC 과거 뉴스 BODY/RULE 완료 writer 검증 완료: 전용 PostgreSQL `test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback` 통과(4.890초). BODY revision 저장 및 RULE assessment/event/membership/job 완료 replay와 writer kind 분리, event/membership 저장 뒤 강제한 실패의 전체 rollback을 확인했다. 컨테이너 전용 DB 실행 결과다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 PC 과거 뉴스 BODY/RULE 완료 경로 로컬 수정: `PostgresQueryStore.complete_external_historical_news_job`이 공통 wrapper를 호출하면서 `DBWriterContext`와 `open_observed_connection`을 import하거나 `writer`를 정의하지 않아, 실제 호출 전에 NameError가 발생하는 코드를 확인했다. 기존 한 native transaction 안의 BODY revision·RULE job 또는 assessment·event/membership·job 완료를 유지하며 `news.external_finish`의 BODY/RULE kind를 각각 등록했다. 공통 DB·PC 과거뉴스 로컬 회귀 54건 중 53건 통과, 전용 PostgreSQL 1건은 PC URL 부재로 skip됐다. 전용 검증 ZIP(1,317,673바이트, SHA-256 `CACF15D08022D5C2D8905EBBA6DAEC8A818E0CC6B2557866CCC21B5EF70F4250`)을 NAS 호스트 `/tmp`에 전송해 해시를 대조했다. 이후 컨테이너 전용 DB에서 BODY/RULE replay·부분 실패 rollback 검사가 통과했다(4.890초). 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 연구 observation export writer pilot 완료: `PostgresQueryStore.create_observation_export`의 기존 per-call native connection context를 공통 wrapper로 감싸고 `research.observation_export / research_observation_export_create`를 registry에 등록했다. revision 선택→fixed manifest→ordered membership 저장과 `load_observation_export_page` reader 경계를 보존했다. 전용 PostgreSQL `test_research_export_keeps_fixed_membership_and_rolls_back_partial_failure`가 통과했다(0.533초): 고정 membership pagination, 이후 revision 비편입, member 부분 저장 실패 시 manifest·member rollback, metrics와 fixture cleanup을 확인했다. 관련 프로젝트 `.venv` 단위검사 156건과 구문·ZIP import 검사도 통과했다. 당시 정적 원장 102/29/6,671/0은 운영 writer·transaction 수가 아니다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 credential profile writer 공통 관측 pilot 완료: `create_credential_profile`, `register_credential_profile`, `rename_credential_profile`, `archive_credential_profile`에 각각 `credential.profile` writer kind를 적용했다. 기존 per-call native transaction과 global `credential-activation` advisory lock은 유지했다. 로컬 회귀 142건 중 141건 통과, 전용 DB 검사는 PC URL 미설정으로 skip됐다. NAS 전용 PostgreSQL `test_credential_profile_writers_preserve_replay_lifecycle_and_independent_metrics`가 통과했다(6.812초): 동시 동일요청 생성의 receipt/profile 단일성, digest 충돌 rollback, rename/archive replay, archived 상태·label 보존, register 재호출과 kind별 commit/rollback metrics·cleanup을 확인했다. ZIP `kiwoom-db-access-credential-profiles-pilot-20260928.zip`(1,312,748바이트, SHA-256 `1880FED52C2E95512305EF685B54250C7CB934EE9FE6196D1273F6205A1239CC`)의 NAS host SHA-256과 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 credential activation finalize 공통 관측 pilot 완료: vault 암호화 파일 저장 후 호출되는 `finalize_credential_activation`을 `credential.activation / credential_activation_finalize` writer context로 계측했다. 기존 per-call connection, global advisory lock, binding revision·activation receipt·credential profile·account settings를 기록하는 단일 native transaction은 유지했다. 관련 로컬 회귀 115건이 통과했다. 전용 PostgreSQL `test_credential_activation_finalize_replays_and_rolls_back_after_file_commit`가 통과했다(8.831초): vault file commit 후 DB 저장 전 상태, 두 동시 finalize의 단일 receipt/binding, 설정 충돌 후 activation·profile·binding/settings rollback, committed vault 파일 유지, call ID/backend PID·commit/rollback metrics 및 비밀 값 미기록을 확인했다. 임시 vault fence는 검사 프로세스 안에서 격리했다. 검사 ZIP `kiwoom-db-access-credential-activation-pilot-20260928.zip`(1,311,643바이트, SHA-256 `D60983CAEE9E9533ADA0F3FB789F1424687E6278599766F3342073F7857CDA94`)의 NAS host SHA-256과 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 계좌 identity/binding 공통 관측 pilot 완료: 실제 identity 등록과 legacy/bootstrap binding append 2개 PostgreSQL writer에 관측 context를 연결했다. 독립 native transaction과 per-profile advisory lock을 유지했다. 로컬 관련 회귀 123건, 전용 PostgreSQL `test_account_identity_and_binding_keep_separate_commits_and_revision_lock`가 통과했다(4.985초). identity replay가 같은 account_ref를 반환하고, 잘못된 계좌 binding rollback, 같은 profile의 동시 append에서 revision 1·2, reader·call ID·backend PID·cleanup을 확인했다. 검사 ZIP `kiwoom-db-access-account-identity-pilot-20260928.zip`(1,301,991바이트, SHA-256 `2A9EB64AAAD8339488872BABE7E2D934421B1999DE0397F2608B661374B07305`)의 NAS 호스트 hash가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다. 이 검증은 vault commit 후 `finalize_credential_activation`이 binding revision을 포함해 수행하는 별도 multi-table transaction을 포함하지 않는다.

2026-09-28 O12 execution ledger 공통 관측 pilot 완료: `PostgresQueryStore`의 intent 생성, event 적용, 계좌 snapshot, runtime lease 획득·해제 5개 쓰기에 writer context와 공통 DB timing을 연결했다. 기존 per-call native transaction과 event INSERT+intent projection 원자성을 유지했다. 로컬 관련 회귀 101건, 전용 PostgreSQL `test_execution_ledger_and_lease_keep_native_boundaries_and_metrics`가 통과했다(3.967초). replay·reader 결과, 실패 rollback, lease CAS와 소유권 상실 거부, backend PID·call metrics를 확인했다. 검사 ZIP `kiwoom-db-access-execution-ledger-pilot-20260928.zip`(1,301,195바이트, SHA-256 `8CD43418B57598B888F8BBE488C574B1F3AA37E5DC3B9C4B14F1A18B0F7B6D24`)의 NAS 호스트 해시가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `document:journal_news_link`·`document:journal_v2_news_links` pilot 완료: legacy와 계정별 v2 collection UPSERT를 명시 공통 관측에 추가했다. legacy·2개 v2 계정 identity 분리, replay, v2 tombstone의 로컬 pull merge와 account-specific reader, rollback, backend PID·call/legacy metrics가 전용 PostgreSQL 검사에서 통과했다(8.958초). 관련 로컬 회귀 176건과 구문 검사가 통과했다. ZIP SHA-256 NAS host 대조 완료. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `CentralJournalSyncService` PostgreSQL document 관측 pilot 완료: 기존 per-collection UPSERT 경계에 v1/v2 20종과 sync-state 2종을 명시 등록했다. 실제 service 경로에서 legacy/v2 계정 분리, v1/v2 tombstone state, replay 동일성, 서로 다른 collection transaction 보존과 앞선 저장 commit 뒤 후속 실패 rollback을 검증했다. 전용 PostgreSQL `test_journal_sync_collections_keep_scopes_and_independent_observed_writes`가 통과했다(21.111초). ZIP 2,177,779바이트, SHA-256 `C084B592D1A2C367937DF8BEB4134B8CDDC71F4068CF7938517FE2A4196EF790`; NAS 호스트 SHA-256도 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `CentralSettingsSyncService`의 `app_settings`·`app_column_settings` 공통 관측 pilot 완료: 각 collection push의 기존 독립 POST/transaction을 유지하면서 두 document kind를 명시 등록했다. 전용 PostgreSQL `test_central_settings_collections_replay_and_keep_independent_transactions`에서 실제 sync 호출, 필터·버전 병합, 열 너비 로컬 보존, replay, 첫 transaction commit 후 두 번째 실패 rollback, metrics와 cleanup이 통과했다(4.449초). v1 검사는 SQLite fixture insert 미commit으로 sync 대상이 비어 실패했으며, fixture transaction을 고친 v2에서 통과했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `MarketDataIngestor` → `PostgresQueryStore.replace_minute_bars` / `replace_daily_bars` 공통 관측 pilot 완료: `_replace_bars`의 기존 명시 COMMIT·rollback·close, minute advisory lock, metadata/revision, capture 전용 WAL savepoint와 COMMIT wait sampler를 유지한 채 연결·cursor만 공통 wrapper로 감쌌다. `query_minute`/`query_daily`와 market-bar phase 표본을 동일 `call_id`로 연결했다. 로컬 회귀 102건(전용 DB 검사 2건 skip), 구문·ZIP import 검사와 전용 PostgreSQL `test_query_market_bars_keep_native_history_and_correlate_both_metrics`가 통과했다(1.501초). 실제 day/minute replay, 동일 minute key의 병렬 revision chain, 실패 rollback, backend PID와 두 legacy/common metric correlation이 확인됐다. 검사 ZIP `kiwoom-db-access-query-market-bars-pilot-20260928.zip`(2,179,903바이트, SHA-256 `0FD65D7CB04C76C2F595F51061403E2E5AFC234810CB2A5F0F28FD6DD4F75D84`)의 NAS host 해시가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `CandidateMonitor`의 decision/event와 checkpoint writer 관측 pilot 완료: PostgreSQL `save_shadow_evaluation`과 `save_shadow_monitor_state` 각각에 공통 wrapper를 연결하고 registry에 별도 writer kind로 등록했다. 원래의 per-observation decision/event transaction과 poll batch checkpoint transaction을 유지했다. 로컬 관련 검사 46건이 통과했고 전용 PostgreSQL `test_shadow_evaluation_and_checkpoint_keep_replay_and_independent_commits`가 통과했다(1.352초). 전용 DB에서 checkpoint 실패 뒤 앞서 commit된 candidate event와 reader sequence가 유지되고, 같은 event replay는 중복 row를 만들지 않으며, immutable decision 오류는 rollback되고, 두 writer의 call ID·backend PID가 분리됨을 확인했다. 검사 ZIP `kiwoom-db-access-shadow-candidate-pilot-20260928.zip`(1,299,765바이트, SHA-256 `CF5FCD256FBBD38815A670F49C3FC7407D6E6E5175AE370CBD25203DC1A2FD5E`)의 NAS host hash가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 PostgreSQL writer inventory 대조 완료: 대조 당시 소스의 정적 원장을 재생성해 `PostgresQueryStore` 메서드 102개, 리터럴 driver 연결 지점 29개, 모든 backend 후보 API 호출 6,664개, AST parse 오류 0개를 확인했다. 이 값들은 연결·호출 후보 수이며 운영 writer나 transaction 수가 아니다. 실제 production route `/api/v1/research/observations`의 watermark 미지정 경로가 `PostgresQueryStore.create_observation_export`를 호출하지만 common writer context/registry kind가 없어 활성 미계측 writer 후보로 확인됐다. 이 메서드는 immutable observation revision을 선택하고 fixed manifest 및 ordered member를 하나의 native transaction에 저장하며, 요청은 `load_observation_export_page`로 읽는다. 다음 구현은 이 manifest·membership 원자성과 기존 reader 결과를 보존하는 관측 pilot 및 전용 PostgreSQL 검증이다. scope alias helper와 `save_five_minute_bars`는 production caller가 확인되지 않아 활성 writer 후보에서 제외했다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `document:news_ai`·`document:news_ai_shared`·`document:news_request_usage` 관측 pilot 완료: 증분 sync UPSERT 세 kind를 명시 목록에 추가했다. 로컬 회귀 92건 통과(27 skip 포함), 구문·diff 검사 통과. v1 전용 DB 검사는 rollback까지 포함한 `news_ai` 호출 3건을 2건으로 세는 assertion 오류로 실패했고, kind별 count를 수정한 v2에서 replay·rollback·metrics·cleanup이 통과했다(1.716초). NAS 호스트 SHA-256 대조 완료. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `document:news_article`·테마 저장 공통 관측 pilot 완료: 뉴스 기사 projection/revision/BODY job의 기존 단일 transaction과 `theme_profile`·`theme_stock`·`theme_metadata`의 증분 UPSERT·각각 독립 교체 transaction을 계측했다. metadata snapshot lineage와 native connection semantics를 유지했다. 로컬 관련 회귀 92건(27 skip 포함), NAS 전용 PostgreSQL 기사 저장·replay·실패 rollback 검사 1건(2.551초), 테마 UPSERT·교체·snapshot·실패 rollback 검사 1건(3.652초)이 통과했다. v2 검사 ZIP의 NAS 호스트 SHA-256이 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset.statistics_cache` / `dataset:top20_statistics_day` 공통 관측 pilot 완료: `load_top20_statistics`의 기존 per-call native connection context를 공통 wrapper로 감쌌다. warm hit의 cache SELECT와 cold fill의 cache SELECT→날짜 advisory lock→원천 snapshot SELECT→cache executemany 및 context 종료 COMMIT 순서를 유지했다. 관련 로컬 회귀 92건 통과(27 skip 포함), 전용 PostgreSQL `test_top20_statistics_cache_preserves_warm_cold_and_concurrent_reads`가 통과했다(0.820초): cold/warm 결과, cache 행 단일성, cache 제거 뒤 동시 조회 결과와 독립 commit/metrics를 확인했다. ZIP `kiwoom-db-access-top20-statistics-cache-pilot-20260928.zip`(2,172,876바이트, SHA-256 `EFE04DAFFEA61DC014002F4D0A7DB7619521D06D7FB7E94E658B3CEABBB6E5B2`) NAS host hash 일치. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset:investor_flow`·`dataset:stock_fundamentals`·`dataset:nxt_eligibility` 공통 관측 pilot 완료: 실제 `MarketDataIngestor` 응답 경로 세 가지를 동종 kind 관측 목록에 추가했다. fundamentals/NXT의 기존 `central_documents` 저장과 후속 dataset snapshot transaction을 분리한 채 유지했다. 관련 로컬 회귀 102건 통과(27 skip 포함), NAS 전용 PostgreSQL `test_investor_fundamental_and_nxt_dataset_writes_preserve_reader_boundaries`가 통과했다(1.438초): 세 snapshot replay/readback·제약 오류 rollback·metrics/call ID와 document writer의 독립 commit을 확인했다. ZIP `kiwoom-db-access-market-ingest-datasets-pilot-20260928.zip`(2,172,419바이트, SHA-256 `0C698F1E86362BBDF856BE2A4AB8FDE54ECE7C8C37D704677526CCEAC7A0B1CE`) NAS host hash가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset:top20_index`·`dataset:market_index_chart` 공통 관측 pilot 완료: 동종 batch만 wrapper로 감싸고 기존 advisory lock·통계 캐시 무효화·metadata 저장·transaction 경계를 보존했다. 관련 로컬 단위검사 137건 통과(27 skip 포함), 전용 PostgreSQL `test_top20_and_market_index_writes_invalidate_statistics_atomically`가 통과했다(1.224초). 캐시 invalidation/rebuild, replay, 실패 rollback에서 원본 snapshot과 cache 보존을 확인했다. ZIP `kiwoom-db-access-index-cache-pilot-20260928.zip`(2,171,597바이트, SHA-256 `DC800F69F0A74085541A72781F00BA2FD52D9F8AEDE3825B9FAB0986EE148DDB`) NAS host hash 일치. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset:ranking`·`dataset:top20_membership` 공통 관측 pilot 완료: `save_dataset_snapshots`에서 두 kind의 동종 batch만 common wrapper 대상에 추가했다. 기존 per-call connection, 비동기 COMMIT 설정, snapshot→metadata→revision 저장 순서와 rollback/close를 보존한다. 관련 access/database/revision/TOP20 로컬 회귀 143건 통과(27 skip 포함), 구문검사 및 `git diff --check` 통과. 전용 검증 ZIP `kiwoom-db-access-ranking-top20-dataset-pilot-20260928.zip`(2,170,811바이트, SHA-256 `EC4BAF4A5DFF8D945A99E6BD48B7AF923FB869CA2E0146B6A6056FF61FDBCC35`)의 NAS host hash가 일치하고, 사용자 실행 결과 전용 PostgreSQL `test_ranking_and_top20_membership_revisions_and_metrics_are_preserved`가 통과했다(2.952초). replay·revision lineage·중간 실패 rollback·backend PID/SQL/commit metrics·legacy call ID 연결이 확인됐다. 혼합 kind batch는 다음 분석 대상으로 남는다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset:new_high`·`dataset:program_flow` 공통 관측 pilot 완료: 두 kind의 동종 batch만 공통 wrapper로 연결했다. `MarketDataIngestor`와 TOP20 program-flow flush는 각각 이 저장 경계를 호출하고, `/api/v1/market/snapshots/{kind}`가 같은 dataset reader를 사용한다. 혼합 kind·ranking·TOP20 경로는 제외했다. 로컬 관련 회귀 92건 중 65건 통과·27건 skip, 구문검사와 diff check 통과. 전용 PostgreSQL `test_new_high_and_program_flow_dataset_replay_preserve_readers_and_correlate_metrics`가 통과했다(0.362초): 각 kind의 replay/readback, 제약 오류 rollback, backend PID·commit/rollback/SQL metrics, legacy call ID 연결 및 cleanup을 확인했다. ZIP(2,168,071바이트, SHA-256 `DC6D160F31E1FC8FBB427225B0DB06985A02B11AC0AA4EE1B33F357B37152061`)의 NAS host hash가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 `dataset:market_state` 공통 관측 pilot 완료: 동종 `market_state` 배치만 기존 per-call 연결을 `open_observed_connection`으로 감싸고, 기존 비동기 COMMIT 설정·저장 순서·rollback/close를 보존했다. 혼합 kind와 다른 dataset writer는 기존 경로를 유지한다. 로컬 PostgreSQL access/database 단위검사 92건 중 65건 통과·27건 skip, 구문검사 통과. v1 검사는 replay 시 갱신되는 `saved_at`을 전체 행 equality로 비교해 실패했으며, payload/key 보존과 저장시각 증가를 검사하도록 고쳤다. 전용 PostgreSQL v2 `test_market_state_dataset_replay_and_failure_preserve_reader_and_correlate_metrics`가 통과했다(0.193초): replay/readback, 제약 오류에 대한 rollback, backend PID와 commit/rollback/SQL metrics, 기존 성공 metric call ID 연결 및 cleanup을 확인했다. v2 ZIP(2,167,632바이트, SHA-256 `D9D38B9537B019EBFCF367ED56030756AF61A37ADC12C1450F27FBC18ECEC288`)의 NAS host hash가 일치한다. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 external market bar writer pilot 완료: `save_external_bars`의 5m·1d native transaction을 각각 공통 관측했다. 전용 PostgreSQL reader/replay·독립 commit·call correlation 검사 1건이 0.994초에 통과하고 임시 instrument 행 정리를 확인했다. NAS ZIP host hash와 일치. 운영 DB·NAS 앱 이미지는 변경하지 않았다.

2026-09-28 O12 PostgreSQL 공통 관측 realtime document 묶음 완료: 실시간 flush의 계좌 매수 편입 종목·0G 가격 제한 기준 문서 writer 두 개를 `document.collection` 명시 kind로 계측했다. 전용 PostgreSQL `test_realtime_document_batch_preserves_readers_and_correlates_metrics` 통과(0.714초); reader 결과와 동일값 replay, call ID/commit metrics를 확인했다. v1 package initializer 누락 문제는 v2에서 수정했고 NAS host SHA-256 대조 완료. 운영 DB와 NAS 앱 이미지는 변경하지 않았다.

2026-09-28 공통 DB 실시간 writer 4종 pilot 완료: `PostgresQueryStore.save_realtime_snapshots`, `save_minute_bars`, `finalize_minute_bars`, `save_second_trade_bars`에 공통 계측을 적용하고 기존 successful legacy sample에 같은 call ID를 연결했다. 각 writer의 native transaction, 분봉 scope lock·operation marker·observation revision, realtime collector의 호출 순서를 유지했다. 실시간 저장·queue 복구·공통 access 회귀 130건이 통과했다. 전용 PostgreSQL `test_realtime_writer_batch_preserves_replay_lineage_and_independent_commits`에서 latest/minute/finalize/second replay, 분봉 중간 실패 rollback, observation history, 절대 초봉 최신값, backend PID·transaction metrics와 legacy call ID 연결 및 cleanup이 통과했다(2.143초). 검사 ZIP `kiwoom-db-access-realtime-writers-pilot-20260928.zip`(1,292,240바이트, SHA-256 `F6DD2985EFFADEACB79F2ACCDE11C533D3B8559683B47D72F5F416FFF2EA3650`)의 NAS 호스트 hash가 일치한다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 PC 종목 과거뉴스 검색/원문 분리 운영: 검색 페이지와 기사·종목별 원문 대기열을 같은 SQLite 트랜잭션으로 저장한다. 검색 수집기는 원문·BODY/RULE 종료를 기다리지 않고 다음 검색 작업으로 넘어간다. 별도 PC 원문 수집기는 대기열을 이어받아 기존 원문 2초 제한 및 BODY/RULE 준비를 실행한다. 재시작 가능한 claim·준비 실패 후 원문 재요청 방지 등 관련 로컬 회귀 61건이 통과했다. 기존 수집기를 작업 경계에서 멈추고 두 새 수집기를 시작했으며 첫 분리 검색 작업 `034020` 범위의 75페이지·749건은 약 71초에 `search_complete`가 되었고 원문기 처리 후 `complete`가 확인됐다. 원문 1건의 준비 DB `ready`도 대조했다. 이 경로는 PC 로컬 수집이며 NAS 과거뉴스 적재·운영 서버 재빌드는 하지 않았다. 대기열 처리율은 기사 중복·언론사 구성에 따라 달라지므로 전체 완료 기간 개선율은 아직 확정하지 않는다.

2026-09-28 공통 DB `document:execution_mock_automation_recovery_decisions` / `document:execution_mock_automation_current_recovery` pilot 완료: mock recovery의 admission lineage와 최신 risk 검사를 유지하고 immutable 결정/current projection 두 transaction을 각각 공통 계측했다. 첫 전용 PostgreSQL 시도와 v2는 fixture의 조회 projection 누락으로 recovery 호출 전 실패했으나, v3에서 seed를 repository 저장 경계로 맞추고 두 reader round-trip을 확인했다. 관련 로컬 unittest 60건과 구문/whitespace 검사가 통과했다. 전용 PostgreSQL 테스트 `test_mock_automation_recovery_preserves_lineage_and_separate_projection`도 통과했다(4.946초). 2회 복구 이력, current projection 별도 쓰기 및 실패 뒤 재시도, 최신 risk 불일치 거부, 5개 transaction metric/call ID 및 cleanup 검증을 통과했다. v3 ZIP은 NAS에 전송하고 SHA-256을 대조했다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:execution_mock_automation_decision_gates` / `document:execution_mock_automation_approved_gates` pilot 완료: `save_mock_automation_decision_gate`의 gate history와 승인 상태에만 기록되는 intent별 approved projection을 각각 계측했다. 기존 두 `upsert_documents` transaction과 actual submit 앞의 호출 순서를 유지한다. 승인/차단/replay·approved projection 실패 후 재시도·최신 risk 거부 통합검사가 통과했다. 관련 local unittest 114건, SQLite fixture/storage preflight, ZIP import/test load 1건과 전용 PostgreSQL `test_mock_automation_decision_gate_preserves_approval_boundary`가 통과했다(11.141초). PG 검사는 gate 저장 계층만 호출하며 주문 제출은 하지 않았다. 기존 unit test가 실제 dispatch의 동일 intent 단일 제출/replay를 확인한다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:execution_mock_automation_dispatch_receipts` / `document:execution_mock_automation_dispatch_by_intent` pilot 완료: submit 뒤 `save_mock_automation_dispatch_receipt`가 쓰는 immutable receipt와 intent reader projection을 별도 transaction으로 계측했다. 저장 함수의 gate 검증·재생 및 transaction 순서는 유지했다. 관련 mock automation·DB·계측 로컬 회귀 114건과 SQLite fixture/storage preflight가 통과했다. 전용 PostgreSQL `test_mock_automation_dispatch_receipt_recovers_separate_intent_projection`에서 이력 commit, projection 실패 뒤 이력 보존, replay 재시도, 두 reader 및 metric call ID 검사가 통과했다(0.591초, cleanup assertion 포함). 이 검사는 receipt 저장 계층만 호출해 주문 transport를 실행하지 않았다. NAS 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:execution_mock_automation_stop_revisions` / `document:execution_mock_automation_current_stop` pilot 완료: 긴급 중지의 immutable stop history와 admission별 latest projection을 각각 계측했다. STOPPED control 저장 후 런타임 주문 차단, stop 저장 순서를 그대로 유지했다. 관련 로컬 회귀 113건과 SQLite control/history/projection preflight가 통과했다. 전용 PostgreSQL `test_mock_automation_stop_preserves_closed_control_and_recovers_projection`에서 projection 실패 뒤 STOPPED control·history 유지, repository 재시도와 latest reader, 두 call ID 및 cleanup 검사가 통과했다(1.575초). 주문 transport는 실행하지 않았다. 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:execution_mock_automation_risk_snapshots` / `document:execution_mock_automation_current_risk` pilot: 모의 계좌 위험 스냅샷의 불변 이력 저장과 계좌별 현재 projection 저장을 각각 공통 계측에 등록했다. 원래의 두 독립 transaction과 단조 revision 규칙을 유지했다. 관련 로컬 위험·admission·DB·계측 회귀 114건, ZIP import·테스트 로드가 통과했다. 전용 PostgreSQL에서 저장/replay, current write 실패 뒤 재시도 복구, 단조 revision 거부, metric call ID와 cleanup 검사가 통과했다(0.590초). NAS 전송과 원격 해시 대조를 확인했고 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:credential_vault_state` pilot: `CredentialStore`의 암호화 파일 저장과 PostgreSQL revision fence write를 공통 계측에 연결했다. 파일 commit point, revision recovery, per-call connection 및 transaction 경계는 그대로 유지했다. 관련 로컬 credential-vault·DB·계측 회귀 114건, ZIP import 및 테스트 로드를 확인했다. 전용 PostgreSQL에서 파일 commit 뒤 fence 실패·vault 재시작 복구, 파일 write 실패의 fail-closed, metric call ID와 cleanup 검사가 통과했다(1.416초). 운영 DB·서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:execution_mock_automation_runner_current` pilot: mock automation runner checkpoint write를 공통 계측에 추가했다. 저장 projection의 version/spec/run/hash gate 및 restore 로직은 유지했다. 로컬 mock runner·공통계측·DB 테스트 97건과 전용 PostgreSQL 최초 저장·상태 갱신·재시작 복원·복원 뒤 추가 write 없음 검사가 통과했다(0.658초, cleanup assertion 포함). 운영 DB나 서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:news_assessment` pilot: 뉴스 RULE 경로의 assessment projection 저장을 공통 계측에 추가했다. `_run_rule`의 기존 async-to-thread 호출과 후속 news event revision 경계를 유지했다. `/api/v1/news/history/body`가 같은 문서를 읽으며 query 계층도 join한다. 관련 로컬 테스트 103건과 전용 PostgreSQL 저장·readback/replay·metric 검사 1건이 통과했다(2.749초, cleanup assertion 포함). 운영 DB나 서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:external_market_collection_status` pilot: `YahooDelayedMarketCollector._save_status`의 최근 수집 상태 write를 공통 계측에 추가했다. 기존 성공/실패 저장 호출과 transaction을 유지했고 repository 내부 domain reader는 찾지 못했다. 외부시장·공통계측·DB 테스트 100건과 전용 PostgreSQL 실패 상태 write/readback/replay·metric 검사 1건이 통과했다(2.201초, cleanup assertion 포함). 운영 DB나 서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:news_original_publication` pilot: 뉴스 BODY 경로의 원문 발행시각 저장을 공통 계측에 연결했다. `_run_body`의 publication marker 저장과 별도 body-revision transaction 순서는 유지했고, `_run_rule`의 기존 reader가 읽는 projection이다. 관련 로컬 테스트 65건, ZIP import/load 검증, 전용 PostgreSQL `_run_body` 저장·readback/replay·metric call ID 검사가 통과했다(0.317초, cleanup assertion 포함). 운영 DB나 서버 이미지는 변경하지 않았다.

2026-09-28 공통 DB `document:market_event_sessions` pilot: KRX session 관측·정규장 종료·전체 관측 종료 marker writer들을 공통 계측에 추가했다. market-event·DB 테스트 65건과 전용 PostgreSQL 세 transaction·replay·계측 검사 1건이 통과했다(0.611초, cleanup assertion 포함). 이 collection을 읽는 domain call site는 현재 검색에서 찾지 못했다. 운영 적용은 하지 않았다.

2026-09-28 공통 DB `document:condition_search_status` pilot: MarketEventService 조건식 handler의 선택 결과/status projection writer kind를 공통 관측에 추가했다. market-event·DB 회귀 65건과 구문·ZIP import 검증이 통과했고 전용 PostgreSQL handler·reader·계측 검사 1건도 통과했다(0.242초, cleanup assertion 포함). 운영 적용은 하지 않았다.

2026-09-28 공통 DB `document:candidate_flow_finalization` pilot: TOP20 장후 수급 보완 완료 marker writer kind를 공통 관측에 추가했다. 관련 idempotency·DB 테스트 55건 및 ZIP import 검사가 통과했고, 전용 PostgreSQL replay·reader skip·계측 검사 1건이 통과했다(0.767초, cleanup assertion 포함). 운영 적용은 하지 않았다.

2026-09-28 공통 DB `document:candidate_flow_capture` pilot: TOP20 최초 편입 종목의 수급 원본 조회 후 쓰는 capture marker kind를 공통 관측에 추가했다. 관련 회귀 99건 통과(1건 skip), ZIP 내부 import·테스트 이름 로드·구문 확인, NAS 해시 대조와 전용 PostgreSQL replay·reader·계측 검사 1건을 통과했다(0.331초, cleanup assertion 포함). 운영 적용은 하지 않았다.

2026-09-28 공통 DB `document:market_data_coverage_intraday` pilot: TOP20 신규 편입 종목의 장중 분봉 backfill marker writer kind를 공통 관측에 추가했다. 관련 회귀 99건 통과(1건 skip), ZIP 실행·소스 import·테스트 이름 로드와 NAS 해시 대조를 확인했고 전용 PostgreSQL replay·reader skip·계측 검사 1건이 통과했다(0.974초, cleanup assertion 포함). NAS 운영 적용은 하지 않았다.

2026-09-28 공통 DB `document:market_data_coverage` pilot: 분봉 coverage 완료 marker writer kind를 공통 관측에 추가했다. 관련 회귀 99건 통과(1건 skip), 통합 ZIP 실행기·소스 import·테스트 이름 로드와 구문검사를 확인했고 전용 PostgreSQL reader/replay·계측 검사 1건이 통과했다(0.661초, cleanup assertion 포함). NAS 운영 적용은 하지 않았다.

2026-09-28 공통 DB `document:market_data_coverage_daily` pilot: 일봉 완료 marker의 `upsert_documents` writer kind를 공통 관측에 추가했다. 일봉 저장과 완료 marker transaction은 기존처럼 분리되어 있다. 관련 로컬 99건 통과(1건 skip). 첫 ZIP은 `scripts`, v2는 `tests.integration` import 누락으로 테스트 시작 전에 실패했다. v3의 ZIP 자체 import와 NAS 해시를 확인했고 전용 PostgreSQL replay·reader·metric 연결 검사 1건이 통과했다(0.263초, cleanup assertion 포함). NAS 운영 적용은 하지 않았다.

2026-09-27 공통 DB `document:market_index_chart_coverage` pilot: 지수 차트 저장 완료 marker writer를 공통 관측 목록에 추가했다. 기존 per-market dataset save와 완료 marker transaction은 분리된 채 유지된다. 관련 TOP20·DB·계측 로컬 회귀 137건 및 구문검사, 전용 PostgreSQL replay와 재실행 skip reader/metric call ID 연결 검사 1건(0.307초, cleanup assertion)이 통과했다. NAS 운영 적용은 하지 않았다.

2026-09-27 PC 과거뉴스 수집 병목 개선: 종목뉴스 준비 작업 295건의 기사 원장 조회 중앙값은 875ms였고, `news_search_observations` 약 150만 행을 기사마다 훑는 실행 계획을 확인했다. `news_articles`의 기존 원문/보관/fallback identity 표현식 인덱스를 추가하자 실제 원장은 해당 인덱스와 observation PK로 조회했다. 새 작업 자식에서 적용 전 최근 300건 조회 중앙값 984ms·p95 1,187ms, 적용 후 583건 중앙값 15ms·p95 16ms였으며 준비 대기 중앙값도 2,187ms에서 0ms로 줄었다. 시황뉴스는 100건 페이지 BODY 준비의 4-worker 처리량 제한을 확인하고 시작 기본값을 6으로 조정해 날짜 경계에서 정상 정지·재개했다. 100건 페이지 callback의 재개 전 최근 100건 중앙값 2,867ms, 재개 후 초기 27건 중앙값 2,063ms·p95 2,282ms였다. 두 기간의 날짜·기사 구성은 동일하지 않으므로 전체 수집 완료시간 개선율은 아직 확정하지 않는다. 관련 로컬 회귀 59건 통과. 네이버 검색 요청 간격과 403 cooldown은 유지했다.

2026-09-27 공통 DB `document:historical_highs` 로컬 pilot: TOP20 역사적 고가 목표 save 경로를 기존 document 계측 목록에 추가했다. 관련 TOP20·DB·계측 로컬 회귀 137건과 구문검사, 전용 PostgreSQL 저장/replay/reader·기존 metric call ID 연계 검사 1건이 통과했다(0.359초, cleanup assertion 포함). NAS 운영 적용은 하지 않았다.

2026-09-27 공통 DB `document:top20_daily_entrants` 로컬 pilot: 실제 TOP20 entrant save 경로를 공통 계측에 추가했다. 관련 TOP20·DB·계측 로컬 회귀 137건과 전용 PostgreSQL 저장/replay/reader·기존 metric call ID 연계 검사 1건이 통과했다(0.199초, cleanup assertion 포함). NAS 운영 적용은 하지 않았다.

2026-09-27 시황뉴스 URL 인덱스 적용 후 지속 계측: WORLD 100건 페이지의 callback은 적용 전 14회 중앙값 55,305ms, 적용 후 872회 중앙값 2,907ms·p95 4,078ms였다. 최근 60분에는 WORLD 날짜 37일·기사 44,201건이 완료됐고 수집기는 2021-09-16으로 진행 중이다. 남은 달력일 1,833일을 같은 37일/시 속도로 단순 환산하면 약 49.5시간이나 연도별 기사량·네트워크 응답 차이는 반영하지 않은 추정이다. 적용 후 `market_preparation_error` 85건은 모두 본문과 목록 요약 부재로 failed 행에 기록됐으며, 원본은 보존된다. 앞서 `page 83 repeats the previous page`가 나온 2020-07-08 FLASH는 원장에서 `complete_boundary` 83페이지·1,230건이고 WORLD는 `empty`라 현재 미완료 날짜가 아니다.

2026-09-27 PC FLASH/WORLD 기사 준비 지연 원인·수정: 같은 수집기의 WORLD 100건 페이지 callback이 44~65초 걸렸다. 기사 URL 조회 5건은 SQLite의 `SCAN market_news_articles`로 각 2.28~3.53초였고 종목명 매칭은 0~16ms였다. 로컬 2.9GB 시장뉴스 DB에 비고유 `article_url` 인덱스를 생성하는 데 5.05초가 걸렸으며 실행 계획이 `SEARCH ... USING INDEX`로 바뀌었다. 같은 준비 경로 8건의 인덱스 적용 전 조회는 2.25~2.42초, 적용 후는 0~15ms였다. 실행 중인 수집기의 인덱스 적용 뒤 WORLD 100건 callback 두 회차는 2.77초·2.64초로 줄었다. 2021-07-07 WORLD는 원본 1,602건과 준비 ready 1,602건, 실패·누락 0건으로 완료됐고 다음 날짜로 진행했다. 기존 DB에도 재개 시 인덱스를 생성하도록 schema 초기화에 추가하고, 준비 worker의 큐·조회·BODY·RULE·SQLite 저장 단계 계측과 보고 집계를 로컬 코드에 추가했다. 관련 단위/회귀 20건 통과. 현재 실행 중인 Python은 인덱스의 효과는 사용하지만 새 세부 계측 코드는 다음 재시작부터 사용한다.

2026-09-27 공통 DB `news.historical_market_batch` 로컬 pilot: 준비된 FLASH/WORLD batch import에 계측을 연결하고 advisory lock 및 저장 transaction을 유지했다. 관련 과거뉴스·DB 회귀 108건과 전용 PostgreSQL 동시 replay·reader·실패 rollback 검사 1건이 통과했다(1.252초, cleanup assertion 포함). NAS 운영 적용은 하지 않았다.

2026-09-27 네이버 FLASH/WORLD 과거 수집 스냅샷 지연: 2.85GB SQLite를 NAS 공유에 중간 게시한 1회가 약 10분 20초 걸려 날짜 처리를 막은 것을 확인했다. PC 수집기에 스냅샷 단계별 시간 로그를 추가했고 --publish-every-days 0은 중간 게시를 생략하되 완료 시 최종 스냅샷 게시를 유지한다. 회귀 3건 통과. 이 설정으로 로컬 수집기를 재시작해 2021-07-07 WORLD 작업 중이며 최근 본문 요청은 성공 중이다. 이 실행의 전체 처리량과 완료 시 최종 snapshot 시간은 아직 미측정이고 NAS 공유 직접 확인은 접근 거부로 생략했다.

2026-09-27 공통 DB `news.request_budget` 로컬 pilot: `claim_news_request`에 scope별 공통 관측을 추가했다. 요청 총량·scope 한도 판단, EXCLUSIVE table lock, 허용 시 UPSERT, 거부 시 반환과 native commit은 기존 순서다. 관련 로컬 회귀 117건과 전용 PostgreSQL 동시 claim·scope/hard limit·계측·정리 통합검사 1건이 통과했다(1.436초). NAS 운영 적용은 하지 않았다.

2026-09-27 공통 DB `news.source_page` 로컬 이관: 검색뉴스와 FLASH/WORLD 수집기의 `save_news_source_page`가 쓰는 기사 revision·BODY job·대상 관계·source observation·run·cursor의 기존 한 transaction에 공통 관측을 연결했다. scope별 kind를 구분하고 실제 SQL·기존 COMMIT·reader는 변경하지 않았다. 공통 DB·뉴스 source·중앙 DB 관련 로컬 회귀 117건과 전용 PostgreSQL replay·FLASH reader·중간 오류 rollback 검사 1건(0.642초, cleanup assertion 포함)이 통과했다. 전용 검사 ZIP(1,280,019바이트)의 NAS 호스트 SHA-256이 일치한다. PC 과거뉴스 완료가 helper를 직접 호출하는 별도 transaction은 이 범위가 아니다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 공통 DB `news.job_enqueue` 로컬 이관: AI 작업 후보별 최신 기사·본문 revision 조회와 `central_news_jobs`의 중복 방지 INSERT가 기존 한 native connection transaction으로 유지된다. `NewsService`의 `asyncio.to_thread` 호출과 AI worker의 별도 claim·결과 저장 transaction은 변경하지 않았다. 공통 DB·뉴스 회귀 71건과 전용 PostgreSQL 병렬 replay·reader·중간 실패 rollback 통합검사 1건(0.752초, 정리 assertion 포함)이 통과했다. 전용 검사 ZIP(1,278,863바이트)은 NAS `/tmp` 전송·해시 대조를 마쳤다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 공통 DB `news.event` 로컬 이관: 일반 RULE worker의 `save_news_event_revision` 호출만 공통 관측에 연결했다. 기존 table lock·사건 재사용·event/membership 이력·한 native connection COMMIT을 유지한다. PC 과거뉴스 완료는 같은 helper를 자기 transaction 안에서 직접 호출하므로 범위 밖이다. 관련 공통 DB·사건 이력·PC 과거뉴스 회귀 58건과 전용 PostgreSQL 동시 replay·reader·늦은 membership 실패 rollback 통합검사 1건이 통과했다(1.219초, cleanup assertion 포함). 전용 검사 ZIP(1,275,800바이트)의 NAS 호스트 SHA-256이 로컬과 일치한다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 PC 뉴스 2초 기사 제한 범위: 네이버 검색 종목뉴스의 원문 기사 확인과 네이버 증권 FLASH/WORLD 시황뉴스의 개별 기사 BODY 준비에 2초 요청 제한을 각각 연결했다. 네이버 검색 목록과 시황뉴스 목록 API의 기존 timeout은 변경하지 않았다. 시황뉴스는 목록 요청/재시도, 페이지 저장·준비 callback, 기사 BODY 요청·예외, 준비 실패를 `data/historical_collection/logs/market-news-timing.jsonl`에 기록하고 기존 집계 스크립트가 두 수집기의 JSONL을 읽는다. 관련 뉴스 회귀 73건 통과(1건 skip), 구문 검사 통과. 실행 중인 시황뉴스 Python 프로세스는 수정 전 시작돼 재시작 전까지 이전 제한을 사용한다. 재시작 후 실제 요청·처리량 비교는 아직 하지 않았다.

2026-09-27 공통 DB `news.ai_results` 로컬 이관: `CentralAIService.analyze`의 `asyncio.to_thread` 호출 아래 PostgreSQL `save_news_ai_results`의 최신 결과 문서, 불변 AI revision, 사용량 문서가 기존 한 connection context에서 저장된다. 공통 관측 wrapper만 추가했고 SQL·COMMIT 경계·reader는 변경하지 않았다. 관련 공통 DB·AI 단위 회귀 39건 통과. 전용 검사 ZIP(1,274,924바이트)의 NAS 호스트 SHA-256이 로컬과 일치하며 사용자 제공 컨테이너 출력으로 reader·실패 rollback 통합검사 1건이 통과했다(0.397초). 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 PC 과거뉴스 지연 진단 로컬 변경: 다음 `news-run` 자식부터 검색 요청별 fetch/요청 간격 대기, 403/429 cooldown, 원문 URL별 접속/읽기/파싱 시간과 HTTP·예외, 검색·기사 SQLite 저장, BODY/RULE 제출·종료 대기를 `data/historical_collection/logs/news-timing-*.jsonl`에 누적 기록한다. 실행 중인 부모 PowerShell이 이전 스크립트여도 자식은 heartbeat 경로로 일자별 로그를 만든다. 부모를 새로 시작하면 세션별 파일 경로가 상태 JSON과 일반 로그에 남는다. `scripts/report_historical_news_timing.py <로그경로>`로 호스트·역할별 중앙값/p95와 단계별 분포를 집계한다. 본 수집의 원문 기사 URL당 접속+읽기 제한은 2초이며 네이버 검색 요청의 기존 20초 제한은 유지한다. 원문 URL이 있으면 그것만 조회하고 원문 URL이 없을 때만 보관 URL을 조회한다. 원문 실패 후 보관 URL은 즉시 조회하지 않고 원장과 `archive_deferred` 로그에 남겨 별도 선택적 보완 대상으로 둔다. 관련 단위/모니터 회귀 47건 통과. 새 자식의 2초 적용 전 초기 표본 검색 130건 중 fetch 중앙값 125ms/p95 172ms, 원문 219건 중앙값 156ms/p95 1,250ms였고 2초 초과는 0건이다. 이 짧은 표본으로 장기 평균이나 전체 수집 속도 개선은 확정하지 않는다.

2026-09-27 공통 DB `news.external_claim` 로컬 이관: PC 과거뉴스 worker의 `/api/v1/news/historical-jobs/claim`이 `asyncio.to_thread(PostgresQueryStore.claim_external_historical_news_job)`를 호출한다. PostgreSQL의 `FOR UPDATE OF j SKIP LOCKED` 선택·`RUNNING` 갱신을 기존 한 connection context에 유지하고 공통 관측만 추가했다. 기존 backend wait sampler와 느린 claim 경고는 유지하며 경고에 같은 `db_call_id`를 기록한다. 빈 결과·성공·UPDATE 실패의 SQL/commit/rollback 단위검사와 PC 과거뉴스 경로 회귀 43건이 통과했다. 사용자 제공 컨테이너 출력에서 전용 PostgreSQL의 병렬 작업 소유권·기사 reader·계측 통합검사 1건이 통과했다(1.005초). 테스트 tearDown은 임시 job 2건과 article revision 2건을 삭제하고 잔여 0건을 확인한다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 공통 DB `news.job_retry` 로컬 이관: `CentralNewsJobRunner.run_once`의 예외 경로가 `asyncio.to_thread(PostgresQueryStore.retry_news_job)`를 호출한다. `attempts` 조회→기존 `PENDING/FAILED` 판정→행 갱신은 한 native connection context에 두고 공통 관측만 적용했다. 로컬 공통 access·뉴스 worker 회귀 37건 통과. 사용자 제공 컨테이너 출력에서 전용 PostgreSQL attempts=2/3 경계·최종 행·계측 통합검사 1건이 통과했다(1.598초). 테스트 tearDown은 임시 job 2건 삭제 후 잔여 0건을 확인한다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 PC 과거뉴스 원문 수집 지연: 00:39–04:39 KST에는 작업 129건·검색 6,616페이지, 04:39–08:39에는 202건·8,054페이지를 완료했다. 08:45–08:48의 `035420` 작업은 검색 종료 후 `www.breaknews.com` 원문 요청 1건만 실행하면서 104→95건을 대기시켰고 약 15초마다 1건씩 진행했다. 해당 작업의 고정된 90페이지에서 이 호스트 원문은 117건이며 읽기 전용 확인 당시 34건이 timeout `fetch_error`, 83건이 미조회였다. 원문을 생략하지 않고 8초 이상 멈춘 호스트에 한해 동시 요청을 최대 2건으로 늘리는 로컬 수정을 적용했다. 기존 빠른 호스트의 1건 직렬화, 결과 전수 회수, 검색 순서의 회귀 6건이 통과했다. 실행 중인 Python 자식 작업은 종전 코드이며 다음 작업 자식부터 새 파일을 읽는다. 운영 속도·차단 응답의 전후 비교는 아직 없다.

2026-09-27 원문 접속 불가 호스트 처리: 최근 4시간 PC 시도 원장에서 `www.breaknews.com` 414/414건이 timeout이었고, 사용자 브라우저에서도 해당 사이트가 열리지 않았다. 같은 호스트 원문 3건이 연속 timeout이면 그 작업의 나머지 기사 중 네이버 보관 URL이 없는 기사만 실제 HTTP 요청 없이 `fetch_error`와 `publisher_original_skipped` 사유로 기록하는 로컬 수정을 적용했다. 보관 URL이 있는 기사는 기존 원문·보관 조회를 계속한다. 다른 호스트의 요청과 3건 미만 오류, 비-timeout 오류는 종전 동작이다. 관련 원문 수집 회귀 30건 통과. 현재 실행 중인 자식 작업에는 적용되지 않으며 다음 자식 작업의 건너뛴 수·완료시간은 아직 실측하지 않았다.

2026-09-27 공통 DB `news.job_claim` 로컬 이관: `CentralNewsJobRunner.run_once`가 `asyncio.to_thread(PostgresQueryStore.claim_news_jobs)`로 호출한다. 만료 `RUNNING` 복구 UPDATE, `FOR UPDATE SKIP LOCKED` 선택, 선택 행의 `RUNNING` 전환을 기존 한 connection context·한 COMMIT 안에 두고 공통 계측만 적용했다. 선택 행이 0건이어도 복구 UPDATE가 실행될 수 있어 공통 `rows_attempted`는 미상으로 기록하고, 기존 성공 지표의 행수는 선택 행수로 유지한다. 기존 지표와 신규 call ID를 연결했다. 공통 계층·뉴스 worker 로컬 회귀 35건 통과. 전용 PostgreSQL 만료 복구·병렬 중복 claim 방지·reader/COMMIT/계측 상관 검사 ZIP `kiwoom-db-access-news-claim-pilot-20260927.zip`(1,271,494바이트, SHA-256 `889DB1635BB9CA90C29C2493F810BA97B5E34550959B08E7C0A851F9D8E61A87`)의 무결성·테스트 import를 확인하고 NAS 호스트 `/tmp`로 전송해 원격 해시가 일치했다. 사용자 제공 컨테이너 출력에서 전용 PostgreSQL의 stale lease 복구·병렬 중복 claim 방지·metric correlation 통합검사 1건이 통과했다(0.278초). 테스트 tearDown이 임시 job 행 3건을 삭제하고 잔여 0건을 확인한다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 공통 DB document 일곱 번째 좁은 kind 로컬 이관: `ka10001` 응답의 `stock_fundamentals` 최신 문서 UPSERT만 `document:stock_fundamentals`로 계측 등록했다. 날짜별 `stock_fundamentals` dataset snapshot은 별도 transaction으로 저장되며 수정하지 않았다. `/api/v1/kiwoom/query`의 당일 freshness 검사, NAS TOP20·PC content reader와 기존 payload를 보존했다. DB·ingest·API 로컬 회귀 88건 통과. 전용 PostgreSQL 검사 ZIP `kiwoom-db-access-stock-fundamentals-pilot-20260927.zip`(1,270,792바이트, SHA-256 `25E4C2DEBF81F0460AC7F36772B005596FF1FF91B2C6A0F6776BA015DD3F8558`)은 무결성·테스트 import 확인 후 NAS 호스트 `/tmp`에 전송했고 원격 SHA-256이 일치한다. 사용자 제공 컨테이너 출력에서 전용 DB replay·당일 stored response reader·COMMIT/계측 call ID 상관 통합검사 1건이 통과했다(3.150초). 테스트 tearDown은 해당 문서를 삭제한 뒤 잔여 행 0건을 확인한다. 운영 오버헤드와 운영 적용은 미검증이다.

2026-09-27 공통 DB document 여섯 번째 좁은 kind 로컬 이관: `ka10100` 응답은 `MarketDataIngestor._ingest_nxt_eligibility`에서 최신 `stock_nxt_eligibility` 문서를 저장한 뒤 날짜별 `nxt_eligibility` dataset snapshot을 별도 transaction으로 저장한다. 이번에는 첫 문서 UPSERT만 `document:stock_nxt_eligibility`로 관측 등록했다. `/api/v1/kiwoom/query`의 당일 문서 재사용과 TOP20의 NXT 자격 reader, PC content reader 및 날짜별 snapshot 저장 경계는 변경하지 않았다. 공통 관측·ingest·API·TOP20·URL 로컬 회귀 140건 통과. 전용 PostgreSQL 검사 ZIP `kiwoom-db-access-nxt-eligibility-pilot-20260927.zip`(1,270,674바이트, SHA-256 `2D16C3788E644A87AC2E28D37DA707768408B614C6A297D27B47BA118DE0D5E7`)은 무결성·테스트 import 확인 후 NAS 호스트 `/tmp`에 전송했고 원격 해시가 일치한다. 사용자 제공 컨테이너 출력에서 전용 DB replay·당일 stored response reader·COMMIT/계측 call ID 상관 통합검사 1건이 통과했다(0.384초). 테스트 tearDown은 해당 문서를 삭제한 뒤 잔여 행 0건을 검사한다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 과거뉴스 archive 봉인 준비도 감사: `scripts/audit_prepared_historical_archive_readiness.py`를 읽기 전용으로 추가해 입력 진행·RULE 출처 연결·사건/검색 projection 수량·시황/coverage manifest·출처 실패 검토를 봉인 전 차단 조건으로 보고한다. 실제 PC `news-archive-20260927.building.sqlite3`에 전체 SQLite 무결성 검사 `ok`를 확인했지만 327,815건 pending, 2,446건 assessment_done, 37건 source_failed이며 RULE 검증 원장·사건·검색/시황 projection·dataset ID가 없어 `ready_for_seal_review=false`다. 도구 회귀 2건 통과; 이 감사는 봉인 구현이나 게시 승인이 아니며 원본 PC DB와 NAS에는 쓰지 않았다.

2026-09-27 PostgreSQL pilot URL 실행기: `scripts/run_postgres_access_integration.py`가 전용 `KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL`을 단독으로 받거나, 없을 때 NAS `KIWOOM_SERVER_DATABASE_URL`에서 진단 DB URL을 파생한다. 명시 URL 단독 경로는 전용 DB명만 허용하고 해당 DB에 read-only preflight한 뒤 기존 스키마를 요구한다. URL guard 단위검사와 공통 DB 관측 단위검사 28건이 통과했다. 현재 Codex 프로세스·사용자 환경에는 URL 변수가 없어 실제 통합검사 실행은 미확인이다.

2026-09-27 과거뉴스 archive B/C 로컬 진행: PC RULE 검증 원장은 고정 검색 준비본과 미완성 archive의 기사·BODY·출처·대상·입력 hash를 대조한 뒤 기존 RULE 계산을 오프라인 재실행한다. 원본에 쓰지 않은 복사본에서 2,446건 모두 일치했고, 현재 출처 fingerprint 추가 후 원본 미완성 archive의 BODY 출처 2,446건도 읽기 전용으로 일치함을 확인했다. 사건 최종화 코드는 부모 revision 우선 작업순서, 기존 seed 정확 재사용, 사건/membership/진행 원자 transaction, identity와 event key가 서로 다른 그룹을 가리킬 때 충돌 후보 ID 기록, 단일 실행 lease와 재시작을 구현했다. 검색기사 목록 projection 빌더는 사건·RULE 검증 완료를 선행시키고 seed `historical` 소유 기사만 목록에 포함한다. 기사/BODY/assessment/사건 ID를 PC에서 고정하며 단일 builder lease, 묶음 transaction 및 재시작을 지원한다. 별도 읽기 전용 reader는 `sealed`와 dataset ID, 완료된 검색 projection을 요구하고 종목별 keyset 페이지·서명 cursor·정확한 기사/BODY/사건 ID 상세만 조회한다. 새 인증 API의 미설정·미봉인 차단, 기존 뉴스 capability 보존, 진단 master 하위 pause/TTL 복구를 로컬 검사했다. PC `CentralNewsClient`에는 별도 cursor 목록·정확한 dataset/기사/BODY ID 상세 메서드를 추가했고 서버 상세도 dataset 불일치를 409로 거부한다. 기존 UTC 발행시각 해석, seed 소유권, 실패 rollback 및 reader의 미봉인 차단·페이지/변조 cursor·기존 support ID 상세 회귀도 통과했다. 나머지 준비기사 327,815건이 아직 ARTICLE/BODY/assessment 단계에 남아 실자료 사건·projection 실행은 의도적으로 차단된다. 시황 projection·전체 봉인·실자료 운영 검증은 미완료다. PC 종목 뉴스 화면에 별도 읽기 전용 `과거 수집 자료` 창을 로컬 연결했으나 봉인된 실제 archive가 없어 실자료 화면 검증은 남아 있다. 운영 NAS와 원본 미완성 archive는 변경하지 않았다.

2026-09-27 공통 DB document 단계의 첫 좁은 이관: `PostgresQueryStore.upsert_documents` 전체를 일괄 전환하지 않고 `news_sync` collection만 기존 connection context/SQL/COMMIT 수를 유지한 채 공통 계측에 연결했다. 뉴스 기사는 `news_article`에서 먼저 별도 transaction으로 저장되고 `news_sync`는 이후 별도 marker transaction으로 기록되며, 다음 조회의 5분 캐시 판단이 이 문서를 읽는다. 기존 성공 지표와 공통 call ID를 연결하고 registry에 이관된 kind만 표시했다. pilot·뉴스·DB·진단 회귀 99건 통과(PC 전용 DB URL 부재로 통합 클래스 1건 skip). 사용자 제공 NAS 컨테이너 출력에서 전용 PostgreSQL의 동일 문서 replay·최종 행값·COMMIT/SQL 횟수·기존 지표와 call ID 연결·측정 행 정리를 확인하는 통합검사 1건이 통과했다(0.595초). 검증 ZIP `kiwoom-db-news-sync-document-pilot-20260927.zip`(1,259,541바이트, SHA-256 `FC97E9EC554753585FE84993E5EE6040A1746A2388BCA7349EA47F9334C17684`)의 NAS 호스트 사본 해시도 일치했다. 다른 document collection과 운영 NAS 소스·이미지·DB는 변경하지 않았다.

2026-09-27 공통 DB document 다음 kind 로컬 구현: `document:krx_trading_day_observations` 한 종류를 기존 `upsert_documents` transaction과 `asyncio.to_thread` 호출 경계를 유지하며 공통 계측에 연결했다. 0s KRX market-operation event가 `latest` 문서를 쓰고 같은 서비스가 `load_documents`로 거래일 증거를 읽어 장후 보완을 결정한다. 기존 SQL 및 collection reader는 변경하지 않았다. DB/TOP20/URL 단위 회귀 68건 통과. 전용 PostgreSQL 통합검사 1건을 NAS 호스트 `/tmp` 전송 뒤 컨테이너 `/tmp`로 복사해 실행했고, 문서 replay·서비스 reader·공통/기존 metric 상관 및 cleanup 검사가 통과했다(0.308초). 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 공통 DB document 세 번째 좁은 kind 검증 완료: `YahooDelayedMarketCollector`가 `asyncio.to_thread`에서 `external_market_roll_state` 문서를 저장하고 다음 수집의 `_load_roll_state`가 읽는 경로에만 기존 `upsert_documents` transaction의 공통 계측을 적용했다. 월물 선택 계산, SQL, COMMIT 횟수, reader는 변경하지 않았다. 관련 DB·collector 단위검사 86건과 runtime discovery 10건, 전용 PostgreSQL replay·collector reader·공통/기존 call ID correlation·정리 통합검사 1건이 통과했다(0.469초). ZIP(`kiwoom-db-access-external-roll-pilot-20260927.zip`, 1,266,515바이트, SHA-256 `6FC1606C5CF4AC87E09133BC3572896883A28291709FCAAA4302D64CC02F0656`)의 NAS 호스트 해시가 일치했다. 운영 NAS 소스·이미지는 변경하지 않았다.

2026-09-27 공통 DB document 네 번째 좁은 kind 검증 완료: 뉴스 source collector의 KRX stock catalog fallback UPSERT를 `document:stock_catalog`로 계측한다. 두 뉴스 collector의 `_catalog` reader가 같은 collection API를 사용하며, TOP20 `replace_documents`와 별도 저장 transaction은 이관하지 않았다. 기존 문서 payload·병합·COMMIT 경계는 변경하지 않았다. 관련 DB·뉴스 카탈로그 회귀 51건, 명시 URL 실행기 단위회귀를 포함한 공통 관측 회귀 28건, 전용 PostgreSQL replay·`MarketFeedNewsCollector._catalog` reader·call ID correlation·cleanup 검사 1건이 통과했다(1.251초). ZIP `kiwoom-db-access-stock-catalog-pilot-20260927.zip`(1,271,802바이트, SHA-256 `6E45EC203BD40A9AC2332315F308147137C813B425BD990A97EE9AFD4C3D06C3`)의 NAS 호스트 해시가 일치했다. 운영 NAS 코드·이미지는 변경하지 않았다.

2026-09-27 공통 DB document 다섯 번째 좁은 kind 검증 완료: `MarketDataIngestor._record_sor_trade_value_comparisons`가 KRX/NXT 조회 분봉과 이미 저장된 SOR 분봉을 비교해 `minute_trade_value_comparisons`를 별도 transaction으로 UPSERT한다. 그 결과를 API `trade_value_comparisons`가 읽어 complete/partial 합계를 만든다. 분봉 본 transaction 뒤의 best-effort 경계와 실패 격리, 계산 규칙은 변경하지 않았고 `document:minute_trade_value_comparisons` 계측만 추가했다. DB·ingest·API·URL 회귀 95건 통과. 전용 PostgreSQL 검사 ZIP `kiwoom-db-access-trade-value-comparison-pilot-20260927.zip`(1,272,266바이트, SHA-256 `202250885819D326455BBE9A30DAD25D0A9B64CBA55D6B1ED0FA31B19FBC2BD6`)은 로컬 무결성과 대상 테스트 import를 확인하고 NAS 호스트로 전송했으며 원격 해시가 일치한다. 사용자 제공 컨테이너 출력에서 전용 DB 문서 replay·요약 reader·call ID correlation 검사가 1건 통과했다(2.343초). 테스트 tearDown도 해당 문서 키를 삭제한 뒤 잔여 행 0건을 확인한다.

2026-09-27 공통 DB native context 추가 계약: 설치된 Psycopg의 실제 `Connection.__exit__`를 사용한 단위검사에서 본문 예외 뒤 ROLLBACK 자체가 실패해도 원래 예외를 보존하고 연결 close·공통 관측 `unknown` 결과를 남김을 확인했다. pilot·뉴스·진단 회귀 34건 통과(PC 전용 DB URL 부재로 통합 클래스 1건 skip). `news.job_finish` 전용 DB 통합검사 통과 사실은 아래 두 번째 pilot 기록과 같다. 다음의 `document.collection` 메서드는 여러 collection에서 사용하며 `theme_metadata`·`news_article`이면 추가 이력 helper도 같은 transaction에서 실행한다. 이 범위의 호출자·reader·revision 계약을 좁히기 전에는 메서드 전체를 일괄 이관하지 않는다.

2026-09-27 공통 DB 두 번째 pilot (로컬): 전용 PostgreSQL에서 native context 계약 2건이 통과한 뒤 `news.job_finish`의 PostgreSQL UPDATE 한 건을 기존 connection context·transaction 그대로 `open_observed_connection`으로 감쌌다. 기존 성공 지표와 새 call ID를 연결하고 registry에 이관 여부를 표시했다. 단위검사 16건, 뉴스·DB·진단 인접 회귀 71건 통과(PC 통합 클래스는 URL 부재로 1건 skip). 검증 ZIP `kiwoom-db-news-finish-pilot-20260927.zip`(1,259,057바이트, SHA-256 `1B31B028C5133EC224C4B6F558B339A7CC7DAC9386664C37291BA8135767A541`)은 NAS 호스트 `/tmp` 전송·해시 대조를 마쳤다. 사용자 제공 컨테이너 실행 출력에서 전용 DB 사전검사와 `finish_news_job` 통합검사 1건이 통과했다(1.144초). 검사는 완료 상태·output_ref·error, 공통 call의 UPDATE 1회/COMMIT 1회, 기존 지표와 call ID 연결 및 측정 행 삭제를 확인한다. 운영 NAS 소스·이미지·DB는 변경하지 않았다. 두 writer pilot의 전용 DB 계약 검증은 완료됐으며 나머지 직접 PostgreSQL 경로는 미이관이다.

2026-09-27 과거뉴스 archive 사건 순서 설계 결정: [B 단계 사건·규칙 출처 계약](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md#2026-09-27-b-단계-사건-순서규칙-출처-설계-결정)을 추가했다. 9/25 고정 검색 준비본 사건 후보 37,816건 중 정확한 seed 사건 재사용은 2,448건이고 새 후보 35,368건은 seed identity/event_key 그룹과 겹치지 않았다. 기존 ID/payload/sequence는 보존하고 새 결과는 실제 PC 계산시각에 append한다. 임시 SQLite에서 늦게 받은 과거 MOU가 최근 저장 판정이 되는 기존 동작과 seed/as-of 불변을 재현해, 저장 순서를 사건 발생 순서로 표시하지 않는 계약을 명시했다. prepared의 null RULE 및 assessment 처리 버전 근거가 없어 현재 2,446건 `assessment_done`은 staging으로만 인정하며 PC 오프라인 검증 원장이 필요하다. 다음은 Sol High의 검증 원장·결정적 사건 처리·재시작/rollback 구현이다. 이번 단계는 설계와 읽기 전용 감사이며 archive 실자료·운영 NAS는 변경하지 않았다.

2026-09-27 native context 전용 DB 검사: `kiwoom-db-native-context-20260927-v3.zip`(1,261,238바이트, SHA-256 `B8D605F328FC613A95F4D16095D7920FA01A91EFC554A2DBE608BDEB7C2E55C2`)을 NAS 호스트 `/tmp`로 전송했고 호스트 복사본 해시가 일치했다. 사용자 제공 컨테이너 실행 출력에서 전용 DB 사전검사와 성공 COMMIT·본문 예외 ROLLBACK 및 지연 제약 COMMIT 실패의 native context 계약 검사 2건이 모두 통과했다(0.221초). 서버 이미지·운영 DB는 변경하지 않았다.

2026-09-27 공통 DB native context 계측 로컬 보완: `ObservedDBConnection`이 Psycopg의 Python `__exit__`를 proxy에서 실행해 기존 성공 COMMIT·본문 예외 ROLLBACK·close 순서를 각 공통 계측 메서드로 통과시킨다. COMMIT 실패 때 driver가 close를 건너뛰는 의미를 유지하면서 outcome `unknown`을 게시한다. 설치된 Psycopg의 실제 `__exit__`를 가짜 연결에 적용한 단위검사 등 pilot 14건 통과. PC에는 전용 DB URL이 없으나 NAS 전용 PostgreSQL native context 통합검사 2건은 통과했다. 이 검사 시점에는 해당 경계를 사용하는 운영 writer가 없었고 `save_query` 명시 COMMIT 경로·transaction·배포 상태는 그대로였다. 검사는 성능/WAL 재측정을 포함하지 않았고 사전검사는 읽기 전용, schema migration은 생략했다.

2026-09-27 공통 DB pilot COMMIT wait 상관 표본: NAS 컨테이너의 전용 `kiwoom_monitor_diagnostic_test`에서 cache SQL 등가 경로 1회+11회, 총 12개 별도 transaction을 capture ON으로 실행했다. backend PID·call ID·COMMIT 시간창을 묶어 별도 연결에서 시작 100ms 후 25ms 간격으로 `pg_stat_activity`를 읽었다. 12회 중 6회에서 wait 표본 98개가 잡혔고 `IO:WalSync` 85개, `LWLock:WALWrite` 13개였다. COMMIT 1,049.522ms와 1,183.287ms 두 호출에서는 각각 `WalSync`만 36개(시작 후 118~1,034ms), 41개(133~1,182ms)가 잡혔다. 460.666ms 호출은 `WALWrite` 11개(161~427ms) 뒤 `WalSync` 1개(453ms)였다. 표본의 blocking PID는 모두 비었고 probe 오류·미완료·표본 한도 도달은 없었다. 각 실행의 측정 키 1행을 삭제·잔여 0으로 검증했다. 운영 DB 분봉 저장의 동일 backend별 WAL wait는 [기존 진단](NAS_RUNTIME_DIAGNOSTICS.md)에 이미 있었다. 이번에 추가 확인한 범위는 **작은 cache transaction도 같은 WAL 대기를 겪는다**는 점이다. 실제 장치 지연·다른 writer와의 경합·WAL 발생량의 원인은 확정하지 않는다. 100ms 미만 호출 6회는 의도적으로 probe를 시작하지 않았다. 운영 DB 쓰기나 서버 이미지 변경은 없다.

2026-09-27 공통 DB 관측 pilot cache SQL 등가 비교: NAS 전용 `kiwoom_monitor_diagnostic_test`에서 `save_query`와 같은 UPSERT→만료 DELETE→명시 COMMIT→close를 호출별 새 연결로 실행하고 raw/OFF/ON을 교차했다. 짧은 사전검사 각 1회와 전체 6회×3구간(모드당 18회)이 성공했다. 전체 표본의 commit p50/p95는 raw 103.057/1,239.752ms, OFF 32.194/1,850.664ms, ON 84.577/1,418.553ms였고 전체 호출 p50/p95는 122.239/1,270.108ms, 52.741/1,865.315ms, 103.033/1,456.337ms였다. 세 모드 모두 긴 COMMIT이 있어 모드별 지연 차이를 래퍼 영향으로 귀속할 수 없다. ON close p50은 0.671ms(raw 0.104ms, OFF 0.101ms)로 capture 기록이 포함된 close 경계의 비용 후보를 보여준다. ON의 세 블록 모두 6개 call·각 2 SQL·COMMIT 1회 capture 검사가 통과했고, 9개 블록 모두 측정 키 1행을 삭제·잔여 0으로 검증했다. 모드당 18회라 nearest-rank p95/p99는 모두 해당 모드의 최댓값이고 NAS 동시 부하·CPU는 통제하지 못했다. 도메인 `save_query` 함수 자체는 호출하지 않은 진단 SQL 등가 비교이며 운영 writer 오버헤드/5% 처리량 가드와 느린 COMMIT 원인은 미판정이다. 운영 DB·이미지는 변경하지 않았다.

2026-09-27 공통 DB 관측 pilot 읽기 전용 오버헤드 표본: NAS 컨테이너의 전용 `kiwoom_monitor_diagnostic_test`에서 `scripts/benchmark_postgres_access_pilot.py`를 임시 ZIP으로 실행했다. 재사용한 read-only connection의 `SELECT 1` 실행·조회 1,000회씩 3구간을 raw / observed capture OFF / observed capture ON 순서를 교차해 측정했다(각 모드 3,000회). 합산 p50/p95는 raw 0.094/0.120ms, OFF 0.107/0.138ms, ON 0.106/0.127ms다. 블록 전체 시간의 중앙값은 raw 108.748ms, OFF 118.879ms, ON 115.037ms이며 ON의 중앙값은 raw보다 5.78% 길었다. ON 세 구간 모두 capture의 SQL 1,000회·commit 1회·drop 0 검사를 통과했다. 블록별 시간 범위와 p95 변동이 크고 CPU·연결 수·동시 부하를 측정하지 않았으며 실제 `save_query` 저장·COMMIT 작업이 아니므로 5% 처리량 가드 통과나 운영 writer 비용을 판정하지 않는다. 운영 소스·이미지·DB 행은 변경하지 않았다.

2026-09-27 과거뉴스 archive 입력·기존 ID 보존 진행: 운영 PostgreSQL을 읽기 전용 일관된 snapshot으로 추출한 seed 338,470행/785,879,040바이트를 PC로 가져와 SHA-256과 무결성을 확인했고, PC의 미완성 archive DB에 기존 ID·table별 `accepted_sequence` 그대로 물질화했다. 9월 25일 검색 준비본 330,261 ready/37 failed에 연결된 원본 요청키 76,007개와 페이지 버전 82,163개를 PC에서 파싱해 기사 관측 813,616건, 페이지 오류 0건, 준비본 기사별 원본 연결 누락 0건을 확인했다. 페이지 응답 hash 변화 4,295개와 기사 항목 변화 후보 108개를 구분했다. 준비본 전체를 입력 원장화하고 앞의 2,446 기사/BODY·평가 문서/RULE 입력만 미완성 archive에 저장했다. 이 중 사건 후보 265건은 아직 사건으로 생성하지 않았다. 원본 BODY snapshot과 본문 hash·획득시각이 모두 일치하는 133,357건을 전수 확인했으며, 앞의 2,446건 중 잘못 출처 확인으로 표시한 행은 없었다. 이 산출물은 중간 범위의 `building`/`parsed_unverified` 입력이며 NAS에 게시하지 않았다. 사용자가 정한 기본은 **수집 종료 후 전체 최종본**, 필요하면 그전에 별도 임시 세대를 게시하는 것이다. 현재 진행 중인 준비본은 검색 556,695 ready/71 failed, 시황 424,890 ready/2 failed로 9월 25일 고정본보다 커졌으므로 입력 시점을 다시 고정해야 한다. PC 검색·시황 준비 DB를 각각 일관된 새 파일로 고정하는 도구는 구현·소형 검증했으나 현재 실자료 스냅샷은 아직 실행하지 않았다. 나머지 기사/BODY·사건 최종화·읽기 전용 NAS reader·소비자/삭제 검증은 미완료다. 상세 근거는 [archive 구현 계약](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md)을 따른다.

2026-09-27 공통 DB 관측 pilot의 동일 호출 짝 검증: 전용 PostgreSQL에서 보완한 통합검사 3건이 다시 통과했다(12.297초). `save_query` 두 호출의 기존/공통 `call_id`가 연결됐고 commit 시간은 기존 2,121ms / 신규 2,121.301ms, 기존 2,012ms / 신규 2,012.055ms였다. connect·execute·commit·total의 내부/외부 계측 순서도 ms 반올림 허용 범위에서 확인했다. 두 COMMIT이 각각 약 2초였다는 사실만 확인됐고 wait event·WAL·lock 원인은 계측하지 않았다. 동일 조건 OFF/ON 성능 오버헤드 검증과 NAS 운영 배포는 여전히 남았다.

2026-09-27 공통 PostgreSQL 관측 pilot 로컬 구현: `central_server/postgres_access.py`가 명시적으로 이관한 `PostgresQueryStore.save_query`의 연결, 두 SQL, COMMIT, close, 실패 outcome을 기록한다. 기존 SQL/transaction/재시도/연결 소유권은 유지한다. 기존 진단 capture가 ON일 때만 새 호출 표본을 저장하며 인증된 `/api/v1/diagnostics/db-calls`에서 집계·제한 raw를 제공한다. 첫 대상은 REST query cache 한 종류다. 무등록은 이 공통 경계를 통과한 호출에서만 보이고 raw 우회는 보이지 않는다. fake connection/중앙 DB/진단/서버 회귀 134건 통과. 로컬 `.venv`에 `psycopg[binary]` 3.3.6 설치 후 import·`pip check` 및 pilot 단위검사 9건 통과. 현재 로컬 `src`와 integration test만 담은 SHA-256 확인 ZIP을 NAS `/tmp`에 전달해 서버 컨테이너의 전용 `kiwoom_monitor_diagnostic_test`에서 통합검사 3건이 통과했다(8.125초, 종료 상태 OK). cache replay·실패 connection의 암묵 폐기·서로 독립적인 두 connection을 확인했고 테스트 key 잔여 행은 0건이다. 한 호출에서 `commit_ms=1521`이 나왔지만 단일 표본으로 원인을 귀속하지 않는다. 기존/new 같은 호출의 단계 시간 비교, 실제 PostgreSQL OFF/ON 오버헤드와 NAS 운영 배포는 아직 미검증이다. 로컬 build marker `2026.09.27-db-access-pilot-v1`.

2026-09-27 NAS DB 공통 경계 설계 검토 당시: [공통 접근·관측 계층 검토](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md)에 연결 소유권·명시/암묵 종료·직접 접속 도구·기존 진단 범위를 정리했다. 대부분의 서버 경로는 호출별 새 연결을 만드는 `PostgresQueryStore._connect`를 사용하며 pool은 없다. 당시 AST 조사에서 store 메서드 102개와 직접 psycopg 접속 지점 20개를 확인했으며 후속 pilot 소스에서 재생성한 원장은 102개·23개다. 추가 3곳은 별도 과거뉴스 seed 도구·검사다. 이 숫자는 운영 writer/commit 수가 아니다. 공통 관측 경계와 기존 transaction/concurrency 보존을 채택하고 첫 이관은 `save_query` 하나로 제한했다. ranking/TOP20의 기존 durability 설정, 읽기 중 쓰는 통계 cache, importer savepoint, 무등록과 우회 탐지, 완료 후 복원할 수 없는 wait event를 별도 계약으로 명시했다. 기존 진단·저장 경계 테스트 21건 통과. 이 문단의 구현 전 상태는 위의 후속 pilot 기록을 따른다.

2026-09-27 PC 완성 과거뉴스 archive 설계 결정: [구현 계약과 검증 순서](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md)를 확정했다. 기존 NAS 뉴스 ID/이력은 PC seed에 그대로 보존하고, 고정한 원본·준비 입력으로 PC에서 사건/평가/조회 DB를 완성한다. 원응답 hash 변화와 기사 내용 변화를 구별하고 확보 실패는 이유가 있는 상태로 보존한다. NAS는 봉인된 SQLite를 읽기만 하며 첫 구현은 기존 현재 뉴스 목록과 과거 수집 자료 목록을 구분한다. 기존 ID 상세·일지 참조는 유지하고 연구 소비자 호환성이 확인되지 않은 행은 삭제하지 않는다. 수집기 전체 종료나 운영 전체 DB dump는 전제하지 않는다. 이번 단계는 문서 설계만 완료했으며 archive 코드·실자료 검증·NAS 게시·기존 자료 삭제는 수행하지 않았다. 다음은 Sol High에서 입력/ID export와 PC 최종화 경계 구현이다.

2026-09-27 모델 라우팅: 사용자가 제공한 양방향 강제 handoff 정책을 [모델 단계 전환 정책](MODEL_HANDOFF_POLICY.md)에 저장하고 개발 불변 규칙·문서 목차에서 참조시켰다. 새 독립 단계는 Luna부터 재평가하고 상향·하향 모두 실제 전환 전 handoff에서 멈춘다. 첨부의 Light/Extra High는 런타임 표기 Low/XHigh로 대응했다.

2026-09-27 과거뉴스 이관 재검토: 사용자 목표는 PC에서 최종 DB를 완성하고 NAS가 대량 재처리 없이 조회하는 것이다. [PC 완성 뉴스 archive 검토](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md)에 읽기 전용 SQLite archive를 권고안으로 정리했다. 현재 `prepared_news`는 중간 BODY/RULE JSON이므로 사건/이력/조회 인덱스와 기존 NAS ID 대응을 PC에서 추가 완성해야 한다. 기존 NAS job 완료·PG 적재가 필수라는 설명은 현재 importer에 한정되며, 완성 archive를 직접 조회하면 그 경로를 사용하지 않을 수 있다. NAS 이관 원장에는 검색 뉴스 23,546건·시황 뉴스 103건이 기록돼 있다. 330,261건은 PC 준비 스냅샷 규모이며 NAS 적재 건수가 아니다. 사용자는 기존 NAS 이관분도 PC에서 재구성한 뒤 정리하는 방향을 원한다. 운영 DB 실제 행·종속 참조를 확인하고 archive 게시·검증을 마친 뒤 이관 전용 행만 별도 정리한다. 검토 문서와 원장 읽기만 수행했고 런타임 코드·NAS 변경·전량 적재 재개는 하지 않았다. 이 설계는 아직 구현·실자료 검증 전이다.

2026-09-27 단발성 PC 준비 과거뉴스 대량 수입 변경: 검색기사의 `upsert_documents("news_article")`를 1건씩 호출하던 NAS 적재기를 최대 25건씩 기존 정상 writer에 넘기도록 했다. 기본 25건에서 기사 단계 write transaction/commit은 최대 25회에서 1회로 줄고, BODY/RULE 완료는 기존 25건 단위 transaction·기사별 savepoint·작업 소유권 검사를 유지한다. 시황 source run/observation은 기존 건별 경로다. 잘못된 준비 문서로 묶음이 실패하면 DB transaction rollback 뒤 기사별로 재시도하며 DB 접속·시간 초과는 대량 건별 재시도로 확대하지 않는다. 기사 단계와 완료 단계의 누적 소요시간을 로그에 기록하고 runner 상태에는 진행 중 건수를 주기적으로 반영한다. PC 스냅샷은 이미 BODY/RULE을 계산했지만 NAS가 live revision/job/event 정합성을 기록하는 작업은 남는다. 로컬 회귀 6건 및 NAS 전용 `kiwoom_monitor_diagnostic_test` PostgreSQL 통합 3건(기사 replay·revision chain, 묶음 rollback, 준비된 BODY/RULE 완료 및 재실행 중복 방지)이 통과했다. 통합검사는 운영 서버의 `/tmp` 복사본만 사용했다. NAS 실행 프로세스가 없는 상태에서 기존 소스·실행본을 `X:\kiwoom-monitor-backups\20260927-prepared-news-oneoff`에 백업하고, 두 스크립트를 NAS 소스 및 `/app/data/maintenance` bind mount 실행 경로에 SHA-256 검증 후 반영했다. 서버 이미지는 재빌드하지 않았고 전체 스냅샷 적재도 재개하지 않았다. 운영 25건 소량 표본은 실패·보류 0건, 기사 단계 누적 4,055ms, BODY/RULE 완료 단계 누적 32,235ms였다. 25건 표본만으로 전체 처리량 개선을 확정할 수 없고, NAS 완료 단계가 여전히 지연된다. BODY/RULE 계산은 PC에서 끝났지만 기존 job 완료 경로의 검증·revision/문서/사건 기록은 NAS가 수행한다. 따라서 이 묶음 변경만으로 사용자가 요청한 경량 대량 적재 목표를 달성했다고 보지 않는다.

2026-09-26 O12 `ka10080` canonical 분봉·metadata batch UPSERT 로컬 구현 `2026.09.26-bar-upsert-wait-correlation-v5`: `SQLiteQueryStore`와 `PostgresQueryStore`의 `replace_minute_bars`에서 고유 키 900행 `executemany`를 bounded multi-row `INSERT ... VALUES (...), (...) ON CONFLICT`로 교체했다. PostgreSQL은 최대 1,000행, SQLite는 최대 80행씩 canonical과 metadata를 배치한다. 독립 검토에서 같은 키가 바뀌었다가 원래 값으로 돌아오는 응답은 dedupe만 하면 기존 `updated_at` 의미가 달라짐을 확인했다. 중복 키의 앞선 관측만 기존 순서대로 적용하고 마지막 관측을 batch에 넣어 고유 키의 묶음 처리와 기존 최종값을 함께 유지한다. metadata는 마지막 입력, revision은 전체 입력 순서와 chain을 유지한다. 기존 transaction/rollback 경계와 동일 OHLCV UPDATE 생략을 유지했다. SQLite 실제 SQL의 1/80/81/900/1001행 경계, PostgreSQL 전용 `kiwoom_monitor_diagnostic_test` 통합 14건, 로컬 관련 회귀 86건이 통과했다. PostgreSQL 1002개 관측·1001개 고유 봉에서 batch 2개와 중복 키 선행 SQL 1개, metadata batch 2개, revision 2 SQL/1002행을 확인했다. NAS 운영 소스/이미지는 v5로 복사·재빌드하지 않았고 동일 운영 조건 성능 계측은 남아 있다.

2026-09-26 O12 진단 계측 로컬 v5: 진단 `measure` 시작/종료 시 PostgreSQL `pg_stat_io`의 backend/object/context별 relation I/O와 `pg_stat_checkpointer`의 checkpoint 횟수·쓰기/sync 시간·buffer 수를 차분해 기존 WAL·동일 writer wait 표본과 함께 반환한다. 누적 통계이며 개별 SQL I/O 귀속값이 아니다. 활성 backend 통계의 공유 반영 지연과 `pg_stat_io` 시간 필드의 `track_io_timing` 한계를 결과에 기록한다. checkpointer 단계 시간은 이 설정과 독립이다. 불가능한 I/O cell의 NULL과 missing/reset counter를 0으로 오인하지 않게 수정했다. 선택적 통계 조회 실패는 해당 항목만 unavailable 처리한다. 전용 PostgreSQL DB에서 두 PG17 view의 실제 SELECT와 조회 실패 후 연결 재사용을 확인했다. NAS 운영 재빌드 및 실측은 아직 남았다.

2026-09-26 O12 독립 검토 후 로컬 수정 `2026.09.26-bar-upsert-wait-correlation-v2`: 분봉 `executemany`는 행별 SQL 시간이 아니라 전체 호출 구간 하나로, 일봉 multi-row UPSERT는 batch 실행마다 별도 구간으로 표시한다. probe 정리 대기가 `bars_ms`에 들어가지 않도록 하고, probe 시작 실패가 저장을 중단하지 않도록 했다. 대기 표본은 해당 SQL/COMMIT 실행 시각 안으로 제한하며, 미완료 probe 상태를 드러낸다. NAS 장치 표본은 실제 읽은 시각·간격으로 연결한다. v1 소스는 NAS 공유에 복사됐지만 v2는 로컬 전용이며 NAS 재빌드/배포 전이다.

2026-09-26 O12 분봉 UPSERT 대기 계측 v1: 진단 capture 중 PostgreSQL 분봉 `executemany` 전체 호출과 일봉 multi-row UPSERT batch 호출에 backend PID wait event·blocking PID 표본을 연결했다. 같은 호출 구간에 host device counter를 맞춰 보고하며, 장치 값은 다른 프로세스의 겹친 I/O를 포함한다. capture OFF에서는 추가 probe를 만들지 않는다. transaction-local `track_wal_io_timing`은 UPSERT 전에 적용한다. v1 관련 unittest 57건·Python 구문검사·`git diff --check` 통과, NAS 누적 소스 동기화와 SHA-256 확인 완료. v1 NAS 재빌드/health marker와 운영 측정은 수행하지 않았다. 이후 로컬 v2 검토 수정은 위 항목에 기록했다.

2026-09-26 PC 직접 연결 SQLite 분봉 후속 `2026.09.26-daily-minute-batch-storage-v1`: `replace_minute_bars`의 `ka10080` revision 최신값 조회를 최대 80개 키씩 묶고 변경 revision INSERT를 최대 40행씩 multi-row 처리한다(24개 필드와 SQLite 999 bind 한도 고려). SQLite transaction 내에서 accepted_sequence를 입력순으로 지정하고 같은 키의 `revision_of` chain 및 payload hash replay dedup을 유지한다. canonical minute 동일 OHLCV UPDATE도 생략하고 metadata/revision은 계속 처리한다. 실제 SQLite trace에서 100행 입력의 revision 조회 2문장·INSERT 3문장, 같은 키 수정 2건 chain, 동일 payload 재전송 dedup, revision 실패 시 canonical·metadata rollback을 확인했다. 저장 관련 회귀 63건 통과. NAS 쪽 PostgreSQL 분봉 batch는 이전 변경이며, 이번 SQLite 변경은 NAS 미배포다.

2026-09-26 O12 일봉 batch UPSERT 로컬 변경 `2026.09.26-daily-bar-batch-upsert-v1`은 다음 누적 marker `2026.09.26-daily-minute-batch-storage-v1`에 포함됐다. `ka10081`의 canonical daily bar와 observation metadata를 행별 UPSERT하지 않고, 같은 키의 마지막 입력을 선택한 뒤 PostgreSQL 최대 1,000행·SQLite 최대 80행의 multi-row UPSERT로 저장한다. 기존 한 transaction, 동일 OHLCV canonical UPDATE 생략, metadata 최신 관측 의미와 rollback 경계는 유지한다. 900행 SQL 경계에서 PostgreSQL canonical/metadata 각 1문장, SQLite 각 12문장을 확인했고 SQLite 실제 DB 중복키·metadata·rollback을 확인했다. 전용 PostgreSQL DB 통합검사 실행과 운영 성능 측정은 아직 남아 있다.

2026-09-26 O12 COMMIT 상관 계측 로컬 구현 `2026.09.26-commit-wait-correlation-v1`: 진단 capture 중에만 같은 분봉/일봉 저장 연결의 backend PID wait event·blocking PID를 25ms 표본으로 저장 호출에 연결하고, 그 transaction에서만 `track_wal_io_timing`을 임시 적용한다. 권한 오류는 savepoint rollback으로 격리해 저장을 유지하며 전역 DB 설정은 바꾸지 않는다. 진단 CLI는 250ms host device 카운터를 COMMIT 경계와 맞춘 delta로 첨부한다(다른 프로세스 I/O와 구간 가장자리 padding 포함). 9개 관련 회귀 통과, NAS 소스 SHA-256 동기화 완료. 사용자의 누적 재빌드와 `/health.server_build` 확인, 운영 재측정은 남아 있다.

2026-09-26 O12 NAS paired 측정 `20260926T072545Z-588bd4f0`: 사용자가 `2026.09.26-minute-revision-call-samples-v1` 빌드에서 60초 측정 후 진단 master를 OFF로 복구했다. `ka10080` 저장 16회·14,122행에서 revision lookup은 16회, 실제 revision INSERT는 584행이었다. 900행 한 호출에서 revision INSERT 584건의 execute 시간이 4,660ms, revision 전체 4,987ms, commit 32ms였다. 별도 900행 호출은 INSERT 0건인데 commit 14,677ms였고, 또 다른 900행 호출은 canonical bar 저장 4,793ms였다. 따라서 revision INSERT 비용은 별도 최적화 후보지만 COMMIT·canonical 저장 지연은 독립적으로 남는다. 60초 동안 WAL 약 30.0MB, `dm-4` busy 80.34%·write await 990.95ms·queue 351.92가 함께 있었고 news claim 113회 및 외부시장 6,539행 수집도 동시 실행됐다. 이 host-wide 수치는 분봉 호출 단독 원인이 아니다. 이 계측은 호출 내 phase를 직접 연결했으나 16건의 작은 표본이다.

O12 후속 로컬 변경 `2026.09.26-minute-revision-batch-insert-v1`: revision payload/UUID/`revision_of` 체인을 입력 순서대로 구성한 뒤 최대 1,000행씩 multi-row INSERT한다. 검토에서 원래 초안의 VALUES 행 순서만으로 `accepted_sequence` 순서를 보장할 수 없는 점을 발견해, 연결된 PostgreSQL sequence 값들을 미리 발급·정렬한 후 각 행에 입력 순서대로 명시한다(행당 24개, batch 최대 24,000 bind parameters). canonical 봉·metadata·revision은 기존 단일 transaction과 advisory lock 안에서 함께 commit/rollback된다. 계측의 `revision_insert_statements`는 batch INSERT SQL 수, 신규 `revision_insert_rows`는 삽입 revision 행 수를 뜻한다. 관련 unit 47건과 NAS 전용 `kiwoom_monitor_diagnostic_test` PostgreSQL 통합검사 7건이 통과했다. 동일 키 revision chain, 재전송 중복 제거, 1,000행 batch 경계, rollback, 동시 writer를 확인했고 NAS `/health`도 해당 build와 `status=ok`를 반환했다. 최초 실 DB 검사에서 실패한 2건은 테스트 subject 조건이 잘못된 문제였으며 `code:market`으로 고쳐 재실행했다. 성능 개선 여부도 같은 운영 조건 재측정 전에는 미확정이다.

2026-09-26 O12 batch 변경 후 NAS 측정 `20260926T082101Z-70672d4f` (60초, diagnostic master 종료 후 OFF): `ka10080` 완료 53회, 저장 25회·22,500행, revision lookup 25회·22,500키, revision INSERT 0건이었다. 무변경 재조회 표본에서 revision 전체 p50/p95/max 217/569/1,179ms, 실제 INSERT execute 0ms; 전체 저장 834/2,534/2,684ms, COMMIT 378/2,144/2,294ms였다. 따라서 batch INSERT 전후 성능은 비교할 수 없고, 변경 데이터 없는 경로에서 INSERT가 발생하지 않는 점만 확인됐다. 동시 구간 뉴스 claim 115회·active query 63표본, 외부시장 수집 5,068행, WAL 19.57MB, dm-4 busy 65.32%·write await 197.17ms·queue 79.18, wait 표본 WalSync 15·WALWrite 11·DataFileRead 22였다. `track_wal_io_timing`이 꺼져 있어 WAL wait 시간은 알 수 없고 host-wide 지표는 분봉 단독 부하가 아니다. master OFF·capture OFF·pause 없음으로 복구를 확인했다.

2026-09-26 O12 격리 PostgreSQL batch 비교 `benchmark_minute_revision_batch_postgres.py`: 운영 DB가 아닌 `kiwoom_monitor_diagnostic_test`에서 합성 900행 중 584행이 바뀌는 동일 작업을 각 경로 3회 실행했다. 이전 행별 경로는 revision lookup 900회·INSERT 584회, 새 경로는 lookup 1회·INSERT SQL 1회였다(새 경로는 별도로 chunk별 sequence allocation SELECT를 한다). revision 처리 구간 중앙값은 399.048ms에서 191.358ms(-52%), 포함 COMMIT 전체 중앙값은 2,042.357ms에서 812.893ms(-60%)였다. COMMIT 표본은 행별 427.939~2,419.807ms, batch 160.848~1,219.288ms로 변동이 커 전체 차이를 batch 효과로 귀속할 수 없다. 각 3회뿐인 NAS 저장장치 공유 테스트 DB 합성 결과이므로 실제 장중 성능 개선 확정은 아니며, benchmark 스크립트는 finally에서 해당 합성 revision 행을 제거했다.

2026-09-26 O12 저장 호출별 revision/COMMIT 결합 계측 (로컬 구현): 이전 60초 표본에서 분리 percentile만으로 같은 저장 호출의 revision과 COMMIT tail을 짝지을 수 없어 `market_bar_saves.kinds.*.call_samples`를 추가한다. timestamp·API ID·행/lookup/insert 수와 저장 호출별 connect/bar/metadata/revision 하위단계/COMMIT/total ms를 함께 반환하며 원시 봉 payload는 넣지 않는다. 서버 저장 동작은 변경하지 않는다. build marker `2026.09.26-minute-revision-call-samples-v1`; 단위검사와 NAS 재빌드·측정은 아직 남아 있다.

2026-09-26 O12 분봉 원천 경계: 닫힌 분의 최종 OHLCV와 거래대금은 `ka10080` 응답 및 앱의 OHLCV 계산값을 채택한다. 0B 누적 체결 차분은 구독 시작 지연·순위 변경·수신 공백 때문에 해당 분의 최종 거래대금을 빠짐없이 복원한다고 보장할 수 없어 진행 중 분봉의 임시값으로 취급한다. 닫힌 조회봉 저장 후 늦은 0B flush/마감은 canonical·metadata·revision을 덮어쓰지 않고 operation ID만 기록한다. PostgreSQL에서는 같은 종목·시장·날짜의 조회봉 교체와 0B 저장을 transaction advisory lock으로 직렬화하고, revision 최신값은 봉당 SELECT 대신 배치 조회한다. SQLite 단위검사는 통과했다. NAS `/health`는 `server_build=2026.09.26-minute-source-authority-v1`, `status=ok`를 반환했다. 뒤이은 60초 단일 측정은 저장 지연이 계속됨을 확인했으나 비교 기준이 없어 개선 효과는 미확정이다. 독립 PostgreSQL 테스트 DB 동시성·rollback 통합검사도 아직 실행되지 않았다. 이는 분봉의 최종 원천 우선순위 결정이며 WAL COMMIT 대기 원인 전체가 제거됐다는 뜻이 아니다.

2026-09-26 O12 revision 단계 분리 계측 (로컬 구현): NAS 측정 `20260926T064325Z-6bf6dd08`에서 60초 동안 `ka10080` 7회·6,300행, revision lookup 7회·6,300키, revision INSERT 1,090회였다. 저장 전체 p50/max는 3,423/9,799ms, revision 전체 단계 p50/max 289/7,783ms, 실제 COMMIT p50/max 245/8,555ms다. 로그의 `revisions_ms=34,895` 사례는 측정 시작(06:43:27.554 UTC)보다 먼저 끝나 해당 집계에는 포함되지 않았다. 같은 사례의 단계별 세부 시간·INSERT 수는 로그에 없어 원인을 더 분해하지 못했다. 표본 중 뉴스 claim 117회, 외부시장 수집 6,539행 등 동시 부하가 있었고 dm-4 busy 91.55%, 평균 queue 662.96으로 관측돼 WAL/장치 수치는 분봉 단독 귀속이 아니다. 로그의 TOP20 10.295초는 snapshot 실행 10.273초·COMMIT 1ms로 별도 기록됐으며 60초 표본 이전 사건이다. 새 계측은 revision source 생성, advisory lock, batch SELECT, 행별 비교 루프, INSERT execute 시간을 나눠 기록한다. 로컬 marker는 `2026.09.26-minute-revision-phase-metrics-v1`; NAS 실행 build는 아직 `2026.09.26-minute-source-authority-v1`이므로 새 계측을 포함하지 않는다.

2026-09-26 O12 두 번째 NAS 측정 `20260926T065335Z-cf197ea9`: 60초 동안 분봉 저장 23회·20,422행, revision lookup 23회·20,422키, revision INSERT 12회였다. revision 시간 p50/max 218/381ms, 전체 저장 p50/p95/max 662/2,093/2,414ms, COMMIT p50/p95/max 166/1,715/2,030ms였다. 이전 34.9초 지연은 이 구간에서 재현되지 않았지만, 짧은 표본만으로 일시적 outlier인지 조건 의존인지 확정할 수 없다. `0B` 저장 표본은 0회였고, news claim 117회·뉴스 active query 60표본, 외부시장 collector 3회 완료가 함께 관측됐다. dm-4 busy 48.53%, average queue 48.06으로 첫 표본보다 낮았으나 작업량·장치상태를 통제한 비교는 아니다. 이 결과에 세부 revision phase 필드가 없으므로 이전 NAS build에서 수행한 표본이다.

2026-09-26 O12 새 revision 단계 계측 NAS 측정 `20260926T070655Z-ea8d03ea` (label `minute-revision-phase-metrics`, 60초): `/health`에서 새 build marker `2026.09.26-minute-revision-phase-metrics-v1`와 `status=ok`를 확인한 뒤 실행했다. `ka10080` 완료 44회, 분봉 저장 22회·19,800행, revision lookup 22회·19,800키, revision INSERT 실행 305회였다. revision source 구성 p50/p95/max 189.5/220/231ms, advisory lock 1/1/10ms, batch lookup 35/42/46ms, 행별 revision 처리 1/1/2,120ms, 그 안의 실제 INSERT execute 0/0/2,099ms였다. revision 전체 p50/p95/p99/max는 233/268/2,358/2,358ms, 전체 저장은 645/2,891/4,366/4,366ms, COMMIT은 213.5/2,393/3,738/3,738ms였다. 행별 처리와 INSERT execute의 tail 값이 근접해 INSERT 실행이 느린 revision outlier의 주요 구간일 가능성이 높지만, 메트릭이 호출별로 결합된 자료가 아니어서 동일 호출의 시간 기여로 확정하지 않는다. 이전 34.9초 outlier는 재현되지 않았다. 같은 60초 동안 WAL 5.23MB·write/sync 71/70, wait 표본 DataFileRead 25·DataFileWrite 12·WALWrite 8·WalSync 9·CPU 38, dm-4 busy 64.01%·평균 queue 190.29·write await 575.9ms, 뉴스 active query 64회·news claim 111회, 외부시장 수집 6,539행이 관측됐다. 따라서 전체 저장장치·WAL 지연을 분봉만의 효과로 귀속하지 않는다. `0B` 저장은 0회다. 진단 master는 실행 뒤 OFF 결과를 반환했다. 이 표본은 INSERT 경로를 다음 조사 후보로 좁혔지만, 원인 확정이나 성능 개선 완료를 뜻하지 않는다.

2026-09-26 분봉 revision 계수 NAS 측정: `news_jobs` 60초 ON/OFF/ON 진단 결과에 revision 조회·삽입 수가 나타났다. 3구간에서 각각 18,022/18,633/16,562개 lookup에 INSERT 0회였다. 분봉 저장 transaction·commit은 호출당 1회였다. revision 단계 p95는 524/382/592ms, commit p95는 798/776/1,342ms, 저장 전체 p95는 1,489/1,069/1,870ms였다. 뉴스 OFF는 런타임 상태와 활성 뉴스 SQL 0건으로 확인됐고, 분봉 호출 수는 56/54/60으로 각 구간에 있었다. dm-4 await는 116/106/140ms, queue 25/23/35로 전체 지연과 함께 변했으나 부하 차이가 통제되지 않아 뉴스 또는 저장장치 단독 원인으로 확정하지 않는다. 결과는 test `20260926T053843Z-62810d24`; build marker는 결과에서 별도 확인하지 않았다. 동시성 검증은 전용 PostgreSQL 테스트 DB 미설정으로 남아 있다.

2026-09-26 O12 진단 계측 보완 로컬 구현: external-market 상태 API가 운영 설정·task 실행·poll 간격·실제 수집 시도/완료·누적 성공 행 수와 최근 결과를 반환한다. A/B/A는 비활성 수집기 또는 poll 주기 이하 구간을 거부하고 각 phase 활동·행 수 차이를 기록한다. 서버 상태 API·collector·진단 CLI 관련 회귀 23건과 Python 구문 검사를 통과했다. 로컬 build marker는 `2026.09.26-external-market-diagnostics-v1`; NAS 반영과 `/health.server_build` 검증은 미완료다.

2026-09-26 O12 진단 master 후속 로컬 구현: 제어 파일의 부모·자식을 단일 세대로 읽고 session ID/revision/monotonic TTL을 추가했다. 반복 ON은 TTL·자식을 초기화하지 않고, 이전 세션의 종료 작업은 새 세션을 수정하지 않는다. A/B/A는 필요한 전체 TTL을 먼저 확인하고, master OFF·만료·교체 시 다음 표본 전에 중단해 부분 보고서를 남긴다. DB 진단 조회 실패도 `aborted`로 보존한다. 진단 상태 API의 경로 변수 미정의 오류도 수정했다. 진단 단위 20건과 서버 API 56건 통과. 로컬 build marker `2026.09.26-diagnostic-session-control-v1`; NAS에서 확인된 운영 build는 아직 `2026.09.26-writer-diagnostics-v4`다. 실제 작업 중지 ACK는 미구현이다.

2026-09-26 O12 보호 경로 진단 설계 검토: [설계 결정과 구현 순서](DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md)에 master→자식 계층, admission/수신/저장 분리, 실제 중지 ACK, 세대·TTL·중단 복구, 원장 없는 sink의 durable 인계, 주문/체결 atomic 그룹과 필수 검증을 정리했다. 설계 검토 중 실제 CLI 변경 함수가 master 필드를 삭제하는 결함을 임시 파일에서 재현해 수정했다. 진단 13건+서버 API 55건 통과. 로컬 식별자 `2026.09.26-diagnostic-master-switch-v2`; 이 단계의 NAS 재빌드·운영 검증은 하지 않았다. 보호 경로 스위치와 인계 저장소는 아직 구현되지 않았다.

2026-09-26 O12 진단 master 계층 (로컬 구현): `tool on/off/status`를 최상단 master로 두고 capture·작업 일시정지·measure·A/B/A 하위 제어는 master ON에서만 작동하도록 연결했다. master OFF/TTL 만료/재시작은 하위 제어를 비활성화하며 새 ON은 이전 하위 override를 초기화한다. build marker는 `2026.09.26-diagnostic-master-switch-v1`로 올렸다. 현재 자식 pause 지점은 선택 작업 8개뿐이고 PostgreSQL writer 계측은 12개이며 실시간·계좌·주문·체결의 개별 switch는 아직 연결되지 않았다. 보호 경로 제어와 drain/재개·gap 검증은 후속 구현 및 NAS 재빌드 뒤 확인해야 한다.

2026-09-26 O12 후속: `2026.09.26-writer-diagnostics-v3`는 NAS `/health.server_build`에서 실행을 확인한 **부분 배포**다. 이후 로컬 v4에 읽기 전용 소스 원장과 `kiwoom_storage_source_inventory.json`, 격리 SQLite/실제 collector의 데이터 보존 검사 7건, 0B flush별 성공 writer COMMIT 집계, NAS 저장장치 연결 그래프의 진단 결과 첨부, WAL FPI·buffer full 집계를 추가했다. 소스에서 키움 API ID 문자열 32종, 중앙 broker 선언 20종, 실시간 등록 type 9종, PostgreSQL 직접 SQL 쓰기 메서드 31개가 확인된다. 이는 실제 활동 writer/commit 수가 아니다. 로컬 관련 회귀 78건이 통과했다. NAS `/health.server_build=2026.09.26-writer-diagnostics-v4`와 인증 진단 API의 12개 writer 응답을 확인했다. 실제 A/B/A 부하·0B flush 표본은 아직 수집하지 않았으며, writer별 WAL 귀속과 전용 PostgreSQL DB 동시성 검증은 남아 있다. 범위와 한계는 [저장 경로 감사](KIWOOM_STORAGE_WRITE_AUDIT.md)를 따른다.

2026-09-26 NAS writer 진단 확장: 기존 기본 OFF·TTL capture에 계측 중인 PostgreSQL writer 12개 registry와 `/api/v1/diagnostics/writers`를 추가했다. 시장 봉·persistent cache·dataset snapshot은 측정된 COMMIT 시간을 기록하고, 모든 계측 표본의 호출·transaction·commit 수·시도 행수와 p50/p90/p95/p99를 구분한다. 이 목록은 전체 NAS 저장 writer 감사가 아니며 계좌·주문·Journal·PC 저장·모든 API/FID 경로의 최종 대조는 미완료다. 실제 affected rows, errors/retries, payload bytes, writer별 WAL attribution도 미계측이다. build marker `2026.09.26-writer-diagnostics-v3`는 NAS `/health`에서 확인했다. 상세 범위는 [writer 감사](KIWOOM_STORAGE_WRITE_AUDIT.md)와 [진단 가이드](NAS_RUNTIME_DIAGNOSTICS.md)를 본다.

2026-09-26 NAS 진단 계측 런타임 제어 (로컬 구현): 저장 단계·writer 표본 수집은 기본 OFF이며 `capture on --ttl 10m`, `capture status`, `capture off`로 앱 재빌드 없이 제어한다. 만료 시 수집을 멈추고 OFF 전환 때 메모리 표본을 비운다. `measure`·A/B/A `test`는 기존 OFF일 때만 자신이 소유한 임시 lease로 수집을 켰다가 복구한다. 서버 빌드 식별자는 `2026.09.26-runtime-diagnostic-v2`; NAS 누적 재빌드와 운영 ON/OFF·자동 만료 확인은 아직 하지 않았다. 이전 비교 첨부에서 `minute_backfill` OFF 구간은 분봉 저장 20건→0건, ON 복귀 구간은 다시 20건이었고, WAL 증가·디스크 지연도 함께 변했다. 이 결과는 해당 30초 표본의 상관관계이며 저장장치 지연 전체를 분봉 하나에 귀속시키지는 않는다.

2026-09-26 KRX 휴장 오조회 방지 로컬 구현: 중앙 WebSocket이 키움 `0s` 장운영(215)을 구독하고 원본 상태코드·시각을 NAS 문서 원장 `krx_trading_day_observations`에 날짜별 보존한다. NAS 장후 보완은 해당 날짜의 `0s` 거래일 증거가 있을 때만 종목별 TR을 시작하고, 새벽 전일 선택은 최근 14일 안의 관측 거래일을 찾는다. 증거가 없는 평일은 휴장으로 단정해 영구 완료하지 않고 대기하며 반복 TR을 보내지 않는다. 키움 공식 명세에서 `kt00002`는 휴장 달력이 아니라 일별 예탁자산 조회다. KRX Open API 서비스 목록에는 거래일 달력 API가 없고, 공식 웹 달력은 동적 화면이라 안정적인 자동 다운로드 계약을 확인하지 못했다. 따라서 이 변경은 공식 연간 달력 완성본이 아니라 실시간 거래일 증거 기반의 fail-closed gate다. 코드·집중 테스트는 로컬 확인했고 NAS 재빌드·장중 0s 실제 수신은 아직 검증하지 않았다.

2026-09-25 과거뉴스 NAS 적재기 후속 개선: NAS 진단에서 `COMMIT` WAL 대기가 보인 점에 대응해 로컬 적재 스크립트는 기사별 BODY/RULE 완료를 기본 25건 단위 트랜잭션으로 묶도록 수정했다. 기사별 savepoint가 있어 다른 실행기가 소유한 작업이나 단일 실패는 그 기사만 되돌리고, 성공한 기사 원장 기록은 PostgreSQL 묶음 commit 뒤에 쓴다. 기사 문서·revision 생성은 기존 canonical 저장 함수를 기사별로 사용해 revision 테이블 잠금 시간을 늘리지 않는다. 현재 실행 중인 NAS 프로세스에는 아직 적용되지 않았고, 코드 문법 검사만 완료했다. 운영 처리량 개선은 NAS에서 새 적재기를 재시작한 뒤 같은 조건의 DB wait·진행량으로 확인해야 한다.

2026-09-25 22:33 KST 과거뉴스 NAS 적재 진행: 사용자가 NAS SSH에서 적재기를 시작했다. 기존 검색 스냅샷 19,191건은 `skipped=19,191, failed=0`으로 기존 원장과 중복 없이 통과했고, 시황 103건은 `imported=103, failed=0, deferred=0`으로 완료됐다. 현재 330,261건 PC 검색 스냅샷 `search-current`를 처리 중이며 로그는 `imported=200, skipped=19,191, failed=0, deferred=0`까지 전진했다. 새 검색기사 100건 묶음 간격은 약 135초였고 같은 속도를 단순 적용하면 약 5일 추정이나 표본은 1개 구간뿐이다. 적재 중 읽기 전용 DB 진단은 `COMMIT`의 `IO:WalSync`/`LWLock:WALWrite` 대기, 차단 쿼리 없음이었다. 준비 스냅샷은 ready 330,261건·failed 37건, 2,064,654,336 B이며 NAS 복사본 SHA-256 `b01f8680fce196a856095ae573970eb51b3e1190f010e7d664c06b08b078e765`가 PC와 일치한다. 적재기는 due PENDING만 조건부 전환하고 같은 트랜잭션에서 완료하도록 고쳤고 다른 실행기 소유 작업은 deferred로 남긴다. 서버 재빌드는 필요 없다. 완료 뒤 본문·RULE 조회와 실패·보류 건을 검증한다.

2026-09-25 Google Drive 백업·복원 현재 상태: [백업 세대와 엄격 복원 경계](GOOGLE_DRIVE_RESTORE_DESIGN.md)의 v2 세대를 실계정에 게시하고 manifest·구성요소·v1 호환 파일의 크기와 SHA-256을 검증했다. 실제 Drive 백업을 임시 DB에 내려받아 설정·테마·뉴스 AI를 복원했고, 별도 시작 프로세스의 정상 완료·제어된 오류·강제 종료 뒤 복구를 확인했다. 관련 회귀 40건 통과. 활성 manifest가 참조하지 않는 과거 부분 업로드 파일 2개는 보존 중이다. 운영 사용자 데이터 폴더와 앱 GUI의 복원, 두 PC 동시 편집, 전원 중단 내구성은 미검증이다. 앱 빌드·NAS 반영은 하지 않았고 monitor/news 두 DB의 동일 촬영 시점도 보장하지 않는다.

2026-09-25 Google Drive 실계정 게시 완료: 설정(6,537 B), 테마(592,681 B), 뉴스 AI(11,230,132 B)의 v2 세대 `13c8af69f64549db8a8c59ea60fd61b7`를 게시했다. 각 원격 구성요소를 다시 내려받아 크기·SHA-256·JSON 형식을 확인했고, v1 호환 별칭 세 파일도 같은 바이트인지 대조했다. 기존 업로드 시 대용량 뉴스 AI 파일은 비재개 전송으로 시간 초과됐고, 재개 업로드로 구성요소와 v1 별칭은 완료됐지만 manifest 요청이 HTTP 500으로 실패했다. 검증 뒤 해당 세대의 manifest만 게시하고 Drive에서 다시 읽어 세대와 구성요소 참조를 확인했다. 업로드 코드에는 1 MiB 재개 청크(5 MiB 이상)와 원격 변경 요청 3회 재시도를 추가했고 관련 Drive·엄격 복원·설정·테마 회귀 40건이 통과했다. 이전 실패에서 만들어진 미참조 세대 파일은 보존되어 있으며 정리 정책은 미결정이다. 실제 v2 복원은 운영 설정을 바꿀 수 있어 이번에 실행하지 않았다. 앱 빌드·NAS 반영은 하지 않았다.

2026-09-25 Google Drive v2 격리 복원 검증: 활성 manifest가 가리키는 실제 설정·테마·뉴스 AI 파일을 내려받아 임시 `monitor.sqlite3`·`news.sqlite3`에 `download(target="both")`로 적용했다. 현재 사용되는 설정 키 87개, 테마 프로필 2개, 종목 뉴스 AI 8,694건, 공통 뉴스 AI 2,421건이 원격 백업과 일치하고 두 DB의 `PRAGMA integrity_check`가 `ok`였다. 백업에는 더 이상 사용하지 않는 옛 설정 키 7개가 남아 있으나 현재 `DEFAULT_SETTINGS`에 없어 복원 대상이 아니다. 운영 DB 복원은 하지 않았고 원격 파일도 변경하지 않았다.

2026-09-25 Google Drive 실제 세대의 시작 전 엄격 복원 격리 검증: 활성 v2 세대 `13c8af69f64549db8a8c59ea60fd61b7`를 별도 임시 데이터 폴더에 `stage_restore(target="both")`로 예약했다. 원장의 세 입력 SHA-256·크기가 원격 manifest와 같았고 예약 직후 활성 설정·뉴스 DB는 변하지 않았다. 새 Python 프로세스가 앱과 같은 `StrictRestoreCoordinator.enter_main()`을 호출해 `COMMITTED`를 기록했다. 복원 전 DB 사본에는 시험용 설정값이 남았고 복원 뒤 테마 프로필 2개·종목 뉴스 AI 8,694건·공통 AI 2,421건과 두 DB 무결성 `ok`를 확인했다. 실제 사용자 데이터 폴더에 복원을 예약하거나 앱 GUI를 시작한 검증은 아니다.

2026-09-25 Google Drive 실제 세대의 실패 복구 격리 검증: 같은 v2 백업을 새 임시 데이터 폴더에 예약하고 별도 시작 프로세스의 뉴스 AI 적용 단계에 예외를 주입했다. 복원 원장은 `ROLLED_BACK`으로 종료됐고 변경 전 설정값·테마 프로필 수·뉴스 AI 0건이 보존됐다. 두 DB 무결성 검사도 `ok`였으며 다음 새 프로세스 시작에서는 해당 복원을 다시 실행하지 않았다. 이는 제어된 예외 검증이며 강제 종료·전원 중단의 운영 내구성 증명은 아니다.

2026-09-25 Google Drive 실제 세대의 강제 종료 복구 격리 검증: 임시 데이터 폴더에 같은 v2 백업을 예약하고 설정·테마 적용 후 뉴스 AI 단계에서 자식 프로세스를 `os._exit(23)`으로 종료했다. 종료 뒤 원장은 복원 전 사본을 가진 `APPLYING`이었고 설정값은 실제로 부분 변경돼 있었다. 다음 새 프로세스의 `enter_main()`은 `ROLLED_BACK`을 기록하며 원래 설정·테마·뉴스 상태를 복원했다. 두 DB `PRAGMA integrity_check`는 `ok`였다. 이 검증은 시험 프로세스의 비정상 종료에 한정하며 OS/PC 전원 중단과 운영 사용자 데이터 폴더는 시험하지 않았다.

2026-09-25 Drive 세대 읽기 전용 점검: manifest는 1개이며 활성 세대 `13c8af69f64549db8a8c59ea60fd61b7`의 설정·테마·뉴스 AI 파일 3개(합계 11,829,350 B)를 모두 참조한다. 첫 업로드 실패로 남은 세대 `48e064cc19de4e078988c18326a76768`는 설정·테마 파일 2개(합계 599,218 B)만 있고 현재 manifest에서 참조하지 않는다. 이 파일은 복원 선택에서 무시되며 삭제하지 않았다. 자동 삭제·보존 기간은 미결정이다.

2026-09-25 Google Drive 복원 파일 사전검증: 모든 원격 백업의 다운로드에 이어 설정·테마·뉴스 AI 파일의 읽기·형식 검사를 끝낸 뒤 DB 적용을 시작한다. 뉴스 AI 파일 손상 시 설정·테마 import가 0회인 회귀와 현재 형식 테마 파일의 프로필 자료 누락 시 기존 테마 유지 검사를 추가했다. 관련 백업·메인창 검사 68건 통과. 파일 적용 중 저장소 간 원자성은 아직 제공하지 않는다.

2026-09-25 Google Drive 백업 수신 순서 보완: 설정·테마·뉴스 AI 파일을 모두 임시 폴더에 받은 뒤 로컬 복원을 시작한다. 두 번째 파일 다운로드 실패를 주입해 설정·AI 어느 것도 적용되지 않음을 확인했다. 뉴스 AI 백업의 UTF-8 오류는 읽기 오류로 처리하고 기존 결과를 유지한다. 관련 백업 검사 27건 통과. 파일별 적용 단계의 DB 오류까지 전체 복원을 원자화한 변경은 아니다.

2026-09-25 뉴스 AI·매매일지 백업 파일 보존: 뉴스 AI JSON은 기존의 압축 형식을 유지하며 임시 파일에 기록하고, 매매일지 SQLite는 별도 임시 DB로 백업한 뒤 대상 파일을 교체한다. 두 경로의 교체 실패 주입에서 이전 백업 보존과 임시 파일 정리를 확인했다. 뉴스·일지·Google Drive·설정·테마 인접 검사 32건 통과. 운영 파일이나 NAS에는 배포하지 않았다.

2026-09-25 설정·테마 백업 파일 보존: 기존 JSON 백업을 바로 덮어쓰지 않고 같은 폴더의 임시 파일에 기록·동기화한 뒤 교체한다. 교체 실패 주입에서 이전 백업 보존과 임시 파일 정리를 확인했고 설정·테마·Google Drive 백업 검사 17건 통과. 운영 파일이나 NAS에는 배포하지 않았다.

2026-09-25 백업 복원 파일 오류 처리 보완: 설정·테마 JSON이 유효한 UTF-8이 아니면 앱 예외 대신 백업 읽기 오류를 반환한다. 설정 백업의 뉴스 표시 열이 `null` 등 잘못된 자료형일 때는 기존 표시 열을 유지하고 복원을 마친다. 손상 입력·정상 복원·메인창 관련 회귀 51건 통과. PC 앱 재시작 전 실행 중인 인스턴스에는 반영되지 않는다.

2026-09-25 설정 백업 불러오기 오류 수정: 확인창이 정의되지 않은 `dialog` 변수를 참조하던 경로를 메인창 부모로 바로잡았다. 같은 경로의 취소·승인 회귀를 추가했다. PC 앱 재시작 전 실행 중인 인스턴스에는 반영되지 않는다.

2026-09-25 이미지 OCR 결과 검토 책임 정리: OCR이 끝난 뒤 행 수정·제외 테마 적용·종목명 확인·미리보기는 `theme_dialogs.py`에서 처리하고, 메인창에는 OCR 작업자 수명과 승인된 변경의 비동기 저장만 남겼다. Excel·OCR 가져오기는 같은 제외 테마 처리 함수를 사용한다. 관련 Qt·테마 회귀 51건 통과. NAS 배포는 하지 않았다.

2026-09-25 Excel 테마 가져오기 UI 책임 정리: 메인창의 Excel 파일 선택·검증·종목명 확인·미리보기·저장 흐름을 기존 `ThemeManagerDialog`로 옮겼다. 이미지 OCR과 Excel의 변경 종목명 확인은 같은 화면 함수를 사용하고, Excel 저장은 테마 관리창의 기존 비동기 `replace_many` 경로를 따른다. 시험용 Excel 승인/취소와 테마·메인·뉴스 회귀 71건 통과.

2026-09-25 테마 다건 저장 로컬 보완: 메인창의 Excel·이미지 테마 미리보기 후 `replace_many`는 기존 `SettingsRequestWorker`로 비동기 실행한다. 완료 팝업과 표 갱신은 저장 성공 뒤에만 표시하고, 실패는 기존 오류 경로로 보고한다. 뉴스창 분리 뒤 설정창·테마 관리창에 남아 있던 뉴스 경로 참조도 조정자 소유 경로로 연결했다. 관련 GUI·테마 검사 70건 통과. 현재 PC 앱 재시작 전 실행 중인 인스턴스에는 적용되지 않는다.

2026-09-25 뉴스창 수명 경계 로컬 정리: 메인창의 뉴스 자식 프로세스·JSON 명령·창 복원/고정 타이머 상태를 `NewsWindowCoordinator`로 이동했다. 종목/시장 뉴스 버튼, 매매일지 연결, 계좌 scope 검증 및 창 외곽 기준 위치 동기화 계약은 유지한다. 초기 뉴스 상세 검사 6건 실패는 시험용 기사에 화면의 `_display_rows` 행 연결을 설정하지 않은 테스트 준비 문제로 확인해 바로잡았다. 뉴스창·메인창·시장뉴스·프로세스·설정·일지 인접 회귀 117건 통과. 이 변경은 PC 화면 코드이며 NAS 배포 항목이 아니다.

2026-09-25 B01~B07 감사 후 로컬 수정: 테마 원격 적용은 조회 전 로컬 revision을 DB 교체 트랜잭션 안에서 재검증하고, 조회 중 편집 시 대기 표식을 유지해 다시 동기화한다. 분봉·일봉 아카이브 재사용은 종류·마감·세션 확정·대상일을 모두 확인한다. 수급은 원본 저장 성공과 유효 응답 확인 후에만 완료 처리하고, 시장지수는 대상일 분봉·일봉 및 연속조회 종료를 확인한다. NXT 여부는 관측일 당일만 재사용하며 비정상 응답은 미확정으로 둔다. NAS `realtime_gap`은 PC에서 현재 TOP20 구간을 부분 자료로 표시하고 누적 기준값을 리셋한다. B03·B08은 사용자 결정에 따라 제외한다. 예정 서버 빌드 `2026.09.25-audit-b01-b07-v1`; 운영 NAS/PC 반영과 장중 통합 검증은 아직 확인 전이다.

2026-09-25 A04~A07·A09 로컬 수정: 분 중간 시작·실시간 큐 유실은 TOP20 `partial`로 남기고 WebSocket에 누락 신호를 전달한다. NXT 일시 오류는 재조회 가능하며 장후 백필은 성공한 날짜만 완료로 표시하고 실패일은 backoff 재시도한다. REST 캐시 DB 작업은 공통 guard 밖으로 이동했고, 동시 동일 요청은 inflight 예약과 잠금 재진입 검증으로 병합한다. A08은 제외, A10은 사용자 결정에 따라 보류했고 A11·A12는 개발 단계로 별도 검증한다. 예정 NAS 빌드 `2026.09.25-top20-quality-lock-v1`; 운영 재빌드와 실시간 전후 측정은 아직 확인 전이다.

2026-09-25 A01~A03 로컬 수정: 0B 누적 거래대금 누락·복귀 시 추정분 중복을 조정한다. 누락이 분 경계를 넘으면 실제 체결별 금액·시각을 알 수 없어 분별 귀속은 추정이며, 이미 마감 저장된 TOP20 분 지수를 소급 재작성하지 않는다. REST 조회 성공과 응답 저장 성공을 내부적으로 구분하고 분봉 백필은 대상일 응답 봉이 실제 저장된 뒤에만 coverage를 완료로 기록한다. TOP20 분 마감값은 membership·편입 DB 쓰기 전에 outbox에 등록한다. 예정 NAS 빌드 `2026.09.25-top20-recording-v1`; 운영 컨테이너 반영·실시간 재현은 아직 확인 전이다.

2026-09-25 `ka10016` 미사용 목록 수집 제거: NAS 장중 5·20·250일 신고가 목록 자동 조회와 PC 직접 연결의 시작 후 목록 조회를 제거했다. 숨겨진 목록 새로고침 버튼·전용 worker·후속 단계도 제거하고 기본정보 다음에 NXT 확인을 시작한다. 화면 신고가 가격·거리·근접 강조는 기존 `ka10081` 일봉과 `0B` 당일 고가·현재가 경로를 유지한다. 기존 NAS `new_high` 스냅샷과 PC `new_high_snapshot` 표는 삭제하지 않으며 과거 자료 조회 호환성을 유지한다. 관련 회귀 208건 통과. NAS 프로젝트의 변경 파일 14개를 SHA-256 대조 후 복사하고 미사용 worker 3개를 제거했으며 기존 파일은 `X:\kiwoom-monitor-backups\20260925-no-ka10016-v1-124709`에 보존했다. 소스 빌드 식별자는 `2026.09.25-no-ka10016-v1`이다. 실행 중인 컨테이너에는 사용자 재빌드 전까지 반영되지 않는다.

2026-09-25 누적 NAS 빌드 소스 동기화: 로컬 소스·스크립트·문서·검사·빌드 설정 970개를 NAS 프로젝트와 비교해 206개 차이(신규 96, 기존 변경 110)를 복사했다. 기존 변경 110개는 `X:\kiwoom-monitor-backups\20260925-cumulative-followup-newhigh-cache-v1-114550`에 원본 SHA-256을 검증하며 백업했다. `.env`, `postgres-data`, `server-data`, `server-secrets`, NAS 전용 `release`와 백업 경로는 복사·삭제 대상에서 제외했다. NAS 소스 빌드 식별자는 `2026.09.25-followup-newhigh-cache-v1`이다. 컨테이너 이미지는 사용자가 재빌드해야 하며 운영 `/health.server_build`는 아직 확인 전이다.

2026-09-25 순위 후속·신고가·query cache 경량화 (로컬 검증): 앱의 세 후속 시작 신호를 현재 표에 적용한 순위 회차로 한 번만 수용한다. NAS KRX 신고가 목록은 평일 09:00~15:29 매분·16:00~19:59 실행 중 1회, 0.2~2초 query cache는 RAM-only로 전환했다. 원본 응답 저장과 30초 순위 수집은 유지한다. 예정 NAS 빌드는 `2026.09.25-followup-newhigh-cache-v1`이며 운영 NAS에는 아직 반영되지 않았다. 연결 풀·별도 TOP20 저장 트랜잭션은 이번 수정 대상이 아니다.

2026-09-25 TOP20 신규 0B 조기 구독 (로컬 검증): 완성된 `ka00198` 20행을 메모리 live projection에 게시한 직후 같은 메모리 종목 목록으로 KRX 0B 구독 요청을 갱신한다. `top20_membership`·당일 편입·TOP20 지수의 DB 저장은 그 뒤에 수행한다. 저장을 의도적으로 막은 회귀에서 `persistence_state=pending`인 동안에도 hub의 새 구독 대상 20종목이 확인됐다. TOP20·실시간 허브·수집기 관련 회귀 70건이 통과했다. 실제 WebSocket REG 승인과 운영 NAS 배포는 아직 검증하지 않았다. 예정 빌드 식별자는 `2026.09.25-top20-early-0b-v1`이다.

2026-09-25 뉴스 DB 시작 이관 경로 (로컬 검증): 실제 PC `news.sqlite3`에는 `news_schema_migrations` v1~v4가 있고 메인 `monitor.sqlite3`에는 옛 뉴스 표가 없다. 기존 시작 경로가 뉴스 스키마 실행기와 옛 뉴스 표 검사를 매번 호출했으나 실제 데이터 재복사는 없었다. 완료된 스키마는 읽기 전용 버전 확인으로 끝내고, 옛 표 이관은 메인 DB v8의 `legacy_news_transfer_migrations` v1에 완료를 기록해 한 번만 실행하도록 수정했다. 전용 DB 소실·옛 표 재출현은 오류로 감지한다. 관련 단위검사 17건 통과. 앱 재시작 전 현재 프로세스에는 반영되지 않았다.

2026-09-25 TOP20 통계 날짜별 저장 (로컬 검증): PC `top20_statistics_daily_cache`, NAS `central_dataset_snapshots(kind='top20_statistics_day')`에 완료일 집계를 저장하고 과거 원본 보완 시 날짜별 무효화한다. PC 일봉 차트도 같은 집계를 재사용한다. 당일은 매번 현재 저장된 분을 계산하며, 0원 TOP20 행은 표본에서 제외한다. 화면은 PC 자동 수집이 앱 실행 중에만 동작함을 안내하고 시간대 순위를 표시한다. PC 운영 DB 읽기 전용 확인에서 TOP20 분 행은 8,440개, 시장 지수 분 행은 15,448개다. 저장소 단위검사 50건 통과. NAS 운영 컨테이너 재빌드·`/health.server_build` 확인 및 실제 데이터의 조회시간 전후 측정은 아직 하지 않았다.

2026-09-25 TOP20 시장 구분 보완: `repair_top20_market_splits()`는 별도 프로세스가 아니며 앱 시작 때 메인 UI 스레드에서 직접 실행되고 이후 QThread에서도 실행되는 중복 경로였다. 시작 중 직접 호출을 제거하고 기존 `Top20MarketRepairWorker`의 저우선순위 QThread 및 완료 후 표시 갱신만 유지했다. 메인 창·분봉 저장소 관련 회귀 51건 통과. 기존 앱 프로세스에는 재시작 전 반영되지 않으며 실제 시작 지연 전후 시간은 미측정이다.

2026-09-25 네이버 검색 종목뉴스 수집기 ‘응답 없음’ 조사: 10:00~10:04 KST 원장·heartbeat·PID 대조에서 수집 PowerShell과 Python 자식은 살아 있었고 완료 작업은 100→104건으로 증가했다. 모니터의 ‘응답 없음’은 Windows 프로세스 상태가 아니라 3분 넘게 새 heartbeat/완료 상태가 없을 때의 자체 판정이며, 긴 기사 묶음·PC 준비 작업 중에도 표시될 수 있다. 모니터의 PC BODY/RULE 현황 집계는 실제 검색 DB 12.291초·시황 DB 4.328초로 합계 약 16.6초였는데 기존 주기는 10초여서 지속적인 CPU 부하가 생겼다. 모니터 집계 주기를 60초로 늘리고 살아 있는 PID의 오래된 응답은 ‘응답 지연 · 프로세스 실행 중’으로 구별했다. 모니터 재실행 전 기존 창에는 수정이 반영되지 않으며, 수정 후 실제 장시간 CPU/응답성은 미측정이다. 09:59 상태 게시 실패 로그 1건은 별도이며 원인은 해당 로그만으로 확정되지 않았다.

2026-09-25 휴장 중 모의 자동운용 오프라인 검증: 저장소 체크포인트를 쓰는 runner 테스트에서 1·2번 관측 처리 후 STOPPED 동안 3번 관측을 읽지 않았고, 새 runner가 커서 2를 복원해 명시 재개 뒤 3번만 한 번 처리했다. 같은 Decision을 두 번 전달한 실행 경계에서는 기존 승인 gate·intent·receipt를 반환하고 broker transport 호출이 1회로 유지됐다. 관련 모의 자동운용 회귀 29건이 통과했다. 이는 저장된 테스트 관측과 모의 transport의 검증이며 NAS 운영·장중 지연·실계좌 주문의 증거는 아니다.

2026-09-25 모의 자동운용 지연 계측(로컬 소스): NAS runner의 최근 poll·관측 DB 조회·관측당 최대 처리 시간과 분봉 `available_at`→처리 완료 경과 시간을 상태 응답에 추가하고 앱에 표시한다. 시각 누락·미래 분봉은 별도 계수하며 이 진단값은 주문 gate나 영속 checkpoint에 사용하지 않는다. 관련 단위 회귀를 통과했으나 NAS 운영 빌드 `2026.09.25-mock-runtime-latency-v1`의 배포 및 장중 실측은 아직 확인하지 않았다. 실전 자동주문 준비도도 이 계측만으로 입증되지 않는다.

2026-09-25 D03 월별 입력 재생성: 기존 불변 입력은 보존하고 종가 단일가 시각 계약으로 2024-09~2026-01의 17개 선택일을 새 `historical-strategy-be5976b6...` ID로 동결했다. OOS 1일은 `SEALED`이며 TRAIN 12일·VALIDATION 4일만 `historical-development-f65533cc...` 개발 입력으로 분리했다. 16개 개발일의 일반 분봉 revision 244,252개와 후보군 revision 16개는 유지되고 종가 체결 657개의 재생 구간만 가상 15:29~15:30 연속봉에서 15:20~15:30 종가 단일가로 정정됐다. 준비도는 TRAIN 488/488·VALIDATION 169/169 최소 연속쌍 `READY`다. 수정 입력의 첫 네 구조 실행은 모두 `ELIGIBLE`이지만 TRAIN 눌림 결과가 기존과 크게 달랐다. 동일 코드 비교에서 2025-04-02 장마감 미체결 주문이 같은 종목의 다음 봉을 기다리며 다음 달까지 예약 상태를 유지해 5~8월 주문 22건을 `pending_order_exists`로 거절한 실행기 오류를 확인했다. 주문을 다음 세션의 첫 관측에서 종목과 관계없이 검열하도록 고쳤다. 최종 구현 hash `cad98fc9bd41...`로 옛/새 TRAIN 눌림 입력을 모두 재실행한 결과 둘 다 `ELIGIBLE`·12활동일·67체결·50종료 거래·순손익 -1,976원이다. 수정 입력에는 후보·제출 주문 각 1건이 더 있으나 다음 세션에서 `session_boundary_before_fill`로 검열됐고, 양쪽 100개 FILL/RISK_FILL 사건 순서와 날짜별 종료 거래·손익이 정확히 같다. 근거는 `data/research/historical-development-results/clockfix-control-old-input-current-code/clockfix-control-comparison.json`에 결과·manifest SHA-256과 함께 보존했다. 같은 최종 구현으로 나머지 TRAIN 돌파·VALIDATION 두 Family까지 네 구조 실행을 완료했고 모두 `ELIGIBLE`이며 결과표는 `data/research/historical-development-results/monthly-2024-09-to-2025-12-clockfix-v2/SUMMARY-boundary-fix.md`다. 이 희소 구조 표본은 전략 수익성 근거가 아니다. 연구 원본·운영 DB와 OOS는 변경하지 않았다.

2026-09-24 주도후보 DART 원장: 코스피·코스닥 개별 주식 주도후보 2,637종목을 대상으로 CREON 수정계수 발생일·상장일·키움 일봉의 장기 거래량 0 전환/재개 단서·상폐 검토 마지막 거래일을 모아 4,587개 사건 단서를 저장했다. 기존 수정계수 DART 조회 1,993건을 원응답과 함께 재사용하고, 나머지는 새 조회로 종결했다(완료 2,531·공식 빈 응답 63). 짧은 거래정지를 놓치지 않도록 각 종목의 2019-09-24~2026-09-24 DART 거래소 유형 I 공시도 별도 조회해 2,620종목 완료·17종목 빈 응답, 공시 332,723건을 저장했다. 접수일과 사건 단서 날짜를 별도로 보존하며, 거래량 0이나 마지막 거래일을 정지·상폐 효력일로 확정하지 않는다. 로컬 DB `PRAGMA integrity_check=ok`, 참조 원응답 14,234개 존재, NAS run `20260924T114555Z-7b48edc19038`의 DB SHA-256 `7b48edc19038a0cc318d82d4ea76528a6903939a16ec22fe2fef44bd0270f4bd`를 확인했다. NAS staging→final의 첫 시도는 SMB `WinError 5`로 실패했지만 복사된 DB·manifest를 검증한 후 동일 staging 재시도에 성공했고, 기존 latest 백업과 새 latest 재조회까지 마쳤다. 최초 거부의 점유 주체는 미확정이다. 이 게시본에는 기존 시장 맥락 작업의 공급 불가 6건이 `coverage_complete=false`로 남아 있고, 거래정지·상폐 실제 효력일 분류는 별도 검증 대상이다.

2026-09-24 15:06 KST 원천 뉴스 요청 제한 점검: 현재 검색뉴스 수집 로그와 FLASH/WORLD 수집 로그에는 403·429 재시도 기록이 없고, 시황 원장에서 최근 30페이지는 약 4분 13초 동안 순차 저장되며 `invalid_count=0`이었다. 시황 원장의 유일한 오류 날짜는 2020-07-08 FLASH의 `page 83 repeats the previous page`이며 403·429가 아니다. 같은 시간대(05:00 UTC 이후) 검색기사 원문 시도 원장에는 `published_at_found/200` 4,893건, `blocked/200` 3,899건, `blocked/403` 9건, `blocked/429` 1건이 있었다. 200 응답 차단 표식은 모두 언론사 원문(`publisher_original`)에서 나왔고 여러 언론사에서 반복된다. 현재 코드는 HTML 어디에나 `captcha`, `robots.txt`, `비정상적인 접근` 문자열이 있으면 차단으로 분류하므로 200 응답이 실제 제한 페이지인지 정상 페이지의 표식 오탐인지는 본문 표본 없이는 확정할 수 없다. 해당 URL을 같은 작업에서 무한 재시도하지는 않으므로 목록 403·429 재시도 폭증이 현재 지연 원인이라는 증거는 없다. 원문 요청 시작 간격 0.2초·4개 작업자와 시황 목록 0.7초 설정은 실제 실행 명령으로 확인했다. 원문 요청별 소요 시간 계측은 아직 없어서 200 차단 페이지의 지연 기여율은 미확정이다.

2026-09-24 14:58 KST 뉴스 병렬 처리 병목 실측: PC 전처리 두 프로세스는 합계 약 4개 코어를 사용했고 12논리 코어 전체 CPU는 49~65%로 포화되지 않았다. PC 준비 원장은 검색 33,038건·시황 3,937건 `ready`로 증가했다. NAS 준비기사 적재는 검색 스냅샷의 신규 500건·기존 100건 건너뜀까지 전진했으나 느리며, 중간에 사용자가 동일 스냅샷 100건 명령을 중복 실행한 구간에서 작업 소유권 충돌 14건이 기록됐다. 이 실패는 현재 일회성 실행 종료 후 단독 재시도해야 하며 전체 적재 완료가 아니다. PostgreSQL 5초 관측에서 블록 읽기 394,697→장시간 조회 취소 후에도 389,192개로 높고, 차단 세션·데드락은 0이었다. 83.7분 실행한 `pc_market` BODY 작업 선점 SELECT(PID 10523)는 사용자 명령으로 해당 조회만 취소했고, 이후 최장 뉴스 작업 조회는 수초 이내였다. 후속 활동 조회에서 `central_documents`와 `central_news_jobs`의 autovacuum이 각각 297초·173초 실행 중이며 WAL 쓰기 및 데이터 파일 읽기 대기가 관측됐다. 이는 대량 과거뉴스 정리 직후 DB I/O와 WAL 경합이 적재와 겹친 상태를 입증하지만 각 작업의 전체 기여율과 VACUUM 종료 후 적재 속도는 미측정이다. VACUUM이나 운영 DB를 중단·변경하지 않고 완료 뒤 동일 지표로 다시 비교한다. 실시간 순위 `ka00198`의 기존 관측 p95는 약 50ms였으며 현재 뉴스 작업으로 순위 지연이 발생했다고 입증되지는 않았다.

2026-09-24 14:40 KST 키움 주도후보 수정 일봉 감시: 1,809/2,637종목 완료 지점에서 상태 JSON의 고정 `.tmp` 파일을 원본에 `os.replace`하는 동안 Windows `PermissionError [WinError 5]`가 나서 백필 프로세스가 종료됐다. 마지막 완료 종목 `199430`은 원장에 `missing_after=0`으로 남아 있다. 상태 임시 파일을 PID/UUID별로 만들고 Windows 일시 점유 오류 5/32에 제한 재시도하도록 수정했으며 같은 오류 재현 단위검사 포함 6건을 통과했다. 기존 상태 원장은 보존하고 `--as-of 2026-09-24 --no-pause-market-hours`로 재개해 14:40:16 기준 1,812종목 완료·실행 중, 신규 완료 종목의 누락 0건을 확인했다. `last_error`의 과거 연결 재설정 문구는 이전 중단 이력이며 이번 정지 원인은 상태 파일 교체 오류다.

2026-09-24 14:24 KST 과거뉴스 PC 선처리 진행: 사용자가 NAS 정리 명령의 `mode=done`을 확인했다. 기존 미완료 과거기사 394,698건 삭제, 미완료 작업 394,758건 취소, 참조 보호 5건이며 NAS 삭제 manifest와 PC 사본의 SHA-256이 일치한다. PC 로컬 전처리기는 삭제 manifest의 검색기사를 4개 작업자로 BODY 다음 RULE을 한 기사에서 연속 계산해 `prepared_news.sqlite3`에 저장 중이다. 네이버 검색 원천 수집기는 14:21 KST 재개했고 기사 원문 확인 뒤 저장된 본문 스냅샷을 재사용하며 기사별 준비 작업 2개를 병렬 실행한다. 시황 FLASH/WORLD 원천 수집기는 14:16 KST 재개했고 각 페이지 원자료 저장 직후 기사별 준비 작업 2개를 병렬 실행한다. 14:23 KST 관측값은 검색 준비 `ready` 17,272건, 시황 준비 `ready` 194건이다. 검색기사 `028260`의 준비 529건은 실제 새 회차와 겹치며 검색 수집 heartbeat는 82페이지·기사 662건으로 전진했다. 시황 수집 상태는 2020-08-25 FLASH, 완료일 2건이고 2020-07-08 FLASH 83페이지의 이전 페이지 반복 오류 1건은 미해결로 남겨 둔다. 시황 원자료 페이지 commit 직후 준비 callback 전에 중단된 경우 재시작 시 직전 페이지를 재처리하도록 보완했고 단위검사 7건을 통과했다. 원자료를 NAS 운영 뉴스로 다시 보내는 과거 raw import는 검색 수집기에서 비활성화했다.

2026-09-24 14:28 KST 검색뉴스 NAS 표본: 준비 원장 최초 100건을 NAS 컨테이너의 정상 뉴스 적재 함수로 투입한 결과 `imported=100, skipped=0, failed=0`이었다. 첫·50번째·100번째 기사의 저장 ARTICLE, `fulltext` BODY와 비어 있지 않은 본문, RULE assessment를 인증 API에서 읽기 전용 확인했다. 검색 준비 19,191건의 불변 SQLite 스냅샷(128,626,688바이트, SHA-256 `0CD2AC49FF1F176DAD2BA1DCFB72D4B759C0D450BF3E71372F9BE44D425F1141`)과 시황 준비 103건의 스냅샷을 NAS maintenance에 해시 일치로 배치했다. 컨테이너에서 두 스냅샷을 차례로 적재하며 잠금·상태·로그를 남기는 `run_prepared_historical_news_imports.py`도 배치했으며, 이 시점에는 사용자 SSH의 백그라운드 실행 결과를 기다리고 있다. 기존 100건은 재실행 때 영속 ledger로 건너뛴다. 현재 진행 중인 PC 준비 원장의 나머지 기사는 이 스냅샷에 포함되지 않았으므로 이후 추가 스냅샷과 적재가 필요하다.

2026-09-24 14:35 KST NAS 백그라운드 적재 실행 확인: 사용자 SSH `docker exec -d`는 화면 출력이 없지만 NAS `prepared-news-import-status.json`에 `running/search`가 기록됐다. 첫 누적 로그는 `imported=100, skipped=100, failed=0`이다. 앞서 시험 투입한 100건은 중복 삽입되지 않았고 다음 100건이 새로 적재됐다. 전체 19,191건과 시황 103건의 완료 여부는 아직 확인되지 않았다. 원래 원천 수집과 PC 준비도 계속 진행 중이며 14:33 KST 관측값은 검색 준비 21,753건, 시황 준비 1,179건이다. NAS 적재 속도는 첫 100건만으로 장시간 평균을 확정하지 않는다.

NAS `import_prepared_historical_news_to_nas.py`의 1건 시황 시험 적재는 `imported=1, skipped=0, failed=0`이었다. 인증된 뉴스 이력 API로 해당 기사 BODY가 `fulltext`이고 본문이 비어 있지 않으며 RULE assessment가 저장됐음을 읽기 전용 확인했다. 이 결과는 1건의 정상 경로 검증이며 전체 검색·시황 대량 적재 완료를 의미하지 않는다. 검색기사 100건 NAS 시험 적재는 사용자 SSH 실행 결과를 기다리는 중이다. 이후 불변 SQLite 준비 스냅샷을 순차 적재하고 각 단계에서 실패·누락을 검사한다. 아래 13:30 KST 문단의 삭제·적재 미확인 표현은 그 시점의 기록이며 이 문단이 최신 상태다.

2026-09-24 과거자료 수집기 모니터 갱신: 화면의 CREON 분봉·주도후보 수정주가/DART 수집 칸과 재시작 버튼을 제거하고, 네이버 검색 종목뉴스·증권 FLASH/WORLD 원천 수집 상태 아래에 PC BODY/RULE 준비 건수, 원문/요약만/실패, 마지막 처리 시각을 각각 표시한다. PC 처리 원장은 별도 읽기 작업으로 10초마다 조회해 창 갱신을 막지 않으며 NAS 운영 DB 적재 완료로 표시하지 않는다. 13:55 KST 상태 파일 기준 원천 뉴스 수집기 둘 다 `stopped`; PC 뉴스 전처리 프로세스는 실행 중이다. 시황뉴스 마지막 15페이지 관측 간격은 약 1.7초이고 기존 고정 대기는 1.5초였다. 수집 중단 상태에서 읽기 전용 8페이지 표본을 0.7초 간격으로 조회했을 때 HTTP 오류 없이 각 0.09~0.33초 응답했다. 시황 수집 실행 기본 간격을 0.7초로 낮췄다. 장시간 차단 여부와 전체 처리량은 재시작 후 확인이 필요하다. 별도 키움 주도후보 수정 일봉 7년 백필은 13:55 KST 현재 1,535/2,637종목으로 여전히 실행 중이며 모니터에서 칸을 제거한 것과 완료 여부는 별개다.

2026-09-24 13:30 KST 과거뉴스 단발성 전환: 사용자가 NAS의 BODY/RULE 미완료 과거기사 삭제와 PC 원자료 재처리를 결정했다. NAS 컨테이너 내부 일회성 정리 스크립트의 미리보기는 기존 범위 399,118기사 중 삭제 대상 394,698기사, 다른 리비전의 참조 때문에 보존할 미완료 5기사, 취소할 미완료 작업 394,758건이었다. 사용자가 `--execute`를 시작했으나 마지막 `mode=done`은 아직 확인하지 않았다. NAS가 생성한 삭제 대상 목록 394,698건은 PC `data/historical_collection/purged-legacy-news-20260924.jsonl`에 SHA-256 일치로 복사했다. 검색 394,595·시황 103건의 PC 재수입 원장은 삭제 완료 확인 후에만 재대기시킨다. 기존 `preprocess_historical_news_to_nas.py`는 NAS 작업 선점에서 60초 읽기 시간 초과로 종료됐고, 이는 PC 선처리 요구와도 맞지 않아 재시작하지 않았다. 대신 `preprocess_historical_news_locally.py`가 PC 원자료에서 BODY/RULE을 계산해 `data/historical_collection/prepared_news.sqlite3`에 저장하도록 실행 중이며 첫 700건 완료·실패 0건을 확인했다. 이 로컬 결과의 정상 NAS 적재 경로와 실제 저장 검증은 아직 남았다. 키움 7년 일봉 백필은 별도 프로세스로 유지한다.

2026-09-24 12:55 KST 사용자 NAS 재빌드 후: 운영 `/health.status=ok`, `server_build=2026.09.24-news-claim-diagnostics-v6`, PC 과거뉴스 범위 `market,search,legacy_backlog`를 확인했다. 재빌드 중 중단된 키움 수정 일봉 백필을 동일 `--as-of 2026-09-24 --no-pause-market-hours`로 재개했고 기존 실패 `008290`·`046070`은 `query_base_dt=2026-09-18` 재조회로 각각 3페이지·누락 0건 완료됐다. 12:55 기준 일봉 1,257/2,637종목 완료·실패 0건, PC 뉴스 BODY/RULE 재개분 30건 완료·실패 0건, FLASH 수입 재개분 1,800건이며 세 프로세스가 실행 중이다. 새 `pc_search` 범위 2건 한정 표본은 첫 선점 요청이 60초 읽기 시간 초과로 종료됐다. 같은 시각 기존 `pc` 범위의 NAS 진단 로그는 연결 12~25ms, SELECT 약 5~8초, 갱신/커밋 대체로 수백 ms를 보였고 PostgreSQL wait 표본에 `IO:BuffileWrite`, `LWLock:WALWrite` 등이 나타났다. 따라서 기존 범위 선점 지연의 주 구간은 SELECT임을 확인했지만 새 범위의 장시간 요청은 서버 완료 로그가 없어 실행계획·대기 원인을 확정하지 않았다. 새 범위 단독 표본은 반복하지 않고 기존 수집·전처리 작업을 유지한다.

2026-09-24 12:43 KST 운영 점검: 키움 수정 일봉 7년 백필 1,217/2,637종목 완료·2종목 실패 상태에서 프로세스는 계속 실행 중이다. PC 과거 검색기사 BODY/RULE은 390건 완료·0건 실패, FLASH/WORLD 수입은 18,500건까지 진행했으며 두 작업의 오류 로그는 비어 있다. 새 검색기사 `pc_search` 단독 선점 표본은 클라이언트 60초 읽기 시간 초과로 종료됐고, 당시 NAS의 다른 뉴스 선점·시장 작업은 진행했으며 PostgreSQL 조회·저장 수 초 지연도 로그에 있었다. 선점 요청 내부 원인은 현재 로그만으로 확정할 수 없어 `database.py`에 연결·SELECT·갱신/커밋 및 DB wait event 계측을 추가했다. 진단 빌드 `2026.09.24-news-claim-diagnostics-v6`의 5개 소스 파일은 기존본을 `X:\kiwoom-monitor-backups\20260924-news-claim-diagnostics-v6`에 백업한 뒤 NAS 프로젝트와 SHA-256 일치로 동기화했다. 운영 컨테이너는 아직 v5이며 진행 중인 백필을 끊지 않도록 재빌드하지 않았다. 30분 일봉 감시는 계속 ACTIVE다.

2026-09-24 11:35~11:51 KST 운영 재빌드 후: NAS `/health.server_build=2026.09.24-pc-news-backlog-v5`, `historical_news_pc_scopes=market,search,legacy_backlog` 확인. 키움 수정 일봉 7년 백필은 NAS 재빌드 중 `WinError 10054`로 882종목 완료 지점에서 멈췄다가 같은 `--as-of 2026-09-24 --no-pause-market-hours`로 재개해 11:51에 984/2,637종목 완료·실행 중이다. `008290`, `046070`은 현재 기준일의 키움 `ka10081`이 날짜·가격이 전부 빈 성공 표식 한 행을 반환해 실패했다. `008290`은 마지막 로컬 거래일 `2026-09-18` 기준 재조회에서 600행과 연속조회가 실제로 확인됐으므로, 빈 첫 표식에만 마지막 확인 거래일로 재시도하는 보완과 단위검사 5건을 적용했다. 현재 실행 중 프로세스는 시작 당시 코드를 사용하므로 이 두 종목은 다음 안전한 재개에서 다시 확인해야 하며 완료로 표시하지 않는다. 기존 NAS 적재 검색기사 `historical_backfill`의 PC BODY/RULE 운영 표본 12건이 오류 없이 완료됐고, 적격 미반영 검색기사 3,352건을 추가 수입했다. PC 본문 작업기 4개 lane과 FLASH/WORLD 과거기사 수입기는 실행 중이며 각 로그·오류 로그로 진행을 확인한다. 검색뉴스 원자료 수집기는 `STOP_NEWS`, 시황뉴스 원자료 수집기는 `STOP_MARKET_NEWS` 정지 요청으로 멈춰 있어 임의 재시작하지 않았다. 일봉 백필 30분 감시 자동화 `7-nas`는 ACTIVE다.

2026-09-24 누적 NAS 빌드 소스 대조: v5 뉴스 변경 12개 파일의 공유 동기화 뒤 Dockerfile 검증 문자열과 Compose 이미지 태그가 여전히 v3인 불일치를 발견해 둘 다 `2026.09.24-pc-news-backlog-v5`로 맞췄다. `server.Dockerfile`의 `COPY` 대상인 로컬 `src` 전체(생성된 egg-info/pycache 제외), `pyproject.toml`, `README.md`, 세 스크립트와 두 배포 파일 총 296개를 NAS 프로젝트와 SHA-256 대조했고, 차이·누락 28개를 `X:\kiwoom-monitor-backups\20260924-cumulative-v5`에 기존본 백업 후 동기화했다. 최종 불일치 0개이며 Python 소스 292개 구문 컴파일이 통과했다. 운영 `/health`는 이 점검 시 Synology 404를 반환해 실행 이미지 빌드는 확인하지 못했다. 소스 동기화는 컨테이너 재빌드·재생성을 뜻하지 않는다.

2026-09-24 과거 뉴스 BODY/RULE 인계 v5 소스 준비: 기존 NAS 적재 검색·FLASH/WORLD 기사와 신규 PC 적재 기사의 미완료 BODY/RULE을 모두 PC `--scope pc`가 원자 선점하도록 바꿨다. NAS 일반 작업기는 이 네 과거 범위를 새로 선점하지 않으며, 완료된 본문·규칙 revision과 기사·source observation은 그대로 보존한다. 검색뉴스 원문시각 확인 때 읽은 동일 HTML에서 본문을 추출해 로컬 `news_article_body_snapshots`에 저장하고 PC BODY가 재사용한다. 기존 수집분에 스냅샷이 없으면 PC BODY가 한 번 읽으며 RULE은 NAS 저장 BODY를 재사용한다. 검색뉴스 수입은 `training_eligible=1` 및 `article_fetch_status=published_at_found`만 대상으로 하므로 이미 원문 불가·시각 미확인으로 판정된 건을 재시도하지 않는다. 관련 단위검사 88건에서 87건 통과·1건 건너뜀(`fastapi` 미설치)과 컴파일 검사를 마쳤다. 변경 소스·문서 11개를 `X:\kiwoom-monitor-backups\20260924-pc-news-backlog-v5`에 기존본 백업 후 NAS 프로젝트에 SHA-256 일치로 동기화했다. 검색뉴스 원자료 수집기는 권한 있는 PC 세션에서 재개됐고 신규 본문 스냅샷 저장을 확인했다. 운영 `/health.server_build`는 여전히 v3이므로 기존 적재분의 실운영 소유권 전환은 사용자 재빌드 후에만 발생한다. PC 전처리기는 `legacy_backlog` capability가 없으면 시작 전에 거부한다. 아래 v3/v4 상태 설명은 당시 기록이며 현 설계는 이 문단을 따른다.

2026-09-24 과거 뉴스 처리 소유권 후속: 운영 NAS `/health.server_build=2026.09.24-pc-news-market-v3`를 확인한 뒤 FLASH 2019-01-01 PC 전담 표본 3건을 올리고 PC BODY 3건·RULE 3건을 모두 NAS 원장에 완료했다. 네이버 검색뉴스의 신규 NAS 업로드는 `historical_news_pc_backfill` 범위로, 시황은 `historical_market_pc_backfill` 범위로 분리하는 `2026.09.24-pc-news-search-v4` 소스를 만들었다. NAS 일반 작업기는 두 PC 범위의 BODY/RULE을 선점하지 않고 PC 작업기는 기본 `scope=pc`에서 두 범위만 선점한다. 검색뉴스 업로드기는 NAS `/health.historical_news_pc_scopes`에 `search`가 없으면 업로드 전에 중단하므로 v3 운영 중 원자료 수집을 재개해도 신규 검색기사의 NAS BODY/RULE 소유권은 바뀌지 않는다. 검색뉴스 원자료·증권 시황뉴스 원자료 수집을 10:50 KST 이후 각각 재개했다. 검색뉴스 수집에서 비주식 후보가 실제로 검색되는 것을 발견해 원본 기사·페이지는 보존하면서 개별 주식 외 후보의 대기 일별 작업 10,703건과 범위 작업 589건을 `excluded`로 변경하고 11:01 KST에 재개했다. 이미 완료한 비주식 검색 3,135일·225범위는 이력으로 보존한다. 시황 2020-07-08 FLASH의 83페이지 반복 오류 1건은 원장에 남아 있으며 완료로 간주하지 않는다. v4 관련 NAS 프로젝트 파일은 `X:\kiwoom-monitor-backups\20260924-pc-news-search-v4`에 기존 파일을 백업한 뒤 SHA-256 일치로 동기화했다. 운영 컨테이너는 여전히 v3이며 사용자 재빌드 후 검색뉴스 PC 처리의 운영 표본을 검증해야 한다.

2026-09-24 PC 창 상태: 시장 뉴스(별도 뉴스 프로세스), Shadow 후보, 전략 연구, 모의 자동운용, 기본 설정 창의 마지막 위치·크기를 이 PC의 QSettings에 저장하고 재실행 시 복원한다. 기본 설정의 기존 SQLite 크기 설정은 초기값으로 유지하며 저장된 창 geometry가 있으면 이를 우선한다. 숨김 처리하는 창은 닫기와 앱 종료의 `stop()` 경계에서도 저장한다. Qt 오프스크린 창 재생성·관련 회귀 46개가 통과했으며, 실행 중인 PC 앱에는 재시작 뒤 적용된다. NAS 컨테이너 재빌드는 이 UI 변경에 필요하지 않다.

2026-09-24 과거 뉴스 PC 전처리 구현: `scripts/preprocess_historical_news_to_nas.py`가 인증된 `/api/v1/news/historical-jobs/claim`, `/complete`로 NAS의 `historical_backfill` BODY/RULE 작업을 가져와 PC에서 같은 추출·평가·공급계약 규칙으로 계산한다. NAS 저장은 기존 불변 revision/문서 원장과 작업 완료를 한 트랜잭션에 기록하며 동일 시도 재전송·만료 시도를 구분한다. 로컬 SQLite 경계 테스트는 통과했으나 PostgreSQL·HTTP 통합, 새 컨테이너 빌드 확인, 실제 기사 처리량/차단률은 미검증이다. 현재 NAS 운영 빌드와 대량 작업은 전환하지 않았다.

2026-09-24 NAS `2026.09.24-pc-news-preprocess-v1`의 `/health=ok`를 확인했다. 직전 이미지의 `FIVE_MINUTE_BAR_COLUMNS` import 실패는 NAS 공유 소스의 누락 codec 정의를 백업 후 동기화해 해소했고, Dockerfile에 서버 import 빌드 검사도 추가했다. 과거 FLASH/WORLD 시황 업로드·개별 주식 후보 필터의 `2026.09.24-pc-news-market-v1` 소스와 빌드 표식은 `X:\kiwoom-monitor-backups\20260924-pc-news-market-v1`에 기존 파일을 백업한 뒤 NAS 프로젝트로 SHA-256 일치 동기화했다. NAS 공유 소스로 임시 SQLite의 인증 API 업로드·시황 조회를 검증했고, 운영 5분봉 테이블은 생성되지 않음을 확인했다. 뉴스 회귀 72개가 통과했다. 현 운영 이미지에는 아직 반영되지 않았으며, 사용자 이미지 재빌드 뒤 `/health.server_build`, PostgreSQL 표본 업로드, PC 본문 작업 처리량을 확인해야 한다. 대량 업로드는 그 이후다. FLASH/WORLD에 일반 종목뉴스의 불필요 기사 사전 필터를 적용하면 종목명이 없는 해외·시황 기사가 빠지는 문제가 있어, 실시간 NAS 수집기와 PC 과거 업로드 모두 기본 정보(제목·링크·발행시각)만 확인하도록 변경했다. 종목 연결은 별도 판정하고 연결되지 않은 기사도 원래 시황 탭에 보존한다. 이 수정은 `2026.09.24-pc-news-market-v2` 소스이며 회귀 97개가 통과했다. 관련 소스·빌드 표식·문서 8개를 NAS 프로젝트에 SHA-256 일치로 동기화하고 이전 파일은 `X:\kiwoom-monitor-backups\20260924-pc-news-market-v2`에 보존했다. 동기화 뒤 운영 `/health.server_build`는 아직 `2026.09.24-pc-news-preprocess-v1`이므로 실시간 NAS 수집에는 재빌드 전까지 적용되지 않는다. 사용자 재빌드 뒤 운영 `/health`가 `ok`, `server_build=2026.09.24-pc-news-market-v2`임을 확인했다. NAS PostgreSQL에 FLASH 2019-01-01 표본 3건과 추가 100건을 인증 API로 올렸고 원천 원장, 기사 revision, 시황 목록, 재전송 `already_imported`를 확인했다. 첫 3건의 본문은 NAS에서 `fulltext`로 완료됐다. 추가 100건 배치는 7.85초였다. 이 103건은 NAS 처리 소유로 남긴다. 사용자 결정에 따라 이후 업로드는 `processing_owner=pc` 범위로 분리하고 NAS 일반 BODY/RULE 선점을 막는 `2026.09.24-pc-news-market-v3` 코드를 준비했다. v3는 아직 운영 컨테이너에 반영되지 않았으므로 새 과거 시황 업로드는 재빌드·PC 경계 표본 확인 전까지 진행하지 않는다. 소유권 분리 코드·API 계약·스크립트·테스트 13개 파일을 `X:\kiwoom-monitor-backups\20260924-pc-news-market-v3`에 기존 파일을 백업한 뒤 NAS 프로젝트에 SHA-256 일치로 동기화했다. 관련 회귀 98개가 통과했다. 운영 `/health.server_build`는 이 시점에 v2다. PC 전처리 스크립트 기본 범위도 `pc_market`으로 좁혀 기존 NAS 처리 기사 선점을 막았고, 수정 전 스크립트는 `X:\kiwoom-monitor-backups\20260924-pc-news-market-v3-script-default`에 보존했다.

2026-09-24 주도후보 7년 일봉 NAS 재조회 완료: 과거 실시간조회순위가 없는 연구 구간에는 `candidate_days`의 장후 주도후보를 별도 provenance로 사용한다. `stocks.market_code` 0/10인 코스피·코스닥 개별 주식 2,637개만 대상으로 하고 ETF·ETN·리츠 308개는 제외했다. `scripts/backfill_candidate_daily_to_nas.py`가 NAS `/api/v1/kiwoom/query`의 `CentralRestBroker`를 통해 키움 `ka10081` 수정 일봉을 2026-09-24 동일 기준일로 연속조회해 NAS 운영 일봉에 저장했다. `data/historical_collection/kiwoom_candidate_daily_7y_status.json`은 16:38 KST `phase=complete`, 2,637개 전부 `complete`이며 각 종목 저장 후 NAS 재조회로 PC 후보 DB의 2019-09-24 이후 기대 거래일 대비 `missing_after=0`을 확인했다. 이전의 `last_error`는 재개 이전 연결 재설정의 잔여 기록이며 최종 코드·로그·누락 검증은 완료 상태다. 9월 24~25일 휴장에 맞춰 `--no-pause-market-hours`로 실행했다. `scripts/report_candidate_event_dates.py`의 기업행동 보고서는 개별 주식 894종목·사건 1,447건(분류 682·수동 검토 667·미매칭 98)을 `data/research/needed_candidate_events_20260924.json`에 보존했다. 이는 후보일 이전 249거래일~다음 일봉 연구 창과 겹치는 CREON/DART 알려진 사건만 포함하며 키움 고유 사건 누락 가능성을 명시한다.

2026-09-24 연구 가격 기준 결정: 타점 연구의 1분봉·5분봉과 체결·장중 위험 계산에는 당시 실제 거래가격인 원주가를 사용한다. 키움 실시간 가격과 CREON 과거 봉은 공급자·시각·가격 기준을 검증해 연결한다. 장기 특징과 거래일 간 수익률은 키움 수정 일봉을 별도로 사용하고 기업행동 사건일·유형과 당시 이용 가능 시점을 함께 기록한다. 수정 일봉의 가격 수준을 원주가 분봉과 직접 비교하지 않는다. 보유 기간이 기업행동일을 가로지르면 수량·현금 변화까지 처리하거나 해당 사례를 격리한다. 키움 기준 수정분봉 전체 재구성은 타점 연구나 D03의 선행 조건이 아니며, 아래 검증 기록은 가격 기준 차이와 향후 필요 시 파생값을 감사하기 위한 근거다. 원본·운영 DB는 이 결정으로 변경하지 않았다.

2026-09-25 NAS/CREON 1분봉 연결 전 표본 검증: 읽기 전용 감사 도구로 2026-09-22 `035720`의 CREON 원주가 정규장 381봉과 NAS 키움 KRX 저장 615봉을 대조했다. 처음에는 CREON 15:30 마감 체결에도 일반 봉처럼 1분을 빼서 15:29 봉 1개가 NAS에 없다고 잘못 보고했다. 실제 두 원천의 15:30 OHLCV가 같아 마감 체결만 같은 15:30 시각으로 비교하자 정규장 381봉 모두 OHLCV 일치, NAS 정규장 외 234봉으로 정정됐다. `347850`은 CREON 정규장 381봉 대비 NAS 0봉이었다. 두 NAS 조회의 전체일 coverage는 미완료다. D03 어댑터도 CREON 15:30 마감 체결을 가상 15:29~15:30 연속 1분으로 재생하던 경계를 수정해 15:20~15:30 종가 단일가 단계로 명시한다. 기존 동결 파생 입력·구조 실행은 이 변경을 포함하지 않으므로 다음 성과 비교 전에 새 ID로 재생성해야 한다. 별도 불변 SQLite의 CREON 원주가 1분봉은 NAS 운영 PostgreSQL과 직접 통합되지 않았고, 5분봉도 기존 1분 전략 입력에서 제외한다.

2026-09-25 CREON 5분봉 시각 감사: 읽기 전용 도구로 2023-09-22의 `005930`·`000660`·`035720`과 2024-08-28의 이 세 종목 및 `347850`을 확인했다. 7종목·일 모두 09:05~15:20의 5분 종료시각 76개와 별도 15:30 마감 체결 1개, 합계 77봉이며 중복·예상 밖 시각·OHLCV 역전·하루 내 1분/5분 혼합은 없었다. 2024-08-29 네 종목은 `005930`·`000660`이 1분봉 381개, `035720`·`347850`이 5분봉 77개로 종목별 전환 시점이 달랐지만 같은 종목·일의 해상도 혼합은 없었다. 이는 저장 시각/구조 표본이며 5분봉과 1분봉의 같은 날짜 가격 집계 검증이나 전체 종목 완전성 보증은 아니다.

2026-09-25 지연 개장일 보완: 2025-11-13 `347850`의 15:30 CREON 1분봉은 일반 봉이고 실제 종가는 16:30이었다. 고정 15:30 마감 가정이 동일 15:30 분의 중복을 만든 직접 원인이다. KRX 공지와 CREON 원시 시각으로 확인한 2021-11-18, 2022-11-17, 2023-11-16, 2024-11-14, 2025-11-13의 10:00~16:30 정규장 예외를 공통 시간표와 읽기 전용 감사·D03 재생에 반영했다. `347850` 2025-11-13 원봉 379개는 이제 중복 없이 정렬되지만 NAS 대응 봉은 0건이어서 공급자 간 가격 일치는 검증할 수 없었다. 2021/2022/2023 지연일 삼성전자 5분봉은 각 77개의 10:05~16:20 종료 구간 및 16:30 종가 체결과 일치했다. 기존 동결 연구 입력은 불변이며 재생성 전 성과 승격에 사용하지 않는다.

2026-09-24 키움 기준 수정주가 전체 검증 **실패**: CREON 원일봉/키움 수정 일봉의 후보일 80,809쌍을 CREON 기업행동 경계별로 검사해 4,080구간 중 84구간에서 단일 조정계수가 성립하지 않았다. 최근 2026-09-14 이후 170쌍을 제외해도 10종목 구간이 실패한다. 5종목의 CREON 필드 19는 전 구간 `100`이지만 키움의 원/수정 일봉은 직접 재조회에서 달랐다. 최근 표본에서는 키움 일봉 종가가 키움·CREON 마감 분봉 종가와도 달라 단순히 원 DB 갱신 지연이라고 단정할 수 없다. 원본/운영 DB는 변경하지 않았고, 자세한 수치·예외는 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 기록했다. 기존 2종목 성공 표본을 전체 검증으로 취급하지 않는다.

2026-09-24 수정주가 제외·후보 원장: `scripts/audit_kiwoom_adjustment_candidates.py`가 세 원본 DB를 읽기 전용으로 검사했다. 동률 올림 가정의 정확한 반올림 경계에서 불일치 84구간은 제외 대상으로, 일봉 기준 단일 4자리 계수 후보 2,948구간은 분봉 대조 대기로 표시했다. 계수 모호 608·관측 1일 432·4자리 계수 없음 8구간도 보류다. 최근 후보일을 제외해도 10구간이 불일치한다. 판정 JSON은 `data/historical_collection/kiwoom-adjustment-candidate-audit-20260924.json`이며 원본·운영 DB는 수정하지 않았다. 일봉 원장의 종목·사건 구간 전체를 분봉 검증 완료로 승격한 것은 0개다.

2026-09-24 수정분봉 실측: 보관 키움 1분봉과 비단위 일봉 계수 후보가 겹친 711종목·일(공통 234,646분) 중 708일은 CREON 원주가와 전부 일치했고 3일은 다른 기준이었다. 키움 ka10080을 사건 이후 기준일로 직접 재조회한 6종목·일은 날짜별 계수를 적용한 CREON 원분봉과 공통 2,025분 OHLC가 모두 일치했다. 183300은 첫 사건 직후 0.5093, 두 번째 이후 0.2041로 같은 과거일 기준이 달라졌다. 059120과 183300은 4자리 계수에서 반올림 동률 처리 결과가 서로 달라 공급자 산식을 확정하지 않는다. 일봉 84개 불일치 구간은 동률 올림/짝수 가정 모두 유지되지만 단일 4자리 후보 수는 각각 2,948/2,856으로 다르다. 6일 표본 외 전 구간·5분봉은 미검증이다. 상세 표본 원장은 `data/historical_collection/kiwoom-adjusted-minute-verified-samples-20260924.json`이다.

2026-09-24 수정주가 재료 DB 감사: 개별 주식 2,637개 모두 키움 수정 일봉이 있고 CREON 원본 1분봉은 2,617개·5분봉은 2,448개에 있다. CREON 조정 작업 2,634개 완료·3개 공급 불가, 사건 2,106건 중 DART 자동 유형 분류는 987건(수동 검토 980·미매칭 139)이다. 사건일 2,106건 모두 키움 일봉이 있지만 실제 CREON 사건일 봉은 1,416건에 있다. 정확한 키움 조정계수 전 종목·전 구간 저장과 봉 단위 검증은 아직 하지 않았다. 상세 집계·예외는 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 기록했다.

2026-09-24 키움 기준 수정주가 재구성 표본: `347850` 사건 전 CREON 원분봉에 키움 원/수정 일봉에서 얻은 계수 `0.2503`을 적용하자 키움 수정 1분봉과 공통 379분 OHLC가 모두 일치했다. `406820`에서는 반올림된 일봉 시가 한 쌍의 비율로는 270분 중 264분만 일치했고, 키움 분봉 가격쌍이 허용하는 계수 구간에는 `0.2502`가 포함됐다. 따라서 키움 기준 수정 계수는 검증된 날짜별 구간으로 산출·기록해야 하며 CREON 수정주가나 일봉 한 가격의 단순 비율을 그대로 사용하지 않는다. 과거 전체 구간은 아직 미검증이고 실시간 체결 원주가를 수정값으로 덮어쓰지 않는다. 상세 근거는 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 있다.

2026-09-24 키움/CREON 원주가 직접 비교: 키움 `ka10080` 수정 옵션 0으로 3종목·4거래일을 새로 조회하고 보관된 CREON 원주가 1분봉과 시작/종료시각을 맞췄다. 공통 1,218분의 OHLC 가격은 모두 일치했고, OHLCV는 1,212분 일치했다(6분 거래량 차이). 표본별 수치와 범위 제한은 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 기록했다. 기존 저장 키움 분봉의 큰 차이는 수정 옵션 1의 기준일 의존 응답이며, 키움/CREON 원주가 자체의 가격 불일치로 해석하지 않는다.

2026-09-24 CREON 수정주가 직접 NAS 수집 준비: 기존 키움 실시간·조회 보완 1분봉은 동일한 `central_minute_bars`를 사용한다. 30초 순위 편입 뒤 구독을 시작한 첫 분봉은 앞부분이 빠질 수 있으며 수집기는 `partial`로 표시하고 당일 `ka10080` 보완이 같은 행을 교체한다. 따라서 기존 행 존재만으로 CREON 수정주가 적재를 건너뛸 수 없다. 새 `central_five_minute_bars` 스키마 마이그레이션 v20은 코드에 추가했고 SQLite 회귀 34건을 통과했다. NAS 컨테이너 재빌드·운영 DB 마이그레이션·CREON 직접 전송·실수집은 아직 하지 않았다. 기존 원주가 원본과 운영 DB는 변경하지 않았다.

2026-09-24 NAS/PC/CREON 봉 기준 실측: NAS 인증 API에서 삼성전자·SK하이닉스·카카오·한화오션 PC 일봉 7,580행이 NAS와 OHLCV 전부 일치했고 NAS에는 PC 범위 밖의 2016년 일봉도 있었다. 삼성전자 2025-09-01 PC/NAS 분봉 382개는 일치하지만 2026-09-18에는 NAS가 11분 더 많고 공통 622분 중 11분의 값이 달랐다. NAS에 기존 실시간·보완분이 있어 단순 일괄 덮어쓰기는 부적절하다. 더 결정적으로 `347850`의 2025-10-31 PC 분봉 381개는 NAS에 어느 시장 키로도 없고, PC 1분봉은 관리자 CREON 실조회의 **원주가**와 379분 중 377분이 일치했다(2분은 거래량만 차이). CREON **수정주가**와는 공통 379분 모두 OHLCV가 달랐고 09:00 가격은 PC 240,000원대 대 CREON 수정 60,000원대였다. 같은 날 키움 PC/NAS 일봉 시가는 60,072원으로 수정 수준이다. 따라서 키움 일봉과 1분봉은 같은 수정 기준이라고 가정하지 않으며, CREON 수정 1분봉을 기존 실시간/원주가 `central_minute_bars`에 덮어쓰지 않는다. CREON 종료시각을 키움 시작시각과 비교할 때는 1분 차를 적용했다. NAS 일봉 전체·분봉 전체의 행 단위 완전성이나 과거 적재 방식은 아직 확인하지 못했다.

2026-09-24 다종목 확대: 기업행동 10종목의 사건 전후 각 1일과 일반 종목 4일, 총 24개 종목·일의 키움 PC 1분봉을 보관된 CREON 원주가 1분봉과 시작/종료시각을 정렬해 비교했다. 공통 7,538분 중 OHLC 일치 5,466분, OHLCV 일치 5,430분이다. 기업행동 전은 3,312/3,312분 가격 일치, 후는 674/2,746분만 일치, 일반 종목은 1,480/1,480분 일치했다. `347850`·`406820` 사건 후 두 날짜의 CREON 원주가를 관리자 세션에서 다시 받아 보관 응답 464분과 모두 일치시켜 파일 오류 가설을 배제했다. 사건 후 일부 키움 1분 가격은 CREON 원주가의 약 1.8~4배지만 다른 종목은 그대로여서 일괄 환산도 할 수 없다. 같은 종목 사건 전 키움 일봉 종가는 CREON 원주가 일봉의 약 1/4인 표본이 있어 일봉과 1분봉 수정 기준을 분리해야 한다. 상세 표본과 제한은 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 기록했다. NAS 봉은 변경하지 않았다.

2026-09-24 키움 직접 재조회로 원인 축소: `347850`의 2025-11-13 10:00 동일 분봉을 `ka10080` 수정 옵션 0으로 조회하면 시가 80,000원이고, 옵션 1/기준일 2025-11-13에서는 319,616원, 옵션 1/기준일 2025-11-14에서는 다시 80,000원이다. `406820`, `203400` 사건일에도 같은 기준일 의존 차이를 재현했다. `ka10081` 일봉 역시 기준일을 사건 전후로 바꾸면 과거 종가가 320,000원에서 80,096원으로 달라진다. 기존 PC 수집기는 일봉 `base_dt=최종일`, 분봉 `base_dt=후보 당일`을 모두 수정 옵션 1로 요청했으므로 두 해상도가 같은 가격 기준일 수 없다. 공급자 내부 조정 산식 자체는 공개 계약에 없지만 요청 조합과 응답 차이는 실증됐다. 현재 NAS 당일 자동 분봉 보완도 수정 옵션 1/당일 기준일을 사용하고 수정/원주가를 같은 테이블에 저장하므로 기업행동일 오염 가능성을 다음 수정에서 다룬다. 운영 DB는 아직 건드리지 않았다.

2026-09-24 과거자료 NAS 파일 게시 완료: `historical-intelligence/v1`의 메인 SQLite run `20260923T125937Z-58f3f4d08f5a`(157,355,573,248바이트)와 `daishin-raw-v1`의 원응답 run `20260923T200030Z-455874db8bf1`(9,004파일, 75,960,068,935바이트)이 `latest.json` 및 manifest에 연결됐다. 게시기는 NAS 복사본을 다시 읽어 SHA-256을 대조한 뒤 포인터를 갱신했고, 이번 확인에서 파일 크기·포인터/manifest 해시·원응답의 메인 run 연결이 일치했다. 이 파일 게시는 운영 PostgreSQL 적재가 아니다. CREON 과거 분봉과 시장 맥락 자료는 아직 NAS 운영 봉 API나 연구의 동일 DB 조회 경로에 통합되지 않았으며, 게시 완료를 연구 데이터 통합 완료로 보지 않는다.

2026-09-24 NAS 뉴스 BODY/RULE 대기 개선: 운영 로그 20:02~20:13 UTC에서 BODY는 분당 8~10건, 평균 처리 0.5~1.3초였으나 대기시간은 최대 약 43시간이었다. 같은 구간 RULE 처리 로그는 없고 기존 단일 실행기의 작업 선택은 BODY를 RULE보다 앞에 둔다. 삼성전자 저장 목록 최신 6건은 GLOBAL 기사 판본은 확인되지만 해당 판본의 BODY 저장분은 아직 없었다. NAS 서버 내 뉴스 작업 실행기를 최대 3개의 비동기 lane(BODY 2, RULE 선호 1)으로 제한하고 PostgreSQL `FOR UPDATE SKIP LOCKED`를 통한 중복 claim 방지를 유지했다. 서버 API와 별도 OS 프로세스로의 분리는 여전히 미구현이다. 로컬 관련 단위검사 102건 중 100건 통과·2건 건너뜀. 새 빌드 `2026.09.24-news-job-lanes-v1`의 서버 소스·배포 파일 6개를 기존 NAS 파일 백업(`X:\kiwoom-monitor-backups\20260924-news-job-lanes-v1`) 후 SHA-256 일치로 동기화했다. 사용자 재빌드 뒤 `/health`가 새 빌드로 응답했고 첫 1분 단계 로그는 BODY 11건·RULE 5건·실패 0건이다. 장시간 처리량, DB/CPU wait, 순위 지연은 아직 미검증이다.

2026-09-24 시장 뉴스 화면 및 수집원 설정: 메인 TOP20 지수 앞 `뉴스` 버튼은 별도 뉴스 프로세스에서 공통뉴스/실시간 속보/해외뉴스 탭을 연다. NAS 연결에서는 활성화된 수집원만 보여주고 `/api/v1/news/market-feed`로 `central_news_source_observations`의 저장 자료를 읽는다. NAS 시장 뉴스는 발행일 2일 제한 없이 저장 이력에서 최신 순으로 읽고, 새 탭의 최대 표시 건수는 기존 뉴스 설정의 50~1000건 값(예: 200·500)을 따른다. PC 직접 연결에서는 `네이버·DART·AI API 연결`의 수집원 설정에 따라 공통 검색어 네이버 검색 API와 최근 2일 FLASH/WORLD를 뉴스창의 별도 작업에서 수집한다. 이 수집은 기존 뉴스 정책의 60초 재확인·2일 요청범위·로컬 중복 제거/분류/광고 필터·설정된 50~1000건 보존을 따르며 전용 `market_news.sqlite3`에 소스별로 저장한다. NAS 수집원 ON/OFF 및 주소는 `NAS 연결 설정`에서 관리한다. `database is locked` 팝업은 04:06 로그상 메인 스레드의 `StockRepository.update_fundamentals` commit에서 발생했다. 펀더멘털 저장을 전용 캐시 writer로 넘겨 UI 직접 쓰기를 제거했지만 실제 SQLite 잠금 소유자는 아직 미확정이다. NAS 첫 200건 11.6초 지연도 계측을 운영 빌드에 적용한 뒤 재측정해야 한다. NAS 빌드 대상 식별자는 `2026.09.24-market-news-feed-settings-v1`이다. 관련 서버 소스·배포 파일 9개는 `X:\kiwoom-monitor-backups\20260924-market-news-feed-settings-v1`에 원본 백업 후 NAS 프로젝트와 SHA-256 일치로 동기화했다. 재빌드·운영 검증 전에는 현재 운영 동작으로 간주하지 않는다.

확인 기준: 2026-09-23 · **현재 제품 릴리즈 2.1.0** · 로컬 `main`에는 2.1.0 이후 검증·문서 변경이 포함됨

현재 작업 소스는 `C:/Users/pc-1/Documents/ChatGPT/kiwoom-realtime-monitor`, 브랜치는 `main`이다. 이전 `e0b9` 워크트리의 검증된 변경과 현재 문서를 이 폴더에 fast-forward로 합쳤다. 테스트 실행기의 source root와 interpreter/data 위치는 별개다. 2026-09-23 순위 브로커 저장 분리 소스의 빌드 식별자는 `2026.09.23-ranking-broker-persistence-v1`이다. 관련 NAS 소스 4개는 SHA-256 일치로 동기화했고 직전 파일은 `X:\kiwoom-monitor-backups\20260923-ranking-broker-persistence-v1`에 보존했다. 재빌드 후 `/health.status=ok`와 이 빌드 식별자 일치를 확인했다. 10:05~10:07 세 회차의 `ka00198` 큐 대기는 거의 0ms였으나, 키움 응답이 직전 30초 기준을 반복 반환해 일부 회차는 최신 20행까지 약 3.3~3.4초가 걸렸다. 이는 브로커 저장 대기와 별개의 공급 응답 신선도 문제다.

2026-09-24 뉴스 상세 보완: NAS `news_assessment.core_sentences`를 기사·본문 revision 일치 확인 뒤 기존 본문 이력 응답에 실어 앱에서 표시한다. 앱의 원문 재요약은 제거하고 검색 요약은 원문이 없을 때만 상세에 표시한다. 한화오션 저장 목록 200건 조회는 첫 실측 11.6초, 반복 0.5~0.7초였고 임시 로컬 DB의 저장 0.04초·표시용 기사 묶음 0.09초였다. 첫 조회 지연의 NAS 내부 단계는 기존 로그로 구분되지 않아 서비스·PC 조회·표 렌더에 1초 초과 단계별 계측을 추가했다. NAS `news_service.py`와 `app.py`는 각 원본을 `X:\kiwoom-monitor-backups\20260924-news-stored-page-timing` 및 `X:\kiwoom-monitor-backups\20260924-news-stored-assessment`에 백업 후 해시 일치로 동기화했다. 운영 컨테이너 재빌드와 PC 테스트 앱 재시작, 느린 첫 조회 로그 비교는 아직 하지 않았다.

2026-09-23 뉴스 빌드 `2026.09.23-news-sources-stored-page-v1`: 네이버 검색 API/증권 종목뉴스 ON/OFF, 현재 TOP20 기반 2일 수집, 원문 초 단위 발행시각과 원본 목록시각 병행 보존, BODY 이후 일반 판정 저장, NAS 저장분 200건 페이지 조회와 스크롤 추가 로딩을 구현했다. PC 직접 연결의 200건 삭제·표시 상한은 뉴스 설정에서 50~1000건으로 조절한다. 관련 NAS 소스 9개는 SHA-256 일치로 동기화하고 직전 파일은 `X:\kiwoom-monitor-backups\20260923-news-sources-stored-page-v1`에 보존했다. 추가한 BODY/RULE/AI 단계별 처리·대기시간 1분 집계 두 파일도 `X:\kiwoom-monitor-backups\20260923-news-job-metrics-v1`에 백업 후 NAS에 동기화했다. 관련 서버 소스·배포 파일 12개의 SHA-256 일치를 확인했다. 운영 `/health.server_build`는 여전히 `2026.09.23-ranking-broker-persistence-v1`이므로 실제 UI/POST 응답 및 수집 진행은 사용자 재빌드 뒤 확인해야 한다. 뉴스 BODY/RULE/AI는 현 운영 서버의 단일 `NewsJobRunner`가 순서대로 1건씩 처리한다. `/api/v1/diagnostics/resources`에서 뉴스 추정 6.1GB/125만 행, 서버 프로세스 메모리 약 212MB를 확인했으며 새 단계별 계측의 운영 결과는 재빌드 후에 확인할 수 있다. 인증 vault 단일 소유 경계를 유지하는 별도 뉴스 작업 프로세스 설계는 O19에 기록했다.

2026-09-23 20시대 과거 수집기 점검: 시황뉴스는 `market-news-state.json.tmp`를 상태 파일로 교체하는 중 `WinError 5`가 발생해 종료된 것을 전체 traceback으로 재현했다. 임시 파일을 실행별 고유 이름으로 만들고 일시적인 파일 점유에는 제한 재시도하게 수정한 뒤 다시 시작해 2019-06-29 FLASH/WORLD 완료와 NAS 상태 게시를 확인했다. 기존 PowerShell 진입점이 네이티브 stderr 첫 줄을 예외로 받아 전체 traceback을 잃던 문제도 수정했다. 종목 뉴스는 실제로 두 수집기가 동시에 실행돼 같은 heartbeat 임시 파일에서 `WinError 32`가 발생한 로그가 있으며, 적어도 한 수집기의 heartbeat는 계속 갱신 중이다. 당시 대신 분봉 원장은 완료 2,806개·3회 실패 139개(코드 거부 122, 1분 응답 봉 없음 17)였다. 이후 재조회와 개별 주식 범위 조정 결과는 아래 최신 항목을 따른다. 뒤이어 실행한 대신 수집기들은 Python 수집 결과 `finished=0, seeded=0`을 로그에 남긴 뒤에도 상태가 `running`으로 남았다. PowerShell wrapper가 이 결과 뒤에 `--fast` 없이 156GB 원장을 전체 집계하는 호출을 이어서 실행했고 해당 자식 프로세스들이 디스크 `PageIn`에서 대기했다. 이 구간을 초기화 정체로 표시한 heartbeat는 수집기 단계 표시의 한계였으며, 상태 보고 호출에 `--fast`를 추가했다. 당시 수정주가·DART는 작업 완료 5,920·공급 불가 122로 표시됐다. 비주식 308코드를 제외한 최신 원장 수치는 아래에 적었다. 모니터는 완료를 정지로 표시하던 부분과 프로세스가 없는데 최근 DB 행만으로 정상 실행 중으로 판단하던 부분을 고쳤다.

2026-09-23 종목 뉴스 누락: PC `data/news.sqlite3`는 9월 23일 삼성전자 기사가 없고 마지막 조회는 18:21 KST였지만, NAS watchlist 기사 이력에는 같은 날 기사 166건이 있었다. 앱의 마지막 조회 시각 이후만 요청하는 cursor와 NAS의 새 검색 결과만 반환하는 경계가 함께 작용해 운영 `/api/v1/news/search`가 0건을 반환함을 재현했다. 로컬 수정본은 앱에 최근 2일 저장분을 반환하고 공급자 검색만 NAS의 마지막 확인 이후로 제한한다. NAS 실자료를 사용한 로컬 읽기 검증에서는 삼성전자 9월 23일 기사 166건을 포함해 963건을 반환했다. `2026.09.23-news-owner-cache-merge-v1` 서버 파일 4개는 NAS 프로젝트에 SHA-256 일치로 동기화했고 기존 파일은 `X:\kiwoom-monitor-backups\20260923-news-owner-cache-merge-v1`에 백업했다. 운영 `/health.server_build`는 아직 `2026.09.23-ranking-broker-persistence-v1`이며, 새 컨테이너 재빌드와 앱 재시작 후 실화면 확인이 남았다.

[2.1.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.1.0), [2.0.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.0.0), [1.1.20](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v1.1.20) GitHub 릴리즈와 로컬 태그·현재 코드를 대조했다. 로컬 `main`의 후속 작업은 새 GitHub 릴리즈나 배포를 뜻하지 않는다.

## 구현된 기반

| 영역 | 현재 존재하는 기능 | 남겨 둘 구분 |
|---|---|---|
| 시세·NAS | 앱 독립 순위·시장·뉴스·계좌 수집, 봉/관측 revision, 직접 연결·장애전환 | 구현 존재와 실제 NAS 최신 이미지·누적 범위는 별도 |
| 고해상도 관측 | 수신 체결 기반 1초 OHLCV·대금·건수, VI·hot cohort·상한가 기록 | 전 종목 원시 틱/호가와 과거 공백 복원은 아님 |
| 뉴스 | 기사/본문/AI/사건 이력, 작업·cursor·원문 정제·추출 요약 | 현재 검색 API 경로와 증권 사이트 과거 수집은 별개 |
| 테마 | 프로필, 편집·병합·분리, 프로필별 이름 결정 원장, LLM 근거 제안·사용자 검토, 백업·NAS 동기화·시점별 스냅샷 | 실제 기사 품질 평가와 과거 사건 일괄 검토는 후속 |
| 타점 | KRX 돌파·눌림 재가속, 후보 감시, 판단·근거 저장 | 사용자 학습과 사례로 개선할 잠정 정책 |
| 연구 | 동결 export/bundle, 재생·Paper 체결/비용, 제한 탐색·지속 campaign·자원 제어, 준비된 일별 입력의 opt-in 평가 날짜 확장 | 실제 NAS 대규모/24시간 자동 export 검증은 별도 |
| 평가 | 독립 시간/종목 구간, 순차 검증, final 잠금·접근·복구·노출 원장 | 전체시장 일반화·외부 DB/수동 열람까지 미사용 증명은 아님 |
| 가설·일지 | 자동 후속 가설, 상세체결 projection/대조, 피드백·개선 제안·채택·재검증 | 연구 성공·수익성 보장을 뜻하지 않음 |
| 모의 실행 | 후보 게시·admission·위험/복구 gate·계좌 owner·runner/supervisor/UI | 제한 모의·실브로커 장중/장시간 검증은 별도 |
| 인증 | NAS vault, 복수 계좌 프로필·인증 교체·서버 시세 담당 선택 API | PC 시세 담당 선택 UI는 미연결. 직접/fallback 신원 결합의 남은 A4B와 구분 |

현재 연구 DB 계약은 v27, 중앙 인증 활성화 원장은 schema v19 계열이다. R01의 늦게 들어온 빈 평일 재확인은 로컬 가짜 NAS 응답으로 검증했다. 새 날짜 자동 준비·등록은 2026-09-25에 실제 NAS 관측 자료를 읽어 격리된 임시 연구 DB에서도 확인했다. 2026-09-22 09:27:00~09:28:30 KST의 기준 입력 1,373건에서 2026-09-23 같은 시각의 입력을 동결·검증·게시하고 새 TRAIN 작업 1건을 PENDING으로 등록했다(오류 0, 원본/NAS 운영 DB 변경 없음). 검증 범위 시작을 09:27:30으로 잡으면 해당 1분봉의 실제 시작 09:27:00이 빠져 `trading_day_evidence_missing`으로 거절되는 것도 확인했다. 두 임시 연구 DB는 검증 후 삭제했다. 단일 일별 기준 입력의 다음 날짜를 완료 24시간 뒤 하루씩 확인하고, 주말은 건너뛰며 관측 0건 평일은 별도 원장에 남겨 하루 뒤 재확인한다. 자료가 확인되면 게시·등록 후 원장에서 제거하고 새 날짜 cursor는 되돌리지 않는다. 운영 캠페인에 R01 source 설정이 없어 실제 장시간 처리량, 24시간 전체 구간, 지연 자료의 NAS 운영 검증은 아직 남았다. 세부 테이블과 호환 의미는 [DB 스키마](../DB_SCHEMA.md)를 따른다. 이전 문서의 v12/v17 등은 해당 단계 설명이며 현재 전체 구현 수준을 뜻하지 않는다.

## 운영 기록에서 확인된 것과 이번에 확인하지 않은 것

기존 보고서에는 PostgreSQL schema19 왕복·rollback 검증, HTTPS/WSS 연결·앱 주소 전환, 자동 모의운용 fixture/fake-cycle 검증 기록이 있다. 따라서 과거 단계의 ‘PostgreSQL/HTTPS/runner 미구현’을 현재 상태로 반복하지 않는다.

TOP20 화면의 최신 순위 조회는 목표 회차의 종목코드·종목명이 모두 채워진 20행을 NAS가 검증한 직후 메모리 projection을 사용한다. 따라서 키움 조회 뒤 PostgreSQL 스냅샷 저장이 느려져도 앱 표시는 그 저장을 기다리지 않는다. 이전 회차나 부분 응답은 공개하지 않고, 과거·연구 조회는 계속 저장 완료본만 사용한다. 2026-09-22 NAS 실행본 `2026.09.22-top20-live-projection-v1`에서 `/health.status=ok`, 중앙 실시간 `READY`, 최신 TOP20 20행과 `persistence_state=persisted`를 확인했다.

`2026.09.22-theme-suggestion-review-v1` 소스는 2026-09-22 19:46에 NAS로 동기화했고 추적 파일 957개의 SHA-256 일치와 운영 `.env`, `postgres-data`, `server-data`, `server-secrets` 제외를 확인했다. 기존 파일은 `X:\kiwoom-monitor-backups\20260922-194611-theme-suggestion-review-v1`에 보존했다. 컨테이너 재빌드와 새 `/health.server_build` 확인은 아직 하지 않았으므로 실행 중인 NAS 서버 기준은 위에서 확인한 `2026.09.22-top20-live-projection-v1`이다. 장시간 수집·다른 PC 동시 이용·실브로커 모의 검증은 [남은 작업](OPEN_ITEMS.md)에서 관리한다.

2026-09-23 네이버 증권 시황 피드 누락을 확인했다. 기존 NAS query-set과 과거 종목명 검색은 FLASH/WORLD 목록을 직접 순회하지 않았다. 두 날짜별 JSON 원천의 파서, 과거 SQLite 원응답·페이지 원장, NAS 독립 cursor 수집 소스를 추가했다. 실제 공개 응답의 FLASH 2020-01-02는 83페이지에서 전날로 넘어가는 경계까지 확인해 당일 기사 1,232건을 저장했다. WORLD 같은 날짜는 0건, WORLD 2023-01-03 이후 표본은 기사가 있었다. 이는 과거 전체 제공 범위를 확정한 결과가 아니다. 소스 6개는 NAS와 SHA-256 일치로 동기화하고 기존 3개는 `X:\kiwoom-monitor-backups\20260923-naver-market-news-v1`에 보존했다. NAS 실행 이미지 재빌드·장시간 수집, 역사 시황 기사와 종목 후보·연구 입력의 연결은 아직 확인하지 않았다.

같은 날 FLASH/WORLD 과거 수집을 별도 로컬 DB `data/naver_stock_market_news.sqlite3`에서 2019-01-01~2026-09-22 범위로 시작했다. 2019-01-01 FLASH 135건/10페이지, WORLD 빈 응답을 확인했고 첫 불변 SQLite 스냅샷을 NAS `server-data/historical-intelligence/market-news-v1`에 발행했다. 진행 상태는 `data/historical_collection/market-news-state.json`, 과거자료 수집기 모니터, NAS `historical-intelligence/v1/STATUS.md`와 `status.json`에서 함께 확인한다. STATUS 문서는 시황뉴스 날짜 하나가 끝날 때마다 갱신하도록 연결했다. 원천별 전체 완료·연구 입력 연결과 NAS 상시 수집 이미지의 재빌드는 여전히 별도 검증 대상이다.

## 새 방향에서 아직 해야 하는 것

과거자료 운영 DB `data/historical_intelligence.sqlite3`를 만들었다. CREON 삼성전자 연속조회는 1분봉 190,102개(2024-08-29 이후)와 그 이전 5분봉 57,710개(2021-08-11~2024-08-28)를 겹치지 않게 저장했고, 5분봉 시각은 구간 종료시각으로 확인했다. 네이버 날짜 지정 검색은 원응답·기사 관계·원문 URL별 확인 시도와 원문 발행시각을 저장한다. 발행시각 확보, 원문 차단, 기사 없음, 시각 없음, 제목 불일치 등을 별도 상태로 남기며 발행시각을 확인한 기사만 현재 학습 적격으로 표시한다.

NAS의 `stock_aliases` 1,164행을 사용해 94,750개 후보 종목·일의 당시 상호를 선택하고, 이전 대화에서 정한 상호변경일 ±14일에는 구·신 이름을 함께 검색하도록 95,090개 재개 가능 뉴스 작업을 만들었다. 2026-09-22에는 DB와 대신 원응답을 `deploy/synology/server-data/historical-intelligence/v1/runs/20260921T185214Z-04bb4a3f5d43`에 불변 스냅샷으로 게시했고, 로컬 검증 복사본의 SHA-256 일치와 SQLite `integrity_check=ok`를 확인했다. NAS 파일을 네트워크에서 실행 중인 SQLite로 직접 열지 않는다. 다른 후보 종목으로 시세 수집 확대와 뉴스 작업 95,090개의 실제 소진·도달기간 확인은 남아 있다.

장시간 수집 로그에서 뉴스 검색 403/429가 작업 전체를 `pending`으로 되돌려 같은 수십 페이지를 재요청하고, 대신 5분봉은 최신일부터 받은 뒤 1분봉 중첩 구간을 import에서 버리는 병목을 확인했다. 뉴스는 막힌 페이지만 공통 60초 제한 대기 후 재시도하고, 대신은 1분 최과거일 전날까지만 5분봉을 요청하도록 수정했다. 대신 재시작 때 1억 건 이상 봉 전체를 집계하던 초기화도 신규 작업 코드에만 한정했고 실제 기존 원장 초기화가 0.042초에 끝나는 것을 확인했다. 2026-09-23 수정본 재시작 뒤 뉴스 페이지 진행과 대신 `024090`·`024120` 완료를 확인했다. `024090`의 5분 원응답은 47,326개·19페이지이며 최신 봉이 1분 최과거일 전날인 2024-08-29로 끊겼다.

대신 수집기의 `Jobs`는 CREON TR 한도나 전체 종목 상한이 아니라 한 프로세스의 최대 작업 수다. 기존 1,000은 남은 원장보다 작아 모니터와 CLI 상한을 10,000으로 늘렸으며, 대기 작업이 먼저 없어지면 그 시점에 정상 종료한다.

2026-09-23 03:15 KST 기존 1,000건 실행을 정상 중지한 뒤 `requested_jobs=10000`으로 재시작했고, 새 자식 프로세스가 `025880`의 1분봉 다운로드 heartbeat를 갱신하는 것을 확인했다. 변경 파일 6개는 NAS와 SHA-256 일치로 동기화했으며 기존 파일은 `X:\kiwoom-monitor-backups\20260923-031908-daishin-job-limit-v1`에 보존했다.

수집기 성능 수정 파일 8개는 `X:\kiwoom-monitor`와 SHA-256 일치로 동기화했고, 덮어쓴 기존 파일은 `X:\kiwoom-monitor-backups\20260923-023816-collector-progress-v1`에 보존했다. 이 변경은 로컬 장시간 수집기와 문서·테스트이며 NAS 컨테이너 재빌드는 필요하지 않다.

뉴스 추가 계측에서 최근 48개 물리 작업은 평균 24.6페이지·29.9초였고 0.5초 검색 간격의 이론 최소는 12.3초였다. 같은 언론사의 대기 작업이 16개 원문 작업자를 점유하는 경로와 반복 403 강제 대기를 확인해, 언론사별 비점유 대기열과 검색 기본 간격 0.7초를 적용했다. 검색 원응답 200페이지의 전체 키도 검사했지만 원문 초·분 단위 시각을 대체할 숨은 시각 필드는 없었다. 따라서 원문시각 정확성을 낮추는 방식은 사용하지 않는다.

수정본 재시작 직후 첫 8개 물리 작업은 227초 동안 검색 263페이지를 처리해 초당 1.16페이지였고, 수정 전 최근 48개 작업의 초당 0.83페이지보다 약 40% 높았다. 여기에는 100페이지 분할 작업과 66페이지 고밀도 작업이 포함됐고 새 실행 구간의 403은 없었다. 아직 짧은 초기 표본이므로 장시간 처리량 확정값으로 사용하지 않는다. 변경 파일 6개는 NAS와 SHA-256 일치로 동기화했고 기존 파일은 `X:\kiwoom-monitor-backups\20260923-030147-news-throughput-v1`에 보존했다.

테마는 기존 프로필 안에 `alias`, `split_to`, `keep_separate` 결정을 저장한다. 사용자가 테마를 병합하거나 이름을 바꾸면 이전 표현이 대표명으로 해석되고, 분리하면 이후 같은 복합 이름을 가져와도 분리된 테마들로 확장된다. 분리된 두 테마는 별도 결정 전까지 다시 별칭으로 합칠 수 없다. 뉴스 AI는 여러 종목을 묶을 기사 근거가 있을 때만 원시 테마명·확신도·근거를 별도 후보로 저장한다. 테마 관리의 `AI 테마 제안 검토`는 기사 제목·발행시각·원문과 활성 프로필 결정을 함께 보여 주고 승인·거절 전에는 종목 테마를 바꾸지 않는다. 사용자가 적용 이름을 고쳐 승인하면 그 별칭 결정도 프로필에 남아 이후 같은 표현에 재사용된다. 같은 창의 뒤 행에서 이름을 수정하지 않았다면 앞 행에서 확정한 최신 별칭을 저장 시점에 다시 적용해 이전 이름으로 되돌리지 않는다. 후보와 검토 상태·기사 문맥은 프로필 복사, 테마 파일 백업과 NAS 전체 메타데이터 스냅샷에 포함된다. 실제 기사에 대한 제안 품질·의미별 병합 정확도와 과거 사건 일괄 후보 생성은 아직 검증하지 않았다.

외부 자료를 기존 strict TOP20 재생에 섞지 않기 위해 `historical_reconstruction/v1` 입력 계약과 hash 검증 loader를 추가했다. 후보는 `posthoc_candidate_days/v1`, `not_contemporaneous_top20=true`로 고정한다. 이 후보는 장중 선정 자료가 아니라 해당 거래일의 완성된 일봉으로 만든 장마감 후보군이므로 장중 선정시각을 합성하지 않고 export 시각을 실제 `available_at`으로 쓴다. 사후 당일 분석과 후보일 장마감 뒤 다음 거래일 타점 연구를 구분한다. 봉과 뉴스는 실제 수집 관측시각을 유지하며, 종목·일에 1분봉이 있으면 1분만 선택하고 없을 때만 5분을 선택한다. 5분을 1분으로 확장하지 않는다. 첫 최종 로컬 표본 `data/research/historical-reconstruction/2026-09-18-initial-v2`는 후보 50개, 현재 확보된 1분봉 종목 2개, 미확보 48개, 봉 762행, 뉴스 관계 3,497행을 고정했다. 뉴스 관계 중 발행시각 확인 2,056개와 차단 1,295개, 시각 없음 74개 등 제외 상태도 manifest에 따로 집계한다. 이는 입력 연결 표본이며 기존 전략 평가나 학습 완료를 뜻하지 않는다.

학습 입력은 백필 가용 범위에 맞춰 1분·5분·일봉 뷰를 분리한다. 최근 약 2년 실제 1분봉은 정밀 타점, 약 5년 5분봉은 거친 장중 구조, 약 7년 일봉은 시장·테마의 장기 맥락에만 사용한다. `interval_seconds`와 직접 수집/집계 provenance를 입력과 결과에 유지하고 실제 시간 지평별 라벨을 만든다. 현재 복원 계약은 1분/5분 구분과 5분→1분 확장 금지를 구현했으며, 일봉 맥락과 최근 1분의 검증된 5분 집계 뷰 연결은 미구현이다.

거래비용의 현재 사용자 합의 기준은 체결금액별 매수 총비용 0.015%, 매도 총비용 0.215%다. 매도 값은 수수료 0.015%와 매도세 0.200%의 합계이며 같은 금액 왕복은 슬리피지 제외 약 0.230%다. 매매일지 예상비용은 이미 이 값을 쓴다. 연구에는 소수 bp를 문자열로 기록하는 `fixed_bps/v2`를 추가해 수수료 1.5bp를 양쪽에, 매도세 20bp를 매도에 적용한다. 기존 `fixed_bps/v1` 정수 계약은 호환용으로 유지한다. 첫 구조 검증 요청의 1bp 수수료·18bp 매도세는 합의값이 아니므로 성과 근거로 재사용하지 않으며 새 계약으로 입력·실행을 다시 만들어야 한다.

후보일과 결과 구간을 분리한 복원본 `data/research/historical-reconstruction/2026-09-18-through-2026-09-21-v1`도 만들었다. 어댑터는 후보 50개를 `historical_candidate_population`으로 유지하고, 수집 작업이 완료된 000150·005930의 다음 거래일 1분봉 각 381개만 `data/research/historical-strategy-input/2026-09-18-v1`에 넣었다. 연구 clock은 2026-09-21 실제 봉 종료시각을 쓰되 원자료가 실제 확보된 2026-09-22 수집시각은 각 payload의 `source_available_at`에 남긴다. 기존 두 전략 Family의 가격 Factor는 이 입력을 실행할 수 있지만 당시 TOP20 자료가 아니므로 rank persistence Factor는 runner에서 거부한다. 현재 정책을 최종값으로 정하지 않았으므로 이 단계에서는 실제 성과 비교 결과를 만들지 않았다.

2026-09-24 백필 확대 후 같은 세 날짜(2024-12-24, 2025-05-29, 2026-01-14)를 새 로컬 봉으로 다시 동결했다. 원본 후보 150개 중 149개에 선택일 1분봉이 있다. 원본 후보에는 ETF·ETN이 섞여 있으므로 `market_code`를 복원 기록에 보존하고, 새 `--individual-stocks-only` 어댑터로 개별 주식만 연구 모집단에 투영했다. 새 개발 패키지 `data/research/historical-development-inputs/multi-period-2024-2026-stocks-v3`는 OOS를 봉인했고 TRAIN 42/42·VALIDATION 45/45 개별 주식에 결과일 봉이 있다. 연속 1분쌍은 각각 41/42·44/45여서 gate는 아직 `BLOCKED`다. 빠진 `000545`·`019175`의 해당 결과일 봉은 각각 14개이며 시각이 30분 간격이다. 이 표본을 1분 연속 체결 가능 자료로 합성하지 않는다.

2026-09-24 D03 후속: 사용자 결정에 따라 결과일 연속 1분봉 쌍이 없는 후보를 **파생 연구 모집단에서만** 제외하는 옵션을 추가했다. 원본 복원본·CREON 원응답은 유지하고 `multi-period-2024-2026-stocks-pairs-v4` manifest에 TRAIN `000545` 14봉, VALIDATION `019175` 14봉, OOS `012860` 0봉의 제외 사유를 기록했다. OOS를 봉인한 새 개발 입력은 TRAIN 41/41·VALIDATION 44/44의 최소 연속쌍 gate에서 `READY`다. 2024-09~2025-12 후보일 321일을 읽기 전용 감사한 결과 150일이 같은 최소 조건을 충족하고 16개월에 적어도 한 날짜씩 있다. 이는 종일 완전성·대표성·수익성 판정이 아니다. 새 비용 계약의 TRAIN 돌파 구조 실행은 기본 512MB RSS 한도에서 12,361/14,975 평가 뒤 `memory_rss_limit_exceeded`로 끝났고, PC 여유 메모리 확인 후 1GB 한도 실행에서는 평가를 완료했다. 아래 체결시각 오류 때문에 이 결과로 성과를 비교하지 않는다. 원본·운영 DB는 변경하지 않았다.

2026-09-24 D03 범위 확대 사전 감사: 위 321일의 종목·결과일 13,632개 중 봉 없음 또는 연속 60초 봉 쌍 없음 307개만 종목·결과일별로 제외하는 [불변 투영 원장](../data/research/historical-minute-exclusions-2024-09-to-2025-12.json)을 만들었다. 13,325개가 남으며 321일 모두 최소 한 후보가 있다. 150일은 모든 후보를 통과시킨 기존 날짜 단위 gate 수치다. 투영은 원본 감사 SHA-256에 묶였고 가격 성과·OOS는 보지 않았다. 다기간 입력 동결과 종일 봉 완전성 검증은 아직 별도 단계다.

2026-09-24 D03 실행시각 오류 확인: 첫 1GB TRAIN 돌파 실행은 14,975개 평가를 마쳤지만 주문 검열 262건 전부에서 `submitted_at`과 다음 봉의 `bar_start`가 같은 정각이었다. 역사 봉은 해당 분의 **종료**시각에 평가되므로 다음 봉은 바로 그 정각에 시작한다. 기존 체결 검증은 정각에도 무조건 1분을 더해 `next_tradable_bar_gap`으로 잘못 검열했다. KRX VI 원장과 겹친 건은 2/262였으며 VI 여부와 관계없이 262건 모두 경계 계산 오류에 해당한다. 정각은 같은 분 시작을 허용하고 초가 있으면 다음 분 시작을 요구하도록 고쳤다. 경계시각 체결·실제 다음 봉 공백을 포함한 관련 검사 33건 통과. 기존 `multi-period-2024-2026-stocks-pairs-v4-1g` 결과는 전략 비교에 쓰지 않으며, 새 `...-v4-1g-clockfix` DB에서 재검증한다. 수정 후 첫 TRAIN 돌파 실행은 14,975개 평가를 마쳤고 봉 공백 검열은 1건으로 줄었다. 이 1건(`069140` 2024-12-26 13:40 주문)은 다음 봉이 13:41에 시작해 13:40 봉이 없고 KRX VI 발동 이력도 없다.

2026-09-24 D03 네 고정 실행 완료: `data/research/historical-development-results/multi-period-2024-2026-stocks-pairs-v4-1g-clockfix/SUMMARY.md`에 TRAIN·VALIDATION의 돌파·눌림 네 실행을 보존했다. 각 TRAIN 14,975개, VALIDATION 16,539개 평가를 마쳤고 네 보고서 모두 `ELIGIBLE`·자료 품질 `PASS`다. OOS 입력·결과는 사용하지 않았고 수수료 1.5bp/매도세금 20bp/추정 슬리피지 5bp를 동일하게 적용했다. 비교표의 `In-fold censored`는 평가 창 안의 수치이고 아래 run-wide 사유에는 연구 구간 종료 검열도 포함하므로 둘을 혼동하지 않는다. 각 분할이 하루뿐이고 현재 시장 순위·VI 주문 가능성도 완전 복원되지 않아 손익을 전략 선택 근거로 쓰지 않는다. 더 넓은 월별 자료·봉 완전성·대표성 검증이 다음 단계다. 관련 연구 경계 회귀 45건은 통과했지만, 전체 핵심 회귀 묶음은 이 실행기의 `PySide6`·`fastapi`·`websockets` 미설치로 25개 로드 오류가 났고 변경 범위 밖 기존 스키마 검사 1건은 마이그레이션 19개 기대/실제 20개로 실패해 통과로 표시하지 않는다.

2026-09-24 D03 월별 구조 입력: 가격 성과를 보지 않고 월별 첫 후보일 16개(2024-09~2025-12)를 고정한 [선택 원장](../data/research/historical-monthly-case-selection-2024-09-to-2026-01.json)에서 불변 복원 632,428개 원천 관측과 개별 주식 파생 입력 259,968개 관측을 생성했다. 개발 구간의 종목·일 657개와 제외 15개는 사전 원장과 일치한다. 12개월 TRAIN·4개월 VALIDATION·기존 2026-01-14 봉인 OOS 분할을 동결하고 [개발 입력](../data/research/historical-development-inputs/monthly-2024-09-to-2026-01-v1/manifest.json)에 TRAIN 181,708개·VALIDATION 63,218개 관측만 분리했다(`oos_included=false`). 준비도 검사가 이전에는 마지막 후보군만 보던 오류와 VALIDATION의 전 분할 seed를 후보로 오인한 오류를 수정한 뒤, 날짜·종목별 연속 1분쌍 gate에서 TRAIN 488/488·VALIDATION 169/169 `READY`를 확인했다. 이 gate는 최소 쌍만 확인하며 종일 봉 완전성, 당시 뉴스 가용성, 사후 후보의 대표성이나 수익성을 보증하지 않는다. 뉴스 백필 진행 중 만들어진 구조 스냅샷이므로 최종 비교 입력으로 승인하지 않았다.

같은 동결 월별 사례 657종목·결과일의 [1분봉 종일 품질 감사](../data/research/historical-monthly-selected-bar-quality-20260924.json)에서 300봉 미만 22건, 봉 사이 10분 초과 간격 15건을 기록했다. 이 문턱은 진단용이며 VI·거래 부재·공급자 공백을 봉 개수만으로 구분하거나 사례를 자동 제외하지 않는다. [10분 초과 구간의 KRX VI 대조](../data/research/historical-monthly-selected-gap-vi-check-20260924.json)에서는 15종목·일의 26구간 중 1구간만 VI 발동 구간과 겹쳤다. 이 겹침도 12분 공백 전체 원인으로 입증된 것은 아니다. [독립 일봉·분봉 근거 대조](../data/research/historical-monthly-selected-gap-evidence-v2-20260924.json)에서 `007815` 8구간은 CREON 1분봉 거래량 합계와 키움 일봉 거래량이 일치했고, 2025-12-02 두 구간은 키움 1분봉에도 사이 거래 봉이 없었다. 나머지 15구간은 VI·키움 분봉으로 직접 설명되지 않았다. [CREON 원응답 대조](../data/research/historical-monthly-selected-gap-raw-20260924.json)에서 15종목·일 모두 원응답의 1분봉 시각·거래량과 SQLite 저장값이 일치했고 26구간 내부에 원응답 봉도 0개였다. 따라서 이번 공백은 저장 누락이 아니지만, CREON 응답의 공백이 모두 무거래 때문인지까지 입증하지는 못한다. OOS는 감사에서 제외했다. 거래소 공시 원문 효력일은 별도 [접수일·일봉 대조 보고서](../data/research/candidate-exchange-effective-dates-reviewed-20260924.json)에 3,083건 보존했고 접수일과 효력일이 다른 사건은 1,562건이다. 2025년 상폐일 이후 동일 코드 거래 1건과 효력일 이후 접수된 정지 공시 1건은 검토 표시를 붙였다. 월별 개발 입력에는 [TRAIN·VALIDATION 동반 자료](../data/research/historical-monthly-exchange-case-context-20260924.json)로 해당 사건 6건을 연결했으며 OOS를 열거나 장중 매매 신호에 자동 투입하지 않았다.

2026-09-24 D03 월별 실행 자원 진단: OOS 없는 16사례 개발 입력의 TRAIN 돌파 실행은 아직 완료되지 않았다. 최초 2GB RSS 한도는 초과했고, 4GB 한도 실행도 PC 여유 메모리가 약 2.2GB로 내려간 시점에 중단했다. 중단한 독립 연구 DB는 `cancelled`로 표시했으며 운영 DB와 원본 입력은 변경하지 않았다. 동일 하루 TRAIN 표본의 2,168건 프로파일에서 126.7초 중 SQLite 트랜잭션 종료가 95.5초였으므로, 판단을 최대 128건씩 한 트랜잭션으로 저장하고 참조하는 주문·후보 사건 전에는 즉시 flush하도록 수정했다. 이후 2,806건 프로파일은 47.9초, SQLite 종료 7.5초였다. 시점 재생은 해당 종목·당일 봉만 Factor에 전달하도록 바꾸고, 논리 결과 해시는 목록 누적 없이 순차 계산한다. 하루 14,975건 완료 실행의 후보 33·제출 67·체결 65·종료 32·순손익 -7,198원과 무거래 사유는 변경 전후 일치했다. 월별 마지막 진단은 판단 71,911건까지 진행했으나 완결 결과가 아니며, 당시 4GB 초과 실행이나 성과 비교로 승격하지 않았다.

이어진 RSS 진단에서 Windows `current_rss_bytes()`가 호출마다 `ctypes` 구조체 타입과 `WinDLL` 객체를 생성하는 것이 메모리 증가의 확인된 원인이었다. 함수만 2만 회 호출한 독립 재현은 29→174MB, 네이티브 바인딩을 1회 캐시한 뒤에는 29→30MB였다. 동일 동결 월별 TRAIN 입력에서 로드 직후 775MB, 첫 판단 전 2,149MB, 1,037판단 후 2,170MB였던 RSS가 수정 후 각각 765MB, 769MB, 775MB로 줄었다. 3만 건의 수정 전 진단 실행과 1천 건의 수정 후 진단 실행은 별도 DB에서 의도적으로 `cancelled`로 종료했고 OOS·운영 DB는 건드리지 않았다. 2GB 한도의 새 독립 월별 네 요청을 준비했으며 전체 완료·성과 적합성은 각 실행과 보고서 검증 뒤에만 판정한다.

2026-09-25 D03 월별 구조 실행 완료: [네 실행 요약](../data/research/historical-development-results/monthly-2024-09-to-2025-12-v2/SUMMARY.md)의 TRAIN 돌파·눌림 각 181,696판단, VALIDATION 돌파·눌림 각 63,213판단이 새 독립 연구 DB에서 2GB RSS 한도 내에 완료됐다. 네 보고서는 모두 `ELIGIBLE`·자료 품질 `PASS`이고 DB의 네 run도 `completed`, 판단 총 489,818건, SQLite `PRAGMA quick_check=ok`이다. VALIDATION 돌파의 `032860` 2025-09-02 11:47 주문 1건은 `next_tradable_bar_gap`으로 검열돼 체결로 가정하지 않았다. 기간 종료 전 미체결 주문도 run-wide 검열 원장에 별도로 남는다. OOS는 입력과 요약에서 제외됐고 원본·운영 DB는 변경하지 않았다. 월별 첫 후보일 16개만 사용했고 당시 실시간 TOP20·VI 주문 가능성·부분 체결은 복원하지 못했으며 뉴스 백필도 진행 중이므로, 이 네 실행의 손익으로 전략을 선택하거나 기각하지 않는다.

2026-09-25 D03 선택일 범위 감사: [읽기 전용 보고서](../data/research/historical-monthly-selection-coverage-20260925.json)는 기존 준비도 감사·제외 투영·동결 선택 원장의 SHA-256 연결을 확인하고 가격 결과나 OOS를 읽지 않았다. 월별 첫 후보일 16개는 개발 후보일 321개 중 4.98%, 파생 후보 종목·일 657개는 전체 13,325개 중 4.93%다. 선택일의 제외 후 후보 수 중앙값은 41개, 나머지 305일은 42개이고, 1분으로 표기된 봉 수/원 후보 수의 일별 중앙값은 각각 367.1개와 364.5개다. 이 두 지표는 큰 수량 차이를 보이지 않지만 월초 날짜 고정의 시장 국면 편향, 종일 봉 품질, 당시 순위·뉴스 가용성을 검증하지 못한다. 표본 확대나 전략 성과 승격의 근거로 단정하지 않는다.

같은 날 거래소 원문 수집 4,220건(효력일 추출 2,692·명시 날짜 없음 1,523·공식 원문 제공 불가 5)을 완료했다. [OpenDART 원문 ZIP API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)에서 받은 ZIP 4,215개 SHA-256과 로컬 DB 무결성을 확인했다. NAS 시장 맥락 새 불변 run `20260924T130303Z-2b6cc96dd69a`에 DB와 원응답 18,449개를 게시했고, NAS DB SHA-256 `2b6cc96dd69a08658d212091afedd85b4e21a261f93a0aee7a723774918f3f90`·manifest·`latest.json` 일치와 원응답 누락 0개를 재검증했다. 기존 run은 유지한다. 기존 공급 불가 6건과 원문 제공 불가 5건은 `coverage_complete=false`로 공개하며 완전한 거래소 효력일 원장이라고 표시하지 않는다.

다기간 어댑터는 여러 후보일을 한 동결 입력에 넣되 각 사례의 결과를 후보 DB에서 확인한 바로 다음 거래일까지로 제한한다. 이 경계로 멀리 떨어진 후보일 사이의 봉을 한 사례에 계속 포함하거나 같은 결과 봉을 여러 사례 성과로 중복시키지 않는다. 첫 다기간 표본 `multi-period-2024-2026-v1`은 2024-12-24→12-26, 2025-05-29→05-30, 2026-01-14→01-15 세 사례와 연구 관측 7,567개를 포함하며, 각 원천·파생 manifest와 파일 hash를 일반 권한 로더로 재검증했다. 수집 진행 중 만든 불변 표본이므로 전체 후보 일반화나 시간순 TRAIN/VALIDATION/OOS 성과 완료를 뜻하지 않는다.

같은 표본의 `historical_chronological_split_plan/v1`은 사례를 통째로 시간순 배정해 2024 사례를 TRAIN, 2025 사례를 VALIDATION, 2026 사례를 OOS로 고정했다. 계획은 파생 데이터셋 ID와 revision hash에 결합되고 각 fold의 마지막 15:30 봉을 포함하는 미포함 종료 경계를 사용한다. OOS 상태는 `SEALED`, 결과 포함은 false이며 이 단계에서는 전략 실행·성과 조회·후보 선택을 하지 않았다.

분할 계획에서 `historical_development_inputs/v1` TRAIN·VALIDATION 입력만 별도 동결했다. TRAIN은 후보군 1개와 1분봉 2,239개, VALIDATION은 직전 후보군 seed와 새 후보군 2개 및 1분봉 2,661개다. 두 입력 모두 원본 역사 모집단 provenance와 source availability 제한을 유지하고 품질 판정 `PASS`·revision 재현성 `VERIFIED`를 확인했다. 패키지는 OOS 입력·관측·결과를 포함하지 않으며 아직 실제 전략 성과 비교를 실행하지 않았다.

이 개발 입력으로 기존 돌파·눌림 재가속 Family의 고정 TRAIN/VALIDATION 구조 실행도 완료했다. 투영 입력의 원천 `historical_reconstruction_strategy/v1` 표시를 runner가 함께 읽고 재생 커서가 `historical_candidate_population`을 소비하도록 보완했으며, 순위 지속성 Factor 차단도 투영 뒤 유지된다. 결과는 `data/research/historical-development-results/multi-period-2024-2026-v1`에 있다. TRAIN 돌파/눌림은 각각 후보 109/3건이었지만 전부 `next_tradable_bar_gap`으로 검열됐고, VALIDATION은 돌파 102건 중 1건만 체결·종료(-28원), 눌림 3건은 전부 검열됐다. 이는 수집 진행 중인 희소 표본의 구조 점검 결과이며 전략 우열·수익성 근거가 아니다. 파라미터는 잠정값, 비용은 브로커 미검증 개발 추정값이고 OOS는 계속 봉인 상태다.

같은 개발 입력의 재실행 준비도 검사를 추가했다. 현재 TRAIN은 후보 50개 중 분봉 7개·연속 1분쌍 6개, VALIDATION은 분봉과 연속쌍 모두 7개라 `BLOCKED`다. 기본 요청 생성은 후보 전부가 분봉과 최소 한 연속 1분쌍을 가질 때만 허용하며, 희소 입력의 구조 점검은 `--allow-partial`을 명시해야 한다. 후보 원장에는 `00499K` 같은 영문 포함 6자리 단축코드가 91개 있었고 숫자 전용 필터 때문에 대신 작업에서 빠진 사실도 확인했다. CREON bridge와 작업 생성을 영문 포함 6자리로 확장하고 현재 원장에 91개를 증분 등록했다.

LLM 학습과 단순 추론을 구분하기 위해 `historical_learning_cases/v1` 준비 계약을 추가했다. 사후 후보 선정, 복원 뉴스 제목·검색 요약, 아직 생성하지 않은 AI 해석, 다음 거래일 결과 라벨을 분리하며 strict 시점 재생이나 모델 학습 완료로 표시하지 않는다. 최종 첫 불변 표본 `data/research/historical-learning-cases/2026-09-18-v2`는 50사례, 뉴스 근거가 있는 사례 22개, 결과 봉이 있는 사례 2개다. 결과 없는 48사례와 차단·시각 미확인·후보일 뒤 발행 관계를 그대로 제외 원장에 남겼다. 현재 `semantic_relevance_reviewed=false`, `ai_interpretation_generated=false`, `model_weight_training_ready=false`다. 같은 데이터셋을 NAS `server-data/historical-intelligence/v1/learning-cases/historical-learning-cases-548aae706fe48133557c3ed398cfb1985fa5a80c1212b2daa623113ada877290`에 불변 게시했고 `latest.json`을 별도 갱신했으며 두 파일의 SHA-256 일치를 확인했다.
이 사례에서 `historical_news_review_queue/v1` 대기열도 만들었다. 2,035개 종목-기사 관계를 공급자·언론사·기사 식별자로 합쳐 고유 기사 1,422개로 줄였고, 종목별 기존 비AI 판정으로 우선 검토 105개를 표시했다. 규칙 판정은 정답이 아니며 전체 항목은 `pending`, `human_review_complete=false`, `llm_used=false`, `model_weight_training_ready=false`다. 로컬 불변 산출물은 `data/research/historical-news-review-queues/2026-09-18-v1`에 있다. 같은 파일을 NAS `server-data/historical-intelligence/v1/news-review-queues/historical-news-review-queue-578f89c206d709720184aa753649dc3840f949a066819b476fc27ce576311805`에 불변 게시하고 `latest.json`을 갱신했으며 두 파일의 SHA-256 일치를 확인했다.

`historical_news_review_workflow.py`로 우선 검토 105행을 `data/research/historical-news-review-work/2026-09-18-priority-v1.csv`에 내보냈다. 이 CSV는 편집 작업본이며 정답 원장이 아니다. 가져오기는 원본 대기열 ID와 파일 hash를 확인하고 관련 판정의 사건 ID, 테마 사용 시 프로필 이름, 검토자와 timezone 포함 검토시각을 요구한다. 검증된 행만 `historical_news_review_decisions/v1` 불변 결과가 되며 그 결과도 `model_weight_training_ready=false`다. 현재 입력된 사람 판정은 0건이다. 워크플로 소스는 `X:\kiwoom-monitor-backups\20260922-213000-human-news-review-workflow-v1` 백업 뒤 NAS와 동기화했고 추적 파일 966개의 SHA-256 불일치는 0개다. 오프라인 연구 CLI이므로 컨테이너 재빌드는 필요하지 않다.

같은 작업표는 앱의 전략 연구 창에서 `과거 뉴스 검토`로 연다. 화면은 최신 불변 대기열에 결합된 작업표만 읽고, 관련·무관·보류, 사건 ID, 기존 테마 프로필 이름과 테마명, 근거 메모를 행 단위로 원자 저장한다. `검토 결과 동결` 전 작업표는 계속 비권위 편집본이며 현재 사람 판정 0건 상태는 바뀌지 않았다.

과거 뉴스 검토 화면까지 포함한 추적 파일 968개는 NAS 프로젝트와 SHA-256 불일치 0개로 동기화했다. 기존 NAS 파일 966개는 `X:\kiwoom-monitor-backups\20260923-001448-historical-news-review-ui-v1`에 보존했고 운영 `.env`의 SHA-256은 전후 동일하다. `postgres-data`, `server-data`, `server-secrets`는 복사 대상에 포함하지 않았다. 서버 동작은 바뀌지 않아 컨테이너 재빌드는 필요하지 않다.

사람 판정 뒤의 사건 누수를 막는 `historical_news_event_split/v1` 계약도 구현했다. 관련 기사만 `canonical_event_id` 단위로 묶고 사건의 최초 발행시각을 timezone-aware 값으로 비교해 TRAIN·VALIDATION·봉인 OOS에 통째로 배정한다. 결정 산출물은 원본 대기열의 기사 발행시각·정밀도·시각 출처를 보존한다. 현재 사람 판정이 0건이므로 실제 분할 산출물은 만들지 않았고 모델 학습 준비 상태도 계속 false다.

사건 분할 코드까지 포함한 추적 파일 971개는 NAS 프로젝트와 SHA-256 불일치 0개로 동기화했다. 기존 NAS 파일 968개는 `X:\kiwoom-monitor-backups\20260923-003116-historical-news-event-split-v1`에 보존했다. 운영 `.env` 해시는 이전 확인값과 같고 `postgres-data`, `server-data`, `server-secrets`는 복사 대상에서 제외했다.

전략 연구의 과거 뉴스 검토 화면에서 `사건 분할 계획`을 직접 실행할 수 있다. 최신 불변 판정에 관련 사건이 최소 3개 있어야 하며 사용자가 TRAIN·VALIDATION 사건 수를 고르면 나머지 최소 1개를 OOS로 봉인한다. 현재 불변 판정 결과가 0개라 실제 계획은 생성하지 않았다.

앱 사건 분할 실행까지 포함한 추적 파일 971개는 NAS와 SHA-256 불일치 0개로 다시 동기화했다. 직전 NAS 파일 971개는 `X:\kiwoom-monitor-backups\20260923-004208-news-event-split-ui-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

사건 분할 뒤 RAG·미세조정 비교가 공통으로 사용할 `historical_news_development_inputs/v1`도 구현했다. 이 계약은 TRAIN·VALIDATION에 배정된 관련 기사만 제목·검색 요약·발행시각 입력과 사람 확정 사건·테마 target으로 투영하고 OOS payload를 완전히 제외한다. 파일 hash, 결정 dataset ID, 분할 plan ID, 구간 간 사건 중복을 로드 때 다시 확인한다. 현재 판정과 분할이 0건이므로 실제 개발 입력은 만들지 않았다.

과거 뉴스 검토 화면의 `개발 입력 생성`은 최신 불변 사람 판정과 정확히 일치하는 가장 최근 사건 분할을 찾아 위 공통 입력을 불변 저장한다. 같은 산출물은 덮어쓰지 않고, 일치하는 분할이 없으면 먼저 사건 분할을 만들도록 안내한다.

앱 개발 입력 생성까지 포함한 추적 파일 974개는 NAS와 SHA-256 불일치 0개로 동기화했다. 직전 NAS 파일 974개는 `X:\kiwoom-monitor-backups\20260923-010929-news-development-inputs-ui-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

RAG·미세조정 실행기에 사람 정답이 든 `validation.jsonl`을 직접 전달하지 않도록 `historical_news_blind_validation/v1`도 구현했다. 이 계약은 VALIDATION의 `sample_id`와 `model_input`만 복사하고 관련성 target, canonical event ID, 테마 프로필·테마명을 재귀 검증으로 차단한다. 원본 개발 dataset ID와 validation 파일 hash에 결합한 불변 요청이므로 두 방법이 같은 입력을 받았는지 이후 확인할 수 있다. 현재 실제 개발 입력이 없어 실제 블라인드 요청도 만들지 않았다.

블라인드 VALIDATION 계약까지 포함한 추적 파일 977개는 NAS와 SHA-256 불일치 0개로 동기화했다. 직전 NAS 파일 974개는 `X:\kiwoom-monitor-backups\20260923-012141-news-blind-validation-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

블라인드 요청의 외부 예측을 불변 저장하는 `historical_news_method_results/v1`과 평가기 전용 `historical_news_method_evaluation/v1`도 구현했다. 결과 계약은 prompt baseline·RAG·미세조정 방법 신원과 모든 요청의 일대일 응답을 묶고, 채점기는 사건 ID 문자열 대신 pairwise 동일 사건 군집, 정규화 테마 집합, 테마 프로필 정확도와 기권 포함 coverage를 계산한다. 현재 개발 입력은 관련 기사만 있으므로 관련성 분류 정확도는 지원하지 않고, OOS 접근과 자동 모델 승격은 false다. 실제 입력과 방법 실행 결과가 없어 실제 점수 보고서는 만들지 않았다.

방법 결과·채점 계약까지 포함한 추적 파일 982개는 NAS와 SHA-256 불일치 0개로 동기화했다. 직전 NAS 파일 977개는 `X:\kiwoom-monitor-backups\20260923-014830-news-method-evaluation-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

공통 개발 입력 계약까지 포함한 추적 파일 974개는 NAS와 SHA-256 불일치 0개로 동기화했다. 직전 NAS 파일 968개는 `X:\kiwoom-monitor-backups\20260923-005948-historical-news-development-inputs-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

수집 진행률은 NAS `deploy/synology/server-data/historical-intelligence/v1/STATUS.md`와 `status.json`에 게시한다. 발행시각이 검증된 과거 기사는 기존 인증된 `news_article` content 경로로 10작업마다 증분 전송한다. 운영 중 100작업마다 게시하는 상태는 작업 원장만 빠르게 집계하고, 17GB대 기사·원문시도 전체 집계는 최종 스냅샷 때 수행한다. 과거 뉴스 검색은 요청 시작 간격 0.5초를 유지하는 4개 작업자로 페이지 응답 대기를 겹친다. 검색과 원문 확인도 파이프라인으로 겹치고, 언론사 원문은 같은 도메인에 한 요청만 허용한 16개 작업자로 병렬 처리한다. 밀도가 50% 이상인 같은 종목·검색어·월의 대기 일자는 날짜 범위 검색으로 묶는다. 완료 표본의 일평균 페이지 수로 한 묶음을 예상 80페이지 이하로 제한하고, 표본이 없으면 일 2페이지로 계산한다. 평균 50페이지 이상인 조합은 일별 작업을 유지하며, 실제 100페이지에 도달한 범위는 날짜를 나눠 재개한다. 범위 결과의 검색 목록 날짜 또는 확인된 원문 `published_at`으로 기존 일별 원장에 다시 귀속하고 날짜를 확인할 수 없는 결과는 임의 날짜에 넣지 않는다. 네이버 검색이 HTTP 403/429를 반환하면 공유 limiter가 모든 검색 작업자의 다음 요청을 지연하고, 현재 작업은 재개 가능한 상태로 둔다. 기본 대기값은 60초이며, 반복 제한 뒤 실행기 수준 재시도도 같은 설정값을 사용한다. 2026-10-02의 30초 시험은 같은 페이지에서 30초·60초 재시도가 403을 반환한 뒤 90초째 성공해 403 재요청만 늘렸다. 45초 시험은 한 동시 요청 구간에서 45초 뒤 다른 페이지가 403을 반환하고 90초째 성공했으며, 뒤이은 단독 403 표본은 45초 뒤 성공했다. 표본에 따라 달라 45초가 기존 60초보다 안정적으로 빠르다고 확정할 수 없어 기본값을 60초로 복원했다. NAS 운영 DB에서 `naver_historical_web`/`historical_backfill` revision으로 저장된 뒤 기존 BODY 작업기가 원문을 처리한다. 별도 역사 SQLite 스냅샷 자체를 운영 뉴스 DB로 열거나 덮어쓰지 않는다.

2026-09-27 과거 뉴스 기사 확인 대기 조사: `probe_historical_backfill.py`가 마지막 페이지 뒤 남은 기사 fetch 결과를 `list(article_pool.drain(wait=True))`로 모두 모은 다음에야 SQLite 저장과 heartbeat를 수행하는 wait-all 경계를 확인했다. 따라서 잔여 원문 확인이 진행 중이어도 최대 11분 이상 화면상 진행이 멈춰 보이고 완료 결과가 일괄 저장될 수 있었다. 로컬 수정은 완료 결과를 25건씩 저장·heartbeat하고 15초마다 활성/대기 수, 최장 요청 시간·호스트를 기록한다. 모니터는 마지막 실제 기사 진행이 3분 이상 없으면 원문 응답 지연으로 표시한다. 원문 본문이 조금씩 계속 도착하는 경우의 무제한 read를 막도록 연결 후 전체 읽기에 45초 deadline을 추가했다. 수정 코드가 읽힌 다음 작업에서는 heartbeat의 기사 완료가 65초 동안 692→834로 늘었고, 이후 남은 대기가 활성 1·대기 72·최장 호스트 `www.nocutnews.co.kr` 4.8초로 드러났다. 이는 같은 도메인에 한 요청만 허용하는 직렬화 큐가 긴 대기의 실제 요인이 될 수 있음을 보여주지만, 앞선 035420 작업의 마지막 254건도 같은 호스트였다고 소급 확정할 수는 없다. OS DNS 이름 해석은 urllib `urlopen` 진입 전에 실행되어 45초 deadline에 포함되지 않는다. 현재 로컬 수집기에 수정 코드가 적용됐으나 NAS 배포는 해당 없음.

로컬 수집기 생존 여부는 프로젝트 루트의 `수집기_모니터.cmd`를 실행해 확인하고 정지한 수집기를 다시 시작할 수 있다. CLI 확인은 `scripts/show_historical_collectors.ps1`을 유지한다. 장시간 단일 종목은 종목·페이지·기사 또는 분봉 단계 heartbeat가 계속 갱신되면 정상 처리 중이다. 뉴스와 대신 수집기는 공용 SQLite writer 충돌을 기다린 뒤 저장하며, 대신 시작 시 대형 분봉 집계 동안 writer transaction을 유지하지 않는다. 네트워크를 쓸 수 없는 실행 세션은 작업 시도를 소진하지 않고 즉시 종료하며, 영문 포함 6자리 단축코드도 뉴스 수집 대상으로 허용한다.

기존 후보 DB는 읽기 전용으로 확인했다. 94,750개 후보 종목·일, 5,910,806개 일봉, 4,562,200개 분봉이 있고 분봉 작업 완료 중 81,901건은 0행이었다. 실제 확보 범위와 다음 작업은 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 적었다.

2026-09-23 바탕화면 원본 DB가 다시 보인 뒤 수집 코드와 실제 행을 재검증했다. 기존 5,910,806개 종목 일봉은 `ka10081`의 `upd_stkpc_tp=1`로 받은 수정주가 계열이며 카카오 2021년 5대1 분할 전 가격도 보정되어 있어 일봉 재수집은 필요하지 않다. `adjust_type`·`adjust_rate`는 전 행 `NULL`이다. 공식 `ka10081` 응답 계약에 사건 종류·조정비율이 없는데 과거 수집기가 응답의 `upd_stkpc_tp`·`upd_rt`를 읽으려 한 것이 직접 원인이다. `upd_stkpc_tp`는 실제로 요청 옵션이며 영웅문 차트 사건 표시는 별도 자료다. 대신 CREON `StockChart`의 필드 18·19에서 수정주가일자·비율만 경량 보완할 수 있으나 사건 종류는 별도 원천이 필요하다. 명시적 기업행동 사건 이력을 이미 확보했다고 표시하지 않는다. 초기 학습은 수정주가 일봉으로 장기·거래일 사이 특징을 만들고 대신 원주가 분봉은 실제 장중 가격 재현에 사용한다.

2026-09-23 D07 실수집에서는 CREON KOSPI·KOSDAQ 1분봉 각 194,880행(2024-08-30~2026-09-22), 5분봉 각 58,464행(2021-08-12~2024-08-29), 일봉 KOSPI 12,401행/KOSDAQ 7,420행을 별도 `data/historical_market_context.sqlite3`에 저장했다. 사용자 KRX CSV 8개의 VI 발동 476,626건(2019-01-02~2026-09-22)도 같은 연구용 DB에 중복 제거해 보존했다. 현재 종목 수집 범위는 아래의 개별 주식 2,637코드다. 시장 맥락 DB와 원응답 9,003개는 `server-data/historical-market-context/v1/runs/20260923T122721Z-412b1d67a41a`에 불변 게시했고 `latest.json`을 원자적으로 갱신했다. NAS DB의 SHA-256 `412b1d67a41a8816123e7df30cc9a687e5f3391d95a27297731f0c5aaefa3206`이 manifest와 일치했다. 공급자가 거부한 세 종목의 시총/주식수·수정주가 작업 각 3건은 `unavailable`이며 manifest의 `coverage_complete=false`에 남았다. 기존 NAS 실시간 DB·`dstr_stk` 스키마는 변경하지 않았다.

같은 날 원본 주도후보 고유 코드 2,945개로 기업행동 보완 원장을 만들고 비주식 308개는 수집 범위에서 제외했다. CREON 필드 18·19의 인접 거래일 수정계수 전환 3,097건을 OpenDART의 해당 기업 공시로 분류했다. 공시 조회창은 발생일 -180일~+14일이며 단일 유형 근거만 자동 확정하고 복수 유형은 수동검토, 무근거는 미일치로 보존한다. 분류 3,097건과 DART 원문 목록을 시장 맥락 불변 게시본에 함께 보존했다. 3,097건 중 개별 주식 사건은 2,106건이며, 비주식 사건 991건은 연구 입력에서 분리해야 한다.
2026-09-23 최신 CREON 범위와 재조회: 원본 후보 2,945코드 중 코스피·코스닥 개별 주식 2,637개만 종목 분봉·시총/주식수·수정주가 작업 대상으로 유지한다. ETF 184·ETN 119·리츠 5개는 `excluded`이고 기존 원응답·저장행은 보존했다. 기존 실패 139개를 관리자 CREON에서 각각 재요청한 결과 ETN 119개는 주식용 `A` 접두사 오류, 주식 3개(`008290`, `046070`, `082660`)는 현재 CREON 종목코드 조회에도 없는 거부, 주식 17개는 1분봉 0건으로 확인됐다. 주식 기준 분봉 완료 2,617/2,637·실패 20, 1분봉 보유 2,617·과거 5분봉 보유 2,448이다. 이전 표적 5분봉 재조회에서는 17종목의 661,048행을 로컬 DB에 추가했다. 시총/주식수와 수정주가 원장은 각각 2,634/2,637 완료·3개 공급 불가다. 기존 DART 사건 3,097건 중 개별 주식 사건은 2,106건(유형 확정 987·수동 검토 980·미매칭 139), 비주식 사건은 991건이므로 학습 입력에서 시장 분류 필터가 필요하다. 새 범위·재조회 결과는 로컬 DB에 반영됐으며 새 역사 DB NAS 불변 스냅샷은 게시하지 않았다.

## 근거를 찾는 위치

[현재 아키텍처](../ARCHITECTURE_CURRENT.md), [모듈 지도](../MODULE_MAP.md), [API 계약](../API_CONTRACT.md), [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md)을 먼저 읽는다. 단계별 완료·테스트 수치는 [아카이브 문서 목록](archive/2026-09-22/README.md)에서 확인한다. 과거 테스트 수치를 이번 작업의 실행 결과로 인용하지 않는다.
2026-10-06 과거자료 수집기 모니터의 반복 전수 집계를 제거했다. 기존 진행률 baseline은 네 PC SQLite DB에서 각각 한 번 초기화했고, 수집기 저장·상태 전환과 같은 SQLite transaction 안에서 trigger가 카운터를 증감한다. 이후 모니터는 3초마다 작은 summary만 읽는다. 현재 초기값: 검색 작업 complete 39,957 / search_complete 9,821 / truncated 996 / pending 33,611 / excluded 10,703; 원문 대기열 complete 4,890,436 / pending 88,611 / running 100; 종목 BODY/RULE ready 2,623,350 (fulltext 1,964,781, summary_only 658,569), failed 720; 시황 BODY/RULE ready 4,837,242 (fulltext 4,639,477, summary_only 197,765), failed 712. 시황 날짜 원장은 complete 2,084 / complete_boundary 2,821 / empty 739, 예상 5,644일 전부 집계됨(pending 0). SQLite 증분 helper/모니터 회귀 11건 통과. 모니터를 다시 띄운 뒤 진행률이 실제 증가하고 SSD 읽기 부하가 내려가는지 운영 확인은 남아 있다.

2026-10-07 O12 recorded collector replay 후속: 활성 NAS source `2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f` 기준 비활성 후보 `2026.10.07-trace-market-inputs-v4-7b0554bc26d50689`를 게시했다(892 files, `active_changed=false`). v4 build 및 `collector-input/v2` import를 확인했고 후보 collector/replay unit 35건이 통과했다. sealed replay DB 검사기에는 capture 보존 4건과 recorded execution 3건을 한 묶음으로 실행하는 `--recorded-capture-gates`를 추가했다. PC에는 전용 replay DB 설정이 없어서 PostgreSQL 7건과 controlled capture OFF/ON 비용 비교는 미실행이다. 후보는 활성화하지 않았으며 NAS 운영 DB·capture 예약·diagnostic control도 변경하지 않았다. 65분 실제 capture와 전체 workload coverage는 여전히 검증 전이다.

2026-10-07 TOP20 원인 입력 후보(로컬): 기본 OFF인 top20_inputs 옵션은 ka00198 qry_tp=5 회차 시각·시도 응답/오류·cache flag를 schema 3 market-input 이벤트로 기록한다. 기존 store/collector-only trace는 schema 2를 유지한다. offline 경로는 운영 코드의 freshness/20-slot/retry 선택만 실행하고 ranking_validation_only를 표시한다. DB·subscriber·fundamentals·index descendants는 실행하지 않으며 source-state 등가나 timing 보존을 주장하지 않는다. schema 3 window reader는 경계에서 분리된 회차 ID를 보고한다. 이 로컬 후보는 NAS에 stage/deploy되지 않았고 10월 8일 capture 대상이 아니다. 관련 regression 묶음 94건 및 34건은 이전 turn 기록에서 통과했으며 이번 turn에서는 테스트를 실행하지 않았다. 후속 작업은 TOP20 fan-out 입력, warm initial state, 전체 descendants 실행/제외 계약이다.
2026-10-07 O12 다음 장중 녹화 사전 확인(읽기 전용): NAS `source-runtime/active.json`은
`2026.10.07-trace-market-inputs-v6-2597a00f99c13b50`를 가리킨다. 이는 활성 소스 포인터
확인이며 내일 시작 시점의 `/health`·인증 capabilities 확인을 대신하지 않는다. 10월 8일
08:55~10:00 KST 녹화와 10월 9일 02:30 KST 사후 검증 예약이 ACTIVE다. 시작 예약은 v6,
schema 2, 0B·0w·0J·0U 지원, 4GiB/100만 event/지연 저장 능력 및 진단 idle을 확인한 뒤에만
capture를 켜고 20:10 KST 이후 1MiB/s 제한으로 보존하도록 설정됐다. 한국거래소의
[2026년 공시 일정 안내](https://kind.krx.co.kr/external/dst/notice/11637/%5B%ED%95%9C%EA%B5%AD%EA%B1%B0%EB%9E%98%EC%86%8C%5D%202026%EB%85%84%20%EC%98%AC%EB%B9%BC%EB%AF%B8%EA%B3%B5%EC%8B%9C%20%EC%95%88%EB%82%B4.pdf)는
10월 8일을 한글날 연휴 전 마지막 정규장으로 다룬다. 시작 직전 거래일 여부는 다시 확인한다.
기존 10월 6일 trace는 160 chunk checksum이 맞아도 35,251 sequence 누락과 97,849 input
rejection이 있고 무결한 1분 구간이 없다. 현재 `recorded_window_events`도 incomplete/누락
trace를 거부하므로 이 자료의 replay gate를 완화하지 않는다. 새 v6 capture가 성공해도 원인
입력 범위는 0B·0w·0J·0U와 관측된 store 호출이며 TOP20 `ka00198`·뉴스·REST 원인
입력은 빠진다. 따라서 장중 무손실이 확인돼도 전체 앱 replay 또는 TOP20 ON/OFF 성능 기준선으로
승격하지 않는다. 운영 source·진단 제어는 이번 확인에서 변경하지 않았다.

2026-10-07 O12 녹화본 장후 판독 준비: `scripts/analyze_db_trace.py`가 NAS 원래 청크 배치와
API로 내려받은 `chunks/` 배치를 모두 읽도록 고쳤다. 청크는 순차 검사하고 DB call 시작·끝만
보유해 원인 입력 전체를 메모리에 쌓지 않는다. 10월 6일 trace 양쪽을 실제로 읽어
160개 checksum 일치, sequence 누락 35,251건, input rejection 97,849건과
`capture_integrity_ok=false`가 동일함을 확인했다. 이 판정은 manifest·청크와
입력 counter에 한정되며 payload blob, workload별 원인 재생, 초기 상태 동등성은 승인하지 않는다.
10월 9일 사후 검증 예약은 manifest·청크뿐 아니라 **참조된 payload bundle 전체의 범위·SHA-256**을
검증하고 함께 보존하도록 갱신했다. 보존 위치 `artifacts/diagnostic-traces/`는 Git에서 제외한다.
복사가 불완전하면 replay-ready로 표시하지 않으며 NAS 운영 release·녹화 예약·진단 제어는 그대로다.

2026-10-07 O12 내려받은 trace 재생 경로 확인: offline reader는 별도 import API 없이
`KIWOOM_DIAGNOSTIC_WORKLOAD_PATH`의 부모 아래 `diagnostic-traces/<trace_id>/`를 읽는다.
manifest·`.jsonl` 청크·`.payloads` 묶음은 해당 trace 폴더 바로 아래에 있어야 하며
`chunks/` 하위 폴더 배치는 분석 스크립트에는 호환되지만 재생 reader에는 호환되지 않는다.
NAS의 완료된 짧은 trace에서 기존 reader의 manifest/첫 청크/첫 payload 해시 조회를
읽기 전용으로 확인했다. 내일 trace의 전체 payload 보존·무결성·workload별 재생은 아직
검증 전이다. 10월 9일 검증 예약에는 평평한 파일 배치와 격리 offline 경로를 명시했다.
현재 `collector_with_background` reader는 선택 창 최대 10분, capture 시작 후 첫 15분 안에서만
끝나는 창을 허용한다. 따라서 65분 기록의 무결성이 확인돼도 그 전체를 한 번에 collector
원인 재생할 수 있는 상태는 아니다. 초기 RAM 상태 등가 역시 별도 검증이 필요하다.

2026-10-07 O12 65분 녹화 후반 재생 경계 확인(로컬): 완료된 65분 trace 형태의 fixture에서
60분 시점의 store operation만 선택한 100초 `recorded_operations` window는 원본 전체
sequence를 검사한 뒤 해당 호출의 payload만 읽고 plan으로 컴파일했다. 첫 구간의 payload는
읽지 않았다. 같은 후반 window의 `collector_with_background`는 기존 15분 prefix 제한으로
명시적으로 거부했다. 경계 관련 동기 회귀 4건 통과. 따라서 후반부는 **기록된 DB 호출 단위**로
비교할 수 있지만, 0B에서 파생된 저장 주기 변경이나 전체 65분의 collector 원인 재생을 검증하는
근거는 아니다. 실제 10월 8일 trace의 무손실·payload 범위·baseline 상태는 아직 미확인이다.

2026-10-07 O12 capture-relative 10분 window 사전 판독기(로컬): opt-in `--window-seconds 600`
분석을 추가했다. 구간별 workload/writer 시작·종료, collector 입력 prefix, payload 참조 크기,
거부 입력을 집계하지만 `metadata_only_preflight=true`, `replay_ready=false`로 표시해
재생 승인과 분리한다. 로컬에 보존된 NAS trace `20261005T235952Z-c138934f2486`를 다시
판독했다. 160개 청크 checksum은 일치했지만 상태는 incomplete, `197333` accepted/written,
`35251` known dropped와 sequence 누락, `97849` input rejected였다. 구간별 결과는 다음과 같다.

| capture 상대 구간(초) | operation 시작 | collector 입력 prefix 누적 | 입력 거부 | 선언 payload 합계 |
|---|---:|---:|---:|---:|
| 0–600 | 404 | 1,379 | 9,121 | 5.75 MB |
| 600–1,200 | 752 | 5,569 | 14,103 | 12.96 MB |
| 1,200–1,800 | 1,255 | 12,646 | 10,351 | 26.06 MB |
| 1,800–2,400 | 1,709 | 21,664 | 10,491 | 41.22 MB |
| 2,400–3,000 | 952 | 26,330 | 13,552 | 46.71 MB |
| 3,000–3,600 | 1,629 | 34,189 | 10,964 | 56.36 MB |
| 3,600–3,953 | 678 | 37,516 | 7,112 | 60.75 MB |

collector 입력 수와 payload 합계는 각 구간 재생에 필요한 시작 prefix라 누적값이다. 전 구간에서
시작·종료 pair 수는 맞고 manifest 내 payload 참조 누락은 없었지만, 이는 sequence 손실이나
거부 입력을 보완하지 않는다. trace 시작은 08:59:52 KST여서 첫 창도 정확한 09:00 경계가 아니며,
각 창에 거부 입력이 있어 어떤 창도 replay 기준선으로 승인할 수 없다. 부분 workload 출현량만
살펴보는 자료다. 단위 테스트 2건, py_compile, diff check 통과. NAS 활성 소스·진단 제어는 변경하지 않았다.

2026-10-07 O12 TOP20 fan-out replay 입력 감사(읽기 전용): 같은 trace manifest는 schema 2이며
`store_inputs`와 `collector_inputs`만 활성화되어 있다. 스트리밍 event inventory에는
`rest_input` 또는 TOP20 순위 `market_input` 이벤트가 없다. `input_coverage.rest_market.accepted=257`은
관측된 DB 호출 범위의 집계이지 REST 요청·응답 결과 tape가 아니다. 따라서 이 10월 6일 trace로는
`ka00198` 및 후속 `ka10001/10100/10080/10081/10045` 응답을 재생할 수 없다. 청크 160개의 checksum이
정상이어도 이 replay 입력 공백은 해소되지 않는다. 로컬 소스에는 opt-in 시장 요청 tape와 native TOP20
lifecycle adapter가 있지만, 이것만으로 NAS 활성 v6 또는 예약 캡처가 해당 입력을 기록한다고 볼 수 없다.
이번 확인에서는 코드, 활성 릴리즈, 캡처 예약, 진단 제어를 변경하지 않았다.
2026-10-07 causal capture + deferred RAM 통합 후보 — 용량 acceptance 미완료:
검증된 T4 v2 소스에 REST/catalog/ranking/subscription/lifecycle/delivery 입력과 deferred
RAM 옵션을 공개 capture API로 연결한 비활성 `2026.10.07-causal-deferred-capture-v1` 후보를 만들었다.
NAS staged release는 `2026.10.07-causal-deferred-capture-v1-24cd50ff92fe752b`, snapshot commit은
`54fa29e5df9a34839b91ea853354e39d9286badc`이며 staging 결과 `active_changed=false`였다.
schema 3, `store_inputs`/`collector_inputs`/`top20_inputs` 기본값 false, 4GiB/1M 기본 한도는 유지한다.
관련 로컬 회귀 78건과 API 옵션 전달 검사는 통과했다. 그러나 native collector→hub→consumer의
20행/message 통제 burst에서 1,000 message(20,000 tick)가 62,001 event와 약 499MiB charge를
발생시켰고 약 88%가 delivery receipt였다. 8GiB/5M 시험도 326,960 tick, accepted 1,013,577에서
`capture_memory_full` 입력 거부가 발생했다. 당시 RSS는 약 1.26GiB이므로 event count보다
보수적 memory charge가 먼저 한도에 도달한다. ON/OFF p95는 작은 통제 fixture에서 각각
6.35ms/2.11ms였다. Windows 통제 burst 결과이며 NAS 장초 throughput 또는 전체 부하 기준선이 아니다.
REST 900행 약 3.0MB와 catalog 5,000종목 약 3.6MB charge는 별도 shape 측정이고 위 burst에는
동시 실행되지 않았다. 실제 최대 혼합 입력량, NAS RSS/MemAvailable/ON-OFF 지연 및 새 후보의
PostgreSQL gate는 미완료다. 단순 8GiB/5M 확대를 승인하지 않으며 receipt 보존·charge 계산
계약 재검토가 필요하다. 현재 NAS active v6와 10월 8일 08:55 예약은 변경하지 않았다.
근거: `artifacts/causal-capacity-on-small.json`, `causal-capacity-off-small.json`,
`causal-capacity-8g-saturation.json`, `causal-capture-candidate-regression.stderr.log`.

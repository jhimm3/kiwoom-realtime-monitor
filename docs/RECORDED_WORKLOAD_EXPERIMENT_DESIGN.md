# 장중 사건 기록을 이용한 반복 부하 실험

## 2026-10-09 전체 앱 최적화의 우선순위와 현재 판정

사용자가 지정한 순서는 P0 전달·정합성·복구 → P1 여러 workload의 공통 장기 지연 →
P2 중복·불필요한 처리 → P3 소비자 영향에 따른 개별 최적화 → P4 2/3/5배 검증이다.
저장 주기·batch·writer 분리·동시성 수치는 유지 조건이 아니다. 불완전한 10월 8일
녹화본으로 그 수치를 확정하지 않는다. SQL 감소만으로 전체 병목 해결을 승인하지 않는다.
각 변경은 동일 입력 hash/초기 상태/선택·제외/동시성/저장소 조건에서 기능과 성능을 함께
비교한다. source time, revision/metadata/완료 의미, retry/rollback/복구 및 주문·체결·
포지션의 연결 소비자를 확인한다. 미측정 지표와 비용 이동은 명시하고 완료를 보류한다.

감사 판정은 (1) 코드·consumer로 확정한 명백한 중복/불필요, (2) 측정 전 결론 보류인
성능 후보, (3) 성능과 별개의 정합성/복구 문제로 나눈다. 완전한 녹화가 올 때까지
확보된 부분의 실제 병목 분석을 멈추지 않되, 기록되지 않은 입력은 만들어 넣지 않는다.
collector 원인을 실행한 workload의 과거 descendant DB 호출은 중복 주입하지 않는다.

### P0: 실제 동시 reader와 sequence 잠금 이전 gate

비활성 `2026.10.09-observation-concurrency-v1-2d67e2620004fa7d`는 기존 recovery v1의
runtime을 그대로 두고 native 검증 두 건을 추가했다. NAS job
`98589a7267e41651c4d3b11bf5246c86`: 57건 통과, 오류·실패·skip 0.
동일 store의 실제 PostgreSQL probe 둘 중 오래된 publication을 늦춰 최신 경계가 먼저
발행되게 하고, 오래된 probe가 행/cursor를 발행하지 않는 것을 확인했다. 실제 nextval의
fast-path RowExclusiveLock은 대기 중인 ALTER SEQUENCE로 이전된 뒤에도 같은
pid/virtualtransaction을 유지했고, pending 경계는 allocator rollback 전까지 열리지 않았다.
대기와 rollback 번호 공백을 입력으로 만들지 않는다. 기존 bootstrap/복구/fill/intent/
취소 drain 회귀도 함께 통과했다. 증거: `artifacts/observation-concurrency-proof-20261009.json`.
운영 active/컨테이너 보존과 임시 PG 정리를 확인했다. 배포 및 전체 P0 완료는 아니다.

다른 순번 소비자의 코드 추적 결과:

| 경로 | 실제 writer/consumer 경계 | 현재 판단 |
| --- | --- | --- |
| 관측 revision → Shadow/모의 runner | 공통 safe page/bootstrap/구형 frames-only 복구 | 위 native 회귀 통과; 추가 읽기 비용·다종목 RSS/지연 미검증 |
| Shadow 후보 → PC CandidatePollWorker | worker는 next_cursor와 caught-up high_watermark를 올림; 운영 producer 교체는 previous.close 후 replacement.start | reader만 보면 같은 위험 모양이지만 현재 단일 producer의 직렬 저장/종료 경계가 있음. 다중 producer 재현 없이 운영 누락으로 단정하지 않음 |
| 모의 체결 → runner/risk monitor/매매일지 | ExecutionRuntime은 start 때 immutable owner를 bind; owned event는 sequence INSERT 전에 계좌 lease 행 FOR UPDATE | 동일 계좌의 owned writer는 COMMIT까지 직렬화됨. unowned offline/import 계약 및 다른 호출자 혼합은 별도 확인 대상이며 잠금 제거 금지 |
| 연구 export | 동결 manifest의 ordinal/count/revision hash로 paging | live accepted_sequence cursor와 다른 계약. 같은 수정 일괄 적용 금지 |

복구 CPU/메모리의 로컬 기준도 남겼다. 6,004 membership/701행 page/300초 horizon을
동일 빈 scratch에서 실행했을 때 시간순 30초 입력은 Shadow 67.830ms, mock 51.230ms,
동일 available_at stress는 각각 9,939.716ms/5,910.522ms였다. stress CPU는 각각
9,921.875ms/5,906.250ms, GC 총 대기는 1ms 미만, 샘플 working set은 약 49~50MB였다.
이는 Windows 통제 membership fixture이며 NAS RSS·native reader/전체 consumer 시간이나
실제 장중 기준선이 아니다. 반복 trim CPU 후보와 종목 수 전체 상한을 검토해야 한다.
증거: `artifacts/observation-recovery-cost-baseline-20261009.json`.

### P0: 수신 큐의 실제 기록 범위와 상한가 사실 수집의 통제 재현

동일 원본 scalar audit에서 09:00~09:10 TOP20 subscriber의 trade enqueue/dequeue/
consume_end는 각각 141,344건, program_trade는 각각 2,307건이었다. 기록된 drop/
untracked_drop은 0이다. 이는 해당 경로의 기록상 처리 확인이며 다른 subscriber의
무손실 증거가 아니다. `MarketEventService.start`는 capture_component 없는 hub subscriber를
만들고, `dropped_delivery`는 capture_component가 없으면 기록하지 않는다.
따라서 recorder의 input_rejected 1,188건과 운영 큐 유실은 서로 다른 값이고, TOP20
receipt만으로 hot-cohort/PC subscriber의 유실 여부를 판정할 수 없다.

`artifacts/probe-market-event-delivery-20261009.py`의 격리 SQLite/실제 hub·event loop
통제 fixture에서 두 결함을 재현했다. 외부 요청·운영 DB 접근·runtime 수정은 없다.

1. `append_upper_limit_facts` 실패 한 번이 `_event_loop` 밖으로 전파되어 consumer가
   종료된다. 뒤의 다른 종목 체결은 queue에 남고 last_ticks/fact가 갱신되지 않는다.
   기존 close도 실패한 task의 OSError를 다시 전파하여 disconnect까지 도달하지 못한다.
2. 상한가 사실 저장을 기다리는 동안 1,001건을 publish하면 실제 1,000건 queue는
   최초 체결 한 건을 버린다. 그 체결만 상한가에 있던 두 번째 종목의 CURRENT 사실과
   last_tick은 남지 않는다. 이후 그 종목 체결이 없으면 현재 코드에 복원 경로가 없다.

증거: `artifacts/market-event-delivery-proof-20261009.json`(실행 소스 hash 포함).
원인 코드는 확인했지만 **10/8 운영 발생 횟수는 미확정**이다. 새 입력으로 하는 통제
정합성 재현을 과거 녹화의 누락 입력 복원이나 실제 성능 기준선으로 사용하지 않는다.

현재 consumer는 인증 `market_event_history(kind=upper_limit)` API와 정규장 종료의
`last_ticks → CLOSED_AT_LIMIT` 생성이다. research factor에는 optional 계약만 있고
코드 추적에서 이 DB 사실이 현재 주문 판단에 직접 들어가는 경로는 확인되지 않았다.
화면의 상한가 강조는 별도 0g/기본정보 값을 사용하므로 이 사실 원장의 consumer로
묶지 않는다. consumer가 적다는 이유로 수집 이력을 제거하지 않는다.

내부 한도/정책도 유지 조건이 아니다:

| 경로 | 실제 경계 | 분류/다음 결정 |
| --- | --- | --- |
| hub subscriber | maxsize 1,000; oldest drop | P0: loss 정책과 consumer별 최신 projection/원본 사실 요구를 분리해 결정. 모든 queue 무제한화 금지 |
| hot-cohort event loop | 같은 task에서 사실 저장 ack까지 대기; 실패 시 종료 | P0: 원인 확정. immutable 사실 입력의 재시도 소유권·종료 drain·재시작 한계와 causal 계보를 먼저 결정한 뒤 최소 수정 |
| 조건 metadata/signals | 2,000/5,000; full 시 log 후 미적재, worker 저장 실패 시 log 후 task_done | P0 후보: registration preflight가 일부 신호를 막지만 전체 overflow/retry/복구 consumer 검증 미완료 |
| VI distinct background saves | task set/inflight dict, 동일 키만 in-flight 공유 | P1/P2 후보: distinct task/global concurrency peak는 미측정. 중복 ack/rollback/취소 계약 유지 |
| 모의 계좌 queue·REST 요청 queue | 현재 unbounded 경로 | 측정 보류: peak와 완료 지연 없이 상한·병렬수 임의 변경 금지 |

이 단계에서는 queue/batch/timer/concurrency 수치를 변경하지 않았다. 확인된 사실 유실
수정과 공통 장기 지연 분석은 별도로 검증한다. 큐를 크게 만드는 것만으로 완료 처리하지 않는다.

#### 2026-10-09 설계 결정: 상한가 사실 판정과 저장 대기의 기능 내부 분리

설계 단계에서 최신 실행 기록의 Astra High를 확인하고 아래 경계를 결정했다.
이 계약에 따른 Sol High 구현/검증 결과는 아래 후속 절에 기록한다. 설계 결정 자체를
runtime 검증·배포 또는 전체 P0 완료로 취급하지 않는다.

**원인 대조 보강.** 최초 probe는 1,001건을 loop에 양보하지 않고 publish했으므로,
그 결과만으로 DB 대기가 유실의 필요조건이라고 주장할 수 없다. 추가 대조는 같은
1,001건/hash `93d721686645c21262f8021b29ffe9f25f474a3b2eea4f2b23e8e18a1dec3498`를
1ms 간격으로 예약하여 두 실행 모두 consumer에 실행 기회를 줬다. 정상 저장은 queue
최대 39/drop 0/두 번째 종목 TOUCHED·CURRENT 보존, 첫 사실 저장을 붙잡은 실행은 queue
최대 1,000/drop 1/두 사실 모두 누락이었다. 이는 Windows/임시 SQLite 통제 대조이며
1ms를 실제 처리율 보장 또는 10/8 입력 복원으로 해석하지 않는다. 증거:
`artifacts/market-event-delivery-cadence-proof-20261009.json`.

**채택안.** `MarketEventService` 내부에서 수신·사실 판정과 DB ack 대기를 분리한다.
공통 hub의 모든 subscriber를 바꾸거나 raw tick queue를 무제한으로 만들지 않는다.
새 범용 Manager/Queue 프레임워크, pool, DB 스키마, 전역 writer lock도 추가하지 않는다.
기존 native `append_upper_limit_facts([one_fact])`와 first-write-wins 계약을 재사용한다.
이 단계는 별도의 batch/SQL 최적화가 아니다.

구체적인 구현 계약:

1. `_event_loop`가 stock_reference 순서, cohort 조건, last_ticks/session 갱신, 가격 기준
   검증을 지금 순서로 수행한다. 보통 경로에서는 DB 완료를 기다리지 않는다. 같은 event의
   판정 결과를 먼저 완전히 만들어 두고 pending에 넣는다. `observe_trade`와 정규장 종가
   호출은 같은 판정/입력 준비 함수를 재사용하며 **await 반환은 실제 DB ack 이후**다.
   종료 시점의 CLOSED_AT_LIMIT은 앞선 accepted fact와 같은 저장 소유자를 사용한다.
2. pending은 `(code, session, status)`별 한 건이다. 기존 `_facts`는 DB ack 완료 집합으로
   유지한다. pending은 ack 완료로 취급하지 않으며, 동일 key 재수신은 같은 completion을
   공유한다. 중복 체결마다 task/future/문서를 새로 만들지 않는다. 날짜·상태·가격 기준·
   effective_at/available_at·source/evidence·fact_key/fact_id를 최초 판정 때 고정한다.
   재시도 시 새 가격·날짜·현재 시각으로 문서를 다시 만들거나 최신 값으로 덮지 않는다.
   기존 predicate와 UNKNOWN/TOUCHED/CURRENT 공존 의미를 유지한다.
3. feature 소유의 저장 worker 한 개가 실제 pending 순서대로 기존 독립 native 호출을
   수행한다. 성공/중복 no-op ack 뒤에만 pending을 제거하고 `_facts`/completion을 갱신한다.
   실제 COMMIT 후 응답만 유실되면 **동일 문서**로 다시 호출하여 ON CONFLICT로 중복 없이
   확정한다. 다른 기능/VI writer의 연결·COMMIT·병렬 실행은 이 worker에 편입하지 않는다.
4. 일시 저장 실패는 pending과 소유권을 유지하고 worker에서 재시도한다. 최초 후보의
   재시도 최소 간격은 1초이며 pending이 없으면 Event 대기로 쉰다. 이 간격은 순위/수신
   timer를 바꾸지 않고 오류 hot loop만 막는다. 반복 실패 로그는 집계/상태 전환 중심으로
   제한한다. 예외가 수신 consumer를 종료시키거나 성공 completion을 만들면 안 된다.
   고장 난 첫 기록을 버려 후속 기록을 성공처럼 진행시키지 않는다.
5. 명시적 pending entry/payload 한도를 둔다. 구현 후보는 **20,000 key / JSON payload
   64MiB**이며 성능 최적화 확정값이나 RSS 제한이 아니다. 실제 구현/NAS gate 전에는 운영
   한도로 확정·배포하지 않는다. 5,000 합성 종목×4상태의 제안 표현은 payload 7,880,000B,
   Windows working-set 증가 25,362,432B/구성 255.386ms였다. 이 fixture에서는 event count가
   먼저 찬다. Future/dict/identity 비용을 payload byte로 숨기지 않는다. 전체 Context 복사는
   큰 원본/REST tape를 붙잡을 수 있으므로 금지한다. 작고 제한된 origin identity만 보관한다.
   증거 `artifacts/upper-limit-pending-design-sizing-20261009.json`; NAS RSS/capture ON/OFF는 미측정.
6. 한도에 닿으면 준비한 현재 event의 불변 plan을 최대 한 개만 보류하고 빈 자리까지
   admission을 기다린다. 한도 밖 별도 list/task로 우회하거나 pending을 oldest-drop하지 않는다.
   이때 hub의 raw queue가 넘칠 가능성은 남는다. `dropped_events` 변화·pending 포화·저장
   실패를 기존 condition runtime의 별도 `fact_collection` 상태/작은 카운터로 공개하고 coverage를
   부분/복구 필요로 표시한다. 조건식 REG의 `_condition_phase`/apply_status를 이 오류로 덮어
   불필요한 조건 재등록을 유발하지 않는다. 상태가 정상이라고 표시한 채 원본을 버리면 안 된다.
   이미 드롭한 틱은 이후 high
   값으로 CURRENT/effective time까지 복원됐다고 추정하지 않는다. 신호/metadata 큐 수정은 별도다.
7. close는 producer 수신을 끊고 이미 큐에 들어온 event를 판정까지 처리한 뒤 pending 및
   실제 executor 호출을 drain한다. 이미 실패한 다른 소유 task가 있어도 finally에서 disconnect와
   전체 소유 작업 정리를 수행한다. 반복 close/cancel은 같은 close 소유자를 기다리며 to_thread
   취소를 실제 COMMIT 취소로 오인하지 않는다. `owned_create_task/owned_to_thread`의 replay
   소유권과 기존 TOP20/VI의 shield/drain 패턴을 재사용한다. worker를 `_tasks` 일반 일괄 cancel에
   섞어 pending을 버리지 않는다. hub 공통 task_done/join 의미를 바꾸지 않는 종료 경계를 쓴다.
8. **저장 보장 경계:** pending RAM 입장은 영속 완료가 아니다. 정상 close 성공은 pending=0,
   실제 native thread=0 이후에만 가능하다. 영구 DB 오류 상태에서 close 성공/재시작 복구 완료를
   보고하지 않는다. 강제 종료·전원 장애의 미확정 RAM 사실은 복원할 수 없으며, 시작 시 이전
   process의 coverage를 완전하다고 가정하지 않는다. COMMIT된 사실은 기존 fact_key 충돌 처리로
   보존한다. 운영 operator의 stop --time 60 강제 종료 가능성도 gate에 포함하고, pending이 남은
   상태의 강제 종료를 graceful 무손실로 통과시키지 않는다. 배포 전 이 한계를 명시한다.
9. 이번 좁은 수정에는 새로운 파일 outbox를 넣지 않는다. 현재 JsonRecordOutbox는 손상/읽기
   오류를 빈 상태로 반환하며 put/remove마다 전체 파일을 재작성하고 fsync 보장이 없다.
   그대로 재사용하면 정합성·추가 I/O 문제를 옮길 수 있다. 비정상 종료 전 pending까지 내구화가
   필요하면 별도의 strict 복구/원자 저장/제한된 I/O 검증을 통과한 후 후속으로 다룬다. 기존 TOP20
   outbox의 동작을 상한가 수정에 끼워 변경하지 않는다.
10. causal 기록은 실제 admission의 작은 origin identity만 보존한다. source trace가 바뀌면
    이전 trace cause를 새 trace에 연결하지 않고 unknown으로 남긴다. worker의 가장 최근 event
    context를 모든 pending에 적용하지 않는다. 현재 hot-cohort에는 delivery capture가 없고
    collector replay도 이 feature를 실행하지 않으므로 TOP20 receipt를 임의 차용하거나 0B의
    지원된 descendant라고 표시하지 않는다. 기존 store capture/unsupported 판정을 유지한다.
    future가 원인 입력을 지원하게 되면 collector→새 writer와 과거 store의 중복 제외를 별도 gate한다.

**검증 및 완료 범위.** 위 고정 입력의 정상/held-writer 대조를 구현 후 다시 실행하여
fact key/문서·누락·queue high-water·pending 수/bytes·처리 완료 지연을 비교한다. 단순
무양보 burst는 별도 queue 한계 시험으로 유지하며 DB 지연 회귀와 혼동하지 않는다.
단일/동시 중복, 두 상태의 첫 입력, 날짜/가격 기준 변경, 잘못된 입력, statement rollback,
실제 COMMIT ack 유실, 독립 peer, 한도 도달, 반복 cancel/close, 정상 재시작을 검증한다.
실제 PG 테스트는 upper-limit/cohort fixture에만 scope를 주고 기존 VI gate도 통과시킨다.
consumer가 받는 정상 저장 history와 정규장 종가 문서를 대조하고 주문 경로가 호출되지
않음을 확인한다. collector/market boundary 및 REST capture 회귀도 실행한다.
NAS actual RSS/CPU/queue/저장 지연과 capture off/on의 부작용을 측정하기 전에는 최종 배포·
성능 개선을 승인하지 않는다. 무한 DB 장애나 event-loop 전체 정체에서 무손실을 보장하는
수정은 아니다. 이 통제 입력은 10/8 녹화본을 대체하지 않으며 P1 원인 규명도 계속 남는다.

#### 2026-10-09 구현 및 로컬 검증: 상한가 사실 pending/ACK/drain

`market_events.py` 내부에 frozen JSON bytes/공유 completion/origin 4개 string/trace token의
명시적 pending 표현과 feature writer를 구현했다. 중복 입력은 같은 pending을 재사용하고,
event loop는 보통 RAM admission까지만 기다린다. 2개 status가 생기는 입력은 capacity
대기 전에 날짜·기준 가격·증거·시각을 모두 고정한다. native 호출은 기존 one-fact 연결과
COMMIT을 유지한다. 실제 ACK 또는 conflict no-op 이후에만 `_facts`로 승격한다.
실패의 동일 문서 retry는 1초 간격이며 caller 취소는 completion을 취소하지 않는다.
shutdown owner는 이미 accepted된 작업을 실행하고 close는 입력/DB drain 후에만 성공한다.
metadata/signal peer 오류도 disconnect와 fact drain을 건너뛰지 않고 마지막에 보고한다.
정규장 완료 표식은 CLOSED_AT_LIMIT ACK 뒤에 저장한다. 새 범용 계층·파일 outbox·pool·
schema·공통 hub 정책·VI transaction을 추가/변경하지 않았다.

로컬 gate: 전달 13/시장 16/capture·REST·collector replay 38/collector 32 통과.
같은 hash `93d721686645c21262f8021b29ffe9f25f474a3b2eea4f2b23e8e18a1dec3498`,
1,001건/1ms 입력의 held-write 결과는 queue high1/drop0/native write3이며 두 번째 종목의
TOUCHED/CURRENT가 남았다. 이전 held 결과는 high1000/drop1/두 사실 없음이었다.
이것은 통제 입력의 전달 회귀이며 Oct8 누락 입력 복원/전체 앱 성능 기준선이 아니다.
무양보 1,001건 burst에서는 여전히 raw queue가 1건을 버리고 fact_collection coverage를
partial/recovery_required로 표시한다. 기존 queue 정책 자체의 무손실 보장은 하지 않는다.

실제 pending 20,000건 표현의 production-debug-off 로컬 측정은 payload7,794,808B,
입장315.765ms/CPU328.125ms였다. count 한도가 먼저 찼고 64MiB payload 미만이었다.
executor는 이 크기 gate에서 가짜로 격리했으므로 native DB 처리량 수치가 아니다.
Windows에서 Linux RSS/MemAvailable은 미측정이다. unittest debug stack을 켠 별도
실행24초를 운영 입장 비용과 혼합하지 않는다. actual executor/COMMIT은 별도 gate다.

비활성 후보 `2026.10.10-upper-limit-fact-drain-v4-97bcf9bf50764f6b`를 기존
observation-concurrency 후보 위에 해시 검증하여 게시했다. 운영 active/원본 trace는
보존한다. v3-baf398c3a624dfd9의 NAS job ffbbb18efeb9189f487134f6b2ed4640은
native rollback/ACK 유실/독립 VI/캡처 계보/cohort 경합 포함106개를 통과했다.
errors/failures/skips0, cleanup/post-job fence 통과, active/두 운영 컨테이너 보존이다.
v4는 원래 호출 시각·세션·조건 선택을 잠금 대기 전에 고정하도록 추가 보완했다.
자정 경계에서 D의 날짜/last_signal_at과 이력·NXT 상태를 유지하는 로컬29개가 통과했고,
직접 연결된 native/시장/전달39개도 job4c4b0a26fd0684d642f155783545c7df에서
errors/failures/skips0으로 통과했다. cleanup/post-job fence 통과, active/두 컨테이너
보존이다. v3 전체106과 v4 직접39는 겹치는 검사이며145개 고유 검사로 합산하지 않는다.
첫 NAS gate의13 error는64MiB tmpfs fixture가 운영256MiB 공간 가드보다 작았기
때문이다. 기존 test-only headroom fixture를 사용했고 운영 가드/거부 검사는 유지한다.
그 다음 gate는107건 중 capture3 판본 불일치와 조건식1 실패였다. 해당 후보에서
새 upper/native 검사는 통과했지만 전체 승인으로 처리하지 않았다. 조건식은 원래
queue.join을 기다리고 있었으며 그 기준을 완화하지 않았다. 같은 소스 단독 gate1은
통과했지만 v2 묶음104건에서는 같은 last_signal assertion이 다시 실패했다.
통제 red gate가 확인한 원인은 eligibility 저장이 기존 state lock을 가진 동안
record_condition_signal이 잠금 없이 이전 cohort를 읽고 current를 별도 저장하는 경합이다.
v3는 기존 잠금 안에서 신호 snapshot/native ACK/RAM publication까지 처리한다.
새 회귀는 eligibility의 실제 저장 시작을 붙잡고 후행 D의 store 진입을 차단하는지,
최종 DB/RAM의 D와 NXT 여부 및 ENTERED/ELIGIBILITY/D 이력이 모두 남는지 검사한다.
기존 저장 함수/transaction/조건식의 D 의미를 바꾸지 않았고, COMMIT 성능 개선으로
분류하지 않는다. native PostgreSQL 동일 경합 검사를 추가했다.
후보는 base runtime의 전체 capture14건을
사용한다. 새 copy-lane/rejection-detail을 기대하는 local HEAD의3건은 그대로 남기고
이번 오래된 base runtime 후보에서는 미검증 계약으로 명시한다.
pending 표현의 Linux RSS 샘플은 검사 stdout에만 있다. 현재 제한된 operator report가
그 수치를 반환하지 않으므로 실제 NAS RSS 증가량/메모리 승인으로 보고하지 않는다.
두 fact의 capture 호환 샘플은 장초 peak overhead 승인 근거가 아니며 저장 shape/초기 상태가
다르면 elapsed 비교를 최적화 증거로 쓰지 않는다. 강제 종료 pending 복구, 전체 peak
capture 비용 및 P1의 공통10~20초 원인 규명은 여전히 남는다. 현재 배포하지 않았다.
signal/metadata worker의 기존 저장 실패 후 task_done 및 종료 timeout/실제 thread drain은
별도 P0 조사 대상이다. 이번 fact worker 정합성 gate를 feature 전체의 복구 승인으로
확대하지 않는다. 상태 잠금의 signal 대기 영향도 큐/처리 지연 측정에서 따로 확인한다.
`probe-market-event-worker-failures-20261010.py`는 실제 feature worker/임시 SQLite와 가짜
REST를 사용한다. signal ENTERED 저장 실패1회 후 DB/RAM 종목은0, queue unfinished0이고
retry 입력은 남지 않았다. ELIGIBILITY 저장 실패1회 후에는 DB NXT=null/RAM NXT=true,
metadata queue unfinished0이었다. 결과는 `market-event-worker-failure-proof-20261010.json`.
원본 녹화/운영 DB/실제 API·주문은 사용하지 않았다. 이 증거는 현재 worker의 단일
저장 실패 처리만 입증하며 재연결/후행 신호 복구·실제 장중 발생·성능까지 일반화하지 않는다.

#### 검토한 대안과 기각 이유

현재 책임은 hub delivery → MarketEventService의 순차 수신/판정/DB ack → 사실 원장이다.
DB await를 그대로 두고 재시도만 추가하면 수신 정체·oldest drop을 더 키울 수 있다.
반대로 raw tick queue를 무제한화하면 RAM과 복구 공백으로 비용이 이동한다. 비교할 선택은:

- 기존 event task 안에서 동일 사건 재시도: 작은 변경이지만 다른 종목 delivery 보호 실패.
- 사실 판정과 immutable pending fact 저장을 기존 feature 안에서 분리: 원인 사건 시각,
  first-evidence/fact_key/source/basis를 고정하고 동일 키 재시도·ack 유실·close drain·종가
  호출의 ack 계약을 보존해야 함. pending 상한 도달 때 조용한 discard 금지. RAM pending은
  비정상 재시작 무손실 근거가 아니므로 기존 복구 요구와 범위를 명시해야 함.
- hub 차원의 consumer별 delivery 정책 변경: TOP20/PC/시장 사실 간 다른 의미와 capture
  receipt/provenance를 함께 건드려 더 넓은 회귀 범위. 확인된 단일 경로부터 검토할 것.

위 채택안으로 feature 안의 최소 분리를 진행한다. 통제 red fixture를 유지하고 동일 사건
세트의 fact 결과·실패/ack 유실/종료와 관련 consumer 회귀로 green을 확인한다. 현재 실제
녹화에서 이 경로가 빠져 있으므로 whole-app 성능 개선 판정은 별도다.

### P1: 장초 여러 writer의 COMMIT 지연이 겹치는 실제 구간

원본 manifest hash `4142345fdc585125be9513cbef2cdaddfedc545dc4be9545e6d3936ba11afc9e`,
1,490 chunk/2,033,667 event의 checksum·연속 seq를 다시 검증했다. 원본은 변경하지 않았다.
09:00~09:10에 시작했거나 COMMIT 창이 겹친 native call 4,026건을 비교했다.
COMMIT 1초 이상 182건, 10초 이상 7건이며 동시 call/COMMIT은 관측 최대 8개였다.
구간 call의 미완료 end는 0이다. 전체 trace 입력 거부 1,188건과 observed-path-only
coverage는 그대로 한계이며, 이 scalar 분석은 완전한 workload replay/성능 기준선이 아니다.

특히 09:04:43.377~09:05:04.553 KST에는 다음 대기가 겹쳤다.

| writer | SQL 실행 합계 | COMMIT |
| --- | ---: | ---: |
| query_minute | 491.256ms | 18,071.270ms |
| shadow_monitor_state | 92.525ms | 20,892.525ms |
| realtime_second_bar | 5,196.105ms | 12,898.138ms |
| execution_runtime_acquire | 1.723ms | 10,714.527ms |

VI 4건도 이 episode에 참여했고 최대 7개 장기 COMMIT이 겹쳤다. 여러 호출이
09:05:04.360~04.377에 함께 반환된 것은 공통 대기 후보의 근거다. WALWrite/WalSync,
장치 I/O, 잠금 owner, event-loop/thread 스케줄 지연, CPU/RSS/GC 중 무엇이 원인인지는
이 trace만으로 구분되지 않는다. COMMIT 계측의 wall time에는 thread 재스케줄 지연도
포함될 수 있으므로 PostgreSQL 물리 대기만으로 단정하지 않는다.
증거: `artifacts/common-commit-window-audit-20261009.json`.

누적 관측 시간 상위는 VI(201 call/SQL 210, writer 340.843초), query_minute(52 call/
46,800행, writer 166.314초), Shadow checkpoint(63 call, 115.506초)다. 이 합은 서로
겹치는 call wall time이며 경과 시간이나 실제 CPU 시간 합계가 아니다. program_flow는
1,187 call/SQL 3,354로 많지만 COMMIT p95 5.374ms였다. 호출 수만으로 VI와 같은
우선순위를 부여하지 않는다. finalize 9 call/SQL 2,853은 기존 행별 비용의 관련 후보다.

다음 비교는 확보된 09:04~09:06 realtime store 23건의 동일 empty-v1/concurrency 8이며,
분봉 REST/Shadow checkpoint의 거부 입력, 미지원 VI·lease·뉴스 등은 포함되지 않는다.
VI와 lease는 scalar 대기는 관측되지만 현재 recorded-operation 실행 allowlist/baseline
범위 밖이다. 원인을 좁히는 데 필요한 때만 이 경계를 최소 보완한다.
디스크 대조 job `ec3a730952cda5a782e91d62220dca83`은 PostgreSQL readiness 단계의
`temporary_postgres_not_ready`로 실패했다. replay/운영 pause 전에 실패했고 서버 재시작은
없었으며 cleanup을 확인했다. 실제 PG 시작 원인은 보고서에 없어 미확정이다.
이는 최적화 결과가 아니다. 로그/상태 근거 없이 권한·대기 시간 설정을 임의 수정하지 않는다.

동일 선택의 RAM 대조는 job `a5208bed012d72e3705be7471e8ceceb`에서 완료됐다.
input hash `2de64361e2511724da8afb5954e82f503935e3a4df3f16a18a2ad27980a1bf07`,
empty-v1 baseline `56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`,
concurrency 8, native store 23 call/SQL 253, 선택 입력 생략 0, timing preserved,
source outcomes match, DB raw 계측 유실·잘림 0이다. COMMIT p50/p95/max는
0.255/0.559/6.083ms, writer total wall 합계 690.174ms였다. baseline 복원과 임시 PG
정리, 기존 active 재개를 확인했다. 승인된 운영 정지 창은 222.340초였으며 실시간 이벤트
무손실을 주장하지 않는다. 증거: `artifacts/common-bottleneck-realtime-ram-proof-20261009.json`.

이는 해당 store 입력 단독의 RAM 조건 대조일 뿐 새 최적화의 before/after가 아니다.
디스크 대조가 실패했고 다른 writer와 원래 DB 상태도 빠져 있으므로 장중 10~20초 대기의
원인을 배제하거나 해결했다고 해석하지 않는다. collector 로직은 재생하지 않았고
source_state_equivalent=false, baseline clock/reference adapters warning도 유지된다.

## 2026-10-09 실제 녹화본의 부분 native-store replay 설계

**상태: 선택된 realtime 부분 입력의 v6 기준 replay와 realtime.minute batch v2 비교를 완료했다. 전체 장초 replay는 미완료.**
사용자 지시에 따른 `partial-operations` 정책을 사용한다. 선택된 realtime 18 operation에 대해
empty-v1 baseline과 분봉 batch v2를 각각 replay했다. 입력 hash는 두 번 동일하고 timing preserved,
선택 입력 생략 0, native DB call 18건이다. 분봉 SQL 242→48, 총 writer 시간 126.836→56.568ms였으며
canonical minute bars/metadata의 전체 hash는 같았다. revision/operation 등 네 표는 전체 hash가 달라
당시 보고서로 의미 동등성은 확인되지 않았다(아래 후속 저장 내용 대조 참조).
비활성 후보 `2026.10.09-realtime-minute-batch-v2-c5d8a54aa816e05c`
NAS gate 12건과 관련 로컬 회귀 62건은 모두 통과했다. 운영 source·DB·control은 변경하지 않았다.
이 범위는 전체 앱 기준선이나 운영 저장장치 병목 재현이 아니다. 원본 capture는 incomplete이며
`source_state_equivalent=false`를 유지한다. 이전 문단의 미실행·gate 미완료 상태는 역사적 기록이다.

### 부분 replay의 저장 내용 비교

`diagnostic_recorded_execution.run_owned_recorded_experiment`는 native 연결이 모두 종료된 뒤,
측정 구간 밖에서 기존 `final_tables`의 모든 컬럼 hash와 `final_sequences`를 그대로 남긴다.
추가 `final_content_comparison`은 `diagnostic_replay_comparison.py`가 같은 소유 cursor로
dataset/document/minute-operation/observation-revision 네 표만 읽어 만든다. 새 DB 연결,
native writer 변경, clock/UUID 고정, 원본 입력 보충이나 baseline/reset 규칙 변경은 없다.

내용 hash에서는 실행 저장 시각인 dataset `saved_at`, document `updated_at`, operation
`processed_at`, revision `received_at`만 분리한다. 분리한 필드의 별도 hash와 null/row 수를
남기고, 기존 전체 hash도 유지한다. revision UUID는 `accepted_sequence`로 대응시키며,
`revision_of`가 같은 kind/subject/observation-key/source의 더 이른 순번을 가리키는지 검사한다.
없는 부모를 NULL로 바꾸지 않으며, 깨진 연결은 해당 표의 `content_projection_valid=false`와
`lineage_errors`로 명시한다. payload와 새 컬럼, source/effective/available 시각, 상태,
accepted sequence와 연결은 내용 비교에 남는다. 행 hash를 정렬하되 중복 행 개수도 보존한다.
읽기 예산은 reference pass까지 64MiB/250,000행이며 초과 시 비교 실패가 드러나고 기존
`finally`의 drain 이후 baseline 복구를 수행한다.

이 값은 **저장 내용과 revision 연결의 비교 근거**다. 저장 시각을 소비하는 정렬·증분 동기화·
신선도 판정 등 reader 동작을 같은 것으로 증명하지 않으므로 `timing_equivalence_verified=false`,
`functional_equivalence_verified=false`를 유지한다. 같은 내용 hash만으로 전체 기능 보존이나
원본 상태 동등성을 승인하지 않는다. 기존 보고서는 이 비교값이 없어 소급 판정할 수 없으며,
같은 고정 입력으로 다시 실행해야 한다.

2026-10-09 검증: 로컬 46건, NAS 비활성 proof 후보 격리 gate 37건이 skip 없이 통과했다.
실제 09:00~09:02 realtime 18 operation을 같은 후보·입력·baseline으로 두 번 replay하여
네 표의 내용·revision 연결 hash가 같고 전체 hash는 달라지는 것을 확인했다. 이는 위 projection의
같은 후보 반복성을 입증한다. batch 수정 전후와 시간 의존 reader 동작은 별도 검증 항목이다.
보고서는 `artifacts/replay-content-proof-comparison-20261009.json`이다. 두 작업은 승인된
운영 앱 정지 후 같은 릴리즈 재개를 확인했으며 운영 DB 컨테이너를 바꾸지 않았다.

후속 전후 대조: 같은 comparison 모듈을 붙인 pre-batch reference
`2026.10.09-replay-content-reference-v1-0c5d206ac8af226b`가 NAS gate 37건을
skip 없이 통과했다. 동일 실제 입력·empty-v1·concurrency 8·RAM PG의 reference 1회와
post-batch proof 2회를 대조해 18 source-call 결과, sequence, 비투영 표 전체 hash,
네 투영 표의 내용/부모 연결 hash가 동일함을 확인했다. 분봉 40행 SQL 242→48,
execute 73.146→39.799/39.301ms, writer total 96.589→57.891/57.168ms이며
COMMIT은 양쪽 1회다. 보고서 `artifacts/replay-content-before-after-comparison-20261009.json`.
이로써 선택된 입력의 **batch 전후 저장 내용 보존**은 확인했다. 시간 의존 reader,
전체 장초 workload, disk/WAL 병목 개선과 source-state 동등성은 별도 검증이며,
불완전한 원본 trace나 broad-equivalence flags를 변경하지 않는다.

### 전달 경계와 구형 checkpoint 복구 설계 (2026-10-09)

#### 구현 상태: 구형 checkpoint frames-only 복구 gate 완료

비활성 `2026.10.09-observation-recovery-v1-da67999ec33a61b4`, NAS 격리 job
`305011ce14daba8d456843b8dab591f2`: 55건 통과, 오류·실패·skip 0. 로컬 회귀 36건 통과.
late native COMMIT 복구/inline·normalized/mock/실제 final COMMIT ack 유실 후 재시작,
기존 cursor와 execution/fill/intent/binding 보존 및 과거 판단 추가 0건을 검사했다.
첫 gate는 nonexistent test selector로 loader error 1건이고 다른 54건은 통과했다.
같은 후보를 올바른 selector로 다시 검사해 55건 전부 통과했으며 실패 보고서도 남겼다.
증거: `artifacts/observation-recovery-proof-20261009.json`와
`artifacts/observation-recovery-report-20261009.json`.
운영 active/두 컨테이너/진단 idle 보존과 임시 DB cleanup 확인. 운영 미배포다.
실제 concurrent-reader/lock-transfer, 같은 scoped workload의 reader 추가 비용,
긴 과거 이력의 RSS/복구 지연은 아직 별도 gate다. 아래 각 단계의 미완료 표현은
당시 구현 상태이며 현재 최상단 acceptance를 우선한다. 역사적 전략 동등성,
원본 상태 동등성 및 운영 저장장치 성능을 승인한 것이 아니다.

#### 구현 상태: 두 consumer의 안전한 공통 bootstrap

`CandidateMonitor`와 `MockAutomationRunner`는 초기 kind별 raw history 조회 대신
`load_observation_bootstrap(INPUT_KINDS, per_kind_limit=5000)`을 사용한다.
`ready=false`면 `bootstrap_pending`을 RAM에 유지하고 기존 poll에서 재시도한다.
그동안 완료 checkpoint를 쓰거나 seed를 증분 전략 판단으로 처리하지 않는다.
mock은 fill 원장 반영과 사용자 control 검사를 계속하며 STOP이면 seed 재시도도 중단한다.
체결 원장은 별도 영속 자료이므로 초기 대기 중 재시작해도 fill을 다시 반영할 수 있다.

준비된 seed의 실제 max sequence만 cursor로 사용하고 `observation_safe_prefix/v1`
delivery protocol을 기존 native checkpoint transaction에서 frames와 함께 저장한다.
초기 저장 실패 시 pending으로 돌아가 seed 저장을 재시도하며 성공 전 증분 판단을 시작하지
않는다. 초기 history는 기존 strict 해석·trim으로만 seed하며 과거 주문/알림/decision을
재실행하지 않는다. protocol 없는 기존 checkpoint는 기존 state 그대로 복원하고 표시도
없게 보존한다. 이것은 구형 checkpoint의 누락 frames 복구 완료를 뜻하지 않는다.

비활성 `2026.10.09-observation-bootstrap-v1-86ca1a8376d8e626`,
NAS 격리 job `0abbb49c0e8368f62cbee053047fb194`: tests=44, 오류·실패·skip 0.
두 실제 consumer의 초기 pending/뒤집힌 COMMIT/후속 poll/저장/재시작을 native PG로
검사했고, shadow inline/normalized delivery header 보존과 seed의 과거 decision 0건을
확인했다. mock fixture는 주문 runtime/transport 없이 admission·consumer·checkpoint를
실행하므로 잘못된 historical dispatch는 gate를 통과할 수 없다. safe reader native gate,
metrics 및 consumer/fill/supervisor unit 회귀도 함께 통과했다. 로컬 선택 회귀는 29건이다.

보고서: `artifacts/observation-bootstrap-proof-20261009.json`와
`artifacts/observation-bootstrap-report-20261009.json`. 임시 RAM PostgreSQL cleanup,
운영 active/두 컨테이너 보존과 진단 idle을 확인했다. 원본 red fixture/report는 변경하지
않고 별도 acceptance에서 pending 중간 상태와 최종 무누락 계약을 검사했다.
운영 배포·과거 전략 동등성·성능 개선은 미확인이다. 구형 C 이하 frames 복구와
failure/cancellation/drain/fill/intent 보존, 실제 concurrent-reader/lock-transfer 및
동일 workload의 reader 비용 검증은 다음 단계다.

#### 구현 상태: 안전한 reader page 1단계

`database_observation_readers.py`에 `ObservationRevisionPage`와 store별
`ObservationDeliveryState`를 구현했다. 페이지는 `ready`, `rows`, `safe_through`,
`exhausted`, `reason`을 구분한다. `safe_through`는 내부 조회 상한이며 consumer의
영속 cursor가 아니다. 기존 증분 list API는 같은 안전한 page를 사용한다.
새 bootstrap API는 안전한 상한 아래에서 kind별 최신 행을 하나의 SELECT snapshot으로
읽는다. 두 실제 consumer의 초기 시작 경로에는 아직 연결하지 않았다.

PostgreSQL은 같은 호출별 native 연결에서 설정/epoch, sequence 상한, 잠금 owner,
실제 행 조회를 순서대로 실행한다. 증분 읽기 SQL은 종전 1회에서 4회로 늘고 pending은
3회에서 반환한다. 짧은 RAM lock은 상태 publication만 보호하며 DB I/O 중 잡지 않는다.
새 writer SQL·등록·transaction lock·연결·COMMIT 직렬화는 없다. frontier 시간과
captured/remaining owner·pending age는 기존 phase 계측 형식으로 기록하며 추가 sampler는
없다. `sampling_status=not_requested`이므로 wait event가 없었다는 증거는 아니다.

비활성 후보 `2026.10.09-observation-safe-page-v3-79ac4191c0c2b8c7`의
격리 NAS job `46022cd36f6b162693c670582f8e0a93`는 17건 모두 통과했다
(오류·실패·skip 0). native writer의 늦은 COMMIT/독립 peer/rollback,
pending·page limit·공통 bootstrap, CACHE/격리 수준 거부와 순번 역행,
기존 reader context/metrics 및 정상 순서 checkpoint 대조를 확인했다.
RAM state의 stale probe/epoch/backend 재사용 및 SQLite page도 검사했다.
로컬 candidate/mock 인접 회귀 포함 20건 통과. v2의 계측 집계 오류는 누락된 기존
phase 필드를 producer에 복원한 v3로 해결했고 실패 보고서도 보존한다.

증거: `artifacts/observation-safe-page-proof-20261009.json`,
`artifacts/observation-safe-page-report-20261009.json`,
`artifacts/observation-safe-page-v2-failed-report-20261009.json`.
운영 active/두 컨테이너 보존, 진단 idle 및 임시 PostgreSQL cleanup을 확인했다.
원본 red proof는 수정하지 않았다. Consumer bootstrap·구형 checkpoint 복구·protocol
저장과 fast-path 이전/실제 동시 reader·동일 workload reader 비용 gate는 남아 있다.
`runtime_deployed=false`, `source_state_equivalent=false`,
`performance_improvement_verified=false`를 유지한다.

**설계 선택 확정; 운영 reader/writer 수정·배포 완료가 아니다.** 별도 delivery ledger나
writer 전체 COMMIT 직렬화 대신, 기존 sequence 잠금으로 안전한 순번 상한을 확인한다.
저장 순번·revision ID·부모 관계·연구 export의 고정 membership는 바꾸지 않는다.

#### PostgreSQL에 이미 있는 경계

[PostgreSQL 17 sequence 구현](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/commands/sequence.c)의
`lock_and_open_sequence`는 nextval 전에 sequence RowExclusiveLock을 최상위 transaction
소유로 얻는다. 단건 BIGSERIAL 기본값과 batch 명시 nextval 모두 해당한다. savepoint
rollback만으로 이 잠금을 해제하지 않는다. 따라서 별도 writer 등록 SQL은 필요하지 않다.

지원 전제는 primary PostgreSQL 17, logged sequence, CACHE 1, INCREMENT 1, NO CYCLE,
READ COMMITTED, `max_prepared_transactions=0`이다. 연결 권한과 실제 sequence 설정을
검사하며 불일치를 자동 ALTER하거나 빈 정상 page로 숨기지 않는다. 직접 accepted_sequence를
삽입하는 restore/import는 기존 전용 DB fencing 아래에서만 허용한다. 운영 중 임의 setval,
sequence DDL, prepared transaction을 지원한다고 주장하지 않는다.

1. 현재 sequence의 할당 상한 H를 먼저 읽는다. 아직 사용하지 않은 sequence는
   `is_called=false`를 반영한다. 그 **다음 SQL**에서 해당 DB/sequence의 granted
   RowExclusiveLock 소유 `(pid, virtualtransaction)` 집합을 얻는다.
2. 이 유한 집합을 pending fence로 보존한다. 최초 집합에 속한 transaction이 남아 있으면
   새 범위를 전달하지 않는다. 이후 시작한 writer를 집합에 계속 추가하지 않는다.
   기존에 확인한 safe prefix가 있으면 그 범위의 남은 page는 읽을 수 있다.
3. 원래 집합이 끝났음을 확인한 **다음 READ COMMITTED SQL snapshot**으로
   `after_sequence < accepted_sequence <= H`를 기존 정렬/limit에 맞춰 읽는다.
   lock 조회와 행 SELECT를 같은 snapshot의 단일 CTE로 합치지 않는다.
4. consumer cursor는 실제 처리한 행의 최대 순번만 기록한다. rollback으로 빈 번호가
   남아도 기다리지 않으며, 내부 H 자체를 checkpoint cursor로 저장하지 않는다.
5. pending/safe fence는 store의 일시 RAM 상태다. 재시작 시 다시 만들며, DB epoch
   (`pg_postmaster_start_time`, database/sequence identity), 설정 변경, sequence 역행과
   replay restore의 기존 store fencing을 검증한다. 이전 epoch의 경계를 재사용하지 않는다.

H 이하 번호를 확보한 미완료 native transaction은 번호 할당 전에 이미 잠금을 보유한다.
H를 읽은 뒤 조사한 소유 집합이 끝나면 H 이하의 늦은 COMMIT이 더 나타날 수 없다는 것이
이 설계의 근거다. CACHE >1에서는 과거 예약 번호를 다음 transaction에서 사용할 수 있어
이 논증이 성립하지 않는다. 상한과 cohort를 거꾸로 읽어서도 안 된다.

[pg_locks 문서](https://www.postgresql.org/docs/17/view-pg-locks.html)는 전체 순간 snapshot을
보장하지 않는다. 필요한 것은 H 이전부터 계속 보유한 잠금의 누락 방지다.
[PG17 lock 구현](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/storage/lmgr/lock.c)은
backend fast-path를 먼저 읽고 main lock table을 읽으며, 이전 시 main 항목 등록 후 fast-path를
지운다. 따라서 중복 owner는 제거하고 fastpath 값으로 제외하지 않는다. 이 소스 검토와 별개로
구현 gate에서 fast-path 이전/연결 종료 경합을 검증한다. pid만으로 transaction을 식별하지 않는다.
예상하지 않은 stronger sequence lock, 준비된 transaction 또는 identity 이상은 명시 오류로
처리한다. 그런 상태를 writer가 끝난 것으로 간주하지 않는다.

#### reader / bootstrap / 복구의 책임

- `database_observation_readers.py`에 경계와 page 책임을 둔다. 별도 전역 worker나
  publication table을 만들지 않는다. QueryStore 계약은 ready 여부, 실제 rows, safe through,
  exhausted를 구분하는 page와 공통 bootstrap 결과를 제공한다. 기존 list reader를 호환해야
  한다면 같은 안전한 구현으로 연결하고 raw `>` 경로를 소비자에 남기지 않는다.
- 두 consumer가 공유하는 store의 fence 갱신은 짧은 메모리 동기화로 보호한다. DB I/O 동안
  Python lock을 잡지 않고 동일 fence/epoch에 대한 결과만 반영한다. epoch 변경·오래된 probe가
  새 fence를 덮지 못해야 한다. 기존 호출별 DB 연결/transaction 수명은 유지한다.
- 최초 bootstrap은 **하나의 안전한 H와 하나의 행 snapshot** 아래에서 두 kind를 읽는다.
  기존 kind별 최신 5,000행 정책은 유지하되 max cursor도 그 snapshot의 실제 seed에서만 얻는다.
  pending과 정상 빈 DB를 구분한다. 초기화 중 잠금을 기다리며 앱 시작을 막지 않고 WARMUP에서
  다음 기존 poll로 이어간다. 과거 알림/주문 없이 seed하는 기존 정책은 유지한다.
- 이미 안전한 delivery protocol로 저장한 checkpoint는 정상 복원한다. protocol 없는 구형
  checkpoint는 기존 C를 보존한 채 `legacy_frames_recovery`로 들어간다. 안전한 H >= C를
  확인한 뒤, 저장된 관측의 `0 < sequence <= C`를 순번 page로 끝까지 읽어 scratch frames를
  재구성한다. 고정 5,000행/시간 되감기를 완전한 복구로 대체하지 않는다.
- 복구는 기존 strict frame 해석·봉 key 교체·trim 정책만 사용하며 `_consume`의 전략 평가,
  후보/decision 저장, 알림, 주문 dispatch를 호출하지 않는다. 새 범위 >C의 처리는 복구 후에
  재개한다. 별도 과거 실행 결과를 만들어 baseline에 주입하지 않는다.
- 복구 중 기존 checkpoint와 strategy state, emitted keys, mock fill_cursor/seen fills,
  pending intent, 계좌·run/spec binding은 보존한다. mock 체결 반영과 기존 recovery/risk 확인은
  계속한다. page 복구 중 새 주문 판단은 하지 않는다. 정상 종료나 저장 실패 때 부분 scratch를
  완료된 checkpoint로 기록하지 않는다.
- scratch 완성 후 기존 native checkpoint transaction 하나로 frames와 delivery protocol을
  함께 저장한다. C는 그대로이며 H로 건너뛰지 않는다. 성공 전에는 ready로 바꾸지 않는다.
  중단 시 scratch를 버리고 다시 복구할 수 있고, COMMIT 응답 유실 후 재시작도 동일 계약으로
  처리한다. shadow inline/normalized 모두 header의 protocol 정보를 보존해야 한다.
- 복구가 복원하는 것은 **앞으로 소비할 입력의 작업 상태**다. 기존 strategy state와 실제
  실행 원장을 보존하므로 과거 누락이 없었던 전략 상태와 같다는 증명은 아니다.
  `historical_decisions_recomputed=false`와 legacy 복구 출처를 남기며 과거 신호·주문까지
  복구했다고 보고하지 않는다. 기존 risk 불일치 차단을 해제하지 않는다.

#### 비용과 구현 acceptance

2026-10-09 frames-only 복구 구현: `ObservationFrameRecovery`가 기존 C까지의
safe page를 기존 poll당 한 묶음씩 읽는다. scratch의 진행 순번은 소비자 cursor와
분리하며 실제 frame 해석·key 교체·두 consumer 각각의 기존 trim 정책만 적용한다.
strategy state/기존 checkpoint는 마지막 저장 성공까지 바꾸지 않는다. mock의 체결
ledger 반영과 STOP/replaced-control 판정은 계속하며 pending intent 정보를 보존한다.
계좌 monitor는 bundle이 별도로 소유한 수신·복구·risk 작업으로 계속 실행되고, 신규
행의 실제 actionable decision에 대한 recovery/risk 불일치 차단은 그대로 유지한다.
복구 때문에 매 page마다 새로운 계좌 REST 요청을 추가하지 않는다.

완성된 scratch를 기존 checkpoint writer에 override로 전달하여 frames/protocol과
`input_recovery` 출처를 한 transaction에 기록한다. COMMIT 반환 전에는 RAM의 기존
frames/protocol을 바꾸지 않는다. 저장 실패 시 완성된 scratch의 저장만 재시도하고,
재시작 시 durable checkpoint가 구형이면 처음부터 복구, 새 protocol이면 정상 복원한다.
부분 scratch를 종료/error checkpoint에 저장하지 않으며 알 수 없는 protocol은
명시적으로 거부한다. cancellation 중 actual native 복구/저장이 끝나기 전에 close가
반환하지 않는다. mock 복구 poll의 fill/control 읽기도 같은 종료 소유권을 갖는다.

출처에는 target C/실제 마지막 읽은 순번/page·행·frame 수/reader 시간과
`historical_decisions_recomputed=false`를 남긴다. strict 조건상 제외한 frame과
정상 sequence 공백은 전략 평가에 주입하지 않는다. 기존 execution state를 보존하는
복구이며 과거 누락이 없었던 전략 state/주문 결과를 다시 만드는 migration은 아니다.
원래 trim 함수와 복구 reducer는 하나의 순수 함수 구현을 공유한다. consumer→복구
진행 및 consumer→trim 깊이가 한 단계 추가되지만, 기존 writer/주문 경계는 늘리지 않는다.
독립 scratch 수명과 prefix 진행이 새 모듈의 책임이며 단순 전달 계층을 추가한 것이 아니다.

로컬 관련 회귀 36건 통과: 6,004행 bounded 복구, pending prefix, 부분 재시작,
final save 실패/실제 저장 후 ack 유실, unknown protocol, 반복 cancel/실제 작업 drain,
mock fills/STOP/intents/binding 보존과 기존 consumer·supervisor 회귀를 포함한다.
긴 membership 이력의 trim 비용과 전체 종목 수에 따른 scratch RAM은 아직 별도
실측 전이다. 이 로컬 결과나 아래 RAM PostgreSQL gate를 장중 성능 개선으로 해석하지 않는다.

새 writer SQL/잠금/COMMIT는 없지만 reader에 sequence/lock 조회가 추가된다. 같은 sequence를
쓰는 ranking의 장기 transaction도 일시적으로 shadow/mock의 새 prefix 전달을 늦출 수 있다.
이 비용을 숨기지 않고 `fence_pending_age`, captured/remaining owner 수, frontier/query 시간,
page/복구 처리량을 기존 진단 결과에 붙인다. 별도 빠른 polling loop는 만들지 않는다.
일괄 장기 대기 대신 다음 기존 poll에서 확인한다. DB statement timeout/취소는 기존 소유
연결에서 처리하고 타 writer를 취소하거나 강제로 종료하지 않는다.

복구는 poll당 제한된 page로 진행한다. per-code trim이 전체 코드 수의 RAM 상한을 보장하지는
않으므로 scratch 총량·실제 RSS·복구 지연도 측정한다. 긴 과거 이력이 있는 운영 자료에서
처리 시간을 확인하기 전에는 정상 장중 무지연 migration이라고 주장하지 않는다.

구현 순서는 (1) safe page와 epoch/fencing, (2) 공통 bootstrap와 두 consumer 연결,
(3) 구형 checkpoint의 frames-only 복구다. 각 단계는 다음을 통과해야 한다.

- native scalar/batch의 뒤집힌 COMMIT, rollback, savepoint, 연결 종료, 동일 backend 재사용,
  새 writer가 끊임없이 생기는 동안 고정 cohort의 진행; 낮은 allocation이 INSERT 전인 경우.
- page limit/빈 page/필터 kind/한 transaction 여러 chunk; fast-path 이전, concurrent reader,
  restart/restore/설정 이상은 누락 없이 처리하거나 명시 거부.
- 하나의 snapshot으로 multi-kind bootstrap, 미완료 bootstrap 중 재시작, 초기 빈 DB,
  shadow와 mock 각각의 polling/checkpoint; 기존 frozen red 보고서는 그대로 보존.
- 구형 checkpoint의 C 이하 누락 frames, C 이후 정상 처리, 복구 중 실패/종료/ack 유실,
  inline/normalized, mock fills/intents 보존과 과거 주문·알림·decision 추가 0건.
- 동일 녹화본의 scoped replay로 저장 내용/순서/부모 관계를 보존하고 reader 추가 비용과
  consumer 지연을 비교한다. 부분 입력 결과를 전체 장초 개선으로 일반화하지 않는다.

설계 probe 결과: 비활성 `2026.10.09-sequence-fence-proof-v1-4c356d3ccf73e70f`,
격리 NAS job `4fc918b5d36ccaab992b5c55f512632a`, tests=4/errors=0/failures=0/skipped=0.
native batch의 낮은 COMMIT 대기 중 높은 peer 독립 COMMIT, scalar 기본값과 batch 혼합,
native rollback, INSERT 전 nextval/savepoint rollback/같은 backend의 다음 transaction을
검증했다. 기존 cohort 종료 후 새 writer가 아직 대기 중이어도 원래 범위를 읽었다.
테스트 내부 probe일 뿐 실제 consumer와 legacy recovery 구현 acceptance는 아니다.
RAM PostgreSQL이므로 운영 저장장치 성능 증거도 아니다. 원본 runtime 파일은 build marker
외에는 바꾸지 않았고 운영 active/container 보존, 임시 job cleanup을 확인했다.
보고서: `artifacts/sequence-fence-report-20261009.json`.

### 커서 수정 전 writer/consumer 계약 감사 (2026-10-09)

아직 runtime 수정이나 해결 설계의 확정이 아니다. 기존 실패 원인과 수정이 필요한
경계를 다음과 같이 좁혔다. 저장 주기/batch 성능 후보와 분리된 정합성 작업이다.

| 경계 | 확인한 코드/계약 | 수정 시 보호할 사항 |
|---|---|---|
| 단건 revision | `database_observation_writes.py`의 `_insert_postgres_observation_revision`은 BIGSERIAL 기본값으로 순번을 할당 | 호출자 소유 transaction, 중복 hash 검사, revision_of, rollback/ack 재시도 |
| batch revision | `database_market_bars.py`의 `_insert_postgres_observation_revisions_batch`는 nextval로 입력 순서의 번호를 먼저 확보하고 명시 INSERT | 모든 chunk와 반복 key의 부모 관계; 실제 COMMIT은 여전히 호출자 소유 |
| 생성자 | dataset snapshot의 ranking/top20_membership와 minute 저장/확정이 위 두 helper를 사용 | 소비자가 요청한 kind와 무관한 writer 때문에 불필요하게 장기 정지하지 않도록 평가 |
| 증분 reader | `database_observation_readers.py`는 `accepted_sequence > cursor`를 읽음 | 다른 연결의 늦은 COMMIT을 누락하지 않는 진행 경계, page limit, 빈 page, 독립 writer |
| shadow | `CandidateMonitor.run_once`가 max 순번을 checkpoint; `_restore_or_bootstrap`도 kind별 최신 5,000건의 max를 초기 cursor로 사용 | polling뿐 아니라 최초 bootstrap, 저장 실패, 기존 checkpoint, inline/normalized 재시작 |
| mock 자동화 | `MockAutomationRunner.run_once`와 `_restore_or_bootstrap`도 같은 max 순번 패턴 | input_cursor와 별도 fill_cursor를 혼동하지 않기; 과거 누락 복구가 역사적 주문을 재실행하지 않기 |
| 연구 export | `database_research_export.py`의 고정 revision membership와 source available_at 순서 | 기존 accepted_sequence/UUID/부모 연결/시각과 고정 dataset을 변경하지 않기 |

PostgreSQL의 [sequence 계약](https://www.postgresql.org/docs/current/functions-sequence.html)은
rollback 시 nextval 번호를 회수하지 않으므로 번호 공백을 정상 상태로 다룬다.
락을 추가한다면 [공식 locking 계약](https://www.postgresql.org/docs/current/explicit-locking.html)의
충돌·수명·메모리 예산도 검증해야 한다. 공식 문서를 확인했다는 사실은 아래 후보의
정합성이나 성능을 검증한 증거가 아니다.

숫자 cursor 하나에는 처리된 revision ID나 미완료 transaction 정보가 없다. 따라서
이미 높은 순번까지 진행한 뒤 reader의 `>` 조건만 수정하는 것으로는 처리 완료 행과
늦게 나타난 행을 정확히 구별할 수 없다. 후보는 두 계약을 비교해야 한다.

1. **안전한 순번 경계:** 미완료 allocation보다 앞까지만 전달한다. 기존 immutable
   accepted_sequence와 consumer 처리 순서를 보존하는 방향이다. 모든 단건/batch의
   순번 확보와 미완료 등록 사이 race, COMMIT/rollback/연결 종료, reader snapshot,
   다중 kind bootstrap을 함께 설계해야 한다. 전체 writer COMMIT를 직렬화하지 않고
   경계를 알아낼 방법과 장기 transaction의 consumer 지연을 먼저 검증해야 한다.
2. **별도 전달 cursor/ack:** 저장 순번과 전달 진행을 분리해 늦은 행을 추적한다.
   consumer/checkpoint 버전과 durable state의 책임이 추가된다. accepted 순서와 다른
   전달이 전략 상태·시각 판단·중복 실행에 미치는 영향, 복구/마이그레이션 비용을 비교한다.

현재 우선 검토안은 1번이나 구현으로 확정하지 않았다. 전체 writer COMMIT 직렬화,
고정 시간/건수 되감기, rollback 공백을 기다리는 단순 연속 번호 검사, 모든 writer가
조용할 때만 읽는 무기한 idle 의존 방식으로 문제를 덮지 않는다. row xmin을 기준으로
일부 행만 통과시키는 방식도 transaction 시작/번호 할당 순서의 차이를 해결했다는
증거 없이 채택하지 않는다. 이들은 설계 검토 조건이며 실측한 성능 결과가 아니다.

최초 시작도 native 재현했다. 비활성
`2026.10.09-bootstrap-cursor-proof-v1-c23bc19ad1f24cf3`의 격리 NAS gate는
정상 대조 1 PASS/초기 bootstrap 중 지연 COMMIT 계약 1 FAIL(오류/skip 0)이다.
아직 낮은 순번의 native finalizer가 COMMIT 대기 중일 때 새 CandidateMonitor가
높은 peer를 bootstrap해 cursor에 저장하면 이후 낮은 적격 완료봉을 누락하고 같은
checkpoint로 재시작해도 복원하지 못한다. 두 COMMIT 뒤 생성한 별도 새 monitor는
둘 다 bootstrap한다. 기본 inline layout이며 운영/주문/실제 mock runner 검증은 아니다.
실패 gate를 유지한 report와 요약은 `artifacts/bootstrap-cursor-report-20261009.json`,
`artifacts/bootstrap-cursor-proof-20261009.json`이다. 활성 source는 보존했고 작업은 정리됐다.

최초 bootstrap은 kind별 별도 연결로 읽으므로 같은 시점의 multi-kind snapshot이
보장되지 않는다. kind 조회 사이 새 입력이 발생하는 경우의 손실 가능성은 코드상의
추가 검증 후보이며 아직 native 재현하지 않았다. 이미 누락한 구형 checkpoint의
복구 정책도 별도 결정해야 한다; 향후 전달을 고치는 것만으로 과거 상태 복구를 주장하지 않는다.

기존 고정 소스의 red reproducer는 높은 peer 순번을 먼저 반환하도록 현재 동작을
명시해 실패를 드러낸다. 안전 경계 계약을 선택한다면 그 중간 동작은 달라질 수 있다.
원본 red evidence는 보존하고, 새 acceptance에서는 해당 계약에 맞는 안전한 cursor
진행을 검사하되 **독립 peer COMMIT, 지연 해제 후 모든 적격 입력 처리, 부모 관계,
중복 방지와 재시작 결과 보존**은 반드시 유지한다. 실패를 expectedFailure/skip으로
바꾸거나 최종 적격 입력 포함 assertion을 삭제하는 것으로 통과시키지 않는다.

필수 후속 gate는 reversed COMMIT/정상 대조/rollback 번호 공백/할당 전후 지연/
batch chunk와 page 경계/다중 kind bootstrap/checkpoint 실패·재시작/COMMIT ack 유실이다.
수정 후 같은 scoped recorded workload로 저장 내용과 시간·queue 부담을 비교한다.
새로운 전달 상태/락/마이그레이션의 계약 선택을 끝내기 전 운영 배포하지 않는다.

### 확인된 실행 경로와 원본 증거

완료봉/consumer 후속 재현도 전후 두 소스에서 동일하게 실패했다. 새 controlled fixture는
진행 중 두 종목의 분봉을 실제로 저장한 뒤, native finalize_minute_bars의 첫 COMMIT만
지연한다. 다른 종목이 먼저 확정되고 실제 CandidateMonitor.run_once가 그 높은 순번을
checkpoint한다. 첫 종목을 COMMIT한 뒤 strict krx-regular/v1 reader에는 적격 완료봉이
있으나 기존 monitor의 frame/checkpoint와 같은 checkpoint를 복원한 monitor에는 없다.
별도 새 monitor의 DB bootstrap은 두 완료봉을 모두 읽는다. 정상 커밋 순서 대조는 둘 다
처리·복구한다. 기본 inline checkpoint, 독립 native transaction과 모든 실제 SQL을 사용했고
collector/주문/네트워크 입력을 합성해 운영 서버에 넣지 않았다.

비활성 reference `2026.10.09-closed-cursor-reference-v2-d1ff934911bfe39c`와 candidate
`2026.10.09-closed-cursor-candidate-v2-64e6fb148a8c28da` 각각 control 1 PASS/desired
contract 1 FAIL, errors/skipped 0이다. operator가 다중 행 assertion의 error_type을 unknown으로
정제했지만 failures=1/errors=0과 정확한 최종 assertion 위치를 대조했다. FAILED gate를
성공으로 바꾸지 않는다. 최초 v1의 legacy profile fixture 오류는 별도 report에 남겼으며
후속 결론에 포함하지 않는다. 요약 `artifacts/closed-cursor-comparison-20261009.json`.
이는 기존 전달/checkpoint 정합성 결함의 실제 클래스 재현이다. 실제 장중 신호·주문 누락,
MockAutomationRunner 및 normalized checkpoint 영향은 검증하지 않았다. 원본 trace,
accepted_sequence, API, writer transaction, 운영 source/DB를 바꾸거나 배포하지 않았다.
다음 해결 설계는 이 실패 계약과 정상 대조를 고정 입력으로 유지하고 독립 transaction,
rollback 공백·retry·checkpoint 복구·실시간 우선순위를 함께 보호해야 한다.

분봉 consumer 후속 검증: 로컬 72건과 비활성 minute-consumer NAS gate 8건이
skip 없이 통과했다. native 순번 reader, source available_at cutoff, 반열린 export
구간, 새 store/retry/rollback을 검증했다. received_at와 processed_at를 늦춰도
같은 source/sequence 결과가 보존되며 available_at를 바꾸는 negative control은
결과를 바꾼다. 이 제한된 검증으로 broad-equivalence flags를 true로 올리지 않는다.

별도 커밋 순서 desired-contract는 전후 두 소스 모두 실패했다. 첫 native writer가
번호를 할당한 뒤 COMMIT에서 잠시 멈춘 사이 다른 subject writer가 먼저 COMMIT하고,
public 증분 reader가 높은 번호까지 진전하게 했다. 첫 writer를 실제 COMMIT한 뒤
public 전체 reader에는 낮은 번호의 revision이 있으나 증분 reader에는 나오지 않았다.
SQL/transaction 소유권은 바꾸지 않고 psycopg connection subclass의 COMMIT 경계만
통제했다. final assertion 실패는 지우거나 expectedFailure로 바꾸지 않았다.
pre/post 각각 tests=1/failures=1/errors=0/skipped=0이고 격리 작업은 정리됐다.
요약 `artifacts/cursor-visibility-comparison-20261009.json`은 acceptance_state=failed를
유지하며 production_fix_applied/live_incident_confirmed=false로 범위를 명시한다.

이 문제는 현재 진행 중 분봉을 이용한 증분 전달 정합성 재현이며 성능 비교가 아니다.
CandidateMonitor와 MockAutomationRunner가 이 reader의 max accepted_sequence를
checkpoint하는 호출 경로는 확인했다. 하지만 완료봉·실제 consumer/restart 누락까지
재현하지 않았으므로 매매 영향은 미확정이다. 다음 단계에서 그 경계를 확인하고,
독립 writer/transaction을 유지하는 해결 방법을 판단한다. 원본 trace의 거부 입력을
채워 넣거나 전체 writer COMMIT를 직렬화해 이 문제를 숨기지 않는다.

현재 NAS operator는 `nas_operator.py → nas_operator_worker.py:replay →
diagnostic_replay_database_cli.py:_run_trace → diagnostic_trace.recorded_window_events →
diagnostic_recorded_execution.run_owned_recorded_experiment`를 호출한다.
이는 실제 저장 함수 인자의 native replay다. 별도 `diagnostic_replay.py`의 합성 shape/news
adapter 지원을 이 경로의 지원으로 해석하지 않는다.

원본은 `20261007T235957Z-e8cb574bf964`, source release는
`2026.10.08-trace-ram-8g-5m-v1-e1cc01dde5bacbb9`다. manifest SHA-256은
`4142345fdc585125be9513cbef2cdaddfedc545dc4be9545e6d3936ba11afc9e`다.
1,490개 chunk의 checksum/sequence와 accepted=written=last_seq=2,033,667,
known_dropped=0을 읽기 전용으로 확인했다. input_rejected=1,188 때문에 원본 상태는
`incomplete`다. 상태를 complete로 바꾸거나 거부 사건을 삭제하지 않는다.

상세 결과는 `artifacts/oct8-scoped-replay-preflight-20261009.json`, 재현 조사기는
`artifacts/inspect_oct8_replay_scope.py`다. 이 조사기는 선택 payload checksum/codec/native
signature와 plan을 확인했지만 DB 연결이나 저장 함수 실행은 하지 않았다. sparse sequence
검증용 내부 옵션을 조사 과정에서 사용한 결과는 실행 승인서가 아니다.

정확한 09:00~09:10 KST는 capture offset 2.8988919258117676~602.8988919258118초다.
capture 시작은 08:59:57.101108 KST이며, 종료 범위는 monotonic duration으로 판정한다.
장후 persistence의 wall-clock finished_at을 capture 길이로 사용하지 않는다.

| 해당 10분의 owner/workload | 기록된 operation start | 거부 | 현재 native 실행 제약 |
| --- | ---: | ---: | --- |
| external_market | 12 | 0 | save_external_bars 지원 |
| rest_market | 171 | 0 | document read/upsert 지원; REST 분봉 저장 전체를 뜻하지 않음 |
| top20_candidate_flow | 23 | 0 | document/dataset 경계 지원 |
| realtime | 101 | 1 | second-bar 입력 거부를 포함하는 전체 구간은 불가 |
| top20 | 1,853 | 99 | replace_minute_bars 52, replace_daily_bars 47 인자 거부 |
| shadow | 383 | 63 | checkpoint 인자 거부와 evaluation/observation adapter 미지원 |
| news | 336 | 0 | claim_news_jobs native adapter 미지원 |
| market_events | 215 | 0 | VI/upper-limit/hot-cohort native adapter 미지원 |
| readers | 659 | 0 | 일부 control/shadow reader adapter 미지원 |
| documents | 30 | 40 | unsupported_document_collection 거부 |
| unsupported | 39 | 별도 | 계좌 recovery/runtime lease 등 의도적 실행 제외 |

거부된 분봉 52건·일봉 47건·shadow checkpoint 63건·초봉 1건은 operation_id와 DB span의
input_operation_id를 연결한 결과 **163건 모두 native operation 반환 및 DB COMMIT 완료**였다.
이들은 녹화 인자 복사 accounting의 8 MiB 제한 초과이며 운영 저장 실패 증거가 아니다.
accounting bytes를 DB payload·네트워크 크기·RSS와 동일시하지 않는다. 같은 연결 기록의 COMMIT
최댓값은 분봉 18,071.270ms, 일봉 7,309.933ms, shadow 20,892.525ms, 초봉 6,819.736ms다.
지연 자체는 관측됐지만 이 수치만으로 대기 원인이나 writer 간 인과관계를 확정할 수 없다.

### 선택한 첫 실행 범위

1. **09:00~09:02 KST, realtime만:** 18 operations, 알려진 actor 1개,
   payload 18개/447,397 bytes. latest·minute·finalize·second·dataset/document 경계 포함.
2. 첫 범위가 실행/복구 gate를 통과하면 **09:04~09:06 KST, realtime만:** 23 operations,
   알려진 actor 1개, payload 23개/1,172,848 bytes. 원본 지연 시간대의 해당 writer 부분 비교다.
3. 09:00~09:01 혼합 범위는 binding은 통과했지만 42 actors 중 원인 actor 불명 항목이 있어
   동시성 충실도 판단을 보류한다. 10분 external_market/rest_market/top20_candidate_flow 조합은
   기존 64 actor cap에 걸린다. 이를 통과시키려고 cap을 올리거나 actor를 임의로 합치지 않는다.

이 범위는 입력 누락 없는 **선택된 저장 경계**의 재현 후보다. upstream collector/timer 변경이나
전체 장초 부하를 검증하지 않으며, 다른 writer를 제외했으므로 원본 경합이 사라질 수도 있다.

### 구현 결정: 명시적 부분 허용, 기존 strict 경로 보존

- operator trace 등록에 `--capture-policy scoped-operations`를 추가한다. 기본은 기존 complete
  정책이다. 원본 manifest/chunk/blob을 그대로 root 소유 등록 영역에 고정하고 policy와 원본
  manifest hash를 registration에 기록한다. 등록 성공과 선택 범위 실행 가능 여부를 분리한다.
- host 등록은 schema 2/3의 durable terminal metadata와 원본 파일 hash를 검사한다. 전체 event
  sequence/rejection accounting 및 선택 입력 자격은 격리 worker의 reader가 실행 전에 검사한다.
  등록 성공 자체를 실제 replay 성공이나 전체 coverage로 해석하지 않는다.
- scoped reader는 schema 2/3의 durable terminal complete/incomplete만 허용한다. accepted/written/
  last_seq, 전체 chunk/sequence/hash, rejection accounting, capture clock을 검사한다. dropped,
  censored, retained/pending/queued/packing/copy 예약 상태, persistence 실패, 불명확한 종료나
  불일치는 거부한다. incomplete는 기록된 input rejection으로 설명되는 경우에만 허용한다.
- 실행에서도 scoped policy와 명시적 include-workload가 필요하다. 첫 구현은
  `recorded_operations`만 허용한다. incomplete collector/전체 TOP20 causal 실행은 확장하지 않는다.
- `scoped-operations`는 선택 owner의 window 내 input rejection이 하나라도 있으면 실패한다. attribution/time을 해석할
  수 없는 rejection도 실패한다. 미지원 method는 기존대로 실패하며 임의의 입력·결과로 대체하지
  않는다. 제외 workload 목록과 해당 rejection 수를 결과에 표시한다.
- 기존 complete trace, raw in-memory fixture, collector descendant suppression, strict TOP20
  frontier, 합성 replay 계약은 유지한다. 원인 replay에 과거 descendant writer를 추가하지 않는다.

### 병목 탐색용 partial-operations

완전 재현 확인과 누락이 있는 부분 부하 실험을 구분한다. 사용자는 녹화 인자 거부가 발생한
구간에서도 확보된 호출을 실행하여 병목을 찾는 것이 우선이라고 명시했다. 이에 명시적
`partial-operations` 등록/실행 정책을 추가한다. 기존 complete/scoped 기본 검증을 바꾸지 않는다.

- 선택 workload의 알려진 rejection은 원본 seq/time/operation/workload/method/reason/detail을
  `omitted_inputs`에 보존한다. projection에도 `original_event_type=input_rejected`를 남긴다.
- 인자가 없는 호출은 실행하지 않는다. 동일 operation ID의 start가 따로 있더라도 거부와 연결되면
  payload와 native call spans를 재생에서 제외한다. 과거 결과나 가짜 입력으로 상태를 맞추지 않는다.
- 원본 순번·체크섬·durable/drain·paired valid inputs·actor·isolated DB·실제 drain/restore 검사는 유지한다.
- 보고서는 `omitted_input_count`, `selected_input_complete=false`,
  `missing_load_reconstructed=false`, `fidelity=surviving_native_operations_only`를 명시한다.
  동일 살아남은 입력/선택/hash/profile로 수정 전후를 비교한다. 누락 부하와 원본 DB 초기 상태의
  차이 때문에 전체 장초 peak 또는 실제 운영 성능 개선으로 일반화하지 않는다.
- 모든 선택 입력이 거부되어 실행할 호출이 없거나, 실제 남은 입력이 손상/미지원/짝 불일치이면
  해당 실험은 실행하지 못한다고 표시한다. 일부 누락을 완전성 실패로 확대해 전체 녹화본을
  버리지 않는다. collector 원인 사건의 causal frontier 완화는 이 store-only 변경에 포함하지 않는다.

### bounded reader와 실행 증명 경계

기존 strict `recorded_window_events`는 전체 scalar event를 보관하고 선택 전 payload를 hydrate하며,
chunk/blob마다 큰 manifest를 다시 읽는 경로가 있다. scoped reader는 다음으로 제한한다.

1. manifest를 한 번 고정하고 `_top20_source_rows`, `_chunk_bytes_from_manifest`,
   `_payload_bytes_from_manifest`의 checksum 경로를 재사용한다. 전체 sequence/counter 검증은
   streaming으로 하고 마지막에 원본 manifest hash 불변을 확인한다.
2. 선택 window/owner의 operation과 matching end, 연결된 native call_start/call_end,
   rejection/coverage 근거만 보관한다. window 밖에서 끝나는 선택 operation도 짝을 확인한다.
   source seq, operation/call ID, 시각은 재번호화하지 않는다. 제외 payload는 hydrate하지 않는다.
3. retained scalar에 50,000 rows/32 MiB 상한을 둔다. 기존 payload 32 MiB, operation 4,096,
   actor 64 제한은 유지하고 초과를 명시적으로 보고한다. 전체 수백만 event list를 만들지 않는다.
4. 검증된 sparse projection은 private proof에 manifest hash·선택 조건·event digest를 묶어서
   runner에 전달한다. CLI/입력 JSON에 sequence 검사를 끄는 boolean을 노출하지 않는다.
   `run_owned_recorded_experiment`와 `_execute_recorded_operations`의 두 plan compile 모두 같은
   proof를 검사한다. raw list에는 기존 연속 sequence 검사를 그대로 적용한다.

### 첫 기준 상태: 명시적 controlled empty fixture

09:00 시점의 운영 DB snapshot은 없다. 이전 임시 fixture baseline ID를 현재 원본 상태와
동일하다고 사용하지 않는다. 첫 실험은 fresh isolated PostgreSQL에서 고정된 empty-v1 baseline을
만들고 `source_state_equivalent=false`를 끝까지 유지한다.

- operator에 `--baseline-profile empty-v1`을 기존 `--baseline ID`와 상호 배타적으로 추가한다.
  fresh dedicated DB에서 기존 provision/seal 함수를 이용한다. 등록 baseline 경로는 그대로 둔다.
- `--preflight-only`는 원본 선택·native binding·baseline seal 결과 ID까지만 확인한다. 녹화 작업은
  실행하지 않으며 `--pause-operational`과 함께 사용할 수 없다.
- 실제 profile replay는 preflight가 반환한 `--expected-baseline-id`를 필수로 받는다. 새 격리 DB에
  같은 profile을 seal한 ID가 일치해야 실행한다. 원본 결과/제외 workload의 과거 출력을 seed하지 않는다.
- 같은 입력/선택/코드/profile로 최소 3회 비교한다. 매 실행 전후 restore와 sequence를 검증한다.
  generated ID/실제 처리시각이 포함된 final hash의 바이트 동일성을 기능 회귀 조건으로 강요하지
  않고 해당 저장 함수의 의미 있는 결과·revision·rollback·재시도 계약을 비교한다.
- 이는 empty-state 부분 진단이다. 원본 UPDATE가 fixture INSERT로 바뀔 수 있으며 실제 운영
  상태나 전체 앱 성능 개선으로 일반화하지 않는다. 이후 source-state baseline 확보는 별도 과제다.

실제 성능 실험은 NAS `--storage disk --pause-operational`을 사용하되 기존 앱 정지→격리 job→
drain/cleanup→동일 앱 복귀 순서를 보존한다. 운영 PostgreSQL은 정지하지 않는다. RAM PG는 기능
gate용이며 디스크 병목 비교에 쓰지 않는다. 현재 worker 4 GiB, PG 768 MiB/shared_buffers 16 MiB,
고정 CPU 제한 등 실험 자원 조건도 보고해 운영과의 차이를 숨기지 않는다.

### 구현 순서와 통과 조건

1. scoped reader/proof/plan 회귀: 선택 거부·불명 attribution·gap/hash/censored/미완료 실패,
   제외 payload 미접근, 원본 seq/call 연결, window 이후 end 보존, sparse proof 변조 실패,
   기존 strict/raw/collector 계약 유지. 대량 비선택 event에서 retained memory가 bounded인지 확인.
2. operator 등록/worker/CLI additive 정책과 empty-v1 preflight를 구현한다. malformed source는 native
   DB 실행 전 거부하고 baseline 불일치는 replay 전 차단한다. preflight가 앱을 정지할 수 없음을 검사한다.
3. 임시 NAS PostgreSQL gate에서 첫 두 범위의 typed native 인자·결과·복구를 확인한다. 실패/취소/
   COMMIT ack loss에도 실제 native drain 후 restore/cleanup, 이후에만 운영 앱 재시작을 검증한다.
4. helper 업데이트와 비활성 실험 release 등록 후 첫 2분 scope를 preflight하고 실제 3회 실행한다.
   운영 앱 release 교체는 필요 없다. helper 정책 변경은 기존 검증된 updater로 별도 적용한다.

보고서는 원본 trace state/hash, 선택 window/owner, 제외/거부/미지원 수, payload hash/bytes,
actor fidelity, source→replay operation/DB call 연결, baseline ID/profile, source_state_equivalent,
실행 source hash와 자원 제한, SQL/COMMIT/전체 처리시간·schedule 지연을 포함한다. DB/cluster 구간
전체 WAL 통계를 특정 writer가 만든 WAL로 표기하지 않는다. capture gate 개발이나 대규모 계측
추가로 첫 부분 replay를 다시 미루지 않는다.

2026-10-05 · O12 · 설계 확정, 원본 capture·window reader·내부 recorded executor를 로컬 구현

### 2026-10-07 collector 입력 확장 후속 상태

활성 NAS source `2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f`를 기반으로 `collector-input/v2`
후보 `2026.10.07-trace-market-inputs-v6-2597a00f99c13b50`를 게시했다(892 files,
clean working tree, `active_changed=false`). v6는 0B/0w/0J/0U 최소 parser payload, 같은 collector
component의 market-state sink 대체, capture와 execution에서 각기 올바른 전용 DB를 사용하는 grouped
acceptance runner를 포함한다. 정확한 후보에서 collector/replay 단위검사 35건이 통과하고 build 및
입력 버전 import를 확인했다. NAS PostgreSQL gate 7건(capture invariant 4건 + recorded execution
3건)은 19.215초에 `skipped=0`으로 통과했다. baseline ID는
`4c4daa238a7e7d4221234087dce35a5b0956caf6629b05896fca7e8df175bbd4`이고 fixture는 controlled,
`source_state_equivalent=false`다. 따라서 실행경계·정합성 검증이지 실제 장중 상태 등가나 성능 개선
증거는 아니다. NAS active pointer, 운영 DB와 진단 제어는 controlled overhead 측정에서 변경하지
않았다. 이후 사용자가 2026-10-07 v6를 활성화했다. 배포 출력에서 build/release 일치와 database
container unchanged를 확인했다. 후속 공개 health는 `WAITING_MARKET`, `observation_expected=false`를
반환했다. 이는 장외 대기이므로 실시간 구독·수신 검증은 아니다. capture 및 verification 예약은 v6 build와 0B·0w·0J·0U 입력 지원을 검사하도록
갱신했다. 다음은 현재 active와
v6 후보를 Windows의 격리된 프로세스에서 capture OFF/ON으로 3회씩 비교했다. 0B profile은 라운드당
100개 WebSocket 모양 메시지와 1,000건 체결, mixed는 같은 0B와 0w/0J/0U 각 100건을 사용했다.
각 실행에서 RAM 집계·hub event 수·12개 격리된 in-memory SQLite 저장 결과가 서로 일치했고,
drop/reject/written은 모두 0이었다. capture copy는 0B 1,000행에서 라운드당 24–40ms, mixed에서
28–40ms였다. 입력 처리 paired p50 차이 중앙값은 active snapshot에서 0.34/0.40ms per message,
v6에서 0.37/0.36ms(0B/mixed)였다. loop p50의 증가 방향은 반복 간 일치하지 않았고 Windows OS
스케줄 지연 outlier가 있었다. trace charged bytes는 3.6–4.4MiB, memory high-water는 11.6–12.3MiB였다.
후속 NAS 측정(2026-10-07)은 NAS Linux의 격리 benchmark 프로세스 RSS를 before/after로 측정했다(컨테이너 전체 cgroup 사용량·여유는 미측정). active v1과 v6 각각 0B/mixed·capture OFF/ON·3회, 총 24조건이 `state=passed`, `functional_fixture_parity=true`였으며 drop/reject/written은 모두 0이었다. capture ON의 paired producer p50 증가는 active에서 0.462ms/0.467ms, v6에서 0.472ms/0.557ms per message(0B/mixed)였다. 100개 입력 표본당 copy 누적 중앙값은 36.1~44.4ms, trace charged bytes는 3.58~4.38MiB, trace high-water는 11.55~12.34MiB였다. child process RSS 증가는 중앙값 0B 약 1.67MiB, mixed active 약 1.95MiB, mixed v6 약 2.25MiB였다. loop p95 변화는 반복 간 일관되지 않아 이 fixture에서 뚜렷한 loop starvation 근거는 없지만, 일반화할 수 없다. 상세 결과는 `artifacts/capture-overhead-20261007-nas.json` 및 PC 비교 `artifacts/capture-overhead-20261007-partial.json`에 있다. 테스트는 parser/collector RAM 경로와 in-memory SQLite store만 포함하며 PostgreSQL, 실제 WebSocket task, 파일 persistence, 65분 지속 capture, 개장 부하를 포함하지 않는다. 따라서 `performance_acceptance=not_decided`, `intraday_baseline=false`이며 이 fixture는 v6 성능 승인이나 성능 개선 완료의 근거가 아니다. 사용자는 이후 v6를 활성화했다.

TOP20·뉴스·REST 원인 replay와 cross-component 인과 replay도 미지원이다.

### 2026-10-07 TOP20 순위 원인 입력 경계 확인

정적 호출 경로에서 TOP20 순위의 원인 입력은 WebSocket 사건이 아니라 broker TR `ka00198`
응답이다. `AutonomousTop20Service.refresh_ranking_once()`는 `request_unrecorded()`를 우선 사용해
응답을 받아 시각 freshness와 20개 슬롯 완전성을 검사하고, 오래됐거나 부분 응답이면 제한된
재시도를 한다. 유효 응답만 메모리 membership으로 먼저 공개한 뒤 collector 구독 대상을 바꾸고,
membership snapshot·daily entrants를 저장하며 기본정보·보조 순위·시장지수 준비 작업을 예약한다.
`request_unrecorded()`는 stale/partial 후보가 영속 query cache에 기록되지 않게 하는 경로이므로,
이를 일반 `request()`로 바꾸는 것은 보존된 동작이 아니다.

현재 v6의 `collector_input`은 0B/0w/0J/0U WebSocket parser 입력이다. 0B/0w를 기록해도 TOP20
service의 subscriber, 순위 TR 응답, freshness/partial 재시도, membership publish, 준비 작업 예약은
실행되지 않는다. store-input에 `top20_membership` 호출이 있어도 이는 결과 저장 경계만 재생하며
순위 계산 원인을 재생하지 않는다. 따라서 TOP20 순위·membership은 분류 **2 + 4**(저장 호출은
재생 가능, cause input은 미기록)다.

원인 재생을 추가한다면 broker 경계에서 허용된 논리 요청과 각 응답 시도를 함께 보존해야 한다:
API ID/body/continuation, source request ID와 시각, 순서, 응답 payload 또는 정규화된 필드,
transport/API 오류, snapshot 시각 및 재시도 구분. raw HTTP bytes와 인증 정보는 필요하지 않다.
재생 broker는 외부 네트워크를 사용하지 않고 기록된 응답을 같은 순서로 공급하며, 기록에 없는
요청은 조용히 생략하지 않고 실패시켜야 한다. 새 코드가 만드는 membership/구독/준비 후속 동작을
과거 store 결과로 중복 주입하지 않도록 source request→generated operation provenance와 동일
component descendant 대체 규칙도 필요하다. 이는 broker/service/replay 계약 변경이라 내일 예정된
v6 capture를 위해 급히 구현·배포하지 않는다. 내일 capture는 현재 명시된 WebSocket 입력의 부분
trace이며 TOP20 원인 경로가 수집됐다고 해석하지 않는다.

### 2026-10-07 뉴스 원인 입력 경계 확인

뉴스 service가 구성되는 조건을 만족하면 app lifespan이 `news_service.start()`를 호출한다. UI가
열려 있어야 하는 구조는 아니다. 다만 개별 외부 요청은 운영 설정과 pause gate에 따라 달라진다.
`CentralNewsService`의 300초 기본 refresh loop는 최근 TOP20 membership을 읽어 종목별 Naver
API/선택적 Naver Stock/DART 수집을 시도한다. 운영 모드는 `read_only_search=True`라 UI 검색은
외부 공급자 호출 대신 저장 결과를 읽지만, 자동 순위 refresh는 별도 background 경로다.
query-set collector는 설정된 검색어별 Naver 페이지를, market-feed collector는 flash/world
페이지를 자체 cursor와 예약 시각에 따라 조회한다. 구성된 뉴스 service에서는 job runner도
startup 때 시작해 BODY/RULE 단계 worker가 영속 queue를 처리한다. BODY 단계는 기사 URL을
가져오고, 빈 큐 polling은 DB claim이므로 외부 뉴스 fetch와 구분한다. `close()`는 이 loop와
worker를 종료한다. 실제 현재 NAS의 source toggle과 실행 횟수는 이번 정적 코드 확인으로 단정하지 않는다.

현재 trace의 0B/0w/0J/0U 입력은 뉴스 collector로 전달되지 않는다. 외부 Naver API/Stock/DART
검색 결과, query-set/market-feed 페이지 결과, 기사 본문 fetch 결과, 실패·retry outcome은
`collector_input`으로 기록되지 않는다. `save_news_source_page`, job queue, revision 등 일부
store operation은 store-input recorder allowlist에 있어 호출 payload 관측은 가능하지만,
현재 recorded executor의 native method allowlist에는 없어 실행 가능한 replay가 아니다.
일반 `news_*` document 호출 일부는 native replay될 수 있어도 공급자 결과·cursor 판단 원인을
재생하지 않는다. 따라서 뉴스 전체는 **2 + 3 + 4**: 일반 문서 저장 일부는 store 경계(2),
뉴스 native store 호출은 기록되더라도 executor 미지원(3), 핵심 외부 결과와 retry/clock 입력은
미기록(4)이다.

향후 최소 cause-input은 공급자별 논리 요청/결과 페이지(검색 query 또는 종목, page/cursor,
요청 시각, 파싱된 기사 필드, pagination/coverage 결과, 오류·retry), BODY fetch의 결과 텍스트와
publication metadata 또는 실패 상태, 그리고 owner/trigger/run ID다. 인증정보·원본 HTTP headers는
제외한다. NEWS input을 재생하면 그 실행에서 생성되는 `save_news_source_page`, article/body
revision, jobs 및 후속 결과를 과거 store operation에서 중복 주입하면 안 된다. 생성 ID와 source
input provenance, cursor·clock 재현, 해당 descendant 선택/제외 계약이 준비되기 전에는 뉴스
단독·제외·조합 replay를 지원한다고 표시하지 않는다.

### 2026-10-07 Kiwoom REST/TR 원인 입력 경계 확인

Kiwoom 호출은 `RestBroker.request()` 또는 `request_unrecorded()`에서 검증된 뒤 priority queue와
단일 broker worker를 통과한다. 같은 논리 요청은 RAM cache/in-flight dedup에서 합쳐질 수 있고,
API별 TTL이 있으면 persistent query cache를 확인한 후 외부 요청을 생략할 수 있다. 정상
`request()`의 응답은 background persistence queue에서 response handler와 `save_query`에 전달될
수 있다. `request_unrecorded()`는 key를 분리하고 이 응답 저장/cache 경로를 우회한다. TOP20
`ka00198` freshness/완전성 검증이 이 경로를 쓴다. broker 로그에는 API ID·continuation·queue wait·
duration 및 오류 종류가 있지만, 응답 값과 서비스가 수행한 검증/재시도 결정은 trace input으로
기록하지 않는다.

현재 store-input allowlist는 `save_query`/`load_query`를 담을 수 있어도 recorded executor는 두
메서드를 실행하지 않는다. 그리고 저장된 query 결과는 service/parser가 이미 응답을 소비한
뒤 cache에 넣은 값이므로, 그 저장 호출 재생만으로 원래 TR 요청·응답 이후의 로직은 재실행되지
않는다. cache hit, in-flight join, persistent cache hit, 외부 network request를 구분하는 broker
결정도 보존되지 않는다. 따라서 REST query cache는 **3 + 4**: cache store 호출은 capture돼도
executor 미지원(3), 논리 요청/응답 및 cache/queue outcome cause는 미기록(4)이다.

시장 자료 REST(TR)는 현재 표의 store-only 분류 2가 원인 replay를 뜻하지 않도록 해석한다.
분봉·일봉·기본정보·수급·NXT·신고가 결과의 native store 호출은 일부 재생할 수 있지만, 실제
Kiwoom 응답을 받아 parser/coverage/freshness/retry/scheduling을 수행하는 과정은 재생하지
않는다. 특히 `request_unrecorded()` 응답은 query cache/store trace에도 남지 않는다.

원인 재생의 최소 단위는 허용된 시장 API ID별 논리 request와 response outcome이다: 정규화 body,
continuation/next key, cache outcome(외부 요청·RAM hit·persistent hit·in-flight join), source
request ID/시각, 정규화 응답 payload 또는 오류 분류, 요청 순서다. queue 대기·외부 요청 시간도
필요한 부하 비교 모드에서만 보존한다. 주문·계좌·인증 TR과 토큰·header·원문 네트워크 bytes는
포함하지 않는다. replay broker는 외부 연결 없이 이 outcome을 service에 공급하고 미기록 요청은
명시적으로 실패시켜야 한다. 코드가 새로 만드는 parser 결과와 canonical DB writer를 실행할 때
과거 descendant store calls를 중복 재생하지 않도록 source request→generated operation 연결도
필요하다. 이는 REST broker/service 전반의 공개 계약과 cache timing을 건드리는 변경이므로, 먼저
허용 시장 TR 범위·민감 필드·cache-hit 의미를 전용 테스트로 확정하기 전에는 구현/배포하지 않는다.

#### 시장 TR별 원인·후속 동작 대조

아래는 호출 경로의 정적 코드 확인이다. broker 로그는 API ID와 queue/실행 시간을 보여주지만,
응답 payload·요청 trigger·feature owner·검증 결과·후속 task ID는 보존하지 않는다. 표의 store replay는
이미 만들어진 쓰기를 재실행하는 것이며 원인 서비스 replay와 구별한다.

| 입력 | 요청 owner/trigger | 응답 뒤 동작 및 consumer | 현재 replay 경계 | 원인 replay에 필요한 최소 입력 |
|---|---|---|---|---|
| `ka00198 qry_tp=5` | `AutonomousTop20Service.refresh_ranking_once`, 30초 순위 schedule | stale 시각·20칸 완전성 검사/제한 재시도 후 membership RAM publish, 0B code 갱신, membership 저장, 신규 편입 준비와 index 작업 예약 | membership store op만 재생 가능; TR·검증·retry·subscription·preparation은 미기록 | 정규화 request/response, `dt/tm`·20개 item, retry 순번/결과, schedule trigger, 수락 여부와 descendant ID |
| `ka00198 qry_tp=1..4` | TOP20 보조 순위 timer (30초/1분/10분/1시간 간격) | 유형별 ranking snapshot archive; 주 membership과 별도 | archive store op는 가능; schedule와 broker cache outcome은 미기록 | `qry_tp`, cache/network outcome, 정규화 item·관측 시각, archive 여부 |
| `ka10001`, `ka10100` | TOP20 entry 준비; 별도 hot market-event cohort metadata/상한가 fallback | 기본정보는 당일 관측을 재사용하되 07시 뒤에는 07시 전 관측을 재조회; NXT eligibility는 날짜별 freshness를 판정하고 document 저장 및 venue 구독 구성 | 일부 document store op만 가능; owner·refresh 판정·구독 변경 미기록 | 종목·날짜·관측시각, 필요한 정규화 필드(`upl_pric`, `nxtEnable` 등), response/error와 freshness 판정 |
| `ka10080` | TOP20 편입 전 당일 및 장후 분봉 보완; broker response handler가 canonical 분봉 적재 | 연속 페이지 종료·날짜별 기대 분 확인 뒤 분봉/metadata/revision 저장 및 coverage 확정 | 분봉 store shape 일부 가능; REST page·coverage/재시도 검증 미기록 | 종목/시장·기준일, page 순서/continuation, 정규화 OHLCV, 종료/검증 판정과 event→writer 연결 |
| `ka10081` | TOP20 entry·장후 일봉 준비; broker response handler가 canonical 일봉 적재 | source window/연속조회 완료/저장 행을 검증해 coverage 확정; 실제 일봉 변경은 신고가 재검증을 무효화 | 일봉 store op 일부 가능; TR·coverage/freshness/retry와 신고가 재계산 원인은 미기록 | 종목/시장·기준일, page/continuation, 정규화 일봉, 완료 판정 및 descendant 연결 |
| `ka10045` | TOP20 최초 편입 수급 및 장후 후보 flow; PC 투자자 수급 서비스도 같은 TR 사용 | 대상일 행 검증 뒤 flow 문서 저장; 첫 `_AL` 요청 예외 시 일반 code로 재시도 | store op 일부 가능; trigger·fallback/error·empty 판정 미기록 | 종목·대상일·시장 suffix, normalized rows/empty/error, fallback/retry와 consumer owner |
| `ka90008` | TOP20 장후 후보 program-flow; PC program trade service도 같은 TR 사용 | 대상일 검증 후 저장; 오늘은 0행도 허용될 수 있고 finalization projection 기록 | store op 일부 가능; 요청·empty 의미·완료 판단 미기록 | 종목·날짜·suffix, 정규화 행/행수, error와 allow-empty 판정 |
| `ka10083`, `ka10094` | TOP20 `historical_high`가 `HistoricalHighService` adapter로 월봉/연봉 요청 | 저장 일봉/coverage를 이용해 고가를 계산하고 결과 문서를 저장 | 결과 store op만 가능; adapter 요청·계산과 저장 일봉 사용 판정 미기록 | 종목·기간·continuation, 정규화 월/연 봉, cache/archive/network outcome, 계산 입력 fingerprint |
| `ka20005`, `ka20006` | TOP20 장후 index backfill (KOSPI/KOSDAQ 각 1회) | 분봉 최대 8페이지 종료 검증과 일봉을 모아 index snapshot 및 coverage 저장 | snapshot store op만 가능; page·시장별 실패 및 완료 판정 미기록 | index code/시장, 분봉 page·continuation, 일봉 응답, 시장별 완료 및 descendants |
| `ka10054` | 독립 `MarketEventService` VI backfill queue | 최대 10페이지 VI event를 정규화하고 중복키/revision 저장; TOP20 preparation과 별도 owner | store/revision capture와 executor 지원은 별도 확인 필요; TR/page/consumer 원인은 미기록 | trigger·종목·continuation, 필요한 VI event 필드, 중복판정과 event→revision ID |
| `ka10016` | 중앙 response handler와 허용 endpoint에 존재; 이번 정적 검색에서 `AutonomousTop20Service` 직접 호출은 발견하지 못함 | `new_high` dataset snapshot 저장 handler 존재; 실제 NAS caller/실행 여부는 미확인 | snapshot writer만 가능할 수 있음; 호출/consumer 미확정 | 기존 broker audit으로 caller·trigger 확인 후 결정; 확인 전 원인 replay coverage로 세지 않음 |

#### 2026-10-07 TOP20 순위 입력 후보 (로컬 구현)

진단 trace 요청에 `top20_inputs` 선택 항목이 추가됐다. 기본값은 `false`이며, 켜면 manifest는
schema 3과 `top20-ranking-input/v1`을 기록한다. 기존 `store_inputs`/`collector_inputs`만 켜는
trace는 계속 schema 2를 사용한다. capabilities 응답은 옵션과 지원 범위를 공개한다.

입력 경계는 TOP20 순위 `refresh_ranking_once()`의 `ka00198 qry_tp=5` 요청/응답 소비 지점이다.
회차 ID 아래에 source time, retry limit, 각 시도의 소비 대상 response 필드, cache-hit flag,
elapsed time, transport error type, cycle outcome을 보존한다. 전체 HTTP 응답·headers·오류문구·인증값은
보존하지 않는다. capture 복사가 실패해도 순위 조회/저장은 계속되며 거부된 회차는 replay 전처리가 거부한다.
같은 TOP20 객체가 만든 store call은 `cause_input_id`에 회차 ID를 붙인다.

offline `replay_ranking_decisions()`는 운영 코드의 최신성·20-slot 검사와 재시도 판정만 실행한다.
외부 네트워크, DB, 구독, 종목 준비, index 작업은 실행하지 않고 과거 descendant store call도 주입하지 않는다.
결과는 `ranking_validation_only`, `downstream_replay_supported=false`, `timing_preserved=false`,
`source_state_equivalent=false`로 명시한다. 따라서 현재는 순위 판정 로직 비교만 가능하며 TOP20 기능
ON/OFF DB 부하 실험으로 해석할 수 없다.

schema 3 window reader는 window에 닿은 market-input 회차 수와 경계가 잘린 회차 ID를 보고한다.
잘린 회차는 완전 replay 입력으로 사용할 수 없다. `ka10001/10100`, `ka10080/81`, `ka10045` fan-out
응답, catalog·subscription 초기 RAM 상태, cross-component descendants는 미지원이다. 다음 구현에서
새 응답 경계를 추가할 때도 외부 TR과 과거 descendant 중복 실행, 기능 제외 시 과거 결과 seed 주입을 막는
사전 검증을 함께 확장해야 한다.

#### 2026-10-07 TOP20 최초 편입 수급 입력 후보 (로컬 구현)

TOP20 `ka10045` 최초 편입 수급만 별도 `top20-candidate-flow-input/v1` market-input 회차로
기록한다. cycle의 종목·대상일, 완료 marker 유무, `_AL` 요청 뒤 일반 code 재시도, 응답에서 실제
ingestor가 저장하는 `stk_orgn_trde_trnsn` 행, 네트워크/RAM cache 구분, 저장 확인, marker 시각과
결과를 최소 payload로 보존한다. 원문 오류문구와 쓰지 않는 응답 필드는 기록하지 않는다. 실제 원본
수급 저장은 TOP20 consumer가 응답을 검증하기 전에 broker persistence task에서 수행되므로 해당
ingest operation에도 원인 input ID를 연결한다.

offline `run_owned_candidate_flow_experiment()`은 봉인된 전용 replay DB를 baseline으로 복구한 뒤
실제 `MarketDataIngestor.ingest`와 TOP20 최초 편입 consumer를 실행하고 종료 후 baseline을 복구한다.
역사적 descendant operation은 보고서에 연결하되 실행하거나 미리 seed하지 않는다. replay 중 native
DB write 실패, 원본의 불확실한 저장 확인, shared in-flight, 미지원 handler는 성공으로 취급하지 않는다.
RAM cache 응답은 원본 investor-flow dataset이 baseline에 있을 때만 소비하며 다시 저장하지 않는다.
재생 취소 시 진행 중인 baseline read와 native write가 drain된 다음 반환한다.

이 경계는 TOP20 최초 편입 수급의 consumer와 ingest 로직 재생(1, 한정)을 지원한다. 일반 장후 수급,
PC 수급 서비스, 임의 `ka10045` fan-out, broker queue/cache 만료·동시요청 스케줄, TOP20 전체 준비
작업은 포함하지 않는다. source state와 timing 동등성은 false다. 로컬 fake-client/SQLite 및 TOP20·broker
결합 회귀 108개(신규 flow 검사 12개 포함)가 통과했다. dedicated PostgreSQL 반복/COMMIT-ack-loss 게이트 2개는
작성됐지만 아직 실행되지 않았다.

### 2026-10-07 TOP20 원인 재생 계약 결정 — 설계 확정, 구현 대기

이 절은 순위 판정 replay와 최초 편입 수급 replay를 전체 TOP20으로 연결하기 위한 후속 계약이다.
아래 기능은 아직 구현·배포된 것으로 세지 않는다. 기존 v6 capture 및 예약 요청을 변경하지 않는다.
앞의 partial replay와 `source_state_equivalent=false` 판정도 유지한다.

#### 실행 경계와 선택한 방식

전체 TOP20 실험은 실제 `AutonomousTop20Service`의 schedule/event/index loop와 준비 작업을
함께 실행하는 **세션 단위**로 한다. `refresh_ranking_once()`만 호출하거나 `_tasks`에 가짜 값을
넣어 후속 작업을 켜는 방식은 사용하지 않는다. 생성·start·종료·native write drain까지 실험이
소유한다. 기존 `_select_ranking_response()`와 candidate-flow 전용 runner는 좁은 검증 도구로 남는다.

외부 입력은 고정하되 저장 timer, batch, 재시도, 준비 단계의 성공 판정은 실행 중인 코드가 결정한다.
과거 DB 호출을 시간표대로 다시 실행하는 mode는 별도다. 새 코드가 실행할 서비스의 과거 store
호출을 함께 넣거나, 끈 서비스의 과거 결과를 측정 도중 주입해서 상태를 맞추지 않는다.

단계 선택은 기존 책임 경계를 따른다. 처음에는 TOP20 **서비스 전체** ON/OFF와 이미 지원된
candidate-flow 단독만 제공한다. basic/daily/high 같은 내부 stage를 끄려면 disabled와 successful을
구분하는 실제 실행 계약이 먼저 필요하다. await 대상에 no-op을 꽂아 성공 marker를 남기는 방법은
금지한다. 지원 전에는 그 stage mask를 명시적으로 거부한다.

#### 시작 상태: 비교 가능성과 원본 동등성은 별도

모든 run은 `baseline_id + ram_seed_id + input_manifest_hash + selection + clock_policy`를 고정한다.
RAM seed는 버전 있는 명시 필드만 복원한다. 객체 `__dict__`, pickle, Task/Future/Lock/connection,
thread 또는 실행 중인 coroutine은 복사하지 않는다. source Python 객체 주소는 trace 안의 식별자일
뿐이며 다음 run의 동일 component 판정은 세션에 봉인한 역할 매핑을 사용한다.

첫 통합 검증은 **controlled fixture DB와 그 DB에 일치하도록 만든 RAM seed**로 한다. 이 상태에서
실제 TOP20 코드와 기록 입력을 반복 실행할 수 있어도 장중 원본 상태 동등성을 뜻하지 않는다.
원래 DB/RAM 상태가 없다는 이유로 임의 초기화한 뒤 `source_state_equivalent=true`로 올리지 않는다.
수정 전후에도 같은 seed를 사용하며 호환되지 않는 필드는 명시 migration 또는 거부로 처리한다.

| 상태 소유자 | 복원·검증할 의미 있는 상태 | 빠졌을 때 처리 |
| --- | --- | --- |
| 순위/편입 | 최신 membership projection, last ranking/aux slot, 당일 first_seen/persisted code, 계좌 편입 **종목 목록만** | cold fixture라고 명시하거나 session 사전 거부 |
| 준비 작업 | 종목·날짜·stage 성공 token, daily input/verified/stage generation, 완료/미완료 및 분봉 보완 설정 | 당일 완료로 추정하지 않음; DB fingerprint/coverage와 불일치하면 거부 |
| catalog/구독 | 시장 분류와 기준일, NXT 값/확인일, subscription revision/day, 해당 서비스의 요청 종목, upstream ready/승인·gap 상태 | 필요한 catalog outcome/구독 증거가 없으면 거부 |
| TOP20 지수 | active/next cohort·activation, 열린 분·complete/gap·samples·segment baseline/누적/구성 | 단순 현재 TOP20 20종목만으로 대체하지 않음 |
| 체결 집계 | MinuteTradeValueAggregator의 봉/누적 기준/추정 보정/source mode/완료 분; 재생 구간에서 참조할 보유 범위 | 이미 소비한 체결을 다시 누적하지 않음; 임의 최근 1분 잘라내기 금지 |
| 대기 저장 | index outbox·pending program snapshot·operation identity 및 저장 확정 여부 | 불확실한 COMMIT 또는 진행 중 저장을 완료로 간주하지 않음 |
| broker/공유 자원 | RAM cache의 payload·상대 만료시각·저장 확인, persistent cache, 선택 범위 밖 공유 요청/구독 소유자 | 미기록 공유 실행/초기 inflight는 첫 버전 session에서 거부 |

첫 seed 계약은 시작 시 in-flight native write, broker job, 준비 coroutine, 진행 중 close가 없는
상태만 지원한다. 실행 중 작업은 단순 `pending=true`로 재생할 수 없다. controlled fixture에서는
이를 구성해 검증한다. 장중 source checkpoint는 향후 자연스러운 안전 지점에서만 시도하며,
성공하지 못해도 운영 작업이나 기존 capture를 멈추지 않는다. 이 경우 source seed 미확보를 남긴다.
DB와 RAM을 서로 다른 시각에 읽은 스냅샷은 일관된 source checkpoint로 승인하지 않는다.
운영 메모리를 대량 동기 복사하거나 전역 pause하여 다음 녹화에 이 seed를 억지로 추가하지 않는다.

warm-up은 같은 seed에서 시작해 **같은 mask를 처음부터** 적용한다. 전체 workload로 warm-up한 후
측정 구간에서만 하나를 빼면 제외 효과가 prefix에 남으므로 별도 전환 실험이다. 시작 전부터 있던
DB 이력은 고정 baseline으로 사용할 수 있지만, 측정 구간의 과거 결과를 baseline에 섞지 않는다.
없는 REST response를 채우려고 미래 문서나 과거 descendant를 seed하는 것도 금지한다.

#### 추가할 최소 입력

1. 순위 `ka00198 qry_tp=5`의 현재 cycle/attempt 입력을 유지한다. 허용 body, 응답 item/dt/tm,
   continuation, 오류 종류와 재시도 ordinal을 request identity로 묶는다.
2. 실제 준비 경로가 소비하는 `ka10001/ka10100`, `ka10080/81`, 필요 시 `ka10083/94`,
   최초 편입 `ka10045`의 논리 요청과 최소 response를 수집한다. catalog_loader의 정규화 결과와
   실패도 외부 입력이다. 새 code/시장/page 요청에 대응하는 입력이 없으면 외부 통신 대신 실패한다.
   입력 필드는 각 consumer와 MarketDataIngestor가 읽는 필드의 합집합으로 검토한다.
3. TOP20 subscriber가 소비하는 trade/program_trade/market_operation 및 ready/gap/queue-loss
   경계를 기록한다. 현재 collector v2는 0s를 포함하지 않으므로 market_operation을 지원한다고
   추정하지 않는다. 0s는 허용된 정규화 MarketOperationTick 필드만 별도 보완한다. 계좌 이벤트나
   원문 네트워크 bytes·headers·오류문구는 추가하지 않는다.
4. 원인 payload가 이미 있는 0B/0w는 다시 직렬화하지 않고 입력 ID와 message 내 위치를 참조한다.
   논리 메시지가 여러 trace chunk/input으로 쪼개지면 모든 조각의 완전성과 parser 출력 ordinal을
   검사한다. 현재 `_recorded_input_high_water` 한 값으로 각 publish의 정확한 원인을 대신하지 않는다.
   참조할 입력이 없으면 그 경로의 coverage 부족으로 기록하고 전체 원인 실험에서는 거부한다.

65분 capture의 complete/drop=0과 위 경로의 replay 준비 완료는 별도 gate다. 추가 scalar receipt도
event capacity·메모리 charge에 포함한다. capture ON/OFF 결과·큐·지연 검증 전에는 예약 옵션을
확대하지 않는다. 지원 입력을 덜 기록했는데 전체 workload가 지원된다고 capability를 올리지 않는다.

#### 큐·공유 요청·저장을 가로지르는 원인 연결

`captured_candidate_flow()`의 parent_input_id와 broker ingest 연결은 이미 있다. 그러나
`RealtimeHub.publish()`는 별도로 시작된 TOP20 event task의 queue에 dict를 전달하므로 producer의
ContextVar가 consumer에 전달되지 않는다. 순위 task의 cause만으로 TOP20 지수/프로그램 저장을
순위 또는 0B에 귀속시키지 않는다.

새 연결은 `trace epoch / component role / input_id / delivery_id / parent IDs`를 명시한다.
Hub에 넘길 event의 **비직렬화 메타데이터**에 원인 receipt를 붙이고 실제 enqueue/dequeue/drop과
함께 유지한다. 공개 event dict의 JSON 필드를 추가하거나 PC/API payload 계약을 바꾸지 않는다.
subscriber별 bounded queue 수명 밖에 event reference를 쌓지 않는다. clone으로 receipt를 잃거나
capture가 재시작되어 epoch가 다르면 이전 원인을 재사용하지 않고 coverage 오류로 드러낸다.

집계 저장은 여러 체결과 순위의 결과이므로 단일 마지막 cause로 충분하지 않다. 선택한 서비스가
생성하는 저장은 **그 서비스가 소유한 sink 집합**으로 과거 호출을 대체하고, 원인 receipt는 실제
소비 입력 범위/집합을 검사하는 데 쓴다. parent 관계는 cycle·epoch·누락 부모·미완료 자식을 검증한다.
동일 dataset을 쓰는 peer는 component가 다르면 유지한다. component 주소나 writer 이름만 같은
것을 근거로 peer를 제거하지 않는다. 선택/미선택 소유자가 한 transaction 또는 shared future를
함께 소유하면 첫 버전은 거부한다. transaction을 쪼개거나 결과를 두 번 ingest하지 않는다.

`전체 - TOP20`은 TOP20 service를 시작하지 않고 그 서비스의 준비 요청·구독 기여·파생 저장도
실행하지 않는다는 뜻이다. 다른 owner가 요청한 같은 자료는 계속 처리할 수 있다. 남은 consumer가
없는 값을 읽으면 그 코드의 실제 공백/재시도 처리를 실행한다. 필요한 다음 외부 입력이 tape에
없을 때만 `input_unavailable`로 끝내며, 성공시키려고 제외 결과를 보충하지 않는다.

실험의 network 공급은 **녹화된 관측 범위**로 고정한다. 구독을 바꿨을 때 실제 키움이 보냈을
미기록 종목까지 생성할 수는 없다. 구독 정책 자체가 관측 범위를 벗어나면 unsupported다.
TOP20 단독 실행에 필요한 parser/hub 같은 공용 경로는 support component와 비용으로 따로 보고한다.

#### 시간·broker·DB 격리

부하 비교는 우선 1x 실제 경과시간을 쓴다. source wall time + replay elapsed로 날짜/분 경계를
결정하고 duration은 replay monotonic으로 측정한다. timer/flush는 새 코드가 예약한다. 과거
COMMIT 완료시각이나 flush 횟수를 재생 스케줄에 강제하지 않는다. 공급 지연·완료 지연·backlog를
각각 기록하고 입력이 밀리면 원본 timing 동일 주장을 하지 않는다. accelerated run은 기능 검증이다.

전체 session에서 broker도 실행할 경우 실제 CentralRestBroker의 cache/priority/persistence 경로를
유지하고 네트워크 client만 허용 tape adapter로 바꾼다. 현재 candidate-flow runner처럼 논리 결과를
직접 공급하는 mode는 broker 부하 비교가 아니다. 요청 전체 elapsed에는 과거 queue 및 DB 저장
대기가 포함되므로 이를 외부 응답 지연으로 재사용하지 않는다. transport 경계의 시작/종료와
논리 consumer 완료를 분리해 보존한다. 이 증거가 없는 옛 입력은 timing 미검증으로 남긴다.

응답 매칭은 API/body/시장/page/논리 consumer·request ordinal 기준이다. 전체 tape의 다음 응답을
무조건 pop하지 않는다. 제외로 안 생긴 요청의 결과를 다른 consumer에게 돌려주지 않는다.
새 코드가 요청 수를 줄인 경우 미사용 응답은 보고하되 강제 요청하지 않는다. 변경된 공유/cache
동작 때문에 매칭이 모호하거나 응답 공급 시점의 정당성을 확인할 수 없으면 실패한다.

기존 전용 baseline allowlist에는 `central_api_query_cache`가 없다. 따라서 실제 broker의 persistent
cache를 실행하는 단계 전에 별도 baseline 버전과 exact reset 범위를 검증해야 한다. source 날짜와
cache expiry clock도 일치시킨다. cache를 조용히 끄거나 기존 baseline ID의 범위를 덮어쓰지 않는다.
DB 연결은 기존 호출별로 유지하고, 취소·실패 시 모든 native thread/connection이 끝난 뒤 복구한다.

#### 구현 결과와 현재 coverage 경계 — 2026-10-07

큐 원인 receipt와 coverage 사전검사를 로컬 구현했다. 활성 NAS source 및 기존 capture 설정은 바꾸지 않았다.

- collector가 같은 수신 message의 분할된 `collector_input` ID를 모아 parser kind/ordinal과 함께 hub event의
  비직렬화 metadata로 전달한다. 각 subscriber는 별도 `delivery_id`와 순번을 갖는다.
- `RealtimeHub.publish()`의 public event dictionary와 JSON 모양은 유지한다. `top20_inputs` capture가
  꺼져 있으면 delivery observer를 호출하지 않는다. 실제 TOP20 event loop의 dequeue 범위에서만
  `capture_owner`를 설정해 이후 같은 task 및 그 자식 작업에 delivery ID를 전달한다.
- coverage checker는 enqueue/dequeue/consume 종료 쌍, trace epoch·subscriber 순서, source component,
  모든 parent input fragment와 row offset, parser 결과 ordinal을 검사한다. drop·재시작 epoch·누락/공유
  parent·복제 event는 재생 준비를 통과하지 못한다. native 분 경계 `program_flow` batch는 마지막 입력에
  임의 귀속시키지 않고 서비스 소유 sink로 분류한다. 제한된 sink 사전 계획은 `top20_index`와
  `program_flow`만 대체하고 peer를 남기며, 혼합 dataset batch는 거부한다.
- 현재 입력 검증 범위는 `trade`와 `program_trade`다. `market_operation`/0s 및 subscription/gap 상태는
  아직 source payload와 정확한 원인 연결이 없어서 명시적으로 미지원이다. 별도 TOP20 사전검사는
  schema 3 manifest의 완료/drain 상태, accepted/written/last_seq, drop·rejection 집계,
  payload capture 옵션, input coverage 및 chunk 경계를 전체 hydrated event와 대조한다.
  파일 경로는 chunk와 payload checksum도 확인한 뒤 같은 gate를 호출한다. 이 gate는 일반 부분
  replay reader 정책을 바꾸지 않으며 TOP20 세션 실행을 승인하지 않는다.
- 결과는 delivery coverage와 store-call 사전 분할뿐이다. `top20_session_execution_ready=false`를
  유지하며 실제 TOP20 timer·REST 준비 작업·DB/RAM seed·전체/단독/제외/조합 실행은 열지 않았다.
  실제 운영 입력의 성능이나 장초 상태 동등성도 검증하지 않았다.
- 긴 캡처용 `recorded_top20_window_frontier`는 chunk checksum과 전체 event sequence, 완료 manifest의
  counters/coverage, 참조된 모든 payload hash를 순차 확인한 뒤 선택 구간의 store pair와 TOP20
  전달 prefix에 필요한 행만 보유한다. 원래 seq를 유지해 selected window를 잘라 만든 새 순번으로
  누락을 숨기지 않는다. delivery prefix는 capture 시작부터 window 끝까지이며 선택 구간 종료 뒤
  도착한 consume 종료도 그 prefix의 열린 delivery를 닫도록 포함한다. reader는 최대 7,200초의
  capture에서 10분 이하 window를 받으며, 보유 metadata는 50,000행/32 MiB, 해제한 payload는
  32 MiB로 제한한다. 초과는 명시 오류다. 사전검사 범위는 `trade`/`program_trade`이며 0B 아닌
  market operation과 subscription/gap 입력의 source 재생은 열리지 않는다. 이는 원인 coverage
  판독이며 service 재생기 입력이나 warm RAM 복원 자료가 아니다.

로컬 focused 검사 12개가 통과했다. collector/TOP20 기존 회귀 117개에서는 Windows 임시 SQLite 파일이
아직 사용 중이라는 `WinError 32`가 1회 발생했다. 단독 재현은 간헐적이었고, 같은 검사를 이전 hub 전달
구현을 메모리에서 적용한 경우에도 통과했다. 따라서 새 receipt 코드의 회귀라고 단정하지 않으며,
파일 잠금/비동기 종료 문제는 미해결 검증 항목으로 둔다. 이 focused 검사는 NAS PostgreSQL이나 운영 부하
검증이 아니다.

#### 다음 gate

manifest·sequence·drop/rejection·coverage gate에 이어 긴 capture용 window reader를 구현했다.
관련 TOP20/capture/replay 검사는 80개 통과했다. 65분 fixture에서 전체 12,013개 event와 payload
참조를 검사하고, 선택 window 판독은 필요한 13개 행만 유지했다. 잘린 sequence, 거부 행과 집계
불일치, incomplete drain, 변조된 chunk/payload, prefix 안의 queue drop은 거부한다. 이것은 로컬
통제 fixture 결과이며 NAS의 실제 65분 trace에서 성공하거나 장중 상태와 동등함을 입증한 것은 아니다.
새 `recorded_top20_window_frontier`는 선택 window 최대 10분, capture-relative 시간 최대 7,200초,
보유 metadata 최대 50,000행/32 MiB, payload 최대 32 MiB다. 용량을 넘으면 명시적으로 실패한다.
기존 `recorded_top20_queue_frontier`의 10,000 event reader 제한은 기존 부분 검사에 남아 있다.

#### 고정 cold fixture identity와 미해결 실행 조건

`diagnostic_top20_seed.py`에 cold controlled fixture의 버전 있는 의미 상태 목록, source clock,
baseline·input·mask·component 결합 사전검사를 추가했다. 첫 seed는 실제 장중 RAM 복사본이 아니라
새 native service/broker/hub/aggregator의 명시된 cold 상태만 허용한다. readiness 표식, 준비 중 작업,
collector 누적값, pending 저장, lock 사용, broker 요청/캐시, 구독이 남아 있으면 실패한다. 임의로
Task·lock·연결이나 객체 `__dict__`를 복사하지 않는다. 고정 시계는 시작 전 원본시각을 반환하고
arm 뒤 실제 monotonic elapsed만 더한다. `CentralRestBroker`에는 선택적 source wall clock을 연결했다.
주입된 시계는 기본정보 cache key의 날짜 경계, persistent-cache TTL, ranking reservation에 쓰이며,
queue 대기·실제 sleep·실행시간 계측은 monotonic/실제 시계를 계속 쓴다. 기본 운영 broker는 시계를
주입하지 않아 기존 동작을 유지한다. query-cache 저장소는 lease 소유 v2 replay store에서만 source
clock을 읽고, 일반 SQLite/PostgreSQL store는 기존 system clock을 쓴다.

기존 baseline v1의 14개 테이블·ID·manifest·snapshot은 유지한다. 별도 opt-in baseline v2는
`central_api_query_cache`를 더해 15개 테이블을 독립 `replay_baseline_v2` snapshot 및
`replay_meta.baseline_v2` manifest에 봉인한다. v2 봉인은 우선 v1 parent가 정확히 복구됐는지 확인하고,
source origin이 같은 frozen clock인지 기록한다. v2 restore도 해당 clock origin이 다르면 DB reset 전에
거부한다. CLI에서 v2 status/seal/restore는 `--baseline-version 2 --source-origin <aware ISO time>`를
명시해야 한다. 로컬 offline `run`도 같은 opt-in 인자를 받는다. 이것을 TOP20 service lifecycle
replay 실행 가능으로 해석하면 안 된다.

이 후속 변경의 로컬 clock/cache/lease/preflight 회귀 58건은 통과했다. 이후 활성 NAS v6를 기반으로 만든
비활성 candidate에서 임시 RAM-backed·network-isolated PostgreSQL을 사용해 v1/v2 acceptance 7건이
통과했고 skipped=0이었다. v1 snapshot/manifest 보존과 v2 seal/restore/rollback/generation fencing,
손상 snapshot 및 변경 clock의 사전 거부를 확인했다. controlled fixture의 v1/v2 baseline ID는 임시
cluster와 함께 제거됐고 source state equivalent는 false다. 따라서 DB correctness는 확인됐지만 재사용할
NAS baseline, 실제 장중 상태 동등성, replay 성능 또는 전체 TOP20 실행은 확인되지 않았다. NAS에는
비활성 후보만 게시됐고 active release와 capture 설정은 바뀌지 않았다. 후속 로컬 offline `run`의 v2
연결과 native cache 호출 지원은 아래 별도 실행 gate가 통과하기 전까지 NAS acceptance 미완료다.
실제 report는 `replay-cache-v2-acceptance-50caa849ae0c62fbcbc8717d8ae13f05.log`이며, 임시 v1/v2
baseline ID는 각각 `56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`,
`3e59d961fd9d4c69f215003d1075ce741607a79693c46516964c933614bab291`이다. 이 ID들은 삭제된 fixture DB에서만 유효하다.

long-window reader와 seed gate를 잇는 preflight는 같은 checked manifest hash, 원본 workload mask,
source component 역할, baseline ID, RAM seed ID와 clock origin을 한 experiment identity로 묶는다.
mask는 warm-up 시작부터 같아야 한다. 이 gate는 판정 자료만 반환하며 DB를 복구하거나 service를 시작하지
않고 `database_restore_verified=false`, `top20_session_execution_ready=false`,
`execution_authorized=false`를 유지한다. controlled fixture seed의 `source_state_equivalent`도 false다.

seed/preflight와 65분 long-window fixture 결합 검사는 로컬 30건이 통과했다. 이어서 cache clock과
별도 v2 baseline 및 offline `run`을 추가했고, NAS 임시 RAM-backed·network-isolated PostgreSQL에서
baseline v1/v2 7건과 실제 `run` 3건, 총 10건이 skipped=0으로 통과했다. 반복 실행, source TTL, collector/cache
공통 clock, workload exclusion, ACK-loss 이후 drain·baseline 복구를 확인했다. 이 controlled fixture는
`source_state_equivalent=false`이며 성능이나 전체 TOP20 통합 replay를 입증하지 않는다. 남은 것은
service 내부의 직접 날짜/벽시계 참조 조사,
request identity별 catalog/REST 입력, durable outbox 및 TOP20 실제 service lifecycle runner다.
전체 TOP20 통합 재생은 아직 닫혀 있다. 이후에만 같은 seed와 입력으로 전체/단독/제외/조합 실행을 열 수 있다.
그 전까지 성능 결론이나 장중 상태
동등성을 주장하지 않으며, 불완전한 장초 capture를 timer/batch/concurrency 결정에 사용하지 않는다.

v2 offline 실행 연결(로컬 구현): `--baseline-version 2 --source-origin <aware trace-start ISO>`를
명시하고 봉인된 v2 origin과 checked trace manifest의 `started_at`을 연결한다. manifest 또는 선택된
collector payload의 source-time/mono pair가 origin과 100ms 넘게 다르면 DB 연결·복구 전에 거부한다.
이 허용 오차는 두 시각의 별도 샘플링을 위한 것이며 source-state equivalence를 증명하지 않는다.
clock은 preflight와 baseline restore 동안 frozen이고 scheduler 시작에만 한 번 arm한다. store-only
부분 구간에서는 cache wall time을 `origin + window_start + real elapsed`로 두고, 실행 duration과
scheduler lag는 0부터 측정한다. collector mode는 prefix를 실행하므로 `origin + real elapsed`를 쓴다.
cache와 선택된 collector만 같은 시계를 사용하며 다른 service/broker의 wall clock을 전역 치환하지 않는다.
`load_query/save_query`는 v2 lease clock이 없는 store에서 사전 거부하고, v1 reset 범위는 그대로다.
workload mask는 기존 plan에 그대로 적용하며 제외한 과거 cache 결과를 DB에 별도 주입하지 않는다.
실행 종료/실패 후 native connection drain을 확인하고 v2 전체 baseline을 복구한다.
로컬 회귀 57건과 임시 NAS PostgreSQL harness의 3개 실행 검사가 통과했다. 전체 harness report는
`replay-cache-v2-acceptance-ed1cd3ae0653aff1d8db76f329493bc9.log`; 임시 v1/v2 baseline은 제거했고
active release 및 운영 컨테이너는 변경하지 않았다.

#### TOP20 service source-clock 감사 — 2026-10-07

범위는 native TOP20 service와 그 시간 의존 helper, broker/cache, market ingestor,
collector와 source observation 경계다. 전체 앱의 모든 API/worker에 대한 clock 인증은 아니다.
운영 코드 수정 전 fake broker/store로 native `_ensure_historical_high`, `refresh_ranking_once`,
`MarketDataIngestor.ingest`를 실행했다. source를 `2001-04-03T09:00:02+09:00`으로 고정해도
신고가 `ka10094.base_dt`와 기준일 없는 time-only 분봉의 `trading_date`는 `2026-10-07`이었다.
membership live projection의 `saved_at`도 source timestamp `986256002.0` 대신 host timestamp를 썼다.
재현 도구는 `artifacts/check_top20_source_clock_readonly.py`; DB·외부 네트워크 접근은 없었다.
이는 source clock 누출 재현이며 운영 병목이나 장중 workload 성능 측정이 아니다.

| 경계 | 현재 판단 | 후속 변경 또는 유지 |
|---|---|---|
| TOP20 날짜·회차·coverage·준비 marker·index 관측 | `self._now()`가 source clock 주입 지점이며 대부분의 의미 판단에 이미 사용됨 | 기존 주입을 유지. native runner는 같은 clock에 service를 바인딩해야 함 |
| `HistoricalHighService` 연/월/일 TR와 250일 대체 evidence | 6곳의 `date.today()`가 source 날짜를 우회함. NAS caller가 source clock을 가지고도 helper에 전달하지 않음 | `load(code, *, as_of: date \| None = None)`에 명시 날짜를 받고 private fresh/incremental/refinement 경로 전체로 같은 날짜를 전달. 기본 호출은 진입 시 `date.today()`를 한 번 사용. NAS는 이미 coverage에 사용한 KST `basis` 날짜를 전달 |
| `MarketDataIngestor._base_date` | `base_dt`가 없거나 잘못됐고 응답 시간이 HHMMSS뿐이면 `datetime.now()` 날짜를 사용함 | ingest에서 구한 같은 `now`를 mandatory fallback으로 전달. 유효 request 날짜와 full YYYYMMDDHHMMSS 응답 날짜는 그대로 우선 |
| TOP20 메모리 membership 공개 `saved_at` | `time.time()` 직접 호출. live API로 노출되는 source metadata임 | 공개 시점의 `self._now().timestamp()` 사용. 순위 target slot의 `observed_at`과 공개 시각은 계속 구별 |
| broker persistent cache·기본정보 07:00 key·ranking reservation | 선택적 source wall clock 연결 및 v2 cache lease 검증 완료 | 유지. RAM TTL·queue wait·transport/handler duration은 real monotonic 유지(현재 1x 정책) |
| TOP20 calendar cache·backfill retry deadline·반복 sleep | 60초 TTL과 실패 재시도 간격은 실제 실행 경과시간임 | monotonic 및 실제 sleep 유지. 날짜 판단만 source clock 사용 |
| collector 데이터 시각·flush due·관측 날짜 | `now_provider`에 연결됨 | source clock 바인딩 유지. reconnect deadline·관측 health gap·capture entry mono/wall stamp는 실제 상태/계측 시각 유지 |
| index collector·minute aggregator·outbox | TOP20은 집계 helper에 명시 시각을 전달. outbox와 index collector에는 독립 host clock 읽기가 없음 | 유지. durable outbox의 seed·복구 fixture는 별도 미완료 |
| observation 및 daily coverage helper 기본값 | TOP20/ingestor caller가 명시 `checked_at`/basis/`updated_at`을 전달하므로 이 경로에서 fallback clock을 쓰지 않음 | 기본 호환 동작 유지. 시각 없는 임의 store-only payload까지 source-state 등가라고 주장하지 않음 |

현재 candidate-flow replay의 `ka10045` ingestor는 request `end_dt`에서 날짜를 얻어 `_now`를
읽지 않는다. 이를 근거 없이 수정하지 않는다. 향후 native broker runner가 분봉·일봉·기본정보·NXT 등
시간 의존 TR를 열 때는 `MarketDataIngestor(now_provider=clock.now)`도 같은 clock에 명시 바인딩해야 한다.
현재 cold seed의 service/broker 검사만으로 이 미래 ingestor 바인딩이 검증됐다고 보지 않는다.
store 내부에서 생성하는 persistence 감사 timestamp까지 source 시각으로 전역 치환하지 않는다.
TOP20의 준비/coverage 판단은 payload의 source 시각을 사용하지만, 새 consumer를 runner에 넣을 때는
그 consumer가 store-generated 시각으로 TTL/최신성을 판단하는지 다시 확인한다.

세 source-clock 누수를 로컬 코드에서 수정했다. `HistoricalHighService.load()`는 호출 시 기준일을 한 번
정하고 모든 fresh/incremental/refinement 및 split 재계산 경로에 전달한다. TOP20 caller는 KST coverage
basis와 동일한 `as_of`를 건넨다. 단위 회귀 12건이 통과했고, adjustment chart 요청의 base date, incremental
요청, 250일 evidence를 검증했다. 분봉 `_base_date`는 mandatory fallback으로 주입된 ingest `now`를 받으며,
기준일 없는 time-only 응답의 source date 회귀를 포함한 ingestor 13건이 통과했다. 유효 request date/full
응답 timestamp 우선권은 기존 테스트로 유지된다. membership 공개 시각은 `now.timestamp()`로 계산되고,
timezone 정보가 없는 입력은 KST로 고정되며
기존 TOP20 단위 67건이 통과했다. source `2001-04-03T09:00:02+09:00`으로 native fake broker/store 경로를
재실행해 신고가 `ka10094.base_dt=20010403`, 분봉 `trading_date=2001-04-03`, membership `saved_at=986256002.0`을
확인했고 세 leak flag가 모두 false였다. DB/외부 네트워크 접근은 없었다. 이는 로컬 clock 정합성 확인이며
전체 TOP20 lifecycle 실행 gate와 `execution_authorized=false`는 그대로다.

#### TOP20 lifecycle 실행 계약과 구현 순서 — 2026-10-07 설계 확정

이 절은 **후속 구현 계약**이다. 현재 seed/preflight를 executor로 승격하거나 기존 capture에
없는 입력을 있다고 간주하지 않는다. 첫 목표는 cold controlled fixture에서 native TOP20의
시작·순위·준비·실시간 소비·종료를 반복하는 correctness gate이며, 운영 상태 동등성과 성능은
별도 수용 대상이다. 최초 profile은 장중 window와 그 시작부터의 prefix만 지원한다.
장후/장전 backfill을 실행하는 시각은 해당 입력·reader 경계가 추가되기 전 명시 거부한다.

**코드로 확인한 실행 경계**

| 경계 | 확인한 실제 동작 | 실행 계약 |
|---|---|---|
| `CentralRestBroker._request/_resolve_cache_or_queue/_run/_run_persistence` | RAM/DB cache, shared future, priority queue, 별도 persistence worker를 거침. `request_unrecorded`도 broker 정책 사용 | broker는 native 그대로 사용하고 `request_with_continuation`의 외부 transport만 tape로 교체 |
| `app.py` broker 조립 | `MarketDataIngestor.ingest` 뒤 일봉 변경 callback이 TOP20 준비 상태를 무효화 | runner도 같은 ingest와 `notify_daily_bars_changed`를 연결. handler를 생략하거나 과거 `recording_succeeded`로 대체하지 않음 |
| `_ensure_market_catalog` | DB catalog를 먼저 읽고 필요하면 별도 동기 `catalog_loader` 호출 | 이 loader의 가공 `(code,name,market)` 목록/실패를 별도 tape 입력으로 공급 |
| `start/_schedule_loop/_event_loop/_index_loop` | 세 native loop가 준비·구독·보완 task와 index/program 저장을 만듦 | 과거 DB 호출을 스케줄로 삼지 않고 native loop가 새 파생 작업을 만들게 함 |
| `_advance/_publish_known_subscription/_event_loop` | `upstream_ready`, 구독 종목, subscriber drop, 0s 거래일 증거가 결과에 영향 | trade/program payload만 있는 기록으로 full lifecycle 지원 판정 금지 |
| `JsonRecordOutbox`와 `_queue_index/_flush_pending_indexes` | 파일 put → DB write → 파일 remove. 시작 시 파일 load 및 재시도 | 전용 run 디렉터리에서 실제 outbox 사용. RAM mock/운영 outbox 공유 금지 |
| `_close`와 `asyncio.to_thread` | program save는 shield/drain하지만 준비·index·catalog 등은 취소되는 await와 실제 thread 완료가 다를 수 있음 | 모든 run 소유 task/thread 종료와 새 작업 생성 차단을 확인한 뒤 restore |

**입력 tape v1: 최소 기록과 재생 규칙**

1. broker 논리 요청에는 source component/feature, 원인 ID 집합, 논리 lane, lane 내 요청 순번,
   API/path, canonical body, 시장/code, continuation/next_key, record_response를 붙인다.
   transport 실행에는 별도 ID와 시작/종료, 결과 `(payload, has_next, next_key)` 또는 허용된 오류
   유형을 기록하고 논리 요청과 연결한다. payload는 기존 bounded blob 저장을 재사용하며 token·
   credential·HTTP header 전체를 추가하지 않는다. catalog도 loader 입력/출력·시간·오류의 짝을 기록한다.
2. `_Job`에 원인 정보를 명시 전달한다. 장수 `_run/_run_persistence` task가 요청자의 ContextVar를
   자동 상속한다고 가정하지 않는다. shared future의 모든 원인, handler와 cache 저장의 parent를
   보존한다. 기존 ka10045 receipt와 별개 평행 계보를 만들지 말고 기존 receipt 계약을 확장한다.
3. 응답 매칭은 lane + 요청 내용 + continuation + 같은 요청의 순번을 사용한다. 전체 요청을 하나의
   FIFO로 꺼내 다른 종목 응답을 배정하지 않는다. 정체성이 모호하거나 새 요청의 입력이 없으면
   `input_unavailable`로 실패한다. 과거 RAM/DB cache hit의 결과를 새로운 transport 응답으로 바꾸지 않는다.
4. cache hit/miss, shared in-flight, ingest 성공과 cache write는 **새 실행에서 결정**한다. tape는
   외부 transport 구간의 지연만 재현하고 과거 queue/SQL/COMMIT 시간을 다시 sleep하지 않는다.
   전송 wrapper 시간이 rate-limit wait를 포함하면 그 범위를 보고서에 명시하고 중복 limiter를 넣지 않는다.
5. 코드 변경으로 요청이 줄면 미사용 tape 응답 수와 이유를 보고한다. 기록 소비량을 맞추려고 불필요한
   요청을 강제하지 않는다. 반대로 요청 증가/재시도 변화로 tape가 부족하면 성능 비교 완료로 처리하지 않는다.
   녹화 중 시작했으나 종료되지 않은 요청과 녹화 이전부터 진행 중인 요청도 prefix closure 실패다.
6. 일치하지 않는 입력 오류를 service가 일반 수집 실패로 잡고 로그만 남겨도 runner의 sticky failure
   receipt에 남겨 최종 성공을 막는다. 실제 기록된 transport 실패/재시도와 replay 계약 위반은 구분한다.

**실시간·DB·파일 초기 상태**

- fresh service/broker/hub/ingestor/collector는 한 source clock을 공유한다. 실제 연결·token·REG는
  실행하지 않는다. 구독 의도와 ACK/ready/gap 전이는 별도 입력 경계로 연결하며 새 코드의 구독 요청에
  대응시킨다. recorded READY를 무조건 true로 고정하거나 과거 subscriber queue를 그대로 주입하지 않는다.
  필요한 0s/구독/재연결 입력이 없으면 해당 native lifecycle profile은 미지원이다.
- cold fixture의 실제 outbox는 run 전용 root 내부 경로와 내용 hash를 experiment manifest에 결합한다.
  빈 상태부터 검증하고 pending fixture는 별도 검증한다. 손상 파일을 native `load()`의 빈 결과로
  숨기지 않도록 실행 전 파일 검증을 한다. outbox의 현재 atomic replace를 fsync 내구성 보장으로 표현하지 않는다.
- baseline v2의 DB 15개 table뿐 아니라 source origin, RAM seed, outbox seed, tape hash, workload mask와
  코드 버전을 experiment identity로 묶는다. v1/v2의 기존 manifest/ID는 수정하지 않고 실행 manifest가 참조한다.
- native reader도 실제 DB를 읽는다. catalog, entrants, account-entry symbols, 기본정보/NXT, coverage,
  신고가, 거래일 증거, cache 및 revision의 초기값이 fixture에 있어야 한다. 계좌 주문 기능은 실행하지 않는다.
  장후 `load_hot_cohort` 등 현재 table/reader closure 밖 경로를 빈 값으로 위조하지 않는다.
- 시작 전 상태만 baseline으로 사용한다. 제외한 기능이 window 도중 만들었을 과거 데이터는 재주입하지 않는다.
  cold prefix를 실행해도 실제 장중 RAM 상태 동등성이 증명되는 것은 아니므로 `source_state_equivalent=false` 유지.

**workload 포함/제외와 종료**

첫 native profile의 선택 단위는 TOP20 service 전체다. 내부 준비·구독·index·program sink를 임의로
빼는 mask는 지원하지 않는다. 기존 store-only profile과 의미가 다름을 보고서에 표시한다. TOP20
원인을 실행할 때는 그 원인에서 나온 membership/entrants/catalog/preparation/ingest/cache/index/program
과거 writer를 전부 대체한다. 데이터셋 이름만 같다는 이유로 peer writer까지 제거하지 않는다.
선택·제외 원인이 같은 transaction/shared job에 섞여 있고 분리 근거가 없으면 실행 전 거부한다.
`all - TOP20`은 service와 그 구독 기여를 만들지 않고 과거 descendant도 주입하지 않는다. 관측되지 않은
종목을 새 구독이 요구하면 recorded universe 부족이며, 진짜 외부 연결로 메우지 않는다.

실행 순서는 검증 → DB/outbox 복구 및 확인 → fresh 객체 생성/seed 검증 → clock arm → native start
→ prefix/선택 window → 외부 입력 종료 → producer 정지 → broker 요청·persistence와 모든 native
task/thread drain → 최종 상태 검증 → DB/outbox 복구다. service 종료 중 outstanding broker 요청을
처리할 수 있도록 broker를 먼저 닫지 않는다. 현재 `close()` 호출만으로 모든 thread가 끝났다고 보지 않는다.
DB connection 수가 0이어도 아직 실행 대기 중인 thread가 나중에 연결을 열 수 있으므로 작업 소유권도 필요하다.
timeout/취소/ACK loss 후 실제 drain을 확인하지 못하면 실패 상태로 격리하고 reset·다음 반복을 금지한다.
DB와 파일은 하나의 원자적 transaction이 아니므로 둘 중 하나의 복구가 실패하면 `cleanup_complete=false`다.
shutdown이 만든 최종 flush는 측정 window 부하와 따로 보고한다. 운영 shutdown을 바꿔야 한다면 별도
정합성 결함으로 재현·회귀 후 수정하며, runner 전용 종료로 덮어 운영과 같다고 주장하지 않는다.

**순차 구현 gate**

| 순서 | 구현/검증 범위 | 통과 조건 |
|---|---|---|
| T1 (완료) | broker request identity/transport·catalog tape와 reader | capture off 동작 유지, lane/continuation/실패/공유 원인 연결, 불완전 입력 거부, native RAM/DB cache와 ingest callback 경계. fake REST/SQLite 회귀 135건. full lifecycle은 닫힘 |
| T2 (완료) | 0s·구독 intent/ACK/READY/hub control/gap 입력 closure와 descendant preflight | 입력 누락·혼합 원인 및 불일치 구독 거부, 실제 native hub READY를 tape가 강제로 만들지 않음, 과거 descendant를 seed로 주입하지 않음. 관련 로컬 회귀 174건 |
| T3 (완료) | opt-in run 소유 task/thread와 실제 outbox seed 및 종료·복구 | PC native 회귀 142건, NAS 임시 RAM-backed·network-isolated PostgreSQL acceptance 13건 통과(skipped=0). 취소 thread drain, ACK loss 재시도, DB/file 공동 복구, v1 snapshot 보존. 보고서 `replay-cache-v2-acceptance-ac1d6c7568abcfccedf0547c765c517c.log`; controlled fixture, 실제 장중 상태·성능 동등성은 미검증 |
| T4 (controlled acceptance 통과) | native TOP20 lifecycle core를 owned runner·peer와 전용 PostgreSQL fixture에 연결 | NAS T4 acceptance 15건, skipped=0; baseline/복구, peer 조합, descendant 대체, drain을 확인. `source_state_equivalent=false`; 실제 장중 상태 등가·입력 완전성·성능은 미검증다음 |
| T5 | 충분한 새 실제 입력으로 동일 workload baseline 및 변경 전후 비교 | 입력·초기상태·mask·버전 고정, lag/동시성/coverage 표시. 무손실/완전성 확인 범위 밖으로 일반화하지 않음 |

T1 delayed-response 검증은 membership 공개 `saved_at`에 실제 공개 시각을 사용하고 target slot의
`observed_at`은 유지하는 계약을 확인했다. 이는 로컬 회귀 범위이며 live NAS API 표시나 장중 동작 검증은 아니다.

설계 확정 단계에서는 코드 경계 추적과 문서만 수행했다. 아래 구현 기록과 구별한다.

#### T4 native TOP20 lifecycle core — 2026-10-07 로컬 부분 검증

`diagnostic_top20_execution.py`와 `diagnostic_top20_transport.py`가 controlled fixture에서 실제
`AutonomousTop20Service`, `CentralRestBroker`, `MarketDataIngestor`, `CentralRealtimeCollector`,
`RealtimeHub`를 시작하고 종료한다. 외부 REST 결과와 catalog, 0B/0s 원인 입력, 구독 요청 및 ACK만
테이프로 공급한다. WebSocket 등록 프레임은 native 생성 로직을 사용하고, 새 native 구독이 기록된
요청과 맞을 때 native ACK 승인 경로를 실행한다. 구독 승인 전·gap 뒤의 실시간 행은 전달하지 않으며
과거 READY나 subscriber 결과는 주입하지 않는다. task는 실제 생성 순번과 명시적 source lane으로
묶고, 동일 source actor의 같은 request signature가 두 task에서 모호하게 공유되면 입력을 거부한다.
실행 전 collector의 pending/live snapshot, 구독 승인, 분·초 누적기 등 명시된 RAM 상태가 비어 있는지
검사한다. 종료 입력 task가 반복 취소되거나 제한시간을 넘으면 drain 전 baseline reset을 허용하지 않는다.

동일 controlled source fixture 세 번에서 membership과 준비 단계 상태가 일치했고 실제 native 구독
승인이 확인됐다. TOP20 제외 실행은 membership, 구독, REST 호출 및 과거 결과 주입이 없었다.
fixture의 일봉·수급 응답은 빈 값으로 기록되어 각 단계가 실패 상태로 유지됐다. 이 검사는
`tests.unit.test_top20_replay_execution` 2건(96.020초) 및 관련 구독·cold-state·취소/drain·native
collector/broker/runtime 회귀 126건에서 통과했다. 검증은 fake REST/realtime 입력과 SQLite이며
성능 비교가 아니다.

이 단계는 native 실행 core의 controlled fixture 검증이다. 통합 source manifest/descendant closure를
읽는 owned PostgreSQL runner, baseline lease와 DB/outbox 복구, replay 전후 최종 DB/revision 비교,
peer workload 동시 실행, 공개 run API는 아직 이 core에 연결되지 않았다. 따라서 `source_state_equivalent=false`,
`full_experiment_acceptance=false`이고 T4 전체 acceptance와 실제 장중 입력 coverage를 주장하지 않는다.
다음 gate는 core를 T3 lease/restore lifecycle에 연결해 PostgreSQL에서 세 번 같은 결과를 확인하고,
TOP20 단독·제외 및 peer workload 조합에서 source descendant를 중복 실행하지 않는지 검증하는 것이다.
운영 NAS release, capture 설정, timer/batch/concurrency는 변경하지 않았다.

#### T4 shared clock/peer execution 경계 — 2026-10-07 로컬 검증 및 NAS 후속 acceptance

`_ReplayStore`의 v2 query-cache 시계 연결은 lease의 generation, active, run-ready 검증을 통과하는
명시적 bound method로 바꿨다. TOP20 core는 이 소유 lease와 clock identity를 검증하며, direct clock
binding을 허용하는 SQLite 경계는 exact store type으로 제한한다. 잘못된 lease 세대, retired lease,
다른 clock 또는 연결 함수를 덮어쓴 store는 native 실행 전에 거부한다.

recorded peer scheduler는 내부 lifecycle owner가 넘긴 runtime에서 이미 arm된 동일 source clock을
재사용하고 clock을 다시 arm하지 않는다. actor task와 DB 호출은 실제 runtime task/executor를 거치며
connection 호출이 끝나야 worker 소유권이 해제된다. TOP20 close가 drain을 증명할 수 있도록 peer 실행은
전용 standalone runner에서 호출할 수 없다. DB operation 실패나 peer 대기자 취소가 있으면 runtime의
실행 성공 표식도 실패로 남아 성능 성공으로 오인되지 않는다. runtime이 없는 기존 호출은 같은 helper의
기본 asyncio 동작을 유지한다.

shared execution·native shutdown boundary·recorded execution·cache clock 회귀 34건이 로컬에서 통과했다.
`artifacts/t4-shared-clock-native-worker-tests.log`의 이 gate 자체는 unit/fake store 범위였다. 이후 아래
T4 native TOP20 + peer PostgreSQL gate가 전용 lease에서 core와 peer를 함께 실행하고 source descendant 중복
방지, baseline 복구 및 종료 drain을 확인해 controlled lifecycle acceptance를 완료했다. 그 결과는 실제
장중 source-state equivalence나 성능 비교를 대신하지 않으며 `source_state_equivalent=false`를 유지한다.

#### T4 native TOP20 + peer PostgreSQL candidate — 2026-10-07 controlled acceptance 통과

`tests.integration.test_top20_session_postgres`와 `scripts/check_replay_cache_baseline.sh --top20-lifecycle`를
검증된 T3 candidate 위에 stage했다. v1 `2026.10.07-top20-session-v1-9eabe984725acb07`은 T3 13건
통과 후 T4 fixture의 25초 setup 상한으로 실패했다. 출력에는 broker handler/cache 저장 1.1~2.6초 구간이
보이지만 그 하위 원인은 미확정이다. cleanup 및 운영 container 보존은 gate가 확인했다. test helper 기본 제한은 유지하고 disposable
PostgreSQL gate에만 180초를 지정한 v2 `2026.10.07-top20-session-v2-57de01203aa5af84`를 새 비활성
candidate로 stage했다. NAS active `trace-market-inputs-v6`는 유지한다. 이 gate는 임시 RAM-backed·network-isolated
PostgreSQL에서 같은 native source clock의 반복 실행, service 제외와 peer writer 유지, descendants 대체,
ACK loss 후 drain 및 DB/outbox baseline 복원을 검사한다. v2 NAS gate가 15건, `skipped=0`으로 통과했다.
v1 baseline ID는 `56e88db7bc556afdd8a64a5800a47a1f6663f339aa299cb9f462a4fb804b56a3`, v2 baseline ID는
`61113931f06005e5afb24abad62622c1d52a34d9549012b3977f593040afe240`이다. v1 snapshot 보존,
반복 restore, TOP20 단독/제외와 peer writer 유지, descendant 대체, 실행 종료/drain 및 DB/outbox 복구
경계를 controlled fixture에서 확인했다. 로그의 두 `recording_gap` 경고는 고정 시각의 TOP20 fixture가
실행 중 stale해져 해당 순위 회차 저장을 건너뛴 것으로, gate 실패나 DB 입력 유실을 뜻하지 않는다.
보고서 `replay-cache-v2-acceptance-ec337a0eda5c1167ff7f36c17a59a872.log`는
`source_state_equivalent=false`와 `fixture=controlled_fixture`를 명시한다. 따라서 T4 lifecycle gate는
통과했지만 실제 장중 상태 등가·입력 coverage·성능 개선을 증명하지 않는다. 운영 release와 두 운영
container는 변경하지 않았다. 다음 단계는 유효한 장중 capture를 확보한 뒤 현재 코드 baseline을 먼저
고정 재생하고, 이후 후보를 하나씩 같은 입력으로 비교하는 것이다.

#### T1 request/catalog 입력 구현 — 2026-10-07

#### T1 request/catalog 입력 구현 — 2026-10-07

`diagnostic_rest_input.py`와 native broker/catalog 호출 지점에 opt-in 경계를 연결했다. `top20_inputs`
설정이 있을 때만 시장 API whitelist의 논리 요청, transport 결과/오류, cache/ingest effect와 catalog
목록을 기록한다. 계좌/credential 요청은 포함하지 않는다. 기존 store/collector 입력 설정만 켠 capture에는
이 경계를 추가하지 않는다. 원본 error message는 저장하지 않고 error type만 기록한다.
market-request-tape/v1을 새 입력 버전으로 기록하며 기존 TOP20 ranking/candidate-flow receipt는 유지한다.
native ka10045 ingestor의 flow cause와 새 논리 요청의 parent 연결을 함께 보존한다.

logical lane은 명시된 actor이며 API/path/body/continuation/next-key/record-response가 같은 요청별
ordinal을 사용한다. 따라서 다른 API 요청을 생략해도 남은 요청의 ordinal이 밀리지 않는다. 실행 때는
source lane을 명시 바인딩하며 task 주소가 같을 것이라고 추측하지 않는다. matching index로 입력을
찾고 한 전송 결과를 여러 번 소비하지 않는다. broker의 RAM/DB cache·shared future·priority와 실제
ingestor/일봉 변경 callback은 그대로 실행하며 과거 cache payload를 신규 전송으로 공급하지 않는다.
catalog도 loader만 교체하고 native DB catalog 재사용 여부와 저장을 그대로 실행한다.

native persistence task는 `_Job.input_group`으로 원인 receipt를 받고, cache/ingest writer에 전달한다.
shared 요청의 부모 집합은 transport/effect 시작·종료에 남긴다. DB effect 도중 집합이 바뀌거나
미기록 요청과 공유되거나 다른 capture epoch의 요청이면 reader에서 거부한다. 이 단계는 mixed ownership을
허용하는 descendant selector가 아니다. TOP20 native lifecycle의 전체/제외 실험은 계속 닫혀 있다.

reader는 외부 verified trace/chunk reader가 전달한 범위의 입력 pair·owner·요청 identity·ordinal·effect
closure와 결과 유형을 검사한다. 전체 trace checksum/sequence를 독립 인증하지 않으며 metadata를
필터링한 배열의 증가 seq만으로 무손실을 주장하지 않는다. 한 번에 50,000 event, 32 MiB encoded payload,
작은 요청 identity는 64 KiB로 제한한다. capture counter 상태는 최대 32,768 lane 및 4 MiB,
shared receipt는 최대 4,096 요청이며 초과는 재생 coverage 거부로 표시하고 native 요청은 계속한다.
transport/catalog 지연은 최대 7,200초의 유효한 pair만 허용한다. 대기시간은 외부 client wrapper 구간만
재현하며 기존 broker queue/DB/COMMIT 시간을 다시 sleep하지 않는다. 이 구간에는 native client의
rate-limit wait와 내부 retry가 포함될 수 있다. 새로운 tape client에 추가 limiter를 겹치지 않는다.

`RequestTapeClient`는 입력을 자체 복사해 고정하고 외부 연결 기능을 제공하지 않는다. cache 경로가
변해 새 transport 입력이 필요하거나 기록된 오류 유형을 재현할 수 없으면 sticky failure로 남긴다.
미사용 외부 입력은 report에 표시하며 기록 소비량을 맞추기 위한 요청을 만들지 않는다. 이 helper와
reader는 offline/local 후보이며 운영 API의 run 선택기나 full TOP20 runner에는 연결하지 않았다.

관련 시각 정합성: delayed response 재현에서 source 09:00:02에 공개된 membership의 `saved_at`이
09:00:00으로 남는 것을 확인했다. 수정은 실제 공개 시점의 `self._now()`를 사용하며 순위 target
slot의 `observed_at`은 유지한다. 이는 성능 최적화와 별개의 표시/출처 시각 수정이다.

실행 gate는 `tests/unit/test_diagnostic_rest_input.py`와 기존 broker/TOP20/ingestor/입력 회귀로 구성했고,
총 135건이 통과했다. 검증은 가짜 REST 입력과 SQLite를 사용했다. NAS PostgreSQL, 실제 capture overhead,
새 native lifecycle 및 실제 장중 입력 coverage는 검증하지 않았다. 운영 release·녹화 설정·timer/batch/concurrency는
변경하지 않았다.

#### T2 TOP20 lifecycle 입력/descendant preflight 구현 — 2026-10-07

`diagnostic_top20_lifecycle_input.py`는 opt-in capture에서 0s 파싱 입력, hub 초기 상태 및 구독
connect/update/disconnect intent, 민감 값을 제거한 구독 그룹, 카운트된 REG ACK receipt, upstream READY,
TOP20 control 입력, capture gap을 기록하고 pair·epoch·hub 전이·ACK 수·READY 근거를 검증한다.
gap이 있거나 READY 근거를 재사용하거나 입력 pair가 불완전하면 해당 lifecycle 입력은 replay 불가로 남긴다.

`SubscriptionTape`는 새 native 구독 intent와 기록된 intent/outcome이 명시적으로 일치할 때만 결과를 공급한다.
반환 결과는 `ready_applied=false`이며 hub READY를 설정하거나 queue에 과거 outcome을 넣거나 network를 호출하지
않는다. 불일치가 발생하면 이후도 unavailable로 유지한다. `compile_lifecycle_descendants`는 TOP20 ON/OFF의
descendant 실행 전 검사를 제공하고, TOP20 OFF 시 과거 source descendant를 주입하지 않으며 peer operation ID는
보존한다. 공유·불명확 provenance는 조용히 분류하지 않고 실행 전 거부한다.

관련 로컬 회귀 174건이 통과했다. fake WebSocket/REST와 SQLite 기반 검증이며 전용 PostgreSQL, 장중 capture,
운영 부하 또는 전체 lifecycle 실행은 검증하지 않았다. T3의 task/thread drain과 native outbox seed/복구 core는
로컬 구현·142건 native 회귀를 마쳤고, NAS 임시 PostgreSQL acceptance 13건도 skipped=0으로 통과했다.
이는 controlled fixture 정합성 gate다. TOP20 ON/OFF native lifecycle replay는 여전히 지원되지 않는다.
운영 release·capture 설정은 변경하지 않았다.

### 2026-10-07 현재 replay 선택 기능의 안전한 해석

현재 기록 실행기는 별도 offline CLI 경로다. 앱의 인증 진단 API가 같은 사건을 재생한다고
간주하지 않는다. 두 execution mode는 아래 범위까지만 안전하게 해석한다.

| 실행 선택 | 실제로 하는 일 | 말할 수 있는 결론 | 말하면 안 되는 결론 |
| --- | --- | --- | --- |
| `recorded_operations` + include/exclude | window의 선택 workload tag에 묶인 native store 호출을 source 시각 상대 오프셋과 actor 순서로 실행 | 기록된 호출 인수·순서에 대한 전용 DB 동작 비교 | 원인 collector/TR/worker를 껐다거나 그 파생 작업까지 빠졌다는 주장 |
| `collector_with_background` + component | 선택한 collector 입력 prefix를 실제 realtime collector parser에 공급하고, 그 component의 지원 sink만 과거 store-call에서 대체 | 해당 parser/frontier와 남겨둔 background store 호출의 혼합 비교 | TOP20 subscriber·REST·news·다른 component까지 포함한 앱 전체 원인 replay |
| synthetic diagnostic workload replay | 정의된 모양의 DB workload를 전용 DB에서 수행 | 해당 synthetic SQL/store shape의 검증 | 실제 장중 사건 입력·동시성 재현 |

planner는 include/exclude를 **workload_id 호출 필터**로 처리하며, 각 workload tag가 전체
인과 기능과 일치하는지는 증명하지 않는다. 제외된 component의 descendant가 다른 workload tag나
producer component로 기록됐으면 남을 수 있다. 반대로 seed로 예전 결과를 보충하면 제외한 작업의
효과를 되살린다. 따라서 현재 masks는 “기록 호출 포함/제외” 실험이라고만 부르고 기능 ON/OFF
실험으로 부르지 않는다. cross-component source→descendant 연결이 검증되지 않은 기능 제외는
비교 결과를 원인 귀속에 사용하지 않는다.

안전한 현재 지원은 다음과 같다. 기록된 native method가 executor allowlist에 있고, operation
쌍·payload·actor sequence가 완전하며, 선택된 입력 거부가 없을 때만 선택 호출을 실행한다.
실행기 밖 method, censored pair, operation/sequence gap, selected input rejection, projection
adapter 누락은 DB 변경 전 오류로 거부된다. collector mode는 realtime workload tag와 명시한
component의 입력 및 initial-state 표식, 필요한 prefix가 모두 있어야 한다. source reset과
window 직전 사건을 위해 window 시작 전 prefix도 포함하므로 사용자가 선택한 구간만의 입력 재생은
아니다. collector 입력은 cold-with-prefix이며 실제 NAS/PC의 warm RAM state와 동등하지 않다.
동일 component의 latest/minute/finalize/second sinks와 v2 market-state-only dataset batch만
대체한다. peer component, 다른 dataset kind, mixed dataset transaction은 유지하거나 사전 거부한다.

이번 단계에서 확인된 결과: 전체 app의 원인 workload ON/OFF·조합 성능 실험을 현재 기능만으로
충족하지 못한다. 검증 가능한 부분은 (a) 지원 native store-call subset의 전용 DB 정합성·시간
shape 비교, (b) 관측된 collector component parser와 그 지정 sink의 hybrid 비교뿐이다. TOP20·뉴스·
REST 원인 입력, cross-component descendant provenance, warm initial state를 추가하기 전에는
부분 결과를 장중 전체 부하 기준선이나 기능별 인과 효과로 일반화하지 않는다.

## 완료 기준

### 2026-10-07 재생 범위 감사와 다음 구현 결정

이 절의 범위 표는 **확장 전 활성 후보 감사**다. 아래의 첫 collector 확장은 이후 로컬 구현했으며,
운영 활성화와 전용 PostgreSQL 검증은 남아 있다. 로컬 소스와
`artifacts/deferred-trace-candidate-source-20261007`의 배포 후보 사본을 대조했다.
사용자가 활성화했다고 보고한 릴리즈는
`2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f`다. 이번 감사에서 NAS를 새로 조회하거나
활성 릴리즈·예약·제어 상태를 변경하지 않았다. 두 소스의 collector는 동일하고,
capture contract 차이는 거부 사유 세부 계측이며 지원 operation 경계는 같다.

**기록 가능 / 현재 실행 가능 / 원인 로직 실행 가능을 구분한다.** store capture allowlist보다
`diagnostic_recorded_execution._METHODS`가 좁다. 입력이 남았다는 사실만으로 현재 실행기가
그 입력을 재생한다고 보고하지 않는다. 반대로 공개 API 부재나 window 제한 때문에 이미
기록된 입력이 없다고 보고하지 않는다. 아래 분류는 허용 payload의 capture가 성공했을 때의
코드상 능력이며 실제 65분 무손실·장초 성능·전체 coverage 검증 결과가 아니다.

| 기능 | 현재 분류 | 재생 가능한 경계 / 빠진 경계 |
| --- | --- | --- |
| TOP20 순위·membership | 2 + 4 | `top20_membership` 저장 호출은 재생 가능(2). 원인 `ka00198` 논리 응답과 stale/partial 재시도, subscription/membership publish, 준비 scheduling은 미기록(4) |
| 0B collector | 1, 제한 있음 | 실제 parser·accumulator·flush 실행. cold prefix이며 원래 RAM 상태와 같지 않음. 같은 component에서 빠진 0w/0J/0U가 있으면 현재 compiler가 거부 |
| realtime latest | 1 또는 2 | 0B만의 collector 결과는 1, 혼합 latest 원래 인수의 store 재생은 2. 혼합 입력 collector 재생은 미지원 |
| realtime minute / finalize / second | 1 또는 2 | 0B collector로 새로 생성하거나 native store 인수 재생. 같은 component의 과거 4개 sink는 collector mode에서 대체 |
| 분봉 REST·저장 | 2 | replace_minute_bars 및 metadata 인수. 원래 broker queue·응답 parsing·페이지 continuation 로직은 제외 |
| 일봉 | 2 | replace_daily_bars 및 coverage 문서. 재조회·generation·완료 여부 결정은 재실행하지 않음 |
| 기본정보 | 2 | stock_fundamentals 문서/dataset. REST 응답 및 당일 갱신 결정 경계 미기록 |
| 신고가 | 2 | historical_highs 문서/new_high dataset. 계산에 사용된 reader 결과·준비 작업 로직 자체는 미재생 |
| TOP20 최초 편입 외국인·기관 수급 | 1, 한정 | `ka10045` 입력부터 native ingest와 완료 marker까지. broker scheduling·shared in-flight는 미지원; RAM cache는 baseline dataset 요구 |
| 장후·PC 외국인/기관 수급 | 2 + 4 | dataset/store 호출은 2, 해당 수집 owner의 입력·재시도 원인은 현재 미기록(4) |
| 프로그램수급 | 2 | program_flow dataset 및 혼합 latest 저장. 실제 0w 사건은 현재 payload에서 제외됨 |
| NXT 여부 | 2 | stock_nxt_eligibility 문서/dataset. 조회·구독 변경의 원인 로직은 제외 |
| 시장상태·지수 | 2 | market_state/market_index_chart 등 허용 dataset. 0J/0U/0s 원인 입력 미기록 |
| 종목 준비 작업 | 2 + 3 | 허용 read/write 호출은 2. 작업 상태 전이·timer·실패 단계 재시도 재생은 없음. 관측 없는 RAM 판단은 4 |
| candidate/shadow | 2 + 3 | checkpoint save/load는 2. save_shadow_evaluation·sequence 기반 revision reader는 입력이 남더라도 현재 executor adapter 없음(3) |
| 뉴스 | 2 + 3 + 4 | 일반 document 일부는 2. source-page/job/revision 등 news native method는 캡처 가능해도 executor 미지원(3). Naver/DART/page/body 결과와 retry/cursor cause는 미기록(4) |
| REST query cache | 3 + 4 | `save_query`/`load_query`는 capture 가능해도 executor 미지원(3). 논리 REST 요청·응답과 cache/queue outcome은 미기록(4) |
| 외부시장 등 background | 2 / 3 / 4 | external bars store는 2. allowlist 밖 operation은 거부 표식만(3); 별도 process·직접 연결 및 미계측 RAM 작업은 4. background 전체를 지원한다고 묶지 않음 |
| 계좌·주문·lease·자격증명 | 4 (의도적 제외) | native capture 제외. 00/04 원문이나 credential을 이번 확장에 넣지 않음 |

1은 실제 로직, 2는 DB/store 호출, 3은 메타데이터/입력만 있으며 실행 미지원,
4는 해당 입력 미기록이다. 한 기능에 여러 경계가 있으므로 혼합 분류를 숨기지 않는다.
owner context가 operation 기본 group을 덮어쓸 수 있어 `workload_id`만으로 일봉·수급·cache를
각각 선택할 수 있다고 보장할 수 없다. method와 dataset kind/collection/API ID까지 해석한
기능 선택표가 필요하다. 한 native transaction에 여러 기능이 섞이면 역사적 호출을 임의로
행 분할하지 않고 그 선택 조합을 거부한다.

#### 먼저 구현할 최소 확장: collector의 누락된 시장 입력

첫 구현 범위를 **0w·0J·0U 입력 보존과 같은 collector의 재생**으로 한정한다.
새 recorder/파일 형식/상시 task를 만들지 않고 기존 opt-in `collector_inputs`와 bounded
payload worker를 재사용한다. 기본 OFF, deferred RAM 4GiB·100만 event·후속 저장 정책은 유지한다.
기존 0B FID 목록은 그대로 두고 다음 whitelist만 추가한다.

| type | 보존 values FID | 실제 소비 경로 |
| --- | --- | --- |
| 0w | 20, 210, 211, 212, 213 | parse_program_trade_ticks → hub publish + collector latest |
| 0J / 0U | 20, 10, 12, 14, 252, 255, 253 | parse_market_index_ticks → hub publish + latest + market_state history |

각 row의 type 및 기존 item/stk_cd/code 식별 필드는 유지해 KRX/NXT/SOR 구분과 지수 item
001/101을 보존한다. frame 내부 순서, source timestamp, 승인/gap, component/input ID를 유지한다.
실제 parser에 전달되는 최소 FID 원형을 보존하여 parser 변경도 비교한다. 값 전체·계좌 필드를
복사하지 않는다. 기존 100행 chunk와 byte/node 한도 및 capture 실패가 실수집을 막지 않는
계약을 유지한다. capabilities의 event types와 input contract version을 함께 확장하고,
구형 0B trace는 그대로 읽되 빠진 혼합 입력 거부를 새 decoder 때문에 무조건 해제하지 않는다.

재생은 지금의 네트워크 차단 collector를 사용하며 **TOP20 service를 자동으로 켜지 않는다.**
0w를 hub에 publish하는 것과 TOP20 subscriber가 받아 program_flow를 생성하는 것은 다른
경계다(`autonomous_top20._event_loop`). 첫 확장에서는 후자는 여전히 store-only이다.
0g 기준정보, 0s 장운영, VI/조건검색, REST outcome, 뉴스 입력은 후속 경계로 명시한다.
그들이 필요한 원인 실험을 이번 확장으로 지원한다고 표시하지 않는다.

#### 중복 재생과 제외 실험의 규칙

- 같은 producer component의 기존 realtime 4개 sink 대체를 유지한다. 0J/0U로 새로
  만들어지는 `save_dataset_snapshots` 중 **전부 market_state인 호출**만 추가 대체한다.
  다른 component 및 다른 dataset까지 method 이름만으로 제거하지 않는다. 혼합 dataset
  batch는 사전 거부하며 transaction을 쪼개지 않는다.
- 0g가 미기록인데 그 component의 stock_price_references 문서까지 대체하지 않는다.
  collector replay와 같이 실행되는 원래 TOP20 program_flow store는 **혼합 경계 실험**이다.
  해당 TOP20 consumer까지 원인 재생했다는 표현을 금지한다.
- 현재 cause_input_id는 input high-water이며 모든 부모 사건 목록이 아니다. 이를 정확한
  1:1 인과 연결로 사용하지 않는다. 보고서에는 component/method/semantic selector별
  replaced/retained/unsupported 및 새 collector operation의 high-water를 따로 남긴다.
- `전체 - 원인 workload` 실험은 그 원인의 다른 component descendant까지 확인된 경우에만
  허용한다. 예를 들어 realtime 제외 후 과거 TOP20 trade-dependent 결과를 주입한 실행은
  원인 제외 실험이 아니다. 현재 지원하는 store-call 제외와 구분하고, 인과 경계가 불명확한
  요청은 시작 전에 거부한다. 누락 결과를 맞추려고 DB seed/output을 몰래 추가하지 않는다.
- 향후 TOP20/뉴스/candidate 원인 실행을 추가할 때는 자기 timer·queue·clock·초기 상태와
  외부 outcome 공급을 소유한 adapter를 함께 등록한다. consumer 입력으로 넘어가는
  queue 경계까지 causal ownership을 전달하기 전에는 해당 descendant 대체를 허용하지 않는다.

#### 나중에 복원할 수 있는 것과 지금 기록해야 하는 것

이미 보존된 store payload는 새 adapter로 후일 실행 범위를 넓힐 수 있다. 뉴스는 생성 ID의
source→replay binding과 baseline 허용 테이블, query cache는 clock/TTL 계약이 먼저 필요하다.
store read 결과는 현재 type/count 중심 요약이라 이를 원래 service의 입력이라고 쓸 수 없다.

REST 기반 준비·순위 로직 전체를 다시 실행하려면 store 인수만으로는 부족하다. 후속 최소
경계는 broker의 **논리 요청·결과**(허용 API ID, 정규화 body, continuation, 결과 payload,
성공/실패 분류, owner/trigger, request ID, source time)다. 외부 bytes/인증 header는 제외한다.
cache hit를 포함한 결과를 service에 공급하되, upstream 실험에서는 새 코드가 요청한 호출과
기록 outcome을 매칭하고 미기록 요청은 외부 통신 대신 실패시킨다. 과거 요청을 강제로 모두
발생시켜 변경된 cache/scheduling 효과를 없애지 않는다. 이는 첫 collector 확장에 합치지 않는다.

처음 상태 역시 별도 제한이다. 현재 initial_state는 approvals/continuous_from 및
warm_accumulators 여부만 남기고 accumulator·pending queue를 복원하지 않는다.
`cold_with_prefix`, `source_state_equivalent=false`를 유지한다. 동일 baseline/동일 prefix
비교는 가능하지만 실제 개장 초기 상태와 동등하다고 주장하지 않는다. 이후 원인 adapter에는
고정된 초기 상태와 영향 범위를 검증한 warm-up 경계가 필요하다.

#### 검증과 다음 단계

1. collector capture/parser/plan/executor/capabilities만 함께 수정한다. 0B 단독 trace 호환,
   0B+0w+0J+0U frame 순서/FID·시장 보존, 비허용 필드 배제, off 상태 경로를 묶어 검사한다.
2. 동일 component market_state history 이중 저장 없음, peer store 유지, 혼합 batch 거부,
   구형 누락 trace 거부, component별 coverage/원인 제외 거부를 검사한다.
3. 기존 전용 DB baseline으로 latest/minute/second/market_state 정합성, rollback/ACK loss,
   종료 drain, source→replay 식별 연결을 검증한다. 실계좌·운영 DB·외부 네트워크는 사용하지 않는다.
4. 현재 활성 후보와 새 후보의 동일 controlled 입력을 비교해 capture ON/OFF enqueue 비용,
   RAM high-water·copy reject·loop 지연을 측정한다. 이 테스트를 장초 실제 성능 증명으로 부르지 않는다.
5. 검증 후 비활성 후보 stage까지 진행한다. 활성 전환/예약의 capability gate 변경은 그 결과를
   보고 별도 배포 단계에서 처리한다. 현재 활성 릴리즈와 예약을 개발 중 교체하지 않는다.

재생 reader `_CAPACITY`는 후보에서도 이미 **1,000,000**이다(32,768이라는 이전 설명은 정정).
남은 reader 제약은 10분 window, 15분 collector prefix, 32MiB hydration 및 전체 scalar rows
유지다. 이들은 recorder hot path와 분리해 후속 bounded streaming reader로 개선할 수 있다.
65분 입력 보존과 65분 전체를 한 번에 실행 가능하다는 주장은 서로 다르다.

현재 결론: 검증된 capture를 유지하면서 collector 입력부터 최소 보완한다. 이 단계만으로
전체 앱 원인 replay가 완성되지는 않는다. 부분 replay 범위와 미지원 항목을 보고하고,
무손실 capture + 주요 workload 지원 + 조합/제외 + 동일 baseline 비교가 모두 검증될 때까지
사용자가 정한 최종 성공으로 처리하지 않는다.

**로컬 구현 결과(2026-10-07):** `collector-input/v2`가 0B·0w·0J·0U의 최소 FID를 기록하고
같은 실제 collector에서 재생한다. schema-2 및 store-input/v1 codec은 유지한다. 버전이 없는
기존 collector 입력은 v1으로 해석하며 구형 trace의 0w/0J/0U 누락 표식·혼합 latest 거부를
유지한다. 새 버전에서는 같은 component의 market_state-only dataset batch도 대체하며,
peer component·다른 dataset·미기록 0g 문서는 유지한다. 혼합 dataset transaction, 알 수 없는
버전, 금지 type/FID는 native DB 접근 전에 거부한다. 제외된 종류만 있는 빈 message는
메타데이터로 집계하고 parser에는 전달하지 않는다. 보고서는 type별 재생/제외 건수,
대체 operation/component/source call ID와 high-water 연결을 남기며
`cross_component_causal_replay=false`를 명시한다. 이는 TOP20 consumer까지 원인 재생한 것이 아니다.

관련 capture/executor/collector/API 회귀 45건과 새 시장 이력 COMMIT 중 취소·drain 검사 1건이
PC 사용자 실행 환경에서 통과했다. 샌드박스의 Windows asyncio socketpair 생성 정지를
faulthandler로 확인한 뒤 사용자 환경에서 실행했다. 전용 PostgreSQL의 실제 혼합 capture →
offline CLI 재생 → native DB call 연결 → baseline 복구 검사는
`test_recorded_execution_postgres.py`에 추가했으나 아직 실행하지 않았다. NAS source stage,
capture OFF/ON 비용 비교, 활성 전환 및 예약 capability gate 변경도 아직 수행하지 않았다.

> 최종 목표는 같은 장중 사건 기록을 고정된 입력 세트로 사용해서, workload별
> ON/OFF 및 코드 수정 전후를 반복 비교할 수 있는 재현 가능한 부하 실험 환경을 만드는 것이다.

장중 여러 작업을 **동시에 한 capture에 기록**한다. 장후에는 전체, 단독, 하나씩 제외,
선택 조합을 같은 기록으로 재생한다. 한 종류만 수집하거나 매 실험마다 새 합성 입력을
만드는 것으로 이 목표를 완료했다고 보고하지 않는다. 쓰기뿐 아니라 읽기 부하도 대상이다.

기존 `diagnostic_replay.py`는 6종류의 제한된 합성/계수 재생이다.
`diagnostic_collector_replay.py`는 고정된 0B fixture를 실제 collector에 넣는다.
추가한 schema-2 trace는 선택 설정으로 store 공개 메서드 인수와 상류 collector 사건을
보존한다. `diagnostic_recorded_execution.py`는 이 입력을 caller-owned test store에서 실행한다.
명시 allowlist store operation은 actor별 순서·actor 간 동시성을 지킨다. collector mode는
상류 사건을 실제 0B parser·accumulator·flush loop에 넣고 해당 component의 과거 sink만 제외한다.
원본 operation/DB call ID와 replay operation ID를 연결한다. 내부 offline wrapper는 전용
baseline lease에 연결됐으며 실제 PostgreSQL gate와 공개 API 연결은 남았다.
`execution_ready=false`는 이 격리 gate까지 통과한 공개
실행 가능 상태를 뜻한다.

2026-10-06 후속: 공개 인증 trace 시작 API는 `store_inputs`와 `collector_inputs` 옵션을
연결했으며, 둘 다 strict boolean·기본 OFF다. 두 옵션은 기존 recorder의 allowlist native
store 입력과 0B collector 입력을 각각 선택하고, capabilities는 schema-2 지원 및 미측정
overhead를 드러낸다. 관련 로컬 회귀 33건이 통과했다. NAS stage/활성화와 예약 요청의 두
옵션 전달, 실제 장중 capture는 별도 검증이 남아 있다. 기존 offline 선택 실행기는 내부 CLI며
공개 replay/run 비교 API는 아직 없다.

2026-10-05 후속 구현은 별도 `kiwoom_monitor_replay_test` 전용 role/database만 허용한다.
DB 관리자 권한으로 전용 로그인 role과 비어 있는 DB만 만든다. psql에서 아래 SQL을
각각 자동 commit해 실행하고, 비밀번호 입력은 대화식 `\password`를 사용한다.

```sql
CREATE ROLE kiwoom_monitor_replay LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS;
CREATE DATABASE kiwoom_monitor_replay_test OWNER kiwoom_monitor_replay TEMPLATE template0;
REVOKE CONNECT ON DATABASE kiwoom_monitor_replay_test FROM PUBLIC;
GRANT CONNECT ON DATABASE kiwoom_monitor_replay_test TO kiwoom_monitor_replay;
```

```text
\password kiwoom_monitor_replay
```

DB 이름이 이미 있거나 role이 존재하면 자동으로 덮어쓰지 말고 속성과 소유자를 먼저 확인한다.
기존 운영·진단 DB에는 이 준비 SQL을 실행하지 않는다.
새 role 접속으로 `python -m kiwoom_monitor.central_server.diagnostic_replay_database_cli provision`을
실행하면 empty DB 검증 뒤 ownership
marker와 공통 schema를 준비한다. 검토된 replay fixture만 넣은 뒤 `seal`로 기준을 한 번 봉인한다.
처음에는 empty-schema smoke baseline도 가능하며, 이는 데이터 크기나 장중 상태를 대표하지 않는다.
CLI는 URL과 owner token을 명령행에 받지 않고 `KIWOOM_REPLAY_DATABASE_URL`,
`KIWOOM_REPLAY_OWNER_TOKEN`에서만 읽는다. 두 값을 실행 환경에서 설정하고 CLI의 `status`,
`seal`, `restore --baseline-id <sha256>` 명령을 사용한다.
seal 이후 매 run은 manifest ID를 지정해 명시 table allowlist와 revision sequence의 다음
observable 값까지 transaction 안에서 복구한다. Postgres advisory lock과 process lock은
run/drain까지 유지하며, 남은 owned connection이나 별도 client session이 있으면 복구를
거부한다. NAS 전용 replay DB에서 rollback/sequence/foreign-session PostgreSQL gate 4개가
통과했다(2026-10-06, 4/4, skipped=0). 봉인한 `controlled_fixture` baseline ID는
`4c4daa238a7e7d4221234087dce35a5b0956caf6629b05896fca7e8df175bbd4`다. 이는 장중 시작 시점의 실제
운영 DB 상태와 동등하지 않으며 실행 보고서도 이 사실을 유지한다.
기존 기록·옛 trace를 실제 사건 원본으로 승격하지 않는다.

NAS 운영자 검증은 후보 소스의 `scripts/check_recorded_replay_baseline.py --provision`으로
묶어서 실행한다. 기존 server 컨테이너에서 별도 Python 프로세스로 실행하며, 운영 DSN은
`postgres`의 role/database catalog 준비에만 사용한다. 고정 이름의 새 role/database를
준비한 뒤 관리자 DSN과 broker credential 환경을 제거하고 replay 전용 접속으로 4개 gate를
실행한다. 무작위 replay 암호·owner token은 기존 secrets mount 안의 mode-0600 파일에
보존한다. 기존 role은 속성·membership·저장 암호 인증, 기존 DB는 소유자를 확인하며
다른 자원을 채택하거나 암호를 재설정하지 않는다. 봉인된 baseline은 다시 만들지 않는다.
이 operator helper는 공개 API가 아니며 현재 source release를 활성화하지 않는다.

이 문서는 [0B 통합 재현 설계](NAS_RUNTIME_DIAGNOSTICS.md#collector-replay-design)의
단독 구현 범위를 다중 workload 실험으로 확장한다. 기존 transaction·취소·종료 소유권,
인스턴스 시계, 운영 격리 계약은 유지한다. 다음 우선순위는 합성 fixture API만 완성하는
것이 아니라 **다중 작업 원본 기록과 선택 재생의 연결**이다.

## 1. 재생 경계 결정

하나의 capture에 다음 세 종류의 기록을 결합한다.

| 기록 | 목적 | 실제 연결 위치 |
|---|---|---|
| DB 작업 입력 | 실제 reader/writer의 인수·키 관계·도착 시각을 고정하여 선택 재생 | `database.py`의 조립된 store 공개 메서드 진입/반환, 명시 허용 adapter |
| 수집기 입력 | 저장 주기·RAM 집계 변경을 포함한 재생 | 우선 `realtime_collector.py`의 0B 처리 직전, 구독 승인·gap 상태 전이 |
| 실행 관측 | 원본/재생의 SQL·COMMIT·wait와 입력의 연결 | 기존 `postgres_access.py`, `diagnostic_metrics.py`, sampler |

첫 번째 기본 실험은 `recorded_operations`다. 기록된 공개 store 메서드를 **현재 코드의
실제 구현**으로 호출한다. 원래 SQL 문자열을 실행하거나 모든 작업을 일반 UPSERT 하나로
바꾸지 않는다. 실제 읽기 쿼리, 내부 비교, revision 생성, 독립 connection/transaction을
그대로 실행한다. 당시 응용 서비스의 조회 결과를 이용해 새 업무 분기를 생성하지는 않는다.
따라서 SQL/저장 로직과 DB 경합 비교에 유효하며 상위 scheduler 변경 검증은 아니다.

저장 주기 변경을 검증할 때는 `collector_with_background`를 사용한다.
0B는 실제 parser → accumulator → 저장 loop로 실행하고, 다른 workload는 같은 capture의
store 작업을 원래 도착 시각에 실행한다. 이때 **collector가 생성하는 과거 store 작업은
재생 목록에서 제거**한다. 예를 들어 기록된 `save_minute_bars`와 0B로 새로 발생한 같은
분봉 저장을 둘 다 실행해서는 안 된다. 새 collector의 DB 호출 수·간격은 측정 결과다.

선택된 실행 경계를 plan의 `execution_frontier`로 고정한다. 입력을 재생하는 상위 단계와
그 단계가 생성하는 기록된 하위 작업을 동시에 선택하면 compiler가 거부한다.
상위 입력과 DB 작업의 `producer_component` 연결이 없는 구형 trace도 이 혼합 방식에서는
거부한다. 단순히 writer kind가 같다는 이유로 다른 생산자의 저장까지 제거하지 않는다.

`AutonomousTop20Service`, `NewsJobRunner`, `CandidateMonitor` 전체를 기본 실험에서
동시에 시작하지 않는다. TOP20은 구독/보완 수집을 만들고, 뉴스는 job 생성 뒤 wake-up하며,
shadow는 새 revision을 읽으므로 기록된 작업과 중복될 수 있다. 향후 이 서비스 자체를
재생할 때도 해당 서비스의 입력·초기 RAM 상태·시계·하위 작업 제외 계약을 갖춘 adapter로
위 경계 하나를 교체한다. 이번 구현에서 범용 broker/전체 앱 시뮬레이터를 만들지 않는다.

## 2. 선택 단위와 coverage

`workload_id`(작업 소유자), `operation_kind`(공개 store 작업), `writer_kind`(실제 SQL 관측)는
서로 다른 식별자다. 사용자 선택은 workload를 기본으로 하며 필요하면 하위 작업까지 좁힌다.
같은 문서 저장 메서드를 쓴다고 뉴스·TOP20·계좌를 한 workload로 묶지 않는다.

| workload 묶음 | 기록/재생 대상 | 경계의 주의점 |
|---|---|---|
| 실시간 체결 저장 | 0B, latest, 분봉 delta/확정, 초봉 | collector 모드에서는 실제 저장 loop, DB 작업 모드에서는 기록된 store 호출 |
| REST 시장 자료 | 분봉·일봉·기본정보·NXT 여부·신고가·외국인/기관·프로그램 수급, query cache | `market_ingest.py` 및 broker가 만든 실제 읽기/쓰기 인수; REST 재요청 없음 |
| TOP20 | membership/index/entrants, 진입 준비의 reader와 coverage/doc writer | 기록된 작업 재생에서는 순위 scheduler를 추가 시작하지 않음 |
| 뉴스 | source page/article/body/rule, claim/finish/retry, 관련 reader | 빈 claim만으로 처리 중 뉴스 부하를 대신하지 않음; job/기사 ID 관계 보존 |
| shadow | revision reader, 평가, checkpoint | 기본 모드는 기록된 인수; checkpoint만으로 monitor 전체라고 표시하지 않음 |
| 시장 이벤트·외부시장 | VI/cohort/상한가, 외부시장 봉·상태 | 기존 native 저장 메서드와 독립 commit 보존 |
| 앱/API 조회·기타 장중 작업 | 실제 store reader, 문서 조회·설정 및 계좌 복구 저장 등 | account ID는 일관된 실험 ID로 변환; 주문 transport·자격증명 활성화는 실행하지 않음 |

이 표는 **필요한 범위이며 구현 완료 목록이 아니다**. 명시적인 method/codec/argument
variant adapter 목록을 기존 writer inventory와 대조한다. `upsert_documents`처럼 범위가
넓은 메서드는 collection까지 허용해야 한다. credentials/vault·토큰·주문 실행·schema
migration은 payload 기록/자동 dispatch에서 제외하고 제외 건수와 사유만 표시한다.

capture manifest와 compiler는 workload별로 observed / captured / replayable / selected /
unsupported / dropped / left-or-right-censored를 공개한다. 선정 구간의 `전체`는
지원되지 않는 대상 작업이 있으면 실행 전 실패한다. 사용자가 명시적으로 제한된 범위를
선택한 실험만 partial로 실행할 수 있다. 미등록 호출을 자동으로 뉴스나 기타 합성 SQL로
바꾸지 않는다. 우리 앱 밖 NAS 작업은 host/storage 관측에 남기지만 재생 대상은 아니다.

coverage의 분모는 manifest에 선언한 관측 경계다. 기존 관측 연결 밖의 직접 DB 경로는
현재 저장 경로 inventory와 대조해 별도 미계측 목록에 둔다. 관측된 호출만 100%라는
이유로 앱 전체 DB 접근을 포괄했다고 표시하지 않는다. 업무 진입 wrapper가 있어도 대응
DB call 연결이 없거나 그 반대인 경우 coverage 불일치로 집계한다.

같은 native transaction 안의 봉·metadata·revision을 별도 workload처럼 잘라 commit하지
않는다. 분봉 delta와 finalize처럼 계약상 묶여야 하는 선택은 registry에 제약을 둔다.
필수 선행 작업을 끈 조합은 누락된 의존성을 알려 거부하며 자동으로 다시 켜지 않는다.
따라서 `단독`은 계약을 만족하는 최소 묶음이며 모든 SQL 조각의 독립 실행을 뜻하지 않는다.

## 3. 사건·인수·관계 계약

새 schema version은 다음 최소 기록을 요구한다.

- capture/producer ID, 연속 seq, wall/monotonic 기준점, source release 및 코드 fingerprint.
- workload_id, producer_component, actor_id, actor_sequence, operation_id,
  parent operation/input ID, API/source/flush ID 및 원본 DB call ID 목록.
- 입력 도착 시각과 실제 메서드 시작/완료 시각. 도착/큐 대기를 알 수 없으면 null.
- 명시 method ID + codec version, 불변 인수 payload 또는 payload blob reference.
- 반환 상태/오류 종류, 제한된 결과 검증값, 생성 ID binding 및 그 이후 인수의 참조 관계.
- 실제 attempted/changed/duplicate/revision/SQL/commit/rollback 수와 시간은 기존 관측에 연결.

price·volume·날짜·시장·동일 key 재등장·operation ID 재시도 관계를 보존한다.
매 호출에 다른 임의 종목을 생성하는 이전 합성 방식을 사용하지 않는다. DB 입력은
메타데이터만으로 복구할 수 없으므로 재생에 필요한 실제 인수를 보존해야 한다.
큰 shadow 문서나 반복 봉 페이지는 worker에서 content hash로 동일 blob을 재사용할 수
있지만, caller에서 직렬화·hash·압축을 수행하지 않는다.

타입은 허용된 primitive/collection과 지정 dataclass/enum codec만 지원한다. pickle,
eval, 임의 module/class import, record에 적힌 임의 메서드 호출, 자유 SQL은 금지한다.
capture wrapper는 store 인수를 바꾸거나 generator를 소비하지 않는다. 지원하지 않는
stream/형태는 unsupported로 남긴다. mutable 인수는 메서드가 수정하기 전에 제한된
불변 사본을 만든다. 실패 시 업무 호출을 계속하고 capture 불완전 상태를 남긴다.

기록은 최상위 공개 store 호출 단위다. 메서드가 내부에서 다른 store 메서드를 부르면
하위 동작은 관측 span으로만 남기며 독립 dispatch하지 않는다. capture context를
`asyncio.to_thread`까지 전달하고 기존 source/request ID와 결합한다. logical actor는
TOP20 schedule/종목 준비 task/news worker/collector flush/API request 같은 실제 실행
주체다. 재사용되는 OS thread ID를 actor로 쓰지 않는다. 관계가 불명확하면 unknown으로
남기고 해당 구간의 causal fidelity를 확인 불가로 표시한다.

반환 ID가 이후 입력에 쓰이면 adapter가 원본 ID → 재생 ID binding을 만든다.
기사 revision·job·accepted sequence 등은 무작정 문자열 치환하지 않는다. 자연키와
기존 seed ID는 유지하고 새 ID 참조는 명시 codec이 해석한다. ordinal cursor나 FK가
해결되지 않는 조합은 사전 검사 또는 실행 중 invalid가 된다. 원본 오류를 강제로 주입해
재생 성공이라고 맞추지 않는다. 장애 주입 실험은 별도 명시 모드다.

## 4. 가벼운 capture와 보존

기존 DB scalar trace는 유지하고, **입력 payload capture는 명시적인 별도 옵션**으로
켠다. 동일 master session/TTL 아래에서 함께 시작·끝내며 기본 OFF다. capture 시작 때
collector의 승인 source/연속 수신 기준/gap 및 상태 유효성도 기록한다. 0B payload
whitelist와 토큰/LOGIN/00·04 원문 제외는 기존 설계를 유지한다.

caller에서는 bounded copy + enqueue만 수행한다. 추가 조회, 파일 접근, payload JSON
직렬화, fsync, 큐 공간 대기는 하지 않는다. copier 동시 실행도 try-acquire 한도로 묶어
복사 중인 객체가 queue 밖에서 무제한 늘어나지 않게 한다. 한도 초과는 조용한 sampling이
아니라 dropped/unsupported 상태다. 종료된 capture token으로 다음 세션에 쓰지 않는다.

2026-10-06부터 streaming recorder의 보수적 RAM charge 예산은 256MiB다. worker 직렬화·청크
buffer에 16MiB를 예약하고 queue/pending/copy-in-progress에는 최대 240MiB를 허용한다.
scalar trace에 8MiB를 별도로 남기는 copy admission 경계를 유지한다. 이는 프로세스 RSS의
hard cap이 아니며 manifest/hash index와 기존 앱 메모리는 별도로 측정해야 한다. copier 동시성 2,
단일 불변 사본 charge 최대 8MiB, 이벤트 수 최대 32,768을 함께 적용한다. 이는 측정으로
승인해야 할 초기 한도이며 65분 무손실 보장이 아니다. queue·blob 보존 한도는 byte 수와
실제 RSS를 모두 검증한다. payload는 chunk 크기에 맞춰 worker가 분할/조립한다.

2026-10-07 deferred-RAM 후보는 별도 `persist_at` 옵션을 요청한 trace에만 4GiB charge
예산과 1,000,000 event cap을 적용한다. Linux host `MemAvailable` 및 cgroup memory headroom이
4GiB+1GiB보다 작으면 시작을 거부한다. 이 값은 Python 실제 RSS의 hard cap이 아니므로 운영
수용량 보장은 아니다. 65분 입력이 끝나면 token을 닫고 queue/payload를 RAM에 유지한다.
master TTL 만료는 장후 flush를 앞당기지 않는다. 운영자는 예약한 20:10 KST 시각을 epoch 초로
`persist_at`에 전달해야 하며, RAM 대기 상태의 API는 새 capture/run을 막는다. 서버 종료 또는
재시작 전에는 volatile tail이 복구되지 않으며 manifest는 incomplete/interrupted로 남는다.

deferred flush는 64KiB write 단위에 최대 1MiB/s pace를 두고, chunk sync 뒤 최대 5초 추가
cooldown을 둔다. 시간이 지난 pace credit을 모아 따라잡지 않는다. deferred mode에서는 growing
blob index를 매 chunk 다시 sync하지 않고 최대 60초 간격과 마지막에 저장한다. JSONL/payload
청크는 manifest 참조 전에 각각 fsync/rename한다. 여유 메모리 18,077,790,208 bytes는 2026-10-07
NAS host snapshot의 한 시각 표본이며, container 여유나 이후 시장 시간 peak를 포함한 승인
근거로 사용하지 않는다.

보존 worker는 5초 또는 high-water 알림에 깨고 backlog를 연속된 bounded chunk로
배출한다. 기존처럼 5초에 청크 하나만 쓰도록 제한하지 않는다. JSON·blob hash·파일 쓰기·
묶음 fsync는 이 worker만 수행한다. checksum과 manifest 참조가 함께 확정된 입력만
complete다. seq는 유실 점검용이며 전역 commit 순서가 아니다. 재시작/디스크 오류/
quota 도달/끝 경계에 걸친 작업은 명시적으로 incomplete 또는 censored다.

payload마다 파일/fsync를 만들던 경로는 청크별 최대 16MiB `.payloads` 묶음으로 바꾼다.
worker가 payload를 순서대로 파일에 쓰고 묶음당 한 번 fsync한 뒤 rename한다. 이후 이벤트
JSONL 청크도 fsync/rename하고 manifest를 갱신한다. manifest의 hash별 `name/offset/bytes`는
확정된 묶음만 참조하며 읽을 때 해당 구간의 SHA-256을 검증한다. 같은 payload hash는 청크
사이에도 재사용한다. 기존 hash별 JSON payload와 API payload 반환 bytes는 호환한다.
묶음 크기 또는 JSONL 1MiB 한도에 걸린 suffix는 순서를 유지해 다음 청크에 기록한다.
fsync/rename 실패 후 orphan/partial 파일은 재생에 공개하지 않는다. 디렉터리 sync를 추가로
검증하지 않았으므로 전원 장애 내구성을 새로 보장하지 않는다. `payload_fsync_count`,
`payload_storage`, `payload_batch_limit_bytes`, `memory_limit_bytes`를 manifest/status에 남긴다.
일반 streaming trace의 worker 깨우기 기준은 16MiB/4,096 queued events와 최대 5초를 유지한다.

기존 `complete`는 수용한 이벤트의 배출 완료를 뜻한다. 입력 거부가 있으면
`input_rejected`/reason/coverage를 함께 검사해야 하며 complete만으로 전체 입력 무손실을
판정하지 않는다. 선택 plan은 해당 workload의 `input_rejected` 사건을 계속 거부한다.
2026-10-06 로컬 후속에서는 거부 이벤트에 `load_documents`의 collection 이름과 복사 예산
초과의 byte/node 구분 및 관측 한도를 추가한다. 원본 payload는 추가하지 않으며 기존 reason과
production store 동작은 유지한다. 이 변경은 NAS v3에 아직 반영되지 않았다.

payload 보존은 같은 볼륨에 I/O를 추가한다. capture OFF/ON에서 producer 추가 시간,
DB 처리량, 수신 지연, RSS, queue/drop, 기록량과 flush/fsync를 대조해야 한다.
목표는 기존 scalar trace 기준 p95 추가 0.1ms·처리량 저하 2% 이내이며 payload 포함
capture가 이를 충족하는지는 미측정이다. 실패하면 한도를 숨겨 늘리거나 sampling한 채
전체라고 하지 않고 비용 원인을 먼저 조정한다. 실제 peak 및 2배 burst의 65분 gate가
통과하기 전에는 개장 65분 수집 준비 완료라고 보고하지 않는다.

## 5. 초기 상태와 반복성

experiment bundle은 immutable capture, 선택 window, 고정 `baseline_id`, 설정,
codec/adapter 버전, 입력 checksum으로 구성한다. 매 반복은 **같은 논리 DB 초기 상태와
sequence 값**, 같은 collector 초기 상태/기록 prefix, 같은 1배속 시계 조건에서 시작한다.
바로 전 실험의 변경된 DB를 다음 실험의 출발점으로 재사용하지 않는다.

새 실험의 DB는 전용 `kiwoom_monitor_replay_test`로 고정하고 기존 통합검사용
`kiwoom_monitor_diagnostic_test`와 분리한다. 이것은 이번 설계에서 새로 필요한 자원이며
아직 생성하지 않았다. API 요청에서 DSN/DB명/SQL/경로를 받지 않는다. 연결 전 URL DB명,
연결 후 current_database, provision 시 생성한 ownership marker를 모두 검사한다.
전용 role/DB 준비 권한이 없으면 provisioning 미완료로 처리한다.

baseline 준비/restore는 장후의 별도 유지보수 작업이다. registry에 정의한 실험 자료와
공통 schema로 baseline을 만들거나, 승인된 일관된 snapshot을 정제하여 import한다.
장중 capture에서 대량 DB dump나 before-image 추가 SELECT를 수행하지 않는다.
실험 시작/반복 사이 restore는 프로세스 run lock과 DB advisory lock 아래, 이전 owned
connection이 모두 종료된 뒤 명시 테이블·sequence 목록에만 적용한다. baseline의 table
digest/row count/sequence와 설정 checksum을 검증한다. 운영/기존 진단 DB는 reset하지 않는다.
외부 세션이나 marker 불일치가 있으면 restore를 거부한다. 성공 후 baseline도 보존한다.

`baseline_origin=controlled_fixture`와 `capture_start_snapshot`을 구분한다. 고정 fixture는
반복 비교에는 유효하지만 실제 장중의 table 크기·key 분포·오래된 row·cache·bloat와
같다는 증거가 아니다. capture가 끝난 뒤 얻은 DB snapshot을 시작 상태라고 부르지 않는다.
capture 시작과 일치하는 snapshot이 없으면 `source_state_equivalent=false`다.
초기 RAM을 완전히 기록하지 못한 collector는 기존 `cold_with_prefix` 제한을 유지한다.

ON/OFF마다 선택된 작업 이외의 **입력**은 고정하지만 DB 결과까지 같다고 보장하지 않는다.
상류 쓰기를 빼면 revision reader가 반환하는 행 수 등이 달라질 수 있다. 결과 변화,
changed-row 변화, dependency 누락을 보고서에 나타내고 직접 비용 감소와 데이터 상태 변화의
영향을 분리한다. 꺼진 작업의 결과를 측정 중 몰래 DB에 넣어 나머지 결과를 맞추지 않는다.
hard dependency를 만족하지 못하는 선택은 valid experiment로 승인하지 않는다.

논리 baseline 복구로 PostgreSQL/OS cache와 물리 저장 상태까지 같아지지는 않는다.
같은 준비 절차·warm-up과 교차 순서 반복으로 편차를 보고한다. 운영 cluster cache를
재시작/삭제하여 초기화하지 않는다. schema 의미가 다른 코드 버전은 자동 paired 비교를
거부하고 별도 baseline 변환 검증을 요구한다.

## 6. 일정·동시성·실험 구성

원본 mono 기준 도착 간격과 시장 wall 시각 위상을 보존한다. 하나의 await loop로 모든
writer를 직렬화하지 않고, 원본 actor 내 순서/의존성을 지키며 다른 actor는 함께 진행한다.
연결·transaction은 기존 store 메서드가 소유한다. 입력을 제출한 시각, worker가 실행한
시각, 완료 시각을 각각 남겨 replayer 자체 대기와 DB 대기를 구별한다.

1배속 실험에서 처리 용량이 부족하면 늦은 입력을 삭제하거나 완료 시각을 강제로 맞추지
않는다. 같은 actor의 선행 완료가 늦어지면 후속 실행도 늦어지고 그 lag를 공개한다.
원본보다 느리거나 빠른 코드가 실제 동시 실행 수를 바꾸는 것은 측정 대상이다.
그런 run을 `timing_preserved=true`로 보고하지 않는다. scheduler lateness, actor/dependency
대기, concurrency cap 대기를 따로 낸다. 최대 제출 지연 100ms/누락 0 기준은 명시적
측정 기준이며 실제 장중 경합이 동일했다는 증명은 아니다.

기본 suite는 같은 bundle/window로 다음을 생성한다.

1. 선택 범위 전체 baseline.
2. workload 하나씩 단독(제약을 만족하는 최소 묶음).
3. baseline에서 workload 하나씩 제외.
4. 사용자가 선택한 조합/쌍.
5. 동일 mask·baseline에서 실제 코드 A/B를 교차 반복.

코드 A/B는 검증된 immutable source release ID와 실제 파일 checksum으로 선택한다.
운영 active pointer를 바꾸며 비교하지 않고, 동일한 runtime image의 격리된 replay 자식
프로세스에서 해당 release를 import한다. 허용 release 목록 밖 경로·symlink는 거부한다.
자식에게는 replay DB 전용 접속 정보와 실험 설정만 전달하고 운영 DSN·브로커 토큰·
주문/계좌 callback은 전달하지 않는다. parent는 자식 종료뿐 아니라 DB 작업 종료를
확인한 뒤 restore/다음 반복을 진행한다. 코드 A에 같은 입력 adapter가 없으면 비교 불가다.
옛 1초 저장을 흉내 낸 루프를 만들어 실제 과거 코드 성능이라고 표시하지 않는다.
Python/dependency/image와 계측 버전 차이도 보고하며 이를 숨긴 paired 결과는 승인하지 않는다.

새 workload를 adapter로 등록할 때 source 등록과 같이 역방향 coverage 검사를 추가한다.
실행 전 compiler가 immutable plan/hash를 만들고 API가 포함/제외/제약/미지원 항목을
반환한다. 실행 중 선택을 바꾸지 않는다. 보고서에는 요청한 선택과 실제 실행 선택이 같음을
검사한다. native transaction 내부 요소를 toggle하려면 별도 코드 변경 실험으로 둔다.

window는 `[start,end)`다. prefix/warm-up, 측정, drain, 검증/정리 시간을 구분한다.
작업은 시작 시각 기준으로 포함하고 window를 넘는 완료는 drain으로 따로 보고한다.
캡처 시작 전 이미 열린 작업과 누락된 의존성이 있으면 확대 prefix나 다른 window를 요구한다.
65분 원본 보존과 65분 동시 재생은 별개다. 첫 공개 실험은 window 최대 10분,
prefix 포함 입력 span 최대 15분으로 시작하고 예상 event/byte 한도를 preflight한다.
기존 120초 replay 상한을 조용히 바꾸지 않고 새 모드의 한도·TTL을 명시한다.

## 7. 보고서와 완료 판정

- bundle/plan/baseline/code/config fingerprint, 선택 범위와 support coverage.
- 원본 입력 수 → 실제 투입 수 → skipped/dropped/unsupported, input-to-operation-to-DB ID 연결.
- workload별 reader/writer 수, SQL 수, 실제 INSERT/UPDATE/no-op/revision, commit/rollback/error.
- acquire/execute/COMMIT p50/p95/max, 초 단위 COMMIT 분포·동시 transaction·actor queue.
- backend와 시각이 일치하는 wait/blocker 표본; 미관측은 null이며 과거 wait를 추정하지 않음.
- interval-wide WAL/DB transaction/device busy/queue와 capture/replayer 자체 비용.
  공통 WAL LSN 또는 pg_stat_wal 차이를 개별 writer의 전용 WAL로 부르지 않음.
- final output digest/불변식, 원본과 달라진 reader 결과 및 changed-row 분포, baseline 복구 상태.
- warm-up/drain을 뺀 측정값, 입력 일정의 보존 여부, 코드별 반복 분포와 표본 수.

DB 오류 0만으로 valid가 아니다. 선택·payload·초기 상태·의존성·최종 자료 검증을 통과해야
paired 결과를 승인한다. 원본 상태 동등성, 논리 반복성, 입력 일정 보존, NAS 환경 동등성은
각각 다른 필드다. 단독/제외 효과는 cache·I/O 경합 때문에 가산적이지 않으므로 합계만으로
각 workload의 확정 기여율을 만들지 않는다. p95는 작은 표본이면 개별 값도 함께 표시한다.

## 8. 구현 파일과 다음 단계

새 파일은 독립 책임에 한정한다. 호출을 전달하기만 하는 manager/service는 추가하지 않는다.

| 위치 | 책임/수정 |
|---|---|
| `diagnostic_trace.py` | payload 입력 옵션, byte budget·worker drain·blob/checksum·manifest·coverage, checksum/sequence 검증을 포함한 선택 window reader |
| 새 `diagnostic_replay_contract.py` | 명시 operation/codec/선택 제약 registry, 불변 capture 값과 plan 검증 |
| `database.py` 조립 지점 + 명시 store 경계 | opt-in 최상위 operation capture; 내부 SQL/connection/transaction 변경 없음 |
| `postgres_access.py` | operation/input context와 기존 DB call의 연결 |
| `realtime_collector.py` | 0B/승인/gap 원인 입력 기록 및 component 식별; input-only 경로 유지 |
| `diagnostic_replay.py` | (후속, 미구현) recorded operation 실행/결과 binding; 이전 synthetic mode 유지 |
| `diagnostic_collector_replay.py` | (후속, 미구현) recorded 0B와 배경 operation을 공유 시계로 실행, 중복 descendant 제거 |
| `diagnostic_runs.py`, `app.py` | (후속, 미구현) plan/capabilities, 명시 새 모드, master/run lock/TTL, 소유 작업 drain/report_url |
| `diagnostic_replay_baseline.py`, `diagnostic_replay_database_cli.py`, `scripts/check_recorded_replay_baseline.py` | 전용 replay DB baseline 생성·검증·복구와 operator acceptance. NAS PostgreSQL gate 4/4 통과; API에서 임의 명령 실행 없음 |

**구현된 로컬 핵심 (2026-10-05):** 명시 registry와 제한 codec, opt-in 불변 입력 복사,
비동기 blob/chunk 보존·checksum·quota, actor/workload/component 및 DB call 연결,
0B/승인/gap 원인 입력 capture, 선택 plan compiler를 추가했다. collector 재생 계획은
해당 component에서 나온 과거 sink operation만 제거하고 같은 method를 호출한 peer는
유지한다. 선택 구간의 sequence, pair, codec, workload, mixed 0w/0J/0U 및 미지원 입력을
검사한다. 기존 고정 collector fixture는 회귀로 유지한다.

관련 로컬 검사 191건이 통과했다(핵심 capture/trace/PostgreSQL access/collector 80건,
뉴스/TOP20/shadow/REST loop 111건). 이는 로컬 단위·mock 저장 경계 검사다. 새 capture의
전용 PostgreSQL 원자성 검사, NAS overhead gate, 실제 장중 입력 capture는 아직 아니다.
NAS에 배포하지 않았으며 capabilities/API도 이 기능을 제공한다고 광고하지 않는다.

capture 전용 PostgreSQL gate는 `test_recorded_workload_capture_postgres.py`의 4건으로 준비했다.
OFF/ON의 native 연결·SQL/commit 수와 typed input, 배치 중간 SQL 실패 후 canonical/metadata/revision
전체 rollback, 진단 blob 쓰기 실패의 native COMMIT 보존, owner COMMIT 대기 중 peer 독립 완료와
COMMIT ACK loss 재시도·중복 이력 방지·범위 정리를 검사한다. PC의 관련 capture 회귀 9건은
통과했지만 전용 PostgreSQL URL이 없어 새 4건은 실행되지 않았다. 실제 PostgreSQL gate는
기존 collector→DB 2건과 묶어 NAS 전용 테스트 DB에서 실행해야 한다.

**남은 구현:** 미지원 operation 및 generated-ID/reference adapters, 실제 기록 capture를 사용하는
end-to-end replay와 workload별 A/B 실행,
코드 A/B 별도 실행, plan/run API와 report 연결. 내부 시간 replay는
caller-owned test store에 한정되며, replay PostgreSQL/A-B 동등성은 검증하지 않았다. compiler의
공개 `execution_ready`는 false다. 각 gate는 관련 동시성·PostgreSQL·실패 정리 검사와 함께 구현한다.

**bounded reader 상태 (2026-10-05):** `recorded_window_events`는 완전한 schema-2 trace만
받고 전체 chunk checksum·sequence·manifest 연속성을 확인한다. operation payload는 선택
window에 들어온 시작 이벤트만 hydrate하며, collector 모드는 지정 component의 capture 시작부터
선택 window 끝까지 prefix payload를 포함한다. 선택 window는 최대 10분, collector prefix 끝은
capture-relative 15분, hydrate한 고유 payload 총량은 32MiB다. 선택 끝이 manifest의
`finished_mono_ns`보다 뒤면 거부한다. 이는 reader 테스트 통과 상태이지 전체 긴 capture의
성능/메모리 overhead나 collector replay runner 완료를 뜻하지 않는다.

공개 API는 별도 plan 준비 요청에서 capture ID·window·baseline ID·허용 release ID·
workload mask를 검증하고 `plan_id`와 coverage를 반환하도록 추가한다. 기존 run API의
새 workload 모드는 이 확정 plan ID만 받는다. 호출자가 arbitrary payload/SQL/함수명/
DB URL을 업로드하여 실행시키는 통로를 만들지 않는다. 기존 synthetic 요청은 기존 의미로
유지하며, 미구현 모드를 capabilities에 먼저 올리지 않는다.

이후 같은 계약 안에서 실제 operation adapters·전용 baseline/restore·통합 runner·API를
연결한다. 선택한 capture 범위의 codec·용량·실패 격리 gate를 통과한 후보만 운영에
적용한다. 일부 범위부터 검증하더라도 미지원 범위를 공개하고 전체 준비 완료로 표시하지
않는다. 단계별 gate:

1. OFF 무부하 경로, 불변 사본, 동시 actor/seq, nested method 한 번 dispatch, whitelist,
   부분 payload/queue/디스크 오류·restart·TTL를 묶어 검사.
2. 동일 key 반복/부분 변경/중복·실패/ID binding·reader, native rollback/ACK loss,
   같은 actor 직렬·다른 actor 동시, 취소 후 실제 thread/commit drain 검사.
3. 단일 capture로 전체·단독·제외·조합을 실행. 누락/중복 0, 관계·입력 동일성,
   collector + background에서 historical descendant 이중 실행 0 검사.
4. 동일 baseline 복구 후 같은 코드 3회로 논리 결과 재현, 코드 A/B 같은 bundle 비교.
   전용 DB명/marker/역할/lock 불일치와 외부 세션에서 reset 거부 검사.
5. NAS capture OFF/ON·65분 peak/2배 burst 비용과 완전성 gate, 실제 다중 작업 capture,
   장후 재생/코드 비교. 이 전에는 전체 장중 재현 완료 또는 병목 개선 완료로 보고하지 않음.

각 구현 gate가 끝나면 저장소 모델 정책대로 다음 단계의 모델을 다시 판단한다.
이 문서 작성으로 NAS 활성 release, 진단 예약, DB, capture 설정은 바뀌지 않았다.

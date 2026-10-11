# 장중 경량 trace와 장후 선택 재생 설계

> 문서 역할 정리(2026-10-11): 경량 scalar trace·synthetic/부분 recorded-operation replay의 독립 계약을 유지한다. 전체 원인 입력 Replay의 후속은 [기록 workload 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md), 현재 완료/잔여는 [현재 상태](CURRENT_STATUS.md)·[O12](OPEN_ITEMS.md)를 따른다. 두 설계의 보존 범위·완료 조건은 서로 대신하지 않는다.

2026-10-05 후속 [고정 장중 입력의 다중 workload 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)은
실제 입력 보존·전체/단독/제외·수정 전후 비교를 추가한다. 이 문서의 scalar trace와
synthetic replay는 유지하지만 실제 payload/key 관계가 기록된 것으로 취급하지 않는다.
당시에는 새 payload capture와 처리량·보존 한도 변경을 구현하지 않았다. 이후 단계의 구현·검증 기록은 아래 날짜별 절에 남기며, 현행 완료·잔여 판단은 위의 현재 상태와 O12를 따른다.

2026-10-05 · O12 · **NAS trace smoke 및 뉴스/shadow·query_minute 합성 replay 완료; 65분 burst와 바이트 기준 청크 검사 통과; chunk-bound 회귀 18건 NAS test container 통과; 실제 NAS 65분 내구성·오버헤드·장중 원본 capture 미완료**

## 2026-10-05 당시 목표와 순서

1. 기존 토요일 trace는 장중 원본이 아니다. NAS active release `2026.10.03-db-minute-replay-v1-388841088675094c`에서 실행한 선택 replay는 합성 shape 검증으로만 해석한다.
2. 다음 예정 capture window는 2026-10-06 **08:55~10:00 KST**, 특히 09:00~09:10이다. 이 문서와 통과 테스트만으로 예약이나 실제 자료 확보가 완료된 것은 아니며 실행 전 거래일과 NAS control state를 확인한다.
3. capture 후 trace에 저장된 writer별 counters·timing으로 재생 가능 범위를 정하고, dedicated DB에서 writer 단독·조합·제외 실험을 한다. 미지원 writer를 조용히 빼고 전체 재현이라고 부르지 않는다.

현재 `measure`는 최대 300초, `_DB_CALLS`는 프로세스별 50,000건, DB 호출 API는 조회 구간 1,800초로 제한된다. 경량 trace와 master TTL은 최대 7,200초이며, 이를 이용해 65분 raw를 메모리에 쌓는 방식은 채택하지 않는다. 부분 replay는 빈 뉴스 claim·inline shadow와 명시적인 synthetic shape 시나리오를 요구하는 `query_minute` adapter를 지원한다.

## 결정: 별도 경량 trace, 기존 세부 capture는 별도 유지

```text
기존 독립 DB 연결/transaction
  → 시작/완료의 작은 불변 이벤트 → bounded RAM queue
                                      ↓
                              background 파일 보존 1개
                                      ↓
                         session/producer별 chunk + manifest
                                      ↓
                  선택 구간 compiler → 전용 DB writer adapter
```

파일 보존 worker만 하나이며 DB 작업을 처리하거나 직렬화하지 않는다. connection ownership, transaction/commit, retry, isolation, durability, writer 스케줄은 변경하지 않는다.

새 모듈 `diagnostic_trace.py`는 queue·파일·세션 수명을 소유한다. 이는 파일 I/O를 DB 호출 경로에서 분리하기 위한 독립 책임이다. 범용 이벤트 버스나 plugin framework는 만들지 않는다. `diagnostic_replay.py`는 입력 검증·선택·재생 책임을 유지한다.

`postgres_access.py`는 legacy raw capture와 **독립된 trace token**을 호출 시작에 얻는다. `_publish`의 기존 raw OFF 조기 return 앞에서 경량 완료 이벤트를 전달한다. native context, 기존 connection wrapper, 연결 실패, rollback, commit 실패까지 동일한 호출 ID를 사용한다. trace ON만으로 `diagnostic_capture_active()`가 true가 되어 뉴스 wait probe가 켜지면 안 된다.

업무 thread의 추가 작업은 이미 확보한 scalar 값 복사, 시각 기록, 짧은 queue 임계구역으로 제한한다. 파일 읽기/쓰기, JSON 직렬화, 압축, fsync, stack 검사, SQL parameter 순회, 추가 DB query는 하지 않는다. queue 공간을 기다리지 않는다. mutex 자체는 짧게 경합할 수 있으므로 lock-free/영향 0이라고 표현하지 않는다.

## 이벤트 계약 v1

| 공통 식별/시각 | 내용 |
|---|---|
| `trace_id`, `producer_id` | 하나의 capture와 프로세스 epoch. 재시작 시 producer를 반드시 바꾼다 |
| `seq` | producer 내 이벤트 순번. queue 수용 전에 발급하며 known drop도 순번을 소비한다 |
| `event_type` | `call_start`, `call_end`, 선택적인 `domain`, `health` |
| `call_id` | 시작/완료/domain 결합 키 |
| `wall_ns`, `mono_ns` | 외부 시각 상관과 프로세스 내 정렬/시간차. 단조시계 기준점은 manifest에 보존 |
| family/kind/operation/source/access mode | 기존 context의 제한된 문자열, 자유 SQL/parameter 제외 |
| `source_release`, `schema_version` | manifest에 한 번 기록하고 이벤트에서 참조 |

`call_start`에는 rows_attempted, parent_call_id와 이미 존재하는 flush_id를 담는다. `call_end`에는 backend PID/DB 이름, acquire/execute/commit/rollback/total 시간, commit 시작·끝, SQL 호출 수, commit/rollback 수, outcome, exception **종류**를 담는다. 알 수 없는 값은 null이다. 모든 SQL 실행창과 전체 exception 메시지는 경량 trace에 넣지 않는다.

기존 `record_writer_transaction`의 domain_counts·payload size·flush_id는 raw capture OFF라도 trace에 작은 `domain` 이벤트로 전달할 수 있게 한다. 이 함수는 성공 후 호출되므로 실패 여부와 transaction 개수의 정본은 공통 call 이벤트다. 필드 whitelist·문자열/배열 상한을 두며 어떤 값이 생략됐는지 count를 남긴다. rows_attempted를 실제 INSERT/UPDATE 행 수로 취급하지 않는다.

seq는 **전역 실행 순서나 DB commit 순서가 아니다**. 여러 producer는 epoch+seq로 식별하고 wall/mono 시간으로 겹침을 계산한다. call 시작만 있는 경우 crash 또는 capture 끝을 넘은 호출로 구분하며 정상 완료로 간주하지 않는다. 시작 전 이미 실행 중이던 호출은 left-censored로 표시한다. 선택 시간창은 반열림 `[start,end)`를 사용하며 경계에 걸친 호출 수를 별도 표시한다.

## 보존, 용량, 장애

초기 설정값은 검증할 예산이며 달성된 성능 수치가 아니다.

- producer당 queue 최대 32,768 이벤트와 보수적 RAM charge 256MiB 예산 중 먼저 도달하는 제한을 적용한다(2026-10-06 상향). worker 예약 16MiB를 제외한 queue/pending/copy 예산은 최대 240MiB이며 scalar용 8MiB 여유도 유지한다. manifest/hash index를 포함한 프로세스 RSS hard cap은 아니므로 실제 RSS 증가를 별도 검증한다.
- background worker는 최대 5초 또는 16MiB/4,096 queued events 알림에 깨며 backlog가 있으면 연속 배출한다. 청크당 UTF-8 JSONL은 최대 1MiB, payload 묶음은 최대 16MiB다. batch의 남은 suffix는 producer 순서를 지켜 worker 메모리에 두며 공개 `queued`에는 pending도 포함한다. 이벤트 한 건이 한도보다 크면 읽을 수 없는 청크를 만들지 않고 trace를 `failed`로 끝낸다.
- 파일은 plain UTF-8 JSONL chunk로 시작한다. 압축 비용은 별도 측정 전 도입하지 않는다. writer는 chunk 하나씩 `.partial`에 쓰고 checksum/first_seq/last_seq/count를 만든다.
- **묶음당** flush/fsync 후 원자적 rename, manifest 원자 갱신으로 확정한다. 플랫폼의 directory sync가 확인되지 않으면 power-loss durability를 보장하지 않는다. fsync는 DB 업무 thread에서 실행하지 않는다.
- schema-2 payload는 청크별 `.payloads` 파일 하나에 연속 저장해 묶음당 한 번 fsync한다. hash별 manifest `name/offset/bytes`로 원래 payload를 읽고 SHA-256을 검사한다. payload 묶음 → 이벤트 청크 → manifest 순서로 확정하며 미확정 참조는 공개하지 않는다. 기존 `payload-<hash>.json` 읽기/API bytes와 hash dedup은 유지한다. `payload_fsync_count`와 메모리/묶음 예산을 status에 기록한다.
- capture당 1GiB, 보존 총량 4GiB를 초기 hard cap으로 둔다. preflight에서 여유 공간과 예상 발생률을 확인한다. 한도/디스크 오류는 trace를 incomplete로 종료하고 업무는 계속한다. 기존 파일을 몰래 지워 active capture 공간을 만들지 않는다.
- manifest에는 accepted/known_dropped/written 이벤트, 마지막 발급·확정 seq, backlog high-water, 파일 bytes/flush/fsync 시간, clock jump, 종료 이유를 담는다. 파일은 writer별로 과도하게 나누지 않는다.
- 종료 상태 `complete`/`incomplete`는 해당 상태를 담은 마지막 manifest를 fsync·원자 교체한 뒤에만 메모리 status에도 공개한다. 마지막 manifest 저장 중에는 `stopping`으로 남고, 저장 실패는 `failed`로 표시한다. `stop(timeout=...)`이 시간 안에 drain을 끝내지 못하면 종료를 기다리는 동안 `stopping`을 반환한다.
- `.partial`/orphan chunk는 재시작 때 checksum과 JSON 경계로 회수하되 과거 manifest를 complete로 바꾸지 않는다. 손상/미확정 구간은 그대로 표시한다.

bounded RAM, DB 비차단, 전원 장애까지 무손실은 동시에 보장할 수 없다. 정상 처리율 내 **관측 대상 호출의 완전성**을 검증하며 crash 직전 RAM tail은 `unknown_tail_loss`로 기록한다. seq 공백은 중간 누락만 검출한다. 마지막 이벤트 이후 유실 수를 seq만으로 알아냈다고 주장하지 않는다. 파일 I/O가 오래 걸리면 손실 가능 시간도 5초보다 길어진다.

최종 상태는 `complete`, `incomplete`, `interrupted`, `failed`로 구분한다. complete라도 coverage는 `observed_paths_only`이며 우회 DB 접근까지 모두 수집했다는 뜻이 아니다. UNREGISTERED와 미계측 경로를 별도 집계한다.

## 제어·재시작·예약

기존 master 아래 `trace` 자식 스위치를 추가하고 raw `capture`와 구분한다. 기본 OFF, master OFF/TTL 만료/재시작 시 신규 이벤트 수집을 중단한다. 이미 수용한 queue만 제한된 shutdown budget 안에 파일로 정리한다. DB 종료를 파일 flush 때문에 무한히 기다리게 하지 않는다. 기존 run lock으로 measure/compare/replay와 동시 실행을 기본 거부한다.

65분을 위해 master와 trace API/CLI의 명시 TTL 상한을 **7,200초**로 맞춘다. workload pause 및 기존 raw/measure의 상한은 확대하지 않는다. child 만료는 master를 넘지 않는다. 반복 master ON이 TTL을 연장하지 않는 기존 규칙을 유지하고 시작 전에 남은 TTL을 검증한다. 로컬 deadline으로 TTL을 확인하며 제어 파일 변경 확인은 background에서 최대 0.5초 주기로 수행한다. API OFF는 해당 프로세스 gate를 즉시 닫고 외부 CLI 변경은 이 polling 지연 이내에 반영한다.

API에 trace start/status/stop/list/manifest/chunk 다운로드를 제공한다. client 경로 입력 금지, ID 검증·실제 경로 제한, 인증/응답량 제한을 유지한다. 65분 전체를 기존 32MiB report JSON 한 개에 합치지 않는다. CLI는 같은 API/control 계약을 사용한다.

예약은 **일회성 외부 제어 작업**이 지정 시각에 인증 API로 master와 trace를 켜고 종료·manifest를 확인하는 방식으로 한다. 구현·배포 후 실제 scheduler 등록과 읽기 확인을 별도 완료 조건으로 삼는다. 서버 재시작 후 과거 제어 파일로 자동 재활성화하지 않는다. 재개하면 새 producer/session segment와 공백을 남긴다. 무인 자동 재개가 필요하면 별도 명시 resume 권한을 가진 외부 예약 작업이 새 lease를 발급해야 하며, 초기 버전은 자동 재개하지 않는다.

오늘 시간이 부족하면 기존 검증된 짧은 measure를 **한 번** 별도 보존하는 fallback은 가능하다. 다만 full sampler/wait probe 비용과 최대 5분, 지원 replay 범위 제약을 명시하고 경량 15분/65분 확보로 대체 보고하지 않는다.

## 실제 replay로 연결하는 계약

**0B는 입력 이벤트이며 단일 DB writer가 아니다.** collector가 만드는 latest/minute/second 등 DB writer 집합을 명시적으로 선택한다. 원시 0B를 다시 주입하면서 동일한 파생 DB writer도 재생하면 이중 부하가 되므로 v1은 DB writer 호출 경계에서 재생한다.

기록을 세 수준으로 구분한다.

1. `timing_only`: 호출 간격·종류·입력 수·지연을 분석할 수 있음. 현재 경량 기본 이벤트의 보장 범위다.
2. `synthetic_shape`: writer별 대표 입력과 초기 상태로 호출 패턴 재생. 원래 WAL·압축률·잠금/캐시 경합의 동등성은 보장하지 않음.
3. `validated_adapter`: 동일 replay 키/종목 overlap, 신규·변경·no-op·authority/revision 상태를 준비하고 결과와 동시성 계약을 검증한 adapter. 원본과의 차이와 fixture 버전을 함께 보고함.

행 수와 payload 크기만으로 3번이 되지 않는다. 특히 ka10080 900행 모두 신규인 fixture와 900행 모두 no-op인 운영 호출은 전혀 다르다. mutable payload 참조를 background에 넘겨 나중에 hash/직렬화하는 방식도 금지한다.

adapter 우선순위: `realtime.latest` → `realtime.minute`/finalize → REST `ka10080` → `realtime.second_bar` → TOP20/membership → 뉴스/그 밖의 readers/writers. 이미 계산하는 키/operation hash와 domain count를 재사용한다. full payload 해시를 추가 계산하지 않는다. 추가 identity 정보가 필요하면 입력 생성 경계의 bounded 익명 key recipe를 별도 버전으로 설계·비용 검증한다. today v1에 없는 정보는 주말에 복구 가능하다고 보장하지 않는다.

replay는 항상 `kiwoom_monitor_diagnostic_test` 확인, 임시 키 scope, fixture 준비/검증/정리, 운영 DB URL 거부를 거친다. 운영 writer를 합치거나 transaction을 쪼개지 않는다. `sql_calls == 2` 같은 우연한 숫자를 장기 호환 식별자로 쓰지 않고 source/adapter 계약 버전으로 검증한다.

동일 lane/flush 내 실제 선후 관계를 보존하며 서로 독립인 writer만 겹치게 한다. 단순 시간 offset만으로 실행하면 원래 직렬 호출도 겹칠 수 있다. 과부하로 worker가 밀렸을 때 호출을 조용히 건너뛰지 않는다. bounded 실행 한도에서 schedule lag/미실행을 기록하고 그 run의 동등 재현 판정을 실패 처리한다. 배속/강제 직렬화는 별도 실험 모드로 명시한다.

선택 구간에 unsupported·dropped·불명 tail·불완전 call·필수 선행 상태가 있으면 exact/complete replay 실행을 거부한다. 명시적 synthetic 부분 실험은 제외 내역을 내보낸다. 65분 원본을 잘라 5~120초부터 replay하고, duration 검증은 원본 길이와 선택 길이를 분리한다. 파일 index로 필요한 chunk만 읽는다.

전체/단독/조합/제외 비교는 같은 fixture 초기 상태로 복원한 후 순서를 교대하고 여러 차례 반복한다. 같은 NAS의 다른 workload·checkpoint·cache 상태는 통제되지 않을 수 있다. A+B에서만 느려져도 한 번의 제외 결과나 이진 분할만으로 원인을 확정하지 않는다. DB별 통계와 cluster 공용 WAL/device 통계를 구분한다.

## 구현·검증 인계

### `query_minute` 합성 shape adapter 후보 (2026-10-03)

저장된 오래된 trace `20261003T054415Z-1984933624e2`에는 `rest.market_bars.minute/query_minute`가 31회, 합계 27,900 input rows로 기록되어 있다. 해당 trace에는 bar별 identity나 신규·변경·동일 여부 counters가 없어 원래 키 중복, 종목 overlap, payload 값, 실제 DML 비율은 복원할 수 없다. 따라서 adapter는 이를 “원본 exact replay”로 부르지 않고 사용자가 반드시 하나를 지정하는 세 가지 합성 실험으로 제한한다: `unchanged_page`(seed 뒤 동일 페이지 재적용), `one_changed_bar`(seed 페이지 뒤 한 bar 수정), `fresh_page`(서로 독립한 새 synthetic key 집합). Source shape가 없는 trace의 row 수만 입력 크기 힌트로 이용한다.

adapter는 재생 call당 최대 1,000행, run당 30,000행으로 제한하고 dedicated DB만 허용한다. setup·검증·cleanup은 임시 `DIAG` key 범위 안에서 하며, 대상 canonical/minute metadata/revision의 key 충돌을 먼저 거부한다. 성공뿐 아니라 의도적인 관측 입력 오류의 rollback과 peer row 보존도 검증한다. 결과는 bar 값, metadata, revision 수와 관측 writer metrics를 나란히 확인한다. 이 합성 실험은 adapter 계약과 예상 update/no-op shape만 검증하며 운영에서의 실제 WAL, 저장장치 경합, 원본 payload 분포와 성능을 예측하지 않는다.

새 `recorded_counts` 모드는 trace 호출의 `rows_attempted`, `bar_changed_rows`, `duplicate_input_keys`, `revision_insert_rows`, `observations`, `metadata_suppressed_rows`, `revision_history_enabled`를 입력 생성과 검증에 사용한다. 모든 필드가 정수이고 shape version 1, observation 전량, metadata suppression 0이어야 한다. 변경·revision 수는 고유 입력 키 수 이하여야 하며, 이 제한으로 설명할 수 없는 shape와 counters가 없는 예전 trace는 실행 전에 거부한다. 반복 키는 같은 최종값을 갖도록 만들고 호출마다 별도 synthetic subject를 준비한다. canonical bar의 기존값과 revision의 기존 payload는 각각 계수에 맞춰 준비하므로 변경 행과 revision 행 수를 독립적으로 맞출 수 있다. 각 호출의 실제 관측 domain counters가 원본과 다르거나 빠지면 보고서를 aborted로 처리한다. 이 모드는 집계 수치를 재현할 뿐 원본 종목/키 overlap, 서로 다른 중복 payload, 신규 대 변경의 비율, 시장·source provenance를 복구하지 않으므로 `synthetic_shape`다.

검증 상태(2026-10-03): PC `.venv`에서 count recipe/API/report·trace/workload 단위 회귀 34건과 NAS source-runtime candidate의 전용 PostgreSQL 통합 검사 6건이 통과했다(전용 DB 검사 18.268초). NAS server image의 Starlette `TestClient` 시험 경로는 `httpx2`가 설치되지 않아 unit bundle import 단계에서 중단했으며, 운영용 패키지는 추가하지 않았다. NAS 통합 검사는 bounded temporary diagnostic rows를 검증하고 정리했으며 active release·운영 DB·server image는 바꾸지 않았다. immutable 후보 `2026.10.03-db-minute-recorded-shape-v1-94fdce4bf35406a7`는 stage 상태다. 새 trace의 recorded-counts end-to-end replay와 실제 관측 call 계수 대조는 아직 수행하지 않았다.

검증 상태: 로컬 trace/replay 관련 단위검사 13건 및 NAS dedicated PostgreSQL adapter 검사 4건이 통과했다. active NAS release `2026.10.03-db-minute-replay-v1-388841088675094c`에서 capabilities/health 확인과 기존 60초 trace 기반 `unchanged_page`, `one_changed_bar`, `fresh_page` 선택 replay도 실행했다. 세 replay는 데이터 검증을 마쳤지만 원 trace는 이전 release에서 만들어져 31개 분봉 호출 모두 shape counters가 비어 있었다. replay schedule lag p95는 각각 약 1.16초·1.40초·9.44초로 원래 timing을 보존하지 못했다. 따라서 이 결과는 synthetic adapter 기능 확인이지, 장중 부하·운영 병목의 크기나 원인을 재현한 acceptance가 아니다.

| 단계 | 책임 파일 | 완료 기준 |
|---|---|---|
| 1. 오늘 trace 핵심 | 새 `diagnostic_trace.py`, `postgres_access.py`, `diagnostic_metrics.py` | raw OFF+trace ON, start/end/domain 결합, 경량 chunk, 과부하/실패/종료 검증 |
| 2. 제어·운영 | `diagnostic_workloads.py`, `diagnostic_runs.py`, `app.py`, `scripts/nas_workload_diagnostic.py` | trace child, TTL7200, 인증 API, 앱 수명 종료, 별도 manifest/download, 기존 capture 회귀 |
| 3. NAS 적용/trace smoke | source-runtime 기존 절차 | active release·health·authenticated trace API 확인 및 60초 trace 633/633 저장, drop 0, checksum 12/12 완료. 장중 capture 아님 |
| 4. 주말 replay adapter | `diagnostic_replay.py`, 해당 writer 계측, 전용 DB integration | 뉴스/shadow 38 calls 및 분봉 3 synthetic scenarios 전용 DB replay 완료. 분봉 원 trace shape counters 부재와 timing miss를 기록; 새 shape 기반 입력 적용은 다음 capture 이후 재평가 |
| 5. 65분 acceptance/예약 | trace 테스트/운영 안내 | 기존 3,900초·20,000-event burst에 더해 1,024 wide Unicode domain events가 약 3.90MB였던 청크 초과를 재현하고, 최대 1MiB로 분리된 모든 청크의 조회·checksum·순번·종료 drain 및 단일 oversized event의 명시적 실패를 로컬과 NAS test container의 trace/replay 18건으로 검사했다. 활성 NAS release `2026.10.03-db-minute-replay-v1-388841088675094c`를 기반으로 한 immutable release `2026.10.05-db-trace-chunk-bounds-v1-9209b8fd29423301`은 NAS 테스트 7/7 통과 후 배포했다. `/health`에서 새 build/release, DB 컨테이너 불변을 확인했고 현재 `WAITING_MARKET`, observation 미예정, trace OFF다. Codex heartbeat `nas-65-minute-db-trace-capture`가 2026-10-06 08:54 KST trace를 시작하고 `verify-nas-db-trace-capture`가 10:01 KST manifest/chunk를 검증하도록 1회 등록됨(PC와 데스크톱 앱 실행 필요). 실제 65분 NAS 보존·오버헤드 측정 및 파일 검증은 실행 뒤 완료 |

<a id="dedicated-db-recorded-operation-execution-2026-10-06"></a>

### 전용 DB 기록 작업 실행(2026-10-06)

`diagnostic_replay_database_cli.py run`은 이미 봉인된 전용 replay DB와 baseline ID, trace ID,
선택 window/workload/mode를 받아 checksummed schema-2 capture를 별도 프로세스에서 실행한다.
운영 서버의 master/pause 설정은 건드리지 않으며 제한된 offline observer 제어 파일을 사용한다.
각 operation은 source ID, 새 replay ID, 그리고 실행 프로세스에서 관측된 DB call ID를 연결한다.
DB-call 집계는 replay connection scope, WAL transaction attribution은 unavailable로 보고한다.

모든 입력·지원 메서드·codec은 baseline 획득/초기화 전에 검증한다. baseline allowlist에 없는
history/projection 부수 쓰기(현재 `news_article`, `theme_metadata`)가 필요한 쓰기는 DB 변경 전에
거부한다. 실행 후 actor/collector와 native DB connection이 모두 drain된 다음 table/sequence 상태를
스냅샷하고 같은 sealed baseline으로 되돌린다. COMMIT acknowledgment 예외도 replay 소유 연결을
닫고 receipt를 해제한 뒤 cleanup하도록 하며 production observed connection semantics는 바꾸지 않는다.
NAS acceptance 명령은 `scripts/check_recorded_replay_baseline.py --execution-gates`다. 이 gate의
controlled fixture는 로컬 기능 검증일 뿐 실제 장중 capture의 state-equivalence·성능 동등성을
증명하지 않는다. NAS 후보 `2026.10.06-recorded-replay-execution-v1-624aade862f1b298`는
887개 source file로 stage됐으며 active pointer는 바뀌지 않았다. NAS 전용 PostgreSQL 실행 gate
2건이 2026-10-06 2/2 통과, skipped=0, 9.595초로 끝났고 sealed baseline 및 server/database
container 불변을 확인했다. controlled fixture라 실제 장중 capture와 동등하지 않다. 다음 acceptance는
실제 capture가 완성된 뒤 bounded window replay다. 공개 replay/run API, generated-ID adapter, WAL transaction
attribution, 장중 acceptance는 남았다.

<a id="authenticated-trace-input-capture-api-2026-10-06-local-candidate"></a>

### 인증된 trace 입력 수집 API(2026-10-06 로컬 후보)

인증된 trace 시작 API는 기본 false 인 엄격한 불리언 store_inputs·collector_inputs를 받아 schema2 recorder로 전달한다. 하나라도 선택하면 schema2이며 둘을 선택하면 허용된 native store 인수·관측 0B collector 입력을 보관한다. 다른 이벤트·민감 계좌 데이터는 제외한다. 인증 capabilities는 schema2·두 OFF 기본·0B 범위·관측 경로 한정·overhead_verified=false를 알린다.
실제 SQLite/collector·invalid/auth/session/TTL 실패·병렬 시작 보호 포함 로컬 33개 통과. 후보 build=`2026.10.06-recorded-capture-api-v1`이며 당시 NAS 준비/활성·완전 65분·부하는 미검증이었다. 이후 888 파일 후보 `2026.10.06-recorded-capture-api-v1-af7d3d49b61aad09`를 준비·활성화했고 인증 health/capabilities에서 build/source·schema2·0B·두 OFF를 확인했다. master/traceOFF·realtime WAITING_MARKET·observation 예상없음이었다.
08:54 수집·10:01 검증 automation은 활성 build/schema2·두플래그를 요구하며 preflight 실패면 진단 OFF를 유지한다. 실제 65분·부하 측정은 남아 있다.

<a id="deferred-ram-capture-and-paced-after-hours-persistence-2026-10-07-local-candidate"></a>

### RAM 지연 수집·장후 속도 제한 저장(2026-10-07 로컬 후보)

trace 시작 API는 선택 persist_at(Unix초)를 받는다. 이 명시적 모드만 charge4GiB·1,000,000이벤트를 사용한다. Linux host/cgroup 여유≥5GiB가 필요하며 RSS hard cap은 아니다. 수집 만료 시 producer token을 닫고 writer가 저장 시각까지 정기 payload/chunk I/O 없이 RAM에 보관한다. master TTL 만료도 저장 시각을 옮기지 않는다. 보관 trace는 기존 진단 run lock을 유지하여 replay/measure/compare와 겹칠 수 없다.

persist_at에서 payload/JSONL을 64KiB 블록·최대 1MiB/s로 쓰고 chunk sync 뒤 최대 5초 cooldown을 더한다. 느린 storage 대기 중 pacing credit을 쌓지 않는다. 지연 flush의 blob index는 최대분당 1회·최종 durable manifest로 저장한다. capture stop은 조기 flush를 유발하지 않는다. 저장 시각 전 shutdown은 interrupted로 표시하고 이미 durable 한 초기 manifest만 남긴다. crash는 설계상 RAM 꼬리를 잃으며 disk/manifest 실패에서 complete를 공개하면 안 된다.

로컬 지연 수명/API·chunk 내구성·기록입력 46회 통과. 이전 32768 상한을 넘는 33001 순서이벤트·payload 무결성·시각 전 payload 파일 없음·pacing·shutdown/masterOFF/만료/여유 거부를 포함한다. 읽기 전용 NAS 표본 MemAvailable=18,077,790,208 바이트지만 container 여유·새 release는 미확인이다.
고정 후보 `2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f`는 비활성 준비이고 build=`2026.10.07-trace-deferred-ram-v1`이다. SSH 읽기는 가능하나 NAS 소스/runtime·활성화는 대화형 sudo가 필요하다.10 월 8 일 automation의 persist_at20:10·저장후검증 갱신이 남았다. health/capabilities 지연기능·container 여유 확인 전 수집하지 않는다.

<a id="t1t4-deferred-capture-capacity-decision-2026-10-07-implementation-pending"></a>

### T1–T4 지연 수집 용량 결정(2026-10-07; 당시 구현 대기)

O12 후속 결정이며 한도를 8GiB/5M로 높이는 것만으로 완전 65분을 증명한다는 가정을 대체한다. 활성 v6·10 월 8 일 08:55 예약 유지. 준비 `2026.10.07-causal-deferred-capture-v1-24cd50ff92fe752b`는 비활성 기능 통합 후보이지 승인 용량 release가 아니다. 로컬 78개/API는 후보 자체 NAS 정합성/메모리/지연/저장을 대체하지 않는다.

<a id="updated-capture-window-2026-10-08"></a>

#### 수집 구간 갱신(2026-10-08)

사용자가 선택한 구간은 09:00–10:00 KST, 3,600초다. 앞선 65분 결과는 과거 용량 증거로 남긴다. 다음 불변 비공개 후보는 정확한 v11 혼합 입력으로 20행 메시지 36,000개를 100ms 합성 시각 간격으로 검사한다. 합성 입력 기간과 실제 benchmark 실행 시간을 구분한다. 요청 기간과 해당 기간의 용량률 추정을 출력하고, 이전 65분률을 60분 측정으로 부르지 않는다.

이 단계에서는 기존 보관 charge 구현을 유지한다. 비공개 계산 예산 15GiB는 물리 RSS 할당량이 아니다. v11은 charge 16,075,246,320바이트와 RSS 최대 4,675,239,936바이트를 기록했다. 용량·RSS·host/cgroup 여유·승인 거부·저장 출력은 독립 한도다. 아래 release 검증 전에는 운영 4GiB/1M, 활성 소스와 예약을 유지한다. 합성 60분 통과만으로 부하·한도 근처 속도 제한 저장·실제 시장 범위 검증이 끝나지 않는다.

<a id="evidence-and-limits-of-the-evidence"></a>

#### 증거와 해석 한계

이전 8GiB/5M 비공개 probe는 전달 tick326960·승인이벤트 1013577·RSS 약 1.26GiB에서 거부했다. 이벤트상한보다 receipt scalar 계산이 먼저 포화했다. queue_delivery는 구독자 delivery 마다 receipt를 만들며 enqueue/dequeue/consume_end는 같은 identity 필드를 각각 dict에 저장한다. _field_charge는 실제 Python 객체를 공유해도 단계별 키·문자열을 재귀 계산한다.

설계용 Windows 비교는 같은 native collector/hub/consumer·2000 메시지×20tick을 사용해 모두 40000delivery·124001 논리이벤트였다. TOP20 구독자 1개뿐이며 REST transport/운영 DB/네트워크/예약 수집/저장은 없었다.

| RAM 표현 시제품 | 계산 MiB | RSS 증가 MiB | 20행 메시지당 producer p50/p95(ms) |
| --- | ---: | ---: | --- |
| 기존 dictionary·계산 | 998.28 | 155.13 | 2.7968 / 3.6025 |
| delivery 필드의 JSON byte | 303.99 | 176.28 | 2.8531 / 3.6355 |
| 기록별 dictionary header·고정 column | 357.86 | 139.47 | 3.1205 / 4.1399 |
| 공유 고정 column·slots 단계 기록 | 357.86 | 106.04 | 2.6304 / 4.0271 |

단일 제어 burst이므로 지연개선·NAS 승인은 아니다. slots 시제품은 enqueue/dequeue/consume_end 필드왕복만 확인하고 저장/재생하지 않았다. 공유 identity 전체를 단계마다 여전히 보수적으로계산했다. 프로세스 내 monkeypatch는 운영·준비후보를 바꾸지 않았다. 보고서=`artifacts/delivery-design-{legacy,json-bytes,columns,shared-slots}.json`, 실험=`artifacts/probe_delivery_ram_representations.py`.

20행 fixture는 다른 입력을 제외하면 대략 `3*delivered_rows + 2*messages` 이벤트를 만든다. 이 형태에서 5M 한도는 3,900초 동안 전달 tick 약 414개/초이며 REST·store·control 여유는 없다. 용량 곡선이지 관측 시장률이 아니다. 원본 행 수·fragment 크기·구독자 수가 달라지면 곡선도 바뀐다. 현재 운영에서 비어 있지 않은 `capture_component`를 제공하는 consumer는 AutonomousTop20Service뿐이다. 다른 consumer를 추가하면 receipt가 늘어나므로 이 용량 가정을 다시 검증해야 한다.

별도 900행 REST 형태는 charge 3,024,848바이트다. 65분 동안 1page/s만으로 store 입력·receipt 전 약 10.99GiB가 되지만 운영률 주장이 아닌 반례다. 불완전한 10월 6일 수집과 없는 로컬 payload bundle로 09:00–09:10 실제 최대를 확정할 수 없다. 입력률을 지정하지 않은 RAM 전용 수집에 유한 용량을 보장할 수 없다. release 검증은 시험 workload·률·크기·여유를 명시해야 한다.

<a id="implemented-boundary-pc-regression-complete-nas-capacity-pending"></a>

#### 구현 경계(PC 회귀 완료·NAS 용량 대기)

논리 delivery 이벤트 3개, 원래 전역 순서, wall/monotonic 시각, delivery/parent/subscriber ID, parser 위치, stage·실패·drop을 모두 유지한다. 최종 이벤트 1개로 통합하거나 표본화·누락 receipt 추론·schema 3/재생 의미 변경을 하지 않는다. caller에 JSON·hash·압축을 추가하지 않는다. JSON-bytes 시제품은 계산량을 줄였지만 RSS가 늘어 기각했다. 광범위 payload 코덱·비동기 압축 재설계는 보류한다.

기존 recorder 경계 안의 비공개 간결 delivery 표현을 지연 수집에만 사용한다. streaming 모드·일반 trace 이벤트는 기존 dictionary 경로를 유지한다. 구성은 다음과 같다:

1. 불변 source context: source component·message/controlID·parent inputID·완전성. 기존 CapturedMessageReceipt/CapturedControlReceipt로 공유하며 전역 content-addressed 문자열 registry를 만들지 않는다. 큰분할메시지의 parent 최대 100개를 delivery행/단계마다 중복 charge 하지 않는다.
2. capture epoch 범위의 불변 subscriber context: trace/component/subscriberidentity.
3. 불변 delivery identity: deliveryID·sequence·event kind·parser ordinal·source/subscriber 참조. 실제 queue 전달에서 1회생성하고 최신구독상태로나중에복원하지 않는다.
4. slots 단계 queue 기록: 원래 clock·global seq·producer·stage/outcome·identity 참조. 필드없음과명시 null·모든진단실패 stage를구분하여보존한다.

불변 그래프 깊이는 고정이고 비공개 보관 counter는 recorder가 기존 trace lock으로 소유한다. 첫 queue/pending 참조에서 charge하고 마지막 참조에서 해제한다. 자식 retain/release는 0↔1 전환에서만 한다. 세션 전체 intern map이나 단계마다 모든 parent 문자열을 순회하는 경로는 없다. 공개 전에 승인 차이를 계산하고 거부 시 소유권·개수를 바꾸지 않는다. 전역 sequence/drop 계산은 같은 승인 경계를 사용한다. 한 원자적 승인 차이에서 공유 자식은 한 번만 계산하며, 불변 구성 시 기본 shallow 할당 비용을 cache한다.

계산은 node/tuple/string/int 할당, deque 참조, 관리 정보, 측정한 allocator 여유와 기존 copy/worker 예약을 포함한다. 사용자 `__sizeof__` 대신 정확한 기본 타입을 사용한다. 보관 상한 모델이지 `RSS == charged_bytes`가 아니며 실제 Linux RSS로 상한을 확인한다. 일반 `freeze_payload`의 byte/node/type/secret 검사는 유지하고 아래 명명 catalog 프로필은 별도 측정 예외로 취급한다. producer 임시 구성과 native queue의 receipt 참조에는 제한된 reserve가 필요하며, 기존 subscriber queue 1,000개·message 10,000행 상한이 계산의 입력이다. 이전 epoch 객체를 새 세션에 승인하거나 charge하면 안 된다.

저장 시 기존 worker가 제한된 batch를 원래 JSONL dictionary로 펼친다. durable chunk 공개 성공 후에만 각 기록·identity 그래프를 해제한다. pending 뒤쪽과 부분 저장 chunk는 전체 charge를 유지한다. 먼저 저장된 stage가 공유 context를 조기 해제하면 안 된다. abort·실패 정리 후 전역 보관 registry·세션 간 참조가 남으면 안 된다. diagnostic_trace.py에 명시적 변환·해제 경계 하나를 두며 새 store·transaction·일반 framework를 만들지 않는다.

예상 수정은 diagnostic_trace.py, realtime_hub.py의 비공개 receipt 타입, diagnostic_top20_input.py의 delivery hook, realtime_collector.py의 source receipt 생성이다. 작은 전용 layout 모듈은 이 표현·계산 계약을 소유할 때만 허용한다. native consumer queue·구독 정책, DB 연결·transaction, collector 출력은 유지한다. native 전달이 계속되어도 수집 실패는 보여야 한다.

<a id="independent-limits-and-correctness-gates"></a>

#### 독립 한도와 정합성 검증

- 지연보관 byte·논리 event·입력별 copy byte/node·copy 병렬수·host/cgroup 여유·저장 byte는독립검사이며각각보고한다.
- 당시 _MAX_STORED_BYTES는기존수집전체 4GiB 다. RAM8GiB도지연 저장에서실패할 수 있다. 최종 JSONL+고유 payload+manifest를측정한뒤수집별저장 quota·전체 보관 quota를명시한다. preflight에서요청 quota 전체가필요하며 min(...)으로몰래줄이지않는다. 기존기록·1MiB/s/cooldown을보존하고 manifest/fsync 포함완료예상을보고하되실제완료는별도 검증이다.
- input_rejected>0이면 scalarenqueue 성공·known_dropped=0·accepted=written이어도최종 incomplete 다. 기존최종 상태가 drop/censorship만검사하므로입력 거부조건을별도정합성 수정으로추가한다.
- 구기록과 durableJSON 동일성·Unicode/parent100분할·다중구독자·dequeue/drop/실패/없는 stage·epoch 전환·첫/후속 stage 거부·병렬승인·pending 뒤쪽·최종 manifest 실패·shutdown을검증한다. 저장출력으로원래 delivery 범위·T4 native/descendant 제외재생을검증한다.
- 반복 작은 메시지만아닌작은/한도 근처분포를측정한다. REST/catalog/ranking/subscription/lifecycle receipt·typed store 입력·지연 저장을포함한다.0개/누락그룹은범위 검증이아니며 native 출력/transaction은 OFF와같아야한다.

<a id="capacity-selection-and-release-gate"></a>

#### 용량 선택과 release 검증

8GiB/5M은 후보값이며 확정 설정이 아니다. 먼저 같은 실패 native fixture·burst 변형, 이어서 명명된 혼합 입력 범위로 간결 구현을 확인한다. 실제 RSS/최대 RSS·MemAvailable/cgroup 여유·charge·event 수·copy 거부·지연/loop lag·최종 출력 byte를 모은다. OFF/ON을 교대로 실행하고 benchmark를 병렬로 돌리지 않는다. 기존 부하 목표·미달을 명시하며 메모리에 맞는다는 이유로 회귀를 승인하지 않는다. source workload는 0B/0w 전달률·message/fragment·fan-out·REST page/row·catalog 갱신·store 형태·lifecycle 변동을 명시해야 한다. 합성 범위는 실제 시장 최대·성능 기준이 아니다.

8GiB 후보 시험의 초기 guard로 host MemAvailable 최소 4GiB를 유지하고 운영 baseline RSS·cgroup 여유를 포함한다. reserve 침해 전에 비공개 시험을 멈춘다. 20GB NAS에서 확인할 제안 여유이지 관측된 최대 필요량은 아니다. 혼합 입력에 필요하고 같은 reserve를 유지할 실제 여유가 있을 때만 12GiB 이상을 검토한다. 가벼운 worker 메모리 압력 검사는 capture만 incomplete로 닫고 승인 데이터를 persist_at까지 유지할 수 있지만, native workload 중지·조기 flush는 금지한다. polling guard를 process RSS의 강제 상한으로 보장하지 않는다.

불변회귀·정확한 NAS 정합성/부하/용량/저장·명시 범위 검증이모두통과한뒤에만 build/options로활성 v6·10 월 8 일예약을대체한다. 시작/검증을함께갱신한다(schema3·3 플래그 true·승인한도·정확 release·persist_at). 시각 전통과못하면 v6 예약유지·확장 T1–T4 미승인을보고한다.

<a id="catalog-copy-profile-decision-2026-10-07-implementation-pending"></a>

<a id="catalog-copy-profile-decision-2026-10-07-pc-implementation-complete-candidate-gate-pending"></a>

#### Catalog 복사 프로필 결정(2026-10-07; PC 구현 완료·후보 검증 대기)

혼합 native 경계 probe는보관 RAM/event 전세번째한도를드러냈다. AutonomousTop20Service._ensure_market_catalog는 5000(code,name,market)행을 5001 문서로바꾸어 replace_documents("stock_catalog",documents)를 1회호출한다. catalog 응답은승인되지만 store 인수의메모리복사계산이기본 8MiB를넘었다.8MiB 파일/네트워크응답이아니다.
거부당시 trace 전체 charge12,908,809 바이트·승인 79·scalar drop0·store 거부 1이었다. 처음한도초과지점은 8,388,644 바이트/44,661node(한도 8,388,608/120,000)다. native 호출은계속되었으며전체 RAM 증가로입력별 거부를해결할 수 없다.

제어 PC 비교(`artifacts/catalog-capture-design-comparison.json`, 경우별 5회 복사):

| 5,000종목 경우 | 보관 copy 계산 | 인코딩 payload 바이트 | copy 중앙값 / 최대 |
| --- | ---: | ---: | ---: |
| 기존 인수·8MiB | 거부 | 생성 없음 | 해당 없음 |
| 기존 인수·16MiB | 14,038,408 | 814,054 | 36.08 / 45.04 ms |
| Columnar prototype, 8MiB | 3,359,372 | 284,101 | 9.23 / 11.98 ms |

columnar 시간은 새 layout 구성·검증을 제외하며 fixture 일치만으로 일반 코덱 정합성을 증명하지 않는다. PC 설계 증거이지 NAS 지연 검증은 아니다. 별도 2500 종목 혼합 지연 probe는 150이벤트를 완료했고 chunk/참조/시퀀스/REST 쌍/구독 승인/delivery 종료를 확인했다. 파일은 1,773,717 바이트다. 5000 종목 실패나 65분 한도를 해결하는 증거는 아니다.

**결정:** 정확한 native store 인수를 유지하고 제한된 **16MiB catalog store 복사 프로필**을 선택한다. 이 후보에 분할·columnar 직렬화·부모 payload 복원을 구현하지 않는다. native catalog task는 owned_to_thread로 실행하고 성공 후 같은 날 재사용하며 tick 별 store 호출은 없다. 이번 release 전에 새 인코딩/복원 계약을 만드는 것보다 측정된 추가 복사 예약을 선택한다. 프로필 구현/시험만 허용하며 활성 배포·8GiB/5M 세션 한도 승인은 아니다.

Implementation contract:

1. diagnostic_replay_contract.py가 소유하는 명명 프로필 stock-catalog-documents/v1을 추가한다. 실제 bound 인수 collection="stock_catalog"인 native replace_documents의 기록 operation_start에만 선택한다. 일반 8MiB·120000node/depth/string/type/secret 검사·다른 메서드는 유지한다. upsert_documents/일반문서/REST/collector/account로 확대하거나 요청에서 임의 한도를 정하지 않는다.
2. start/end에 payload_profile·collection을 기록하고 작업 쌍 검증에서 둘의 일치를 요구한다. 없으면 기존 8MiB, 미지/불일치/잘못된 위치는 실패 종료한다. codec tag·원래 list/tuple/dict 타입/값/순서·operation/actor/cause ID·native 단일 호출은 그대로다. store 전용 재생에서 오늘 template·상류 receipt로 catalog를 재생성하지 않는다.
3. diagnostic_trace.emit_payload는 실제 값의 method/collection/profile을 확인하고 **복사 전에** 선택 16MiB를 예약한다. 해당 한도로 1회 복사하며 8MiB 실패 후 재시도하지 않는다. 성공/거부/예외/stop/epoch 변경/finally 마다 로컬 예약액을 쓴다. 기존 2copy semaphore의 최대예약은 32MiB이며 RSS/여유/세션 계산에 포함한다. 일반 _COPY_RESERVATION은 8MiB 다. capture 실패도 native 결과/오류/연결 소유권을 바꾸지 않는다.
4. decode도 대칭이다. 공통 작업 인수 reader가 metadata를 확인하고 선택한도로 thaw 한 뒤 collection/method binding·기존 operation 검증을 한다. thaw_payload의 내부 한도 인수는 허용하되 기본 8MiB·최종 freeze도 같은 선택한도다. 미검증 내용으로 한도를 늘리거나 8MiB 실패 뒤 decode를 재시도하지 않는다. compile_recorded_plan·diagnostic_recorded_execution._prepare·compile_lifecycle_descendants와 추가 operation reader를 이 경로로 연결한다. REST/collector는 일반 codec이다. pair/profile 불일치는 native 호출 전에 실패해야 한다.
5. capabilities에 지원 copy profile/한도를 명시한다. schema3·store-input/v1 typed encoding은 유지하고 metadata로 확장 범위를 밝힌다. 구 reader는 제한을 유지해 초과를 거부한다. 원래 dataset 선택/출처 규칙을 유지한다. native TOP20 재생은 후속 catalog 쓰기를 대체하고 TOP20 제외는 재주입하지 않으며 지원된 store 전용은 저장 native 인수를 정확히 1회 호출한다. peer는 독립이다.
6. 기존 16MiB blob/bundle·전체 파일 quota를 유지하고 최종 직렬 byte를 별도 측정한다. 저장 한도를 우회하는 권한이 아니다.5000 종목은 명명 시험 범위이지 모든 catalog/name의 최대가 아니다.16MiB 또는 120000node 초과는 명시적 거부/incomplete 다. 검증은 시험 종목 수·문자열 형태·전체 workload를 명시해야 한다.

활성화·예약 변경 전에 필요한 검증:

- 정확한 5000 종목 native 수집/durable 왕복 뒤 폐기 PG에서 catalog store 전용 재생·전체/제외 TOP20 후속 경로를 확인한다. 같은 인수·행 값/순서·단일 호출·baseline 복원을 검증하며 조용히 건너뛰지 않는다.
- 혼합 OFF/ON 출력·프로필 구분을 확인한다. 잘못된 method/collection·미지 profile·end 불일치·secret·byte/node초과·OFF·copybusy·병렬 16/8MiB 예약·복사실패·stop/epoch 변경 모두 native 동작·계산을 보존해야 한다.
- 모든 거부를 capture_limit_reached로 부르지 않는다. payload/profile/node는 정합성/입력범위 실패다. 예상 메모리/event 승인 사유일 때만 포화 시험으로 분류하고 첫 한도/거부를 정확히 출력한다.
- 혼합 durable 저장·OFF/ON 교대를 완료한다. 비공개 4GiB 여유 guard를 유지하며 실제 4GiB/1M·8GiB/5M 한도에 닿도록 개수를 늘린다. 정확한 불변 NAS 소스로 RSS/MemAvailable/cgroup·지연·직렬 byte·pacing 저장 시간을 기록한 뒤 세션/파일 한도를 결정한다.

PC 구현 증거(2026-10-07): 프로필을 복사 전에 선택·16MiB 예약하고 2copy 최대 32MiB 다. 재생 인수 reader는 같은 제한 decode·method/collection/profile 검증을 사용한다. start/end는 필드 존재 여부까지 같아야 한다. 집중 10개·합동 95개 통과. 비공개 5000 종목 혼합 probe는 catalog1 호출/5001 문서를 저장·재읽고 profile 쌍·294이벤트 시퀀스/체크섬/참조를 검증했으며 Windows 저장 5.29초였다. 코덱/로컬 파일 증거일 뿐 RSS 표본·20 메시지로 NAS I/O·65분을 인정하지 않는다.
v4 NAS는 DSM이 --cpus1에 NanoCPUs can not be set을 반환해 테스트에 도달하지 못했다. trap은 활성/두 운영 container 보존을 확인했다. v5는 CPU 옵션 제거·API 통과 뒤 trace start에서 51개 중 41 오류였다. /tmp tmpfs128MiB가 시작 여유 256MiB 보다 작았기 때문이며 용량 probe 전 설정 실패다.
v6도 지연 시작의 실제 file-store4GiB+64MiB가 tmpfs512MiB를 넘어 실패했고 T1–T4 테스트 3개가 후보에 없었다.51개 중 21 오류는 설정/fixture이지 용량 측정이 아니다. v7 `2026.10.07-causal-deferred-capture-v7-5127bafdc15c374a`는 폐기 tmpfs만 5GiB로 올리고 누락 3개를 추가했다. 네트워크 격리·메모리 12GiB·읽기 전용 소스·활성/container 동일성 검사를 유지했다. 이 준비 시점 v7 NAS 검증·용량/RSS/여유/ON-OFF/저장 결과는 미완료이고 예약은 유지했다.
오프라인 4GiB 혼합은 5000 메시지·REST/catalog250회·325758이벤트·charge2,967,469,329·peakRSS842,534,912 바이트로 포화하지 않아 입력 형태 측정만 인정한다. NAS 검증은 5000 종목 혼합으로 두 후보 한도의 명시적 메모리/event 포화와 4GiB 여유 확인을 요구한다.

수정 범위는 diagnostic_replay_contract.py·diagnostic_trace.py·operation preflight reader·후보 app.py capabilities·대상 회귀/integration fixture·비공개 용량 script 다. 승인 후보 app에서 구성하고 무관한 변경이 있는 root app을 쓰지 않는다. DB 메서드·native catalog 변환·런타임 일정·활성 pointer·예약 변경은 프로필 구현 범위가 아니다.

<a id="nas-v7-sizing-evidence-and-private-v8-continuation"></a>

#### NAS v7 용량 증거·비공개 v8 후속

제공 causal-capture-sizing.1sKoif.log는 API·unit94·전용 PG15를 건너뜀 없이 통과했다. 혼합 5000 종목 durable smoke는 294이벤트·payload/chunk 참조를 확인했다. 그러나 4GiB/1M은 630132이벤트,8GiB/5M은 1267390에서 보관 메모리 복사 예약 승인 거부로 포화했다.
8GiB의 charge8,556,861,911·RSS2,508,378,112·host 여유 15,572,205,568·cgroup 여유 10,363,731,968은 바이트이며 charge는 RSS가 아니다. 단순 OFF/ON p95=2.967/7.896ms 다. 혼합 호출·실제 consumer는 별도 지연 검증이 필요하다. 합성/외삽 65분률은 실제 시장 최대가 아니며 정합성 통과만으로 용량/부하를 승인하지 않는다.

v8은 v7의 native 소스를 유지하고 build·비공개 probe/gate만 바꾼다. 폐기 16GiB container에서 12GiB/5M·시작여유 13GiB·실행 중 host/cgroup4GiB를 검사하고 RSS 최대·최소 여유·분류실패·혼합 OFF/ON 지연·진행을 보고한다. payload/profile/type 오류를 뒤의 포화로 덮지 않는다. POSIX logging wrapper는 pipeline 뒤에도 명령 실패를 유지한다.
tmpfs5GiB는 빈 store의 4GiB+64MiB disk preflight에는 맞지만 charge12GiB가 별도전체 4GiB durable quota에 맞는 증거는 아니다. 전체 pacing drain·실제 시장 최대·운영 RSS/지연은 미완료다. 통과 전 운영 4GiB/1M·활성 v6·예약 유지. PC97개/API·158이벤트 smoke 거부/drop0 통과이며 NAS 용량 시험은 아니다.

<a id="replay-report-call-correlation-2026-10-03-local-candidate"></a>

### 재생 보고서 호출 연결(2026-10-03 로컬 후보)

재생마다 replay_call_id를 만들고 관측 DB request_id로 전달한다. source trace/report의 source_call_id·생성 db_call_id·시도행·domain개수·elapsed/execute/commit 시간·transaction/commit/rollback 수·SQL 수·결과를 연결한다. writer 지표에 없는 query-minute 형태 필드는 0 으로 암시하지 않고 incomplete로 센다. 직접 호출 합계는 연결된 호출만으로 구성한다.

phase.database_delta는 측정 구간 전후 차이를 유지하고 scope를 옆에 명시한다. pg_stat_wal은 cluster 전체, pg_stat_database는 대상 DB이지만 sampler query·무관 활동도 포함한다. 이 counter로 트랜잭션별 WALbyte를 알 수 없으므로 call WAL은 null·이유를 기록한다. call 별 COMMIT 수/시간은 직접 연결하고 cluster/DB 구간 counter는 별도 노출한다.

보고서 변경은 로컬·활성 NAS 미배포다. 우선 10 월 6 일 장중 수집을 막지 않는다. 수집은 입력행/변경행/중복키/revision 삽입을 기록하는 활성 db-minute-replay-v1을 쓰고 재생/보고서 개선은 안전한 수집·검증 뒤 배포할 수 있다. 로컬 78개 통과·진단 DB URL이 없어 전용 PG4개 건너뜀. 보고서 변경 NAS 검증은 남았다.

필수 테스트: multi-thread seq/queue, begin/end 밖 경계, overflow 표시, chunk checksum·partial·manifest crash recovery, disk full/permission 오류, master OFF/TTL/instance 변경, clock jump, shutdown 시 flush timeout, raw/trace 독립성, observer 실패의 DB 결과 불변, native transaction/peer 독립 commit, API 인증·경로 탈출·용량 제한.

오버헤드 목표는 OFF 대비 호출 hot path 추가 p95 0.1ms 이하 및 동일 DB workload 처리량 저하 2% 이하로 두되 **목표이며 미측정**이다. 메모리 증가/수집 bytes/sec/flush·fsync 시간을 함께 보고한다. 단순 SELECT와 대표 writer 모두 검증한다. 목표 초과 시 scope를 축소하거나 오늘 fallback으로 전환하며 green이라고 보고하지 않는다. NAS 같은 볼륨의 진단 파일 I/O는 0이 아니므로 저장장치 상관 분석에 진단기 자체 writes를 표시한다.

이 설계만으로 13시·10월 6일 capture 예약이나 자료 확보가 완료된 것은 아니다. 현재 활성 NAS release, 배포 여부, trace 실제 시작/종료, coverage와 유실 여부는 각각 별도 증거로 남긴다.

<a id="rest-request-lane-sizing-correction-2026-10-07"></a>

#### REST 요청 lane 용량 계산 수정(2026-10-07)

v8 NAS 비공개 12GiB/5M은 mixed24361회에서 owner 별 4MiB request-lane 예산이 broker identity2개를 거부하여 멈췄다. 메모리여유·전체 event가 먼저가 아니었다. 구 계산은 request tuple/signature 문자열의 문자마다 4byte를 더했고 tuple repr의따옴표/escape가 실제 보관 문자열보다 빠르게 계산값을 키웠다.

v9는 내부 ordinal-map 키에 canonical UTF-8 signature 전체를 보관하고 정확한 길이·기존고정 512byte·보수적 actor charge를 계산한다. UTF-8은 가역적이어서 actor/signature 별 ordinal·정확요청대조는 유지한다. payload/signature 형식/sequence 쌍/reader/4MiB/32768 키는 동일하다. 기존예산 내 4000 요청·Unicode/escape·반복·byte/entry 거부·새 token reset을 검증했고 재귀보관크기 추정은 charge 보다 작았다.

v9 NAS는 3968 요청키/2,911,020 바이트로 4MiB 보다 낮았다. 이후 100ms 합성메시지 29731개(약 49.6분)에서 12GiB 복사예약을 포화했다. accepted1,905,826·capture_memory_full28·drop0·peakRSS3,735,203,840·최소 host 여유 14,344,683,520·cgroup 여유 13,431,042,048 바이트다. REST 키 과대계산 수정은 확인했지만 12GiB도 합성 65분에 부족하다.

비활성 v10은 비공개예산 16GiB·폐기 cgroup20GiB만 늘린다.100ms39000 메시지 동안 host/cgroup4GiB 여유·RSS/최소 여유를 보고하며 운영 4GiB/1M은 유지한다. PC100/API·작은 16GiB smoke 통과. NAS65분은 시작하지 못했다. 운영 preflight는 예산+1GiB=17GiB 여유가 필요했지만 직전 host18,073,481,216 바이트는 180,129,792 부족이었다. 입력 없으므로 16GiB 포화 증거가 아니다.
v11 `2026.10.08-causal-deferred-capture-v11-dffa8b971619a1ea`는 정확한 v10/928 파일/commit `ae1e7cbe9c71ec44ca83cdeddbcae4bf529137d8`에서 비공개예산만 15GiB로 바꾸고 활성/runtime 유지했다.39000/5M/cgroup20GiB/guard4GiB에서 37201 메시지(약 62분) 뒤 복사예약포화다. accepted2,384,653·거부 28·drop0·charge16,075,246,320·peakRSS4,675,239,936·host 최소 13,404,901,376·cgroup 최소 16,789,262,336 바이트다. 물리 reserve는 지켰으나 15GiB charge가 부족했고 event5M이 먼저가 아니다. 복사 max2481ms·메시지 max1833ms의 책임 event 미확정으로 부하 미승인이다.

실제 host MemAvailable≥18,253,611,008 바이트(17GiB)일 때만 기존 v10/16GiB를 다시 확인한다. v11 시작표본 18,036,588,544는 217,022,464 부족했다. 운영 preflight를 우회/약화하지 않으며 4GiB/1M 유지다. v10 합성 전체완료도 실제 시장 최대·한도 근처 pacing 저장·별도전체 4GiB 파일 quota·복사이상치원인을 증명하지 않는다.

2026-10-08 제어 60분 copy/GC 증거: 비공개 15GiB/5M이 36000 메시지/2,307,608이벤트를 거부/drop0 으로 완료했다. 계측반복의최장 REST 불변복사 2448.337ms 중 같은 thread 자동 GC2430.272ms, charge19268byte realtime1989.087ms 중 GC1988.862ms, save_query1636.053ms 중 GC1617.831ms 였다. Gen2 합계 24801.542ms/38회다.
긴복사원인을 GC 정지로 좁혔지만 운영범위·허용부하 검증은 아니다. 큰지연 저장은 남았다. 전역 GC disable/freeze/threshold는 선택수정이 아니다. 수집별 packed 보관과배경 RAM 인코딩을 비교하되 불변입력·소유/refcountcharge·sequenceID·후속출처·취소/shutdown·durable 형식을 유지한다. 계측시간을최종비계측성능 기준으로 쓰지 않는다.

<a id="deferred-capture-gc-decision--2026-10-08"></a>

### 지연 수집 GC 결정 — 2026-10-08

**상태: 구현 설계 선택·당시 미구현·배포 미승인.** 첫 후보는확인된 `2026.10.08-causal-capture-60m-v1-a8376b5b6bdf96e7`에서 시작하며 변경있는 main·계측 copy-profile을기준으로삼지않는다. schema3·모든인과 경계·native workload 소유·recorder 선택·저장재생형식은 유지한다.

<a id="evidence-and-alternatives"></a>

#### 증거와 대안

NAS copy-profile은 최장 REST 복사 2448.337ms 중 같은 thread의 GC 2430.272ms, 작은 realtime 복사 1989.087ms 중 GC 1988.862ms를 확인했다. 관측된 정지 기전의 증거이며 특정 보관 객체군이 전체 GC 비용을 단독으로 설명하는 증거는 아니다. 기존 `_hold_deferred`는 persist_at까지 writer의 `_QUEUE` drain을 막아 event dictionary·불변 payload tuple·DeliveryStage/Context 그래프를 세션 전체에 보관한다. slots 공유는 RSS를 줄이지만 단계별 GC 추적 instance는 남긴다.

| 선택지 | 판단 | 이유 |
| --- | --- | --- |
| 예산 확대·수집 입력 축소 | GC 수정으로 기각 | 관측된 정지를 제거하지 못하며 범위 축소는 수집 목표에 어긋난다. |
| process 전체 GC disable/freeze/threshold 조정 | 기각 | 수집 밖의 매매·구독·cache·순환 객체에도 영향을 주고 회수를 미루거나 숨긴다. |
| producer에서 모든 event/payload 직렬화 | 선택 안 함 | 장기 객체는 제한하지만 전체 인코딩이 collector/DB caller 지연에 추가된다. 구 delivery 전용 PC 실험은 혼합 NAS 입력 검증이 아니다. |
| slots·공유 참조만 확대 | 완전한 수정으로 선택 안 함 | event별 Python 그래프가 남아 오래된 세대의 탐색 상한을 확정하지 못한다. |
| 새 recorder process·공유 메모리 전송 | 보류 | 이번 수정 범위를 넘어 IPC·시작/종료·실패 복구·입력 소유 경계를 추가한다. |
| 기존 recorder worker가 제한 batch를 RAM byte segment로 packing | 첫 후보로 선택 | producer copy 정책·지연 파일 쓰기·worker 소유권을 유지하며 한 시간 보관 그래프를 제거한다. CPU/GIL/backlog 검증이 필요하다. |

여기서 packing은 바이트 인코딩이며 **압축이 아니다**. 수집 중 zlib/zstd·content hash·중복 제거·파일 쓰기·두 번째 worker를 추가하지 않는다. hash·중복 제거는 기존 durable 쓰기 경계에서 한다. 최종 성능 검증까지 CPython GC 설정은 유지한다.

<a id="data-path-and-ownership"></a>

#### 데이터 경로와 소유권

1. producer는 기존의 제한된 type/secret/cycle 검증·불변 payload 복사·2copy semaphore를 유지한다. scalar/delivery는 기존 seq/epoch lock을 사용한다. 가변 native payload를 worker에 넘기지 않으며 native 작업은 recorder 공간을 기다리지 않는다.
2. 지연 세션의 `_QUEUE`는 제한된 원시 staging queue다. 기존 단일 worker는 persist_at와 무관하게 running/awaiting_persistence에서도 RAM으로 drain한다. 비지연 모드는 기존 writer다. sequence는 승인 때 한 번만 부여하며 packing·저장에서 재번호·재정렬하지 않는다.
3. worker는 `_LOCK` 밖에서 기존 필드 열거·불변 payload JSON 규칙으로 제한 batch를 펼친다. metadata/payload JSON을 비공개 길이 prefix frame에 따로 두어 저장 때 전체 payload를 복원·재인코딩하지 않는다. metadata의 첫/끝 sequence·검증 길이, segment의 format/version/event 수·할당 계산을 기록한다. 내부 RAM 형식이며 새 replay/wire 형식은 아니다.
4. 목표 1MiB의 봉인된 불변 bytes segment를 보관한다. 허용된 큰 단일 frame은 전용 제한 segment를 쓴다. packed frame 옆에 event dictionary·객체 tuple·DeliveryStage·decoded payload를 중복 보관하지 않는다. segment/index는 event 수가 아닌 block 수에 비례한다. 덜 찬 가변 segment는 worker 소유이며 계산 대상이다.
5. 원시 source 기록은 `_LOCK` 안에서 segment 이전이 성공할 때까지 소유·charge한다. 이후 기존 규칙으로 공유 Context 참조를 해제하고 packed segment를 원자적으로 공개한다. 사용 중 native receipt는 유효하며 후속 dequeue/consume는 정상적으로 context를 다시 보관할 수 있다. subscriber/collector receipt·context 값은 바꾸지 않는다.
6. persist_at에 승인을 닫고 승인된 복사·원시 packing을 drain한 뒤 기존 payload hash/bundle/chunk writer에 frame을 전달한다. 제한된 metadata frame 하나씩만 decode하고 미리 인코딩한 payload byte로 기존 digest/bundle을 쓴다. 마지막 참조 frame이 durable COMMIT될 때까지 segment byte·charge를 유지한다. 부분 chunk는 cursor/segment 소유권·미저장 뒤쪽을 유지하고 전체 원시 그래프를 다시 만들지 않는다.
7. accepted/written·drop/거부·producer/call/event ID·source/후속 연결·원래 시각의 의미는 그대로다. packing은 durable 쓰기가 아니므로 persist_at 전 written은 0이다. complete는 전체 event의 durable 저장·최종 manifest fsync가 필요하다.

수명·lock·승인과 기존 durable writer는 diagnostic_trace.py에 둔다. diagnostic_trace_ram.py는 제한 frame/segment packing·cursor 반복·할당 계산만 소유할 때 허용한다. 일반 queue/service/plugin 계층을 추가하거나 freeze_payload·broker·collector·DB transaction·재생 workload 계약을 바꾸지 않는다.

<a id="limits-and-state-contract"></a>

#### 한도와 상태 계약

- 초기 **비공개 후보** staging은 원시 charge 64MiB·원시+packing 32,768event다. byte 검사에는 진행 중 copy 예약을 포함한다. 기존 disk 48MiB backlog와 달리 RAM으로 계속 drain한다. 시험값이며 운영 승인값은 아니다.
- 전체 보관 event는 원시+packing+packed+writer 대기를 합산한다. 세션 5M 한도가 전체 수집 32,768개로 줄어들면 안 된다. 공유 entry·segment 부분 cursor는 승인 event의 논리 개수를 바꾸지 않는다.
- 원시 계산은 유지한다. packed 계산은 실제 bytes/buffer 할당·segment/index·고정 allocator 여유를 센다. 살아 있는 Python 그래프에 JSON 길이를 상한으로 쓰지 않는다. 표현별 계산이며 RSS 비율 할인은 아니다. 인코딩 중 두 표현을 모두 세거나 증명된 최악 임시 reserve를 쓴다. 공개 시 기존 charge를 원자적으로 교체하며 미계산 할당 구간이 없어야 한다.
- 비공개 worker 임시 reserve 128MiB로 시작한다. 허용 최대 payload·Unicode string/bytes·metadata·segment 봉인 복사·writer chunk/bundle buffer를 포괄함을 증명한다. 부족하면 승인 전에 상한을 조정하고 우회하지 않는다. 입력 copy 한도·scalar reserve·독립 파일 quota·host/cgroup preflight를 유지한다.
- 15GiB/5M은 정확한 NAS 검증에서 운영값을 고를 때까지 비공개다. packing으로 charge/RSS 관계가 바뀌므로 구 15GiB를 자동 승계하거나 작은 출력만으로 낮은 예산을 승인하지 않는다.
- 원시 최대값 초기 1MiB/256event와 짧은 제한 보조 간격으로 worker를 깨운다. 승인 lock 밖에서 인코딩하고 초기 128event/4MiB 원시 charge/10ms의 작은 batch 후 양보한다(허용된 큰 기록 하나는 예외). 조정 후보이며 평균뿐 아니라 단일 기록 GIL/loop lag도 측정한다. thread는 collector 정지 없음을 보장하지 않는다.
- staging/전체 예산 소진 시 native는 계속하고 명시적 거부·추가 capture 승인 중단/incomplete를 기록한다. 승인된 원시/packed 데이터는 persist_at까지 보존한다. 공간 확보용 조기 flush·입력 폐기/대체는 금지한다. scalar 거부 event도 enqueue하지 못해도 실패를 노출한다. 무제한 원시 RAM으로 돌아가지 않는다.
- stop/만료는 늦은 승인을 거부하고 사전 승인 copy를 기다린다. 저장 전에 원시 staging을 packed segment로 봉인한다. status는 packing backlog·저장 시각 대기를 구분한다. packing 오류는 charge된 원시 뒤쪽·packed 앞쪽을 보존하고 failed/incomplete로 표시하며 complete를 금지한다. 저장 전 shutdown은 기존 interrupted/손실 규칙으로 두 표현을 해제하고 조기 disk flush를 하지 않는다.
- O(1) counter로 staging_events/bytes, packing_events/bytes/reservation, packed_events/bytes/segments, writer_pending_events, 최대값·pack 시간·거부 사유를 유지한다. 운영 status에 event별 시간 log·전체 queue 순회를 넣지 않는다. probe는 counter·작은 제한 fixture decoder를 사용하고 확인하려고 수백만 event를 복원하지 않는다.

<a id="required-tests-and-acceptance-order"></a>

#### 필수 테스트와 검증 순서

1. frame/segment: 0/Unicode/최대 payload·큰 단일 frame·잘못된 길이·기존 metadata/payload 인코딩·segment 간 순서·제한 decode를 확인한다. hash·인과 ID·delivery 없음/null 구분·승인 catalog 프로필은 같아야 한다.
2. 상태/계산: 병렬 producer/copy 예약, pack/admit/stop 경합, batch 간 공유 context, 첫/후속 stage 거부, 원시 해제 후 receipt 재사용, 구 epoch, pack 실패, 부분 disk COMMIT, 최종 manifest 실패, 폐기/shutdown을 확인한다. 모든 경계에서 음수 계산이 없고 승인 event마다 소유자·위치가 하나임을 assert한다.
3. T1–T4 모든 종류를 포함한 작은 native 혼합 왕복에서 OFF와 같은 consumer 결과·store 호출을 확인한다. 속도 제한 durable 저장 후 모든 payload·쌍·delivery 종료를 검증한다.
4. 같은 36,000메시지로 비계측 OFF/현재/packed를 교대하고 동일 입력 burst 변형을 비교한다. producer p50/p95/max·loop heartbeat 지연·CPU·GC 시간·원시 backlog 최대/나이·거부/drop·RSS/host/cgroup 여유·byte/event·wire 일치를 측정한다. 계측 원인 probe로 최종 시간을 승인하지 않는다. GC 정지를 encoding/GIL/queue 지연으로 옮겨서는 안 되며 기존 부하 목표를 유지한다.
5. 격리 NAS scratch에 전체량을 1MiB/s·cooldown·파일 quota 검사와 함께 저장한다. 모든 chunk/체크섬/sequence/참조를 제한 메모리로 스트리밍 검증하고 작은 smoke의 hydrated list를 수백만 event에 쓰지 않는다. 인코딩/저장 최대 RSS·최종 byte·fsync 지연·disk 여유·완료 시간을 측정하고 중단/실패는 별도 검증한다. 운영 파일·제어는 보존한다.
6. 이후 전체/staging 한도 선택·승인 후보 준비/활성화·시작/검증 예약 둘 다 09:00–10:00 및 정확한 build/release/schema/options/persist_at으로 갱신한다. 그전에는 활성 v6·기존 예약을 유지한다. 이 설계는 실제 장 시작 최대 처리량·전체 재생 범위의 증거가 아니다.

구현 인계: Sol High. 소유권/계산 이전·producer/worker 경합·부분 durable segment cursor의 방법은 구체화했지만 복잡한 동시성 작업이 남는다. Astra 설계 단계는 여기서 끝난다. 더 단순한 구현도 이 불변 규칙을 보존하고 범위를 넓히지 않아야 한다.

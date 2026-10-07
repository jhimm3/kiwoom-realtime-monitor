# 장중 경량 trace와 장후 선택 재생 설계

2026-10-05 후속 [고정 장중 입력의 다중 workload 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)은
실제 입력 보존·전체/단독/제외·수정 전후 비교를 추가한다. 이 문서의 scalar trace와
synthetic replay는 유지하지만 실제 payload/key 관계가 기록된 것으로 취급하지 않는다.
새 payload capture와 처리량·보존 한도 변경은 아직 구현하지 않았다.

2026-10-05 · O12 · **NAS trace smoke 및 뉴스/shadow·query_minute 합성 replay 완료; 65분 burst와 바이트 기준 청크 검사 통과; chunk-bound 회귀 18건 NAS test container 통과; 실제 NAS 65분 내구성·오버헤드·장중 원본 capture 미완료**

## 목표와 이번 순서

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

### Dedicated DB recorded-operation execution (2026-10-06)

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

### Authenticated trace input capture API (2026-10-06 local candidate)

The authenticated trace-start API now accepts strict `store_inputs` and `collector_inputs`
booleans, both defaulting to false, and forwards them to the schema-2 recorder. Either opt-in
selects schema 2; both together retain allowlisted native store operation inputs and observed
0B collector inputs while the collector excludes other event types and sensitive account data.
The authenticated capabilities response advertises schema 2, the two OFF defaults, 0B scope,
observed-path-only coverage, and `overhead_verified=false`. API/capture regression coverage passed
33 local tests, including real SQLite store and collector paths, invalid/auth/session/TTL failures,
and existing concurrent-start protection. Candidate build is
`2026.10.06-recorded-capture-api-v1`; NAS staging, activation, a complete 65-minute capture,
and capture overhead remain unverified. NAS source-runtime candidate
`2026.10.06-recorded-capture-api-v1-af7d3d49b61aad09` (888 files) was staged and then activated.
Authenticated `/health` and capabilities reads confirmed matching build/source release, schema 2,
the 0B scope and both OFF defaults. Master and trace were OFF; realtime was `WAITING_MARKET` with
observation not expected. The 08:54 capture and 10:01 verification automations require the active
build/schema-2 capability and both input flags, and leave diagnostics off if preflight fails. A real
65-minute capture and overhead measurement remain open.

### Deferred RAM capture and paced after-hours persistence (2026-10-07 local candidate)

The trace-start API accepts optional `persist_at` (Unix seconds). Only this explicit mode uses
a 4GiB charged-RAM budget and 1,000,000-event cap. Startup requires Linux host and cgroup memory
headroom of at least 5GiB; this is a headroom check, not an RSS hard cap. At capture expiry the
producer token closes and the owned writer keeps payloads in RAM without periodic payload/chunk
I/O until the deadline. Master TTL expiry does not move the deadline. The retained in-memory trace
holds the existing diagnostic run lock, so replay/measure/compare cannot overlap it.

At `persist_at`, payload and JSONL bytes are paced in 64KiB blocks at no more than 1MiB/s, then
the worker adds up to five seconds of cooldown after each chunk sync. It does not accumulate pace
credit while waiting on slow storage. During deferred flush, the growing blob index is checkpointed
at most once per minute plus a final durable manifest. Capture stop does not trigger early flush;
server shutdown before the deadline marks the trace interrupted and leaves only its already durable
initial manifest. A process crash loses the RAM tail by design. Disk/manifest failure must never
publish `complete`.

Local regression coverage passed 46 executions across deferred lifecycle/API, chunk durability,
and recorded input tests, including 33,001 sequenced events held past the former 32,768-event cap,
payload integrity, no pre-deadline payload files, paced writes, shutdown, master OFF, expiry, and
headroom refusal. NAS host `MemAvailable` was 18,077,790,208 bytes in one read-only snapshot;
NAS container headroom and the new release have not been verified. The immutable candidate
`2026.10.07-trace-deferred-ram-v1-2c08d26bca5b6f4f` is staged but inactive; build marker is
`2026.10.07-trace-deferred-ram-v1`. SSH access can read NAS files, but NAS source-runtime tests and
activation require interactive sudo authentication. The October 8 automations still need to be
updated to pass `persist_at=20:10 KST` and verify after persistence. Do not start the capture until
the deferred capability is live on `/health`/capabilities and container headroom is verified.

### T1–T4 deferred capture capacity decision (2026-10-07; implementation pending)

This decision continues O12 and supersedes any assumption that raising the deferred limits to
8GiB/5M alone proves a complete 65-minute recording. Active v6 and the October 8 08:55 reservation
remain unchanged. The staged `2026.10.07-causal-deferred-capture-v1-24cd50ff92fe752b` is an inactive
functional integration candidate, not an accepted capacity release. Its local 78-test/API gate does
not substitute for its own NAS correctness, memory, latency, and persistence gates.

#### Updated capture window (2026-10-08)

The user selected 09:00–10:00 KST: the capture request is 3,600 seconds. The earlier 65-minute
results remain historical sizing evidence. The next immutable private candidate must test 36,000
20-row messages at 100ms synthetic timestamps with the exact v11 mixed-input profile. Synthetic
input duration is distinct from benchmark wall-clock duration. Add the requested duration to probe
output and report capacity-rate estimates for that duration explicitly; do not relabel an earlier
65-minute rate as a 60-minute measurement.

Keep the retained-charge implementation unchanged for this step. Its private 15GiB accounting
budget is not physical RSS allocation: v11 recorded 16,075,246,320 charged bytes and a process RSS
peak of 4,675,239,936 bytes. Capacity, RSS, host/cgroup headroom, admission rejection and persistent
output are independent limits. The operational 4GiB/1M limits and active/scheduled source remain
unchanged until the release gates below pass. A 60-minute synthetic capacity pass by itself does not
complete overhead, paced near-capacity persistence or actual market coverage acceptance.

#### Evidence and limits of the evidence

The earlier 8GiB/5M private probe refused an input at 326,960 delivered ticks and 1,013,577 accepted
events, with process RSS about 1.26GiB. Receipt scalar accounting, not the event cap, saturated first.
`queue_delivery` creates one receipt per subscriber delivery; enqueue/dequeue/consume_end each store
the same identity fields in a separate dictionary. `_field_charge` recursively charges keys and
referenced strings for every stage, even when their actual Python objects are shared.

A design-only Windows comparison used the same native collector/hub/consumer path with 2,000
messages of 20 ticks: 40,000 deliveries and 124,001 logical events in every case. Inputs had one
TOP20 subscriber; no REST transport, production DB, network, scheduled capture, or persistence ran.

| RAM representation prototype | Charged MiB | RSS increase MiB | Producer p50/p95 ms per 20-row message |
| --- | ---: | ---: | --- |
| Existing dictionaries/accounting | 998.28 | 155.13 | 2.7968 / 3.6025 |
| JSON bytes for delivery fields | 303.99 | 176.28 | 2.8531 / 3.6355 |
| Fixed columns with per-record dictionary header | 357.86 | 139.47 | 3.1205 / 4.1399 |
| Shared fixed columns with slotted stage record | 357.86 | 106.04 | 2.6304 / 4.0271 |

These are single controlled burst runs, not a latency win or NAS acceptance. The slotted prototype
verified field roundtrips for enqueue/dequeue/consume_end, but did not persist or replay them. It
still conservatively charged the full shared identity separately per stage. Actual production
code and the staged candidate were not changed by these process-local monkeypatches. Reports are
`artifacts/delivery-design-{legacy,json-bytes,columns,shared-slots}.json`; experiment source is
`artifacts/probe_delivery_ram_representations.py`.

The 20-row fixture implies approximately `3*delivered_rows + 2*messages` events before other inputs.
Thus 5M events alone permits at most about 414 delivered ticks/s for 3,900 seconds in this particular
shape, with no allowance for REST/store/control activity. This is a capacity curve, not the observed
market rate. More source rows than TOP20 deliveries, fragment sizes, or subscribers change the curve.
Only AutonomousTop20Service currently supplies a nonempty production `capture_component`; adding
another such consumer would increase receipts and must invalidate that sizing assumption.

The separate 900-row REST shape costs 3,024,848 charged bytes. Even one such page/s for 65 minutes
would be about 10.99GiB before store inputs/receipts; this is a counterexample scenario, not a claim
that production has this rate. The incomplete October 6 capture and missing local payload bundles
cannot establish the true 09:00–09:10 maximum. There is no finite RAM-only promise for an unspecified
input rate. The release gate must name the tested workload/rate/size envelope and its margins.

#### Implemented boundary (PC regression complete; NAS capacity pending)

Keep all three logical delivery events, original global sequence order, wall/monotonic timestamps,
delivery/parent/subscriber IDs, parser positions, stages, failures and drops. Do not combine them into
one terminal event, sample them, infer missing receipts, or change wire schema 3/replay semantics.
Do not add caller-side JSON/hash/compression. The JSON-bytes prototype reduced accounting but raised
RSS and is rejected. Broad payload codec or asynchronous compression redesign is deferred.

Implement a private compact delivery representation inside the existing recorder boundary, enabled
only for deferred captures. Streaming mode and ordinary trace events retain the current dictionary
path. The compact path consists of:

1. Immutable source context: source component, message/control ID, parent input IDs and completeness.
   Share it through the existing CapturedMessageReceipt/CapturedControlReceipt object, not a global
   content-addressed string registry. Large fragmented messages may contain up to 100 parent IDs;
   do not multiply their retained-memory charge by every delivered row and stage.
2. Immutable subscriber context scoped to its capture epoch: trace/component/subscriber identity.
3. Immutable delivery identity: delivery ID, sequence, event kind, parser ordinal and references to
   source/subscriber contexts. It is created once at the actual queue delivery, not reconstructed
   later from the latest subscription state.
4. A slotted per-stage queue record: original clocks, global seq, producer, stage/outcome and identity
   reference. Preserve absent fields versus explicit null and every diagnostic failure stage.

The immutable data graph has fixed depth; private retention counters are recorder-owned and guarded
by the existing trace lock. A node is charged on the first retained queue/pending reference and
released on the last. Retain/release children only on those 0↔1 transitions. There is no session-wide
intern map and no per-stage traversal of all parent strings. Compute an admission delta before
publishing a record; on rejection no ownership/count is changed. Global sequence/drop accounting
continues through the same admission boundary. Shared children must be counted once when computing
one atomic admission delta. Cache the built-in shallow allocation charges at immutable construction.

Charge includes node/tuple/string/int allocation, deque references, bookkeeping and a measured
allocator margin, plus existing copy/worker reserves. Use exact built-in types, not arbitrary user
`__sizeof__` methods. This is an upper-bound retention model, not `RSS == charged_bytes`. Confirm
its bound against actual Linux RSS. Keep the generic `freeze_payload` byte/node/type/secret checks
unchanged; the narrowly named catalog copy profile below is a separate measured exception.
Transient producer construction and native queue-held receipt references require a bounded reserve;
the existing subscriber queue cap of 1,000 and message bound of 10,000 rows are inputs to that bound.
An old-epoch object must never be admitted or charged to a new session.

At persistence, the existing worker expands one bounded batch into the original JSONL dictionaries.
Only after the existing durable chunk publication succeeds does it release each retained record and
its identity graph. Pending suffixes and partially written chunks retain their full charge. A stage
persisted before its siblings must not free shared context early. Abort/failure cleanup must leave no
global retention registry or cross-session references. Use one explicit conversion/release boundary
in `diagnostic_trace.py`, not a new store, transaction, or general framework.

Likely edits: `diagnostic_trace.py`, the private receipt types in `realtime_hub.py`, delivery hooks in
`diagnostic_top20_input.py`, and source receipt creation in `realtime_collector.py`. A small dedicated
record-layout module is allowed only if it owns this representation/accounting contract. Native
consumer queue policy, subscription policy, DB connections/transactions and collector outputs do not
change. A capture failure must remain visible while native delivery continues.

#### Independent limits and correctness gates

- Deferred retained bytes, logical event count, per-input copy bytes/nodes, copy concurrency,
  available host/cgroup memory, and persistent bytes are independent gates. Report each separately.
- `_MAX_STORED_BYTES` is currently 4GiB across existing captures. Even an 8GiB RAM candidate can
  still fail at delayed persistence. Introduce an explicit per-capture persistence quota and matching
  total-retention quota only after measuring final JSONL+unique-payload+manifest bytes. Require the
  whole requested quota to be available at preflight; do not silently shrink it with `min(...)`.
  Preserve old recordings and the 1MiB/s pacing/cooldown. Report projected finish time including
  manifest writes/fsync; actual completion remains a separate gate.
- Any `input_rejected > 0` must make terminal capture state incomplete, even if scalar enqueue worked,
  `known_dropped == 0`, and accepted == written. Current terminal-state construction only checks
  known_dropped/censorship; add the input-rejection condition as a separate correctness correction.
- Test durable JSON equality with legacy records, Unicode/100-parent fragmented inputs, multiple
  subscribers, dequeue/drop/failure/missing stages, epoch switch, rejected first/later stages,
  concurrent admission, pending suffix retention, final-manifest failure and shutdown. Validate
  original delivery coverage and T4 native/descendant exclusion replay against persisted output.
- Measure small and near-capacity input distributions, not only repeated small messages. Include
  REST/catalog/ranking/subscription/lifecycle receipts, typed store inputs and deferred save. No
  zero/missing group counts as coverage. Native outputs/transactions must match OFF controls.

#### Capacity selection and release gate

8GiB/5M is a candidate setting, not a selected configuration. First run the compact implementation
against the same failing native fixture and its burst variants, then a mixed named input envelope.
Collect actual RSS/peak RSS, MemAvailable/cgroup headroom, memory charge, event count, copy rejection,
latency/loop lag and final output bytes. Use alternating OFF/ON rounds; no parallel benchmark jobs.
Keep the existing documented overhead goals and explicitly report misses; do not relabel a regression
as acceptable merely because memory fits. The source workload must identify 0B/0w delivery rates,
messages/fragments, subscriber fan-out, REST pages/rows, catalog refreshes, store shapes and lifecycle
churn. A synthetic envelope is not an actual market maximum or performance baseline.

Keep at least 4GiB host MemAvailable as an initial guardrail while testing an 8GiB candidate, and
include production baseline RSS/cgroup headroom. Stop the private test before violating the reserve.
The reserve is a proposed safety margin to verify on this 20GB NAS, not an observed peak requirement.
Do not increase to 12GiB or more unless the mixed envelope needs it and measured NAS headroom permits
it with the same reserve. A lightweight worker-side memory-pressure check may close only the capture
gate as incomplete and keep accepted data until persist_at; it must not pause native workloads or
flush early. Never promise this polling guard is a hard process RSS limit.

Only after immutable candidate regressions, exact-source NAS correctness/overhead/capacity/persistence
gates and explicit coverage checks pass may its build/options replace active v6 and the October 8
reservation. Update start AND verification expectations together (schema3, all three flags true,
accepted limits, exact release, deferred persist_at). If the deadline arrives before gates pass,
leave the existing v6 reservation unchanged and report that expanded T1–T4 capture was not approved.

#### Catalog copy profile decision (2026-10-07; PC implementation complete, candidate gate pending)

The mixed native boundary probe exposed a third limiting factor before retained RAM or event count:
`AutonomousTop20Service._ensure_market_catalog` converts 5,000 `(code, name, market)` rows into
5,001 document rows and invokes `replace_documents("stock_catalog", documents)` once. The catalog
response itself is accepted, but that store operation exceeds the default 8MiB *in-memory copy
charge*. This is not an 8MiB file or network response. At rejection the whole private trace held only
12,908,809 charged bytes, 79 accepted events, zero scalar drops and one rejected store input.
The first over-budget visit was 8,388,644 bytes / 44,661 nodes (limits 8,388,608 / 120,000).
The native call continued. Increasing the total session RAM cannot fix this per-input rejection.

Controlled PC comparison (`artifacts/catalog-capture-design-comparison.json`, five copies per case):

| 5,000-stock case | Retained copy charge | Encoded payload bytes | Copy median / max |
| --- | ---: | ---: | ---: |
| Unchanged arguments, 8MiB | rejected | not produced | not applicable |
| Unchanged arguments, 16MiB | 14,038,408 | 814,054 | 36.08 / 45.04 ms |
| Columnar prototype, 8MiB | 3,359,372 | 284,101 | 9.23 / 11.98 ms |

The columnar timing excludes building/validating its new layout, and fixture equality is not a
general codec correctness proof. These numbers are PC design evidence, not NAS latency acceptance.
The 2,500-stock mixed deferred probe separately completed 150 events, with all chunks, references,
sequences, REST pairs, subscription approvals and delivery closure verified; its files totalled
1,773,717 bytes. It does not excuse the 5,000-stock failure or establish a 65-minute bound.

**Decision:** keep the exact native store arguments and select a bounded **16MiB catalog store copy
profile**. Do not implement fragmentation, columnar serialization or parent-payload reconstruction
for this candidate. The native catalog task runs via `owned_to_thread`, is reused for the same day
after success, and has no per-tick store call. The measured extra copy reservation is preferable to
a new encoding/reconstruction contract before this release. This decision only permits implementing
and testing the profile; it does not approve active deployment or 8GiB/5M session limits.

Implementation contract:

1. Add one named profile, `stock-catalog-documents/v1`, owned by `diagnostic_replay_contract.py`.
   Select it only for captured `operation_start` of the native `replace_documents` method with
   actual bound arguments `collection == "stock_catalog"`. Keep the generic 8MiB limit, the
   120,000-node/depth/string/type/secret checks and all other methods unchanged. Do not broaden
   `upsert_documents`, ordinary documents, REST/collector inputs or account inputs. No request
   option may set an arbitrary per-input limit.
2. Record `payload_profile` and `collection` on the operation's start and end. Both must match in
   operation-pair validation. Absence retains legacy 8MiB behavior; unknown, mismatched or misplaced
   profiles fail closed. Keep codec tags, original argument list/tuple/dict types, values, order,
   operation/actor/cause IDs and the single native call exactly unchanged. No catalog regeneration
   from today's template or upstream receipt is allowed during store-only replay.
3. `diagnostic_trace.emit_payload` verifies the method/collection/profile against the actual value
   before admission and reserves the selected 16MiB **before copying**. Copy once using that bound;
   do not first copy 8MiB and retry. Use a local reservation amount on every success, rejection,
   exception, stop/epoch-change and finally path. With the existing two-copy semaphore the maximum
   in-flight reservation becomes 32MiB; include it in RSS/headroom tests and session accounting.
   The generic `_COPY_RESERVATION` remains 8MiB. Native results/errors and connection ownership do
   not change when capture fails.
4. Make decoding symmetric: a central operation-argument reader validates profile metadata, thaws
   with the selected bound, verifies the decoded collection/method binding and runs existing
   operation validation. `thaw_payload` may accept an internal bounded-byte parameter while its
   default stays 8MiB and final freeze validation uses the same selected limit. Do not infer a larger
   budget from unverified payload contents or silently retry decoding after an 8MiB error.
   Route the operation readers in `compile_recorded_plan`, `diagnostic_recorded_execution._prepare`
   and `compile_lifecycle_descendants` through that reader. Audit any additional operation readers;
   REST/collector readers continue to use the generic codec. Pair/profile mismatch must fail before
   native store invocation.
5. Declare supported input copy profiles and limits in candidate capabilities. Schema 3 and
   `store-input/v1` typed encoding remain unchanged; profile metadata declares the expanded input
   envelope. Older readers remain bounded and reject oversized inputs. Preserve the original
   dataset selection/provenance rules: native TOP20 replay replaces its descendant catalog write;
   TOP20 exclusion never reinjects it; explicitly supported store-only replay invokes the exact
   stored native arguments once. Peer operations remain independent.
6. Preserve existing 16MiB blob/bundle bounds and total file quota. Measure final serialized bytes
   and keep those gates independent; this change is not permission to bypass a persistent bound.
   The 5,000-stock fixture is a named envelope, not a maximum for every possible catalog/name size.
   Values over 16MiB or 120,000 nodes remain visibly rejected/incomplete. Capacity acceptance must
   name actual tested catalog counts/string shapes alongside the rest of the workload.

Required acceptance before activation or reservation change:

- Exact 5,000-stock native catalog capture and durable roundtrip, then standalone catalog store
  replay and full/excluded TOP20 descendant checks in the disposable PostgreSQL fixture. Check the
  same arguments, row values/order, single invocation and baseline restoration. No quiet skipped gate.
- Mixed OFF/ON output equality and distinct profile checks; wrong method/collection/unknown profile,
  mismatched end, secret field, oversized bytes/nodes, capture OFF, copy busy, concurrent 16MiB and
  8MiB reservations, copy failure and stop/epoch-change all preserve native behavior and accounting.
- Do not classify every input rejection as `capture_limit_reached`: payload/profile/node rejection
  is a correctness/envelope failure. A near-capacity probe counts as a memory/event saturation test
  only for the expected memory/event admission reasons. Emit exact first-limit/rejection details.
- Complete the mixed probe's bounded durable-save gate and alternating OFF/ON rounds. Increase
  the mixed probe counts enough to actually reach each tested 4GiB/1M or 8GiB/5M limit, with the
  private 4GiB headroom guard. Run the exact immutable source on NAS, record RSS/MemAvailable/cgroup,
  capture latency, serialized bytes and elapsed paced persistence, then decide the session/file limits.

PC implementation evidence (2026-10-07): the named profile is selected before copying and has a
16MiB reservation; the two-copy gate therefore reserves at most 32MiB. The replay argument reader
uses the same bounded decoder and verifies method, collection, and profile. Start/end metadata must
match including field presence. Ten focused profile tests and the combined 95-test capture/replay
regression set passed. A private 5,000-stock mixed probe durably wrote and re-read one catalog call
(5,001 documents), validated the profile pair, all 294 event sequence/checksum references, and
finished persistence in 5.29 seconds on Windows. This is codec and local-file evidence only; its
RSS sample and 20-message fixture do not establish NAS I/O overhead or a 65-minute capacity bound.
The v4 exact-source NAS gate did not reach tests: DSM Docker rejected its `--cpus 1` option with
`NanoCPUs can not be set`; the trap confirmed the active release and both operational containers
were unchanged. Candidate v5 removed that unsupported CPU limit and its API gate passed, but 41 of
51 unit tests errored at trace start because the candidate's 128MiB `/tmp` tmpfs had less than the
required 256MiB free-space preflight. This is a test-container setup failure before capacity probes,
not evidence that the deferred RAM budget is too small. v6 still failed before capacity probes:
deferred trace start reserves 4GiB+64MiB of real file-store headroom, exceeding its 512MiB test
tmpfs, and three T1–T4 tests were absent from the candidate release. The 51-test run had 21 errors;
these are gate setup/fixture failures, not capacity measurements. Candidate v7,
`2026.10.07-causal-deferred-capture-v7-5127bafdc15c374a`, raises only the disposable test tmpfs to
5GiB and adds the three missing tests. It retains network isolation, the 12GiB memory ceiling,
read-only candidate source and active/container invariance checks. The v7 NAS gate was pending at this preparation checkpoint; no
4GiB/1M or 8GiB/5M capacity, RSS/MemAvailable, ON/OFF latency or paced-persistence result has been
established, and the scheduled capture remains unchanged.
The offline 4GiB mixed capacity probe completed 5,000 messages with 250 REST/catalog rounds, 325,758
events, 2,967,469,329 charged bytes and 842,534,912 bytes peak RSS. It did not saturate the budget,
so it is only an input-shape measurement. The candidate NAS gate now requires mixed 5,000-stock
probes to hit an explicitly classified memory or event limit for both proposed envelopes; these
counts and the 4GiB headroom guard still need NAS confirmation.

Scope of edits: `diagnostic_replay_contract.py`, `diagnostic_trace.py`, operation preflight readers,
candidate `app.py` capabilities, targeted regression/integration fixtures and the private capacity
gate scripts. Build from the accepted candidate app, not the unrelated dirty root app. No database
method, native catalog transformation, runtime scheduling, active pointer or reservation change is
part of implementing this profile.

#### NAS v7 sizing evidence and private v8 continuation

The supplied `causal-capture-sizing.1sKoif.log` completed API, 94 unit regressions and 15 dedicated
PostgreSQL checks without skips. The mixed 5,000-stock durable smoke verified 294 events and all
payload/chunk references. Saturation, however, rejected inputs at 630,132 events for 4GiB/1M and
1,267,390 events for 8GiB/5M, both classified as retained-memory copy-reservation admission.
For 8GiB the charged bytes were 8,556,861,911, RSS 2,508,378,112, host available 15,572,205,568 and
cgroup headroom 10,363,731,968. These are bytes, not GiB; charge is not an RSS measurement.
Simple OFF/ON p95 was 2.967/7.896ms. Mixed calls and actual consumer work need separate latency
acceptance; neither the synthetic envelope nor its extrapolated 65-minute rate establishes the
observed market maximum. No capacity or overhead approval follows from correctness passing.

v8 preserves all native v7 source except the build marker and private probe/gate files. It tests
12GiB/5M in a disposable 16GiB container, requiring 13GiB start headroom and checking host/cgroup
4GiB headroom during execution. It reports sampled RSS peaks, minimum headroom, classified failure,
mixed OFF/ON latency, and visible progress. Invalid payload/profile/type failures cannot be masked
by later saturation. The POSIX logging wrapper preserves command failure past the output pipeline.
The 5GiB tmpfs remains sufficient for the current empty-store 4GiB+64MiB disk preflight; it does not
prove that a 12GiB charged session will fit the independent global 4GiB durable quota.
The full-capacity paced drain, valid market maximum, operational RSS and latency acceptance remain
open. Operational 4GiB/1M defaults, active v6 and the scheduled capture must remain unchanged until
those gates pass. PC exact-source validation passed 97 tests and API wiring; a 158-event smoke
had zero input rejection/drop. This smoke is not a NAS capacity test.

### Replay report call correlation (2026-10-03 local candidate)

Each replay execution now has its own `replay_call_id`, passed as the observed DB call's `request_id`. The report joins that value to the source trace/report `source_call_id`, the generated `db_call_id`, and the call's attempted rows, domain counts, elapsed/execute/commit times, transaction/commit/rollback counts, SQL count, and outcome. Query-minute shape fields that were not recorded by the writer metrics are explicitly counted as incomplete instead of being implied as zero. Aggregate direct-call totals are built only from these linked calls.

The existing `phase.database_delta` remains an interval-wide before/after delta, with an adjacent scope record: `pg_stat_wal` is cluster-wide, while `pg_stat_database` is for the measured database and includes sampler queries and unrelated activity there. PostgreSQL does not expose WAL bytes attributable to one transaction through these counters, so per-call WAL remains unavailable; the report sets it to null and explains why. Per-call commit counts/timings are directly correlated, and cluster/database interval counters remain separately visible.

This report change is local and is not deployed to the active NAS source release. It does not block the higher-priority October 6 market-hours capture. That capture should use the active `db-minute-replay-v1` source whose minute writer records input rows, changed rows, duplicate input keys, and revision inserts; replay/report improvements can be deployed after the trace is safely captured and verified. Local regression tests passed 78 tests; 4 dedicated PostgreSQL tests were skipped because this PC run had no diagnostic DB URL. NAS acceptance of the report change remains pending.

필수 테스트: multi-thread seq/queue, begin/end 밖 경계, overflow 표시, chunk checksum·partial·manifest crash recovery, disk full/permission 오류, master OFF/TTL/instance 변경, clock jump, shutdown 시 flush timeout, raw/trace 독립성, observer 실패의 DB 결과 불변, native transaction/peer 독립 commit, API 인증·경로 탈출·용량 제한.

오버헤드 목표는 OFF 대비 호출 hot path 추가 p95 0.1ms 이하 및 동일 DB workload 처리량 저하 2% 이하로 두되 **목표이며 미측정**이다. 메모리 증가/수집 bytes/sec/flush·fsync 시간을 함께 보고한다. 단순 SELECT와 대표 writer 모두 검증한다. 목표 초과 시 scope를 축소하거나 오늘 fallback으로 전환하며 green이라고 보고하지 않는다. NAS 같은 볼륨의 진단 파일 I/O는 0이 아니므로 저장장치 상관 분석에 진단기 자체 writes를 표시한다.

이 설계만으로 13시·10월 6일 capture 예약이나 자료 확보가 완료된 것은 아니다. 현재 활성 NAS release, 배포 여부, trace 실제 시작/종료, coverage와 유실 여부는 각각 별도 증거로 남긴다.

#### REST request-lane sizing correction (2026-10-07)

The v8 NAS private 12GiB/5M sizing run stopped at 24,361 mixed rounds because two broker request
identities were rejected by the per-owner 4MiB request-lane budget. Memory headroom and global
event capacity were not the limiting boundary. The old estimate charged four bytes for every
character of the string form of the request tuple and canonical signature; Python's tuple
representation expands quotes and escapes, so that estimate grew much faster than the actual
retained strings.

v9 stores the complete canonical UTF-8 signature bytes in the internal ordinal-map key and charges
their exact byte length, with the existing 512-byte fixed allowance and conservative actor charge.
UTF-8 encoding is reversible, so per-actor, per-signature ordinals and exact request matching remain
unchanged. The trace payload, signature format, sequence pairing, replay reader, 4MiB budget, and
32,768-key limit are unchanged. Tests cover 4,000 distinct requests under the old budget, Unicode and
escaped signatures, repeated signatures, byte/entry-limit rejection, and reset on a new trace token.
The recursive retained-size estimate remained below charged bytes for those fixtures.

The v9 NAS run confirmed 3,968 unique request keys charged at 2,911,020 bytes, below the unchanged
4MiB limit. It then reached the 12GiB copy-reservation budget at 29,731 synthetic trade messages,
whose 100ms timestamp cadence represents about 49.6 minutes. The run recorded 1,905,826 accepted
events and 28 capture_memory_full input rejections, with zero known drops; RSS peak was
3,735,203,840 bytes, minimum host MemAvailable 14,344,683,520 bytes, and cgroup headroom
13,431,042,048 bytes. This expected classified saturation proves the REST-key overcharge is fixed,
but the 12GiB budget does not cover even this synthetic 65-minute input envelope.

The inactive v10 candidate increases only the private probe budget to 16GiB and the disposable
cgroup ceiling to 20GiB. It must retain a 4GiB host/cgroup reserve while completing 39,000 messages
at 100ms cadence, reporting RSS and minimum headroom. Operational recorder defaults remain 4GiB/1M.
The exact-source PC gate passed 100 unit regressions/API and a small 16GiB smoke. The v10 NAS
65-minute synthetic-envelope gate did not start: its production preflight requires charge budget plus
1GiB, or 17GiB available, and the preceding host MemAvailable sample was 18,073,481,216 bytes
(180,129,792 bytes short). No input was recorded, so this is not evidence of 16GiB saturation.
Inactive v11 `2026.10.08-causal-deferred-capture-v11-dffa8b971619a1ea` is staged from exact v10 source
(928 files, commit `ae1e7cbe9c71ec44ca83cdeddbcae4bf529137d8`, active/runtime unchanged) and changes
only the private probe budget to 15GiB. The same 39,000-message envelope, 5M event cap, disposable
20GiB cgroup and in-run 4GiB host/cgroup guard remained. It saturated copy reservation after 37,201
messages (about 62 minutes at synthetic 100ms cadence): 2,384,653 accepted events, 28
`capture_memory_full` rejections, zero known drops. Charged bytes were 16,075,246,320 and process RSS
peak was 4,675,239,936 bytes; host MemAvailable stayed at or above 13,404,901,376 and cgroup
headroom at or above 16,789,262,336 bytes. Thus the measured physical reserve held, but 15GiB charge
was short of the 65-minute envelope and 5M event count was not the first limit. The maximum payload
copy took 2,481ms and one message took 1,833ms; the event responsible is not attributed, so capture
overhead remains unapproved.

Next, retry the existing inactive v10 16GiB private gate only when current host MemAvailable reaches
18,253,611,008 bytes (17GiB), its configured budget plus startup reserve. The v11 large-run starting
snapshot was 18,036,588,544 bytes, 217,022,464 bytes short. Do not bypass or weaken production
preflight. Operational defaults remain 4GiB/1M. Even a complete v10 synthetic envelope does not prove
the real market maximum, paced near-capacity persistence, the independent global 4GiB file quota, or
the source of the copy-time outlier.

2026-10-08 controlled60-minute copy/GC evidence: private15GiB/5M probe completed36,000
messages /2,307,608 events with no input rejection/drop. In the instrumented repeat, the largest
REST immutable copy took2448.337ms, of which2430.272ms was automatic GC in the same thread.
A19,268-byte charged realtime input took1989.087ms, with1988.862ms in GC. The save_query
copy outlier took1636.053ms, with1617.831ms in GC. Gen2 totaled24801.542ms /38 collections.
This narrows the measured long-copy cause to GC pauses; it does not establish production workload
coverage or acceptable overhead. Large deferred persistence is still pending. Process-wide GC
disable/freeze/threshold tuning is not a chosen fix. Evaluate capture-local packed retention against
background RAM encoding, preserving immutable inputs, ownership/refcount charges, sequence IDs,
descendant provenance, cancellation/shutdown, and unchanged durable schema. Avoid treating
instrumented timing as the final uninstrumented performance baseline.

### Deferred capture GC decision — 2026-10-08

**Status: implementation design selected; not implemented or deployment-approved.** Base the first
candidate on the verified `2026.10.08-causal-capture-60m-v1-a8376b5b6bdf96e7` source, not the dirty
main checkout or the instrumented copy-profile candidate. Keep schema3, all causal boundaries,
native workload ownership, recorder opt-ins and persisted replay format unchanged.

#### Evidence and alternatives

The copy-profile NAS run attributes 2430.272 of2448.337ms in its worst REST copy to same-thread GC.
A small realtime input similarly spends1988.862 of1989.087ms in GC. It demonstrates the observed
pause mechanism, not which retained object family alone accounts for all GC cost. Current deferred
`_hold_deferred` prevents the existing writer from draining `_QUEUE` until persist_at. That queue
retains event dictionaries, frozen payload tuple graphs and `DeliveryStage`/`Context` graphs for the
entire session. The existing slots sharing reduces RSS but leaves GC-tracked instances per stage.

| Choice | Decision | Reason |
| --- | --- | --- |
| Larger budget or fewer captured inputs | Reject as GC fix | Does not remove the observed pauses; reducing coverage violates the capture objective. |
| Process-wide disable/freeze/threshold tuning | Reject | Affects trading, subscriptions, caches and cyclic application objects outside capture; postpones or hides reclamation. |
| Serialize every event/payload in its producer | Do not select | Bounds long-lived objects but adds full encoding directly to collector/DB caller latency; the old delivery-only PC experiment does not validate mixed NAS inputs. |
| More slots/shared references alone | Do not select as complete fix | Still retains per-event Python graphs; cannot establish a bound on old-generation scan work. |
| New recorder process/shared-memory transport | Defer | Adds IPC, startup/shutdown, failure recovery and input ownership boundaries beyond this correction. |
| Existing recorder worker packs bounded batches into RAM byte segments | Select for first candidate | Removes the hour-long Python graph while preserving producer copy policy, deferred filesystem writes and existing worker ownership. Must pass CPU/GIL/backlog gates. |

Here packing means encoding to bytes, **not compression**. Do not add zlib/zstd, content hashing,
deduplication, filesystem writes or a second worker during capture. Hash/deduplicate at the existing
durable write boundary. CPython GC settings remain untouched, including in final performance gates.

#### Data path and ownership

1. Producers keep existing bounded type/secret/cycle validation, immutable payload copy and the
   two-copy admission semaphore. Scalar/delivery admissions keep the existing seq/epoch lock.
   Mutable native payloads never escape to a worker. Native work never waits for recorder space.
2. `_QUEUE` becomes the bounded *raw staging queue* for deferred sessions. The single existing
   recorder worker drains it while running AND awaiting_persistence, regardless of persist_at.
   Non-deferred recording keeps the current writer path. Sequence numbers are assigned only once,
   at admission; compaction and persistence cannot renumber or reorder events.
3. The worker expands a bounded raw batch outside `_LOCK`, using the same field enumeration and
   frozen-payload JSON rules as the current writer. Store metadata JSON and payload JSON separately
   in a private length-prefixed frame, so persistence need not hydrate/re-encode a whole payload.
   The frame contains first/last sequence information via its metadata and validated lengths;
   segment headers carry format/version, event count and allocation/accounting information.
   This is internal RAM framing, not a new replay/wire format.
4. Retain sealed immutable `bytes` segments, targeting1MiB. A single larger permitted frame gets
   its own bounded segment. No per-event dictionary, tuple of objects, `DeliveryStage`, or decoded
   payload survives alongside its packed frame. Segment/index objects scale with blocks, not events.
   A partially filled mutable segment is worker-owned and counted until sealed.
5. Raw source records remain owned and charged until the segment transfer succeeds under `_LOCK`.
   Then release their shared `Context` refs with the existing release rules and publish the packed
   segment atomically. A native receipt still in use remains valid; future dequeue/consume stages
   may re-retain its context normally. Do not mutate subscriber/collector receipts or context values.
6. At persist_at, after admission closes and all admitted copies and raw packing drain, feed frames
   to the current payload-hash/bundle/chunk writer. Decode only one bounded metadata frame at a time;
   use the pre-encoded payload bytes for the existing digest and bundle format. Retain segment bytes
   and their charge until their last referenced frame is durably committed. Partial chunks retain
   cursor/segment ownership and the uncommitted suffix, without reintroducing a full raw graph.
7. `accepted`, `written`, drops/rejections, producer/call/event IDs, source/descendant links and
   original timestamps keep their meanings. Packing is not durable writing: written stays0 before
   persist_at. `complete` still requires all events durable and the final manifest fsynced.

Use `diagnostic_trace.py` for lifecycle/locks/admission and the existing durable writer. A small
`diagnostic_trace_ram.py` is permitted solely for bounded frame/segment packing, cursor iteration
and allocation accounting. Do not introduce a generic queue/service/plugin abstraction or change
`freeze_payload`, broker, collector, DB transaction or replay workload contracts.

#### Limits and state contract

- Initial **private candidate** staging limits:64MiB raw retained charge and32,768 raw+packing events;
  include in-flight copy reservations when enforcing bytes. This buffer drains continuously to RAM,
  unlike the old48MiB disk-backed backlog. These are test values, not approved operational sizing.
- Total retained-event admission counts raw + packing + packed + durable-writer pending events.
  The session5M candidate cap must not accidentally become a32,768 total-capture cap. Shared entries
  and a segment's partial cursor must not change the logical count of accepted events.
- Keep raw accounting as today. Packed accounting counts actual allocated `bytes`/buffer capacity,
  segment/index overhead and fixed allocator slack; it must never use input JSON length as a bound
  for a still-live Python graph. This is representation-specific accounting, not an RSS-ratio discount.
  During encoding count both representations or reserve a proven worst-case transient allowance.
  Publishing must atomically replace old charge with new charge; no uncharged allocation window.
- Start with a128MiB worker transient reserve for the private candidate. Prove it covers worst allowed
  payload, Unicode encoder string/bytes, metadata, segment sealing copies and writer chunk/bundle buffers.
  If it does not, adjust the bound before admission rather than bypassing checks. Retain per-input
  copy limits, scalar reserve, independent persistent-file quota and existing host/cgroup preflight.
- The15GiB/5M setting remains private until exact-source NAS gates select operational limits. Packing
  changes the relationship between charge and RSS; do not carry the old15GiB requirement into production
  automatically or infer a lower approved budget from compact output size alone.
- Wake the worker on raw high-water (initial1MiB or256 events) and a short bounded fallback interval.
  Encoding runs outside the admission lock. Process at most a small bounded batch (initial128 events,
  4MiB raw charge or10ms elapsed, allowing one legal oversized record), then yield. These are tuning
  candidates; collect single-record GIL/loop-lag outliers as well as average throughput. A thread does
  not guarantee that encoding cannot stall the collector.
- On staging/total-budget exhaustion, keep native work running, record the explicit rejection and
  stop further capture admission as incomplete; preserve all accepted raw/packed data until persist_at.
  Do not flush early or drop/replace recorded inputs to make room. Failures remain observable even
  if the scalar rejection event itself cannot be enqueued. Never silently revert to unbounded raw RAM.
- On stop/expiry, reject late admissions consistently and wait for pre-admitted copies. Seal raw
  staging into packed segments before persistence. Status must distinguish packing backlog from
  awaiting the persistence time. On packing error, preserve charged raw suffix and packed prefix,
  mark failed/incomplete and never publish complete. On shutdown-before-persistence, follow the
  existing explicit interrupted/loss semantics and release both representations; no implicit early disk flush.
- Keep O(1) status counters for staging_events/bytes, packing_events/bytes/reservation, packed_events/
  bytes/segments, writer_pending_events, high-water, pack time and rejection reason. No per-event timing
  log or full-queue walk on production status. Diagnostic probes must use counters or a bounded small
  fixture decoder; do not hydrate millions of packed events merely to inspect them.

#### Required tests and acceptance order

1. Frame/segment boundaries: zero/Unicode/maximum payload, oversized single frame, malformed lengths,
   exact legacy metadata/payload encoding, cross-segment ordering and bounded decode. Hashes, causal IDs,
   delivery absence-vs-null semantics and approved catalog copy profiles must remain identical.
2. State/accounting: simultaneous producers and copy reservations; pack/admit/stop races; shared context
   spanning batches; rejected first/later delivery stages; native receipt reused after raw release;
   stale epoch; pack failure; partial disk commit; final-manifest failure; discard/shutdown. At every
   boundary assert accounting nonnegative and every accepted event has exactly one owner/position.
3. Small native mixed roundtrip including all T1–T4 types, with same consumer results/store calls as OFF;
   verify every payload, pair and delivery closure after paced durable persistence.
4. Same36,000-message envelope, alternating uninstrumented OFF/current/packed candidates, plus a
   burst variation with identical inputs. Measure producer p50/p95/max, event-loop heartbeat delay,
   process CPU, GC durations, raw-backlog high-water/age, rejection/drop, RSS/host/cgroup headroom,
   retained bytes/events and wire equality. Instrumented cause probes cannot approve final timing.
   GC pauses must not simply move to encoding/GIL or queue latency; existing overhead goals still apply.
5. Full-volume paced persistence to an isolated NAS scratch directory, retaining1MiB/s/cooldown and
   file quota checks. Stream all chunk/checksum/sequence/reference validation with bounded memory;
   do not use the current small-smoke `hydrated` list for millions of events. Measure peak RSS during
   encoding AND saving, actual final bytes, fsync latency, disk free space and completion time.
   Test interruption/failure separately; production capture files and controls remain untouched.
6. Only then select total/staging limits, stage/activate the accepted operational candidate and update
   BOTH start and verification reservations to09:00–10:00, exact build/release/schema/options/persist_at.
   Until then active v6 and both existing reservations remain unchanged. This design is not proof of
   real opening-market maximum throughput or complete replay coverage.

Implementation handoff: Sol High. The ownership/accounting transfer, producer/worker races and
partially durable segment cursor are concrete but remain complex concurrency work. Astra design
work ends here; a simpler implementation must preserve these invariants rather than broaden scope.

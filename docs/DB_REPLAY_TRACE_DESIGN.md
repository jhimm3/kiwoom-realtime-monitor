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

- producer당 queue 최대 32,768 이벤트와 보수적으로 계산한 RAM charge 64MiB 중 먼저 도달하는 제한을 적용한다. 문자열 상한과 Python 객체 오버헤드를 포함한 실제 RSS 증가를 검증한다.
- background worker는 5초마다 최대 1MiB의 UTF-8 JSONL 청크 하나를 배출한다. 4,096 이벤트 batch의 남은 suffix는 producer 순서를 지켜 다음 배출까지 worker 메모리에 두며, 공개 `queued`에는 이 pending 이벤트도 포함한다. 이벤트 한 건이 1MiB보다 크면 읽을 수 없는 청크를 만들지 않고 trace를 `failed`로 끝낸다.
- 파일은 plain UTF-8 JSONL chunk로 시작한다. 압축 비용은 별도 측정 전 도입하지 않는다. writer는 chunk 하나씩 `.partial`에 쓰고 checksum/first_seq/last_seq/count를 만든다.
- **묶음당** flush/fsync 후 원자적 rename, manifest 원자 갱신으로 확정한다. 플랫폼의 directory sync가 확인되지 않으면 power-loss durability를 보장하지 않는다. fsync는 DB 업무 thread에서 실행하지 않는다.
- capture당 1GiB, 보존 총량 4GiB를 초기 hard cap으로 둔다. preflight에서 여유 공간과 예상 발생률을 확인한다. 한도/디스크 오류는 trace를 incomplete로 종료하고 업무는 계속한다. 기존 파일을 몰래 지워 active capture 공간을 만들지 않는다.
- manifest에는 accepted/known_dropped/written 이벤트, 마지막 발급·확정 seq, backlog high-water, 파일 bytes/flush/fsync 시간, clock jump, 종료 이유를 담는다. 파일은 writer별로 과도하게 나누지 않는다.
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

### Replay report call correlation (2026-10-03 local candidate)

Each replay execution now has its own `replay_call_id`, passed as the observed DB call's `request_id`. The report joins that value to the source trace/report `source_call_id`, the generated `db_call_id`, and the call's attempted rows, domain counts, elapsed/execute/commit times, transaction/commit/rollback counts, SQL count, and outcome. Query-minute shape fields that were not recorded by the writer metrics are explicitly counted as incomplete instead of being implied as zero. Aggregate direct-call totals are built only from these linked calls.

The existing `phase.database_delta` remains an interval-wide before/after delta, with an adjacent scope record: `pg_stat_wal` is cluster-wide, while `pg_stat_database` is for the measured database and includes sampler queries and unrelated activity there. PostgreSQL does not expose WAL bytes attributable to one transaction through these counters, so per-call WAL remains unavailable; the report sets it to null and explains why. Per-call commit counts/timings are directly correlated, and cluster/database interval counters remain separately visible.

This report change is local and is not deployed to the active NAS source release. It does not block the higher-priority October 6 market-hours capture. That capture should use the active `db-minute-replay-v1` source whose minute writer records input rows, changed rows, duplicate input keys, and revision inserts; replay/report improvements can be deployed after the trace is safely captured and verified. Local regression tests passed 78 tests; 4 dedicated PostgreSQL tests were skipped because this PC run had no diagnostic DB URL. NAS acceptance of the report change remains pending.

필수 테스트: multi-thread seq/queue, begin/end 밖 경계, overflow 표시, chunk checksum·partial·manifest crash recovery, disk full/permission 오류, master OFF/TTL/instance 변경, clock jump, shutdown 시 flush timeout, raw/trace 독립성, observer 실패의 DB 결과 불변, native transaction/peer 독립 commit, API 인증·경로 탈출·용량 제한.

오버헤드 목표는 OFF 대비 호출 hot path 추가 p95 0.1ms 이하 및 동일 DB workload 처리량 저하 2% 이하로 두되 **목표이며 미측정**이다. 메모리 증가/수집 bytes/sec/flush·fsync 시간을 함께 보고한다. 단순 SELECT와 대표 writer 모두 검증한다. 목표 초과 시 scope를 축소하거나 오늘 fallback으로 전환하며 green이라고 보고하지 않는다. NAS 같은 볼륨의 진단 파일 I/O는 0이 아니므로 저장장치 상관 분석에 진단기 자체 writes를 표시한다.

이 설계만으로 13시·10월 6일 capture 예약이나 자료 확보가 완료된 것은 아니다. 현재 활성 NAS release, 배포 여부, trace 실제 시작/종료, coverage와 유실 여부는 각각 별도 증거로 남긴다.

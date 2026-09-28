# 진단도구 하위 스위치 설계 검토

2026-09-26 · O12 · 제품 2.1.0

## 결론과 적용 상태

최상단 `진단도구 ON/OFF` 아래에 측정, 작업, 수신, 저장 스위치를 둔다.
master ON은 진단 조작을 허용할 뿐 운영에서 꺼 둔 기능을 켜지 않는다.
master OFF·TTL 만료·재시작은 진단 override를 해제한다. 이는 **운영 기능을
정지한다는 뜻이 아니다**. 기존 사용자 STOP, 자격증명 차단, 주문 안전 gate는
독립적으로 유지한다.

아래 내용은 **후속 구현 기준으로 정한 설계**다. 현재 구현은 선택 작업 8개,
capture, writer 계측 12개에 한정된다. 모든 writer를 멈출 수 있거나 모든 경로의
보존 검증이 끝났다는 뜻이 아니다. 이번 검토에서는 master 보존 결함만 최소 수정했다.
보호 경로의 transaction·실시간 저장 흐름은 변경하지 않았다.

## 코드에서 확인한 사실

| 항목 | 확인 근거 | 판단과 조치 |
| --- | --- | --- |
| 하위 변경 시 부모 삭제 | `scripts/nas_workload_diagnostic.py`의 `_set`, `_set_capture`가 새 JSON에 `diagnostic_tool`을 누락 | 임시 파일에서 `tool on → pause`와 `tool on → capture on` 모두 부모 OFF 재현. 부모 필드 보존으로 수정하고 실제 변경 함수 회귀 추가 |
| 측정 중 master 만료를 확인하지 않음 | 종전 `_measure` 반복은 DB 조회와 sleep만 수행했다 | 로컬 후속 구현에서 전체 TTL 사전 검사와 표본별 세션 확인, 부분 `aborted` 보고서를 추가. 긴 단일 DB query는 2초 statement timeout으로 제한 |
| OFF 확인과 자식 확인의 세대가 다를 수 있음 | 종전 `diagnostic_workloads.py` reader가 제어 파일을 여러 번 읽었다 | 로컬 후속 구현에서 하나의 제어 파일 세대로 부모·자식을 평가하고 세션 ID와 revision 추가 |
| 자식 표시 만료가 부모보다 길 수 있음 | 종전 reader가 자식 만료를 직접 반환했다 | 로컬 후속 구현에서 `min(parent, child)` 적용. 캡처 캐시는 최대 0.5초 뒤 재확인하므로 즉각 중지를 보장하지 않음 |
| 고정 2초 뒤 OFF 구간 측정 | `_run_measurement`의 `sleep(2)` | 실제 진행 중 작업의 종료 확인이 아님. observed 상태 ACK가 필요 |
| 실시간 재시도는 메모리 보존 | `realtime_collector.py::_flush_snapshot_cycle`, `_pending_*`, `drain_dirty`, `drain_closed` | 함수 앞에서 return하면 무제한 누적 또는 재시작 손실 가능. durable 인계 없이 sink 중지 금지 |
| 일부 index outbox만 파일 보존 | `autonomous_top20.py::_queue_index`, `persistent_outbox.py::JsonRecordOutbox` | 모든 순위/0B의 원장이 아님. 현재 파일 교체는 fsync도 없어 전원 중단 내구성까지 검증됐다고 할 수 없음 |
| 실계좌 이벤트 큐는 1,000건 RAM | `real_runtime.py::RealAccountContext`, `_handle_account_event`, `_write_account_events` | 큐 가득 참을 오류로 기록하나 durable 보존 아님. 저장만 멈추고 계속 수신하는 구현 불가 |
| 주문은 전송 전에 UNKNOWN 원장 저장 | `application/order_lifecycle.py::OrderLifecycle.submit` | 진단 중지 때문에 중간 취소하거나 UNKNOWN 주문을 다시 전송하면 안 됨 |
| 주문 상태와 이벤트는 한 transaction | `central_server/database.py::append_execution_event` | 두 SQL을 독립 스위치로 분리하지 않음 |
| 중앙 실시간 구독은 공용 연결·그룹 사용 | `realtime_collector.py::_send_subscription`: 그룹 3000에 00/04, 0J/0U, 0s 등이 함께 있음 | 0B만 중지할 때 socket 전체 종료 금지. 유형별 구독 ACK와 공백을 구분 |
| BODY/RULE/AI는 claim 후 완료 처리 | `news_jobs.py::run_once`, `_loop` | stage별 새 claim을 막고 기존 소유 작업은 완료. finish만 끄는 것은 소유권/재처리 회귀 위험 |

현재 주문 실행에서 확인한 `execution_runtime.py`·`OrderLifecycle`·REST transport는
**모의주문 경로**다. 실계좌 감시 경로가 있다고 실주문 전송 구현까지 있다고
표시하지 않는다. 실주문 전송을 추가할 때 같은 계약과 별도 검증을 적용한다.

## 1. 최상단과 하위 상태 계약

```text
진단도구 master (기본 OFF, TTL 기본 10분 / 최대 60분)
├─ 측정: 저장 단계 / DB wait·WAL / host I/O / CPU·메모리
├─ 작업: 뉴스 소스·BODY·RULE·AI / 분봉 보완 / 장후 보완 / 후보·외부시장
├─ 수신·배포: 순위 / 0B / 프로그램·시장상태 / 계좌 이벤트
└─ 저장: 원천별 atomic write group / cache / 파생·Journal / PC 로컬 writer
```

모든 항목에 `configured`, `requested_pause`, `effective`, `observed_state`,
`paused_by_diagnostic`, `expires_at`, `supported`, `reason`, `owner`, `session_id`,
`control_revision`, `observed_revision`, `scope`, `inflight`, `pending`,
`recovery_required`를 둔다. 구현되지 않은 항목은 `unsupported`다.
`configured`는 운영 설정이며, 단순히 요청됐다는 이유로 `observed_state=PAUSED`를
반환하지 않는다. scope는 NAS/PC/독립 적재기, 계좌 binding, 환경, venue를 구분한다.

상태 전이는 `RUNNING → DRAINING → PAUSED → RESUMING → RUNNING`이다.
만료 또는 OFF 중이라도 복구가 끝나지 않으면 `RECOVERY_REQUIRED`와 이유를 남긴다.
OFF인 master 아래에서 계속되는 필수 기록·복구는 진단 측정 작업이 아니라 운영의
보존 책임이다. 정상 복귀 여부는 별도 상태로 드러낸다.

- OFF→ON은 새 세션을 만든다. 이미 ON에서 `tool on`을 반복하면 기존 상태를
  지우거나 TTL을 무한 연장하지 않고 현재 상태를 반환한다. 새 세션은 명시 OFF→ON.
- 자식 mutation은 파일 lock 안에서 부모/instance/session/revision을 재검증하고
  원자 교체한다. reader는 한 payload만 해석한다. 부분/잘못된 파일은 진단 OFF로
  취급하되, 기존 필수 복구 원장까지 삭제하지 않는다.
- TTL은 자식·측정이 부모를 넘지 않는다. 런타임에서는 남은 시간을 monotonic
  deadline으로 고정한다. 같은 revision을 다시 읽는 것으로 수명이 늘어나면 안 된다.
- 정리 작업은 자신이 소유한 `(instance, session, owner, revision)`만 해제한다.
  부모가 만료됐거나 새 세션이면 성공적인 no-op이다. 새 사용자의 pause/capture를
  지우거나, 예외로 부분 측정 보고서를 잃으면 안 된다.
- master OFF는 활성 측정도 중단하고 부분 결과를 `aborted`로 저장한다. 새 표본
  수집·주기 로그는 멈춘다. status/기존 보고서 읽기는 진단을 켜지 않아도 가능하게
  한다. 이는 작업 스위치 우회가 아니라 읽기 전용 확인이다.
- 구현은 기존 `diagnostic_workloads`, registry, CLI 경계를 확장한다. 단순 전달용
  Manager 계층을 새로 만들지 않는다. 인증된 제어와 파일 권한을 유지하고 비밀·기사
  원문·계좌번호를 측정 로그에 넣지 않는다.

## 2. 무엇을 멈추는지 구분

**작업 중지**는 새 작업의 admission을 닫고 진행 중인 처리와 commit은 마무리한다.
**수신 중지**는 외부 자료를 받지 않으므로 회복 불가능한 공백이 생길 수 있다.
**저장 중지**는 받은 자료를 durable 인계한 뒤 특정 DB writer의 dequeue만 멈춘다.
세 동작을 같은 bool이나 성공 반환으로 처리하지 않는다.

| 제어 대상 | 멈출 위치 | 중단·재개 계약 |
| --- | --- | --- |
| 기존 선택 작업 8개 | 각 소유 loop의 다음 admission | in-flight 마무리 후 ACK. 기존 cursor/진행 원장으로 재개 |
| 뉴스 BODY/RULE/AI | stage 조건을 넣은 claim 경계 | 모든 worker 및 HTTP claim 진입점이 같은 gate 확인. 이미 선점한 작업은 body/result/finish까지 완료. 외부 PC 실행기는 별도 등록 없으면 미제어로 표시 |
| `news.job_finish` 등 저장 하위항목 | 해당 stage admission을 닫는 종속 제어 | 진행 중 finish를 누락하지 않는다. 단독 finish OFF로 측정했다고 표시하지 않고 실제 함께 멈춘 범위를 보고 |
| REST 분봉·일봉/기초정보/NXT/수급 | 신규 broker 작업과 응답 persistence 경계 각각 | 이미 도착한 응답은 commit 또는 durable 인계. DB 성공 전 coverage/완료 표시 금지. 캐시와 in-flight dedup 유지 |
| REST persistent cache | 캐시 읽기·쓰기 policy | 캐시 OFF로 운영 원장까지 끄지 않음. 캐시 hit, miss, 외부 TR 증가를 함께 측정. rate limit 그대로 유지 |
| TOP20 순위 수집 | ka00198 새 회차 admission | 기존 메모리 배포·DB 저장·0B 등록 흐름의 진행분은 끝냄. 새 순위 없음/기존 값 stale을 PC에 표시. 0B 자체를 암묵 중지하지 않음 |
| TOP20 저장 | membership/entrant/index별 명시 저장 그룹 | 현재 index outbox만으로 전체 보존을 주장하지 않음. 메모리 배포는 계속하되 `persistence_state=pending`과 backlog 표시 |
| 0B 수신 | 0B 구독 그룹 변경과 REG/REMOVE 확인 | 00/04·0s 등 공용 연결 유지. 중단 시작·재개 승인 시각/코드/venue 기록. 0B gap을 0거래로 바꾸지 않음 |
| 0w/0g/지수/장운영 수신 | 해당 유형 그룹 | 유형별 제어. 장운영 증거 중지 시 거래일 미확인을 휴장으로 확정하지 않음 |
| 0B latest/minute/finalize/second 저장 | 기존 flush의 atomic write group | durable 인계 후 해당 writer만 지연. 분봉 원장 이전 finalize 금지. finalize 대기가 전체 flush lock을 점유하지 않게 함 |
| 실계좌 REST 감시 | 다음 `read_account` 주기 | 진행된 복구 snapshot 저장. 이벤트 수신·기록은 별도 유지. 재개 후 binding 확인하고 새 대사 |
| 실계좌 00/04 수신 | 계좌 이벤트 구독 | 보호 제어. 공백 기록, 주문 admission 차단, 재개 후 미체결/체결/잔고 대사. REST 대사로 이벤트 전수 복원을 보장하지 않음 |
| 계좌 이벤트 DB 저장 | durable inbox→`save_real_account_event` | RAM 1,000건 큐를 저장소로 간주하지 않음. inbox 확인 전 허용하지 않음. source/binding/sequence 보존 |
| 신규 주문 전송 | 전송권 획득 및 `SUBMISSION_STARTED` 이전 | 이미 전송권을 얻은 요청은 응답/UNKNOWN 기록까지 마무리. 신규 요청은 diagnostic-paused로 거절, 재개 시 몰아서 전송하지 않음 |
| 취소 전송 | 별도 cancel admission | 신규 매수/매도 전송 스위치와 분리. 진행 중 취소는 `CANCEL_PENDING` 대사까지 지속 |
| 주문·체결 원장 | event+intent atomic group | 직접 쓰기 무시 금지. 현재 주문 원장이 결정 근거이므로 원장 OFF는 전송 admission도 차단. 이미 수신한 외부 이벤트는 inbox에 보존 후 재개 시 반영 |
| Journal·파생 projection | 원장 소비 cursor 앞 | source 원장은 계속 기록. 저장 성공 후 cursor 전진. 재개 시 중복 없이 복원. 실제 원장 재생 가능 여부를 항목별 검증 |
| PC SQLite/PC 수집기 | 해당 프로세스 소유 gate | NAS 파일을 원격 SQLite처럼 열지 않음. 각 PC agent가 같은 진단 session을 수락·만료·ACK. 연결 없는 PC는 unknown/미제어 |

보호 경로는 `--force`와 정확한 scope를 요구한다. force는 위험한 구간의 명시 선택이며
유실·부분 commit·중복 주문을 허용하는 옵션이 아니다. dependency가 자동으로
같이 정지된다면 명령 결과와 보고서에 전부 표시한다. 예를 들어 원장과 주문 전송을
함께 멈춘 실험으로 원장 단독 부하를 추정하지 않는다.

## 3. 저장 중지의 자료 보존 설계

선택: **재생 가능한 원장이 있는 경로는 그 원장을 이용하고, 없는 경로만 좁은
durable 인계 저장소를 추가한다.** 모든 DB 호출을 SQL 문자열로 가로채는 범용
우회 저장기는 만들지 않는다. 이 추가는 구현·마이그레이션·강제 종료 테스트가 필요한
새 저장 책임이며, 현재 적용돼 있지 않다.

0B 및 계좌 이벤트의 PostgreSQL sink를 독립 중지하려면 NAS 로컬 bind volume 안의
전용 inbox/outbox가 필요하다. 선택한 구현 방식은 단일 소유 writer가 관리하는
로컬 SQLite이며 PC가 SMB로 직접 여는 경로를 만들지 않는다. 정상 모드에서는
기존 경로를 유지하고, pause 전환 barrier에서 대상 writer의 기존 pending과 이후
입력을 인계한다. durable 인계 실패 시 pause ACK를 내지 않고 기존 저장을 유지한다.

인계 레코드에는 `operation_id`, source, scope/binding revision, 관측/수신 시각,
원본 payload hash, writer ID와 재생 버전, 순서·의존 operation ID를 저장한다.
분봉 delta는 operation ID와 값을 그대로 유지하고, 초봉/최신값의 합치기는 현재
저장 계약이 허용하는 경우만 적용한다. observation 이력을 latest로 축약하지 않는다.
계좌 payload는 접근 제한·암호화 정책을 먼저 적용하고 비밀키/토큰은 인계하지 않는다.

local durable commit 완료 → DB writer 적용 → DB commit 확인 → 인계 ACK 순서다.
DB commit 뒤 ACK 전 종료는 재생 가능한 상태로 남기고 DB 멱등키로 이중 합산을 막는다.
ACK된 레코드만 정리하며, 파일 손상은 빈 원장으로 바꾸지 않고 오류와 복구 필요로
드러낸다. `JsonRecordOutbox`를 그대로 계좌 원장으로 재사용하지 않는다.

용량/최대 지연/디스크 여유 기준을 상태에 표시한다. 한도에 접근하면 실험을 중단하고
운영 sink로 복귀·drain한다. DB와 인계 저장소 모두 실패하면 silent drop 금지:
주문은 차단하고 수신 공백·보존 실패를 보고한다. 어떠한 저장소로도 받지 못한 외부
이벤트까지 보존됐다고 주장하지 않는다. 기존 인계 backlog를 끝까지 복구한 뒤에만
재개 완료를 표시한다.

인계 저장도 I/O를 발생시킨다. 특히 PostgreSQL과 같은 NAS volume이면 sink OFF가
전체 디스크 쓰기 OFF는 아니다. 인계 bytes/commit을 측정에 따로 기록하고, 비교는
PostgreSQL writer의 부하 기여도로 한정한다. durable 인계가 아직 없는 writer는
목록에 두되 `unsupported_reason=durable_handoff_not_ready`로 거절한다.

## 4. 동시성·주문·복구 경계

- gate lock은 admission/revision/inflight 등록에만 사용한다. DB 호출, socket,
  drain 대기, TTL sleep 중 공통 lock을 잡지 않는다. 검증 후 실행 사이의 race는
  소유자의 barrier와 admission ticket으로 닫는다.
- 진행 중 transaction을 진단 명령으로 취소하지 않는다. deadline까지 drain이
  안 끝나면 `DRAINING_TIMEOUT`이고 PAUSED가 아니다. thread 작업을 cancel해도
  실제 DB commit이 중단됐다고 가정하지 않는다.
- 봉+metadata+observation+operation ID, execution event+intent처럼 함께
  성공해야 하는 단위 내부에는 스위치를 넣지 않는다. 해당 그룹을 하나로 제어하고
  내부 단계는 계측으로 분리한다. 진단 추가와 transaction 병합 최적화를 한 번에 하지 않는다.
- 주문 pause와 admission은 기존 계좌/자동운용 gate를 함께 재검증한다. 이미
  `SUBMISSION_STARTED`가 저장된 주문은 진단 재개로 재송신하지 않는다.
  응답 유실은 UNKNOWN 유지와 broker 대사로 처리한다.
- 부모 OFF/만료는 진단 veto만 제거한다. 운영 STOPPED, credential-paused,
  binding 변경, 원장 backlog, 미완료 대사 등은 자동 해제하지 않는다.
  새 주문을 허용할 때 현재 계좌 신선도와 운영 승인 revision을 다시 확인한다.
- 실시간 source 중지 뒤 보완 TR은 봉을 보완할 수 있어도 원래 모든 tick/체결 사건을
  복원하지는 못한다. 재개 첫 부분 분봉에 COMPLETE를 붙이지 않고 기존
  `_continuous_from`/completeness 규칙과 명시 gap을 유지한다.
- 재시작은 진단 OFF로 시작하지만 durable backlog와 공백 원장은 유지한다.
  오래된 instance/session cleanup이 새 프로세스 상태를 수정하지 못하게 한다.

## 5. 비교 측정의 완료 기준

1. 전체 A/B/A 시간, drain/recovery 상한, 결과 저장 여유가 master TTL 안에 있는지
   먼저 확인한다. 부족하면 시작하지 않고 필요한 TTL을 제시한다. 자동 연장 금지.
2. 기준 구간 → 대상 `PAUSED` ACK → 안정 구간 → `RESUMING`/backlog drain →
   재개 `RUNNING` ACK → 재개 구간 순서. drain과 replay는 별도 phase로 저장한다.
3. 각 구간의 실제 revision·dependency·inflight·backlog·처리량, 전체 저장의
   count/median/p90/p95/max, 실패·재시도·시도 행수/실제 반영 행수, WAL·wait·I/O·
   CPU/메모리를 기록한다. 값이 없으면 unknown이며 0으로 채우지 않는다.
4. 다른 조작, 부모 만료, PC failover, 외부 적재기/백업 변화, workload 없음,
   표본 탈락/부족, DB 통계 reset은 aborted/confounded/inconclusive로 구분한다.
   과부하 중 조회 timeout도 실패 표본으로 남기고 무한정 측정하지 않는다.
5. 측정 query는 timeout을 두고 새 연결/표본 전 부모를 재확인한다. 종료 시 자신의
   query·sampler만 정리한다. master OFF 뒤 상세 수집이 계속되지 않아야 한다.
6. master OFF의 단일 종료 이력과 부분 보고서 저장은 허용한다. 주기적인 diagnostic
   로그와 sampler는 멈춘다. 일반 운영 오류 로그는 diagnostic master와 무관하다.
7. writer별 WAL은 전역 delta로 추정하지 않는다. 같은 입력의 독립 DB 실험과
   운영 A/B/A를 구분하며, 측정 오버헤드와 인계 쓰기 비용도 보고한다.

## 6. 구현 순서와 통과해야 할 검사

| 단계 | 변경 | 필수 검사 |
| --- | --- | --- |
| 1 | master 유지·세대·TTL·측정 취소와 observed ACK | 실제 CLI ON→자식→OFF, 동일 ON, 동시 변경, 옛 cleanup, 파일 손상, 시간 변경, 만료 중 partial report |
| 2 | stage/source별 선택 작업 gate 및 registry 일치 | BODY/RULE/AI별 pause/drain/resume, 외부 claim 진입점, 운영 OFF 유지, cursor/재시도 유지 |
| 3 | 보호 수신·주문 admission gate | 유형별 구독 ACK, 공용 계좌 연결 유지, REG 실패, gap, UNKNOWN/중복 주문/취소·자격증명 경합 |
| 4 | durable 인계와 atomic writer별 제어 | 인계 전후·DB commit 전후·ACK 전후 강제 종료, duplicate/out-of-order, 디스크 full, 재시작 replay, 동일 ID 다른 payload 거절 |
| 5 | NAS/PC/독립 적재기 통합 및 배포 | 독립 PostgreSQL 테스트 DB, 전후 source/파생/완료값 대조, 전체 registry/control 매핑 검사, 누적 build 확인, 실제 OFF→ON 관측 |

단계 4의 새 저장소는 DB/schema 계약, 소유자, 보호 정책, 진단 자식 등록을 같은 변경에
갱신한다. 원장 없이 저장을 중지하는 기능을 먼저 배포하지 않는다. 독립 PostgreSQL
검사 및 장중 데이터가 필요한 확인은 운영 DB에 가짜 주문·이벤트를 넣어 대체하지 않는다.
휴장에 가상 입력으로 통과해도 장중 실수신 검증으로 표시하지 않는다.

검토 범위는 진단 제어와 그 보존 경계다. API/FID/Journal/PC writer의 전수 경로 원장,
모든 실제 reader 계약, WAL 원인 제거가 완료됐다는 뜻은 아니다. 미등록 경로는
계속 감사하며, 신규 DB/테이블/경로는 반드시 master 아래 자식 제어·측정·복구
검증을 함께 추가한다.

## 이번 검토 검증과 후속 담당

임시 제어 파일에서 부모 손실을 재현했고 `_set`/`_set_capture`에 부모 보존을 추가했다.
진단 단위 검사 13건과 서버 API 회귀 55건, 합계 68건 통과(exit code 0).
빌드 식별자는 app/Compose/Dockerfile 모두 `2026.09.26-diagnostic-master-switch-v2`로
일치시켰다. 이 검토에서 NAS 재빌드나 새 빌드의 운영 동작을 검증한 것은 아니다.
Windows 테스트는 `fcntl` lock만 대체하므로 Linux 다중 프로세스 lock 검증 증거는 아니다.

후속 로컬 단계에서 세션 ID/revision/monotonic TTL, 단일 파일 읽기, 반복 ON의
멱등성, 이전 세션 cleanup 거절, 측정 도중 종료·부분 보고서를 구현했다.
진단 단위 20건과 서버 API 회귀 56건, 합계 76건 통과했다. build marker는
`2026.09.26-diagnostic-session-control-v1`로 올렸다. 실제 작업의 drain ACK와
보호 경로 switch는 여전히 미구현이다.
후속 구현은 **GPT-6 Sol High** 권고다. 설계된 gate·인계·주문/수신 수명은 동시성 검증을
함께 수행한다. 구조가 고정된 문서·반복 테스트 정리는 GPT-6 Luna Low/Medium으로 낮출 수 있다.

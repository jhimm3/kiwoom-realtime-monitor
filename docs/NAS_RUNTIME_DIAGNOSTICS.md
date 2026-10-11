# NAS 진단 운영·API·제어 계약

통합일: **2026-10-11** · 제품 2.1.0. 기존 NAS 진단 운영, 진단 API 설계, 하위 스위치 설계를 한 문서로 통합했다. 실제 소스·배포 release·검증 결과는 [CURRENT_STATUS](CURRENT_STATUS.md#current-summary), 녹화·Replay·성능 잔여는 [OPEN_ITEMS O12](OPEN_ITEMS.md)를 따른다.

## 범위와 현재 책임

운영의 제어→측정→상태→보고서→종료를 CLI/API의 같은 control·수집·보고서 경계로 처리한다. 관측 수량과 실제 drain/보존 여부를 구분하며 기본 OFF 계측을 자동으로 상시 켜지 않는다.

| 책임 | 코드 정본 |
|---|---|
| control 파일·master·세션/CAS·TTL | `central_server/diagnostic_workloads.py` |
| 표본과 보고서 계산 | `central_server/diagnostic_sampling.py`·`diagnostic_metrics.py` |
| run/child·취소·종료·보고서 수명 | `central_server/diagnostic_runs.py` |
| 인증된 조회·제어·실행 API | `diagnostic_read_routes.py`·`diagnostic_control_routes.py`·`diagnostic_run_routes.py` |
| CLI와 제한 운영 명령 | `scripts/nas_workload_diagnostic.py` · [NAS operator](NAS_OPERATOR_COMMANDS.md) |
| writer/저장 경로·독립 transaction | `diagnostic_writer_registry.py` · [저장 경로 감사](KIWOOM_STORAGE_WRITE_AUDIT.md) |

등록된 작업/계측 범위는 capabilities/workloads/writers 응답과 코드 registry에서 확인한다. 과거 문서의 8개 작업/12개 writer 수량을 현재 전수 목록으로 사용하지 않는다. 계좌·주문·실시간 수신/저장 독립 pause와 durable 인계는 항목별 검증 전까지 구현 완료로 보지 않는다. 아래 보호 경로 표는 확장 계약이며, capabilities에 없는 스위치를 사용할 수 있다는 뜻이 아니다.

## 운영 확인 순서

1. 현재 build/release·인증 capabilities·등록 범위와 TTL/권한을 확인한다. GET/status/기존 report 조회는 master나 capture를 켜지 않는다.
2. 명시 진단 세션에서 전체 측정·drain·복구·보고서 여유보다 긴 TTL을 확보한다. 같은 ON 반복으로 lease를 자동 연장하거나 운영 OFF 기능을 켜지 않는다.
3. 읽기 측정은 workload를 바꾸지 않는다. compare만 명시적으로 대상 admission을 제어하며 활동 없는 workload·이미 paused/운영 OFF 대상을 비교하지 않는다.
4. 실제 수신/작업/저장 활동·ACK·실패·pending과 구간별 표본을 함께 확인한다. requested/effective만으로 정지·복구 완료나 특정 writer의 원인을 확정하지 않는다.
5. 실제 worker/child 종료와 자신의 override 정리, 보고서 저장·회수 및 master/capture 상태를 확인한다. 운영 STOP/계좌 안전 gate를 진단 종료로 해제하지 않는다.

API 경로·필드·인증의 정본은 [API_CONTRACT](../API_CONTRACT.md)다. 아래 요약과 실제 구현의 세부 상한이 다르면 해당 contract와 runtime capabilities를 대조한다. 진단만으로 임의 shell/SQL을 실행하거나 운영 DB에 합성 주문/재생 입력을 쓰지 않는다.

<a id="api-contract"></a>

## 인증 API·실행·보고서 계약

기존 조회 경로를 유지하고 아래 제어·실행·보고서 경로는 기존 Bearer 인증을 사용한다. 전체 endpoint/필드와 trace·Replay 추가 옵션은 API_CONTRACT를 따른다.
GET은 master나 capture를 켜거나 workload를 멈추지 않는다.

| 경로 | 입력·결과 |
| --- | --- |
| `GET /api/v1/diagnostics/capabilities` | build/producer, 지원 항목·원천·scope·상한·미지원 이유; 실행 없이 확인 |
| `PUT /api/v1/diagnostics/control` | `target=master|capture|workload`, `enabled` 또는 `paused`, workload, TTL, expected instance/session/revision. target별 허용 필드만 수락 |
| `GET /api/v1/diagnostics/snapshot` | 고정 section enum(`postgres,activity,news_jobs,storage,host`) 선택, 선택 PID. 즉시 읽기 전용 bounded snapshot |
| `POST /api/v1/diagnostics/runs` | `kind=measure|compare|replay`, seconds, label, compare workload, request_id, expected session/revision. 202 + run_id/status URL |
| `GET /api/v1/diagnostics/runs/{run_id}` | 상태·phase·실제 기간·수집/누락 수·부분 결과·정리 결과 |
| `POST /api/v1/diagnostics/runs/{run_id}/cancel` | 해당 run에 cooperative stop 요청; 202. 반복은 멱등, 완료됐으면 현재 결과 |
| `GET /api/v1/diagnostics/reports` | 기존 20개 포함 bounded 목록·cursor·schema/version·날짜·완료 상태 |
| `GET /api/v1/diagnostics/reports/{report_id}` | summary 또는 전체 JSON download; 생성된 보고서만 반환 |
| `GET /api/v1/diagnostics/history` | 제어/수집 이벤트의 제한된 페이지, raw log/임의 파일 경로 금지 |

master/TTL/CAS와 보호 경로 제어는 아래 [통합 제어 계약](#control-contract)을 적용한다. runtime ACK가 없는 경로는 `pause_requested`이며 단순 `effective=false`를 drain 완료로 설명하지 않는다.

run은 활성 master에서 시작한다. master/capture 시작도 API로 가능하므로 셸이 필요 없다.
capture가 이미 활성화돼 있으면 남은 TTL을 검증해 빌려 쓰고 종료 시 끄지 않는다.
없으면 run owner로 capture를 만들고 자신이 만든 것만 정리한다. request_id는 같은
session에서 같은 요청 재전송 시 기존 run을 돌려주며, 내용이 다르면 409다.
compare만 명시적으로 pause한다. measure는 운영 workload를 바꾸지 않는다.

기존 시간 상한을 유지한다: master 60..7200초, capture/pause 60..3600초, measure 5..300초,
compare phase 5..1100초, replay 5..120초. 기존 전체 TTL 사전검사를 보존한다. label은 최대 80자,
보고서 목록/이력은 페이지당 최대 100건, activity 표본은 최대 200 backend,
EXPLAIN node는 최대 256개·전체 응답 1 MiB로 제한하고 잘림을 표시한다.
잘못된 enum/비유한 숫자/경로형 ID는 거절한다. 부재 404, 미지원 501, 충돌 409,
수집 실패는 section별 error code/type로 반환하며 DSN·예외 원문을 넣지 않는다.

### 수집·중단·재시작

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
  lifespan shutdown은 stop→실제 worker/child join→owner 정리 순서다. app 종료는 실제 작업 종료까지 off-loop 대기하고 수동 bounded stop과 구분한다. 수동 진단의 bounded join budget이 지나도
  살아 있는 worker를 완료로 표시하거나 새 run slot을 열지 않는다.
- 시작 manifest를 원자 기록한다. 프로세스 재시작 시 이전 running manifest는
  `aborted:producer_restarted`로 정리하고 자동 재개하지 않는다. instance가 같은
  Uvicorn process restart도 producer ID로 구분한다. 다른 owner의 자식 상태는 지우지 않는다.
- cleanup은 `(instance, session, owner)`가 여전히 같은 항목에만 적용한다. control
  revision 변경은 비교를 incomplete로 표시하고 새 수동 override를 덮어쓰지 않는다.
- compare의 2초 대기는 기존처럼 settle 시간일 뿐 drain ACK가 아니다. 이미 paused인
  대상은 거절하고 종료·예외·취소 모두 자신이 만든 pause만 해제한다. 모든 phase의
  실제 활동 증거가 부족하면 인과 판정을 하지 않는다.

### 정보 정확성·보관·노출

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


<a id="control-contract"></a>

## master·하위 상태 계약

```text
진단도구 master (기본 OFF, TTL 기본 10분 / 최대 120분)
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

### 작업·수신·저장 제어의 구분

**작업 중지**는 새 작업의 admission을 닫고 진행 중인 처리와 commit은 마무리한다.
**수신 중지**는 외부 자료를 받지 않으므로 회복 불가능한 공백이 생길 수 있다.
**저장 중지**는 받은 자료를 durable 인계한 뒤 특정 DB writer의 dequeue만 멈춘다.
세 동작을 같은 bool이나 성공 반환으로 처리하지 않는다.

| 제어 대상 | 멈출 위치 | 중단·재개 계약 |
| --- | --- | --- |
| 등록된 선택 작업 | 각 소유 loop의 다음 admission | in-flight 마무리 후 ACK. 기존 cursor/진행 원장으로 재개 |
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

### 보호 저장의 확장 계약 — durable 인계 미완료

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

### 동시성·주문·복구

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

### 비교 측정의 완료 기준

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


## 부분 replay와 전체 원인 Replay

<a id="collector-replay-design"></a>
<a id="2026-10-02-기록된-db-호출의-장후-부분-재생-pilot-로컬-후보"></a>

경량 scalar trace·synthetic/recorded-operation replay는 [경량 trace 계약](DB_REPLAY_TRACE_DESIGN.md), 동일 장중 사건·초기 상태·전체/단독/제외·코드 A/B는 [원인 입력 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md)을 따른다. 기존 collector fixture는 실제 parser/집계/store 경계 검사이며 전체 당시 운영 부하의 동일 재현이 아니다.

- 재생은 전용 DB/초기 상태·scope를 사전에 검증한다. 원천 입력에서 파생되는 DB 호출과 tape의 하위 호출을 중복 적용하지 않는다.
- 입력 종료→feeder 종료→실제 collector/save drain→내용 검증→진단 소유 행 정리 순서를 유지한다. waiter 취소가 실제 thread 종료를 뜻하지 않는다.
- DB-call raw·순번/청크 무결성과 원인 입력/초기 상태 동등성을 분리한다. incomplete·입력 거부·lineage/계좌/REST/뉴스 범위 누락을 전체 Replay-ready로 표시하지 않는다.
- 저장본 검증·부분 native replay·장중 무손실·전체 Replay·전체 앱 성능 acceptance는 각각 별도 판정이다. O12의 아직 남은 확인을 과거 후보 통과 수치로 닫지 않는다.

## 계측 해석과 변경 전후 검증

분봉·일봉의 bars/metadata/revision/COMMIT/total, 공통 DB call_id/backend PID/execute/commit/close·오류/재시도, 실제 반영 행수와 미계측을 구분한다. 시도 행수나 flush count를 성공 COMMIT/전체 수신량으로 바꾸지 않는다.

COMMIT/SQL 단계와 같은 backend의 wait/blocking owner 및 같은 시각 장치/queue 표본으로 후보를 구분한다. `no_sample`/100ms 미만/observer 실패를 wait 없음으로 해석하지 않는다. 보유 레코드 시각은 최초 enqueue 시간과 다르다. `UNREGISTERED`는 관측 경계의 무등록 호출만 탐지하며 미이관 경로 전체 수가 아니다.

`pg_stat_wal`/`pg_stat_io`/checkpointer는 cluster, database 통계는 DB, 장치는 host scope다. reset·감소·NULL·권한/버전·통계 반영 지연을 표시하고 전체 WAL/중첩 block layer/장치 delta를 writer 한 호출의 I/O로 귀속하지 않는다. 연결 설정·transaction-local 설정과 실제 timing 가용성을 구분한다.

외부시장 비교는 collector의 task/polling/완료/저장 활동을 확인하고 phase를 polling 주기보다 길게 둔다. 30초 표본에서 활동 0건이면 효과를 판단할 수 없다. A/B/A는 같은 입력·다른 부하·원천 보존의 검증을 대신하지 않는다.

transaction 또는 실시간 저장 변경은 [필수 보존 검사](KIWOOM_STORAGE_WRITE_AUDIT.md#transaction실시간-저장-변경-시-필수-검증)의 동시/중복/순서 역전·commit 전후 장애·재시작·멱등·원천/파생/완료값 대조를 함께 실행한다. 새 writer/DB/경로에는 master 아래 등록·계측·중단 중 보존·복구 계약을 같이 갱신한다.

모델 판단은 [현행 정책](MODEL_HANDOFF_POLICY.md)을 따르며 이 사용자는 Luna를 사용하지 않는다. 현재 source·실제 NAS·Linux flock·독립 PostgreSQL·수동 UI/장중·장시간 검증을 서로 대신하지 않는다.

## 통합 전 근거

- [진단 운영·CLI·이전 측정 전문](archive/2026-10-11/history/NAS_RUNTIME_DIAGNOSTICS_through_20261011.md).
- [원래 API 설계·검증 조건](archive/2026-10-11/reports/NAS_DIAGNOSTIC_API_DESIGN.md).
- [원래 하위 스위치 설계·구현 acceptance](archive/2026-10-11/reports/DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md).
- [문서별 이동·승계와 원문](archive/2026-10-11/README.md).

이 통합은 기존 계약을 모으는 문서 작업이며 새 스위치·durable 저장소·권한·API·운영 변경의 구현이나 실행이 아니다.

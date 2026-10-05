# 장중 사건 기록을 이용한 반복 부하 실험

2026-10-05 · O12 · 설계 확정, 원본 capture·window reader·내부 recorded executor를 로컬 구현

## 완료 기준

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

초기 예산은 producer 총 64MiB에 copy-in-progress와 worker pending까지 포함한다.
scalar trace에 8MiB를 예약하고 나머지를 입력/작업 payload가 사용한다. copier 동시성 2,
단일 불변 사본 charge 최대 8MiB, 이벤트 수 최대 32,768을 함께 적용한다. 이는 측정으로
승인해야 할 초기 한도이며 65분 무손실 보장이 아니다. queue·blob 보존 한도는 byte 수와
실제 RSS를 모두 검증한다. payload는 chunk 크기에 맞춰 worker가 분할/조립한다.

보존 worker는 5초 또는 high-water 알림에 깨고 backlog를 연속된 bounded chunk로
배출한다. 기존처럼 5초에 청크 하나만 쓰도록 제한하지 않는다. JSON·blob hash·파일 쓰기·
묶음 fsync는 이 worker만 수행한다. checksum과 manifest 참조가 함께 확정된 입력만
complete다. seq는 유실 점검용이며 전역 commit 순서가 아니다. 재시작/디스크 오류/
quota 도달/끝 경계에 걸친 작업은 명시적으로 incomplete 또는 censored다.

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

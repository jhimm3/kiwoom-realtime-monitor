# 의존성 축소와 기능 보존 검증 계획

2026-10-08 · 제품 2.1.0 · 조사 기준 `035af0a37c667aed3171140aa3cb7e424ec15fb3`

기능 사이의 의존성을 좁히되 입력, 출력, 실패, 완료, 수명 의미를 보존한다.

### 2026-10-11 TOP20 0W 저장 경계

실제 경로는 hub program event → TOP20 정규화/enqueue → `ProgramSnapshotWriter` → 기존
`save_dataset_snapshots` → `central_dataset_snapshots`의 program_flow/subject/snapshot_key →
기존 dataset 조회 API·replay 소비자다. 순위·구독·준비와 index loop 저장 시계는 서비스에 남기고,
writer는 pending·실제 owned save·실패 병합·최종 drain을 책임진다. batch SQL/transaction과
재시도 키를 바꾸지 않는다. QueryStore aggregate는 같은 backend를 조립하며 새 연결 계층은 없다.

전체 저장 흐름의 파일은 service/DB에서 service/writer/DB로 하나 늘지만, 저장 변경은 순위·구독
메서드 없이 writer/DB에서 이해할 수 있다. 정상 flush의 호출 깊이는 기존
index loop→flush→save pending→native write와 같다. 종료의 drain 호출은 실제 상태/task owner에
대한 호출이며 전달만 하는 wrapper를 추가하지 않는다. composite 서비스 저장 계약은 전달받는
writer의 요구까지 포함한 기존 10개, writer 자체 계약은 1개다.

변경 전 소스와 cold seed를 `tmp/top20-program-writer-before`에 보존했다. 기존 seed v1 문서의
키/bytes/hash와 native batch 실행 본문은 동일하며 불필요한 compatibility property는 만들지 않았다.
223건 관련 회귀와 복구 계약 결함 주입 3건의 실제 실패 탐지를 확인했다. owner 경로가 검증 대상인
기존 테스트만 이동했고 기능 기대값/assertion/CI 목록·실행 순서는 유지한다. 실제 결과와 별도
최종 `all-local` 3,758/3,758건·272/272 worker는 통과했다. 이 작업 트리의 hosted GitHub CI와
실제 PostgreSQL 검증은 미실행이며, 범위와 실행 경로는 `CURRENT_STATUS.md`에 구분했다.

### 2026-10-11 PC 수명 경계 검토

현재 AppController는 실제 worker·저장 타이머·시작/교체·종료 대기를 소유하므로 추가 관리 계층을
만들지 않았다. 수명 계약 검토 중 마지막 분봉/가격 저장 실패가 RAM pending을 복원해도 창이
닫히는 원인을 임시 DB에서 확정해 기존 owner의 종료 판단만 보완했다. 닫기 재요청은 같은 writer의
명시 재시도이며 이미 완료한 TOP20 partial·백업·화면 저장은 반복하지 않는다.

오래된 실패 알림과 최신 COMMIT이 교차하는 조건도 분봉/가격에서 재현했다. 종료 때 마지막 batch만
보관해 새 값으로 대체된 옛 실패 key를 제외하고, 대체되지 않은 peer와 마지막 batch 자체의 실패를
복원한다. native queue/SQL/transaction·정상 timer/worker 순서는 유지한다. source 파일/관리 계층
추가는 없으며 보조 함수는 실패 payload의 복원 정책을 실제로 처리한다.

AppController 기존 검사 26개의 본문/assertion은 유지하고 세 개의 고위험 종료 검사를 추가했다.
내부 메서드를 SimpleNamespace에 얹던 MainWindow fixture 한 개는 실제 owner로 바꾸되 assertion과
다른 42개 검사를 유지했다. 기존 목록에서 자동 발견되며 테스트 파일/CI 범위·순서 변경은 없다.
핵심 72건 및 마지막 보완의 직접 영향 29건 통과, 변경 전 동작에 대한 실패 탐지와 raw failed 기록은
CURRENT_STATUS의 최신 항목에 구분했다. 이번 변경을 포함한 누적 `all-local` 3,758/3,758건·272/272
worker는 최종 통과했다. hosted CI는 게시 뒤 독립 환경에서 실행하고, 실행 PC의 수동 확인 및 다른 writer job의 실패 완료/
영속 보존 정책은 이 검증으로 대신하지 않는다.

### 최초 단계 기록

첫 단계는 기존 검사를 빠짐없이 실행하고 실패와 미검증을 구분하는 검증 진입점이다.
이후 인증된 뉴스 조회 API 세 개를 첫 분리 대상으로 삼는다.
**1단계 검증 진입점과 2단계 뉴스 조회 경로 분리는 완료했다.** Hosted workflow 자체도 통과했지만,
main에서 required check로 강제되는지는 확인되지 않아 3단계를 완료로 판정하지 않는다. 최종 `all-local`
은 사용자 Windows 환경에서 1,868건/38 worker, 실패·오류·skip·미실행 0으로 통과했다.
Linux 전용 5개 모듈 65건과 disposable PostgreSQL 통합 87건도 hosted job에서 통과했고,
worker/process tree 종료와 미실행 검사 0을 확인했다. 최신 결과는
[GitHub workflow run 37808050356](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37808050356),
commit `2ecfbe0d0e34f345913a663d75c35936ff2f6454`다. 문서 정정만 포함한 후속 commit에서도
동일 workflow가 다시 성공했다. 첫 Linux 실행에서 발견한 fake Docker 실행권한 fixture를 보완했으며,
제품 동작 코드는 변경하지 않았다.

뉴스 history/sources/market-feed 세 경로를 `news_read_routes.py`로 옮겼다. ASGI 요청
12건과 Uvicorn loopback 실제 HTTP 요청 13건에서 상태, 헤더, 본문이 보존 기준선과 일치했고,
조회 전후 SQLite dump도 동일했다. 3단계 workflow를 hosted runner에서 실행해 Windows
`all-local` 1,465건과 disposable PostgreSQL 검증을 모두 통과했다. main의 required check 강제 여부는
현재 확인할 수 없다. GitHub branch-protection 조회는 통합 권한 403을 반환했고, ruleset 조회는 빈 목록을
반환했지만 이 조합만으로 보호 규칙의 부재를 단정하지 않는다. 저장소 관리자 권한으로 설정을 확인하기 전까지
main 병합 gate 적용 상태는 미검증으로 둔다. 성공한 check-run의 정확한 이름은
`Windows all-local regression`과 `Disposable PostgreSQL integration`이다. main 병합을 두 독립 환경의
전체 검증으로 보호하려면 설정 담당자가 이 두 검사를 required checks로 선택해야 한다.
NAS 배포는 계획에 포함되지 않는다.

이 계획은 새 사용자 요청에 따른 후속 작업이다. 완료된 과거 리팩터링 1~5단계를
재개하지 않는다. [개발 불변 규칙](../DEVELOPMENT_GUARDRAILS.md),
[현재 모듈 지도](../MODULE_MAP.md), [남은 작업](OPEN_ITEMS.md)을 함께 따른다.

## 검증 범위와 남은 공백

| 확인한 대상 | 확인 결과 | 남은 조치 |
|---|---|---|
| `scripts/run_core_regression.ps1` | 기존 143회 호출(142개 고유 모듈), 두 batch, 중복 호출을 보존했고 결과 보고도 확인했다 | 없음 |
| PC 수명 검증 | `test_app_controller`와 `test_main_window`를 독립 worker로 실행해 69건 통과 | 없음 |
| TOP20 수명 검증 | shutdown 및 execution boundary 모듈 10건 통과 | 없음 |
| API 경로 검증 | trace 경로 5개 기대값을 보완했고 뉴스 읽기 라우트를 4개 메서드만 가진 `NewsReadStore` 계약으로 분리했다 | 명시된 기대 목록과 변경 전 ASGI/HTTP 기준선을 유지 |
| 자동 실행 | GitHub run [37760150253](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37760150253), commit `b46150ce76f74640d1401c4707db711692e4e5f7`: Windows `all-local` 1,465건(실패·오류·skip 0, 9/9 worker와 process tree 종료 확인)과 disposable PostgreSQL job(경계 검사 및 87 tests)이 모두 통과. job 로그/report는 14일 artifact로 보존 | `main`에 required check를 설정해 병합을 차단하도록 적용 |
| DB 검증 | 소비자/직접 연결 정적 감사와 PostgreSQL 통합 테스트가 별도로 있다 | 정적 통과, SQLite 동작, PostgreSQL 동작을 서로 대신하지 않도록 결과 구분 |

처음의 코드 구조와 목록은 소스 대조로 확인했다. 수정 후 `all-local`의 9개 worker와
1,465건 통과를 현재 워크트리에서 확인했다. 모든 worker 및 프로세스 트리 종료가 확인됐고
잔류 자손은 없었다. 제한 환경의 정지 stack은
BaseProactorEventLoop._make_self_pipe → socketpair → accept를 가리켰고, 같은 두 테스트를
사용자 실행 환경에서 비교해 통과를 확인했다. 뉴스 계약 테스트는 보존한 ASGI 기준선과
Uvicorn loopback 실제 HTTP 기준선을 각각 비교한다.

trace 목록 누락은 `GET/POST /api/v1/diagnostics/trace`,
`POST /api/v1/diagnostics/trace/stop`,
`GET /api/v1/diagnostics/trace/{trace_id}`,
`GET /api/v1/diagnostics/trace/{trace_id}/chunks/{chunk_name}`다.
전체 경로의 다른 차이는 실행 시 추가 확인한다.

## 목표 경계와 보존할 의미

| 경계 | 책임과 의존성 | 변경 시 함께 검증할 의미 |
|---|---|---|
| NAS 조립 | `app.create_app`이 설정, 저장소, 서비스 생성과 연결을 소유 | 같은 설정의 활성 기능, 인증, 실제 연결 인스턴스 |
| API | 기능별 라우트가 요청 검증과 응답 변환을 소유하고 필요한 조회 계약만 사용 | 경로/메서드, 상태 코드, 필드/단위, 인증 실패, 입력 제한 |
| DB | 기존 QueryStore와 두 backend를 유지하고 소비자별 필요한 계약만 좁힘 | per-call 연결, transaction/rollback, lock, revision, 완료 표식, wake-up |
| PC | MainWindow가 표시, AppController가 작업자 수명과 조정, 기존 feature controller가 해당 작업을 소유 | 신호 전달과 스레드, 최신 결과, 중복 시작, 종료 전 저장, 재시작 |
| TOP20와 실시간 | 현재 owner와 task/queue 경계를 보존하며 독립 가능한 책임만 단계적으로 분리 | 순위 시각, 구독 ACK/READY, 수신 공백, pending 복원, commit 전후 재시도 |

AppController를 단순 전달자로 바꾸지 않는다. 현재 독립 상태와 종료 책임이 있는
controller를 이름이나 크기 때문에 재분리하지 않는다. Protocol은 의존성 범위를
표현할 뿐 transaction이나 실패 격리를 자동 보장하지 않는다.

각 변경은 `호출자 → 처리/저장 owner → 테이블/키 → 조회자 → API/화면/후속 작업`을
한 줄로 적고, 같은 입력의 값·순서·부수효과를 전후 비교한다. 테스트용 대역은
외부 시스템과 실패 주입에 쓰고, 저장 동작 검증은 실제 임시 DB를 사용한다.
정적 호출 수나 mock 호출 성공만으로 기능 보존을 판정하지 않는다.

## 1단계 공통 검증 진입점

다음 구현의 범위는 테스트 실행 도구와 목록이다. 제품 소스의 책임 이동은 2단계다.

### 파일과 실행 방식

- `tests/regression_profiles.json`: 기존 143회 모듈 호출(142개 고유 모듈)과 두 실행 묶음을 순서까지 보존한
  `core` 및 아래 프로필의 단일 목록. 기존 PowerShell 목록과 전체 순서를 대조했고 검토한
  실행 순서 fingerprint를 둔다. 프로필은 모듈 또는 기존 batch 이름만 선언한다.
- `scripts/run_regression.py`: 표준 라이브러리 기반 실행기. 선택 프로필의 batch를
  순차 자식 프로세스로 실행하고 `unittest.TestResult`에서 결과를 수집한다.
  동일 파일의 내부 worker 모드로 충분하며 새로운 실행 프레임워크를 만들지 않는다.
- `scripts/run_core_regression.ps1`: 현재 Python 탐색 순서를 유지하고 새 실행기의
  `core` 프로필로 연결한다. PowerShell 쪽에 두 번째 테스트 목록을 유지하지 않는다.
- `tests/unit/test_run_regression.py`: 작은 합성 suite와 별도 프로세스로 실행기 자체의
  실패 판정을 검사한다. 제품 테스트 전체를 다시 실행하는 테스트를 만들지 않는다.

| 프로필 | 포함할 기존 테스트 |
|---|---|
| `core` | 현재 143회 호출(142개 고유 모듈)과 두 batch, 기존 중복 호출 보존 |
| `pc-lifecycle` | `test_app_controller`, `test_main_window`, `test_market_cache_writer`, `test_run_test_app_with_data` |
| `top20-lifecycle` | `test_top20_program_shutdown`, `test_top20_execution_boundaries` |
| `runner` | `test_run_regression`; 검증 실행기의 실패/중단 판정 검사 |
| `news-api-contract` | 변경 전 뉴스 HTTP 응답 기준선과 같은 fixture/요청으로 비교 |
| `all-local` | core 두 batch와 나머지 네 프로필. 이미 실행한 모듈은 중복 실행하지 않음 |

프로필의 짧은 이름은 모두 `tests.unit.` 아래 모듈이다. `pc-lifecycle`의 Qt 테스트는
각 모듈을 별도 프로세스로 실행한다. 이는 native 객체 종료 오류를 구분하기 위한
테스트 수명 경계이며 제품 controller를 나누는 근거가 아니다.

명령 계약은 `python scripts/run_regression.py --profile <name> --output <new-directory>`다.
출력 경로 기본값은 무시되는 `tmp/regression/<unique-run-id>`이며 기존 결과를 덮어쓰지 않는다.
`--list`는 선택 batch와 모듈만 보여주고 앱 import나 테스트 실행을 하지 않는다.
v1은 명시적 프로필 선택을 사용한다. 변경 파일 자동 추론과 전역 import 그래프는 만들지 않는다.
CI의 새 모듈 검사는 기준 revision과 비교해 새 `tests/unit/test_*.py`가 manifest의
core batch 또는 프로필에 등록됐는지만 검사한다. 등록을 대신하거나 해당 모듈을 실행하지 않는다.
기존에 등록되지 않은 테스트 파일의 실행 여부도 이 검사로 바뀌지 않는다.

### 실행과 결과 판정

1. 자식 cwd를 저장소 루트로 고정하고 `src`, 루트, `tests/unit` 경로를 명시한다.
   실제 `kiwoom_monitor` 로딩 위치가 이 워크트리의 `src`인지 확인한다.
   실행별 임시 디렉터리를 만들고 `TEMP/TMP/TMPDIR`, `QT_QPA_PLATFORM=offscreen`을 지정한다.
2. 결과에 commit, 변경 유무, 테스트 대상 파일의 hash, Python 실행기/버전, 실제 소스 경로,
   프로필, 시작/종료시각, batch별 실행 수·실패·오류·skip·expected failure·unexpected success,
   종료 코드와 로그 위치를 기록한다. 환경변수 전체나 접속 문자열은 저장하지 않는다.
3. 테스트 발견 0건, import/의존성 오류, assertion 실패, unexpected success,
   worker 결과 파일 누락/손상, timeout, 중단, 비정상 프로세스 종료는 성공이 아니다.
   worker가 `OK`나 통과 JSON을 썼어도 실제 프로세스 종료 코드가 0이 아니면 실패다.
4. skip 또는 expected failure가 있으면 `incomplete`로 보고하고 비영 종료한다.
   v1에는 이를 성공으로 바꾸는 허용 목록이나 `ignore-failures` 옵션을 만들지 않는다.
   의도적인 환경별 제외는 해당 기능을 포함하지 않는 별도 프로필로 표현한다.
5. batch 제한시간은 명시된 양수 `--timeout-seconds`로 제어하며 기본값은 batch당 1800초다.
   Windows worker의 실행 과정에서 멈추면 worker와 그 자손 프로세스 전체를 종료하고,
   실제 종료된 PID를 확인한 뒤 `timeout`을 기록한다. 시작 직후 종료 전 자손을 놓치는
   경쟁 조건도 검사한다. 종료를 확인하지 못하면 남은 batch를 시작하지 않고 보고한다.
   정상 종료한 batch의 실패는 기록한 뒤 다음 독립 batch를 실행할 수 있다.
6. 최종 상태는 `passed/failed/incomplete`다. 필수 batch가 모두 실행되고 실패·미검증 없이
   종료한 경우만 `passed`, exit 0이다. 부모는 첫 batch 전에 예정 목록을 `run.json`에 남기고
   완료 때마다 진행과 남은 목록을 갱신한다. 프로세스가 강제 종료돼 최종 보고가 없으면
   최신 상태와 남은 batch가 `incomplete`로 남는다.

실행기 회귀는 정상 1건, 실패, import 오류, 0건, skip, expected failure,
unexpected success, 결과 작성 후 비정상 종료, timeout, 사용자 중단, 소스 경로 불일치를 포함한다.
Windows에서 timeout과 중단 때 worker가 만든 자식과 손자 프로세스가 모두 종료됐는지
별도 생성시각과 PID를 가진 fixture로 확인한다. 종료 확인에 실패하면 다음 테스트를 실행하지 않고
기존 로그와 미완료 batch 목록을 남긴다.
목록 이관 검사는 기존 143회 호출과 두 batch의 순서·중복 구성이 유지됨을 확인한다.
실제 실행 순서인 PC/TOP20 수명 프로필 후 `all-local` 전체 회귀까지 완료했다.
첫 실행에서 확인한 다섯 테스트 결함은 각각 다음 이유로 테스트만 보정했다: 저장소 인스턴스에
걸리지 않은 실패 주입, 명시 route 기대값의 trace 경로 누락, 미검증 일봉 fixture에 검증 완료
기대 적용, 현재 날짜에 종속된 30일 통계 fixture, 핵심 문장이 비어 있는 뉴스 fixture.
보정 후 같은 core 목록을 포함한 `all-local` 전 worker가 통과했다. 제품 코드 변경은 없다.

API 로딩이 실패하면 설치된 의존성과 예외를 먼저 확인한다. `httpx2` 설치,
FastAPI/Starlette 임의 교체, 가짜 import, 인증 검사 생략은 금지한다.
필요하면 지원되는 ASGI/HTTP 경로로 같은 요청·인증·validation assertion을 보존한다.
`news-api-contract`은 lifespan context도 명시적으로 실행하고 전송 방식을 결과 파일에 기록한다.

## 2단계 뉴스 조회 API 분리

1단계의 결과와 API 기준선이 확보된 뒤 진행한다. 첫 제품 변경의 범위는
`GET /api/v1/news/history/{kind}`, `GET /api/v1/news/sources`,
`GET /api/v1/news/market-feed` 세 경로다.

- `central_server/news_read_routes.py` 한 모듈에 `create_news_read_router(store, authorize)`를
  두고 요청 검증, 조회 호출, 응답 조립을 옮긴다. Router는 HTTP 경계를 직접 소유한다.
- 같은 모듈의 `NewsReadStore` Protocol에는 기존 시그니처 그대로 `load_news_history`,
  `load_document`, `load_news_source_diagnostics`, `load_market_news_feed` 네 메서드만 둔다.
  runtime wrapper나 별도 repository 객체를 추가하지 않는다.
- `create_app` 내부에서 지연 import하고 기존 store와 authorize를 전달해 router를 등록한다.
  backend 선택, initialize/close, 서비스와 lifespan은 기존 owner에 남긴다.
- 뉴스 수집·분석·작업 claim/complete, DB SQL과 schema, 계좌와 주문, 실시간 분봉은 이 이동에
  포함하지 않는다. 특히 분봉 조회는 RAM overlay와 coverage가 결합되어 있어 후속 조사 대상이다.

라우트를 옮기기 전에 임시 SQLite fixture와 당시 등록된 FastAPI 라우트를 사용해 ASGI 기준
응답 12건을 `tests/fixtures/api_contract_baselines/news_reads_v1.json`에 보존했고, Uvicorn
loopback 실제 HTTP 기준 응답 13건은 `news_reads_http_v1.json`에 별도로 보존했다. 두 기준선은
요청, 상태, `content-type`/`content-length`, 전체 JSON 본문을 저장한다. ASGI 검증은 앱 lifespan을
실행하며 실제 HTTP 검증도 같은 fixture와 경로에 요청을 보낸다. HTTP baseline은 200 네 건,
인증 401 네 건, 404 한 건, 422 네 건이다. 기준 응답은 이후 코드가 자동 생성하거나 덮어쓰지
않는다. 테스트용 토큰과 데이터만 사용했다.

보존 테스트 `tests.unit.test_news_api_contract_baseline`은 동일한 임시 SQLite fixture를 채워
**등록된 HTTP 라우트와 앱 lifespan**을 통해 실행한다. 기준 응답 파일은 capture 명령으로 한 번
생성했고, 이후 test/verify 경로는 해당 파일이 없거나 응답이 다르면 실패한다.
각 경로의 인증 401, 유효 요청 200, Query 제한 422, 미지원 history kind 404,
빈 결과의 구조, limit 전달, source별 필터를 고정한다. history의 `as_of` 필터,
`known/revisions`, 현재 body 조회에서만 조건부로 붙는 assessment 의미를 보존한다.
날짜·기사 ID·관측 시각은 고정 fixture를 사용한다. DB/API/client의 기존 관련 회귀도 함께 실행한다.

기준선 저장 후 테스트는 같은 요청과 fixture를 다시 보내 현재 응답 전체를 기준 JSON과 비교한다.
독립 router 테스트는 네 조회만 가진 대역으로 조립 가능함을 확인하고, 실제 앱 조립 테스트는
같은 인증과 route 등록을 확인한다. backend는 기존 SQLiteQueryStore/PostgresQueryStore다.
조회 테스트의 요청 전후 저장값 대조로 의도하지 않은 쓰기도 확인한다.

분리 전 요청 경로는 `app.py handler → QueryStore 구현`, 후에는
`news_read_routes.py handler → 같은 QueryStore 구현`이다. 요청당 전달 계층은 늘지 않는다.
조립을 이해할 파일은 하나 늘지만 뉴스 요청의 검증/응답 변경은 라우트 모듈에 한정된다.
이 효과가 없거나 전체 app/services를 새 라우트에 다시 전달해야 한다면 이동을 중단하고 경계를 재검토한다.

서버 소스를 바꾸는 실제 구현 시 기존 API 계약, 모듈 지도, 배포 방식별 빌드 식별자 규칙을
함께 적용한다. 경로 정적 감사의 파일/함수 identity 이동은 검토 기록을 남기며 이전 원장은 보존한다.

최종 확인: 세 경로는 `news_read_routes.py`에 있고 app은 기존 store와 인증 dependency만
전달한다. 명시된 HTTP 테스트, 12건 ASGI 기준선, 13건 loopback HTTP 기준선, DB 무변경 검사,
`all-local` 1,465건이 통과했다. 정적 QueryStore 소비자 감사는 변경 전후 모두
`review_required`이며 parse 오류와 stale binding은 없었다. 네 조회 호출의 승인된 binding은
새 모듈로 이동했고 app의 router 등록 forwarding edge 하나가 추가됐다. 기존 감사 기준선의
미해결 계약 변경과 항목은 남아 있으므로 정적 감사 전체 통과로 간주하지 않는다.

## 후속 검증과 자동 실행

API 프로필은 라우트 목록 검사를 넘어 인증·요청 검증·실제 DB 조회까지 실행한다.
DB 프로필에는 기존 `audit_query_store_consumers.py --check`,
`audit_postgres_access.py --check`와 실제 backend 테스트를 연결한다.
정적 도구의 parse 오류도 보고서에서 확인하며 기준선을 자동 덮어써 통과시키지 않는다.

PostgreSQL 변경은 `kiwoom_monitor_diagnostic_test` 전용 DB에서 중복·역순·동시 도착,
commit 전 실패와 commit 후 응답 유실, 재시작, revision/coverage 일치를 검사한다.
DB 미제공은 미검증이며 SQLite 성공으로 대체하지 않는다. 실제 NAS DB에는 테스트를 실행하지 않는다.

`.github/workflows/dependency-regression.yml`은 PR·push·수동 실행에서 로컬 검증 경로를
호출한다. Windows job은 `requirements.lock.txt`와 `all-local` 프로필을 그대로 사용한다.
Linux job은 PostgreSQL 17 disposable service를 띄우고, 전용 `kiwoom_monitor_diagnostic_test`
DB에서 기존 스키마 경계 검사 후 PostgreSQL access integration suite를 실행한다. 둘 다 실패
상태에서도 결과와 로그를 14일 보관한다. 로컬에서 workflow YAML을 parse하고 두 job 및 runner
구성을 확인했으며 manifest 목록 명령도 성공했다. 위 hosted run에서 두 job이 통과했다.
새 테스트 파일 등록 검사는 위 실행 단계와 별도로 기준 revision의 unit 모듈 목록을 비교한다.
현재 전체 409개 `tests/unit/test_*.py` 중 149개가 core/profile에 등록돼 있고 260개는 등록되지
않았다. 정적 AST 집계상 미등록 파일에는 `test_*` 메서드 2,205개가 있으며 테스트 메서드가
없는 파일은 없다. 매매 진입 분류, 자격증명 저장, 종목/일봉 저장소 같은 제품 경계 테스트도
포함된다. 14개는 별도 점검 스크립트에서 정확한 모듈 이름으로 참조되지만, 이를 상시 CI에서 실행된다고 볼 근거는
없다. 새 파일 검사는 기존 260개나 기존 미등록 파일에 추가되는 테스트 함수를 탐지하지 않으므로,
이 목록의 실행 필요성·중복·환경 의존성을 분류하는 별도 감사가 남아 있다. 일괄 등록은 하지 않는다.
GitHub 확인 결과 기본 브랜치는 `main`이고 branch protection 응답은 `Branch not protected`,
저장소 rulesets는 빈 목록이었다. 따라서 workflow는 실행되지만 현재 required check로 병합을
차단하지 않는다.

저장소 전체 의존성 금지 규칙은 한 번에 적용하지 않는다. 첫 뉴스 라우트의 상위 앱/UI 역방향
import 금지와 네 조회 계약부터 검사하고, 이후 실제로 분리한 경계만 늘린다.
자동 검사 실패 시 해당 변경의 완료와 후속 구조 변경을 보류한다.

## 단계별 완료와 남은 범위

1. **검증 진입점:** 기존 테스트 목록 보존, 실행기 실패 판정 회귀, 실제 결과 보고가 완료돼야 한다.
2. **첫 분리:** 뉴스 조회 세 경로의 전후 동일성, 인증/오류, 실제 DB 연결, core 회귀가 통과해야 한다.
3. **자동 실행:** Windows all-local, Linux 전용, disposable PostgreSQL hosted job은 성공했다.
   하지만 `main`의 required check 적용 여부는 현재 GitHub integration 권한에서 확인되지 않는다. 저장소
   관리자 설정을 확인해 이 workflow가 요구되는 검사가 맞는지 검증하기 전까지 3단계는 완료하지 않는다.
4. **다음 경계:** 위 방식으로 DB 소비 계약, TOP20 상태/저장, PC 수명 순서에서 하나씩 선정한다.
   여러 계층의 전면 재작성이나 transaction 통합은 별도 설계 없이는 시작하지 않는다.

이번 설계로 실시간 성능, 장시간 무손실, 실주문, NAS 배포가 검증되지는 않는다.
기존 O12와 진행 중인 capture/persistence 보호 조건은 그대로 따른다.
1단계 검증 진입점과 2단계 뉴스 조회 분리는 완료됐다. 3단계 workflow의 hosted Windows, Linux,
PostgreSQL job은 통과했지만, GitHub integration 권한이 branch-protection 조회를 거부해 기본 브랜치의
required check 적용 여부는 미검증이다. 설정 부재를 단정하지 않으며, 저장소 관리자 확인 전까지 3단계는
완료로 표시하지 않는다. 이 계획만으로 NAS 배포를 시작하지 않는다.

## 2026-10-08 전체 테스트 의존성 조사 결과

[409개 파일별 원장과 수정 대상](TEST_DEPENDENCY_AUDIT.md),
[미등록 260개 보호 동작 근거](REGRESSION_COVERAGE_AUDIT.md)를 작성했다.
기준선 당시 기존 CI 149개와 미등록 260개를 동일 기준으로 조사했다. 기준선 분류는 일반 후보 246,
별도 환경 5, 계약/fixture 검토 9이며 함수형 11개/45건 발견 공백이 있었다. 전체 일괄 확대안은
보류하고 추가 21개만 단독 검증 뒤 manifest에 반영했다. 현재 170개 등록/239개 미등록이며
남은 일반 후보는 226개다. 상세 상태와 검증 결과는 아래 후속 기록을 따른다.
전체 제안안 실행의 8개 실패 모듈을 성공으로 세지 않으며 Linux 5개는 미검증이다.
기대 해시/DB 감사 기준선은 현재 값으로 무조건 덮어쓰지 않는다. 제품 변경과 테스트 의존성 개선은 별도 작업이다.

### 2026-10-08 의존성 감사 후속

실행 소유권 동시성 테스트는 `database._execution_intent_values` patch를 SQLite authorizer gate로
바꿨다. helper 위치가 이동해도 실제 transaction 경계에서 owner 경쟁을 검증한다. `test_mock_account_drain`
14건과 결함 주입 대조 3건이 통과했다. P1 첫 12개도 115건 단독 통과 후 `all-local`에 추가했다.
manifest는 기존 149개 기준을 유지하며 추가 21개를 포함한다. P1 두 번째 8개는 178건 단독 통과 후
추가했다. 편입 후 `all-local` 1,774건/30 worker가 실패·오류·skip·미실행 없이 통과했고 모든 process
tree 종료를 확인했다. 세부 결과와 SHA256은 [409개 의존성 조사 후속 기록](TEST_DEPENDENCY_AUDIT.md)
및 최종 산출물 `tmp/regression/dependency-audit-ci170-fixture-refactor-verified/run.json`에 있다.
P1 두 번째 묶음에서 발견한 test-to-test fixture 결합은 `credential_owner_test_support.py`로 옮겼다.
연관 테스트 120건 격리 통과와 최종 `all-local` 1,774건/30 worker를 다시 확인했다. 미등록 UI 통합 테스트
2건은 이후 별도 조사에서 테스트 기대가 기존 UI 계약과 다르고 열린 SQLite WAL을 오탐한다는 점을 확인했다.

### 2026-10-08 계좌 UI/API 회귀 후속

`test_nas_credentials_ui_integration`은 실제 동작을 바꾸지 않고, 연결 해제 profile의 기본 숨김과
“연결 해제 계좌도 보기” 선택 후 표시를 각각 검사하도록 수정했다. 비활성 적용 뒤 API의
`active_profile_id=None`·주문 OFF, apply 횟수, 암호화 vault 파일 구성과 평문 키 비노출 검사는 유지했다.
SQLite `-wal`/`-shm`은 열린 DB에서 허용되는 root 산출물로 한정했다. 다른 테스트 모듈의 Response helper
import도 해당 통합 테스트 내부 helper로 옮겼다. 단독/P0 profile 2건 통과 후 manifest에 등록했으며,
현재는 171개 등록/238개 미등록(일반 226, Linux 별도 5, 계약/fixture 검토 7)이다.

최종 `all-local`은 1,776건/31 worker, 실패·오류·skip·expected failure·미실행 0으로 통과했다.
모든 worker process tree 종료를 확인했고 잔류 자손은 0이다. 결과는
`tmp/regression/dependency-audit-ci171-final/run.json`, manifest SHA256은
`d30785e869fce85bf60a3d8007aa3e67b22ac3ed34bd5e5cc7e69db35a931254`다. PostgreSQL live, Linux 5개,
호스티드 CI, 운영 NAS 배포와 main 병합은 이 단계에서 검증·수행하지 않았다.

### 2026-10-08 전역 자격증명 UI 검사 추가

`test_global_credentials_ui`에서 SQLite가 열린 동안 생성되는 `central.sqlite-wal`과
`central.sqlite-shm`을 정상 DB sidecar로 확인했다. root 산출물 제한은 유지하고 이 두 파일만 허용했다.
테스트 간 provider/HTTP fake import는 중립 support 모듈 또는 해당 테스트 내부 helper로 옮겼다.
단독 및 P0 profile 11건 통과 뒤 172번째 CI 모듈로 등록했다. 전체 `all-local`은
1,787건/32 worker, 실패·오류·skip·expected failure·미실행 0, worker/process tree 종료 32/32,
잔류 자손 0으로 통과했다. 결과는
`tmp/regression/dependency-audit-ci172-global-credentials-final/run.json`, manifest SHA256은
`c4bad473533024fcdc7ae6a6be2db17f1becba6c236e6a06a6b268e3cbc728bd`다.
GitHub hosted CI 및 실제 PostgreSQL/NAS 검증은 이 172개 manifest에서 확인하지 않았다.
미등록 `test_dart_credential_owner`는 단독 18건 중 1건이 read-only search 계약과 어긋난 fixture 기대에서
실패해 성공으로 계산하지 않고 보류 원장에 남겼다.

### 테스트 변경의 단계별 검증 절차

테스트를 수정할 때마다 전체 `all-local`을 반복하지 않는다. 변경 영향에 따라 좁은 검증부터 쌓고,
관련 작업 묶음이 끝났거나 공통 실행 기반을 바꿨거나 최종 완료를 판정할 때 전체 회귀를 실행한다.

1. 단일 테스트 모듈만 바뀌면 해당 모듈을 unittest로 실행한다. 고위험 동작은 관련 테스트 메서드의
   실패·복구 경로도 바로 실행한다.
2. 공용 fixture/support/helper가 바뀌면 저장소에서 실제 import 소비자를 모두 찾고, 소비 모듈들을
   한 invocation으로 실행한다. 알려진 실패·skip도 그대로 드러내고 성공으로 합산하지 않는다.
3. 관련된 수정들을 한 작업 단위로 끝낸 뒤 계좌·DB·뉴스·Qt 수명 등 해당 영역의 기존 regression
   profile을 실행한다. failure injection은 patch 적용 확인에서 끝내지 말고, 실패 발생과 rollback·retry·
   recovery assertion까지 도달하는지 확인한다.
4. 실행기, manifest 처리, 공통 초기 상태, 프로세스 종료 감시를 바꿨거나 영역 경계를 넘는 영향을
   발견했거나 완료를 판정할 때 `all-local`을 실행한다. 반복 수정 중에는 관련 모듈부터 다시 돌린다.
5. Windows 단독 실행은 저장소의 실행 Python, 워크트리 `src`, workspace temporary directory와
   Qt offscreen 설정을 사용한다. 명령은 저장소 루트에서 다음 형태로 실행하고, 프로세스 종료 코드와
   unittest의 failure/error/skip을 직접 확인한다.

   ```powershell
   $taskTempPath = Join-Path $PWD 'tmp\test-temp'
   New-Item -ItemType Directory -Force -Path $taskTempPath | Out-Null
   $env:PYTHONPATH = "$PWD\src;$PWD\tests\unit"
   $env:TEMP = $taskTempPath
   $env:TMP = $env:TEMP
   $env:QT_QPA_PLATFORM = 'offscreen'
   & '<검증된 Python 실행기>' -m unittest tests.unit.test_<module>
   ```

   여러 소비자 모듈은 같은 호출에 공백으로 나열한다. Qt·비동기 종료가 걸린 영역에는 직접
   `unittest` 결과 외에 소유 작업 종료/자손 정리가 확인되는 기존 전용 회귀 profile을 함께 사용한다.
6. 최종 `all-local` 직전 변경 파일 목록을 저장하고, `run.json`의 `planned_batches`가 현재 manifest의
   승인된 172개 고유 모듈과 일치하며 `test_file_sha256`가 모두 덮는지 대조한다. 미등록 변경 테스트는
   CI 범위를 암묵적으로 넓히지 말고 모듈별 결과·실행 건수·파일 hash를 별도 기록한다. 테스트 발견 0건이나
   기존 실패는 통과로 세지 않고 사유를 남긴다. 실행기가 자동 해시하지 않는 공용 fixture/support,
   fixture data, runner, manifest는 실행 전후 SHA256을 별도 기록한다. 실행 중 파일이 바뀌거나 manifest·
   test set이 달라지면 해당 run은 최종 증거가 아니므로 다시 실행한다.
7. 통과는 모든 계획된 검사 종료, failure/error/skip/expected failure/unrun 0, worker와 process tree
   종료 확인을 뜻한다. Linux 전용, hosted CI, 실제 PostgreSQL/NAS 검증은 실행 결과와 별도로 표시한다.
   검증된 브랜치가 게시된 다음 GitHub CI를 독립 환경의 마지막 확인으로 사용한다.

이 절차는 검사 강도를 줄이지 않는다. 대상 모듈과 직접 소비자 검사 후 관련 영역을 확인하고,
필요한 단계에서만 전체 회귀를 반복해 수정 원인을 더 빨리 찾는다.

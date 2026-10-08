# 의존성 축소와 기능 보존 검증 계획

2026-10-08 · 제품 2.1.0 · 조사 기준 `035af0a37c667aed3171140aa3cb7e424ec15fb3`

기능 사이의 의존성을 좁히되 입력, 출력, 실패, 완료, 수명 의미를 보존한다.
첫 단계는 기존 검사를 빠짐없이 실행하고 실패와 미검증을 구분하는 검증 진입점이다.
이후 인증된 뉴스 조회 API 세 개를 첫 분리 대상으로 삼는다.
**1단계 검증 진입점과 2단계 뉴스 조회 경로 분리를 완료했다.** 최종 `all-local`은 사용자
Windows 환경에서 9개 worker로 1,465건을 실행해 모두 통과했다(실패·오류·skip 0). 모든
worker와 프로세스 트리 종료를 확인했고 잔류 자손은 없었다. 기존 core 두 batch의 1,365건도
통과했고, PC 수명 69건, TOP20 수명 10건, 실행기 17건, 뉴스 기준선 비교 2건이 통과했다.
실행 소스는 이 워크트리의 `src`로 검증됐다. 첫 core 실행에서 발견된 다섯 테스트 결함은 원인을 재현한 뒤 테스트
조건·fixture를 바로잡았고, 제품 동작 코드는 변경하지 않았다.

뉴스 history/sources/market-feed 세 경로를 `news_read_routes.py`로 옮겼다. ASGI 요청
12건과 Uvicorn loopback 실제 HTTP 요청 13건에서 상태, 헤더, 본문이 보존 기준선과 일치했고,
조회 전후 SQLite dump도 동일했다. 다음은 3단계 CI 연결이며 NAS 배포는 계획에 포함되지 않는다.

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
| 자동 실행 | `.github/workflows/dependency-regression.yml`에 Windows `all-local`과 disposable PostgreSQL 통합 job을 추가했다. 각 job은 로그/report를 14일 artifact로 보존한다 | GitHub에서 첫 실행을 확인하고 필수 check 적용 여부를 확인 |
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
구성을 확인했으며 manifest 목록 명령도 성공했다. GitHub hosted runner의 첫 실행은 아직 없다.
필수 check의 branch protection 적용 여부도 확인되지 않았다. workflow 파일 존재만으로 CI 성공이나
병합 차단 완료라고 보고하지 않는다.

저장소 전체 의존성 금지 규칙은 한 번에 적용하지 않는다. 첫 뉴스 라우트의 상위 앱/UI 역방향
import 금지와 네 조회 계약부터 검사하고, 이후 실제로 분리한 경계만 늘린다.
자동 검사 실패 시 해당 변경의 완료와 후속 구조 변경을 보류한다.

## 단계별 완료와 남은 범위

1. **검증 진입점:** 기존 테스트 목록 보존, 실행기 실패 판정 회귀, 실제 결과 보고가 완료돼야 한다.
2. **첫 분리:** 뉴스 조회 세 경로의 전후 동일성, 인증/오류, 실제 DB 연결, core 회귀가 통과해야 한다.
3. **자동 실행:** 같은 명령의 CI 성공과 필수 check 적용 여부를 각각 기록한다.
4. **다음 경계:** 위 방식으로 DB 소비 계약, TOP20 상태/저장, PC 수명 순서에서 하나씩 선정한다.
   여러 계층의 전면 재작성이나 transaction 통합은 별도 설계 없이는 시작하지 않는다.

이번 설계로 실시간 성능, 장시간 무손실, 실주문, NAS 배포가 검증되지는 않는다.
기존 O12와 진행 중인 capture/persistence 보호 조건은 그대로 따른다.
1단계 검증 진입점과 2단계 뉴스 조회 분리는 완료됐다. 3단계 workflow 구성도 작성했으며,
실제 CI 실행과 필수 check 적용 여부 확인이 남아 있다. 이 계획만으로 NAS 배포를 시작하지 않는다.

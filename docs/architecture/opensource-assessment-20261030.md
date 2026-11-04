# 오픈소스 활용 후보 감사와 단계별 전환 계획

> **도입 상태.** 후보 비교는 조사 근거다. 설치·교체·벤치마크·DB 변경은 승인된 개별 전환으로 관리한다. 현재 AR의 남은 검증은 정본에서 확인한다. [관련 계약](app-target-20261023.md#0-현재-기준과-진행-현황).

확인일: **2026-10-30 KST**. 단계 **A — 조사/보고**. B는 경계 정리 제안, C는 미실행 비교 계획, D는 별도 승인 대상이다. 설치·버전 변경·제품 교체·DB schema 변경·benchmark·제품 회귀 실행은 하지 않았다.

현재 목표/진행 상태의 정본은 [v1 현재 현황](app-target-20261023.md#0-현재-기준과-진행-현황)이다. Windows/macOS/Linux 공통 실행 컴퓨터·로컬 SSD/NAS 장후 보관은 별도로 승인된 [v1 목표](app-target-20261023.md#portable-execution-target)다. **이 OSS 감사는 기존 AR 완료 조건·우선순위를 바꾸지 않으며, 목표 배치 승인도 라이브러리 도입 승인이 아니다.** R3/AR를 새 OSS 프로젝트로 다시 수행하지 않는다.

**최신 기능별 종합 비교:** [10절 전체 시스템 보완](#system-wide-supplement). 기존 TOP10은 당시 범용 기능 비교이며 전체 후보 수의 상한이 아니다. 9절의 저장/조회/전송 근거는 반복 조사하지 않고 연결했다.

## 1. 조사 범위와 현재 외부 라이브러리

현재 작업 트리(source165, HEAD `0d7da46` + 기존 미커밋 변경)를 기준으로 제품 Python **414개**, script **180개**, 검사 **532개**의 구문/의존 import를 대조했다. 구문 오류0은 탐색 근거이며 1,126파일 전체의 책임 정밀 감사 완료를 뜻하지 않는다. 후보는 아래 실제 호출자·본문·저장/소비·종료 계약을 추가 확인했다. 기존 모듈 감사·검증 이력은 재사용하고 판정을 초기화하지 않았다.

선언은 [pyproject.toml](../../pyproject.toml), 전체 고정판 목록은 [requirements.lock.txt](../../requirements.lock.txt)를 정본으로 참조한다. 프로젝트 직접 의존성은 중복 extra를 제외해 **13종**, lock은 **97종**이다. root 개발 `.venv`는 프로젝트 자체를 포함해106개 distribution이며 lock의97개와 일치하는 버전이 설치돼 있다. 이는 실제 제품 역할의 설치 요건을 모두 충족한다는 뜻이 아니다.

| 직접 선언/사용 역할 | 현재 고정판 | 기존 활용·라이선스 metadata |
|---|---|---|
| core | websockets16.1.1 / tzdata2026.3 | REST/WSS 실시간·시간대. BSD-3-Clause / Apache-2.0 |
| query/GUI | PySide6 6.11.1 / Pillow12.3.0 | Qt/이미지. LGPL-3.0-only 또는 GPL 대안 / MIT-CMU |
| server | FastAPI0.141.1 / Uvicorn0.52.4 / psycopg3.3.6 | API·서버·PG. MIT / BSD-3-Clause / LGPL-3.0-only |
| server/chatgpt | cryptography46.0.7 / google-auth2.41.1 | AES-GCM·서명 검증. Apache-2.0 또는 BSD-3-Clause / Apache-2.0 |
| 선택 OCR | paddlepaddle3.2.2 / paddleocr3.7.0 | 기존 OCR. Apache-2.0 계열, 포함 binary/모델 고지는 별도 유지 |
| 선택 Drive | google-api-python-client2.187.0 / google-auth-oauthlib1.2.3 | 기존 API/OAuth. Apache-2.0 |

이미 활용하는 추가 직접/간접 경계는 다음과 같다. 위 라이선스는 설치된 metadata 확인값이며 배포할 정확 wheel·포함 native dependency의 LICENSE/NOTICE를 대체하지 않는다.

| 라이브러리 | lock/root 개발환경 | 실제 역할 구분 |
|---|---|---|
| Pydantic/core | 2.13.4 / 2.46.4, MIT | 제품15파일 직접 import. 서버 모델·일부 장전 입력에 이미 사용; 공통 domain/query에 일괄 강제하지 않음 |
| Starlette | 1.6.0 | FastAPI 하위 기반과 credential middleware에 실제 사용 |
| NumPy / OpenCV | 2.3.5 / 4.10.0.84 | 제품 직접 사용은 OCR 경계. NumPy는 BSD 계열·포함 binary 고지, OpenCV는 Apache 계열 |
| pandas | 3.0.5 | 제품 직접 import0이지만 PaddleX가 runtime 요구. 미사용 패키지로 단정/제거 불가 |
| pypdfium2 | 5.13.0 | 일지 전략팩 PDF 추출에 실제 사용. wrapper/PDFium 포함 고지 필요. pyproject 역할 직접 선언은 없음 |
| HTTPX / aiohttp | 0.28.1 / 3.14.3 | HTTPX는 검사28파일, aiohttp는 잠금 목록에 존재. 제품 src 직접 import는 각각0 |
| psutil / filelock | 7.2.2 / 3.32.3 | psutil은 script1/검사1, 제품 RSS/identity는 자체 구현. 제품 직접 의존성 승격은 별도 변경 |
| dateutil/cachetools/requests 등 | 전체97 목록에 포함 | transitive 설치와 제품 직접 사용을 구분. csv/json/logging/argparse/asyncio/zoneinfo는 이미 표준 라이브러리 활용 |
| openpyxl / win32com | 제품 역할 선언 없음 | openpyxl은 비교 script1, CREON은 선택 Windows COM. 현재 root에 openpyxl 미설치 |

**의존성 기술 부채 — 이번에 수정하지 않음:** lock 밖에는 annotated-doc0.0.5·rsa4.9.1, pip26.2.1·PyInstaller6.22.2와 build 보조 altgraph/pefile/hooks/pywin32-ctypes가 있다. 개발 editable 프로젝트 metadata는1.1.14이나 pyproject/검증 wheel은2.1.0이다. metadata 버전만으로 실제 import 소스를 판정하지 않는다. 기존 독립 설치 manifest/소스 origin 증거를 우선한다. PDF·Pydantic 등 직접 쓰는 기능이 OCR/server의 우연한 transitive 설치에 기대지 않는지 역할별로 정리할 계획이 필요하다. 이번에는 lock/환경을 일괄 갱신하지 않았다.

## 2. 교체 검토 우선순위 TOP 10

**10개 도입 권고가 아니다.** 알려진 결함/범용 구현 부담·변경 위험·예상 총비용을 함께 비교한 순위다. 성능·메모리·장애율 개선은 전부 **미측정**이다. 비교할 가치가 있는 후보와 유지/보류 판정을 구분한다.

| 순위·기능 | 실제 현재 구현/호출 경계 | 후보·적합 범위 | 판정·통합/이전 범위 |
|---|---|---|---|
| 1 OS 자원/프로세스 관측 | [RSS](../../src/kiwoom_monitor/application/research_resources.py#L49)→연구 guard, [별도 서버 RSS](../../src/kiwoom_monitor/central_server/resource_usage.py#L35), [시작토큰](../../src/kiwoom_monitor/presentation/process_control.py#L140)→durable owner/복구 | **psutil**. RSS bytes 관측과 OS API 유지보수. macOS RSS/시작토큰 공백의 해결 후보 | RSS 비교 우선. identity는 기존 FILETIME/boot tick과 다른 표현이므로 구토큰/판본/UNKNOWN/PID reuse 검증 후 별도 판단. Popen/native handle join은 유지 |
| 2 기본 사용자 자료 경로 | [AppPaths.for_current_user](../../src/kiwoom_monitor/infrastructure/app_paths.py#L50)→bootstrap/restore/child | **platformdirs**로 설치본 기본 base directory만 계산 | 좁은 비교 우선. Windows 기존 경로·source checkout·명시 root·DB 이름은 유지. 경로가 실제 로컬 SSD인지는 별도 계약 |
| 3 일반 HTTP 전송 | [CentralContentClient._request](../../src/kiwoom_monitor/infrastructure/central_content_client.py#L482), Remote·기사 fetch 등 | **HTTPX sync Client**. 기존 thread/owner 안에서 요청·TLS·pool 재사용 | 유지할 읽기 경로부터 조건부 pilot. 없어질 장중 NAS 왕복을 먼저 이관하지 않음. 키움 주문·credential·SSE는 첫 범위 제외 |
| 4 서버 입력 shape 검증 | [allocation route](../../src/kiwoom_monitor/central_server/operational_settings_routes.py#L65)→[config 검증](../../src/kiwoom_monitor/central_server/ram_shadow_selection_config.py#L90); 이미 Pydantic 모델 다수 | 기존 **Pydantic/TypeAdapter** 재사용 | 일반 구조 검사만 조건부. null/omitted/coercion/extra·기존 오류 보존. domain 업무·CAS·승인 검증은 유지; 새 DTO/변환 계층만 늘면 기각 |
| 5 HTML 기사 본문 추출 | [HTMLParser 기반 추출/정제](../../src/kiwoom_monitor/infrastructure/article_text.py#L85)→BODY→RULE/AI | **lxml.html** mechanics 우선 비교, **Trafilatura**는 범용 fallback 대안 | 비교 후보. 한국어 정제·정확 selector·발행 근거·본문 판본은 유지. 파서 출력 변화는 새 처리 판본으로 다루며 기존 BODY 재작성 없음 |
| 6 XLSX 셀 읽기 | [ThemeRepository](../../src/kiwoom_monitor/infrastructure/excel/theme_repository.py#L14) ZIP/XML 약40행→테마 검토/승인 | **openpyxl** read-only workbook/cell reader | 조건부 중간 가치. reader만 대상, 테마 구분/매칭·미리보기·저장은 유지. 작은 기존 구현보다 dependency 비용이 크면 유지 |
| 7 서버 환경변수 파싱 | [CentralServerSettings.from_environment](../../src/kiwoom_monitor/central_server/config.py#L121)→create_app | **pydantic-settings**의 타입/alias 파싱 | 조건부. frozen dataclass 반환·빈값 fallback/우선순위/clamp/한국어 오류/비밀 비노출 유지. `.env`/새 secret/CLI 입력원을 자동 추가하지 않음 |
| 8 거래일·예외 일정 참조 | [KRX 시간](../../src/kiwoom_monitor/application/market_session_schedule.py#L27), [이전 영업일](../../src/kiwoom_monitor/application/market_data_finalization.py#L27) | **exchange_calendars XKRX**를 오프라인 날짜 참조와 대조 | 제한 후보. 저장된 실제 거래일 우선, 키움0s·NXT·현재 phase/거래가능성 대체 불가. 달력 판본/가용시점 고정 필요 |
| 9 비Windows desktop secret 보관 | [DPAPI](../../src/kiwoom_monitor/infrastructure/kiwoom_rest/local_config.py#L25)→설정/계좌 mirror/뉴스/Drive/OAuth | **keyring**의 승인된 OS backend | Mac/Linux 신규 backend만 조건부. 기존 Windows DPAPI/NAS AES-GCM 유지. account HMAC·activation·회전 lock/원자 다중저장은 keyring이 해결하지 않음 |
| 10 schema migration 실행기 | [중앙 runner](../../src/kiwoom_monitor/central_server/schema_migrations.py#L36), [PC runner](../../src/kiwoom_monitor/infrastructure/persistence/schema_migrations.py#L28) | **Alembic** | 현재 보류. 작은 runner2개를 위해 SQLAlchemy/Mako·기존 판본/receipt 승계 비용을 늘릴 실익이 확인되지 않음. 자동생성은 source/T0/선별 이전 정책을 해결하지 않음 |

## 3. 공식 지원·유지보수·라이선스 비교

아래는 2026-10-30 공식 배포/문서 조회값이다. **최신판 설치/업그레이드 권고가 아니며 공식 지원 선언·wheel 존재와 이 앱의 실제3OS 수락은 별개다.** 릴리스가 오래됐다는 사실만으로 방치 프로젝트라고 단정하지 않았다.

| 후보 | 확인한 공개판/라이선스 | 환경·유지보수 근거/남은 확인 | 공식 자료 |
|---|---|---|---|
| psutil | 7.2.2, 2026-01-28, BSD-3-Clause | Python≥3.6; CPython3.13/Windows·macOS·Linux와 ARM 배포. native memory API 오류/권한 회귀 필요 | [배포](https://pypi.org/project/psutil/)·[API](https://psutil.readthedocs.io/stable/index.html)·[고정판 변경](https://github.com/giampaolo/psutil/tree/release-7.2.2) |
| platformdirs | 4.11.10, 2026-09-18, MIT | Python≥3.10·3.13, pure wheel, 3OS 경로 지원. 실제 사용자 권한/child 전달은 미검증 | [배포](https://pypi.org/project/platformdirs/)·[플랫폼](https://platformdirs.readthedocs.io/en/latest/platforms.html) |
| HTTPX | 0.28.1, 2024-12-06, BSD-3-Clause | Python≥3.8·pure wheel. upstream master는3.13 CI 포함, 고정판 CI는Ubuntu3.8~3.12. 최근 확인한 commit은2026-02-23; 앱3OS 설치/동작 미수락 | [배포](https://pypi.org/project/httpx/)·[Client](https://www.python-httpx.org/advanced/clients/)·[TLS](https://www.python-httpx.org/advanced/ssl/) |
| Pydantic | 사용2.13.4/core2.46.4; 최신2.14.2(2026-10-29), MIT | 기존 서버 판본 재사용부터 평가. 최신은Python≥3.10·3.13/core3OS wheels. 버전변경을 검증 리팩터링과 묶지 않음 | [배포](https://pypi.org/project/pydantic/)·[TypeAdapter](https://docs.pydantic.dev/latest/concepts/type_adapter/)·[strict](https://docs.pydantic.dev/latest/concepts/strict_mode/) |
| lxml / Trafilatura | lxml6.1.3(2026-09-02), BSD-3-Clause / Trafilatura2.3.1(2026-10-06), Apache-2.0 | lxml Python≥3.8·cp313 3OS/x64/ARM wheels. Trafilatura≥3.10·3.13 선언, 필수 추출 dependency 증가. 실제 한국어 정확도 미측정 | [lxml](https://pypi.org/project/lxml/6.1.3/)·[HTML](https://lxml.de/lxmlhtml.html)·[Trafilatura](https://pypi.org/project/trafilatura/)·[고정 의존성](https://raw.githubusercontent.com/adbar/trafilatura/v2.3.1/pyproject.toml) |
| openpyxl | 3.1.5, 2024-06-28, MIT | Python≥3.8/pure wheel·et-xmlfile. 공식 source는접근보호로 최근commit/issue 대응 확인 불가. 성숙도와 현재 활발한 유지보수를 구분. 실제3.13 수락 없음 | [배포](https://pypi.org/project/openpyxl/)·[최적화/close](https://openpyxl.readthedocs.io/en/stable/optimized.html) |
| pydantic-settings | 2.16.2, 2026-10-28, MIT | Python≥3.11·3.13/pure wheel, 공식 Windows env 동작·OSIndependent. 별도 dependency, 현재 lock에 없음 | [배포](https://pypi.org/project/pydantic-settings/)·[설정 의미](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |
| exchange_calendars | 4.13.2, 2026-03-10, Apache-2.0 | Python≥3.10,<4·3.13 선언. NumPy/pandas/음력 등6 dependency. NXT 지원 근거 없음 | [배포](https://pypi.org/project/exchange-calendars/)·[XKRX](https://raw.githubusercontent.com/gerrymanoim/exchange_calendars/4.13.2/exchange_calendars/exchange_calendar_xkrx.py) |
| keyring | 25.7.0, 2025-11-16, MIT | Python≥3.9/pure wheel, OS vault 지원. Linux headless D-Bus/daemon/unlock 조건, backend 전체3.13/ARM 미검증. dev판과stable 구분 | [배포](https://pypi.org/project/keyring/)·[문서](https://keyring.readthedocs.io/en/latest/)·[고정 dependency](https://raw.githubusercontent.com/jaraco/keyring/v25.7.0/pyproject.toml) |
| Alembic | 1.20.1, 2026-10-22, MIT | Python≥3.10·3.13/pure wheel, SQLAlchemy/Mako 및 native DB 환경 검증 필요 | [배포](https://pypi.org/project/alembic/)·[SQLite batch](https://alembic.sqlalchemy.org/en/latest/batch.html) |

Trafilatura2.3.1은 `charset_normalizer≥3.5.2`, `urllib3≥2.8,<3`을 요구한다. 현재 lock3.5.0/2.7.0에 단순히 얹을 수 없다. lxml/courlan/htmldate/jusText도 따라오므로 lxml 단독보다 통합/업데이트 부담이 크다. htmldate의 추정 시각을 원본 발행 근거로 승격하지 않는다.

## 4. 직접 구현 유지와 비채택 비교

| 대상/검토 대안 | 유지 이유·공식 비교 근거 |
|---|---|
| CentralRestBroker / 범용 queue·tenacity | 기존 asyncio 큐 위의 우선순위·동일요청 합류·generation·제어/저장 drain이 제품 책임. [tenacity9.2.1](https://pypi.org/project/tenacity/)(Apache-2.0)의 retry는 주문 멱등성을 제공하지 않음. 기본 decorator를 주문/유료호출에 공통 적용하지 않음 |
| native 저장 / SQLAlchemy Core | [SQLAlchemy2.1.6](https://pypi.org/project/SQLAlchemy/)(MIT)는 성숙하나 SQLite BEGIN IMMEDIATE·PG SKIP LOCKED/JSONB와 같은 cursor의 원자 업무는 남음. Engine+기존SQL 두 체계가 생기는 전면전환 보류 |
| canonical JSON / orjson·msgspec | [orjson3.13.0](https://pypi.org/project/orjson/)은 MPL-2.0 AND(Apache-2.0 OR MIT), [msgspec0.22.0](https://pypi.org/project/msgspec/)은 BSD-3-Clause. 3OS cp313 wheel이 있어도 bytes/NaN/정수/datetime·정렬hash·receipt 의미를 바꿀 수 있음. 새schema체계/변환 부담 대비 근거 부족, 재벤치 프로젝트로 승격하지 않음 |
| TTL/상태 cache / cachetools | [7.2.7](https://pypi.org/project/cachetools/)(MIT), 현lock6.2.6. broker는 expiry순 eviction, 설정은commit ACK/generation, 후보는연관상태동시삭제다. 일반LRU와다름. 작은 helper에 subclass/lock 추가할 실익 부족 |
| 복원/vault lock / portalocker·filelock | 현 Win32/POSIX shared/exclusive·권한/symlink·child 전체수명 유지. [portalocker4.4.2](https://pypi.org/project/portalocker/)(BSD-3-Clause)는 Windows shared lock에pywin32 추가, [filelock4.2.4](https://pypi.org/project/filelock/)(MIT)는 현3.32.3과다른major. marker/soft lock과 동등하지 않음 |
| 일정 / APScheduler | [3.11.3](https://pypi.org/project/APScheduler/)(MIT). 현재 순수 경계계산+기존QTimer/worker owner에 misfire/coalescing/executor/jobstore 수명을 추가할 이득 없음. 키움 보정시계·장중 phase는 앱 계약 |
| 시간 / dateutil | [3.0.0](https://pypi.org/project/python-dateutil/)(Apache/BSD), 현lock2.9.0.post0. 엄격aware ISO/HTTP Date는 datetime/email.utils/zoneinfo로 이미 처리. 관대한추론으로 PIT의 unknown/거부를 정상시각으로 채우지 않음 |
| logging / Loguru·structlog | [Loguru0.7.3](https://pypi.org/project/loguru/)(MIT), [structlog26.1.0](https://pypi.org/project/structlog/)(MIT OR Apache-2.0). 현재표준logging 위의비밀제거·KST14일보관·소유marker·busy/legacy보존·sink오류전달은 남음. 새queue/formatter요구없어 유지 |
| HTML 편의 / BeautifulSoup·readability-lxml | [BeautifulSoup4.15.0](https://pypi.org/project/beautifulsoup4/)(MIT)는 본문판별기아님. [readability-lxml0.9](https://pypi.org/project/readability-lxml/)(Apache-2.0)는Trafilatura와겹침. 후보를늘리기위한새추상화/중복추출기보류 |
| CSV·DOCX·PDF | CSV는이미stdlib. DOCX는원문순서/revision문단·표 의미가 [python-docx1.2.0](https://pypi.org/project/python-docx/)(MIT)의 paragraphs와다르고 lxml 추가가필요. PDF는기존 [pypdfium2 5.13](https://pypi.org/project/pypdfium2/5.13.0/) 활용 유지, 역할설치/실제PDF검증만후속 |
| 연구/매매 계산 | NumPy/pandas 기본 rolling/resample/ddof로 PIT·revision·연속분봉·Fraction·정수bps·MDD 사건순서를대체할수없음. raw20판본변경비용대비병목/절감미입증. 전략·Risk·주문·단회전송·UNKNOWN/부분FILL·단일owner와시계보정은유지 |

기존 FastAPI/Uvicorn/websockets/google-auth·cryptography·Pillow/PySide·OCR/PDF의 성숙한 구현은 계속 재사용한다. 단순히 직접 import0이라는 이유로 transitive 패키지를 지우지 않는다. CSV/JSON/시간/CLI/asyncio 큐처럼 표준 라이브러리를 이미 쓰는 것은 자체 엔진으로 분류하지 않는다.

## 5. 현재 AR과 연결할 B단계 제안

| AR | 이번 리팩터링에서 유지/명확화할 경계 | 라이브러리 교체와의 분리 |
|---|---|---|
| AR01/02/03 | 기존 시장/설정 owner·RAM/큐·입력공급, OS/물리 위치와분리 | 새scheduler/HTTPManager/cache owner를추가하지않음. Nas통신이남는경로만후속HTTP비교 |
| AR04/05 | native command/receipt·원본/복사·동기화ACK·XLSX reader→사용자검토→저장 | ORM/마이그레이션/일괄schema교체없음. 파일reader/HTTP전송만독립후보 |
| AR06 | 본문parser mechanics와한국어정제/발행근거/RULE·AI·작업수락판본구분 | 파서교체는별도처리판본·입력비교로관리, 과거BODY/hash/유료재분석정책불변 |
| AR07 | 기존전략·주문·fresh Risk/control·계좌/키·checkpoint 소유유지 | psutil관측/HTTP계층이권한성공을뜻하지않음. C20 후보와라이브러리교체동시통합금지 |
| AR08/09 | 기존AppPaths/RSS/process/credential/safe logging·선택도구·role extras | 교차OS 공개계약만명확화. 기존owner의작은함수교체가능성을검토하며새범용계층강제없음 |

B도 이번에는 **설계 제안**이다. 구현에 필요한 최소 책임 정리는 기존 AR 작업으로 수행하고, 라이브러리 교체 commit/검증 범위와 섞지 않는다. 별도 승인 없는 commit 자체는 계속 금지다.

## 6. 리팩터링 이후 별도 전환 순서

1. 현재 AR의 안전/상태·호출·종료·복구를 먼저 정리한다. 승인된 OS 목표에서 필요한 RSS·경로 계약을 고정한다.
2. 재개/독립 검증 승인 뒤 psutil RSS와 platformdirs부터 각각 비교한다. 영속 process token·keyring은 뒤의 별도 범위다.
3. 남길 일반 읽기 HTTP 경로 하나에서 HTTPX sync를 평가한다. 키움 `fork_verified_candidate`는 `copy.copy`를 쓰므로 pool속성을단순추가하면원본/후보수명이공유된다. 주문/회전토큰/SSE전면전환은후순위다.
4. 서버에이미있는 Pydantic의수기shape제거, HTML파서·XLSX reader는각각독립입력/오류비교로판정한다. pydantic-settings·달력·keyring은현재코드가충분하면보류를유지한다.
5. C결과가좋고사용자가선택한기능만D로전환한다. 승인전설치/benchmark/교체없음. 장후NAS전송/OS배포의실제구현과도동일저장·수명계약을동시에바꾸지않는다.

## 7. C단계 성능·안정성 비교와 D단계 롤백 계획

**공통 측정 방법:** 기존 소스/입력·의존판본·호스트·실행주체와 warm/cold를 고정한다. 동일 입력의 독립 기대값·오류·저장/호출/COMMIT 결과를 먼저 비교하고, 경과시간/CPU·peak RSS·연결/파일 handle·대기열/종료를 기존 도구로 측정한다. 라이브러리 import 메모리와 전체역할 RSS를 포함한다. 필요한반복수는입력분산/노이즈를보고정하고새일률임계값을만들지않는다. 업스트림benchmark를앱의개선수치로쓰지않는다. 이번측정0.

| 후보 | 같은 입력/실패에서 확인할 것 | 기존 검사·rollback 단위 |
|---|---|---|
| psutil | bytes/WorkingSet동등성·권한/프로세스부재·PID재사용·macOSguard·호출주기·cold import RSS | test_research_resources/process_control·기존측정함수. nativehandle/구tokenreader 유지 |
| platformdirs | Windows기존경로·source/explicitroot·Mac/Linux/ARM·권한·restore/child전달·재시작 | test_app_paths/strict_restore·base계산함수. DB이동/이전원본삭제없음 |
| HTTPX | 정확wire/오류/TLS trust/redirect·한도·Date clock·boundedread·취소/close·응답손실·동일requestID | test_central_content_client/remote_kiwoom_rest_client/central_credentials_client/credential_barrier/news_body_processor/research_result_delivery. 전송adapter/package만복귀 |
| Pydantic/settings | null/omitted·bool/int·extra·원422/409/503·부분PATCH·빈env/별칭우선·비밀오류·모든반환필드 | test_ram_shadow_optional_runtime/central_operational_settings/central_server_config 등실제등록ID재선정. JSON/hash/native/form정책유지 |
| HTML | 고정허가HTML·Naver/일반publisher·깨진태그·사진/댓글/저작권·한국어경계·발행/수정충돌·길이·빈본문 | test_article_text/news_jobs/news_observation_history/news_body_processor/historical_backfill. 정답은원문에서독립확인; 새입력parser만복귀 |
| XLSX | shared/inline/richtext·희소행·sheet관계·수식cache/부재·선행0·행순서·손상/handleclose·승인전쓰기0 | test_theme_import/theme_dialogs는일부stub이라실제XLSX보강필요. workbook재저장없이reader복귀 |
| 달력/keyring | 실제거래일우선·휴장/특별일·UTC/KST/PIT·범위밖UNKNOWN; vault잠김/없음/취소·계정분리·회전동시성·재시작 | 기존일정/시계/credential/OAuth/Drive검사에직접영향범위만. 원시각·비밀/옛자료일괄이전없음 |

동일주문·유료분석·쓰기요청을기존/후보 양쪽에 실제전송하는A/B는하지않는다. 고정입력·로컬응답fixture·기존실제격리SQLite/PG로비교한다. timeout/기대값/skip로실패를숨기지않는다. 정확한기존ID는실행시점의증분도구로선정하고, 현재에미리실행통과로기록하지않는다.

**롤백:** 이전 adapter와역할별의존구성으로코드를되돌려도이미수락한주문/ACK·BODY·연구판본·DB가자동취소되지는않는다. 기존/신규불변자료reader와필요제거조건을명시하고영구이중상태/중복전송을남기지않는다. 과거원본·hash·판본재작성은금지다.

**신규 의존성 정책 제안:** D승인 기능의 소비역할에만 직접선언·검증된 정확버전/기존lock·wheelhash/실제설치manifest를함께고정한다. 전이dependency·3OS/ARM·LICENSE/NOTICE·공식보안공지/의존충돌을확인하며이미설치됐다고생략하지않는다. 자동latest 업그레이드나lock전체재생성은하지않는다. 보안/bugfix는해당기능의관련검사후수동판단,major는별도전환계획으로관리한다. 기존CI/가드·증분검사/배포notice를재사용하며별도상시전수감사·대형관리시스템은만들지않는다. **이번은후보의공식metadata/문서검토이지전체dependency의CVE무결함감사가아니다.**

## 8. 장기 비용 효과와 최종 판단

| 기대 효과 | 현재 근거 | 아직 입증되지 않은 것 |
|---|---|---|
| OS별관측유지부담감소 | RSS직접구현2모듈·macOS공백을기존측정계약에서외부화가능 | 순코드삭제량/속도/RSS/장애율개선. 영속identity이전은추가비용 |
| 자료폴더규칙외부화 | AppPaths의Windows기본계산한경계만바꿀수있음 | 새OS실제권한/restore/기기인계수락. 로컬SSD보장아님 |
| 전송/검증중복축소 | 여러HTTPclient·서버dict shape의기계적부분존재 | 필요없는경로제거후절감량과pool총비용. 새로운relay만생기면기각 |
| 포맷변형대응외부화 | 수동HTML깊이추적·XLSX셀읽기를범용파서에맡길수있음 | 한국어정확도·전체메모리·최근유지보수/TCO; 작은40행reader보다비쌀수있음 |
| 불필요전환회피 | native/주문·캐시/큐·표준기능·과학판본은라이브러리로없어지는책임이아님 | 근거없는인시/비용/성능절감률은제시하지않음 |

**A단계 결론:** psutil RSS·platformdirs·유지할 읽기 HTTP와 서버 Pydantic 재사용을 우선 비교 대상으로 남긴다. HTML/XLSX/settings/달력/keyring은 조건부이며 Alembic·ORM·serializer·scheduler·logging·연구계산 전면전환은 현재 유지/보류다. B/C/D 및 실제신규의존성·제품·환경전환은아직승인/실행되지않았다. 이 보고서로 기존 AR/F의 구현·검증 완료 건수를 올리지 않는다.

<a id="data-stack-supplement"></a>
## 9. 새 목표 배치의 저장·전송·연구 조회 추가 감사

확인일 **2026-10-30 KST**, 추가 A단계다. 기존1~8절의 OS/HTTP/파서 후보는 재조사하지 않고 재사용했다. 이번에는 SQLite/PostgreSQL 기준에서 **Parquet·PyArrow·DuckDB·Polars와 실제 파일 운반 도구**의 누락 범위만 보완했다. 아래는 분석/계획이며 설치·교체·benchmark·DB/schema·환경/운영 변경은0이다. 기존 AR 완료 조건·완료76/잔여14·하위39는 변경하지 않는다.

### 9.1 현재 경로·설치 상태를 기준으로 한 결론

권장 비교 출발점은 **장중 row store + 장후 불변 자료 + 연구용 선택 reader**다. 모든 자료를 한 엔진/파일에 합치거나 네 패키지를 모두 도입하라는 제안이 아니다.

```text
기존 수집/판단 owner → RAM·기존 로컬 native DB
                     → ACK된 일관된 cut → 검증된 불변 산출물
                     → 파일 운반 → NAS 내용 검증/원자 수락·첫 receipt
                     → 고정 manifest의 필요한 입력 → 로컬 연구 reader → 기존 계산
```

원본·순서·판본·가용시점과 native 수락은 기존 앱이 소유하고, 범용 파일 인코딩/운반/scan은 OSS에 맡길 수 있는지 비교한다. NAS 단절이 새 컴퓨터의 주문 권한을 만들지 않으며 일일 archive 복사와 실제 실행 인계도 다르다.

| 실제 현재 코드·근거 | 유지할 의미 / 새 후보의 경계 |
|---|---|
| [0B 수신](../../src/kiwoom_monitor/central_server/realtime_collector.py#L1077), [RAM 관측](../../src/kiwoom_monitor/central_server/realtime_collector.py#L951) | callback은 Hub/RAM/latest·분봉을 갱신하며 DB는 별도 저장 루프다. RAM은 시간/최대10,000건·gap·durable=False. **원시0B 전량 영구보관은 현 기능이 아니다.** |
| [flush/재시도](../../src/kiwoom_monitor/central_server/realtime_collector.py#L1251), [분봉 native](../../src/kiwoom_monitor/central_server/database_market_bars.py#L1006) | 1초 loop·flush lock·owned native await·실패 operation ID/대기 payload 보존. SQLite/PG는 중복 operation·권위·delta/revision/metadata를 같은 transaction으로 수락. Parquet writer로 대체할 의미가 아님 |
| [SQLite/PG 연결](../../src/kiwoom_monitor/central_server/database.py#L615), [PG live dataset 정책](../../src/kiwoom_monitor/central_server/database_datasets.py#L146) | SQLite WAL/busy timeout과 호출별 연결, PG도 호출별 연결. 일부 ranking/market_state/new_high/program_flow/membership만 async commit. 모든 시세/주문 저장의 공통 설정이 아님. NAS 왕복 제거와 엔진/연결 비용 개선을 분리 비교 |
| [같은 중앙 store](../../src/kiwoom_monitor/central_server/database.py#L529), [local server DB 전달](../../src/kiwoom_monitor/infrastructure/central_server_process.py#L54) | desktop의 monitor/news/journal 분리와 달리 중앙 market/news/execution은 같은 SQLiteStore의 lock/file을 공유할 수 있음. Parquet나 로컬 URL 선택만으로 혼합부하 경합이 해결됐다고 하지 않음 |
| [순위 snapshot/export](../../scripts/export_preserved_rank_history.py#L141), [게시](../../scripts/export_preserved_rank_history.py#L469) | SQLite live WAL/PG repeatable snapshot, 관계·원 sequence·JSON·hash·stream/fsync·검증 뒤 manifest 게시를 이미 구현. 전면 재작성보다 source cut/publication을 재사용 |
| [순위 import](../../src/kiwoom_monitor/central_server/database_rank_import.py#L211), [ACK-loss 확인](../../scripts/import_preserved_rank_history.py#L254) | 빈 offline target의 일회성 초기 반입이다. 누적 archive importer로 그대로 사용 불가. 실제 owner 종료 후 정확한 첫 receipt 확인·충돌 거부는 재사용 |
| [연구 입력](../../src/kiwoom_monitor/infrastructure/research_data_source.py#L253), [로컬 reader](../../src/kiwoom_monitor/infrastructure/research_data_source.py#L338) | 공개 observation export의 fixed watermark/membership·연속 ordinal·revision hash 검증. 현재 전체 bytes/list/tuple을 만들므로 scan만 바꾸면 전체 peak RSS가 줄어든다는 보장은 없음 |

현재 root 개발환경 metadata/선언을 읽어 확인한 결과 PyArrow·DuckDB·Polars·Paramiko·s3fs·ADBC는 미설치/미선언이다. fsspec2026.7.0만 lock/root에 있지만 제품 직접 의존성은 아니다. Windows PATH에는 OpenSSH의 ssh/scp/sftp가 있고 rsync/rclone/duckdb는 발견되지 않았다. 전체 디스크/다른 환경의 설치 부재를 뜻하지 않는다. WSL을 새로 기동하거나 NAS 서비스·권한·데이터/API를 조회하지 않았다.

### 9.2 저장/분석 후보의 역할·안전·비용 비교

| 대상 | 적합한 후보 역할 | 정확성·장애/운영 제한 | CPU·메모리·공간·유지보수 평가 |
|---|---|---|---|
| SQLite | 장중 로컬 최신 상태·분봉/revision·주문/복구/receipt의 기존 row store | PK/transaction/native guard 재사용. WAL은 network filesystem 공유 운영 모델이 아님. 활성 본체 한 파일 복사는 일관된 backup이 아님. WAL과 synchronous/실제 fsync 조건을 구분 | 서버 설치 부담이 작으나 같은 file writer/lock·checkpoint·뉴스 혼합부하는 비교 필요. 중앙 연결은 FULL을 명시 강제하지 않아 실행환경 내구성을 따로 확인해야 함. 새 SSD의 실제 성능 미측정 |
| PostgreSQL | 기존 NAS catalog·수락/조회·결과, 필요한 경우 실행 컴퓨터의 row store 대안 | native claim/CAS/순서/원자성 유지. `synchronous_commit OFF`의 성공 반환을 영구 보관 확인으로 사용하지 않음 | 같은기기 PG여도 connection/transaction 비용과 서버 운영이 남음. SQLite보다 빠르다는 근거 없음. 로컬/원격 PG·durability를 분리 비교 |
| Parquet 형식 | 장후 고정된 분봉·순위/관측 판본·연구 입력의 불변 분석 파일 | per-record update·관계 transaction·작업 claim·주문 ACK가 없음. 파일/manifest publication을 앱이 보장해야 함 | 열 선택·통계/partition skip·compression으로 읽기량/공간이 줄 가능성. 작은파일·압축 CPU·metadata/footer·정정/compaction 비용이 생김. 실제 절감 미측정 |
| PyArrow | Parquet writer/reader·명시 typed schema·RecordBatch/Dataset I/O | Dataset API는 ACID/transaction을 제공하지 않고 write 반환이 일반적으로 OS page cache 기준. thread/write 옵션이 행순서/기존파일을 바꿀 수 있음 | C++ import·buffer/read-ahead·encoding/decoding 비용. Parquet mmap이 압축해제 메모리를 없애지 않음. pandas를 필수 처리층으로 넣을 필요는 없음 |
| DuckDB | 고정 파일 집합의 로컬 SQL scan·열/범위 선택·join/집계 | 선택한 in-process 모드는 single-writer process 또는 writer 없는 다중 reader. NAS 공유 writable DB를 기본으로 삼지 않음. 별도 서버/DuckLake는 다른 구조이며 이번 후보에서 제외 | SQL/Parquet pushdown·spill 가능하지만 memory_limit가 process RSS 상한은 아님. thread·spill SSD/handle 비용 포함. 기존DB도 조건/정렬/page를 쓰므로 무조건 개선 아님 |
| Polars | typed columnar 데이터의 lazy scan·필요 열/행 선택·batch 변환 | `read_parquet().lazy()`는 이미 전량 읽은 뒤다. streaming 미지원 연산의 in-memory fallback·최종 collect/DTO 변환이 남음 | native thread와 기존 연구 worker 중복, buffer/cast 비용 포함. streaming이 OOM 방지를 보장하지 않음. 기존 scientific 계산/정책을 DF 기본값으로 바꾸지 않음 |

근거: [SQLite WAL/동시성](https://sqlite.org/wal.html), [PG async commit](https://www.postgresql.org/docs/current/wal-async-commit.html), [Arrow Dataset의 보장 한계](https://arrow.apache.org/docs/python/dataset.html#a-note-on-transactions-acid-guarantees), [DuckDB concurrency](https://duckdb.org/docs/2.0/connect/concurrency), [Polars streaming](https://docs.pola.rs/user-guide/concepts/streaming/).

**선택안:** row store를 유지한 채 기존 snapshot에서 파생 Parquet를 만드는 제한 비교부터 판단한다. PyArrow가 실제 필요한 writer인지, DuckDB 또는 Polars 단독 I/O로 충분한지는 API/동일입력 비용으로 비교한다. DuckDB+Polars+PyArrow 모두를 같은 데이터의 필수 중간층으로 만들지 않는다. NAS가 파일 보관만 하면 세 엔진을 NAS에 설치할 필요도 없다.

### 9.3 공통 OS와 새 native 의존성의 누락 범위

기존1~3절의 OS owner/AppPaths·프로세스 신원/RSS·자격증명 분류는 그대로 재사용한다. 아래는 새 데이터 스택에만 추가되는 차이다. 배포/wheel 게시·공식 지원 선언은 이 앱의 실수신·저장·종료·복구 수락이 아니다.

| 후보·공식 확인판 | 라이선스/Python | OS·아키텍처 및 유지보수 확인 |
|---|---|---|
| Parquet 규격 / PyArrow26.0.0(2026-10-09) | Apache-2.0 / PyArrow≥3.11·CPython3.13 | cp313 Windows x64, macOS12+ x64/ARM64, Linux glibc2.28+/musl1.2+ x64/ARM64 파일 확인. 해당목록에서 Windows ARM64 wheel은 확인 못함. 포함 codec/native LICENSE/NOTICE와 선택OS 조합은 별도 확인. [공식 배포](https://pypi.org/project/pyarrow/26.0.0/)·[release](https://arrow.apache.org/release/26.0.0.html)·[설치](https://arrow.apache.org/docs/python/install.html) |
| DuckDB2.0.0(2026-10-21) | MIT / ≥3.11·일반3.13, free-threaded 미지원 | 2.0LTS 최소2027-10까지 지원 안내. Win10+ x64/ARM64, Linux x64/ARM64, macOS12+ ARM64. macIntel은2.0 partial/2.1 지원중단예고이므로 모든Mac 일반지원으로 쓰지 않음. [배포](https://pypi.org/project/duckdb/)·[공식지원표](https://duckdb.org/docs/2.0/operations_manual/installing_duckdb/requirements)·[LTS](https://duckdb.org/2026/10/21/announcing-duckdb-200) |
| Polars2.1.0(2026-10-25) | MIT / ≥3.11·3.13 | wrapper 외에 polars-runtime-32 native가 필요. cp311-abi3 Win x64/ARM64, mac x64/ARM64, Linux glibc/musl x64/ARM64 확인. legacy CPU/64bit index/rtcompat는 별도 구성. [배포](https://pypi.org/project/polars/)·[runtime](https://pypi.org/project/polars-runtime-32/2.1.0/)·[설치](https://docs.pola.rs/user-guide/installation/) |

Windows 파일 handle/mmap은 실제 close를 확인한 뒤 이동/정리해야 한다. POSIX rename/permission, Windows ACL·대소문자, macOS Unicode 파일명, WSL 경로·권한이 같다고 가정하지 않는다. 업무 시각/종목 identity는 파일 mtime·파일명 비교로 복원하지 않는다. Arrow C Stream/PyCapsule은 같은 process의 batch 교환 계약이며 NAS 전송/원격 공유 RAM/영속 ACK가 아니다. 선택 API에 따라 PyArrow 없이 Arrow 호환 교환이 가능하므로 dependency를 자동 추가하지 않는다. [Arrow C stream](https://arrow.apache.org/docs/format/CStreamInterface.html)·[Polars Arrow](https://docs.pola.rs/user-guide/misc/arrow/)·[WSL 권한](https://learn.microsoft.com/en-us/windows/wsl/file-permissions).

### 9.4 파일 전송 후보와 앱의 수락/복구 책임

**파일 운반 완료 → 내용/업무 원자 수락 → 실행 권한 인계는 별개다.** 기존 [연구 결과 배송](../../src/kiwoom_monitor/research_result_delivery.py#L112)의 동일 source/target pin·응답불명 때 첫 receipt 확인·로컬 immutable ACK와 [동기화 delta](../../src/kiwoom_monitor/infrastructure/central_content_sync.py#L291)를 필요한 범위에서 재사용한다. 단순 `saved` 수량 ACK나 TOP20 key별 최신값 outbox를 전체 archive queue로 승격하지 않는다.

| 도구·공식판/라이선스 | 실제 후보 역할과 조건 | 안전/효율·유지보수 판단 |
|---|---|---|
| 기존 OpenSSH SFTP | 현재Windows client 존재. NAS쪽 기존SSH/SFTP 서비스·권한·extension은미확인. [공식sftp](https://man.openbsd.org/sftp.1) | 새패키지없는C비교 baseline. resume는같은원본prefix 전제, fsync는서버extension 조건. 로컬명령존재만으로NAS전송구성완료가아님 |
| rclone1.75.2(2026-10-09), MIT | Win/macOS/Linux/ARM64 native binary, Python판본과별개. 불변파일의 `copy` 운반만조건부C우선후보. [배포](https://rclone.org/downloads/)·[license](https://rclone.org/licence/)·[copy](https://rclone.org/commands/rclone_copy/) | `copy`는대상여분파일을삭제하지않음. 기본partial실패삭제/rename은byte-resume·batch원자성·fsync보장이아님. `sync/move/--delete`·새daemon·원본삭제는제안범위밖. checksum부재시다시download검증비용도포함 |
| rsync3.5.1(2026-09-21), GPLv3계열 | 양쪽rsync필요. Linux/macOS↔NAS 준비된POSIX환경에서조건부. Windows는공식안내가Cygwin/cwRsync를제시하며WSL등계층이추가됨. [배포](https://rsync.samba.org/)·[manual](https://rsync.samba.org/ftp/rsync/rsync.1.html)·[Windows](https://rsync.samba.org/download.html) | block delta/partial 재사용가능하지만새불변압축파일에서도유리한지는미측정. `--delay-updates`는순차파일rename, `--fsync`는개별완료파일범위라업무batch COMMIT이아님. UID/GID/ACL복제를공통전제로두지않음 |
| Paramiko5.1.0(2026-10-18), LGPL-2.1 | Python≥3.9·3.13/pure wrapper. bcrypt·cryptography·invoke·PyNaCl backend별검증필요. [배포](https://pypi.org/project/paramiko/)·[SFTP API](https://docs.paramiko.org/en/latest/api/sftp.html) | Pythonowner 안의진행률/취소·연결수명제어가실요구일때만조건부. `put(confirm=True)`는크기stat확인이지content hash/수락아님. 신뢰hostkey·재시작/secret노출·종료계약이남음 |
| fsspec공개2026.9.0 / 현lock2026.7.0, BSD-3-Clause | ≥3.10·3.13/pure wrapper. SFTP는Paramiko추가. [배포](https://pypi.org/project/fsspec/)·[transaction](https://filesystem-spec.readthedocs.io/en/stable/features.html) | transaction은semi-atomic/backend별. 현lock의SFTP소스는AutoAddPolicy·원격/tmp·파일별posix_rename이므로새native수락대체불가. 단일NAS운반에추상화층을추가할실익불충분, 현재보류 |

rclone1.75.2 SFTP의 실제 Move는 posix-rename extension이 없으면 대상 Remove→Rename fallback이 있고, Update는 ReadFrom→Close로 명시 fsync를 확인하지 못했다. SFTP hash는 원격 shell/hash 명령 조건이며 지원이 없으면 추가 read/download 비용이 생긴다. 일반 종료코드0·rename 성공만으로 내구성 ACK를 만들면 안 된다. [고정판소스](https://raw.githubusercontent.com/rclone/rclone/v1.75.2/backend/sftp/sftp.go)·[partial 동작](https://rclone.org/docs/#inplace)·[hash/경로](https://rclone.org/sftp/)·[추가검증비용](https://rclone.org/commands/rclone_check/). fsspec의 이 판단도 [현lock 소스](https://raw.githubusercontent.com/fsspec/filesystem_spec/2026.7.0/fsspec/implementations/sftp.py)에 한정한다.

도구가 맡을 것은 검증된 immutable 파일의 복사다. source cut·batch ID/stream·범위·schema/파일목록/hash·정정 관계·중복/충돌·첫 receipt·미전송 상태·retention은 기존 앱/native 책임이다. NAS 수락 전 로컬 원본을 지우지 않고, 복사한 lease/control을 권한으로 사용하지 않는다. 운영 NAS의 서비스 활성화나 SSH키/권한 변경을 이번에 요구·실행하지 않았다.

### 9.5 연구 조회에서 반드시 유지할 의미

1. **고정 membership·PIT:** [현재 export](../../src/kiwoom_monitor/central_server/database_research_export.py#L154)의 watermark/연속ordinal/revision ID 집합과순서hash를고정한다. 후보가현재수정된파일목록을임의로다시glob하면안된다. `max(sequence)`도안전한처리prefix가아니다. 예를들어11은commit됐지만10이아직진행중인snapshot에서11만cursor로진전시키면나중commit된10을놓칠수있다. 기존export의committed행/membership과원owner의처리/수락cut을구분해재전송·누락검증한다. 기존 `/research/observations`는watermark없는첫GET가 [export를생성](../../src/kiwoom_monitor/central_server/research_read_routes.py#L141)하므로DB쓰기가있다. 이번에는호출하지않았다.
2. **최신판본 다음 품질 판정:** [현재replay](../../src/kiwoom_monitor/application/research_replay.py#L111)는당시available cut에서관측key별최신revision을고른뒤완결성등을검사한다. 새SQL/Polars에서complete행을먼저필터하면최신불완전정정이숨긴옛정상봉이되살아난다. predicate pushdown의범위는warmup/전일·이전universe seed/가용시점까지보존해야한다.
3. **명시schema:** 종목코드의선행0·정수원/주/단위·NULL/UNKNOWN/품질·original/effective/received/available 시각·revision/source/sequence를보존한다. UTC/KST는같은순간으로비교하고파일mtime·자동timezone추론이나9시간재가산을쓰지않는다. Parquet UTC표현/Arrow timezone metadata/문자열원본을구분한다.
4. **정밀도/실패:** Polars `strict=False`가잘못된값을null로바꾸거나 `to_dicts`가ns를μs로줄이는동작, DuckDB timestamp유형차이를확인한다. 정수/Fraction·bps절사/올림·사건순MDD·과학적판단은기존구현유지. [Polars casting](https://docs.pola.rs/user-guide/expressions/casting/)·[Python변환](https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.to_dicts.html)·[DuckDB시각](https://duckdb.org/docs/2.0/sql/data_types/timestamp).
5. **물리hash와논리identity:** 압축/row-group/writer판본/metadata/compaction으로파일bytes가달라질수있다. 전송파일SHA와기존payload/input/revision hash를별도로유지한다. 명시파일목록·footer/schema·행관계도검증하며같은row수만맞추지않는다. 이미수락된원본문/hash/판본을일괄변환하지않는다.
6. **reader도과학판본:** [scientific raw20](../../src/kiwoom_monitor/domain/research_contract.py#L25)에research_data_source/research_replay/research_run이포함된다. reader만수정해도원bytes가달라지면새구현identity다. 기존선택artifact/hash/과거결과를그대로재표지하지않고별도C후보로비교한다.
7. **수명/권한:** DuckDB extension은autoload/직접DB접근능력이있으므로NAS내부PG/공유SQLite에임의attach하거나새extension을자동설치하지않는다. 기존공개조회·고정입력경계와단일writer를유지한다. reader/connection/Arrow buffer·mapping·spill파일실제종료를확인한다. [SQLiteextension](https://duckdb.org/docs/2.0/core_extensions/sqlite)·[PGextension](https://duckdb.org/docs/2.0/core_extensions/postgres/overview).

### 9.6 후속 C 비교·복구 계획 — 모두 미실행

| 비교 축 | 같은 입력에서 확인할 결과/비용 | 중단·복구 조건 |
|---|---|---|
| 장중 저장baseline | 로컬SQLite/로컬PG/원격PG를구분, 같은durability·시장단독/뉴스혼합/주문control별로실제연결·SQL/COMMIT·queue/flush 지연·CPU/peakRSS·handle을측정 | 중복operation/원revision·query COMPLETE권위·ACKloss·flush취소/close/재시작. 새SSD/엔진효과와NAS왕복제거를분리 |
| columnar생성 | 기존frozen cut/JSONL과후보Parquet의같은원자료·순서/관계·시각/판본을검증. Snappy/ZSTD/무압축·partition/rowgroup은가설로비교 | writer/close/footer/검증/manifest공개전후kill·파일잘림·동일ID다른내용·늦은정정. 미검증파일은수락manifest에미포함 |
| NAS파일운반 | 같은산출물로기존SFTP와rclone을우선비교, rsync는양쪽환경조건이이미맞을때만추가. 재전송bytes·hash재읽기·압축CPU·전체시간/RSS를포함 | 부분전송/같은크기내용변경/rename전후/수락전후/ACKloss/로컬ACK기록실패. 같은첫receipt로만pending해소,불명확하면UNKNOWN·원본보존 |
| 연구reader | 현재native/JSONL, DuckDB단독, Polars단독부터비교. 변환준비/무결성/반복scan/DTO·전체연구까지측정 | 늦은불완전정정·동시각tie·gap·NULL·큰정수/Decimal·UTC/KST동일순간·warmup/seed. 기존독립assertion/논리결과가유지돼야함 |
| 전체자원/OS | nativeimport 포함peakRSS·threads·읽은bytes/row-group·read-ahead·spill SSD·동시worker·Windowshandle·mac/Linux 실제역할별수명 | memory_limit/streaming만으로RSS상한/OOM불발을가정하지않음. 취소·강제종료·재시작/cleanup과기존guard유지 |

기존 고정 fixture/receipt·허용된 복사본을 우선 사용하고 새 export 생성/실제 NAS 부하·배포는 별도 범위로 확인한다. `tracemalloc`만으로 native 메모리 절감을 판단하지 않는다. [Arrow writer](https://arrow.apache.org/docs/python/generated/pyarrow.dataset.write_dataset.html)의기본preserve_order=False/overwrite옵션은batch멱등성이아니며, [DuckDB자원](https://duckdb.org/docs/2.0/guides/performance/how_to_tune_workloads)·[Polars lazy](https://docs.pola.rs/user-guide/concepts/lazy-api/)의한계까지비교한다. 이미Parquet압축된산출물에추가전송압축/delta가유리하다고가정하지않는다.

기존 `test_export_preserved_rank_history`·`test_import_preserved_rank_history`/실제PG대응·`test_research_data_source`·`test_research_observation_history`·`test_research_resources`·`test_research_bundle_execution`·`test_minute_bar_revisions`·`test_research_result_delivery`·Collector/동기화/outbox 검사를 재사용해 실제 변경 시점에 선정한다. 이번에는 목록/본문을 확인했을 뿐 실행하지 않았다. 새형식·전송차이를구분못하는부분만후속보강하고기대값/시한/skip로실패를감추지않는다.

### 9.7 유지/OSS 위임 판단과 v1 연결

- **앱에 유지:** native 거래/수락·source cut·fixed membership·PIT/최신revision·원순서·해시판본·계좌권한·첫receipt·부분성공/ACK복구·종료/미전송보존. 단순범용wrapper가아닌고유계약이다.
- **OSS 비교 가능:** 불변파일의columnar encoding/scan·필요열/행의물리적읽기·검증된파일의운반. 새Manager/서비스/상주큐를먼저만들지않는다.
- **우선 C 후보:** 기존rowstore유지 + 시장자료한종류의Parquet파생산출물 + 기존SFTP/rclone transport비교 + DuckDB또는Polars reader하나. 서로다른변경으로검증하며전체스택동시교체를피한다.
- **현재 제외:** Parquet/DuckDB로주문·실시간claim DB교체, 공유NAS writable DB·권한복사·자동failover, 분산lakehouse/별도queryserver·새recording시스템·원시0B전량저장확대.
- **완료기준 불변:** AR03입력/flush, AR04native저장, AR05archive/인계, AR07연구/주문, AR08설치/수명, AR09선택기능의기존기준을추가완료로계산하지않는다. F02~F13/F15~F19/F21~F25는후보영향범위이지새연결수락이아니다. 기존1~8절과정본11.72의승인·운영제한을유지한다.

**추가 A 결론:** 장중에는 기존 SQLite/PG native 저장을 우선 유지하고, 장후 불변 데이터 형식과 연구 읽기·파일 운반을 분리해 OSS 활용을 검토할 가치가 있다. 안전성·공간·CPU/RSS·전송량·유지보수의 실제 우위는 아직 측정하지 않았다. 소스 버전/기존 근거를 재사용한 누락 보완이며 신규 도입·bench·schema·운영 전환 승인이나 AR 완료 조건 변경이 아니다.

<a id="system-wide-supplement"></a>
## 10. 전체 목표 시스템의 누락 기술 추가 탐색

**범위 확장(2026-10-30):** 특정 제품 목록이나 TOP10 개수에 맞추지 않고 공통 실행·실시간 처리·로컬 저장·시계열·NAS 전송/복구·과거 조회·뉴스·자동매매·연구 역할을 대조했다. 1~8절의 범용 기능 감사와 9절의 파일/조회/전송 조사는 재사용하고, 아래 누락된 선택지만 본문·호출/소비 계약·공식 자료로 보완했다. 전체 코드의 새 전수감사나 세계의 모든 라이브러리 조사를 완료했다는 뜻은 아니다. 현재 소스165·기존 완료/실패·미통합 후보는 그대로다.

**판정:** 새로운 상주 DB/브로커/연구 플랫폼을 묶어 도입할 근거는 없다. 기존 native 저장·공개 입력/결과·작업 수명은 재사용하고, 대량 반입/읽기의 기존 DB 기능, 검증된 파일의 형식·운반·백업, 역할별 설치 재현성을 각각 비교하는 것이 유리할 가능성이 크다. 속도·압축률·메모리·CPU·장애율·인시 절감은 모두 **미측정**이다. vendor benchmark나 wheel 크기는 본 앱의 효과 측정값이 아니다.

### 10.1 기능별 최종 비교와 도입 우선순위

아래 순위는 **AR 종료 후 별도 승인할 비교 순서**이며 AR 구현 우선순위·완료 조건을 바꾸지 않는다. **유지**=기존 구현 재사용, **비교1**=구체적인 기존 경계에서 좁게 비교할 가치, **조건2**=실제 요구/비용 근거가 더 필요한 후보, **보류/비채택**=현재 도입하지 않음. 조건부 항목을 의무 과제로 추가하지 않는다.

| 기능 | 현재 방식 | 활용 가능한 후보 | 추천 대안 | 기대 효과·미측정 | 주요 위험·추가 비용 | 우선순위 |
|---|---|---|---|---|---|---|
| 3OS 공통 엔진·기동 | REST/WSS·asyncio·headless CLI, Qt/OS adapter 분리 중 | 기존 Python/websockets/FastAPI/PyInstaller, uv·pip-tools | 공통 엔진·기존 CLI 재사용, 설치 도구는 둘 중 하나만 비교 | 역할별 불필요 설치·잠금 관리 수작업 감소 가능 | 기존 Windows lock은 3OS lock 아님; native/ARM·서명·child 종료는 별도 | 유지, 설치 조건2 |
| 실시간 수신·RAM·backpressure | Hub callback/구독별 bounded queue·gap·generation·flush drain | 기존 asyncio, AnyIO·uvloop·PyZMQ·NATS/JetStream·Redis Streams | 현재 queue/owner 유지 | 새 daemon·network hop·중복 상태를 피함; 가속 수치 없음 | broker ACK와 DB/주문 수락 다름; 새 로그·소비자·인증·재전달 수명 | 유지; 새 broker 비채택 |
| 장중 로컬 SSD·주문/분봉 저장 | SQLite/PG native transaction·revision·operation dedup | sqlite3·psycopg, APSW·LMDB·RocksDB/RocksDict | 기존 rowstore 우선 | 기존 정확성·복구/조회 재사용, 엔진 교체비용 억제 | KV로 바꾸면 보조색인/관계/CAS 이식; durability 완화 금지 | 유지; 대체 엔진 보류 |
| 남길 PG 경로의 연결 | 호출별 connect→transaction→close | psycopg_pool | 실제 연결 비용·실행 위치 확정 뒤만 비교 | 연결 생성/준비 비용·연결 수 감소 가능 | borrower drain·GUC/시계·예외 후 반납·pool worker 소유권 추가 | 조건2 |
| 장후 대량 NAS 반입 | 512행 executemany·검증·첫 receipt; 현재 순위 importer는 빈 target 전용 | 기존 psycopg COPY FROM STDIN | 향후 누적 수락 설계 안에서 COPY와 기존 batch 비교 | protocol/행 변환 부담 감소 가능, 새 패키지 없음 | staging/검증/UPSERT 비용; COPY만으로 멱등/receipt 해결 안 됨 | 비교1 |
| 대량 연구 export의 메모리 | 일부 named cursor, 관측 manifest는 fetchall+ID/membership 목록 | 기존 ServerCursor·bounded 처리 | 기존 묶음 읽기를 end-to-end 재사용 | 중간 목록/RSS 감소 가능 | 같은 snapshot·정렬/hash·membership 보존, 장기 transaction 비용 | 비교1 |
| 장기 PG 시간 범위 조회 | PK/UNIQUE·B-tree·종목/시장/날짜 범위 | BRIN·선언형 partition·TimescaleDB | BRIN부터 조건부, partition/extension은 후순위 | 읽는 페이지·index 공간 감소 가능 | 늦은 정정의 물리 상관성·global UNIQUE·DDL/extension 복구 | 조건2 |
| 고밀도 시계열 전용 DB | 현재 필수 계약은 raw tick 전량 archive가 아님 | QuestDB·InfluxDB3 Core | 현재 native 유지 | 특화 ingestion/ASOF의 이점은 해당 workload에서만 가능 | revision 교체/다중 table 원자성·새 서버/복구·제품판 한계 | 현재 비채택 |
| 장후 불변 자료·단일 연구 조회 | fixed manifest+JSONL/native reader | Parquet/PyArrow·DuckDB·Polars | 9절의 자료 1종·reader 1개 비교 재사용 | 열/범위 pruning·압축·Python 객체 감소 가능 | logical hash/순서/PIT·native RSS·spill·footer/게시 내구성 | 비교1, 기존 조사 재사용 |
| 대형 다중 독자 조회 서비스 | 공개 query/고정 연구 입력; 별도 분석 서버 없음 | ClickHouse·chDB | 파일 reader로 설명 못 하는 반복 scan 병목이 있을 때만 검토 | 장기 aggregate·동시 독자 이점 가능 | daemon/merge·별도 projection; chDB Windows native 공백 | 보류 |
| archive 판본·catalog | source cut·불변 manifest·ordered membership | Delta/delta-rs·Iceberg/PyIceberg·DuckLake | 현재 manifest 유지 | 다중 writer/도구의 snapshot·schema 관리가 필요할 때 유용 | catalog/파일 동시 보관·유지관리·vacuum/expiry·원자 게시 | 보류 |
| 배열/과학 자료 저장 | 관계형 revision·뉴스·receipt, 일반 Python 연구 입력 | HDF5/h5py·Zarr·ArcticDB | N차원 배열 소비자 없으면 도입 안 함 | 배열 slice/chunk 이점은 현재 미입증 | 관계/PIT 재구현; ArcticDB 현판은 production 제한 라이선스 | 현재 비채택 |
| 장후 운반·수락·재시도 | 불변 파일/검증·native receipt/ACK 기반 | 기존 OpenSSH SFTP·rclone, rsync·Paramiko·fsspec | 9절 transport 비교 재사용 | 전송 구현 부담·재전송량 감소 가능 | 전송 exit0/rename이 영속 수락 아님; 두 번째 동기화 원장 금지 | 비교1, 기존 조사 재사용 |
| 별도 백업·재해 복구 | SQLite Online Backup·Drive manifest·strict restore/before-image | restic·Kopia·Borg | 일관된 산출물 1종의 restic 비교, GUI/정책 필요시 Kopia 대안 | 암호화·중복제거·버전 보관 직접 구현 감소 가능 | 읽기/hash CPU·cache·키 회복·검증/restore 비용; prune 별도 승인 | 비교1; Borg 후순위 |
| 뉴스 수집·본문 | 외부 NAVER API/기존 BODY parser·revision/claim | 기존 HTTP/파서, lxml·Trafilatura | 1~8절의 좁은 parser/전송 후보 재사용 | 범용 HTML 처리 유지비 감소 가능 | 본문/발행 근거·처리판본·유료 재분석 정책 | 기존 조건부 유지 |
| 뉴스 RULE literal 탐색 | 작은 term-in-text 사전·re·문맥/회사/부인 판정 | pyahocorasick·ahocorasick-rs | 다중 literal 탐색 비용이 확인될 때 하나만 비교 | 같은 본문 반복 scan 감소 가능 | 사전 구축/RAM·overlap/순서·evidence span·regex와 의미 차이 | 조건2 |
| 뉴스 grouping·검색 | identity/종목/시각 SQL, 48h 사건 충돌+SequenceMatcher | RapidFuzz·FTS5·PG FTS/pg_trgm | 현재 조회/identity 유지; 새 본문 검색 요구 때 파생 index | 현재 SQL의 가속 근거 없음; 검색 요구 생길 때 후보 | 유사도/한국어 token 차이·오그룹→자동 AI 대상 변경·색인 쓰기 | 현재 보류 |
| 자동매매·체결·Risk | Paper/모의 실행·fresh control·native checkpoint·계좌 owner | Backtrader·Backtesting.py·VectorBT | 기존 업무/안전 엔진 유지 | 전면 이식·두 엔진 유지 부담 방지 | OHLC 추론/float 비용·UNKNOWN/부분 FILL·실주문 권한과 불일치 | 엔진 교체 비채택 |
| 연구 파라미터 탐색 | 작은 결정적 grid·seed·native trial/예산·baseline/OOS | Optuna sampler ask/tell | 탐색공간 확대가 확정되면 제안 부분만 비교 | 범용 탐색 알고리즘 재구현 감소 가능 | 표본/후보열·병렬 재현·pruning은 연구방법 변경; 두 원장 위험 | 조건2 |
| 연구 수치 계산 | Fraction/Decimal·정수 bps·사건순 MDD | SciPy·Numba | 새 통계 요구 또는 입증된 순수 수치 kernel만 | 직접 통계 구현/JIT 계산비 감소 가능 | native/JIT cold start·큰 dependency·overflow/반올림/시각 의미 | 요구 전 보류 |

### 10.2 기존 DB 기능과 로컬 KV 대안

현재 [PG 연결](../../src/kiwoom_monitor/central_server/database.py#L736)은 호출별 KST 세션을 만들고, [관측 wrapper](../../src/kiwoom_monitor/central_server/postgres_access.py#L186)는 driver의 commit/rollback/close 순서를 관측한다. [native await](../../src/kiwoom_monitor/central_server/async_lifetime.py#L49)는 취소 뒤에도 실제 작업 완료를 합류한다. pool을 `_connect`에 끼우는 것만으로 반납·shutdown이 끝나지 않는다. fresh Risk/control·뉴스 claim/CAS를 캐시하거나 transaction을 합칠 근거도 아니다.

[분봉 writer](../../src/kiwoom_monitor/central_server/database_market_bars.py#L1493)는 이미 operation/권위 조회를 묶고 bounded multi-row UPSERT/키별 RETURNING을 쓴다. psycopg3.1+의 `executemany`는 지원 libpq에서 내부 pipeline을 사용하므로 **'현재 batching/pipeline 없음'이라고 판단하면 틀린다.** 실제 libpq capability/비용은 이번에 실행하지 않았다. [순위 exporter](../../scripts/export_preserved_rank_history.py#L178)·[import 검증](../../src/kiwoom_monitor/central_server/database_rank_import.py#L141)에는 named cursor도 있다. 반면 [관측 export](../../src/kiwoom_monitor/central_server/database_research_export.py#L70)는 fetchall 후 ID/hash/membership 목록을 만들어 전체 소비까지 묶음 처리할 후보가 남는다. [공식 pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html)·[cursor](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html)·[COPY](https://www.psycopg.org/psycopg3/docs/basic/copy.html).

| 새 확인 대상 | 공식판·라이선스·3OS/설치 조건 | 추천 경계와 남는 위험 |
|---|---|---|
| psycopg_pool | 3.3.3(2026-09-22), LGPL-3.0-only, Python≥3.10/pure wheel. 현 psycopg/binary3.3.6의 cp313은 Win x64·Mac x64/ARM·Linux x64/ARM; Win ARM wheel 미확인 | 연결비용 확인 뒤 borrower/drain/GUC를 통합. pool 자체가 pure여도 libpq와 worker가 남음. [배포](https://pypi.org/project/psycopg-pool/)·[pool](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)·[binary](https://pypi.org/project/psycopg-binary/3.3.6/) |
| PostgreSQL built-ins | PostgreSQL License, 기존 서버 기능. COPY·BRIN·partition은 새 Python 의존성 없음; 운영 서버 버전은 미조회 | BRIN은 물리 상관성 있는 범위에만, lossy 재검사·summary 필요. partition UNIQUE/PK는 partition key를 포함해야 하므로 global revision-ID/sequence 그대로 적용 불가. 기존 B-tree/원자 수락 유지. [BRIN](https://www.postgresql.org/docs/current/brin.html)·[partition](https://www.postgresql.org/docs/current/ddl-partitioning.html) |
| APSW | 3.54.1.0(2026-10-18/19), any-OSI, Python≥3.11. cp313 Win/Mac/Linux x64/ARM wheel; 별도 SQLite native 포함 | 전체 C API/VFS/hook이 필수일 때만. sqlite3의 backup/progress로 충분한 현 경계는 유지. 예외·transaction/context manager 차이, 같은 process에서 서로 다른 SQLite로 같은 DB 혼용 위험. 단일 writer 제약은 그대로. [배포](https://pypi.org/project/apsw/)·[차이](https://rogerbinns.github.io/apsw/pysqlite.html) |
| LMDB / py-lmdb | lmdb3.0.0(2026-10-02), OLDAP-2.8, Python≥3.9. cp313 Win x64/ARM·Mac x64/ARM·Linux glibc x64/ARM wheel | 로컬 ordered KV에는 적합하나 writer1·mmap/mapsize·장기 reader의 파일 성장 관리가 필요. 현재 SQL/관계/보조색인/원자 metadata·revision을 직접 다시 구현할 이득 미입증. sync/metasync 완화 결과를 내구성 동등 비교로 쓰지 않음. [배포](https://pypi.org/project/lmdb/3.0.0/)·[공식 API](https://lmdb.readthedocs.io/en/latest/) |
| RocksDB / RocksDict | rocksdict0.3.29(2025-12-01), wrapper MIT·엔진 고지 별도. cp313 Win x64·Mac x64/ARM·Linux x64/ARM wheel, Win ARM 미확인 | LSM/WriteBatch는 write-heavy KV 후보지만 compaction·write amplification·block cache·stall이 추가. 기본 async WAL은 OS buffer 반환이며 sync 내구성과 다름. rowstore 의미 이식·이중 저장 비용 때문에 현재 비채택. Python object pickle 기본값을 보존 형식으로 자동 채택하지 않음. [배포](https://pypi.org/project/rocksdict/0.3.29/)·[동기 쓰기](https://github.com/meta/rocksdb/wiki/Basic-Operations)·[tuning](https://github.com/meta/rocksdb/wiki/RocksDB-Tuning-Guide) |

SQLite [Online Backup](https://sqlite.org/backup.html)은 이미 [일지](../../src/kiwoom_monitor/infrastructure/persistence/journal_backup.py#L25)·[strict restore](../../src/kiwoom_monitor/infrastructure/persistence/strict_restore.py#L176)에 사용한다. 이를 새 복사 엔진으로 대체할 이유는 없다. 페이지 backup은 일일 논리 증분 archive가 아니며 live WAL 본체만 복사하면 안 된다. 선택 dataset의 PG async commit을 archive 내구성 ACK나 주문 저장으로 확대하지 않는다.

### 10.3 전용 시계열/분석 DB의 적합성과 운영 부담

[실제 분봉 조회](../../src/kiwoom_monitor/central_server/database_market_bars.py#L871)는 종목·날짜·시장/분 범위와 metadata join이고, [관측 export](../../src/kiwoom_monitor/central_server/database_research_export.py#L70)는 available_at·accepted_sequence·revision_id 순서의 membership을 고정한다. 따라서 append 처리량이나 최신행 dedup만으로 엔진을 고를 수 없다. 아래 서버 지원과 Python client wheel 지원은 서로 다른 조건이다.

| 후보 | 현재 공식 확인·지원/라이선스 | 맞을 수 있는 역할 / 현재 비채택·보류 이유 |
|---|---|---|
| TimescaleDB | 2.31.0(2026-10-20), Apache-2.0 edition과 TSL 기능 구분. Win/Mac/Linux 설치 경로 있으나 PG ABI/선택 ARM 조합 앱 수락 없음. [release](https://github.com/timescale/timescaledb/releases/tag/2.31.0)·[지원](https://www.tigerdata.com/docs/get-started/timescaledb-supported-platforms)·[license](https://raw.githubusercontent.com/timescale/timescaledb/2.31.0/LICENSE) | PG를 유지할 때 봉의 chunk/columnstore 후순위 후보. UNIQUE/PK의 partition 열·nullable 시각 및 [현재 global revision 제약](../../src/kiwoom_monitor/central_server/central_schema.py#L631) 이식 필요. continuous aggregate는 TSL이며 당시 가용 판본/PIT를 자동 재현하지 않음 |
| ClickHouse | Apache-2.0. 공식 GitHub26.10.2.103-stable과 package stable26.10.3.7/LTS26.8.36.3 채널 구분. Linux x64/ARM; Windows server는 WSL/VM/container 조건, 3OS client 지원과 별개. [packages](https://packages.clickhouse.com/)·[Python](https://clickhouse.com/docs/integrations/python) | 장기 다중 독자/반복 aggregate가 파일 reader보다 실제 유리할 때 파생 조회만. 별도 daemon·projection·merge·권한/백업 비용. ReplacingMergeTree의 background 대체는 원revision 보존이 아니며 다중 statement transaction은 experimental/조건부. [transaction](https://clickhouse.com/docs/guides/developer/transactional)·[merge](https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree) |
| chDB | 4.4.0(2026-09-11), Apache-2.0, Python3.13, Mac/Linux x64/ARM. Windows native 없음. [배포](https://pypi.org/project/chdb/) | embedded ClickHouse로 daemon은 줄지만 Windows만 다른 실행 경로가 필요. 현재 공통 reader 우선 후보에서 제외 |
| QuestDB | 서버10.1.0(2026-10-22), client6.0.0, Apache-2.0 OSS. 서버는 Intel Mac 지원 제거; client cp313 Mac x64 wheel과 혼동 금지. [release](https://github.com/questdb/questdb/releases/tag/10.1.0)·[배포](https://pypi.org/project/questdb/) | O3/ASOF·고밀도 append에 적합할 수 있으나 현재 필수 raw 전량 저장 요구 없음. DEDUP은 행 교체, HTTP ILP는 다중 table 원자 ledger가 아님. JVM/native·재정렬/복구 비용; 자동 backup/PITR 등은 Enterprise 구분. [dedup](https://questdb.com/docs/concepts/deduplication/)·[backup](https://questdb.com/docs/operations/backup/) |
| InfluxDB3 Core | 3.12.1(2026-10-14), Core MIT/Apache-2.0·Enterprise commercial. 현재 native Win x64/Linux x64·ARM/Mac ARM; Intel Mac은 예정. [설치](https://docs.influxdata.com/influxdb3/core/install/)·[release](https://github.com/influxdata/influxdb/releases/tag/v3.12.1) | 기본432 query-file와10분 파일 조건은 약72h scan 범위이며 자료 삭제 시한은 아님. 값을 높이면 RAM/read 비용; 장기 compaction은 Enterprise 비교. 시리즈+시각 overwrite는 revision ledger가 아니므로 장기 NAS 연구 우선안에서 제외. [제약](https://docs.influxdata.com/influxdb3/core/reference/config-options/) |

**우선 현재 native → 9절 고정 파일 reader → 그래도 남는 실측 병목에 맞는 대안 하나** 순서다. 시계열 DB를 모든 주문/뉴스/연구의 공통 저장으로 만들지 않는다.

### 10.4 archive 판본·배열 저장과 별도 백업

현재 [순위 export](../../scripts/export_preserved_rank_history.py#L469)는 같은 snapshot의 관계/원자료를 stream하고 manifest를 마지막에 게시한다. [Drive backup](../../src/kiwoom_monitor/infrastructure/persistence/google_drive_sync.py#L122)은 component hash/size와 manifest를 보관하지만 서로 다른 DB 전체의 동일 업무 cut을 보장하는 것은 아니다. [복원](../../src/kiwoom_monitor/infrastructure/persistence/strict_restore.py#L371)은 before-image/lease·실패 rollback을 소유하고 [연구 결과 전달](../../src/kiwoom_monitor/research_result_delivery.py#L112)은 첫 receipt와 로컬 ACK를 구분한다. 새 catalog/backup도 이 책임을 대신하지 않는다.

| 후보 | 공식판·라이선스·지원/추가 의존 | 권고·기대 효과와 재개 조건/위험 |
|---|---|---|
| Delta / delta-rs | deltalake1.6.6, Apache 계열, Python≥3.10/cp310abi3 Win x64·Mac ARM·Linux x64/ARM; 이 판의 Intel Mac/Win ARM wheel 미확인. [배포](https://pypi.org/project/deltalake/1.6.6/) | table snapshot/MVCC·정정/compaction을 여러 reader가 실제 공유할 때 보류 재개. 단일 table transaction·backend atomic create 요구, NAS SMB/NFS 다중 writer를 자동 보장하지 않음. log/checkpoint·vacuum·protocol 호환 비용. [transaction](https://delta-io.github.io/delta-rs/1.5.1/how-delta-lake-works/delta-lake-acid-transactions/) |
| Iceberg / PyIceberg | 0.12.0, Apache-2.0, Python≥3.10/cp313 Win x64·Mac x64/ARM·Linux x64/ARM. Win ARM 및 선택 I/O/codec 조합 미수락. [배포](https://pypi.org/project/pyiceberg/0.12.0/) | 다수 도구의 큰 table/schema/partition snapshot 공유 요구 때만. catalog/metadata·expiry·파일 보존 비용. 공식 SQLite SQL catalog는 개발/탐색용이며 동시 운영 해법이 아님. 기존 PG catalog도 권한/schema/backup 비용이 남음. [catalog](https://py.iceberg.apache.org/configuration/) |
| DuckLake | 1.1 stable/MIT, DuckDB native extension/ABI·catalog 의존. 선택 OS/ARM extension 조합 미수락. [transaction](https://ducklake.select/docs/stable/duckdb/advanced_features/transactions) | 다중 분석 writer/여러 table commit 필요 때만. DuckDB catalog는 writer process1, SQLite는 로컬 writer1+retry, PG는 원격 multiuser. 새 서버가 항상 필수는 아니지만 공유 NAS SQLite 해법도 아님. [catalog 선택](https://ducklake.select/docs/stable/duckdb/usage/choosing_a_catalog_database) |
| HDF5/h5py·Zarr | h5py3.16.0 BSD-3-Clause/native HDF5, Zarr3.4.2 MIT/NumPy·codec. Python3.13·h5py3OS x64/ARM wheel; Zarr pure wrapper와 하위 native 지원은 별개. [h5py](https://pypi.org/project/h5py/3.16.0/)·[Zarr](https://pypi.org/project/zarr/3.4.2/) | N차원 배열 slice가 실제 필요할 때만. HDF5 SWMR writer1의 flush/refresh와 Zarr 겹치는 chunk 동기화는 별도. 현재 관계/receipt·PIT를 배열로 다시 구현할 실익 없음. [SWMR](https://docs.h5py.org/en/stable/swmr.html)·[동시성](https://zarr.readthedocs.io/en/stable/user-guide/performance/#thread-safety-and-process-safety) |
| ArcticDB | 6.28.2, **BSL1.1 / production 유료 조건**, Python3.13 Win x64/Linux x64/Mac ARM wheel·다른 OS/ARM conda 조건. [공식 배포/조건](https://pypi.org/project/arcticdb/6.28.2/) | versioned time series라도 일반 OSS와 동일 분류하지 않음. 현재 OSS 도입 후보에서 제외. 과거 라이선스 전환판 고정도 검증 없는 해결책 아님 |
| restic | 0.19.1, BSD-2-Clause, Go standalone CLI·Python ABI 독립. Win x64·Mac x64/ARM·Linux x64/ARM, Win ARM asset 없음. [release](https://github.com/restic/restic/releases/expanded_assets/v0.19.1)·[license](https://github.com/restic/restic) | 일관된 snapshot/불변 산출물1종의 암호화·중복제거 backup 비교 우선. archive 조회 형식과 별개 repository. key 회복·cache/index·check/restore·hash CPU가 남음; SFTP/CIFS 환경 미조회. [backend](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html) |
| Kopia | 0.23.1 Apache-2.0, standalone CLI/GUI. Win x64·Mac x64/ARM·Linux x64/ARM, Win ARM asset 없음. [release](https://github.com/kopia/kopia/releases/expanded_assets/v0.23.1)·[license](https://raw.githubusercontent.com/kopia/kopia/v0.23.1/LICENSE) | GUI/개별 정책이 필요할 때 restic의 대안 하나. 기본 snapshot verify를 전체 blob download/decrypt 검증으로 오해하지 않음. maintenance/cache·정책 운영 추가. [일관성/검증](https://kopia.io/docs/advanced/consistency/) |
| Borg | stable1.4.5 BSD-3-Clause; Windows WSL/Cygwin은 experimental,2.0 native도 beta/experimental. [stable 설치](https://borgbackup.readthedocs.io/en/stable/installation.html)·[개발판](https://borgbackup.readthedocs.io/en/master/) | 이미 Linux/mac 운영 기반이 있는 경우만 후순위. 공통 Windows backup을 위해 새 호환 환경을 추가할 이점 미입증 |

**보관·수락·복원은 분리한다.** catalog snapshot은 파일/table 상태를 고정할 뿐 앱의 available_at/PIT·frozen universe·원 revision/hash·producer cut·첫 receipt·계좌 lease를 만들지 않는다. catalog commit timestamp를 업무 가용시각으로 쓰지 않는다. [DuckLake backup](https://ducklake.select/docs/stable/duckdb/guides/backups_and_recovery)도 catalog와 참조 data/delete 파일을 함께 보관하고 수동 backup 중 write/compaction/cleanup 중지를 요구한다. Delta vacuum/Iceberg expiry도 frozen 입력 보존을 자동 해결하지 않는다.

SQLite Online Backup/PG의 일관된 export를 먼저 만들고 그 산출물에 backup을 적용한다. 활성 DB 파일의 일반 복사, VSS snapshot, encrypted blob 존재만으로 SQL/여러 DB의 업무 일관성·복원 성공을 보고하지 않는다. backup 도구 rollback은 격리 경로 restore 후 기존 reader/native 소비까지 검증하며 운영 주문상태를 되감지 않는다. 기존 DB/과거 자료는 그대로 보관하고 사용자가 확정한 순위+최소 예외만 필수 이전한다. 새 도구 검토가 과거 뉴스 전체 이전/재분석·운영 prune/삭제의 이유가 되지 않는다.

### 10.5 뉴스 수집·규칙·검색: 범용 알고리즘과 업무 판단의 구분

[외부 검색](../../src/kiwoom_monitor/infrastructure/naver_news.py#L379)은 NAVER에 query_text를 전송한다. [저장 이력](../../src/kiwoom_monitor/central_server/database_news_revisions.py#L1257)과 [종목 조회](../../src/kiwoom_monitor/central_server/database_news_revisions.py#L1391)는 identity/종목/가용시각/최신판본을 고르는 SQL이며, 주요 경로가 본문 `%LIKE%` 검색은 아니다. **FTS를 넣으면 현재 뉴스 조회가 빨라진다고 결론 내릴 근거가 없다.** 본문 parser/HTTP 후보는 1~8절을 재사용하며 crawler 플랫폼을 새로 붙일 실제 요구도 확인되지 않았다.

[방향/분류](../../src/kiwoom_monitor/application/news_analysis.py#L250)는 literal와 regex, [공급계약 RULE](../../src/kiwoom_monitor/application/news_rules.py#L75)은 원문 span·대상회사·부인/해지/가능성·금액을 판정한다. [grouping](../../src/kiwoom_monitor/application/news_grouping.py#L31)은 URL 중복→48h→범주/수치/상대방/단계 충돌→Jaccard/SequenceMatcher 순서다. [RULE worker](../../src/kiwoom_monitor/central_server/news_jobs.py#L270)는 article/BODY revision을 고정하며 문자열 유사는 관련 가능성으로만 저장한다. grouping 변경은 [자동 AI enqueue](../../src/kiwoom_monitor/central_server/news_service.py#L401)의 대표기사/유료요청 대상에도 영향을 준다.

| 후보 | 공식판·라이선스·3OS/설치 조건 | 비교/유지 판단 |
|---|---|---|
| pyahocorasick | 2.3.1(2026-04-27), BSD-3-Clause·부속 Public Domain, Python≥3.10. cp313 Win x64·Mac ARM/universal2·Linux glibc/musl x64/ARM. Win ARM 미확인. [배포](https://pypi.org/project/pyahocorasick/2.3.1/)·[API](https://pyahocorasick.readthedocs.io/en/latest/) | 큰 literal 사전/반복 scan 비용이 확인되면 후보. 작은 Python in보다 빠르다는 앱 근거 없음. 모든 overlapping hit·기존 사전순서·matched[:2] evidence를 보존; automaton 구축/RAM 포함 |
| ahocorasick-rs | 1.0.3(2025-10-08), Apache-2.0, Python≥3.10. cp313 Win x64·Mac x64/universal2·Linux glibc x64/ARM; Win ARM/musl 미확인. [배포/API](https://pypi.org/project/ahocorasick-rs/1.0.3/) | 위와 대안 관계. default가 overlapping 전체 hit가 아니므로 Standard/overlapping 의미 조정 필요. DFA/GIL 해제 이득은 build/RSS·호출 비용과 함께 비교; 둘 다 설치하지 않음 |
| RapidFuzz | 3.14.6(2026-08-30), MIT, Python≥3.11. cp3133OS x64/ARM wheel, Windows VC++ runtime·source C++17 조건. [배포](https://pypi.org/project/RapidFuzz/3.14.6/)·[점수 정의](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html) | 현재 SequenceMatcher와 알고리즘이 달라 점수0.60→60 치환 불가. token_set의 부분집합 고득점을 사건 동일성으로 쓰지 않음. grouping 병목 근거 없으므로 직접 교체 보류 |
| FTS5 / PG FTS·pg_trgm | SQLite Public Domain/PG License, 새 Python 패키지 없는 DB 기능. 실제 linked SQLite compile option·PG extension/권한/OS 환경은 미조회. [FTS5](https://sqlite.org/fts5.html)·[PG FTS](https://www.postgresql.org/docs/current/textsearch-intro.html)·[pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html) | 별도 본문 탐색 요구 시 파생 index. 기본 한국어 token 검색은 형태소 분석/부분문자열과 다름; FTS5 trigram 3글자 미만과 추출불가 trgm 패턴은 제약. 색인 쓰기/WAL·공간·rebuild·PIT 최신판 선택은 남음 |

RULE 자동화·과거 재분석·AI 정책을 새 matcher가 결정하지 않는다. 원문/본문/hash/판본을 그대로 두고 literal hit/span·방향/분류·그룹/대표기사·관련 가능성·AI 대상이 같은지 비교할 후보 계획만 남긴다.

### 10.6 자동매매·연구: 엔진 대체보다 필요한 범용 도구만

[Paper engine](../../src/kiwoom_monitor/application/research_execution.py#L323)은 다음 실제 봉·세션/품질·비용 유효기간·불확실 체결을 명시한다. [비용 모델](../../src/kiwoom_monitor/domain/simulation_contract.py#L24)은 원화 정수/Decimal·매수 올림/매도 내림·판본/근거를 보존하고, [평가](../../src/kiwoom_monitor/application/research_evaluation.py#L1902)는 Fraction 표본 계산·사건순 MDD를 쓴다. 일반 framework의 float commission/OHLC 추정과 같지 않다. fresh Risk/control·계좌 단일 owner·CAS/lease·UNKNOWN/부분 FILL·미확정 주문 복구는 앱 책임으로 유지한다.

[탐색](../../src/kiwoom_monitor/application/research_search.py#L245)은 작은 정수 grid·seed와 baseline/no-trade/ablation/cost-stress를 생성하며, [실행](../../src/kiwoom_monitor/research_process.py#L1233)은 native attempt/owner/generation 안에서 commit한다. Optuna를 추가하더라도 또 다른 완료/권한 DB를 만들 필요는 없다. [raw20 recipe](../../src/kiwoom_monitor/domain/research_contract.py#L25)와 [불변 완료 게시](../../src/kiwoom_monitor/research_run.py#L449)를 보존하며 같은 hash/판본으로 새 계산 결과를 덮어쓰지 않는다.

| 후보 | 공식판·라이선스·지원/의존 | 추천/기각 근거와 변경 범위 |
|---|---|---|
| Optuna | 5.0.0(2026-09-07), MIT, Python≥3.9/3.13·pure wrapper. NumPy/SQLAlchemy/Alembic 등 추가. [배포](https://pypi.org/project/optuna/5.0.0/)·[의존](https://raw.githubusercontent.com/optuna/optuna/v5.0.0/pyproject.toml) | 넓은/조건부 탐색이 실제 필요할 때 ask→기존 evaluate/native commit→tell만 후보. 작은 약50행 grid는 유지가 경제적일 수 있음. commit/tell 사이 중단·실패 예산·seed/순서를 보존하며 pruning/병렬은 별도 연구방법 결정. [ask/tell](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/009_ask_and_tell.html)·[병렬 재현 한계](https://optuna.readthedocs.io/en/stable/faq.html#how-can-i-obtain-reproducible-optimization-results) |
| SciPy | 1.18.1(2026-08-21), BSD 계열, Python≥3.12. cp3133OS x64/ARM·native BLAS/NumPy. [배포](https://pypi.org/project/scipy/1.18.1/) | 새 검정/최적화 요구 때 직접 구현보다 검토 우선. 현재 약20행 Fraction 평균/분산을 바꿀 실익 없음. bootstrap의 batch*n 메모리·시계열 의존성/블록 정책은 자동 해결 안 됨. [bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html) |
| Numba | 0.68.1(2026-10-27), BSD 계열, Python3.10~3.15·NumPy<2.6/llvmlite0.50계열. cp313 Win/Linux x64·ARM/Mac ARM; 현 Intel Mac wheel 미확인. [배포](https://pypi.org/project/numba/0.68.1/)·[요구](https://raw.githubusercontent.com/numba/numba/0.68.1/setup.py) | 병목이 입증된 순수 ndarray kernel만 조건부. 현재 Mapping/dataclass/Decimal/Fraction/aware 시각·I/O 전체 JIT 대상 아님. llvmlite 압축wheel만37~60MB이며 실제 RSS/JIT cold start는 별도; fixed-width overflow/연산 의미 확인 필요. [지원](https://numba.readthedocs.io/en/stable/reference/pysupported.html)·[의미](https://numba.readthedocs.io/en/stable/reference/pysemantics.html) |
| Backtesting.py | 0.6.6(2026-07-22), AGPL-3.0·Python≥3.9/pure wrapper, NumPy/pandas/Bokeh. [배포](https://pypi.org/project/backtesting/0.6.6/)·[API](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html) | 현 엔진 교체 비채택. 전체 OHLCV DataFrame·기본 체결/float 비용을 현재 revision/PIT·원화 반올림/불확실성에 이식하면 두 엔진 유지비가 큼. copyleft 배포 의무도 검토 필요 |
| Backtrader | 1.9.78.123(2023-04-19), GPLv3+, pure wrapper. 공식 metadata3.13/ARM 수락 근거 없음. [배포](https://pypi.org/project/backtrader/1.9.78.123/)·[체결](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/) | multi-feed/order 기능은 있지만 현 episode/원가/lease·계좌 권한을 대체하지 않음. 오래된 release만으로 폐기 프로젝트라 단정하지 않되 실제 이식/검증 이익 부족 |
| VectorBT | 1.2.0(2026-10-19), **Apache2+Commons Clause 제한**·Python≥3.11,<3.15, 다수 scientific/시각화/Jupyter 의존. [배포](https://pypi.org/project/vectorbt/1.2.0/)·[license](https://raw.githubusercontent.com/polakowo/vectorbt/v1.2.0/LICENSE.md) | 현 엔진 교체 비채택. 판매제한 때문에 일반 permissive OSS와 구분. NumPy≥2.4.6이 현2.3.5 lock과 직접 충돌. 조합 배열/buffer·JIT와 fill/revision 엔진의 책임은 별개. [고정 의존성](https://raw.githubusercontent.com/polakowo/vectorbt/v1.2.0/pyproject.toml) |

연구용 패키지를 NAS 보관소나 조회 역할에 필수 설치하지 않는다. framework 이름이 미래 정보 차단 증거는 아니며 [기존 replay](../../src/kiwoom_monitor/application/research_replay.py#L111)의 available cut→최신 revision→품질 순서를 그대로 검증해야 한다. 절차/후보열을 바꾼 성과 차이를 '같은 연구의 계산 속도 개선'으로 보고하지 않는다.

### 10.7 실시간 통신·OS 실행·설치 재현성

[Hub](../../src/kiwoom_monitor/central_server/realtime_hub.py#L67)는 subscriber별 bounded queue/oldest drop·범위 gap을 소유하고, [route](../../src/kiwoom_monitor/central_server/realtime_routes.py#L124)는 sender 취소/합류 후 구독을 해제한다. [Collector](../../src/kiwoom_monitor/central_server/realtime_collector.py#L430)의 generation·credential drain과 flush의 실패 재대기·native join은 generic broker로 없어지지 않는다. 현재 단일 공통 엔진 안의 callback을 네트워크로 바꿀 이유는 확인되지 않았다.

| 후보/기존 재사용 | 공식판·라이선스·OS 조건 | 판정·추가 비용 |
|---|---|---|
| uv / pip-tools | uv0.13.6 MIT/Apache-2.0·Rust standalone/Python3.13, Win x64/Mac x64·ARM/Linux x64 Tier1·Win ARM/Linux ARM Tier2. pip-tools7.6.2 BSD-3-Clause/pure·Python≥3.9/3.13. [uv지원](https://docs.astral.sh/uv/reference/policies/platforms/)·[배포](https://pypi.org/project/uv/)·[pip-tools](https://pypi.org/project/pip-tools/) | 기존 pip+role manifest를 기준으로 하나만 비교. 장중 처리 성능과 무관. uv sync 기본 exact는 미선택 패키지를 지우므로 기존 환경에 실행하지 않음. pip-tools는 OS/Python별 compile 차이, uv는 새 도구/lock·다운로드/index/TLS 관리비. [sync](https://docs.astral.sh/uv/concepts/projects/sync/)·[환경별 lock](https://pip-tools.pypa.io/en/latest/#cross-environment-usage-of-requirements-in-requirements-txt-and-pip-compile) |
| 기존 PyInstaller / wheel+CLI | 기존 환경6.22.2·조회판6.22.3, GPL2+Bootloader exception/일부 Apache2. Python3.13·3OS/ARM 배포물. [공식](https://pyinstaller.org/en/stable/)·[license](https://pyinstaller.org/en/v6.22.3/license.html) | 교체보다 재사용 우선. cross compiler가 아니며 각 OS 빌드/서명/native/child 종료 수락 필요. headless는 wheel/CLI도 가능. 현 Windows spec의 DLL/UpdateHelper.exe/OCR 전체 bundle이 자동 공통 배포물이 되지 않음 |
| AnyIO / uvloop | AnyIO4.15.1 MIT/pure·Python≥3.10/3.13, 현lock4.14.2. uvloop0.23.0 MIT/Apache2/native libuv·cp313 Mac/Linux x64/ARM, Windows 미지원. [AnyIO](https://pypi.org/project/anyio/)·[uvloop](https://pypi.org/project/uvloop/) | 전체 asyncio 엔진 전환 비추천. AnyIO level cancellation과 기존 edge/shield·native drain 의미 다름. uvicorn[standard]의 auto는 설치된 uvloop 유무에 따라 backend가 달라지므로 OS manifest/수락에서 구분. 새 runtime 가속은 미측정. [취소](https://anyio.readthedocs.io/en/stable/cancellation.html)·[Uvicorn loop](https://uvicorn.dev/concepts/event-loop/) |
| NATS / JetStream | server2.15.2/client nats-py2.16.0 Apache-2.0. server3OS/ARM Go binary·client asyncio. [release](https://github.com/nats-io/nats-server/releases/tag/v2.15.2)·[client](https://pypi.org/project/nats-py/) | 현재 미채택. Core at-most-once는 Hub와 중복, JetStream은 durable log/consumer/retry owner 추가. native transaction·첫 receipt·단일 주문 권한은 별도로 남음. [Core](https://docs.nats.io/learn/core-nats/)·[JetStream](https://docs.nats.io/learn/jetstream/pull-consumers) |
| PyZMQ / libzmq | PyZMQ27.2.0 BSD-3-Clause/libzmq release4.3.5 MPL2, Python≥3.9/3.13·3OS/ARM wheel. [배포](https://pypi.org/project/pyzmq/)·[license](https://zeromq.org/license/) | daemon 없는 IPC 대안이나 현 API/WSS/RAM 병목 미입증. HWM drop/block·socket pattern·linger/context/thread 종료·native/libsodium 배포/보안 관리 추가. [socket](https://zeromq.org/socket-api/) |
| Redis Streams | Redis8.10.3 RSALv2/SSPLv1/AGPLv3 선택·redis-py8.1.0 MIT/Python3.13. server Windows는 WSL2/호환제품 경로, native 공통 의존성 아님. [license](https://redis.io/legal/licenses/)·[release](https://github.com/redis/redis/releases/tag/8.10.3)·[client](https://pypi.org/project/redis/) | 현재 미채택. 별도 server·AOF/RDB·pending/claim·trim/보존과 DB 수락의 두 수명을 운영해야 함. at-least-once는 주문/DB 멱등성 보장 아님. [Streams](https://redis.io/docs/latest/develop/data-types/streams/)·[Windows](https://redis.io/blog/redis-on-windows-10/) |

[현재 lock](../../requirements.lock.txt)은 Windows/Python3.13 전체환경 목록이고 hash lock이 아니다. [role extras/CLI](../../pyproject.toml#L9)와 기존 설치 manifest를 기준으로 OS/역할별 dependency closure·정확 artifact·license/security update를 관리할 후보로 남긴다. 이미 안정적인 pip 경로를 도구가 새롭다는 이유로 교체하지 않는다. PyInstaller/uv의 ARM asset만으로 Qt/OCR/CREON/secret backend의 ARM 지원을 주장하지 않는다.

별도 universal service manager를 만들 필요는 확인되지 않았다. 기존 headless CLI를 바깥에서 [systemd](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)·[launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)·[Windows SCM](https://learn.microsoft.com/en-us/windows/win32/services/service-programs)과 연결할 배포 경계만 남긴다. SCM은 ServiceMain/control handler 계약이 필요하므로 console 등록만으로 완료되지 않는다. UI 소유 parent-pid child와 독립 엔진 수명, 서비스 restart와 신규 주문 명시 재개는 분리한다. 실제 서비스 설치/설정은 하지 않았다.

### 10.8 전환 계획·롤백과 장기 유지비 판단

현재 단계는 전부 **A 조사**, 아래는 리팩터링 후 별도 승인할 **C 비교 계획**이다. B 책임/공개 계약은 기존 AR에 재사용하고 라이브러리 교체를 같은 변경으로 묶지 않는다. D는 개별 결과/승인 뒤에만 진행한다.

| 비교 묶음 | 동일 입력·계약/비용 확인 | 원복/도입 중단 기준 |
|---|---|---|
| 기존 DB 기능 재사용 | frozen 입력에 현 batch 대 COPY, 현 export 대 bounded 전체경로. 연결/SQL/COMMIT·전체시간·CPU/peakRSS·같은 정렬/hash/관계/첫 receipt·취소/ACK loss 비교 | 기존 native SQL/reader 유지, 새 결과를 원본에 덮어쓰지 않음. durability/최신 control·원자 수락을 약화해야 이기는 후보는 채택 안 함 |
| 파일/조회/운반 | 9.6의 기존 계획 재사용. 엔진·물리형식·운반을 독립 변경으로 비교 | 원 산출물/manifest/reader와 pending ACK를 보존, 파일 전송 성공을 앱 수락으로 승격하지 않음 |
| 백업 | 일관된 사본1종의 backup→손상/중단/retry→격리 restore→기존 실제 reader/native 소비까지. source cut과 암호화/전송/check/restore 비용 분리 | 운영 restore·키 이관/유실정책·prune/삭제는 별도 승인. 기존 backup/원자료 보관, 후보 repo만으로 권한/미확정 주문을 자동 복원하지 않음 |
| 뉴스·연구 알고리즘 | literal span/순서·RULE/그룹/AI 대상 또는 frozen PIT/정수비용/OOS/seed·native trial를 독립 비교. 새 통계/탐색 정의는 판본 분리 | hit/결과 차이를 임의 threshold/기대값 변경으로 숨기지 않음. 과거 BODY/hash/판본/완료 연구를 재작성하지 않음 |
| OS 설치 도구 | 기존 pip/lock/manifest 대 후보의 역할별 resolved set·origin·artifact/hash·다운로드/캐시/설치시간·cold start/종료·dependency 부재 확인 | 격리 새 환경에서만, 기존 .venv에 exact sync/clean 실행 안 함. 최종 패키지/권한·native 의미가 달라지면 별도 판단 |

비교는 기존 Collector/receipt/분봉revision·news analysis/RULE/grouping/jobs·research source/search/execution/result-delivery·OS 설치/종료 검사를 선택해 재사용한다. 실제 변경이 없는 기능의 전체 회귀·과거 전수감사를 반복하지 않는다. 새검사는 기존 검사가 형식/전송/알고리즘 차이를 판별하지 못하는 경계에만 후속 검토한다. 이번 실제 제품 검사/benchmark는 **0건**이며 통과/성능 우위를 보고하지 않는다.

**유지비 절감의 근거와 한계:** 기존 DB 기능·표준 API 재사용은 새 wrapper/동작 체계·업데이트 대상을 덜 늘린다. 형식·운반·암호화 backup 같은 범용 구현은 검증된 도구에 위임할 여지가 있지만 앱의 안전/작업/가용시각 책임은 남는다. 새로운 daemon/catalog/과학 스택은 배포·인증·권한·schema/compaction·backup·3OS native 조합 수를 늘린다. 그래서 단순한 라이브러리 LOC 감소보다 변경 경로·직접 의존 수·고유 상태/원장 수·해당 장애/업데이트 검증 비용을 합쳐 판단한다. 현재 인시·금액/CPU 절감률의 확정 수치는 없다.

**현재 결론:** 기존 엔진/native·안전/저장/복구 계약 재사용을 유지한다. 누락된 실질 후보는 COPY/묶음 처리, restic, 역할별 uv 또는 pip-tools, 실제 비용/요구가 생긴 literal matcher·sampler 등이다. 기존1~9절의 OS 보조·Parquet/단일 reader/전송 후보와 경쟁/대안 관계로 관리하며 전부 필수 설치하지 않는다. 시계열 DB/catalog/broker/백테스트 전면교체는 지금 도입할 근거가 없다. 필요하면 역할별로 제거·교체할 수 있는 기존 경계만 유지한다. AR01~09의 완료 판정/조건·F 실제 연결·기존 비용/구조 가드·원실패는 바꾸지 않았다.

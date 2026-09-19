# NAS API 인증·운영 설정 런타임 변경 — 설계 확정 및 Sol 구현 계약

검토일: 2026-09-15. 상태: **설계 검토 완료, R0·R1 로컬 구현 완료**.
구현 진행: R0~R5, R6a·R6b1·R6b2, R6b3a·R6b3b1·R6b3b2·R6b3c1·R6b3c2a 및 R6b3c2b 계좌 WS·REST 연계, R6c1 PC 입력과 R6c2 계획된 재연결/기존 표 유지·관측 간격 표시를 구현했다(2026-09-15~16). R7a NAS 원본 백업/누적 소스 동기화·전체 hash 확인과 R7b 이미지 재빌드·시작/health·DB 읽기·앱 WS·순위 회차 진행 확인 완료. R7c2 HTTPS/WSS·프록시 신뢰와 앱 저장 주소 전환도 확인했다. 장중 동시 토큰 실환경 검증은 남아 있다. 자동주문 화면/실제 운용 완료를 뜻하지 않는다.
다중 계좌 보완: 같은 날 사용자 요청에 따라 실전·모의 각각 복수 계좌 등록/운영을 포함했다.
계좌 신원과 API 연결 프로필, 공통 시세 수집 역할을 구분한다. 1.1절은 이후 단계 모두에 적용한다.
이 문서는 앞선 재검토 요청서를 대체한다. 최초 검토에서는 문서만 갱신했고 이후 사용자
진행 요청으로 R0 코드를 구현했다. 실제 인증키와 NAS 배포는 변경하지 않았다.
아래 파일·메서드는 검토 당시/신규를 구분한다.

## 1. 사용자 관점의 결정

앱의 NAS 설정에서 인증키를 입력하고 적용하면 해당 공급자 연결만 갱신한다.
모의투자 재신청 때마다 .env 편집, 컨테이너 재생성, account_ref 등록 명령을 반복하지 않는다.
처음 이 기능을 설치할 때는 코드·암호화 의존성·영속 볼륨 반영을 위한 누적 빌드가 한 번 필요하다.

- 같은 모의계좌의 키 갱신: 같은 매매일지와 실행 회차를 유지하고 계좌 조회를 재검증한다.
- 다른 모의계좌 등록: 자동으로 별도 계좌 신원을 만들고 별도 일지/실행 회차로 연결한다.
  기존 계좌 옆에 추가하며 기존 연결을 자동 교체하지 않는다. 새 계좌는 조회부터 시작한다.
- 네이버/DART/AI 키 변경: 기존 기사·분류·수집 위치·사용량을 보존한다.
- 실전 키 변경: REST 전환과 WebSocket 재인증 때 짧은 조회·수신 공백은 가능하다.
  기존 표는 유지하고 갱신 중 상태와 실제 공백을 기록한다. 무중단 수신을 약속하지 않는다.
- DB·포트·NAS 접근 토큰·계좌 HMAC 키·보관 경로는 서버 초기 설정으로 남긴다.
- PC 직접 연결 인증정보와 NAS 인증정보는 서로 다른 저장 대상이다. 자동 복사하지 않는다.

초안의 세 질문은 다음과 같이 결정한다.
① NAS 전용 암호화 파일 사용. ② 실제 키움 신원으로 새 계좌 자동 구분.
③ 실전 키 변경은 명시적 적용으로 처리하고 실제 수신 공백을 알린다.
외부 비밀관리 서버나 범용 공급자 플러그인은 도입하지 않는다.

### 1.1 다중 계좌 계약 — 단일 활성 계좌 전제 제거

**계좌 저장 구조는 이미 복수를 지원하지만 활성 API 연결은 아직 그렇지 않다.**
AccountScope는 broker/environment/account_ref이고 binding은 credential_profile_id를 별도로 갖는다.
journal_process.py의 계좌 선택은 이미 저장된 계좌별 일지를 표시한다. 반면 app.py는
main_binding/mock_binding, 단일 account_query_manager/mock_order_gateway를 사용한다.
계좌 선택 콤보만 추가해서 복수 계좌 API 조회가 완성됐다고 볼 수 없다.

1. **계좌(account_ref):** 실제로 검증한 하나의 계좌. 별명/키 변경과 무관하게 같은 일지 신원을 유지.
2. **연결 프로필(credential_profile_id):** 해당 계좌의 API 인증정보와 revision.
   실전 A·실전 B·모의 A·모의 B처럼 복수 등록. 환경과 검증된 계좌가 프로필에 귀속된다.
3. **시세 수집 담당(market_profile_id):** 공통 순위·시세·조건검색을 담당하도록 지정한 실전 프로필 한 개.
   앱에서 보고 있는 계좌와 별개다. 일지 계좌를 선택해도 시세 수집 담당은 바뀌지 않는다.

프로필 ID는 NAS가 생성하고 키 이름/별명을 ID로 사용하지 않는다.
구 nas-real-default/nas-mock-default는 기존 ID와 binding을 보존해 최초 프로필로 이관한다.
네이버/DART/AI는 이번에는 공급자당 global 프로필 하나를 유지한다.
키 저장 identity는 (provider, credential_profile_id)이며 provider 종류 7개가 키 개수 제한은 아니다.

**추가·갱신·사용 중지**

- 기존 프로필의 키 갱신에서 ka00001이 다른 계좌를 반환하면 기존 프로필에 덮지 않고
  ACCOUNT_CHANGED 결과와 '새 계좌로 추가' 경로를 제공한다. 새 프로필+scope를 준비해 적용한다.
- 같은 계좌의 새 키는 기존 프로필 갱신을 우선한다. 같은 계좌/키를 다른 이름으로 등록해도
  runtime·WS·주문 실행자를 중복 생성하지 않는다. 동일 scope의 활성 연결 프로필은 하나다.
- 새로운 계좌를 추가해도 기존 계좌의 정상 조회·보유·미체결 수집은 유지한다.
  모의투자 갱신으로 만료된 이전 계좌는 사용 중지할 수 있고 일지/미확정 주문은 남긴다.
- 계좌별로 별명, 조회 사용, 모의 수동주문 허용을 관리한다. 글로벌 '모의주문 ON'을
  모든 새 계좌에 적용하지 않는다. 새 실전 주문 전송 기능은 이 설정 변경에 추가하지 않는다.
- 계좌를 삭제/사용 중지해도 연구 결과·거래·복기·binding revision을 삭제하지 않는다.
  시세 담당 프로필은 대체 담당을 검증·적용하기 전에 삭제/사용 중지할 수 없다.
- 한 계좌 인증 실패는 해당 계좌 상태에 표시한다. 다른 계좌나 공통 뉴스/시세의 장애로 승격하지 않는다.

**공통 데이터와 계좌별 데이터**

순위·분봉·기본정보·뉴스·Shadow는 한 벌을 공유한다. 각 계좌의 주문/체결/잔고/비용/
매매일지와 주문 실행 회차는 scope별로 분리한다. 보유 종목 수집 범위는 모든 사용 계좌의
종목 합집합을 중복 제거해 기존 hub에 전달한다. 계좌 수만큼 TOP20/뉴스를 반복 조회하지 않는다.
전체 계좌 요약은 표시 단계에서만 합산하고 FIFO·손익·비용·전략 평가 원본은 계좌별로 계산한다.
'전체 계좌'는 조회 필터이며 주문 대상이 될 수 없다.

**API 대상 명시와 호환**

- 신규 계좌 선택 조회는 POST /api/v3/kiwoom/account-query에 account_scope,
  credential_profile_id, expected_binding_revision과 기존 페이지 필드를 필수로 전달한다.
  요청 본문 대상→활성 binding→응답 context가 모두 같아야 한다. 옛 cursor를 다른 계좌로 넘기면 거절한다.
- 모의주문은 신규 /api/v2/mock/accounts/{account_ref}/orders와 그 하위 조회·취소 경로에서
  scope/profile/binding revision을 검증한다. intent_id도 해당 계좌에 귀속되는지 확인한다.
  기존 manual request_id가 두 계좌에서 같아도 서로의 주문으로 판단하지 않는다.
- 현재 v2 account-query 및 기존 v1 모의주문 경로는 이관 당시 지정한 legacy default profile에만
  고정한다. 화면에서 선택한 계좌에 따라 이 경로의 암묵적 대상이 바뀌면 안 된다.
  그 프로필이 비활성/변경되어 호환 대상을 확정할 수 없으면 명시 오류를 반환한다.
- RemoteKiwoomRestClient/계좌 worker는 선택 계좌 context를 전달한다. 잘못된 계좌 결과를 받아
  폐기하는 방어뿐 아니라 요청 단계에서 정확한 계좌를 선택해야 한다.
- 계좌 목록은 NAS 등록 계좌와 로컬에 이력이 남은 과거 계좌를 합쳐 보여준다.
  거래가 아직 없는 신규 계좌도 선택 가능하고 과거 계좌는 '기록 열람만 가능' 상태를 구분한다.
  자동 일지 보완 작업에도 scope를 고정하며 UI에서 보고 있는 한 계좌에만 종속시키지 않는다.
- 로컬 장애전환은 NAS가 반환한 익명 계좌와 같은 신원으로 검증된 로컬 프로필이 있을 때만 한다.
  현재 PC의 '기본 실전 계좌'로 다른 계좌 요청을 대신하지 않는다.
  PC의 ApiProfiles는 현재 실전/모의 한 쌍씩이므로 NAS 다계좌를 지원한다는 이유로
  PC 직접 연결도 다계좌 지원이 끝났다고 표시하지 않는다.

**한도와 WebSocket**

키움 공식 안내는 계좌별(토큰별) 국내 실전 조회 5회/초, 모의 국내 TR 1회/초,
계좌별(토큰별) 세션 1개를 명시한다. 따라서 한도를 환경 전체의 단일 5/1 큐로 묶지 않는다.
검증된 계좌/인증 연결별 broker·요청 간격·세션을 유지하되 같은 계좌 키의 준비/교체는 기존
간격을 이어받는다. 미검증 신규 프로필의 인증 탐색은 제한된 준비 큐로 직렬화하고
기존 계좌와 같다고 확인되면 신규 runtime을 시작하지 않고 기존 갱신으로 연결한다.

같은 토큰으로 시장 WS와 계좌 WS 두 개를 만들지 않는다. 시세 담당 계좌는 기존 시장 WS에서
계좌 이벤트도 분배하고, 나머지는 각 인증 세션의 계좌 이벤트만 구독한다.
다른 토큰 세션의 동시 운용은 실환경 검증 항목이며, 기존 종목별 0B/0w 등록 정책은 이번에 변경하지 않는다.
비담당 계좌를 추가/갱신/선택할 때 시장 WS를 재연결하지 않는다.

NAS 부하 제어는 API 한도와 구분한다. 비시세 계좌의 인증·REST 작업은 초기 동시 실행 2개로
제한하고 조회 시작 시각을 분산한다. 이 제한에 시세 담당 ka00198을 넣지 않는다.
제한 대기 상태는 공개하고 활성 계좌를 임의 누락하지 않는다.

**저장·수명 최소 확장**

- 기존 AccountScope/계좌별 일지 스키마는 유지한다.
- 신규 central_credential_profiles: profile_id(PK), provider, environment, label, lifecycle_state,
  created_at, archived_at. 키나 원계좌번호는 넣지 않는다.
- 계좌별 운영 문서 server_account_settings는 scope별 단일 active_profile_id,
  monitor_enabled, mock_order_enabled, revision을 소유한다. 중복 활성 route를 원자적으로 거절한다.
  GET/PUT /api/v1/settings/accounts/{account_ref}에서 검증된 scope와 expected revision을 받는다.
  전체 계좌에 적용되는 동일 이름의 글로벌 monitor/order 토글은 신규 화면에서 사용하지 않는다.
- 공통 시세 담당 market_profile_id와 legacy 기본 프로필은 별도 서버 운영 메타데이터다.
  역할 변경도 expected revision으로 보호하고 사용자 화면 계좌 선택과 연동하지 않는다.
- credential_runtime의 고정 공급자 처리는 유지하되 활성 bundle/operation/lock을 프로필 맵으로 소유한다.
  account query session manager와 mock runtime은 해당 검증 scope별 인스턴스다.
  API 요청은 선택 bundle을 응답 완료까지 고정하며 계좌 목록 전체를 매번 재생성하지 않는다.

이 보완은 R1의 프로필 저장, R2의 프로필별 API, R3의 복수 모의 runtime,
R4의 계좌 목록/선택 UI, R6의 시세 담당과 비담당 실전 계좌 분리에 선행 반영한다.
하나만 활성화하는 임시 구현을 먼저 만든 뒤 다시 전면 수정하는 순서로 진행하지 않는다.

## 2. 코드 감사: 초안에서 반드시 고쳐야 할 점

| 근거(현재 코드) | 확인된 사실 | 설계 결정 |
| --- | --- | --- |
| central_server/config.py:147–165, app.py:134–179 | 암호화 저장소를 읽기도 전에 env 키/ref가 없으면 시작 검증이 실패할 수 있음 | 초기 운영 설정 읽기 → 인증 저장소 합성 → 기능별 유효성 검사 순서로 변경 |
| app.py:181–251, 335–367 | 모의 객체가 시작 시 ref/run에 묶이고 인증 실패 시 None이 됨 | 모의 runtime 조립을 재사용 가능한 한 책임으로 추출; 실패 상태에서도 새 키로 복구 가능 |
| rest_broker.py:138–188 | close는 낮은 우선순위 종료 항목을 큐에 넣음. 요청은 자동 start, asyncio.to_thread로 실행 | close를 회전용 장벽으로 사용하지 않음. 신규 접수 차단·실제 통신 완료·세대 검사 필요 |
| realtime_collector.py:113–135 | 분봉·초 자료 누적기와 저장 재시도분을 collector가 보유 | collector/hub를 재생성하지 않고 연결 인증만 교체 |
| application/account_identity.py:59–75 | 기존 bind 함수는 registry 저장 및 binding revision 증가를 즉시 수행 | 검증/미리보기와 활성 binding 확정 분리. 준비 과정에서 기본 프로필을 바꾸지 않음 |
| mock_account_monitor.py:177, execution_runtime.py, execution_repository.py:116 | monitor 종료가 실행 소유권(lease)을 해제하지 않음 | 실제 작업 종료 후 owner/generation 일치 조건으로 해제; 이전 작업의 늦은 쓰기 차단 |
| news_service.py:37–75, news_sources.py | watchlist와 query-set이 네이버 client를 각각 보유 | 두 경로 모두 갱신. 작업기/사용량/source cursor는 유지 |
| ai_service.py:28, 49, 134 | 모델만 바꿀 수 있고 키는 시작 설정에서 읽음 | AI 실행 요청에 인증 revision을 고정하고 다음 요청부터 새 키 사용 |
| central_operational_settings.py:23, news_settings_dialog.py:437 | update가 GET 후 전체 PUT이라 타 화면 변경을 덮을 수 있음 | 실제 변경 필드만 전송, revision 충돌 검출 |
| app.py:637–639 | 메모리를 먼저 바꾼 뒤 DB에 저장 | 검증·저장 성공 후 공개 상태 변경, 적용 실패는 별도 상태 표시 |
| api_settings_dialog.py:198,273,304, news_settings_dialog.py:66,437 | 기존 설정 조회/저장도 GUI에서 동기 HTTP 수행 | 관련 설정 경로를 worker로 옮겨 입력·창 이동을 막지 않음 |
| central_server_config.py:24, deploy/synology/README.md HTTPS 안내 | HTTP/HTTPS 모두 지원; 실제 운영 HTTPS 여부는 이번에 확인하지 않음 | 비밀키 전송은 검증된 HTTPS 사용, redirect 금지 |

줄 번호는 검토 시점 기준이며 아래 단계의 함수/책임 이름으로 다시 찾아 구현한다.
위 항목은 코드 경로에서 확인한 구조다. 운영 중 키 교체 성공을 실측한 결과는 아니다.

## 3. 설정 소유권과 범위

| 종류 | 값 | 변경 방식 |
| --- | --- | --- |
| 공급자 인증 7종 | kiwoom_real, kiwoom_mock, naver, dart, openai, gemini, claude | 전용 쓰기 API → 암호화 파일 → 공급자별 적용 |
| 이미 런타임 지원 | AI 공급자/모델/일일 한도, 뉴스 주기/검색어/제외 언론사, DART 사용 여부, Shadow 사용/전략/주기/최신성 | 기존 operations 부분 PUT 개선 후 계속 사용 |
| 이번 확장 | 뉴스 요청 예산 3종, 모의계좌 조회 사용/모의 수동주문 허용, 조건식 선택, 해외시세 사용/주기/상품/roll 설정 | 같은 operations 경계에서 각 소유자에 적용 |
| 수집 기반 정책 | AUTONOMOUS_TOP20_ENABLED, MARKET_EVENT_COLLECTION_ENABLED, RESEARCH_OBSERVATION_HISTORY_ENABLED, NEWS_HISTORY_JOBS_ENABLED | 이번에는 초기 운영 설정 유지. 순위·이력 보존을 끄는 일반 토글로 노출하지 않음 |
| 서버/복구 기반 | DB/포트/host/접근 토큰/HMAC/파일 경로/신뢰 프록시 | 초기 설정 유지 |
| 자동 생성되는 계좌 메타데이터 | account_ref, execution_run_id, profile binding | 새 활성 신원에서 결정. env ref/run은 구 설치 최초 이관 검증에만 사용 |

API 키 변경은 모의 수동주문 허용이나 자동매매 활성화를 뜻하지 않는다.
AI provider=none의 현재 의미는 요청 공급자 fallback을 허용할 수 있으므로,
이번에 이를 임의로 '전체 AI 중지'로 재해석하지 않는다. 완전 중지는 별도 명시 정책이다.

## 4. 비밀 저장과 최초 이관

### 4.1 선택한 저장 경계

신규 central_server/credential_store.py 하나가 파일 형식·암호화·원자 교체·revision을 소유한다.
7종 공급자는 고정 enum으로 제한하고 API로 파일 경로나 임의 env 이름을 받지 않는다.

- 새 전용 영속 디렉터리 예: NAS deploy/synology/server-secrets → 컨테이너 /app/secrets.
  시장 DB/server-data와 분리하며 .gitignore·.dockerignore·NAS 동기화 제외 목록에 추가한다.
- master.key는 최초 사용 시 라이브러리의 난수 생성으로 자동 생성한다. 사용자가 API 키를
  바꿀 때마다 master key를 입력하지 않는다. 새 env 비밀값을 매번 요구하지 않는다.
- Linux에서 디렉터리 0700, 파일 0600, 전용 디렉터리 심볼릭 링크/경로 이탈 거절.
  일반 사용자와 공유 폴더 권한도 배포 단계에서 확인한다.
- (provider, credential_profile_id)별 envelope 파일: schema_version, provider, credential_profile_id, active_revision,
  active encrypted payload, 이전 revision 한 개(복구용), 적용 메타데이터.
  raw 키 쌍·계좌 활성화 메타데이터·request_id·operation_id·keyed request digest는
  암호화 payload에 함께 묶는다.
- cryptography의 AESGCM, 256bit 키, 매 저장 새 96bit nonce, provider/profile/schema/revision을
  associated data로 인증한다. 암호문 바꿔치기·손상은 복호화 오류로 처리한다.
- 같은 디렉터리에 제한 권한 임시파일 생성 → flush/fsync → os.replace → 디렉터리 fsync.
  프로필별 처리와 파일쓰기 잠금으로 동시 저장을 직렬화한다. Uvicorn 한 worker가 전제이며,
  다중 서버 프로세스가 같은 vault를 쓰는 배포는 이번 지원 범위에서 명시적으로 거절한다.
- OAuth access token은 지금처럼 메모리에만 둔다. 저장할 것은 App Key/Secret 등 장기 인증이다.
- master와 파일이 같은 NAS에 있으므로 NAS 관리자 침해까지 방어하지는 않는다.
  목적은 일반 DB·설정 동기화·Git·진단·화면 조회에 인증정보가 섞이지 않게 하는 것이다.

### 4.2 우선순위·손상·삭제

1. 공급자 파일이 처음부터 없는 경우에만 env 키를 초기 호환값으로 읽는다.
   첫 파일 생성 전에 기존 중앙 문서 저장소에 비밀 없는 공급자·프로필별 초기화 표식을 남긴다.
   초기화 표식 또는 activation/disable 이력이 있는데 파일이 없으면 유실로 판정한다.
   첫 파일 생성 도중 중단된 경우도 RECOVERY_REQUIRED에서 명시적인 이관 재시도로 복구한다.
2. 공급자 파일이 있으면 명시적 disabled 기록까지 env보다 우선한다.
3. 복호화 실패/파일 손상/키 분실 시 옛 env 키로 조용히 되돌아가지 않는다.
   해당 공급자를 RECOVERY_REQUIRED로 두고 다른 정상 공급자와 DB 조회는 계속한다.
   파일 revision이 DB의 확정 revision보다 작아도 과거 파일 복원으로 보고 조용히 활성화하지 않는다.
4. 암호문이 있는데 master가 없으면 새 master를 자동 생성해 덮어쓰지 않는다.
5. 최초 env 이관은 검증 전 사용 가능한 기존 자격을 보존한다. 검증 실패했다고 기존 파일이나
   env를 삭제하지 않는다. 임포트 여부와 인증 성공 여부는 별개 상태로 보인다.
6. 앱의 빈 입력은 미변경이다. API에서 replace는 필요한 키 쌍 전체를 요구하고 빈 문자열/
   한쪽만 전달을 거절한다. 삭제는 명시적 disable 명령과 tombstone으로 표현한다.
7. 일반 백업에 master/키/암호문을 포함하지 않는다. 별도 보호 복구 절차에서 vault와 master를
   따로 보관하거나, 분실 시 공급자 키를 재입력한다. 계좌 HMAC 키는 기존 보호 복구 규칙을 유지한다.
   HMAC 키와 새 암호화 master를 재사용하지 않는다.

기존 DEVELOPMENT_GUARDRAILS 18번의 '백업'은 구현 시 일반 백업 제외와 별도 보호 복구를
명확히 구분해 갱신한다. 일반 설정/뉴스/Google Drive/NAS 동기화에는 신규 비밀 필드를 넣지 않는다.

### 4.3 네트워크·오류 노출

앱 → NAS 비밀 쓰기는 system_ssl_context의 인증서 검증을 사용하는 HTTPS로 제한한다.
기존 DSM 역방향 프록시 구성을 재사용한다. HTTP 주소에서 키를 보내는 fallback과 인증서 검증 해제는 없다.
신뢰할 프록시 주소만 명시하고 전달된 scheme을 신뢰한다. 임의 X-Forwarded-Proto로 우회할 수 없어야 한다.
credential HTTP client는 모든 redirect를 거절한다. 기존 NAS Bearer 인증을 사용하고 키 조회 API는 만들지 않는다.

인증 PUT/POST body, Pydantic 검증 실패의 input/body, 공급자 return_msg, URL query,
예외 repr, 토큰 응답을 그대로 로깅/응답하지 않는다. request 크기·필드 길이를 제한하고
필드명/안전한 코드만 반환한다. SecretStr만으로 422 응답 안전성을 보장한다고 간주하지 않는다.
이 API에서 받은 인증값은 PC 로컬 설정 미러나 자동 백업에 저장하지 않는다.

## 5. 키 변경 API와 적용 상태

기존 /api/v1/settings/operations에는 비밀값을 추가하지 않는다.
신규 capability runtime_credentials_v1, multi_account_query_v3, scoped_mock_orders_v2와
공급자별 지원 목록으로 구 NAS와 구분한다.
계좌 신원 기능이 준비되지 않은 NAS는 account_setup_required 상태를 반환한다.

| 제안 API | 입력 | 출력/계약 |
| --- | --- | --- |
| GET /api/v1/settings/credentials | 없음 | 공급자·프로필별 configured/source/revision/validation/runtime 상태, 익명 연결 계좌, 지원 기능. 키/토큰/키 끝자리 없음 |
| POST /api/v1/settings/credentials/{provider}/profiles | request_id, label | 키 없는 draft profile ID 생성. 같은 request_id 재시도는 같은 ID |
| POST /api/v1/settings/credentials/{provider}/profiles/{profile_id}/prepare | request_id, expected_revision, replacement 키 쌍 또는 명시 disable | 202 operation_id. 검증·계좌 미리보기 시작, 활성 키/binding 미변경 |
| GET /api/v1/settings/credential-operations/{id} | 없음 | 진행 단계, 준비 만료시각, 같은/다른 계좌 판정, safe error. 비밀값 없음 |
| POST /api/v1/settings/credential-operations/{id}/apply | expected_revision, 확인한 target_account_ref(계좌 변경 시) | 202 적용 시작. 이미 같은 operation 적용 시 동일 결과 |
| DELETE /api/v1/settings/credential-operations/{id} | 없음 | 준비 단계만 취소. 적용 시작 후에는 중도취소 불가 상태 반환 |

request_id는 UUID, 공급자·프로필과 expected revision에 묶는다. 같은 ID 다른 payload 재사용은 409.
서버는 request digest를 keyed HMAC으로 계산해 비교하고 원문 키를 식별값으로 쓰지 않는다.
prepare 자료는 TTL 5분, 프로필별 최대 1건, 전체 고정 상한; raw 후보는 메모리에서만 유지한다.
앱은 비동기 worker로 작업 상태를 확인한다. 네트워크 timeout을 실패로 단정해 새로운 ID로
apply를 반복하지 않는다. 재시작 전 미적용 후보는 만료되고 다시 준비한다.
활성 commit에 기록된 operation은 재시작 후에도 성공/복구 필요 상태를 조회할 수 있다.

상태는 준비 VALIDATING → READY → 적용 DRAINING → COMMITTING → ACTIVE.
키 입력 불량은 FAILED, 낡은 revision은 CONFLICT, 미완료 통신은 BUSY.
검증 전 저장을 허용하는 AI는 validation=UNVERIFIED를 별도로 표시한다.
runtime의 DEGRADED/RECOVERY_REQUIRED는 저장 성공 여부와 구분한다.
같은 자격 재입력은 활성 연결이 정상일 때만 로컬 비교 후 no-op이다.
같은 키라도 인증 실패로 비활성화된 상태라면 재검증·복구를 실행할 수 있어야 한다.

### 파일·DB·실행 객체의 일관성

세 경계를 한 트랜잭션인 것처럼 취급하지 않는다.

1. 후보 검증과 신원 조회를 완료하고 익명 대상 scope를 찾는다. 신규 registry identity 생성은
   허용하되 현재 credential profile의 binding은 아직 바꾸지 않는다.
2. 해당 공급자의 신규 작업 접수를 잠그고 이미 전송된 호출을 끝낸다. 준비/적용 deadline은
   네트워크 timeout과 구분한다. 기다리는 화면은 즉시 상태를 받고 서버 전체를 기다리지 않는다.
3. 새 키와 target scope/run, operation_id를 vault active에 원자 저장한다. **이것이 durable commit**이다.
4. 계좌는 activation operation_id를 멱등키로 DB binding을 한 번만 확정한다.
   기존 append_account_binding의 매번 revision 증가 동작을 재시작 복구에 그대로 쓰지 않는다.
5. DB binding과 vault의 scope/revision이 일치한 후 새 runtime을 공개하고 필요한 gate를 연다.

commit 전 오류는 기존 설정 유지. commit 이후 DB/실행 연결 실패는 새 active를 기준으로
복구하며 RECOVERY_REQUIRED를 반환한다. 이때 '옛 연결로 복구 완료'라고 응답하지 않는다.
시작 시 vault의 미완료 activation을 멱등 finalize한 다음 계좌 기능을 연다.
정말 이전 키로 돌아갈 때는 별도 새 activation으로 기록하고 과거 revision을 삭제하지 않는다.
외부 키 폐기/기간 만료 때문에 옛 키까지 무효할 수 있으므로 rollback 성공을 보장하지 않는다.

신규 DB 테이블 central_credential_activations는 operation_id(PK), provider,
credential_revision, request_id, request_digest, profile_id, account_ref, run_id, binding_revision, committed_at만 보관한다.
계좌가 아닌 공급자의 계좌 관련 열은 NULL이다. 과거 operation 재시도도 이 원장에서 찾아
이미 적용된 요청을 다시 실행하지 않는다. DB 기록 전에 중단된 최신 commit은 vault에서 복구한다.
키/토큰/원계좌번호는 없다. binding append와 activation insert는 같은 DB 트랜잭션으로 실행한다.
중앙 스키마의 구현 시점 다음 버전을 사용하고 SQLite/PostgreSQL 동등 마이그레이션을 추가한다.

## 6. 키움 연결의 수명과 계좌 정책

### 6.1 공통: 현재 broker와 한도 유지

실전 5회와 모의 1회 한도, 요청 잠금, 최근 호출 시각, WS는 검증된 계좌 연결별로 별개다.
초안의 '후보 client/broker를 새로 띄워 검증'은 같은 환경의 합산 제한을 우회할 수 있어 수정한다.
기존 키 갱신의 후보 OAuth/ka00001 검증도 **해당 계좌 연결의 기존 요청 직렬화·호출 간격**에 참여한다.
새 계좌는 제한된 준비 큐에서 신원을 확인한 뒤 별도 broker를 만든다.
추가 계좌가 검증되기 전에 한도가 독립이라고 가정해 기존 계좌와 중복 송신하지 않는다.

KiwoomRestClient에 후보 자격 검증/활성화 메서드를 추가하되 요청 잠금·최근 호출 시각을
유지하고 후보 토큰은 활성 토큰과 구분한다. 검증 HTTP는 기존 client의 전송/간격 함수를 재사용한다.
후보 ka00001은 CentralRestBroker 내부 관리 작업으로 실행해 직접 HTTP 우회 경로를 만들지 않는다.
실전 검증 작업은 ka00198보다 낮은 우선순위다. 검증 한 호출 동안의 지연은 가능하지만
대기 중 순위를 밀어내는 별도 키움 병렬 큐를 만들지 않는다.

회전 시 broker의 새 접수/계좌 페이지/모의 submit·cancel·reconcile/WS 토큰 재발급까지
동일 계좌 연결의 적용 gate를 따른다. 이미 송신한 동기 호출은 결과와 DB 반영까지 기다린다.
asyncio task 취소는 to_thread 내부 작업 종료를 뜻하지 않는다.
시간 초과면 BUSY로 남기고 실제 작업이 끝나기 전 새 계좌 연결을 열지 않는다.
pending queue는 세대별로 명시 오류로 종료하거나 재접수 가능 상태로 넘긴다.
낡은 응답/continuation/토큰은 새 generation에 게시하지 않는다.

### 6.2 모의계좌

| 검증 결과 | scope/run/일지 | 주문·복구 정책 |
| --- | --- | --- |
| 같은 계좌의 새 키 | 기존 account_ref와 run_id 유지 | 신규 주문 잠금 → 실제 호출 drain → 키 교체 → 재조회/reconcile → 상태 확정 시 허용 복원 |
| 다른 계좌 | 새 profile과 registry의 account_ref, 새 run_id, 별도 일지 | 기존 profile 덮어쓰기 거절 후 새 계좌 추가; 기존 계좌 유지, 새 주문 허용 OFF |
| 이전 키 만료 + 미종결 주문 | 이전 scope/run과 미확정 상태 보존 | 새 키가 같은 계좌면 복구 조회 허용; 다른 계좌면 이전 run을 수동 확인 대상으로 남기고 새 계좌 조회 허용 |

미종결 주문이 있다는 이유만으로 키 갱신을 영구 차단하지 않는다.
단, 실제 submit/cancel/reconcile가 실행 중이면 종료까지 대기한다.
응답을 못 받은 UNKNOWN 주문은 자동 재전송/자동 취소/성공 처리하지 않는다.
현재 코드는 broker_order_id가 없는 UNKNOWN을 자동 일치시키는 근거가 부족하므로 수동 확인으로 남긴다.
새 계좌의 같은 주문번호를 이전 계좌 주문에 연결하지 않는다. 이전 주문을 새 scope로 이관하거나 alias로 합치지 않는다.
이전 계좌를 다시 등록하면 HMAC identity 기준으로 기존 scope를 찾으며 이름이나 화면의 '상시/대주'로 판단하지 않는다.

신규 central_server/mock_runtime.py는 기존 app.py 모의 조립 블록과 start/drain/close를
한 곳에서 재사용하는 bundle이다. 단순 Manager 래퍼를 여러 개 만들지 않는다.
ExecutionRuntime에 신규 주문 gate, 활성 owner/generation 검사, 실제 작업 drain, 조건부 lease release를 추가한다.
ExecutionRepository의 모든 늦은 저장/갱신도 소유권을 확인한다. 기존 60초 lease 만료를
무조건 기다리거나 강제로 다른 owner의 lease를 지우는 것으로 해결하지 않는다.
새 runtime이 lease와 계좌 준비를 마치기 전 app.state/라우트에 활성이라고 공개하지 않는다.
HTTP 요청은 시작 시 획득한 bundle을 응답 문서 작성까지 유지한다. 현재 app.py의
mock_order_document가 전역 gateway를 다시 읽는 경로도 함께 고쳐야 한다.
WS의 계좌 scope resolver는 해당 연결 세대의 identity/binding을 캡처하며 늦은 옛 frame이
현재 전역 binding을 읽어 새 계좌 자료가 되지 않게 한다. health/capability/route도
하나의 활성 runtime 상태를 조회하고 시작 시 설정의 bool만으로 사용 가능을 표시하지 않는다.

### 6.3 실전 시세·계좌

기존 CentralRestBroker, RealtimeHub, AutonomousTop20Service, MarketEventService,
CentralRealtimeCollector는 유지한다. app 전체 재생성이나 collector 누적기 복사를 하지 않는다.
collector에는 연결만 중지/재인증하는 명시 메서드를 추가한다.

- 해당 계좌 client의 인증 활성화 후 그 계좌의 기존 WS를 완전히 닫고 새 WS 하나를 연다.
  모든 REG 승인 뒤 준비 완료를 알리고, hub의 최신 union으로 등록한다.
- 보류 저장/초·분봉 누적/Top20 outbox/조건 코호트는 보존한다.
  공백 포함 구간은 capture gap으로 기록하며 복귀 첫 누적값을 중복 가산하지 않는다.
- AccountQuerySessionManager는 활성 binding 교체 전 세션을 무효화한다.
  새 key로 예전 next_key를 계속 사용하지 않는다. 계좌조회 캐시/세대도 분리한다.
- 준비 중 표를 비우거나 과거 표를 최신 성공으로 표시하지 않는다.
  planned reconnect를 health, capabilities, 중앙 realtime worker와 failover 정책에 연결한다.
  유한 적용 deadline 안에서만 계획된 재연결로 처리하고, 이후 실제 실패는 기존 장애전환 정책으로 복귀한다.
  실패를 영구히 숨기는 '설정 변경 중' 상태를 만들지 않는다.
- 시장 WS와 계좌 신원 검증 결과는 각각 상태로 표시한다.
  신원 복구 중에는 계좌 데이터 게시를 차단하고 검증된 시세 기능은 독립적으로 운용한다.
- 같은 NAS에서 실전↔모의 환경 자체 전환은 이 API로 허용하지 않는다.
  고정 provider kiwoom_real/kiwoom_mock 안의 지정한 프로필별로 갱신한다.
  이 절의 시장 collector 보존/재연결은 market_profile_id 담당에만 적용한다.
  비담당 계좌 변경은 해당 계좌 연결만 갱신한다.

## 7. 뉴스·AI·자주 바꾸는 운영값

CentralNewsService와 NewsJobRunner를 키 변경마다 close/start하지 않는다.
watchlist 네이버 client, QuerySetNewsCollector 네이버 client, DART client 및 AI 인증 snapshot을
각 소유자의 명시 메서드로 갱신한다. 처음 키가 없어서 service=None이었던 경우도 생성·연결해야 한다.
set_ai_service를 재사용해 job runner 참조까지 연결한다.

- watchlist 검색 한 번의 다중 페이지, query-set 한 검색어의 페이지 순회는 시작 인증 revision으로 완료한다.
- AI는 논리 요청 접수 시 provider/model/credential revision을 고정한다.
  대기·진행 중 중복 병합에는 revision을 포함하되 이미 성공한 결과 캐시는 키 변경만으로 폐기하지 않는다.
- 키 변경으로 뉴스 원문·분류·source cursor·pending marker·일일 사용량·선택 우선 종목을 초기화하지 않는다.
  이전 키 작업의 완료/오류가 새 revision의 인증 상태를 덮어쓰지 않는다.
- 네이버 검증 요청도 기존 claim_news_request 예산을 사용한다.
  예산 부족·점검·timeout은 INVALID_CREDENTIAL과 구별해 UNVERIFIED/RETRYABLE로 표시한다.
- DART는 기존 기업코드 캐시를 유지한다. AI 저장 과정에서 유료 기사 분석을 자동 실행하지 않는다.
  AI/DART의 강제 검증 수단이 없으면 저장됨·미검증 상태를 제공하고 첫 실제 요청으로 판정한다.
- API 키 교체/요청 예산 증가를 이유로 같은 날짜 사용량을 지우지 않는다.
  네이버 총 예산은 기존 상한 범위에서 watchlist+query-set 합계와 원자적으로 검증한다.
  현재 사용량보다 새 한도가 낮아지면 신규 호출만 멈추고 이미 사용한 수치를 유지한다.
- 조건식 변경은 기존 단일 WS의 조건 등록 절차를 따르며 확정 전 기존 선택을 유지한다.
  기존 당일/익일 코호트를 삭제하지 않는다. 새 선택은 이후 관측부터 적용하고 종료/실패 사유를 공개한다.
- 해외시세 주기·상품 변경은 기존 collector가 다음 회차부터 반영하고 자료/roll 이력을 유지한다.
  중지/재개에도 시장 순위 collector는 건드리지 않는다.

operations GET에 revision/적용 상태를 추가하되 기존 필드는 유지한다.
새 클라이언트는 expected_revision과 실제 변경 필드만 PUT한다.
기존 update()도 full snapshot을 다시 보내지 않도록 바꾸고 구 클라이언트는 누락 revision을 허용하되
서버에서 직렬화한다. 구 클라이언트의 전체 PUT 덮어쓰기까지 방지한다고 주장하지 않는다.
proposed 검증 → DB 저장 → 적용 순서를 따르고, DB 실패 시 공개 메모리는 기존 상태 그대로다.
저장 후 적용 오류는 persisted revision과 applied revision을 구분해 재시작/재시도에서 복구한다.

## 8. 최소 구현 구조

| 파일 | 책임 |
| --- | --- |
| 신규 central_server/credential_store.py | 암호화 파일, revision, 원자 commit, 기존 env 최초 이관 |
| 신규 central_server/credential_runtime.py | 프로필별 operation/lock/TTL와 계좌별 활성 bundle, prepare/apply/recovery 조정. 공급자 enum 고정 |
| 신규 central_server/mock_runtime.py | 기존 모의 객체 조립 및 해당 연결의 실제 수명 |
| 기존 config.py/app.py/contracts.py | 초기 설정과 활성 인증 합성, API 입력·인증·capability, 상태 조립 |
| 기존 client.py/rest_broker.py/realtime_collector.py | 환경별 요청 간격·교체 장벽·WS 재인증과 누적 상태 보존 |
| 기존 account_identity.py/account_query.py/execution_runtime.py/execution_repository.py/database.py | 계좌 멱등 활성화, 조회 세션·주문·소유권 경계 |
| 기존 news_service.py/news_sources.py/ai_service.py/market_events.py/external_market_collector.py | 각 기능의 런타임 인증·운영값 갱신 |
| 신규 infrastructure/central_credentials_client.py | 쓰기 전용 자격 API, HTTPS·redirect 제한·safe error. 기존 operations client와 비밀 body 분리 |
| 신규 presentation/nas_credentials_dialog.py | NAS 인증 입력/상태·worker 수명; 기존 ApiSettingsDialog에서 열기 |

현재 인증 변경 이해에는 config/app/client/provider 약 4개 파일이 필요하다.
변경 후에는 저장 경계/적용 조정/필요 시 UI를 더해 약 6~7개가 필요하나, 각 파일이 실제
영속성 또는 수명을 소유한다. 라우트→manager→service→provider 같은 단순 전달 계층은 추가하지 않는다.
한 단계에서 기존 대형 app.py 전부를 분리하지 않고 모의 조립 중복이 생기는 부분만 추출한다.

## 9. Sol 단계별 구현·완료·테스트 계약

앞 단계 완료 후 다음 단계로 진행한다. 각각 코드·단위 회귀를 끝내되 NAS 중간 배포는 하지 않는다.

### R0. 운영 설정 정확성·UI 지연 선행 보완

로컬 구현 완료. 변경 필드 전송·revision 충돌·저장 실패 보존·적용 실패 재시도,
NAS/뉴스 설정 비동기 실행과 창 해제 후 완료 수명을 반영했다.
서버/설정 GUI/후보/뉴스/AI 관련 회귀 87개 통과, 프로세스 종료 코드 0.
수정 파일 compileall과 diff 공백 검사 통과. 실패 주입 테스트의 저장/적용 오류 로그는 예상 결과다.
실제 NAS 이미지·다른 PC에서의 운용 확인은 R7에 남긴다.

- 목적: 현재 설정의 stale 덮어쓰기/DB 실패 불일치를 제거하고 새 설정창이 멈추지 않게 함.
- 대상: central_operational_settings.py, app.py operations, api_settings_dialog.py, news_settings_dialog.py.
- 입출력: 기존 GET/부분 PUT 유지, 새 revision 선택 필드와 persisted/applied 상태 추가.
- 완료: 다른 화면의 미변경 필드를 전송하지 않음; DB 실패 시 기존 메모리/실행값 유지; 모든 관련 HTTP는 worker에서 실행.
- 테스트: 두 화면 동시 수정, stale revision 409, 구 NAS/구 클라이언트, DB write 실패,
  지연 HTTP 중 창 이동·닫기·중복 저장. Qt deleteLater/DeferredDelete 후 프로세스 exit=0 확인.

### R1. 암호화 저장·설정 합성·계좌 활성화 원장

로컬 구현 완료. [R1 결과](RUNTIME_API_SETTINGS_R1_IMPLEMENTATION.md)에 구현·검증 범위를 기록했다.
파일 저장/기동 합성/멱등 활성화/중앙 v19를 추가했다. 복수 계좌 runtime·실행 중 교체·
입력 UI는 R2 이후이며 실제 NAS PostgreSQL·POSIX 권한 확인은 R7에 남긴다.
기존 주 실행 환경이 mock이면 모의 모니터 키와 혼합하지 않도록 주 실행 키만
`nas-main-mock-default` 파일로 보존한다. 정상 real 시장 프로필과 기존 계좌 신원은 유지한다.

- 목적: 재시작과 DB/파일 사이의 중단에도 계좌·키가 혼합되지 않게 함.
- 대상: 신규 credential_store.py, config.py, application/account_identity.py,
  database.py/central_schema.py, pyproject.toml, server.Dockerfile, compose 및 제외 규칙.
- 입출력: provider+profile+expected revision → encrypted active envelope; operation_id → 하나의 binding activation.
  기존 env 초기값은 fallback, 새 저장된 disabled는 env보다 우선.
- 완료: env 키를 비워도 vault에서 시작; 손상 공급자만 복구 필요; 매번 env ref/run 입력 불필요.
- 테스트: 암호문 roundtrip/변조/다른 provider 복사/권한/master 분실/동시 write/replace 실패,
  DB activation 재시도 멱등성, 이전 DB fixture 마이그레이션/rollback/SQLite·PostgreSQL 동등성.
  fixture 비밀은 테스트 실행 때만 생성하고 응답·로그·일반 백업 불포함을 확인.

### R2. 준비·적용 API 및 공통 교체 장벽

공통 코드 로컬 완료. [R2a REST 장벽 결과](RUNTIME_API_SETTINGS_R2_BARRIER_IMPLEMENTATION.md)와
[R2b API 결과](RUNTIME_API_SETTINGS_R2_API_IMPLEMENTATION.md)를 따른다.
operation/profile API·TTL·idempotency·HTTPS·safe body 처리·commit 조정을 구현했다.
실제 mock/news/real owner 연결은 기존 R3/R5/R6 순서다. 미연결 provider는 prepare를 503으로
거절하므로 공통 API capability만 보고 실행 중 키 교체가 가능하다고 판단하지 않는다.
실제 NAS proxy/TLS·PostgreSQL·프로세스 중단 검증은 R7이며 지금은 배포 전이다.

- 목적: timeout/중복 클릭/서버 중단에도 중복 적용하지 않고 기존 호출을 안전하게 끝냄.
- 대상: credential_runtime.py, app.py/contracts.py, client.py/rest_broker.py.
- 입출력: 5절 API, 고정 공급자/순서/TTL, 202 operation_id와 단계별 상태.
- 완료: revision/idempotency 작동; 실제 to_thread 종료 전 활성 세대 변경 금지;
  키움 후보 검증은 환경별 기존 간격과 순위 우선순위 준수.
- 테스트: 요청 취소 뒤 thread 지연 완료, pending queue, WS 토큰 refresh 동시성,
  위조 forwarded header/인증 실패/422 body 비노출, apply 각 경계 프로세스 중단 주입.
  commit 전 기존 active, commit 후 새 active 복구, 재시작 후 candidate 만료 검증.

### R3. 모의계좌 키 갱신·새 계좌 활성화

진행 중. [R3a 주문 장벽 결과](RUNTIME_API_SETTINGS_R3_COMMAND_BARRIER_IMPLEMENTATION.md)를 먼저 구현했다.
HTTP 대기자가 취소돼도 gateway가 실제 명령 task를 소유하며 잠금 후 drain을 기다린다.
ExecutionRuntime은 실제 동기 주문·취소·reconcile와 종료를 직렬화하고 느린 전송 중 heartbeat는 유지한다.
lease release는 account/run/owner가 같은 경우만 해제한다. 이 메서드를 운영 계좌 교체에 연결하지 않았다.
후속 [R3b monitor/DB 결과](RUNTIME_API_SETTINGS_R3_MONITOR_FENCE_IMPLEMENTATION.md)에서 monitor/WS
실제 종료와 늦은 DB 쓰기의 트랜잭션 소유권 검사를 구현했다. 정상 monitor 종료는 실제 작업을
drain한 뒤 현재 lease만 해제하며 새 계좌 교체 owner는 아직 연결하지 않았다.
후속 [R3c bundle 결과](RUNTIME_API_SETTINGS_R3_BUNDLE_IMPLEMENTATION.md)에서 기존 모의 조립을
계좌/run/profile 고정 bundle로 연결하고 owned start/close·연결별 신원·요청별 gateway 캡처를 구현했다.
인증·계좌 불일치·monitor 시작 실패는 모의 기능만 닫으며 불일치 신원을 binding으로 확정하지 않는다.
후속 [R3d 계좌 설정 결과](RUNTIME_API_SETTINGS_R3_ACCOUNT_SETTINGS_IMPLEMENTATION.md)에서
검증 scope별 운영 문서·설정 revision CAS·중복 profile 활성화의 트랜잭션 롤백과 인증 GET을 구현했다.
기본 설정 생성은 모의 binding/활성화와 같은 DB 트랜잭션이다. 신규 계좌 주문 OFF를 저장하며
같은 profile 갱신과 완료 replay는 사용자 설정을 되돌리지 않는다.
후속 [R3e 모의 owner 결과](RUNTIME_API_SETTINGS_R3_MOCK_OWNER_IMPLEMENTATION.md)에서
복수 모의 계좌 owner와 실제 prepare/apply hooks를 연결했다. 동일 계좌의 client/큐/UUID/run을
유지하며 신규 계좌는 별도 run·실제 주문 OFF로 조립한다. monitor ON은 초기 조회/lease 준비 후
ACTIVE를 공개하고 OFF 설정은 모니터를 시작하지 않는다. 초기 boot는 계좌별 비동기·최대 2개이며
선택 사용자의 새 적용이 대기 중인 구형 bootstrap보다 우선한다.
후속 [R3f 계좌 제어 결과](RUNTIME_API_SETTINGS_R3_ACCOUNT_CONTROLS_IMPLEMENTATION.md)에서
키 비활성화 tombstone·기존 binding 보존과 실제 계좌 설정 PUT을 연결했다. 토글은 실제 종료→CAS 저장→
새 bundle 준비를 거치며 저장 후 실패는 접수를 닫고 동일 설정으로 복구할 수 있다.
후속 [R3g 선택 계좌 API 결과](RUNTIME_API_SETTINGS_R3_SCOPED_ACCOUNTS_IMPLEMENTATION.md)에서
v3 명시 계좌 조회·v2 선택 모의 주문/조회/취소와 계좌별 cursor/owned query 수명을 연결했다.
R3 서버 기능은 로컬 완료다. 다음은 R4 입력/계좌 선택 UI와 PC client/worker context 전달이다.
NAS 동기화·실환경 검증까지 완료로 표시하지 않는다.

- 목적: 사용자의 모의투자 갱신 문제를 컨테이너 재시작 없이 해결.
- 대상: mock_runtime.py, mock_account_monitor.py, execution_runtime.py,
  execution_repository.py, app.py의 mock routes와 startup, account identity 연결.
- 입출력: 검증 신원 → 같은 scope/run 또는 새 scope/run. 새 계좌 주문 허용 OFF.
- 완료: 현재 같은 인증 실패 상태에서도 새 키로 계좌 조회 복구; 실전 broker·순위·뉴스 유지.
- 테스트: 같은 계좌, 신규 계좌, 과거 계좌 재등록, 이전 키 만료,
  in-flight submit/cancel/reconcile, order ID 없는 UNKNOWN, lease release/CAS와 늦은 owner write,
  startup 인증/계좌 불일치 시 서버 전체 종료 없이 모의만 비활성화.

### R4. NAS 인증 입력 UI와 실제 모의 경로 연결

R4는 기존 기본 v2 조회와 시세 경로를 유지하며 두 부분으로 구현했다.
- R4a 로컬 완료: 설정의 NAS 메뉴에서 모의 프로필 추가·빈 키 입력·prepare 상태/계좌 확인·
  명시 apply·비활성화·계좌 조회/수동 모의주문 허용을 관리한다. HTTPS만 사용하고 redirect는 거절한다.
  키는 PC 설정/미러에 쓰지 않으며 같은 NAS 대상의 창 재열기는 메모리의 안전한 요청 ID를 공유한다.
  적용 timeout은 같은 operation 상태만 조회한다. prepare 응답 자체가 유실돼 operation ID를 모르면
  같은 프로필/revision/disable/request_id로 키를 다시 입력해 확인하며 자동으로 새 요청을 만들지 않는다.
  계좌 설정 PUT 응답 유실은 GET으로 재확인한 뒤 CAS를 진행한다.
- R4b 로컬 완료: 조회 가능한 익명 계좌의 인증 GET `/api/v3/kiwoom/accounts`와
  `account_contexts_v3` capability를 추가한다. Qt 단건 worker로 목록을 가져오고 저장 일지가 없는
  신규 계좌도 선택할 수 있다. 선택 scope는 조회 시작 worker에서 현재 binding을 한 번 확인한 뒤
  고정하고 v3의 모든 날짜/페이지/비용 응답에 같은 context를 요구한다.
  빈 결과도 완료 context의 계좌로 저장하며 조회 도중 선택 변경/연결 revision 변경은 저장 전에 거절한다.
  PC client의 모의 submit/GET/cancel도 같은 context의 v2 경로만 사용한다. 응답 미확인 submit은
  같은 ID/내용을 우선 확인하고 새 ID를 막는다. 선택 NAS 계좌는 PC 단일 프로필로 fallback하지 않는다.
  기존 기본 계좌 v2와 시장자료 fallback는 유지한다. 새 자동주문 UI는 이 단계 범위가 아니다.

상세 계약과 검증은 [R4a 구현 결과](RUNTIME_API_SETTINGS_R4_NAS_UI_IMPLEMENTATION.md)와
[R4b 선택 계좌 연결 결과](RUNTIME_API_SETTINGS_R4_SELECTED_ACCOUNT_IMPLEMENTATION.md)를 따른다.

- 목적: 사용자가 env/등록 스크립트를 사용하지 않고 모의계좌를 갱신할 수 있게 함.
- 대상: central_credentials_client.py, nas_credentials_dialog.py, api_settings_dialog.py.
- 입출력: capability 및 익명 상태 조회, 빈 키 입력 유지, 확인한 계좌로 apply.
- 완료: 저장 대상 NAS 명확; PC 키 미변경; prepare/실패/적용/복구 상태 표시;
  같은 계좌는 일반 적용, 다른 계좌는 새 일지/이전 미확정 기록을 확인한 뒤 적용.
- 테스트: HTTPS 검증/redirect 거절/HTTP에서 입력 전송 0건, 구 NAS 비활성,
  GUI 중복 클릭·닫기·재열기, timeout 후 같은 operation 조회, 비밀값 설정 미러/백업 유출 0건.

### R5. 뉴스·DART·AI 및 운영값 확대

R5a 네이버 부분은 로컬 완료했다. 고정 `nas-naver-default` 프로필을 키 없는 설치에도 등록하고,
검증 검색의 실제 HTTP 요청을 기존 watchlist/공통 일일 예산에 합산한다. 종목별·공통 검색은
작업 시작 시 client를 고정하며 진행 중 모든 페이지와 성공/실패 DB 저장이 끝난 뒤 함께 교체한다.
HTTP waiter 취소와 수집 thread 종료는 구분한다. 대기 deadline 초과는 실제 종료 뒤 commit 없이
이전 연결을 재개한다. commit 시작 이후 실패는 예전 키를 복원하지 않고 NAVER만 부재로 둔다.
기사·cursor·작업·사용량을 초기화하지 않고, disable tombstone과 None→enabled를 지원한다.
API 계좌 대상은 null이며 계좌용 PC 관리 화면을 임의 재사용하지 않는다.
R5b DART도 로컬 완료했다. 고정 기본 프로필, 회사코드 캐시를 건드리지 않는 공시 검색 검증,
종목 task 접수 시 NAVER/DART/사용 여부 snapshot, 독립 pause와 실제 저장 drain을 연결했다.
동시 교체에도 한 공급자 resume가 다른 공급자 pause를 풀지 않으며 NAVER query-set는 DART 교체 동안 계속한다.
cache Path/30일 갱신 정책·운영 enabled·기사/cursor/작업/사용량을 보존한다.
인증 오류와 점검/한도/timeout을 stable 실패 코드로 구분한다. 상세: [R5b 보고서](RUNTIME_API_SETTINGS_R5_DART_IMPLEMENTATION.md).
R5c AI도 로컬 완료했다. OpenAI/Gemini/Claude 고정 기본 프로필을 keyless vault 설치에도 등록한다.
prepare는 유료 분석을 실행하지 않으며 UNVERIFIED 후보를 명시 apply한다. ACTIVE는 실행 키 적용 완료다.
첫 실제 분석 성공/401/403/429·5xx는 현재 revision의 runtime_validation만 갱신한다.
vault의 당시 validation은 불변이며 캐시 응답으로 새 키를 검증 완료로 처리하지 않는다.
본문 준비 전에 공급자/model/key/revision을 고정하고 접수 task와 공유 실행 task를 구분한다.
공급자별 교체는 준비·실행 대기·실제 분석·최종 저장을 drain한다. HTTP waiter 취소는 실제 작업을 취소하지 않는다.
공유 실행 키에는 body hash/revision을 포함하지만 저장 성공 캐시의 품질 키와 운영 일일 상한은 유지한다.
키 교체만으로 과거 AI 작업을 재예약하거나 사용량을 초기화하지 않는다. commit 이후 실패는 해당 공급자만 차단한다.
상세: [R5c 보고서](RUNTIME_API_SETTINGS_R5_AI_IMPLEMENTATION.md).
R5d1 일반 공급자 입력 UI도 로컬 완료했다. 기존 NAS 메뉴에 네이버/DART/AI 선택을 추가하고
기존 NasCredentialsDialog/CentralCredentialsClient를 공급자 고정 옵션으로 확장했다.
global 프로필에는 계좌 추가·조회·주문 설정을 표시하거나 요청하지 않는다. 서버 고정 기본 프로필만 표시한다.
키는 빈 password 입력으로 시작하고 전달 직전/종료 시 비운다. PC 설정/DB/백업에 저장하지 않는다.
공급자별 같은 NAS client를 재사용해 pending operation을 유지하고 다른 공급자/NAS와 섞지 않는다.
operation provider/profile/revision/ID/계좌 null/READY 검증 종류를 확인한 뒤 명시 apply한다.
timeout 뒤 같은 operation만 조회하고 AI ACTIVE/캐시를 인증 성공으로 표현하지 않는다.
상세: [R5d1 보고서](RUNTIME_API_SETTINGS_R5_PROVIDER_UI_IMPLEMENTATION.md).
R5d2a 해외 지연 시세 운영도 로컬 완료했다. 기존 operations에 enabled/poll/auto-roll/confirmation을
추가하고 saved OFF는 ENV ON보다 우선한다. 초기 OFF라도 같은 수집기를 준비해 재시작 없이 활성화한다.
기존 수집/roll/일봉 기준 캐시를 보존하고 단일 owned cycle로 thread와 최종 저장을 drain한다.
설정 갱신도 수집기 owned task로 직렬화해 HTTP waiter 취소 뒤 완료하고 shutdown은 이를 기다린다.
저장 전 실패는 기존 실행을 유지한다. 저장 후 실패는 기존 revision/applied_revision의 복구 필요를
표시하고 같은 revision 명시 재저장으로 회복한다. PC는 구 NAS의 누락 필드를 보내지 않는다.
상품/활성 월물 수동 변경은 이번 입력에 포함하지 않는다. 초기 symbol과 기존 자동 roll 계약을 유지한다.
상세: [R5d2a 보고서](RUNTIME_API_SETTINGS_R5_EXTERNAL_OPERATIONS_IMPLEMENTATION.md).
R5d2b 조건검색 운영도 로컬 완료했다. 기존 operations에 enabled/정확한 이름/substring을 추가하고
같은 market_events 서비스와 단일 WebSocket 수신 loop에서 적용한다. 새 조건 전체 초기 결과를
확인한 뒤 전환하고 기존 등록을 해제한다. 실패/시간초과/잘못된 페이지/해제 실패를 실행 상태로
공개하며 기존 코호트와 VI/체결 수집을 유지한다. 초기 결과 다음 실시간 신호를 적용하고 접수 queue의
조건 문맥을 고정한다. late 실패는 operations GET도 복구 필요로 공개하며 같은 영속 revision으로
명시 재저장할 수 있다. 조건검색 OFF에도 기존 종목의 당일/익일 보존을 유지한다.
상세: [R5d2b 보고서](RUNTIME_API_SETTINGS_R5_CONDITION_OPERATIONS_IMPLEMENTATION.md).
R5d2c 공통 뉴스 query-set 운영 UI도 로컬 완료했다. 기존 서버의 ON/OFF·검색어·주기를
NAS 설정창에 연결했고 기존 worker/부분 PUT/revision CAS와 구 NAS 필드 제외를 유지한다.
한 줄 검색어에서 공백/빈 줄/중복을 정리하며 최대 50개와 UI ON의 빈 목록을 검증한다.
PC 직접 뉴스 키/설정에 검색어를 복사하지 않는다. NAS 폼은 스크롤하고 저장 버튼은 유지한다.
OFF/교체 뒤 이전 cycle의 나머지 검색어가 조회되는 문제와 단축 주기가 이전 예약을 기다리는 문제를
가짜 실행으로 재현했다. 같은 collector에서 접수 검색어 완료 뒤 새 정책을 확인하고 정상 완료
last_success + 현재 주기로 다음 수집을 판정한다. 실패 backoff/부분 페이지 예약·기사/cursor/예산은 보존한다.
상세: [R5d2c 보고서](RUNTIME_API_SETTINGS_R5_NEWS_QUERY_OPERATIONS_IMPLEMENTATION.md).
다음은 R6 실전 인증 교체다. R7까지 중간 NAS 배포 없음.
상세: [R5a 구현 보고서](RUNTIME_API_SETTINGS_R5_NAVER_IMPLEMENTATION.md). R7까지 중간 NAS 배포 없음.

- 목적: 공급자별 키 갱신과 일상 수집 설정을 같은 NAS 화면에서 처리.
- 대상: news_service.py/news_sources.py/ai_service.py, existing operations,
  market_events.py/external_market_collector.py, 관련 UI.
- 입출력: 인증 revision snapshot 및 proposed operational changes.
- 완료: 키 없던 설치에서 활성화; watchlist/query-set 둘 다 새 키 사용;
  기존 기사 전체 재수집·분류 재예약·사용량 초기화 없음; 공급자 한 곳 실패를 다른 기능에 전파하지 않음.
- 테스트: 다중 페이지 중 회전/AI 대기 중 회전/옛 작업 늦은 실패,
  예산 하향/교체 후 합산 사용량 유지, None→enabled, 조건식 등록 실패/구 코호트 보존,
  해외시세 중지·재개와 roll 이력 보존. 원문/요약 품질 로직은 이 단계에서 변경하지 않음.

### R6. 실전 인증 교체

기존 R6 범위를 수명 경계 → 실제 적용 → PC 표시 순으로 나눠 진행한다.
- R6a 로컬 완료: 기존 실시간 collector의 계획된 pause/drain/resume·세대 fence,
  실제 token thread/socket 종료/직렬 owned 저장/장 마감 처리 완료, 같은 source 재등록의
  누적 baseline/연속 관측시각 재설정, 계좌 조회 pause/실제 접수 drain/선택적 cursor 폐기.
  취소 대기자가 실제 저장/장 마감 완료 전에 끝나는 문제를 수정 전 재현했다.
  기존 aggregator/실패 저장 대기분·hub/순위 및 독립 real/mock 한도를 유지한다.
  이 기반은 아직 실전 인증 hook/PC 입력에 연결하지 않았다. 새 공개 endpoint/SQL 없음.
  상세: [R6a 보고서](RUNTIME_API_SETTINGS_R6_RECONNECT_BARRIERS_IMPLEMENTATION.md).
- R6b1 로컬 완료: 기존 DB 활성화 트랜잭션의 모의 전용 계좌 설정 claim을 실전에도 적용했다.
  binding·활성화 원장·단일 active_profile_id가 함께 확정되며 중복 계좌 등록 실패는 모두 rollback한다.
  최초 조회 설정은 ON·모의주문은 OFF이고 키 갱신은 기존 설정/revision을 보존한다.
  disable은 과거 binding을 보존하며 적용 완료 replay가 이후 설정/새 프로필을 되돌리지 않는다.
  기존 실전 receipt의 설정 누락은 명시 replay 시에만 보완한다. 새 SQL/기동 일괄 변경은 없다.
  실제 kiwoom_real hook/시세 담당 선택에는 아직 연결하지 않았다.
  상세: [R6b1 보고서](RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_CLAIMS_IMPLEMENTATION.md).
- R6b2 로컬 구현: `real_runtime.py`의 실제 owner와 기존 HTTPS prepare/apply API를 연결한다.
  시세 담당은 기존 nas-real-default client/broker/collector를 유지하고 추가 실전계좌는 별도
  read-only client/broker/query 세션을 사용한다. v3 계좌 지정 조회와 keyless 최초 등록을 연결한다.
  후보 ka00001 검증·계좌 중복/불일치·동일 계좌 run/scope·활성화 원장·disable 이력을 보호한다.
  commit 후 실패는 기존 키를 다시 열지 않는다. paused 상태의 후보 검증도 기존 broker queue와
  같은 물리 요청 잠금/한도에서 수행한다. 최초 시세 담당은 아직 기존 기본 프로필로 고정한다.
  과거 체결/비용 조회 완료를 실전계좌의 실시간 잔고/미체결 수집 완료로 취급하지 않는다.
  상세: [R6b2 보고서](RUNTIME_API_SETTINGS_R6_REAL_CREDENTIAL_OWNER_IMPLEMENTATION.md).
- R6b3a 로컬 완료: 기존 문서의 persisted market_profile_id/불변 legacy 기본 역할·CAS/binding 자격 검증,
  담당 disable/연결 해제 보호와 인증된 GET을 구현했다. 이 단계 당시 적용 revision은 null이고 PUT은 없었다.
  내부 저장은 연결 변경 의도만 확정하며 실제 runtime 전환 완료로 취급하지 않는다.
- R6b3b1 로컬 완료: 기존 실전 owner의 async context manager에 역할 독점 구간을 구현했다.
  credential 후보는 첫 await 전에 예약되고 READY/apply/cancel까지 기존 예약을 유지한다.
  어느 실전 credential 후보라도 남아 있으면 역할 전환은 BUSY로 거절하며 사용자 확인을 기다리지 않는다.
  역할 예약 중 새 실전 credential 준비는 BUSY다. 모의 owner/시장 순위 조회는 이 예약을 쓰지 않는다.
  현재 역할·binding·활성 profile·owner에 적용된 vault revision과 같은 context를 검증/재검증한다.
  취소/예외는 예약을 해제하고 종료는 예약 종료를 기다리며 종료 중 새 작업을 거절한다.
  예약 구간은 실제 저장/연결을 변경하지 않는다. 공개 PUT이나 적용 revision은 추가하지 않았다.
- R6b3b2 로컬 완료: 소유 task가 독점 구간에서 drain/직전 검증/문서 CAS/기존 broker client 교환과
  단일 collector 재연결을 수행한다. 물리 client/토큰/잠금/호출 이력을 복제하지 않는다.
  각 계좌 query manager는 drain 후 broker/limiter만 바꾸며 binding/run/cursor와 legacy/v2 대상을 보존한다.
  broker의 cache generation은 역할 교환/이후 key 활성화에도 증가해 다른 계좌의 예전 cache를 재사용하지 않는다.
  app token/시각/실시간 계좌 resolver는 현재 담당을 사용하며 첫 재개 frame 전에 올바른 context를 공개한다.
  인증된 역할 PUT과 routing 적용 revision을 연결했다. 적용 revision은 모든 REG 승인/연속 수신 완료가 아니다.
  재기동도 저장된 담당을 기존 market broker에 배치하고 legacy 계좌는 별도 과거 조회로 복원한다.
  저장 전 실패는 이전 routing/cursor를 복구한다. 저장 결과 불명확/저장 후 실패/복구 실패는 두 계좌 조회와
  시장 연결을 차단하고 적용 revision을 null로 둔다. 저장 역할을 임의로 되돌리지 않으며 정상 키/저장 상태
  확인 후 서버 재시작으로 복구한다. 별도 실행 중 역할 복구 API는 이번 단계에 추가하지 않았다.
  HTTP 대기 취소는 물리 전환 task를 취소하지 않으며 종료는 그 task가 끝나기를 기다린다.
  신규 호출자는 소유 구간 안에서 owner.close를 직접 await하지 않고 구간을 먼저 종료해야 한다.
  DB CAS만으로 vault 변경과 실시간 연결 전환의 경쟁이 해결됐다고 가정하지 않는다.
- R6b3c1 로컬 완료: 기존 파서/연속조회 공유, 별도 real/mock 환경 reader와 실전 통합 주문·두 venue 잔고 조회를 구현했다.
  같은 수량은 합산하지 않고 충돌/누락/중복/연속 cursor 순환은 실패로 닫는다. 원계좌번호는 정규화 결과에 복사하지 않는다.
  실전 context는 계좌당 한 개의 소유 read task를 사용하며 취소/키 변경/역할 전환/종료 때 전체 cycle을 drain한다.
  기존 broker/요청 잠금/한도를 유지하고 일반 v1 계좌 무신원 조회를 차단한다. 실전 결과는 mock 실행 원장에 넣지 않는다.
  이 단계는 read 기반뿐이며 자동 monitor·실전 저장·WS·계좌 운영 PUT/applied revision을 완료했다고 주장하지 않는다.
- R6b3c2a 로컬 완료: 기존 owner/context에 계좌별 30초 REST 수집과 ON/OFF를 연결한다.
  첫 계좌 회차도 30초 뒤 실행하여 부팅 순위를 앞에 두고, 동일 계좌 수동 조회 중 자동 회차는 건너뛴다.
  기존 실전 broker/비담당 동시 실행 2개를 유지한다. 새 client/manager/실전 주문 경로는 없다.
  실전 결과는 central_documents 내부 real_account_recovery에 scope/profile/binding/설정 revision/출처/관측시각과 append한다.
  저장 검증과 삽입을 같은 SQLite/PG 트랜잭션에서 수행하고 일반 content API로 공유하지 않는다.
  키/역할 변경·OFF·종료는 실제 조회와 thread 저장을 drain하며 OFF/ON은 시장 collector를 재연결하지 않는다.
  계좌 HTTPS PUT은 real/mock owner를 분배한다. real 주문 토글 true/profile 변경은 허용하지 않는다.
  적용 revision은 REST 정책만 증명하고 monitor_status.mode=rest_poll 및 성공 관측/오류를 따로 제공한다.
  설정 결과 불명확/복구 실패는 해당 계좌 수집 paused/applied null, 시장/역사 조회는 유지한다.
  정상 키/저장 상태 확인 후 서버 재시작으로 복구하며 실행 중 설정 복구 API는 이번 단계에 추가하지 않는다.
- R6b3c2b 로컬 완료: 기존 시장 collector의 00/04를 owner에 분배하고 비담당은 자기 계좌 전용 WS를 쓴다.
  mock 계좌 socket 수명은 private base로 공유하고 real/mock URL·관측시간·신원/승인 정책을 분리한다.
  9201 HMAC/current binding/scope와 monitor generation을 검증하여 외부 계좌/늦은 callback을 거절한다.
  별도 writer가 real_account_event 내부 문서를 저장하며 REST/event 저장은 같은 DB 계좌 fence를 쓴다.
  0.5초 wake 병합과 30초 backup을 유지하고 시장 callback은 네트워크/DB를 기다리지 않는다.
  전용 계좌 이벤트도 기존 hub/daily 실제 매수 종목 수집 범위에 합치며 담당 이벤트는 중복 발행하지 않는다.
  재개 전에 계좌 수집기를 먼저 준비하고 OFF/키/역할/종료는 socket/실제 token/이벤트 저장까지 drain한다.
  pending 저장 실패는 보존/재시도하고 살아 있는 전환 commit을 막는다. overflow는 오류/수량으로 공개한다.
  RAM 대기는 강제 종료를 견디는 영속 outbox가 아니다. 동시 토큰·실환경 승인/수신은 R7 검증 항목이다.
  기존 mode/applied_revision은 REST 정책이며 별도 realtime 상태에 승인·마지막 실제 저장을 공개한다.
  기존 실전 scope/커서와 주문 허용 계약을 유지하고 새로운 실전 자동주문 경로를 만들지 않는다.
- R6c1 로컬 완료: 기존 PC HTTPS client/dialog에 실전 프로필 추가·확인/적용·비활성화·계좌 조회 ON/OFF를 연결했다.
  모의/실전 공급자와 진행 요청을 분리하고 검증한 UUID 계좌/revision으로만 apply한다.
  실전 모의주문 토글을 숨기고 true 요청/응답을 거절한다. PC 키/DB 저장·시세 담당 변경은 없다.
  계좌 설정 적용과 실제 REG 준비 상태를 분리 표시하며 상태 다시 확인으로 갱신한다.
  기존 모의 이름의 client 메서드는 호환 진입점으로 유지하고 공통 계좌 메서드가 실제 처리를 맡는다.
- R6c2 로컬 완료: 기존 collector에 최대 30초 planned reconnect와 종료/만료 통지를 연결했다.
  기존 health/capabilities/WS에 상태를 추가하고 늦게 연결한 PC에도 남은 대기를 전달한다.
  PC는 같은 generation 통지로 deadline을 늘리지 않으며 자체 만료도 처리한다.
  계획된 상류 오류만 유예하고 실제 NAS transport 단절·deadline 이후 장애는 기존 정책을 따른다.
  기존 표를 비우지 않고 모든 REG 승인 후 정상 준비를 표시한다. 휴장/구독 대기는 거짓 장애를 만들지 않는다.
  paused broker의 명시 대기는 일반 API 오류로 분리하여 앱의 로컬 TR을 유발하지 않는다.
  마지막/첫 허용 0B의 monotonic 관측 간격을 UTC 시각·최신 상태·PC/NAS 로그·PC 문구로 기록한다.
  이전 관측이 없으면 null이며 종목별 누락량/거래소 지연 분석이나 별도 DB 이력은 아니다.
  실제 drain/집계/source baseline은 유지한다. 실제 거래시간 교체/공백/PostgreSQL 검증은 R7이다.

- 목적: 순위 우선순위와 집계 상태를 보존하면서 실전 키를 실행 중 변경.
- 대상: realtime_collector.py, rest_broker.py/client.py, account_query.py, app.py,
  central_realtime_worker.py/failover_client.py의 planned reconnect 처리.
- 입출력: 검증된 candidate → 동일 broker/client 요청 잠금에서 active 인증 변경;
  연결 generation/실제 수신 gap/등록 완료 상태.
- 완료: 계좌당 WS 한 개와 공통 시세 수집 한 벌, 요청 대기 중 ka00198 우선, 계좌 cursor 혼합 0건,
  기존 초·분봉/재시도 저장분 보존, 실패 deadline 후 실제 장애 판정 복귀.
- 테스트: 순위/백필 동시 대기, REG 실패·늦은 이전 frame, 옛 next_key,
  집계 분 중간 교체와 거래대금 중복/손실 검사, planned reconnect 종료/rollback 실패.
  실환경은 주문 없이 인증/조회/구독부터 확인하고 수신 공백 시간을 측정.

### R7. 누적 배포·운영 검증

R7a 배포 준비(2026-09-16): 누적 build는 `2026.09.16-runtime-credentials-r7-deploy-v1`이다.
Docker context에 NAS 백업/비밀 제외를 추가하고 실제 Dockerfile cryptography 설치/영속 secret mount를 확인했다.
관련 회귀 368개를 실행해 통과했다(Windows POSIX 권한 검사 1개 skip). 실제 권한 검사는 Linux/NAS에서 남아 있다.
사용자 재빌드/시작 후 NAS의 R7 build 일치/health ok와 DB 읽기·인증 메타데이터·앱 WS ready를 확인했다.
HTTPS 초기 설치는 R7c2에서 완료했다. HTTPS 8443의 엄격한 TLS/DB/WSS ready와 실제 peer 신뢰를 확인했다.
앱 저장 NAS 주소만 HTTPS로 바꾸고 접속 토큰/다른 설정은 보존했다. 기존 실행 앱은 재시작이 필요하다.
원본 57개 백업/변경 135개 동기화·전체 763개 hash 확인을 완료했다. 순위 회차도 30초간 진행했다.
초기 nginx/WSS 404와 HTTPS_REQUIRED는 사용자 프록시 설정/서버 재적용 후 해소됐다.
사용자 재빌드 이후 운영 결과는 R7 보고서와 NAS_DEPLOYMENT_PENDING을 따른다.
이미지 재빌드/health 일치/실환경 검증이 끝나기 전에는 R7 완료로 표시하지 않는다.

- 대상: API_CONTRACT, DB_SCHEMA, MODULE_MAP, ARCHITECTURE_CURRENT,
  DEVELOPMENT_GUARDRAILS, CHANGELOG, deploy/synology/README, NAS_DEPLOYMENT_PENDING.
- 완료: 인증 지원 capability는 구현 완료 공급자만 표시하고, 이미지 의존성에도 cryptography 반영.
  단순 pyproject 추가만으로 Dockerfile의 --no-deps 설치가 해결된다고 보지 않는다.
- 관련 회귀: test_central_server_config/app/rest_broker/realtime_collector,
  test_account_identity, test_mock_account_monitor, test_order_lifecycle,
  test_central_news_service/ai_service, test_news_source_collection,
  test_api_settings_dialog/news_settings_dialog, 신규 credential store/runtime/UI 및 activation migration.
- 다계좌 필수 회귀: 실전 A/B+모의 C/D 동시 등록, 같은 계좌 중복 키 등록,
  B 추가/갱신/오류 중 A 수집 유지, A 페이지 조회 중 UI B 선택, A/B의 같은 주문번호,
  신규 거래 없는 계좌 선택, 과거 계좌 열람, 계좌별 주문권한, default 생략 호환,
  PC failover 신원 불일치, 동일 계좌 준비/회전의 합산 한도와 서로 다른 계좌 한도 독립성.
  계좌 수 증가 부하 검사는 비시세 작업의 제한 대기와 ka00198 응답 지연을 함께 측정한다.
- NAS source sync는 경로 검증·기존 코드 백업 후 .env/postgres-data/server-data/server-secrets 보존.
  파일 hash와 세 곳 build ID 일치 확인 후 사용자에게 누적 재빌드 안내.
- 재빌드 후 기존 env 이관, 모의 키 변경/재시작 영속성, provider별 실패 격리,
  순위 30초 회차 연속성, 뉴스 중복 0, 별도 일지 scope, 인증값 비노출을 확인.
  실제 주문 검증은 이 기능 설치와 분리하고 구체적인 모의주문 조건이 있을 때만 수행한다.

## 10. 검토 결론과 남은 실환경 확인

핵심 공개 조회 API와 기존 AccountScope 모델을 유지하면서 구현 가능하다.
새로 필요한 구조는 비밀 파일 저장, 적용 작업 수명, 모의 runtime 조립 세 경계다.
현재 요청에 외부 secrets service나 전체 서버 재작성은 필요 없다.

설계 재검토와 R0~R5, R6a·R6b1·R6b2, R6b3a·R6b3b1·R6b3b2·R6b3c1·R6b3c2a 및 R6b3c2b 계좌 WS 연계를 로컬 구현했다. R6c PC 입력/계획된 재연결과 R7 기본 배포/HTTPS 연결도 확인했다. 장중 실환경과 PostgreSQL 통합/Linux 권한 검증은 남아 있다.
R2/R3/R6의 동시성·lease·실제 thread 종료·중단 복구 구현은 SOL HIGH 권고 구간이다.
모델 자동 변경을 수행한 것은 아니다. 나머지는 기존 구현 원칙대로 진행한다.

NAS HTTPS/WSS·실제 프록시 peer 신뢰는 확인 완료했다. 운영에서 추가 확인할 것은 Synology 실제 볼륨 권한,
공급자 키 재발급이 기존 토큰에 미치는 영향, 계좌 만료 시 실제 UNKNOWN/미체결 상태다.
이 결과는 구현의 검증 단계에서 확인하며 '없음' 또는 '복구됨'으로 미리 단정하지 않는다.

## 공식 기술 근거

- [cryptography AESGCM](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM):
  암호화와 무결성 검증, nonce 재사용 금지, associated data 결합 계약에 근거.
- [FastAPI 오류 처리](https://fastapi.tiangolo.com/tutorial/handling-errors/):
  RequestValidationError의 입력 body 접근과 사용자 정의 오류 처리 경계 확인.
- [Python asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread):
  동기 함수를 별도 thread에서 실행하는 경계. 실제 종료 보장은 repository 회귀로 검증해야 한다.
- [키움 REST API 이용안내](https://openapi.kiwoom.com/intro/serviceInfo):
  실전/모의 계좌별 등록과 App Key 관리 안내. 한 키로 모든 계좌를 자동 열거한다고 가정하지 않는다.
- [키움 REST API 사용안내](https://openapi.kiwoom.com/intro):
  계좌별(토큰별) 호출 제한 및 세션 제한. 이는 프로필별 실행 설계의 외부 근거이며
  실제 여러 계좌의 동시 세션 성공 여부는 배포 검증에 남긴다.

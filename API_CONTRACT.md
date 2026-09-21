# NAS API 계약

상태 정리 기준: 2026-09-22. 아래 API·필드·실패 처리 계약은 유지한다. R0~R6 로컬 구현과 R7 기본 배포·HTTPS/WSS 연결은 완료 기록이 있으며, 최신 누적 빌드의 실행 반영과 장중·장시간 검증은 별도다. 단계 이름이 붙은 설명은 그 단계의 구현 범위를 나타낸다. 현재 구현·배포 상태는 [현재 상태](docs/CURRENT_STATUS.md), 미완료 구현과 운영 검증은 [남은 작업](docs/OPEN_ITEMS.md)을 따른다.

현재 계약 버전은 `api_version=v1`, `schema_version=1`이다. 모든 `/api/v1/*` HTTP 요청은 `Authorization: Bearer <NAS 접속 토큰>`이 필요하다. `/health`만 공개다. WebSocket은 같은 헤더 또는 `?token=`을 사용한다.

자동 시장자료 조회에서 `GET /api/v1/market/snapshots/{kind}`의 `kind`는 `investor_flow`, `program_flow`, `new_high`, `market_index_chart`를 포함한다. `GET /api/v1/content/stock_fundamentals`, `stock_nxt_eligibility`, `historical_highs`는 NAS가 만든 종목 문서를 반환한다. `historical_highs.document.target`은 `price`, `first_year`, `last_year`, `occurred_on`, `evidence`를 가진다. 중앙 연결이 정상인데 자료가 비어 있으면 앱은 빈 저장 결과로 처리하고 Kiwoom TR을 대신 만들지 않는다.

중앙 `00` 실시간에서 확인한 실제 매수 체결은 `account_entry_symbols_daily` 내부 문서에 거래일·종목·환경·관측시각만 저장한다. 이 목록은 NAS의 시장자료 구독·보완 범위에만 사용하며 TOP20 membership, TOP20 지수 및 계좌별 매매일지 계약을 변경하지 않는다.

## 공통 오류

- `401`: 접속 토큰 없음/불일치
- `400`: 허용하지 않은 Kiwoom API·경로·연속조회 값 또는 잘못된 입력
- `404`: 지원하지 않는 스냅샷 종류/콘텐츠 컬렉션
- `405`: 전체 교체가 허용되지 않은 콘텐츠 컬렉션
- `429`: 중앙 AI 공급자가 호출 한도를 반환한 경우. 클라이언트는 즉시 연속 재요청하지 않는다.
- `502`/`503`/`504`: 상류 Kiwoom/뉴스/AI 처리 실패 또는 일시적 혼잡. JSON `detail`은 비밀값 없이 사용자에게 표시 가능한 원인을 담는다.
- `503`: 서버에 필요한 공급자 자격 증명이 없음

## 상태와 설정

### 시세 담당 역할 조회·변경 (구현 계약)

인증된 `PUT /api/v1/settings/market-profile` 요청은 market_profile_id,
expected_revision(0 이상 정수), expected_binding_revision(1 이상 정수) 세 필드만 받는다.
role revision/binding 충돌·후보 실행 미준비·실전 credential 후보 대기는 409, 잘못된 입력은 422,
실전 owner 없는 설치 또는 전환/복구 실패는 503이다. 성공은 GET과 같은 응답을 반환한다.
활성 실전 owner가 있는 설치에서만 변경을 적용하며 모의 profile은 대상이 될 수 없다.

전환은 기존 두 계좌 조회·시장 WS·물리 REST 작업을 drain하고 직전 snapshot/DB CAS를 확인한 뒤,
기존 중앙 broker와 계좌 broker 사이의 검증된 client만 교환한다. 물리 요청 잠금/토큰/호출 이력은
각 client를 따라간다. 기존 단일 시장 collector/집계/hub·우선순위 큐를 유지한다.
같은 담당 변경은 저장/연결을 다시 하지 않는다. 기본계좌/v2 암묵적 대상과 두 계좌 cursor/binding은 유지한다.
실시간 계좌 event의 신원은 현재 시세 담당을 따로 사용하며 기본계좌 신원을 대신 쓰지 않는다.

GET의 applied_revision은 담당 REST/계좌 routing과 collector 재연결 시작이 적용된 역할 revision이다.
전체 REG 승인/실시간 수신 준비 완료를 뜻하지 않으며 기존 upstream ready 계약은 별도다.
미준비/실패/owner 없는 설치는 null이다. 재시작도 저장된 담당을 먼저 배치하고
legacy 기본계좌는 별도 조회 context로 복원한다. 대상 복원 실패 시 다른 계좌로 자동 대체하지 않는다.
저장 전 검증/CAS 실패는 이전 routing/cursor를 복구한다. 저장 결과가 불명확하거나 저장 후 실패하면
MARKET_ROLE_RECOVERY_REQUIRED로 두 대상 조회/시장 연결을 차단하고 적용 revision을 null로 둔다.
이 상태는 기존 역할로 임의 복귀하지 않고 저장 상태/키 정상 확인 후 서버 재시작으로 복구한다.
HTTP 대기 취소는 소유된 전환 task를 취소하지 않으며 종료가 그 task의 완료를 기다린다.

아래는 R6b3a 저장 기반 및 R6b3b1 내부 보호 계약이다.

인증된 `GET /api/v1/settings/market-profile`은
`{"settings":{"market_profile_id":"nas-real-default","legacy_real_profile_id":"nas-real-default","revision":0},"applied_revision":null}`을 반환한다.
문서가 없으면 이 기본값을 읽기만 하고 저장하지 않는다. 저장 문서 손상은 409
`MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED`이며 기본값으로 덮지 않는다.
R6b3a 당시에는 applied_revision=null·GET만 제공했다. 실제 전환 PUT은 위 R6b3b2에서 연결했다.
일반 content 동기화로 역할 문서를 쓰는 경로도 열지 않는다.

내부 저장은 역할 expected_revision과 검증된 계좌 expected_binding_revision을 함께 확인한다.
활성 kiwoom_real/real profile·활성 registry·해당 계좌의 active_profile_id가 일치해야 한다.
monitor OFF는 시세 담당 자격을 없애지 않는다. legacy_real_profile_id는 nas-real-default로 고정한다.
같은 역할 저장은 revision/updated_at을 유지하며 충돌은 전체 rollback한다.
시세 담당 profile의 disable 또는 계좌 연결 해제는 MARKET_PROFILE_REQUIRED로 거절한다.
이미 완료한 credential replay는 이후 역할·계좌 설정을 변경하지 않는다.
실제 역할 변경 API/REST·WS 전환은 R6b3b2, 계좌별 REST·실시간 운영은 R6b3c에서 연결했다. 실제 복수 계좌의 장중 전환·수신 검증은 남은 작업으로 구분한다.

R6b3b1은 실제 전환 전에 사용할 서버 내부 독점 구간을 구현했다. 실전 credential 후보가
VALIDATING/READY/apply 상태이면 담당 전환은 PROFILE_BUSY로 거절하며 기다리지 않는다.
전환 구간 중에는 새 실전 credential 후보를 받지 않는다. 현재 역할/계좌 binding/활성 profile과
실행에 적용된 암호화 vault revision을 검증하고 저장 직전에 같은 snapshot을 재검증한다.
취소/예외는 구간을 해제하며 종료는 구간이 끝나기 전에 context를 닫지 않는다.
독점 구간 자체는 문서/REST/WS를 변경하지 않는다. R6b3b2 호출자가 구간 안에서 전환한다.

### 선택 계좌 조회·모의주문 (구현 계약)

R6b3c1은 실전 계좌의 read-only 복구 조회 기반을 연결했다. 서버 내부
RealCredentialOwner.read_account(profile_id, expected_binding_revision=...)는 admitted real scope와
현재 binding revision을 확인하고 계좌당 한 개의 소유 task로 조회한다. 호출자 취소는 실제 조회를 취소하지 않는다.
기존 중앙/계좌 broker를 사용하며 새 client·요청 한도·WS를 만들지 않는다.
키 변경/시세 담당 변경/종료는 여러 TR로 구성된 실제 조회 전체가 끝나기를 기다린다.
현재/미체결 ka10075, 체결 ka10076, 잔고 kt00018, 예수금 kt00001은
일반 v1 kiwoom/query에서 400 ACCOUNT_QUERY_SCOPE_REQUIRED로 차단한다.
이 단계는 새 공개 계좌 복구 조회 endpoint/계좌 운영 PUT을 추가하지 않는다.
R6b3c1 당시 자동 수집·실시간 적용은 후속이며 실전 monitor applied_revision은 null이었다.

R6b3c2a는 같은 계좌 설정 GET/HTTPS PUT에 real owner를 연결한다. 요청 필드는 기존 mock과 같으며
현재 active_profile_id를 유지하고 real mock_order_enabled는 반드시 false여야 한다(위반 422).
계좌별 monitor_enabled는 기존 broker의 30초 REST 복구 조회/실전 전용 저장을 켜고 끈다.
첫 자동 회차도 30초 뒤 시작하고 같은 계좌 수동 조회가 있으면 그 회차를 건너뛴다.
OFF/ON은 시장 collector/순위를 중지하거나 reconnect하지 않으며 일반 v1 계좌 조회 차단을 유지한다.
실전 GET/PUT은 monitor_status를 추가한다: mode=rest_poll, state=waiting|collecting|off|paused|unavailable,
poll_interval_seconds(기본 30), last_success_at(aware ISO|null), error_code(비밀 없는 코드|null).
applied_revision은 REST 수집 정책이 적용된 revision이다. 성공 자료를 받았다는 증거는 last_success_at이며,
WS/REG 승인·실제 주문 허용을 의미하지 않는다. 키 binding이 바뀌면 이전 성공 상태를 초기화한다.
설정 변경 대기 취소는 소유 task를 취소하지 않는다. 실제 조회/저장 완료 뒤 설정 CAS/ON-OFF를 적용한다.
낡은 설정은 409 ACCOUNT_SETTINGS_REVISION_CONFLICT다. 저장 결과 불명확/적용 복구 실패는
503 ACCOUNT_SETTINGS_RECOVERY_REQUIRED, 해당 계좌 수집 paused 및 applied_revision=null이다.
이 경우 시장 collector·역사 조회는 유지하고 임의 DB rollback 없이 정상 키/저장 상태 확인 후 서버 재시작으로 복구한다.
R6b3c2a 당시 계좌 WS 분배·비담당 WS는 후속이었다. 공개 snapshot 조회나 실전 주문 API는 추가하지 않는다.

R6b3c2b는 monitor_status에 realtime={source: shared_market|account_only,
state: ready|waiting|off|paused, last_event_at: aware ISO|null, dropped_events: int,
error_code: 안전한 코드|null}를 추가한다. 기존 mode=rest_poll/applied_revision 의미는 유지한다.
ready는 시장 연결의 승인 완료 또는 계좌 전용 REG 성공이며 last_event_at은 실제 이벤트 저장 성공이다.
시세 담당은 기존 00/04 시장 WS를 공유하고 비담당은 자신의 real 인증/계좌 전용 00/04 연결만 연다.
raw 9201 HMAC/현재 binding/scope를 검증하고, 다른 계좌/누락 신원/이전 monitor generation의 callback은 버린다.
비담당 체결·잔고도 기존 hub에 익명 scope로 한 번 전달한다. 매수 종목은 기존 NAS 시장 수집 범위에 합친다.
실전 연결 LOGIN/REG 성공을 확인한 뒤 REAL을 전달하며 PING은 그대로 응답한다.
계좌 전용 관측은 평일 08:00~20:00이다. 휴장일 달력/동시 토큰 실제 운용은 R7 실환경 항목이다.
00 접수/취소·04 변경·연결 상태는 0.5초 동안 합쳐 계좌 REST 복구를 깨우며 30초 backup을 유지한다.
계좌 이벤트 저장은 별도 소유 writer에서 진행하여 시장 수신이나 REST 조회 완료를 기다리지 않는다.
저장 실패는 pending event를 보존해 30초 간격으로 재시도한다. 미저장 이벤트가 있으면 살아 있는
키/역할/설정 전환은 commit하지 않고 이전 정책 복구를 시도한다. 오류는 raw exception 대신 안전한 코드다.
1000개 queue가 가득 차면 dropped_events와 REAL_ACCOUNT_EVENT_QUEUE_FULL을 기록하고 REST 복구를 요청한다.
메모리 대기는 DB outage 중 강제 종료/프로세스 재시작을 견디는 영속 outbox가 아니다.
실제 socket 종료가 실패하면 REAL_ACCOUNT_DRAIN_FAILED로 표시하고 새 연결/설정 commit을 차단한다.
저장 설정은 유지하고 해당 계좌 수집 paused/applied_revision=null로 둔다. 정상 키/종료 상태 확인 후 재시작으로 복구한다.
이 전달을 실전 주문 실행/reconcile이나 유일한 execution ledger로 해석하지 않는다.

실전 reader의 주문 조회는 stex_tp=0 통합, 잔고는 dmst_stex_tp=KRX/NXT를 각각 조회한다.
같은 종목의 같은 수량은 한 번만 사용하며 수량 충돌/계좌 테이블 누락/중복 주문 페이지/
연속 cursor 순환/서로 다른 거래소의 같은 주문번호는 실패로 처리하고 부분 결과를 반환하지 않는다.
정상 빈 list는 빈 자료이며 누락/null 테이블을 빈 계좌로 단정하지 않는다.
기존 mock reader는 KRX 조회·mock 환경 검사와 출력 필드 계약을 유지한다.
조회 결과는 정규화된 account/orders이며 원계좌번호·OAuth 토큰을 결과에 복사하지 않는다.
실전 결과를 기존 mock 실행 원장에 저장하지 않는다.

capability `multi_account_query_v3`는 명시 대상 조회 경로, `scoped_mock_orders_v2`는
실제 mock owner의 선택 계좌 주문 경로가 있음을 표시한다. 개별 계좌의 키/주문 허용 여부는 별도로 확인한다.
모의와 R6b2 실전 owner가 연결된 설치는 admitted profile별 client/broker로 계좌를 지정해 조회한다.
실전 owner는 vault·계좌 HMAC 검증·real 주 실행 환경을 갖춘 설치에 연결된다.
그 외 기존 설치의 실전 조회는 검증된 main profile만 사용하고 미연결 profile은 503 PROFILE_RUNTIME_NOT_READY다.

R6a 단계에서는 서버 내부의 실전 재연결 기반을 구현했다. collector의 pause/drain/resume와 이전 연결
generation 차단·실제 token/socket/저장/장 마감 완료, 계좌 query의 신규 BUSY/실제 작업 drain과
선택적 cursor 폐기를 제공했다. R6a 자체는 실제 kiwoom_real 인증 hook/입력 UI나
새 공개 endpoint/실전 지원 capability/응답 필드를 추가하지 않은 기반 단계다.
후속 R6b owner/API와 R6c1 입력·R6c2 planned reconnect/등록 확인/관측 간격 PC 표시까지 로컬 연결했다.

R6b1은 기존 credential activation 저장 계약을 실전계좌에도 적용한다. 검증된 scope의
binding·적용 원장·단일 active_profile_id는 같은 트랜잭션에서 확정된다. 중복 profile의
계좌 claim 실패는 전체 rollback하며 같은 profile 키 갱신은 운영 설정을 보존한다.
사용 중지는 과거 binding을 유지하고 계좌 설정을 OFF로 저장한다. 완료 replay는 이후
설정이나 새 활성 profile을 덮지 않는다. 최초 저장 monitor ON은 실제 runtime 시작을 뜻하지 않는다.
R6b1 단계 자체는 새 실전 hook/endpoint/capability나 실전 설정 PUT 지원을 추가하지 않았으며, 후속 연결 범위는 아래 R6b2/R6b3 계약을 따른다.

R6b2는 기존 credential prepare/apply/status API에 kiwoom_real hook을 연결한다. provider metadata의
supported는 실제 owner 연결 여부를 반영하고 v3 계좌 목록/조회는 검증된 실전 profile별로 분리한다.
R6b2 단계에서는 시세 담당으로 nas-real-default의 기존 client/broker/단일 collector를 유지했다. 이후 담당 변경은 위 역할 조회·변경 계약을 따른다. 추가 실전계좌의
과거 조회/키 갱신은 시장 WS를 재연결하지 않는다. v2 계좌 조회의 암묵적 대상은 여전히 기본 프로필이다.
사용 중지는 비시세 profile만 허용하며 시세 담당에는 MARKET_PROFILE_REQUIRED를 반환한다.
동일 계좌 키 갱신의 scope/run은 유지하고 계좌 변경·중복 연결은 준비에서 거절한다.
commit 후 실패는 RECOVERY_REQUIRED이며 기존 키를 다시 열지 않는다. 명시 복구의 후보 검증도
기존 broker queue/요청 잠금/한도를 쓰며 일반 조회는 계속 차단한다.
ACTIVE는 REST 인증/계좌 binding 적용이며 모든 WS REG 승인·연속 수신 완료를 뜻하지 않는다.
실전 monitor ON 저장만으로 계좌별 실시간 수집 완료를 주장하지 않으며 해당 GET의 applied_revision은
수집 운영 적용 전 None이다(사용 중지가 실제 적용된 비시세 계좌는 OFF revision 확인 가능).
시세 담당 전환/CAS/PUT은 R6b3b2, 실전 계좌 운영 PUT·추가 계좌 실시간 수집은 R6b3c, PC 입력/재연결 표시는 R6c에서 연결했다. 구현 완료와 실제 복수 계좌·토큰의 장중 운용 검증은 구분한다.

| Method / path | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/v3/kiwoom/account-query` | 기존 v2 페이지 필드 + 필수 account_scope, credential_profile_id, expected_binding_revision | payload/batch_id/page_index/complete/next_key/context/provenance; v2 응답 형태 유지 |
| `POST /api/v2/mock/accounts/{account_ref}/orders` | 기존 v1 지정가 주문 필드 + 필수 account_scope, credential_profile_id, expected_binding_revision | 기존 주문/events + 검증된 context |
| `GET /api/v2/mock/accounts/{account_ref}/orders/{intent_id}` | query: environment=mock, credential_profile_id, expected_binding_revision; broker=kiwoom 기본 | 해당 계좌/current run 주문/events + context |
| `POST /api/v2/mock/accounts/{account_ref}/orders/{intent_id}/cancel` | account_scope, credential_profile_id, expected_binding_revision, 선택 quantity | 취소 결과의 주문/events + context |

모든 경로는 bearer 인증을 요구한다. account_scope는 정확히 broker/environment/account_ref이며
0이 아닌 UUID를 사용한다. POST 대상 필드는 필수, binding revision은 양의 정수이고 bool은 거절한다.
모의 path의 account_ref와 body scope도 같아야 한다. 잘못된 scope는 400, 대상/연결 버전 불일치는 409,
미준비 profile/비활성 계좌는 503, 다른 계좌 또는 run의 intent는 404 MOCK_ORDER_NOT_FOUND다.
잘못된 대상·cursor·intent는 공급자 요청 전에 거절한다. 새 경로의 HTTP 예외는 고정 오류 코드를 사용한다.

조회는 kt00007/kt00015만 허용한다. cursor는 계좌 bundle별 API/path/body/binding에 고정되고
연결 교체나 bundle 종료 뒤 재사용할 수 없다. 응답 직전에도 binding을 확인한다.
HTTP 대기자 취소는 실제 페이지 작업을 취소하지 않으며 bundle 종료가 실제 작업까지 기다린다.
계좌별 최대 32개 owned 작업·256개 열린 cursor이며 혼잡/종료는 503이다.
모의 페이지는 기존 공통 2개 read limiter와 계좌별 mock broker의 한도를 사용한다.

모의 주문은 기존 명시 주문 허용·KRX 지정가 정책을 유지한다. 주문 허용 OFF이면 신규 주문/취소는 503이다.
활성 binding이 있는 경우 주문 허용 OFF라도 해당 계좌/current run의 저장 주문 GET은 가능하다.
비활성 profile의 과거 기록 열람은 향후 UI/일지 DB 경로로 제공하며 이 실행 API로 다른 profile을 대신 선택하지 않는다.
새 scoped intent ID는 environment/account_ref/run/request_id로 분리한다. 동일 요청은 기존 상태를 반환하며
SUBMISSION_UNKNOWN도 자동 재전송하지 않는다. 기존 v1의 run/request_id ID 계산과 기본 nas-mock-default는 유지한다.
v2 계좌 조회의 legacy main profile도 화면 선택에 따라 바뀌지 않는다.

| Method / path | 요청 | 응답 핵심 | 역할 |
| --- | --- | --- | --- |
| `GET /health` | 없음 | `status`, `api_version`, `schema_version`, `server_time`, `server_build` | 공개 생존 확인 |
| `GET /api/v1/capabilities` | 없음 | 버전 + `capabilities` boolean map | 서버가 제공하는 기능 협상 |
| `GET /api/v1/settings/operations` | 없음 | AI·뉴스·Shadow 설정과 revision/applied_revision/apply_status | 영속값과 실행 적용 상태 읽기 |
| `PUT /api/v1/settings/operations` | 변경 필드, 선택 expected_revision | 저장된 전체 구조와 적용 상태 | 부분 변경; 충돌 409, 저장/적용 실패 503 |

R5d2a는 operations에 `external_market_enabled`/`external_market_auto_roll_enabled`(strict bool),
`external_market_poll_seconds`(strict int 60~86400), `external_market_roll_confirmations`(strict int 1~100)을 추가한다.
기존 revision CAS/부분 변경/인증과 revision/applied_revision/apply_status 계약을 유지한다.
상품 symbol이 없는 서버에서 ON은 저장 전 422 EXTERNAL_MARKET_SYMBOLS_REQUIRED다. 초기 OFF에도
symbol이 있으면 같은 수집기를 조립하며 새 설정은 재시작 없이 활성화된다. 저장 OFF는 ENV ON보다 우선한다.
갱신은 현재 수집 cycle의 실제 HTTP·봉·roll·status 저장까지 drain한 뒤 적용하며 요청 waiter 취소도
이를 취소하지 않는다. shutdown은 pending 갱신과 실제 수집 완료를 기다린다. 다른 중앙 서비스는 유지한다.
기존 봉/roll 이력/일봉 기준 cache를 초기화하지 않는다. 상품/활성 월물 수동 변경 필드는 이번 범위에 없다.
저장 전 실패는 기존 실행/버전을 유지하고 저장 후 실패는 503 OPERATIONAL_SETTINGS_APPLY_PENDING/
복구 필요로 공개한다. GET으로 확인 후 같은 revision을 명시 재저장할 수 있으며 자동 재적용하지 않는다.
PC는 구 NAS의 응답에 없는 네 필드를 보내지 않고 해당 controls를 비활성화한다. 새 endpoint/SQL 변경 없음.

R5d2b는 같은 operations에 `hot_cohort_condition_enabled`(strict bool),
`hot_cohort_condition_name`/`hot_cohort_condition_substring`(strict string, 최대 120자)을 추가한다.
정확한 이름이 있으면 우선하고 없으면 substring에 유일하게 일치하는 조건식을 선택한다.
ON에서 두 선택값이 비면 422 CONDITION_SELECTION_REQUIRED다. 조건검색 서비스가 조립되지 않은
서버에 조건 필드를 쓰면 저장 전 422 CONDITION_RUNTIME_NOT_READY다. 기존 인증/부분 PUT/CAS는 유지한다.
응답의 `condition_runtime_supported`와 `condition_status`는 현재 메모리 실행 상태다.
`condition_status`는 enabled/configured_exact_name/configured_substring, active(seq/name 또는 null),
policy_revision/active_policy_revision, coverage=KRX, apply_status를 가진다.
상태는 WAITING_CONNECTION/WAITING_LIST/WAITING_REGISTER/WAITING_CLEAR/ACTIVE/OFF/RECOVERY_REQUIRED다.
operations의 ACTIVE는 설정 전달 완료이며 실제 조건 등록 완료는 condition_status.ACTIVE로 확인한다.
나중에 등록/해제 실패가 확인되면 operations GET도 RECOVERY_REQUIRED를 반환한다.
같은 expected_revision으로 명시 재저장해 다시 시도하며 변경값이 없으면 영속 revision은 증가하지 않는다.

설정 PUT은 WebSocket 응답을 기다리지 않는다. 기존 단일 수신 loop가 변경을 감지해 CNSRLST와
CNSRREQ를 보내고 전체 초기 결과/페이지를 확인한 뒤 새 조건을 활성화하고 이전 조건을 CNSRCLR로 해제한다.
후보 초기 결과와 실시간 신호를 합쳐 최대 5,000건 보류하며 초기 결과 다음 실시간 신호 순서로 적용한다.
잘못된 seq/페이지 cursor/형식, 등록 실패/15초 응답 timeout/queue 초과는 부분 후보 적용을 막는다.
런타임 변경 뒤 seq 없는 초기 응답은 추측하지 않고 timeout/복구 필요로 처리한다.
실패한 후보/이전 조건 해제는 ACK를 확인하며 실패하면 명시 재저장 또는 실제 재연결이 필요하다.
미완료 요청이 있는 중간 변경은 이전 응답/후보 정리를 마친 뒤 최신 정책만 등록한다.
OFF는 신규 조건 신호를 무시하고 등록 해제를 요청하되 기존 코호트·VI·체결·hub·일자 보존 정책은 유지한다.
queue에 접수된 신호는 당시 조건식 seq/name을 보존한다. 조건 교체로 과거 접수 신호의 이름을 바꾸지 않는다.
`/api/v1/market/events?kind=cohort`의 condition.runtime도 저장 목록 진단에 현재 메모리 상태를 합쳐 반환한다.
PC는 지원 flag가 true이고 세 필드가 있을 때만 입력을 활성화/전송한다. 새 endpoint/SQL/연결 owner 없음.

R5d2c PC NAS 운영 화면은 기존 `news_query_set_enabled`, `news_query_set`,
`news_query_set_refresh_seconds`를 편집한다. 서버 필드/endpoint 의미를 변경하지 않는다.
검색어는 한 줄에 하나이며 앞뒤 공백/빈 줄/중복을 제거하고 구문 내부 공백은 유지한다.
정규화 후 최대 50개, UI에서 ON은 한 개 이상을 요구하고 OFF는 빈 목록도 허용한다.
수집 주기는 60~86400초다. 세 필드가 모두 있는 NAS에만 입력을 활성화/전송하며 기존 worker/
부분 PUT/expected_revision CAS를 사용한다. 변경 없는 값과 이 화면이 편집하지 않는 설정은 보내지 않는다.
등록 종목 뉴스 주기 `news_refresh_seconds` 및 선택 종목의 60초 freshness와 공통 검색 주기를 분리한다.
검색어 운영값은 PC 직접 뉴스 설정/키에 복사하지 않는다. NAS 화면은 폼을 스크롤하며 저장 버튼을 유지한다.

기존 QuerySetNewsCollector는 한 번 시작한 검색어의 owned 통신/페이지/저장을 완료한다.
수집 중 ON/OFF·검색어·주기가 바뀌면 이전 정책으로 다음 검색어를 시작하지 않고 다음 정기 loop가
새 정책을 사용한다. 정상 완료 source의 다음 조회 시각은 last_success + 현재 poll_seconds로
판정한다. 따라서 저장된 오래된 next_schedule_at 때문에 단축 주기가 밀리지 않으며 주기 연장도 적용된다.
실패 backoff/부분 페이지/명시 next_schedule_at=0은 기존 예약 의미를 유지한다.
기사/본문/분류 작업·source cursor·요청 사용량·일일 상한을 초기화하지 않는다. 제거한 검색어 cursor도
보존해 다시 추가하면 해당 위치를 재사용한다. 즉시 수집 요청/새 TR/SQL 테이블/범용 계층은 추가하지 않는다.

### 런타임 인증 공통 API (구현 계약)

R5d1 PC NAS 설정 메뉴는 네이버/DART/OpenAI/Gemini/Claude의 고정 global 키 관리 화면을 제공한다.
같은 HTTPS client/worker를 사용하되 client의 공급자는 생성 시 고정한다. 같은 공급자/NAS 대상의
재열기는 메모리 pending operation을 유지하고 다른 공급자/주소/토큰 변경은 별도 client를 사용한다.
입력 키는 password/빈 값으로 시작하고 전달 직전·종료 때 비우며 PC 설정/DB/미러/백업에 저장하지 않는다.
네이버는 client_id/client_secret, 나머지는 api_key만 전송한다. global 임의 프로필을 만들거나
계좌 설정을 조회하지 않는다. READY는 provider/profile/revision/operation ID/계좌 null을 확인하며
NAVER/DART는 VERIFIED, AI는 VERIFIED 또는 UNVERIFIED만 적용할 수 있다.
disable도 prepare 후 명시 apply하며 timeout은 같은 operation GET으로 확인한다. 자동 재적용하지 않는다.
ACTIVE receipt에 validation이 없어도 AI 키를 인증 성공으로 표시하지 않는다. 프로필 runtime_validation은
기존 R5c 상태를 그대로 읽고 실제 인증은 명시 분석으로 확인한다. 서버/API/DB 변경은 없다.

R5c는 `openai`/`gemini`/`claude`와 각각 고정 `nas-{provider}-default`를 지원한다.
keyless vault 설치에도 supported=true/revision=0/UNCONFIGURED로 등록하고 임의 global profile은
503 PROFILE_RUNTIME_NOT_READY다. replacement는 `{api_key}`, disable=true에서는 빈 객체이며 계좌 대상은 null이다.
prepare는 유료 분석/기사 HTTP를 실행하지 않고 READY의 validation=UNVERIFIED로 반환한다.
disable 후보의 validation=VERIFIED는 비활성화 준비 완료를 뜻하며 인증 성공을 뜻하지 않는다.
명시 apply 후 ACTIVE는 키 적용 완료이며 새 키 유효성의 검증 완료를 뜻하지 않는다.
profiles의 기존 validation은 vault 저장 당시 불변값이고 선택 추가 필드 runtime_validation은
활성 revision에서만 UNVERIFIED/VERIFIED/INVALID_CREDENTIAL/ACCESS_DENIED/RETRYABLE/REQUEST_FAILED/DISABLED를 반환한다.
AI 외 공급자·미활성/복구 필요 프로필에는 null이다. 재시작/키 교체 때 초기화되는 실행 상태로,
첫 실제 분석 성공은 VERIFIED, HTTP 401은 INVALID_CREDENTIAL, 403은 ACCESS_DENIED,
429/5xx/통신 실패는 RETRYABLE, 그 밖의 공급자 요청 실패는 REQUEST_FAILED다.
과거 성공 캐시 응답은 새 키를 검증하지 않으며 이전 revision의 늦은 실패도 새 상태에 반영하지 않는다.
AI 요청은 본문 준비 전에 공급자/model/key/revision을 고정한다. 교체는 해당 공급자의 접수·준비·
실행 대기·실제 분석·최종 저장을 drain하며 HTTP waiter 취소/서버 종료에도 실제 완료를 기다린다.
교체 중 해당 공급자 신규 분석은 재요청 안내 오류를 반환하고 다른 공급자 gate는 열어 둔다.
공유 실행 키에는 body hash/revision을 포함한다. 저장 성공 캐시의 품질 키는 바꾸지 않으며
키 교체만으로 재분석을 예약하거나 일일 사용량/상한을 초기화하지 않는다.
AI 응답의 선택 추가 credential_revision은 접수 context이고 새 results/사용량 JSON의 같은 필드는
실제 분석에 사용한 revision이다. 캐시 results는 과거 revision을 보존하며 구형 결과는 해당 필드가 없을 수 있다.
commit 이후 실패는 해당 공급자 키 부재/RECOVERY_REQUIRED로 두고 이전 ENV 키를 사용하지 않는다.
PC 공급자 입력 UI는 R5d1에서 연결했다. 실제 공급자 인증·분석 및 운영 검증은 별도이며 DB migration/AI 품질 규칙 변경은 없다.

R5b는 provider `dart`의 고정 `nas-dart-default`를 지원한다. keyless vault 설치에도 metadata
supported=true/revision=0/UNCONFIGURED로 공개한다. 임의 DART profile은 503 PROFILE_RUNTIME_NOT_READY다.
prepare는 replacement `{api_key}` 또는 disable=true+빈 객체이며 계좌 대상은 null이다.
기존 client의 회사코드 캐시를 건드리지 않는 당일 공시 검색 page_count=1로 검증한다.
000의 list 응답/013 조회 없음은 VERIFIED, 010/011/901은 FAILED+INVALID_CREDENTIAL,
020/800/900·timeout·HTTP 429/5xx는 FAILED+CREDENTIAL_VALIDATION_RETRYABLE다.
그 밖의 접근 제한/잘못된 응답은 FAILED+CREDENTIAL_VALIDATION_FAILED이며 원래 URL/메시지/키를 노출하지 않는다.
재시도 가능 실패를 검증 성공으로 처리하거나 자동 apply하지 않는다. 기존 operation 멱등성/TTL/HTTPS를 따른다.
apply는 종목 수집/최종 저장을 drain하고 cache Path/기사/cursor/작업/예산을 유지한다.
NAVER 공통 검색은 DART 교체 동안 계속하며 두 공급자 동시 교체도 각 pause를 독립적으로 유지한다.
키 적용/disable은 운영 dart_enabled를 켜거나 끄지 않는다. 운영 변경은 다음 접수 수집부터 적용한다.
commit 이후 실패는 DART None/RECOVERY_REQUIRED, disable ACTIVE는 tombstone 적용 완료다.
PC 공급자 입력 UI는 R5d1에서 연결했다. 실제 DART 키/IP·응답과 운영 검증은 별도이며 DB migration/분류 규칙 변경은 없다.

R5a는 provider `naver`의 고정 `nas-naver-default` profile을 지원한다. vault 설치가 정상이라면
키 없는 설치에도 metadata에서 `supported=true`, `revision=0`, `runtime=UNCONFIGURED`로 공개한다.
prepare의 replacement는 `{client_id, client_secret}`이며 `disable=true`에서는 빈 객체다.
검증은 기존 네이버 클라이언트의 검색 1건 요청으로 수행하고 모든 실제 HTTP 시도(legacy fallback 포함)를
기존 watchlist/shared 일일 예산에 포함한다. 성공은 VERIFIED, 인증/예산 실패는 FAILED로 반환하고 키를 노출하지 않는다.
계좌 대상은 READY/apply 모두 `target_account_ref=null`이다. NAVER 입력은 R5d1의 NAS 뉴스·AI 공급자 관리 화면에서 제공하며 계좌용 입력과 구분한다.
apply는 두 수집 경로의 실제 진행 작업/저장을 drain한 뒤 함께 교체하며 기사/cursor/작업/사용량을 유지한다.
pause 중 새 종목 조회는 저장 목록만 읽고 fresh 조회 완료 시각을 기록하지 않는다.
deadline 초과는 BUSY 후 실제 종료 뒤 FAILED/이전 연결 재개, commit 시작 이후 실패는 RECOVERY_REQUIRED/
NAVER 요청 차단으로 처리한다. disable ACTIVE는 키 부재 적용 완료이며 env 키를 재사용하지 않는다.
새 임의 NAVER profile은 `503 PROFILE_RUNTIME_NOT_READY`, 미연결 real provider는
`503 PROVIDER_RUNTIME_NOT_READY`다. DB migration/뉴스 분류 규칙 변경은 없다.

R4a PC 관리 화면은 아래 기존 API를 HTTPS로만 호출하며 redirect를 따르지 않는다.
capability와 provider/profile의 supported를 모두 확인한다. 빈 키 입력으로 시작하고 PC 키
설정/DB/미러에 NAS 키를 저장하지 않는다. prepare/status 응답의 operation/provider/profile/
expected revision/확인된 계좌를 검사하며 READY의 같은 revision/계좌만 apply한다.
서버 재시작 후 committed receipt의 revision은 expected revision+1로 확인한다.
apply 응답 유실은 동일 operation GET으로 확인하고 새 apply를 자동 전송하지 않는다.
operation ID를 못 받은 prepare는 메모리의 같은 request_id/프로필/revision을 유지한 재입력만 허용한다.
계좌 PUT 응답 유실은 설정 GET을 먼저 재확인한다. R6c1에서 같은 화면을 실전 프로필에도 확장했다.
NAS 설정에는 모의·실전 관리 버튼을 따로 제공하며 provider별 client/진행 요청을 공유하지 않는다.
실전은 `kiwoom_real` 프로필 생성/prepare/apply 및 `environment=real` 계좌 GET/PUT을 사용한다.
실전 PUT의 `mock_order_enabled`는 false만 허용하며 화면에서도 모의주문 토글을 숨긴다.
설정 revision 적용과 실제 `monitor_status.realtime.state=ready`를 분리해 표시한다.
상태 다시 확인은 계좌 설정 GET이며 새 인증/주문/분석을 실행하지 않는다. R6c2의 계획된
재연결 상태는 계좌 설정과 별도 프로토콜이며 아래 유한 대기 정책을 따른다.
키 관리 자체는 매매일지 기본 계좌·명령·시세 담당을 변경하지 않는다. R4b의 별도 계좌 선택을 따른다.

R6c2 `planned_reconnect_v1`은 기존 capabilities의 선택적 bool 필드다.
`/health`와 인증된 capabilities는 `realtime_connection`을 추가하며 collector가 없으면 null이다.
기존 WS ready/central_ready/상류 connection_opened/connection_failed는 선택적
`connection_status`를 추가한다. 상태 변경은 새 `connection_status` event의 payload로도 배포한다.
기존 클라이언트의 ready/subscription 접수 의미와 기존 이벤트/코드는 유지한다.

상태 필드는 `generation`, `phase`, `paused`, `shutdown`, `planned_reconnect`,
`remaining_seconds`, `started_at`, `outcome`, `observation_expected`, `previous_trade_at`,
`first_trade_at`, `gap_seconds`다. credential/계좌번호/account_ref는 포함하지 않는다.
planned reconnect는 담당 collector의 실제 drain 시작부터 최대 30초이며 같은 전환의
반복 drain/fence나 같은 generation 통지로 deadline을 갱신하지 않는다.
모든 REG 승인 후 READY에서 끝나며 재개 시작 자체의 실패/종료도 끝낸다. 대기 중 재시도할
상류 오류는 deadline 안에서만 유예한다. 정상 관측시간의 deadline 초과는
명시 connection_failed로 기존 장애/failover 정책에 복귀한다. 거래시간/구독 대기는
유예를 종료하고 waiting_market/waiting_subscription으로 알리며 거짓 장애를 만들지 않는다.
deadline은 표시/장애 판단 경계이고 실제 token/socket/저장 drain을 취소하거나 fence를 해제하지 않는다.

PC는 명시 active 상태와 유효한 int generation, 유한 0초 초과~30초 remaining만 받아들인다.
같은 generation의 통지는 로컬 deadline을 늘리지 않고 끝난/이전 generation으로 대기를 다시 시작하지 않는다.
서버 상태가 끊겨도 PC 자체 deadline에서 유예를 해제한다. 정상 상태 통지까지 기존 순위표는
비우지 않으며 계획된 상류 오류만 connection_failed 신호/즉시 로컬 전환을 유예한다.
실제 NAS transport 단절과 기존 일반 장애는 기존 정책을 따른다.

저장 자료 조회가 불가능한 TR에서 broker가 paused이고 유효한 계획된 전환이면
HTTP 503 detail `{code: REALTIME_RECONNECTING, connection_status: ...}`다.
원격 client는 이를 `CentralPlannedReconnect` 일반 API 대기 오류로 분리하며
`CentralServerUnavailable` transport 오류로 변환하지 않는다. 따라서 REST failover는 로컬 TR을 만들지 않는다.
NAS DB 우선 경로와 순위 우선순위는 유지한다. deadline 이후 오류는 기존 HTTP/장애 판단을 따른다.

gap_seconds는 교체 전 마지막 허용 0B 관측에서 새 REG 후 첫 허용 0B 관측까지의
monotonic 간격이다. UTC 관측시각과 함께 최신 상태·PC 상태 문구·PC/NAS 로그에 기록한다.
이전 관측이 없으면 null이며 0으로 추정하지 않는다. 종목별 누락량/거래소 지연이나
API와 NAS 수신 지연 차이를 뜻하지 않는다. 별도 DB 이력/outbox는 추가하지 않았다.

R4b의 인증 `GET /api/v3/kiwoom/accounts`는 `{accounts: [context]}`를 반환한다.
context는 기존 v3 응답과 같은 broker/environment/account_ref/credential_profile_id/
binding_revision/verified_at/verification_method이며 실제 mock admitted bundle과 main 조회 binding만 포함한다.
활성 자격 프로필에 사용자가 입력한 이름이 있으면 표시 전용 `display_label`을 추가한다. 이 값은 계좌
식별·조회 일치 판정에 사용하지 않으며 원문 계좌번호나 키움 자격정보를 포함하지 않는다.
capability `account_contexts_v3`는 이 목록 기능을 표시한다. 빈 목록은 현재 조회 가능한 연결 없음이고
저장된 옛 계좌를 삭제/병합하지 않는다. 이 API는 키움 TR·인증 갱신을 하지 않는다.
PC는 Qt worker로 목록을 읽고 선택 scope를 조회 worker 시작 시 같은 목록에서 다시 확인한다.
고정 scope/profile/binding 버전의 모든 페이지·날짜·비용을 검증하고 빈 결과도 완료 context로 저장한다.
조회 도중 UI 선택이 바뀌거나 context가 바뀌면 결과를 저장하지 않는다. 명시 선택 계좌는 PC 단일
프로필로 fallback하지 않으며 구 NAS의 v3 미지원도 기본 v2 계좌로 대체하지 않는다.
기존 context 없는 v2 기본 조회와 시세 fallback는 유지한다.
고정 mock client의 submit/GET/cancel은 기존 v2 scoped 경로만 쓴다. submit request_id는 호출자가
명시하고 자동 retry하지 않는다. 응답 미확인 client는 같은 ID/내용의 확인을 먼저 요구한다.
이 상태는 client 메모리 수명이며 외부 caller는 해결 전 client/ID를 유지해야 한다.
서버의 영속 intent/UNKNOWN 중복 방지 계약을 바꾸지 않는다. 자동주문 UI/실제 주문 검증은 범위 밖이다.

R3d는 인증된 `GET /api/v1/settings/accounts/{account_ref}?environment=mock|real&broker=kiwoom`
읽기를 추가한다. 응답은 `{settings: {scope, active_profile_id, monitor_enabled,
mock_order_enabled, revision}, applied_revision: int|null}`이다. 미등록/환경 불일치 계좌는
404 ACCOUNT_IDENTITY_UNVERIFIED, 잘못된 scope는 400, 저장 문서 손상은
409 ACCOUNT_SETTINGS_RECOVERY_REQUIRED다. R3e mock owner가 연결된 계좌는 실제 확인한 설정
revision을 반환하고 미연결 계좌는 null이다. 저장 토글만으로 실제 연결/주문 상태를 판단하지 않는다.
R3f는 실제 mock owner가 있는 서버에 같은 경로의 인증·HTTPS PUT을 추가한다.
요청은 `{expected_revision, active_profile_id, monitor_enabled, mock_order_enabled}` 전체 필드다.
profile 변경/선택은 허용하지 않으며 현재 active profile을 명시한다. scope query의 environment는 필수,
broker는 kiwoom 기본이다. 중복/알 수 없는 query·JSON 필드와 bool 아닌 토글을 거절한다.
응답은 GET과 같은 settings/applied_revision이다. 409는 낡은 revision 또는 준비 중 profile,
503 ACCOUNT_SETTINGS_APPLY_FAILED는 저장 전 실패 후 이전 연결 복원,
503 ACCOUNT_SETTINGS_RECOVERY_REQUIRED는 저장/실행 결과를 다시 확인해야 하는 상태다.
동일 설정도 실제 적용이 미확인되면 실행 연결을 다시 준비하며 설정 revision은 늘리지 않는다.
R3f 당시 실전 설정 변경은 503 PROFILE_RUNTIME_NOT_READY, mock owner 없는 구동은 PUT 405였다.
현재 R6b3c2a는 real/mock 중 하나의 owner만 있어도 PUT을 등록하며 요청 environment의 owner가 없으면
503 PROFILE_RUNTIME_NOT_READY다. 둘 다 없는 구동은 PUT 405를 유지한다.
이 collection은 일반 content API의 허용 목록에 포함하지 않는다.

`runtime_credentials_v1` capability는 공통 API가 존재한다는 뜻이다. 아래 GET의
`providers[].supported`와 해당 `profiles[].supported`가 참인 경우만 실행 중 변경할 수 있다.
R3e는 vault/HMAC registry가 있는 서버의 모의계좌 owner를 연결하며 R5a NAVER 지원은 위를 따른다. 미연결 공급자는
`503 PROVIDER_RUNTIME_NOT_READY`, 시세 담당 main mock profile은 `503 PROFILE_RUNTIME_NOT_READY`다.
R3f의 모의 `disable=true`는 저장된 검증 계좌/run을 대상으로 기존 prepare/apply를 사용한다.
새 인증 조회 없이 연결을 drain하고 빈 credentials의 명시 disabled tombstone을 저장한다.
DB activation receipt와 active_profile=None/토글 OFF는 같은 트랜잭션이며 기존 binding은 추가하지 않는다.
계좌 이력·미확정 주문은 유지한다. disabled ACTIVE는 비활성화 적용 완료이며 거래 연결 준비를 뜻하지 않는다.
재시작 시 tombstone은 env보다 우선하고 계좌 bootstrap/TR/WS를 시작하지 않는다.
monitor ON 모의계좌는 초기 read/lease가 확인된 후 ACTIVE를 표시한다. 신규 계좌 주문은 OFF다.
기본 `/api/v1/mock/orders`는 admitted nas-mock-default bundle만 사용하고 boot/교체 중에는 503이다.
provider는 kiwoom_real/kiwoom_mock/naver/dart/openai/gemini/claude 중 하나다.

| Method / path | 요청 | 응답 |
| --- | --- | --- |
| `GET /api/v1/settings/credentials` | 없음 | 공급자 지원 여부와 프로필 configured/source/revision/validation/runtime, 익명 account_ref. 키·토큰 없음 |
| `POST /api/v1/settings/credentials/{provider}/profiles` | UUID request_id, label(최대 120자) | 키 없는 draft profile UUID. 같은 request_id/내용은 같은 profile |
| `PUT /api/v1/settings/credentials/{provider}/profiles/{profile_id}` | expected_revision, label(1~120자) | 실전·모의계좌의 표시 이름만 변경. 계좌 신원·binding·인증키·설정·매매 이력은 유지 |
| `POST /api/v1/settings/credentials/{provider}/profiles/{profile_id}/prepare` | UUID request_id, expected_revision, replacement 또는 disable=true | 202 operation_id와 상태. 활성 키/binding 미변경 |
| `DELETE /api/v1/settings/credentials/{provider}/profiles/{profile_id}` | expected_revision | 연결 해제된 profile만 archived 처리. 계좌 신원·binding·activation·매매 이력은 보존하고 일반 목록에서 제외 |
| `GET /api/v1/settings/credential-operations/{operation_id}` | 없음 | 상태·safe error_code·revision·committed·계좌 미리보기. 비밀 없음 |
| `POST /api/v1/settings/credential-operations/{operation_id}/apply` | expected_revision, Kiwoom은 확인한 target_account_ref UUID | 202 적용 시작 또는 동일 작업 결과 |
| `DELETE /api/v1/settings/credential-operations/{operation_id}` | 없음 | 준비만 취소. 적용 시작 뒤 취소 불가 |

replacement는 공급자별 전체 필드다: Kiwoom app_key/secret_key, Naver client_id/client_secret,
DART api_key, AI api_key. 빈 값·토큰 필드·미지정 필드·중복 JSON 키는 허용하지 않는다.
request_id가 같고 내용이 다르면 409이며 적용 완료 재요청은 외부 적용을 다시 실행하지 않는다.
준비 TTL은 5분, 한 프로필당 한 작업·전체 진행 중 32개·보존 상태 최대 256개다.
취소·만료·HTTP 단절은 이미 시작한 실제 통신을 종료된 것으로 간주하지 않는다.
deadline 이후 BUSY는 실제 작업 종료를 기다리며 늦은 자동 적용을 하지 않는다.
준비만 했던 작업은 서버 재시작 후 410, 파일 commit 이후 작업은 복구 대상이다.

쓰기에는 HTTPS 또는 `CREDENTIAL_TRUSTED_PROXIES`에 명시한 IP가 전달한 단일
`X-Forwarded-Proto: https`가 필요하다. Uvicorn proxy_headers는 false이며 임의 헤더를 신뢰하지 않는다.
이 API에는 query를 허용하지 않는다. query는 access-log scope에서도 제거한다.
본문 상한은 16KiB이며 오류는 `detail: {code}`만 반환한다. 400 query, 409 충돌/준비 상태,
410 알 수 없는 작업, 413 본문 초과, 422 잘못된 입력, 426 HTTPS 필요, 503 저장소/미지원 상태다.
ACTIVE는 파일·DB commit과 실제 runtime revision 확인이 끝난 상태다. 파일 commit 이후 실패는
RECOVERY_REQUIRED이며 이전 키로 자동 rollback하지 않는다. DB 최종화는 binding과 같은 트랜잭션이다.

### 운영 설정 적용

운영 설정의 `expected_revision`이 현재 `revision`과 다르면 저장하지 않고 409를 반환한다.
미변경 요청은 revision을 증가시키지 않는다. 구 클라이언트의 revision 생략은 읽기/부분 쓰기
호환을 위해 허용하므로 구 클라이언트의 전체 덮어쓰기를 모두 방지하는 계약은 아니다.
저장 실패는 `OPERATIONAL_SETTINGS_SAVE_FAILED`이며 메모리와 실행 설정을 유지한다.
저장 후 적용 실패는 `OPERATIONAL_SETTINGS_APPLY_PENDING`; GET의
`apply_status=RECOVERY_REQUIRED`, `applied_revision<revision`으로 구분한다.
현재 revision을 읽고 빈 부분 PUT으로 재적용하거나 재시작 시 영속 설정을 다시 조립한다.
성공 시 `apply_status=ACTIVE`, `applied_revision=revision`이다.
| `GET /api/v1/diagnostics/resources` | 없음 | 프로세스/호스트 메모리, 디스크·DB 전체 크기, 뉴스/시장/연구/계좌/기타별 추정 용량·건수와 현재 보존 상태 | NAS 자원 진단 |

운영 설정 `ai_provider`는 `none/openai/gemini/claude`, 뉴스 간격은 60~86,400초다. 이 API는 공급자 비밀키를 반환하거나 변경하지 않는다.

NAS 연결 설정과 뉴스 설정은 이 운영 설정 API를 함께 사용한다. 뉴스 설정에서 저장할 때는 먼저 GET으로 그 화면이 편집하지 않는 값을 보존한 뒤 AI 공급자·모델·일일 한도, DART 사용 여부와 NAS 처리 제외 언론사를 합쳐 PUT한다. `news_processing_excluded_providers`는 최대 100개의 언론사명 또는 원문 도메인이다. 일치하는 새 기사는 제목·링크와 source observation을 보존하지만 BODY/RULE/자동 AI 작업을 만들지 않는다. 이미 대기 중이거나 완료된 작업은 소급 삭제하지 않으며, 제외를 해제한 뒤 기사가 다시 관측되면 누락 BODY 작업을 멱등 예약한다.

Shadow 후보 설정은 `shadow_candidate_enabled`, 검증된 `shadow_candidate_config` 객체, `shadow_candidate_poll_seconds`, `shadow_candidate_universe_max_age_seconds`다. PUT 성공 시 NAS 프로세스를 재시작하지 않고 후보 감지 task만 교체하거나 중지한다. 잘못된 전략 조건은 422로 거부하고 현재 task와 저장 설정을 유지한다. `.env` 값은 DB 운영 설정이 아직 없을 때의 최초 기본값으로만 사용한다.

## Kiwoom 조회

### `POST /api/v2/kiwoom/account-query`

검증 계좌 전용 읽기 경계다. 현재 `kt00007`, `kt00015`와 `/api/dostk/acnt` 조합만 허용한다. 첫 요청은 `batch_id=""`, `page_index=0`, `next_key=""`로 시작한다. 응답은 `payload`, 서버가 발급한 `batch_id`, `page_index`, `complete`, `next_key`, 익명 `context`, `provenance`를 반환한다. `context`에는 `broker`, `environment`, 지속 UUID `account_ref`, `credential_profile_id`, `binding_revision`, 검증 시각·방법이 있으며 원문 계좌번호와 API 자격증명은 없다.

연속 페이지는 첫 요청과 같은 `api_id/path/body`, 서버가 반환한 `batch_id/next_key`, 정확히 증가한 `page_index`를 보내야 한다. 서버는 120초 세션 동안 이 값과 실제 인증 binding을 고정한다. 만료 cursor, 본문·순서·cursor 불일치, 조회 중 binding 변경은 `409`로 전체 batch를 무효화한다. 마지막 응답의 `complete=true` 전에는 어느 페이지도 완료된 계좌 수입으로 저장하지 않는다. 클라이언트의 20페이지 안전 상한에서 다음 페이지가 남으면 partial 오류다.

NAS 통신이 페이지 도중 끊기면 검증된 직접 API binding이 같은 `account_ref`일 때만 첫 페이지부터 다시 조회한다. 다른 계좌이면 전체 결과를 폐기한다. 구 NAS의 404/405/501은 capability 부족이며 v1 payload에 화면 선택 계좌를 붙여 v2처럼 저장하지 않는다. 계좌 API는 중앙·직접 병행검증 대상에서 제외한다. 일반 시세용 v1 조회와 페이지별 failover 계약은 그대로 유지된다.

2026-09-13 구현 상태 정정: 위 장애전환 계약에서 직접 adapter는 아직 저장 binding과 현재 자격을 fresh `ka00001`으로 대조하지 않는다. 따라서 같은 실제 계좌임을 검증하는 부분은 [A4b 계획](docs/archive/2026-09-22/reports/A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md) 1~3단계의 미구현 보완이다.

### 구현 예정: `POST /api/v2/accounts/resolve`

인증된 HTTPS에서 `{environment, account_number}`를 받아 NAS의 기존 검증 registry와만 대조한다. 응답은 `{canonical_scope, matched_binding: {credential_profile_id, binding_revision, verified_at, verification_method}, matched_at}`이며 NAS가 저장한 검증 근거와 PC 로컬 자격의 검증 revision은 구별한다. 요청으로 NAS registry나 활성 profile/binding을 생성·교체하지 않는다. 미등록은 `ACCOUNT_NOT_REGISTERED`이며 local origin의 중앙 결합만 보류한다. PC 키 소유를 NAS가 독립 증명하는 API가 아니라 신뢰하는 단일 소유자 PC의 관측값을 대조하는 경계다.

예정 capability는 `account_identity_resolve_v2`다. HTTP·인증서 오류·redirect는 거절하고 raw를 repr/validation `input`/예외/로그/DB/캐시에 남기지 않는다. 임의 proxy 헤더로 TLS 판정을 우회할 수 없어야 한다. R7c2 기록에서 NAS HTTPS/WSS 연결과 앱의 HTTPS 주소 전환은 확인됐다. 이 전송 경계의 확인과 별개로 resolve·aliases 경로 및 이 capability는 **현재 미구현**이다.

먼저 만든 local origin의 결합은 별도 인증 HTTPS `POST /api/v2/accounts/aliases`로 구현할 계획이며 현재 미구현이다. 입력 `{environment, account_number, origin_scope}`를 서버에서 같은 계좌로 대조한 후 기존 alias 저장 계약을 적용하고 익명 AccountScopeAlias를 반환한다. client가 canonical ref를 선택하지 않는다. 같은 연결은 멱등이며 대상 교체·환경 교차·연쇄/순환은 거절한다. 두 신원 경계가 준비된 뒤 위 capability를 true로 제공한다. raw는 일반 콘텐츠에 포함하지 않는다. 직접 query context의 기존 scope는 origin으로 유지하고 선택 canonical_scope로 매핑을 표현해 alias 뒤에도 저장 key가 변하지 않게 한다.

### `POST /api/v1/kiwoom/query`

요청:

```json
{"api_id":"ka10001","path":"/api/dostk/stkinfo","body":{"stk_cd":"005930"},"cont_yn":"N","next_key":""}
```

응답:

```json
{"payload":{},"has_next":false,"next_key":"","cache_hit":false,"archive_hit":false}
```

허용 API와 경로는 `central_server/rest_broker.py`의 `READ_ONLY_ENDPOINTS`가 현재 구현 원본이다. 주요 조회는 `ka00198`, `ka10016`, `ka10001`, `ka10100`, `ka10054`, `ka10080`, `ka10081`, `ka10083`, `ka10094`, `ka10045`, `ka90008`, `ka20005`, `ka20006`, `kt00007`, `kt00015`다. `ka10054`는 VI 시작·재연결 누락 보완용 저우선순위 조회다. 주문 API는 허용하지 않는다. `cont_yn=Y`에는 `next_key`가 필수다.

2026-09-13 감사에서 같은 allowlist의 `ka00001`이 공개 v1 route에서도 원문 응답으로 전달될 수 있음을 확인했다. 위 신원 대조 구현 시 공개 전달만 명시 거절하고 내부 broker의 `ka00001`/limiter는 유지한다. 이는 해당 민감 TR의 호환 정책 변경이며 기존 시세 요청/응답 형태는 유지한다. 현재 코드에서 이미 차단된 것으로 해석하지 않는다.

클라이언트 의존: `RemoteKiwoomRestClient`는 `payload/has_next/next_key`를 직접 읽으며, 중앙 접속 실패와 정상 HTTP API 오류를 구분한다. `archive_hit`는 완료 coverage가 있는 중앙 분봉·일봉을 재사용했음을 나타내는 선택 필드다. 필드 제거·이름 변경은 스키마 버전 상승 없이 금지한다.

O1의 mock 계좌 대조는 일반 `/api/v1/kiwoom/query`와 분리된 내부 broker namespace를 사용한다. 확인된 조회 allowlist는 미체결 `ka10075`, 체결 `ka10076`, 계좌평가잔고 `kt00018`, 주문가능금액 `kt00001`이며 모두 `/api/dostk/acnt`다. 주문 `kt10000~kt10003`은 이 조회 broker, 장애전환 클라이언트, 병행검증 클라이언트에서 거부된다. 별도 mock transport만 `/api/dostk/ordr`로 한 번 전송하며 아래 인증 API는 `MOCK_ORDER_TRANSPORT_ENABLED=1`일 때만 사용 가능하다.

## 수동 모의주문

| Method / path | 요청 | 응답 핵심 |
| --- | --- | --- |
| `POST /api/v1/mock/orders` | `request_id`, 6자리 `symbol`, `side=BUY/SELL`, 양수 `quantity`, 양수 `limit_price`, 선택 `expires_seconds=10..600` | intent·broker ID, `policy_version`, 상태, 체결수량, 시각, `events[]` |
| `GET /api/v1/mock/orders/{intent_id}` | 없음 | 현재 원장 상태와 `events[]` |
| `POST /api/v1/mock/orders/{intent_id}/cancel` | 선택 `quantity`; 0은 남은 수량 전체 | 취소 요청 뒤 현재 원장 상태와 `events[]` |

검증된 다계좌 경로는 `/api/v2/mock/accounts/{account_ref}/orders...`를 사용한다. A5의 읽기 경계인
`GET /api/v2/mock/accounts/{account_ref}/execution-events`는 `credential_profile_id`,
`expected_binding_revision`, `environment=mock`, 선택 `after_sequence>=0`, `limit=1..1000`을 받는다.
응답은 검증된 익명 `context`, 수락 순서대로 정렬된 `events[]`, `next_cursor`, `has_more`다.
각 행은 `accepted_sequence`, 불변 `source_event_id`, intent의 `run_id/decision_id/symbol/venue/side`,
event의 상태·발생/수신시각·broker 주문/체결 ID·수량·가격을 함께 가진다. 커서는 계좌 전체 원장을
기준으로 하며 run이 바뀌어도 이어진다. 다른 계좌나 오래된 binding으로 읽을 수 없고 이 API는
Kiwoom TR 또는 주문을 만들지 않는다. capability는 `execution_event_read_v1`이다.

신규 주문은 인증된 수동 KRX 지정가만 받는다. `09:00~15:20`은 검증된 정규장 정책이며 `16:00~20:00`은 키움 모의투자의 새 KRX 애프터 지원 여부를 broker 응답으로 확인하는 `manual-mock-krx-after-limit-probe/v1`이다. probe 허용은 지원 확정이 아니며 접수·거절 결과를 원장과 응답의 `policy_version`으로 구분한다. NXT·시장가·15:20~16:00 신규 주문은 허용하지 않는다.

`request_id`는 같은 `MOCK_EXECUTION_RUN_ID` 안의 멱등키다. 같은 내용으로 다시 요청하면 기존 결과를 반환하고 broker에 재전송하지 않는다. 다른 종목·방향·수량·가격에 같은 ID를 재사용하면 400이다. 전송과 취소 전에 계좌 REST 복구를 먼저 수행한다. 응답 유실은 `SUBMISSION_UNKNOWN`, 취소 응답 유실은 `CANCEL_PENDING`으로 남기며 자동 재전송하지 않는다. 신규 수동 LIMIT 전송은 시행일별 공통 세션 정책의 KRX 정규장 연속매매 `09:00 <= KST < 15:20`와 위 `16:00~20:00` KRX 애프터 probe 경로를 구분한다. 장후종가·NXT·동시호가 등 허용되지 않은 신규 주문은 `PRECHECK_REJECTED` 상태와 `UNSUPPORTED|reason|venue|order_type|session|phase|schedule|profile` 근거를 `events[]`에 남긴다. 이 시간 gate는 조회·취소·broker 대조·재연결 복구·늦은 체결을 막지 않으며, 미체결 잔량은 broker 확인 없이 시각만으로 종료하거나 16시에 자동 재주문하지 않는다. 후보나 전략은 이 API를 자동 호출하지 않는다. O2-M 자동 운용의 READY 지원은 별도 `krx-regular/v1` 범위이며 수동 애프터 probe로 확대되지 않는다.

## 뉴스와 AI

| Method / path | 요청 핵심 | 응답 핵심 |
| --- | --- | --- |
| `POST /api/v1/news/search` | `stock_code`, `stock_name`, 선택 `since`, 자동분석 옵션 | `stock_code`, 최신순 `items[]`(최대 1000) |
| `POST /api/v1/news/analyze` | 종목, provider/model, `events[1..20]`, `article_count` | 중앙 AI 서비스 분석 문서. 공급자 429/5xx는 상태 코드를 보존한다. |
| `GET /api/v1/news/history/{kind}` | `kind=article/body/ai/event/membership`, 선택 `target`, `identity`, Unix 초 `as_of`, `limit` | `known`, `kind`, `revisions[]`. 서버 가용시각 역순의 불변 이력 |
| `GET /api/v1/news/sources` | 선택 `source_id`, `days=1..31`, `limit` | `scope=query_set`, `coverage=configured_query_set`, source별 cursor/checked/last success/최근 실행·관측과 raw/unique/distinct identity/duplicate/truncated/error/request/budget, 본문·규칙·target·job 요약 |

사건은 `identity`, `title`, `body`, `body_hash`, `articles[]`를 가진다. 본문 최대 길이는 2,000,000자다. AI 결과의 요약·판정·근거는 요청한 `stock_name`의 주가·실적·사업 영향 관점이어야 하며, 종목이 단순 나열됐거나 직접 근거가 없으면 `판단 자료 부족`으로 반환한다. `body_hash`는 본문뿐 아니라 대상 종목과 분석 계약 버전(`target-company-v2`)을 포함하므로 이전의 시장 전체 관점 결과는 새 자동분석의 완료 캐시로 재사용하지 않는다. 캐시 재사용 여부와 사용량 구조는 중앙 AI 서비스와 클라이언트 테스트가 보호한다.

규칙 관련성 판정은 새 기업 사건 없이 이미 발생한 종목·시장 가격 움직임만 설명하는 기사를 `시세 반영·시장 요약`으로 제외한다. 환율·유가·금리·실적 전망 자체의 급등락은 종목 주가 반응으로 취급하지 않으며, 같은 제목에 별도의 주가·지수 반응이 남아 있으면 시황 기사로 판정한다. `투자심리`는 인수합병, 일반 `증시 전망`은 기업 실적 전망으로 분류하지 않는다.

`news/search`는 기존 종목 owner 기사에 해당 종목 코드로 `confirmed` 연결된 N3 GLOBAL 기사를 합친다. unresolved·ambiguous·다른 종목은 제외하고, 같은 identity가 겹치면 기존 종목 owner 기사를 우선한다. GLOBAL의 같은 identity 판본은 중앙 수락 순서가 가장 최신인 confirmed 판본 하나만 사용한다. 합친 결과는 게시시각 내림차순, 같은 시각은 identity 오름차순이며 게시시각 결측·오류는 `since`가 없을 때 마지막에 둔다. `since`가 있으면 시각을 확인할 수 없거나 cutoff 이하인 행은 제외한다. 목록 병합은 NAVER 요청이나 BODY/RULE/AI 예약을 추가하지 않고, 자동 AI 후보는 기존 종목 owner 기사만 유지한다.

기사 이력의 `target`은 종목코드, `identity`는 기존 기사 identity다. 본문 이력의 `target`은 `article_revision_id`, AI 이력의 `target`은 분석 대상 종목코드다. `as_of`는 `available_at <= as_of`인 행만 반환하며 최초 원장 기록 전에는 `known=false`다. 이 API는 현재 화면용 `news_article/news_ai` projection을 변경하지 않는다.

사건 이력의 `target`은 종목코드, `identity`는 영속 `event_id`다. 소속 이력은 `target=event_id`, 선택 `identity=article_revision_id`로 조회한다. `event` 행은 정확한 article/body revision과 규칙·점수 산식 버전, 역할·범위·확실성·신규성, 근거 span, 금액/상대방, 대상별 방향·직접성, `ai_required` 이유를 가진다. URL은 event ID가 아니며 `possible_related_event_ids`는 후보 관계일 뿐 과거 행을 병합하지 않는다.

`news/sources`는 NAVER 검색 결과의 설정된 query set 범위만 설명한다. 전수 시장 피드가 아니며 `truncated` 또는 `query_set_error` 구간은 누락 가능 구간이다. `days`와 선택 `source_id`는 summary의 기간·source 집계 및 본문/규칙/target 집계에 적용되고, `limit`은 `runs[]`와 `observations[]` 상세행 수만 제한한다. `unique_count`는 그 기간에 새로 추가된 immutable article revision 수이고, `distinct_identity_count`는 관측된 서로 다른 기사 identity 수이므로 실제 기사 수를 볼 때 둘을 구분한다. `job_queue_scope=all_news_jobs`는 전역 작업 대기열, `budget_scope=today_kst_all_news_sources`는 오늘 KST 전체 source 예산임을 뜻한다. 인증키·비밀은 응답과 저장 문서에 포함하지 않는다.

## 시장 데이터

| Method / path | query | 응답 |
| --- | --- | --- |
| `GET /api/v1/market/minute-bars` | `code`, `trading_date=YYYY-MM-DD`, 선택 `market=KRX/NXT/SOR/COMBINED` | 같은 식별자 + `bars[]`, `coverage.complete/markets`; COMBINED 행에 `source_market` 포함 |
| `GET /api/v1/market/recent-minute-bars` | `code`, `end_date=YYYY-MM-DD`, 선택 `market=KRX/NXT/SOR/COMBINED`, `trading_days` 1~5 | 마지막 실제 거래일 목록 + 정렬된 `bars[]` |
| `GET /api/v1/market/latest-market-caps` | 반복 `codes=6자리 종목코드`, 1~200개 | 종목별 마지막 저장 0B의 `market_cap_eok`, `observed_at`; 현재가·등락률은 반환하지 않음 |
| `GET /api/v1/market/trade-value-comparisons` | `code`, `trading_date=YYYY-MM-DD`, 선택 `limit` | SOR 실시간값·KRX/NXT 분봉 조회값·차이·차이율 `comparisons[]`; KRX+NXT 완전 비교만 집계한 `summary` |
| `GET /api/v1/market/events` | `kind=vi/cohort/upper_limit`, 선택 `code`, `limit` | 불변 `history[]`; cohort는 현재 projection `current[]`와 조건 선택 진단 `condition` 포함 |
| `GET /api/v1/market/daily-bars` | `code`, 선택 `market`, `limit` 1~5000 | 식별자 + `bars[]` |
| `GET /api/v1/market/coverage` | `kind`, `subject`, ISO `start/end`, 선택 `available_by`, 고정주기 자료만 `expected_seconds` | 상태·관측 수·가용 수·결측 구간·부재 의미 |
| `GET /api/v1/market/external-bars` | `instrument`, `timeframe=5m/1d`, `limit` 1~10000 | 공급원·계약코드가 포함된 `bars[]` |
| `GET /api/v1/market/snapshots/{kind}` | 선택 `subject`, `limit` 1~5000 | `kind`, `subject`, `snapshots[]` |
| `GET /api/v1/market/top20-statistics` | `start_date/end_date=YYYY-MM-DD`, 최대 367일 | NAS TOP20 `hourly[]`, 정규장 일별 `comparisons[]`; 전체시장 거래대금은 `ka20006` 확정 일봉 |
| `GET /api/v1/research/observations` | offset 포함 ISO `start/end`, `kinds=ranking,top20_membership,minute_bar` 중 하나 이상, 선택 `subject`, `watermark`, `cursor`, `limit` 1~1000 | 고정 `manifest/watermark`, 순서가 붙은 `observations[]`, `next_cursor` |

스냅샷 `kind`는 `ranking`, `top20_membership`, `top20_index`, `market_state`, `investor_flow`, `program_flow`, `new_high`, `stock_fundamentals`, `nxt_eligibility`를 허용한다. `stock_fundamentals`와 `nxt_eligibility`는 시점 이력과 별도로 범용 콘텐츠의 `stock_fundamentals`, `stock_nxt_eligibility` 컬렉션에서 종목별 최신값도 조회할 수 있다. 봉의 중앙 저장 단위는 거래대금 백만원(`trade_value_million_won`)이다.

NAS 연결 중 TOP20 차트는 `top20_index` 중앙 스냅샷을 읽고, 통계는 서버가 같은 원본을 집계한 `top20-statistics`를 읽는다. PC 직접 연결 중에는 로컬 `monitor.sqlite3`를 사용한다. 정규장 TOP20 합계는 09:00부터 15:30 종가 단일가 체결분까지 포함한다. 과거 전체시장 분모는 `ka20006` 일봉의 코스피·코스닥 거래대금을 사용하므로 장중 `0J/0U` 최종 수신 전에 끝난 값으로 과거 통계를 고정하지 않는다.

거래대금 비교의 `summary.complete_count`와 차이 통계는 `query_scope=KRX+NXT`인 분만 대상으로 한다. `partial_count`와 `scope_counts`는 KRX 또는 NXT 한쪽만 보완된 중간 자료를 따로 보여준다. `total_difference_percent`는 완전 비교 합계의 `(SOR-조회)/조회`, `average_difference_percent`는 분별 차이율 평균이며 `mean_absolute_difference_percent`와 `max_absolute_difference_percent`는 방향을 제거한 오차 크기다.

NAS 클라이언트의 당일 분봉은 `GET /api/v1/market/minute-bars`, 오늘과 직전 거래일 분봉은 `GET /api/v1/market/recent-minute-bars`의 `COMBINED` 보기를 한 번 읽는다. 같은 분에 SOR가 있으면 SOR 한 벌을 사용하고, 없을 때만 KRX+NXT를 합친다. `minute-bars.coverage.complete`는 해당 거래일의 장후 분봉 수집이 끝났다는 중앙 완료 문서가 있을 때만 참이다. 매매일지는 행이 하나 이상 있다는 이유만으로 장 종료 확정하지 않으며 이 값을 받은 뒤에만 `after_close_confirmed`로 저장한다. 구 NAS가 COMBINED를 지원하지 않으면 앱은 기존 KRX·NXT 중앙 조회로 호환한다. 중앙 연결이 정상인데 저장 행이 없으면 빈 결과를 반환하며 앱이 `/api/v1/kiwoom/query`의 `ka10080`을 대신 발생시키지 않는다. 중앙 연결 자체가 끊기고 사용자가 로컬 장애전환을 켠 경우에만 PC 직접 조회가 허용된다. 범용 조회의 첫 페이지는 완료 coverage가 있는 `ka10080/ka10081` 아카이브와 `ka10001/ka10100` 최신 중앙 문서를 먼저 검사한다. 적합한 저장 자료가 있으면 broker queue와 Kiwoom TR을 사용하지 않고 `archive_hit=true`로 반환한다.

coverage 상태는 `complete/partial/missing`이다. `available_by`를 지정하면 그 시각까지 실제로 가용했던 관측과 그 전에 저장된 완료 근거만 센다. 응답의 `window_closed`는 요청한 시간 구간이 cutoff 전에 끝났는지, `session_finalized`는 별도 장후 완료 근거가 있는지를 나타내며 서로 대신하지 않는다. 기존 `explicit_complete`는 `session_finalized`와 같은 완료 근거의 호환 필드다. 분봉·일봉은 거래가 없으면 행 자체가 없을 수 있으므로 `expected_seconds`를 허용하지 않고, 별도 장후 완료 문서가 있을 때만 0개 행을 완전 수집으로 판정한다. 순위 후보군·TOP20 지수·시장 상태처럼 고정 주기를 가질 수 있는 자료에만 `expected_seconds`를 주어 `missing_intervals`를 계산한다. `absence_meaning`은 실제 무체결 가능성과 미수집을 구분하지 못하는 경우 이를 명시한다.

외부 시장 봉은 현재 임시 Yahoo 지연 시세 수집 결과다. `NASDAQ_FUTURES`와 `WTI_FUTURES`는 실제 월물 계약을 함께 저장하며 각 행의 `provider`, `contract`, `bar_time`을 제거하지 않는다. 서버는 전월물과 차월물을 동시에 수집하고 차월물 거래량 우위를 연속 확인하면 대표 월물을 앞으로만 교체한다. 월물 간 절대가격 차이는 등락률로 계산하지 않으며, 방향값은 선택된 월물 자체의 전일 종가 기준이다. 이 자료는 실시간 주문 판단용 시세가 아니다.

연구 관측의 첫 요청은 최대 24시간 범위에서 현재 커밋된 revision ID 집합을 새 immutable manifest로 확정한다. 다음 페이지는 첫 응답의 `watermark`와 `next_cursor`를 그대로 보내며 시작·종료·종류·대상도 동일해야 한다. 추출 도중 새 관측이 들어와도 이미 확정한 ID 집합에는 추가되지 않는다. manifest의 `revision_count`와 `revision_ids_hash`로 전체 페이지의 누락·중복을 검사한다. 현재 quality의 `recording_gap=unknown`은 D1 기록 자체만 고정했고 세션별 수집 공백 판정은 아직 하지 않았다는 뜻이다.

`minute_bar` 연구 payload는 KRX/NXT/SOR 원본을 합치지 않으며 `bar_start/end`, OHLCV, 백만원 거래대금, 단위, `window_closed`, `session_finalized`, `capture_quality`, `finalization_source`와 신규 revision의 `session`, `phase`, `schedule_version`, `session_support`를 포함한다. 실시간 flush revision은 형성 중 누적 전체 봉이고 `window_closed=false`다. 타이머 마감은 실제 `bar_end` 뒤의 처리 시각을 `available_at`으로 사용한다. `session_finalized`는 장후 조회처럼 거래 세션 전체가 끝난 뒤의 확인인지 따로 표시한다. strict 연구 입력은 KRX·실제값·시간상 마감·수집 완전 revision 중 명시한 버전 있는 session profile이 허용한 봉만 사용한다. SOR 원본은 통합 시세 연구가 명시적으로 요청할 때만 사용한다. 기존 payload의 필드 누락은 기존 `krx-regular/v1` reader 경계에서만 호환한다.

## 자동 모의운용 후보 게시

`POST /api/v1/research/mock-automation-candidates`는 PC의 완료된 final holdout 결과를 NAS에
게시하는 전용 Bearer 인증 경계다. 요청은 현재 검증된 mock 계좌의 `account_ref`,
`credential_profile_id`, `expected_binding_revision`과 다음 세 불변 문서를 받는다.

- `mock_automation_candidate_package/v1`: 기존 `final_candidate/v1` 문서와 그 hash, 등록 family,
  정규 parameters, execution/session model, scientific implementation hash, final batch/run/result,
  OOS 평가 근거를 담는다.
- `mock_automation_eligibility_policy/v1`: final 실행 시작 전에 동결한 최소 거래일·거래수·순손익과
  최대 drawdown 기준을 담는다. 미정 수치는 합격으로 취급하지 않는다.
- `mock_automation_eligibility_receipt/v1`: 위 정책과 결과를 결합해 `ELIGIBLE` 또는 `BLOCKED`와
  관측 수치·사유를 고정한다.

서버는 256 KiB 크기, version, 등록 family와 정규 필드, 모든 content hash와 계보, 현재 scientific
implementation hash, 현재 mock binding을 다시 검사한다. 같은 ID·같은 내용은 `unchanged`, 같은 ID에
다른 내용은 409다. 응답의 `orders_started`는 항상 false이며 게시만으로 runtime lease나 주문 transport를
호출하지 않는다. capability는 `mock_automation_candidate_publish_v1`이다. 이 세 문서는 아래 범용 콘텐츠
allowlist에 포함되지 않는다.

`GET /api/v1/research/mock-automation-candidates/{account_ref}`는 READY 명세 작성에 사용할 게시 후보를
읽는다. query의 `credential_profile_id`가 현재 검증된 mock binding에서 같은 `account_ref`를 가리켜야
한다. 응답의 `binding`은 profile ID, broker/environment/account_ref, binding revision, `verified_at`,
`verification_method`를 포함하고,
`candidates[]`의 각 행은 package, eligibility policy, account-scoped receipt의 완전한 묶음이다. 누락되거나
계보가 맞지 않는 저장 묶음은 409이며 다른 계좌 후보를 섞지 않는다. capability는
`mock_automation_candidate_read_v1`이다.

`POST /api/v1/research/mock-automation-specs`는 게시된 ELIGIBLE 후보를 READY 운용 명세로
동결하는 별도 Bearer 인증 경계다. 요청은 같은 mock 계좌의 `account_ref`,
`credential_profile_id`, `expected_binding_revision`, 중앙 shadow 후보의 `shadow_event_id`와 다음
문서를 받는다.

- `ForwardEvaluationSpec`: 등록 family/factor, 후보의 session, NAS data path, 평가기간과 미정값 없는
  forward 기준을 고정한다.
- `StrategyStageRevision` 3개: `DRAFT→EVALUATED→VALIDATED→SHADOW` 순서와 후보 package hash,
  eligibility receipt ID, 실제 shadow event ID를 각각 증거로 연결한다.
- `MockAutomationOperatingSpec`: candidate/final 계보, 현재 mock binding, forward profile ID,
  동시 전략·포지션·자금·일손실·자료 공백·장애 한도와 고정 운영 정책을 담는다.

서버는 요청 전체 256 KiB 상한, 후보·policy·receipt·final 계보, 등록 family/factor/session, 실제 저장된
shadow event의 monitor/config/전략/version/전이/시각, 현재 mock binding과 READY 판정을 모두 저장 전에
검사한다. 기존 stage 이력은 요청 chain의 정확한 prefix일 때만 이어 쓴다. 동일 내용 재게시는
`unchanged`이고 충돌하는 profile/stage/spec은 409다. 응답의 `orders_started`는 항상 false다.
`GET /api/v1/research/mock-automation-specs/{account_ref}`는 저장 명세와 게시 계보 기준 readiness를
조회하며 runtime 시작 상태는 아래 계좌 상태 API에서 따로 읽는다. capability는
`mock_automation_spec_publish_v1`이다. 현재 중앙 shadow 생성기는 breakout family만 실행하므로 다른
등록 family는 실제 대응 shadow event가 추가되기 전까지 이 경계를 통과할 수 없다.

## 자동 모의운용 제어

capability `mock_automation_runtime_v1`이 참인 서버는 저장된 READY 운용 명세에 한해 다음 Bearer 인증
경계를 제공한다. 시작 요청은 `account_ref`, `credential_profile_id`, `spec_id`, 현재
`expected_settings_revision`, `credential_revision`을 받는다. 서버는 기존 mock credential owner에서
flat 계좌를 수동 bundle에서 불변 자동 run bundle로 교체하고 admission·최신 broker 대사를 완료한 뒤
runner를 시작한다. 중간 실패는 자동 bundle의 신규 주문을 닫은 상태로 유지한다.

| Method / path | 의미 |
| --- | --- |
| `GET /api/v1/mock-automation/accounts/{account_ref}` | `credential_profile_id`의 control·runtime·runner·복원 오류 상태 조회 |
| `POST /api/v1/mock-automation/start` | 저장된 RUNNING/신규 control의 READY 명세를 계좌별 runner로 시작 |
| `POST /api/v1/mock-automation/stop` | `expected_control_revision` CAS로 신규 주문 판단 중지 |
| `POST /api/v1/mock-automation/resume` | 중지 뒤 새 broker 대사를 기록하고 같은 revision을 CAS로 재개 |

중지·재개 요청은 account/profile/spec, 현재 `expected_control_revision`, 비어 있지 않은 `reason`을
포함한다. 재개는 재시작 뒤 수동 bundle에서도 안전하게 자동 bundle을 다시 만들 수 있도록 현재
`expected_settings_revision`과 `credential_revision`도 요구한다. STOP은 기존 주문을 임의 취소하거나
보유를 청산하지 않는다. NAS 재시작은 저장된 RUNNING
control만 복원하며 계좌 자격·설정·명세가 맞지 않으면 재시도 간격을 두고 fail-closed 상태를 노출한다.
후보 게시만으로 시작할 수 없으며 위 READY 명세 게시까지 성공해야 시작 요청의 `spec_id`가 생긴다.

## 범용 콘텐츠

계약상 검증 계좌의 일지 문서는 `journal_v2_fills`, `journal_v2_reviews`, `journal_v2_setups`, `journal_v2_cycle_overrides`, `journal_v2_group_overrides`, `journal_v2_entry_snapshots`, `journal_v2_costs`, `journal_v2_enrichment_tasks`, `journal_v2_analysis_revisions`, `journal_v2_research_links`에 저장하고 뉴스 연결은 `journal_v2_news_links`, 삭제 상태는 `journal_v2_sync_states`로 분리한다. 각 v2 문서는 origin/canonical broker·environment·UUID를 명시해야 하며 legacy 자료는 v1 전용이다. 현재 앱은 `journal_v2_sync=true`를 확인한다.

서버는 v1 `journal_sync_states`를 허용하고 신규 뉴스 연결은 독립 `journal_news_links_v2=true` capability로 협상한다. sync state도 `(collection, origin owner, document_key)`로 계좌별 분리하며 envelope owner/key, target namespace와 origin/canonical scope를 함께 검증한다. v2 문서는 kiwoom/real 또는 mock/UUID와 origin owner 및 scope 기반 hash key를 검증하며 alias 증명이 없는 canonical 변경은 거절한다. v1은 legacy owner/key만 허용한다. optional 404와 401/500/timeout 구별은 유지한다.

| Method / path | 의미 |
| --- | --- |
| `GET /api/v1/content/{collection}` | `owner`, `limit`, `offset`, `updated_after`로 증분 조회 |
| `POST /api/v1/content/{collection}` | `documents[]`를 `(collection, owner, key)` 기준 upsert |
| `PUT /api/v1/content/{collection}` | 테마 컬렉션 전체 스냅샷 교체 |

문서 항목은 `owner`, `key`, `document`로 구성된다. GET 결과에는 `updated_at`도 포함된다. `news_article`은 선택 `collector_id`, `collection_scope`를 보내 수집기별 최초 수신과 로컬 projection 동기화를 구분할 수 있다. 허용 컬렉션은 `central_server/app.py`의 `content_collections`가 계약 원본이다. 전체 교체는 `theme_profile`, `theme_stock`, `theme_metadata`만 가능하다.

`execution_feedback_evidence`는 서버 내부 `ForwardEvaluationRepository`가 사용하는 비공개 컬렉션이며 위 공개 콘텐츠 API allowlist 대상이 아니다. owner는 `strategy_ref`, key는 전체 문서 내용 hash인 `evidence_id`다. 계좌·기간·선택/PIT/최종노출 상태·체결 품질·실제 비용이 같은 불변 문서만 멱등 저장한다.

`execution_feedback_reviews`도 같은 비공개 저장 경계다. owner는 `strategy_ref`, key는 `review_id`다. 저장 전에 같은 owner의 원본 `execution_feedback_evidence`가 존재해야 하며, 서버 repository가 원본과 명시 표본 정책으로 다시 계산한 revision과 정확히 일치해야 한다. 이 문서는 사용자 복기나 전략을 수정하는 API가 아니며 아직 개선안을 실행 대기열에 넣지 않는다.

`execution_feedback_improvement_proposals`는 적격 review에서 파생한 비공개 검토 대기 제안이다. owner는 `strategy_ref`, key는 `proposal_id`다. 등록 Family와 factor, 정규화한 기준 전략, 명시 허용값 안에서 정확히 한 정수 파라미터만 달라야 한다. 저장 전에 원본 review의 계좌·전략·evidence와 평가 방향을 다시 대조한다. seed는 반환 순서만 바꾸며 제안 ID에는 영향을 주지 않는다. 저장만으로 전략 채택·재검증·주문이 실행되지 않는다.

`execution_feedback_strategy_versions`는 owner=`parent_strategy_ref`, key=`version_id`인 비공개 불변 문서다. 저장된 proposal과 family/factor/parameters/계좌/근거가 일치해야 하며 같은 proposal을 다른 채택 사유로 두 번 버전화하지 않는다. 내부 전략 구현 버전은 그대로 두고 이 envelope ID가 새 전략 버전 참조가 된다.

`execution_feedback_revalidation_requests`는 owner=`parent_strategy_ref`, key=`request_id`인 queue 전 불변 의도다. 전략 버전·proposal·campaign·template과 파생할 exact `experiment_id`/`job_id`를 고정하며 한 전략 버전은 다른 campaign에 다시 요청할 수 없다.

`execution_feedback_revalidation_receipts`는 owner=`campaign_id`, key=`receipt_id`다. 저장된 request와 실제 queue의 exact `experiment_id`와 `job_id`가 일치했음을 기록한다. 중앙 문서와 연구 SQLite 사이에 분산 트랜잭션을 만들지 않으며 모든 ID를 내용 기반으로 고정해 request 또는 job 저장 뒤 중단해도 재시도는 같은 연구 job을 재사용한다. final holdout template과 주문 활성화는 이 경로에서 거절한다.

`execution_mock_automation_specs`는 owner=`mock account_ref`, key=`spec_id`인 비공개 O2-M 운용 명세다. 최종 후보 package/result hash, 저장된 forward profile, 현재 최신 `ka00001` mock binding과 동시 전략/포지션·자금·손실·데이터/장애·후보 교체·중지·복구 한도를 내용 hash로 고정한다. 미정 필드는 `BLOCKED`이며 현재 실행 범위는 단일 전략·단일 포지션·KRX 정규장이다. 문서 저장은 runtime claim, 주문 transport 활성화 또는 주문 전송을 수행하지 않는다.

`execution_mock_automation_admissions`는 owner=`mock account_ref`, key=`admission_id`다. 명세의 final batch/run/candidate/result가 CR3 연구 원장의 단일 완료 행과 일치하고 최신 SHADOW revision 및 mock binding이 유지될 때만 기존 O1 lease claim 전에 저장한다. spec당 admission은 하나다. `execution_mock_automation_lease_receipts`는 같은 owner 아래 결정적 자동 `execution_run_id`가 account lease를 얻은 결과를 기록하며 `new_orders_enabled=false`만 허용한다. 다른 수동/자동 run이 lease를 보유하면 receipt는 생기지 않는다.

`execution_mock_automation_recovery_decisions`는 owner=`mock account_ref`, key=`decision_id`인 append-only 복구 판정이다. admission/lease/spec 계보, broker account/orders fingerprint, 관측·account 시각, 미체결/포지션/예약자금 수, 주문가능금액, 검증된 당일 손익과 데이터 공백·unknown·재접속·잔고 불일치 값을 기록한다. 종결 주문 이력은 미체결로 세지 않는다. 보유가 있으면서 신규 진입에만 필요한 근거가 미완결이면 `MANAGE_ONLY_ORDERS_DISABLED`로 기록할 수 있고, 대사·scope·잔고가 불명확하면 계속 `BLOCKED`다. 통과 상태도 주문 활성화를 뜻하지 않는다.

`execution_mock_automation_decision_gates`는 owner=`mock account_ref`, key=`gate_id`인 매 Decision 판정이다. gate v2는 최신 recovery 뒤에도 현재 binding과 lease, 영속 control revision, 서버 시각·평가기간, KRX 정규 연속장, Decision/account/order freshness, 계좌별 FIFO와 broker 비용이 완결된 당일 순손익, 데이터 경로·공백, 자금·손실·장애 한도와 O1의 비종결 intent를 다시 검사한다. ENTER와 EXIT를 구분하며 EXIT는 같은 run·symbol의 확인 보유에서 미체결 매도 수량을 뺀 범위만 허용한다. v1 기록은 읽기 호환으로 보존하지만 control revision이 없어 새 제출 승인 근거로 사용하지 않는다. 통과 상태 `APPROVED_FOR_SINGLE_SUBMISSION`은 해당 `strategy_decision_id`에서 결정한 한 LIMIT intent에만 유효하다.

`execution_mock_automation_dispatch_receipts`는 승인 gate와 결정적 O1 intent/order state 연결을 기록한다. 같은 spec/Decision은 같은 intent ID이며 기존 O1 intent가 있으면 broker에 다시 제출하거나 새 receipt를 만들지 않는다. `execution_mock_automation_control`은 계좌별 RUNNING/STOPPED와 단조 증가 revision을 저장하고 O1 intent 저장 트랜잭션이 이를 다시 확인한다. `execution_mock_automation_stop_revisions`는 중지 감사 이력이며, 재개는 명시 요청과 중지 이후 새 broker 대사를 모두 요구한다. 기존 주문 취소나 포지션 청산은 별도 O1 대조/명시 정책으로만 처리한다. 이 컬렉션들은 공개 주문 API가 아니다.

`execution_mock_automation_risk_snapshots`는 공개 API가 아닌 계좌별 불변 근거다. 자동 account bundle의 기존
broker 복구와 O1 상세 `FILL`, `kt00015` 실제 비용을 FIFO로 대조하며 KST 거래일, broker 조회 구간,
binding/account/reconciliation/event/cost revision을 함께 저장한다. `execution_mock_automation_current_risk`는
최신 revision만 가리킨다. 비용 누락, aggregate-only 체결, 잔고 불일치는 당일 손익 unknown이며 운영
recovery와 Decision gate는 current snapshot ID/revision이 일치할 때만 진행한다.

`theme_metadata`의 `owner=default,key=full` 항목에는 선택 필드 `effective_at`, `origin_device`를 함께 보낼 수 있다. `document`는 `ThemeBackupService.export_document()`의 전체 프로필 문서여야 한다. 서버는 클라이언트가 보낸 시각을 가용시각으로 신뢰하지 않고 실제 요청 수신·DB 수락 시각을 별도로 기록한다.

`app_settings`는 PC 전용 값을 제외한 공통 설정, `app_column_settings`는 메인 표의 표시 여부와 순서를 저장한다. `journal_news_link`는 owner=`group_id`, key=`stock_code|identity`인 legacy 연결이다. `journal_v2_news_links`는 owner=불변 origin UUID, key=`journal-news-link:v2:`+정규 scope/group/stock/identity JSON의 SHA-256이다. 삭제는 문서의 `is_deleted/updated_at` revision으로 전파하며 envelope 저장 시각만으로 tombstone을 취소하지 않는다. `linked_at`은 최초 연결시각을 유지한다.

`stock_fundamentals`와 `stock_nxt_eligibility`는 키움 조회가 NAS를 통과할 때 갱신되는 종목별 최신 문서다. `/api/v1/kiwoom/query`의 `ka10001` 보관 응답은 `observed_at`이 KST 당일인 문서만 재사용하며, 이전 거래일 문서는 새 Kiwoom 조회로 갱신한다. 과거 재현에는 같은 응답을 관측 시각별로 남긴 시장 스냅샷을 사용하고, 최신 문서를 과거 시점의 값으로 간주하지 않는다.

## 테마 이력

| Method / path | query | 응답 |
| --- | --- | --- |
| `GET /api/v1/themes/history` | 선택 `as_of` Unix 초, `limit` 1~1000 | `known`, `as_of`, 최신순 `snapshots[]` |

각 snapshot은 `snapshot_id`, 활성 프로필 이름인 `profile_id`, `content_hash`, `effective_at`, 서버의 `received_at`·`available_at`, `origin_device`, 직전 이력의 `revision_of`, 전체 `document`를 가진다. `as_of`를 주면 그 서버 가용시각까지 수락된 행만 반환한다. 첫 기록보다 이른 시각은 `known=false`, 빈 배열이며 현재 테마로 대신 채우지 않는다. 늦게 도착한 다른 PC 문서는 과거 편집시각을 보존하되 서버에 실제 도착한 이후부터만 조회된다.

## 실시간 WebSocket

### `WS /api/v1/realtime`

서버 연결 직후:

```json
{"type":"ready","schema_version":1}
```

계좌 이벤트 `order_execution`(00)과 `account_balance`(04)는 서버 시작 시 같은 자격으로 확인한 `ka00001` HMAC 지문과 FID 9201이 일치할 때만 발행한다. payload에는 `origin_scope`의 익명 UUID만 포함하며 9201 원문은 포함하지 않는다. 필드 누락·불일치 이벤트는 화면 계좌로 보정하지 않고 폐기한다.

클라이언트 명령:

```json
{"type":"subscribe","codes":["005930"],"nxt_codes":["005930"]}
{"type":"ping"}
```

서버 제어 응답은 `pong`, `subscribed`, `central_ready`, `connection_opened`다. `connection_opened.scope`는 해당 앱 요청의 준비 완료인 `client`와 NAS가 키움에 승인받은 전체 합집합인 `upstream`을 구분한다. 이전 서버처럼 `scope`가 없으면 클라이언트 요청 확인으로 호환한다. 데이터 이벤트는 중앙 수집기가 전달하는 `trade`, `order_execution`, `market_state`, `program_trade`, `stock_reference` 등이다. `stock_reference`는 `code`, `upper_limit_price`, `lower_limit_price`, `base_price`, `market`을 한 기준 묶음으로 전달한다. 인증 실패 close code는 `4401`이다.

중앙 키움 등록은 한 WebSocket 안에서 `0B`, `0w`, `0g`를 독립된 타입 그룹으로 등록하며, 전송 `data` 행은 최대 100개 item씩 나눈다. 모든 요청 종목의 0B를 먼저 보장하고 남는 KRX/NXT 상세와 0w 대상은 TOP20, 실제 보유·매수 편입, 나머지 앱 요청 순으로 선택한다. `0g`는 같은 중복 제거 종목의 가격제한·기준가를 전달한다. 화면의 구독 종목 수는 거래소별 item 수가 아니라 중복 제거된 종목코드 수다.

## 변경 규칙

R3c 로컬 소스의 `/health.mock_account_available`은 실제 모의 bundle 시작 실패 시 false다.
`mock_account_error`는 계좌 불일치의 `ACCOUNT_CONTEXT_MISMATCH` 또는 그 밖의
`MOCK_ACCOUNT_STARTUP_FAILED`만 공개한다. 공급자 원문 오류·키·토큰을 공개 health에 전달하지 않는다.
기존 `/api/v1/mock/orders`의 기본 계좌 계약은 유지하고, 한 응답의 주문과 events는 동일 gateway로 읽는다.

1. 기존 필드와 경로는 삭제·의미 변경하지 않는다.
2. 선택 필드 추가를 우선한다.
3. 호환 불가능 변경은 새 `/api/v2`와 새 `schema_version`으로 병행 제공한다.
4. 변경 시 `test_public_api_route_contract_is_stable`과 대응 클라이언트 테스트를 함께 수정한다.

## D4 shadow 후보 조회

`GET /api/v1/research/candidates?after_sequence=0&limit=100`은 인증된 읽기 전용 API다. 응답은 `high_watermark`, 불변 `events[]`, `next_cursor`, `has_more`, 생성기의 `quality`를 반환한다. event에는 서버 `accepted_sequence`, `event_id`, 종목, 감지·만료 시각, 판단 기준가와 입력 근거가 있다. `status=ACTIVE/EXPIRED`는 조회 시각의 표시값이며 원장을 수정하지 않는다. 생성 기능이 꺼져 있어도 HTTP 200과 `quality.status=DISABLED`를 반환한다. 앱의 Shadow 창에서 감지 여부와 조건을 저장하면 같은 서버 실행 중 즉시 이 상태가 바뀐다. 이 경로는 주문이나 체결을 만들지 않는다.

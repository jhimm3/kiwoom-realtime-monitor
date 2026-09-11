# NAS API 계약

현재 계약 버전은 `api_version=v1`, `schema_version=1`이다. 모든 `/api/v1/*` HTTP 요청은 `Authorization: Bearer <NAS 접속 토큰>`이 필요하다. `/health`만 공개다. WebSocket은 같은 헤더 또는 `?token=`을 사용한다.

## 공통 오류

- `401`: 접속 토큰 없음/불일치
- `400`: 허용하지 않은 Kiwoom API·경로·연속조회 값 또는 잘못된 입력
- `404`: 지원하지 않는 스냅샷 종류/콘텐츠 컬렉션
- `405`: 전체 교체가 허용되지 않은 콘텐츠 컬렉션
- `429`: 중앙 AI 공급자가 호출 한도를 반환한 경우. 클라이언트는 즉시 연속 재요청하지 않는다.
- `502`/`503`/`504`: 상류 Kiwoom/뉴스/AI 처리 실패 또는 일시적 혼잡. JSON `detail`은 비밀값 없이 사용자에게 표시 가능한 원인을 담는다.
- `503`: 서버에 필요한 공급자 자격 증명이 없음

## 상태와 설정

| Method / path | 요청 | 응답 핵심 | 역할 |
| --- | --- | --- | --- |
| `GET /health` | 없음 | `status`, `api_version`, `schema_version`, `server_time`, `server_build` | 공개 생존 확인 |
| `GET /api/v1/capabilities` | 없음 | 버전 + `capabilities` boolean map | 서버가 제공하는 기능 협상 |
| `GET /api/v1/settings/operations` | 없음 | `ai_provider`, `ai_model`, `ai_daily_limit`, `news_refresh_seconds`, `dart_enabled` | NAS 운영값 읽기 |
| `PUT /api/v1/settings/operations` | 위 5개 필드 전부 | 저장된 동일 구조 | 비밀키가 아닌 운영값 변경 |
| `GET /api/v1/diagnostics/resources` | 없음 | 프로세스/호스트 메모리와 디스크, DB 크기 | NAS 자원 진단 |

운영 설정 `ai_provider`는 `none/openai/gemini/claude`, 뉴스 간격은 60~86,400초다. 이 API는 공급자 비밀키를 반환하거나 변경하지 않는다.

NAS 연결 설정과 뉴스 설정은 이 운영 설정 API를 함께 사용한다. 뉴스 설정에서 저장할 때는 먼저 GET으로 `news_refresh_seconds` 등 그 화면이 편집하지 않는 값을 보존한 뒤, AI 공급자·모델·일일 한도와 DART 사용 여부만 합쳐 PUT한다.

## Kiwoom 조회

### `POST /api/v1/kiwoom/query`

요청:

```json
{"api_id":"ka10001","path":"/api/dostk/stkinfo","body":{"stk_cd":"005930"},"cont_yn":"N","next_key":""}
```

응답:

```json
{"payload":{},"has_next":false,"next_key":"","cache_hit":false,"archive_hit":false}
```

허용 API와 경로는 `central_server/rest_broker.py`의 `READ_ONLY_ENDPOINTS`가 계약 원본이다. 현재 `ka00198`, `ka10016`, `ka10001`, `ka10100`, `ka10080`, `ka10081`, `ka10083`, `ka10094`, `ka10045`, `ka90008`, `ka20005`, `ka20006`, `kt00007`, `kt00015`만 허용한다. 주문 API는 허용하지 않는다. `cont_yn=Y`에는 `next_key`가 필수다.

클라이언트 의존: `RemoteKiwoomRestClient`는 `payload/has_next/next_key`를 직접 읽으며, 중앙 접속 실패와 정상 HTTP API 오류를 구분한다. `archive_hit`는 완료 coverage가 있는 중앙 분봉·일봉을 재사용했음을 나타내는 선택 필드다. 필드 제거·이름 변경은 스키마 버전 상승 없이 금지한다.

## 뉴스와 AI

| Method / path | 요청 핵심 | 응답 핵심 |
| --- | --- | --- |
| `POST /api/v1/news/search` | `stock_code`, `stock_name`, 선택 `since`, 자동분석 옵션 | `stock_code`, `items[]` |
| `POST /api/v1/news/analyze` | 종목, provider/model, `events[1..20]`, `article_count` | 중앙 AI 서비스 분석 문서. 공급자 429/5xx는 상태 코드를 보존한다. |

사건은 `identity`, `title`, `body`, `body_hash`, `articles[]`를 가진다. 본문 최대 길이는 2,000,000자다. AI 결과의 요약·판정·근거는 요청한 `stock_name`의 주가·실적·사업 영향 관점이어야 하며, 종목이 단순 나열됐거나 직접 근거가 없으면 `판단 자료 부족`으로 반환한다. `body_hash`는 본문뿐 아니라 대상 종목과 분석 계약 버전(`target-company-v2`)을 포함하므로 이전의 시장 전체 관점 결과는 새 자동분석의 완료 캐시로 재사용하지 않는다. 캐시 재사용 여부와 사용량 구조는 중앙 AI 서비스와 클라이언트 테스트가 보호한다.

## 시장 데이터

| Method / path | query | 응답 |
| --- | --- | --- |
| `GET /api/v1/market/minute-bars` | `code`, `trading_date=YYYY-MM-DD`, 선택 `market` | 같은 식별자 + `bars[]` |
| `GET /api/v1/market/daily-bars` | `code`, 선택 `market`, `limit` 1~5000 | 식별자 + `bars[]` |
| `GET /api/v1/market/coverage` | `kind`, `subject`, ISO `start/end`, 선택 `available_by`, 고정주기 자료만 `expected_seconds` | 상태·관측 수·가용 수·결측 구간·부재 의미 |
| `GET /api/v1/market/external-bars` | `instrument`, `timeframe=5m/1d`, `limit` 1~10000 | 공급원·계약코드가 포함된 `bars[]` |
| `GET /api/v1/market/snapshots/{kind}` | 선택 `subject`, `limit` 1~5000 | `kind`, `subject`, `snapshots[]` |

스냅샷 `kind`는 `ranking`, `top20_membership`, `top20_index`, `market_state`, `investor_flow`, `program_flow`, `new_high`, `stock_fundamentals`, `nxt_eligibility`를 허용한다. `stock_fundamentals`와 `nxt_eligibility`는 시점 이력과 별도로 범용 콘텐츠의 `stock_fundamentals`, `stock_nxt_eligibility` 컬렉션에서 종목별 최신값도 조회할 수 있다. 봉의 중앙 저장 단위는 거래대금 백만원(`trade_value_million_won`)이다.

coverage 상태는 `complete/partial/missing`이다. `available_by`를 지정하면 그 시각까지 실제로 가용했던 관측과 그 전에 저장된 완료 근거만 센다. 분봉·일봉은 거래가 없으면 행 자체가 없을 수 있으므로 `expected_seconds`를 허용하지 않고, 별도 장후 완료 문서가 있을 때만 0개 행을 완전 수집으로 판정한다. 순위 후보군·TOP20 지수·시장 상태처럼 고정 주기를 가질 수 있는 자료에만 `expected_seconds`를 주어 `missing_intervals`를 계산한다. `absence_meaning`은 실제 무체결 가능성과 미수집을 구분하지 못하는 경우 이를 명시한다.

외부 시장 봉은 현재 임시 Yahoo 지연 시세 수집 결과다. `NASDAQ_FUTURES`와 `WTI_FUTURES`는 실제 월물 계약을 함께 저장하며 각 행의 `provider`, `contract`, `bar_time`을 제거하지 않는다. 서버는 전월물과 차월물을 동시에 수집하고 차월물 거래량 우위를 연속 확인하면 대표 월물을 앞으로만 교체한다. 월물 간 절대가격 차이는 등락률로 계산하지 않으며, 방향값은 선택된 월물 자체의 전일 종가 기준이다. 이 자료는 실시간 주문 판단용 시세가 아니다.

## 범용 콘텐츠

| Method / path | 의미 |
| --- | --- |
| `GET /api/v1/content/{collection}` | `owner`, `limit`, `offset`, `updated_after`로 증분 조회 |
| `POST /api/v1/content/{collection}` | `documents[]`를 `(collection, owner, key)` 기준 upsert |
| `PUT /api/v1/content/{collection}` | 테마 컬렉션 전체 스냅샷 교체 |

문서 항목은 `owner`, `key`, `document`로 구성된다. GET 결과에는 `updated_at`도 포함된다. 허용 컬렉션은 `central_server/app.py`의 `content_collections`가 계약 원본이다. 전체 교체는 `theme_profile`, `theme_stock`, `theme_metadata`만 가능하다.

`app_settings`는 PC 전용 값을 제외한 공통 설정, `app_column_settings`는 메인 표의 표시 여부와 순서를 저장한다. 열 너비는 모니터별 값이므로 후자에 포함하지 않는다. `journal_news_link`는 매매일지 묶음과 뉴스 기사의 연결을 보존한다. `theme_metadata`는 전체 프로필 문서의 활성 프로필·별칭·등록 종목 목록까지 보존하며, GET의 `updated_at`은 세 테마 컬렉션 교체가 끝났음을 나타내는 완료 시각으로도 사용한다. 클라이언트는 이 완료 시각과 로컬 대기 변경 시각을 비교해 최신 스냅샷을 선택한다.

`stock_fundamentals`와 `stock_nxt_eligibility`는 키움 조회가 NAS를 통과할 때 갱신되는 종목별 최신 문서다. 과거 재현에는 같은 응답을 관측 시각별로 남긴 시장 스냅샷을 사용하고, 최신 문서를 과거 시점의 값으로 간주하지 않는다.

## 실시간 WebSocket

### `WS /api/v1/realtime`

서버 연결 직후:

```json
{"type":"ready","schema_version":1}
```

클라이언트 명령:

```json
{"type":"subscribe","codes":["005930"],"nxt_codes":["005930"]}
{"type":"ping"}
```

서버 제어 응답은 `pong`, `subscribed`, `central_ready`, `connection_opened`다. 데이터 이벤트는 중앙 수집기가 전달하는 `trade`, `order_execution`, `market_state`, `program_trade` 등이며 기존 Qt worker가 이 이름에 의존한다. 인증 실패 close code는 `4401`이다.

## 변경 규칙

1. 기존 필드와 경로는 삭제·의미 변경하지 않는다.
2. 선택 필드 추가를 우선한다.
3. 호환 불가능 변경은 새 `/api/v2`와 새 `schema_version`으로 병행 제공한다.
4. 변경 시 `test_public_api_route_contract_is_stable`과 대응 클라이언트 테스트를 함께 수정한다.

# TOP20 장중 집계와 장후 분봉 재계산

상태: **2026-10-12 로컬 구현·검증 / NAS 미배포·전용 PostgreSQL gate 대기**. 사용자 결정은 장중 0B 집계, 장후 종목별 마감 1분봉 재계산, 30초 편입·이탈의 50% 배분이다. 현재 실행 코드와 검증 범위는 [CURRENT_STATUS](CURRENT_STATUS.md), 후속은 [OPEN_ITEMS](OPEN_ITEMS.md)를 따른다.

## 1. 사용자에게 보이는 동작

- 장중에는 기존 **장중 실시간 집계**를 표시한다.
- 전체일 장후 보완 뒤에는 **장후 분봉 재계산**을 기본 표시한다. 현재 전체일 보완 시작은 20:05이며, 시각 도달 자체가 자료 완료를 뜻하지 않는다.
- 그 분의 두 30초 구간에 모두 속한 종목은 100%, 한 구간에만 속한 종목은 50%를 반영한다. 두 구간에 전혀 없으면 0%다.
- 두 값 모두 정확한 30초 확정 거래대금이라고 부르지 않는다. 장중에는 수신·구성 교체 지연이 있고, 장후에는 분봉 거래대금 계산과 시간 배분의 근사가 있다. 32~33초 수신 때문에 장후 배분을 임의로 27/60 또는 33/60으로 바꾸지 않는다.
- 장후 자료가 부족하면 계산 가능한 분을 표시하고 누락 범위를 명시한다. 정규장 전체가 갖춰지지 않은 합계를 완성된 일별 거래대금이나 시장 대비 비율로 표시하지 않는다.
- 이 변경은 NAS TOP20 지수 차트와 관련 통계에 적용한다. 종목 표의 1분·1분강도, 전략·주문·연구 입력과 PC 직접 수집의 저장 계약은 별도다.

## 2. 현재 코드에서 확인한 경로

| 책임 | 현재 코드와 사실 |
|---|---|
| 구성·장중 금액 | `application/top20_trade_value_collector.py`: 다음 00/30초 경계에 구성을 적용하고 `cohort_segments`를 분별 보존한다. 실제 `advance()` 시각의 누적값을 baseline으로 삼으므로 처리 지연은 30초 귀속 오차가 될 수 있다. |
| 장중 저장 | `central_server/autonomous_top20.py::_write_index` → `save_dataset_snapshot(kind=top20_index, subject=day, key=minute)`; 원본 품질은 `realtime_complete/partial`이다. |
| 장후 종목 범위 | `backfill_day`는 당일 한 번이라도 편입한 종목과 별도 계좌·코호트 수집 대상을 보완한다. TOP20 계산 대상은 해당 분의 구성 이력으로만 정한다. |
| 장후 분봉 | `_backfill_minutes` → 기존 broker의 `ka10080` → `market_ingest.py::_ingest_minutes` → 분봉·metadata 저장 → `market_data_coverage` 완료 문서. |
| 거래대금 의미 | 현재 조회 봉은 `round(volume × (O+H+L+C)/4 / 1,000,000)` 백만원이다. metadata도 `estimated`, `kiwoom-ka10080;trade_value=ohlcv_estimate`다. 마감 OHLCV라는 사실이 거래대금 실측을 뜻하지 않는다. |
| 변경 전 조회 | `database_top20_statistics.py`와 `MainWindow._nas_top20_rows`는 `realtime_complete`만 채택한다. 차트는 남은 시각의 간격을 `수집 중단`이라고 표시한다. |
| 변경 전 완료 근거 한계 | 기존 `market_data_coverage`에는 반환된 전체 분 목록과 값 지문이 없다. `session_finalized=true`와 일부 행 존재만으로 빠진 모든 분을 0으로 인증할 수 없다. |

앞서 재현·수정한 지연 순위 응답의 collector 시계 되감기 문제와 이번 장후 재계산은 다른 변경이다. 그 수정의 107건 통과를 이번 기능의 검증으로 재사용하지 않는다.

## 3. 선택한 저장 구조와 대안

**기존 실시간 원장 + 날짜당 하나의 장후 파생 문서**를 사용한다. 기존 generic dataset 테이블을 재사용하며 새 SQL 테이블·전역 연결·별도 worker/service는 만들지 않는다.

| 대안 | 판단 |
|---|---|
| `top20_index`의 분 행을 장후 값으로 덮기 | 실시간 관측·품질·연구 revision 의미가 바뀌므로 사용하지 않는다. |
| API 조회 때마다 종목별 분봉을 모두 읽어 재계산 | 연간 통계 조회 비용과 조회 시점별 불일치가 커져 사용하지 않는다. |
| 별도 날짜별 장후 문서를 원자적으로 교체 | 선택. 계산·입력 근거·범위를 묶어 게시하고 기존 원본과 읽기 계약을 보존한다. |

새 문서는 재생 원본이 아니라 다시 만들 수 있는 표시용 계산본이다. 최신 날짜 문서의 교체는 허용하며, 이전 실시간 관측이나 완료된 연구 결과를 고치지 않는다. 이전 계산 세대 전부의 영구 보관은 이번 범위에 추가하지 않는다.

저장 identity:

- `kind=top20_index_reconciled`, `subject=YYYY-MM-DD`, `snapshot_key=YYYY-MM-DD`.
- 기존 `save_dataset_snapshot`의 독립 native transaction으로 하루 문서 전체를 저장한다. 저장 성공 전에는 새 계산본을 공개하지 않는다.
- `observation=None`으로 저장한다. 기존 `top20_index_observation`을 호출하지 않으며, `RESEARCH_OBSERVATION_KINDS` 및 실시간 TOP20 recorder/outbox payload에 추가하지 않는다. 파생 출처는 문서 안에 보존한다.
- 같은 입력·알고리즘 지문이면 재저장하지 않는다. 입력이나 계산 버전이 달라지면 새 `generation`으로 전체 문서를 교체한다.

## 4. 계산 규칙

KST 분 `m`에 대해 첫 구간 `[m, m+30초)`의 구성 `A`, 뒤 구간 `[m+30초, m+60초)`의 구성 `B`를 구한다.

`weight(code,m) = (1[code in A] + 1[code in B]) / 2`

`value_eok(m) = Σcode weight(code,m) × (KRX_million(code,m) + NXT_million(code,m)) / 100`

NXT가 비대상임을 확인한 종목의 NXT 항은 0이다. SOR 봉은 이 계산에 합치지 않는다. 현재 KRX/NXT 조회 봉의 저장 거래대금을 그대로 사용하고 UI에서 OHLCV 추정을 다시 계산하지 않는다. 중간 단계의 추가 반올림 없이 최종 표시만 반올림한다.

예: A가 앞뒤 모두 속하고 해당 분 100억원, B가 앞 구간에만 속하고 80억원, C가 뒤 구간에만 속하고 40억원이면 `100 + 40 + 20 = 160억원`이다.

구성 해석:

1. 해당 분의 `top20_index.payload.cohort_segments`가 우선이자 이번 버전의 구성 근거다. 원래 금액이 `partial`이어도 구성 자체가 유효하면 사용할 수 있다.
2. 기록된 배열 순서를 유지한다. 분 시작에 기존 구성과 새 구성의 두 항목이 같은 00초로 기록될 수 있으므로 **같은 경계에서는 마지막 항목**을 적용한다. 한 구간 내부의 중복 종목은 한 번만 계산한다.
3. 첫 00초 구성이 있고 30초 변경이 없으면 같은 구성을 뒤 구간에도 적용한다. 30초 기록만 있으면 앞 구간은 미확인이다.
4. 분 밖의 activation, 역순 시각, 00/30 이외 경계, 파싱 실패는 그 분의 구성 미확인 사유다. 이전 분의 구성을 누락 분 전체에 자동 연장하지 않는다.
5. 원본 ranking/membership의 회차 시각을 실제 collector 적용시각으로 소급 치환하지 않는다. 두 값은 현재 `prepare()`의 다음 경계 정책과 응답 지연 때문에 같지 않을 수 있다. 구성 기록 없는 과거 분의 복원은 이번 버전에서 미확인으로 남긴다.
6. 장후에는 기존에 저장된 명목상 00/30초 구성에 고정 50% 규칙을 적용한다. 장중에 실제 baseline이 교체된 초까지 복원했다고 주장하지 않는다.
7. 15:30 봉을 정규장 합계에 포함하는 기존 규칙을 유지한다. 단일가 거래도 이 버전에서는 같은 50% 근사이며, 정확한 체결 시각에 따른 분배라고 표시하지 않는다.

전체일 차트 범위는 기존 `[08:00,20:00)`의 720분, 정규장 통계는 기존 09:00~15:30 포함 391분이다. 특별 개장일의 통계 범위 변경은 기존 기능의 별도 계약이며 이번 변경으로 임의 보정하지 않는다.

KOSPI/KOSDAQ 분해는 대상일에 유효한 저장 분류 근거를 사용한다. 확인할 수 없으면 기존 `unknown` 금액 묶음에 넣고 전체 금액은 보존한다. 오늘 카탈로그로 과거 시장 분류를 확정하지 않는다.

## 5. 분봉 완료 근거와 0 처리

존재하는 봉은 대상일·종목·venue·분이 일치하고 `QUERY/BACKFILLED + COMPLETE`, 현재 지원하는 조회 출처와 닫힌 세션 근거가 있는 것만 채택한다. 실시간 부분 봉이나 metadata 없는 숫자를 장후 확정 입력으로 올리지 않는다.

이번 기능과 함께 `market_data_coverage`의 동일 owner/key에 **추가 필드만** 넣은 `minute_backfill_coverage/v2` 근거를 만든다. 기존 소비자의 필드 의미는 유지한다.

- 정확한 요청 날짜·종목·venue·주기·수정주가 설정, 전체일 범위, 페이지 수·연속조회 종료 사유·완료시각.
- 대상일에 실제 반환된 정렬·중복 제거 minute 목록, canonical OHLCV/거래대금의 내용 지문, 저장된 값·출처 metadata 대조 결과.
- 모든 페이지의 broker 적재 성공, 유효한 페이지 진행과 정상 종료가 확인되어야 한다. 반복 continuation·페이지 상한·서로 다른 중복 봉·잘못된 응답·저장 불일치는 실패다.
- `market_ingest.py`의 기존 봉 정규화 계산을 순수 함수로 추출해 적재와 완료 대조가 함께 사용한다. 계산식을 복제하거나 금액 단위를 바꾸지 않는다.
- 대상일 전체 빈 응답은 기존처럼 완료로 승격하지 않는다. 기존 완료 문서만 있다고 v2 근거를 만들어 넣지 않는다.

**봉이 없는 분을 0으로 사용할 수 있는 경우**는 해당 code/venue의 전체일 v2 근거와 저장된 반환 봉·metadata가 현재 입력 snapshot에서 일치하고, 그 반환 목록에 해당 분이 없는 경우다. 이는 공급자가 완결한 조회 범위에서 봉을 제공하지 않았다는 의미다. 단순 DB 행 부재, 구형 완료 표식, 일부 페이지만 받은 응답은 0의 근거가 아니다. 존재하는 명시적 거래대금 0 봉은 유효 입력이면 그대로 사용한다.

구형 coverage가 있는 날짜는 유효한 존재 봉을 사용할 수 있지만, v2 근거 없는 빈 분은 미확인으로 남긴다. 기존 일반 보완의 skip 정책을 전부 바꾸어 과거 날짜를 무조건 재조회하지 않는다. 별도의 명시 날짜 복구 작업에서만 기존 broker로 필요한 근거를 보강한다.

필요 venue는 KRX와 대상일 NXT 적격 근거로 정한다. 대상일의 NXT 봉/완료 조회는 NXT 입력 필요 근거가 될 수 있다. NXT를 제외하려면 대상일에 유효한 `nxt_eligibility` 저장 근거가 있어야 한다. 오늘 최신 eligibility를 과거일에 소급하지 않는다. 필요한 NXT 입력이나 대상 여부가 미확인이면 통합 금액을 완전으로 올리지 않는다.

## 6. 날짜 문서

필수 필드:

| 필드 | 내용 |
|---|---|
| `schema_version` | `top20_reconciliation/v1` |
| `trading_date`, `generation` | KST 날짜, 계산 버전과 입력 내용으로 만든 지문 |
| `algorithm` | `minute_bar_half_cohort/v1` |
| `basis`, `value_kind`, `origin`, `unit` | `after_close_estimate`, `derived_from_estimate`, `derived`, `eok_won` |
| `input_as_of`, `available_at` | 입력 read snapshot 기준시각과 이번 계산본 게시 시각. 과거 분 시각으로 소급하지 않는다. |
| `input_fingerprint`, `sources` | 코호트 key/hash, code/venue별 봉·metadata·coverage 지문, eligibility·시장분류 근거와 계산 버전 |
| `rows` | 시간순 전체 720분. 분별 `minute`, `status`, `market_values`, `total`, 누락 사유·필요 입력 식별자 |
| `coverage` | 전체일/정규장 각각 expected·complete·missing 분 수, 누락 범위, 시장분류 미확인 종목 수 |
| `status` | 전체일 기준 `complete` 또는 `partial`. 입력 자체가 잘못되면 게시 실패이며 complete로 저장하지 않는다. |

완전한 분의 `status=complete`, 금액은 0 이상 유한수다. 한 종목/venue/구간이라도 필요한 근거가 없으면 그 분은 `status=unconfirmed`, 금액은 `null`이다. 알려진 종목만 더한 금액을 정상 분값으로 노출하지 않는다. `capture_state=realtime_complete`를 새 계산본에 붙이지 않는다.

내용 지문은 값과 품질에 영향을 주는 필드로 계산한다. 동일 값 재조회 시각만 변한 경우 새 금액 세대를 만들지 않으며 실제 최초 게시 시각을 보존한다. 저장 크기·행 수는 기존 dataset 제한 아래에서 검사하고 초과 시 잘라서 정상 게시하지 않는다.

## 7. 입력 읽기·작업 수명·게시

`backfill_day`의 기존 owned task 안에서 종목 보완 시도가 끝난 뒤 `reconcile_top20_day(day)`를 호출한다. 수급·시장지수 보완 실패만으로 TOP20 계산을 생략하지 않는다. 필요한 종목 분봉의 성공 여부는 입력 근거가 판정한다.

흐름은 `AutonomousTop20Service → store.load_top20_reconciliation_inputs(day) → 순수 계산 → 기존 save_dataset_snapshot`이다. 일정 loop나 실시간 collector lock 안에서 계산·DB 읽기를 기다리지 않는다. 새 스케줄러·전역 작업자·queue를 추가하지 않는다.

입력 읽기는 신규 store 메서드가 한 번의 **일관된 read snapshot**에서 수행한다. PostgreSQL은 이 신규 read 호출만 `REPEATABLE READ READ ONLY`, SQLite는 명시 read transaction을 사용한다. 기존 writer의 connection/commit/rollback/격리수준은 바꾸지 않는다. cohort→필요 종목/venue→봉·metadata·coverage·역사 분류/eligibility를 같은 snapshot에서 읽고 반환 뒤 연결을 닫는다. 계산은 연결 밖에서 수행한다.

게시 중 취소·저장 실패는 기존 owned thread drain 규칙을 따른다. 실패한 새 계산이 기존 게시본을 지우지 않는다. 이전 게시본은 자신의 `available_at` 기준 계산본으로 표시하며 새 수정까지 반영됐다고 주장하지 않는다. 입력이 바뀐 다음 성공 보완·명시 재계산에서 새 세대를 게시한다.

일부 자료가 부족한 경우에도 유효한 `partial` 날짜 문서를 한 번 게시한다. 같은 지문은 재게시하지 않는다. 원본 보완 실패의 기존 재시도를 유지하지만, 더 이상 얻을 수 없는 구성 이력의 미확인만으로 밤새 무한 재계산하지 않는다. 전체 저장/계산 실패는 기존 제한 재시도·다음 실행에서 복구하며 다음날 07:40 중단 경계를 지킨다.

`top20_after_close`와 `minute_backfill` 진단 일시정지를 존중한다. 계산본 writer와 표시 통계 cache는 writer registry·저장 감사·측정 목록에 등록한다. 진단 중단/재개는 기존 게시본을 유지하고 미완료 작업을 다음 허용 실행에서 재시도한다.

## 8. API·화면·통계 선택

기존 `/api/v1/market/snapshots/top20_index`는 실시간 저장 원본을 계속 반환한다. 새 읽기 API:

`GET /api/v1/market/top20-series?trading_date=YYYY-MM-DD&basis=display|realtime`

- `display`는 해당 날짜의 유효한 장후 계산본이 있으면 그 날짜 전체에 이를 선택한다. 없으면 실시간 원본을 선택한다.
- `realtime`은 비교·진단을 위한 원본이다. 한 날짜 안에서 장후 값이 없는 분만 장중 값으로 채워 서로 다른 계산법을 섞지 않는다.
- 응답은 날짜·basis·generation·게시시각·coverage·분별 상태/금액을 함께 전달한다. 입력 날짜·basis·인증은 기존 라우터 방식으로 검사한다.
- `load_stored_top20_series`를 새 client 메서드로 추가한다. 기존 `load_stored_top20_index`의 tuple 계약은 유지한다. failover/validation facade와 NAS worker에 새 조회를 연결한다.
- 새 API가 없는 구형 서버의 404/명시 미지원에서만 기존 저장 API로 호환 조회한다. 인증·통신·서버 오류를 구형 서버로 가장하지 않는다.

`/api/v1/market/top20-statistics`에는 `basis=realtime|display` 선택을 추가하고 기본은 기존 `realtime`으로 유지한다. 새 앱은 `display`를 명시한다. 기존 경로/기본 필드의 의미를 바꾸지 않는다.

표시 통계 cache는 `top20_statistics_display_day`로 분리한다. SQLite/PostgreSQL 모두 기존 날짜별 native cache transaction과 PostgreSQL advisory lock을 재사용한다. raw TOP20/시장지수 변경은 기존·표시 cache를, 장후 계산본 변경은 표시 cache만 같은 writer transaction에서 무효화한다. cache identity에 선택 basis/generation/계산 버전을 보존한다.

장후 날짜에서 정규장 391분이 모두 complete이면 일별 합계·근사 비율을 표시한다. 전체일이 partial이어도 정규장만 complete이면 정규장 합계는 완전하다. 정규장 일부가 빠지면 `top20_eok=null`, `known_top20_eok=알려진 분 합계`, coverage를 반환하고 비율은 계산하지 않는다. 시장 분모가 없을 때도 값 0으로 단정하지 않는다. 분모는 기존 `ka20006` KOSPI+KOSDAQ 정의를 유지한다.

장후 계산본이 없는 실시간 날짜도 표시용 통계에서는 알려진 분 합계와 수집 범위를 함께 반환한다. 장중은 진행 중, 과거 불완전일은 부분 합계로 표시한다. 기존 raw API 결과를 읽는 구형 앱까지 완전성 표시가 개선됐다고 보고하지 않는다.

UI 변경:

- 장후 계산본을 당일 `self._top20_collector.completed`에 합치지 않는다. 화면용 결과로만 전달하여 장중 계산 상태를 오염시키지 않는다.
- 차트 상단에 `장중 실시간 집계` 또는 `장후 분봉 재계산 · 게시시각 · 확인 N/720분`을 표시한다.
- 새 응답의 gap은 근거에 따라 `구성 이력 없음`, `분봉 확인 대기`, `실시간 집계 미확인` 등으로 표시한다. 단순 숫자 행의 부재만으로 `수집 중단`이라고 단정하지 않는다.
- 1/5/60분 보기는 같은 선택본을 쓴다. 큰 봉에 미확인 분이 하나라도 있으면 그 봉도 부분 상태이며, 남은 분만 더한 값을 정상 완성봉으로 그리지 않는다.
- 날짜의 시간 축은 08:00~20:00을 유지하고 값이 없는 앞 구간도 미확인 범위로 표현한다. 초기 화면을 11시 첫 정상값에서 시작하게 만들지 않는다. 60봉 단위 스크롤과 빈 구간 표현을 함께 확인한다.
- 일별 그래프는 부분 합계를 정상 일봉으로 그리지 않고 미확인 상태로 구분한다. 시간대 평균은 complete 분만 쓰되 표본 수·범위와 추정 포함을 표시한다.
- 창을 열거나 날짜/주기를 바꿀 때 조회하고, 당일 창이 열린 상태에서는 기존 NAS worker 중복 방지·요청 결과 신선도 규칙으로 30초마다 읽기 전용 갱신한다. 장후 게시가 완료되면 자동 전환한다.

## 9. 구현 파일과 순서

새 계층은 추가하지 않는다. 순수 계산 정책과 backend SQL에 필요한 두 파일만 새로 허용한다.

1. `domain/top20_reconciliation.py`(신규): 입력 DTO, 경계 정규화, 50% 계산, 품질·범위 요약, 계산본 선택 규칙. I/O·스레드·시간 조회 없음.
2. `central_server/market_ingest.py`, `autonomous_top20.py`: 공통 봉 정규화, v2 완료 근거, 기존 장후 task의 계산 호출·retry/drain. 기존 실시간 outbox shape는 유지.
3. `central_server/database_top20_reconciliation.py`(신규), `database.py` 및 source/consumer 감사 등록: 일관된 입력 read와 series 조회. 상위 service/UI를 DB에서 import하지 않는다.
4. `database_datasets.py`, `database_top20_statistics.py`: 계산본 게시와 표시 cache 무효화, display 통계. generic save 경계를 우회하는 별도 SQL writer를 만들지 않는다.
5. `market_dataset_read_routes.py`, remote/failover/validation client, `presentation/top20_trade_value.py`, `main_window.py`: 새 opt-in 읽기·품질 보존·자동 전환·빈 구간 표시.
6. 진단 writer/workload registry·저장 경로 감사·API/HISTORICAL 계약·MODULE_MAP·회귀 profile를 구현과 함께 갱신한다. 실시간 녹화 입력 계약과 연구 원본 범위는 보존한다.

현재 이해 경로는 collector→service→dataset store→API/client→MainWindow/chart다. 추가 후 장중 경로는 같고, 장후 producer에 순수 계산 호출 한 번과 read API의 basis 선택이 추가된다. Manager/Service 중계 객체를 만들지 않는다.

## 10. 필수 검증과 과거 날짜 적용

| 검증 | 통과 조건 |
|---|---|
| 배분 | 고정 구성 100%, 편입/이탈 50%, 두 경계 같은 시각의 마지막 구성, 중복 코드, 분/날짜 경계에서 이중 합산 없음 |
| 지연 | 32/33초 처리 사례에서도 장후 50% 규칙 유지. 장중 결과와 차이가 날 수 있음을 출처에 반영 |
| 구성 누락 | 첫 30초만 존재·분 밖 activation·누락 분·잘못된 시각을 0/완전으로 승격하지 않음 |
| 봉·단위 | 백만원→억원 1/100, 명시 0, NaN/음수 거절, KRX/NXT 합산·SOR 중복 차단 |
| coverage | 구형/v2, 페이지 상한/반복/중복 충돌, 적재 실패, 저장 값/metadata 수정, 전체 빈 응답, 증명된 빈 분을 각각 구분 |
| 과거 기준정보 | NXT 필요성 미확인·대상일 false·NXT 실제 입력·시장 분류 미확인을 구분하고 오늘 자료로 소급 확정하지 않음 |
| 저장 수명 | SQLite와 전용 PostgreSQL에서 일관 read, 계산 중 입력 변경, 게시 실패·취소·재시작·동일 세대 재시도, 이전 게시본과 cache 보존/무효화 |
| 소비자 | 기존 raw API/연구/recorder unchanged, 새 API 인증·404 호환·잘못된 basis, 당일 collector 상태 미오염, UI 늦은 날짜 응답 폐기·worker drain |
| 통계·차트 | 08시 이전 공백 표시, 1/5/60분 품질, 15:30 포함, 부분 합계/분모 없음의 비율 차단, complete 0분도 시간대 표본 수에 포함 |
| 장후 일정 | 20:05 별도 task, 순위 우선권, 수급 실패와 계산 독립, 진단 pause, 다음날 07:40, 같은 부족 근거로 무한 게시하지 않음 |

새 테스트는 회귀 profile에 등록한다. 필요한 HTTP 검증은 현재 지원되는 ASGI 경로를 사용하고 `httpx2`를 추가하지 않는다. 소스 수정 후 기존 107건을 포함한 관련 회귀를 다시 실행한다. 커밋·push·배포는 별도 현행 gate를 통과해야 한다.

10/1·2·6·7·8 등의 과거 자료는 구현 후 **읽기 전용 dry-run**으로 구성/봉/coverage/NXT 근거별 복원 가능 분 수를 먼저 출력한다. 기존 partial 금액을 단순 합산하거나 모두 complete로 바꾸지 않는다. 필요한 근거 보강과 날짜별 계산본 게시 뒤 원본 보존, 오전 범위, 정규장 합계, missing count를 대조한다. 설계만으로 해당 날짜가 모두 복원됐다고 보고하지 않는다.

## 11. 다음 단계

로컬 구현은 기존 장후 task·generic writer·backend read/cache와 NAS worker/chart에 연결했다. 별도 Manager/Service, 새 SQL 테이블, 실시간 원장 덮어쓰기는 없다. 전용 PostgreSQL 검사와 실제 NAS 적용 뒤 장중/장후 acceptance가 남는다. 구현 중 위 계약을 바꿔야 하는 새 근거가 나오면 해당 선택만 다시 설계 검토한다.

## 12. 구현·검증 기록 — 2026-10-12

- 조회 OHLCV metadata의 `unit=unknown`은 복합 봉 전체의 단위다. 현재 `ka10080` 조회 출처와 명시적인 `trade_value_million_won` 필드로 기존 백만원 계약을 판정한다. 새 단위로 저장 원본을 고치지 않는다.
- SQLite 입력 읽기는 한 native read transaction, PostgreSQL은 독립 `REPEATABLE READ READ ONLY` transaction이다. PG 날짜/time 컬럼을 기존 reader처럼 text/HH:MI로 정규화한다. 표시 cache는 기존 날짜 advisory lock과 원본·계산본 writer의 동일 transaction 무효화를 사용한다.
- 게시 task가 취소돼도 기존 장후 owner가 실제 DB thread의 완료까지 join하고 종료가 이를 기다린다. 표시 계산본 저장과 표시 조회는 실시간 입력 recorder에서 제외하며 원본 codec·raw capture 입력 계약을 확장하지 않는다. 실제 원본 저장은 계속 capture되는지 함께 확인했다.
- 새 display 시간대 평균은 확인 표본 분 수를 함께 반환한다. 완성된 정규장과 두 전체시장 분모를 모두 확인한 경우만 비율을 표시한다. NAS 조회 오류는 표시하고 404 등 명시적인 series 기능 부재만 기존 NAS raw 자료로 호환한다. 구형 서버의 품질 없는 통계를 새 display 통계로 오인하지 않는다.
- 과거 5일의 저장 HTTP 입력 읽기 대조는 원자 DB snapshot이 아니며, 기존 API의 완료 요약을 사용했다. v2 원본 receipt가 노출되지 않으므로 빈 분을 0으로 증명하지 않았다. 10/1·2·6·7·8 정규장 확인 분은 각각 **302/326/326/322/316**, 08~11시 확인 분은 **86/94/93/94/81**이다. 모두 일별 전체 합계는 null이며 기존 원본·운영 DB에 게시하지 않았다. 상세 JSON은 `data/diagnostics/top20-reconciliation-historical-dry-run.json`이다.
- NAS candidate는 현재 운영 release의 검증된 dependency/schema 계약을 유지한 비활성 소스다. 일반 stage가 로컬 Dockerfile 빌드 marker 차이로 거부한 기록을 보존했다. 소스 모드에서 새 이미지나 운영 runtime 계약을 바꾸지 않았다.
- 최종 비활성 후보는 `2026.10.12-top20-reconciliation-v1-5d30f2d9db66dd92` (1054파일)이며 manifest와 최종 로컬 소스 지문이 모두 일치했다. 준비 후 상태 조회에서도 활성 release는 기존 `2026.10.11-mock-state-api-v1-6f6782b2c18171af`였다. 현재 HEAD는 `941347e30ce9ff55603fced66e90fd75de094672`이며 이 TOP20 변경은 미커밋이다. 후보는 다른 동시 개발의 기존 dirty source도 포함하므로 독립 PG gate 없이 운영에 적용하지 않는다.
- 전용 PostgreSQL gate 시도는 `operation_busy`로 실행 전에 거절됐다. 기존 10/12 녹화 예약은 `armed` 상태이며 예약 lock을 유지한다. 예약·제어·운영 release를 변경하지 않았다. 새 PG 테스트는 해당 잠금 해제 뒤 실행해야 하며 아직 통과로 보고하지 않는다.
- 로컬 검증 수량과 최초 실패/후속 통과는 CURRENT_STATUS를 따른다. 전체 all-local·hosted CI·commit/push·NAS 배포·역사 자료 게시는 미실행이다.
- 최종 기능·종료·녹화·계좌 경계 21모듈 **395건 통과**. 이후 늦은 일별 응답·과거 화면의 현재 0원 표시를 수정하고 화면/클라이언트 **10건 재검증 통과**. 실제 Qt 화면은 Windows 한글 글꼴을 명시한 offscreen 이미지로 08:00 축·미확인 회색·조회값을 대조했다. 직접 PostgreSQL 연결 감사는 통과했지만 소비자 감사는 다른 mock runner 작업 4곳의 delta, 전체 테스트 등록은 별도 뉴스 테스트 미등록 때문에 통과 전이다. 잘못 지정한 테스트 모듈의 로딩 실패는 성공으로 계산하지 않았고 실제 파일명을 확인한 뒤 회귀를 재실행했다.

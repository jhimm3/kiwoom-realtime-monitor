# 초봉 연결 조사 — 실시간 0B / 연구 분봉

## 2026-10-16 저장 신고가 우선 표시와 갱신 전 파란 점

사용자 요청에 따라 순위표가 처음 그려질 때 저장된 신고가를 먼저 표시하고 새 조회로 갱신한다. 갱신 전 값은 신고가 가격 셀 오른쪽 위에 작은 파란 점과 툴팁으로 구분하며 새 결과가 반영되면 점을 없앤다. 값이 없으면 기존 `-`를 유지하고 점을 만들지 않는다. 5일·20일·250일·역사적 신고가 선택과 신고가 거리/행 강조는 같은 가격 선택 경로를 사용한다.

- 기존 `high_price_policy.selected_high_price`가 저장 일봉 값과 마지막250일 캐시를 화면 대체값으로 사용한다. 새 일봉/역사적 조회로 가격이 낮아지는 경우도 새 값으로 교체한다. 당일0B 고가 overlay는 유지한다. 표시 가능한 값이 있다는 이유로 원래 수집·기간 확인 근거를 승인하지 않는다.
- `DailyBarRepository.load_targets`는 저장된5/20개 일봉에서 표시 고가를 복원하지만 `collection_verified`와 기간 상태는 미확인으로 유지한다. 짧은 원본 이력으로250일 최고가를 새로 계산하지 않고 기존 stocks 캐시를 사용한다. 원본/메타데이터·스키마·저장 승인 조건은 유지한다.
- 기존 MainWindow의 cache 읽기를 후속 조회 대기 뒤가 아닌 첫 순위 적용에 연결했다. 현재 선택 기간의 새 일봉 응답/기준일·기간 근거로 표시 상태를 갱신하고, 역사적 고가는 기존 checked-on 정보와 성공 응답을 사용한다. 실패·불완전 응답은 기존 숫자를 지우지 않으며 기간별로 갱신 대기를 표시한다. 날짜가 바뀌면 이전 기준값에 점을 다시 표시한다. 0B callback에 새 DB 접근·TR·timer·worker는 추가하지 않았다.
- 기존 `NxtMarkerDelegate`가 신고가 셀의 선택 role(`UserRole+4`)에4px 파란 원을 그린다. 기존 NXT 삼각형·hover/선택 표시와 수명은 유지한다. 별도 widget/계층을 만들지 않았다.

검증: 관련12모듈130건/49.781초가 실패/오류/skip0, worker·자식 종료 확인으로 통과했다. 실행 중 소스 지문 변경0이며 최종 지문도 같은 실행과 일치한다. 실제 SQLite 캐시→순위 첫 표시→0B/거리→새 일봉·역사적 응답·기간 전환·불완전 응답·날짜 변경, 새 값이 낮아지는 경우, NXT/파란 점의 실제 paint를 확인했다. 격리 MainWindow 렌더링에서70,000 저장값 위 파란 픽셀12개→68,000 새 값 위0개, 기존 NXT 표시 유지도 확인했다. 증거는 `data/diagnostics/second-trade-transition-20261016/high-cache-{first,acceptance}.*`와 `high-cache-{cached,fresh}*.png`다. 제품 변경은 기존4파일이며 운영 DB·계좌·조회/주문·서버/실행 앱 재시작은 없었다. 로컬 미커밋·미배포이며 all-local/CI·NAS/장중 수락과 구분한다.

## 2026-10-16 거래대금·강도·알림을 11일 변경 전 동작으로 복구

사용자는 ‘미확인 때문에 막지 말라’는 요청을 **10월11일 변경 전처럼 복구**로 구체화했다. Git `44af48de`의 직전 `89fd6fd`에서 `MainWindow._render_trade_values`는 수신 거래대금을 즉시 표시하고, 그 값으로 강도와 기존 설정 임계값 알림을 계산했다.11일 변경은 자료 품질이 COMPLETE가 아니면 강도를 비우고 알림을 막았으며 금액에 부분/미확인 접두어를 붙였다. NAS 미확인 snapshot은 새0B가 와도 미확인이 유지되는 경우를 재현했다. 수신 지연과 표시·계산 차단은 별개였다.

해당 동작만 기존 `main_window.py` 안에서 복구했다. 현재/직전1분과5분·60분·1일의 금액/강도·기존 알림, 일반/제외 종목 테마 합계는 수신값을 사용한다. 품질·누락·복구 상태는 셀/합계 툴팁에 별도로 남긴다. 체결 당시 관측 기록의1/5분 금액과 화면 합계도 품질 때문에 None으로 지우지 않고 기존 `input_quality`와 함께 보존한다. 이는 기록 경로이며 새 주문 권한을 만들지 않는다. 자료 집계/보완·누락 원장·복구 transaction, 현재 키움/KST 시계, 실제 체결 시각, 기존120ms 화면 갱신, TOP20 지수의 별도 구간 근거와 매수/매도 압력의 실제 공백 판정은 보존한다. 전체 파일/11일 이후 기능을 되돌리거나 초봉 생산을 복원한 것은 아니다.

관련9모듈116건 첫 실행을 끝까지 마친 뒤 새 시험의 표시 소수 자릿수 기대값 실패2건과 그 실패 후 임시 DB 종료 오류2건을 수집했다. 숫자값을 비교하도록 시험만 보완한 최종116건은 실패/오류/skip0,41.851초로 통과했고 worker/자식 종료와 실행 전후 소스 변경0을 확인했다. 실제0B callback→갱신→현재/직전1분·강도·알림/설정OFF, NAS 미확인·수신 공백, 테마 합계·체결 관측 기록, 기존 복구·저장·차트를 함께 검증했다. 같은 합성 자료를 Git 직전 renderer와 현재 renderer에 넣어 현재/직전1분의 금액·강도·알림/배경이 일치함을 확인하고 실제 Qt 화면을 렌더링했다. 제품 변경은 `main_window.py` 한 파일이다. 증거: `data/diagnostics/second-trade-transition-20261016/live-display-pre11-{first,final}.*`, `live-display-pre11-acceptance.json`, `live-display-pre11.png`. 로컬 미커밋·미배포이며 all-local/CI·NAS/장중 수락은 별도다. 기존 사용자 DB·실행 중 앱·운영 서버를 수정/재시작하지 않았다.

## 2026-10-16 테마 H2 상승률 시작가격 의미 복구

RMS 후속으로 H2의 입력→계산→상태/제안→Runtime 공유·기록→API→Windows 경로를 대조했다. 기존 H2는 창의 첫 관측 초 종가부터 마지막 가격까지의 상승률을 비교했는데,0B 전환 뒤에는 첫 개별 체결 가격부터 계산했다. 같은 체결을 기존 역사 집계기와 실제 Collector→RAM adapter→H2에 넣어 **대장 선정까지 뒤바뀌는 회귀**를 재현했다. 새 전략을 설계하거나 기본 임계값을 정한 작업이 아니다.

### 원인과 같은 자료의 전후 대조

합성10건에서 종목A의 첫 관측 초 가격은90→100, 창 마지막 가격은100이다. 종목B는 첫 관측 초100, 마지막105이며 두 종목의 현재 창 대금은 각각18,900원으로 같다. 기존 H2는A0bp·B500bp로B를 선택했지만 전환 후에는A1,111bp·B500bp로A를 선택했다. 시작 가격만 원래 기준으로 복구한 뒤에는 모든 member metric과 대장 선정이 역사 계산과 일치했다. 고가·마지막 가격·현재/이전 창 대금·대금비·건수는 수정 전후 같다. 근거는 `theme-h2-before.json`, `theme-h2-after.json`과 `probe_theme_h2.py`다(모두 `data/diagnostics/second-trade-transition-20261016/`).

`evaluate_theme_leadership`은 현재0B에만 창 첫 관측 초의 마지막 **가용** 수신가격을 시작 가격으로 선택한다. 모든 수신 체결은 기존대로 고가·대금·건수와 원본 참조에 남긴다. 초봉 객체/OHLCV 집계·저장·새 상태·타이머·조회·무체결 초 보간은 추가하지 않는다. 기존 두 창·대금/가격 순위·표시 대장 확인·추세/확산/상한·보호 제안 정책과 계좌/주문 권한도 바꾸지 않는다. 역사 초봉과 완료 분봉 point는 기존 분기를 유지한다. 지연 체결의 관측 시각 신선도와 가용시점·누락/공백 검사는 보존한다.

### 연결과 호환성

- 기존 `CentralRealtimeCollector.ram_observation_snapshot`→Runtime의 한 `_trade_cut`/공유 불변 point→`market_theme_shadow_inputs_from_ram`→`evaluate_market_theme_shadow`→H2를 사용한다. 새 변환/집계/수집 owner는 없다. 실제 소비자의 공통0B 공유와 시장 분봉 읽기·동적 비중 RMS 경로는 유지한다.
- H2 metric은 기존 상태·`evaluate_entry_thesis`·Shadow/R4 비교에 전달된다. 기존 Runtime의 context 내용 hash·`latest_results`·선택적 기록 writer와 `research_read_routes.py`의 응답 외피를 재사용한다. 기존 원장을 재작성하지 않는다. 새 주도주/구조 producer나 미정 돌파·연구 규칙 적용 정책으로 승격하지 않는다.
- 현재0B의 새 결과에만 `leadership.facts.price_return_basis=first_observed_second_last_received_price/v1`을 기록한다. 기존 context 내용 hash에 포함된다. Windows 대장 열 tooltip은 이 명시 필드가 있을 때만 가격 기준과 모든 체결의 고가/대금/건수 반영을 설명한다. 필드가 없는 과거0B·초봉·분봉 결과에 새 의미를 소급하지 않는다.
- 연구의 `compute_minute_theme_leadership`과 명시 역사 초봉 factor는 원래 경로를 유지한다. 분봉 연구를 틱으로 만들거나 현재0B와 가격·시각 일치를 일반화된 연구 활용 조건으로 추가하지 않는다.

최초 제한 환경의 재현은 결과 파일 생성 뒤 종료 확인 전에 중단해 `theme-h2-before-interrupted.json`으로 보존했고 유효한 종료 검증으로 사용하지 않는다. 같은 소스를 승인된 사용자 실행 환경에서 다시 수행한 전/후 재현은 각각 종료 코드0이며 collector 정리까지 완료했다. 운영 NAS·DB·계좌·주문·원본 백업은 사용하지 않았다. 관련 회귀·API 결과와 최종 소스 지문은 같은 디렉터리의 `theme-h2-regression*`, `theme-h2-api*`, `theme-h2-acceptance.json`에 기록한다. 아래 H2 표본 의미 검토 대기 문구는 이 복구 이전 기록이다.

### 최종 검증과 남은 범위

관련9모듈547건은468.901초, 서버 API89건은108.161초에 각각 실패/오류/skip0으로 통과했다. 두 실행 모두 worker/자식 종료를 확인했고 실행 중 소스 수정0, 전후/최종 지문 변경0이다(각432/424파일). 첫 초의 가격 변화·반복/단일 수신·고가 보존·창 사이 빈 초·가용시점 이전 체결 배제·같은 초만 관측한 경우·원본 불변·대장/추세·보호/연구 비교·0B 공유·시장1분/5분·RMS·화면·API 인증/기존 route 계약을 포함한다. 한국어 Qt 대장 [tooltip](../../data/diagnostics/second-trade-transition-20261016/theme-h2-tooltip.png)도 실제 widget으로 렌더링·확인했다. 필드 없는 과거 결과에는 안내를 붙이지 않는 검사가 통과했다.

최종 지문 대조에서 RMS 완료 이후 제품 변경은 H2 계산·Shadow 사실 표시·Windows 안내의3파일뿐이며 Collector/Runtime·분봉·RMS·주문/연구 구현은 이전과 같았다. 기존 H2 정책과 추세/확산/대장 안정화/상한·진입 근거/행동 제안·역사 초봉 adapter의8개 정의는 HEAD와 AST가 같고 Collector의 초봉 생성/저장 호출은0이다. 이는 현재 로컬 코드 검증이며 all-local/CI·실제 장중·운영 NAS 수락은 아니다. Commit/push·배포·실계좌 활성/주문은 하지 않았다. 일반화된 연구 규칙 생산·실시간 적용 방법과 미정 주도주/돌파·운용 조건은 여전히 별도이며 이번 복구로 완료 처리하지 않는다.

## 2026-10-16 동적 비중 RMS의 기존 표본 의미 복구

기존 동적 비중 RMS는 실제 관측한 초별 마지막 가격 사이의 수익률을 사용했다. 초봉을 제거하면서 모든0B 수신가격 사이의 수익률로 바뀐 것은 같은 수식이라도 표본 의미를 바꾸는 회귀였다. 실제 RAM adapter와 보존된 `SecondTradeAccumulator`를 격리 합성 시험에서 같은 체결로 대조했다. 역사 집계기는 시험 비교에만 사용하고 제품 Collector에는 다시 연결하지 않았다.

| 같은 마지막 가격10000→10100→10200 | 기존 관측초 RMS(bp) | 수정 전 개별체결 RMS(bp) | 수정 후(bp) |
| --- | --- | --- | --- |
| 초마다1회 수신 |100|100|100|
| 같은 가격을 초마다4회 수신 |100|43|100|
| 초 안에 다른 가격을 수신한 뒤 마지막 가격 도달 |100|1157|100|

`dynamic_allocation_inputs_from_ram`은 기존 시각/sequence 순서의0B 행에서 실제 관측한 초별 마지막 수신가격을 선택한 뒤 기존 최소3개 가격·정수/양수 검사·Fraction 제곱평균·올림 계산을 그대로 쓴다. 기존 정책 창을 변경하거나 무체결 초를 채우지 않는다. 한 초에 수신이 많아도 여러 초를 관측한 것처럼 보지 않는다. 구간 대금은 모든 수신 체결의 가격×수량을 합산하며 세 재현의 대금도 이전과 같다. 초봉 객체·OHLCV 생산/저장·새 공유 상태/조회/타이머·운용 조건은 추가하지 않았다.

현재0B facts의 선택 진단 `volatility_basis=last_received_price_per_observed_second/v1`과 `volatility_sample_count`는 계산 기준과 실제 가격 표본 수를 표시한다. Windows 열 이름/tooltip도 관측초별 마지막 수신가격·가격 표본 수·무체결 초 미보간을 설명한다. 과거 기록은 원래 구형 입력으로 표시하고 새0B 결과로 재표지하지 않는다.

관련6모듈337건은 실패/오류/skip0·279.641초·worker/자식 종료 확인으로 통과했다. 같은 초 반복·초 안의 가격 변화·한 초만 관측/빈 초·0B/분봉 공존·기존 분봉 비교 거절/양의 비교·가격/위험/owner·공유 관측/시장1분·5분·화면을 포함한다. 실행 도중 소스 수정은 없고 전후 관련 지문 변경0이다. 근거는 `data/diagnostics/second-trade-transition-20261016/allocation-rms-before.json`, `allocation-rms-after.json`, `allocation-rms-regression.{json,log}`, `allocation-rms-regression-source.json`이다.

그 뒤 격리 Qt 렌더에서 RMS 열 제목 잘림을 확인해 해당 열의 너비만 header에 맞췄다. 다른 열 너비는 유지한다. 최종 UI5건은0.863초·실패/오류/skip0·worker/자식 종료 확인으로 재통과했고 실행 전후 지문 변경0이다.337건 이후의 제품 소스 차이는 이 열 너비 한 줄뿐임을 해시로 확인했다. 과거 분봉 비교 함수와 연구 비교 두 파일은 HEAD와 같고 Collector의 초봉 생성/저장 호출은0이다. `allocation-rms-ui-final.{json,log}` 및 `allocation-rms-acceptance.json`에 검증 경계를 기록했다. [최종 격리 화면](../../data/diagnostics/second-trade-transition-20261016/allocation-rms-shadow-results.png)에서 한국어 열 제목·값·기존 분봉 결과·주문 미승인을 확인했다. 현재 전체 소스337건 재실행으로 합쳐 보고하지 않는다.

연구의 일반 실시간 적용 방법과 미정 돌파·운용 조건은 만들지 않았다. 테마 H2 등 다른 표본 의미 차이의 검토와 현재 전체 all-local/CI·실제 장중/NAS 수락은 별도다. 기존 자료·주문/위험 경계는 보존했으며 commit/push·NAS 조회/배포·실계좌 활성/주문·운영 DB 작업은 하지 않았다. 아래 RMS 검토 대기 문구는 이 복구 이전의 기록이다.

## 2026-10-16 승인된 분봉 연결 복구와 관련 재검증

선행 조사 후 사용자가 분봉 연결 복구·0B라는 이유만의 차단 수정·화면/테스트 동반 확인을 승인했다. **이번 완료 범위는 기존 분봉 기능 복구이며 연구 결과의 새로운 실시간 적용 규칙을 만드는 작업은 아니다.**

### 실제 복구

- `market_theme_shadow_monitor.py::_snapshot`은 기존 v3/v4 study가 있을 때만 같은 Collector cut에 `include_closed_minutes=True`를 요청한다. 별도 분봉 생성/저장/재집계를 추가하지 않는다.
- 같은 Runtime의 기존 `_market_minute_key/_market_minutes/_read_market_minute_history/_attach_ready_market_minutes`와 단일 owned task/cache/종료 drain을 복구했다. 기존 `a5af` 작업 경로에 보존된 이 네 함수 및 세 원래 회귀를 해당 범위만 대조·복구했고 다른 파일/실행 소스를 전환하지 않았다.1분 후보·5분 지속 계산은 원래 함수·정책·단위대로 다시 연결한다.0B 현재가격/H1/H2/계좌·보호는 비동기 분봉 읽기를 기다리지 않는다.
- `_position_study_observation_from_ram`의0B snapshot 조기 반환만 제거했다.0B와 완료 분봉이 같은 입력 묶음에 공존할 수 있다. 기존 exact 분봉 모의 비교의 가격·시각·정밀도·품질·owner 조건은 유지하며, 결과는 원래 `conditional_shadow` 범위다. 이를 현재 EV/매매 규칙/주문 승인으로 바꾸지 않았다.
- Windows 안내와1분/5분 tooltip은 실시간 판단에서도 봉 계산을 유지하고 현재가·체결·보호는0B를 쓴다고 표시한다. 동적 비중의 선택적 분봉 모의 비교는 과거 비교 조건과 미정 실시간 적용 방법을 tooltip에서 구분한다. 과거 초봉 표시·현재 만료/결측 표시·주문 미승인은 유지한다.
- 공통0B `RamTradeObservationCut`은 명시 복사에서 원시 행을 분리하고 인덱스의 행 참조를 유지하며 불변 point를 공유한다. 회차별 공통 변환/계산 구조·0B 가격×체결량과 별도 FID14 차이, 초봉 생산 중단 변경은 유지했다. RMS의 관측 간격이나 기존 비교 정책을 추가 변경하지 않았다.

### 검증과 수집 오류

| 실행 | 결과 | 의미 |
| --- | --- | --- |
| 최초 관련25모듈 |932건 /648.289초, 실패0·오류12·skip0 | 동일 소스로 끝까지 수행. 오류는 서로 다른 두 원인이며 실행 도중 수정0. 전체 통과로 표기하지 않는다. |
| 오류 원인1 | 공통 인덱스의 `mappingproxy` 기본 deepcopy가11개 가격 경계 subtest에서 `TypeError` | 명시 복사 경계를 보완하고 원시 행 격리·불변 point 공유 검사 추가. 가격·승인·위험 검사를 제거하지 않았다. |
| 오류 원인2 | 지연 체결 후 `r4_action_comparison`가 있다고 기대한 검사1건의 `KeyError` | 실제 합성 재현은 `native_turnover_window_incomplete:005930`로 비중 자체가 보류·결과None이었다. 그 사실·미승인을 검사하고, 분봉 capture partial 거절은 별도 직접 검증했다. |
| 실패 검사·관련6모듈 재검증 |334건 /308.142초, 실패·오류·skip0 |0B/분봉 공존의 양의 비교, 기존 가격·시각 불일치/불완전 분봉 거절, 기존 owner/Risk/정책·lease, 시장1분/5분, 실제 SQLite 읽기와 bytes 불변, 취소/종료, 화면을 확인했다. |
| 실행 소스 | 두 실행 각각 전후 지문 변경0·worker/자식 종료 확인 | 마지막 실행은 제품/관련 시험/API 기준 등429파일. 전체 Windows all-local/CI의 현재 통과로 확대하지 않는다. |
| 격리 화면 | 한국어 글꼴 지정 후 안내·결과 영역 렌더 확인 | 최초 offscreen 글꼴 미지정 이미지는 글자가 깨져 검증으로 채택하지 않았다. 가짜 API/계좌 fixture이며 실제 앱/NAS 수락은 아니다. |

8개 소비자의 분봉 조회는 동일20종목을1회씩 읽고 동일 범위 계산은1회 공유했다. 같은 분·코호트·commit의 반복 상태 조회는 추가 읽기/계산 없이 원래 TTL을 유지하고, 기존 분봉 commit 변화 때만 다시 읽는 검사가 통과했다. 분봉 읽기를 막아둔 상태에서도0B core가 다음 입력을 처리했으며 종료 취소는 기존 읽기 종료까지 drain했다.0B 인덱스 변환도1/8 소비자에서 각각1회였고 화면 결측과 실제0을 구분했다. 이는 격리 검증이며 운영 NAS 성능 측정은 아니다.

근거: `data/diagnostics/second-trade-transition-20261016/minute-restoration-first.{json,log}` 및 `minute-restoration-recheck.{json,log}`, 각각의 `-source.json`. 화면은 [안내](../../data/diagnostics/second-trade-transition-20261016/restored-minute-shadow.png)와 [결과](../../data/diagnostics/second-trade-transition-20261016/restored-minute-shadow-results.png)다.

### 보존과 남은 범위

과거 비교용 `position_target.py`와 `research_evaluation.py`의 구현·규칙, 계좌/Risk/주문 소유권·운용 승인, 기존 원장·봉·백업은 보존한다. 새 연구 규칙·가격 배율/근접 셀·허용오차·해상도 일반화·주문 연결은 만들지 않았다. 일반 실시간 연구 적용 방법, 일봉/5분봉을 포함한 연구 구현 범위 확대, RMS 관측 간격 의미 검토는 미정/별도 검토다. Commit/push·NAS 조회/배포·실계좌 활성/송신·운영 저장 중단 수락은 수행하지 않았다. 아래 감사의 코드 변경 중단 문구와 ‘현재’ 문제는 복구 전 기록이다.

## 2026-10-16 후속 사용자 기준과 전환 재검토

**이 절은 승인 전의 기준 문서화와 선행 조사·보고 기록이다.** 당시에는 아래 문제를 고치거나 테스트 기대값을 바꾸지 않았다. 이후 승인된 복구와 관련 재검증은 위 절을 따른다. 아래 원래 조사표의 ‘현재’는 그 표를 작성한 전환 전 시점이다.

### 연구와 실시간 적용의 역할

- 연구는 일봉·5분봉·1분봉 등 필요한 자료로 스스로 유효한 규칙을 발견·검증하고, 그 규칙을 실시간 자동매매에 적용할 수 있는 방법을 찾는 역할이다. 특정 봉 해상도의 일치 자체가 연구의 목적은 아니다. 각 구현이 실제 지원하는 자료 범위와 이 요구 전체의 구현 여부를 구분한다.
- 실시간 현재가·체결 변화와 매매 실행 관측은 기존0B를 사용한다. 실시간 시장·테마·포지션 판단에 필요한 기존 봉 계산까지0B로 바꾸는 지시는 아니다. 직전 확정 분봉·과거 일봉·5분봉은 필요한 역할대로 유지할 수 있다.
- 검증된 규칙의 조건을 현재 가용 자료에서 평가하는 방법을 연결한다. `직전 분봉 종가 = 현재0B 가격`이나 과거 연구 사례와 현재의 절대 가격·시각·수량 일치, 동일 해상도를 연구 결과 활용의 일반 조건으로 만들지 않는다. 과거 계산의 정확 재현 조건은 그 비교 안에서만 유지한다. 구체적인 적용 규칙과 미정 정책은 임의로 채우지 않는다.
- 연구의 완결·보완 자료와 당시 실시간에 알 수 있었던 자료는 구분한다. 봉 안의 체결 경로를 만들어내지 않는다. 원본 자료·현재 계좌·Risk·보호·주문 수명의 검증은 해상도/가격 일치 검사와 별개다.

### 확인한 과도한 전환

| 항목 | 현재 코드와 확인 근거 | 기능 영향과 판정 |
| --- | --- | --- |
| 확정 분봉 연구 support 공급 제거 | `MarketThemeShadowRuntime._snapshot`의 이전 v3/v4 study 조건부 `include_closed_minutes=True`가 diff에서 제거됐다. Collector의 `ram_observation_snapshot` 및 `MinuteBarAccumulator.closed_observation_snapshot` 자체는 남아 있다. | 필요한 기존 분봉을 함께 전달할 수 있는데 Runtime이 요청하지 않는다. 분봉을 유지해야 하는 연결의 누락이다. 기존 분봉 accumulator/저장 자체를 삭제했다는 뜻은 아니다. |
| 0B snapshot의 연구 비교 일괄 차단 | `_position_study_observation_from_ram` 맨 앞의 `input_resolution == '0B'`이면 `current_0B_position_study_support_unconfigured`로 반환한다. 뒤의 확정 분봉 검사는 실행하지 않는다. | snapshot에0B가 있다는 사실만으로 함께 존재할 수 있는 분봉 연구 비교를 막는다. 이번 전환에서 추가한 과도한 차단이다. 영향은 v3/v4 `conditional_shadow`이며 동적 비중 전체가 이 조건 하나로 중단된다고 확대하지 않는다. |
| 시장의1분·5분 관찰을0B로 대체 | Runtime `_dispatch` 및 `market_theme_shadow_inputs_from_ram`은 `market_primary_timeframe='0B'`로 고정한다. `market_minute_trade_inputs`를 공급하는 Runtime 경로가 없으며 `market_minute_trade_inputs_from_rows`는 현재 제품/도구 호출자가 없다. `_dispatch`는 `compute_price_trade_candidates`용0B 입력만 전달한다. | `market_theme_shadow`의 기존1분 후보·5분 지속/확인 계산은 함수와 테스트에 남아 있지만 실제 Runtime의 해당 분기가 선택되지 않는다. 기존 분봉 기능을 초봉 보조와 함께 제거한 과도한 변경이다. H1 순위 기반 시장 판단 전체가 없어졌다는 뜻은 아니다. |
| 화면과 회귀 기대값도0B 전용으로 고정 | `candidate_monitor_dialog` 안내는 ‘실시간 시장·테마·동적 비중은 개별0B, 연구는 확정 분봉’이며1분 표시를 ‘실시간0B와 별도 연구 결과’로 설명한다. `test_candidate_monitor`는 live minute read0회/primary0B/분봉입력None을, `test_live_0b_minute_transition`은0B support 차단을 기대한다. | 사용자 기준보다 좁은 구현을 화면과 검사도 지지하고 있다. 코드 수정 시 함께 재검토할 대상이며 지금 기대값을 바꾸거나 기존 통과 기록으로 기능 적합성을 주장하지 않는다. |

### 이전부터 있던 연구 적용 제한

이하 조건은 이번 초봉 전환에서 새로 만든 조건과 구분한다. `git blame`에서 분봉 support adapter의 기존 가격·시각·정수초 검사는 `93883dff`에 존재하고,0B 조기 반환만 미커밋 추가다. 과거 비교 계약의 의미를 보존하는 것과 현재 자동매매에 적용할 방법을 만드는 것은 별개의 작업이다.

| 항목 | 현재 확인한 조건 | 새 사용자 기준에 따른 해석 |
| --- | --- | --- |
| 분봉 종가와 현재 기준가격·관측 시각 일치 | `_position_study_observation_from_ram`과 `position_target.position_study_support_observation` 모두 `bar.close == request.reference_price_won`, 봉 `available_at == reference_price_available_at`을 검사한다. | 같은 분봉 가격을 출발점으로 한 제한된 모의 비교에는 근거가 있으나, 직전 분봉과 현재0B를 각자 역할로 사용하는 일반 규칙의 요건은 아니다. 현재 실시간 가격 변화만으로 연구 비교가 막힐 수 있다. 자동매매 연결에 그대로 강제할 근거는 없다. |
| 과거 연구 사례의 정확 가격·수량·상태 셀 | `position_study_current_goal_binding`은 과거 결과의 initial/target quantity·reference price·cost와 현재 목표를 비교한다. `position_study_support_observation` 및 `evaluate_position_study_conditional_shadow`는 실제 OHLCV·규모·비용·상태 등을 포함한 정확 셀을 만들어 일치하는 표본만 비교한다. | 현재 구현은 일반화한 학습 규칙을 적용하는 모델이 아니라 제한된 사례별 조건부 비교다. ‘유효한 규칙을 연구해 자동매매 방법을 찾는다’는 기능 전체가 완료된 것으로 볼 수 없다. 정확 재현 검사를 일괄 삭제하거나 근접 셀/가격 배율을 임의로 추가하지 않는다. |
| 정수초 시각만 허용 | support adapter는 `cut.microsecond`/`at.microsecond`를 거절하고, 지원 관측 계산도 정수 시간차를 요구한다. | 기존 연구 표현의 정밀도 제한이다. 소수초0B/수신 시각 자체가 무효라는 일반 규칙으로 사용하면 불필요한 거절이 된다. 원래 표현 보존과 실제 적용 방식의 구분이 필요하다. |

### 그대로 유지할 경로와 별도 검토

| 경로 | 확인 결과 |
| --- | --- |
| 표준 연구·기존 Family·새 주도주 연구 | native 분봉 replay/Family/campaign 경로는 남아 있고0B로 치환되지 않았다. 새 Family의 명시 `minute_bar/KRX/final_completed` 계약은 현재 구현 범위이지 전체 연구를1분봉으로 제한하는 사용자 요구가 아니다. 일봉·5분봉까지 모두 연구·적용 가능하다고 검증한 것은 아니다. |
| 확정 분봉 factor/JSON/원본 대조 | `compute_minute_theme_leadership`, `minute-research-v2` preview, v2 source-check는 실제 분봉을 사용한다. 분봉을 가짜 틱으로 바꾸지 않는다. 이1분 전용 도구의 자료 검증을 연구 전체의 해상도 규칙으로 확대하지 않는다. 과거 초봉 비교/원본 대조는 명시 역사 경로로 남아 있다. |
| 현재가·체결·하드 보호 | Collector의 기존0B 가격 관측과 Runner/Risk/listener 경로는0B로 유지한다. 직전 분봉 완료나 연구 결과를 기다리는 조건을 보호 경로에 추가하지 않는다. |
| 구간 거래대금·거래량 | 기존 초봉도0B의 가격×체결량을 조합했다. 현재 `_observe_ram_trade`도 같은 조합을 쓰며 FID14 누적대금 차이는 별도 필드다. FID14만 없다고 자료 부족으로 처리했던 잘못은 앞서 수정됐고 이번에는 변경하지 않았다. 실제 체결량/누적량 근거가 모두 없는 경우와 구분한다. |
| 동적 비중의 변동성 값 | 기존 초봉 종가 사이 RMS가 현재 개별0B 가격 사이 RMS로 계산된다. 분봉을0B로 바꾼 사례는 아니지만 관측 간격이 바뀌므로 같은 수치/모델 입력이라고 간주할 수 없다. 적절한 간격·기존 연구 입력과의 대응은 별도 검토이며 임의의 새 창·기준은 정하지 않는다. |
| 테마 H2 | 원래 초봉 두 창의 가격·대금/고점/관측 상태를 개별0B 관측으로 바꿨다. 원래 분봉을 제거한 사례로 확인하지 않았다. 원시 체결과 초봉 종가의 표본 의미 차이는 남으며 같은 성과 검증으로 재표지할 수 없다. |
| 공통 관측 공유 | 한 snapshot의0B 인덱스·불변 point를 공유하는 현재 코드가 있다. 이것은 봉 계산 제거의 근거가 아니며 이번에는 성능 측정이나 NAS 병목 없음 판정을 하지 않았다. |
| 실제 보호·원본 검증 | source 승인·가용 시점·실제 결측/공백·자료 변조·현재 계좌/미체결/Risk/주문 수명 검사는 ‘불필요한 가격 일치 조건’과 다르다. 이 조사에서 제거 대상으로 지정하거나 완화하지 않는다. |

### 조사 범위와 상태

현재 로컬 호출자→Collector/입력 adapter→순수 계산→Runtime→UI·시험 기대값을 읽었고, HEAD 대비 diff와 기존 설계 `r4-model-support.md`, 기존1분/5분 모듈 지도를 대조했다. 변경 전체가 HEAD에 커밋돼 있지 않으므로 HEAD diff만으로 모든 중간 수정 시점을 확정하지 않으며, 기존 문서/시험과 현재 호출 경로를 함께 근거로 사용했다. 운영 NAS 소스·설정·DB·실제 계좌는 조회하지 않았다.

이번 작업은 문서만 수정하며 제품·도구·시험 코드 및 데이터는 수정하지 않는다. 테스트 실행·새 회귀 통과·전환 완료를 주장하지 않는다. 기존 전환 시험의 실패/재검사 기록은 `data/diagnostics/second-trade-transition-20261016/`에 그대로 남긴다. `input-role-audit-before.json`/`input-role-audit-after.json`은 이번 문서 작업 전후 코드·검사 자료의 지문 비교이며 실행 검증 보고서가 아니다. 수정안 구현·새 연구 범위 확대·배포는 이번 범위가 아니다.

## 전환 전 연결 조사 기록

2026-10-16 후속: 새 주도주 돌파의 기존 모의 Runner와 판단/성과 campaign 연결은 실시간0B·연구 분봉으로 구현했으며 해당 경로는 초봉을 읽지 않는다. 아래 시장/테마 쉐도우·동적 비중·초봉 생산/저장 등 앱 전체 전환은 아직 미완료다. ‘새 돌파 연결’과 ‘초봉 전체 제거’를 구분한다.

2026-10-16. 사용자 확정: **실시간 자동매매에 필요한 관측은 0B 체결, 연구·백테스트의 가격 자료는 분봉**이다. 초봉을 공통 중간 자료로 요구하지 않는다. 이 문서는 현재 연결의 읽기 전용 조사이며 집계·저장 중단 또는 소비자 전환 완료 보고가 아니다.

## 확인 범위와 운영 소스

- 로컬 `src/`, `scripts/`, `tests/`의 Python1,065개를 읽고 직접 식별자 참조48파일을 찾았다. 제품22개·도구4개·시험22개다. 직접 참조 외에 app binding, API, client/UI, 연구 저장/identity 경계를 별도 추적했다.
- 기존 무암호 `kiwoom-nas status`로 활성 release `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`, health `ok`, diagnostics idle, trace OFF를 확인했다. 변경 명령은 실행하지 않았다.
- 해당 release의 `src/`, `scripts/`565개를 NAS에서 읽기 전용 스캔했다. 직접 참조26파일이 로컬과 같고, 이 파일들의 초봉 참조 줄도 모두 동일하다. 파일 전체 hash는23개가 같고 `database.py`, `database_market_bars.py`, `realtime_collector.py`는 다르므로 로컬/NAS 전체 소스 동일로 보고하지 않는다.
- NAS Python3.8 AST가 처리하지 못한 비관련2파일(`historical_backfill.py`, `prepare_missing_historical_market_news.py`)은 원문 식별자 검색은 했으며 초봉 직접 참조가 없었다. 관련26파일의 AST 오류는 없다. 로컬 AST 오류는0이다.
- 개별 RAM 선택·계좌별 현재 활성 설정은 읽지 않았다. 아래 ‘연결’은 실제 소스 경로가 존재한다는 뜻이며 모든 슬롯이 지금 실행 중이라는 뜻은 아니다. 운영 DB 행 전수 조회·원본 대조 실행·재시작·배포는 하지 않았다.
- 근거: [로컬 참조·AST 목록](../../data/diagnostics/second-trade-audit-20261016/local-inventory.json), [활성 NAS 참조·AST 목록](../../data/diagnostics/second-trade-audit-20261016/nas-inventory.json), [읽기 전용 scanner](../../data/diagnostics/second-trade-audit-20261016/scan_sources.py). AST의 직접 Call 목록만으로 callback을 제외하지 않고 `owned_to_thread`/`drain_thread_call` 전달도 수동 대조했다.

## 전체 연결과 새 기준의 처리 대상

| 영역 | 현재 연결·계산 | 상태와 새 기준 |
| --- | --- | --- |
| 0B 생산 | `realtime_collector.py`의 체결 처리에서 분봉 accumulator와 `SecondTradeAccumulator.add`를 각각 호출 | Collector 실행 시 연결. 별도 초봉 TR가 아니라 동일0B의 파생 집계. 0B·분봉은 유지하고 초봉 생산 경계를 제거할 대상 |
| 초 단위 상태 | `minute_bars.py::SecondTradeAccumulator`가 날짜/초/종목/venue별 OHLCV·추정 대금·관측 건수·가용시각, 누적량 기준·중복·늦은 체결 상태를 관리 | DB 저장과 RAM snapshot이 같은 `_bars`를 공유. 원본 모든 거래소 체결 이력이 아님 |
| 정기/종료 저장 | `realtime_collector.py`의 매분45초 flush에서 현재 분보다 이전 초봉을 저장. 종료·인증 전환은 대기분까지 flush. `_pending_second_retries`로 실패 재시도 | 연결. 집계·저장 중단 시 dirty/retry/종료 drain/진단 pending도 함께 변경해야 함 |
| 저장소·schema | `database.py` protocol → `database_market_bars.py` SQLite/PG writer·reader, `database_codec.py`12열, `central_schema.py` migration4의 table/PK/index | 연결. 테이블은 `central_second_trade_bars`; 절대 상태 UPSERT. 기존 자료 보존과 새 생산 중단을 구분 |
| 공통 RAM owner | `MarketThemeShadowRuntime.start/_snapshot/_dispatch/_close_owned` → Collector retention/snapshot → `_second_trades.observation_snapshot` | 실제 연결. 최대60초·200종목·10,000행. DB를 읽지는 않지만 **현재 입력 형태는 초봉**이므로 ‘이미 직접0B’라고 부르면 안 됨 |
| 테마 주도력 H2 | `market_theme_shadow_inputs_from_ram` → `MarketThemeShadowInputs.second_points` → `evaluate_theme_leadership` | 연결. 인접 두 초 단위 창에서 수익률·고점 후퇴·대금/이전 대금 비율·관측 건수·우위 순위·leadership 변화 계산. 실시간0B 관측 상태로 바꿀 대상 |
| 시장 보조 관찰 | `market_price_trade_inputs_from_ram` → `compute_price_trade_candidates` | RAM Runtime 연결. 현재 Runtime은1m 주 후보와 `second_trade_auxiliary` 보조를 함께 계산. 초봉 보조는 주 분봉 후보/TTL의 필수 veto가 아니지만 의존성은 존재함. 실시간 판단은0B 기준으로 재정의 |
| 종목 thesis | `evaluate_stock_thesis_shadow` → H2 context → `evaluate_entry_thesis` | 간접 연결. thesis 유지/약화·proposal/action이 H2 품질·주도력 결과에 의존. 초봉 입력만 빼면 같은 판단이 유지되는 것으로 간주하지 않음 |
| 포지션 행동 비교 | `MarketThemeShadowRuntime._refresh_position_comparison` → 기존 계좌 RAM·`trade_references`; 선택 정책이 요구하면 stock-thesis 결과도 전달 | 최신 기준가격은 직접0B. thesis를 요구하는 정책만 위 H2에 간접 의존. 테마 없는 price/risk 전용 정책까지 초봉 필수라고 묶지 않음 |
| 동적 비중 | `_dispatch` 또는 기존 context wake → `_refresh_position_inputs` → `dynamic_allocation_inputs_from_ram` → 기존 배분 계산 | 별도 연결. `snapshot['rows']`의 `trade_second`로 최근 창 대금 합계와 close 수익률 RMS를 계산하고 `ram-allocation-window` refs 생성. EV/출구 capacity의 독립 근거를 이 숫자로 채우지는 않음. 실시간0B 상태로 바꿀 대상 |
| 표준 연구·백테스트 | `research_contract.RESEARCH_OBSERVATION_KINDS` = ranking/top20_membership/minute_bar; 기존 두 `research_families` = 완료 분봉/순위 | **초봉 DB 소비 없음.** 이 경로는 분봉 기준 유지. 기존 단순 전략 자체가 사용자 전략 완성본이라는 뜻은 아님 |
| 선택형 연구 factor | `research_factors`에 `second_trade_bar/as_available/single_venue` 의존 ThemeLeadership factor 등록 → `compute_theme_leadership` | 등록·계산 함수는 존재. 표준 두 Family에서 사용하지 않으며 별도 제품 호출자는 등록 lambda뿐. 새 연구 기준에서는 분봉으로 정의할 대상 |
| JSON preview 실험 | `scripts/preview_market_theme_shadow.py`가 명시 JSON의 `second_points`를 `SecondTradePoint`로 변환 → market/theme 또는 stock-thesis evaluator | 실행 가능한 별도 명시 CLI. 운영 초봉 DB를 자동 수집해 정규 연구에 투입하는 작업은 아님. 새 입력은 분봉 기준으로 정리하고 예전 사례는 과거 재현으로 구분 |
| 저장 thesis 근거 | `ResearchRepository.append_theme_leadership/append_entry_thesis` 및 `load_entry_thesis_evidence` → thesis 선택/preview | 파생 leadership·thesis·refs 보존/읽기 경계. 초봉 테이블을 읽지 않음. 일반 연구 실행에서 `append_theme_leadership`를 호출하는 제품 경로는 찾지 못함. 과거 근거를 새 분봉 전략 근거로 자동 재표지하지 않음 |
| 원본 대조 API | UI `check_ram_shadow_sources` → 인증 `GET /api/v1/research/ram-shadow/source-check` → app callback → `read_stock_thesis_source_check` → `load_second_trade_bars` | **저장 초봉의 실제 제품 reader1곳.** 사용자 명시 원본 대조 때만 조회. 정규 연구 loop/실시간 판단이 아님. 새 근거는 분봉 기준으로 재정의 |
| 미연결 DB adapter | `read_market_theme_shadow_inputs` → `load_second_trade_bars` | reader의 다른1곳. 제품/도구 호출자는 없고 시험에서만 호출. 이를 ‘현재 정규 연구가 초봉을 사용한다’는 근거로 확대하지 않음 |
| 독립 실시간 돌파 | `breakout_strategy.py::evaluate_realtime_breakout`가 RAM snapshot 초봉 rows를 요구 | 함수·시험만 존재, **제품/도구 호출자0**. 사용자 전략의 기본 경로로 연결하지 않음 |
| API·화면·선별 기록 | 기존 `research_read_routes.py` RAM 응답 → `CentralContentClient.load_ram_shadow` → `candidate_monitor_dialog.py`; Runtime이 기존 `shadow_*` 문서로 선별 저장 | H2·시장 보조·allocation 등 파생 결과 표시/저장. 일반 초봉 차트/초봉 export API는 찾지 못함. 화면의 source-check 초봉 일치 건수도 해당 진단 결과일 뿐 |
| 저장량 진단 | `database_storage_diagnostics.py`가 초봉 table을 주식 누적자료 범주로 집계 | 읽기 전용 용량 분류. 저장 중단 후에도 과거 자료가 있으면 표시할 책임은 남음 |
| recorder/replay | 아래 diagnostic writer/capture/replay 경로가 second writer 호출·결과 table·pending 상태를 포함 | 실시간 판단 입력과 다른 재현/진단 책임. 새 생산 경계 변경 시 새 진단 기대값을 조정하되 과거 fixture/자료는 유지 |
| scientific identity | `research_implementation.py`가 theme_leadership/market_features/market_shadow/breakout 등 전체 파일을 hash | 향후 초봉 경계 변경은 source identity에 반영됨. 기존 결과를 새 소스의 검증으로 자동 승격하지 않음 |

## 직접 초봉 참조 파일 전체

제품22개:

- `application/`: `breakout_strategy.py`, `market_research_features.py`, `market_theme_shadow.py`, `research_evaluation.py`, `research_factors.py`, `theme_leadership.py`.
- `central_server/` 저장: `central_schema.py`, `database.py`, `database_codec.py`, `database_market_bars.py`, `database_storage_diagnostics.py`.
- `central_server/` 수집/판단: `minute_bars.py`, `realtime_collector.py`, `market_theme_shadow_inputs.py`, `market_theme_shadow_monitor.py`.
- `central_server/` 진단: `diagnostic_collector_replay.py`, `diagnostic_recorded_execution.py`, `diagnostic_replay.py`, `diagnostic_replay_baseline.py`, `diagnostic_replay_contract.py`, `diagnostic_top20_execution.py`, `diagnostic_writer_registry.py`.

명시 도구4개: `scripts/audit_postgres_access.py`, `scripts/check_postgres_integration.py`, `scripts/compare_recorded_capture_overhead.py`, `scripts/preview_market_theme_shadow.py`.

시험22파일의 정확한 줄·hash는 inventory에 보존했다. 집계·저장 전용 시험, Collector/DB, H2/entry, RAM/포지션, 재생·PostgreSQL 경계 시험과 고정 diagnostic API baseline이 연결된다. 보존 백업·이전 clock audit의 초봉 행 수는 과거 자료의 진단 기록이며 새 실행 consumer가 아니다. 문서의 언급 수를 소비자 수로 합산하지 않았다.

## 이미 직접0B인 경로와 반증

- app의 `MockAutomationSupervisor`는 `price_source=collector.automation_price_observation`과 기존 automation price listener를 사용한다. 이 quote/손절 관측은 같은0B의 승인 최신가격이며 초봉 DB를 읽지 않는다. 시장/테마·allocation의 초봉 창과 분리해야 한다.
- 일반 현재가·등락률·거래대금, 분봉 집계 및 조회 보완은 초봉 table을 읽지 않는다. 0B 구독을 끄는 것은 요청한 초봉 제거와 다르다.
- PC `app_controller.flush_minute_cache`의 ‘1초 단위로 묶어 SQLite에 저장’ 주석은 **1초 저장 주기**에 대한 표현이다. 실제 값은 `MinuteOhlcv`와 `MinuteBarRepository`에 저장하며 별도 PC 초봉 table 생산 근거가 아니다. `trade_review_analysis.py`의 초 단위 언급도 체결 시각과 분봉 구간을 비교하는 설명이다.
- 표준 연구 export는 초봉 kind를 지원하지 않는다. `research_evaluation.py`의 `historical_second_trade_leadership`은 과거 맥락에 **없는 입력을 missing으로 표시**한 것이다. 초봉 공급이나 실제 소비의 증거가 아니다.

## 전환 순서와 현재 경계

1. 실시간 H2/시장 보조/동적 비중의 필요한 관측을 동일0B 수신 owner의 상태·입력에 대응시킨다. 현재 초봉 rows를 이름만 바꿔 직접0B라고 부르지 않는다. 연구 쪽은 분봉 기준으로 정의한다.
2. 같은 판단 의미의 적용 가능 범위와 미정 조건을 명시하고 기존0B 최신가격/보호·기존 분봉 연구를 유지한다. 서로 다른 해상도의 결과를 동일 입력 재현이라고 표기하지 않는다.
3. 초봉 consumer 전환 뒤 생산·정기/종료 저장·재시도·retention owner·진단 계약을 함께 조정한다. 현재 `_prune_old_state`는 `drain_dirty`에서 호출되고 dirty 행은 보존되므로 저장/flush 호출만 빼면 정리·메모리 수명이 달라진다. source reset/인증 전환/종료 pending도 검증해야 한다.
4. 기존 초봉 자료·백업·과거 결과를 보존하고 새 입력으로 과거 결과의 ID/지문을 덮어쓰지 않는다. 관련 회귀 후 실제 NAS source/설정/운영 검증을 별도로 한다.

현재는 **연결 조사와 사용자 입력 기준 문서화까지 완료**다. 초봉 의존성을 제거하는 제품 수정, NAS 테스트·배포, 집계/저장 중단, 자료 삭제는 하지 않았다.

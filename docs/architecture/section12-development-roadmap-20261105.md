# 12절 개발과 라이브러리 개선 로드맵

**2026-11-05 · 사용자 승인 · DD와 MICRO 추가.** 기본 저장소 `main`, 착수 HEAD `c1e42a294c3294bb6ce648983d359ea3dbc5b614`를 기준으로 한다. 목표는 돌파·종가베팅·신규주·낙주와 추가한 일봉 과대낙폭·VI/호가 초단기의 개발 가능한 요구를 제품과 격리 검증까지 연결하고, 라이브러리와 구조 변경의 효과를 같은 입력으로 판정하는 것이다. 운영 전환은 별도 작업으로 남긴다.

전체 단계와 완료 기준은 이 문서 한 곳에서 관리한다. **S12-01 → S12-02 → S12-03 → S12-04 → S12-05** 순서로 통합한다. 각 단계는 실행할 코드, 직접 관련 검사, 검증 기록과 로컬 커밋을 남긴다. [정본 12절](app-target-20261023.md#12-연구노트-기반-네-운용군의-목표-설계) · [기능별 계약](r3-ownership.md) · [남은 작업](../OPEN_ITEMS.md) · [저장소 전체 로드맵](../../FUTURE_DEVELOPMENT_ROADMAP.md).

**현재 상태.** S12-01~04 수락 · 전체4/5 · S12-05 대표3·4·5·7·9,5/11 개발 수락. 낙주 P0/P1의 명시 READY/UI/API→기존 자동 Episode→현재 조건 재판단·정정 우선·첫0B 보존·부분/늦은 체결·당일 미완료/익일 복구·일지를 Windows115/실제PG21건으로 확인했다. 다음은 기존 대표1·2·6·8·10·11과 DD/MICRO·독립 계좌/원주문 검증이다. 운영 제외와 원래 완료 기준을 유지한다. [항목5 수락](#s12-acceptance-panic-five) · [대표 잔여](#s12-acceptance-remaining).

## 1. 착수 기준과 선행 작업

- `main`은 추적 중인 `origin/main`보다 로컬 커밋 2개가 앞선다. 인계 시점의 작업 트리는 깨끗했다. 이 로드맵 요청 전에 수행한 Windows 서버 RSS 변경, 익일 보유 회귀검사 및 관련 기록은 미커밋 작업으로 보존한다. S12-01에서 대상 파일과 지문을 대조하고 해당 단계에서 수락한다. 별도 검증 작업 트리와 연구 브랜치, `docs/research/` 원본은 기존 근거로 보존한다.
- 현재 개발 환경은 Windows 11 AMD64, Python 3.13.15, SQLite 3.50.4다. 저장소 `src`가 실제 import 위치다. 기존 `.venv`에 psutil 7.2.2, PySide6 6.11.1, FastAPI 0.141.1, psycopg 3.3.6, NumPy 2.3.5, pandas 3.0.5가 있다. DuckDB, Polars, PyArrow, Optuna, pyahocorasick, pyqtgraph, platformdirs는 없다. PostgreSQL 드라이버가 있으며 이번 조사에서 별도 테스트 서버의 가동을 확인하지는 않았다. Windows 환경 결과와 macOS/Linux 또는 패키징 수락은 따로 기록한다. [역할별 의존성](../../pyproject.toml) · [고정 목록](../../requirements.lock.txt).
- 선행 RSS 비교에서 기존 Windows 서버 측정 50회는 모두 `None`이었다. psutil은 같은 프로세스의 양수 working set을 반환했고, 기존 연구의 native 측정은 정상이며 더 빨랐다. 따라서 선행 소스는 Windows 서버 진단만 교체했다. 기존 돌파 시험 명세로 마감 보유 → 새 lease → 익일 보호 청산을 확인했다. 미확정 SELL의 익일 대사와 늦은 FILL은 별도 임시 검사 1건에서 통과했으며 정규 회귀 등록은 S12-03에서 판단한다. 선행 관련 6모듈 163건은 이 소스의 근거로만 사용한다. [RSS 비교](opensource-assessment-20261030.md#windows-server-rss-psutil) · [격리 실행](../../tmp/psutil-rss-c-20261105/targeted-final/run.json) · [익일 대사 실험](../../tmp/psutil-rss-c-20261105/verify-unknown-next-session.log).
- AR 기준은 검증 완료 83, 재발 시 이관 1, 운영 전환에 묶인 잔여 6이다. C24의 기존 CI/NAS 증거는 판본에 묶어서 인용한다. C16은 같은 종료 오류가 재발할 때만 조사한다. 새 제품 개발은 AR의 종결·운영 수락과 별도로 집계한다. [AR 정본 0절](app-target-20261023.md#0-현재-기준과-진행-현황).

## 2. 개발 대상과 현재 코드의 지원 범위

| 영역 | 현재 확인한 구현 | 이번 개발 범위 |
|---|---|---|
| 돌파 | **부분 구현.** 명시 주도주·가격 구조와 0B 가격 교차, 완료 1분봉 연구, Shadow, 고정 수량 LIMIT 모의 진입과 보유분 청산·보호가 연결돼 있다. 진입 문맥이 만료돼도 체결 포지션은 고정 보호 근거를 쓴다. 과거 완료봉 돌파·눌림은 별도 비교 Family다. | 당시 가용 근거로 후보와 구조를 구성하고, 확인형과 선취매형의 차이, 최초·추가 물량, 축소·무효화·새 setup 재진입을 명시한 연구 명세와 제품 흐름에 연결한다. 기존 frozen Family와 과거 결과를 보존한다. [판단](../../src/kiwoom_monitor/application/leader_breakout.py) · [Family](../../src/kiwoom_monitor/application/research_families.py). |
| 종가베팅 | **공통 수명만 부분 검증.** 기존 명세의 익일 보유, 단일 포지션 재시작, 사후 분류가 있다. 마감 setup과 익일 반응을 소유하는 실행 Family는 없다. | 일반·신규주 마감 setup, 다음 거래 세션의 갭·회복·축소/청산, 연속 종가베팅의 새 계획을 구현한다. 휴장·특별 세션·정지·미확인 입력을 분리하고, 같은 날 미체결/미확정과 실제 보유를 구분한다. [일정](../../src/kiwoom_monitor/application/market_session_schedule.py) · [사후 분류](../../src/kiwoom_monitor/application/trade_setup_classification.py). |
| 신규주 | **전략·프로필 미구현.** 사후 분류는 일봉 단서로 신규주를 분류할 수 있고 누락 가능성을 표시한다. 과거 자료의 `stocks.reg_day`와 공시 효력일 어댑터가 있으나 당시 상장 유형·단계와 거래 가능 조건을 내는 전략 입력은 없다. | 상장일·유형·당시 가용 근거의 출처와 판본을 연결한다. 상장 첫날/이후, IPO와 명시 지원 유형, 신규주 돌파·신규주 종가베팅 두 조합을 검증한다. 각 조합은 별도 전략 판본을 사용하며 같은 계획을 여러 화면에서 중복 생성하지 않는다. 불충분한 장기 이력은 미확인으로 유지한다. [상장·공시 자료](../HISTORICAL_BACKFILL_PLAN.md) · [운용군 연구 명세](../research/mimosa-capture-study-20261030/06_자동매매_전략명세.md). |
| 낙주 | **전략 미구현.** 장중 급락·반등에 해당하는 사후 분류와 공통 가격 관측은 있다. 장중 전용 진입·부분 체결·반등 관리·당일 종결의 실행 Family는 없다. | 급락 중 분할 접근과 매수 유입 확인형을 연구 판본으로 구분한다. 실제 체결분만 청산에 귀속하고 잔여 취소·늦은 체결·당일 미완료를 복구한다. DD와 계획 판본·보유 수명을 분리한다. [백테스트 연구 명세](../research/mimosa-capture-study-20261030/07_백테스트_연구설계.md). |
| 일봉 과대낙폭(DD) | **전략 미구현.** 일봉 조회와 기존 보유·복구 기술이 있다. 일봉 과대낙폭의 근거·분할 예산·다일 보유를 소유하는 Family는 없다. | 완결 일봉·가용 시점·변동 대비 낙폭·재료 지속 여부로 연구 판본을 만든다. 같은 총계획 예산에서 일회/분할 접근을 비교하고, 보유한 거래 세션 수·반등/실패·갭·미청산을 재현한다. 장중 낙주의 당일 종결 규칙을 자동 적용하지 않는다. [EX-DD 명세](../research/mimosa-capture-study-20261030/07_백테스트_연구설계.md#ex-dd-상대-낙폭과-재료-존속을-구분하면-반등-매매가-개선되는가). |
| VI·호가 초단기(MICRO) | **전략 미구현.** 기존 공식 실시간 입력과 VI 수신·기록 경계가 있다. VI 사건·해제 후 관측과 호가·체결의 순서를 결합해 검증한 전략 경로는 없다. | 먼저 VI와 호가 기반 가설을 별도 판본으로 나누고, 승인된 원본 체결·호가·VI의 출처와 순서·거래 가능 상태·전송 지연·가격/수량 기준을 연결한다. 원본이 부족한 경우 관측·실증 지원 범위를 명시한다. 단순 가격 접촉으로 체결을 확정하거나 봉으로 호가 순서를 재구성하지 않는다. [EX-MICRO 명세](../research/mimosa-capture-study-20261030/07_백테스트_연구설계.md#ex-micro-vi호가초단기-매매의-실행-가능성). |
| 공통 시장·뉴스·세션 입력 | **부분 구현.** 기존 Collector의 승인된 0B, native 분봉·정정, TOP20 시장/테마·1분/5분 구분, 뉴스 RULE·본문/기사 판본과 보정 시계가 있다. 조회순위가 가격·대금 선도를 대신할 수 없다. 상장/PIT 세션, 행동별 입력 대응과 계획별 갱신 연결이 부족하다. | 기존 수집·판본·RAM을 재사용한다. 필요한 봉/자료의 가용시각, venue·단위·모집단·완결과 gap을 명시한다. 행동별 요구와 자료 변경→영향 계획을 연결한다. 뉴스·호가·수급은 해당 변형이 요구하는 경우에만 사용한다. [Collector](../../src/kiwoom_monitor/central_server/realtime_collector.py) · [시장 계산](../../src/kiwoom_monitor/application/market_research_features.py) · [Shadow 입력](../../src/kiwoom_monitor/central_server/market_theme_shadow_inputs.py). |
| 계획·계좌·체결 | **계좌 안전 구현, 다계획 미구현.** Supervisor가 계좌당 Runner 하나와 현재 명세를 관리한다. Episode는 해당 run의 FILL을 한 전략 상태에 반영한다. 체크포인트에 단일 포지션/대기 intent와 고정 보호 근거가 있고, OrderIntent에는 다계획 물량 귀속이 없다. | 기존 계좌 owner 안에서 계획 ID·판본·결정 ID·최초/추가 물량·예약·미확정·복구를 분리한다. 현재 판본 수락과 중복 방지를 native 쓰기 경계에 연결하고, 실제 FILL로만 보유·손익을 갱신한다. 동일 종목 계획의 노출/우선순위는 명시 정책으로 검증한다. [Supervisor](../../src/kiwoom_monitor/central_server/mock_automation_supervisor.py) · [Episode](../../src/kiwoom_monitor/central_server/mock_automation_episode.py) · [주문 계약](../../src/kiwoom_monitor/domain/order_contract.py). |
| 연구·성과·실시간 대응 | **부분 구현.** 동결 자료, 시점 재생, 단일 포지션 Paper 엔진, 제한 탐색/캠페인, final·결과 수락·일부 0B 대응이 있다. 다전략·동일 자금 평가, 사건군 분할, 네 방식별 결과의 공통 전달 경로가 부족하다. | 고정 연구 자료와 명세·비용·체결 의미를 묶고 다변수·동일계좌 가상 평가, 시장/상장 단계 층화, MFE/MAE·익일 갭·미체결·무거래·비용 결과를 연결한다. 승인된 근거로 연구→Shadow를 검증하고 후보 수락과 실행 권한을 분리한다. [재생](../../src/kiwoom_monitor/application/research_replay.py) · [Paper](../../src/kiwoom_monitor/application/research_execution.py) · [탐색](../../src/kiwoom_monitor/application/research_search.py). |
| 저장·표시·역할·수명 | **부분 구현.** native SQLite/PG 저장과 수락/ACK, 연구 결과 원장, Qt 설정 saved/applied, 후보·일지 조회와 headless 역할 경계가 있다. 네 방식이 설정→계획→결과/복구까지 같은 ID로 연결되지 않는다. | 공개 설정/판본, 연구·Shadow·계좌의 지원 수준, 근거·실제 보유·미체결·복구를 UI/API/일지에서 같은 ID로 대조한다. 이전 자료 조회, 실제 코드 부재, 종료와 재시작을 검증한다. [DB 스키마](../../DB_SCHEMA.md) · [API 계약](../../API_CONTRACT.md). |

12절 목표의 완결 흐름은 현재 **0/4**다. 기존 돌파의 제한된 연결과 공통 관측·체결·복구는 구현 출발점으로 활용한다.

12절의 연구 기준인 r003은 186개 보고서, 52개 원칙, 30개 사례군, 34개 쟁점, 8개 실험 설계를 제공한다. 기본 운용군 EX-BR/CLOSE/IPO/PANIC에 EX-DD/MICRO를 추가하고 EX-TH/RISK를 공통 비교로 묶는다. 실증은 지지/비지지·판정 유보·자료 부족·설계 무효를 구분한다. 원문 숫자, 독립 확인되지 않은 사례와 사후 분류는 실제 기본 설정이나 승인으로 이관하지 않는다. [r003 연구·진행 현황](../research/mimosa-capture-study-20261030/08_연구진행현황.md) · [12.11 미정 정책](app-target-20261023.md#1211-미정-매매운용-정책).

### 구조 변경의 우선 판단

1. **계획과 계좌 실행을 분리한다.** 현재 단일 명세에 결합된 관측·전략·체결 투영을 같은 계좌 owner 안에서 계획별로 분리한다. 전략마다 계좌 Runner나 lease를 만들면 동일 잔고·예약·보호의 권한이 겹친다. `central_execution_intents/events/account_snapshots/runtime_leases`의 수락/동시성 계약을 재사용하고, 필요한 계획 관계와 수량 귀속만 native 저장에서 설계한다. 전용 행과 기존 문서의 확장 중 실제 조회·원자성·복구·이전 비용을 비교한다.
2. **입력과 판단을 연결하고 중복 계산을 줄인다.** 현재 0B Collector, native 분봉/revision, 시장·테마 계산과 조립 owner를 재사용한다. 같은 cut/판본/단위에서만 공유하고 정정·만료·source 전환은 실제 의존 계획만 갱신한다. 초봉/독립 수집기/전략별 전체 재스캔을 새 공통 요구로 두지 않는다. 실제 교차·손절·주문/FILL과 최신 Risk/control 확인은 기존 순서대로 소비한다.
3. **연구와 실시간은 규칙을 공유하고 각자의 수락을 유지한다.** 동결 봉과 승인된 실시간 입력은 가용시각·해상도·체결 표현을 따로 다룬다. Paper의 비용과 주문 결과, 실제 FILL 및 계좌 상태, UI 표시의 소유자를 명시한다. 범용 백테스트 엔진으로 계좌 권한·미확정 복구를 옮기는 설계는 비교 근거가 생길 때만 재검토한다. [12.14 계산 계약](app-target-20261023.md#strategy-compute-efficiency) · [실행·Risk 계약](r3-ownership.md#execution).

### S12-01 입력 공급과 행동의 대응

아래 표는 S12-01의 개발 입력과 결측 시 처리 범위다. 승인된 실시간 관측은 그 수신·출처·연속성까지 고정한다. 격리 자료의 수치와 증거는 운영 정책을 구성하지 않는다.

| 입력군 | 공급·시점·판본과 소비자 | 행동별 지원과 미확인 처리 |
|---|---|---|
| 가격·체결 | 기존 `realtime_collector`의 승인된 종목/거래소 0B·source generation·수신 순서 → Shadow 및 기존 실행 입력. 1h는 `market_events`의 별도 원본/수락 경계다. | 돌파 교차, 낙주 급락/반등, 종가베팅·신규주·DD의 진입 이후 반응 및 보호. MICRO는 VI 종류별 원시 시각과 공통 순서가 입증된 체결/호가가 필요하다. 현행 1h 해제 `effective_at`과 DB 수락 순번으로 교차 스트림 순서를 만들지 않는다. 호가 깊이/해제가/순서 미확인은 의존 변형의 해당 신규 행동을 제한한다. |
| 봉·정정·구조 | native 분봉 writer/revision → 고정 watermark export → `research_data_source`와 `research_replay`. 시장·종목·단위·완결·가용시각·판본을 유지한다. 현재 일봉은 `ka10081` 최신 행과 metadata/coverage다. | 돌파의 이전 고가, 종가베팅의 마감 구조, 신규주의 짧은 이력, 낙주의 반응, DD의 이전 변동성과 완결 일봉 setup을 구분한다. 최신 일봉 조회를 과거 당시 판본으로 간주하지 않는다. DD의 불변 일봉 입력 경계와 수정주가/실제 가격의 대응이 필요하다. 결측 이력·정정·부분봉은 0이나 이전 정상 판본으로 바꾸지 않는다. |
| 시장·후보 맥락 | TOP20 구성/조회순위와 1분 가격·대금, 5분 지속을 기존 `market_research_features`와 RAM Shadow 소유자가 각각 제공한다. 출처·분모·거래소·판본을 남긴다. | 돌파 주도성, 종가베팅 관심, 신규주 모집단, 낙주 유동성, DD 동종 비교, MICRO 체결/호가의 범위를 각각 정의한다. TOP20 밖을 전 시장에서 관측했다고 표시하지 않는다. 같은 의미의 입력을 공유하고 다른 모집단/해상도를 합치지 않는다. |
| 뉴스·테마 | `news_jobs`와 기존 기사/BODY/RULE·테마 판본의 최초 확인/가용시각과 처리 버전, 재구성 상태를 유지한다. | 필요한 진입 가설에만 사용한다. 종가베팅·DD의 재료 유지/훼손/불명, 낙주의 악재, 신규주 관련 근거를 구분한다. 문맥 미확인을 호재/무악재로 처리하거나 기존 보유 보호를 중단하지 않는다. |
| 거래일·상장 | 고정 해시의 거래일·상장 입력 → `market_session_schedule`, `market_data_contract`, 명시 증거 기반 `research_replay`. 판본 가용시각과 KST 날짜를 따로 보존한다. | 종가베팅·DD는 확인된 다음 거래일을, 신규주는 당시 보고된 상장 유형·날짜를 사용한다. 돌파·낙주·MICRO도 해당 세션/거래소를 확인한다. 미확인 날짜를 평일로 채우거나 누락된 일봉을 상장일로 해석하지 않는다. 상장 단계는 거래 정지·호가 지원의 증명이 아니다. |
| 계좌·위험 | 실제 FILL·원주문·예약은 native 실행 원장, 수락 전 최신 계좌·Risk/control과 lease는 기존 실행 owner가 소유한다. 연구는 고정 명세와 합성 잔고/체결을 쓴다. | 네 기본 운용군과 DD/MICRO 모두 진입과 보호의 입력/권한을 분리한다. 가격 관측으로 체결·잔고를 만들지 않는다. 늦은 체결·미확정 예약과 익일 상태는 최신 계좌 대사까지 보존한다. 계획별 귀속과 수명 구현은 S12-03에서 검증한다. |

분봉 비교 기준 자료는 실제 격리 SQLite 저장 → 고정 export → 기존 분봉 재생으로 만든 합성 판본 22,807개다. 일반/집중 조건은 동일 자료의 고정 477/7,597개 재생 프레임을 사용한다. 고정 해시, 입력 원장, 중복/정정·가용시각·결측 및 거래소 경계의 독립 기대값은 [자료 근거](../../tmp/s12-01-frozen-baseline/fixture/evidence.json)에 보존한다. S12-02에서 현재 reader와 후보의 CPU·시간·메모리를 같은 입력으로 비교한다.

기존 JSONL의 첫 실행/반복 실행 시간 중앙값은 일반 352.3/345.3ms, 집중 479.2/472.4ms다. 20회 모두 재생 해시가 일치했고, 호출당 프로세스 읽기 약 30.48MB와 샘플 최대 RSS 중앙값 약 240MiB를 관측했다. 첫 실행은 새 프로세스의 앱 import 이후이며 OS 파일 캐시를 통제하지 않았다. [현행 비용 측정](../../tmp/s12-01-frozen-baseline/benchmark-base.json).

S12-01에서는 명시 해시로 고정한 별도 거래일·상장 근거를 읽고, 기존 재생 함수에 전달한 경우 분봉의 원래 가용시각에 보였던 세션만 선택하도록 연결했다. 기본 연구 작업의 데이터셋과 기존 세션 해석은 기존 경로를 사용한다. 새 전략에서 해당 근거와 행위 정책을 묶는 연결은 S12-04에 남긴다. 격리 관련 회귀 242건에는 새 검사 12건과 기존 분봉·과거 재생·돌파·RSS 경로가 포함된다. [실행 근거](../../tmp/s12-01-references/targeted-final/run.json).

## 3. 실행 순서와 단계별 완료 기준

아래 파일은 실제 소유자와 연결 지점이다. 단계 내에서 수정 대상은 호출 경로와 재현 결과로 좁힌다. 관련 변경 없는 기존 경계는 이전 검증을 소스와 계약이 일치하는 범위에서 재사용한다.

| ID · 선행 | 구현·구조 대상 | 검증과 단계 완료 기준 |
|---|---|---|
| **S12-01 공통 입력·연구 기준선** · 로드맵 승인 | `realtime_collector.py`, `market_theme_shadow_inputs.py`, `market_research_features.py`, `market_session_schedule.py`, `research_replay.py`, `research_data_source.py`, 현재 native 분봉/revision·뉴스/상장 reader. 0B/봉/뉴스·테마/세션·상장/계좌의 가용·판본·상태를 동결 입력과 행동별 지원표에 연결한다. 누락된 PIT 상장·다음 세션 공급을 기존 reader·격리 자료로 준비한다. 라이브러리 비교 입력과 현행 비용을 함께 고정한다. | **4운용군과 DD/MICRO 입력표, 6입력군**, 모든 행에 출처·판본·가용·실패 의미와 실제 소비자. 누락/미래 판본/부분·정정/시간대·source 교체/세션 경계를 재생해 미래 정보 사용·오인용 0. 0B callback의 새 DB·네트워크 호출 0. 동일 의미의 계산은 한 번, 서로 다른 입력은 별도 실행. `test_market_research_features`, `test_central_minute_bars`, `test_market_session_schedule`, `test_research_replay`의 영향 검사. |
| **S12-02 라이브러리 비교·병목 개선** · S12-01 동결 표본 | 아래 7영역을 대조한다. 비용의 원인이 직접 구현·I/O·중복 계산 중 어디인지 판별한 뒤 기존 표준/라이브러리 재사용·native 개선·후보 도입을 같은 범위에서 비교한다. Windows RSS 선행 변경은 `resource_usage.py` → 인증 API → 기존 설정 화면, 역할 pin·고지까지 검토한다. 채택한 부분은 실제 caller에 연결하고 원복·의존 역할을 기록한다. | **7영역 전부** 유지/부분 채택/대체 판정과 근거. 실험 대상은 고정 입력의 정확성 대조 후 동일 호스트에서 cold/warm·보통/집중 조건으로 각 **5회 이상** 교차 실행한다. 변경마다 목표 지표의 재현 가능한 이득 또는 확인된 기능 결함 해결, 안전/결과 불일치 0. 제품 도입 0건도 근거가 있으면 유효하다. |
| **S12-03 계획·계좌·주문 수명** · S12-01, S12-02의 계약 중 해당 부분 | `domain/strategy_contract.py`, `domain/order_contract.py`, `mock_automation_supervisor.py`, `mock_automation_episode.py`, `execution_runtime.py`, `order_lifecycle.py`, `execution_repository.py`, `database_execution.py`, 필요 시 `central_schema.py`/`schema_migrations.py`. 기존 단일 계좌 owner 안에 계획별 수락, 물량/예약, FILL 귀속, 재시작·익일·미확정 주문 복구와 신규 진입/보호의 분리를 구현한다. 새 계획 판본은 구형 명세의 권한을 자동 승계하지 않는다. | 실제 **격리 SQLite와 PostgreSQL**에서 빈 DB와 필요한 구형 사본 경로를 각각 검증한다. 계획 2개가 같은 계좌/종목을 사용해도 미귀속/수동분 분리와 실제 합계가 일치한다. 같은 결정 재전달·COMMIT 뒤 ACK 손실·겹친 lease·늦은 FILL에서 중복 intent/전송·귀속 차이·초과 매도 **0**. 재시작 전후 주문/수량/FILL cursor/원판본 일치. `test_execution_repository`, `test_order_lifecycle`, `test_mock_automation_runner`, 기존 native PG 실행기와 보호 회귀. |
| **S12-04 네 전략·연구·Shadow** · S12-01~03 | `application/research_families.py`, `leader_breakout.py`, `research_hypotheses.py`, `research_search.py`, `research_execution.py`, `research_evaluation.py`, `research_process.py`, `candidate_monitor.py`, 기존 시장/테마/뉴스 reader와 후보 publication. 돌파 기준 흐름, 종가베팅, 신규주 2조합, 낙주를 같은 실행 계약에서 완결한다. DD는 완결 일봉·다일 수명, MICRO는 VI/호가·체결 순서와 실제 확인 가능한 체결 범위를 연결한다. 입력과 안전 경계가 고정되면 독립 전략 연구를 병렬로 준비할 수 있다. | **4/4 운용군, 신규주 2/2 조합**에 실제 판단과 진입→보유→종결의 합성 경로가 있다. 각 방식에 명시적인 무거래·입력 누락·보호 사례가 있고 Shadow 주문 호출은 **0**. **8/8 직접·공통 실험**이 동결 자료와 실행 가능한 요청·결과를 남긴다. DD의 다일·분할과 MICRO의 순서·미확인 호가 및 체결을 합성 자료에서 각각 검증하고, 실증 자료 제약은 별도 기록한다. 개발/검증/최종보류 사건군 교차 0. 과거 성과는 가용 자료가 있는 시나리오만 별도로 집계하고 손실·무거래·미완료도 결과에 포함한다. `test_leader_breakout`, `test_candidate_monitor`, `test_research_execution`, `test_research_hypotheses`, `test_research_process`를 우선 재사용한다. |
| **S12-05 제품 연결·통합 종결** · S12-01~04 | `research_dialog.py`, `candidate_monitor_dialog.py`, `mock_automation_dialog.py` 및 후보 화면, `operational_settings_routes.py`, `research_read_routes.py`, `journal_enrichment.py`, 기존 remote/client·headless 조립. 선택→저장→적용→판단→계좌 수락→FILL→일지/연구 조회의 판본과 상태를 맞춘다. 비활성·교체·재시작과 구형 결과 소비를 연결한다. 구조/DB/API 문서와 증분 검사 원장을 갱신한다. | 정본 **12.12의 11/11 시나리오**에 독립 기대값과 실행 근거. DD의 다일 보유/분할 복구와 MICRO의 지연·순서/호가 결측 두 추가 경계를 별도 검증한다. 조회 역할은 판단 코드 부재에서도 작동하고, headless 역할은 Qt 메인 화면 없이 기동·종료한다. 저장과 적용, 신호와 보유, 기존/새 판본이 화면·API·일지에서 일치. 영향을 받은 자동 검사 100% 통과, 미실행/skip/timeout/expected failure는 별도 미수락. 구조·증분 검사와 작업자 종료, 단계별 로컬 커밋 및 깨끗한 Git 작업 상태를 확인한다. |

연구·모의 개발에는 명시적인 연구 정책과 합성 실행 조건을 사용한다. 전략 진입 임계값·총손실 허용·공동 계좌의 실제 우선순위처럼 미정인 값은 판본과 근거를 표시하고, 당시 입력이 부족하면 영향을 받는 행동만 제한한다. 기본값 없는 독립 경로를 구현하고 실제 정책 선택은 분리한다. 합성 시나리오만 있는 전략은 제품 개발·검증 수준과 실증 수준을 각각 표시한다.

## 4. 라이브러리 비교 범위와 판정

| 비교 영역 | 현행과 후보 | 동일 입력에서 확인할 것과 적용 경계 |
|---|---|---|
| 백테스트 엔진·다변수 | 기존 Paper·시간순 replay·제한 grid 대 Backtesting.py 엔진의 의미 대조, 탐색 후보 Optuna ask/tell. | 엔진은 가용 판본·다음 실제 봉·원화 비용·부분체결·익일·미확정·모호성 결과를 대표 사례에서 비교한다. 엔진 전체 교체는 이 조건의 정확 대조와 배포/라이선스 이점이 있을 때만 재평가한다. Optuna는 동일 후보열로 평가 adapter를 먼저 대조한 뒤 동일 예산·seed·개발 구간에서 탐색 효율을 비교한다. 평가·commit·권한은 기존 owner가 맡는다. [Backtesting.py API](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html) · [Optuna ask/tell](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/009_ask_and_tell.html). |
| 시계열 처리·분석 | 기존 Python·SQLite 범위 조회/재생 대 Polars lazy 또는 기존 NumPy 배열 경로. pandas는 설치돼 있으나 현재 개발 경로의 의존성으로 승격할 이유를 먼저 검증한다. | 동결 자료의 정렬·동률·timezone·null·정정 순서, 정수/Decimal, cold import와 변환 비용을 포함한다. 후보는 배치 연구 reader/집계에 한정해 비교하고 0B callback으로 배치 프레임을 들여오지 않는다. [Polars lazy](https://docs.pola.rs/user-guide/lazy/using/) · [NumPy 정렬·검색](https://numpy.org/doc/stable/reference/routines.sort.html). |
| 시장 데이터 가공·집계 | 현재 bounded Python/기존 native 집계 대 현행 책임에서의 입력 공유·증분 갱신, 기존 NumPy 또는 이미 비교하는 Polars의 좁은 계산. | TOP20 범위/거래소, 1분과 5분, 완결·보완·분 경계·누적 대금 단위, 동시에 도착한 종목/정정의 결정을 대조한다. 정렬·스캔·cache 적중·p95·RSS·SQL 호출과 변환 비용을 비교한다. 밀린 0B·손절/교차 관측의 누락 0을 먼저 확인한다. |
| 뉴스 규칙·분류 | 현행 `news_rules.py`/정규식과 `news_grouping.py` 대 pyahocorasick의 고정 문자열 후보 매칭. | 동일 한국어 본문·RULE판본으로 매칭 위치/순서·중첩·경계·제외·분류·그룹 ID와 downstream AI 대상/저장을 비교한다. 판본별 사전 구성·메모리·갱신 비용을 포함한다. 정규식 문맥과 업무 규칙은 계속 검증한다. 후보는 문자 매칭이 병목일 때만 실행 실험한다. [pyahocorasick 문서](https://pyahocorasick.readthedocs.io/en/latest/). |
| 차트·시각화 | `top20_trade_value.py` 및 `journal_chart_widget.py`의 Qt paint와 기존 계산·캐시 대 PyQtGraph의 선택적 렌더링. | 병목이 관측된 화면 하나에서 같은 봉/막대·표식·한국어·확대/축소·교차선·마우스 위치/시간, 빈 자료와 대량 입력을 재생한다. 프레임 시간/CPU·peak RSS·초기 기동·패키징 비용을 대조한다. 화면 전체 교체 전에 현 Qt의 작은 수정 효과를 확인한다. [PyQtGraph 0.14.0](https://pypi.org/project/pyqtgraph/0.14.0/) · [고정판 렌더링 API](https://pyqtgraph.readthedocs.io/en/pyqtgraph-0.14.0/api_reference/graphicsItems/plotdataitem.html). |
| SQLite·PostgreSQL·DuckDB | 기존 SQLite·PG native row store와 범위 조회/배치 대 DuckDB의 동결 분석 reader, 필요 시 같은 고정 산출물의 Parquet. | 분봉 revision 한 묶음의 `database_research_export.py` → 자료 loader → replay를 우선 비교한다. projection·row ordering/hash·시점 결과를 맞추고 준비/변환/정리 포함 wall·CPU·RSS·I/O·출력 크기를 잰다. DuckDB에서 Parquet를 준비할 수 있으면 이 실험에 PyArrow를 추가할 필요가 없다. SQLite/PG native 쓰기 변경은 COMMIT/ACK/claim/rollback/동시성으로 별도 판정한다. 주문 원장·lease의 분석 DB 이관은 범위 밖이다. [SQLite WAL](https://www.sqlite.org/wal.html) · [PostgreSQL 격리](https://www.postgresql.org/docs/current/transaction-iso.html) · [DuckDB Parquet](https://duckdb.org/docs/2.0/data/parquet/overview#partial-reading) · [OS 지원](https://duckdb.org/docs/2.0/operations_manual/installing_duckdb/requirements). |
| 복잡한 범용 경계 | psutil RSS 선행 변경과 기존 native 측정, 기존 `AppPaths` 대 platformdirs, 필요 시 이미 설치된 서버 Pydantic 재사용. | RSS/결측/cold 비용과 역할별 배포물을 대조한다. 경로 후보는 기존 Windows 경로·명시 root/checkout·3OS 기본 경로의 비교에만 쓴다. 설정 파싱은 저장·적용·null/오류·구형 판본이 같은 경우에만 좁게 정리한다. process identity/handle, 복구·자료 경로 이동은 각각 기존 안전 경계에서 검증한다. [기존 OSS 조사](opensource-assessment-20261030.md) · [psutil 고정판](https://pypi.org/project/psutil/7.2.2/). |

비교표는 각 후보의 **요구 적합성, wall/CPU·p50/p95, peak RSS/추가 시작 비용, 라이선스·의존 패키지, Windows/macOS/Linux 및 필요한 아키텍처 지원, 통합·원복 비용, 유지보수 면적**을 함께 기록한다. 버전·wheel/hash·빌드 환경을 고정하고 공식 라이선스/배포 파일을 확인한다. 구조가 단순한 현행을 유지해도 결과와 판단 근거를 남긴다. 작은 배치나 적용 경계의 동일성이 성립하지 않으면 무리한 실행 실험 대신 비교 불가 이유와 대조 근거를 적는다.

실행 우선순위는 **Windows RSS 선행 자료 검토 → 분봉 revision 연구 조회의 한정 대조 → 나머지 확인된 병목**이다. 현행 Paper와 Backtesting.py는 시점·체결 의미와 AGPL 조건 때문에 우선 코드/계약 비교로 시작한다. PyQtGraph는 PySide6를 재사용하지만 NumPy 등을 GUI 역할에 포함시키는 비용을 측정한다. 현재 NumPy/pandas 설치는 전체 환경의 OCR 의존성을 포함하며 조회 역할의 직접 사용 근거가 되지 않는다. DuckDB는 포장 대상 OS/아키텍처별 지원과 spill까지 확인한다. 메모리 옵션을 프로세스 전체 RSS 한도로 해석하지 않는다. [Backtesting.py 라이선스](https://github.com/kernc/backtesting.py/blob/master/LICENSE.md) · [PyQtGraph 고정 의존성](https://raw.githubusercontent.com/pyqtgraph/pyqtgraph/pyqtgraph-0.14.0/setup.py) · [DuckDB 자원 제한](https://duckdb.org/docs/current/operations_manual/limits).

동일 의미의 결과는 ID·시각·상태·순서·수량·원화 단위·hash까지 대조한다. 의도적으로 달라지는 결과는 새 판본과 독립 기대값으로 검증한다. 후보 도입은 측정 변동보다 큰 반복 가능한 이득 또는 확인된 기능 결함 해결과 함께, 수용 가능한 메모리/호출·통합 비용이 근거로 남는 경우에 한한다. 매 틱 DB/I/O 추가, 주문 안전 경계 약화, 과거 결과의 무단 재계산, 지원 역할 밖 의존성 확장은 채택 조건에 포함할 수 없다.

### S12-02 비교 판정과 근거

| 영역 | 제품 판정과 재개 조건 |
|---|---|
| 백테스트·다변수 | **기존 Paper와 제한 grid 유지.** 같은 시각의 관측 판본·다음 실제 분봉·갭 미체결·비용·봉 내부 손익 범위의 현행 계약을 정확 ID 10건으로 확인했다. Backtesting.py의 기본 다음 행 체결 의미와 라이선스는 현재 대체에 필요한 이점보다 통합 부담이 크다. Optuna는 탐색 공간 확대 근거가 있을 때 동일 개발 자료·예산·seed로 sampler만 대조한다. |
| 시계열 처리·분석 | **기존 Python 재생과 현행 의존성 유지.** 데이터 변환과 전체 검증이 드는 연구 reader는 아래 고정 자료에서 비교한다. Polars는 동질 배열의 별도 배치가 병목으로 확인될 때 같은 가용시각·정정·원화 자료로 검토한다. NumPy/pandas가 전체 개발 환경에 설치된 사실은 조회 역할에 의존성을 넣는 근거가 되지 않는다. |
| 시장 가공·집계 | **기존 Python 계산 개선.** 정렬한 승인 0B를 종목·두 관측 창으로 한 번 분류해 종목마다 전체 관측을 다시 훑는 경로를 제거했다. 7쌍의 반복에서 결과와 증거 순서가 모두 같았다. 80건은 0.464→0.277ms, 8,000건은 37.933→20.056ms로 중앙값이 줄었다. 같은 프로세스의 순수 계산 측정이며 장중 응답 시간이나 주문 경로의 개선율은 별도다. [동일 입력 측정](../../tmp/s12-02-market/paired-market.json). |
| 뉴스 RULE·분류 | **기존 matcher/RULE/그룹 유지.** BODY·근거·요약·regex 문맥/금액·최근 후보 그룹을 하나의 문자열 matcher로 대체할 수 없다. 기존 분류 표본은 `assess_stock_news` 중심이고 전체 RULE 처리 시간에서 literal 매칭이 병목이라는 근거가 없다. 고정 RULE 판본의 실제 phase 비용이 확보될 때만 pyahocorasick의 리터럴 부분을 비교한다. [뉴스 경계](../../src/kiwoom_monitor/central_server/news_jobs.py). |
| 차트·시각화 | **기존 Qt 유지.** R3-081에서 유지한 이동평균·시각 변환 캐시를 소스에서 확인했다. 같은 과거 표본의 마지막 렌더 변경은 CPU 이점이 없어 철회됐다. TOP20의 현재 화면에서 보이는 범위의 병목을 재현할 때 작은 데이터 준비 개선과 PyQtGraph 한 화면을 비교한다. [기존 측정과 판정](../../REFACTOR_BACKLOG.md#r3-081--차트-paint의-전체-원본-ma좌표-재계산). |
| SQLite·PostgreSQL·DuckDB | **원장 SQLite/PG와 JSONL reader 유지.** 같은 동결 판본 22,807건의 행 순서·내용 및 두 재생 결과를 DuckDB 2.0.0의 Parquet 경로와 대조했다. 결과 해시 40/40이 일치했다. DuckDB는 디스크 크기 30.48→2.11MB와 샘플 RSS 약 22~27MiB 절감을 보였고, 전체 검증/읽기·재생의 벽시간은 20쌍 모두 더 길었다. Parquet 저장/운반의 절감 효과는 장후 보관 별도 범위의 후보로 기록한다. [같은 입력 비교](../../tmp/s12-02-duckdb/comparison.json). |
| 범용 경계 | **Windows 서버 진단에 psutil 적용.** 같은 프로세스에서 기존 Windows 서버 RSS 누락을 재현한 후 교체했고, 연구 native 측정은 정상이며 더 빨랐다. platformdirs는 자료 경로·복구·명시 역할 입력의 제품 계약을 해결할 근거가 생길 때 검토한다. 현재 settings API의 기존 Pydantic 검증을 활용하고 설정 owner를 추가하지 않는다. [RSS 원측정](opensource-assessment-20261030.md#windows-server-rss-psutil). |

시장 계산의 별도 Python 할당 측정에서 최대값은 80건 36,319→37,453바이트, 8,000건 2,690,316→2,642,316바이트였다. 전체 프로세스 RSS와는 별도의 지표다. 현재 소스의 영향 검사 6모듈 413건이 통과했고, 같은 초에 들어온 여러 종목의 원래 수신 순서를 확인하는 회귀를 추가했다. [할당 측정](../../tmp/s12-02-market/paired-market-allocations.json) · [격리 회귀](../../tmp/s12-02-market/targeted/run.json).

## 5. 위험과 대응

| 위험 | 대응과 확인 |
|---|---|
| 미래 자료나 사후 분류가 과거 판단에 섞임 | 발생·수신·가용·판단·정정과 판본을 각각 기록한다. 같은 cut에서 최신 가용 판본을 선택한 뒤 품질을 검증한다. 동일 사건군/보유 기간 중첩을 분리하고 final을 반복 탐색에 사용하지 않는다. |
| 두 계획의 동시 노출, 손절·ACK·재시작 손실 | 계좌 단일 owner, fresh Risk/control/lease, durable-before-submit, native CAS/claim, original intent/FILL을 보존한다. 부분 체결·늦은 ACK·취소 경합·재시작을 양 DB에서 재현한다. 중대한 안전 결함은 영향을 받는 단계만 중단하고 재현과 선택지를 보고한다. |
| 세션·상장·기존 판본의 의미가 바뀜 | 실제 거래일 근거와 달력 참조, KRX/NXT·거래 정지/미확인을 구분한다. 정책·DB 변경은 버전과 구형 자료 reader, 빈 DB/선별 이전 사본, 저장 실패·재시작 원복 경계를 함께 검사한다. 원본·기존 연구 판본·실제 체결은 보존한다. |
| 수치가 빨라 보이지만 실시간 비용이 증가함 | 변환·대기·복사·잠금·쓰기·cold import를 포함해 원인별로 비교한다. 정상과 burst replay에서 값/순서/보호 사건이 같아야 하며 느린 후보는 철회한다. |
| 후보 패키지 설치/플랫폼 또는 격리 PG가 확보되지 않음 | 공식 배포물과 기존 도구를 먼저 확인한다. 신규 의존성은 비교용 격리 환경과 해당 역할만 사용한다. 실행 증거를 얻지 못하면 사유를 기록하고 기존 경로를 유지한다. 변경한 native 경계의 필수 PostgreSQL 검증은 환경 미확보로 남기며 SQLite 성공으로 대체하지 않는다. |
| 기존 미완료나 부수 결함으로 범위가 바뀜 | 작업은 이 문서의 ID로 연결한다. R3-085는 모의 재시작·대사에 직접 닿는 경계만 S12-03에 포함한다. 실제 키/컴퓨터 인계는 별도다. 기타 부수 문제는 `OPEN_ITEMS`의 기존 ID 또는 보류 목록에 연결하고 우선순위를 바꾸지 않는다. |

## 6. 승인 이후 운영 규칙과 전체 완료

- 사용자의 **최초 1회 승인과 DD/MICRO 추가 지시**에 따라 S12-01~05의 구현·구조 변경, 위 후보의 격리 비교, 문서에 정한 조건을 충족한 라이브러리의 해당 역할 소스 반영과 단계별 로컬 커밋을 진행한다. 기존 개발 환경은 그대로 사용한다. 비교에 추가 패키지가 필요하면 버전·출처를 고정한 별도 로컬 환경에만 준비한다. 새로운 후보군, 지원 범위나 완료 기준의 중대한 변경은 별도 검토한다.
- 단계마다 **현행 확인 → 구현안 확정 → 코드 → 관련 검사 → 결과 대조 → 정본/F·증분 원장 갱신 → 로컬 커밋**을 수행한다. 독립 읽기·검사·후보 실험은 격리할 수 있다. 동일 상태/저장/수명 변경과 기본 저장소 통합은 순차로 진행한다. 선택 회귀와 정확 ID/종료를 기록하고 전수 재실행을 자동으로 대체하지 않는다.
- **개발 범위**는 가짜 브로커, 격리 SQLite/PG, 합성 입력, 허용된 동결 과거 자료, 별도 설치물/환경 검증이다. 운영 서비스·자료를 사용하지 않는 로컬 실패/복구·전환 시나리오도 포함한다.
- **운영 제외**는 실제 매매·실계좌 주문 및 실제 계좌 인증으로 하는 주문 시험, NAS 운영 자료 변경·배포, 운영 서비스 정지/재시작, 운영 데이터 삭제·이전, 실제 컴퓨터/키 간 계좌 권한 인계, T0 확정/실행, GitHub push·새 hosted CI 실행·외부 공개다. 실제 거래소 세션과 모의 브로커 체결 품질, 사용자 운영 자금/손실/우선순위, 설치·NAS·장중 수락은 별도 목록으로 남긴다.
- 전체 완료율은 **완료된 단계 수 / 5**로 표시하고, 각 단계는 표의 필수 조건을 모두 확인할 때만 완료로 센다. **4운용군과 2개 신규주 조합, DD/MICRO 2유형, 8개 실험, 7영역 라이브러리 판정, 11개 정본 시나리오와 2개 추가 검증 경계**를 각각 별도 집계한다. 필수 환경·입력·정책을 기다리는 항목은 이유와 재개 조건을 명시한다. 최종 보고에서 실제 구현/변경 파급, 비교 성능/RSS, 채택 의존성, 검사 ID·소스, 단계별 커밋, 운영 전환 잔여를 분리한다.

**승인 기록.** 2026-11-05에 사용자가 다섯 단계의 로드맵을 승인했고, 일봉 과대낙폭과 VI·호가 초단기도 같은 범위에 포함하도록 후속 지시했다. 추가 실험은 기존 계좌·저장·운영 안전 경계에서 진행한다.

<a id="s12-03-native-order-work"></a>

### S12-03 주문 원장 작업 기록

기존 모의 계좌 lease와 주문 저장 트랜잭션에 명시적 계획 판본·예약·FILL 귀속을 연결했다. 구형 주문 객체와 저장 표현은 기존 형식을 사용한다. 격리 SQLite·PostgreSQL의 같은 표본에서 같은 종목 두 계획의 예약, COMMIT 응답 유실 시 중복 전송 방지, 익일 새 lease에서 늦은 FILL 귀속, 보유량을 넘는 매도 거절, 두 계획의 매도 이후 수동 보유 1주 보존을 확인했다. 전송 전 취소·만료는 예약을 해제하며 제출 상태가 미확정이면 계속 대사한다.

최종 소스 `0c51666c`에서 Windows 9모듈 480건과 PostgreSQL 17.11의 주문 원장·권한·구형 복구·새 계획 14건이 통과했다. 구조 검사와 증분 원장의 실행 대조가 통과했고 PG 격리 프로세스와 포트가 종료됐다. 앞선 병렬 Windows 시도의 제한 시간 초과는 통과 건수에서 제외했다. [Windows 회귀](../../tmp/s12-03-first-review/ledger-final/run.json) · [PostgreSQL 회귀](../../tmp/s12-03-pg-baseline/planned-native-bootstrap/postgres-run.json) · [격리 종료](../../tmp/s12-03-pg-baseline/planned-native-bootstrap/owned-job.json) · [증분 검증](../../tmp/s12-03-first-review/review-execution-final.json).

두 매수는 계좌가 비어 있을 때 같은 승인 명세에서 미리 수락한 합성 표본이다. 보유 중 새 매수와 여러 운용군의 정책·보호 연결은 S12-04에서 판단한다. 새 전략 권한 승계와 운영 계좌의 미귀속 보유 대사는 운영 전환의 별도 경계다.

### S12-04 연구 입력 연결 작업 기록

- 기존 분봉 단일 연구 요청은 선택한 거래일 근거 파일의 SHA256을 실행 ID에 묶고, 분봉의 원래 가용시각별로 달력 판본을 선택한다. 같은 자료에서 정정 전 관측은 남고 정정 이후 관측만 제외되는지, 자료 변조와 출력 경로 충돌을 거부하는지 확인했다. 직접 관련 연구 회귀 36건이 통과했다. [연구 검사](../../tmp/s12-04-pinned-replay/research-module.log).
- DD의 일봉 수집은 명시적인 `ka10081` 요청에서 가용시각·완결·수정주가 모드를 불변 판본으로 남긴다. SQLite와 격리 PostgreSQL에서 재수신·정정·고정 export 및 실패 롤백을 확인했다. [PostgreSQL 검사](../../tmp/s12-03-pg-baseline/s12-04-daily-first/postgres-run.json).
- MICRO의 VI 원시 시각 해석은 발동·해제의 서로 다른 FID를 사용하고 원본 영수증을 보존한다. 결측·늦은 근거와 과거 보완 자료를 분리했다. VI 모듈 10건이 통과했다. 실시간 전략 소비자와 실제 호가·체결 교차 순서의 입증은 남아 있다. [VI 검사](../../tmp/s12-04-micro-vi-clock/test-market-events-native.log).

<a id="s12-04-frozen-strategy-experiments"></a>

### S12-04 동결 자료의 전략 연구 실행

기존 연구 명령의 `single_run`으로 `breakout_study/v1`, `theme_leader_study/v1`, `close_betting/v1`, `ipo_breakout/v1`, `ipo_close/v1`, `panic_rebound/v1`을 실행한다. `krx-regular/v1`, 해시를 고정한 `strategy_references`, `completed_close_then_next_open/v1`과 `no_intrabar_fill/v1`을 명시한다. 거래일·상장·뉴스·테마는 각 판단 시점의 증거를 적용한다. 완료봉에서 제안하고 확인된 후속 시가와 유효 비용을 쓰는 모형이며, 갭으로 초기 위험거리가 사라진 돌파는 체결을 평가하지 않는다. `shadow_only: true`를 선택한 동결 재생은 판단과 후보를 보존하며 주문·체결 실행기를 호출하지 않는다.

`daily_decline_experiment`, `micro_experiment`, `account_risk_experiment`는 `dataset` JSON 파일, `dataset_sha256`, `policy`, `database`, `runs_dir`를 명시한다. 자료·정책·구현 지문을 연구 실행 ID에 묶고, 상세 결과는 실행별 `manifest.json`에 보존한다. DD는 완료 일봉·시장 대비 낙폭·거래일·원주가 보호를 연결하고, MICRO는 순서가 입증된 VI·호가·체결과 명시 영수증을 사용한다. 계좌 위험 비교는 같은 총예산 아래 예약 경쟁과 체결 귀속을 기록한다. 자세한 요청과 기대 결과는 기존 연구 명령의 연결 검사로 재현할 수 있다. [분봉·합성 연구 요청 검사](../../tests/unit/test_research_process.py).

이 시점에는 8개 실험군의 합성 요청을 실행할 수 있었고 S12-04와 S12-05의 수락은 보류했다. 후속 수락 범위와 남은 계좌 연결은 [S12-04 완료 근거](#s12-04-stage-acceptance)와 [12.12 잔여 조건](#s12-acceptance-remaining)에 기록한다.

현재 소스 `2c3bcae3`에서 Windows 25모듈 633건과 격리 PostgreSQL 12건이 통과했다. 기존 Shadow 198건과 주도주 돌파 86건, 새 동결 Shadow의 주문 호출 0건을 포함한다. 구조와 증분 원장 대조를 통과했고 PostgreSQL 임시 프로세스와 포트 종료를 확인했다. [직접 회귀 542건](../../tmp/s12-04-strategy-wiring/related/run.json) · [기존 소비자 91건](../../tmp/s12-04-strategy-wiring/prior-dialog-fixtures/run.json) · [PostgreSQL 12건](../../tmp/s12-03-pg-baseline/s12-04-native-research/postgres-run.json) · [증분 실행 대조](../../tmp/s12-04-strategy-wiring/review-execution-final.json).

<a id="s12-04-breakout-filters-and-account-valuation"></a>

### S12-04 돌파 조건 비교와 계좌 가격 평가

- `breakout_filter_comparison` 요청은 `breakout_study/v1`의 B0 `strategy`와 B1·B2·B3 `breakout_variants`를 받는다. 한 개발 구간의 동결 관측과 비용·예산·수량·종료 조건을 공유한다. 직전 판본에 새 조건만 더하고, 같은 종목·신호 판본을 기준으로 공통 후보와 회피 손실·놓친 이익을 비교한다. B1은 가격 진행·종목 상대 대금, B2는 같은 시점·범위의 시장 상대 대금, B3는 당시 확인된 일봉 여백 또는 재료를 추가한다. 완료 분봉의 확정 가격·대금은 사후 수집된 자료도 사용할 수 있다. 복원 연구의 봉 종료 시각은 실제 수신 시각과 구분해 실행 기록에 표시한다.
- `account_risk_experiment`는 명시 `MARK`의 원시 관측시각·가용시각·가격과 상세 체결을 사용해 평가액과 최대 낙폭을 계산한다. 표시 가격·체결에 공백이 있으면 확인된 시점 사이의 낙폭 하한을 제시한다. 계좌에 예약된 현금은 평가액에서 중복 차감하지 않는다. 기간 전체 성과와 주어진 관측 시점에서의 결과를 구분한다.
- 월별 로컬 재구성 자료에서 학습 12일 181,696개 분봉과 검증 4일 63,213개 분봉의 파일·판본 해시를 대조했다. 양쪽의 종목-거래일과 분봉 판본은 겹치지 않았고, 후보군 문맥 판본 1건은 공유됐다. 최종보류 자료는 봉인 메타데이터만 확인했다. 이 표본의 분봉은 모두 사후 복원본이다. 장중 수신 지연·정정 순서나 당시 시장·재료까지 비교하려면 해당 관측의 별도 근거가 필요하다. [로컬 자료 감사](../../tmp/s12-04-comparisons/historical-input-audit.json) · [분봉 자료 출처](../../tmp/s12-04-comparisons/breakout-input-eligibility.json).
- 별도의 로컬 개발 표본인 2024-12-26 학습 2,239봉과 2025-05-30 검증 2,661봉에서 B0~B3를 각각 실행했다. B0와 B1 모두 각 구간에서 후보를 생성했다. B2·B3는 시장 상대 대금 근거가 없어 `breakout_market_turnover_unavailable`을 남겼다. 사후 선정 후보군, 봉 종료 시각 대리값, 진입 범위 점검용 비용 0원 조건이므로 장중 실행 품질이나 비용 후 성과와 구분한다. [로컬 재생 결과](../../tmp/s12-04-comparisons/historical-pair/summary.json).
- 기존 `CandidateMonitor`는 완료봉 관측과 체크포인트를 소유하고, 모의 Runner는 후보 수락·새 승인·계좌 소유권·최신 Risk를 거친다. Section 12 정책과 근거를 이 경계에 연결하고, 보유 후 순서 있는 0B 보호와 승인된 실행을 별도 확인하는 작업이 남아 있다.

현재 소스 `6e4e99b7`에서 Windows 16모듈 408건이 통과했다. 새 비교의 공통 신호 대응, 로컬 복원 재생, 평가 공백의 낙폭 하한, 기존 Shadow 및 연구 화면 회귀를 확인했다. 구조와 증분 원장 실행 대조를 함께 확인했다. [연구·화면 210건](../../tmp/s12-04-comparisons/final-research/run.json) · [기존 Shadow 198건](../../tmp/s12-04-comparisons/final-shadow/run.json) · [증분 대조](../../tmp/s12-04-comparisons/review-execution.json).

<a id="s12-04-native-shadow-and-journal"></a>

### S12-04 완료봉 Shadow와 S12-05 연구·일지의 부분 연결

| 범위 | 연결 | 남은 검증 또는 재개 조건 |
|---|---|---|
| 돌파·테마·종가베팅·신규주 두 조합·낙주 | 기존 설정에서 `section12-minute-shadow/v1`을 명시 선택한다. 실시간 KRX 마감 봉과 당시 확보한 문맥을 기존 관측 원장에서 읽는다. TOP20 또는 명시 종목을 선택하고 후보 유효시간을 입력한다. 후보가 판단 판본과 근거 ID를 보존한다. | B2/B3·TH·PANIC의 시장·테마·뉴스 및 CLOSE/IPO의 거래일·상장 근거는 명시 판본을 받는다. 실시간 공급·수락과 전략별 승인 모의 실행은 이어서 검증한다. |
| 계획 주문·보호 | 0B 주도주에 명시 계획을 전달했을 때 격리 SQLite/PG가 고정 증거를 수락과 전송 전에 검사한다. 실제 모의 원장 경로에서 최초 체결과 재시작 뒤 보호를 확인했다. | 제품의 명시 승인 설정에서 해당 계획을 선택하는 연결, 추가 물량·특수 보호·부분 체결/취소 경합 및 나머지 전략의 판본별 승인과 복구가 필요하다. |
| DD·MICRO | DD 일봉 셋업은 거래일 증거와 최신 가용 판본을 검사한다. MICRO는 기존 수집기의 0B와 별도 VI 저장에서 확인된 근거 및 결측을 구분한다. | DD는 수정주가 기준·벤치마크·원주가 보호와 체결별 비용 판본이 필요하다. MICRO는 호가 깊이·거래 가능 상태·공통 순서·해제 체결가가 필요하다. 현 자료에서 MICRO 후보는 `UNKNOWN`이다. |
| 연구 화면·모의 일지 | 연구 화면은 전략과 실행 범위, 동결 자료 및 시계 근거를 표시한다. 일지는 기존에 확인된 NAS 모의 계좌에서만 상세 체결과 원주문의 계획 참조를 대조한다. v12는 기존 projection에 nullable 참조만 추가한다. | 12.12의 각 시나리오에서 설정→실행→체결→일지/연구가 같은 판본을 유지하는지 연결한다. 새 설치와 선별 이전의 통합 검증을 이어간다. |

로컬 개발용 6개 TRAIN·VALIDATION 자료의 281,323행을 대조했다. 657개 종가 단일가 행은 같은 종목의 확인된 다음 개장과 연결되지 않았고, 26일의 테마 자료는 날짜별 관측 합집합이었다. 이전 B0/B1 결과의 완료봉 가격·대금은 사용할 수 있다. 당시 시장·뉴스·테마와 호가/체결 순서를 요구하는 조건은 확보한 근거를 별도로 판정한다. [자료 감사](../../tmp/s12-04-local-data-audit/evidence.json) · [12.12 검증 대응](../../tmp/s12-05-verification-matrix/matrix.json) · [격리 PostgreSQL 계획·일지](../../tmp/s12-03-pg-baseline/s12-04-planned-leader-parity/postgres-run.json).

현재 소스 `93339ef6`에서 Windows 변경·직접 영향 32건, 같은 파일의 기존 검토 사례 363건, 공용 검사 자료의 소비자 53건, 격리 PostgreSQL 13건이 통과했다. 총 461건과 구조·증분 대조를 확인했다. 판단 모듈을 물리적으로 제거한 서버에서 연구 결과 수신·저장도 확인했다. 앞선 26모듈 997건 실행은 응답 기준표 두 검사와 조회 전용 구성 한 검사가 실패한 이전 소스의 기록이다. 기준표 두 검사는 변경 전 `defe6bb`에서도 실패해, 기존 S12 계획 원장의 진단 범위와 일봉 내보내기 종류만 반영했다. 조회 전용 서버는 판단 모듈 없이 태그를 읽을 수 있게 고쳤다. [변경·직접 영향](../../tmp/s12-04-shadow/current-exact-v1/evidence.json) · [기존 검토](../../tmp/s12-04-shadow/prior-reviewed-exact-v1/evidence.json) · [공용 검사 자료](../../tmp/s12-04-shadow/prior-support-exact-v1/evidence.json) · [격리 PostgreSQL](../../tmp/s12-03-pg-baseline/s12-04-native-shadow-journal-current/postgres-run.json) · [증분 대조](../../tmp/s12-04-shadow/current-review-v3.json) · [변경 전 응답 검사](../../tmp/s12-04-shadow/baseline-defe6bb/cases.log).

별도 빈 설치·선별 설치 표본은 순위와 명시 설정·수동 테마·미완료 저장을 이전하고 재기동 뒤 최초 영수증을 복구했다. 미확정 주문과 계좌 소유권 복구는 별도다. 계획형 부분 체결 표본은 취소 응답 유실 뒤 새 실행 소유자가 취소를 재전송하지 않고 늦은 FILL을 대사했다. 최초 보호 근거와 최신 계획 잔량을 함께 검증해 매도하는 실제 제품 경로는 남아 있다. [설치 검증](../../tmp/s12-05-selective-install/run.json) · [부분 체결 재현](../../tmp/s12-planned-leader-product-design/prove_partial_protection.py).

<a id="s12-protection-lineage-and-retention"></a>

### S12-04 계획 보호와 S12-05 사건 분할·선별 보존

- 계획형 0B는 최초 손절 시점의 증거와 현재 계획 잔량·가격을 구분한다. 합성 격리 경로에서 취소 응답 유실 후 늦은 상세 FILL을 복구하고, 같은 계획의 2주 보호 주문을 한 번 전송했다. 취소 전에 전량 체결된 경우 상세 체결이 모두 대사된 뒤 3주를 한 번 전송한다. 누적 수량만 확인된 상태, 다른 계획, 계획을 뺀 일반 청산은 전송 전에 거절한다. 일반 실행 명세의 활성화와 후속 보호 주문은 남아 있다.
- 완료봉 Shadow의 활성 상태는 후보를 만든 봉과 앞선 교차 확인의 판본을 체크포인트에서 복구한다. 정정이 오면 기존 이벤트를 변경하지 않고 활성 표시를 해제한다. B2/B3와 TH는 명시한 시장·일봉·재료·테마가 판단 컷에 확인된 경우에만 해당 후보를 만든다. 실시간 자동 공급과 부족한 실증 자료는 별도다.
- Section 12의 개발 `single_run`은 선택 `evaluation.source_event_links`에 검토한 사건 ID, 출처 사례 참조, 동결 관측 판본 ID를 받는다. 명시한 동일 사건의 가용 구간은 하나의 학습 또는 검증 구간 안에 있어야 한다. 최종평가 표시가 있는 원본이나 묶음 하위 자료는 관측 파일을 열기 전에 거절한다. 이 판정 범위는 한 요청 안에서 사용자가 명시한 연결이다.
- `daily_decline_experiment`는 `dd-native-setup-evidence/v1` 설명 파일의 SHA256과 동결 매니페스트 SHA256을 각각 확인한다. 동결한 KRX `ka10081` 일봉, 거래일, 수정주가 기준과 시장 벤치마크를 사용해 셋업 상태를 표시한다. 합성 거래 결과와 분리하며 원주가 보호, 체결별 비용 및 실제 분할 실행은 별도다.
- MICRO용 오프라인 해석기는 0B의 선택 최우선 호가·보고 거래소와 0D의 가격·수량·호가 시각을 분리한다. 예상 체결가는 체결가와 구분하며, 거래소가 불명확한 응답에 임의의 장소·교차 자료 순서를 부여하지 않는다. 실시간 호가 공급, 종목 거래 가능 상태와 VI 해제 체결가는 별도 검증이 필요하다. [키움 공식 실시간 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/_data/kiwoom_api_spec.json#L97852).
- 빈 설치와 선별 설치는 순위·현재 설정·미완료 저장의 최초 영수증을 각각 검증한다. 종료한 합성 계좌의 미확정 주문 한 건은 원본 이벤트 ID·커서·계획 판본과 함께 `OFFLINE_RECOVERY_REQUIRED`로 보존한다. 같은 증거의 재시도는 최초 영수증을 반환하고 대상의 주문·승인·lease를 만들지 않는다. 실제 복구에는 신규 소유자의 계좌·브로커 재대사와 명시 관리 승인 절차가 필요하다.

현재 소스 `0524cfaf`에서 변경 및 영향 Windows 384건과 격리 PostgreSQL 15건이 통과했다. PostgreSQL에서 늦은 상세 체결을 복구하는 두 경로를 확인했고 임시 작업자와 연결을 종료했다. [Windows 실행](../../tmp/s12-04-shadow/protection-lineage-exact-v1/evidence.json) · [PostgreSQL 실행](../../tmp/s12-03-pg-baseline/s12-04-protection-lineage-current/postgres-run.json) · [증분 대조](../../tmp/s12-04-shadow/protection-lineage-review.json).

<a id="s12-refreshed-cancel-and-offline-command"></a>

### S12-04 최신 계좌 대사의 계획 보호와 S12-05 오프라인 명령

- 기존 계좌 갱신에서 최신 Risk 판본으로 바뀌면 현재 판본과 최초 손절 근거를 함께 사용해 같은 계획의 취소 요청을 만든다. 갱신 전 판본은 거절하고 이미 수락한 요청을 재시도할 때는 재조회·재전송을 생략한다. 취소 응답 유실 이후 전량 체결도 같은 취소 기록과 늦은 상세 체결을 확인한 경우에만 잔량을 보호한다. 실제 실행 명세의 선택 연결, 미체결 원주문 만료와 이후 보호는 별도 작업이다.
- 가짜 WebSocket의 0D 메시지는 등록 승인 뒤 정확한 종목·거래소 범위에서만 수신하고 해제 대기 때는 차단한다. 0B는 이 세 구간에서 모두 전달됐다. 증거는 수신 메시지의 순서이며 실제 운영 구독은 별도다.
- 오프라인 증거는 `python -m scripts.preserve_selected_order_evidence`의 `capture`, `accept`, `inspect`로 다룬다. `capture`는 중지 확인된 원본과 선택한 계좌·실행·주문을 읽어 새 패킷 파일과 SHA256을 만든다. `accept`는 원본 파일 해시와 대상 범위를 검증해 기존 오프라인 SQLite에 수락한다. `inspect`는 최초 영수증을 읽는다. 기존 파일 덮어쓰기와 주문 권한 승계는 제외한다.

현재 소스 `c7ce8060`에서 변경 및 영향 Windows 246건과 격리 PostgreSQL 16건이 통과했다. 새 모의 경로는 계좌 갱신으로 바뀐 Risk의 귀속, 취소 재요청 시 재조회·재전송 0, 늦은 전량 체결 뒤 계획 잔량만 한 번 보호하는 것을 대조했다. [Windows 실행](../../tmp/s12-04-shadow/refreshed-cancel-exact-v1/evidence.json) · [PostgreSQL 실행](../../tmp/s12-03-pg-baseline/s12-04-refreshed-cancel-current/postgres-run.json) · [증분 대조](../../tmp/s12-04-shadow/refreshed-cancel-review.json).

<a id="s12-expiry-and-reconstructed-market"></a>

### S12-04 미체결 만료와 돌파 복원 시장비율 · S12-05 화면 연결

- 최신 계좌 조회와 원래 승인·판단·정책이 일치하면 승인된 대기 종료 뒤 같은 미체결 매수를 취소한다. 종료 이전, 바꾼 만료시각, 다른 주문 또는 체결 근거가 있는 경우에는 이 요청을 수락하지 않는다. 취소 응답 유실 후 기존 대사를 통해 늦은 상세 체결을 같은 계획의 보유로 남기며, 만료 자체에는 보호 매도 권한이 없다.
- B2/B3 연구는 `reconstructed_market_proxy`에 고정한 SHA256과 개발 분할을 대조한다. U001과 U201을 독립 실행하며 각 지수의 현재 분 원값을 과거 20거래일 동일 분의 중앙값과 비교한다. 4개 지수·분할 조합에서 각각 380개 대상 분과 7,600개 기준 분이 있고, 분봉 전체 일합계와 일봉 원값은 84 지수·거래일에서 일치했다. 벤더 도움말은 9번 필드의 거래대금 단위를 확정하지 않으므로 동일 공급자 원단위 상대값으로 기록했다. 거래소 범위가 같다는 가정으로 지수 합산을 하지 않았으며, 이 비교는 재구성 봉 종료시각을 사용한다. B0/B1, 당시 가용 시장 근거 및 최종 보류 자료는 기존 의미를 유지한다. [동결 목록](../../tmp/s12-04-b2-index-proxy/frozen/manifest.json) · [대신증권 StockChart 도움말](https://money2.daishin.com/e5/mboard/ptype_basic/HTS_Plus_Helper/DW_Basic_Read_Page.aspx?boardseq=284&m=9508&p=8839&page=1&searchString=StockChart&seq=102&v=8642).
- 후보 화면은 적용된 전략·종목 범위·판단 시각·자료 결측을 표시한다. 연구 화면은 복원 시장비율의 범위와 SHA256, 변형별 실행 ID와 후보 수를 표시하고 Paper 범위를 구분한다. 원본 수정, 실제 주문, 전환 및 배포는 운영 범위에 남는다.

| 분할 | 범위 | B0 | B1 | B2 | B3 | B1·B2 공통 / B1만 / B2만 |
|---|---|---:|---:|---:|---:|---:|
| 훈련 2024-12-26 | U001 | 3 | 5 | 1 | 0 | 0 / 4 / 1 |
| 검증 2025-05-30 | U001 | 2 | 2 | 2 | 0 | 2 / 0 / 0 |
| 훈련 2024-12-26 | U201 | 3 | 5 | 1 | 0 | 1 / 3 / 0 |
| 검증 2025-05-30 | U201 | 2 | 2 | 5 | 0 | 0 / 2 / 5 |

네 실행 모두 B2/B3 시장 근거 결측은 0건이었다. B3의 재료 근거 결측은 표 순서대로 11·42·32·6건이었고 B3 후보는 0건이었다. 후보 수는 각 전략의 보유 상태와 재진입 기회에 따라 달라진다. 이 비교는 사후 선정된 두 거래일의 무비용 점검이므로 장중 실행 품질·비용 후 성과는 별도로 확인한다. [비교 실행과 SHA256](../../tmp/s12-04-b2-index-proxy/comparison-verified/summary.json).

현재 소스 `955b096c`에서 Windows 310건과 격리 PostgreSQL 18건이 통과했다. Windows 최초 실행의 180초 한도에 걸린 한 묶음 211건만 별도 실행해 통과했고, 먼저 끝난 99건과 동일 소스임을 대조했다. 구조 검사와 증분 실행 대조를 통과했다. [Windows 합산 근거](../../tmp/s12-04-shadow/expiry-market-proxy-exact-combined.json) · [PostgreSQL 실행](../../tmp/s12-03-pg-baseline/s12-04-expiry-market-proxy-current/postgres-run.json) · [증분 대조](../../tmp/s12-04-shadow/expiry-market-proxy-review.json).

<a id="s12-protection-readback-and-micro"></a>

### S12-04 늦은 체결 보호와 S12-05 MICRO·복구 통합

- 손절을 만든 0B 원본과 계산 결과를 한 쌍으로 잡는다. 보호 기록 선택 전에 세대가 바뀐 입력은 버리고, 이미 저장한 손절 근거는 원본을 재계산해 확인한다. 미체결 만료 취소 뒤 부분 또는 전량 체결된 경우, 브로커 최종 상태와 상세 체결을 Risk에 반영한 후의 새로운 손절과 별도 최신 0B 가격이 있어야 최초 보호 매도를 한 번 수락한다.
- 중지하고 재시작하면 기존 보호 의도·승인·계획 수량을 확인한다. 같은 원장 관측 판본에는 새 계획 조회를 반복하지 않는다. 주문번호가 연결된 늦은 매도 체결만 계획 잔량을 줄인다. 접수 응답 유실로 주문번호가 없는 보호 매도는 예약을 유지하며 다른 주문의 상세 체결을 붙이거나 재전송하지 않는다.
- 관리자 조회 `GET /api/v1/research/micro-inputs`는 종목·거래소·변형을 받아 기존 거래일·VI 기록과 단일 종목 RAM을 읽는다. `BOOK_SCALP`와 거래일 근거가 없는 NXT는 KRX VI 자료를 읽지 않는다. 후보와 주문 권한은 없으며 확인한 입력과 네 종류의 부족 근거를 응답한다. 기존 수집 범위와 유지시간을 사용한다.
- 신규주 돌파·종가베팅 두 `AFTER_LISTING_DAY` 연구는 보유 중 상장일과 유형이 정정돼도 최초 진입 단계·상장 판본·분봉·의도 ID가 유지됐다. 두 조합의 Paper 매도량은 각각 4주와 2주+2주였다. 같은 모의 계좌의 계획 매수는 최초 영수증 COMMIT 응답 유실과 키 교체 뒤에도 기존 주문·체결·커서를 유지했다. 재시도에서 최초 영수증을 받았고 전송은 한 번, 새 소유자는 한 명이었다. 신규 진입은 명시 재개 전까지 닫혀 있다.
- DD 관리자 조회는 이미 저장한 KRX 일봉의 확인 가능한 창에서 셋업을 평가하고 자료 범위·정정·결측을 응답한다. 거래일·수정주가 기준·벤치마크 등은 호출자가 제공한 근거로 표시한다. DD·MICRO·RISK 완료 결과는 명시 요청에서 기존 결과 전용 인증으로 원본을 보낼 수 있다. 수신 영수증은 과학적 검증이나 주문 승인을 부여하지 않는다.
- 연구는 해당 컷 이후 정보를 쓰지 않는다. 당시 이미 알 수 있었던 일봉·재료·테마를 복원했다면 `reconstructed_as_of`로 표시해 일반 Paper에서 사용한다. 컷 이후와 시점 미확인 자료는 판단에서 제외하고 다른 확인 자료로 가능한 연구를 계속한다. 일부 분에만 존재하는 복원 시장비율은 그 분만 평가한다. 결과에 전체 시장 분 중 근거가 있는 분 수를 표시하며, 다른 전략과 실시간 판단·주문 기준은 해당 근거 계약을 따른다.

U001의 실제 훈련 자료에서 시장 근거를 09:01~10:30의 90분으로 제한한 재생을 완료했다. 결과는 `90/380분`과 B0/B1/B2/B3 후보 `3/5/0/0`을 남겼다. 나머지 분의 시장 근거 부재를 연구 실패나 확인된 시장 조건 미충족으로 해석하지 않는다. [부분 자료 재생](../../tmp/s12-04-b2-index-proxy/partial-current-u001-train/result.json).

합성 연구는 4/4 운용군의 판단·진입·보유·종결, 2/2 신규주 조합, BR·CLOSE·IPO·PANIC·DD·MICRO·TH·RISK 8/8의 실행 요청과 저장 결과를 확인했다. 정본 12.12의 제품 승인·복구 수락과 실제 자료 사건군 검증은 아래 조건을 따른다.

저장소 연결 감사에는 DD 일봉 1개, MICRO 거래일·VI 2개, 보호 원장 조회 2개의 호출 지점과 기존 호출의 함수 이동만 반영했다. SQLite의 선별 주문 증거 수락 서명을 추가했으며 전달 관계는 기존 감사 기준을 유지한다. [검토 차이](../../tmp/s12-04-shadow/protection-readback-consumer-delta.json).

모의운용 화면 검사의 공용 자료는 같은 테스트 모듈에서만 사용됨을 확인했다. 과거 통합용 선택에서 이미 폐기된 내용 동기화 검사 두 건과 직접 소비 관계가 없는 검사 묶음을 현재 선택에서 분리했다. 최초의 넓은 실행은 감사 차이·폐기된 검사 ID·시간 초과를 포함한 별도 기록이다. [초기 실행 기록](../../tmp/s12-04-shadow/protection-readback-and-research-v2/evidence.json).

최종 소스 `45bfe0fc`에서 Windows 407건과 격리 PostgreSQL 22건이 통과했다. 새 DD·MICRO 조회의 인증·실제 저장소 연결, 원본 연구 결과 전달과 조회 전용 역할, 보호 매도 대사·화면, 후보·복원 문맥의 가용 시각을 확인했다. 구조·저장소 호출·증분 원장 대조를 통과했다. [Windows](../../tmp/s12-04-shadow/protection-readback-research-final/evidence.json) · [PostgreSQL](../../tmp/s12-03-pg-baseline/s12-04-protection-readback-research-final/postgres-run.json) · [증분 대조](../../tmp/s12-04-shadow/protection-readback-research-final-review.json).

<a id="s12-native-plan-runner"></a>

### S12-04 계획형 0B 모의 실행 · S12-05 명세·후보 복구

- 계획형 모의운용은 사용자가 지정한 정책 파일과 `leader-0b-native-plan/v1`을 새 명세에 결합한다. 기존 게시·승인·START·단일 계좌 소유를 거쳐 기존 0B 실행기에서 최초 매수와 같은 계획의 청산을 요청한다. 기존 명세의 선택과 표시도 유지한다.
- 매수 대기 종료 뒤 확인된 원주문의 미체결 잔량만 취소한다. 부분 체결분과 후속 상세 체결분은 같은 계획에서 관리하고, 확인된 종료 후 별도의 손절이 있을 때 계획 잔량을 보호한다. 접수 응답 유실이나 누적 체결만 있는 경우에는 새 주문을 기다린다.
- 기존 명시 재개는 최신 계좌 바인딩·lease·Risk와 대사를 기존 제어 트랜잭션에 결합한다. 보호 검토 저장의 응답 유실과 제어 COMMIT 뒤 응답 유실을 구분하고, 최초 승인과 종료 근거를 복원한다.
- 종가베팅과 신규주의 완료봉 후보는 최초 판단 ID에 묶인 거래일·상장 판본을 기존 후보 원장과 화면에서 확인할 수 있다. 종가베팅은 휴장일을 지난 다음 개장 시각을 보존한다. 이전 후보의 동일 재요청은 원본 내용을 유지한다. 같은 후보에서 실제 주문이나 보유 수량을 계산하지 않는다.

현재 계획의 접수·상세 체결·후속 보호를 확인한다. 보호 가격은 최신 Risk에 포함될 때까지 같은 수신 세대에서 보존한다. 다른 수신 세대나 유효기간이 지난 가격은 버린다. 보유 0인 원주문은 다른 주문의 종료 보고를 사용할 수 없고, 같은 원주문의 취소·최종 확인·계획 판본과 Risk 커서로만 재개할 수 있다. 다른 setup의 신호는 별개로 관측되지만 실제 재진입 기준은 미정이다.

보호 준비 기록의 저장 응답이 유실된 뒤 주문 자체가 생성되지 않았으면 같은 손절 근거와 새 Risk·가격으로 다시 준비한다. 같은 원주문의 계획 판본과 예약이 그대로인지 확인하고 기존 최초 수락 트랜잭션을 사용한다. 전송 상태가 미확정이거나 원장에 주문이 있는 경우에는 대사를 기다린다.

현재 실행 소스 `08d1bdeb`에서 Windows 영향 검사 352건과 격리 PostgreSQL 24건이 통과했다. 앞서 PostgreSQL 모듈 실행에서 33건 중 28건의 개별 성공을 기록했으나 실행 제한 시간에 도달했다. 변경 11건, 완료 기록이 부족한 5건, 증분 원장에서 요구한 기존 검사 8건의 공식 실행 기록을 같은 소스에서 남겼다. 임시 데이터베이스와 실행 작업, 루프백 포트가 정리됐다. [Windows 실행](../../tmp/s12-04-shadow/native-plan-runner-final/evidence.json) · [PostgreSQL 영향 검사](../../tmp/s12-03-pg-baseline/s12-05-native-plan-reviewed-exact/postgres-run.json) · [SQLite·PostgreSQL 대조](../../tmp/s12-03-pg-baseline/s12-05-native-plan-parity-exact/postgres-run.json) · [기존 증분 검사](../../tmp/s12-03-pg-baseline/s12-05-native-plan-reviewed-ledger/postgres-run.json) · [임시 환경 정리](../../tmp/s12-03-pg-baseline/s12-05-native-plan-reviewed-exact/owned-job.json).

12.12의 동일 사건 분할을 고려아연 개발 자료로 재생했다. 거래소 공시에 기록된 2024-10-02 14:09 정지·14:39 재개와 같은 날 30분 정지를 보도한 기사 한 건을 같은 사건으로 묶었다. 동결된 2024-10-04 분봉 381개에는 `historical_case_date=2024-10-02`가 남아 있고 후보 모집단 판본 1개도 같은 사례일을 가리킨다. 원본 TRAIN 파일의 해시와 판본 ID를 대조한 뒤 일반 연구 요청에서 사건 1개가 저장됐다. 같은 묶음을 10월 4일 정오 기준으로 학습·검증에 나눈 대조 요청은 연구 결과 저장 전에 거절됐다. 두 출처는 분할 메타데이터로 사용했다. 공시의 조치 시각을 공개 시각으로 해석하지 않았고 보도의 최초 본문 판본이나 봉 내부 체결 순서도 확정하지 않았다. [거래소 공시](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20241002800242) · [당일 보도](https://www.fnnews.com/news/202410021433567484) · [발행사 당일 설명](https://campaign.koreazinc.co.kr/campaign/%EA%B8%B0%EC%9E%90%ED%9A%8C%EA%B2%AC%EB%AC%B8/) · [판본 및 분할 대조](../../tmp/s12-05-verification-matrix/reviewed-halt-event-v2/evidence.json).

<a id="s12-paper-panic"></a>

### S12-04 낙주 연구의 뉴스 미확인 표본

일반 Paper에서 `strategy.panic_allow_unresolved_news: true`를 선택하면 P0·P1이 컷까지 관측한 가격·대금만으로 뉴스 미확인 표본을 탐색할 수 있다. 뉴스의 확인 수준은 판단 사유와 요청·저장·화면에 표시한다. 당시 확인된 악재와 확인 근거 간 충돌은 진입을 막고 이후에 가용해진 뉴스는 앞선 판단에 사용하지 않는다. 선택을 생략한 연구와 Shadow는 확인된 뉴스 근거를 요구한다.

고려아연의 동결된 2024-10-04 분봉과 복원 정규장 시각에서 P0·P1을 각각 같은 자료와 조건으로 재생했다. 뉴스 확인을 요구한 두 실행에는 후보가 없었다. 미확인 표본을 허용하면 P0는 후보 1건과 Paper 매수·매도 각 1건이 기록됐고 P1은 후보 0건이었다. 사례는 사후 선택된 TRAIN 자료이며 결과는 조건별 연구 경로 점검이다. [한국거래소 매매 안내](https://global.krx.co.kr/contents/GLB/01/0109/0109000000/guide_to_trading_in_the_korean_stock_market.pdf) · [비교 요청과 결과](../../tmp/s12-05-verification-matrix/panic-coverage-main/coverage.json).

현재 소스 `63a3aa5d`의 Windows 영향 검사 62건과 구조·저장소 호출·증분 원장 대조를 통과했다. 동일 분봉의 확인형과 미확인 허용형, 컷 이전·이후 악재, Shadow 요청 거절, 화면과 저장 결과를 대조했다. [정확한 실행 ID와 종료 기록](../../tmp/s12-04-shadow/panic-paper-final/evidence.json) · [증분 대조](../../tmp/s12-04-shadow/panic-paper-final-review.json).

<a id="s12-panic-shadow-readback"></a>

### S12-05 낙주 Shadow의 최초 후보 근거

P1이 재시작 후 확인 봉에서 후보를 내면 최초 급락 봉의 판본·가용시각·확인가, 실패선·목표가를 같은 후보에 보존한다. 조회와 후보 화면에서 이 값을 읽고, 최초 판단을 바꾸는 동일 ID 재저장을 거절한다. 예전 후보는 원본 문서로 조회하고 재전달로 소급 갱신하지 않는다. 부분 체결과 반등 후 잔량 취소가 결합된 계획 주문은 추가 수락 조건으로 남는다.

소스 `f526bbab`의 Windows 영향 검사 89건과 격리 PostgreSQL의 공통 후보 저장·재시작 검사 1건이 통과했다. 낙주 재시작 검사는 최초 급락 봉과 실패선 890원·목표가 945원을 SQLite 재조회와 화면까지 대조했고, PostgreSQL 검사는 같은 저장 계약을 쓰는 종가베팅의 기존 후보 호환성을 확인했다. 구조·저장소 호출·증분 원장 대조도 통과했다. [Windows](../../tmp/s12-04-shadow/panic-shadow-readback-final/evidence.json) · [격리 PostgreSQL](../../tmp/s12-03-pg-baseline/s12-05-panic-shadow-readback-pg/postgres-run.json) · [증분 대조](../../tmp/s12-04-shadow/panic-shadow-readback-final-review.json).

<a id="s12-native-micro-book"></a>

### S12-04 MICRO의 KRX 호가 관측

명시한 종목과 기존 중앙 수집 대상이 겹칠 때 KRX 0D를 같은 WebSocket에서 등록한다. 승인된 종목당 최신 한 건을 수집기에 보관하고, 관리자 조회는 같은 가용 시각의 0B 컷과 함께 요청 범위의 0D를 읽는다. 등록 확인 전·해제 대기 중 메시지는 제외하며, 수집 단절이나 연결 종료 뒤에는 새 관측을 기다린다. 호가 원문 시각과 수집기 가용 시각을 분리하고, 실제 수량 0과 결측 수량을 구분한다. 예정 체결과 실제 체결은 각각의 입력에 둔다. 새 호가는 메모리에서만 사용한다. [관리자 조회 계약](../../API_CONTRACT.md).

소스 `dd7b1f79`의 Windows 영향 검사 62건과 구조·저장소 호출·증분 원장 대조가 통과했다. 가짜 연결로 승인 전 제외, 승인 후 조회, 조회 범위 만료, 단절 후 새 관측, 해제 대기 중 폐기, 0D 등록 거절 후 0B 수신 복구를 확인했다. 관리자 조회는 결측·부분·양쪽 호가 및 0 수량을 구분했다. 실제 거래소에서의 호가 공급과 체결 순서는 별도 수락 조건이다. [영향 검사](../../tmp/s12-04-shadow/micro-native-book-acceptance/evidence.json) · [증분 대조](../../tmp/s12-04-shadow/micro-native-book-acceptance-review.json).

<a id="s12-04-stage-acceptance"></a>

### S12-04 네 전략·연구·Shadow 수락

| 단계 완료 조건 | 현재 검증 |
|---|---|
| 4/4 운용군과 신규주 2/2 조합 | 돌파, 종가베팅, 신규주 돌파·종가베팅, 낙주 P0·P1의 고정 자료에서 후보와 진입·보유·종결을 검증했다. 새 신호 부재·자료 결측·보유 보호를 각 전략에서 확인했다. 신규주는 상장 첫날과 이후를 비교하고 나중에 정정된 상장 근거가 기존 보유를 재분류하지 않는지 대조했다. |
| Shadow 주문 호출 0 | 동일 자료로 돌파·테마, 종가베팅·신규주 두 조합, 낙주 P0·P1의 Shadow 요청을 재생했다. 후보가 생성되고 Paper의 판단·봉 처리 진입점 호출과 원장의 체결·주문 이벤트가 모두 0이었다. 실제 중앙 Shadow의 최초 후보 근거 조회와 재시작은 별도 검사에서 확인했다. |
| 8/8 직접·공통 실험 | BR·CLOSE·IPO·PANIC·DD·MICRO·TH·RISK의 명시적 합성 요청과 저장 결과를 검사했다. DD의 다일 보유·분할과 원주가, MICRO의 지연·순서·호가 결측과 상세 체결·늦은 체결을 검증했다. KRX 0D 조회, 실증 거래 가능 상태와 체결 순서의 경계는 위 항목에 구분했다. |
| 자료 분할과 과거 성과의 범위 | 검토 사건의 교차 분할을 저장 전에 거절하고 최종평가 자료의 관측 읽기를 차단했다. 손실·무거래와 갭·결측·미완료의 구분을 합성 검사에서 대조했다. 과거 자료의 90/380분 범위와 낙주 단일 훈련 사례는 탐색 재생 결과로 별도 기록했다. [사건 분할](../../tmp/s12-05-verification-matrix/reviewed-halt-event-v2/evidence.json) · [복원 시장비율](../../tmp/s12-04-b2-index-proxy/partial-current-u001-train/result.json) · [낙주 탐색](../../tmp/s12-05-verification-matrix/panic-coverage-main/coverage.json). |

현재 소스 `0fdb56f7`의 영향 및 수락 검사 72건과 증분 원장 대조가 통과했다. 단계의 합성·Shadow 범위를 수락한다. 제품의 전략별 계좌 승인·주문 원장·재시작·화면·일지 연결은 S12-05의 12.12 시나리오로 판정한다. [실행 증거](../../tmp/s12-04-shadow/s12-04-strategy-acceptance/evidence.json) · [증분 대조](../../tmp/s12-04-shadow/s12-04-strategy-acceptance-review.json).

<a id="s12-close-overnight-native"></a>

### S12-05 항목 3 · 종가베팅 익일 재기동 검증

완료봉으로 만든 종가베팅의 최초 근거를 후보 원장에 저장하고 그 내용 해시를 명시한 시험용 계획을 공통 모의 주문 원장에 연결했다. 가짜 브로커에서 4주 매수의 상세 체결 2주와 취소 미확정을 남긴 뒤 주문 소유자와 저장소를 다시 열었다. 계획 참조와 후보 판본을 복원하고, 지연된 상세 체결 1주를 같은 계획에 한 번 반영한다. 휴장·다음 세션 미확인 상태는 대기하며, 확인된 익일 갭에서 보유 3주만 매도한다. 같은 매도 판단을 재전달할 때 가짜 브로커에 전송된 매도는 1건이다. 매매일지에서 두 매수 체결과 익일 매도 체결의 계획과 원래 정책 참조를 대조한다.

시험은 공통 주문 커널에 명시적으로 승인한 합성 명세를 사용한다. 제품의 종가베팅 계획에는 해당 Family의 명시적 승인 명세, 최초 후보와 주문 계획의 native 결속, 기동 시 복원 호출 경로가 필요하다. 마감 진입 주문 방식·잔량 처리와 익일 대응의 적용 판본을 승인된 명세에 고정한 뒤 제품 경로에서 같은 시나리오를 재검증한다. [정본 미정 정책](app-target-20261023.md#1211-미정-매매운용-정책).

최초 PostgreSQL 검사는 시험 후보의 빈 만료 시각에서 실패했다. 완료봉 Shadow와 같은 진입 마감 기한을 시험 후보에 넣은 뒤 현재 소스 `52c96c2f`에서 Windows 226건과 격리 PostgreSQL 25건이 통과했다. 구조·저장소 호출·증분 원장 대조도 통과했고 임시 데이터베이스와 작업자 종료를 확인했다. [최초 실패](../../tmp/s12-03-pg-baseline/s12-05-close-overnight-b/official-runner.log) · [Windows 실행](../../tmp/s12-04-shadow/s12-05-close-overnight-verified/evidence.json) · [PostgreSQL 실행](../../tmp/s12-05-close-overnight/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-overnight/final-review.json).

<a id="s12-close-final-research"></a>

### S12-05 항목 3 · 종가베팅 독립 최종평가

기존 독립 최종평가 요청에서 일반 종가베팅 `close_betting/v1`의 고정 정책·비용·달력 파일을 사용할 수 있다. 후보 해시에는 달력 판본의 SHA-256을 포함하며 경로는 제외한다. 준비 과정에서 파일 내용이 동결된 해시와 일치하는지 검증한 뒤 관측 자료를 열고 접근을 기록한다. 재생과 결과 원장은 같은 달력 판본을 사용한다. 달력 정정의 효력은 각 판단 시점에 계산한다.

합성 검증에서는 금요일의 4주 Paper 매수와 월요일의 4주 Paper 매도를 대조했다. 마감 뒤 달력 정정은 앞선 판단을 바꾸지 않았다. 입력 파일을 바꾸면 관측 자료를 열거나 접근 원장을 만들기 전에 실패했고, 정상 요청을 재실행하면 기존 run과 결과 해시를 반환했다. 기존 후보 v1/v2와 과거 결과는 기존 판본으로 유지한다. 이 구현 시점에는 종가베팅 연구 후보의 게시를 지원하지 않았다. 후속 후보 게시·Shadow 대조는 아래 수락 범위를 따른다. [실행 검사](../../tests/unit/test_research_final_execution.py) · [후속 연결](#s12-close-candidate-admission).

최종평가·요청 CLI·연구 화면과 관련 기존 경로를 포함한 Windows 선택 검사 120건이 통과했다. [실행 증거](../../tmp/s12-04-shadow/s12-05-close-final-verified/evidence.json) · [증분 대조](../../tmp/s12-05-close-final/review-verified.json).

<a id="s12-close-candidate-admission"></a>

### S12-05 항목 3 · 종가베팅 최종 결과와 Shadow 후보의 연결

종가베팅의 실제 독립 최종평가 run·보고서·배치 원장에서 후보 문서를 만들고, 기존 계좌 확인 API로 저장·중복 재시도·재조회할 수 있다. 후보의 정책·비용·달력 참조·구현 해시와 원 run을 대조한다. 연구의 달력 파일 판본은 고정하며, 장중 Shadow는 당시 관측한 자체 달력을 보존한다. 과거와 현재의 날짜·가격·달력 파일 해시 일치를 일반 적용 요건으로 추가하지 않았다. 대조 대상은 같은 종가베팅 규칙과 수량이며 Shadow의 실제 진입 근거·만료·익일 세션 참조를 확인한다.

연결 시험에서 독립 최종평가가 완료돼도 보고서의 최종 fold가 `SEALED`로 남는 결함을 확인했다. 기존 native 실행 소유와 최종 전용 입력을 검증한 경로에서만 보고서의 최종 fold를 평가하도록 수정했다. 원래 동결 요청에 접근 시각을 소급 기입하지 않는다. 일반 연구의 미공개 OOS는 계속 봉인한다. 합성 검증 기준은 실행 전에 정한 1거래·1활동일·순손익 하한 -100원이며 실제 -12원의 손실을 보존했다. 이 기준은 제품 운용 기본값으로 추가하지 않았다.

모의운용 화면은 저장된 종가베팅 후보를 ‘종가베팅 연구 적격’으로 표시하고 별도 ‘Shadow 대조’ 동작으로 일치 건수와 최근 시각을 보여 준다. 아직 종가베팅 주문 명세와 기동 연결이 없어 READY 작성은 차단한다. 가격·호가·취소/잔량·익일 대응의 승인 계약을 연결한 다음 같은 native 주문/FILL·복구 시나리오로 수락해야 한다. [최종 결과→API→native Shadow 검사](../../tests/unit/test_research_final_execution.py) · [화면 검사](../../tests/unit/test_mock_automation_dialog.py) · [정본 미정 정책](app-target-20261023.md#1211-미정-매매운용-정책).

현재 소스 `7b5e9273`의 Windows 선택 검사 198건과 구조·저장소 호출·증분 대조가 통과했다. 신규 보고서·화면 검사는 2건이며 기존 최종평가 검사의 실제 게시·Shadow 연결을 확장했다. [실행 증거](../../tmp/s12-04-shadow/s12-05-close-candidate-admission/evidence.json) · [증분 대조](../../tmp/s12-05-close-admission/review-verified.json).

<a id="s12-close-native-entry"></a>

### S12-05 항목 3 · 종가베팅 최초 진입의 native 결속

종가베팅의 주문 조건을 명세에 고정하는 내부 형식과 최초 진입 dispatcher를 구현했다. 가격·잔량 처리 방식과 두 슬리피지 수치는 명시 입력이며, 표본 값을 제품 기본값으로 추가하지 않았다. 기존 신선도 한도 안에서 완료봉 Shadow의 최초 규칙·수량과 현재 수신된 0B 가격을 대조한다. 계획은 원래 후보·명세·run에 귀속되고 최초 가격·결정·증거 해시를 기존 불변 저장 owner에 남긴다.

주문을 생성하거나 제출하는 기존 native 트랜잭션은 현재 계좌 lease·제어·Risk 검사와 함께 해당 Shadow 원본·만료·현재 활성 설정 및 저장된 가격 근거를 재검증한다. 비활성 Shadow나 승인 뒤 바뀐 가격 근거는 주문·계획 수량 변경 전에 거절한다. 같은 후보의 다른 가격 재전달과 만료 뒤 재요청은 이미 수락한 주문·최초 영수증을 반환한다. 신규 진입을 만들 때는 후보 만료와 가격 한도를 검증한다.

검증의 승인 명세는 가짜 브로커와 격리 DB에 명시한 합성 입력이다. 일반 READY 게시·실행기 기동은 아직 미연결이며, 마감 잔량 취소·같은 계획의 0B 보호·익일 관리와 재시작에서 최초 근거를 소비하는 연결을 이어서 구현해야 한다. 12.12 항목 3은 진행 상태다. [SQLite 검사](../../tests/unit/test_mock_automation_specification.py) · [같은 PostgreSQL 검사](../../tests/integration/test_observation_bootstrap_postgres.py) · [내부 명세](../../API_CONTRACT.md).

현재 소스 `d1743cc7`에서 Windows 영향 검사 286건과 격리 PostgreSQL 26건이 통과했다. 구조 검사는 원래 후보와 만료를 같은 intent 트랜잭션에서 읽는 추가 SQL 지점 1개를 담당 근거에 기록했고 기존 수락 기준선을 유지했다. 저장소 소비 계약과 선택 실행 ID·현재 소스·작업자 및 격리 DB 정리를 대조했다. [Windows](../../tmp/s12-04-shadow/s12-05-close-entry-verified/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-native-entry/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-native-entry/review-verified.json).

<a id="s12-acceptance-seven-nine"></a>

### S12-05 항목 7·9 · 개발 수락

항목 7은 기존 모의계좌 owner·주문 원장과 가짜 브로커로 검증했다. 계획 주문의 영수증 COMMIT 응답이 유실돼도 같은 결정의 재전달은 최초 영수증을 반환하고 주문 전송은 1건이다. 같은 계좌를 확인한 새 키로 교체한 후에도 원 run·원주문·기존 체결과 늦은 상세 체결 1건을 보존하며, 계획 수량 2주와 마지막 체결 커서가 일치한다. 활성 lease는 새 소유자 한 명이고 신규 진입에는 명시 재개가 필요하다. 키 저장 COMMIT 응답 유실 후 재기동도 같은 계좌·run·재개 대기를 복원했다. 다른 계좌와 검증 실패 키는 기존 자격증명과 실행 소유자를 대체하지 않았다. [현재 소스 검사](../../tmp/s12-04-shadow/s12-05-acceptance79/evidence.json) · [계좌 경계 검사](../../tmp/s12-04-shadow/s12-05-acceptance79-boundaries/evidence.json).

항목 9는 일반 연구 요청에서 같은 사건의 여러 출처와 후속 관측을 같은 fold에 묶고, 이를 나누면 결과 저장 전에 거절하는 기능을 수락했다. 기존에 검토한 TRAIN 자료의 원본 해시를 다시 대조하고, 같은 출처 2개·사건 1개·후속 분봉 381개를 현재 코드로 실행했다. 한 사건 묶음이 저장됐고 같은 자료를 TRAIN·VALIDATION으로 나눈 대조 요청은 DB·결과 생성 전에 실패했다. 과거 사건의 출처 참조는 분할 메타데이터이며, 발행 시각이나 봉 내부 경로가 불명인 원본을 당시 신호로 간주하지 않는다. 당시 알려진 복원 자료와 미래·상충 자료의 구별은 기존 합성 검사로 함께 확인했다. [현재 개발 사례 실행](../../tmp/s12-05-acceptance79/historical-event/evidence.json) · [연구 분할·시점 검사](../../tmp/s12-04-shadow/s12-05-acceptance79/evidence.json).

수락 근거는 현재 제품 소스 `7b5e9273`에 묶인 기존 검사 8건과 개발 사례 재생이다. 새 제품 코드나 검사·정책 수치를 추가하지 않았다. S12-05 전체에는 남은 9개 대표 연결과 DD·MICRO 추가 경계의 수락이 필요하다. 새 전략별 주문 승인·복구는 각 담당 항목에서 별도로 판정한다. [소스·실행·정리·항목 대조](../../tmp/s12-05-acceptance79/acceptance.json).

<a id="s12-acceptance-remaining"></a>

### 12.12 통합 수락 현황과 잔여 조건

| 12.12 | 상태 | 근거와 남은 조건 |
|---|---|---|
| 1 · 2 · 6 | 진행 | 돌파 최초 진입·만료·보호와 명시 재개를 격리 모의 실행에서 확인했다. 계좌 위험 연구는 동일 현금에서 계획별 예약·부분 체결·수량과 계좌 평가를 대조한다. 추가·부분 청산·재진입과 서로 다른 전략의 동시 승인은 전략별 행동 근거·수량·공유 계좌 배분 정책을 확정해 같은 계좌의 다른 계획 및 수동 수량과 대조해야 한다. 기존 취소 후 잔량 재요청의 `ADD`·`REDUCE`는 같은 주문의 잔량을 다시 요청하는 기능이다. [계좌 위험 연구 검증](../../tests/unit/test_account_risk_research.py). |
| 3 | 개발 수락 | 일반 READY builder/서버·명시 화면·인증 조회·공개 기동에서 원계획의 진입/부분 체결/미확정 취소·늦은 체결·DB 재개방·Shadow 비활성·익일 갭 청산을 연결했다. 마감/휴장·가격 중단/정지 상태·오래된 0B는 가짜 청산/새 진입을 만들지 않으며 원래 3주를 보존한다. 실제 Collector의 마감 가격·첫 분봉으로 3주만 청산하고, 원장/계좌 잔량 0·일지 재조회/화면 계획 ID와 BUY=3/SELL=3을 확인했다. 기존 v1/v2 중지 정책과 최초 손절·정정/native 검증은 유지한다. 합성 계좌·명시 휴장/정지 입력의 소프트웨어 수락이며 운영 시장 감지·주문/체결 품질은 별도다. [수락 근거](#s12-acceptance-close-three). |
| 4 | 개발 수락 | 신규주 두 방식/두 상장 단계의 실제 final/Shadow→정상 READY·UI/API→공개 자동 Episode→원계획·첫0B 구조 보호/부분·늦은 체결·재개→일지/실제 화면 조회를 연결했다. 부족한 이력을0으로 채우지 않고, 원래 계획 ID/정책 증거·상장 프로필과 BUY3/SELL3·잔량0을 보존했다. [수락 근거](#s12-acceptance-ipo-four). |
| 5 | 개발 수락 | 낙주 P0/P1의 final/현재 Shadow→명시 READY/UI/API→공개 자동 관리·native 현재 판단/정정·일지를 연결했다. 부분2/취소 UNKNOWN/늦은1은 실제3주만 관리하고, 최초 반등/실패와 당일 미완료를 보존해 DB 재열기·익일3→부분1/확인된 취소→잔량2→0을 확인했다. 무체결 취소 뒤 늦은1도 같은 실행 중 원계획으로 복원한다. [수락 근거](#s12-acceptance-panic-five). |
| 7 | 개발 수락 | 같은 계획의 재전달·COMMIT 응답 유실·같은 모의계좌의 검증된 키 교체·상세 체결 복구와 새 진입의 명시 재개를 검증했다. 다른 계좌의 기존 권한 승계는 거절한다. [수락 근거](#s12-acceptance-seven-nine). |
| 8 · 10 | 진행 | 늦은 시장·뉴스·정정의 신규 진입 제한과 데이터가 바뀌어도 유지되는 최초 보호, saved/applied 및 조회 전용 수신은 확인했다. 각 새 전략의 실행 활성·비활성 전환에서 기존 보유 관리와 원장·화면 일치를 추가 확인한다. |
| 9 | 개발 수락 | 합성과 검토된 TRAIN 사례에서 여러 출처·후속 봉을 같은 사건에 묶고 학습·검증 분할을 차단했다. 원래 공시 수신 시각과 수정 전 보도 판본은 확인하지 못해 최초 신호 입력에서 제외했다. [수락 근거](#s12-acceptance-seven-nine) · [검토된 개발 사례](../../data/research/historical-monthly-exchange-case-context-20260924.json). |
| 11 | 진행 | 빈 설치·순위/설정/저장과 선별 주문 증거의 최초 영수증은 검증했다. 독립 대상의 계좌 UUID와 원본 커서는 같은 계좌나 주문을 증명하지 못한다. 원본·대상의 검증된 계좌 연결과 일자·원주문·상세 체결의 브로커 근거가 있어야 관리 상태를 안전하게 복구한다. [격리 반례](../../tmp/s12-05-selective-install/independent-recovery-cut-audit.md). |
| DD · MICRO | 부분 검증 | DD의 보유·분할은 합성이고 동결 일봉은 셋업만 평가한다. 원주가 보호와 체결별 비용이 필요하다. MICRO의 KRX 호가 깊이는 가짜 연결과 관리자 조회로 검증했다. 실제 종목 거래 가능 상태·교차 순서·VI 해제 체결가는 별도 근거를 기다린다. |

<a id="s12-close-native-exit"></a>

### S12-05 항목 3 · 종가베팅 원체결 복원과 native 청산

종가베팅 최초 진입의 불변 근거와 같은 계획의 상세 체결을 복원하는 청산 dispatcher를 연결했다. 원래 매수의 종결과 취소 잔량을 확인한 뒤 체결별 정수 평균단가를 기존 공통 계산으로 구한다. 현재 0B 가격의 손절, 원래 달력에 고정한 다음 세션의 보유 기한과 초과 보유를 판단하고 해당 계획의 실제 잔량만 지정가로 청산한다. 완료봉의 갭 판단과 별개인 0B 대응이며 봉이나 틱을 합성하지 않는다. 연구용 사후 복원 분봉을 제한하는 새 조건은 추가하지 않았다.

청산 근거는 기존 immutable 문서 owner에 저장한다. 원체결 해시·단가·수량·마지막 체결 커서와 판단 및 주문 가격을 동결한다. 실제 intent 생성과 제출의 native 트랜잭션은 원래 후보·계획·상세 체결과 청산 근거를 다시 읽는다. 최초 생성이 예약한 판본 1회를 제출 시에도 확인하고 현재 Risk·계좌 lease·제어 검사는 공통 경로를 재사용한다. Shadow 활성 설정을 바꿔도 이미 보유한 계획은 원래 정책으로 관리한다. 같은 청산의 재전달은 보유가 0이 된 뒤에도 최초 주문·영수증으로 연결된다.

격리 시나리오는 실제 store를 닫고 다시 연다. 매수 4주 중 서로 다른 단가의 상세 체결 2주·1주와 잔량 취소를 저장하고 3주·평균단가 103원을 복원한다. 장중 100원에서는 보유, 94원 손절 관측에서는 93원 지정가 매도, 원래 익일 개장 후 8분에는 99원 매도를 각각 확인한다. 이 수치는 명시한 합성 검증 입력이다. Shadow 비활성·설정 제거 뒤에도 관리되고, 과도한 가격 이탈과 승인 후 변조한 청산 근거는 주문·계획 변경 전에 거절한다. 매도 상세 체결 뒤 재전달은 전송 1회를 유지한다. [SQLite와 공통 검증](../../tests/unit/test_mock_automation_specification.py) · [동일 PostgreSQL 시나리오](../../tests/integration/test_observation_bootstrap_postgres.py).

일반 READY 게시·실행기 기동, 마감 잔량 취소와 완료봉 갭 대응은 후속 연결이다. 이번 내부 dispatcher를 일반 기동이나 중지 상태의 자동 보호 전체 완료로 계산하지 않는다. 항목 3과 S12-05 전체의 수락 상태는 진행이며 전체 단계 완료율 4/5, 대표 연결 수락 2/11을 유지한다. 구조 변경은 원래 셋업·계획 head의 공통 decoder 재사용과 같은 native 트랜잭션의 결속이다. 처리 성능 향상을 주장하거나 라이브러리를 추가하지 않았다.

현재 소스 `700bacca`에서 Windows 영향 검사 299건/8모듈과 격리 PostgreSQL 28건이 통과했다. 신규 검사는 같은 두 시나리오의 SQLite·PostgreSQL 4건이다. 기존 익일 검사의 셋업 복원 중복을 공통 codec으로 전환했고 원래 assertion은 유지했다. 구조·QueryStore 소비·증분 실행 대조를 통과했으며 이전 감사 판정과 미결 49건을 보존했다. Windows 작업자 8개와 PostgreSQL 소유 클러스터 4개의 종료·임시 환경 정리를 대조했다. [Windows 실행](../../tmp/s12-04-shadow/s12-05-close-exit-verified/evidence.json) · [PostgreSQL 실행](../../tmp/s12-05-close-native-exit/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-native-exit/review-verified.json).

<a id="s12-close-native-cancel"></a>

### S12-05 항목 3 · 원래 진입 기한의 잔량 취소와 늦은 체결

종가베팅 내부 dispatcher에서 원래 셋업의 진입 기한에 남은 매수 주문을 취소하도록 연결했다. 기존 계좌 runtime의 취소 builder와 native `CANCEL_STARTED` 수락을 재사용한다. 최초 진입과 현재 브로커 주문 ID·상태·기체결·잔량, 상세 체결과 계획 판본 및 현재 Risk를 같은 트랜잭션에서 다시 확인한다. 이 검증은 새 매수나 미체결 정정을 승인하지 않는다. 입출력·저장 owner는 유지했고 최초 진입·청산·취소의 원래 후보 조회만 같은 native 함수로 공유했다.

취소 전에 기한이 오지 않았거나 승인 뒤 원래 근거가 바뀌면 브로커 전송과 취소 이벤트·계획 변경을 거절한다. 미체결 0주와 부분 체결 2주를 각각 취소하고, 취소 수락 또는 응답 유실 뒤 DB와 runtime을 닫았다 다시 연다. 재요청은 기존 `CANCEL_PENDING`을 반환하고 전송은 1회다. 이후 도착한 상세 체결 1주를 같은 계획에 1회만 추가해 실제 보유는 1주 또는 3주가 된다. 다음 지원 세션에서 현재 0B 보호 청산은 해당 실제 수량만 매도한다. 장마감 동시호가에 지원되지 않는 신규 지정가 청산을 강제하지 않는다. [SQLite 시나리오](../../tests/unit/test_close_mock_execution.py) · [같은 PostgreSQL 시나리오](../../tests/integration/test_close_mock_execution_postgres.py).

현재 내부 진입·취소·보유 청산 부품은 연결됐으며 일반 READY 게시와 실행기의 자동 기동·관리, 완료봉 갭 대응과 중지 상태 관리는 남아 있다. 12.12 항목 3의 전체 완료와 별도로 기록하며 대표 연결 수락 2/11과 단계 완료율 4/5를 유지한다. 새 수치 기본값·라이브러리·테이블은 없고 속도 향상을 주장하지 않는다.

최종 소스 `3d1df674`에서 Windows 관련 98건/6모듈과 격리 PostgreSQL 7건이 통과했다. 새 검사 4건은 같은 2개 시나리오의 SQLite·PostgreSQL 검사이며 기존 CLOSE 진입·청산과 공통 취소 검사를 함께 선택했다. 새 단위 모듈은 기존 계좌·주문 안전 profile, 새 PostgreSQL 모듈은 필수 CI 그룹에 등록했다. 최초 소스의 96건·7건은 실행 통과 뒤 최종 대조에서 분류 미등록으로 거절됐고, 등록을 보완한 현재 소스로 필요한 선택 검사를 다시 확인했다. 기존 기대값이나 수락 기준은 바꾸지 않았다. 최종 증분 실행·분류·구조·저장소 소비 대조를 통과했고, 작업자 6개와 소유 PostgreSQL 클러스터 1개를 종료·정리했다. [Windows](../../tmp/s12-04-shadow/s12-05-close-cancel-verified-v2/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-native-cancel/pg-evidence.json) · [최종 대조](../../tmp/s12-05-close-native-cancel/review-verified.json) · [최초 등록 거절](../../tmp/s12-05-close-native-cancel/review-first-registration-failure.json).

<a id="s12-close-native-gap"></a>

### S12-05 항목 3 · 완료봉 갭 규칙과 native 관측 결속

기존 연구의 마감 동시호가·첫 완료 분봉 갭 규칙을 순수 함수로 공유하고 내부 종가베팅 청산에 연결했다. 새 셋업은 원래 선택한 다음 세션의 전체 window를 함께 보존한다. 과거 셋업에 이 필드가 없으면 임의로 달력을 만들지 않고 기존 손절·기한 경로를 유지한다. 연구·Shadow의 수익률 기준과 IPO 종가베팅의 부분 청산 규칙은 바꾸지 않았다.

native 갭 청산은 실제 완료 KRX 봉 두 판본의 원 payload·가용 시각·메타데이터를 기존 불변 exit 문서에 보존한다. 현재 0B 가격으로 해당 계획의 실제 잔량을 매도하며 봉 종가와 0B의 절대 가격 일치를 요구하지 않는다. 주문 생성과 제출 트랜잭션은 기존 codec으로 정확한 두 revision ID를 다시 조회해 저장 근거·계획·결정과 대조한다. 이 추가 조회는 갭 청산에만 있으며 손절·기한 청산과 원래 수집 경로는 유지한다.

격리 native 원장에 명시한 합성 완료 관측을 입력했다. 마감 가격 105원과 첫 분봉 시가 96원의 갭을 확인한 뒤 현재 0B 103원으로 실제 3주를 매도했다. 첫 분봉 종가 102원은 주문 가격으로 재사용하지 않았다. 봉이 없거나 개장 봉이 판단 시점 이후에 가용하면 갭 청산은 생성되지 않는다. 승인 뒤 저장한 payload를 바꾸거나 존재하지 않는 native revision을 넣으면 주문·계획 변경 전에 거절한다. 원래 달력 window의 보존과 연구·Shadow·완료 결과·화면을 함께 대조했다. [단위 시나리오](../../tests/unit/test_close_mock_execution.py) · [같은 PostgreSQL 시나리오](../../tests/integration/test_close_mock_execution_postgres.py) · [공유 규칙](../../src/kiwoom_monitor/application/close_and_ipo.py).

현재 소스 `4a973680`에서 Windows 관련 21건/7모듈과 격리 PostgreSQL 8건이 통과했다. 새 검사는 같은 갭 시나리오의 SQLite·PostgreSQL 2건이고 기존 셋업 검사에는 독립 달력 fixture와 전체 window의 일치 assertion을 추가했다. 기존 기대값·판정·미결 49건을 보존했다. 구조·QueryStore 소비·증분 실행 대조를 통과했고 Windows 작업자 7개와 PostgreSQL 소유 클러스터 1개를 종료·정리했다. [Windows](../../tmp/s12-04-shadow/s12-05-close-gap-verified/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-native-gap/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-native-gap/review-verified.json).

일반 READY 게시와 실행기 자동 기동·복원·중지 시 관리, 실제 collector의 완전한 동시호가 관측 공급은 남아 있다. 현재 collector의 기본 분봉 payload는 1분 구간이며 이번 합성 동시호가 전체 구간 입력을 실제 수집 완료로 간주하지 않는다. 이는 승인 로드맵 항목 3의 입력·실행 연결 잔여다. 대표 연결 수락 2/11과 전체 단계 4/5를 유지하며, 라이브러리 추가나 처리 성능 향상을 주장하지 않는다.

<a id="s12-close-episode"></a>

### S12-05 항목 3 · 기존 Episode의 종가베팅 계획 수명 연결

기존 Supervisor가 조립하는 Episode에 종가베팅 내부 실행 경로를 연결했다. 기존 Runner의 task·입력 attach/detach·잠금과 종료 합류, 계좌 monitor와 Gateway의 fresh cut·명령 직렬화·credential drain, runtime/native의 주문 권한을 재사용한다. 새 `ClosePlanManagement`는 해당 전략의 Shadow 커서와 원래 계획 입력·갭 판본만 관리한다. 새 worker나 주문 권한 owner를 만들지 않았다. 과거 Shadow seed는 신규 주문으로 실행하지 않는다.

신규 `close_entry_snapshot/v2`는 원래 후보·셋업·만료를 보존한다. 공개 운송 커서는 제외하고 만료는 같은 순간의 KST ISO로 정규화해 PostgreSQL과 SQLite 표현 차이를 제거했다. 기존 v1은 원래 해시·계획·최초 영수증을 유지한다. 새 v2 보유의 재시작은 공개 Shadow 이력 검색 없이 원래 후보와 native 계획·상세 체결을 복원한다. v1 prototype은 이전 입력 재현을 위해 기존 페이지 조회를 유지한다.

갭 입력은 고정된 두 시간 구간의 native 관측만 조회한다. 5,000개 등의 이력 개수로 필요한 봉을 잘라내지 않는다. 같은 관측 키의 판본을 선택하고 시점·동률 상충·미완결을 구분하며 가용 시각은 Python의 실제 순간으로 비교한다. KST/UTC 인자와 1마이크로초 미래 경계를 대조했다. schema 25는 기존 관측 원장에 조회 인덱스 하나를 추가하며 기존 행·테이블·체결을 다시 쓰지 않는다. 구조 소비 원장은 정확한 새 읽기 6곳·store 전달 1곳·native 메서드 1개만 추가했고 기존 미결 후보 66개는 보존했다.

같은 격리 시나리오를 두 DB에서 실행했다. 실제 Supervisor 조립·Runner 기동 후 새 Shadow에서 4주·105원 매수를 생성했다. 상세 체결 2주 뒤 계좌 owner가 갱신한 cut으로 잔량을 취소하고, 취소 중 체결 1주를 보존했다. DB와 runtime을 닫았다 다시 열고 Shadow를 비활성화해도 원래 보유를 복원했다. 갭 입력이 없을 때는 보유하고, 명시한 native 완료 관측이 들어오면 현재 0B 103원으로 3주를 매도했다. 매도 상세 체결 후 같은 계획의 잔량·미귀속 수량은 모두 0이며 전송은 BUY·SELL 각 1회다. 검증 승인은 합성 내부 명세이며 일반 READY 작성과 게시의 기존 차단은 그대로다.

현재 소스 `23095b39`에서 Windows 관련 374건/13모듈과 격리 PostgreSQL 12건이 통과했다. 새 검사는 실행기 경로와 v1 호환의 두 DB 4건이며 schema 검사는 실제 추가 인덱스 계약으로 갱신했다. 기존 안전 제한·검사 기대값을 완화하지 않았다. 구조·저장소 소비·증분 실행 대조가 통과했고 기존 감사 판정과 미결 49건을 보존했다. Windows 작업자 13개와 소유 PostgreSQL 클러스터 1개를 종료·정리했다. [Windows](../../tmp/s12-04-shadow/s12-05-close-runner-verified/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-runner/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-runner/review-verified.json).

순서 있는 0B 손절의 durable 고정과 일부 체결 보호 취소, 중지 제어하 보유 관리·실제 완전한 동시호가 공급·일반 READY/UI 및 최종 상태 정합성의 수락은 남아 있다. native 승인에 필요한 fresh Risk·대사·단일 계좌 권한과 기존 제어 제한은 유지했다. 내부 실행기의 연결을 항목 3 전체 완료나 운영 수락으로 계산하지 않는다. 대표 연결 2/11·전체 단계 4/5는 유지하며 라이브러리 추가나 속도 향상은 주장하지 않는다.

<a id="s12-close-flat-status"></a>

### S12-05 항목 3 · 청산 후 현재 상태

보유·주문이 끝나고 새 Shadow 후보가 없는 회차에서는 이전 주문 전송 상태와 pending intent를 지운다. 변경 전 실패를 재현했고 현재 소스 `9756ee1b`의 직접 검사 6건과 구조·소비·증분 실행 대조를 확인했다. 공용 fixture의 AST가 HEAD와 같음을 확인한 뒤 이번 변경의 소비자 목록만 갱신했으며 이전 감사·실행 기록은 보존했다. [현재 실행](../../tmp/s12-04-shadow/s12-05-close-flat-status-final/evidence.json) · [증분 대조](../../tmp/s12-05-close-flat-status/review-verified.json).

최초 선정은 이 파일의 과거 공용 fixture 목록 때문에 151건으로 확장됐다. 긴 Supervisor 묶음의 180초 제한과 보고 요약 오류를 원기록에 보존하고 전체 통과로 계산하지 않았다. 추가 범위는 반복하지 않았으며 변경된 원장 판본의 직접 6건을 다시 확인했다. native SQL·주문·계좌·시계 계약은 이번에 변경하지 않았다. 청산 후 표시 조건만 닫으며 순서 있는 보호·중지 관리·실제 동시호가 공급·일반 READY/UI는 남는다.

<a id="s12-close-sticky-stop"></a>

### S12-05 항목 3 · 첫 0B 손절과 부분 체결 보호

기존 Supervisor 가격 callback→Episode의 RAM 보호 요청→기존 checkpoint 수락→Gateway의 최신 계좌 cut→native 취소·청산에 연결했다. 첫 관측은 `protection_review`와 원래 불변 Risk 판본으로 보존한다. 이후 새 Risk나 회복 가격으로 그 근거를 바꾸지 않는다. `close-entry-protective-cancel/v1`은 마감 전 원매수 잔량을 한 번 취소하며 기존 deadline/v1과 같은 native 수명을 사용한다. 청산 v2는 원래 손절과 별도의 현재 실행 가격을 보존한다. native 트랜잭션에서 같은 checkpoint·Risk·원매수의 당시 체결 커서를 재검증한다. 새 worker·권한 owner·테이블이나 매 틱 DB 접근은 없다.

합성 시나리오는 실제 Runner를 깨워 105원 매수의 상세 체결 2주 뒤 94원 손절을 고정하고, 103원 회복 뒤에도 마감 전 잔량 취소를 한 번 보냈다. 취소 중 체결 1주를 보존하고 실제 DB를 다시 연 뒤 Shadow가 꺼져 있어도 오래된 최초 손절과 현재 103원으로 실제 3주만 청산했다. 체결 기준보다 앞선 가격은 손절을 만들지 않으며 callback의 DB 읽기·쓰기는 모두 0회다. 저장된 보호 요청이나 과거 체결 커서를 변조한 반례는 취소 전송·주문 상태·이벤트 변경 전에 거절한다. 이 가격·수량은 검증용 명시 입력이며 연구/실행 기본값이 아니다.

현재 소스 `a9054555`에서 Windows 영향 회귀 93건/5모듈과 격리 PostgreSQL 고유 14건을 확인했다. 신규 검사는 같은 보호 수명·native 반례의 두 DB 4건이다. 기존 안전 제한·기대값과 감사 미결 49건은 유지했다. 기존 통합 fixture 소비자 목록에서 필요한 추가 5건만 두 번째 클러스터로 확인했고 현재 검사를 불필요하게 반복하지 않았다. 구조·소비·증분 실행 대조와 Windows 작업자 5개·PostgreSQL 클러스터 2개의 종료·정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-close-stop-final/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-stop/pg-evidence-all.json) · [증분 대조](../../tmp/s12-05-close-stop/review-verified.json).

첫 손절 보존·부분 체결 보호 취소 조건을 닫는다. 항목 3의 중지 상태 관리·실제 동시호가 공급·일반 READY/UI와 나머지 대표 연결은 계속 진행이다. 전체 4/5·대표 연결 2/11을 유지하며 속도 향상이나 실제 운영 수락을 주장하지 않는다.

<a id="s12-close-stopped-management"></a>

### S12-05 항목 3·10 · 명시한 정책의 중지 후 원계획 관리

기존 중지 명세의 의미를 일괄 바꾸지 않는다. `close-mock-execution-policy/v1`은 기존 JSON·ID와 STOPPED 권한을 유지한다. 새 v2는 `stopped_management: continue_original_plan/v1`을 명시해야 하며, 그 선택을 명세 hash에 포함한다. 별도 사용자 운영 정책을 기본값으로 확정하지 않고 내부 합성 검증은 명시한 v2를 사용한다. application EXIT gate와 native control fence가 같은 정책을 확인한다. 원래 계획의 typed 취소·청산과 같은 주문의 QUEUED/제출 수명만 진행하고 신규 진입이나 RUNNING 전환은 허용하지 않는다. 같은 계좌/run/spec/revision·lease·중지 후 최신 대사·Risk·원체결·수량의 검사와 native 근거 재검증은 보존한다.

같은 실제 Runner/가짜 브로커/DB 재개방에서 두 경로를 검증한다. 하나는 첫 손절 후 마감 전 취소·늦은 체결·재시작·보호 청산이며, 다른 하나는 손절 없이 원래 마감 취소·익일 완료봉 갭 청산이다. 두 경로 모두 실제 3주만 매도하고 마지막까지 원래 STOPPED control을 유지한다. 명세가 v2여도 새 BUY는 native 단계에서 거절한다. 기존 v1 STOPPED의 보호 취소는 계속 거절하며, 누락되거나 판본과 맞지 않는 중지 정책도 저장할 수 없다. v1 JSON roundtrip과 기존 계획 영수증의 검사는 유지한다.

현재 소스 `b762960b`의 Windows 관련 336건/6모듈과 격리 PostgreSQL 17건을 확인했다. 신규 검사는 v2의 두 관리 경로와 v1 차단의 두 DB 6건, 정책 직렬화 1건이다. 변경된 공용 명세 fixture의 기본 경로도 실제 270건으로 재검증했으며 개별 안전 제한은 바꾸지 않았다. 모듈별 묶음 제한만 기존 180초 대신 300초로 지정했다. 구조·소비·증분 실행 대조가 통과했고 기존 감사·미결 49건을 유지했다. Windows 작업자 6개와 PostgreSQL 소유 클러스터 1개의 종료·정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-close-stopped-final/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-stopped/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-stopped/review-verified.json).

중지 후 관리의 소프트웨어 연결만 닫으며 실제 사용자 정책 선택·운영 활성은 수행하지 않는다. 실제 완전한 동시호가 공급·일반 READY/UI와 나머지 대표 연결은 남아 있다. 전체 4/5·대표 연결 2/11은 유지한다.

<a id="s12-close-collector-gap"></a>

### S12-05 항목 3 · Collector 마감 가격과 첫 분봉의 실제 코드 연결

갭 규칙이 소비하는 마감 종가를 실제 승인 KRX 0B 가격 관측으로 보존한다. Collector가 기존 RAM·보류/재시도·flush를 소유하고, `save_dataset_snapshots`의 기존 native 트랜잭션이 metadata와 불변 관측을 저장한다. 기존 1분봉·초봉·시장 판정과 일반 연구 선택 종류는 바꾸지 않는다. 새 `closing_auction_price/v1`에는 실제 체결/수신 시각·가격·출처와 원래 장구분 문자열을 보존하며 OHLC를 만들지 않는다. [키움 공식 0B 필드 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EC%8B%A4%EC%8B%9C%EA%B0%84%EC%8B%9C%EC%84%B8/subscribe_domestic_stock_trade_pubsub.py).

마감 체결을 정각 하나에 제한하지 않는다. KRX가 설명한 랜덤 종료 30초 범위와 기존 지연 개장 일정을 재사용하며, 실제 체결 시각을 metadata `effective_at`에 그대로 저장한다. 이 범위는 전략 수치 기본값이 아니다. 판본과 당시 가용성은 기존 원장에서 확인한다. 실제 분봉의 `종목:KRX` 키와 과거 비교의 종목 키를 함께 조회하고, 직접 마감 가격이 있으면 그 가격을 사용한다. 기존 완료 동시호가 봉을 사용한 비교도 유지한다. 새 입력 경로의 청산 snapshot v3는 원장 두 판본을 담고 주문 생성·제출의 같은 native 트랜잭션이 다시 대조한다. [KRX 단일가매매·랜덤 종료](https://global.krx.co.kr/contents/GLB/06/0602/0602010202/GLB0602010202T1.jsp).

합성 메시지를 실제 Collector에 공급해 예정 마감 7초 뒤 체결, COMMIT 응답 유실 후 원래 보류 자료 유지·동일 판본 재시도, 다음 구독 승인과 첫 완료 분봉을 확인했다. source 교체 뒤에도 원래 마감 가격은 유지된다. 실제 DB 재개방·Shadow 비활성 뒤 Runner는 현재 103원으로 실제 3주만 매도한다. UTC/KST와 한 마이크로초 차이의 cutoff를 대조했고, 동결 청산 근거의 가격을 바꾸면 native 원관측과의 차이로 주문·계획 변경 전에 거절한다. 새 저장 종류도 기존 운영 상태 검사의 실제 표본에 포함해 전체 저장 종류 일치 assertion을 유지했다. 이는 실제 소프트웨어 경로의 합성 검증이며 장중 수집/체결 품질 수락은 아니다.

현재 소스 `39b6be7c`의 Windows 영향 회귀 431건/15모듈과 PostgreSQL 고유 19건을 확인했다. 신규 5건은 마감 가격 factory와 같은 정상·변조 경로의 두 DB 검사다. 기존 상태 검사는 새 가격 자료 1건을 추가해 보강했다. 구조·소비·증분 대조가 통과했고 기존 감사·미결 49건은 보존했다. 저장소 소비 계약은 기존 Collector의 dataset flush 1개만 추가해 388→389이며 프로토콜·native 서명과 미해결 후보 66건은 유지했다. Windows 작업자 15개와 최종 PostgreSQL 클러스터 2개의 종료·정리를 확인한다. [Windows](../../tmp/s12-04-shadow/s12-05-close-collector-gap-verified/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-collector-gap/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-collector-gap/review-verified.json).

최초 Windows 판본은 상태 표본의 누락 1건을 발견했고 보강 뒤 현재 소스로 다시 검증했다. 전체 19건 PostgreSQL 시도 두 번은 묶음 240초 제한으로 보고서가 완성되지 않아 통과로 계산하지 않았다. 환경의 지정값이 자식에 전달되지 않아 제한이 유지됐으며, 최종은 12·7건으로 나눠 실제 완료한 결과만 합쳤다. 제한 환경의 직접 재검사 정체는 소유한 검사 프로세스 두 개만 확인·중단하고 정상 Windows 실행으로 재검증했다. 기존 개별 검사·주문·대사 제한은 바꾸지 않았다.

보통 장중 0B callback의 같은 8,000건을 이전 HEAD와 현재 함수 본문에 5쌍씩 공급했다. 공통 snapshot·분봉 결과 해시는 모두 같고 DB 호출은 0회였다. CPU 중앙값은 표본에서 모두 0.172초, 벽시간 중앙값은 0.169→0.178초였다. 공통 helper는 현재 소스이며 전체 앱·장중·RSS 측정이 아니다. 처리 속도 향상을 주장하지 않는다. [입력·함수 지문·짝 비교](../../tmp/s12-05-close-collector-gap/callback-comparison.json).

Collector 생산 경로 연결 조건을 닫는다. 다음은 일반 READY 게시·화면/기동 연결이며, 기존 12.14의 마감 입력 중복과 보유 중 정정 판본 소비도 그 연결 수락에서 확인한다. 전체 4/5·대표 연결 2/11을 유지한다.

<a id="s12-close-ready-current-gap"></a>

### S12-05 항목 3 · 일반 READY 게시·화면·공개 기동과 현재 갭 판본

기존 READY builder와 서버 게시 검증의 CLOSE 일괄 차단을 실제 계약 검증으로 바꿨다. CLOSE의 분봉 실험 계약·factor 판본과 일치하는 Shadow를 확인하며 기존 연구 Family 등록표는 유지한다. `MockAutomationPublicationDialog`에서 진입/청산 가격 허용 범위(bp)와 중지 후 관리 v1/v2를 명시해야 한다. v2 선택은 원계획 관리 의미를 그대로 저장하고 v1은 기존 권한·표현을 유지한다. 인증 게시·조회에서 정책과 spec ID가 같고 게시만으로 주문은 시작하지 않는다. 일반 화면의 종가베팅 연구 대조 전용 분기도 같은 명세 작성 경로로 전환했다.

기존 공용 CLOSE fixture를 일반 builder·게시 검증으로 연결했다. 실제 native Runner 표본은 공개 `Supervisor.activate`에서 동일 계좌 context·설정을 확인한 뒤 새 Shadow 진입·일부 체결·취소·늦은 체결·DB 재개방·Shadow 비활성·익일 원계획 청산까지 사용한다. 계좌 bundle/브로커는 합성이며 실제 사용자 인증·운영 계좌 전환 증거는 아니다. STOPPED의 기존 v1/v2 정책 검증과 native 변조·Risk/control/lease/CAS는 유지한다.

12.14의 직접 연결 두 항목도 처리했다. Collector는 같은 종목/거래일에 연속한 동일 마감 가격을 최초 사실 하나로 보존한다. 가격 A→B→A와 다음 날짜는 별도 사실이며 모든 실제 0B callback은 계속 순서대로 전달한다. COMMIT 응답이 없어도 원래 보류 문서는 그대로 재시도한다. HOLD 중 갭 자료는 기존 제한 주기의 indexed 두 구간으로 다시 조회해 늦은 실제 정정을 그 가용시각부터 반영한다. 이미 고정한 청산 근거는 다시 쓰지 않는다. 정정 재현에서 fresh 대사가 계획 판본을 갱신해 청산을 막는 직접 원인을 확인했고, 대사 뒤 원래 계획 head를 다시 읽어 현재 판본으로 기존 native CAS를 사용한다. 새로운 시계·worker·DB 소유자·스키마·매 틱 DB 접근은 없다.

소스 `37272aff`에서 Windows 고유 380건/6모듈과 격리 PostgreSQL 20건이 통과했다. 공용 명세 모듈 272건·화면 48건·CLOSE 15건·Collector 39건과 기존 경계 6건이다. PostgreSQL은 CLOSE 전체 13건과 영향받는 기존 7건을 두 소유 클러스터로 실행했다. 구조·저장 소비 계약·증분 실행 대조가 통과했고 기존 감사/미결 49건을 보존했다. 이전 ID를 포함한 첫 선택 시도는 화면 모듈을 발견하지 못해 별도 실패로 보존했으며, 통과한 332건은 반복하지 않고 실제 48건만 재검증했다. 현재 소스가 같은 실행만 합쳤다. Windows 일곱 작업자(실패 선택 포함)와 두 PG 클러스터의 종료를 확인했다. 실패 선택의 임시 자료는 보존한다. [Windows 대조](../../tmp/s12-05-close-ready/windows-evidence.json) · [원 선택 실패](../../tmp/s12-04-shadow/s12-05-close-ready-final/evidence.json) · [화면 재검증](../../tmp/s12-04-shadow/s12-05-close-ready-ui/evidence.json) · [PostgreSQL 대조](../../tmp/s12-05-close-ready/pg-evidence.json) · [증분 대조](../../tmp/s12-05-close-ready/review-verified.json).

같은 마감 0B 1,200건을 이전 HEAD와 현재 callback 본문에 5쌍 교차 공급했다. 공통 snapshot·분봉과 모든 즉시 가격 callback의 해시는 같고 DB 호출은 0회였다. 보류 가격 사실은 1,200→3건(A→B→A), 벽시간 중앙값은 59.1→28.3ms였다. 공통 helper는 현재 코드이며 원시 0B를 줄이거나 전체 앱·실시간/RSS 성능을 증명한 것은 아니다. [동일 입력·함수 지문·측정](../../tmp/s12-05-close-ready/closing-callback-comparison.json).

일반 게시·명시 화면·공개 기동 연결과 위 두 입력 조건을 닫는다. 다음은 대표 시나리오 3의 휴장/정지와 계좌·원장·표시 연결 대조이며, 전체 4/5·대표 수락 2/11은 유지한다. 다른 시나리오나 전략의 완료 조건을 추가하지 않는다. 운영·배포·실거래·push는 실행하지 않았다.

<a id="s12-acceptance-close-three"></a>

### S12-05 대표 시나리오 3 개발 수락 · 원계획 익일 관리와 일지 정합

일반 READY 게시·화면 정책 선택·인증 조회·공개 기동/재기동의 `f563a82` 구현과 이전 보호·미확정 취소·늦은 FILL·native 반례를 재사용한다. 그 코드와 정책은 이번에 바꾸지 않았다. 남은 수락 연결을 같은 실제 Collector/Runner/native 원장 경로의 SQLite·PostgreSQL 표본으로 보강했다.

진입 주문은 4주이고 첫 상세 체결 2주·취소 중 늦은 상세 체결 1주를 실제 보유로 귀속한다. 종료/재개방과 Shadow 비활성 뒤에도 원래 계획과 3주를 보존한다. 장 마감·주말·명시한 합성 휴장일에 강제 청산/가짜 익일 진입이 없고, 다음 세션의 가격 없음·명시 HALTED·오래된 0B도 주문하지 않는다. 실제 Collector 코드에 공급한 승인 마감 가격과 첫 완료 분봉이 갭 청산을 결정하면 현재 103원으로 3주만 매도한다. 상세 매도 체결 뒤 계획 수량·미대사 수량·활성 주문·가짜 브로커 보유가 모두 0이다. 최초 손절/v1-v2 중지 관리·미확정 취소의 기존 검사를 같은 관련 모듈로 확인했다. 휴장/정지는 명시한 합성 입력의 처리 검증이며 실제 거래소 휴장·정지 통지의 운영 수집이나 지연/체결 품질 수락을 뜻하지 않는다.

그 원장을 2행씩 일지에 투영하고 재개방했다. 상세 체결 ID 세 개와 BUY=3/SELL=3, 원계획 ID가 보존되고 같은 원장을 다시 받아도 새 행은 0이다. 일지 결과에서 기대값을 만들지 않고 가짜 브로커의 독립 주문/체결 스냅샷으로 거래 요약을 공급했다. 기존 화면의 `TradeHistoryQueryService`가 요약과 상세 체결을 대사해 세 표시 행에 원래 계획을 연결한다. 수익성·실제 비용·실시간 주문 품질을 이 수량 증거로 대신하지 않는다.

최종 소스 `a41ccb0a`에서 Windows 관련 28건(CLOSE 16·일지 12)과 PostgreSQL CLOSE 전체 14건이 통과했다. 새 검사는 같은 마지막 수락 시나리오의 두 DB 2건이다. 변경하지 않은 일반 게시 명세 272건·화면 48건은 직전 구현의 보호 경계와 증거를 재사용해 반복하지 않았다. 현재 소스의 구조·증분 실행 대조와 Windows 작업자 3개(신규 단독 확인 1·관련 2) 및 최종 PG 클러스터 2개의 종료·정리를 확인했다. 첫 PG 14건 묶음은 전체 240초 제한으로 최종 보고서가 없어 통과로 계산하지 않았고, 같은 6·8건을 나눠 실제 완료한 결과만 합쳤다. 실패 시도의 종료·자료 정리도 확인했으며 개별 검사/주문 제한은 바꾸지 않았다. [Windows](../../tmp/s12-04-shadow/s12-05-close-acceptance-related/evidence.json) · [PostgreSQL](../../tmp/s12-05-close-acceptance/pg-evidence.json) · [PG 실행1 종료](../../tmp/s12-03-pg-baseline/s12-05-close-acceptance-deadline/owned-job.json) · [PG 실행2 종료](../../tmp/s12-03-pg-baseline/s12-05-close-acceptance-runner/owned-job.json) · [증분 대조](../../tmp/s12-05-close-acceptance/review-verified.json).

이로써 기존 12.12의 항목 3을 개발 수락한다. 대표 수락은 3/11이고 전체 단계는 4/5다. 다음은 항목 4의 신규주 두 조합을 명시 원계획·주문·복구와 연결한다. 실제 운영 전환·NAS 변경·외부 공개는 수행하지 않았다.

<a id="s12-ipo-final-lineage"></a>

### S12-05 항목 4 선행 · 신규주 두 조합의 최종 명세와 Shadow 연결

일반 CLOSE만 허용하던 최종 후보 형식을 신규주 종가베팅/돌파에도 연결했다. 기존 `final_research_candidate/v1`과 CLOSE 내용/hash를 유지하며 자금·명시 정책·KRX 완료봉 실행·동결한 거래일/상장 참조 digest를 원본 final run과 대조한다. 같은 내용의 참조를 다른 경로에 두면 같은 후보이고, 참조 변조는 final 접근 전에 거절한다. 일반 연구의 입력·성공 기준·수치 기본값은 추가하지 않았다.

실제 final 준비/실행·cache·후보 게시/조회·CandidateMonitor를 두 방식과 상장 첫날/이후의 네 조합으로 검증했다. 신규주 종가베팅은 Paper BUY4→SELL2→SELL2, 돌파는 같은 날 BUY4→SELL4이며 상장 자료가 뒤늦게 SPAC으로 정정돼도 최초 셋업/상장 단계/참조는 유지한다. 표시에서는 신규주 두 방식을 일반 종가베팅과 구분한다. 이는 연구/Paper와 후보·Shadow 선행 연결이다. **IPO READY와 native 주문·보유/복구는 다음 작업**이며 유효한 CLOSE 실행 조건을 전달해도 IPO 주문 권한을 얻지 못한다.

처음 구조 검사가 후보 검증→연구 adapter의 새 순환을 찾았다. 기존 순수 `close_and_ipo`의 정책 해석을 두 소비자가 공유하도록 정리해 순환을 제거했다. 새 owner/worker/DB/API 형식/라이브러리나 구조 한도 증가는 없다. PostgreSQL 표준 실행기에서 드러난 검사 준비의 단축 가져오기와 무관한 테스트의 사전 로딩은 해당 두 검사 모듈의 저장소 기준 가져오기·자기 setup 수명으로 바로잡았다. 별도 실행기 우회나 개별 검사 제한 변경은 하지 않았다. 격리 import/준비 probe도 통과했다.

최종 소스 `3e6e218a`의 Windows 고유 202건/12모듈과 PostgreSQL 고유 16건이 통과했다. PG는 신규주 네 조합의 native 후보 저장/조회 1건과 기존 CLOSE 실행/보호/복구 소비자를 포함한 16건이다. 같은 검사 파일의 기존 증분 기록 대조에서 요구된 Windows14·PG13은 앞선 188·3건을 반복하지 않고 보완했다. 새 고유 검사는 final 네 조합·UI 구분·PG 네 조합의 3건이며 이전 CLOSE final의 폐기된 일괄 차단 문구 기대값은 현재 명시 실행 정책 거절로 바꿨고 ValueError·단계/주문 0 검증은 유지했다. 구조·소비·증분 실행 대조가 통과했고 원감사·미결49건을 유지했다. Windows 소유 작업자 13개와 최종 PG 클러스터 3개의 종료/정리를 확인했다. 선행 Windows176 두 판본은 현재 통과로 합치지 않았고 PG 준비 가져오기 실패 두 번은 실패 자료로 보존한다. [Windows](../../tmp/s12-05-ipo-final/windows-evidence.json) · [PostgreSQL](../../tmp/s12-05-ipo-final/pg-evidence.json) · [PG 종료](../../tmp/s12-03-pg-baseline/s12-05-ipo-final-qualified/owned-job.json) · [증분 대조](../../tmp/s12-05-ipo-final/review-verified-complete.json).

항목 4의 최종 명세·Shadow 선행 조건을 닫으며 native 실행과 전체 항목 4는 미완료다. 다음은 원래 상장 프로필을 가진 실제 모의 계획의 진입·부분 청산/당일 종료·보호·재시작이다. 대표 수락3/11·전체4/5와 실제 운영 제외를 유지한다.

<a id="s12-ipo-native-claims"></a>

### S12-05 항목4 선행 · 신규주 원계획의 native 진입/청산 claim

기존 CLOSE 어댑터·명시 가격 조건·계좌 Risk/gate·native 주문 트랜잭션·계획 CAS/FILL을 두 IPO 방식에 재사용했다. 현재 Shadow와 불변 후보의 family를 같은 transaction에서 대조하고 원래 셋업/상장 참조를 보존한다. CLOSE의 기존 스냅샷/hash 표현은 유지하고 IPO 청산은 v4로 실제 보유·이미 청산한 수량을 함께 고정한다. 새 worker·권한 owner·테이블·라이브러리는 없다.

내부 합성 명세로 실제 CandidateMonitor→가짜 브로커→native 원장을 검사했다. IPO 종가는 BUY4 뒤 선언한 2주 tranche 중 SELL1만 체결·잔량 취소가 브로커에서 확정된 표본에서 다음 SELL1만 생성하고, 이미 합계2주를 청산하면 같은 갭에서 더 매도하지 않는다. 실제 BUY2만 체결됐으면 2주 tranche로 잔여를 없애지 않고 보유한다. DB/런타임 재개 후 원래 익일 기한에 잔여2주를 청산한다. 여기의 취소 확정은 명시한 합성 broker 응답이며 자동 매도 취소/정정 수명 검증은 아니다. 돌파는 원래 돌파선과 같은 가격에서는 유지하고 미만 또는 원래 당일 기한에 실제 BUY3만 청산한다. 두 방식의 상장 첫날/이후를 확인했고, 현재 Shadow OFF/SPAC 변경도 원래 계획·상장 단계를 다시 쓰지 않는다. 원래 상장 근거를 승인 이후 변조하면 주문 전 거절하고 재요청은 매수1회다.

소스 `cd8e60b5`에서 Windows 관련74건/5모듈과 PostgreSQL 고유8건이 통과했다. 새 고유 검사는 SQLite4·PG4이고 PG 기존 CLOSE 진입·구형 증거·부분 취소 응답 유실/늦은 체결·완료 갭4건을 함께 확인했다. 원감사/미결49건과 기대값을 보존한 증분 실행 대조·구조·저장소 소비 검사를 통과했다. Windows 작업자5개와 PG 클러스터2개는 종료·자료 정리를 확인했다. 첫 입력 준비의 세션 판본·단계 동률 오류 및 누락된 family 어댑터 분기를 수정했고, 앞선 소스의 probe 통과를 현재 증거로 합치지 않았다. [Windows](../../tmp/s12-04-shadow/s12-05-ipo-native-claims/evidence.json) · [PG](../../tmp/s12-05-ipo-native/pg-evidence.json) · [증분 대조](../../tmp/s12-05-ipo-native/review-verified.json).

**미완료:** 이 검사는 명시한 내부 합성 spec의 하위 주문 경계다. 일반 IPO READY 게시 차단은 유지한다. 기존 Episode의 자동 진입/보유·부분 체결 취소/늦은 체결·첫0B 구조 실패 후 반등에도 보호 보존·UI/API/일지까지 연결해야 항목4를 수락한다. 현재 fresh quote에서 구조 실패를 재검증하는 것만으로 최초 교차 보호 완료라고 하지 않는다. 대표3/11·전체4/5 유지.

**미체결 관리 지시 대조:** 이전 「자동매매 개발」 대화의 사용자 지시는 WAIT/CANCEL/REPRICE·REENTER/ABANDON을 목적·현재 타점/슬리피지/EV/위험으로 선택하고, 취소 확정 전 재주문·UNKNOWN 중복 제출을 금지하는 것이다. 모든 미체결을 무조건 취소하는 정책이 아니다. 2026-11-05 후속 지시에 따라 이미 접수된 같은 방향 주문의 미체결 가격 변경은 TR 부담을 줄이는 정정을 우선하며, 정정의 원주문/부분 체결/응답 유실 대사를 기존 실행 수명에 연결한다. **현재 조건을 다시 판단해 주문한다는 원칙은 정정에도 그대로 유지한다.** 주문 필요성·목적/목표·현재 체결/미체결·타점/위험과 명시 가격 조건을 재평가한 뒤, 같은 방향의 유효한 잔여만 정정한다. 조건이 사라진 매수는 취소/포기하고 보호 청산의 위험 우선은 유지한다. 가격만 기계적으로 추격하지 않는다. 현 IPO 하위 검사는 아직 그 자동 정정 기능을 구현·수락한 증거가 아니다. 주문 목적을 바꾸거나 정정을 지원하지 않는 경우의 취소·재주문과 구분한다. 실제 운영 전송은 이번 승인 범위 밖이다.

<a id="s12-ipo-owned-runner"></a>

### S12-05 항목4 · 기존 자동 Episode와 최초0B 구조 보호

기존 Supervisor/Runner/Episode·계좌 monitor/Gateway·ClosePlanManagement에 IPO 두 방식을 연결했다. 계획 입력은 기존 RAM map과 불변 entry snapshot이 소유하고, 최초 주문 dispatch 중에도 원래 candidate를 잠시 보존해 ACK된 첫 체결의 보호가 새 DB 조회를 요구하지 않게 한다. 장중 돌파에는 익일 세션을 만들지 않으며 원래 당일 기한/돌파선과 상세 체결 수량만 사용한다. 종가는 기존 달력·완료 갭 규칙의 부분 tranche/잔여를 사용한다. 새 task·권한 owner·테이블·라이브러리·연구 조건은 없다.

`ipo_structure_stop/v1`은 원 spec/package·execution run·candidate event/snapshot·돌파선과 최초0B 가격/시각/ref·ACK된 실제 원가/수량을 결속한다. 현재 비율 손절에 닿지 않았어도 원래 돌파선 미만이면 기존 보호 요청을 고정/깨운다. 원래 가격이 반등해도 최초 요청을 유지하고, 기존 durable checkpoint 이후에만 native 취소를 허용한다. native 검증은 원래 셋업과 과거 Risk/상세 매수 FILL prefix를 다시 대조한다. 기존 비율·Leader 절대가격 보호는 유지한다.

실제 공개 Supervisor 기동을 명시한 내부 합성 저장 spec으로 검사했다. 과거 Shadow seed는 주문하지 않고 이후 새 신호 BUY4@107만 받는다. 두 상장 단계 모두 상세 체결2 후 원래 돌파선104 아래 첫0B103을 고정했고, 비율 손절선에는 닿지 않았다. 체결 cut보다 앞선 가격은 새 손절을 만들지 않고 106 반등도 최초 요청을 덮지 않았다. callback DB 읽기/쓰기0이며 돌파선·원 event·수량 변조는 거절했다. durable 최초 요청으로 매수 잔량을1회 취소하고 취소 중 늦은 BUY1을 보존했다. 실제 DB/런타임을 다시 열고 Shadow OFF·과거 신호 재스캔0에서, 최초 가격이 신선도 기한을 넘은 뒤에도 새106원/실제3주만 청산했다. 종가도 원래 진입 기한 취소·늦은 BUY1·DB 재개 뒤 native 완료 갭으로2주를 줄이고 원래 익일 기한에 잔여1주만 청산했다. 원래 셋업/상장 단계·계획은 끝까지 유지했다.

소스 `56e8585d`에서 Windows72건/4모듈과 PG고유8건을 통과했다. 새 고유 검사4건은 같은 두 자동 수명의 SQLite/PG 검사이며 각각 두 상장 단계를 실행한다. PG는 IPO기존4+새2와 기존 CLOSE 최초 보호/반등/늦은 체결·native checkpoint/FILL cut 반례2다. 구조·저장소 소비 delta0·증분 실행 대조를 통과했고 원감사/미결49건을 보존했다. Windows 소유 작업자4·PG 클러스터2의 종료·private 자료/port/password/handle 정리를 확인했다. 사전 probe6건 통과와 최종 소스의72/8건을 구분한다. [Windows](../../tmp/s12-04-shadow/s12-05-ipo-owned-runner/evidence.json) · [PG](../../tmp/s12-05-ipo-runner/pg-evidence.json) · [증분 대조](../../tmp/s12-05-ipo-runner/review-verified.json).

이로써 항목4의 자동 원계획/최초 구조 보호 연결을 닫는다. 일반 IPO READY 게시 차단은 아직 유지하며 정상 후보 선택/게시·UI/API·일지와 미체결 정정 우선은 다음 작업이다. 접수된 같은 방향 미체결의 정정에도 **현재 조건 재판단 → 필요한 잔여 목표만 실행**을 유지한다. 현재 `REPRICE` 경로는 취소 확정 후 신규 주문이며 브로커 `kt10002` 정정 구현과 구분한다. 정정 원문 사본은 원주문번호·정정수량/가격과 새 응답 주문번호/모주문번호를 제공하므로 원주문/FILL 계보를 보존해 연결한다. 단순 가격 추격·응답 불명 재전송은 하지 않는다. 대표3/11·전체4/5와 실제 운영 제외를 유지한다.

<a id="s12-ipo-normal-ready"></a>

### S12-05 항목4 · 신규주 정상 READY·명시 화면·API 연결

검증된 신규주 native/자동 수명 위에 일반 READY builder/서버 게시·조회와 Qt 선택창을 연결했다. 두 방식은 자신의 family/final 계보·Shadow·KRX 명시 가격 범위와 v1/v2 중지 관리 선택을 유지한다. 기존 CLOSE 형식/hash·기본 안전 계약은 유지하고 후보 적격이나 READY 게시만으로 주문하지 않는다. 화면은 신규주 종가/돌파를 구분하며 빈 가격/미선택 관리 정책은 게시할 수 없다. 검토창 취소는 서버 게시/주문을 만들지 않는다.

실제 final 실행/동결 참조/불변 후보→CandidateMonitor→정상 READY 게시/HTTP 재조회까지 두 방식×두 상장 단계로 검증했다. 잘못된 final 계보·현재 binding은 계속 거절하고 게시 응답 `orders_started=false`·주문0을 확인했다. 네 조합의 native/자동 fixture도 이제 직접 내부 spec 저장이 아니라 동일 일반 builder/서버 publisher를 거친다. 이후 공개 Supervisor 활성화→원계획 진입/첫 구조 보호·부분/늦은 FILL·재개/부분·잔여 청산을 다시 확인했다.

정상 게시에 필요한 동일 시각 evaluated/validated의 단계 순서 결함을 수정했다. 기존 native writer와 repository reader가 hash의 사전순으로 동률을 풀면 나중 validated가 evaluated보다 앞서 보일 수 있었다. 새 domain `strategy_stage_revision_sort_key`를 두 소비자가 공유하며 원래 시각·ID/hash는 그대로 보존한다. 실제 source finish/evaluation의 동일 시각을 둔 정상 게시·멱등 재조회와 기존 predecessor/CAS·rollback/commit 비용 검사를 통과했다. 임의 microsecond를 추가하거나 원본을 다시 쓰지 않았다.

최종 소스 `582975a9`에서 Windows163건/6모듈·PostgreSQL8건이 통과했다. PG는 정상 READY를 거친 IPO native4/Runner2, 실제 final 네 조합의 HTTP 게시/조회1, 기존 CLOSE 진입1이다. UI 고유 검사1건을 추가했고 기존 IPO UI/최종 후보의 이전 blanket-block 기대값은 새 정상 경로와 구체적인 가격/계보 거절로 전환했으며 게시/주문0·변조/기존 안전 assertion은 유지했다. UI의 무관한 과거 fixture class-body import는 실제 setUp 안으로 옮겨 모듈 단독 조회가 과거 연구 fixture를 미리 요구하지 않게 했고 관련 기존50건을 실행했다. 원감사/미결49·증분 실행·구조/저장소 소비 delta0을 통과했다. 소유 Windows 작업자6/PG 클러스터2의 종료·private 자료/port/password/handle 정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-ipo-ready/evidence.json) · [PG](../../tmp/s12-05-ipo-ready/pg-evidence.json) · [증분 대조](../../tmp/s12-05-ipo-ready/review-verified.json).

일반 신규주 READY/UI/API 연결을 닫는다. 대표 항목4 전체는 일지와 현재 조건 재판단을 유지한 미체결 정정 우선이 남아 미완료다. 다음은 같은 원계획·상장 프로필·실제 체결을 일지/화면과 대조하고 접수 주문의 정정/원번호 계보를 기존 수명에 연결한다. 실제 브로커 주문·운영/배포·push 없이 진행한다. 전체4/5·대표3/11 유지.

<a id="s12-acceptance-ipo-four"></a>

### S12-05 대표 시나리오4 개발 수락 · 신규주 두 조합과 원계획 일지

정본12.12 항목4의 네 조건을 기존 검증과 현재 연결로 대조했다. **부족한 장기 이력의0 보정 없음**은 실제 짧은 명시 분봉/상장 입력을 소비하고 연속 분/상장 유형·단계 결손을 구분하는 기존 CloseIpo 검사와 실제 final 네 조합으로 확인했다. 불변 순수 정책의 이전 근거는 재사용했다. **실제 지원 조합**은 IPO_CLOSE/IPO_BREAKOUT×상장 첫날/이후 네 조합의 원 final·후보/Shadow·정상 READY·명시 UI/API→공개 자동 기동을 거쳤다. **같은 계획 ID**는 native entry/실제 상세 FILL/부분·잔여/재시작/일지·화면 조회까지 동일 ID와 원 policy_ref다. **보유의 소급 재분류 없음**은 늦은 SPAC 정정·새 source OFF/빈 설정·일자 경계/DB 재개에도 원래 셋업·상장 단계/원계획을 보존한다. 일반 전략으로 자동 교체하지 않는다.

추가 제품 코드는 필요하지 않았다. 기존 cursor projection과 별도 일지 DB·TradeHistoryQueryService를 실제 신규주 자동 시나리오의 마지막 체결 뒤 연결했다. 돌파는 상세 BUY2+늦은 BUY1/SELL3의3개 실행 ID, 종가는 BUY2+늦은 BUY1/SELL2/잔여SELL1의4개다. 두 방식/두 상장 단계 모두 일지를 다시 열어 BUY3/SELL3·native/account잔량0과 원래 계획 ID/정책 증거를 확인했다. 독립 broker 요약을 별도로 저장하고 실제 화면 조회에서 전부 같은 원계획에 연결했다. cursor 재요청은 새 저장0이며 원래 일지 행을 바꾸지 않았다.

최종 소스 `3b75e1cf`에서 Windows18건(IPO6·기존 일지12)과 PG IPO6건을 통과했다. 수정한 IPO helper의 실제 소비자만 선택했고, 변경 없는 UI/최종 연구/활성화·CLOSE의 직전 수락163/8근거를 보존해 반복하지 않았다. 구조·증분 실행 대조/원감사·미결49보존을 확인했다. 제품 소스/저장소 소비는 직전217328d와 동일하므로 이전 소비 감사 delta0을 재사용했다. 소유 Windows 작업자2/PG 클러스터2의 종료·private 자료/port/password/handle 정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-ipo-journal/evidence.json) · [PG](../../tmp/s12-05-ipo-journal/pg-evidence.json) · [증분 대조](../../tmp/s12-05-ipo-journal/review-verified.json) · [정상 READY](#s12-ipo-normal-ready) · [상장/순수 정책](../../tests/unit/test_close_and_ipo.py).

이로써 원래 항목4를 개발 수락한다. 대표3·4·7·9, **4/11**이며 전체 단계는4/5다. 현재 조건을 다시 판단한 접수 미체결의 정정 우선은 **S12-05 공통 미체결 실행 후속**으로 계속 개발한다. 이를 원래 항목4의 추가 선행/수락 조건으로 승격하지 않으며 기존 12.12 조건과 집계 기준은 바꾸지 않는다. 실제 운영·배포·실거래·push는 수행하지 않았다.

<a id="s12-amendment-transport"></a>

### S12-05 공통 미체결 관리 · 정정1회 전송 선행

후속 사용자 기준은 계속 **현재 조건 재판단→필요한 잔여 목표만 실행**이며, 이미 접수된 같은 방향 미체결의 가격 변경은 정정을 우선한다. 기존 REPRICE는 취소 확정 뒤 신규 주문인 경로로 보존하고 broker 정정 구현과 구분한다. 새 전송 경계는 기존 `KiwoomMockExecutionTransport.amend`와 추가 `BrokerAmendmentSubmission`이다. 원래 KRX mock LIMIT와 양의 명시 수량/가격만 같은 `request_once/_send`로1회 전송한다. 원문 사본436~437쪽의 원/새 주문번호·보고 수량을 보존하고 누적 FILL/잔여 보유로 간주하지 않는다. 거절이나 응답 유실은 자동 재전송/취소·신규 우회를 만들지 않는다. 불일치 응답에서 이미 보고된 새 번호도 `AmendmentResponseUnknown`에 남겨 후속 대사가 버리지 않게 한다.

소스 `5ea0f63c`에서 관련 Windows29건/3모듈이 통과했다. 새 검사5건은 BUY/SELL·보고 수량/0패딩 원번호·수량 축소 응답의 보존, 변조/모호 ACK와 알려진 보고 번호 보존·재전송0, 거절 뒤 우회0, 명시 수량/가격과 지원 주문의 전송 전 거절, 실제 REST client/fake opener의 네트워크 유실(토큰1+정정1요청)이다. 기존 매수/취소·주문 수명·REST client 검사를 함께 확인했다. 구조/저장소 소비 delta0·증분 실행 대조/원감사·미결49를 보존했고 소유 작업자3개가 종료/임시 자료를 정리했다. 새 DB/worker/권한 owner·라이브러리·공개 API·실제 broker 요청은 없다. [Windows](../../tmp/s12-04-shadow/s12-05-amend-transport/evidence.json) · [증분 대조](../../tmp/s12-05-amend-transport/review-verified.json) · [원문 고정 계약](../reference/키움_REST_API_로컬_텍스트_색인.md#kt10002---주식-정정주문-2026-11-05-원문-사본-확인).

전송 메서드만으로 자동 정정 수명을 완료했다고 하지 않는다. 다음은 현재 조건을 다시 판단한 명령의 durable claim→원주문/정정 번호·부분/늦은 FILL·예약→거절/응답 유실/재시작 대사와 기존 Gateway/Episode 연결이다. native 저장/회복 경계는 아직 변경하지 않았으므로 새 PG 수락은 이 단계에서 요구하지 않았고 이후 변경 단계에서 검사한다. 실제 운영 전송은 제외한다. 전체4/5·대표4/11 유지.

<a id="s12-amendment-account-input"></a>

### S12-05 공통 정정 · 기존 계좌 조회의 원주문/방향 보존

기존 ka10075/ka10076 페이지의 `orig_ord_no`를 optional `BrokerOrderDetails.original_broker_order_id`에 보존하고 매수정정/매도정정의 방향을 해석한다. 선행0을 유지하며 누락/전부0은None이다. BrokerOrderSnapshot·상세 FILL·권한/귀속은 바꾸지 않는다. 현재 노출 판단의 기존 명시 수량/방향/가격/거래소 소비도 유지한다. 원주문 번호만으로 새 주문을 자동 귀속하거나 승인하지 않는다.

현재 source `e163e50d`의 Windows48건/4모듈(조회14·실전 reader·모의 monitor·계좌 전체 노출 소비자)이 통과했다. 새2건은 실제 공유 reader의 모의5/실전6개 기존 조회만으로 원0000139/새0000140·매수와 원0000141/새0000142·매도, 예약140000/가용640000/보유10·기존 상세ID를 확인하고 open/filled 양쪽의 결손 원번호와 가짜 상세 체결0을 대조한다. 모두 가짜 broker 응답이며 실제 계좌 조회0이다. 구조/저장소 소비 delta0·증분 대조/원감사·미결49를 보존했고 소유 작업자4의 종료/임시 자료 정리를 확인했다. [실행](../../tmp/s12-04-shadow/s12-05-amend-account/evidence.json) · [증분 대조](../../tmp/s12-05-amend-account/review-verified.json).

다음은 원주문과 정정 주문 각각의 상세 체결·가용 시각을 기존 논리 의도/계획에 보존하는 native 수명이다. 현재 원장은 broker 번호 하나와 단일 누적 체결 cut을 사용하므로 번호만 교체하면 늦은 원주문 FILL을 거절/누락할 수 있다. 기존 단일 계좌 owner/transaction·불변 intent/plan·durable-before-submit은 재사용하며, 이 입력 보존을 자동 정정/ACK 복구 완료로 처리하지 않는다. 전체4/5·대표4/11과 운영 제외 유지.

<a id="s12-amendment-physical-ledger"></a>

### S12-05 원/정정 주문별 체결·사전 저장·재개 기반 — 2026-11-06

기존 ExecutionRecord에 선택적 physical broker legs·amendment command·native revision을 추가했다. 원래 intent/계획/가격/수량은 불변이고 기존 미정정 저장 문서에는 새 필드가 없다. 정정1회 전송 전 같은 owner/transaction으로 AMENDMENT_STARTED를 저장하며 UNKNOWN/PENDING은 재시작과 원주문 전량 체결 뒤에도 활성 SUBMISSION_UNKNOWN으로 유지한다. 불명확 응답의 새 번호는 관측값으로만 보존한다. 원/새 번호의 cut·raw 체결번호는 각각 관리하므로 다른 주문의 동일 체결번호와 더 늦게 도착한 고유 상세 체결이 누락되지 않는다. 전체 체결은 주문별 max(상세,보고)의 합이며 실제 FILL 사건은 원번호/원체결번호 그대로 기존 계좌 사건 reader에 연결한다. 부분 정정은 실제 수락 수량만 이전하고 두 잔여를 보존하며 한쪽 취소를 전체 취소로 처리하지 않는다. stale legacy writer가 명령을 덮으면 사건 insert까지 같은 native transaction에서 롤백한다. 기존 계좌 owner/lease·원장·조회·수명을 재사용했고 새 schema/collection/worker/TR는 없다. Offline PositionAccount 입력 decoder도 typed legs/command를 복원한다.

최종 source `ecd5f06b5b7f11492da7638d74f82e3b5fa26d4225e6f6fc18b8bd7a2e475ae8`에서 **Windows115건/7모듈 + 실제 격리 PostgreSQL9건**을 수락했다. 신규8/7건과 기존 등록 검사2건은 매수/매도6주의 원상세2→실제 정정3/원잔여1→혼합 보고5→더 오래된 고유 상세→최종 상세6, 같은 raw ID의 물리번호별 중복 방지, 독립 재열기/계좌 사건, 부분 이전2·각 취소·늦은 FILL, 정정 전 저장 직후 중단·UNKNOWN 재전송0·stale rollback·만료 lease·구 진입 승인 재사용 금지를 대조한다. 기존 주문/미체결·원장·target·CLOSE 취소/보호·native 계획/PG 계측 검사는 그대로다. 구조1327site/위반0·소비 delta0·증분 정적7253/기존미결49 보존과 worker7/PG job·DB/port/private 정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-amend-ledger-accepted/evidence.json) · [PG](../../tmp/s12-03-pg-baseline/s12-05-amend-ledger-accepted-module/postgres-run.json) · [증분 대조](../../tmp/s12-05-amend-ledger/review-verified.json).

첫 source `c83b66dc`는 Windows113PASS/PG8PASS1FAIL이었다. 기존 PG 원장 계측이 miss 후 별도 조회의 sql_calls2를 거부했다. 해당 기존 기대값은 바꾸지 않고 indexed direct CTE와 miss 전용 JSON-leg 분기를 같은 한 문장으로 합쳤다. 최종 원/새 번호 모두 reader 연결/transaction/SQL1을 유지한다. JSON-leg 분기 비용은 miss 경로에 남으며 전체 성능 향상을 주장하지 않는다. 등록 검사에서 core 순서 지문 변경을 거부한2건은 지문을 변경하지 않고 기존 필수 account-order-safety profile에 신규 unit module을 배치해 종결했다. 기존 core/CI 보호 검사는 유지하고 최종 등록2건을 별도로 통과했다. [최초 실패](../../tmp/s12-03-pg-baseline/s12-05-amend-ledger/postgres-run.json) · [원문](../../tmp/s12-03-pg-baseline/s12-05-amend-ledger/official-runner.log) · [정확한 구조 비용 계약](guardrails.json).

이는 **수명 기반 개발 수락**이며 자동 정정 완료가 아니다. 자동 계획의 AMENDMENT_STARTED는 현재 조건/native Risk 승인 연결 전까지 구 진입 승인으로 열리지 않는다. 다음은 pending/UNKNOWN 명령과 ka10075/ka10076의 parent/새 번호·상세 체결을 대조하는 복구, ACK보다 이른00 관측 보존/결합, 원/정정 leg 예약과 현재 가격을 보는 Risk/보호·일지/전송 증거 소비자, 현재 조건 재판단을 실제 Gateway/Episode에 연결하는 작업이다. 미확인 child를 symbol만으로 귀속하거나 ACK를 FILL/원주문 소멸 증거로 삼지 않는다. 기존 대표4/11과 운영 제외/전체 완료 기준은 변경하지 않는다.

<a id="s12-amendment-recovery"></a>

### S12-05 정정 응답 유실·조기 체결의 계좌 대사와 재개 — 2026-11-06

기존 계좌 cut의 원번호·새 번호·실제 주문 수량/가격·방향·거래소가 pending 정정과 하나의 물리 주문으로 대응할 때만 기존 의도에 결합한다. 같은 native owner/lease/transaction에서 해당 복구 입력을 다시 대조하며 ACK 시각이나 FILL을 만들어내지 않는다. 과거·미래 cut/결손/상충·다중 새 번호·잘못된 방향/가격/수량/거래소와 native 직전 변조는 UNKNOWN을 유지하고 전송0이다. 원문 PDF781쪽의00 원번호904를 optional metadata로 보존하며 원번호 선행0·없음/0의 미확인을 유지한다. 원번호가 확인된 조기00의 실제 unit ID·수량·가격·시각은 기존 사건 원장에 AMENDMENT_FILL_OBSERVED로 먼저 내구 저장하고 계획 포지션에 합산하지 않는다. command의 pending keys와 같은 native revision CAS가 귀속 전 자료를 활성 UNKNOWN으로 보존한다. 계좌 대사/known ACK 후 같은 원장의 관측을 기존 물리 FILL 경로로 한 번만 반영하고 marker 제거를 함께 저장한다. binding COMMIT 직후 중단·재열기와 응답 도중 체결도 보존한다. 이미 원주문이 목표 수량만큼 체결됐더라도 별도 물리 잔여/미반영 관측을 완료 상태로 숨기지 않는다. 요청 수량을 넘는 합성 충돌은 raw 관측/UNKNOWN을 유지하고 기존 수량 cap을 완화하지 않는다.

실제 monitor의 기존00/04 queue·owned/drain·전체 계좌 query를 그대로 사용한다. 기존 account-wide before cut이 있으면 재사용하고, 없을 때만 전체 broker recovery당 기존 run-active reader를 한 번 사용한다. 일반 회복에서 정정 작업이 비었으면 주문 operation lock을 기다리지 않는다. PositionAccount 관측은 원/새 물리 번호의 실제 주문 수량·각 가격/잔여와 동일 intent_ref를 대조하고, 이미 대사한 terminal 결과를 재사용해 활성 원장 제거도 확인한다. 새 TR·worker·DB schema/collection·주문 권한·라이브러리는 없다.

source `e444f09ef7d25476f0e1581c7e2c6900a825159478e2582bfb3d27219378a070`의 **Windows224건/12모듈 + 실제 격리 PostgreSQL13건**을 수락했다. 신규10건을 포함한 unit14/PG11 모듈과 직접 영향 검사는 native 복구 입력 변조·결손/상충·restart 중간 중단·원/새 같은 체결번호·조기00→기존3회 recovery→상세3주·물리 예약210200/두 잔여·cap 충돌의 내구 보존을 대조했다. 기존 parser·실전/모의 monitor·미체결/원장·계좌 노출·barrier/종료·CLOSE 계획·일지·실제PG 권한/SQL1 검사를 보존했다. 구조1327/위반0·소비 delta0·증분7263/기존미결49·worker12와PG DB/port/private 정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-amend-recovery-final/evidence.json) · [PG](../../tmp/s12-03-pg-baseline/s12-05-amend-recovery-final/postgres-run.json) · [증분](../../tmp/s12-05-amend-recovery/review-verified-final.json).

추가 SQLite 합성 terminal 실험1건은 실제 상세6주→native 활성0/화면용 미체결0·동일 물리 두 번호의 종결 관측을 확인했다. 이 실험은 정규224/13건과 따로 기록한다. [실행](../../tmp/s12-05-amend-recovery/terminal-family-probe.json).

선행31건 probe의2FAIL은 일반 계좌 조회가 비어 있는 정정 operation lock을 기다린 원인과 새 테스트의 관측 시점 순서였다. 기존 계좌 검사/기한·가짜 transport의 사전 저장 assertion은 유지하고 빈 작업은 lock 전에 반환하며 새 가짜 요청은 최초 사전 저장 assertion 후00을 전달하도록 고쳤다. 같은 두 모듈32건 재검증과 최종224건을 분리 보존한다. [최초](../../tmp/s12-05-amend-recovery/probe.json) · [관련 재검증](../../tmp/s12-05-amend-recovery/probe-v2.json).

이 수락은 복구/계좌 관측의 개발 근거이며 자동 정정·현재 조건/Risk 승인·각 전략의 보호/일지/실제 운영 수락은 아니다. 원번호가 결손인00은 이 번호만으로 plan 귀속하지 않고 계좌 대사로 확인된 사실만 사용한다. 다음은 실제 현재 조건 재판단→native 승인→기존 Gateway/Episode와 physical 예약·보호/전송 증거 소비자다. 전체4/5·대표4/11과 운영 제외/원래 완료 기준은 유지한다.

현재 단계의 첫 정규 source `bda53781`도224/13PASS였지만 별도 부분 취소 terminal 실험이 보유1/활성 주문0을 불일치로 표시했다. 원인은 미사용 할당량과 취소 후 실제 미체결을 같은 값으로 비교한 소비자였다. 해당 비교에서 terminal 물리 주문의 broker 미체결0을 사용하고 기존 partial-transfer/cancel 공유 unit/PG 시나리오에 보유1·미체결0·관측 완료를 추가했다. 원래 cap·취소/늦은 FILL·queue deadline과 기존 assertion은 유지한다. 최종 source의224/13 및 증분 대조로 재검증했다. [최초 정규](../../tmp/s12-04-shadow/s12-05-amend-recovery/evidence.json) · [부분 취소 재현](../../tmp/s12-05-amend-recovery/cancelled-terminal-probe-failed.json) · [같은 입력 보완](../../tmp/s12-05-amend-recovery/cancelled-terminal-probe-passed.json).

<a id="s12-close-current-order-management"></a>

### S12-05 종가베팅·신규주 현재 조건 재판단과 native 정정 — 2026-11-06

기존 명시 게시의 새 v3/v4 정책은 **현재 조건 재판단 후 정정 우선**이다. v3은 중지 뒤 대사만, v4는 명시한 원계획 관리를 유지한다. 구형 v1/v2 저장 표현·hash·권한은 변경하지 않으며 자동 승격하지 않는다. Qt 게시와 저장 명세 표시에 새 선택을 연결했다. 게시만으로 주문하지 않는다.

기존 ClosePlanManagement/Episode의 승인된 종가베팅·신규주 두 방식은 현재 Shadow/제어, 현재 0B, 현재 계좌·Risk·실제 물리 잔량과 원래 셋업/허용 가격을 다시 판단한다. 현재 유효하면 기존 Gateway의 명령·종료 소유와 Runtime 잠금 안에서 한 물리 잔량을 정정한다. 매수 조건 해제·원래 신호 만료·신규주 돌파 상실·예산/위험 조건 미충족은 잔량 취소다. 이미 전송한 주문의 최초 전송 TTL을 새 진입 허가로 재사용하지 않으며, 원래 신호 유효기간 안의 관리와 구분한다. 청산은 최초 고정 보호 근거·가격 허용 범위와 현재 계획의 실제 보유 잔량을 사용하고 가격 기준을 새로 옮기지 않는다. UNKNOWN은 기존 계좌 대사만 요청하며 정정·취소·새 주문을 재전송하지 않는다.

실제 native 주문 트랜잭션은 기존 owner/lease/control, 최초 승인/셋업/청산 근거, 최신 Risk·계획 head·현재 Shadow와 같은 assessment를 재검증한다. 준비 뒤 설정이 바뀌면 변경 사건/전송 전에 거절한다. AMENDMENT_STARTED의 내구 저장 뒤 한 번만 전송하며, 시간 공급이 진행해도 명령 시작과 사건 수신 시각은 같은 캡처를 쓴다. Risk의 상세 체결은 계좌/환경/일자/물리 주문/체결번호로 구분하고 aggregate debt는 물리 주문별로 대조한다. 실제로 접수·대사한 미체결 매수는 체결 없음의 0 근거가 될 수 있지만 미귀속/미확정/중복·잔량 불일치는 계속 미확인이다. 매도 예약은 취소된 옛 배분이 아닌 현재 활성 물리 잔량이다. 새 worker·권한 owner·schema·라이브러리는 없다. 현재 Shadow 조회1곳은 기존 제한 주기 관리에 추가했고 매 틱 DB 경로를 만들지 않았다. 같은 material 입력의 재평가는 계좌 TR을 추가하지 않는다.

source `ad312cf921683fe1a48eaffd745ca8f6dbc06e8962b7715a0f6070c22334b05b`의 **Windows254건/13모듈 + 실제 격리 PostgreSQL20건**이 통과했다. 신규17건은 두 DB의 현재 관리 각6건, Risk4건, 실제 진행 시계1건이다. 독립 가짜 브로커는 정정3/원주문1, 원래 전송 만료 뒤의 관리, 원주문 늦은 체결, 같은 raw ID의 원1+새3/청산4, 응답 유실 후1회 복구, 중지/v4 보호, 실제 설정 변조의 native 거절과 신규주 두 방식×두 상장 단계를 대조한다. 해당3개 실제 FILL은 별도 일지 DB의 재개/재전달에서 원계획으로 한 번씩 남는다. 반복한 같은 입력2회는 추가 계좌 읽기0이다. 기존 CLOSE/IPO 공개 Runner·READY/화면·원장/계좌/FIFO·Gateway/drain·SQL1/권한 검사는 유지했다. 성능의 벽시간 개선율이나 실증 수익은 주장하지 않는다. 구조1328·위반0, QueryStore125/135/130·검토390·미기록delta0, 증분7280정의/287기록·기존미결49를 보존했다. worker13/TEMP와 PG DB/port/private 정리를 확인했다. [Windows](../../tmp/s12-04-shadow/s12-05-current-management/evidence.json) · [PG](../../tmp/s12-03-pg-baseline/s12-05-current-management/postgres-run.json) · [증분](../../tmp/s12-05-current-management/review-verified-final.json) · [정리](../../tmp/s12-05-current-management/cleanup.json).

첫 probe의 property 호출·계좌 cut 참조와 native 직전 사건 assertion 위치는 새 연결/검사에서 수정했고, 가짜 조회 시계는 각 후속 조회의 실제 순서를 표현하도록1ms씩 진행시켰다. 제품의 strict-newer cut·기존 assertion/제한은 완화하지 않았다. 첫4건 실패와 다음4건 결과, 관련32건 통과는 최종254/19와 구분해 보존한다. [최초](../../tmp/s12-05-current-management/probe.json) · [다음](../../tmp/s12-05-current-management/probe-v2.json) · [관련](../../tmp/s12-05-current-management/probe-v3.json).

이 수락은 종가베팅·신규주의 명시 새 정책에 대한 공통 실행 연결이며 모든 전략의 자동 정정이나 실제 운영 수락이 아니다. 남은 범위는 기존 대표1·2·5·6·8·10·11, DD/MICRO, 다른 전략의 현재 조건/행동 정책과 선별 전송 증거 소비자다. 전체4/5·대표3/4/7/9의4/11과 원래 완료 기준을 유지한다. 운영 주문·NAS 변경/배포·T0/권한 인계·push는 수행하지 않았다.

기존 게시 화면 공용 fixture의 PostgreSQL final 게시 소비자1건을 별도 동일 소스로 실행해 통과했다. 앞선19건과 합쳐20건이며 반복 실행은 아니다. [추가 게시 연결](../../tmp/s12-03-pg-baseline/s12-05-current-management-publication/postgres-run.json).

<a id="s12-selected-amendment-evidence"></a>

### S12-05 항목11 · 정정 원장의 선별 보존과 offline v2 — 2026-11-06

원/정정 주문을 선별 보존하는 기존 stopped-source→capture CLI→offline immutable receipt→inspect/retry 경로를 확장했다. 미체결 정정의 두 물리 주문이 v1에서 빠지고, 원/새 raw 체결번호가 같으면 상세 보존이 거절되며, UNKNOWN의 command/marker도 빠지는 것을 실제 native 모의 원장으로 재현했다. 원본 자료는 변경하지 않고 정정 자료에만 `selected-order-evidence/v2`를 선택한다. 미정정 v1의 직렬화·hash·최초 영수증과 기존 검사는 보존한다. AMENDMENT 사건이 있는 자료를 물리 사실 없이 v1으로 내려 누락시키는 것은 거절한다.

v2는 원래 intent/계획·원/새 주문 ID와 부모/주문 수량·정정 이동량·물리 가격·상세/보고 체결·raw ID/cut·현재 command/assessment·native revision을 보존한다. 같은 raw ID는 물리 주문마다 구분하고 실제 상세 FILL만 계획 수량/커서에 합산한다. 귀속 전 AMENDMENT_FILL_OBSERVED의 structured parent/revision·원시각과 pending key를 보존하고, 복구 후에는 원래 관측과 실제 FILL1개를 함께 남긴다. 부모/이동 수량·상세/총계·관측/marker와 원래 start/상태를 canonical codec에서 대조한다. 일반 자유문 reason과 native owner/권한은 전송하지 않는다. 새 DB schema/worker/라이브러리나 주문 권한 경로는 없다.

source `352bc279fd41b3679b187ca6e0802ed1154d0e18f28a4de3c9362385507b79a3`에서 **신규4건 + 기존 v1 보존1건 = Windows5건/2모듈**이 통과했다. 독립 가짜 브로커와 실제 SQLite 원장을 정지/drain한 뒤 읽었고, 실제 CLI의 pinned canonical bytes, 원4/정정3·이동3, 원/새 같은 raw ID의 상세1+1, 미귀속 관측의 계획0, 복구 뒤 관측1+실제 FILL1, child/이동/상세/marker/원번호/총계 변조·downgrade 거절, 대상의 최초 영수증 재조회·재전달과 원본 byte 불변을 확인했다. 대상 실행 intent/events/control·주문 권한은 생성되지 않는다. 기존 v1의 실제 commit/ACK 손실·rollback·source-free retry·CLI/보존 검사는 기대값 변경 없이 재사용했다. 구조1328·위반0/consumer delta0, 증분7284정의/288기록·기존미결49, worker2/TEMP 정리를 대조했다. backend SQL/native 트랜잭션은 변경하지 않았으며 PostgreSQL 대상 기능을 추가한 것이 아니다. [현재5건](../../tmp/s12-04-shadow/s12-05-selected-amendment-evidence/evidence.json) · [증분](../../tmp/s12-05-current-management/preservation-review-verified.json) · [정리](../../tmp/s12-05-current-management/preservation-cleanup.json).

최초3건 실패는 보존 기능의 실제 누락을 보여준다. 첫 수정의2통과/1오류는 원장에 있는 `broker_revision=...;amendment_parent=...`를 그대로 읽도록 맞춰 관련3건을 통과시킨 뒤, 복구 관측/실제 FILL 분리와 CLI를 추가해 최종5건을 대조했다. 기존 시계·native 수량/권한·검사 기대값을 완화하지 않았다. [수정 전](../../tmp/s12-05-current-management/preservation-before.json) · [첫 수정](../../tmp/s12-05-current-management/preservation-after.json) · [관련 재검증](../../tmp/s12-05-current-management/preservation-after-v2.json).

이는 항목11의 선별 자료 보존 소비자 수락이며 독립 컴퓨터의 계좌/원주문 검증이나 운영 관리 인계 수락이 아니다. 최초 영수증은 계속 `OFFLINE_RECOVERY_REQUIRED`, `order_authority=false`다. 신뢰할 원본·대상의 계좌 연결/namespace, fresh target 계좌·일자/원주문·상세 체결/coverage 근거와 원본/대상 좌표 구분은 기존 재개 조건으로 남긴다. 전체4/5·대표3/4/7/9의4/11과 다른7개·DD/MICRO의 잔여를 유지한다. 다음 독립 개발은 낙주 native 진입/부분 체결·반등·잔량 취소/당일 미완료 연결이며 운영·실계좌·NAS·T0/실제 권한 인계·push는 실행하지 않는다.

<a id="s12-panic-final-shadow"></a>

### S12-05 항목5 선행 · 낙주 P0/P1 최종 후보와 원래 Shadow — 2026-11-06

기존 P0/P1 급락·확인·뉴스 판단과 CandidateMonitor의 `panic_entry_setup` 보존은 재사용하고, 끊겨 있던 **독립 최종 Paper→canonical 후보→immutable 게시→실제 native Shadow 대조**를 연결했다. 기존 generic `final_research_candidate/v1`에 지원 분봉 집합을 별도로 두어 PANIC만 추가하고 CLOSE/IPO 집합·문서 형식은 유지한다. 기존 final claim/owner·동결 reference·원본/구현 hash·캐시·평가 분할·비용·warmup을 그대로 사용한다. 원래 셋업과 P1 다음 완료봉 확인·뉴스 당시 가용 판본을 대조하며, 미래 뉴스나 후속 봉으로 최초 신호를 만들지 않는다. 파라미터/뉴스의 parse와 canonical 표현은 기존 `panic_rebound`에 모아 분봉 설정과 후보 reader가 공유한다. 후보 reader가 분봉 실행 어댑터를 거꾸로 참조하던 새 cycle을 제거했으며 새 계층·worker·DB schema/권한 owner·라이브러리는 없다.

명시한 `panic_allow_unresolved_news` 최종 Paper도 일반 Paper와 같이 연구할 수 있게 했다. flag/미확인 reason·원래 입력과 실제 후속 봉 체결을 그대로 보존한다. 연구 후보의 ELIGIBLE 판정은 실시간 승인과 별개이며, 해당 설정을 기존 native Shadow로 구성하는 것은 계속 거절한다. 새 근거·임계값을 실행 기본값으로 넣지 않았고, 실제 PANIC READY/주문 수명은 다음 연결이다.

source `2c347d86cc95be9e797c7fe98c4a61199ec5e1598c4e84a2479bc7b8a991a299`의 **Windows34건/9모듈 + 실제 격리 PostgreSQL2건**이 통과했다. 신규 unit3/PG1은 P0/P1 최종 실행→캐시→실제 후보 게시→순서 있는 native 분봉 Shadow를 연결하고 원셋업900/실패895/목표945·P1 확인910, quantity4, 실제 수신 이후의 후속 시가920/925와 청산960을 대조한다. 모두 합성 연구 표본이며 숫자는 제품 기본값이나 실증 수익이 아니다. 원래 수신보다 빠른 시가를 체결로 쓰지 않으며, 후기 악재는 cut 이후면 배제하고 cut에 들어오면 진입 대조를 거절한다. target/수량/변형/뉴스 ref와 원실행 parameters 변조도 거절한다. native 게시/Shadow만으로 execution intent/events/lease는0이다. 뉴스 미확인 연구의 실제 final 완료·원래 reason/flag와 native Shadow 구성 거절을 따로 확인했다. 기존 PANIC6건, 일반 Paper/armed 복구·정정, CLOSE/IPO final/READY HTTP·후보, final 소유/캐시/자료/비용 회귀와 처리 코드 부재의 조회 수신/새 서버2건을 재사용했다. [현재32건](../../tmp/s12-04-shadow/s12-05-panic-final/evidence.json) · [조회2건](../../tmp/s12-04-shadow/s12-05-panic-query/evidence.json) · [PG2건](../../tmp/s12-03-pg-baseline/s12-05-panic-final/postgres-run.json) · [증분](../../tmp/s12-05-panic/review-verified-final.json) · [정리](../../tmp/s12-05-panic/cleanup.json).

구조1328·위반0/consumer delta0, 증분7288정의/290기록·기존미결49를 보존했고 worker9/TEMP 및 PG DB/port/private 정리를 대조했다. 초기 probe는 실제 final family 차단과 ordinary-Paper 전용 unknown 제한을 재현했다. 새 fixture의 참조 파일에는 기존 필수 빈 listings를 넣었고, available=end+2초에 맞는 실제 후속 시가와 명시한 초기2분 warmup을 사용했다. 기존 engine/평가 기대값·warmup120·한도/skip/CI를 바꾸지 않았다. 새 cycle은 허용 목록을 늘리지 않고 기존 PANIC parser 책임으로 해소했다. [첫 final 차단](../../tmp/s12-05-panic/probe-v2.json) · [입력 순서 확인](../../tmp/s12-05-panic/probe-v3.json) · [warmup 확인](../../tmp/s12-05-panic/probe-v7.json) · [연결 probe](../../tmp/s12-05-panic/probe-v9.json).

이는 **S12-05 항목5의 선행 결과/Shadow 연결**이며 낙주 native 주문·부분 체결/반등·잔량 취소/늦은 FILL·당일 미완료 관리·일지/READY UI 수락은 아니다. 다음은 이 원래 셋업과 명시 실행 정책을 같은 계좌 owner/native claim·현재 조건 재판단/정정 우선·기존 Episode에 연결한다. 전체4/5·대표3/4/7/9의4/11, 다른7개·DD/MICRO와 독립 계좌 인계의 기존 잔여/운영 제외를 유지한다. 실제 주문·NAS 변경/배포·권한 인계·T0·push는 실행하지 않았다.

<a id="s12-panic-native-plan"></a>

### S12-05 항목5 · 낙주 현재 근거의 READY backend와 native 원래 잔량 — 2026-11-06

낙주 P0/P1의 최종 후보/Shadow를 기존 READY 게시·admission·단일 계좌 Runtime/lifecycle에 연결했다. 명시 `panic-minute-native-plan/v1`과 `PanicMockExecutionPolicy`는 진입 불리 가격 한도·보호 가격 한도·STOPPED 관리 의미를 동결한다. 값을 제품 기본값으로 채우지 않았으며 기존 명세는 새 필드를 생략해 직렬화와 ID를 보존한다. 이 단계의 READY는 backend 경계이며 화면 선택과 자동 Episode 연결은 아래 잔여다.

연구 당시의 기사 ID/절대가격을 미래 진입 조건으로 재사용하던 연결을 제거했다. CandidateMonitor가 실제 현재 설정의 규칙·수량·자본·뉴스 근거를 `panic_entry_parameters`로 원래 셋업과 함께 보존한다. 후보 대조는 검증한 규칙과 명시 수량/자본이 같은지 확인하고 해당 신호의 현재 뉴스 판본을 cut에서 판단한다. 과거 metadata 없는 신호는 이력/비교 reader에서 읽을 수 있지만 새 native 진입은 현재 근거를 요구한다. 시각 미확인·후기 뉴스와 unknown 연구의 원래 구분은 유지한다. 실제 진입은 fresh 승인0B와 원래 실패/반등 구조·신호 유효기한·가격/자본을 다시 판단한다. 원래 접수 승인을 AMEND에 재사용하는 것은 계속 거절한다.

내구 진입/청산 근거는 기존 immutable document owner에 저장하고 QUEUED와 SUBMISSION_STARTED의 기존 native transaction에서 원래 후보/현재 설정·원래 상세 FILL·계획 head/revision·Risk/control/lease를 다시 대조한다. 기존 CLOSE/IPO native reader의 한 SQL 지점을 공통 minute scope로 옮겼으며 새 주문 owner·task·schema·공개 QueryStore API·라이브러리는 없다. exit는 원래 매수가 확인된 terminal 상태가 된 뒤 실제 상세 체결과 현재 계획 잔량만 사용한다. 부분2→취소 응답 UNKNOWN→늦은1은 보유3이며 원래 주문4로 청산하지 않는다. 반등 SELL3의 상세1/확인된 취소 후 남은2는 새 현재 실패 판단과 같은 계획의 새 revision으로 처리한다. 이는 broker가 확인한 종료 뒤의 잔량 처리 검사이며 자동 정정 대신 취소하는 정책을 추가한 것이 아니다.

source `de4456e50daae48e51d940b1d6a03af7f6ef5524ec6b3fff061420e2a8d6376f`에서 **Windows57건/11모듈 + 실제 격리 PostgreSQL9건**이 통과했다. 신규 unit4/PG3은 연구 표본과 다른 날짜·가격945/950·새 뉴스로 P0/P1 READY/native 진입, 불리 가격·실패/이미 반등 거절, 원래 영수증 재시도, 부분/늦은 체결·청산 잔량2·최종0, native 직전 설정/셋업/청산 수량 변조의 전송/예약0을 확인한다. STOPPED `reconcile_only`는 전송0, 명시 `continue_original_plan`은 기존 정규장 profile 밖16:00에서 주문을 보류하고 실제 DB 재열기·다음 평일 fresh 계좌 cut 뒤 원래 잔량3을 overdue 관리하며 STOPPED와 원래 근거를 보존한다. 기존 activation/옛 명세·generic 후보·CLOSE/IPO native/정정·수동 보유 격리·계좌 원장/lease/SQL 계측·인증 READY와 조회 역할 검사를 재사용했다. 모든 시장/계좌/체결은 합성 모의 자료이며 과거 실증 수익이나 실제 운영 수락이 아니다. [Windows](../../tmp/s12-04-shadow/s12-05-panic-native-final/evidence.json) · [PG](../../tmp/s12-03-pg-baseline/s12-05-panic-native-final/postgres-run.json) · [증분](../../tmp/s12-05-panic/native-review-verified.json) · [정리](../../tmp/s12-05-panic/native-cleanup.json).

구조1328/위반0·소비 delta0·증분7295정의/292기록·기존미결49와 worker11/TEMP·PG job/DB/port/private 정리를 대조했다. native reader의 기존 `_close_native_documents` SQL1 지점은0으로 닫고 공통 `_minute_native_documents`의 같은 지점1만 기록했다. 기존 채택 목록이나 런타임 SQL 한도는 초기화하지 않았다. 속도/메모리 개선을 주장하지 않는다. 첫 정규 Windows57건 중 기존 CLOSE 취소1건이 남은 옛 reader 이름으로 실패했고 actual PG8건은 통과했다. 실패를 보존한 뒤 그 호출을 수정하고 기존 PG 취소 counterpart를 추가해 동일 최종 source의 영향 검사를 확인했다. [최초57건](../../tmp/s12-04-shadow/s12-05-panic-native/evidence.json) · [수정 전 PG8건](../../tmp/s12-03-pg-baseline/s12-05-panic-native/postgres-run.json). 초기 새 fixture의 `source_final.finished_at` 필드와16:00의 기존 after-hours에 대한 정확한 `SESSION_PROFILE_MISMATCH` 기대만 바로잡았으며 제품의 시간/세션 제한을 바꾸지 않았다. [최초 fixture 오류](../../tmp/s12-05-panic/native-probe.json) · [세션 기대 확인](../../tmp/s12-05-panic/native-probe-v3.json) · [최종 연결 probe](../../tmp/s12-05-panic/native-probe-v5.json).

**항목5 전체는 미완료다.** 남은 직접 연결은 기존 Episode의 첫0B 실패/반등 고정·깨우기/checkpoint, 부분 BUY 미체결의 현재 조건 재판단→정정 우선/필요 취소와 UNKNOWN 복구, 현재 보유/SELL 잔량 자동 관리, READY UI·실제 화면/일지/API 경로다. 기존 종가베팅·신규주의 현재 정정 정책과 공통 물리 원장·Gateway/command/drain을 재사용하며 새 자동 관리 엔진을 만들지 않는다. 전체4/5·대표3/4/7/9의4/11과 원래 나머지1/2/5/6/8/10/11·DD/MICRO·독립 계좌 인계 및 완료 기준은 유지한다. 실제 주문·NAS 변경/배포·T0·권한 인계·push는 수행하지 않았다.

<a id="s12-acceptance-panic-five"></a>

### S12-05 대표 시나리오5 · 낙주 자동 관리·현재 정정·제품 연결 수락 — 2026-11-06

낙주 P0/P1의 실제 final/현재 Shadow→명시 READY 화면·인증 게시/조회→기존 Supervisor/Runner/Episode→native 주문·보유·일지 경로를 연결했다. 가격 한도와 STOPPED 관리 선택은 사용자가 입력하며 새 기본값을 만들지 않는다. 기존 client는 typed operating_spec 전체를 전송하므로 별도 전달 계층을 추가하지 않았다. 게시·조회만으로 주문하지 않는 기존 경계도 유지한다.

기존 ClosePlanManagement를 같은 수명의 MinutePlanManagement로 옮겨 CLOSE/IPO/PANIC이 공유한다. Shadow 커서·원래 계획·checkpoint는 이 owner가, 명령 직렬화·fresh 계좌 cut·권한/lease·복구·drain은 기존 Gateway/monitor/Runtime/native 저장소가 계속 소유한다. 순수한 물리 주문·계좌·가격/자본 검증만 minute_order_management로 모았고 전략별 원셋업과 실패/반등 조건은 각 adapter에 남겼다. 옛 CLOSE checkpoint/wire는 보존하며 실제 caller·소비자와 정확한 read/forward identity만 이동했다. 새 worker·DB schema·공개 QueryStore API·라이브러리·주문 권한은 없다.

**현재 조건 재판단→유효한 접수 잔량은 정정 우선**을 실제 자동 관리와 native AMEND/CANCEL 재검증에 연결했다. 최초 전송 TTL 뒤에도 현재 판단이 유효한 BUY는 정정하고, 셋업 실패·현재 설정 실효는 취소한다. 원래 진입 승인으로 AMEND를 열지 않으며 native 직전 설정 변조는 전송/사건 변경0이다. UNKNOWN은 재전송하지 않고 실제 계좌 대사로 확인한다. STOPPED/Shadow OFF의 SELL도 원래 청산 근거·보호 한도와 실제 계획 잔량을 다시 확인해 정정한다. 이미 접수된 주문을 무조건 취소→신규로 바꾸지 않았다.

첫 실제0B 실패/반등은 RAM callback에서 고정·깨우기만 하고 DB0이다. 같은 Episode checkpoint의 저장 ACK 뒤 행동하며 native transaction이 첫 관측과 원래 계획을 다시 대조한다. 반등/재하락으로 원래 관측을 덮어쓰지 않는다. 부분 BUY2→취소 UNKNOWN→늦은1은 실제 보유3이다. P1은 첫 반등 뒤 가격이 범위 안으로 돌아와도 원래 반등을 보존하고16:00의 기존 세션 거절로3주를 유지한다. 실제 DB 재열기·다음 거래일 fresh cut 뒤3주 청산, 상세1/확인된 취소 뒤 잔량2→최종0을 확인했다. 무체결 취소로 RAM 계획이 제거된 뒤 늦은1도 기존 계좌 FILL 전달이 해당 원계획만 다시 활성화해 재시작 전 SELL1로 관리한다. accepted SELL 재열기는 중복 전송0이고 Shadow 과거 재스캔은 금지한 상태로 검증했다. 목표 미달 추격이나 미확인 체결의 보유 합산은 하지 않는다.

검증 중 같은 raw 체결번호가 원주문1주·정정 주문3주에 쓰이면 Episode가1주로 표시하는 실제 결함을 재현했다. 번호가 확인된 체결의 중복 키를 intent/물리 주문번호/raw ID로 구분해4주로 바로잡았다. 물리 번호가 없는 구형 자료는 기존 raw ID 중복 방지를 유지한다. 실제 journal projection은 원1+정정3/매도4와 같은 plan_id를 보존하고 재요청 추가 저장0, Episode 잔량0을 대조했다. [수정 전](../../tmp/s12-05-panic/episode-physical-fill-probe.json) · [수정 후](../../tmp/s12-05-panic/episode-physical-fill-fixed.json).

최종 source `0269fdf45dff82b2216c78d554993e1ebf611164aa31136cb89b3dce845cfad5`에서 **Windows115건/10모듈 + 실제 격리 PostgreSQL21건(낙주 전체10·관련 회귀10·기존 화면 소비자1)**이 통과했다. 새 낙주 현재 판단/native 변조·원래 잔량·첫 관측·자동 복구·정규장 밖/익일·일지·정상 READY/API/UI와 기존 CLOSE/IPO·generic FILL/checkpoint/drain·leader 보호·수동 물량 격리·실제PG 원장/lease/SQL 계측을 대조했다. 구조1328site/38retirable·위반0, 소비 protocol125/PG135/SQLite130/검토390·delta0, 증분7310정의/295기록·기존미결49 보존과 같은 소스 지문/실제 실행 ID·소유 작업자10 및 PG 세 클러스터·DB/port/private/TEMP/registry 정리를 확인했다. 기존 UI fixture의 등록 소비자1건은 증분 대조가 누락을 발견해 같은 source에서 추가 실행했다. [Windows](../../tmp/s12-04-shadow/s12-05-panic-management-final-v3/evidence.json) · [낙주PG](../../tmp/s12-03-pg-baseline/s12-05-panic-management-final-v3/postgres-run.json) · [관련PG](../../tmp/s12-03-pg-baseline/s12-05-panic-management-regression-final-v3/postgres-run.json) · [화면 소비자PG](../../tmp/s12-03-pg-baseline/s12-05-panic-management-ipo-compat-final/postgres-run.json) · [증분](../../tmp/s12-05-panic/management-review-verified.json) · [정리](../../tmp/s12-05-panic/management-acceptance.json).

같은 working SELL의 두 무변화 poll에서 불필요한 진입 설정 조회는 **2→0**, 추가 broker 조회는0이다. SELL이 소비하지 않는 진입 설정 조회만 제거했으며 현재 Risk/보호·원장·복구를 생략하지 않았다. 전체 CPU/지연/RSS 개선을 주장하지 않는다. [동일 입력 수정 전](../../tmp/s12-05-panic/working-sell-read-before.json) · [수정 후](../../tmp/s12-05-panic/working-sell-read-after.json). 첫 정규115건의 누락 Risk import·구형 FILL 중복 방지와 옛 모듈을 참조하던 기존 검사1건은 실패를 보존한 뒤 실제 경로/호출을 수정했다. 기대값·안전 한도를 완화하지 않았다. 첫 PG 낙주 전체 묶음은240초 도구 제한에 도달해 미수락으로 남겼고 진행 근거에 따라 도구 예산360초에서 같은 전체10건을 완료했다. 제품 timeout은 바꾸지 않았다. [최초115건](../../tmp/s12-04-shadow/s12-05-panic-management-final/evidence.json) · [옛 호출1건](../../tmp/s12-04-shadow/s12-05-panic-management-final-v2/evidence.json) · [최초PG 도구 timeout](../../tmp/s12-03-pg-baseline/s12-05-panic-management-final/fixture.json).

정본12.12 항목5의 원래 완료 조건인 목표 미달 추격 없음·실제 체결만 보유/매도·늦은 FILL/당일 미완료 관리를 닫았다. **개발 수락은 대표3·4·5·7·9의5/11, 전체4/5**다. 남은 대표1·2·6·8·10·11과 DD/MICRO·독립 계좌/원주문 검증을 그대로 이어간다. 모든 시장/계좌/체결 입력은 합성이며 과거 실증 성과나 실제 운영 수락이 아니다. 실제 주문·NAS 변경/배포·T0·권한 인계·push는 수행하지 않았다.

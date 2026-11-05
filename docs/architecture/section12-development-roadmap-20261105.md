# 12절 개발과 라이브러리 개선 로드맵

**2026-11-05 · 사용자 승인 · DD와 MICRO 추가.** 기본 저장소 `main`, 착수 HEAD `c1e42a294c3294bb6ce648983d359ea3dbc5b614`를 기준으로 한다. 목표는 돌파·종가베팅·신규주·낙주와 추가한 일봉 과대낙폭·VI/호가 초단기의 개발 가능한 요구를 제품과 격리 검증까지 연결하고, 라이브러리와 구조 변경의 효과를 같은 입력으로 판정하는 것이다. 운영 전환은 별도 작업으로 남긴다.

전체 단계와 완료 기준은 이 문서 한 곳에서 관리한다. **S12-01 → S12-02 → S12-03 → S12-04 → S12-05** 순서로 통합한다. 각 단계는 실행할 코드, 직접 관련 검사, 검증 기록과 로컬 커밋을 남긴다. [정본 12절](app-target-20261023.md#12-연구노트-기반-네-운용군의-목표-설계) · [기능별 계약](r3-ownership.md) · [남은 작업](../OPEN_ITEMS.md) · [저장소 전체 로드맵](../../FUTURE_DEVELOPMENT_ROADMAP.md).

**현재 상태.** S12-01~03 수락 · 완료 3/5 · S12-04 진행 중. S12-05의 연구·일지 중 독립적으로 검증 가능한 연결을 먼저 적용했다. 사용자 추가 범위는 S12-01의 입력, S12-04의 전략·연구, S12-05의 검증에 포함한다.

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

8개 실험군의 합성 요청을 실행할 수 있다. 사건군을 분리한 실증 자료 비교와 전략별 실행 근거를 실제 Shadow와 단일 계좌 모의 승인·복구에 연결하는 작업이 남아 있다. S12-04와 S12-05는 진행률에 합산하지 않는다.

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

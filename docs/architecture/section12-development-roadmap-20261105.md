# 12절 개발과 라이브러리 개선 로드맵

**2026-11-05 · 사용자 승인 · DD와 MICRO 추가.** 기본 저장소 `main`, 착수 HEAD `c1e42a294c3294bb6ce648983d359ea3dbc5b614`를 기준으로 한다. 목표는 돌파·종가베팅·신규주·낙주와 추가한 일봉 과대낙폭·VI/호가 초단기의 개발 가능한 요구를 제품과 격리 검증까지 연결하고, 라이브러리와 구조 변경의 효과를 같은 입력으로 판정하는 것이다. 운영 전환은 별도 작업으로 남긴다.

전체 단계와 완료 기준은 이 문서 한 곳에서 관리한다. **S12-01 → S12-02 → S12-03 → S12-04 → S12-05** 순서로 통합한다. 각 단계는 실행할 코드, 직접 관련 검사, 검증 기록과 로컬 커밋을 남긴다. [정본 12절](app-target-20261023.md#12-연구노트-기반-네-운용군의-목표-설계) · [기능별 계약](r3-ownership.md) · [남은 작업](../OPEN_ITEMS.md) · [저장소 전체 로드맵](../../FUTURE_DEVELOPMENT_ROADMAP.md).

**현재 상태.** S12-01~04 수락 · 완료 4/5 · S12-05 진행 중. 12.12의 대표 연결 11개 중 항목 7·9를 개발 수락했다. 항목 3은 내부 계획 수명·첫 손절·부분 체결 보호와 명시한 v2 정책의 중지 후 관리를 연결했다. 기존 v1 중지 권한은 보존한다. 실제 동시호가 입력과 일반 READY/UI가 남아 있다. [S12-04 완료 근거](#s12-04-stage-acceptance) · [항목 7·9 수락](#s12-acceptance-seven-nine) · [종가베팅 중지 관리](#s12-close-stopped-management) · [12.12 수락·잔여](#s12-acceptance-remaining).

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
| 1 · 2 · 6 | 진행 | 돌파 최초 진입·만료·보호와 명시 재개를 격리 모의 실행에서 확인했다. 계좌 위험 연구는 동일 현금에서 계획별 예약·부분 체결·수량과 계좌 평가를 대조한다. 추가·부분 청산·재진입과 서로 다른 전략의 동시 승인은 전략별 행동 근거·수량·공유 계좌 배분 정책을 확정해 같은 계좌의 다른 계획 및 수동 수량과 대조해야 한다. 기존 미체결 정정의 `ADD`·`REDUCE`는 같은 주문의 잔량을 다시 요청하는 기능이다. [계좌 위험 연구 검증](../../tests/unit/test_account_risk_research.py). |
| 3 | 진행 | 종가베팅 내부 실행기에 새 후보 진입·잔량 취소·DB 재개방·익일 갭 청산·실제 잔량 0을 연결했다. 신규 v2 근거는 원래 후보를 보존하고 v1 계획·영수증은 유지한다. 첫 0B 손절·최초 Risk·체결 기준을 보존하며 부분 체결 잔량을 마감 전 취소한다. 가격 회복·늦은 체결·재시작 뒤에도 원래 요청으로 실제 잔량만 매도한다. 명시한 실행 정책 v2는 STOPPED를 유지하며 원래 계획의 취소·청산만 계속한다. 기존 정책 v1의 중지 권한과 신규 진입 차단은 보존한다. 실제 완전한 동시호가 공급 및 일반 READY 게시가 남아 있다. [중지 관리](#s12-close-stopped-management) · [첫 손절](#s12-close-sticky-stop) · [갭 입력](#s12-close-native-gap). |
| 4 · 5 | 진행 | 신규주 두 조합과 낙주의 진입·보호는 연구와 완료봉 Shadow에서 확인했다. 각 전략 명세에 결합된 모의 주문·재시작, 낙주 부분 체결과 잔량 취소 경합을 같은 계획 ID로 확인해야 한다. |
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

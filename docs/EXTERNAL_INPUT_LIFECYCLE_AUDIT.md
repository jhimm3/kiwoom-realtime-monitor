# 외부 입력 lifecycle·최신성 감사

작성: 2026-10-06 · [앱 전체 부하 감사](WHOLE_APP_LOAD_OPTIMIZATION_REVIEW.md)의 별도 원장

## 판단 기준과 확인 범위

수집량 최소화가 목표가 아니다. 화면·알람·자동매매·장후 분석·재시작 복구에 필요한 자료는 확보하고,
소비자가 없는 자동 실행과 필요한 갱신이 빠지는 경로를 함께 찾는다.
화면 닫힘, 기능 OFF, NAS 자율 수집 OFF, 프로세스 종료는 서로 다른 상태다.
선택 기능이 켜져 있으면 그 기능의 producer가 화면 없이 실행되는 것 자체는 불필요 수집이 아니다.

`src/`와 `scripts/`의 Python 468개를 구문 검색했다. parse error 0, lifecycle 호출 666개,
worker class 34개, subscription 전송 후보 7개, start/stop/close 등 제어 후보 1,091개를 찾았다.
합계 1,798개는 **수집기 개수도 실제 실행 수치도 아니다**. 로컬 timer, 테스트/replay helper,
CLI, connection close 등을 포함한다. 상위 실행 owner를 기준으로 아래 표에 묶었다.
동적 dispatch·외부 실행기·운영 설정·현재 창 상태까지 정적 검색으로 확정하지 않는다.

- 상세 검색 결과: `artifacts/external-lifecycle-static-20261006.csv` (파일·행·함수·표현식).
- 구문 검사 요약: `artifacts/external-lifecycle-static-summary-20261006.json`.
- 기존 API 상태: `artifacts/external-lifecycle-live-status-20261006.json`.
- 새 상시 계측, task, timer, subscription, DB write는 추가하지 않았다. 기능을 켜거나 끄지도 않았다.

### 실제 확인한 상태

18:57 KST NAS active build는 `2026.10.06-recorded-capture-api-v1`.
운영 설정 revision 7은 applied 7/ACTIVE였다.

| 기능 | 확인한 값 | 해석 한계 |
|---|---|---|
| Naver API/종목 site/시황/query-set | 모두 OFF | 관련 coroutine 존재만으로 외부 HTTP 발생이라고 볼 수 없음 |
| DART | ON, stock-refresh effective | 개별 HTTP 요청 횟수·대상은 이 상태 응답에 없음 |
| 외부시장 | ON, 300초, running | 187시도/187완료, 최근 저장 행 3,495, 마지막 오류 없음. 일별 모든 source의 최신성 증명은 아님 |
| Shadow | ON, 2초 | NAS 결정 생성과 PC 후보 창 polling은 별도 owner |
| 조건검색 | ON | 조건검색 OFF도 전체 market-event service OFF와 다름 |
| 모의계좌 | health에서 available=true | 계좌 수·자동매매 활성 여부나 모든 profile의 상태를 뜻하지 않음 |
| 진단 master/trace | OFF/OFF | 과거 incomplete capture만 존재. 이번 감사가 새 capture를 시작한 것은 아님 |

PC에서는 `run_test_app_with_data.py`와 그 자식 `news_process` 실행 경로를 확인했다.
각각 launcher/child PID가 있어 이를 독립 앱 두 개라고 세지 않았다. 해당 목록에서는 journal/research 실행 경로가 확인되지 않았다.
현재 창 가시성·알람 설정·사용자가 의도한 idle 모드까지는 확인하지 않았다.
따라서 **현재 실행을 '모든 선택 기능 OFF인 idle'이라고 부를 수 없다.**

## NAS 입력의 시작·유지·종료

표의 분류: **필수**=해당 모드의 활성 기능을 유지하는 데 필요, **조건부**=기능/설정/요청 시에만 필요,
**불필요 후보**=소비자 없는 분기가 확인되거나 의심됨, **보류**=소비자 또는 최신성 기준 확인 필요.
필수/조건부 분류는 현재 빈도가 최적이라는 뜻이 아니다.

| ID·자료 | 자동 시작·주기/유지 조건 | feature owner → 실제 consumer | UI/기능 OFF·stop/unsubscribe | 모드별 판단 |
|---|---|---|---|---|
| N1 TOP20 주순위 `ka00198` | NAS 자율 TOP20 start 즉시, 이후 :00/:30 회차; 현재 loop는 장외에도 동작 | TOP20 → 화면, membership 이력, 구독 대상, entry 준비, 지수/후보 universe | PC가 없어도 유지. 자율 TOP20 서비스 close/config OFF 때 종료 | 자율수집 모드 필수. 장외 지속 필요·최신성은 보류; UI 없음만으로 제거 불가 |
| N2 보조 순위 | 주순위 회차에서 30초/1분/10분/1시간 slot별; 수신과 저장 시간창은 다름 | TOP20 부가 수집 → 보조 순위/시점 이력 | TOP20 close에 함께 종료. 개별 slot 실패는 다음 slot까지 대기 | 조건부. 실제 사용하는 보조 이력·시간창을 확인한 뒤 과수집 판단 |
| N3 KRX 종목 카탈로그 | TOP20 첫 준비 및 날짜 변경 시 갱신 | catalog → 종목 시장 구분, 뉴스 종목 매칭 | PC와 독립. TOP20 close로 종료; 실패 fallback이 있으면 당일 완료 처리 가능 | 조건부 필요. 갱신 실패 후 stale 허용기간은 아래 U3 |
| N4 기본정보/NXT/일봉/분봉/수급/신고가 TR | 신규 entry·계좌 매수 추적·미완료 단계에서 시작; 단계별 완료/실패 관리 | TOP20 준비 → 실시간 대상, 화면 신고가, 후보, coverage | 이미 완료된 단계는 반복 생략. 장후 전체일 보완은 별도. TOP20 close로 owned task 취소 | 조건부 필요. 완료 후 원본 변경은 U1, 중복 준비는 별도 검증 |
| N5 장후/전일 복구 TR·지수 | 관측 거래일에 20:05 이후, 이른 아침 전일 미완료 복구; retry/완료 marker | TOP20 backfill → 장후 분석, 일지, 확정 coverage | PC 화면과 무관. 완료/설정/서비스 종료까지 소유 | 조건부 필요. entry 완료와 final 완료를 혼동하지 않음 |
| N6 중앙 market WebSocket | hub code 합집합 또는 시장 이벤트 전용 모드가 필요할 때; session·REG ACK 후 수신; 연결 실패 3초 재시도 | collector → RAM 화면/분·초봉, TOP20, 시장 상태·VI/급등 cohort, 계좌 | PC disconnect는 PC owner만 제거. TOP20/event owner가 남으면 유지. 코드·event-only 모두 없으면 연결 대기 | NAS 자율 또는 PC 실시간 모드 필수. 한 upstream 구독으로 여러 consumer 공유 |
| N7 시장 이벤트·조건검색·VI `ka10054` | market-event 서비스 start, cohort 복원; WS 재연결 때 VI REST 보완 최대 10페이지 | market events → 조건/VI/상한가·마감 사실, cohort 추적 | 조건검색 OFF는 CNSRCLR만 실행; VI/시장 상태 owner는 남음. 전체 service OFF/close가 별개 | 조건부 필요. 재연결마다 보완량은 과수집 여부 보류 |
| N8 계좌 `00/04` 및 REST 복구 | profile monitor ON; 시장 profile은 중앙 WS 공유, 다른 실계좌는 account-only WS. 실계좌 event wake+장중 safety 300초+장전/장후 :15 확인·실패 retry | 계좌 owner → 주문/체결/잔고·위험·복구·일지 | PC와 무관. monitor OFF 때 해당 monitor/전용 WS drain. 명시적 계좌 조회 API는 별도 요청 가능 | 설정 ON이면 필수. 보호 주기 임의 제거 금지 |
| N9 모의계좌·자동운용 | 활성 profile monitor/운용 설정에 따라 독립 mock broker·WS; 초기/연결/event 복구 | mock runtime → 모의 상태/주문/운용 안전 | OFF 때 bundle close. 성공 후 무조건 반복하는 REST safety loop는 실계좌와 같지 않음 | 조건부 필수. 체결 이벤트 누락 시 freshness는 별도 검증 |
| N10 0w 프로그램수급 | TOP20/PC 요청 program code REG; 실제 tick 수신 시 RAM pending | TOP20 program owner → PC 프로그램수급·보완자료 | hot-cohort는 program 구독 제외. owner code 제거/서비스 close로 해제 | 조건부. 같은 초 값 변경을 버리면 안 됨. 과수집 비율 미확정 |
| N11 지수·시장상태·계좌 공통 group | 중앙 WS의 REG group에 `00/04,0J/0U,0s`, market-event가 있으면 `1h` 포함 | 계좌·시장 상태·지수·거래일 증거/마감 | 계좌 monitor OFF만으로 이 공통 group 전체가 사라지지는 않음 | **보류:** 계좌 consumer가 없을 때 `00/04`가 불필요 후보. 나머지 owner와 분리 검증 |
| N12 Naver/DART 종목 뉴스 | news service start→기본 300초 TOP20 대상 refresh; source ON·client 조건 확인 | news → 기사/공시, 화면, 본문/규칙·연구 | PC가 없어도 설정 ON이면 유지. source 모두 OFF면 loop가 있어도 외부 수집은 return | 조건부. 현재 Naver OFF/DART ON. 단순히 '뉴스 전체 OFF'로 부를 수 없음 |
| N13 query-set·FLASH/WORLD | news service가 loop 생성, 기본 300초 due를 최대 60초마다 확인 | 뉴스 원천별 cursor → 기사/시황/연구 | source OFF·client 없음이면 외부 요청 생략; service close 때 cancel | 조건부. 서로 다른 scope이고 URL dedup 전 network가 겹칠 수 있으나 목적 확인 전 제거 금지 |
| N14 BODY/AI 후속 외부 요청 | job queue claim 뒤 HTML fetch/AI provider. idle은 최대 5초 backoff+wake | news job → 본문·분석 결과 | source 수집 OFF라도 기존 pending job이 남으면 실행 가능. historical PC 전용 scope는 NAS claim 제외 | 조건부 필요. BODY/BODY/RULE worker 이름은 우선순위이지 AI 실행 금지 의미가 아님 |
| N15 Yahoo 외부시장 | 운영 ON에서 즉시+300초, 5일 5분봉; UTC 하루 한 번 2년 일봉 | external market → 월물/roll·종가·차트 컨텍스트 | 운영 OFF/close 때 poll 종료. UI 닫힘과 무관. 동시 collect는 합침 | 조건부. 범위 과수집 여부 보류, 일봉 실패 재시도는 U2 |

공통 REST broker는 별도 자료 소유자가 아니라 요청 전송·중복 합침 경계다.
동일 fingerprint의 동시 요청과 유효 cache는 합치지만 다른 목적·시장·body·만료 시점은 별도다.
TOP20와 market-event가 기본정보/NXT를 함께 필요로 하는 것과 upstream HTTP 중복은 구분한다.

소스: [app](../src/kiwoom_monitor/central_server/app.py) lifespan,
[autonomous_top20](../src/kiwoom_monitor/central_server/autonomous_top20.py),
[realtime_collector](../src/kiwoom_monitor/central_server/realtime_collector.py),
[realtime_hub](../src/kiwoom_monitor/central_server/realtime_hub.py),
[market_events](../src/kiwoom_monitor/central_server/market_events.py),
[real_runtime](../src/kiwoom_monitor/central_server/real_runtime.py),
[mock_runtime](../src/kiwoom_monitor/central_server/mock_runtime.py),
[mock_account_monitor](../src/kiwoom_monitor/central_server/mock_account_monitor.py),
[news_service](../src/kiwoom_monitor/central_server/news_service.py),
[news_jobs](../src/kiwoom_monitor/central_server/news_jobs.py),
[external_market_collector](../src/kiwoom_monitor/central_server/external_market_collector.py).

## PC 입력의 lifecycle

| ID·자료 | 자동 시작·유지 조건 | feature owner → consumer | UI/OFF·종료 조건 | 판정 |
|---|---|---|---|---|
| P1 순위 REST 또는 NAS snapshot | main startup 첫 요청+선택한 조회유형 schedule | 메인 모니터 → 순위/구독/화면/후속 준비 | hide/minimize는 정지 아님. main shutdown에서 worker/timer 종료 | 모니터 모드 필수. NAS mode에서 직접 Kiwoom과 중복 수신 여부는 failover 상태와 구분 |
| P2 Kiwoom 직접 WS | 직접 모드 또는 NAS 장애 fallback, 요청 종목·시장 session에 따라 REG | realtime worker → 메인 실시간 화면·로컬 집계 | 장외 대기 15초, disconnect retry 3초, 설정/종목 변화 재구독. main stop | 조건부 필수. NAS 복구는 요청 종목 전체 REG ACK 뒤 로컬 경로 정지; 짧은 겹침은 장애전환 절차 |
| P3 NAS WS | 중앙 모드 realtime worker start, 연결 유지·3초 retry | NAS client → 메인 실시간/현재·직전 분 | hide와 독립. stop/close 때 연결 해제. 연속 실패 시 fallback | 조건부 필수. PC가 닫혀도 NAS의 다른 owner 수집까지 정지시키지 않음 |
| P4 기본정보/NXT/분·일봉/신고가 | 수신 순위의 준비 단계 또는 명시 조회, cache/검증 조건 | 자료 worker → 표시/차트·분석 | NAS 모드는 저장 자료 우선, 직접/장애전환은 TR 가능. owned worker 완료/취소·main shutdown | 조건부. 완료 marker·원본 변경 invalidation을 함께 판정 |
| P5 후보 API | 후보 창이 보이거나 후보 알람 ON일 때만 2초 polling | 후보 창 → 표·알람 | 숨김 AND 알람 OFF에서는 미시작 또는 worker 대기. show/알람 ON에서 재개하고 누락 구간은 조용히 catch-up. main shutdown은 worker를 깨워 종료 대기 | **PC 소스에서 수정·단위검증 완료.** NAS shadow generation/자동운용은 계속 독립 실행. 실제 poll 감소량은 운영 계측 전 미확정 |
| P6 중앙 설정/콘텐츠 | 중앙 모드 startup pull, 설정 60초 sync; 동시에 하나만 실행 | 앱 설정·테마·일지 뉴스 연결 등 | main process 생존 동안. UI hide는 정지 아님 | 조건부. 변경 없는 write skip/cursor가 있는 경로와 전체 sync를 구분 |
| P7 뉴스 child·선택 종목 | 뉴스/시황 열기 또는 일지→뉴스 요청에 child start; 선택 종목 준비/명시 refresh | 뉴스 화면 → 기사·본문·AI | child는 마지막 창 닫힘에도 생존. parent stop/종료 명령/parent 소멸 때 shutdown | 조건부. 아래 P8/P9의 hidden 상태 문제 분리 |
| P8 종목 뉴스 60초 timer | stock window show 때 start; 재열기 시 저장 자료를 즉시 재준비 | 뉴스 준비 → 표시, 모드/설정에 따라 원격·직접 조회/자동 AI 연쇄 | 창 close 시 parent 유무와 관계없이 timer stop; 재열기 시 timer 재개. 진행 중인 저장 작업과 P9 중앙 sync는 별도 유지 | **PC 소스에서 lifecycle 수정·회귀 검증 완료.** 숨김 중 timer-driven prepare는 멈추지만 운영 외부 호출·DB 절감량은 미측정 |
| P9 뉴스 child 중앙 sync | central mode에서 child 시작 1초 후, 이후 60초. local DB signature가 바뀌면 push, 각 회차에서 delta pull | 로컬 기사·AI·매매일지 뉴스 연결을 중앙으로 올리고 중앙 기사·AI·뉴스 연결·테마를 PC cache로 반영. `EntrySnapshotWriter`는 체결 시각에 PC 뉴스 DB를 읽어 매매 진입 snapshot에 넣고, 테마 화면은 로컬 DB의 AI 제안을 사용 | 뉴스 창이 닫혀도 child process가 살아 있으면 유지. 성공한 push만 manifest에 반영하고 실패/미전송 delta를 다음 회차에 재시도. local/server mode에 따라 article catalog 포함 여부가 다름 | **조건부 필수로 유지.** 닫힌 동안에도 미전송 변경 보존과 진입 snapshot용 cache freshness 소비자가 있다. 매분 delta 조회량은 성능 후보지만, pull 정지·주기 변경은 cache 최신성과 재시도 의미를 바꿀 수 있어 동일 조건 측정과 on-open catch-up 계약 전까지 보류 |
| P10 시황 뉴스 UI | show/tab/manual refresh; source별 inflight 합침 | 시황 화면 | 자체 주기 timer 없음; 실행 중 요청 lifecycle은 child 종료 소유 | 조건부·현재 구조상 수요 기반. 과수집 확인 없음 |
| P11 매매일지 | 일지 child 시작: 중앙 sync 매분; 선택 차트/계좌·보완 요청 | 일지/복기·장후 분석 | 별도 child/window lifecycle. 표시용 local 1초 timer와 NAS/키움 네트워크 요청은 별개 | 조건부. 중앙 전체 sync 비용·숨김 시 사용은 별도 확인; 현재 프로세스 목록에서 미확인 |
| P12 연구 campaign | main startup에 만든 dialog가 저장된 RUNNING campaign 복원 가능; 수동 실행도 있음 | 연구 기능 → 결과·검증. NAS data source 선택 시 paginated export | close 시 hide+취소/재시도 중지; main shutdown drain | 조건부. worker 1초 loop와 실제 source due 기본 60초를 구분. 현재 child 실행 미확인 |
| P13 역사 자료 수집 scripts | monitor 버튼/CLI/PowerShell로 명시 시작; 범위·stop file·lock | 과거 봉/뉴스/DART/CREON 확보 → 연구/이관 | main UI와 별개 프로세스, 지정 범위 완료·stop으로 종료 | 조건부. PC가 한가할 때만 돈다는 규칙 없음. 완료일 재검증은 U6 |
| P14 KRX/KIND 종목 catalog | 보조 API 작업 뒤 KST 일별 준비 또는 수동 동기화; single worker | 종목 cache → 시장/이름/검색 | main worker controller shutdown; 완료일 gate | 조건부. NAS catalog와 별도 consumer/cache여서 동일 source 수신만으로 삭제 불가 |
| P15 Google Drive | 연결+자동동기화 설정 시 startup metadata; 변경 후 1.5초 debounce upload, 종료 dirty upload | 설정/테마 백업·복원 | 단발 owned worker. 상시 pull timer 아님 | 조건부. 사용 설정과 미전송 변경 보존 |
| P16 GitHub 업데이트 | 패키지 실행(`sys.frozen`) + 자동확인 ON에서 startup 1.2초 뒤 한 번, 또는 수동 | 업데이트 UI | single worker/timeout, 완료 뒤 종료 | 조건부. 현재 테스트 소스 실행을 자동 업데이트 polling으로 세지 않음 |
| P17 체결 진입 snapshot·수급 보완 | main constructor가 writer 시작; 체결 시 snapshot/수급, 시계 due 검사로 장후 7일·장전/주말 4일 보완 후보 enqueue | 계좌 증거 → 일지/진입 수급·프로그램수급 | main shutdown drain; 종목별 60초 RAM cache·일별 in-process 중복 방지 | 조건부 필요. 자동매매 OFF라도 수동 체결 증거 consumer 있음. journal child와 같은 자료 요청은 교차 확인 |

소스: [bootstrap](../src/kiwoom_monitor/bootstrap.py),
[app_controller](../src/kiwoom_monitor/presentation/app_controller.py),
[main_window](../src/kiwoom_monitor/presentation/main_window.py),
[candidate_monitor_dialog](../src/kiwoom_monitor/presentation/candidate_monitor_dialog.py),
[central_realtime_worker](../src/kiwoom_monitor/infrastructure/kiwoom_rest/central_realtime_worker.py),
[realtime_worker](../src/kiwoom_monitor/infrastructure/kiwoom_rest/realtime_worker.py),
[news_process](../src/kiwoom_monitor/news_process.py),
[stock_news_window](../src/kiwoom_monitor/presentation/stock_news_window.py),
[journal_process](../src/kiwoom_monitor/journal_process.py),
[research_dialog](../src/kiwoom_monitor/presentation/research_dialog.py).

RULE 로컬 계산, credential RAM 정리, 단순 queue flush, UI repaint, 파일 command polling, replay helper는
생명주기 목록에는 포함하지만 외부 입력 횟수에는 포함하지 않는다. AI는 요청/queue job 때만 provider에 접속한다.

## 최신성: 과수집과 과소·갱신 부족을 함께 판정

| ID | 자료·소비자 요구 | 현재 코드/증거 | 분류·다음 확인 |
|---|---|---|---|
| U1 일봉 coverage·5/20/250일 신고가 | 같은 종목/시장/범위의 현재 봉과 증거가 일치해야 함. 신규주 source 종료 증거도 보존 | 재현 확인: canonical 일봉의 값 변경 뒤 coverage fingerprint는 불일치로 판정되지만, 같은 날짜의 entry stage RAM 완료 표식은 계속 유효하고 신고가 문서는 `checked_on >= day`만으로 재계산을 건너뜀. 서비스 재시작 후에도 같은 날짜 historical-high guard가 남음. LG이노텍 NXT의 실제 mismatch도 확인 | **정합성 결함 확정:** 실제 값 변경 때 code/market 입력 freshness를 준비 단계와 historical high에 전달해 해당 stage만 다시 검증·계산해야 함. 매 순위 주기 DB 재조회로 대체하지 말 것. `-` 검증을 없애는 방식 금지 |
| U2 외부시장 일봉 | 성공한 최신 일봉/종가가 roll·분석에 필요 | daily 실패를 내부 catch한 정상 반환 뒤 `_last_daily_date` 완료 처리 가능 | **갱신 부족 경로 확인:** 실패 주입으로 다음 poll 재시도 검증 후 별도 정합성 수정 |
| U3 종목 catalog·시장 이벤트 reference | 순위 시장 구분과 활성 cohort의 NXT 구독은 현재 거래일 기준 자료가 필요 | `_ensure_market_catalog(day)`는 날짜가 다른 저장 catalog를 fallback으로 계속 쓰며 갱신 실패 시 기존 market map이 하나라도 있으면 `_market_catalog_day=day`로 표시해 그날 재시도를 막는다. map은 TOP20 ranking의 `market_provider`로 소비된다. 별도로 `MarketEventService._load_metadata()`는 활성 cohort의 `stock_nxt_eligibility` 문서가 있으면 `observed_at` 날짜를 검사하지 않고 `nxt_eligible`로 구독을 갱신한다. 같은 문서의 날짜를 검사하는 `AutonomousTop20Service._nxt_enabled()`와 동작이 다르다. NAS read-only API 확인(2026-10-06, build `2026.10.06-top20-daily-freshness-v2`): cohort 218종목 중 활성 18종목은 오늘 관측 문서 18, stale 0, missing 0. 비활성 200종목 중 오래된 문서는 137개였고, 전체 cohort 문서는 모두 존재했다. | **현재 stale 활성 NXT 구독은 관측되지 않음. 재활성화 시 freshness 결함 가능성은 코드상 남음.** `record_condition_signal()`은 비활성 종목을 다시 활성화할 때 이전 `nxt_eligible` 값을 즉시 구독에 적용한 뒤 metadata queue에 넣는다. `_load_metadata()`는 오래된 문서를 발견해도 날짜 검사 없이 재사용하므로 재조회 없이 stale 값이 유지될 수 있다. 현재 이 137개가 재진입했는지/잘못 구독됐는지는 미확인이다. freshness 검사를 바로 추가하면 기동 시 활성 cohort의 `ka10100`가 몰릴 수 있어 수정은 보류하고 TR 분산·재시도 정책을 검증한다. 종목 catalog의 관측일은 읽기 API가 노출하지 않아 현재 freshness를 확인하지 못했다. |
| U4 순위 slot | 화면은 최신 회차, 이력은 수집된 관측시각/공백이 명확해야 함 | NAS 주 순위 `qry_tp=5`는 30초 경계마다 요청되고 TOP20 membership은 매 정상 회차 기록된다. `ranking` 보관은 07:55~08:05:59 KST에 한정된다. 보조 `qry_tp=4/1/2/3`은 같은 시간창 안에서 각각 30초/1분/10분/1시간 slot으로 요청되며, `MarketDataIngestor._ingest_ranking()`이 응답을 query type별 `ranking` snapshot으로 보존한다. PC `RankingService`는 사용자가 선택한 query type의 저장 순위를 먼저 읽고 최신·완전성 검사를 한 뒤 직접 조회로 fallback할 수 있다. slot marker는 요청 전에 메모리에 기록된다. 주 순위는 오래되거나 부분인 응답에 제한 재시도하고 끝내면 해당 회차 저장을 건너뛰며, 보조 요청 실패는 다음 예정 slot까지 기다린다. 10분/1시간 순위는 보관 시간창 안에 재시도 slot이 없을 수 있다. 누락된 slot을 나중에 재생성하지 않는다. 관련 schedule·consumer 동작은 코드와 단위 테스트에서 확인했으며 실 NAS 호출별 성공/실패 수는 이 API 표본에서 노출되지 않았다. | **기능상 필요한 조건부 수집, 실패 때 보관 공백 가능.** 보조 순위는 화면에서 선택 가능한 저장자료 소비자가 확인되므로 ownerless 요청으로 제거하지 않는다. 요청 전 slot 예약은 한 프로세스 내 중복 방지지만 실패 복구를 보장하지 않는다. 현재 자료만으로 주기·동시성을 바꾸지 않고, 다음 정상 capture에서 query type별 요청·응답·보관 결과를 확인한다. 실패한 `qry_tp=2/3`을 같은 시간창에서 재시도할 필요와 과거 slot 복구 가능성은 판단 보류한다. |
| U5 WS/event 큐·계좌 | 승인된 코드 구독, cohort 조건·가격제한가 사실, 계좌 주문/체결/잔고의 실제 메시지 freshness와 복구 | `RealtimeHub`는 subscriber마다 1,000개 queue를 두고 포화되면 가장 오래된 이벤트를 제거한다. 외부 PC WebSocket은 `realtime_gap`으로 drop 수를 받으며 TOP20은 자체 subscriber drop 증가를 로그에 반영하지만 `MarketEventService._event_loop()`는 자신의 `dropped_events`를 확인하지 않는다. 그 서비스는 0B를 cohort 밖이면 건너뛰므로 `RealtimeHub.publish()`가 `subscriber.codes`가 빈 경우 coded event도 필터하지 않는 동작은 cohort가 비었을 때 불필요 fanout 후보가 된다. 시장-event 조건 신호 queue 상한은 5,000이며 정상 운영 중 포화되면 해당 I/D 신호를 버리고 오류 로그만 남긴다. real-account event queue도 1,000 상한이며 포화 시 새 이벤트를 버리고 `REAL_ACCOUNT_EVENT_QUEUE_FULL` 상태와 drop 수를 올린다. 이후 monitor wake/REST safety reconciliation은 계좌 현재 상태를 맞출 수 있지만 빠진 각 이벤트 이력을 복원한다고 보장하지 않는다. 조건 runtime endpoint는 상태만 내고 queue depth/drop 수를 제공하지 않는다. | **정합성·관측 부족 후보; 장중 발생률은 미확인.** 정적 경로로는 포화 시 drop 정책과 market-event drop 감시 공백을 확인했다. 다음 정상 장중 기존 health/operational 상태·로그에서 drop/recovery를 먼저 확인하고, 부족할 때만 queue depth/high-water/drop/last-event를 bounded counter로 임시 관측한다. 새 상시 계측, queue 상한 변경, 재시도 또는 coalescing은 기능·순서·복구 의미를 검증하기 전 적용하지 않는다. |
| U6 역사 뉴스 완료일 | 완료한 과거일은 재실행하지 않는 고정 archive 목적과, 원천의 사후 정정·누락을 다시 확인하는 최신성 목적을 구분 | `scripts/run_naver_stock_market_news.py`를 monitor/CLI에서 명시 실행할 때만 날짜별 FLASH/WORLD 페이지 수집이 시작된다. 앱 기동·뉴스창 표시와 연결된 자동 timer는 확인되지 않았다. 기본 worker 8개가 각 저장 페이지 기사를 `ConcurrentArticlePreparation`에 넘겨 BODY/RULE 준비도 수행한다. 날짜 원장은 SQLite `market_news_days/pages/articles`이고, 원문 페이지 hash와 row, source/date별 완료 표식을 둔다. page size 미만 또는 더 오래된 날짜 행이 확인되면 완료, 정상 빈 응답은 `empty` 완료로 표시한다. 완료 표식(`complete`, `complete_boundary`, `empty`)이 있으면 재호출은 즉시 반환해 원천을 재검증하지 않는다. 페이지 저장은 callback 전에 commit하며, 다음 페이지를 재개할 때 직전 raw page를 callback에 다시 전달해 처리 중단을 보완한다. 재개 중 같은 페이지의 응답 hash가 바뀌면 원본을 덮어쓰지 않고 오류 처리한다. 완료 데이터는 별도 명시 실행인 `import_historical_market_news_to_nas.py`가 100건 이하 배치로 중앙 뉴스 API에 올리고 import ledger에 기록한다. 로컬 preprocessing은 URL로 raw article을 찾아 BODY/RULE을 보완한다. immutable NAS SQLite snapshot은 날짜별 archive 검색/상세 API의 기반이며 운영 뉴스 DB와 별개다. | **수집은 조건부 단발 작업, 고정 archive 목적에는 적정. 최신 원천 정정·사후 누락 확인에는 재검증이 없어 갱신 부족이지만, 현재 요구가 archive 재현성인지 최신화인지로 나뉘므로 수정 판단은 보류한다.** 완료 상태를 지우거나 같은 source/date page를 덮어쓰지 않는다. 최신 정정이 필요하다고 결정되면 새 수집 version/snapshot에 관측하고 기존 archive와 비교해야 한다. `empty`가 원천의 정상 응답인지 일시적인 빈 응답인지 현재 row에는 별도 증거가 없어, 해당 케이스의 실운영 빈 응답 빈도는 미확인이다. 관련 기존 단위검사는 페이지 경계, resumable batch, callback 전 raw commit/재생, 날짜 불일치 오류를 다룬다. 이 결과는 정적 경로와 fixture 검사이며 현재 수집 프로세스 실행 여부나 NAS/원천 최신 상태 확인은 아니다. |
| U7 TOP20 분봉 entry/final | TOP20·실제 계좌 매수 편입 종목의 진입 전 자료와 앱 분봉 reader가 보는 장중/장후 자료를 각각 충족 | 정상 TOP20 회차마다 신규/미완료 종목을 `nas-top20-entry-data` task에 넣는다. 당일 entry backfill은 08:00부터 허용되고 KRX, 당일 NXT eligible이면 NXT에 `ka10080` 연속조회한다. 응답에 나타난 대상일 분봉 key를 모아 저장본과 대조한 뒤에만 날짜·종목·시장별 `market_data_coverage_intraday/entry_backfill`을 기록한다. 해당 owner에 coverage 문서가 있으면 다음 TOP20 회차는 다시 검사하지 않고 해당 시장 fetch를 건너뛴다. 20:05 이후는 별도 owned after-close task가 거래일 `0s` 증거를 확인하고 KRX, eligible NXT 각각 full-day 조회를 한다. `market_data_coverage`에 `window_closed`와 `session_finalized`가 기록되고 저장 분봉도 있을 때만 재실행을 건너뛴다. 실패는 retry schedule로 다시 시도한다. PC의 중앙 분봉 API reader는 DB 저장 분봉을 읽고, 장중 표시 경로는 collector의 live minute projection을 우선 제공할 수 있다. | **범위 분리는 목적에 맞고 확인된 빈 응답/미저장 행은 완료로 승격하지 않는다.** Entry는 완료 뒤 원본 정정이나 새 누락을 다시 검증하지 않고, full-day marker도 동일 날짜 coverage+봉 존재를 기준으로 생략한다. 따라서 source correction이나 이미 기록된 범위의 재검증 필요성은 판단 보류이며, entry 자료를 full-day 자료로 오인하면 안 된다. stored coverage가 있으면 코드상 해당 scope 재요청을 생략하는 동작은 확정이나 실제 stale correction 영향은 미확인이다. 신규 편입/실패 재시도와 full-day 완료 의미는 코드에서 확인했으며 신규 편입 시 실제 응답 수, 실시간 DB row와 화면 live projection의 시간차는 이번 정적 감사에서 측정하지 않았다. |
| U8 기본정보·source 전환 | `ka10001`의 시가총액·유통비율·유통주식수·상한가 기준값은 장중 소비자가 쓰는 시점에 유효해야 함. 원본 250일 고가는 수정주가 일봉 계산과 구분 | NAS `AutonomousTop20Service`는 성공한 TOP20 회차(30초)마다 현재 순위 코드, 구독 준비에서는 실제 계좌 매수 편입 코드를 basic stage 대상으로 예약한다. 같은 KST 날짜 관측은 07:00 전까지 재사용하지만, 07:00 이후에는 `observed_at >= 07:00`이어야 current로 인정한다. stage token과 `CentralRestBroker` RAM/영속 cache key도 `00:00`/`07:00`/날짜 경계로 바뀌므로 07시 전 응답을 새 구간 응답처럼 다시 쓰지 않는다. basic 성공은 서비스 RAM에서 해당 구간 동안 완료로 두고 실패는 다음 순위 회차에서 재시도한다. ingest는 latest document와 날짜별 dataset snapshot을 저장한다. 반면 PC `StockRepository.fundamentals_to_refresh()`는 KST 날짜 및 시가총액·유통비율·250일 고가·상한가 필드만 검사하고 07:00 구간을 구분하지 않으며 유통주식수 누락도 재조회 조건으로 보지 않는다. `MainWindow._start_fundamentals_loading()`이 그 판정을 받아 worker를 시작하며, NAS remote client는 최신 저장 payload를 freshness 확인 없이 사용하고 PC direct client는 직접 `ka10001`을 요청한다. 화면/거래강도는 기본정보의 유통비율·유통주식수 등을 사용하고 시가총액은 실시간 0B 값을 우선할 수 있다. `ka10001`의 원본 250일 고가는 수정주가 기준 일봉 고가와 병합·대체한다. | **NAS 수집은 의도한 갱신 구간과 실패 재시도를 갖춤. PC의 같은 날짜 07:00 경계는 갱신 부족 위험이 코드상 확인됐다.** PC가 07시 전에 기본정보를 성공 저장하면, 같은 날 `fundamentals_updated_at`이 오늘이고 위 네 필드가 채워진 경우 07시 이후에도 PC gate는 재요청 후보로 내지 않는다. 유통주식수가 비어 있어도 네 필드와 날짜가 충족되면 같은 날 보완 요청이 발생하지 않을 수 있다. NAS 최신 저장 payload도 요청 시 관측 시각을 검사하지 않는다. NAS 기본정보 준비가 PC 표시에 우연히 선행해도 PC가 그 값을 다시 읽는 시점은 이 gate에 좌우된다. 따라서 실제 화면/거래강도에서 stale 값이 유지된 사례는 아직 미확인이나, 07시 이후 신규·재등장 시 갱신 규칙을 NAS와 일치시킬 필요가 있는 정합성 후보로 기록한다. 즉시 변경하지 않는다. NAS 07:00·자정 cache 분리와 PC 당일 freshness 검사는 기존 테스트가 각각 확인하지만, PC 06:xx 성공→07:xx 같은 날 재등장/재시작 시 stale skip 하는 조합 회귀는 확인되지 않았다. |
| U9 후보/뉴스 hidden UI | UI/알람/자동분석 등 실제 수요 있을 때 새 자료 필요 | P5 hidden+알람 OFF polling과 P8 news close timer는 PC 소스에서 대기 처리. P9는 진입 시각 뉴스 snapshot 및 미전송 delta 보호 때문에 유지 | **P5/P8 lifecycle gate는 기능 검증 완료.** P9 empty-delta pull의 호출량만 성능 측정 후보로 남음 |
| U10 뉴스 다중 source·외부시장 최근 창 | 기사 coverage/정정/roll 분석에 충분한 범위 필요 | NAS/PC·여러 source가 같은 URL/봉을 받을 수 있음; source 목적·권한·scope가 다름 | **판단 보류:** 저장 dedup과 network 중복 분리. 마지막 성공/원본 정정/실제 consumer 요구 없이 범위를 축소하지 않음 |
| U11 DART 종목 공시 | consumer가 요구하는 최근 범위의 공시와 신규 종목 매핑 | 중앙 `CentralNewsService.start()`가 소유한 refresh loop는 TOP20 membership이 10분 이내일 때 대상별 수집을 수행한다. 설정에서 DART가 켜지고 API key가 구성된 경우 `_collect()`가 `DartDisclosureClient.search()`를 호출한다. `search()`는 최근 30일, `page_count=30`으로 기본 1페이지만 읽고 응답 `total_page`를 검사하지 않는다. 별도 `list_disclosures()`는 `page_count=100`으로 전체 페이지를 순회하지만 종목뉴스 자동 refresh에서는 호출하지 않는다. corp-code cache는 30일이다. | **과소수집 가능성은 코드상 확인, 실제 누락 규모는 미확인.** DART 공식 개발가이드는 `page_count` 최대 100과 `total_page`를 제공한다. 자동 refresh에서는 응답별 페이지/전체 건수를 기록하지 않아 30건 초과 회사를 현재 소급 판별할 수 없다. 운영 응답의 페이지 수와 실제 소비자 범위를 확인하기 전에는 pagination 활성화로 요청량을 늘리지 않는다. |
| U12 NAS 뉴스 실패·page limit | 실패/상한 도달한 source 구간을 cursor로 재개해야 함 | FLASH/WORLD는 어제분을 02시까지만 방문. Naver stock site는 `max_pages=3`마다 next page를 이어가지만 100페이지 한도에 도달하면 `next_page=1`, `page_limit_reached=true`를 반환한다. 서비스가 그 경우에도 `site_pending_latest_published_at`을 비우고 최신 게시시각을 cursor로 승격해 미조회 과거 페이지를 건너뛸 수 있다. 당시 live snapshot(18:57 KST)은 Naver 종목 site·시황 source 모두 OFF였다. | **조건부 갱신/coverage 결함 후보:** page 100에 닿을 만큼 backlog가 크고 종목 site source가 켜진 경우만 영향 가능성이 있다. 현재 실행 상태·누락 규모는 확인되지 않았다. source가 켜질 때 `site_last_page_limit_at`, `site_next_page`, cursor와 실제 응답을 확인하고, 미완료 경계에서는 cursor를 확정하지 않는 재개 규칙을 별도로 검증한다. 현재 OFF 표본을 실제 요청 오류·누락 건수로 세지 않는다. |

## idle 모드 판정표

| 실행 모드 | 남아야 하는 입력 | 제거 후보/보류 |
|---|---|---|
| NAS 자율수집 ON, PC 없음 | TOP20·시장 event·설정 ON 계좌·뉴스·외부시장 등 해당 NAS feature 입력 | PC가 없다는 이유로 ownerless 취급 금지. 장외 TOP20 지속과 공통 group의 소비자는 별도 확인 |
| PC 메인 숨김/최소화, 실시간 모니터 사용 중 | 순위·중앙/직접 WS·선택된 알람/계좌 소비 | 후보 창 자체가 숨겨지고 후보 알람도 OFF면 PC 후보 polling은 대기. main hide가 전체 기능 OFF라는 규칙은 없음 |
| 뉴스 창 닫힘, 자동 AI/알람 OFF | 진행 중 기사 저장과 P9의 미전송 변경·중앙 delta 동기화는 보존. 매매 체결 시각 뉴스 snapshot의 로컬 cache consumer가 있음 | P8 표시용 준비 timer는 정지. P9는 조건부 유지; 빈 delta pull 비용만 별도 측정 후보 |
| TOP20+market-event OFF, PC WS 없음 | 별도 ON 계좌/news/external owner만 해당 | 중앙 market collector는 연결 대기. 공통 group이 다른 owner 때문에 켜질 경우 종류별 소비자 확인 |
| 모든 선택 기능 OFF를 의도한 idle | 명시 사용자 요청, 필요한 제어/복구·접속 상태 확인 | 현재 이 상태를 실측하지 않음. 운영 설정을 임의 OFF로 바꿔 시험하지 않음. owner별 수요가 0인 producer 목록을 테스트 fixture로 먼저 검증 |

## 필요한 경우에만 하는 최소 확인

1. 기존 로그의 `api_id/continuation/queue_wait/duration`, HTTP path, WS 연결·REG ACK·재연결, 뉴스 job stage,
   외부시장 diagnostic_status, PC 수집기 timing/state 파일을 먼저 사용한다.
2. 빈 구간만 짧은 테스트 fixture 또는 임시 계측으로 확인한다. 상시 공통 수집 프레임워크·새 background loop를 만들지 않는다.
3. 부족한 앞단 필드는 `timestamp, source, kind(TR/event), trigger, owner, consumer, code_count, row/message_count`다.
   요청 시도/완료/실패와 WS raw 수신/parse 결과를 구분해 DB writer에 도달하지 않은 입력도 센다.
   값이 없으면 `unknown`으로 남긴다. async task/to_thread 문맥과 QThread/별도 process 전달은 동일하다고 가정하지 않는다.
4. 요청별 payload·token·계좌·원문·full URL은 로그에 넣지 않는다. 집계는 bounded RAM/기존 진단 경계로 제한하며 event별 동기 fsync는 추가하지 않는다.
5. 임시 확인은 종료·제거 조건과 overhead를 같이 둔다. 관측을 위해 새 subscription/TR를 자동으로 만들지 않는다.

### 다음 작은 변경의 순서

- 숨김+알람 OFF 후보 polling(P5)과 뉴스 child close timer(P8)는 PC 소스에서 구현·기능 검증했다. 뉴스창 재열기 시 즉시 저장 자료를 다시 준비한다. P9는 진입 시점 뉴스 snapshot과 실패한 push retry 소비자가 있어 유지하며, delta pull의 호출량/주기만 성능 후보로 보류한다. 다음 단계는 B0 capture 품질/overhead와 정상 장초 기준선이며, 이번 lifecycle 변경만으로 절감량을 주장하지 않는다.
- 외부시장 일봉 실패(U2), 0w 종료 drain(C1), 일봉 coverage(U1)는 **정합성 작업**으로 분리한다.
- 그 외 과수집/과소 후보의 interval·batch·동시성은 정상 baseline과 소비자 최신성 조건이 마련될 때까지 유지한다.
- 현재 incomplete capture를 lifecycle 충족·전체 성능 기준선으로 승격하지 않는다. 외부 입력 owner를 바꾸는 비교는 그 owner의 원인 입력/제어 상태까지 재현해야 한다.

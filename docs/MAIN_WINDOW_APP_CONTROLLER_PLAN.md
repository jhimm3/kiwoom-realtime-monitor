# MainWindow → AppController 책임 이전과 연결 보존 계약

2026-10-04 · 단계 A-D 로컬 구현·offscreen 회귀 완료 / 설치 앱 수동 확인 남음

## 범위와 기준

사용자는 AppController 도입을 확정했고, 첨부 로드맵 중 MainWindow 관련 부분만 적용하도록 했다. 최우선 완료 조건은 기존 기능과 연결의 보존이다. NAS DB/replay 최적화 및 다른 제품 기능은 이번 범위에 넣지 않는다.

- 작업 기준: `e83f` 워크트리, `e465371c74613928533806c39f5c1f6bf207aabe`.
- 실제 대상은 `src/kiwoom_monitor/presentation/main_window.py`의 `MainWindow`다.
- `bootstrap.py`가 runtime·저장소·경로를 생성하여 MainWindow에 주입한다. 기존 생성자 인자를 유지한다.
- 테스트 앱은 이 워크트리의 `scripts/run_test_app_with_data.py`를 통해 실행한다. 원본 프로젝트의 `.venv` 재사용과 실행 소스는 별개다. 자동 검증에는 임시 DB와 fake API를 사용한다.
- [연결 기준 JSON](MAIN_WINDOW_CONNECTION_BASELINE.json)은 MainWindow 내부 메서드 정의 267개, `.connect()` 지점 116개, `QTimer.singleShot()` 지점 24개, Controller/Coordinator/Writer/QTimer 생성 지점 35개를 기록한다. 조건부·동적 생성도 포함한 **정적 호출 지점 수**이며 실제 생성 객체나 신호 전달 횟수가 아니다. 자식 Controller 내부·상속받은 연결까지 전수 확인했다는 뜻도 아니다.
- 기준 JSON은 변경 전 상태로 고정한다. 변경 후에는 이전→이후 연결 대응과 실행 증거를 별도로 기록하며 수신자 이름이나 연결 개수만 같다고 성공으로 판정하지 않는다.

## 책임 결정

새 `presentation/app_controller.py`의 `AppController(QObject)`는 Qt GUI 스레드에 둔다. 앱 전체 자원 수명, 시작/종료 조정, API 교체, 순위 뒤 작업 순서를 단계별로 이곳으로 옮긴다. QWidget 없이 QApplication/QCoreApplication 이벤트 루프와 fake 작업 객체로 조정을 검증할 수 있어야 한다.

| 담당 | 소유 책임 |
|---|---|
| `bootstrap.py` | 기존 API runtime·저장소·데이터 경로 구성 및 앱 실행 |
| `MainWindow` | 위젯, 표 행/선택/색상/모달 상태, 차트·대화상자 표시, 사용자 입력, 창·열 설정 |
| `AppController` | 앱 전체 수명 상태, 기능별 작업 객체의 보유와 수명 조정, 시작 허용/종료 대기, API 교체·후속 실행 순서 |
| 기존 `*WorkerController` | 개별 QThread 생성·중복 방지·결과 신호·finished 정리·deleteLater |
| 기존 `RankingExecutionCoordinator`, `RealtimeSubscriptionCoordinator`, `SecondaryDataFollowupCoordinator` | 순위 응답/일정 판단, 거래시간별 구독 판단, 보완 단계 정책. 정책을 AppController에 복제하지 않음 |
| 기존 aggregator·TOP20 collector·저장소·writer | 봉/지수 계산, 저장 의미와 transaction, writer queue. 계산·SQL 구현을 AppController로 옮기지 않음 |

MainWindow 전체를 인자로 받아 private 속성을 자유롭게 읽고 쓰는 방식, `__getattr__` 프록시, MainWindow 메서드를 이름만 바꿔 위임하는 전체 façade는 사용하지 않는다. 필요한 의존성을 명시적으로 전달한다. 보조 타입이 필요하면 같은 모듈의 고정된 데이터형으로 두며 범용 hook/플러그인/작업 등록 프레임워크를 만들지 않는다.

AppController는 feature controller를 이름 있는 속성으로 제공한다. MainWindow는 화면에 필요한 신호를 그 객체에 직접 연결할 수 있다. 모든 tick을 AppController가 다시 emit하는 중계는 만들지 않는다. 조정 책임이 실제로 이동한 연결만 AppController가 직접 수신한다. QObject parent와 Python 참조 소유자를 구분하며 모든 controller의 thread affinity는 기존 GUI 스레드로 유지한다.

## 주요 연결과 보존해야 할 효과

| 진입 → 현재 경로 | 반드시 보존할 결과·순서 | 관련 검사 |
|---|---|---|
| 앱 생성 → writer 시작 → 최초 순위 예약 | 신호/상태 준비 뒤 작업 시작, 최초 순위는 Drive 응답을 기다리지 않음, 인자 없는 실행도 API 설정 안내 | `test_main_window`, 새 시작 순서 검사 |
| 순위 timer → `RankingExecutionCoordinator` → `RankingWorkerController.start` | PreciseTimer·기존 경계/2.5초 사전 양보, 중복 실행 차단, 실행 중 도래한 회차 한 번 재요청 | `test_ranking_execution`, `test_ranking_worker_controller` |
| 순위 completed → `_on_ranking_loaded` | 응답 검증/부분·모달 지연/표 적용 뒤 구독·후속 예약, NAS 저장 응답과 직접 API 재시도 차이 | `test_main_window`, `test_ranking_execution` |
| NXT finished → 목록 동기화 → 구독 → 후속 조회 | 기존 3개 수신자 연결 순서, NXT 불가 종목 등록 방지 | `test_nxt_eligibility_worker_controller`, 새 연결 검사 |
| NAS 순위 적용 → 저장 분봉 선조회 → 실시간 구독 → 5초 fallback | 중앙 자료는 REG 완료 전에도 복원 가능, stale codes/revision의 후속 중복 실행 방지 | `test_main_window`의 NAS·followup 검사 |
| 실시간 subscription_ready → `_start_realtime_followups` | 동일 순위 revision에서 1회, 현재 종목 포함 여부·priority/closing guard 유지 | `test_main_window`, `test_realtime_subscription` |
| connection_opened/codes_added → aggregator baseline reset | 현재 aggregator를 참조함. API 교체 전 aggregator에 결합된 callback을 남기지 않음 | `test_realtime_worker_controller`, 새 runtime 교체 검사 |
| trade_received → 메모리 봉/화면/대기 저장 | tick 원본과 누적 snapshot 의미, 기존 배치/화면 갱신 간격과 queue 경계 | `test_main_window`, `test_market_cache_writer` |
| market cache minute_saved → TOP20 repair; history_saved → 완료 표시 | 저장 요청 수락 시점이 아닌 저장 성공 신호 뒤 후속 처리 | `test_market_cache_writer`, `test_main_window` |
| minute/price 실패 → pending 복구 → timer | 실패한 옛 값보다 그동안 들어온 최신 값 우선, closing 중 재예약 방지 | `test_main_window` 실패 복구 검사 |
| daily high/fundamentals/historical/NXT → 메모리·캐시·표 | 날짜/시장/완료 증거·0B 우선권·수신완료 표식 보존, 단순 존재로 완료 판정하지 않음 | 기존 각 worker/controller 및 main 검사 |
| order_executed → `EntrySnapshotWriter.enqueue` | 계좌 scope·execution key·실제 체결 당시 문맥 보존. 새 주문 실행 경로를 만들지 않음 | 기존 snapshot 계좌 검사 + 연결 검사 |
| API 설정 변경 → 기존 7종 worker 중지 대기 → factory 교체 | 늦은 순위 응답 폐기, controller 객체 재생성으로 신호가 중복되지 않음, 성공/실패 후 상태 처리 유지 | 기존 late response 검사 + 새 end-to-end 검사 |
| Drive 완료/실패·strict restore → 변경 표식/후속 동기화 | strict restore 후 종료 업로드 억제, 변경 범위 both·실패 dirty 복구 | `test_main_window`, `test_google_drive_worker_controller` |
| OCR completed/finished → 진행창·검토창·후속 처리 | 복수 수신자 순서와 OCR 전용 종료 제한시간 보존 | `test_image_theme_ocr_worker_controller`, main 연결 검사 |
| 뉴스/일지 자식 프로세스 → 명령/종료 | 소스 경로·계좌 scope·PID 소유권·메인 종료 연동 유지 | 기존 news/journal 검사 + 종료 연결 검사 |
| 표 헤더/클릭/더블클릭/테마/차트/설정 | UI 연결은 MainWindow에 유지, 컬럼 controller와 dialog parent 보존 | `test_main_window`, `test_main_window_layout` |

자식 Controller의 `.finished` 처리와 `_release_finished_worker` 순서도 검토한다. 특히 `RankingWorkerController`는 완료 신호 중계 후 worker 참조를 정리한다. 새 코드가 참조 정리 전에 즉시 재시작하여 실패하지 않도록 기존 다음 이벤트 턴 예약을 유지한다.

## 구현 순서와 첫 작업의 정확한 경계

### A. AppController 자원 소유와 종료 조정 — 완료

1. 먼저 변경 전 baseline 검사를 실행하고 결과를 기록한다. 새 종료 순서·중복 close·진행 중 worker 검사는 현재 구현에서의 관측 결과도 남긴다. 기존에 실패하는 항목을 새 리팩터링의 성공 기준으로 몰래 바꾸지 않는다.
2. AppController가 기존 feature worker controller 12개와 `MarketCacheWriter`, `EntrySnapshotWriter`의 참조·생성을 소유하게 한다. 생성과 시작을 구분하여 신호를 모두 연결한 뒤 writer를 기존 최초 작업보다 먼저 시작한다. 선택 경로가 없으면 writer를 생성하지 않는 계약을 유지한다.
3. MainWindow의 `_closing` 상태와 `_workers`, `_running_workers`, `_request_worker_stop`, `_finish_shutdown`의 실행 정책을 옮긴다. AppController는 현재 worker를 매번 조회한다. 처음 얻은 QThread 목록을 고정하지 않는다. TOP20 NAS 동적 worker set도 수명 추적 대상에 포함한다.
4. `MainWindow.closeEvent`는 AppController의 종료 요청 결과로 accept/ignore만 결정한다. AppController는 최초 종료의 단 한 번 실행, 진행 중 worker의 재확인, 기존 100ms 첫 확인/250ms 후속 확인, 종료 준비 완료 신호를 소유한다. UI에 종료 진행 문구를 알리는 신호와 준비 완료 신호를 별도로 제공한다.
5. UI 저장·표시 작업과 아직 이전하지 않은 데이터 flush는 명시적인 callback으로 연결한다. callback에는 기존의 `flush_partial_top20`, `flush_price_cache`, `flush_minute_bars`, `flush_daily_comparisons`, `start_exit_backup_if_needed`, 자식 창 종료 등이 포함된다. **전체 closeEvent를 callback 한 개로 남기지 않는다.** AppController 본문이 아래 실행 순서와 대기 결정을 실제로 소유해야 한다.
6. 종료 시 정지시킬 기존 timer 참조는 고정된 필드/튜플로 전달한다. 지연 생성되는 trade tick timer는 실제 현존 참조를 조회한다. geometry·media·dialog 처리는 MainWindow가 맡고, stop/flush/wait의 순서는 AppController가 결정한다. callback과 자원 참조의 자료형이 필요하면 `app_controller.py` 내부 고정 dataclass 하나로 제한한다.
7. 기존 MainWindow의 `_ranking_worker` 등 읽기용 property는 새 소유 객체를 가리키게 한다. MainWindow에 독립적인 controller/closing 상태를 복제하지 않는다. `_closing` 호환 getter가 필요하면 AppController의 단일 상태를 읽으며, 새 핵심 구현에서는 AppController의 public 상태를 사용한다.
8. A에서는 API 교체 정책, 순위 응답 해석, 보완 날짜 정책과 저장 형식을 바꾸지 않는다. `_api_reloading`은 B에서 일괄 이전한다. 역할 이전이 끝난 메서드를 MainWindow와 AppController 양쪽에 유지하지 않는다.

**단계 A 이전 `closeEvent` 순서:** closing 설정 → TOP20 창 위치/partial record → 메인 위치·열 저장 → 순위/화면 timer 정지 → 가격 flush → 분봉 timer 정지/flush → cache writer drain → 비교 timer 정지/flush → tick timer·소리·보조 대화상자 정지 → 종료 업로드 조건 판단 → worker interruption → OCR 전용 stop → running worker 대기 → 뉴스/일지 종료 → close 허용. 이 기록은 변경 전 기준이며 현재 실행 순서는 AppController 구현과 테스트를 기준으로 한다.

이 순서는 현행 코드의 기준이다. 아래의 기존 종료 의문점은 실행으로 확인하기 전 성공 계약으로 인정하지 않는다. A의 순수 이동과 결함 수정은 변경/검증 결과를 구분하며, 재현으로 확인된 해당 수명 경계 결함만 별도로 최소 수정한다.

**A의 완료 기준:** AppController가 종료 상태·실제 대기 결정을 소유하고 QWidget 없이 이를 검사할 수 있음; MainWindow는 close 수락/표시를 맡음; 기존 feature controller의 신호/worker 정리와 최초 작업 순서 보존; 새 종료 검사가 MainWindow를 통한 실제 연결도 검증함.

### B. API runtime 교체와 시작 흐름 — 구현 완료

`_start_initial_ranking`, `_restart_for_api_settings`, `_api_runtime_workers`, `_finish_api_runtime_reload`의 정책·reloading 상태·예약을 옮긴다. 화면 갱신은 명시적 결과 신호로 분리한다. 기존 주입 factory와 관련 worker 7종, entry writer loader 교체, 구독 reset, aggregator 교체, 캐시 무효화를 하나의 검토 단위로 삼는다. 기존 결과 수신 callback이 새 aggregator/client를 바라보는지 검사한다. runtime 생성 실패·교체 중 close·이전 응답 후착의 계약을 기준과 대조한다.

이 단계에서 API runtime 교체 상태와 예약, 초기 순위 시작 요청을 AppController로 이전했다. MainWindow는 기존 화면 갱신·상태 표시를 명시적 callback/signal로 제공한다. 이전 runtime의 worker가 끝나고 queued 결과가 전달된 뒤 factory를 적용하며, 새 runtime 적용 시 feature controller factory, EntrySnapshotWriter loader, market-data client, 구독/aggregator/cache 화면 효과를 함께 갱신한다. factory 실패 시 이전 loader/client를 보존하고 실패 신호를 내며 reload 상태를 해제한다. 닫기 중 교체 timer와 초기 순위 timer를 취소한다. 회귀는 지연 worker 결과·정상 교체·factory 실패·close 중 취소를 확인한다.

### C. 순위·구독·보완 조정 — 로컬 구현·회귀 완료

순위/세션 timer, 응답 결정과 보류 응답 flush, 실시간 구독 적용, 적용된 순위 종목 snapshot, 후속 보완 순서를 AppController가 소유한다. 기존 `RankingExecutionCoordinator`, `RealtimeSubscriptionCoordinator`, `SecondaryDataFollowupCoordinator`를 정책 판단에 재사용한다. MainWindow는 표·cache rendering을 맡고 고정된 UI/data callback을 제공한다.

응답 경로는 표 적용 → 분봉 요청 → 실시간 구독 → 구독 준비 또는 fallback 후속 작업 순서를 유지한다. NXT 확인 완료 뒤에는 종목 목록 갱신 → 구독 → 후속 보완 순서를 유지한다. 직접 API의 부분 응답은 1.5초 뒤 재시도하고 NAS 저장소의 부분 응답은 다음 순위 일정까지 기다린다. 모달이 열려 있으면 최신 응답 하나만 보류한다. API 설정 교체 중 이전 runtime 결과를 버리고, 종료 시 timer 예약과 새 후속 작업을 중단한다. 후속 종목은 table에서 다시 추출하지 않고 적용된 순위 tuple을 사용한다.

변경 전 정적 연결 기준 116건 중 105건은 MainWindow에 유지되고, 11건은 [연결 대조](MAIN_WINDOW_CONNECTION_COMPARISON.json)에 기록한 대로 AppController로 이동했다. 단계 A-C에서 MainWindow 화면 결과·제어 연결 16건이 추가됐다. 관련 19개 모듈의 offscreen 회귀 138건이 프로세스 exit code 0으로 통과했고, 직접 영향을 받는 두 모듈은 별도로 65건을 통과했다. 로컬 Qt 검증이며 설치 앱 수동 검증이나 NAS 실행 증거는 아니다.

### D. pending 저장과 화면 의존 상태 — 로컬 구현·offscreen 회귀 완료

가격·당일고가·시가총액, 종목 분봉·시장지수 분봉, 개발용 비교 CSV의 pending 값과 1초/1초/500ms timer, flush, writer 실패 merge/retry를 AppController로 옮겼다. MainWindow 생성자가 넘긴 동일 저장소·종목 조회 객체를 AppController가 보유한다. 기존 writer queue, 저장 단위, payload, 시간 간격, repository 호출과 transaction은 바꾸지 않았다.

실제 UI와 공유되는 `_current_prices`, `_today_high_prices`, 시가총액·화면 렌더 캐시는 MainWindow에 뒀다. 체결 tick과 지수 tick은 MainWindow에서 기존 표시·집계를 적용한 뒤 AppController의 저장 대기열에 전달한다. AppController는 TOP20 collector나 체결 snapshot의 계산을 가져오지 않았다. TOP20 저장 완료 신호는 `minute_cache_saved`를 통해 기존 시장 보완 UI receiver에 전달한다. 저장소 직접 호출 fallback도 같은 성공 신호를 낸다.

실패 복구는 저장 중 보관했던 값을 현재 pending 값 뒤에 합쳐 같은 키의 새 값이 우선하게 한다. 종료 시 producer와 queued 결과 signal을 기다린 뒤 AppController가 세 pending을 flush하고 writer 종료를 기다린다. 종료 중 실패한 저장은 예전 동작대로 메모리에 남기되 재시도 timer는 시작하지 않는다. 이 단계는 버퍼 수명과 연결만 옮겼으며 날짜 경계 동작, 비교 저장 실패 정책, 종료 뒤 재시작/영속 복구 정책은 변경하지 않았다.

`tests.unit.test_app_controller`, `test_main_window`, `test_market_cache_writer`의 offscreen 회귀와 새 coalescing/Qt failure signal/late trade shutdown 검사를 포함한 관련 19개 모듈 **142건이 통과했다**(현재 워크트리 소스 우선, 종료 코드 0). 지연 체결이 종료 대기 중 MainWindow 메모리 처리기를 거쳐 실제 임시 SQLite에 현재가와 분봉을 기록하는 것도 확인했다. 상세 연결 수는 [정적 대조표](MAIN_WINDOW_CONNECTION_COMPARISON.json)에 기록했다.

경계 검토 결과 TOP20 collector는 이미 해당 도메인 상태를 소유하고, snapshot 계산은 UI에서 관측하는 계좌·화면 데이터를 함께 소비한다. 두 책임은 기존 담당자와 MainWindow에 남겨 독립 데이터 소유자인 척 AppController로 이동하지 않았다. 실제 설치된 앱의 전체 화면 흐름·장중 수신은 아직 수동 확인하지 않았다.

## 파일·호출 깊이 비용

| 흐름 | 변경 전 | A/B 이후 목표 |
|---|---|---|
| 개별 worker 생성/정리 | MainWindow → feature controller → worker | AppController → 동일 feature controller → 동일 worker |
| 화면 결과 전달 | worker → feature controller → MainWindow | 동일. 앱 조정이 필요한 결과만 AppController가 수신 |
| 종료 | MainWindow.closeEvent가 UI/저장/interrupt/wait를 혼합 | MainWindow.closeEvent → AppController의 실제 종료 정책 → 기존 자원 |

새 책임 모듈은 우선 1개다. 종료 이해에 AppController 한 파일이 더 필요하지만 UI 수천 줄을 읽지 않고 종료 상태/순서를 이해·검사할 수 있어야 한다. UI 처리와 업무 처리가 양쪽 파일을 계속 왕복하도록 옮겨졌다면 단계 완료로 보지 않는다.

## 검증 계획

### 변경 전/후 같은 조건으로 실행

- 공통: `tests.unit.test_main_window`, `test_main_window_layout`, `test_ranking_execution`, `test_realtime_subscription`, `test_secondary_data_schedule`, `test_market_cache_writer`.
- 소유권 이동: ranking/realtime/minute_history/daily_high/fundamentals/historical_high/nxt_eligibility/krx_stock_catalog/top20_market_repair/google_drive/image_theme_ocr/update worker controller의 기존 단위검사.
- 추가: `tests/unit/test_app_controller.py`에서 초기화/중복 시작·close 2회/완료 지연/작업 참조 변경/종료 재진입과 기다리는 동안 자원 생존을 검사한다. `test_main_window.py`에는 실제 새 연결을 통한 종료와 최초 조회 경로를 추가한다.
- 실행기: 원본 `.venv/Scripts/python.exe`를 사용하되 `PYTHONPATH` 맨 앞을 **현재 워크트리 src**로 지정하고 import된 `main_window.__file__`를 기록한다. Qt는 offscreen으로 실행하고 테스트 DB는 임시 위치에 둔다.
- `OK`뿐 아니라 프로세스 exit code와 종료 로그를 확인한다. 기존 tearDown의 deferred deletion/event 처리·GC를 유지한다. 실행 중 QThread 파괴, native 종료 예외, 남은 worker가 있으면 통과로 보지 않는다.

### 동작 연결 기준

단순히 `.connect()`가 있거나 mock이 한 번 호출된 것으로 끝내지 않는다. fake worker의 실제 Qt signal을 emit하여 순위 표 적용→구독→보완, 저장 성공→완료 표식, 저장 실패→최신 pending 보존, 종료→worker 종료→최종 창 close까지 검사한다. 순서가 중요한 복수 수신자에는 관측 event 목록을 사용한다. 지연된 finished/deleteLater와 API 교체 이전 결과도 포함한다.

실행형 검사 뒤 테스트 앱에서 최초 순위·순위 변경·NXT/일반 세션·신고가·가격·차트·뉴스·일지·설정/API 재연결·종료를 확인한다. 주말/장외라 실제 신규 체결·시장 전환을 확인할 수 없으면 fake 검증과 장중 미확인을 구분한다. 소유권 리팩터링만으로 운영 DB 쓰기나 주문을 새로 실행하지 않는다.

## 단계 A에서 재현·수정한 종료 경계

다음은 사전 조사 가설 중 테스트로 재현된 항목과 현재 처리 상태다. 사용자 운영 데이터 손실이 실제 발생했다고 확대 해석하지 않는다.

1. **비교 writer enqueue 순서:** cache writer를 먼저 drain한 뒤 비교 결과를 enqueue하면 마지막 비교값을 놓칠 수 있었다. 종료 때 producer를 먼저 중단하고 queued 결과를 처리한 다음 price/minute/comparison을 모두 flush하도록 바꿨다. 실제 SQLite writer 종료 전 저장 및 비교 완료 신호를 확인했다.
2. **writer 종료 제한시간:** cache writer drain timeout을 종료 허용으로 해석하면 아직 실행 중인 writer가 남을 수 있었다. timeout은 경고만 내고 실제 writer가 끝날 때까지 종료를 보류한다. 테스트에서 writer가 살아 있는 동안 종료가 거부되는 것을 확인했다.
3. **후착 producer 결과:** worker interruption을 요청한 즉시 저장 writer를 중지하지 않는다. 실행 중 producer와 queued 결과 signal이 끝난 뒤 flush한다. 지연된 분봉 결과가 DB에 도달하고 저장 완료 handler가 창 종료보다 먼저 실행되는 경계를 테스트했다.
4. **별도 QThread·timer:** journal/investment 안내 timer를 종료 목록에 넣었다. `_theme_save_request`는 기존에 AppController의 QObject 자식이 아니어서 동적 QThread 검색에서 빠질 수 있었다. SettingsRequestWorker를 AppController 자식으로 생성하도록 연결하고, 실제 대기 중인 child QThread가 끝나기 전에는 종료하지 않는 회귀를 추가했다.
5. **동적 worker 목록:** TOP20 NAS 작업과 feature controller가 현재 보유한 worker를 각 대기 단계에서 다시 조회한다. 참조 교체 및 feature controller가 참조를 비운 뒤에도 남아 있는 QThread를 확인한다.

관련 구현·검증 결과는 [보류 원장](OPEN_ITEMS.md), [정적 연결 비교](MAIN_WINDOW_CONNECTION_COMPARISON.json), `tests/unit/test_app_controller.py`에서 확인한다. A/B 검증은 로컬 offscreen 회귀이며 설치된 테스트 앱의 수동 전체 흐름이나 NAS 배포 검증은 포함하지 않는다.

## 현재 진행 상태

- 완료: 첨부 MainWindow 범위 확인, 현재 구조·연결 지점 정적 기록, AppController 책임과 단계 A/B 구현 경계 결정.
- 완료: 단계 A AppController가 기존 feature controller 12개, optional writer 2개, TOP20 NAS 동적 worker와 종료 상태·대기 순서를 소유한다. MainWindow의 controller/writer 호환 접근자는 AppController의 단일 객체를 읽는다.
- 완료: 단계 B API runtime 교체 상태/worker 대기, runtime factory 적용, feature factory·entry writer loader·market client 교체, 구독/aggregator/cache 화면 효과 연결, 초기 순위 시작 조정을 이전했다. 실패 시 이전 runtime 보존 및 종료 중 예약 취소를 검사했다.
- 완료: 단계 C 순위 일정·응답 판단·NXT 구간·실시간 구독·보완 후속 순서를 AppController로 이전했다. 적용 종목은 widget에서 다시 추출하지 않고 controller snapshot을 쓴다. 직접 부분 응답 재시도, NAS 저장 부분 응답 대기, modal 최신 응답, API 교체 후착 결과, close timer 정리를 Qt 회귀로 검사했다.
- 재현 후 수정: comparison flush를 writer drain 앞으로 옮겼고, 실행 중 producer 결과 신호 전달 뒤 queue를 drain한다. writer drain timeout은 종료 허용으로 처리하지 않고 writer 종료까지 기다린다. worker 목록은 각 확인 때 다시 조회한다. 종료 중 새 OCR·업데이트·NAS 요청을 막고 journal 안내 timer를 멈춘다.
- 연결 비교: 변경 전 116건 중 99건은 MainWindow에 남고 17건은 AppController로 이동했다. 단계 A-D에서 MainWindow 화면 결과·제어 연결 17건이 추가됐다. 이전 연결은 [전후 비교](MAIN_WINDOW_CONNECTION_COMPARISON.json)에 개별 대응시켰다. 정적 AST 수치는 신호 전달 증거가 아니며 Qt 회귀 결과와 구분한다.
- 실행 검증: 기준 Python/Qt 환경과 현재 worktree `src`로 관련 19개 모듈 142건이 통과했다(exit code 0). Qt teardown은 DeferredDelete/event 처리를 실행하고 남은 QThread를 확인했다. 로그는 `artifacts/app-controller-validation/stage-d-suite.log`에 있다.
- 단계 A-D 로컬 구현이 끝났다. 실제 설치 앱의 전체 흐름·장중 실시간 수신 수동 확인 및 NAS 배포는 하지 않았다. 여기까지의 결과는 [보류 원장](OPEN_ITEMS.md)과 [정적 연결 대조](MAIN_WINDOW_CONNECTION_COMPARISON.json)에 기록한다.
